"""
verify.py — headless checks of both build targets, from a clean checkout.

What it proves, with Playwright's Chromium against the local static server:

  1. Every standalone page and the embed render with no JavaScript errors
     (uncaught exceptions or console errors), in light and dark.
  2. The charts draw: the map has state geometry and station dots, the city
     chart has series paths, the hurricane panel has geography.
  3. Graceful degradation: with the data feed answering 503, a browser that
     has loaded the site before renders the last data it saved and says so;
     a browser with nothing cached renders the frame with an explicit
     no-data state. Neither path raises a script error.
  4. The market overlay off state reserves no space: the chart's viewBox is
     the weather-only height and no ladder text exists; with ?market=on the
     taller layout and the ladder appear.

Requires: python3 -m pip install playwright (and a Chromium build, which
`python3 -m playwright install chromium` fetches if none is cached). Runs
scripts/build.py first unless --no-build. Writes verify-out/report.json and
screenshots. Exit 1 on any failure.

    python3 scripts/verify.py
"""
from __future__ import annotations
import argparse
import json
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "verify-out")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Server:
    def __init__(self, target: str, fail: bool = False):
        self.port = free_port()
        args = [sys.executable, os.path.join(ROOT, "scripts", "serve_local.py"), "--target", target,
                "--port", str(self.port), "--quiet"] + (["--fail-fetch"] if fail else [])
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        self.url = f"http://127.0.0.1:{self.port}"

    def stop(self):
        self.proc.terminate()
        self.proc.wait(timeout=5)


