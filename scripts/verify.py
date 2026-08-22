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
                chk.add(f"{scheme} market on: ladder layout and strike chips", vb_on == "0 0 960 655" and ladder_on == 1 and chips == 22, f"viewBox={vb_on} ladder={ladder_on} chips={chips}")
                chk.add(f"{scheme} market toggles: no script errors", not errs, "; ".join(errs)[:300])
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
            chk.add("embed: ?market=on shows the placeholder ladder", page.locator("#chart text", has_text="Strike ladders").count() == 1)
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