class Check:
    def __init__(self):
        self.results = []

    def add(self, name: str, ok: bool, detail: str = ""):
        self.results.append({"name": name, "ok": bool(ok), "detail": detail})
        print(("  ok   " if ok else "  FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))

    @property
    def failed(self):
        return [r for r in self.results if not r["ok"]]


def errors_of(page):
    """Collect uncaught exceptions and console errors, ignoring the browser's
    own 'Failed to load resource' lines, which are network status, not script
    faults (the forced-failure pass produces them on purpose)."""
    errs = []
    page.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
    page.on("console", lambda m: errs.append("console: " + m.text)
            if m.type == "error" and "Failed to load resource" not in m.text else None)
    return errs


def run(no_build: bool) -> int:
    from playwright.sync_api import sync_playwright
    os.makedirs(OUT, exist_ok=True)
    if not no_build:
        subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "build.py")], check=True, cwd=ROOT)
    chk = Check()
    srv = Server("standalone")
    emb = Server("embed")
    bad = Server("standalone", fail=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for scheme in ("light", "dark"):
                ctx = browser.new_context(color_scheme=scheme, viewport={"width": 1200, "height": 900})
                page = ctx.new_page()
                errs = errors_of(page)
                # ---- standalone pages
                pages = [("index.html", "#map path", "map geometry"), ("city.html?station=KLAX", "#chart path", "city series"),
                         ("hurricane.html", "#basin path", "basin geography"), ("scorecard.html", "#overall table", "scorecard table"),
                         ("climate.html", "#panels svg path", "climate series"), ("about.html", "footer.site", "footer")]
                for path, sel, what in pages:
                    page.goto(f"{srv.url}/{path}")
                    page.wait_for_timeout(900)
                    n = page.locator(sel).count()
                    chk.add(f"{scheme} standalone {path}: renders {what}", n > 0, f"{sel} count={n}")
                    if page.locator("#pageStatus, #chartStatus").count():      # data pages carry a status strip
                        status = page.locator(".status").first.inner_text() if page.locator(".status").count() else ""
                        chk.add(f"{scheme} standalone {path}: status strip present", "Data as of" in status or "No data" in status, status[:80])
                    page.screenshot(path=os.path.join(OUT, f"{scheme}-{path.split('?')[0]}.png"), full_page=True)
                chk.add(f"{scheme} standalone: no script errors", not errs, "; ".join(errs)[:300])
                # ---- market overlay: on by config on the standalone site; off reserves no space
                page.goto(f"{srv.url}/city.html?station=KLGA&market=off")
                page.wait_for_timeout(900)
                vb_off = page.locator("#chart").get_attribute("viewBox")
                ladder_off = page.locator("#chart text", has_text="Strike ladders").count()
                chk.add(f"{scheme} market off: weather-only height, no ladder", vb_off == "0 0 960 390" and ladder_off == 0, f"viewBox={vb_off} ladder={ladder_off}")
                page.goto(f"{srv.url}/city.html?station=KLGA&market=on")
                page.wait_for_timeout(900)
                vb_on = page.locator("#chart").get_attribute("viewBox")
                ladder_on = page.locator("#chart text", has_text="Strike ladders").count()
                chips = page.locator("#skRow button").count()
                chk.add(f"{scheme} market on: ladder layout and strike chips", vb_on == "0 0 960 655" and ladder_on == 1 and chips >= 4, f"viewBox={vb_on} ladder={ladder_on} chips={chips}")
                live_lbl = page.locator("#chart text", has_text="ForecastEx quotes").count()
                chk.add(f"{scheme} market on: ladder labelled with the exchange and its as-of time", live_lbl == 1, f"count={live_lbl}")
                price_paths = page.locator("#chart path[stroke-width='1.8']").count()
                chk.add(f"{scheme} market on: quote history drawn for the default strikes", price_paths >= 1, f"paths={price_paths}")
                chk.add(f"{scheme} market toggles: no script errors", not errs, "; ".join(errs)[:300])
                # ---- hover layer on the city page: chips, level labels, picker dots and the crosshair
                def tip_after(locator):
                    locator.hover(force=True); page.wait_for_timeout(120)
                    return page.locator("#tip").inner_text()
                t_chip = tip_after(page.locator("#skRow button").nth(2))
                chk.add(f"{scheme} hover: strike chip shows the book", "Yes bid" in t_chip and "No bid" in t_chip and "Quotes as of" in t_chip, t_chip[:80])
                t_lvl = tip_after(page.locator("#chart text.lvlnm").first)
                chk.add(f"{scheme} hover: level label names the source, cycle and value", "forecast" in t_lvl and "Cycle" in t_lvl and "Value" in t_lvl, t_lvl[:80])
                t_pick = tip_after(page.locator("#pick g").nth(3))
                chk.add(f"{scheme} hover: picker dot shows tomorrow and today", "tomorrow" in t_pick and "Observed so far today" in t_pick, t_pick[:80])
                box = page.locator("#chart").bounding_box()
                page.mouse.move(box["x"] + box["width"] * 0.35, box["y"] + box["height"] * 0.3); page.wait_for_timeout(120)
                t_x = page.locator("#tip").inner_text()
                chk.add(f"{scheme} hover: crosshair lists the series at one time", "Observed" in t_x or "NWS" in t_x, t_x[:80])
                chk.add(f"{scheme} hover: no script errors", not errs, "; ".join(errs)[:300])
                # ---- hurricane page: season tiles, count ladders, the landfall board, the vendor lane status
                page.goto(f"{srv.url}/hurricane.html")
                page.wait_for_timeout(900)
                tiles = page.locator("#tiles .tile").count()
                ladders = page.locator("#ladders .ladder").count()
                lf_rows = page.locator("#landfall table tr").count()
                vendor = page.locator("#vendor").inner_text()
                chk.add(f"{scheme} hurricane: tiles, count ladders and the landfall board", tiles >= 3 and ladders >= 2 and lf_rows >= 2, f"tiles={tiles} ladders={ladders} landfall rows={lf_rows}")
                chk.add(f"{scheme} hurricane: vendor lane reports its state", "Not enabled" in vendor or "Lane on" in vendor or "LiveCyc" in vendor, vendor[:80])
                dots = page.locator("#basin circle").count()
                chk.add(f"{scheme} hurricane: reference locations drawn", dots >= 100, f"circles={dots}")
                page.locator("#basin circle").nth(40).hover(force=True); page.wait_for_timeout(120)
                t_dot = page.locator("#tip").inner_text()
                chk.add(f"{scheme} hover: reference location names itself and the lane state", "Country" in t_dot and ("probabilities" in t_dot), t_dot[:80])
                page.locator("#ladders .lrow").first.hover(force=True); page.wait_for_timeout(120)
                t_row = page.locator("#tip").inner_text()
                chk.add(f"{scheme} hover: count ladder row shows the book and settlement", "Yes bid" in t_row and "Settles" in t_row, t_row[:80])
                found = ""
                for i in range(min(page.locator("#basin path").count(), 80)):
                    page.locator("#basin path").nth(i).hover(force=True); page.wait_for_timeout(40)
                    t = page.locator("#tip").inner_text()
                    if "Landfall contract" in t: found = t; break
                chk.add(f"{scheme} hover: a shaded landfall region shows its contract", "Yes bid" in found, found[:80])
                # ---- climate page: live contract markers
                page.goto(f"{srv.url}/climate.html")
                page.wait_for_timeout(900)
                markers = page.locator("#panels svg circle, #panels svg path[stroke-width='1']").count()
                chk.add(f"{scheme} climate: contract markers drawn from the quote snapshot", markers >= 5, f"markers={markers}")
                page.locator("#panels svg circle[r='8']").first.hover(force=True); page.wait_for_timeout(120)
                t_mk = page.locator("#tip").inner_text()
                chk.add(f"{scheme} hover: climate marker shows settlement and the book", "Settles" in t_mk and "Yes" in t_mk, t_mk[:80])
                pb = page.locator("#panels svg").first.bounding_box()
                page.mouse.move(pb["x"] + pb["width"] * 0.5, pb["y"] + pb["height"] * 0.5); page.wait_for_timeout(120)
                t_ser = page.locator("#tip").inner_text()
                chk.add(f"{scheme} hover: climate series point shows year, value and source", "Value" in t_ser and "Latest" in t_ser, t_ser[:80])
                # ---- scorecard hover: overall, station and day cells
                page.goto(f"{srv.url}/scorecard.html")
                page.wait_for_timeout(900)
                # ---- the divergence figure: four views, ranking column and hover
                fig_rows = page.locator("#divsvg g").count()
                chk.add(f"{scheme} scorecard: divergence figure draws a row per station", fig_rows >= 20, f"rows={fig_rows}")
                page.locator("#divsvg circle").first.hover(force=True); page.wait_for_timeout(120)
                t_dot = page.locator("#tip").inner_text()
                chk.add(f"{scheme} hover: figure dot shows the forecast and its gap from consensus", "Consensus median" in t_dot and "From the consensus" in t_dot, t_dot[:80])
                titles = []
                for i in range(4):
                    page.locator("#divControls button").nth(i).click(); page.wait_for_timeout(250)
                    titles.append(page.locator("#divTitle").inner_text())
                chk.add(f"{scheme} scorecard: all four views render a titled figure", len([t for t in titles if t]) == 4 and page.locator("#divsvg g").count() >= 20, "; ".join(t[:26] for t in titles))
                page.locator("#divControls button").first.click(); page.wait_for_timeout(250)
                col = page.locator("#divsvg text", has_text="Consensus error").count()
                chk.add(f"{scheme} scorecard: the scored view ranks by consensus error", col == 1, f"header={col}")
                page.locator("#overall td").nth(2).hover(force=True); page.wait_for_timeout(120)
                t_ov = page.locator("#tip").inner_text()
                chk.add(f"{scheme} hover: scorecard overall cell shows the source's statistics", "all stations" in t_ov and "MAE" in t_ov, t_ov[:80])
                page.locator("#days td").nth(5).hover(force=True); page.wait_for_timeout(120)
                t_day = page.locator("#tip").inner_text()
                chk.add(f"{scheme} hover: scorecard day cell shows cycle and lead", "Cycle" in t_day and "Lead" in t_day, t_day[:80])
                # ---- map hover: a station dot and a shading cell
                page.goto(f"{srv.url}/index.html")
                page.wait_for_timeout(900)
                page.locator("#map g.dot").nth(2).hover(force=True); page.wait_for_timeout(120)
                t_md = page.locator("#tip").inner_text()
                chk.add(f"{scheme} hover: map dot shows tomorrow's forecasts and today so far", "tomorrow" in t_md and "Observed" in t_md, t_md[:80])
                page.locator("#map rect[data-i]").nth(600).hover(force=True); page.wait_for_timeout(120)
                t_cell = page.locator("#tip").inner_text()
                chk.add(f"{scheme} hover: shading cell names the derived field value", "NWS forecast field" in t_cell, t_cell[:80])
                chk.add(f"{scheme} hurricane and climate: no script errors", not errs, "; ".join(errs)[:300])
                ctx.close()

            # ---- embed target
            ctx = browser.new_context(viewport={"width": 980, "height": 500})
            page = ctx.new_page()
            errs = errors_of(page)
            page.goto(f"{emb.url}/?station=KPHX&theme=light")
            page.wait_for_timeout(900)
            chk.add("embed: city series render", page.locator("#chart path").count() > 0)
            chk.add("embed: no site chrome", page.locator("header.site").count() == 0 and page.locator("footer.site").count() == 0)
            chk.add("embed: market off by default (weather-only height)", page.locator("#chart").get_attribute("viewBox") == "0 0 960 390")
            chk.add("embed: theme parameter applied", page.evaluate("document.documentElement.getAttribute('data-theme')") == "light")
            page.screenshot(path=os.path.join(OUT, "embed-light.png"), full_page=True)
            page.goto(f"{emb.url}/?station=KPHX&theme=dark&market=on")
            page.wait_for_timeout(900)
            chk.add("embed: ?market=on shows the ladder", page.locator("#chart text", has_text="Strike ladders").count() == 1)
            page.screenshot(path=os.path.join(OUT, "embed-dark-market.png"), full_page=True)
            chk.add("embed: no script errors", not errs, "; ".join(errs)[:300])
            ctx.close()

            # ---- degradation path 1: nothing cached, feed down -> explicit no-data state
            ctx = browser.new_context()
            page = ctx.new_page()
            errs = errors_of(page)
            page.goto(f"{bad.url}/index.html")
            page.wait_for_timeout(900)
            status = page.locator(".status").first.inner_text()
            chk.add("feed down, nothing cached: explicit no-data state", "No data available" in status, status[:80])
            chk.add("feed down, nothing cached: frame still renders", page.locator("header.site").count() == 1 and page.locator("#map").count() == 1)
            page.screenshot(path=os.path.join(OUT, "degraded-nocache.png"), full_page=True)
            chk.add("feed down, nothing cached: no script errors", not errs, "; ".join(errs)[:300])
            ctx.close()

            # ---- degradation path 2: a browser that has the site cached, then the feed fails
            ctx = browser.new_context()
            page = ctx.new_page()
            errs = errors_of(page)
            page.goto(f"{srv.url}/index.html")
            page.wait_for_timeout(900)
            ok_first = "Data as of" in page.locator(".status").first.inner_text()
            page.route("**/data/**", lambda route: route.fulfill(status=503, body="outage"))
            page.reload()
            page.wait_for_timeout(900)
            status = page.locator(".status").first.inner_text()
            chk.add("feed fails after a good load: last saved data shown and labelled", ok_first and "last data this browser saved" in status, status[:100])
            chk.add("feed fails after a good load: map still drawn from cache", page.locator("#map path").count() > 0)
            page.screenshot(path=os.path.join(OUT, "degraded-cached.png"), full_page=True)
            chk.add("feed fails after a good load: no script errors", not errs, "; ".join(errs)[:300])
            ctx.close()
            browser.close()
    finally:
        srv.stop(); emb.stop(); bad.stop()

    report = {"passed": len(chk.results) - len(chk.failed), "failed": len(chk.failed), "results": chk.results}
    with open(os.path.join(OUT, "report.json"), "w") as fh:
        json.dump(report, fh, indent=1)
    print(f"verify: {report['passed']} passed, {report['failed']} failed -> verify-out/report.json")
    return 1 if chk.failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-build", action="store_true")
    a = ap.parse_args()
    sys.exit(run(a.no_build))
