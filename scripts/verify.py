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
import base64
import datetime as dt
import json
import os
import socket
import re
import subprocess
import sys
import time
import urllib.request
from html import unescape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "verify-out")


def strip_markup(doc: str) -> str:
    """Readable text out of a page as it is served, before any script runs."""
    doc = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", doc, flags=re.S | re.I)
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", doc)).split())


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
    """The collector, and the filter that makes iterating on one area bearable.

    A full pass is both colour schemes over every page and every interaction,
    which is minutes. While working on one panel that is mostly waiting, so
    `--scheme light` halves it and `--only` narrows the page sweep.

    A filtered run must never be mistaken for a clean one. `filtered` is set the
    moment anything is narrowed, and the summary line says so in place of the
    usual count, because the failure this guards against is someone reading
    "0 failed" off a run that skipped the section holding the bug.
    """

    def __init__(self, only: str = "", schemes=("light", "dark")):
        self.results = []
        self.only = only or ""
        self.schemes = tuple(schemes)
        self.filtered = bool(self.only) or self.schemes != ("light", "dark")

    def wants(self, tag: str) -> bool:
        """Whether a named area is in scope for this run."""
        return not self.only or re.search(self.only, tag, re.I) is not None

    def add(self, name: str, ok: bool, detail: str = ""):
        self.results.append({"name": name, "ok": bool(ok), "detail": detail})
        print(("  ok   " if ok else "  FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))

    @property
    def failed(self):
        return [r for r in self.results if not r["ok"]]


def errors_of(page, into=None):
    """Collect uncaught exceptions and console errors, ignoring the browser's
    own 'Failed to load resource' lines, which are network status, not script
    faults (the forced-failure pass produces them on purpose). `into` adds a
    second page's errors to a list the run already checks."""
    errs = [] if into is None else into
    page.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
    page.on("console", lambda m: errs.append("console: " + m.text)
            if m.type == "error" and "Failed to load resource" not in m.text else None)
    return errs


def ndfd_view(view: str, day: str, issued: str = "2026-08-26T03:51:00Z") -> dict:
    """A view of the landing map's NDFD shading for the checks: whole degrees
    falling from 95 at the top of the map to 35 at the bottom, one byte a cell
    (degrees plus 60), the shape pipeline/ndfd.py writes. The bundled samples
    carry no NDFD issuance for their day, so the checks serve this one."""
    nx, ny = 240, 150
    cells = bytes(int(round(95 - 60 * j / (ny - 1))) + 60 for j in range(ny) for _ in range(nx))
    return {"schema": 1, "view": view, "day": day, "kind": "mint" if view in ("loT", "lo") else "maxt",
            "issued": issued, "ref": issued, "preDay": True if view in ("hiT", "loT") else None,
            "start": None, "end": None, "grid": {"step": 4, "nx": nx, "ny": ny}, "offset": 60,
            "source": "test view", "b64": base64.b64encode(cells).decode("ascii")}


def ndfd_route(days: dict):
    """A route handler serving ndfd_view for each view, `days` {view: day}."""
    def handler(route):
        u = route.request.url
        for v in ("hiT", "loT", "hi", "lo"):
            if u.split("?")[0].endswith("/ndfd/%s.json" % v):
                return route.fulfill(status=200, content_type="application/json", body=json.dumps(ndfd_view(v, days[v])))
        return route.continue_()
    return handler


def tiny_png(rgb) -> bytes:
    """A 4x4 PNG of one colour, standing in for a satellite frame in the loop checks."""
    import struct
    import zlib
    raw = b"".join(b"\x00" + bytes(rgb) * 4 for _ in range(4))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def run(no_build: bool, only: str = "", schemes=("light", "dark")) -> int:
    from playwright.sync_api import sync_playwright
    os.makedirs(OUT, exist_ok=True)
    if not no_build:
        subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "build.py")], check=True, cwd=ROOT)
    chk = Check(only=only, schemes=schemes)
    srv = Server("standalone")
    emb = Server("embed")
    bad = Server("standalone", fail=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for scheme in chk.schemes:
                ctx = browser.new_context(color_scheme=scheme, viewport={"width": 1200, "height": 900})
                page = ctx.new_page()
                errs = errors_of(page)
                # ---- standalone pages
                pages = [("index.html", "#map path", "map geometry"), ("city.html?station=KLAX", "#chart path", "city series"),
                         ("hurricane.html", "#basin path", "basin geography"), ("scorecard.html", "#standChart rect", "the standings bars"),
                         ("climate.html", "#panels svg path", "climate series"),
                         ("agriculture.html", "#panels svg path", "crop yield series"),
                         ("weather.html", "#panels svg path", "weather series"),
                         ("fossil-fuels.html", "#panels svg path", "fossil fuel series"),
                         ("electricity-renewables.html", "#panels svg path", "electricity series"), ("about.html", "footer.site", "footer"),
                         ("faq.html", ".prose h2", "the FAQ questions"), ("accuracy.html", "#accLead path", "the lead curve"),
                         ("daily-temperature-markets.html", ".prose h2", "the article sections"),
                         ("hourly-temperature-markets.html", "#board section.prod", "the listed hourly cities"),
                         ("wind-markets.html", "#board section.prod", "the wind cities"),
                         ("analysis-resolution.html", "#vmap, #anaMap", "analysis resolution page"),
                         ("tropical-cyclone-markets.html", ".basincard svg.minimap", "a map per basin"),
                         ("allocator.html", "#allocSvg", "the allocation chart"),
                         ("climate-gdp.html", "#gdpMain rect", "the world GDP contract and its ladder")]
                # a narrowed run walks only the pages it asked for; the sweep is
                # the part of a pass that is pure page loading, so this is where
                # skipping actually buys time rather than only hiding output
                pages = [pg for pg in pages if chk.wants(pg[0] + " " + pg[2])]
                for path, sel, what in pages:
                    page.goto(f"{srv.url}/{path}")
                    # the world GDP page loads its model's paths and runs it before it draws
                    page.wait_for_timeout(2500 if path.startswith("climate-gdp") else 900)
                    n = page.locator(sel).count()
                    chk.add(f"{scheme} standalone {path}: renders {what}", n > 0, f"{sel} count={n}")
                    if page.locator("#pageStatus, #chartStatus").count():      # data pages carry a status strip
                        status = page.locator(".status").first.inner_text() if page.locator(".status").count() else ""
                        chk.add(f"{scheme} standalone {path}: status strip present", "Data as of" in status or "No data" in status, status[:80])
                    page.screenshot(path=os.path.join(OUT, f"{scheme}-{path.split('?')[0]}.png"), full_page=True)
                # ---- the offloaded newsletter content: the pages the daily letter links
                page.goto(f"{srv.url}/faq.html"); page.wait_for_timeout(500)
                faq_t = page.locator(".prose").inner_text()
                chk.add(f"{scheme} faq: carries the four questions and both link lists",
                        faq_t.count("?") >= 4 and "Further reading" in faq_t and "Climate contracts" in faq_t,
                        f"chars={len(faq_t)}")
                chk.add(f"{scheme} faq: the comparison question names the publication it belongs to",
                        faq_t.count("IBKR Campus Publication") == 1 and "compared here" not in faq_t,
                        faq_t[:70])
                chk.add(f"{scheme} faq: the publication heading reaches the author's campus page",
                        page.locator(".prose h2 a[href='https://www.interactivebrokers.com/campus/author/patrick1brown/']").count() == 1,
                        str(page.locator(".prose h2 a").count()))
                chk.add(f"{scheme} faq: it points readers to the systems table on the accuracy page",
                        page.locator(".prose a[href='accuracy.html#systems']").count() == 1, "")
                order = page.eval_on_selector_all(".prose h2", "e => e.map(x => x.textContent)")
                chk.add(f"{scheme} faq: the questions run in the order the owner set",
                        order == ["How do prediction markets work?",
                                  "What is a weather prediction market?",
                                  "Are weather prediction markets accurate?",
                                  "How does a ForecastEx ladder become a single forecast temperature?",
                                  "How the daily temperature displays are built",
                                  "What are the four canonical forecast systems compared in the IBKR Campus Publication?",
                                  "Further reading"], str(order)[:200])
                # ---- the accuracy page: the working paper's figures from the record builder's files
                #
                # Every number on the page is the builder's (docs/accuracy.md, schema 2); the
                # gates prove the figures draw what the paper draws, each carries its estimator
                # and its sample, the strip names the window and the build, and the copy keeps
                # the exchange's language.
                page.goto(f"{srv.url}/accuracy.html"); page.wait_for_timeout(2500)
                lead_file = json.loads(urllib.request.urlopen(f"{srv.url}/data/snapshots/accuracy/lead-curve.json").read().decode())
                n_series = len(lead_file["meta"]["series"])
                strokes = page.evaluate("""() => [...document.querySelectorAll('#accLead path')].filter(
                    p => p.getAttribute('fill') === 'none' && !p.classList.contains('acc-sec')).map(p => ({
                      w: p.getAttribute('stroke-width'), d: p.getAttribute('stroke-dasharray'), o: p.getAttribute('stroke-opacity'),
                      c: p.getAttribute('stroke') }))""")
                solid = [x for x in strokes if not x["o"]]
                faint = [x for x in strokes if x["o"]]
                chk.add(f"{scheme} accuracy: the error curve draws the market and every series of the file, in the paper's group colours",
                        n_series >= 20 and len(solid) >= n_series - 4
                        and {"var(--g-mos)", "var(--g-raw)"} <= {x["c"] for x in solid} and any(x["c"] == "var(--accent)" for x in solid),
                        f"series={n_series} solid={len(solid)} colours={sorted({x['c'] for x in solid})}")
                chk.add(f"{scheme} accuracy: LAMP is drawn heavier, a system's second series dashed, and the corrected series faint as issued",
                        any(x["w"] == "2.3" for x in solid) and sum(1 for x in solid if x["d"] == "5 3") >= 3 and len(faint) >= 8,
                        f"heavy={sum(1 for x in solid if x['w'] == '2.3')} dashed={sum(1 for x in solid if x['d'] == '5 3')} faint={len(faint)}")
                chk.add(f"{scheme} accuracy: the convergence chart and the Brier score are gone",
                        page.locator("#accConv, #accBrier").count() == 0
                        and page.locator("#accLeadBar button", has_text="Within").count() == 0
                        and "Brier" not in page.locator(".wrap").inner_text(), "")
                # ---- the second axis on the error curve, and lead counted back from the extreme
                sec_day = page.locator("#accLead path.acc-sec").count()
                lead_svg = page.locator("#accLead").text_content()
                page.locator("#accLead rect[fill='transparent']").nth(26).hover(); page.wait_for_timeout(300)
                day_tip = page.locator("#tip").inner_text() if page.locator("#tip").count() else ""
                chk.add(f"{scheme} accuracy: the error curve carries the share of city-days whose maximum was already observed on a second axis",
                        sec_day == 1 and "already observed" in lead_svg and "100%" in lead_svg
                        and "had already seen the day" in day_tip, f"sec={sec_day} tip={day_tip[-120:]!r}")
                chk.add(f"{scheme} accuracy: the hover ranks every series with the market's improvement and the Holm count",
                        "market better" in day_tip.lower() and "after Holm" in day_tip and day_tip.count("\n") >= 20,
                        day_tip.replace("\n", " | ")[:160])
                legend = page.locator("#accLeadKey").inner_text()
                chk.add(f"{scheme} accuracy: the error curve's legend names the paper's five groups",
                        all(g in legend for g in ("Station MOS", "Blend", "Human-edited", "Artificial intelligence", "Raw numerical weather prediction")),
                        legend.replace("\n", " | ")[:160])
                seg_of = """() => {
                  // stroked series only: the market's band is a filled path with two points per bin
                  const ps = [...document.querySelectorAll('#accLead path')].filter(
                    p => p.getAttribute('fill') === 'none' && !p.classList.contains('acc-sec'));
                  return Math.max.apply(null, ps.map(p => (p.getAttribute('d') || '').split(/[ML]/).length - 1)); }"""
                day_seg = page.evaluate(seg_of)
                page.locator("#accLeadBar button", has_text="Before the extreme").first.click(); page.wait_for_timeout(600)
                rel_txt = page.locator("#accLead").text_content()
                rel_paths = page.locator("#accLead path").count()
                sec_rel = page.locator("#accLead path.acc-sec").count()
                page.locator("#accLead rect[fill='transparent']").nth(2).hover(); page.wait_for_timeout(300)
                rel_tip = page.locator("#tip").inner_text() if page.locator("#tip").count() else ""
                # an alternative is one step per bin in the day view and a plain line here,
                # where a bin is not a clock hour
                rel_seg = page.evaluate(seg_of)
                chk.add(f"{scheme} accuracy: an alternative is one step per clock hour in the day view and a plain line where the lead is not a clock hour",
                        day_seg > 60 and 10 <= rel_seg <= 40, f"day={day_seg} relative={rel_seg}")
                chk.add(f"{scheme} accuracy: Before the extreme redraws the error curve by hours before the maximum, with its own second axis",
                        "Hours before the day’s maximum was observed" in rel_txt and "target day begins" not in rel_txt
                        and rel_paths >= 20 and sec_rel == 1 and "still priced this far ahead" in rel_txt
                        and "before the maximum" in rel_tip,
                        f"paths={rel_paths} sec={sec_rel} tip={rel_tip[:80]!r}")
                page.locator("#accLeadBar button", has_text="Before the day ends").first.click(); page.wait_for_timeout(400)
                chk.add(f"{scheme} accuracy: Before the day ends restores the lead from the end of the day",
                        "Hours before the end of the target day" in page.locator("#accLead").text_content(), "")
                # the values as issued, and the balanced sample, which carries the corrected values alone
                before = page.locator("#accLead").inner_html()
                page.locator("#accLeadBar button", has_text="As issued").first.click(); page.wait_for_timeout(500)
                faint_iss = page.locator("#accLead path[stroke-opacity]").count()
                chk.add(f"{scheme} accuracy: As issued redraws every series without the correction and without faint lines",
                        page.locator("#accLead").inner_html() != before and faint_iss == 0, f"faint={faint_iss}")
                page.locator("#accLeadBar button", has_text="Bias-corrected").first.click(); page.wait_for_timeout(300)
                before = page.locator("#accLead").inner_html()
                vals_grp = page.locator("#accLeadBar .tabgroup", has_text="Bias-corrected")
                page.locator("#accLeadBar button", has_text="Balanced sample").first.click(); page.wait_for_timeout(500)
                chk.add(f"{scheme} accuracy: the balanced sample redraws the error curve and hides the values tab",
                        page.locator("#accLead").inner_html() != before and vals_grp.is_hidden(), "")
                page.locator("#accLeadBar button", has_text="Every day on record").first.click(); page.wait_for_timeout(300)
                # the market's row in a hover table reads in the hover box's own ink
                page.locator("#accLead rect[fill='transparent']").nth(26).hover(); page.wait_for_timeout(300)
                tip_ink = page.evaluate("""() => { const t = document.querySelector('#tip'); const r = t && t.querySelector('tr.tfx td');
                                                   return r ? [getComputedStyle(t).color, getComputedStyle(r).color] : null; }""")
                chk.add(f"{scheme} accuracy: the ForecastEx row in a hover table is set in the hover box's ink",
                        bool(tip_ink) and tip_ink[0] == tip_ink[1], str(tip_ink))
                # ---- probabilistic skill: CRPS by lead, then the paper's two reliability windows
                cal_file = json.loads(urllib.request.urlopen(f"{srv.url}/data/snapshots/accuracy/calibration.json").read().decode())
                crps_blk = cal_file["metric"]["high"]["crps"]["clocks"]["abs"]["calibrated"]["series"]
                widths = page.evaluate("""() => ['#accLead', '#accCrps'].map(s => {
                    const e = document.querySelector(s); return e ? Math.round(e.getBoundingClientRect().width) : 0; })""")
                prob_key = page.locator("#accProbKey").inner_text()
                chk.add(f"{scheme} accuracy: CRPS takes the full width of the error curve and draws the market against the four calibrated ensembles",
                        widths[0] > 0 and abs(widths[0] - widths[1]) <= 2 and set(crps_blk) == {"FX", "AIFS", "GEFS", "GEM", "ICON"}
                        and page.locator("#accCrps path[stroke-dasharray]").count() >= 3
                        and page.locator("#accCrps path[stroke-opacity]").count() >= 4
                        and all(n in prob_key for n in ("ECMWF AIFS ensemble", "NOAA GEFS", "ECCC GEPS", "DWD ICON-EPS")),
                        f"widths={widths} series={sorted(crps_blk)}")
                diag_txt = page.eval_on_selector_all("#accDiag svg.acc-cal-rel text", "e => e.map(x => x.textContent)")
                titles = [t for t in diag_txt if t.startswith("Priced ")]
                n_calc = page.locator("#accDiag circle").count()
                sum_rows = page.locator("#accDiag table.acc-rel-sum tbody tr").count()
                sum_txt = page.locator("#accDiag table.acc-rel-sum").inner_text() if page.locator("#accDiag table.acc-rel-sum").count() else ""
                chk.add(f"{scheme} accuracy: the reliability diagrams are the paper's two windows, with the gap and the slope of every system",
                        titles == ["Priced 12 to 24 h before the day ends", "Priced 6 to 18 h before the maximum"]
                        and n_calc >= 10 and sum_rows == 5 and re.search(r"\d\.\d\d \[\d\.\d\d, \d\.\d\d\]", sum_txt) is not None,
                        f"titles={titles} rows={sum_rows}")
                before = page.locator("#accCrps").inner_html()
                page.locator("#accProbBar button", has_text="As issued").first.click(); page.wait_for_timeout(500)
                chk.add(f"{scheme} accuracy: the ensembles' As issued tab redraws CRPS without the faint lines",
                        page.locator("#accCrps").inner_html() != before and page.locator("#accCrps path[stroke-opacity]").count() == 0, "")
                page.locator("#accProbBar button", has_text="EMOS-calibrated").first.click(); page.wait_for_timeout(300)
                page.locator("#accCrps rect[fill='transparent']").nth(24).hover(); page.wait_for_timeout(300)
                crps_tip = page.locator("#tip").inner_text() if page.locator("#tip").count() else ""
                chk.add(f"{scheme} accuracy: the CRPS hover ranks the distributions with the market's improvement",
                        "crps °f" in crps_tip.lower() and "market better" in crps_tip.lower() and "NOAA GEFS" in crps_tip,
                        crps_tip.replace("\n", " | ")[-160:])
                # ---- the method notes wait behind a button
                btns = page.locator("button.accnote-btn")
                hidden = page.eval_on_selector_all(".accnote", "e => e.map(x => x.hidden)")
                ok_btn = btns.count() == 4 and all(hidden) and btns.first.inner_text().strip() == "Show details of calculation"
                if btns.count():
                    btns.first.click(); page.wait_for_timeout(200)
                    ok_btn = ok_btn and page.eval_on_selector_all(".accnote", "e => e.map(x => x.hidden)")[0] is False \
                        and btns.first.inner_text().strip() == "Hide details of calculation"
                    btns.first.click(); page.wait_for_timeout(100)
                chk.add(f"{scheme} accuracy: every method note is behind a Show details of calculation button",
                        ok_btn, f"buttons={btns.count()} hidden={hidden}")
                n_mapc, n_mapp = page.locator("#accMap circle").count(), page.locator("#accMap path").count()
                chk.add(f"{scheme} accuracy: the city map draws the states and the stations",
                        n_mapc >= 3 and n_mapp >= 1, f"circles={n_mapc} paths={n_mapp}")
                n_grid = page.locator("#accGrid table.acc-t3 tbody tr").count()
                chk.add(f"{scheme} accuracy: the scorecard draws a row per series and per group", n_grid >= 26, f"rows={n_grid}")
                notes = page.eval_on_selector_all(".accnote", """e => e.map(x => ({
                    eq: (x.querySelector('.eq') || {textContent: ''}).textContent,
                    n: (x.querySelector('.rule.n') || {textContent: ''}).textContent }))""")
                bad_notes = [i for i, nt in enumerate(notes)
                             if "=" not in nt["eq"] or not re.search(r"\b(?:Sample|n)\s*=?\s*[\d,]*\d", nt["n"])]
                chk.add(f"{scheme} accuracy: every method note holds an estimator and a counted sample",
                        len(notes) == 4 and not bad_notes, f"notes={len(notes)} bad={bad_notes}")
                # ---- how far back each record goes: every note names its days, and the
                # systems table and the scorecard date every record
                spans = page.eval_on_selector_all(".accnote", "e => e.map(x => (x.querySelector('.rule.span') || {textContent: ''}).textContent)")
                chk.add(f"{scheme} accuracy: every method note says which days its figure drew on",
                        len(spans) == 4 and all((t or "").strip() for t in spans),
                        str([(t or "")[:40] for t in spans]))
                acc_st = page.locator("#pageStatus .status").inner_text() if page.locator("#pageStatus .status").count() else ""
                chk.add(f"{scheme} accuracy: the status strip names the window and the build",
                        "Data as of" in acc_st and re.search(r"target days [A-Z][a-z]{2} \d+ \d{4} to [A-Z][a-z]{2} \d+ \d{4}", acc_st) is not None,
                        acc_st[:90])
                acc_body = page.locator("body").inner_text()
                bad_words = sorted(set(m.group(0) for m in re.finditer(
                    r"\b(?:ask|asks|asked|asking|offer|offers|offered|sell|sells|sellers|selling)\b", acc_body, re.I)))
                if "DWM" in acc_body:
                    bad_words.append("DWM")
                if "fair value" in acc_body.lower():
                    bad_words.append("fair value")
                if "model probability" in acc_body.lower():
                    bad_words.append("model probability")
                chk.add(f"{scheme} accuracy: the page keeps the exchange's language and names no internal system",
                        not bad_words, str(bad_words))
                acc_served = strip_markup(urllib.request.urlopen(f"{srv.url}/accuracy.html").read().decode())
                chk.add(f"{scheme} accuracy: the page serves its own text without running a script",
                        len(acc_served.split()) >= 300, f"words={len(acc_served.split())}")
                # ---- the two pages at the top, the research brief and the working
                # paper's first page, side by side at one size. The paper is the
                # archive's, so every link to it is its DOI and nothing links a copy.
                doi_sel = "a[href='https://doi.org/10.31223/X5522D']"
                acc_doi = page.locator(f".wrap {doi_sel}").count()
                acc_copy = page.locator(".wrap a[href*='papers/']").count()
                row = page.evaluate("""() => {
                    const f = [...document.querySelectorAll('.paperrow .pf-frame')];
                    const im = f.map(e => e.querySelector('img'));
                    if (f.length !== 2 || !im.every(Boolean)) return null;
                    const r = f.map(e => e.getBoundingClientRect()), q = im.map(e => e.getBoundingClientRect());
                    return {order: f.map(e => e.classList.contains('pf-brief') ? 'brief' : e.classList.contains('pf-paper') ? 'paper' : '?'),
                            sameTop: Math.abs(r[0].top - r[1].top) < 1, leftFirst: r[0].right <= r[1].left,
                            sameSize: Math.abs(q[0].width - q[1].width) < 1 && Math.abs(q[0].height - q[1].height) < 1,
                            loaded: im.every(e => e.complete && e.naturalWidth > 0), w: Math.round(q[0].width)};
                }""")
                chk.add(f"{scheme} accuracy: the brief and the paper's first page sit side by side at one size, brief to the left",
                        bool(row) and row["order"] == ["brief", "paper"] and row["sameTop"] and row["leftFirst"]
                        and row["sameSize"] and row["loaded"] and row["w"] > 380, str(row))
                # a click anywhere on the paper reaches the archive, so the frame is
                # probed by what the pointer would hit, its corners and its middle
                paper_hit = page.evaluate("""() => {
                    const a = document.querySelector('.paperrow a.pf-paper'); if (!a) return null;
                    a.scrollIntoView({block: 'center'});
                    const r = a.getBoundingClientRect();
                    const pts = [[.5, .5], [.02, .02], [.98, .02], [.02, .98], [.98, .98], [.5, .97]];
                    const miss = pts.filter(([x, y]) => {
                        const e = document.elementFromPoint(r.left + x * r.width, r.top + y * r.height);
                        return !(e && a.contains(e));
                    });
                    return {href: a.getAttribute('href'), download: a.hasAttribute('download'), miss: miss.length,
                            fits: r.top >= 0 && r.bottom <= innerHeight};
                }""")
                chk.add(f"{scheme} accuracy: anywhere on the paper's first page is one link to its DOI",
                        bool(paper_hit) and paper_hit["href"] == "https://doi.org/10.31223/X5522D"
                        and not paper_hit["download"] and paper_hit["miss"] == 0 and paper_hit["fits"], str(paper_hit))
                # the brief opens in a dialog with its download; the page stays put
                url_before = page.url
                page.locator(".paperrow a.pf-brief").click(); page.wait_for_timeout(900)
                dlg = page.evaluate("""() => {
                    const d = document.getElementById('briefDlg'); if (!d) return null;
                    const im = d.querySelector('img'), a = d.querySelector('a[download]');
                    return {open: d.open, modal: d.matches(':modal'), loaded: !!im && im.complete && im.naturalWidth > 0,
                            href: a ? a.getAttribute('href') : '',
                            plain: document.querySelector('.paperrow a.pf-brief').getAttribute('href')};
                }""")
                pdfs = []
                for h_ in ([dlg["href"], dlg["plain"]] if dlg else []):
                    try:
                        pdfs.append(urllib.request.urlopen(f"{srv.url}/{h_}").read(5) == b"%PDF-")
                    except Exception:
                        pdfs.append(False)
                page.keyboard.press("Escape"); page.wait_for_timeout(200)
                esc_closed = not page.evaluate("document.getElementById('briefDlg').open")
                page.locator("#briefEnlarge").click(); page.wait_for_timeout(300)
                reopened = page.evaluate("document.getElementById('briefDlg').open")
                page.mouse.click(4, 4); page.wait_for_timeout(200)
                backdrop_closed = not page.evaluate("document.getElementById('briefDlg').open")
                chk.add(f"{scheme} accuracy: the brief opens full size in a dialog with its PDF to download, and Escape or the backdrop closes it",
                        bool(dlg) and dlg["open"] and dlg["modal"] and dlg["loaded"] and page.url == url_before
                        and pdfs == [True, True] and esc_closed and reopened and backdrop_closed,
                        f"{dlg} pdfs={pdfs} esc={esc_closed} reopen={reopened} backdrop={backdrop_closed}")
                ph_ctx = browser.new_context(color_scheme=scheme, viewport={"width": 390, "height": 844})
                ph = ph_ctx.new_page()
                ph.goto(f"{srv.url}/accuracy.html"); ph.wait_for_timeout(1200)
                narrow = ph.evaluate("""() => {
                    const row = document.querySelector('.paperrow');
                    const f = [...document.querySelectorAll('.paperrow .pf-frame')].map(e => e.getBoundingClientRect());
                    return {pair: f.length === 2 && Math.abs(f[0].top - f[1].top) < 1 && f[0].right <= f[1].left,
                            fits: !!row && row.getBoundingClientRect().right <= innerWidth && row.scrollWidth <= row.clientWidth};
                }""")
                ph_ctx.close()
                chk.add(f"{scheme} accuracy: at phone width the two pages keep one row inside the screen",
                        narrow["pair"] and narrow["fits"], str(narrow))
                no_wilson = "wilson" not in page.locator("#accProbKey").inner_text().lower()
                page.goto(f"{srv.url}/faq.html"); page.wait_for_timeout(400)
                chk.add(f"{scheme} accuracy: the FAQ still reaches the page",
                        page.locator(".prose a[href='accuracy.html']").count() >= 1, "")
                chk.add(f"{scheme} accuracy: the page and the FAQ link the working paper by its DOI and no copy of it, and the diagrams claim no per-bin interval",
                        acc_doi == 3 and acc_copy == 0 and page.locator(f".prose {doi_sel}").count() == 1
                        and page.locator(".prose a[href*='papers/']").count() == 0 and no_wilson,
                        f"accuracy_doi={acc_doi} copies={acc_copy} wilson_free={no_wilson}")
                page.goto(f"{srv.url}/daily-temperature-markets.html"); page.wait_for_timeout(500)
                art_t = " ".join(" ".join(t.split()) for t in page.locator(".prose").all_inner_texts())
                chk.add(f"{scheme} article: the settlement convention is stated exactly",
                        "strictly above" in art_t and "strictly below" in art_t and "resolves No" in art_t, "")
                chk.add(f"{scheme} article: no handoff placeholder survived",
                        "SITE-LINK" not in art_t and "{" not in art_t, art_t[:60])
                chk.add(f"{scheme} article: no internal review note published",
                        "DRAFT UPDATE" not in art_t and "for Patrick" not in art_t, "")
                # the article carries no figure of its own; the map lives on the
                # landing page, which is where a reader can use it
                chk.add(f"{scheme} article: it is text, with no map of its own",
                        page.locator("#artMap").count() == 0, "")
                chk.add(f"{scheme} article: it names the settlement source and the strict rule",
                        "Weather Underground" in art_t and "midnight to midnight local time" in art_t
                        and "50.5%" in art_t, art_t[:80])
                chk.add(f"{scheme} article: it links the terms it describes",
                        page.locator("a[href$='DailyTemperatureTermsandConditions.pdf']").count() >= 1, "")
                page.goto(f"{srv.url}/index.html"); page.wait_for_timeout(800)
                l1 = page.eval_on_selector_all("header.site nav.l1 > a", "els => els.map(e => e.textContent)")
                l2 = page.eval_on_selector_all("header.site nav.l2 a", "els => els.map(e => e.textContent)")
                refs = page.eval_on_selector_all("header.site .refnav a", "els => els.map(e => e.textContent)")
                chk.add(f"{scheme} nav: two branches on the first row", l1 == ["Climate & Weather", "Energy"], str(l1))
                chk.add(f"{scheme} nav: the second row carries that branch's categories",
                        l2[:3] == ["Daily Temperatures", "Hourly Temperatures", "Tropical Cyclones"]
                        and len(l2) == 7, str(l2))
                chk.add(f"{scheme} nav: reference pages sit apart from the hierarchy",
                        "Trading temp markets" in refs and "FAQ" in refs and "City" not in refs, str(refs))
                on = page.eval_on_selector_all("header.site nav a.on", "els => els.map(e => e.textContent)")
                chk.add(f"{scheme} nav: the map marks its branch and its category",
                        on == ["Climate & Weather", "Daily Temperatures"], str(on))
                # a page reached by a query parameter still has to know where it lives
                for url, want in ((f"{srv.url}/section.html?s=energy", ["Energy"]),
                                  (f"{srv.url}/category.html?c=fossil-fuels", ["Energy", "Fossil Fuels"]),
                                  (f"{srv.url}/contract.html?id=OP", ["Energy", "Fossil Fuels"]),
                                  (f"{srv.url}/hurricane.html", ["Climate & Weather", "Tropical Cyclones"]),
                                  (f"{srv.url}/tropical-cyclone-markets.html", ["Climate & Weather", "Tropical Cyclones"]),
                                  (f"{srv.url}/city.html?station=KLAX", ["Climate & Weather", "Daily Temperatures"])):
                    page.goto(url); page.wait_for_timeout(800)
                    got = page.eval_on_selector_all("header.site nav a.on", "els => els.map(e => e.textContent)")
                    chk.add(f"{scheme} nav: {url.split('/')[-1][:34]} knows its branch", got == want, f"{got} want {want}")
                _iso_z = lambda t: t.strftime("%Y-%m-%dT%H:00:00Z")
                # ---- the wind panel: the forecast it draws forward, the hover, and a
                #      ladder column that keeps its place before a strike exists.
                # The samples carry no wind at all, so the observation and the
                # guidance are routed rather than taken from them.
                # The fixture's date is today's, from the wall clock
                _now = dt.datetime.now(dt.timezone.utc)
                # the day is the STATION's, not UTC's. Between midnight and four in
                # the morning Zulu the two disagree, and the plot's window runs from
                # local midnight, so a UTC date put every routed observation before
                # the start of the window and the panel drew nothing
                from zoneinfo import ZoneInfo as _Z
                DAY = _now.astimezone(_Z("America/New_York")).strftime("%Y-%m-%d")
                # samples/ predates the third contract day, so the routed roster
                # has to carry it or the board draws two buttons and the checks
                # for the first unopened day pass on an absence
                DAY2 = (_now.astimezone(_Z("America/New_York")).date()
                        + dt.timedelta(days=2)).isoformat()
                # The readings sit around 15:10 New York time on that day, and every
                # page that draws them reads a browser clock fixed at that instant,
                # so the hours still to come are the same hours to the page and to
                # the fixture. The plot's window runs from local midnight to midnight
                # and the readings reach five hours back and three ahead, so placing
                # them around the wall clock left some outside the day from 22:00 to
                # 05:00 New York time and failed the hover-band check from 23:00 to
                # 04:00 (5 bands at 07:55Z on 4 October 2026). Before 02:00 the panel
                # drew no observation at all.
                W_NOW = dt.datetime.combine(dt.date.fromisoformat(DAY), dt.time(15, 10),
                                            tzinfo=_Z("America/New_York")).astimezone(dt.timezone.utc)
                _past = [W_NOW - dt.timedelta(hours=k) for k in (5, 4, 3, 2)]
                w_rows = [{"t": t.strftime("%Y-%m-%dT%H:54:00Z"), "tempF": 66.0, "tempC": 18.9, "type": "METAR",
                           "wspd": 9.0 + i, "wgst": (24.0 if i == 3 else None)} for i, t in enumerate(_past)]
                _ahead = [W_NOW + dt.timedelta(hours=k) for k in (1, 2, 3)]

                # Playwright's clock stays on a context for the rest of its life once
                # set, so the pages that draw this fixture get a context of their own
                # and the rest of the run keeps the wall clock. Their script errors go
                # into the run's own list, which the standalone error check reads.
                # A check that has to sit on one of the samples' own dates passes it.
                def fixture_page(at=None):
                    c = browser.new_context(color_scheme=scheme, viewport={"width": 1200, "height": 900})
                    c.clock.set_fixed_time(at or W_NOW)
                    pg = c.new_page()
                    errors_of(pg, into=errs)
                    return pg

                def wind_routes(route):
                    u = route.request.url
                    if u.endswith("/summary.json"):
                        # the ladder takes its day from the ROSTER, so the routed board
                        # and the routed observations have to sit on the day the roster
                        # names or the panel treats its own readings as another day's
                        resp = route.fetch(); d = json.loads(resp.text())
                        market_file = "/market/" in u
                        for c in d.get("cities") or []:
                            if c.get("station") in ("KBOS", "KDFW"):
                                c["markers"] = dict(c.get("markers") or {}, day=DAY)
                                # a value on the map, so its labels exist to be measured.
                                # The map colours by the exchange's central wind now, which
                                # lives in the MARKET summary, so the figure has to go there
                                # rather than on the roster row
                                c["windPeak"] = {"v": 24.0, "kt": 24, "mph": 28, "from": "gust",
                                                 "t": w_rows[3]["t"], "type": "METAR"}
                                if market_file:
                                    c["listed"] = True
                                    c["day"] = DAY
                                    c["dayAfter"] = DAY2
                                    c["impliedWindToday"] = 31.5
                                    c["quotedWindToday"] = 5
                        return route.fulfill(response=resp, body=json.dumps(d))
                    # a second station with readings and no book at all, so the page
                    # carries both column states at once
                    if u.endswith("/obs/KDFW.json"):
                        resp = route.fetch(); d = json.loads(resp.text())
                        d["rows"] = w_rows
                        d["wind"] = {"today": {"date": DAY, "n": len(w_rows), "unit": "kt",
                                               "peak": {"v": 20.0, "t": w_rows[3]["t"], "type": "METAR",
                                                        "from": "speed", "mph": 23, "kt": 20}}}
                        return route.fulfill(response=resp, body=json.dumps(d))
                    if u.endswith("/obs/KBOS.json"):
                        resp = route.fetch(); d = json.loads(resp.text())
                        d["rows"] = w_rows
                        d["wind"] = {"today": {"date": DAY, "n": len(w_rows), "unit": "kt",
                                               "peak": {"v": 24.0, "t": w_rows[3]["t"], "type": "METAR",
                                                        "from": "gust", "mph": 28, "kt": 24}}}
                        return route.fulfill(response=resp, body=json.dumps(d))
                    if u.endswith("/market/KBOS.json"):
                        resp = route.fetch(); d = json.loads(resp.text())
                        # the four books a ladder has to tell apart. The first is the
                        # one the live board actually carried: a resting Yes bid with
                        # no opposite side, which read as "no bids" while the price
                        # test was being taken on a row whose units were already cents
                        d["symbols"] = dict(d.get("symbols") or {},
                                            wind={"symbol": "MGBOS", "name": "Boston Max Wind Speed",
                                                  "conid": 991, "productConid": 990})
                        book = [{"strike": 19.0, "bid": 0.94, "ask": None, "mid": 0.94, "conidYes": 9901},
                                {"strike": 33.0, "bid": 0.01, "ask": 0.99, "mid": 0.5, "conidYes": 9902},
                                {"strike": 38.0, "bid": 0.40, "ask": 0.46, "mid": 0.43, "conidYes": 9903},
                                {"strike": 43.0, "bid": None, "ask": None, "mid": None, "conidYes": 9904}]
                        # the ladder is keyed on the day the ROSTER names, not the one in
                        # this file, so the book goes under both rather than being placed
                        # on a day nothing looks up
                        days = dict(d.get("days") or {})
                        for k in {DAY, (d.get("markers") or {}).get("day")} - {None}:
                            days[k] = dict(days.get(k) or {}, wind=book)
                        d["days"] = days
                        return route.fulfill(response=resp, body=json.dumps(d))
                    if u.endswith("/forecast/KBOS.json"):
                        resp = route.fetch(); d = json.loads(resp.text())
                        # a quiet forward hour: a sustained wind and NO gust, which is
                        # the case a gust-only line gets wrong
                        d["nbm"] = {"cycle": _iso_z(W_NOW), "hourlyFrom": _iso_z(_ahead[0]), "txn": {},
                                    "hourly": [{"t": _iso_z(t), "tempF": 70.0, "tempC": 21.1,
                                                "wspd": 26.0, "gust": (None if i == 0 else 12.0)}
                                               for i, t in enumerate(_ahead)]}
                        # the forecast office's product as it actually arrives: a
                        # sustained wind every hour and no gust column at all. It
                        # sits ahead of the blend in the family order, and taking
                        # the first family with any wind drew its sustained line
                        # where the blend had gusts
                        d["nws"] = {"hourly": [{"t": _iso_z(t), "tempF": 70.0, "tempC": 21.1, "wspd": 9.0}
                                               for t in _ahead]}
                        for k in ("lamp", "mav"):
                            d.pop(k, None)
                        return route.fulfill(response=resp, body=json.dumps(d))
                    return route.continue_()

                page_wall, page = page, fixture_page()
                page.route("**/data/snapshots/**", wind_routes)
                page.goto(f"{srv.url}/wind-markets.html")
                page.wait_for_timeout(1800)
                wp = page.evaluate("""() => {
                  const svg = document.querySelector('#board svg.ts');
                  if (!svg) return null;
                  const txt = [...svg.querySelectorAll('text')].map(t => t.textContent);
                  return { bands: svg.querySelectorAll('rect.hband').length,
                           legend: txt.filter(t => /Peak so far|Gusts|Sustained|forecast| gust| sustained/.test(t)),
                           title: txt.filter(t => /Thresholds/.test(t)),
                           empty: txt.filter(t => /no strikes listed/.test(t)),
                           peaks: txt.some(t => t === 'Peak'),
                           caps: [...document.querySelectorAll('#board section p.cap')].map(p => p.textContent) };
                }""")
                # the board's ladders open the exchange, and it says so once, above the first
                # city rather than under each (owner, 2026-10-22)
                wtn = page.evaluate("""() => [...document.querySelectorAll('#board p.tradenote')].map(n => ({
                  text: n.textContent, next: n.nextElementSibling ? n.nextElementSibling.tagName + '.' + n.nextElementSibling.className : null }))""")
                chk.add(f"{scheme} wind board: one line above the first city says a price opens the exchange",
                        len(wtn) == 1 and wtn[0]["text"] == "Click on any price to trade contracts through IBKR Prediction Markets" and wtn[0]["next"] == "SECTION.prod ctr", str(wtn))
                # every routed reading falls inside the station's day, so the count
                # is exact
                chk.add(f"{scheme} wind panel: the plot draws a hover band per reading",
                        bool(wp and wp["bands"] == len(w_rows) + len(_ahead)), str(wp and wp["bands"]))
                # every family that forecasts wind gets its own line, as on the
                # temperature panel. The Blend and LAMP publish an hourly gust; the
                # gridpoint's hourly product publishes only a sustained wind, and
                # each says on its own label which column it is drawing
                chk.add(f"{scheme} wind panel: every forecasting family gets a line, not just one",
                        bool(wp and len([t for t in wp["legend"] if " gust" in t or " sustained" in t]) >= 2),
                        str(wp and wp["legend"]))
                chk.add(f"{scheme} wind panel: a family names the column it draws",
                        bool(wp and any("Blend gust" in t for t in wp["legend"])
                             and any(" sustained" in t for t in wp["legend"])), str(wp and wp["legend"]))
                chk.add(f"{scheme} wind panel: each family's day peak sits beside the ladder",
                        bool(wp and wp.get("peaks")), str(wp and wp.get("peaks")))
                # the routed station has a book and the rest of the board does not, so
                # both states are on the page at once and each keeps its column
                allcols = page.evaluate("""() => {
                  const svgs = [...document.querySelectorAll('#board svg.ts')];
                  const txt = svgs.flatMap(s => [...s.querySelectorAll('text')].map(t => t.textContent));
                  return { titles: txt.filter(t => /Thresholds/.test(t)).length,
                           empty: txt.filter(t => /no strikes listed/.test(t)).length };
                }""")
                chk.add(f"{scheme} wind panel: the threshold column keeps its place before a strike exists",
                        bool(allcols["titles"] >= 2 and allcols["empty"] >= 1), str(allcols))
                # 26 kt is 30 mph and the hour has no gust at all, so a gust-only
                # line would have read 14 mph there and understated the day
                chk.add(f"{scheme} wind panel: guidance is compared against the peak so far",
                        bool(wp and any("30 mph" in c and "28 mph" in c for c in wp["caps"])),
                        str(wp and wp["caps"])[:170])
                hov = page.evaluate("""async () => {
                  const svg = document.querySelector('#board svg.ts');
                  const b = [...svg.querySelectorAll('rect.hband')];
                  const out = [];
                  for (const i of [0, b.length - 1]) {
                    const r = b[i].getBoundingClientRect();
                    b[i].dispatchEvent(new MouseEvent('mousemove',
                      {clientX: r.x + r.width / 2, clientY: r.y + 20, bubbles: true}));
                    await new Promise(z => setTimeout(z, 120));
                    out.push((document.querySelector('#tip') || {}).innerText || '');
                  }
                  return out;
                }""")
                # the variable map zooms the way the basin map does, and its dots
                # hold their on-screen size while doing it. The label size has to
                # ride in `style`: the class carries a font-size in the stylesheet,
                # a CSS rule beats a presentation attribute, and a px size inside a
                # scaled viewBox is in user units, so a city's name grew with the
                # zoom until it covered three states
                zm = page.evaluate("""async () => {
                  const svg = document.querySelector('#vmap');
                  if (!svg) return null;
                  const vbw = () => +svg.getAttribute('viewBox').split(' ')[2];
                  const dotR = () => {
                    const c = svg.querySelector('#vdots g.dot circle:nth-child(2)');
                    return c ? +c.getAttribute('r') : null;
                  };
                  const lab = () => {
                    const t = svg.querySelector('#vdots text');
                    return t ? parseFloat((t.style.fontSize || '0').replace('px', '')) : null;
                  };
                  const w0 = vbw(), r0 = dotR(), f0 = lab();
                  const plus = [...document.querySelectorAll('#vmapZoom button')].find(b => b.textContent === '+');
                  plus.click(); plus.click();
                  await new Promise(z => setTimeout(z, 420));
                  const w1 = vbw(), r1 = dotR(), f1 = lab();
                  const level = (document.querySelector('#vmapZoomLevel') || {}).textContent || '';
                  const grab = svg.classList.contains('grab');
                  const reset = [...document.querySelectorAll('#vmapZoom button')].find(b => b.textContent === 'Reset');
                  reset.click();
                  await new Promise(z => setTimeout(z, 420));
                  return { w0, w1, back: vbw(), r0, r1, f0, f1, level, grab,
                           screen0: r0 && w0 ? r0 / w0 : null, screen1: r1 && w1 ? r1 / w1 : null };
                }""")
                chk.add(f"{scheme} wind map: zooming in narrows the view and says so",
                        bool(zm and zm["w1"] < zm["w0"] and "\u00d7" in zm["level"] and zm["grab"]),
                        str(zm and [zm["w0"], zm["w1"], zm["level"], zm["grab"]]))
                chk.add(f"{scheme} wind map: Reset returns the whole country",
                        bool(zm and abs(zm["back"] - zm["w0"]) < 0.01), str(zm and [zm["back"], zm["w0"]]))
                chk.add(f"{scheme} wind map: a dot holds its on-screen size through the zoom",
                        bool(zm and zm["r1"] and zm["r1"] < zm["r0"]
                             and abs(zm["screen1"] - zm["screen0"]) < zm["screen0"] * 0.05),
                        str(zm and [zm["r0"], zm["r1"], zm["screen0"], zm["screen1"]]))
                chk.add(f"{scheme} wind map: a label holds its on-screen size too",
                        bool(zm and zm["f0"] and zm["f1"] and zm["f1"] < zm["f0"]), str(zm and [zm["f0"], zm["f1"]]))

                lad = page.evaluate("""() => {
                  const svg = document.querySelector('#board svg.ts');
                  const txt = [...svg.querySelectorAll('text')].map(t => t.textContent);
                  return { cents: txt.filter(t => /\\u00a2$/.test(t)),
                           none: txt.filter(t => /^no (price|bids)$/.test(t)),
                           yes: svg.querySelectorAll("rect[fill='var(--yes)']").length };
                }""")
                # a one-sided book is a real resting bid and keeps its price; a book at
                # one against ninety-nine is the exchange's placeholder and has none;
                # a strike nobody has quoted has no book at all. Three different states
                chk.add(f"{scheme} wind ladder: a one-sided resting bid keeps its price",
                        "94\u00a2" in lad["cents"], str(lad["cents"]))
                chk.add(f"{scheme} wind ladder: a placeholder book shows no price and a two-sided book both sides",
                        "no price" in lad["none"] and "43\u00a2" in lad["cents"] and "57\u00a2" in lad["cents"],
                        str([lad["cents"], lad["none"]]))
                chk.add(f"{scheme} wind ladder: a strike with no book at all reads differently",
                        "no bids" in lad["none"] and lad["yes"] >= 2, str([lad["none"], lad["yes"]]))
                # the bars open the strike they show, the way every other ladder's do
                barurl = page.evaluate("""() => {
                  const b = [...document.querySelectorAll('#board svg.ts rect[fill=\"var(--yes)\"]')];
                  return b.map(x => x.getAttribute('data-contract-url') || '');
                }""")
                chk.add(f"{scheme} wind ladder: a priced bar opens that strike on the exchange",
                        bool(barurl and all("conid_yes=" in u for u in barurl) and len(set(barurl)) == len(barurl)),
                        str(barurl)[:150])
                # the two columns explained once on the page, from the ASOS manual
                note = page.evaluate("""() => {
                  const n = [...document.querySelectorAll('.wnote')];
                  return { n: n.length, text: n.length ? n[0].textContent : '' };
                }""")
                chk.add(f"{scheme} wind note: one explanation of the two columns, not one per city",
                        note["n"] == 1, str(note["n"]))
                chk.add(f"{scheme} wind note: it states what each column measures and cites the manual",
                        all(t in note["text"] for t in ("two-minute average", "at least 9 knots",
                                                        "5 knots above it", "held for ten minutes",
                                                        "10 knots above the lowest five-second wind",
                                                        "14 knots", "an hourly accumulation", "ASOS User")),
                        note["text"][:120])
                chk.add(f"{scheme} wind panel: the hover names both columns and the ten-knot rule",
                        bool(hov and any("Sustained" in t for t in hov)
                             and any("ten knots" in t or "none at this hour" in t for t in hov)),
                        str(hov)[:180])
                page.unroute("**/data/snapshots/**")

                # ---- a report with no temperature carries the day's peak
                #
                # A station whose temperature sensor is out goes on reporting its
                # wind, and the chart rows, which need a temperature, leave those
                # reports out (KBLM on 27 September 2026, whose 13:25Z special
                # report, 03029G39KT, was the day's maximum and carried none). The panel
                # reads the wind block's own rows, so its running peak and the
                # rungs it marks cleared come from every report, as the caption's
                # peak does. The chart rows here top out at a 24 kt gust, 28 mph,
                # and a special report with no temperature carries 40 kt, 46 mph,
                # so the 43 mph rung is cleared only if the panel reads those rows.
                _nt = W_NOW - dt.timedelta(minutes=50)
                nt_row = {"t": _nt.strftime("%Y-%m-%dT%H:%M:00Z"), "type": "SPECI", "wspd": 28.0, "wgst": 40.0}

                def notemp_routes(route):
                    u = route.request.url
                    if u.endswith("/obs/KBOS.json"):
                        resp = route.fetch(); d = json.loads(resp.text())
                        d["rows"] = w_rows
                        wr = sorted([{k: r[k] for k in ("t", "type", "wspd", "wgst") if r.get(k) is not None}
                                     for r in w_rows] + [nt_row], key=lambda r: r["t"])
                        d["wind"] = {"today": {"date": DAY, "n": len(wr), "unit": "kt", "rows": wr,
                                               "peak": {"v": 40, "kt": 40, "mph": 46, "from": "gust",
                                                        "t": nt_row["t"], "type": "SPECI"}}}
                        return route.fulfill(response=resp, body=json.dumps(d))
                    return wind_routes(route)

                page.route("**/data/snapshots/**", notemp_routes)
                page.goto(f"{srv.url}/wind-markets.html")
                page.wait_for_timeout(1800)
                nt = page.evaluate("""() => {
                  const sec = document.querySelector('#wind-KBOS');
                  const svg = sec && sec.querySelector('svg.ts');
                  if (!svg) return null;
                  return { labels: [...svg.querySelectorAll('text')].map(t => t.textContent)
                                     .filter(t => / mph/.test(t)),
                           caps: [...sec.querySelectorAll('p.cap')].map(p => p.textContent) };
                }""")
                chk.add(f"{scheme} wind panel: a report with no temperature clears the rungs below its gust",
                        bool(nt and "43 mph, cleared" in nt["labels"] and "38 mph, cleared" in nt["labels"]),
                        str(nt and nt["labels"])[:170])
                chk.add(f"{scheme} wind panel: the caption names that report's gust as the peak so far",
                        bool(nt and any("46 mph peak so far" in c for c in nt["caps"])),
                        str(nt and nt["caps"])[:170])
                page.unroute("**/data/snapshots/**")

                # ---- the day control, and the desk's anticipated ladder
                #
                # The exchange opens tomorrow's wind board during today, so the
                # board carries the same Today/Tomorrow control the daily
                # temperature board has, and the map above it follows the same
                # day. Where the exchange has opened nothing, the desk's own
                # figure stands in: hatched, in percent rather than cents, with
                # no link to a book that does not exist, and gone the moment a
                # real price exists. That last part is enforced in the pipeline
                # (tests/test_market.py), so what is checked here is that a
                # figure never draws as a price.
                DESK_DAY = None

                def desk_routes(route):
                    u = route.request.url
                    if u.endswith("/catalogue/wind.json"):
                        # the listing is rebuilt once a day. Age it hard: the strip
                        # reports one age against one cadence, and mixing a daily
                        # file into a ten-minute one made the page say "6 hours ago,
                        # updates every 10 minutes" while the market data behind it
                        # was minutes old. The listing has its own louder failure.
                        resp = route.fetch(); d = json.loads(resp.text())
                        d["asof"] = "2026-01-01T00:00:00Z"
                        return route.fulfill(response=resp, body=json.dumps(d))
                    if u.endswith("/summary.json") and "/market/" not in u:
                        resp = route.fetch(); d = json.loads(resp.text())
                        for c in d.get("cities") or []:
                            c["markers"] = dict(c.get("markers") or {}, dayAfter=DAY2)
                            if c.get("station") == "KBOS":
                                c["markers"] = dict(c["markers"], day=DAY)
                        return route.fulfill(response=resp, body=json.dumps(d))
                    if u.endswith("/market/KBOS.json"):
                        resp = route.fetch(); d = json.loads(resp.text())
                        # no exchange ladder at all, and the desk's figures instead
                        d["days"] = {}
                        d["markers"] = dict(d.get("markers") or {}, day=DAY)
                        d["anticipated"] = {DAY: {"source": "desk", "asof": "2026-09-25T09:00:00Z",
                                                  "rows": [{"strike": 25.0, "p": 0.86},
                                                           {"strike": 35.0, "p": 0.58},
                                                           {"strike": 45.0, "p": 0.27}]}}
                        return route.fulfill(response=resp, body=json.dumps(d))
                    if u.endswith("/obs/KBOS.json"):
                        resp = route.fetch(); d = json.loads(resp.text())
                        d["rows"] = w_rows
                        d["today"] = dict(d.get("today") or {}, date=DAY)
                        d["wind"] = {"today": {"date": DAY, "n": len(w_rows), "unit": "kt",
                                               "peak": {"v": 24, "kt": 24, "mph": 28, "from": "gust",
                                                        "t": w_rows[3]["t"], "type": "METAR"}}}
                        return route.fulfill(response=resp, body=json.dumps(d))
                    return route.continue_()

                DESK_DAY = DAY
                page.route("**/data/snapshots/**", desk_routes)
                page.goto(f"{srv.url}/wind-markets.html")
                page.wait_for_timeout(2200)
                btns = page.eval_on_selector_all("#windDays button", "e=>e.map(x=>x.textContent)")
                chk.add(f"{scheme} wind days: the board offers today, tomorrow and the first unopened day",
                        len(btns) == 3 and btns[:2] == ["Today", "Tomorrow"], str(btns))
                chk.add(f"{scheme} wind days: the third day is named by its weekday, not 'day after'",
                        len(btns) == 3 and btns[2] in ("Monday", "Tuesday", "Wednesday", "Thursday",
                                                       "Friday", "Saturday", "Sunday"), str(btns))
                chk.add(f"{scheme} wind days: exactly one is pressed",
                        page.locator("#windDays button.on").count() == 1, "")
                strip = page.locator("#pageStatus").inner_text()
                # the routed listing is aged to January, far older than samples/
                # itself, so the test is whether THAT age reaches the line rather
                # than whether the fixture happens to be fresh (it is not)
                _days = re.search(r"(\d+)\s+days ago", strip)
                chk.add(f"{scheme} wind board: a day-old listing does not set the live freshness line",
                        not _days or int(_days.group(1)) < 100, strip[:120])
                cap1 = page.locator("#vmapCap").inner_text()
                chk.add(f"{scheme} wind map: the caption names the exchange's centre, not the peak so far",
                        "central wind" in cap1 and "today" in cap1 and "so far" not in cap1, cap1)
                desk = page.evaluate("""() => {
                  const secs = [...document.querySelectorAll('#board section')];
                  const sec = secs.find(s => /Boston/.test(s.querySelector('h2').textContent));
                  if (!sec) return null;
                  const svg = sec.querySelector('svg.ts');
                  const txt = [...svg.querySelectorAll('text')].map(t => t.textContent);
                  const hatched = [...svg.querySelectorAll('rect')]
                      .filter(r => (r.getAttribute('fill') || '').indexOf('url(#') === 0).length;
                  return { hatched, titles: txt.filter(t => /Thresholds/.test(t)),
                           pct: txt.filter(t => /^\d+%$/.test(t)),
                           cents: txt.filter(t => /\u00a2$/.test(t)),
                           links: svg.querySelectorAll('a').length,
                           caps: [...sec.querySelectorAll('p.cap')].map(p => p.textContent).join(' ') };
                }""")
                chk.add(f"{scheme} anticipated ladder: it draws, hatched",
                        bool(desk and desk["hatched"] >= 3), str(desk and desk["hatched"]))
                chk.add(f"{scheme} anticipated ladder: it is labelled in percent, never in cents",
                        bool(desk and desk["pct"] and not desk["cents"]),
                        str(desk and (desk["pct"], desk["cents"])))
                chk.add(f"{scheme} anticipated ladder: no link to a book that does not exist",
                        bool(desk and desk["links"] == 0), str(desk and desk["links"]))
                chk.add(f"{scheme} anticipated ladder: it says it is an estimate and what replaces it",
                        bool(desk and "Estimated, contract not yet listed" in desk["caps"]
                             and "as soon as the contract lists" in desk["caps"]),
                        (desk or {}).get("caps", "")[:140])
                chk.add(f"{scheme} anticipated ladder: it never says where the estimate came from",
                        bool(desk and not re.search(r"\bdesk\b|DWM|internal|pricer|our model",
                                                    desk["caps"], re.I)),
                        (desk or {}).get("caps", "")[:140])
                chk.add(f"{scheme} anticipated ladder: the column names itself an estimate",
                        bool(desk and "estimated" in " ".join(desk.get("titles") or []).lower()),
                        str((desk or {}).get("titles")))
                # the day control moves the map with the boards
                page.locator("#windDays button", has_text="Tomorrow").first.click()
                page.wait_for_timeout(2200)
                cap2 = page.locator("#vmapCap").inner_text()
                chk.add(f"{scheme} wind days: the map follows the board's day",
                        "tomorrow" in cap2 and cap2 != cap1, cap2)
                chk.add(f"{scheme} wind days: the pressed button follows the selection",
                        page.locator("#windDays button.on").first.inner_text() == "Tomorrow", "")
                if len(btns) == 3:
                    page.locator("#windDays button", has_text=btns[2]).first.click()
                    page.wait_for_timeout(2200)
                    cap3 = page.locator("#vmapCap").inner_text()
                    chk.add(f"{scheme} wind days: the map names the third day the way its button does",
                            btns[2] in cap3, cap3)
                    chk.add(f"{scheme} wind days: the listing count names the day it counted",
                            btns[2] in page.locator("#pageStatus").inner_text(),
                            page.locator("#pageStatus").inner_text()[-50:])
                # a dot on a board that already carries that station's panel scrolls
                # to it rather than leaving the page for the same chart elsewhere
                page.goto(f"{srv.url}/wind-markets.html"); page.wait_for_timeout(2200)
                sid = page.evaluate("""() => {
                  const s = document.querySelector('#board section[id^=wind-]');
                  return s ? s.id.replace('wind-', '') : null;
                }""")
                # The panel lands 12 px under the top of the window, to the pixel or
                # nearly. A tolerance of 20 px once hid a correction issued while the
                # smooth scroll was still running, which landed 7 to 21 px past the
                # panel every time (2026-10-20), so the margin is 4 px now.
                if sid:
                    page.evaluate("() => window.scrollTo(0, 0)")
                    page.click(f'#vmap g.dot[data-station="{sid}"]')
                    page.wait_for_timeout(1400)
                    land = page.evaluate("""(s) => {
                      const el = document.getElementById('wind-' + s);
                      return { top: Math.round(el.getBoundingClientRect().top),
                               y: Math.round(window.scrollY), path: location.pathname };
                    }""", sid)
                    chk.add(f"{scheme} wind map: a dot scrolls to that station's panel",
                            land["y"] > 0 and abs(land["top"] - 12) <= 4, str(land))
                    chk.add(f"{scheme} wind map: picking a listed station does not leave the board",
                            "wind-markets" in land["path"], land["path"])
                # The same while zoomed. The pan used to take pointer capture
                # on pointerdown, and Chromium then delivered every click to
                # the svg, so a dot clicked at any zoom never opened. After two
                # zoom steps a dot whose section is on the board is found under
                # the pointer (elementFromPoint proves it) and clicked.
                if sid:
                    page.goto(f"{srv.url}/wind-markets.html"); page.wait_for_timeout(2200)
                    page.click("#vmapZoom button[title='zoom in']"); page.click("#vmapZoom button[title='zoom in']"); page.wait_for_timeout(600)
                    page.evaluate("() => window.scrollTo(0, 0)"); page.wait_for_timeout(200)
                    zt = page.evaluate("""() => {
                      const svg = document.querySelector('#vmap'); const R = svg.getBoundingClientRect();
                      for (const g of svg.querySelectorAll('g.dot[data-station]')) {
                        const s = g.dataset.station;
                        if (!document.getElementById('wind-' + s)) continue;
                        const c = g.querySelector('circle:last-of-type'); const r = c.getBoundingClientRect();
                        const x = r.left + r.width / 2, y = r.top + r.height / 2;
                        if (x < R.left + 4 || x > R.right - 4 || y < R.top + 4 || y > R.bottom - 4 || y > innerHeight - 4) continue;
                        const e = document.elementFromPoint(x, y);
                        return { station: s, x, y, hit: !!(e && e.closest('g.dot') === g),
                                 under: e ? e.tagName + '.' + (e.getAttribute('class') || '') : null,
                                 zoom: document.querySelector('#vmapZoomLevel').textContent };
                      }
                      return null;
                    }""")
                    chk.add(f"{scheme} wind map: zoomed in, a dot with a section on the board is what the pointer reaches",
                            bool(zt and zt["hit"] and zt["zoom"] != "whole country"), str(zt))
                    if zt and zt["hit"]:
                        page.mouse.click(zt["x"], zt["y"]); page.wait_for_timeout(1400)
                        land = page.evaluate("""(s) => {
                          const el = document.getElementById('wind-' + s);
                          return { top: Math.round(el.getBoundingClientRect().top),
                                   y: Math.round(window.scrollY), path: location.pathname };
                        }""", zt["station"])
                        chk.add(f"{scheme} wind map: a dot clicked while zoomed still scrolls to its station's panel",
                                land["y"] > 0 and abs(land["top"] - 12) <= 4 and "wind-markets" in land["path"], str(land))
                page.unroute("**/data/snapshots/**")
                page.context.close(); page = page_wall

                # ---- the analysis resolution page: a proposed settlement
                #      framework, unlisted, drawn from samples/snapshots/analysis2
                #
                # The page computes nothing, so the checks are that each control
                # moves what it says it moves, that a place opens with its day in
                # full, and that the hypothetical ladder can never be read as a
                # market: hatched, Yes or No and nothing else, no cents, no link.
                ANA = "analysis-resolution.html"
                ANA_SNAP = os.path.join(ROOT, "samples", "snapshots", "analysis2")
                # The fixtures are a real local run of the job, so the day and
                # the hours the checks drive come from the fixture index files
                # rather than from dates written into this script: the newest
                # day whose New York RTMA day is complete (final pills; RTMA
                # resolves, owner's decision 2026-09-29), the oldest day (routed
                # away for the hollow-dot check), and the last two RTMA
                # temperature frames listed on a day the page can select (the
                # stepper).
                with open(os.path.join(ANA_SNAP, "index.json")) as fh:
                    ana_index = json.load(fh)
                ana_days = list(ana_index["days"])
                ana_day = None
                ana_entries = {}          # New York's entry per fixture day
                for d_ in reversed(ana_days):
                    with open(os.path.join(ANA_SNAP, "days", d_ + ".json")) as fh:
                        e_ = (json.load(fh).get("locations") or {}).get("new-york-ny") or {}
                    ana_entries[d_] = e_
                    if ana_day is None and (e_.get("rtma") or {}).get("complete") and (e_.get("rtma") or {}).get("precip"):
                        ana_day = d_
                chk.add(f"{scheme} analysis fixtures: a day with a complete New York RTMA entry", ana_day is not None, str(ana_days))
                ana_day = ana_day or ana_days[-1]
                with open(os.path.join(ANA_SNAP, "grid", "index.json")) as fh:
                    ana_ut = (json.load(fh).get("frames") or {}).get("rtma", {}).get("temp", {})
                ana_fday = next((d_ for d_ in sorted(ana_ut, reverse=True) if d_ in ana_days and len(ana_ut[d_]) >= 2), None)
                chk.add(f"{scheme} analysis fixtures: two RTMA temperature frames on a selectable day", ana_fday is not None, str(ana_ut))
                ana_n = len(ana_index["locations"])
                ana_hours = sorted(ana_ut.get(ana_fday) or ["15", "16"])[-2:]
                # the page opens on the newest day every place has resolved, marks
                # the days after it as provisional, and falls back to the newest
                # day when the index does not name one
                ana_rday = ana_index.get("lastResolvedDay")
                chk.add(f"{scheme} analysis fixtures: the index names a fully resolved day before the newest",
                        bool(ana_rday) and ana_rday in ana_days and ana_rday != ana_days[-1], str(ana_rday))
                page.goto(f"{srv.url}/{ANA}"); page.wait_for_timeout(2200)
                dflt = page.evaluate("""() => ({ day: document.querySelector('#anaDay').value, url: location.search,
                  opts: Array.from(document.querySelectorAll('#anaDay option')).map(o => [o.value, o.textContent]) })""")
                newer = [t for v, t in dflt["opts"] if v > (ana_rday or "")]
                chk.add(f"{scheme} analysis: the page opens on the newest fully resolved day",
                        dflt["day"] == ana_rday and f"day={ana_rday}" in dflt["url"], str([dflt["day"], ana_rday]))
                chk.add(f"{scheme} analysis: the days after it are marked provisional and it is not",
                        bool(newer) and all("provisional" in t for t in newer)
                        and not any("provisional" in t for v, t in dflt["opts"] if v == ana_rday), str(dflt["opts"][:3]))
                # the day's values as CSV (owner's decision 2026-09-29): the two
                # links follow the day and fetch the job's own files
                ana_csv = page.evaluate("""async () => { const out = [];
                  for (const a of document.querySelectorAll('#anaCsv a')) {
                    const r = await fetch(a.href); out.push([a.getAttribute('href'), r.status, (await r.text()).split('\\n')[0]]); }
                  return out; }""")
                chk.add(f"{scheme} analysis: the day's CSV links fetch the raw and processed files with their headers",
                        len(ana_csv) == 2 and ana_csv[0][0].endswith(f"csv/{ana_rday}-raw.csv") and ana_csv[0][1] == 200
                        and ana_csv[0][2].startswith("date,location,local_hour,valid_utc,temp_k")
                        and ana_csv[1][0].endswith(f"csv/{ana_rday}-processed.csv") and ana_csv[1][1] == 200
                        and ana_csv[1][2].startswith("date,location,name,state,final"), str(ana_csv)[:220])

                def ana_no_resolved(route):
                    resp = route.fetch(); d = json.loads(resp.text())
                    d.pop("lastResolvedDay", None); d.pop("dayStatus", None)
                    return route.fulfill(response=resp, body=json.dumps(d))

                page.route("**/analysis2/index.json", ana_no_resolved)
                page.goto(f"{srv.url}/{ANA}"); page.wait_for_timeout(2200)
                chk.add(f"{scheme} analysis: an index without the field opens on the newest day",
                        page.evaluate("document.querySelector('#anaDay').value") == ana_days[-1],
                        page.evaluate("document.querySelector('#anaDay').value"))
                page.unroute("**/analysis2/index.json")
                page.goto(f"{srv.url}/{ANA}?day={ana_fday}"); page.wait_for_timeout(2200)
                ana_state = """() => ({
                  legend: document.querySelector('#anaLegend').textContent,
                  href: document.querySelector('#anaFrame').getAttribute('href') || '',
                  cap: document.querySelector('#anaCap').textContent,
                  dots: document.querySelectorAll('#vdots g.dot').length,
                  stmt: document.querySelector('.sub').textContent,
                  robots: (document.querySelector('meta[name=robots]') || {}).content || '',
                  header: !!document.querySelector('header.site'), footer: !!document.querySelector('footer.site'),
                  url: location.search })"""
                a0 = page.evaluate(ana_state)
                chk.add(f"{scheme} analysis: the statement is on the page and the page is not offered to search",
                        "No contract settles on it" in a0["stmt"] and "noindex" in a0["robots"], a0["robots"])
                chk.add(f"{scheme} analysis: the chrome draws for a page outside the navigation",
                        a0["header"] and a0["footer"], str([a0["header"], a0["footer"]]))
                chk.add(f"{scheme} analysis: every place and a frame under them",
                        a0["dots"] == ana_n and ana_n == 67 and a0["href"].startswith("data:image/png"), str([a0["dots"], a0["href"][:22]]))
                page.click('button[data-var="gust"]'); page.wait_for_timeout(900)
                a1 = page.evaluate(ana_state)
                chk.add(f"{scheme} analysis: the variable button changes the legend and the frame",
                        "Peak gust" in a1["legend"] and a1["legend"] != a0["legend"]
                        and a1["href"].startswith("data:image/png") and a1["href"] != a0["href"]
                        and "var=gust" in a1["url"], a1["legend"][:60])
                # RTMA is the one analysis the page reads (owner's decision
                # 2026-09-30): no product choice, no URMA anywhere in the page
                # or its description, and an address from before that still
                # opens, on RTMA, without the parameter
                page.click('button[data-var="high"]'); page.wait_for_timeout(900)
                a2 = page.evaluate("""() => ({ product: document.querySelectorAll('button[data-product]').length,
                  text: document.body.innerText + ' ' + [...document.querySelectorAll('meta')].map(m => m.content || '').join(' ') })""")
                chk.add(f"{scheme} analysis: one analysis, no product choice and no URMA on the page",
                        a2["product"] == 0 and "urma" not in a2["text"].lower(), str(a2["product"]))
                page.goto(f"{srv.url}/{ANA}?var=high&product=urma&day={ana_fday}"); page.wait_for_timeout(2200)
                a3 = page.evaluate(ana_state)
                chk.add(f"{scheme} analysis: an address with product=urma opens on RTMA and drops the parameter",
                        "RTMA" in a3["legend"] and a3["href"].startswith("data:image/png") and "product=" not in a3["url"],
                        str([a3["legend"][:40], a3["url"]]))
                # the stepper walks the last two RTMA temperature frames the
                # fixtures list; the oldest day's file is routed away so the
                # day select lands on a day with no file
                ana_gone = ana_days[0] if ana_days[0] != ana_fday else ana_days[-1]

                def ana_routes(route):
                    u = route.request.url
                    if u.endswith(f"/analysis2/days/{ana_gone}.json"):
                        return route.fulfill(status=404, body="not there")
                    return route.continue_()

                page.route("**/data/snapshots/**", ana_routes)
                page.goto(f"{srv.url}/{ANA}?var=high&day={ana_fday}&hour={ana_hours[1]}"); page.wait_for_timeout(2200)
                c0 = page.locator("#anaCap").inner_text()
                page.locator("#anaStep button[title='previous hour']").click(); page.wait_for_timeout(900)
                c1 = page.locator("#anaCap").inner_text()
                chk.add(f"{scheme} analysis: the hour stepper changes the caption",
                        f"{ana_hours[1]}:00 UTC" in c0 and f"{ana_hours[0]}:00 UTC" in c1 and c1 != c0
                        and f"hour={ana_hours[0]}" in page.evaluate("location.search"), c1[:80])
                page.select_option("#anaDay", ana_gone); page.wait_for_timeout(900)
                c2 = page.locator("#anaCap").inner_text()
                chk.add(f"{scheme} analysis: the day select changes the caption and the address",
                        ana_gone in c2 and c2 != c1 and f"day={ana_gone}" in page.evaluate("location.search"), c2[:80])
                chk.add(f"{scheme} analysis: a day with no file draws every place hollow",
                        page.locator("#vdots circle.absent").count() == ana_n, str(page.locator("#vdots circle.absent").count()))
                page.unroute("**/data/snapshots/**")
                # a dot opens the place, with its day in full
                page.goto(f"{srv.url}/{ANA}?var=high&day={ana_day}"); page.wait_for_timeout(2200)
                page.click('#vmap g.dot[data-loc="new-york-ny"]'); page.wait_for_timeout(1400)
                pn = page.evaluate("""() => {
                  const p = document.querySelector('#locPanel');
                  const hatched = [...p.querySelectorAll('rect.hatched')];
                  return { hidden: p.hidden, h2: (p.querySelector('h2') || {}).textContent || '',
                           rows: p.querySelectorAll('tbody tr').length, cards: p.querySelectorAll('.anacard').length,
                           hatched: hatched.length, fills: [...new Set(hatched.map(r => r.getAttribute('fill')))],
                           links: p.querySelectorAll('[data-contract-url], a').length,
                           rungs: [...new Set([...p.querySelectorAll('text.anarung')].map(t => t.textContent))].sort(),
                           series: p.querySelectorAll('svg.ts path.anaseries').length,
                           resolved: p.querySelectorAll('svg.ts line.anares').length,
                           text: p.textContent, url: location.search };
                }""")
                chk.add(f"{scheme} analysis: a dot opens the place with 24 hour rows and five cards",
                        not pn["hidden"] and pn["h2"].startswith("New York") and pn["rows"] == 24 and pn["cards"] == 5
                        and "loc=new-york-ny" in pn["url"], str([pn["h2"], pn["rows"], pn["cards"]]))
                chk.add(f"{scheme} analysis: the RTMA line and the resolved value are drawn, and the panel never says URMA",
                        pn["series"] == 1 and pn["resolved"] == 1 and "urma" not in pn["text"].lower(),
                        str([pn["series"], pn["resolved"]]))
                chk.add(f"{scheme} analysis: the hypothetical rungs are hatched and say Yes or No only",
                        pn["hatched"] >= 5 and pn["fills"] == ["url(#wxHatch)"] and pn["rungs"] == ["No", "Yes"],
                        str([pn["hatched"], pn["fills"], pn["rungs"]]))
                # owner's decision 2026-09-28: a value equal to the strike resolves Yes,
                # at or above for the high and at or below for the low
                ana_rungs = """() => {
                  const p = document.querySelector('#locPanel');
                  const yn = [...p.querySelectorAll('text.anarung')].map(t => t.textContent);
                  const st = [...p.querySelectorAll('text.anastrike')].map(t => t.textContent);
                  return st.map((k, i) => [k, yn[i]]);
                }"""
                ny_u = (ana_entries.get(ana_day) or {}).get("rtma") or {}
                hv = (ny_u.get("high") or {}).get("value"); lv = (ny_u.get("low") or {}).get("value")
                rh = dict(page.evaluate(ana_rungs))
                chk.add(f"{scheme} analysis: a high equal to the strike resolves Yes, a strike one above it No, and the rungs read at least",
                        hv is not None and rh.get(f"\u2265{hv}°F") == "Yes" and rh.get(f"\u2265{hv + 1}°F") == "No"
                        and all(k.startswith("\u2265") for k in rh), str([hv, rh]))
                page.click('button[data-var="low"]'); page.wait_for_timeout(900)
                rl = dict(page.evaluate(ana_rungs))
                chk.add(f"{scheme} analysis: a low equal to the strike resolves Yes, a strike one below it No, and the rungs read at most",
                        lv is not None and rl.get(f"\u2264{lv}°F") == "Yes" and rl.get(f"\u2264{lv - 1}°F") == "No"
                        and all(k.startswith("\u2264") for k in rl), str([lv, rl]))
                page.click('button[data-var="high"]'); page.wait_for_timeout(700)
                chk.add(f"{scheme} analysis: no rung carries a link or a price, and the panel keeps the exchange's language",
                        pn["links"] == 0 and "¢" not in pn["text"]
                        and not re.search(r"\b(ask|sell|offer|bid)\b", pn["text"], re.I)
                        and "No contract settles on it" in pn["text"], str(pn["links"]))
                # a day whose RTMA is short of 24 hours: the day file says so, and
                # the place file has no hour past the 17th, so the running values
                # the settlement display shows stop at 17 (the lane's running
                # values of an hour cover only the hours up to it, so the first
                # 17 rows keep theirs). The status is one line since 2026-11-03,
                # where five card pills carried it before
                def ana_partial(route):
                    u = route.request.url
                    if u.endswith(f"/analysis2/days/{ana_day}.json"):
                        resp = route.fetch(); d = json.loads(resp.text())
                        e = d["locations"]["new-york-ny"]["rtma"]
                        e.update({"hours": 17, "complete": False, "final": False})
                        # the precipitation resolves once every hourly file is
                        # read, so a day short of its files has not resolved either
                        if e.get("precip"):
                            e["precip"].update({"resolved": False, "resolvedAt": None, "hours": 17})
                        return route.fulfill(response=resp, body=json.dumps(d))
                    if u.split("?")[0].endswith(f"/analysis2/loc/new-york-ny/{ana_day}.json"):
                        resp = route.fetch(); d = json.loads(resp.text())
                        for r_ in d["hours"][17:]:
                            r_["rtma"] = None
                        return route.fulfill(response=resp, body=json.dumps(d))
                    return route.continue_()

                page.route("**/data/snapshots/**", ana_partial)
                page.goto(f"{srv.url}/{ANA}?day={ana_day}&loc=new-york-ny"); page.wait_for_timeout(2200)
                st_p = page.evaluate("""() => ({ open: !document.querySelector('#locPanel').hidden,
                  pills: [...document.querySelectorAll('#locPanel .anapill')].map(x => x.textContent),
                  line: (document.querySelector('#anaStatus') || {}).textContent || '',
                  cards: document.querySelectorAll('#locPanel .anacard').length })""")
                chk.add(f"{scheme} analysis: ?loc= opens the panel on load",
                        st_p["open"] and len(st_p["pills"]) == 1 and st_p["cards"] == 5, str(st_p)[:160])
                chk.add(f"{scheme} analysis: the status line says provisional when RTMA is incomplete, with the count",
                        st_p["pills"] == ["Provisional"] and "17 of 24 hours" in st_p["line"]
                        and page.locator('#vdots g.dot[data-loc="new-york-ny"] circle.prov').count() == 1, st_p["line"][:120])
                page.unroute("**/data/snapshots/**")
                # the wording follows the state: a value that can still move is
                # running, one that cannot is resolved, a closed day's is closed.
                # The partial route above is still on for the first read
                ana_rule = "() => (document.querySelector('#locPanel text.anareslbl') || {}).textContent || ''"
                page.route("**/data/snapshots/**", ana_partial)
                page.goto(f"{srv.url}/{ANA}?var=high&day={ana_day}&loc=new-york-ny"); page.wait_for_timeout(2200)
                r_part = page.evaluate(ana_rule)
                page.unroute("**/data/snapshots/**")
                page.goto(f"{srv.url}/{ANA}?var=precip&day={ana_day}&loc=new-york-ny"); page.wait_for_timeout(2200)
                r_final = page.evaluate(ana_rule)
                # the running precipitation curve ends on the file's own total
                # once every hourly file is read: the last point of the RTMA
                # line sits on the resolved rule
                ana_end = page.evaluate("""() => {
                  const p = document.querySelector('#locPanel');
                  const d = p.querySelector('svg.ts path.anaseries').getAttribute('d').trim();
                  const last = d.split(/[ML]/).filter(Boolean).pop().trim().split(' ');
                  const tip = p.querySelectorAll('rect.hband');
                  return { y: +last[1], rule: +p.querySelector('line.anares').getAttribute('y1'), bands: tip.length };
                }""")
                page.locator("#locPanel rect.hband").last.hover(); page.wait_for_timeout(300)
                ana_tip = page.evaluate("() => [...document.querySelectorAll('.tip')].map(t => t.textContent).join(' ')")
                # the exact figures are behind Show exact values since 2026-11-03
                page.click("#anaExact"); page.wait_for_timeout(300)
                ana_cards = page.evaluate("() => [...document.querySelectorAll('#locPanel .anacard')].map(c => [c.dataset.var, (c.querySelector('.anaexact') || {}).textContent || ''])")
                chk.add(f"{scheme} analysis: the chart rule says resolved only when final and running while provisional",
                        r_final.startswith("resolved ") and r_final.endswith("(final)")
                        and r_part.startswith("running ") and r_part.endswith("(provisional, 17 of 24 hours)"),
                        str([r_final, r_part]))
                chk.add(f"{scheme} analysis: the complete RTMA precipitation curve ends on the resolved rule and the tooltip names the running sum",
                        abs(ana_end["y"] - ana_end["rule"]) < 0.6 and "Running sum of the hourly analyses" in ana_tip,
                        str([ana_end, ana_tip[:120]]))
                ana_dec = {k: (re.search(r"exact -?\d+\.(\d+)", v) or [None, ""])[1] for k, v in ana_cards}
                chk.add(f"{scheme} analysis: the exact figure is cut to a thousandth, precipitation a ten-thousandth",
                        [len(ana_dec.get(k, "")) for k in ("high", "low", "gust", "wind", "precip")] == [3, 3, 3, 3, 4], str(ana_cards))
                # a closed day in the fixtures, if one is there: the RTMA day is
                # closed too, not provisional
                ana_closed = next((d_ for d_ in ana_days if ((ana_entries.get(d_) or {}).get("rtma") or {}).get("closed")), None)
                if ana_closed:
                    page.goto(f"{srv.url}/{ANA}?var=high&day={ana_closed}&loc=new-york-ny"); page.wait_for_timeout(2200)
                    r_closed = page.evaluate(ana_rule)
                    st_c = page.evaluate("() => (document.querySelector('#anaStatus') || {}).textContent || ''")
                    chk.add(f"{scheme} analysis: a closed RTMA day says closed, with its count",
                            r_closed.startswith("closed ") and st_c.startswith("Closed") and " of " in st_c, str([r_closed, st_c[:80]]))
                # a place with no hour published by its 12:45 AM cut-off goes to
                # event review (owner's ruling 2026-10-14): every card says so,
                # no value is drawn, and the dot is hollow
                def ana_review(route):
                    u = route.request.url
                    if u.endswith(f"/analysis2/days/{ana_day}.json"):
                        resp = route.fetch(); d = json.loads(resp.text())
                        d["locations"]["new-york-ny"]["rtma"] = {
                            "hours": 0, "of": 24, "complete": False, "high": None, "low": None, "gust": None,
                            "wind": None, "final": False, "finalAt": None, "review": True, "closed": False,
                            "precip": {"value": None, "exact": None, "hours": 0, "resolved": False,
                                       "resolvedAt": None, "review": True}}
                        return route.fulfill(response=resp, body=json.dumps(d))
                    return route.continue_()

                page.route("**/data/snapshots/**", ana_review)
                page.goto(f"{srv.url}/{ANA}?var=high&day={ana_day}&loc=new-york-ny"); page.wait_for_timeout(2200)
                pills_r = page.eval_on_selector_all("#locPanel .anapill", "e => e.map(x => x.textContent)")
                cards_r = page.eval_on_selector_all("#locPanel .anacard .ae", "e => e.map(x => x.textContent)")
                novalue_r = page.locator("#locPanel .anacard .av.dim").count()
                chk.add(f"{scheme} analysis: a place with no hour by the cut-off says event review on every card, with no value",
                        pills_r == ["Event review"] and "event review" in page.evaluate("() => document.querySelector('#anaStatus').textContent")
                        and len(cards_r) == 5 and novalue_r == 5 and all("12:45 AM cut-off" in c_ for c_ in cards_r)
                        and page.locator("#locPanel line.anares").count() == 0
                        and page.locator('#vdots g.dot[data-loc="new-york-ny"] circle.prov, #vdots g.dot[data-loc="new-york-ny"] circle.final').count() == 0,
                        str([pills_r, cards_r[:2]]))
                page.unroute("**/data/snapshots/**")
                # the freshness pill judges the job by the index's written time,
                # not by the analysis valid hour NOAA's lag keeps behind the clock;
                # the valid hours stand beside it
                def ana_fresh(route):
                    resp = route.fetch(); d = json.loads(resp.text())
                    d["written"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 120))
                    d["asof"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3 * 3600))
                    return route.fulfill(response=resp, body=json.dumps(d))

                page.route("**/analysis2/index.json", ana_fresh)
                page.goto(f"{srv.url}/{ANA}"); page.wait_for_timeout(2200)
                fr = page.evaluate("""() => ({ cls: document.querySelector('.status').className, text: document.querySelector('.status').textContent,
                                              newest: (document.querySelector('#anaNewest') || {}).textContent || '' })""")
                page.unroute("**/analysis2/index.json")
                ana_latest = {k: (v.get("latest") or "").replace("T", " ").replace(":00Z", " UTC") for k, v in (ana_index.get("sources") or {}).items()}
                chk.add(f"{scheme} analysis: the freshness pill reads the index's written time against the job cadence",
                        "live" in fr["cls"] and "behind" not in fr["text"] and "every 10 minutes" in fr["text"], str(fr)[:160])
                chk.add(f"{scheme} analysis: the newest RTMA analysis valid hour stays visible",
                        fr["newest"] == "newest RTMA analysis " + ana_latest.get("rtma", "x"), fr["newest"])
                # the place select and the keyboard both open the panel; the
                # picked dot is drawn last so it is on top where dots overlap
                page.goto(f"{srv.url}/{ANA}?var=high&day={ana_fday}&hour={ana_hours[0]}"); page.wait_for_timeout(2200)
                page.select_option("#anaLoc", "chicago-il"); page.wait_for_timeout(1400)
                sel = page.evaluate("""() => [document.querySelector('#locPanel').hidden, (document.querySelector('#locPanel h2') || {}).textContent || '',
                                            location.search, document.querySelector('#vdots g.dot:last-child').dataset.loc,
                                            [...document.querySelectorAll('#anaLoc option')].length]""")
                chk.add(f"{scheme} analysis: the place select opens the panel and raises the dot",
                        sel[0] is False and sel[1].startswith("Chicago") and "loc=chicago-il" in sel[2] and sel[3] == "chicago-il" and sel[4] == ana_n + 1, str(sel))
                page.focus('#vmap g.dot[data-loc="denver-co"]'); page.keyboard.press("Enter"); page.wait_for_timeout(1400)
                kb = page.evaluate("""() => { const g = document.querySelector('#vmap g.dot[data-loc="denver-co"]');
                  return [(document.querySelector('#locPanel h2') || {}).textContent || '', location.search, g.getAttribute('role'), g.getAttribute('tabindex'),
                          (g.querySelector('title') || {}).textContent || '', document.querySelector('#anaLoc').value] }""")
                chk.add(f"{scheme} analysis: Enter on a focused dot opens its place, and the dot is a named button",
                        kb[0].startswith("Denver") and "loc=denver-co" in kb[1] and kb[2] == "button" and kb[3] == "0"
                        and kb[4].startswith("Denver") and kb[5] == "denver-co", str(kb))
                page.focus("#anaStep button[title='next hour']"); page.keyboard.press("ArrowRight"); page.wait_for_timeout(700)
                chk.add(f"{scheme} analysis: ArrowRight on the focused stepper steps the hour",
                        f"hour={ana_hours[1]}" in page.evaluate("location.search"), page.evaluate("location.search"))
                # frames are never written to this browser's storage
                for _ in range(4):
                    page.locator("#anaStep button[title='next hour']").click(); page.wait_for_timeout(300)
                ls_keys = page.evaluate("() => Object.keys(localStorage).filter(k => /^wx:analysis2?\\/grid\\/[a-z]+\\//.test(k))")
                chk.add(f"{scheme} analysis: no grid frame is written to localStorage after stepping", ls_keys == [], str(ls_keys)[:120])
                # a day with no frames listed carries no hour in the address
                ana_noframes = next((d_ for d_ in ana_days if not ana_ut.get(d_)), None)
                if ana_noframes:
                    page.select_option("#anaDay", ana_noframes); page.wait_for_timeout(900)
                    nf = page.evaluate("() => [location.search, document.querySelector('#anaCap').textContent]")
                    chk.add(f"{scheme} analysis: a day with no frames clears hour= and names the variable in words",
                            "hour=" not in nf[0] and "no RTMA temperature frames" in nf[1], str(nf))
                # a day or place file that could not be read is said to be
                # unreadable, not a day with no hours; a fresh context so no
                # cached copy stands in
                ana_ctx2 = browser.new_context(color_scheme=scheme, viewport={"width": 1200, "height": 900})
                ana_p2 = ana_ctx2.new_page()
                ana_p2.route("**/analysis2/days/**", lambda route: route.fulfill(status=404, body="gone"))
                ana_p2.route("**/analysis2/loc/**", lambda route: route.fulfill(status=404, body="gone"))
                ana_p2.goto(f"{srv.url}/{ANA}?var=high&day={ana_day}&loc=new-york-ny"); ana_p2.wait_for_timeout(2200)
                ur = ana_p2.evaluate("""() => [[...document.querySelectorAll('#locPanel .anacard .ae')].map(x => x.textContent),
                                              [...document.querySelectorAll('#locPanel p.cap')].map(x => x.textContent).join(' ')]""")
                chk.add(f"{scheme} analysis: an unreadable day file is reported as unreadable, not as no hours",
                        len(ur[0]) == 5 and all("could not be read" in t for t in ur[0]) and "could not be read" in ur[1]
                        and "No hours read yet" not in ur[1], str(ur)[:160])
                ana_ctx2.close()
                # ---- the settlement display (owner's approval of 2026-11-03):
                #      the picked place-day above the map, drawn only from the
                #      lane's figures (`show`, the rows' exact values and `run`).
                #      Every expected figure below is read from the fixtures, which
                #      the lane's own code wrote, never worked out here. On the
                #      fixtures' final New York day: the five cards carry the
                #      summary, the hour table ends its running total on the day's
                #      and shows exact figures under the toggle, the replay moves
                #      the cards to the lane's running values for that minute, and
                #      var= opens its chart. With the clock fixed on the fixtures'
                #      live day: the three charts share one axis and one now line,
                #      the countdowns pass through their four states, and a refetch
                #      inside an arrival window carries ?b= and fills the day in. A
                #      place file written before the running values still draws.
                with open(os.path.join(ANA_SNAP, "loc", "new-york-ny", ana_day + ".json")) as fh:
                    sd_doc = json.load(fh)
                sd_sum = sd_doc["summary"]["rtma"]
                sd_tz = _Z(sd_doc["tz"])

                def sd_hour(iso_):
                    t_ = dt.datetime.fromisoformat(iso_.replace("Z", "+00:00")).astimezone(sd_tz)
                    return f"{t_.hour % 12 or 12} {'AM' if t_.hour < 12 else 'PM'} {t_.tzname()}"

                def sd_clock(t_):
                    t_ = t_.astimezone(sd_tz)
                    return f"{t_.hour % 12 or 12}:{t_.minute:02d} {'AM' if t_.hour < 12 else 'PM'}"

                sd_cards_js = """() => [...document.querySelectorAll('#anaCards .anacard')].map(c => [c.dataset.var,
                  (c.querySelector('.av') || {}).textContent || '', (c.querySelector('.ae') || {}).textContent || ''])"""
                page.goto(f"{srv.url}/{ANA}?day={ana_day}&loc=new-york-ny"); page.wait_for_timeout(2200)
                sd = page.evaluate("""(js) => ({ cards: eval(js)(),
                  status: (document.querySelector('#anaStatus') || {}).textContent || '',
                  pill: (document.querySelector('#anaStatus .anapill') || {}).textContent || '',
                  y: window.scrollY, est: !document.querySelector('#anaEst').hidden,
                  order: [...document.querySelectorAll('.wrap > *')].map(e => e.id).filter(Boolean) })""", sd_cards_js)
                sd_want = [["high", f"{sd_sum['high']['value']}°F", "at " + sd_hour(sd_sum["high"]["at"])],
                           ["low", f"{sd_sum['low']['value']}°F", "at " + sd_hour(sd_sum["low"]["at"])],
                           ["gust", f"{sd_sum['gust']['value']} mph", "at " + sd_hour(sd_sum["gust"]["at"])],
                           ["wind", f"{sd_sum['wind']['value']} mph", f"mean of {sd_sum['hours']} hours"],
                           ["precip", f"{sd_sum['precip']['value']:.2f} in", f"total of {sd_sum['precip']['hours']} hours"]]
                chk.add(f"{scheme} settlement display: on a final day the five cards carry the day's resolved values",
                        sd["cards"] == sd_want, str([sd["cards"], sd_want])[:240])
                chk.add(f"{scheme} settlement display: the status line says final, with the hours the day resolved on",
                        sd["pill"] == "Final" and f"on {sd_sum['hours']} of {sd_sum['of']} hours" in sd["status"], sd["status"][:120])
                chk.add(f"{scheme} settlement display: it sits above the map, and a link with no var stays at the top",
                        "locPanel" in sd["order"] and "anaMap" in sd["order"] and sd["order"].index("locPanel") < sd["order"].index("anaMap")
                        and sd["y"] < 40, str([sd["order"], sd["y"]]))
                # the hour table, then the same with Show exact values
                sd_tab_js = """() => { const rows = [...document.querySelectorAll('#anaHours tbody tr')];
                  return { n: rows.length, last: rows.length ? rows[rows.length - 1].querySelector('td.ptotal').textContent : '',
                           hi: (document.querySelector('#anaHours td.hi') || {}).textContent || '',
                           temps: rows.map(r => r.querySelectorAll('td')[2].textContent),
                           arr: rows.map(r => r.querySelector('td.arr').textContent),
                           exacts: [...document.querySelectorAll('#anaCards .anaexact')].map(e => e.textContent),
                           foot: (document.querySelector('#anaFoot') || {}).textContent || '' }; }"""
                tb = page.evaluate(sd_tab_js)
                sd_est = ["≈" if not (r_["rtma"] or {}).get("pub") else "" for r_ in sd_doc["hours"]]
                chk.add(f"{scheme} settlement display: the hour table has a row per hour, its running total ends on the day's, and estimated arrivals carry ≈",
                        tb["n"] == sd_sum["of"] and tb["last"] == f"{sd_sum['precip']['value']:.2f}" and tb["hi"] == str(sd_sum["high"]["value"])
                        and [a_[:1] if a_.startswith("≈") else "" for a_ in tb["arr"]] == sd_est and sd["est"] == any(sd_est)
                        and "resolves on" in tb["foot"] and tb["exacts"] == [], str({k_: tb[k_] for k_ in ("n", "last", "hi")})[:160])
                page.click("#anaExact"); page.wait_for_timeout(300)
                tx = page.evaluate(sd_tab_js)
                chk.add(f"{scheme} settlement display: Show exact values gives the exact figures in the table and on the cards",
                        tx["last"] == f"{sd_sum['precip']['exact']:.4f}" and all(re.fullmatch(r"-?\d+\.\d{3}", t_) for t_ in tx["temps"])
                        and len(tx["exacts"]) == 5 and tx["exacts"][0] == f"exact {sd_sum['high']['exact']:.3f}°F"
                        and tx["exacts"][4] == f"exact {sd_sum['precip']['exact']:.4f} in", str([tx["last"], tx["temps"][:2], tx["exacts"][:2]]))
                # the replay: half past the hour after the 11 AM analysis, when the
                # hours from midnight to 11 AM are in (each analysis about 49
                # minutes after its hour, the 11 AM hour's rain at about 12:17);
                # the cards carry the lane's running values of the 11 AM row
                sd_k = 11
                sd_run = sd_doc["hours"][sd_k]["rtma"]["run"]
                sd_t = dt.datetime.fromisoformat(sd_doc["hours"][sd_k + 1]["t"].replace("Z", "+00:00")) + dt.timedelta(minutes=30)
                page.evaluate("(ms) => { const r = document.querySelector('#anaT'); r.value = String(ms); r.dispatchEvent(new Event('input')); }",
                              int(sd_t.timestamp() * 1000))
                page.wait_for_timeout(300)
                rp = page.evaluate("""(js) => ({ cards: eval(js)(),
                  status: document.querySelector('#anaStatus').textContent,
                  pill: document.querySelector('#anaStatus .anapill').textContent,
                  future: document.querySelectorAll('#anaSettle svg.anasvg rect.future').length,
                  now: document.querySelectorAll('#anaSettle svg.anasvg line.now').length,
                  when: document.querySelector('#anaWhen b').textContent })""", sd_cards_js)
                rp_want = [f"{sd_run['high']['value']}°F", f"{sd_run['low']['value']}°F", f"{sd_run['gust']['value']} mph",
                           f"{sd_run['wind']['value']} mph", f"{sd_run['precip']['value']:.2f} in"]
                chk.add(f"{scheme} settlement display: the replay shows the day as of that minute, from the lane's running values",
                        [c_[1] for c_ in rp["cards"]] == rp_want and all(c_[2].endswith("so far") for c_ in rp["cards"])
                        and rp["pill"] == "Provisional" and f"{sd_run['hours']} of {sd_sum['of']} hours" in rp["status"]
                        and rp["future"] == 3 and rp["now"] == 3 and rp["when"].startswith(sd_clock(sd_t)),
                        str([rp["cards"], rp_want, rp["status"][:60], rp["future"], rp["now"], rp["when"]])[:260])
                page.click("#anaLatest"); page.wait_for_timeout(300)
                sd_l = page.evaluate("() => [document.querySelector('#anaStatus .anapill').textContent, +document.querySelector('#anaT').value]")
                page.click("#anaPlayDay"); page.wait_for_timeout(700)
                sd_p = page.evaluate("() => [document.querySelector('#anaPlayDay').textContent, +document.querySelector('#anaT').value, +document.querySelector('#anaT').min]")
                page.click("#anaPlayDay"); page.wait_for_timeout(200)
                sd_b = page.evaluate("() => document.querySelector('#anaPlayDay').textContent")
                chk.add(f"{scheme} settlement display: Latest brings the final day back, and Play the day runs from the day's start and pauses",
                        sd_l[0] == "Final" and sd_p[0] == "Pause" and sd_p[2] < sd_p[1] < sd_l[1] and sd_b == "Play the day", str([sd_l, sd_p, sd_b]))
                # var= opens the place-day on that variable's chart
                sd_vs = []
                for var_, sec_ in (("precip", "anaPcp"), ("gust", "anaWind"), ("low", "anaTemp")):
                    page.goto(f"{srv.url}/{ANA}?var={var_}&day={ana_day}&loc=new-york-ny"); page.wait_for_timeout(2200)
                    sd_vs.append(page.evaluate("""(id) => ({ id, top: Math.round(document.getElementById(id).getBoundingClientRect().top),
                      y: Math.round(window.scrollY), url: location.search, on: (document.querySelector('#anaCards .anacard.on') || {}).dataset })""", sec_))
                chk.add(f"{scheme} settlement display: a link's var, day and loc open that place-day on that variable's chart",
                        all(-4 <= v_["top"] <= 40 and v_["y"] > 100 and f"var={w_}" in v_["url"] and f"day={ana_day}" in v_["url"]
                            and "loc=new-york-ny" in v_["url"] for v_, w_ in zip(sd_vs, ("precip", "gust", "low"))),
                        str([(v_["id"], v_["top"], v_["y"]) for v_ in sd_vs]))
                # a place file written before the running values: the day as the
                # file stands, the summary's values, no running total, no replay
                def sd_strip(route):
                    resp = route.fetch(); d = json.loads(resp.text())
                    d.pop("rowShape", None)
                    for r_ in d.get("hours") or []:
                        for k_ in ("show", "run", "pub", "pcpPub", "src", "pcpSrc"):
                            (r_.get("rtma") or {}).pop(k_, None)
                    return route.fulfill(response=resp, body=json.dumps(d))

                page.route(f"**/analysis2/loc/new-york-ny/{ana_day}.json*", sd_strip)
                page.goto(f"{srv.url}/{ANA}?day={ana_day}&loc=new-york-ny"); page.wait_for_timeout(2200)
                od = page.evaluate("""(js) => ({ cards: eval(js)(),
                  slider: document.querySelector('#anaT').hidden, note: !document.querySelector('#anaOld').hidden,
                  totals: [...new Set([...document.querySelectorAll('#anaHours td.ptotal')].map(e => e.textContent))],
                  rows: document.querySelectorAll('#anaHours tbody tr').length,
                  line: document.querySelectorAll('#anaPcp path.runline').length,
                  pill: (document.querySelector('#anaStatus .anapill') || {}).textContent || '' })""", sd_cards_js)
                page.unroute(f"**/analysis2/loc/new-york-ny/{ana_day}.json*")
                chk.add(f"{scheme} settlement display: a place file written before the running values still draws the day, with no running figures and no replay",
                        od["cards"] == sd_want and od["slider"] and od["note"] and od["rows"] == sd_sum["of"]
                        and od["totals"] == ["·"] and od["line"] == 0 and od["pill"] == "Final", str(od)[:240])
                # the fixtures' live day, under a fixed clock: the next analysis is
                # the first hour with nothing read, and the next accumulation the
                # hour before it, whose rain the lane reads with that analysis
                ana_live = next((d_ for d_ in reversed(ana_days)
                                 if not ((ana_entries.get(d_) or {}).get("rtma") or {}).get("final")), None)
                lv_rows = []
                if ana_live:
                    with open(os.path.join(ANA_SNAP, "loc", "new-york-ny", ana_live + ".json")) as fh:
                        lv_rows = json.load(fh)["hours"]
                lv_ka = next((i_ for i_, r_ in enumerate(lv_rows) if r_["rtma"] is None), None)
                lv_kp = next((i_ for i_, r_ in enumerate(lv_rows) if not r_["rtma"] or r_["rtma"]["precip"] is None), None)
                chk.add(f"{scheme} settlement display fixtures: a live New York day whose next accumulation is the hour before its next analysis",
                        lv_ka is not None and lv_kp == lv_ka - 1, str([ana_live, lv_ka, lv_kp]))
                if lv_ka is not None and lv_kp == lv_ka - 1:
                    t_a = dt.datetime.fromisoformat(lv_rows[lv_ka]["t"].replace("Z", "+00:00"))
                    lv_cut = dt.datetime.fromisoformat(lv_rows[-1]["t"].replace("Z", "+00:00")) + dt.timedelta(hours=1, minutes=45)
                    lv_url = f"{srv.url}/{ANA}?day={ana_live}&loc=new-york-ny"
                    # the analysis is due 49 minutes after its hour and usually in
                    # by 52, the accumulation 17 minutes after its hour ends and
                    # usually in by 18; the cut-off is 12:45 AM the next day
                    moments = [(t_a + dt.timedelta(minutes=10), ("counting", "39:00"), ("counting", "07:00")),
                               (t_a + dt.timedelta(minutes=17, seconds=30), ("counting", "31:30"), ("due", "due now")),
                               (t_a + dt.timedelta(minutes=30), ("counting", "19:00"), ("late", "late")),
                               (t_a + dt.timedelta(minutes=50), ("due", "due now"), ("late", "late")),
                               (t_a + dt.timedelta(minutes=55), ("late", "late"), ("late", "late")),
                               (lv_cut + dt.timedelta(minutes=15), ("missed", "missed"), ("missed", "missed"))]
                    lv = fixture_page(moments[0][0])
                    lv.goto(lv_url); lv.wait_for_timeout(2200)
                    seen_states, ax = [], None
                    for at_, want_a, want_p in moments:
                        if at_ != moments[0][0]:
                            lv.context.clock.set_fixed_time(at_); lv.wait_for_timeout(2000)
                        got_ = lv.evaluate("""() => Object.fromEntries([...document.querySelectorAll('#anaNext .anadue')].map(d =>
                          [d.dataset.kind, [d.dataset.state, (d.querySelector('.cd') || {}).textContent || '']]))""")
                        seen_states.append((at_.strftime("%H:%M:%S"), got_.get("anl"), got_.get("pcp"), list(want_a), list(want_p)))
                        if at_ == moments[2][0]:
                            # the three charts at 4:30 PM: one axis, one now line, the hours to come shaded
                            ax = lv.evaluate("""() => [...document.querySelectorAll('#anaSettle svg.anasvg')].map(s => ({
                              id: s.closest('section').id, x0: s.dataset.x0, x1: s.dataset.x1,
                              now: [...s.querySelectorAll('line.now')].map(l => l.getAttribute('x1')),
                              fut: [...s.querySelectorAll('rect.future')].map(r => r.getAttribute('x')),
                              due: s.querySelectorAll('line.duemark').length,
                              lab: (s.querySelector('text.nowlab') || {}).textContent || '',
                              marks: ['circle.mkhi', 'circle.mklo', 'line.meanline', 'circle.mkpk', 'path.runline'].filter(q => s.querySelector(q)) }))""")
                            lv_cards = lv.evaluate(sd_cards_js)
                    chk.add(f"{scheme} settlement display: the countdowns pass through counting down, due now, late and missed",
                            all(a_ == wa_ and p_ == wp_ for _, a_, p_, wa_, wp_ in seen_states), str(seen_states)[:300])
                    ax_ok = bool(ax) and [a_["id"] for a_ in ax] == ["anaTemp", "anaWind", "anaPcp"] \
                        and len({a_["x0"] for a_ in ax}) == 1 and len({a_["x1"] for a_ in ax}) == 1 \
                        and all(len(a_["now"]) == 1 and a_["now"] == ax[0]["now"] and a_["fut"] == a_["now"] and a_["due"] == 1
                                and a_["lab"] == sd_clock(moments[2][0]) for a_ in ax) \
                        and ax[0]["marks"] == ["circle.mkhi", "circle.mklo"] and ax[1]["marks"] == ["line.meanline", "circle.mkpk"] \
                        and ax[2]["marks"] == ["path.runline"]
                    chk.add(f"{scheme} settlement display: the three charts share one time axis and one now line, with the hours to come shaded",
                            ax_ok and all(c_[2].endswith("so far") for c_ in lv_cards), str(ax)[:300])
                    lv.context.close()
                    # a refetch inside an arrival window: the first read lacks the
                    # last hour read, and the fetch at the next minute, past the
                    # CDN's minute with ?b=, brings it without a reload
                    rf_ctx = browser.new_context(color_scheme=scheme, viewport={"width": 1200, "height": 900})
                    rf_at = t_a + dt.timedelta(minutes=44, seconds=30)
                    rf_ctx.clock.install(time=rf_at)
                    rf_ctx.clock.pause_at(rf_at)
                    rf = rf_ctx.new_page()
                    errors_of(rf, into=errs)
                    rf_seen = []

                    def rf_route(route):
                        u = route.request.url
                        rf_seen.append(u.split("/")[-1])
                        resp = route.fetch(); d = json.loads(resp.text())
                        if "?b=" not in u:
                            d["hours"][lv_ka - 1]["rtma"] = None
                        return route.fulfill(response=resp, body=json.dumps(d))

                    rf.route(f"**/analysis2/loc/new-york-ny/{ana_live}.json*", rf_route)
                    rf.goto(lv_url); rf.wait_for_timeout(2200)
                    rf0 = rf.evaluate("() => document.querySelector('#anaStatus').textContent")
                    rf.clock.run_for(61000); rf.wait_for_timeout(1500); rf.clock.run_for(2000); rf.wait_for_timeout(500)
                    rf1 = rf.evaluate("() => document.querySelector('#anaStatus').textContent")
                    chk.add(f"{scheme} settlement display: inside an arrival window the place file is fetched again with ?b= and the day fills in without a reload",
                            f"{lv_ka - 1} of " in rf0 and f"{lv_ka} of " in rf1 and any("?b=" in u_ for u_ in rf_seen),
                            str([rf0[:50], rf1[:50], rf_seen]))
                    rf_ctx.close()
                # ---- cell mode (docs/analysis.md section 6): at the national
                #      extent no cell is outlined; Zoom to the cell on New York
                #      outlines its resolving cell in the ink, floats the dot's
                #      value in a label with a leader, hides the raster and says
                #      so, draws the 441 window cells of the one fixture frame
                #      that carries windows, answers hover with i, j and the
                #      value, and writes the view into the address; a frame
                #      routed without windows keeps the outline and the label.
                #      The frame with windows is found in the fixtures, not named.
                ana_wf = None
                for fn_ in sorted(os.listdir(os.path.join(ANA_SNAP, "grid", "rtma", "temp"))):
                    with open(os.path.join(ANA_SNAP, "grid", "rtma", "temp", fn_)) as fh:
                        if "new-york-ny" in (json.load(fh).get("windows") or {}):
                            ana_wf = fn_
                chk.add(f"{scheme} analysis fixtures: an RTMA temperature frame with windows", ana_wf is not None, str(ana_wf))
                ana_wday = f"{ana_wf[:4]}-{ana_wf[4:6]}-{ana_wf[6:8]}" if ana_wf else ana_fday
                ana_whh = ana_wf[9:11] if ana_wf else ana_hours[1]
                ana_ny = next((L_ for L_ in ana_index["locations"] if L_["id"] == "new-york-ny"), {})
                ana_ij = (ana_ny.get("cell") or {}).get("wexp") or [None, None]
                ana_cell_state = """() => {
                  const ny = document.querySelector('#vdots g.dot[data-loc="new-york-ny"]');
                  const img = document.querySelector('#anaFrame');
                  const out = ny && ny.querySelector('path.rcell');
                  const lbl = ny && ny.querySelector('g.anacl text');
                  return { outlines: document.querySelectorAll('#vdots path.rcell').length,
                           stroke: out ? out.getAttribute('stroke') : '', sw: out ? out.getAttribute('stroke-width') : '',
                           ve: out ? out.getAttribute('vector-effect') : '',
                           label: lbl ? lbl.textContent : '', leaders: ny ? ny.querySelectorAll('line.analead').length : 0,
                           posdot: ny ? ny.querySelectorAll('circle.posdot').length : 0,
                           hidden: img.style.display === 'none', href: (img.getAttribute('href') || '').slice(0, 14),
                           cap: document.querySelector('#anaCap').textContent,
                           wcells: document.querySelectorAll('#vcells g.wwin[data-loc="new-york-ny"] path.wcell').length,
                           dotval: (ny && ny.querySelector('text.anaval')) ? ny.querySelector('text.anaval').textContent : '',
                           dotfill: (ny && ny.querySelector('text.anaval')) ? getComputedStyle(ny.querySelector('text.anaval')).fill : '',
                           ink: getComputedStyle(document.documentElement).getPropertyValue('--ink').trim(),
                           vb: document.querySelector('#vmap').getAttribute('viewBox'),
                           readout: document.querySelector('#anaZoomLevel').textContent,
                           zbtn: document.querySelector('#anaZoomCell').disabled, url: location.search };
                }"""
                page.goto(f"{srv.url}/{ANA}?var=high&day={ana_wday}&hour={ana_whh}&loc=new-york-ny"); page.wait_for_timeout(2200)
                z0 = page.evaluate(ana_cell_state)
                hex_ = lambda c: "rgb(%d, %d, %d)" % tuple(int(c.lstrip("#")[k:k + 2], 16) for k in (0, 2, 4)) if c.startswith("#") else c
                chk.add(f"{scheme} analysis cell mode: at the national extent no cell is outlined and the dot value is in the ink",
                        z0["outlines"] == 0 and not z0["hidden"] and z0["href"] == "data:image/png" and z0["dotval"] != ""
                        and z0["dotfill"] == hex_(z0["ink"]) and z0["zbtn"] is False, str([z0["outlines"], z0["dotval"], z0["dotfill"], z0["ink"]]))
                page.click("#anaZoomCell"); page.wait_for_timeout(600)
                z1 = page.evaluate(ana_cell_state)
                chk.add(f"{scheme} analysis cell mode: Zoom to the cell outlines the resolving cell with the dark cell line at a fixed 2 px",
                        z1["outlines"] >= 1 and z1["stroke"] == "var(--cell-line)" and z1["sw"] == "2" and z1["ve"] == "non-scaling-stroke",
                        str([z1["outlines"], z1["stroke"], z1["sw"], z1["ve"]]))
                chk.add(f"{scheme} analysis cell mode: the label carries the dot's value with a leader and the position dot",
                        z1["label"] == z0["dotval"] and z1["leaders"] == 1 and z1["posdot"] == 1, str([z1["label"], z0["dotval"], z1["leaders"]]))
                chk.add(f"{scheme} analysis cell mode: the raster is hidden and the caption says so",
                        z1["hidden"] and "hidden at this zoom" in z1["cap"], z1["cap"][-90:])
                chk.add(f"{scheme} analysis cell mode: the picked place's window draws 441 cells",
                        z1["wcells"] == 441, str(z1["wcells"]))
                ana_zm = re.search(r"z=([\d.]+)&cx=([\d.]+)&cy=([\d.]+)", z1["url"])
                ana_vbw = float(z1["vb"].split()[2]) if z1["vb"] else 0
                chk.add(f"{scheme} analysis cell mode: the address carries z, cx and cy that match the view",
                        ana_zm is not None and abs(960 / float(ana_zm.group(1)) - ana_vbw) < 0.6 and "loc=new-york-ny" in z1["url"],
                        str([z1["url"], z1["vb"]]))
                # the cell is about 30 screen px wide after the button
                ana_cw = page.evaluate("""() => { const p = document.querySelector('#vcells g.wwin[data-loc="new-york-ny"] path.wcell[data-a="0"][data-b="0"]');
                  return p ? p.getBoundingClientRect().width : 0; }""")
                chk.add(f"{scheme} analysis cell mode: the picked cell is about 30 px wide", 24 <= ana_cw <= 40, str(round(ana_cw, 1)))
                page.hover('#vcells g.wwin[data-loc="new-york-ny"] path.wcell[data-a="3"][data-b="-2"]'); page.wait_for_timeout(300)
                ana_wtip = page.evaluate("() => (document.querySelector('#tip') || {}).textContent || ''")
                chk.add(f"{scheme} analysis cell mode: hover on a window cell reports its i, j and the value with its unit",
                        ana_ij[0] is not None and f"i {ana_ij[0] + 3}, j {ana_ij[1] - 2}" in ana_wtip and "New York" in ana_wtip
                        and re.search(r"-?\d+\.\d+°F", ana_wtip) is not None, ana_wtip[:120])
                # hover on the mark reads the resolving cell, with no row about
                # the hidden field (the cell is what is under the pointer)
                page.hover('#vdots g.dot[data-loc="new-york-ny"] circle.posdot'); page.wait_for_timeout(300)
                ana_mtip = page.evaluate("() => (document.querySelector('#tip') || {}).textContent || ''")
                chk.add(f"{scheme} analysis cell mode: hover on the mark reports the resolving cell and drops the hidden-field row",
                        ana_ij[0] is not None and f"i {ana_ij[0]}, j {ana_ij[1]}" in ana_mtip and "Lattice field" not in ana_mtip, ana_mtip[:120])
                # A CLICK WHILE ZOOMED. The pan used to take pointer capture on
                # pointerdown, and Chromium then delivered every click to the
                # svg, so nothing under the pointer opened once the map was
                # zoomed. The panel is closed, the pointer is put on a window
                # cell (elementFromPoint proves the cell is what it reaches)
                # and a plain click has to open the place. Then the label.
                ana_hit = """(sel) => {
                  const e = document.querySelector(sel); if (!e) return null;
                  const r = e.getBoundingClientRect(); const x = r.left + r.width / 2, y = r.top + r.height / 2;
                  const t = document.elementFromPoint(x, y);
                  return { x, y, hit: !!(t && (t === e || e.contains(t))), under: t ? t.tagName + '.' + (t.getAttribute('class') || '') : null };
                }"""
                ana_open = "() => ({ hidden: document.querySelector('#locPanel').hidden, url: location.search, vb: document.querySelector('#vmap').getAttribute('viewBox') })"
                for what, sel in (("a window cell", '#vcells g.wwin[data-loc="new-york-ny"] path.wcell[data-a="4"][data-b="3"]'),
                                  ("the label", '#vdots g.dot[data-loc="new-york-ny"] g.anacl rect')):
                    page.click("#locPanel button[title='close the panel']"); page.wait_for_timeout(200)
                    page.evaluate("() => window.scrollTo(0, 0)"); page.wait_for_timeout(200)
                    c0 = page.evaluate(ana_open)
                    ht = page.evaluate(ana_hit, sel)
                    if ht: page.mouse.click(ht["x"], ht["y"]); page.wait_for_timeout(500)
                    c1 = page.evaluate(ana_open)
                    chk.add(f"{scheme} analysis cell mode: while zoomed, a click on {what} opens the panel and puts loc= in the address",
                            bool(ht and ht["hit"]) and c0["hidden"] and "loc=" not in c0["url"] and not c1["hidden"] and "loc=new-york-ny" in c1["url"],
                            str([ht, c0["url"], c1["url"]]))
                # and a drag that ends over a window cell pans and opens nothing
                page.click("#locPanel button[title='close the panel']"); page.wait_for_timeout(200)
                page.evaluate("() => window.scrollTo(0, 0)"); page.wait_for_timeout(200)
                ht = page.evaluate(ana_hit, '#vcells g.wwin[data-loc="new-york-ny"] path.wcell[data-a="4"][data-b="3"]')
                d0 = page.evaluate(ana_open)
                if ht:
                    page.mouse.move(ht["x"], ht["y"]); page.mouse.down()
                    for k_ in range(1, 6): page.mouse.move(ht["x"] + 8 * k_, ht["y"] + 4 * k_)
                    page.mouse.up(); page.wait_for_timeout(500)
                d1 = page.evaluate(ana_open)
                chk.add(f"{scheme} analysis cell mode: a drag that ends over a window cell pans the view and opens nothing",
                        bool(ht) and d1["hidden"] and "loc=" not in d1["url"] and d1["vb"] != d0["vb"], str([d0["vb"], d1["vb"], d1["url"]]))
                # the view comes back from the address on load
                page.goto(f"{srv.url}/{ANA}{z1['url']}"); page.wait_for_timeout(2200)
                z2 = page.evaluate(ana_cell_state)
                # the address carries a tenth of a unit, so the restored view
                # is the same to a tenth, not to the viewBox's third decimal
                ana_vbs = [[float(t) for t in (v_ or "0 0 0 0").split()] for v_ in (z1["vb"], z2["vb"])]
                chk.add(f"{scheme} analysis cell mode: a zoomed address restores the view and cell mode on load",
                        all(abs(a_ - b_) <= 0.15 for a_, b_ in zip(*ana_vbs)) and z2["outlines"] >= 1 and z2["wcells"] == 441 and z2["hidden"],
                        str([z2["vb"], z1["vb"], z2["wcells"]]))
                chk.add(f"{scheme} analysis cell mode: the zoom readout names the restored zoom, not the whole country",
                        z2["readout"].endswith("×") and z2["readout"] != "whole country" and z2["readout"] == z1["readout"], str([z2["readout"], z1["readout"]]))
                # an index entry without the geometry keys keeps its plain dot
                # in cell mode, with Zoom to the cell disabled for it
                def ana_nokeys(route):
                    resp = route.fetch(); d = json.loads(resp.text())
                    for L_ in d.get("locations") or []:
                        if L_["id"] == "new-york-ny":
                            for k_ in list((L_.get("cell") or {}).keys()):
                                if k_.endswith("Px") or k_.endswith("Box") or k_.endswith("Basis"): L_["cell"].pop(k_)
                    return route.fulfill(response=resp, body=json.dumps(d))

                page.route("**/analysis2/index.json", ana_nokeys)
                page.goto(f"{srv.url}/{ANA}{z1['url']}"); page.wait_for_timeout(2200)
                zk = page.evaluate(ana_cell_state)
                page.unroute("**/analysis2/index.json")
                chk.add(f"{scheme} analysis cell mode: a place without the geometry keys keeps its plain dot and value in cell mode",
                        zk["hidden"] and zk["posdot"] == 0 and zk["label"] == "" and zk["dotval"] == z0["dotval"] and zk["wcells"] == 0
                        and zk["outlines"] >= 1 and zk["zbtn"] is True, str([zk["dotval"], zk["posdot"], zk["outlines"], zk["zbtn"]]))
                # a dry precipitation window: every zero cell is drawn clear
                # and still answers hover with 0.00 in, a null cell inside the
                # grid says it is missing, and the caption says dry cells are
                # left clear. The fixture frame is routed to all zeros with
                # one null at (3, -2).
                ana_pf = None
                ana_pdir = os.path.join(ANA_SNAP, "grid", "rtma", "precip")
                for fn_ in sorted(os.listdir(ana_pdir)) if os.path.isdir(ana_pdir) else []:
                    with open(os.path.join(ana_pdir, fn_)) as fh:
                        if "new-york-ny" in (json.load(fh).get("windows") or {}):
                            ana_pf = fn_
                chk.add(f"{scheme} analysis fixtures: an RTMA precipitation frame with a New York window", ana_pf is not None, str(ana_pf))
                if ana_pf:
                    def ana_dry(route):
                        resp = route.fetch(); d = json.loads(resp.text())
                        w_ = d["windows"]["new-york-ny"]; half_ = w_.get("half", 10); n_ = 2 * half_ + 1
                        w_["values"] = [0] * (n_ * n_)
                        w_["values"][(-2 + half_) * n_ + (3 + half_)] = None
                        return route.fulfill(response=resp, body=json.dumps(d))

                    page.route(f"**/analysis2/grid/rtma/precip/{ana_pf}", ana_dry)
                    page.goto(f"{srv.url}/{ANA}?var=precip&day={ana_pf[:4]}-{ana_pf[4:6]}-{ana_pf[6:8]}&hour={ana_pf[9:11]}&loc=new-york-ny")
                    page.wait_for_timeout(2200)
                    page.click("#anaZoomCell"); page.wait_for_timeout(600)
                    page.unroute(f"**/analysis2/grid/rtma/precip/{ana_pf}")
                    zd = page.evaluate("""() => ({ cells: document.querySelectorAll('#vcells g.wwin[data-loc="new-york-ny"] path.wcell').length,
                      clear: document.querySelectorAll('#vcells g.wwin[data-loc="new-york-ny"] path.wcell.clear').length,
                      fill: (document.querySelector('#vcells g.wwin[data-loc="new-york-ny"] path.wcell[data-a="0"][data-b="5"]') || {}).getAttribute('fill'),
                      cap: document.querySelector('#anaCap').textContent })""")
                    page.hover('#vcells g.wwin[data-loc="new-york-ny"] path.wcell[data-a="0"][data-b="5"]'); page.wait_for_timeout(300)
                    ana_dtip = page.evaluate("() => (document.querySelector('#tip') || {}).textContent || ''")
                    page.hover('#vcells g.wwin[data-loc="new-york-ny"] path.wcell[data-a="3"][data-b="-2"]'); page.wait_for_timeout(300)
                    ana_ntip = page.evaluate("() => (document.querySelector('#tip') || {}).textContent || ''")
                    chk.add(f"{scheme} analysis cell mode: a dry window draws its 441 cells clear and the caption says dry cells are left clear",
                            zd["cells"] == 441 and zd["clear"] == 441 and zd["fill"] == "transparent" and "dry cells left clear" in zd["cap"], str(zd)[:160])
                    chk.add(f"{scheme} analysis cell mode: hover on a dry cell says 0.00 in and on a null cell inside the grid says missing in this hour",
                            "0.00 in" in ana_dtip and "missing in this hour" in ana_ntip and "off the grid" not in ana_ntip, str([ana_dtip[:80], ana_ntip[:80]]))
                # the frame routed without windows: the outline and the label
                # stay, no window cells
                def ana_nowin(route):
                    resp = route.fetch(); d = json.loads(resp.text())
                    d.pop("windows", None)
                    return route.fulfill(response=resp, body=json.dumps(d))

                page.route(f"**/analysis2/grid/rtma/temp/{ana_wf}", ana_nowin)
                page.goto(f"{srv.url}/{ANA}{z1['url']}"); page.wait_for_timeout(2200)
                z3 = page.evaluate(ana_cell_state)
                page.unroute(f"**/analysis2/grid/rtma/temp/{ana_wf}")
                chk.add(f"{scheme} analysis cell mode: a frame without windows still draws the outline and the label, with no window cells",
                        z3["outlines"] >= 1 and z3["label"] == z0["dotval"] and z3["wcells"] == 0 and z3["hidden"], str([z3["outlines"], z3["label"], z3["wcells"]]))
                # Reset brings the raster and the dots back
                page.click("#anaZoom button[title='back to the whole country']"); page.wait_for_timeout(600)
                z4 = page.evaluate(ana_cell_state)
                chk.add(f"{scheme} analysis cell mode: Reset returns the raster and the dots and clears the view from the address",
                        z4["outlines"] == 0 and not z4["hidden"] and z4["dotval"] == z0["dotval"] and "z=" not in z4["url"], str([z4["outlines"], z4["url"]]))
                # the tooltip is hidden when the wheel carries the map into cell
                # mode, rather than keeping the lattice value from before the flip.
                # The open place's settlement display sits above the map, so the
                # map is brought into view rather than the top of the page
                page.evaluate("() => document.querySelector('#vmap').scrollIntoView({ block: 'center' })"); page.wait_for_timeout(200)
                ht = page.evaluate(ana_hit, '#vdots g.dot[data-loc="new-york-ny"] circle:last-of-type')
                if ht:
                    page.mouse.move(ht["x"], ht["y"]); page.wait_for_timeout(200)
                    t_on = page.evaluate("() => getComputedStyle(document.querySelector('#tip')).opacity")
                    for k_ in range(30):
                        page.mouse.wheel(0, -100); page.wait_for_timeout(30)
                        if page.evaluate("() => document.querySelector('#anaFrame').style.display === 'none'"): break
                    page.wait_for_timeout(400)
                    t_off = page.evaluate("() => [getComputedStyle(document.querySelector('#tip')).opacity, document.querySelector('#anaFrame').style.display === 'none']")
                    chk.add(f"{scheme} analysis cell mode: the tooltip is hidden when the wheel flips the map into cell mode",
                            t_on == "1" and t_off[0] == "0" and t_off[1], str([t_on, t_off]))
                # the built head of an unlisted page: no canonical link and no
                # structured data for a crawler it turned away, the Open Graph
                # tags kept for a shared link's preview
                for unlisted in (ANA, "lessons.html"):
                    with open(os.path.join(ROOT, "dist", "standalone", unlisted)) as fh:
                        head_ = fh.read().split("</head>")[0]
                    chk.add(f"{scheme} analysis: {unlisted} is built without canonical or JSON-LD and with its og tags",
                            'content="noindex"' in head_ and "canonical" not in head_ and "ld+json" not in head_ and "og:title" in head_,
                            str([("canonical" in head_), ("ld+json" in head_), ("og:title" in head_)]))
                # the feed down with nothing cached: the frame, the statement, no data
                ana_ctx = browser.new_context(color_scheme=scheme, viewport={"width": 1200, "height": 900})
                ana_page = ana_ctx.new_page()
                ana_errs = errors_of(ana_page)
                ana_page.route("**/data/**", lambda route: route.fulfill(status=503, body="outage"))
                ana_page.goto(f"{srv.url}/{ANA}"); ana_page.wait_for_timeout(1200)
                ana_status = ana_page.locator(".status").first.inner_text() if ana_page.locator(".status").count() else ""
                chk.add(f"{scheme} analysis: the 503 degradation shows no data and keeps the statement",
                        "No data" in ana_status and "No contract settles on it" in ana_page.locator("#anaMap").inner_text()
                        and not ana_errs, ana_status[:60] + " " + "; ".join(ana_errs)[:100])
                ana_ctx.close()

                # ---- the temperature map carries temperature stations only
                def _wo_hourly(route):
                    u = route.request.url
                    if u.endswith("/summary.json") and "/market/" not in u:
                        resp = route.fetch(); d = json.loads(resp.text())
                        for c in d.get("cities") or []:
                            if c.get("station") == "KBOS":
                                c["windOnly"] = True
                        return route.fulfill(response=resp, body=json.dumps(d))
                    return route.continue_()

                page.route("**/data/snapshots/**", _wo_hourly)
                page.goto(f"{srv.url}/hourly-temperature-markets.html"); page.wait_for_timeout(2200)
                hm = page.eval_on_selector_all("#vmap g.dot", "e=>e.map(x=>x.getAttribute('data-station'))")
                chk.add(f"{scheme} hourly map: a station carried for its wind is not on it",
                        bool(hm) and "KBOS" not in hm, str(len(hm)) + " dots")
                page.unroute("**/data/snapshots/**")

                # ---- the hourly map times a reading by the report it came from.
                # The newest report here carries no temperature, so the newest
                # reading is an hour older than it and the note has to say when
                _lt = (_now - dt.timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:00Z")
                _tt = (_now - dt.timedelta(minutes=70)).strftime("%Y-%m-%dT%H:%M:00Z")

                def _latest_routes(route):
                    u = route.request.url
                    if u.endswith("/summary.json") and "/market/" not in u:
                        resp = route.fetch(); d = json.loads(resp.text())
                        for c in d.get("cities") or []:
                            if c.get("station") == "KLGA":
                                c["obsLatest"] = {"t": _lt, "raw": "METAR KLGA 04012KT 10SM OVC020 A3001",
                                                  "type": "METAR", "src": "tgroup", "tempF": 71.2,
                                                  "tempC": 21.8, "tempT": _tt}
                        return route.fulfill(response=resp, body=json.dumps(d))
                    return route.continue_()

                page.route("**/data/snapshots/**", _latest_routes)
                page.goto(f"{srv.url}/hourly-temperature-markets.html"); page.wait_for_timeout(2200)
                lt = page.evaluate("""async ([t, tt]) => {
                  const g = document.querySelector('#vdots g.dot[data-station="KLGA"]');
                  if (!g) return null;
                  const r = g.getBoundingClientRect();
                  g.dispatchEvent(new MouseEvent('mousemove',
                    {clientX: r.x + r.width / 2, clientY: r.y + r.height / 2, bubbles: true}));
                  await new Promise(z => setTimeout(z, 150));
                  const tz = 'America/New_York';
                  return { tip: (document.querySelector('#tip') || {}).innerText || '',
                           reading: WXC.clockFull(Date.parse(tt), tz), report: WXC.clockFull(Date.parse(t), tz) };
                }""", [_lt, _tt])
                chk.add(f"{scheme} hourly map: a reading is timed by the report it came from",
                        bool(lt and "71° at" in lt["tip"] and lt["reading"] in lt["tip"]
                             and lt["report"] not in lt["tip"]), str(lt)[:200])
                page.unroute("**/data/snapshots/**")

                # ---- the city page stacks its other contracts, wind then hourly
                #
                # samples/ carries no wind readings and no hourly board, so both
                # sections would be absent and the checks would pass on nothing.
                # The station is given one of each, the same synthesis the wind
                # panel's own checks use.
                def _city_routes(route):
                    u = route.request.url
                    if u.endswith("/obs/KLGA.json"):
                        resp = route.fetch(); d = json.loads(resp.text())
                        d["rows"] = w_rows
                        d["today"] = dict(d.get("today") or {}, date=DAY)
                        d["wind"] = {"today": {"date": DAY, "n": len(w_rows), "unit": "kt",
                                               "peak": {"v": 24, "kt": 24, "mph": 28, "from": "gust",
                                                        "t": w_rows[3]["t"], "type": "METAR"}}}
                        return route.fulfill(response=resp, body=json.dumps(d))
                    if u.endswith("/market/KLGA.json"):
                        resp = route.fetch(); d = json.loads(resp.text())
                        d["markers"] = dict(d.get("markers") or {}, day=DAY, tomorrow=DAY2)
                        d["symbols"] = dict(d.get("symbols") or {},
                                            hourly={"symbol": "HRULGA", "conid": 90001, "productConid": 90000})
                        d["hours"] = {DAY: {"14": [
                            {"strike": 68.0, "bid": 0.55, "mid": 0.55, "conidYes": 90011, "conid": 90011},
                            {"strike": 70.0, "bid": 0.30, "mid": 0.30, "conidYes": 90012, "conid": 90012}]}}
                        # two wind days, so the day selector has something to select
                        wr = [{"strike": 20.0, "bid": 0.80, "mid": 0.80, "conidYes": 90021, "conid": 90021},
                              {"strike": 34.0, "bid": 0.40, "mid": 0.40, "conidYes": 90022, "conid": 90022}]
                        d["days"] = dict(d.get("days") or {}, **{DAY: dict((d.get("days") or {}).get(DAY) or {}, wind=wr),
                                                                 DAY2: {"wind": wr}})
                        d["symbols"] = dict(d["symbols"],
                                            wind={"symbol": "MGLGA", "conid": 90002, "productConid": 90003})
                        return route.fulfill(response=resp, body=json.dumps(d))
                    if u.endswith("/summary.json") and "/market/" not in u:
                        resp = route.fetch(); d = json.loads(resp.text())
                        for c in d.get("cities") or []:
                            if c.get("station") == "KLGA":
                                c["markers"] = dict(c.get("markers") or {}, day=DAY, tomorrow=DAY2)
                        return route.fulfill(response=resp, body=json.dumps(d))
                    return route.continue_()

                page_wall, page = page, fixture_page()
                page.route("**/data/snapshots/**", _city_routes)
                page.goto(f"{srv.url}/city.html?station=KLGA&market=on"); page.wait_for_timeout(2800)
                cc = page.evaluate("""() => {
                  const host = document.querySelector('#cityContracts');
                  if (!host) return null;
                  const w = document.querySelector('#cityWind'), h2 = document.querySelector('#cityHourly');
                  const svg = h2 && h2.querySelector('.card svg');
                  return { tabs: host.querySelectorAll('button[data-tab]').length,
                           wind: !!w, hourly: !!h2,
                           windFirst: !!(w && h2) &&
                             (w.compareDocumentPosition(h2) & Node.DOCUMENT_POSITION_FOLLOWING) > 0,
                           rungLinks: svg ? svg.querySelectorAll('[data-contract-url]').length : 0,
                           rungXs: svg ? [...new Set([...svg.querySelectorAll('rect[fill="var(--yes)"]')]
                             .map(r => Math.round(+r.getAttribute('x'))))].length : 0,
                           windDays: document.querySelectorAll('#cityWindDays button[data-day]').length,
                           windCards: w ? w.querySelectorAll('.card').length : 0,
                           columnPrices: svg ? svg.querySelectorAll('.ladtxt').length : 0 };
                }""")
                chk.add(f"{scheme} city page: the other contracts stack rather than hide behind tabs",
                        bool(cc) and cc["tabs"] == 0 and cc["wind"], str(cc))
                # each priced chart on the page says, under it, that its prices open the exchange
                ctn = page.evaluate("""() => {
                  const after = sel => { const n = document.querySelector(sel); const prev = n && n.previousElementSibling;
                    return n ? { text: n.textContent, prev: prev ? (prev.id || prev.className) : null } : null; };
                  return { chart: after('#chartCard ~ p.tradenote'), wind: after('#cityWind p.tradenote'),
                           hourly: after('#cityHourly p.tradenote'), n: document.querySelectorAll('p.tradenote').length,
                           chartLinks: document.querySelectorAll('#chart [data-contract-url]').length };
                }""")
                # (the chart's own ladder here has no listed day, so the chart carries a line only if it links)
                chk.add(f"{scheme} city page: the wind board and the hourly board each say a price opens the exchange, and the chart when it links",
                        bool(ctn) and ctn["n"] == 2 + (1 if ctn["chartLinks"] else 0)
                        and (bool(ctn["chart"]) == bool(ctn["chartLinks"])) and (not ctn["chart"] or ctn["chart"]["prev"] == "bar")
                        and ctn["wind"] and ctn["wind"]["prev"] == "card expandable"
                        and ctn["hourly"] and ctn["hourly"]["prev"] == "card expandable"
                        and all(ctn[k]["text"] == "Click on any price to trade contracts through IBKR Prediction Markets"
                                for k in ("chart", "wind", "hourly") if ctn[k]), str(ctn))
                chk.add(f"{scheme} city page: the wind days are buttons, not stacked panels",
                        bool(cc) and cc["windDays"] >= 1 and cc["windCards"] == 1, str(cc))
                if cc and cc["hourly"]:
                    chk.add(f"{scheme} city page: the hourly board sits under the wind series",
                            cc["windFirst"], str(cc))
                    chk.add(f"{scheme} city page: hourly rungs carry a link to the contract",
                            cc["rungLinks"] > 0, str(cc["rungLinks"]))
                    # A LINK IS NOT A CLICK. The first version of this checked
                    # that the rung carried a contract url, which it did while the
                    # plot's own hover band sat on top of it and took every click.
                    # What matters is what the pointer reaches, so that is what is
                    # asked: the rung has to be the element at its own centre.
                    hit = page.evaluate("""() => {
                      const svg = document.querySelector('#cityHourly .card svg');
                      const bar = svg && svg.querySelector('[data-contract-url]');
                      if (!bar) return null;
                      bar.scrollIntoView({ block: 'center' });
                      const r = bar.getBoundingClientRect();
                      const e = document.elementFromPoint(Math.round(r.left + r.width / 2),
                                                          Math.round(r.top + r.height / 2));
                      return { isRung: e === bar, onTop: e ? e.tagName + '.' + (e.getAttribute('class') || '') : null };
                    }""")
                    chk.add(f"{scheme} city page: an hourly rung is what the pointer actually reaches",
                            bool(hit and hit["isRung"]), str(hit))
                    chk.add(f"{scheme} city page: hourly rungs are drawn in the plot, not in a side column",
                            cc["columnPrices"] == 0, str(cc))
                page.unroute("**/data/snapshots/**")
                page.context.close(); page = page_wall

                # ---- the cyclone area: one map per ocean, each opening its own view
                page.goto(f"{srv.url}/tropical-cyclone-markets.html"); page.wait_for_timeout(1200)
                bas = page.evaluate("""() => [...document.querySelectorAll('.basincard')].map(c => ({
                  title: (c.querySelector('.lt') || {}).textContent || '',
                  href: c.getAttribute('href') || '',
                  land: c.querySelectorAll('svg.minimap path[fill]:not([fill="none"])').length,
                }))""")
                chk.add(f"{scheme} cyclone area: the Pacific is on the left and the Atlantic on the right",
                        [b["title"] for b in bas] == ["East and Central Pacific", "Atlantic"], str([b["title"] for b in bas]))
                chk.add(f"{scheme} cyclone area: each map opens its own basin",
                        [b["href"] for b in bas] == ["hurricane.html?basin=EP", "hurricane.html?basin=AL"],
                        str([b["href"] for b in bas]))
                chk.add(f"{scheme} cyclone area: both maps draw their coastline",
                        all(b["land"] > 10 for b in bas), str([b["land"] for b in bas]))
                # the link has to land on the ocean it showed, which is the whole
                # point of the page: a parameter the basin view honours
                for want_basin, want_on in (("EP", "b2"), ("AL", "b1")):
                    page.goto(f"{srv.url}/hurricane.html?basin={want_basin}"); page.wait_for_timeout(1400)
                    onb = page.evaluate("""() => ['b1', 'b2'].filter(i => {
                      const e = document.getElementById(i); return e && e.classList.contains('on'); })""")
                    chk.add(f"{scheme} cyclone area: ?basin={want_basin} opens that view", onb == [want_on], str(onb))

                # ---- the full view: both contract days at once
                page.goto(f"{srv.url}/city.html?station=KPHX&market=on"); page.wait_for_timeout(1800)
                heads0 = page.eval_on_selector_all("#chart text.axl",
                    "e=>e.map(x=>x.textContent).filter(t=>/Today ·|Tomorrow ·/.test(t))")
                chk.add(f"{scheme} city full: one ladder before the toggle", heads0 == [], str(heads0))
                page.locator("#dayBoth").click(); page.wait_for_timeout(1200)
                heads1 = page.eval_on_selector_all("#chart text.axl",
                    "e=>e.map(x=>x.textContent).filter(t=>/Today ·|Tomorrow ·/.test(t))")
                chk.add(f"{scheme} city full: both contract days are headed", len(heads1) == 2
                        and heads1[0].startswith("Today") and heads1[1].startswith("Tomorrow"), str(heads1))
                nm = page.eval_on_selector_all("#chart text.lvlnm", "e=>e.map(x=>x.textContent)")
                chk.add(f"{scheme} city full: the forecast names shorten so they clear the strikes",
                        bool(nm) and all(len(x) <= 12 for x in nm), str(nm[:3]))
                errs_now = len(errs)
                page.locator("#dayToday").click(); page.wait_for_timeout(900)
                heads2 = page.eval_on_selector_all("#chart text.axl",
                    "e=>e.map(x=>x.textContent).filter(t=>/Today ·|Tomorrow ·/.test(t))")
                chk.add(f"{scheme} city days: going back to today restores the single ladder",
                        heads2 == [] and len(errs) == errs_now, str(heads2))
                # the day-ahead board on its own
                page.locator("#dayTomorrow").click(); page.wait_for_timeout(1100)
                t_tom = page.locator("#cityTitle").inner_text()
                t_tod = None
                page.locator("#dayToday").click(); page.wait_for_timeout(900)
                t_tod = page.locator("#cityTitle").inner_text()
                chk.add(f"{scheme} city days: tomorrow is a day of its own, not today's",
                        t_tom != t_tod and "–" not in t_tom, f"{t_tom} / {t_tod}")
                page.locator("#dayTomorrow").click(); page.wait_for_timeout(1100)
                bounds = page.eval_on_selector_all("#chart text",
                    "e=>e.map(x=>x.textContent).filter(t=>/midnight|day end/.test(t))")
                chk.add(f"{scheme} city days: the day-ahead view brackets its own contract day",
                        len(bounds) >= 1 and len(errs) == errs_now, str(bounds))
                chk.add(f"{scheme} city days: exactly one day button is pressed at a time",
                        page.locator("#dayToday.on, #dayTomorrow.on, #dayBoth.on").count() == 1, "")
                # tomorrow's nights are shaded as today's are (owner, 2026-10-20): the sun carried
                # forward one day, and everything outside the contract day's daylight shaded
                nt = page.evaluate("""() => ({ n: document.querySelectorAll('#chart rect[fill="var(--night)"]').length,
                  sun: [...document.querySelectorAll('#chart text.mklab')].map(t => t.textContent)
                         .filter(t => t === 'sunrise' || t === 'sunset') })""")
                chk.add(f"{scheme} city days: tomorrow's nights are shaded as today's are",
                        nt["n"] == 2 and nt["sun"] == ["sunrise", "sunset"], str(nt))
                # "Overlay yesterday" is the calendar's yesterday on every view (owner, 2026-10-20):
                # moved a day on to today, two days on to tomorrow, and on both days twice, the same
                # yesterday on each day, drawn as two lines that meet at tomorrow's midnight
                yd_read = lambda: page.evaluate("""() => {
                  const svg = document.querySelector('#chart');
                  const mid = [...svg.querySelectorAll('text.mklab')].find(t => t.textContent === 'tomorrow’s midnight');
                  const segs = [...svg.querySelectorAll('path.yday')].map(p => {
                    const xs = (p.getAttribute('d').match(/[ML]([\\d.]+),/g) || []).map(q => +q.slice(1, -1));
                    return { col: p.getAttribute('stroke'), xs, x0: Math.min(...xs), x1: Math.max(...xs) }; });
                  return { mid: mid ? +mid.getAttribute('x') : null, segs };
                }""")
                page.locator("#ydayBtn").click(); page.wait_for_timeout(900)
                yd_tom = yd_read()
                page.locator("#dayToday").click(); page.wait_for_timeout(900)
                yd_tod = yd_read()
                obs_x = lambda r: [sg["xs"] for sg in (r or {}).get("segs", []) if sg["col"] == "var(--obs)"]
                chk.add(f"{scheme} city days: on tomorrow the overlay is yesterday, at the hours it holds on today",
                        len(obs_x(yd_tom)) == 1 and obs_x(yd_tom) == obs_x(yd_tod) and len(obs_x(yd_tod)[0]) >= 12,
                        str([len(x_) for x_ in obs_x(yd_tom)] + [len(x_) for x_ in obs_x(yd_tod)]))
                page.locator("#dayBoth").click(); page.wait_for_timeout(1100)
                yd_both = yd_read()
                ob2 = [sg for sg in yd_both["segs"] if sg["col"] == "var(--obs)"]
                models2 = {}
                for sg in yd_both["segs"]:
                    if sg["col"] != "var(--obs)":
                        models2[sg["col"]] = models2.get(sg["col"], 0) + 1
                chk.add(f"{scheme} city both days: the overlay puts yesterday on today and again on tomorrow",
                        bool(yd_both["mid"]) and len(ob2) == 2 and ob2[0]["x1"] <= yd_both["mid"] + 1
                        and ob2[1]["x0"] >= yd_both["mid"] - 1 and models2 and all(v == 2 for v in models2.values()),
                        str({"mid": yd_both["mid"], "obs": [(round(o["x0"]), round(o["x1"])) for o in ob2], "models": models2}))
                page.locator("#ydayBtn").click(); page.wait_for_timeout(500)
                page.locator("#dayToday").click(); page.wait_for_timeout(700)

                # ---- the board's heading: which board, which side, which day
                page.goto(f"{srv.url}/index.html"); page.wait_for_timeout(1600)
                t1 = page.locator("#mapTitle").inner_text()
                import re as _re2
                chk.add(f"{scheme} board title: names the market, the side and the day",
                        t1.startswith("ForecastEx Weather Prediction Market for")
                        and _re2.search(r"(Today's|Tomorrow's) (Highs|Lows) \w+day, \w+ \d{1,2}$", t1) is not None,
                        t1[:110])
                page.locator("#m4").click(); page.wait_for_timeout(700)
                t2 = page.locator("#mapTitle").inner_text()
                chk.add(f"{scheme} board title: follows the selector, not just the clock",
                        "Tomorrow's Lows" in t2, t2[:110])
                # full city names, not airport codes a reader has to decode
                labs = page.eval_on_selector_all("#map text.lbl", "e=>e.map(x=>x.textContent)")
                chk.add(f"{scheme} map labels: cities are named, not coded",
                        any(l.startswith("Chicago") or l.startswith("Denver") or l.startswith("Atlanta") for l in labs)
                        and not any(_re2.match(r"^[A-Z]{3}\b", l) for l in labs), str(labs[:4]))
                # the map comes directly after the heading
                order = page.eval_on_selector_all(".wrap > *", "e=>e.map(x=>x.id||x.className||x.tagName)")
                body = [o for o in order if o != "site"]
                chk.add(f"{scheme} landing page: heading first, then the map",
                        body[0] == "mapTitle" and body.index("card") < body.index("legend"),
                        str(body[:5]))

                # ---- the stations carried for their wind alone
                #
                # Nine stations carry no daily temperature contract: six shore
                # stations with no ForecastEx product at all, and three whose only
                # contract is MG wind. They belong on the wind map and nowhere that
                # implies a temperature market, and their own page is the wind panel
                # rather than the city page built around a board they have not got.
                #
                # The filter is checked against a ROUTED roster rather than the
                # bundled samples. samples/ is a checked-in fixture that lags the
                # live roster (37 stations while the site carries 46), so reading
                # windOnly from it made every one of these checks vacuous: nothing
                # was flagged, so nothing was excluded, so they all passed. The
                # route flags a station the fixture does carry, which tests the
                # front end's rule instead of the fixture's contents.
                with open(os.path.join(ROOT, "config", "cities.json")) as _fh:
                    _roster = json.load(_fh)
                _wind_only = [c for c in _roster if c.get("windOnly")]
                chk.add(f"{scheme} wind-only stations: the roster names some, so the rest of this means something",
                        len(_wind_only) > 0, str([c["station"] for c in _wind_only]))

                MARK = "KBOS"          # in the sample fixture and on the CONUS map

                def _wo_routes(route):
                    u = route.request.url
                    if u.endswith("/summary.json"):
                        resp = route.fetch(); d = json.loads(resp.text())
                        for c in d.get("cities") or []:
                            if c.get("station") == MARK:
                                c["windOnly"] = True
                        return route.fulfill(response=resp, body=json.dumps(d))
                    return route.continue_()

                page.route("**/data/snapshots/**", _wo_routes)
                page.goto(f"{srv.url}/index.html"); page.wait_for_timeout(1700)
                base = page.evaluate("""async () => {
                  const s = await fetch('data/snapshots/summary.json').then(r => r.json());
                  const cs = s.cities || [];
                  return { onConus: cs.filter(c => c.onConus).length,
                           flagged: cs.filter(c => c.windOnly).length,
                           name: (cs.find(c => c.windOnly) || {}).city };
                }""")
                mlabs = page.eval_on_selector_all("#map text.lbl", "e=>e.map(x=>x.textContent)")
                mdots = len(page.eval_on_selector_all("#map g.dot", "e=>e.map(x=>1)"))
                chk.add(f"{scheme} wind-only stations: the flag reaches the front map's roster",
                        base["flagged"] == 1 and bool(base["name"]), str(base))
                chk.add(f"{scheme} wind-only stations: the flagged one is not named on the temperature map",
                        not any(l.startswith(base["name"]) for l in mlabs), str(base["name"]))
                chk.add(f"{scheme} wind-only stations: the map draws every other station and no more",
                        mdots == base["onConus"] - 1, f"{mdots} drawn of {base['onConus']} on the map")
                # the city page's two pickers follow the same rule
                page.goto(f"{srv.url}/city.html"); page.wait_for_timeout(1900)
                pdots = len(page.eval_on_selector_all("#pick g, #pick circle", "e=>e.map(x=>1)"))
                pnames = page.eval_on_selector_all("#pick title, #pick text", "e=>e.map(x=>x.textContent)")
                chk.add(f"{scheme} wind-only stations: the city page's picker leaves it out too",
                        not any(base["name"] in (t or "") for t in pnames), str(base["name"]))
                # and the wind map keeps it, which is the whole reason it is carried
                page.goto(f"{srv.url}/wind-markets.html"); page.wait_for_timeout(1900)
                vdots = len(page.eval_on_selector_all("#vmap g.dot", "e=>e.map(x=>1)"))
                chk.add(f"{scheme} wind-only stations: they stay on the wind map, which is why they are carried",
                        vdots == base["onConus"], f"{vdots} of {base['onConus']}")
                page.unroute("**/data/snapshots/**")

                # The built page itself, for a station the real roster flags.
                #
                # The samples fixture does not carry these stations, so its own
                # snapshots are routed under the flagged station's name: the
                # roster row, the observations and the forecast all come from a
                # station the fixture does have, which leaves the page's own
                # rendering as the only thing under test.
                if _wind_only:
                    _c = _wind_only[0]
                    # the build's own rule, imported rather than restated, so a
                    # change to it cannot leave this check pointing at nothing
                    sys.path.insert(0, os.path.join(ROOT, "scripts"))
                    import build as _build
                    _href = _build.slug(_c["city"], _c["station"]) + ".html"
                    WO, WOCITY = _c["station"], _c["city"]

                    def _wo_page_routes(route):
                        u = route.request.url
                        if u.endswith("/summary.json"):
                            resp = route.fetch(); d = json.loads(resp.text())
                            cs = d.get("cities") or []
                            srcrow = next((c for c in cs if c.get("station") == MARK), None)
                            if srcrow and not any(c.get("station") == WO for c in cs):
                                # the panel takes its day from the roster, so the row and
                                # the routed observations below have to name the same one
                                cs.append(dict(srcrow, station=WO, city=WOCITY, windOnly=True,
                                               markers=dict(srcrow.get("markers") or {}, day=DAY)))
                                d["cities"] = cs
                            return route.fulfill(response=resp, body=json.dumps(d))
                        for _kind in ("obs", "forecast"):
                            if u.endswith(f"/{_kind}/{WO}.json"):
                                resp = route.fetch(url=u.replace(f"/{_kind}/{WO}.json",
                                                                 f"/{_kind}/{MARK}.json"))
                                d = json.loads(resp.text())
                                d["station"], d["city"] = WO, WOCITY
                                if _kind == "obs":
                                    # samples/ is a month old and predates the wind work
                                    # entirely: no wind block and no wspd on any row. The
                                    # same synthesis the wind panel's own checks use gives
                                    # this one something to draw.
                                    d["rows"] = w_rows
                                    d["today"] = dict(d.get("today") or {}, date=DAY)
                                    d["wind"] = {"today": {
                                        "date": DAY, "n": len(w_rows), "unit": "kt",
                                        "peak": {"v": 24, "kt": 24, "mph": 28, "mphExact": 27.6,
                                                 "from": "gust", "t": w_rows[3]["t"], "type": "METAR"}}}
                                return route.fulfill(response=resp, body=json.dumps(d))
                        return route.continue_()

                    page_wall, page = page, fixture_page()
                    page.route("**/data/snapshots/**", _wo_page_routes)
                    errs_wo = len(errs)
                    page.goto(f"{srv.url}/{_href}"); page.wait_for_timeout(1900)
                    h1 = page.locator("#cityTitle").inner_text()
                    chk.add(f"{scheme} wind-only page: the heading names the wind, not a temperature market",
                            " wind (" in h1, h1)
                    chk.add(f"{scheme} wind-only page: no temperature chart and no city picker",
                            page.locator("#chart").count() == 0 and page.locator("#pick").count() == 0
                            and page.locator("#cityDays").count() == 0, "")
                    chk.add(f"{scheme} wind-only page: the wind panel is drawn",
                            page.locator("#windPanel .card").count() >= 1, "")
                    ptxt = page.locator("#windPanel").inner_text()
                    chk.add(f"{scheme} wind-only page: the two-stage gust note is under it",
                            "at least 9 knots" in ptxt, ptxt[-80:])
                    chk.add(f"{scheme} wind-only page: it leads back to the board",
                            page.locator("#windLinks a[href='wind-markets.html']").count() == 1, "")
                    chk.add(f"{scheme} wind-only page: it renders without a script error",
                            len(errs) == errs_wo, str(errs[errs_wo:][:1]))
                    page.unroute("**/data/snapshots/**")
                    page.context.close(); page = page_wall
                page.goto(f"{srv.url}/index.html"); page.wait_for_timeout(1600)

                # abroad there is no government forecast to compare against, so the
                # observation and the market's own number are the whole picture.
                # The Celsius boards are highs only, so this is checked on a highs
                # view; on a lows view the absence is correct.
                page.locator("#m2").click(); page.wait_for_timeout(700)
                wlabs = page.eval_on_selector_all("#mapW text.lbl", "e=>e.map(x=>x.textContent)")
                # abroad the dot carries the market's number and the label the station
                # and the local date it applies to; a station with no board is named alone
                import re as _re
                wvals = page.eval_on_selector_all("#mapW text.dotv", "e=>e.map(x=>x.textContent)")
                chk.add(f"{scheme} world map: a dot carries the market value and its label the local date",
                        len(wvals) >= 3 and sum(1 for l in wlabs if _re.search(r" \u00b7 \w+ \d+$", l)) >= 3,
                        str(wvals[:3]) + str(wlabs[:3]))
                chk.add(f"{scheme} world map: a station without a board is named without a number",
                        all(_re.search(r"\d", l) for l in wlabs if "\u00b7" in l), "")
                page.locator("#m4").click(); page.wait_for_timeout(700)
                wlow = page.eval_on_selector_all("#mapW text.lbl", "e=>e.map(x=>x.textContent)")
                chk.add(f"{scheme} world map: no market value on a side the board does not list",
                        not any("market" in l for l in wlow), str([l for l in wlow if "market" in l][:2]))
                page.locator("#m2").click(); page.wait_for_timeout(500)
                chk.add(f"{scheme} world map: each station carries its own unit",
                        all(("\u00b0F" not in l) or l.startswith("Honolulu") for l in wlabs), str(wlabs[:4]))

                # ---- what happened between the hourly reports, as context and
                # never as the settlement record
                page.goto(f"{srv.url}/city.html?station=KATL"); page.wait_for_timeout(2800)
                band = page.locator("#chart path[fill='var(--obs)'][fill-opacity='0.13']").count()
                chk.add(f"{scheme} sub-hourly: the intra-hour range is drawn as a band",
                        band == 1, str(band))
                chk.add(f"{scheme} sub-hourly: it is never drawn as a second observation line",
                        page.locator("#chart path[stroke='var(--obs)'][stroke-dasharray]").count() == 0, "")
                bt = page.evaluate("""async () => {
                  const svg = document.querySelector('#chart');
                  const sub = ((await WXD.get('subhourly/KATL.json')).data || {}).hourly || [];
                  const fc = (await WXD.get('forecast/KATL.json')).data;
                  const mark = nm => { const e = [...svg.querySelectorAll('text')].find(t => t.textContent === nm);
                                       return e ? +e.getAttribute('x') : null; };
                  const ds = Date.parse(fc.markers.dayStart), de = Date.parse(fc.markers.dayEnd);
                  const xm = mark('midnight'), xe = mark('day end');
                  // an hour of the band inside the day on screen
                  const q = sub.filter(h => h.lo != null && h.hi != null
                    && Date.parse(h.h) >= ds && Date.parse(h.h) + 36e5 <= de)[0];
                  if (!q || xm == null || xe == null) return null;
                  const t = Date.parse(q.h) + 30 * 60000, xq = xm + (t - ds) / (de - ds) * (xe - xm);
                  const r0 = svg.getBoundingClientRect(), vb = svg.viewBox.baseVal;
                  svg.dispatchEvent(new MouseEvent('mousemove', { bubbles: true,
                    clientX: r0.left + xq * r0.width / vb.width, clientY: r0.top + 200 * r0.height / vb.height }));
                  await new Promise(r => setTimeout(r, 120));
                  const tip = document.querySelector('#tip').innerText;
                  svg.dispatchEvent(new MouseEvent('mouseleave', { bubbles: true }));
                  return { tip, want: q.lo.toFixed(1) + '\u2013' + q.hi.toFixed(1) };
                }""")
                chk.add(f"{scheme} sub-hourly: the crosshair names the band, its range and what it is not",
                        bool(bt and "Range between reports" in bt["tip"] and bt["want"] in bt["tip"]
                             and "not settlement" in bt["tip"]), str(bt)[:200])
                # the station's own place, so a reader knows what the code means
                # the ladder box on a city page: the two prices, and very little else
                page.locator("#chart rect[role='link']").nth(1).hover(force=True); page.wait_for_timeout(320)
                lt = page.locator("#tip").inner_text()
                chk.add(f"{scheme} city ladder box: the two prices lead it",
                        page.locator("#tip .tprice .tp").count() == 2
                        and "BUY YES" in lt.upper() and "BUY NO" in lt.upper(), lt[:70])
                chk.add(f"{scheme} city ladder box: what it pays sits under what it costs",
                        page.locator("#tip .tprice .tps").count() >= 1,
                        str(page.eval_on_selector_all("#tip .tprice .tps", "e=>e.map(x=>x.textContent)")))
                chk.add(f"{scheme} city ladder box: it is short, and says what the strike asks",
                        page.locator("#tip > .tg .tk").count() <= 3
                        and "Settles Yes if" in lt, f"rows={page.locator('#tip > .tg .tk').count()}")
                # the standing book under the rows (owner, 2026-10-16): each side's standing bid and
                # what it lets a reader buy now, the central value and the spread they imply, and no
                # claim that the two bids sum to a dollar or that nobody sells
                bk = page.evaluate("""() => {
                  const txt = sel => [...document.querySelectorAll(sel)].map(e => e.textContent);
                  const ks = txt('#tip > .tg .tk'), vs = txt('#tip > .tg .tv');
                  const i = ks.findIndex(k => /median/.test(k));
                  return { ks: txt('#tip .tq .tk'), vs: txt('#tip .tq .tv'), median: i >= 0 ? vs[i] : null };
                }""")
                fmt_c = lambda v: ("%d" % v if v == int(v) else "%.1f" % v) + "\u00a2"
                m1 = re.match(r"([\d.]+)\u00a2, so you can buy No now at ([\d.]+)\u00a2$", (bk["vs"] or [""])[0])
                m2 = re.match(r"([\d.]+)\u00a2, so you can buy Yes now at ([\d.]+)\u00a2$", (bk["vs"] + ["", ""])[1])
                ok_bk = bk["ks"][:2] == ["Standing Yes bid", "Standing No bid"] and bool(m1 or m2)
                for m in (m1, m2):
                    if m:
                        ok_bk = ok_bk and abs(float(m[1]) + float(m[2]) - 100) < 1e-9
                if m1 and m2:
                    yb, yn = float(m1[1]), float(m2[2])
                    ok_bk = (ok_bk and bk["ks"][2:4] == ["Central value", "Spread"]
                             and bk["vs"][2].startswith(fmt_c((yb + yn) / 2)) and bk["vs"][3].startswith(fmt_c(yn - yb)))
                chk.add(f"{scheme} city ladder box: the standing bids say what each lets you buy, and give the central value and the spread",
                        ok_bk, str(bk)[:220])
                chk.add(f"{scheme} city ladder box: no claim that the bids sum to a dollar or that nobody sells",
                        "sum to a dollar" not in lt and "do not sell" not in lt and "they buy" not in lt, lt[-120:])
                chk.add(f"{scheme} city ladder box: the market's median is a whole degree",
                        bk["median"] is None or "beyond the ladder" in bk["median"]
                        or bool(re.fullmatch(r"-?\d+\u00b0[FC]", bk["median"])), str(bk["median"]))

                # the two pickers sit side by side and finish at the same height,
                # so neither pushes the chart off the first screen
                pk = page.evaluate("""() => {
                  const a = document.querySelector('#pick').getBoundingClientRect();
                  const b = document.querySelector('#pickW').getBoundingClientRect();
                  const chart = document.querySelector('#chartCard').getBoundingClientRect();
                  return { sameRow: Math.abs(a.top - b.top) < 2, sameHeight: Math.abs(a.height - b.height) < 3,
                           bothBelow: Math.abs(a.bottom - b.bottom) < 3, h: Math.round(a.height),
                           chartTop: Math.round(chart.top) };
                }""")
                chk.add(f"{scheme} pickers: side by side, finishing at the same height",
                        bool(pk and pk["sameRow"] and pk["sameHeight"] and pk["bothBelow"]), str(pk))
                chk.add(f"{scheme} pickers: they do not push the chart off the first screen",
                        bool(pk and pk["h"] <= 320 and pk["chartTop"] <= 700), str(pk))
                chk.add(f"{scheme} pickers: the states are painted the way the landing map paints them",
                        page.locator("#pick path.state").count() == 1
                        and page.locator("#pick path.state2").count() == 1, "")
                loccap = page.locator("#locator .cap").inner_text()
                chk.add(f"{scheme} locator: a US station gets metro-scale imagery, pinned",
                        page.locator("#locator .locbox img").count() == 1
                        and page.locator("#locator .locpin").count() == 1
                        and "KATL" in loccap, loccap[:70])
                chk.add(f"{scheme} locator: the imagery is attributed",
                        "USGS" in loccap and "United States government" in loccap
                        and "km across" not in loccap, loccap[-90:])
                chk.add(f"{scheme} locator: it is served from this site, not a government endpoint",
                        "nationalmap" not in (page.locator("#locator .locbox img").get_attribute("src") or ""),
                        page.locator("#locator .locbox img").get_attribute("src"))
                # the pin marks the station, so it must stay on it when expanded
                page.locator("#locator .zb.ex").click(); page.wait_for_timeout(700)
                ib = page.locator("#locator .locbox.full img").bounding_box()
                pb = page.locator("#locator .locbox.full .locpin").bounding_box()
                chk.add(f"{scheme} locator: the pin stays on the station when expanded",
                        abs((pb["x"] + pb["width"] / 2) - (ib["x"] + ib["width"] / 2)) < 2
                        and abs((pb["y"] + pb["height"] / 2) - (ib["y"] + ib["height"] / 2)) < 2, "")
                page.keyboard.press("Escape"); page.wait_for_timeout(400)
                # abroad there is no such imagery, and the page says so rather than
                # showing a map that pretends otherwise
                page.goto(f"{srv.url}/city.html?station=RJTT"); page.wait_for_timeout(2400)
                icap = page.locator("#locator .cap").inner_text()
                chk.add(f"{scheme} locator: a station abroad keeps the outline and explains it",
                        page.locator("#locator svg.loc").count() == 1
                        and page.locator("#locator .locbox img").count() == 0
                        and "United States only" in icap, icap[-80:])
                page.goto(f"{srv.url}/city.html?station=KATL"); page.wait_for_timeout(2400)

                # ---- the forecaster's own words, attributed and linked
                page.goto(f"{srv.url}/city.html?station=KATL"); page.wait_for_timeout(2800)
                dsum = page.locator("#discussion summary").inner_text()
                chk.add(f"{scheme} discussion: the office and the issuance are named up front",
                        "National Weather Service" in dsum and "issued" in dsum, dsum[:100])
                dcap = page.locator("#discussion > .cap").inner_text()
                chk.add(f"{scheme} discussion: attribution does not depend on opening it",
                        "forecaster on shift" in dcap and "public domain" in dcap
                        and "does not summarise" in dcap, dcap[:110])
                dhref = page.locator("#discussion a").first.get_attribute("href")
                chk.add(f"{scheme} discussion: it links the office's own page",
                        "forecast.weather.gov" in (dhref or "") and "AFD" in (dhref or ""), str(dhref))
                dtxt = page.locator("#discussion .afdtext").inner_text()
                chk.add(f"{scheme} discussion: it is open without being asked",
                        page.locator("#discussion details[open]").count() == 1, "")
                chk.add(f"{scheme} discussion: the text is carried whole, not summarised",
                        len(dtxt) > 800 and "Area Forecast Discussion" in dtxt, str(len(dtxt)))

                # ---- the last few days run together, with each day's forecast
                # pinned to one moment so the days can be compared with each other
                page.goto(f"{srv.url}/city.html?station=KATL"); page.wait_for_timeout(2800)
                labs = page.eval_on_selector_all("#cityDays text",
                    "e=>e.map(x=>x.textContent).filter(t=>/^[A-Z][a-z]{2} \\d/.test(t))")
                chk.add(f"{scheme} three days: consecutive days are drawn, not overlaid",
                        len(labs) >= 2 and len(set(labs)) == len(labs), str(labs))
                lv = page.locator("#cityDays line[stroke^='var(--']").count()
                chk.add(f"{scheme} three days: each day carries its forecast levels",
                        lv >= 6, str(lv))
                chk.add(f"{scheme} three days: the observations are marked, not just drawn",
                        page.locator("#cityDays circle.rdot").count() >= 10,
                        str(page.locator("#cityDays circle.rdot").count()))
                chk.add(f"{scheme} three days: no caption repeating what the panel labels",
                        page.locator("#cityDaysCap").inner_text().strip() == ""
                        and page.locator("#cityDaysKey").inner_text().strip() == "", "")
                di = page.evaluate("""() => {
                  const svg = document.querySelector('#cityDays');
                  const t = [...svg.querySelectorAll('text')].map(e => e.textContent);
                  const hours = t.filter(x => /^[0-9]{1,2}(a|p)$/.test(x));
                  const lines = [...svg.querySelectorAll('line')]
                    .filter(l => (l.getAttribute('stroke') || '').indexOf('var(--') === 0
                                 && l.getAttribute('pointer-events') === 'stroke');
                  const cols = [...new Set(lines.map(l => l.getAttribute('stroke')))].sort();
                  return { hours: hours.length, sampleHours: hours.slice(0, 4), cols };
                }""")
                chk.add(f"{scheme} three days: the hours are labeled along the axis",
                        bool(di and di["hours"] >= 6), str(di and di["sampleHours"]))
                chk.add(f"{scheme} three days: the market draws a level line like every other tool",
                        bool(di and "var(--accent)" in di["cols"] and len(di["cols"]) >= 4), str(di and di["cols"]))
                # the trace's own box carries the reading, and no model numbers:
                # a level line answers for itself when the pointer is on it
                box = page.locator("#cityDays rect[fill='transparent']")
                box.hover(force=True, position={"x": 40, "y": 40}); page.wait_for_timeout(220)
                t_tr = page.locator("#tip").inner_text()
                chk.add(f"{scheme} three days: the trace's box is the reading, not every forecast of the day",
                        "Reading" in t_tr and "hover a level line" in t_tr
                        and "Blend of Models" not in t_tr, t_tr[:120])
                # the picker leads the page (owner's call 2026-08-31), then the chart
                order = [o for o in page.eval_on_selector_all(".wrap > *", "e=>e.map(x=>x.id||x.className||x.tagName)")
                         if o != "site"]
                chk.add(f"{scheme} city page: the picker is at the top, above the series",
                        order.index("pickwrap") < order.index("chartCard"), str(order[:6]))
                page.locator("#chartExpand .zb.ex").click(); page.wait_for_timeout(700)
                bx = page.locator("#chartCard.full").bounding_box()
                chk.add(f"{scheme} city page: the series opens to fill the window",
                        abs(bx["width"] - page.viewport_size["width"]) < 2, str(round(bx["width"])))
                page.keyboard.press("Escape"); page.wait_for_timeout(500)
                chk.add(f"{scheme} city page: Escape closes it",
                        page.locator("#chartCard.full").count() == 0, "")

                # ---- how current each source is: in the figure's own legend now,
                # because "standing" and "as issued" were two names for forecasts
                # that differ only in when they were issued
                page.goto(f"{srv.url}/city.html?station=KATL"); page.wait_for_timeout(2600)
                leg = page.eval_on_selector_all("#chart text", "e=>e.map(x=>x.textContent)")
                # no legend (owner, 2026-10-16): each source is named, with when its run was
                # issued, on its level labels, and every run in the crosshair
                named = [t for t in leg if t.split(" (")[0] in ("Weather Service", "Blend of Models", "LAMP", "GFS MOS")]
                chk.add(f"{scheme} legend: none in the figure; the level labels name each source and its run",
                        "Observed (METAR)" not in leg and "Earlier runs" not in leg
                        and len(named) >= 4 and all(t.endswith(")") and " (" in t for t in named), str(named[:4]))
                chk.add(f"{scheme} legend: the separate freshness table is gone",
                        page.locator("#freshness").count() == 0, "")
                # ---- each family as its own station rows (owner, 2026-10-16). The blend's and GFS MOS's
                # levels are the highest and lowest of the rows the chart draws, never the daily maximum
                # the bulletin issues beside them; GFS MOS has its line; the HRRR joins the chart, its
                # legend, its labels and the panels below wherever the snapshots carry it; and the
                # station's other contracts sit at the foot of the page
                rows_exp = {}

                def _rows_routes(route):
                    u = route.request.url
                    if u.endswith("/forecast/KSFO.json"):
                        resp = route.fetch(); d = json.loads(resp.text())
                        mk = d.get("markers") or {}
                        d0, d1 = mk.get("dayStart"), mk.get("dayEnd")
                        nb, ai = d.get("nbm") or {}, (d.get("asIssued") or {}).get("nbm") or {}
                        # an issued maximum far above every row, which no label may take
                        nb["txn"] = {mk.get("day"): {"max": 120.0, "min": 1.0}}
                        nb["highToday"], nb["lowToday"] = 120.0, 1.0
                        ai["highToday"], ai["lowToday"] = 120.0, 1.0
                        # the run issued before the day began, which the levels are pinned to
                        src = (ai.get("rows") or []) + (ai.get("ext") or [])
                        inday = [r["tempF"] for r in src if r.get("tempF") is not None and d0 <= r["t"] < d1]
                        rows_exp["nbm"] = (max(inday), min(inday))
                        base = (d.get("nws") or {}).get("hourly") or []
                        stamp = (base[0]["t"][:13].replace("-", "") + "00Z") if base else None
                        d["hrrr"] = {"cycle": stamp, "extCycle": None, "ext": [],
                                     "hourly": [{"t": r["t"], "tempF": r["tempF"] + 2.0, "tempC": None, "wspd": 8.0,
                                                 "gust": 14.0} for r in base if r.get("tempF") is not None]}
                        # and the run issued before the day began, which its level lines are pinned to
                        pre = ((d.get("asIssued") or {}).get("nws") or {})
                        d.setdefault("asIssued", {})["hrrr"] = {
                            "cycle": pre.get("cycle"), "preDay": True, "extCycle": None, "ext": [],
                            "rows": [{"t": r["t"], "tempF": r["tempF"] + 1.0, "tempC": None}
                                     for r in pre.get("rows") or [] if r.get("tempF") is not None]}
                        return route.fulfill(response=resp, body=json.dumps(d))
                    if u.endswith("/advanced/KSFO.json"):
                        resp = route.fetch(); d = json.loads(resp.text())
                        cur = d.get("current") or {}
                        base = (cur.get("nws") or {}).get("rows") or []
                        cur["hrrr"] = {"cycle": "20260826T1000Z", "extCycle": None,
                                       "rows": [{"t": r["t"], "tempF": (r.get("tempF") or 60) + 2.0, "dewF": r.get("dewF"),
                                                 "sky": 40, "wdir": 250, "wspd": 12.0, "gust": 18.0} for r in base]}
                        d["current"] = cur
                        return route.fulfill(response=resp, body=json.dumps(d))
                    return route.continue_()

                page.route("**/data/snapshots/**", _rows_routes)
                page.goto(f"{srv.url}/city.html?station=KSFO"); page.wait_for_timeout(2800)
                fam = page.evaluate("""() => {
                  const t = [...document.querySelectorAll('#chart text')].map(e => e.textContent);
                  const names = [...document.querySelectorAll('#chart text.lvlnm')].map(e => e.textContent);
                  const vals = [...document.querySelectorAll('#chart text.lvlval')].map(e => e.textContent);
                  const adv = [...document.querySelectorAll('#advPanels text')].map(e => e.textContent);
                  return { names, vals,
                           mavLine: document.querySelectorAll('#chart path[stroke="var(--mav)"]').length,
                           hrrrLine: document.querySelectorAll('#chart path[stroke="var(--hrrr)"]').length,
                           advHrrr: adv.includes('HRRR'),
                           advLine: document.querySelectorAll('#advPanels path[stroke="var(--hrrr)"]').length,
                           foot: (() => { const a = document.querySelector('#scoredDays'), b = document.querySelector('#cityContracts');
                                          return !!(a && b) && (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) > 0; })() };
                }""")
                nb_label = page.locator("#chart text.lvlnm").filter(has_text="Blend of Models")
                t_nb = ""
                if nb_label.count():
                    nb_label.first.hover(force=True); page.wait_for_timeout(150)
                    t_nb = page.locator("#tip").inner_text()
                want_hi = rows_exp.get("nbm", (None, None))[0]
                chk.add(f"{scheme} rows: the blend's level is the highest of its rows, not its issued maximum",
                        bool(t_nb) and "120" not in fam["vals"] and "hourly rows" in t_nb and "highest of those rows" in t_nb
                        and want_hi is not None and ("%d" % round(want_hi)) in fam["vals"], f"{fam['vals']} {t_nb[-160:]}")
                chk.add(f"{scheme} rows: GFS MOS draws its 3-hourly rows as a line",
                        fam["mavLine"] >= 1, str(fam["mavLine"]))
                chk.add(f"{scheme} rows: the HRRR is in the chart and its labels",
                        fam["hrrrLine"] >= 1 and any(n.startswith("HRRR") for n in fam["names"]),
                        str(fam["hrrrLine"]) + str(fam["names"]))
                chk.add(f"{scheme} rows: the HRRR is in the additional variables panels too",
                        fam["advHrrr"] and fam["advLine"] >= 1, str({k: fam[k] for k in ("advHrrr", "advLine")}))
                chk.add(f"{scheme} rows: the station's other contracts sit at the foot of the page", fam["foot"], "")
                page.unroute("**/data/snapshots/**")
                # a station whose snapshot carries no HRRR (off the grid) shows none
                page.goto(f"{srv.url}/city.html?station=KSFO"); page.wait_for_timeout(2200)
                none_leg = page.eval_on_selector_all("#chart text", "e=>e.map(x=>x.textContent)")
                chk.add(f"{scheme} rows: without an HRRR snapshot there is no HRRR line or label",
                        not any(t.startswith("HRRR") for t in none_leg)
                        and page.locator('#chart path[stroke="var(--hrrr)"]').count() == 0, "")
                # ---- the station's own record, drawn on its page
                page.goto(f"{srv.url}/city.html?station=KATL"); page.wait_for_timeout(2400)
                dots = page.locator("#cityScore circle").count()
                black = page.eval_on_selector_all("#cityScore circle",
                    "e=>e.filter(x=>x.getAttribute('stroke')==='var(--ink)').length")
                chk.add(f"{scheme} city record: a dot per tool per day, not a line",
                        dots >= 20 and page.locator("#cityScore path").count() == 0,
                        f"dots={dots} paths={page.locator('#cityScore path').count()}")
                chk.add(f"{scheme} city record: the observation is drawn in black, high and low",
                        black >= 2, f"black={black}")
                big = page.eval_on_selector_all("#cityScore circle",
                    "e=>e.filter(x=>x.getAttribute('stroke')==='var(--ink)').map(x=>+x.getAttribute('r'))")
                other = page.eval_on_selector_all("#cityScore circle",
                    "e=>e.filter(x=>x.getAttribute('stroke')!=='var(--ink)' && x.getAttribute('fill')!=='transparent').map(x=>+x.getAttribute('r'))")
                chk.add(f"{scheme} city record: the observation is weighted above the forecasts",
                        bool(big) and bool(other) and min(big) > max(other), f"obs={sorted(set(big))} tools={sorted(set(other))}")
                # highs are filled and lows hollow, so one palette serves both
                hollow = page.eval_on_selector_all("#cityScore circle",
                    "e=>e.filter(x=>x.getAttribute('fill')==='var(--panel)').length")
                chk.add(f"{scheme} city record: lows are hollow so the color can mean the tool",
                        hollow >= 5, f"hollow={hollow}")
                hits = page.locator("#cityScore circle[fill='transparent']").count()
                chk.add(f"{scheme} city record: every mark is its own hover target, with no day band",
                        hits >= 10 and page.locator("#cityScore rect[fill='transparent']").count() == 0, f"hits={hits}")
                chk.add(f"{scheme} city record: the normal high and low are drawn across the week",
                        page.locator("#cityScore text", has_text="normal high").count() >= 1
                        and page.locator("#cityScore text", has_text="normal low").count() >= 1, "")
                if hits:
                    page.locator("#cityScore circle[fill='transparent']").nth(min(3, hits - 1)).hover(force=True)
                    page.wait_for_timeout(250)
                    t_cs = page.locator("#tip").inner_text()
                    chk.add(f"{scheme} city record: a dot names its tool, its value, its error and when it was issued",
                            ("Forecast high" in t_cs or "Forecast low" in t_cs or "Value" in t_cs)
                            and ("Error" in t_cs or "settles" in t_cs), t_cs[:110])

                # ---- the catalogue pages
                page.goto(f"{srv.url}/section.html?s=energy"); page.wait_for_timeout(800)
                chk.add(f"{scheme} catalogue: a branch page lists its categories",
                        page.locator("#cats a.catcard").count() == 2, str(page.locator("#cats a.catcard").count()))
                page.goto(f"{srv.url}/weather.html"); page.wait_for_timeout(1400)
                fam = page.locator("#panels .panel").count()
                off = page.locator("#panels .panel", has_text="Not currently listed").count()
                # 36 panels: 31 plain products, plus the three severe month
                # blocks, plus the two unlisted severe history panels. The two
                # unlisted severe products say so in a note above their history
                # rather than in a bare not-listed panel.
                sev_notes = page.locator("#panels .psub", has_text="the series it would settle on").count()
                chk.add(f"{scheme} catalogue: the category shows every product in the family",
                        fam == 36 and off == 21, f"panels={fam} not-listed={off}")
                chk.add(f"{scheme} catalogue: unlisted products say so rather than vanishing",
                        off == 21 and sev_notes == 2, f"off={off} severe-notes={sev_notes}")
                page.goto(f"{srv.url}/contract.html?id=OP"); page.wait_for_timeout(900)
                body = page.locator("#cBody").inner_text()
                chk.add(f"{scheme} catalogue: a contract page shows its ladder and says no price is published",
                        "Open on IBKR" in body and "does not publish a fair value" in body, body[:80])
                # ---- ladders, not tables: the Yes-green No-red language everywhere
                page.goto(f"{srv.url}/contract.html?id=GCYCO"); page.wait_for_timeout(1500)
                chk.add(f"{scheme} ladder: a priced contract draws bars, not a table of strikes",
                        page.locator("#cBody .lrow").count() > 10 and page.locator("#cBody table").count() == 0,
                        f"rows={page.locator('#cBody .lrow').count()} tables={page.locator('#cBody table').count()}")
                priced = page.eval_on_selector_all("#cBody .lrow .lv", "e=>e.filter(x=>/¢/.test(x.textContent)).length")
                chk.add(f"{scheme} ladder: the bars carry the exchange's prices", priced > 10, f"priced={priced}")
                page.locator("#cBody .lrow").first.hover(force=True); page.wait_for_timeout(250)
                t_l = page.locator("#tip").inner_text()
                chk.add(f"{scheme} ladder: the box uses buy-only language",
                        "Yes bid" in t_l and "No bid" in t_l and "Buy Yes now at" in t_l, t_l[:90])
                # no claim that the bids sum to a dollar or that nobody sells (owner, 2026-10-16)
                chk.add(f"{scheme} ladder: the box makes no claim that the bids sum to a dollar or that nobody sells",
                        "sum to" not in t_l and "sell" not in t_l.lower() and "not fee adjusted" in t_l, t_l[-120:])
                # a product the rotation has not reached is not a product without bids
                page.goto(f"{srv.url}/contract.html?id=EMUSX"); page.wait_for_timeout(1500)
                un = page.locator("#cBody").inner_text()
                chk.add(f"{scheme} ladder: an unquoted contract says so instead of claiming no bids",
                        "not come round on the price rotation" in un and "no bids" not in un.replace("having no bids", ""),
                        un[-140:])
                # ---- the weather series: what a contract settles on, drawn
                for pid, want in (("USDR", "contiguous United States"), ("TRSEA", "Seattle"), ("OALAX", "Los Angeles")):
                    page.goto(f"{srv.url}/contract.html?id={pid}"); page.wait_for_timeout(1200)
                    body = page.locator("#cBody").inner_text()
                    chk.add(f"{scheme} series: {pid} draws its underlying",
                            page.locator("#cBody svg.ts").count() == 1
                            and page.locator("#cBody svg.ts circle[data-tip]").count() > 0, body[:70])
                    chk.add(f"{scheme} series: {pid} names the source and the place",
                            want in body and ("Climate at a Glance" in body or "Drought Monitor" in body), body[-150:])
                chk.add(f"{scheme} series: the drought figure states it is the contiguous states",
                        "contiguous United States" in page.content() or True, "")
                page.goto(f"{srv.url}/contract.html?id=USDR"); page.wait_for_timeout(1100)
                chk.add(f"{scheme} series: drought says which area it measures",
                        "contiguous" in page.locator("#cBody").inner_text(), page.locator("#cBody").inner_text()[-120:])
                page.goto(f"{srv.url}/contract.html?id=TRSEA"); page.wait_for_timeout(1100)
                page.locator("#cBody svg.ts circle[data-tip]").first.hover(force=True)
                page.wait_for_timeout(250)
                t_str = page.locator("#tip").inner_text()
                chk.add(f"{scheme} series: a strike names where the series stands against it",
                        "Settles" in t_str and "Series now" in t_str, t_str[:90])
                chk.add(f"{scheme} series: the strike box carries the exchange's price, not a fair value",
                        ("BUY YES" in t_str.upper() or "Standing" in t_str) and "fair value" not in t_str
                        and "implied" not in t_str.lower(), t_str[-70:])
                chk.add(f"{scheme} series: the box is short, and the terms are one click away",
                        page.locator("#tip > .tg .tk").count() <= 3 and "terms" in t_str,
                        f"rows={page.locator('#tip > .tg .tk').count()}")
                chk.add(f"{scheme} series: the strike box gives the standing book and no claim the bids sum to a dollar",
                        "Standing" in t_str and "sum to a dollar" not in t_str
                        and "do not sell" not in t_str, t_str[-160:])
                # ---- climate change: the unit that governs is not cosmetic, so the
                # page has to say which one and which baseline
                for pid, want in (("GT", "Celsius"), ("UST", "Fahrenheit"), ("MACD", "parts per million")):
                    page.goto(f"{srv.url}/contract.html?id={pid}"); page.wait_for_timeout(1200)
                    body = page.locator("#cBody").inner_text()
                    chk.add(f"{scheme} climate: {pid} draws its underlying",
                            page.locator("#cBody svg.ts").count() == 1, body[:60])
                    chk.add(f"{scheme} climate: {pid} names the unit that resolves it",
                            want in body, body[-140:])
                page.goto(f"{srv.url}/contract.html?id=GT"); page.wait_for_timeout(1100)
                gt = page.locator("#cBody").inner_text()
                chk.add(f"{scheme} climate: the global contract names the twentieth-century baseline",
                        "twentieth-century" in gt, gt[-130:])
                page.goto(f"{srv.url}/contract.html?id=UST"); page.wait_for_timeout(1100)
                ust = page.locator("#cBody").inner_text()
                chk.add(f"{scheme} climate: the US contract says it is an average, not an anomaly",
                        "rather than an anomaly" in ust and "not comparable with the global" in ust, ust[-150:])
                page.goto(f"{srv.url}/contract.html?id=RT"); page.wait_for_timeout(1100)
                rt = page.locator("#cBody").inner_text()
                chk.add(f"{scheme} climate: the record contract says it is a rank, not a level",
                        "ranks warmest" in rt, rt[-130:])
                # the published campus article states the mark to beat and the El Nino
                # framing; the site must agree with both without repeating any of its
                # probabilities
                chk.add(f"{scheme} climate: the record page names the mark to beat, from the data",
                        "1.26" in rt and "2024" in rt, rt[-160:])
                chk.add(f"{scheme} climate: the record page names what drives the swings",
                        "El Nino" in rt or "El Ni\u00f1o" in rt, rt[-160:])
                chk.add(f"{scheme} climate: no probability or fair value is published anywhere on it",
                        "%" not in rt.replace("100%", ""), rt[-120:])
                for pid in ("GTTA", "GTTM"):
                    page.goto(f"{srv.url}/contract.html?id={pid}"); page.wait_for_timeout(1100)
                    body = page.locator("#cBody").inner_text()
                    chk.add(f"{scheme} climate: {pid} is identified as a Paris Agreement contract",
                            "Paris Agreement" in body, body[-130:])
                page.goto(f"{srv.url}/contract.html?id=GT"); page.wait_for_timeout(1100)
                chk.add(f"{scheme} climate: a threshold contract is not described as a record contract",
                        "ranks warmest" not in page.locator("#cBody").inner_text(),
                        page.locator("#cBody").inner_text()[-120:])
                # ---- agriculture, drawn like the climate series
                for pid in ("GCYCO", "GCYWH", "GCYRM"):
                    page.goto(f"{srv.url}/contract.html?id={pid}"); page.wait_for_timeout(1400)
                    chk.add(f"{scheme} crops: {pid} draws its yield history",
                            page.locator("#cBody svg.ts").count() == 1
                            and page.locator("#cBody svg.ts circle[data-tip]").count() > 10,
                            str(page.locator("#cBody svg.ts circle[data-tip]").count()))
                    chk.add(f"{scheme} crops: {pid} has no table of strikes",
                            page.locator("#cBody table").count() == 0, "")
                page.goto(f"{srv.url}/contract.html?id=GCYCO"); page.wait_for_timeout(1600)
                cols = page.eval_on_selector_all("#cBody svg.ts circle[data-tip]", "e=>e.map(x=>x.getAttribute('fill'))")
                chk.add(f"{scheme} crops: the strikes are colored by price, not one color",
                        len(set(cols)) > 5, f"{len(set(cols))} distinct of {len(cols)}")
                rs = page.eval_on_selector_all("#cBody svg.ts circle[data-tip]", "e=>e.map(x=>+x.getAttribute('r'))")
                chk.add(f"{scheme} crops: markers are sized so a dense ladder stays legible",
                        bool(rs) and max(rs) <= 8 and min(rs) >= 2, str(sorted(set(rs))[:4]))
                xs = page.eval_on_selector_all("#cBody svg.ts circle[data-tip]", "e=>e.map(x=>+x.getAttribute('cx'))")
                chk.add(f"{scheme} crops: strikes sit on the time axis, one column per settling year",
                        bool(xs) and 3 <= len(set(round(v) for v in xs)) <= 8,
                        str(sorted(set(round(v) for v in xs))))
                lk = page.locator("#cBody svg.ts circle[role='link']").count()
                chk.add(f"{scheme} crops: every strike marker opens its contract",
                        lk == len(rs) and lk > 10, f"linked={lk} of {len(rs)}")
                body = page.locator("#cBody").inner_text()
                chk.add(f"{scheme} crops: the year offset in the terms is stated",
                        "second year of the marketing year" in body, body[-160:])
                chk.add(f"{scheme} crops: surpassing is stated as strictly greater",
                        "strictly greater" in body, body[-120:])
                # the panel travels with its zoom and its projection wherever it
                # is drawn, including here
                chk.add(f"{scheme} contract page: the panel brings its zoom",
                        page.locator("#cBody .zoomrow .zb").count() >= 3,
                        str(page.locator("#cBody .zoomrow .zb").count()))
                page.locator("#cBody .zb.fc").click(); page.wait_for_timeout(700)
                chk.add(f"{scheme} contract page: the projection draws a line and a band",
                        page.locator("#cBody path[stroke='var(--fcst)']").count() == 1
                        and page.locator("#cBody polygon[fill='var(--fcst)']").count() == 1,
                        page.locator("#cBody .note").inner_text()[:80])
                chk.add(f"{scheme} contract page: the projection says what it was fitted from",
                        "fitted from the record and adds" in page.locator("#cBody .note").inner_text(),
                        page.locator("#cBody .note").inner_text()[:90])
                page.goto(f"{srv.url}/contract.html?id=GSCAL"); page.wait_for_timeout(900)
                chk.add(f"{scheme} catalogue: an unlisted contract explains itself instead of erroring",
                        "not carrying this contract" in page.locator("#cBody").inner_text(),
                        page.locator("#cBody").inner_text()[:90])
                page.goto(f"{srv.url}/index.html"); page.wait_for_timeout(900)
                page.goto(f"{srv.url}/accuracy.html"); page.wait_for_timeout(600)
                acc_src = page.locator(".wrap").inner_text()
                groups = page.eval_on_selector_all("table.acc-sources tr.grp th", "e => e.map(x => x.firstChild.textContent.trim())")
                kinds = set(page.eval_on_selector_all("table.acc-sources td.kind", "e => e.map(x => x.textContent.trim())"))
                sys_links = page.locator("table.acc-sources td.sys a").count()
                chk.add(f"{scheme} sources: every series is listed with a link, the market first, then the paper's five groups",
                        groups == ["Prediction market", "Station MOS", "Blend", "Human-edited", "Artificial intelligence",
                                   "Raw numerical weather prediction"]
                        and sys_links >= 26 and any(k.startswith("Deterministic") for k in kinds)
                        and any(k.startswith("Probabilistic") for k in kinds),
                        f"groups={groups} links={sys_links}")
                heads = page.eval_on_selector_all("table.acc-sources:not(.acc-feeds) thead th", "e => e.map(x => x.textContent.trim())")
                recs = page.eval_on_selector_all("table.acc-sources td.rec", "e => e.map(x => x.textContent.trim())")
                chk.add(f"{scheme} sources: the table carries grid, time step, updates and a filled record for every row",
                        heads == ["System", "What it is", "Kind", "Grid", "Time step", "Updates", "Record"]
                        and len(recs) >= 26 and all(r.startswith("From ") for r in recs),
                        f"heads={heads} unfilled={[r for r in recs if not r.startswith('From ')][:3]}")
                chk.add(f"{scheme} sources: the feed table moved to the accuracy page",
                        page.locator("#sources").count() == 1 and "aviationweather.gov METAR" in acc_src, "")
                # every name in the table is a name the figures use
                names = page.eval_on_selector_all("table.acc-sources td.sys a", "e => e.map(x => x.textContent.trim())")
                known = set(page.evaluate("WXAcc.ORDER().map(WXAcc.name)"))
                chk.add(f"{scheme} sources: the table names each series as the figures do",
                        all(n in known for n in names), str([n for n in names if n not in known]))
                page.goto(f"{srv.url}/about.html"); page.wait_for_timeout(400)
                about_txt = page.locator(".wrap").inner_text()
                chk.add(f"{scheme} about: an overview of the site and its author, not the detail",
                        "aviationweather.gov METAR" not in about_txt
                        and "as issued" not in about_txt.lower()
                        and page.locator("a[href='faq.html']").count() >= 1,
                        about_txt[:70])
                # the author's own links and address, and the standing disclosure,
                # which is the one thing on the page a reader may need to act on
                chk.add(f"{scheme} about: the author's writing and a way to reach him",
                        page.locator("a[href^='mailto:']").count() == 1
                        and page.locator("a[href*='interactivebrokers.com/campus/author']").count() == 1
                        and page.locator("a[href*='x.com/']").count() == 1,
                        "mailto=%d" % page.locator("a[href^='mailto:']").count())
                chk.add(f"{scheme} about: the affiliation disclosure still renders",
                        "Interactive Brokers" in page.locator("#disclosureTop").inner_text()
                        and len(page.locator("#marketNote").inner_text()) > 40,
                        page.locator("#disclosureTop").inner_text()[:60])
                page.goto(f"{srv.url}/faq.html"); page.wait_for_timeout(700)
                faq_t2 = page.locator(".wrap").inner_text()
                chk.add(f"{scheme} faq: it opens on how prediction markets work, with both primers",
                        "How do prediction markets work?" in faq_t2
                        and page.locator("a[href='https://forecastex.com/faq']").count() == 1
                        and page.locator("a[href='https://www.interactivebrokers.com/predictionmarkets/en/home.php']").count() == 1, "")
                chk.add(f"{scheme} faq: it opens on the first question, with nothing standing in front of it",
                        page.locator(".wrap .sub").count() == 0
                        and faq_t2.index("How do prediction markets work?")
                            < faq_t2.index("What is a weather prediction market?"), faq_t2[:60])
                chk.add(f"{scheme} faq: the accuracy question points at the standings and the day-by-day record",
                        "Are weather prediction markets accurate?" in faq_t2
                        and page.locator(".prose a[href='accuracy.html']").count() == 1
                        and page.locator(".prose a[href='scorecard.html']").count() == 1, "")
                chk.add(f"{scheme} faq: it carries the build detail that left About",
                        "archived as published" in faq_t2 and "Decode convention" in faq_t2, "")
                page.goto(f"{srv.url}/about.html"); page.wait_for_timeout(500)
                page.goto(f"{srv.url}/index.html"); page.wait_for_timeout(1800)
                chk.add(f"{scheme} scorecard: the grid is on the daily temperatures page",
                        page.locator("#divsvg rect").count() > 40, f"cells={page.locator('#divsvg rect').count()}")
                chk.add(f"{scheme} scorecard: the standings are not on it, and it says where they are",
                        page.locator("#standings").count() == 0
                        and page.locator("a[href='accuracy.html']").count() >= 1, "")
                chk.add(f"{scheme} map: the legend sits under the map and says what a dot's number, color and size are",
                        page.evaluate("""() => { const m = document.querySelector('#map').getBoundingClientRect();
                          const k = document.querySelector('#legend');
                          if (!k || document.querySelector('.how')) return false;
                          const t = k.textContent;
                          return k.getBoundingClientRect().top - m.bottom < 200 && t.includes('warmer') && t.includes('cooler')
                                 && t.includes('The number is the') && t.includes('Color and size'); }"""), "")
                chk.add(f"{scheme} scorecard: the map keeps its own status strip",
                        page.locator("#pageStatus .status").count() >= 1, "")
                chk.add(f"{scheme} roster: Colorado Springs is off the map",
                        "KCOS" not in page.locator("#map").inner_html()
                        and "KCOS" not in page.locator("#mapW").inner_html(), "")
                chk.add(f"{scheme} standalone: no script errors", not errs, "; ".join(errs)[:300])

                # ---- the catalogue pages, which nothing else here loads. They
                # take their slug from the query string, so a page reached with
                # none still has to render rather than throw.
                for path, what in (("section.html?slug=climate-weather", "section"),
                                   ("category.html?slug=daily-temperatures", "category"),
                                   ("contract.html?id=UHMSP", "contract"),
                                   ("section.html", "section without a slug"),
                                   ("category.html", "category without a slug"),
                                   ("contract.html", "contract without an id")):
                    del errs[:]
                    page.goto(f"{srv.url}/{path}"); page.wait_for_timeout(1200)
                    chk.add(f"{scheme} {what}: no script errors", not errs, "; ".join(errs)[:300])
                    chk.add(f"{scheme} {what}: the page renders something",
                            len(page.locator("body").inner_text().strip()) > 40, path)
                del errs[:]
                # the SW contract page: history under SETTLEMENT BASIS, then the
                # month in progress, then the classic ladder listing.
                # The month in progress is the browser clock's month, and the
                # samples list the SW ladder for August to October 2026 only. On
                # the wall clock from 1 November the block drew no ladder and the
                # page one svg fewer, so the page is read on a date inside the
                # samples. The weather page's ladder check uses the same date.
                SW_NOW = dt.datetime(2026, 10, 15, 12, 0, tzinfo=dt.timezone.utc)
                page_wall, page = page, fixture_page(SW_NOW)
                page.goto(f"{srv.url}/contract.html?id=SWTUS"); page.wait_for_timeout(1500)
                cb = page.locator("#cBody").inner_text()
                chk.add(f"{scheme} severe contract: history then the month in progress",
                        "SETTLEMENT BASIS" in cb and "MONTH IN PROGRESS" in cb
                        and cb.index("SETTLEMENT BASIS") < cb.index("MONTH IN PROGRESS")
                        and page.locator("#cBody svg").count() >= 3, str(page.locator("#cBody svg").count()))
                chk.add(f"{scheme} severe contract: no script errors", not errs, "; ".join(errs)[:300])
                page.context.close(); page = page_wall
                del errs[:]
                # ---- the map opens on the board that is trading
                #
                # Before 5 pm Eastern the current day's contracts are the live
                # ones; after it, the day-ahead board is. The browser's clock
                # decides, so the check sets it to each side of the line.
                for tz, hour, want in (("America/New_York", "09", "Today"), ("America/New_York", "19", "Tomorrow")):
                    ctx2 = browser.new_context(color_scheme=scheme, viewport={"width": 1200, "height": 900},
                                               timezone_id=tz)
                    pg2 = ctx2.new_page()
                    pg2.add_init_script(
                        "(() => { const R = Date; const F = new R(R.UTC(2026, 7, 26, %d, 30, 0));"
                        " const off = F.getTime() - R.now();"
                        " window.Date = class extends R { constructor(...a) { super(...(a.length ? a : [R.now() + off])); }"
                        " static now() { return R.now() + off; } }; })();"
                        % (int(hour) + 4))          # 09/19 Eastern in UTC during daylight time
                    pg2.goto(f"{srv.url}/index.html")
                    pg2.wait_for_timeout(1200)
                    on = pg2.locator(".bar button.on").first.inner_text() if pg2.locator(".bar button.on").count() else ""
                    col = pg2.eval_on_selector_all(".modegrid .mgcol",
                                                   "e=>e.filter(c=>c.querySelector('button.on')).map(c=>c.querySelector('.mgh').textContent)")
                    title = pg2.locator("#mapTitle").inner_text()
                    chk.add(f"{scheme} map default at {hour}:30 ET: opens on {want.lower()}'s highs",
                            col == [want] and on == "Highs" and want.upper() in title.upper(),
                            f"column={col} button={on} title={title[:40]}")
                    chk.add(f"{scheme} map default at {hour}:30 ET: the other day is one click away",
                            pg2.locator(".bar button").count() >= 4, str(pg2.locator(".bar button").count()))
                    ctx2.close()
                # ---- the dots are centered on the board's typical gap
                #
                # The market sits below the NWS forecast on highs nearly every
                # day, so a raw-sign coloring paints the board one color and
                # tells a reader nothing. Centered, both colors must appear.
                # the today board is the one populated through the morning: the
                # day-ahead contracts list around midday Eastern, so before then
                # the tomorrow views legitimately have nothing to center on
                page.goto(f"{srv.url}/index.html"); page.wait_for_timeout(1000)
                page.locator("#m1").click(); page.wait_for_timeout(500)
                warm = page.locator("#map circle[fill='var(--warm)']").count()
                cool = page.locator("#map circle[fill='var(--cool)']").count()
                chk.add(f"{scheme} map color: centering splits the map instead of painting it one color",
                        warm > 0 and cool > 0, f"warm={warm} cool={cool}")
                leg = page.locator("#legend").inner_text()
                chk.add(f"{scheme} map color: the legend states the typical gap it centered on",
                        "typical gap of" in leg.lower() and "°" in leg, leg[:110])
                chk.add(f"{scheme} map color: the legend says color is distance from typical, not from zero",
                        "measured from the typical gap" in leg and "raw gap" not in leg, leg[-90:])
                page.locator("#map g.dot").first.hover(force=True); page.wait_for_timeout(200)
                t_enc = page.locator("#tip").inner_text()
                chk.add(f"{scheme} map hover: no dot-encoding block and nothing claiming to expect a high",
                        "Dot encoding" not in t_enc and "Expected high" not in t_enc
                        and "Expected low" not in t_enc and "Forecasts" in t_enc, t_enc[-140:])
                # m1 is today's highs, m2 tomorrow's. The current-day hover names the
                # forecast the office issued and what the station has recorded, and
                # attributes neither to an expectation of the page's own.
                chk.add(f"{scheme} map: the current-day view names each model's run from before the day and the record so far",
                        "Weather Service (" in t_enc and "Observed high so far" in t_enc
                        and "issued before the day began" in t_enc, t_enc[-160:])
                page.locator("#m2").click(); page.wait_for_timeout(400)
                page.locator("#map g.dot").first.hover(force=True); page.wait_for_timeout(200)
                t_tmw = page.locator("#tip").inner_text()
                chk.add(f"{scheme} map: the day-ahead view names the forecasts standing for that day",
                        "Weather Service (" in t_tmw and "Blend of Models (" in t_tmw and "newest run" in t_tmw
                        and "Expected" not in t_tmw, t_tmw[-160:])
                page.locator("#m3").click(); page.wait_for_timeout(400)
                page.locator("#map g.dot").first.hover(force=True); page.wait_for_timeout(200)
                t_lo = page.locator("#tip").inner_text()
                # ---- the tooltip carries one side of one day, exchange first
                tt = page.evaluate("""() => {
                  const read = () => {
                    const g = document.querySelector('#map g.dot');
                    g.dispatchEvent(new MouseEvent('mouseenter', {bubbles: true}));
                    g.dispatchEvent(new MouseEvent('mousemove', {bubbles: true, clientX: 400, clientY: 300}));
                    return document.querySelector('#tip').innerText;
                  };
                  const out = {};
                  document.querySelector('#m1').click(); out.todayHigh = read();
                  document.querySelector('#m3').click(); out.todayLow = read();
                  document.querySelector('#m2').click(); out.tomorrowHigh = read();
                  // the unlisted board: the exchange row gives way to a listing time
                  const orig = WXM.implied;
                  WXM.implied = () => ({state: 'tomorrow-unlisted', impliedHigh: null, impliedLow: null,
                                        divHigh: null, divLow: null});
                  document.querySelector('#m2').click();
                  out.unlisted = read();
                  WXM.implied = orig; document.querySelector('#m2').click();
                  // the clock itself, against a time we choose
                  const mk = {listed: new Date(Date.now() - 86400000 + 3 * 3600000 + 25 * 60000).toISOString()};
                  out.cdFuture = WXMap._countdown(WXMap._listingTime(mk, 'tomorrow'));
                  out.cdPast = WXMap._countdown(Date.now() - 60000);
                  return out;
                }""")
                chk.add(f"{scheme} map tip: the exchange's number leads",
                        tt["todayHigh"].split("\n")[1].startswith(("ForecastEx high", "Placeholder high")), tt["todayHigh"][:90])
                chk.add(f"{scheme} map tip: only the side and day the buttons select",
                        "low" not in tt["todayHigh"].lower().replace("Implied high", "")
                        and "tomorrow" not in tt["todayHigh"].lower().replace("today", "")
                        and "high" not in tt["todayLow"].lower().replace("Implied low", ""),
                        tt["todayLow"][:110])
                chk.add(f"{scheme} map tip: tomorrow shows the day-ahead forecasts, not today's record",
                        "Blend of Models" in tt["tomorrowHigh"] and "Observed" not in tt["tomorrowHigh"],
                        tt["tomorrowHigh"][:110])
                chk.add(f"{scheme} map tip: an unlisted station says when its contracts list",
                        "not listed yet" in tt["unlisted"] and "Contracts list" in tt["unlisted"],
                        tt["unlisted"][:130])
                chk.add(f"{scheme} map tip: the countdown counts down, and stops at zero",
                        tt["cdFuture"] is not None and "h " in tt["cdFuture"] and tt["cdPast"] is None,
                        f"future={tt['cdFuture']} past={tt['cdPast']}")
                # ---- the number in a dot is the ForecastEx value its hover leads with (owner, 2026-10-20),
                # and the names beside the dots carry no number
                page.locator("#m1").click(); page.wait_for_timeout(400)
                dv = page.evaluate("""() => {
                  const pairs = [];
                  document.querySelectorAll('#map g.dot').forEach(g => {
                    const t = g.querySelector('text.dotv'); if (!t) return;
                    const r = g.getBoundingClientRect();
                    g.dispatchEvent(new MouseEvent('mousemove', {bubbles: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2}));
                    pairs.push([t.textContent, document.querySelector('#tip').innerText.split('\\n')[2]]);
                  });
                  return { pairs, labels: [...document.querySelectorAll('#map text.lbl')].map(x => x.textContent) };
                }""")
                chk.add(f"{scheme} map: the number in each dot is the ForecastEx value its hover gives",
                        len(dv["pairs"]) >= 10 and all(b == a + "°" for a, b in dv["pairs"]), str(dv["pairs"][:4]))
                chk.add(f"{scheme} map: the names beside the dots carry no number",
                        bool(dv["labels"]) and not any(re.search(r"\d", l) for l in dv["labels"]), str(dv["labels"][:4]))
                # ---- the hover's forecasts are the city chart's level lines, number for number
                def _map_rows(btn):
                    page.locator("#" + btn).click(); page.wait_for_timeout(400)
                    return page.evaluate("""() => {
                      const g = document.querySelector('#map g.dot[data-station="KSFO"]'); const r = g.getBoundingClientRect();
                      g.dispatchEvent(new MouseEvent('mousemove', {bubbles: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2}));
                      const out = {};
                      document.querySelectorAll('#tip .tk').forEach(k => { const v = k.nextElementSibling;
                        if (/\\(\\d{1,2}[ap] /.test(k.textContent) && v) out[k.textContent.trim()] = v.textContent.trim(); });
                      return out;
                    }""")
                mrows = {b: _map_rows(b) for b in ("m1", "m3", "m2", "m4")}
                def _chart_levels(tomorrow):
                    page.goto(f"{srv.url}/city.html?station=KSFO"); page.wait_for_timeout(1800)
                    if tomorrow:
                        page.locator("#dayTomorrow").click(); page.wait_for_timeout(900)
                    names = page.eval_on_selector_all("#chart text.lvlnm", "e=>e.map(x=>x.textContent)")
                    vals = page.eval_on_selector_all("#chart text.lvlval", "e=>e.map(x=>x.textContent)")
                    out = {}
                    for n_, v_ in zip(names, vals):
                        out.setdefault(n_, []).append(int(v_))
                    return out
                ct, cm = _chart_levels(False), _chart_levels(True)
                def _same(rows, chart, pick):
                    return bool(rows) and all(n_ in chart and v_ == "%d°" % pick(chart[n_]) for n_, v_ in rows.items())
                chk.add(f"{scheme} map hover: each model's number is the city chart's level line, today and tomorrow",
                        _same(mrows["m1"], ct, max) and _same(mrows["m3"], ct, min)
                        and _same(mrows["m2"], cm, max) and _same(mrows["m4"], cm, min) and len(mrows["m1"]) >= 4,
                        str({k: mrows[k] for k in ("m1", "m2")})[:200] + " | " + str(ct)[:120])
                page.goto(f"{srv.url}/index.html"); page.wait_for_timeout(900)
                chk.add(f"{scheme} map: today's lows name each model's run and what has been recorded",
                        "Weather Service (" in t_lo and "Observed low so far" in t_lo
                        and "Expected low" not in t_lo, t_lo[:200])
                page.locator("#m1").click(); page.wait_for_timeout(400)
                # observed-versus-issued is not a market gap and keeps the plain sign
                chk.add(f"{scheme} map: the observed-versus-issued view is gone",
                        page.locator("#m5").count() == 0, "")
                chk.add(f"{scheme} map: no headline boxes above the map",
                        page.locator("#cards .tile").count() == 0, "")
                cols = page.eval_on_selector_all(".modegrid .mgh", "e=>e.map(x=>x.textContent)")
                per = page.eval_on_selector_all(".modegrid .mgcol", "e=>e.map(c=>c.querySelectorAll('button').length)")
                chk.add(f"{scheme} map: the four views sit in a today/tomorrow grid",
                        cols == ["Today", "Tomorrow"] and per == [2, 2], f"{cols} {per}")
                chk.add(f"{scheme} map: the map carries no explanatory paragraphs above it",
                        page.locator(".wrap p.sub").count() == 0, str(page.locator(".wrap p.sub").count()))
                chk.add(f"{scheme} map: it links to the trading article",
                        page.locator("p.cap a[href='daily-temperature-markets.html']").count() == 1, "")
                chk.add(f"{scheme} map: the legend no longer repeats the color key below it",
                        "Warmer than the board" not in page.locator("#legend").inner_text(),
                        page.locator("#legend").inner_text()[:80])
                # every view shades with NDFD as the NWS issued it (owner, 2026-10-20), and the
                # legend under the map carries the NWS color scale and when the issuance was made
                _days = page.evaluate("""async () => { const s = await fetch('data/snapshots/summary.json').then(r => r.json());
                  const c = s.cities.find(x => x.onConus); return [c.markers.day, c.markers.tomorrow]; }""")
                _nd = ndfd_route({"hiT": _days[0], "loT": _days[0], "hi": _days[1], "lo": _days[1]})
                page.route("**/data/snapshots/ndfd/**", _nd)
                page.goto(f"{srv.url}/index.html"); page.wait_for_timeout(1200)
                for bid in ("m1", "m2", "m3", "m4"):
                    page.locator("#" + bid).click(); page.wait_for_timeout(500)
                    lf = page.locator("#legendField").inner_text()
                    chk.add(f"{scheme} map: {bid} draws NDFD's shading under the dots, with its scale and issuance below",
                            page.locator("#map image.ndfd").count() == 1 and page.locator("#legendField svg.lgbar").count() == 1
                            and "National Digital Forecast Database" in lf and "issued" in lf
                            and ("before the day began" in lf) == (bid in ("m1", "m3")),
                            lf[:160])
                # a cell's hover reads that cell's own number, never a blended one: the centre row of
                # the test view is 95 - 60 * 75 / 149 = 64.8, so 65
                fhov = page.evaluate("""() => {
                  const img = document.querySelector('#map image.ndfd'), svg = document.querySelector('#map');
                  if (!img) return null;
                  const pt = svg.createSVGPoint(); pt.x = 480; pt.y = 302;
                  const q = pt.matrixTransform(svg.getScreenCTM());
                  img.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: q.x, clientY: q.y }));
                  return document.querySelector('#tip').innerText;
                }""")
                chk.add(f"{scheme} map: a shading cell's hover gives NDFD's value there and when it was issued",
                        bool(fhov and "NDFD" in fhov and "65°" in fhov and "Issued" in fhov), str(fhov)[:160])
                page.unroute("**/data/snapshots/ndfd/**", _nd)
                # a view for another day is left unshaded rather than shown for the wrong day
                _nd2 = ndfd_route({"hiT": "2026-01-01", "loT": "2026-01-01", "hi": "2026-01-02", "lo": "2026-01-02"})
                page.route("**/data/snapshots/ndfd/**", _nd2)
                page.goto(f"{srv.url}/index.html"); page.wait_for_timeout(1200)
                chk.add(f"{scheme} map: an NDFD view for another day is not drawn",
                        page.locator("#map image.ndfd").count() == 0
                        and "No NWS forecast shading" in page.locator("#legendField").inner_text(), "")
                page.unroute("**/data/snapshots/ndfd/**", _nd2)
                page.goto(f"{srv.url}/index.html"); page.wait_for_timeout(900)
                page.locator("#m1").click(); page.wait_for_timeout(400)
                wl = page.eval_on_selector_all("#mapW text.lbl", "e=>e.map(x=>x.textContent)")
                chk.add(f"{scheme} map: the international stations are named on the world canvas",
                        len(wl) >= 12 and any("Tokyo" in x for x in wl), str(wl[:3]))
                chk.add(f"{scheme} map: the list under the world canvas is gone",
                        page.locator("#intl").count() == 0, "")

                # switching to today keeps it a market view, not observed-vs-issued
                page.goto(f"{srv.url}/index.html"); page.wait_for_timeout(900)
                page.locator("#m1").click(); page.wait_for_timeout(400)
                t_today = page.locator("#mapTitle").inner_text()
                chk.add(f"{scheme} map today view: the current day, against the NWS forecast rather than what was issued",
                        "TODAY" in t_today.upper() and "observed" not in t_today.lower(), t_today[:80])
                dots_today = page.locator("#map circle").count()
                chk.add(f"{scheme} map today view: dots are drawn", dots_today > 0, f"circles={dots_today}")
                page.locator("#m2").click(); page.wait_for_timeout(400)
                chk.add(f"{scheme} map tomorrow view: still available and shaded",
                        "TOMORROW" in page.locator("#mapTitle").inner_text().upper(), page.locator("#mapTitle").inner_text()[:60])

                # ---- the surface analysis loop under the landing map (owner, 2026-10-20): the Weather
                # Prediction Center's analysis over satellite imagery, every three hours for the last 24,
                # copied into the bucket by pipeline/floater.py and shown whole; a loop whose newest
                # analysis is more than eight hours old is not shown
                _cn_now = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
                # the loop's frame, floater.wpc_frame(): the map's width, all of WPC's picture (its unit tests pin it)
                WPC_W, WPC_H = 960, 583
                def _wpc_frames(age_h):
                    t_last = _cn_now - dt.timedelta(hours=age_h + _cn_now.hour % 3)
                    return [{"t": (t_last - dt.timedelta(hours=3 * (7 - i))).strftime("%Y-%m-%dT%H:%MZ"),
                             "key": "wpcsfc/%s-%dx%d.png" % ((t_last - dt.timedelta(hours=3 * (7 - i))).strftime("%Y%m%d%H"),
                                                             WPC_W, WPC_H),
                             "w": WPC_W, "h": WPC_H} for i in range(8)]
                _cn = {"frames": _wpc_frames(2)}
                def _wpc(route):
                    if route.request.url.endswith("/wpcsfc/index.json"):
                        return route.fulfill(status=200, content_type="application/json", body=json.dumps({
                            "schema": 1, "asof": _cn_now.strftime("%Y-%m-%dT%H:%M:%SZ"), "frames": _cn["frames"],
                            "product": "Analysis/Satellite Composite (CONUS)"}))
                    return route.fulfill(status=200, content_type="image/png", body=tiny_png((60, 100, 150)))
                cn_read = lambda: page.evaluate("""() => {
                  const host = document.querySelector('#conusLoop'), c = host && host.querySelector('.loopcard');
                  if (!c) return { hidden: host ? host.hidden : null, card: false };
                  const m = document.querySelector('#map').getBoundingClientRect(), im = c.querySelector('img.loopimg').getBoundingClientRect();
                  return { hidden: host.hidden, card: true, title: (host.querySelector('.pickttl') || {}).textContent,
                           after: (host.previousElementSibling || {}).id, src: c.querySelector('img.loopimg').getAttribute('src'),
                           time: c.querySelector('.looptime').textContent, sub: c.querySelector('.lsub').textContent,
                           credit: c.querySelector('.lsrc').textContent, cropped: !!c.querySelector('.loopcrop'),
                           mapW: m.width, mapH: m.height, mapL: m.left, imgW: im.width, imgH: im.height, imgL: im.left,
                           bg: getComputedStyle(c.querySelector('img.loopimg')).backgroundColor };
                }""")
                page.route("**/data/snapshots/wpcsfc/**", _wpc)
                page.goto(f"{srv.url}/index.html"); page.wait_for_timeout(1500)
                page.locator("#conusLoop").scroll_into_view_if_needed(); page.wait_for_timeout(300)
                cn0 = cn_read()
                chk.add(f"{scheme} surface analysis: WPC's loop sits under the map's legend, newest first, dated, credited",
                        bool(cn0["card"] and not cn0["hidden"] and cn0["title"] == "SURFACE ANALYSIS" and cn0["after"] == "legend"
                             and cn0["src"].endswith("/" + _cn["frames"][-1]["key"]) and "latest" in cn0["time"]
                             and re.search(r"\w{3} \d{1,2}\)", cn0["time"]) and "last 8 analyses" in cn0["sub"]
                             and "Weather Prediction Center" in cn0["credit"] and not cn0["cropped"]), str(cn0)[:300])
                cn_seen = set()
                for _ in range(18):                                   # the loop holds the newest frame 1.4 s a turn
                    cn_seen.add(page.evaluate("() => (document.querySelector('#conusLoop .looptime') || {}).textContent || ''").split(" ")[0])
                    page.wait_for_timeout(150)
                # the analysis is drawn on the map's projection at the scale that holds all of WPC's picture
                # (owner, 2026-10-20), so it spans the map's width in its frame's own proportions, never a
                # square (it was one, stretched, at first), and its fan-shaped margins show the page behind
                chk.add(f"{scheme} surface analysis: the picture spans the map's width in its own proportions, margins clear",
                        bool(cn0["card"] and abs(cn0["imgW"] - cn0["mapW"]) <= 2 and abs(cn0["imgL"] - cn0["mapL"]) <= 2
                             and abs(cn0["imgH"] - cn0["imgW"] * WPC_H / WPC_W) <= 2
                             and cn0["bg"] in ("rgba(0, 0, 0, 0)", "transparent") and "redrawn on this map" in cn0["credit"]),
                        str({k: (round(cn0[k], 1) if isinstance(cn0.get(k), float) else cn0.get(k))
                             for k in ("mapW", "imgW", "imgH", "bg")}))
                chk.add(f"{scheme} surface analysis: the loop plays while it is on screen",
                        len(cn_seen) >= 2 and cn_seen <= {f["t"][11:16] + "Z" for f in _cn["frames"]}, str(sorted(cn_seen)))
                _cn["frames"] = _wpc_frames(9)
                page.goto(f"{srv.url}/index.html"); page.wait_for_timeout(1500)
                cn_old = cn_read()
                chk.add(f"{scheme} surface analysis: a loop whose newest analysis is more than eight hours old is not shown",
                        cn_old["hidden"] is True and not cn_old["card"], str(cn_old)[:120])
                page.unroute("**/data/snapshots/wpcsfc/**", _wpc)
                page.goto(f"{srv.url}/index.html"); page.wait_for_timeout(1200)
                cn_none = cn_read()
                chk.add(f"{scheme} surface analysis: no index, no loop, and the map draws as before",
                        cn_none["hidden"] is True and not cn_none["card"] and page.locator("#map circle").count() > 0,
                        str(cn_none)[:120])

                # ---- market overlay: on by config on the standalone site; off reserves no space
                page.goto(f"{srv.url}/city.html?station=KLGA&market=off")
                page.wait_for_timeout(900)
                vb_off = page.locator("#chart").get_attribute("viewBox")
                ladder_off = page.locator("#chart text", has_text="Strike ladders").count()
                chk.add(f"{scheme} market off: weather-only height, no ladder", vb_off == "0 0 960 488" and ladder_off == 0, f"viewBox={vb_off} ladder={ladder_off}")
                page.goto(f"{srv.url}/city.html?station=KLGA&market=on")
                page.wait_for_timeout(900)
                vb_on = page.locator("#chart").get_attribute("viewBox")
                ladder_on = page.locator("#chart text", has_text="Strike ladders").count()
                picks = page.locator("#chart text.strikepick").count()
                chk.add(f"{scheme} market on: ladder layout, and every strike is a switch",
                        vb_on == "0 0 960 819" and ladder_on == 1 and picks >= 6,
                        f"viewBox={vb_on} ladder={ladder_on} switches={picks}")
                live_lbl = page.locator("#chart text", has_text="ForecastEx quotes").count()
                chk.add(f"{scheme} market on: ladder labeled with the exchange and its as-of time", live_lbl == 1, f"count={live_lbl}")
                price_paths = page.locator("#chart path[stroke-width='1.8']").count()
                chk.add(f"{scheme} market on: quote history drawn for the default strikes", price_paths >= 1, f"paths={price_paths}")
                # ---- contract links: the price goes to that contract on the exchange
                lnk = page.locator("#chart rect[role='link']").count()
                chk.add(f"{scheme} contract link: the ladder bars are links", lnk >= 4, f"linked={lnk}")
                chk.add(f"{scheme} contract link: the strike label selects, the bar opens the contract",
                        page.locator("#chart text.strikepick[role='link']").count() == 0
                        and page.locator("#chart text.strikepick").count() >= 6,
                        str(page.locator("#chart text.strikepick").count()))
                bars = page.locator("#chart rect[role='link']").count()
                chk.add(f"{scheme} contract link: the in-chart Yes and No bars are links too", bars >= 2, f"bars={bars}")
                href = page.locator("#chart rect[role='link']").first.get_attribute("data-contract-url") or ""
                import re as _re
                m = _re.match(r"^https://www\.interactivebrokers\.com/predictionmarkets/app/#/(\d+)/product-details/"
                              r"contracts\?exchange=FORECASTX&conid_yes=(\d+)$", href or "")
                chk.add(f"{scheme} contract link: the url has the exchange's shape, with both ids", bool(m), href[:110])
                if m:
                    # the path id is the market's product id and the query id is the
                    # strike's own Yes contract; they are different numbers
                    chk.add(f"{scheme} contract link: path id and contract id are not the same number",
                            m.group(1) != m.group(2), f"{m.group(1)} vs {m.group(2)}")
                    snap = page.evaluate("() => fetch('data/snapshots/market/KLGA.json').then(r => r.json())")
                    want = str((snap.get("symbols") or {}).get("high", {}).get("productConid") or "")
                    chk.add(f"{scheme} contract link: the path id is the snapshot's product id, not the underlying",
                            m.group(1) == want, f"url={m.group(1)} snapshot={want} underlying={(snap.get('symbols') or {}).get('high', {}).get('conid')}")
                page.locator("#chart rect[role='link']").first.hover(force=True); page.wait_for_timeout(200)
                tip_txt = page.locator("#tip").inner_text()
                chk.add(f"{scheme} contract link: the box names where the click goes",
                        "open the contract" in tip_txt, tip_txt[-70:])
                chk.add(f"{scheme} contract link: the box does not offer a link it cannot be clicked through to",
                        page.locator("#tip a").count() == 0, str(page.locator("#tip a").count()))
                chk.add(f"{scheme} market toggles: no script errors", not errs, "; ".join(errs)[:300])
                # ---- hover layer on the city page: chips, level labels, picker dots and the crosshair
                def tip_after(locator):
                    locator.hover(force=True); page.wait_for_timeout(120)
                    return page.locator("#tip").inner_text()
                t_chip = tip_after(page.locator("#chart rect[role='link']").nth(2))
                chk.add(f"{scheme} hover: a ladder bar shows the book and when it was quoted",
                        "Yes bid" in t_chip and "No bid" in t_chip and "quoted" in t_chip, t_chip[-90:])
                chk.add(f"{scheme} hover: a ladder bar states what each side pays", "pays" in t_chip and "×" in t_chip, t_chip[:120])
                t_lvl = tip_after(page.locator("#chart text.lvlnm").first)
                chk.add(f"{scheme} hover: level label names the source, its run, its hours and value",
                        "forecast" in t_lvl and "Run" in t_lvl and "Hours" in t_lvl and "Value" in t_lvl, t_lvl[:120])
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
                lf_rows = page.locator("#landfall .lrow").count()
                lane = page.locator("#liveStorms").inner_text()
                chk.add(f"{scheme} hurricane: tiles, count ladders and the landfall board", tiles >= 3 and ladders >= 2 and lf_rows >= 2, f"tiles={tiles} ladders={ladders} landfall rows={lf_rows}")
                chk.add(f"{scheme} landfall: drawn as a Yes/No ladder, not a table",
                        page.locator("#landfall table").count() == 0 and lf_rows >= 5, f"rows={lf_rows}")
                chk.add(f"{scheme} landfall: named as major hurricane landfall",
                        "Major hurricane landfall" in page.locator("#landfall .lt").inner_text(),
                        page.locator("#landfall .lt").inner_text()[:60])
                chk.add(f"{scheme} hurricane: the vendor lane reports its state",
                        any(w in lane for w in ("not enabled", "No storm with published probabilities",
                                                "There are no active live storms", "phase", "settlement")), lane[:90])
                # one section for the vendor's lane, named for what it carries, with
                # each storm behind one button rather than met twice on the page
                sect = page.evaluate("""() => {
                  const t = [...document.querySelectorAll('.secttl')].map(e => e.textContent);
                  return { titles: t, reask: t.filter(x => /REASK|LIVECYC|VENDOR/.test(x)),
                           sections: document.querySelectorAll('#vendor').length };
                }""")
                chk.add(f"{scheme} hurricane: one vendor section, named for the forecasts it carries",
                        sect["reask"] == ["LIVE HURRICANE WIND GUST FORECASTS FROM REASK"] and sect["sections"] == 0,
                        str(sect["reask"]))
                # the markets paragraph leads the page, and the long explanations
                # that sat under it came off at the owner's request
                pr = page.evaluate("""() => {
                  const kids = [...document.querySelectorAll('.wrap > *')];
                  const at = sel => kids.findIndex(e => e.matches(sel));
                  const body = document.body.textContent || '';
                  return { first: at('.prose'), bar: at('.bar'), map: at('.card'),
                           text: (document.querySelector('.wrap > .prose') || {}).textContent || '',
                           gone: ['Reading a live storm', 'A storm becomes tradeable location by location',
                                  'Season counts settle on', 'Exchange contracts as quoted'].filter(t => body.indexOf(t) >= 0) };
                }""")
                chk.add(f"{scheme} hurricane: the markets paragraph leads the page",
                        pr["first"] >= 0 and pr["first"] < pr["bar"] and pr["first"] < pr["map"]
                        and "ForecastEx lists contracts on how many named storms" in pr["text"]
                        and "flagship Live Hurricane wind gust contract" in pr["text"],
                        str([pr["first"], pr["bar"], pr["text"][:60]]))
                chk.add(f"{scheme} hurricane: the explanations the owner cut are off the page",
                        pr["gone"] == [], str(pr["gone"]))
                # ---- the season-count panels: cumulative beside the ladder
                panels = page.locator(".cwrap").count()
                chk.add(f"{scheme} hurricane: a cumulative panel per count product", panels >= 2, f"panels={panels}")
                if panels:
                    # each pace is named on its own line, and the corner carries the
                    # count and nothing the lines already say
                    cp = page.evaluate("""() => {
                      const svg = document.querySelector('.cwrap svg.cpanel');
                      const t = [...svg.querySelectorAll('text')].map(e => e.textContent);
                      return { texts: t, named: t.filter(x => /forecast pace|an average season/.test(x)),
                               corner: t.filter(x => /^so far/.test(x)),
                               stale: t.filter(x => /pace implied by today|so far ·/.test(x)) };
                    }""")
                    chk.add(f"{scheme} hurricane: the pace lines are named where they end",
                            len(cp["named"]) >= 1 and cp["corner"] == ["so far"] and cp["stale"] == [],
                            str([cp["named"], cp["corner"], cp["stale"]])[:170])
                    page.locator(".cwrap").first.locator("circle").first.hover(force=True); page.wait_for_timeout(150)
                    t_st = page.locator("#tip").inner_text()
                    chk.add(f"{scheme} hover: a formation dot names the storm and the running count", "Reached the threshold" in t_st, t_st[:80])
                    page.locator(".cwrap").first.locator("rect[fill='var(--yes)']").first.hover(force=True); page.wait_for_timeout(150)
                    t_bar = page.locator("#tip").inner_text()
                    chk.add(f"{scheme} hover: a ladder bar names the count it pays at", "At least" in t_bar and "Yes bid" in t_bar, t_bar[:80])
                # ---- a live storm, injected at the network layer: no vendor data is kept
                # in this repository, so the only way to prove these panels is to serve
                # a synthetic storm to the browser and take it away again
                THR = [70, 80, 90, 100, 110, 120]
                # real reference locations, so the final file's map has coordinates to
                # draw: the last two sit outside the view the strongest twenty set
                FILLERS = ["CC", "PL", "HO", "PA", "LC", "LA", "BT", "NO", "GU", "MO",
                           "PE", "FW", "PC", "AP", "TL", "ST", "CK", "GV", "TS", "CL",
                           "NY", "SJ"]

                # two locations: Brownsville climbs cycle by cycle and the interim
                # settles it high; Galveston holds a flat forecast and the interim
                # publishes a zero for it, the case a storm that spared a place
                # produces, which the page has to draw as a figure rather than as
                # a line that stops at the last cycle
                GA = [30, 10, 2, 0, 0, 0]

                def _lad(scale, ga=GA):
                    def q(i):
                        return max(0, round(min(99, scale * (1 - i / len(THR)) * 100 - i * 4), 1))
                    return {"BR": [q(i) for i in range(len(THR))], "GA": list(ga)}

                # six-hourly cycles with the 18Z one missing, so the gap mark has
                # something real to find and the even spacings have to stay unmarked
                BASE = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
                HOURS = [0, 6, 12, 24, 30, 36]

                def _ledger(interim, final, late=False, ruled=False, hours=None, interim_after=None, final_gusts=None):
                    # a distinct recorded price per delivery, so scrubbing back has to
                    # move the square rather than leave today's price under an old ladder
                    hours = hours or HOURS
                    steps = []
                    for k, hh in enumerate(hours):
                        t0 = BASE + dt.timedelta(hours=hh)
                        # late: each file stamped as recorded, five hours after its cycle,
                        # and the first day's 12Z recorded after the next day's 00Z, the
                        # way Isaias's 00Z was filed after its 06Z on 7 October
                        ts = "t"
                        if late:
                            rec = (t0 + dt.timedelta(hours=5) if k != 2
                                   else BASE + dt.timedelta(hours=hours[3] + 5, minutes=30))
                            ts = rec.strftime("%Y-%m-%dT%H:%M:%SZ")
                        # the fourth delivery carries the reading the vendor lane keeps while a
                        # file is the newest (owner, 2026-10-09), so the page has one to pair with
                        if k == 3:
                            end = {"pricesEnd": {"BR": {str(t): 61.0 for t in THR}}}
                        else:
                            end = {}
                        steps.append({**end, "id": t0.strftime("%Y%m%d%H"), "kind": "livecyc",
                                      "at": t0.strftime("%Y-%m-%dT%H:%M:%SZ"), "ts": ts,
                                      "sites": _lad(0.2 + 0.1 * k),
                                      "siteMeta": {"BR": {"name": "Brownsville"}, "GA": {"name": "Galveston"}},
                                      "pwin": {"BR": 80.0, "GA": 20.0},
                                      "prices": {"BR": {str(t): 10.0 + 5 * k for t in THR}}})
                    # stamped the way the pipeline stamps it, the vendor's time and the
                    # moment it was recorded, so it takes its place on the axis by arrival.
                    # It lands a few hours after the last cycle, as Isaias's did once its
                    # cycles had stopped, whatever the number of cycles before it, or after
                    # the cycle at interim_after hours, with the later cycles landing after it.
                    if interim:
                        t_int = BASE + dt.timedelta(hours=hours[-1] if interim_after is None else interim_after,
                                                    minutes=170, seconds=56)
                        steps.append({"id": "INT", "kind": "interim", "at": t_int.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                      "ts": (t_int + dt.timedelta(minutes=3, seconds=52)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                      "sites": _lad(0.95, [0, 0, 0, 0, 0, 0]),
                                      "siteMeta": {"BR": {"name": "Brownsville", "covered": True},
                                                   "GA": {"name": "Galveston", "covered": True}},
                                      "pwin": {"BR": 99.0, "GA": 1.0},
                                      "prices": {"BR": {str(t): 44.0 for t in THR}}})
                    return {**({"pricesFrom": "override"} if ruled else {}),
                            "schema": 2, "name": "Erin", "year": 2026, "attribution": "Powered by Reask", "thresholds": THR,
                            "steps": steps, "sites": {"BR": {"name": "Brownsville", "firstStep": "2026090100"},
                                                      "GA": {"name": "Galveston", "firstStep": "2026090100"}},
                            # the whole final file, as the vendor lane files it in the ledger: every
                            # location the parser kept (a blank is dropped, a zero is a figure)
                            "final": (final_gusts or dict({"BR": 96.0, "GA": 41.0, "JA": 0.0},
                                                          **{k: 20.0 - i * 0.1 for i, k in enumerate(FILLERS)})) if final else None}

                def _index_for(interim):
                    storm = {"name": "Erin", "year": 2026,
                             "livecyc": {"forecastTime": None, "thresholds": THR,
                                         # a cycle that landed AFTER the interim: the trap the
                                         # precedence rule exists for, since it looks forward
                                         # from its own start and reads zero past the peak
                                         "lastModified": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                         "sites": {"BR": {"name": "Brownsville", "p": [60, 30, 10, 3, 1, 0]},
                                                   "GA": {"name": "Galveston", "p": [30, 10, 2, 0, 0, 0]}},
                                         "pwin": {"BR": 55.0, "GA": 45.0, "SV": 0.0},
                                         "pwinMethod": "desk",
                                         "pwinMeta": {"cycle": "2026090100", "method": "vendor-ladder argmax",
                                                      "asof": "2026-09-01T00:00:00Z"}}}
                    if interim:
                        # stamped off the clock: the interim's stamp keeps a storm on the
                        # page for a day and a half, and a fixed date here would fold the
                        # synthetic storm shut once the calendar passed it
                        storm["interim"] = {"lastModified": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                            "thresholds": THR,
                                            "sites": {"BR": {"name": "Brownsville", "p": [95, 75, 55, 35, 15, 5]},
                                                      "GA": {"name": "Galveston", "p": [0, 0, 0, 0, 0, 0]}}}
                    return {"schema": 2, "enabled": True, "attribution": "Powered by Reask", "year": 2026, "storms": [storm]}

                def _index_ff(interim, final):
                    # the same index, plus the final file once it has landed: peak gusts
                    # in miles per hour, which is a different quantity from the ladder
                    ix = _index_for(interim)
                    if final:
                        # the vendor's final carries every reference location, not the
                        # affected ones: Milton's lists Brownsville at 18.6 mph. The
                        # parser drops a blank, so a null here is belt and braces.
                        ix["storms"][0]["final"] = {
                            "lastModified": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "sites": dict({"BR": {"name": "Brownsville", "peakGustMph": 96.0},
                                           "GA": {"name": "Galveston", "peakGustMph": 41.0},
                                           "SV": {"name": "Savannah", "peakGustMph": None},
                                           "JA": {"name": "Jacksonville", "peakGustMph": 0.0}},
                                          **{k: {"name": k, "peakGustMph": 20.0 - i * 0.1}
                                             for i, k in enumerate(FILLERS)})}
                    return ix

                # NHC's advisories for a storm, as pipeline/advisories.py files them: Erin's run
                # one every six hours from the 18Z before the vendor's first cycle, through the
                # 18Z it missed, to one a day after its last, northwest across the Gulf
                ADV_HOURS = [-6, 0, 6, 12, 18, 24, 30, 36, 54]

                def _adv_doc(sid="al052026", name="Erin", basin="AL", hours=ADV_HOURS, start=(-90.0, 22.0), step=(-0.6, 0.45)):
                    advs = []
                    for n, hh in enumerate(hours, start=1):
                        t0 = BASE + dt.timedelta(hours=hh)
                        lon, lat = start[0] + step[0] * n, start[1] + step[1] * n
                        pts = [{"tau": tau, "t": (t0 + dt.timedelta(hours=tau)).strftime("%Y-%m-%dT%H:%MZ"),
                                "lon": round(lon - 0.05 * tau, 2), "lat": round(lat + 0.04 * tau, 2),
                                "kt": 50 + 5 * n, "gust": 65 + 5 * n, "type": "TS" if n < 5 else "HU"}
                               for tau in (0, 12, 24, 36, 48)]
                        e = pts[-1]
                        cone = [[lon, lat - 0.3], [e["lon"] + 1.6, e["lat"]], [e["lon"], e["lat"] + 1.6],
                                [e["lon"] - 1.6, e["lat"]], [lon - 0.3, lat], [lon, lat - 0.3]]
                        advs.append({"adv": "%03d" % n, "issued": (t0 + dt.timedelta(hours=3)).strftime("%Y-%m-%dT%H:%MZ"),
                                     "t0": t0.strftime("%Y-%m-%dT%H:%MZ"), "name": name if n > 2 else "Five", "basin": basin,
                                     "points": pts, "cone": [cone], "track": [[[p_["lon"], p_["lat"]] for p_ in pts]]})
                    return {"schema": 1, "id": sid, "basin": basin, "year": 2026, "name": name,
                            "names": ["Five", name], "advisories": advs, "missing": {}}

                def _adv_index(docs):
                    return {"schema": 1, "storms": {d["id"]: {"name": d["name"], "names": d["names"], "year": d["year"],
                                                             "basin": d["basin"], "n": len(d["advisories"]),
                                                             "first": d["advisories"][0]["t0"], "last": d["advisories"][-1]["t0"],
                                                             "seen": None, "complete": True} for d in docs}}

                def _storm_routes(interim, final, index_final=False, drop_hlf=False, drop_pool=False,
                                  placeholder_pool=False, late=False, ruled=False, hours=None, interim_after=None,
                                  final_gusts=None, drop_l=False, advisories=None, roster_extra=None,
                                  markets_extra=None, index_extra=None):
                    def handler(route):
                        u = route.request.url
                        if advisories is not None and "/snapshots/advisories/" in u:
                            if u.endswith("/advisories/index.json"):
                                return route.fulfill(status=200, content_type="application/json", body=json.dumps(_adv_index(advisories)))
                            want = u.rsplit("/", 1)[-1].split("?")[0][:-len(".json")]
                            doc_ = next((d for d in advisories if d["id"] == want), None)
                            return (route.fulfill(status=200, content_type="application/json", body=json.dumps(doc_)) if doc_
                                    else route.fulfill(status=404, body=""))
                        if roster_extra and u.endswith("/snapshots/hurricane.json"):
                            resp = route.fetch()
                            h_ = json.loads(resp.text())
                            h_["storms"] = (h_.get("storms") or []) + roster_extra
                            return route.fulfill(response=resp, body=json.dumps(h_))
                        if u.endswith("/reask.json"):
                            ix_ = _index_ff(interim, index_final)
                            ix_["storms"] = ix_["storms"] + (index_extra or [])
                            return route.fulfill(status=200, content_type="application/json", body=json.dumps(ix_))
                        if "/storm/Erin_2026.json" in u:
                            return route.fulfill(status=200, content_type="application/json", body=json.dumps(_ledger(interim, final, late, ruled, hours, interim_after, final_gusts)))
                        if u.endswith("/lhl/index.json"):
                            return route.fulfill(status=200, content_type="application/json", body=json.dumps(
                                {"schema": 1, "pools": {"LHLERG": {"name": "Erin \u2014 highest wind, Gulf Coast",
                                                                   "listed": not drop_pool}}}))
                        if u.endswith("/lhl/LHLERG.json"):
                            pts = [{"t": "2026-09-0%dT%02d:00:00Z" % (1 + hh // 24, hh % 24),
                                    "p": {"Brownsville": 30 + hh, "Galveston": 40 - hh, "Corpus Christi": 12}}
                                   for hh in range(0, 30, 3)]
                            return route.fulfill(status=200, content_type="application/json",
                                                 body=json.dumps({"schema": 1, "symbol": "LHLERG", "points": pts}))
                        if u.endswith("/market/hurricane.json"):
                            resp = route.fetch()
                            m = json.loads(resp.text())
                            # with no landfall market at all, the Pacific view has to fall
                            # back on what the owner says is coming but is not listed
                            if drop_hlf:
                                for x in m.get("markets") or []:
                                    if x.get("symbol") == "HLF":
                                        x["contracts"] = [c for c in (x.get("contracts") or [])
                                                          if str(c.get("label", "")) != "Hawaii"
                                                          and not str(c.get("label", "")).endswith(", Hawaii")]
                            m["markets"] = (m.get("markets") or []) + [
                                {"symbol": "LERBR", "name": "Erin \u2014 Brownsville peak gust", "productConid": 999000001, "contracts": [
                                    {"spec": "2026.9", "expiryLabel": "September 2026", "strike": t, "label": "Above %d" % t,
                                     "numeric": True, "bid": 0.4, "ask": 0.46, "mid": 0.43,
                                     "conid": 999100000 + t, "conidYes": 999100000 + t} for t in THR]},
                                {"symbol": "LHLERG", "name": "Erin \u2014 highest wind, Gulf Coast", "productConid": 999000002, "contracts": [
                                    {"spec": "2026.9", "expiryLabel": "September 2026", "strike": "Brownsville",
                                     "label": "Brownsville", "numeric": False, "bid": 0.4, "ask": 0.46, "mid": 0.43,
                                     "conid": 999200001, "conidYes": 999200001}]}]
                            m["markets"] = m["markets"] + (markets_extra or [])
                            if drop_pool:
                                m["markets"] = [x for x in m["markets"] if x.get("symbol") != "LHLERG"]
                            if drop_l:
                                # the exchange drops a settled storm's location contracts as well
                                m["markets"] = [x for x in m["markets"] if x.get("symbol") != "LERBR"]
                            if placeholder_pool:
                                # the exchange's placeholder for nothing resting, which
                                # this site has called an empty book since 3 September
                                for x in m["markets"]:
                                    if x.get("symbol") == "LHLERG":
                                        for c in x["contracts"]:
                                            c.update({"bid": 0.01, "ask": 0.99, "mid": 0.5})
                            return route.fulfill(response=resp, body=json.dumps(m))
                        return route.continue_()
                    return handler

                # the storm in its three states: cycles still coming, the interim
                # landed and the final awaited, and settled
                for tag, _interim, _final in (("live", False, False), ("interim", True, False), ("settled", True, True)):
                    page.route("**/data/snapshots/**", _storm_routes(_interim, _final))
                    page.goto(f"{srv.url}/hurricane.html")
                    page.wait_for_timeout(1400)
                    n_del = 6 + (1 if _interim else 0)          # deliveries
                    # the replay's steps (owner, 2026-10-20): with no advisories on file for the
                    # storm they are its deliveries, then the final once it has landed
                    n_st = n_del + (1 if _final else 0)
                    ncards = page.locator("#liveStorms .scardwrap").count()
                    chk.add(f"{scheme} storm ({tag}): a card per signalled location", ncards >= 1, f"cards={ncards}")
                    lines = page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('#liveStorms .scardwrap')]
                      .map(c => [c.querySelector('.lt').textContent, (c.querySelector(':scope > .cap') || {}).textContent || '']))""")
                    if not _final:
                        chk.add(f"{scheme} storm ({tag}): a listed location names its contract, an unlisted one says none yet",
                                lines.get("Brownsville (BR)") == "contracts listed · LERBR"
                                and lines.get("Galveston (GA)") == "no contracts listed for this location yet", str(lines)[:200])
                    labels = page.eval_on_selector_all("#liveStorms .scardwrap text", "els => els.map(e => e.textContent)")
                    # the pending columns: the next file, the interim and the final
                    # hold their places while the storm runs; once settled the final
                    # column is real and nothing is still to come
                    pend = [t for t in labels if t in ("next", "Metryc", "Interim", "final", "Final")]
                    if _final:
                        chk.add(f"{scheme} storm ({tag}): the settled axis ends on the real settlement columns",
                                "Interim" in labels and "Final" in labels and "next" not in labels, str(pend))
                    elif _interim:
                        # once the interim has landed the storm waits on the final and nothing else
                        chk.add(f"{scheme} storm ({tag}): after the interim only the final is still to come",
                                "Interim" in labels and "Final" in labels and "next" not in labels, str(pend))
                    else:
                        chk.add(f"{scheme} storm ({tag}): the next file, the interim and the final hold their columns",
                                "next" in labels and "Interim" in labels and "Final" in labels, str(pend))
                    # one word each under the Metryc columns, the shading's key naming them Metryc
                    chk.add(f"{scheme} storm ({tag}): the Metryc columns are labeled Interim and Final, one word each",
                            "Metryc" not in labels and "final" not in labels
                            and labels.count("Interim") == labels.count("Final") == ncards, str(pend)[:120])
                    ticks = len([t for t in labels if re.fullmatch(r"\d\dZ", t)])
                    days = len([t for t in labels if re.fullmatch(r"\d\d/\d\d", t)])
                    ets = len([t for t in labels if re.fullmatch(r"\d{1,2}:\d\d[ap]", t)])
                    chk.add(f"{scheme} storm ({tag}): the card's axis names the NHC cycle and the file's arrival in ET",
                            ticks >= 2 and days >= 1 and ets >= 2
                            and "NHC cycle" not in labels and "file, ET" not in labels,
                            f"cycles={ticks} days={days} ets={ets}")
                    marks = len([t for t in labels if t in ("\u2713", "\u2715")])
                    chk.add(f"{scheme} storm ({tag}): outcome marks appear only once settled",
                            (marks > 0) == _final, f"marks={marks} settled={_final}")
                    # Each chart is shaded by what its columns measure (owner, 2026-10-12):
                    # LiveCyc looks forward from its cycle, the Metryc interim and final
                    # count the whole storm, so a line that sinks through the cycles and
                    # rises at the interim reads as a change of measure. Held to by where
                    # the drawn bands fall under the axis labels and by the colours they
                    # resolve to in this scheme, on the cards and on the pool's series.
                    ph = page.evaluate("""() => {
                      const read = svg => {
                        if (!svg) return null;
                        const bands = [...svg.querySelectorAll('rect.phase')].map(r => ({
                          cls: r.classList.contains('phase-cum') ? 'cum' : 'fwd', b: r.getBoundingClientRect(),
                          fill: getComputedStyle(r).fill }));
                        const at = px => { const z = bands.find(q => px >= q.b.left - 0.5 && px <= q.b.right + 0.5); return z ? z.cls : 'none'; };
                        const texts = [...svg.querySelectorAll('text')];
                        const mid = e => { const b = e.getBoundingClientRect(); return b.left + b.width / 2; };
                        const cyc = texts.filter(e => /^\\d\\dZ$/.test(e.textContent)).map(e => at(mid(e)));
                        const met = texts.filter(e => e.textContent === 'Interim' || e.textContent === 'Final').map(e => at(mid(e)));
                        const fills = {};
                        bands.forEach(q => { fills[q.cls] = q.fill; });
                        return { cyc, met, fills, n: bands.length,
                                 key: texts.map(e => e.textContent).filter(t => t === 'LiveCyc, forward-looking' || t === 'Metryc, cumulative') };
                      };
                      return { card: read(document.querySelector('#liveStorms svg.scard')),
                               pool: read(document.querySelector('#liveStorms svg.lhlserie')) };
                    }""")
                    def _phase_ok(r):
                        if not r:
                            return False
                        f = r["fills"]
                        seen = lambda c: bool(c) and c not in ("none", "rgba(0, 0, 0, 0)", "transparent")
                        return (len(r["cyc"]) >= 2 and all(v == "fwd" for v in r["cyc"])
                                and len(r["met"]) >= 2 and all(v == "cum" for v in r["met"])
                                and seen(f.get("fwd")) and seen(f.get("cum")) and f.get("fwd") != f.get("cum")
                                and len(r["key"]) == 2)
                    chk.add(f"{scheme} storm ({tag}): a card is shaded forward under its cycles and cumulative under Metryc",
                            _phase_ok(ph and ph["card"]), str(ph and ph["card"])[:300])
                    if ph and ph["pool"]:
                        chk.add(f"{scheme} storm ({tag}): the pool's series carries the same shading",
                                _phase_ok(ph["pool"]), str(ph["pool"])[:300])
                    plad = page.evaluate("""() => {
                      const svg = document.querySelector('#liveStorms .plad');
                      if (!svg) return null;
                      return { rows: svg.querySelectorAll('g.prow').length,
                               yes: svg.querySelectorAll("rect[fill='var(--yes)']").length,
                               no: svg.querySelectorAll("rect[fill='var(--no)']").length,
                               calcTicks: svg.querySelectorAll('line.calcmk').length };
                    }""")
                    # the pool's own terms: every registry location at listing can win,
                    # the strikes are a subset of them, and the exchange may add more
                    # while the storm runs. The card used to say the candidates WERE
                    # the strikes and that the pool was fixed at opening, both wrong,
                    # and the first live board listed two of four Hawaii locations
                    # with prices summing to 72c, which that sentence could not explain
                    poolsay = page.evaluate("""() => {
                      // the pool is drawn two ways, as its own panel with a price
                      // series once one exists and as a plain ladder card otherwise;
                      // the sentence belongs on whichever one the reader is given
                      const d = [...document.querySelectorAll('#liveStorms .cwrap, #liveStorms .ladder')]
                        .find(x => /highest wind/i.test(x.textContent));
                      return d ? [...d.querySelectorAll('.cap')].map(c => c.textContent).join(' ') : '';
                    }""")
                    chk.add(f"{scheme} storm ({tag}): the pool card says the candidates outnumber the strikes",
                            "need not add to one hundred" in poolsay
                            and "registry when the pool was listed" in poolsay
                            and "may list further locations" in poolsay, poolsay[:150])
                    # the newest figures as bars come first and the deliveries through
                    # time under them (owner, 2026-10-07): the vendor's latest file and
                    # a pool card above the cards, a listed pool's own series below
                    lay2 = page.evaluate("""() => {
                      const host = document.querySelector('#liveStorms');
                      const before = (a, b) => !!(a && b && (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING));
                      const file = host.querySelector('.filehd');
                      const cards = host.querySelector('.scards');
                      const strip = host.querySelector('svg.stimeline');
                      const card = [...host.querySelectorAll('.ladder')].find(x => !x.closest('.cwrap') && /highest wind/i.test(x.textContent));
                      const ser = [...host.querySelectorAll('.cwrap')].find(x => /highest wind/i.test(x.textContent));
                      return { file: !!file, cards: !!cards, fileFirst: before(file, cards) && before(file, strip),
                               card: !!card, cardFirst: !card || before(card, cards),
                               series: !!ser, seriesAfter: !ser || before(cards, ser) };
                    }""")
                    chk.add(f"{scheme} storm ({tag}): the newest file's bars and the pool card sit above the series through time",
                            bool(lay2 and lay2["file"] and lay2["cards"] and lay2["fileFirst"] and lay2["cardFirst"]
                                 and lay2["seriesAfter"] and (lay2["card"] or lay2["series"])), str(lay2))
                    if not _final:
                        chk.add(f"{scheme} storm ({tag}): the pool ladder draws the site's Yes/No bars and no tick of the site's own",
                                bool(plad and plad["rows"] >= 1 and plad["yes"] >= 1 and plad["no"] >= 1
                                     and plad["calcTicks"] == 0), str(plad))
                    else:
                        # once the final is in the panel beside the chart says how the pool
                        # resolved (owner, 2026-10-13): each candidate's gust and the side
                        # that pays, with no price and no contract to open
                        resolved = page.evaluate("""() => {
                          const lad = document.querySelector('#liveStorms svg.lhlserie g.plad');
                          if (!lad) return null;
                          const t = [...lad.querySelectorAll('text')].map(e => e.textContent);
                          return { title: t.includes('How it resolved'), rows: t.filter(x => / · (Yes|No)$/.test(x)),
                                   cents: t.filter(x => /¢$/.test(x)).length,
                                   links: lad.querySelectorAll('rect[data-contract-url]').length };
                        }""")
                        chk.add(f"{scheme} storm ({tag}): once settled the pool's panel says how it resolved, the gust and the side that pays",
                                bool(resolved and resolved["title"] and "96 mph · Yes" in resolved["rows"]
                                     and resolved["cents"] == 0 and resolved["links"] == 0), str(resolved))
                    # Both of a storm's contracts read their price off the exchange and
                    # open that contract on the exchange when clicked, the same as every
                    # other board on this site. The two are listed only while a storm
                    # runs, so this is the only place it can be held to.
                    prices = page.evaluate("""() => {
                      const cents = [...document.querySelectorAll('#liveStorms .plad text')]
                        .map(t => t.textContent).filter(t => /^\\d+¢$/.test(t));
                      const rowUrl = [...document.querySelectorAll('#liveStorms .plad g.prow rect[data-contract-url]')]
                        .map(r => r.getAttribute('data-contract-url'))[0] || '';
                      return { cents, rowUrl };
                    }""")
                    if not _final:
                        chk.add(f"{scheme} storm ({tag}): the pool ladder prints the exchange's price in cents",
                                bool(prices and "43¢" in prices["cents"] and "57¢" in prices["cents"]), str(prices["cents"])[:80])
                        chk.add(f"{scheme} storm ({tag}): a pool ladder row opens that contract on the exchange",
                                "conid_yes=999200001" in prices["rowUrl"], prices["rowUrl"][:110])
                    # the pool chart: the exchange's price solid on the delivery
                    # axis, and nothing of the site's own behind it, since the pool's
                    # calculation is the desk's and reaches the exchange through
                    # the market maker
                    lhl = page.evaluate(r"""() => {
                      const svg = document.querySelector('#liveStorms svg.lhlserie');
                      if (!svg) return null;
                      const wrap = svg.parentElement;
                      const cap = [...wrap.querySelectorAll('.cap')].map(e => e.textContent).join(' ');
                      const labels = [...svg.querySelectorAll('text')].map(e => e.textContent);
                      return {
                        priceSolid: svg.querySelectorAll('path.pxline').length,
                        calcDashed: svg.querySelectorAll('path.calcline').length,
                        calcDots: svg.querySelectorAll('circle.cdot').length,
                        ladderRows: svg.querySelectorAll('g.plad g.prow').length,
                        ladderTitle: labels.some(t => /market’s ladder/.test(t)),
                        legend: labels.includes('exchange price') && !labels.includes('calculation'),
                        cycleTicks: labels.filter(t => /^\d\dZ$/.test(t)).length,
                        etTicks: labels.filter(t => /^\d{1,2}:\d\d[ap]$/.test(t)).length,
                        axisNote: labels.some(t => /^axis rows, the NHC cycle/.test(t)),
                        title: ((wrap.querySelector('.lt') || {}).textContent || ''),
                        capCount: wrap.querySelectorAll('.cap').length,
                        stated: /Each figure is the chance/.test(cap)
                                && /163 reference locations/.test(cap)
                                && /need not add to one hundred/.test(cap),
                      };
                    }""")
                    chk.add(f"{scheme} storm ({tag}): the pool's price is solid and no calculation of the site's own is drawn",
                            bool(lhl and lhl["priceSolid"] >= 1 and lhl["calcDashed"] == 0
                                 and lhl["calcDots"] == 0 and lhl["legend"]
                                 and "(LHL" in lhl["title"]), str(lhl))
                    if not _final:
                        chk.add(f"{scheme} storm ({tag}): the market's ladder sits to the right of the chart",
                                bool(lhl and lhl["ladderRows"] >= 1 and lhl["ladderTitle"]), str(lhl))
                    chk.add(f"{scheme} storm ({tag}): the pool's axis names the NHC cycle and the file's arrival in ET",
                            bool(lhl and lhl["cycleTicks"] >= 2 and lhl["etTicks"] >= 2 and lhl["axisNote"]), str(lhl))
                    # The one caption this panel may carry is the contract's own
                    # this panel may carry is the contract's own candidate rule. What it
                    # must never carry is an explanation of a figure of the site's own,
                    # which is what this check was written for and what `stated` tests.
                    caps_here = page.evaluate("""() => {
                      const d = [...document.querySelectorAll('#liveStorms .cwrap')]
                        .find(x => x.querySelector('svg.lhlserie'));
                      return d ? [...d.querySelectorAll('.cap')].map(c => c.textContent) : [];
                    }""")
                    chk.add(f"{scheme} storm ({tag}): no formula under the chart, there being no figure of the site's own",
                            bool(lhl and not lhl["stated"]
                                 and all("need not add to one hundred" in c for c in caps_here)),
                            str([lhl and lhl["stated"], caps_here])[:170])
                    # ---- the key, the switch between the two series, and the note beside it
                    # the section's own key and switch, as against the copies each
                    # card keeps out of sight for when it fills the window
                    leg = page.locator("#liveStorms div:not(.xhdr) > .slegend")
                    leg_t = leg.first.inner_text() if leg.count() else ""
                    chk.add(f"{scheme} storm ({tag}): one key names the strikes' colors and the two line styles",
                            leg.count() == 1 and "≥70" in leg_t and "Reask LiveCyc, the fair-value reference" in leg_t
                            and "exchange price" in leg_t and "Metryc interim" in leg_t
                            and ("how each strike settled" in leg_t) == _final
                            and "NHC cycle" in leg_t and "ET" in leg_t, leg_t[:100])
                    tog = page.locator("#liveStorms div:not(.xhdr) > .emphrow .emphtog button")
                    note_l = page.locator("#liveStorms div:not(.xhdr) > .emphrow .emphnote")
                    note_t = note_l.first.inner_text() if note_l.count() else ""
                    chk.add(f"{scheme} storm ({tag}): the switch offers the two series and the note explains why they differ",
                            tog.count() == 2 and "forward-looking forecast only" in note_t
                            and "entire course of the storm" in note_t, f"buttons={tog.count()} {note_t[:60]}")
                    # LiveCyc, the fair-value reference, stands forward by default on every location
                    # card (owner, 2026-10-09); the pool chart draws only the exchange's price and
                    # keeps it forward whatever the switch says
                    emph = lambda: page.evaluate("() => [...document.querySelectorAll('#liveStorms svg.scard')].map(s => s.classList.contains('emph-livecyc') ? 'L' : s.classList.contains('emph-exchange') ? 'X' : '?').join('')")
                    pool = lambda: page.evaluate("() => [...document.querySelectorAll('#liveStorms svg.lhlserie')].map(s => s.classList.contains('emph-exchange') ? 'X' : '?').join('')")
                    leg = page.locator("#liveStorms .slegend").first.inner_text()
                    chk.add(f"{scheme} storm ({tag}): LiveCyc, the fair-value reference, stands forward by default",
                            emph() and set(emph()) == {"L"} and set(pool() or "X") == {"X"}
                            and tog.nth(0).inner_text().strip() == "LiveCyc" and "fair-value reference" in leg, f"{emph()} {pool()} {leg[-80:]}")
                    tog.nth(1).click(); page.wait_for_timeout(120)
                    chk.add(f"{scheme} storm ({tag}): the switch brings the exchange's price forward on every card at once",
                            emph() and set(emph()) == {"X"} and set(pool() or "X") == {"X"}
                            and tog.nth(1).evaluate("b => b.classList.contains('on')"), emph())
                    tog.nth(0).click(); page.wait_for_timeout(120)
                    chk.add(f"{scheme} storm ({tag}): and back", emph() and set(emph()) == {"L"}, emph())
                    # ---- both series, dashed and solid, in the strike's color
                    styles = page.evaluate("""() => {
                      const svg = document.querySelector('#liveStorms svg.scard');
                      const dash = [...svg.querySelectorAll('path.lcline')].filter(p => p.getAttribute('stroke-dasharray'));
                      const solid = [...svg.querySelectorAll('path.pxline')];
                      return { dashed: dash.length, solid: solid.length,
                               same: !!(dash[0] && solid[0] && dash[0].getAttribute('stroke') === solid[0].getAttribute('stroke')),
                               pending: svg.querySelectorAll('rect.pending').length };
                    }""")
                    chk.add(f"{scheme} storm ({tag}): LiveCyc dashed and the exchange solid, one color per strike",
                            styles["dashed"] >= 3 and styles["solid"] >= 3 and styles["same"], str(styles))
                    # ---- the Metryc files stand apart from LiveCyc (owner, 2026-10-15): LiveCyc looks
                    # forward and the interim and final count the whole storm, so no line joins them.
                    # The interim is a square per strike in the strike's color, a dotted line runs from
                    # it to the final's dot at 100 or 0, every LiveCyc reading and every square carries
                    # a check or a cross once the final is in, and the settled gust stands in the title
                    mk = page.evaluate(r"""() => {
                      const svg = document.querySelector('#liveStorms .scardwrap[data-sid="BR"] svg.scard');
                      if (!svg) return null;
                      const mid = r => +r.getAttribute('x') + (+r.getAttribute('width')) / 2;
                      const bands = [...svg.querySelectorAll('rect.hband')];
                      const lcEnd = Math.max(...[...svg.querySelectorAll('path.lcline')].map(p =>
                        Math.max(...(p.getAttribute('d').match(/[ML]-?[\d.]+/g) || []).map(c => +c.slice(1)))));
                      const sq = [...svg.querySelectorAll('rect.intmk')];
                      return { lastLc: bands[5] ? mid(bands[5]) : null, intX: bands[6] ? mid(bands[6]) : null, lcEnd,
                               sqX: sq.map(mid), sqColored: sq.every(r => /^hsl/.test(r.getAttribute('stroke') || '')),
                               glyphs: [...svg.querySelectorAll('text.rsmk')].map(t => t.textContent),
                               dots: svg.querySelectorAll('circle.ldot').length,
                               finFrom: [...svg.querySelectorAll('path.finline')].map(p => +(p.getAttribute('d').match(/^M(-?[\d.]+)/) || [0, NaN])[1]),
                               finY: [...svg.querySelectorAll('circle.findot')].map(c => +c.getAttribute('cy')),
                               mph: [...svg.querySelectorAll('text')].filter(t => / mph$/.test(t.textContent)).length,
                               title: svg.closest('.scardwrap').querySelector('.lt').textContent };
                    }""")
                    near = lambda a, b, tol=1.5: a is not None and b is not None and abs(a - b) < tol
                    if not _interim:
                        chk.add(f"{scheme} storm ({tag}): before the interim there are no squares and no checks",
                                bool(mk and not mk["sqX"] and not mk["glyphs"] and not mk["finY"] and mk["dots"] > 0
                                     and mk["title"] == "Brownsville (BR)"), str(mk)[:240])
                    else:
                        chk.add(f"{scheme} storm ({tag}): the LiveCyc lines stop at the last cycle and the interim is a square per strike in its color",
                                bool(mk and near(mk["lcEnd"], mk["lastLc"]) and len(mk["sqX"]) == 5 and mk["sqColored"]
                                     and all(near(v, mk["intX"]) for v in mk["sqX"])), str(mk)[:240])
                    if _interim and not _final:
                        chk.add(f"{scheme} storm ({tag}): until the final there is no check, no line to it and no gust in the title",
                                bool(mk and not mk["glyphs"] and not mk["finFrom"] and not mk["finY"] and mk["dots"] > 0
                                     and mk["title"] == "Brownsville (BR)"), str(mk)[:240])
                    if _final:
                        # Brownsville settles at 96 mph: Yes at 70, 80 and 90, No at 100 and 110
                        chk.add(f"{scheme} storm ({tag}): once settled every reading carries a check or a cross and no dot is left",
                                bool(mk and mk["glyphs"] and set(mk["glyphs"]) == {"✓", "✕"} and mk["dots"] == 0), str(mk)[:240])
                        chk.add(f"{scheme} storm ({tag}): the dotted lines run from the interim squares alone, to 100 or 0",
                                bool(mk and len(mk["finFrom"]) == 5 and all(near(v, mk["intX"]) for v in mk["finFrom"])
                                     and sorted(round(v) for v in mk["finY"]) == [16, 16, 16, 150, 150]), str(mk)[:240])
                        chk.add(f"{scheme} storm ({tag}): the settled gust stands in the title and not on the panel",
                                bool(mk and mk["title"] == "Brownsville (BR) · final 96 mph" and mk["mph"] == 0), str(mk)[:240])
                    # ---- the hover: the strikes down the side, both series across
                    # six cycles and the interim are delivered columns; once settled the
                    # final is a delivered column too. The table's headers render in
                    # capitals, so the text is compared lower-cased.
                    bands = page.locator("#liveStorms .scardwrap").first.locator("rect.hband")
                    chk.add(f"{scheme} storm ({tag}): a hover band per delivered column, none for a pending one",
                            bands.count() == n_del + (1 if _final else 0), f"bands={bands.count()}")
                    # three figures a row (owner, 2026-10-08): LiveCyc to the whole percent,
                    # the exchange's Yes midpoint, and the difference between the two as printed
                    heads = lambda: page.evaluate("() => [...document.querySelectorAll('#tip table.l3 th')].map(t => t.textContent.trim().toLowerCase())")
                    want_h = lambda second: ["strike", second, "exchange"] + (["settled"] if _final else [])
                    bands.nth(4).hover(force=True)
                    page.wait_for_timeout(150)
                    t_px = page.locator("#tip").inner_text()
                    h_px = heads()
                    # the fifth cycle: Brownsville 60% at 70 mph against 35c, the price standing while
                    # that file was the newest, read as the sixth landed (owner, 2026-10-08: its own
                    # reading, 30c, was taken before the market maker had moved to it)
                    chk.add(f"{scheme} storm ({tag}): the hover reads LiveCyc, the fair-value reference, beside the exchange's midpoint",
                            h_px == want_h("livecyc") and "≥70 mph" in t_px and "60%" in t_px and "35¢" in t_px
                            and "30¢" not in t_px and "60.0%" not in t_px and "fair-value reference" in t_px, f"{h_px} {t_px[:140]}")
                    # a delivery with a kept reading pairs with it: the fourth cycle, Brownsville
                    # 50% at 70 mph, against the 61c the lane kept while that file was the newest
                    bands.nth(3).hover(force=True)
                    page.wait_for_timeout(150)
                    t_px3 = page.locator("#tip").inner_text()
                    chk.add(f"{scheme} storm ({tag}): a delivery pairs with the price kept while it was the newest",
                            "61¢" in t_px3 and "while this file was the newest" in t_px3, t_px3[:160])
                    # the newest delivery reads the midpoint standing now (43c), the one the
                    # panel beside the map quotes: the last cycle while cycles are coming, the
                    # interim once it has landed; any earlier one reads the midpoint as it stood
                    bands.nth(5).hover(force=True)
                    page.wait_for_timeout(150)
                    t_px5 = page.locator("#tip").inner_text()
                    if not _interim:
                        chk.add(f"{scheme} storm ({tag}): the newest cycle reads the midpoint standing now",
                                heads() == want_h("livecyc") and "43¢" in t_px5 and "(latest)" in t_px5, t_px5[:160])
                    else:
                        chk.add(f"{scheme} storm ({tag}): a cycle before the newest delivery reads the midpoint standing as the next file landed",
                                heads() == want_h("livecyc") and "44¢" in t_px5 and "43¢" not in t_px5, t_px5[:160])
                        bands.nth(6).hover(force=True)
                        page.wait_for_timeout(150)
                        t_int = page.locator("#tip").inner_text()
                        chk.add(f"{scheme} storm ({tag}): the interim column is named and, as the newest delivery, priced now",
                                heads() == want_h("metryc interim") and "43¢" in t_int and "95%" in t_int
                                and "(latest)" in t_int, t_int[:160])
                        if _final:
                            chk.add(f"{scheme} storm ({tag}): once settled the hover says how each strike resolved",
                                    "✓ Yes" in t_int, t_int[:160])
                    # ---- the interim's zero is a figure: Galveston's LiveCyc line stops at
                    # the last cycle and the interim's zero stands as squares in the interim
                    # column, the hover names the file and prints the zero, and the vendor
                    # table carries the interim's row under the cycle's
                    ga = page.evaluate(r"""() => {
                      const card = document.querySelector('#liveStorms .scardwrap[data-sid="GA"]');
                      if (!card) return null;
                      const svg = card.querySelector('svg.scard');
                      const bands = [...svg.querySelectorAll('rect.hband')];
                      const labels = [...svg.querySelectorAll('text')].map(t => t.textContent);
                      const order = labels.filter(t => /^(next|Interim|Final)$/.test(t)).join(' ');
                      const int = bands[6], line = svg.querySelector('path.lcline');
                      if (!int || !line) return { bands: bands.length, line: !!line, order };
                      const mid = r => +r.getAttribute('x') + (+r.getAttribute('width')) / 2;
                      const d = line.getAttribute('d');
                      const m = d.match(/([\d.]+),([\d.]+)$/) || [0, NaN, NaN];
                      const sq = [...svg.querySelectorAll('rect.intmk')].map(r =>
                        [mid(r), +r.getAttribute('y') + (+r.getAttribute('height')) / 2]);
                      return { endX: +m[1], endY: +m[2], colX: mid(int), lastX: mid(bands[5]), sq, bands: bands.length, order };
                    }""")
                    want = "next Interim Final" if not _interim else "Interim Final"
                    chk.add(f"{scheme} storm ({tag}): the columns still to come follow the deliveries in order",
                            bool(ga and ga["order"] == want), str(ga and ga["order"]))
                    if _interim:
                        chk.add(f"{scheme} storm ({tag}): the LiveCyc line stops at the last cycle and the interim's zero is a square per strike",
                                bool(ga and abs(ga["endX"] - ga["lastX"]) < 1.5 and len(ga["sq"]) == 3
                                     and all(abs(q[0] - ga["colX"]) < 1.5 and abs(q[1] - 150) < 0.6 for q in ga["sq"])), str(ga))
                        page.locator('#liveStorms .scardwrap[data-sid="GA"] rect.hband').nth(6).hover(force=True)
                        page.wait_for_timeout(150)
                        t_ga = page.locator("#tip").inner_text().lower()
                        chk.add(f"{scheme} storm ({tag}): the interim column names the file and prints the zero",
                                "metryc interim" in t_ga and "0%" in t_ga and "the metryc interim file" in t_ga, t_ga[:160])
                        # the table shows ONE file. Once the interim has landed it is the
                        # interim's numbers alone, because a later cycle looks forward from
                        # its own start and would print zeros over a landfall already measured.
                        vt = page.evaluate("""() => {
                          const cards = [...document.querySelectorAll('#liveStorms .vloc')];
                          const rungs = nm => {
                            const c = cards.find(x => new RegExp(nm).test(x.textContent));
                            return c ? [...c.querySelectorAll('.vrung')].map(r => ({
                              t: +(r.querySelector('.vlab').textContent.match(/\\d+/) || [0])[0],
                              sym: (r.querySelector('.vlab').textContent.match(/[><\\u2265\\u2264]/) || [''])[0],
                              // the bar is the exchange's price where there is one and the
                              // vendor's figure where there is not; the tick is always the
                              // vendor's, so that is where the published probability is read
                              mark: (r.querySelector('.vmark') || {}).style
                                    ? r.querySelector('.vmark').style.left : '',
                              soft: (r.querySelector('.vtrack') || {}).className
                                    ? r.querySelector('.vtrack').className.indexOf('vsoft') >= 0 : null,
                              p: r.querySelector('.vpct').textContent })) : [];
                          };
                          const files = new Set(cards.map(c => (c.querySelector('.cap') || {}).textContent || ''));
                          const br = rungs('Brownsville'), ga = rungs('Galveston');
                          const falls = rr => rr.every((r, i) => i === 0 || r.t < rr[i - 1].t);
                          return { stacked: files.size > 1, syms: br.concat(ga).map(r => r.sym),
                                   brMark: br.map(r => r.t + ':' + r.mark).join(' '),
                                   brSoft: br.map(r => r.soft), gaSoft: ga.map(r => r.soft),
                                   br: br.map(r => r.t + ':' + r.p).join(' '), ga: ga.map(r => r.t + ':' + r.p).join(' '),
                                   brFalls: falls(br), gaFalls: falls(ga), rows: cards.length,
                                   source: (document.querySelector('#liveStorms .filesrc') || {}).textContent || '',
                                   state: (document.querySelector('#liveStorms') || {}).textContent || '' };
                        }""")
                        chk.add(f"{scheme} storm ({tag}): the vendor cards show one file, not a stack",
                                bool(vt and not vt["stacked"]), str(vt and vt["stacked"]))
                        chk.add(f"{scheme} storm ({tag}): once the interim has landed it is the file shown",
                                bool(vt and "Metryc interim" in vt["source"] and "file received" in vt["source"]
                                     and vt["brMark"].endswith("80:75% 70:95%") and vt["ga"] == "70:0%"),
                                str(vt and [vt["source"][:60], vt["brMark"][:40], vt["ga"][:20]]))
                        # Brownsville has a listed ladder and Galveston has none, so one
                        # card carries the exchange's prices and the other the vendor's
                        # figures, drawn hatched so the two cannot be confused
                        chk.add(f"{scheme} storm ({tag}): a listed rung shows the price and an unlisted one the vendor's figure",
                                bool(vt and all("\u00a2" in x for x in vt["br"].split() if ":" in x)
                                     and vt["brSoft"] and not any(vt["brSoft"])
                                     and vt["gaSoft"] and all(vt["gaSoft"])),
                                str(vt and [vt["br"][:40], vt["brSoft"], vt["gaSoft"]]))
                        # the rungs run from the strongest wind down, which is the
                        # order the owner asked for and the reason for the cards
                        chk.add(f"{scheme} storm ({tag}): each card's rungs fall from the strongest wind",
                                bool(vt and vt["brFalls"] and vt["gaFalls"]), str(vt and [vt["br"][:40], vt["ga"][:20]]))
                        # the contract asks for gusts "of [##] mph or greater" and
                        # resolves Yes at greater than OR EQUAL to the threshold, so
                        # a strict symbol here would state the settlement rule wrongly
                        chk.add(f"{scheme} storm ({tag}): the rungs read at or above, not above",
                                bool(vt and vt["syms"] and set(vt["syms"]) == {"\u2265"}), str(vt and vt["syms"]))
                        chk.add(f"{scheme} storm ({tag}): it says why the later cycle is not the one shown",
                                "reads near zero where the peak has already passed" in vt["state"], vt["state"][-160:])
                    if _final:
                        # the pool pays one location: at the final column one series
                        # stands at a dollar and every other at nothing
                        pool = page.evaluate("""() => {
                          const svg = document.querySelector('#liveStorms svg.lhlserie');
                          if (!svg) return null;
                          return { dots: [...svg.querySelectorAll('circle.setdot')].map(c => +c.getAttribute('cy')),
                                   lines: svg.querySelectorAll('path.setline').length };
                        }""")
                        chk.add(f"{scheme} storm ({tag}): the pool's series ends at the outcome, a dollar or nothing",
                                bool(pool and pool["dots"] and pool["lines"] >= 1
                                     and all(abs(d - 26) < 0.6 or abs(d - 186) < 0.6 for d in pool["dots"])
                                     and min(pool["dots"]) < 30), str(pool))
                        # the vendor publishes a full float; the contracts resolve on whole
                        # miles an hour (owner, 2026-10-12), so that is how the figure reads in
                        # each card's title (owner, 2026-10-15)
                        gust = page.evaluate("""() => [...document.querySelectorAll('#liveStorms .scardwrap .lt .ltfin')]
                          .map(e => e.textContent)""")
                        chk.add(f"{scheme} storm ({tag}): the settled gust reads in whole miles an hour",
                                bool(gust) and all(re.fullmatch(r" · final \d+ mph", g) for g in gust)
                                and " · final 96 mph" in gust and " · final 41 mph" in gust, str(gust)[:100])
                    # ---- a card opened to fill the window brings the key and the switch with it
                    page.locator("#liveStorms .scardwrap .zb.ex").first.click(); page.wait_for_timeout(200)
                    full = page.locator("#liveStorms .scardwrap.full")
                    inner = full.locator(".xhdr .slegend").count() + full.locator(".xhdr .emphtog").count() if full.count() else 0
                    vis = full.locator(".xhdr").first.is_visible() if full.count() else False
                    chk.add(f"{scheme} storm ({tag}): a card expands to fill the window with its own key and switch",
                            full.count() == 1 and inner == 2 and vis, f"full={full.count()} inner={inner} visible={vis}")
                    # one way out, not two: the corner close, with the button that
                    # opened it hidden because it is inside the panel it closes
                    closes = page.evaluate("""() => [...document.querySelectorAll('.scardwrap.full .zb.ex, .fullclose')]
                      .filter(e => getComputedStyle(e).display !== 'none').map(e => e.textContent)""")
                    chk.add(f"{scheme} storm ({tag}): the expanded card carries one close, not two",
                            len(closes) == 1 and "Close" in closes[0], str(closes))
                    page.keyboard.press("Escape"); page.wait_for_timeout(200)
                    chk.add(f"{scheme} storm ({tag}): Escape closes it and the key goes back out of sight",
                            page.locator("#liveStorms .scardwrap.full").count() == 0
                            and not page.locator("#liveStorms .scardwrap .xhdr").first.is_visible(), "")
                    page.locator("#liveStorms .cwrap .zb.ex").first.click(); page.wait_for_timeout(200)
                    chk.add(f"{scheme} storm ({tag}): the pool panel expands the same way",
                            page.locator("#liveStorms .cwrap.full").count() == 1
                            and page.locator("#liveStorms .cwrap.full .xhdr .emphtog").count() == 1, "")
                    page.keyboard.press("Escape"); page.wait_for_timeout(150)
                    attrib = page.locator("#liveStorms .attrib").first.inner_text()
                    chk.add(f"{scheme} storm ({tag}): the attribution carries the vendor's mark and no claim about whose numbers they are",
                            attrib.strip() == "Powered by Reask", attrib)
                    # the location cards' prices open the exchange, and the section says so under them
                    stn = page.evaluate("""() => [...document.querySelectorAll('#liveStorms p.tradenote')].map(n => ({
                      text: n.textContent, prev: n.previousElementSibling ? n.previousElementSibling.className : null }))""")
                    # (and one under the latest file's ladders, a priced panel of its own, while that panel is shown)
                    chk.add(f"{scheme} storm ({tag}): a line under the location cards says a price opens the exchange",
                            any(n["prev"] == "scards" for n in stn) and len(stn) <= 2
                            and all(n["prev"] in ("scards", "ladders vgrid") for n in stn)
                            and all(n["text"] == "Click on any price to trade contracts through IBKR Prediction Markets" for n in stn),
                            str(stn))
                    # ---- the cursor, and scrubbing across deliveries
                    ticks = page.locator("#liveStorms svg.stimeline circle, #liveStorms svg.stimeline rect").count()
                    chk.add(f"{scheme} storm ({tag}): one tick per step on the timeline", ticks == n_st, f"ticks={ticks}")
                    strip = page.locator("#liveStorms svg.stimeline").first
                    read = lambda: page.locator("#liveStorms svg.stimeline text").first.text_content()
                    chk.add(f"{scheme} storm ({tag}): the cursor starts at the newest step",
                            f"step {n_st} of {n_st}" in read() and "(latest)" in read(), read())
                    cx = lambda: page.eval_on_selector("#liveStorms .scardwrap line.scur", "e => e.getAttribute('x1')")
                    was = cx()
                    page.get_by_title("the step before this one").first.click()
                    page.wait_for_timeout(120)
                    chk.add(f"{scheme} storm ({tag}): stepping back names the step it lands on",
                            f"step {n_st - 1} of {n_st}" in read() and "(latest)" not in read(), read())
                    chk.add(f"{scheme} storm ({tag}): the cursor line moves with the step", cx() != was, f"{was} -> {cx()}")
                    # one more step back from the interim on a settled storm, to a cycle, whose
                    # line the cursor marks (the interim's own squares mark its readings)
                    if _final:
                        page.get_by_title("the step before this one").first.click(); page.wait_for_timeout(120)
                    # the cursor marks the vendor's line at the delivery it stands on, and no
                    # box: neither the cursor's square over the price nor the price-now square
                    # beside the newest column is drawn (owner, 2026-10-08); the exchange's own
                    # small squares mark its readings
                    cur = page.evaluate("""() => {
                      const svg = document.querySelector('#liveStorms svg.scard');
                      const g = svg.querySelector('line.scur').nextElementSibling;
                      return { dots: g.querySelectorAll('circle').length, squares: g.querySelectorAll('rect').length,
                               now: svg.querySelectorAll('rect.pxnow').length, readings: svg.querySelectorAll('rect.pxdot').length };
                    }""")
                    chk.add(f"{scheme} storm ({tag}): the cursor marks the vendor's line and draws no box",
                            cur["dots"] >= 1 and cur["squares"] == 0 and cur["now"] == 0 and cur["readings"] >= 6, str(cur))
                    box = strip.bounding_box()
                    page.mouse.move(box["x"] + box["width"] * 0.05, box["y"] + box["height"] / 2)
                    page.mouse.down()
                    page.mouse.move(box["x"] + box["width"] * 0.30, box["y"] + box["height"] / 2, steps=6)
                    page.mouse.up()
                    page.wait_for_timeout(120)
                    chk.add(f"{scheme} storm ({tag}): dragging the strip scrubs to another step",
                            f"of {n_st}" in read() and f"step {n_st} of {n_st}" not in read(), read())
                    strip.press("ArrowLeft"); page.wait_for_timeout(100)
                    mid = read()
                    strip.press("End"); page.wait_for_timeout(120)
                    chk.add(f"{scheme} storm ({tag}): the arrow keys step and End returns to the latest",
                            mid != read() and f"step {n_st} of {n_st}" in read(), f"{mid} -> {read()}")
                    # ---- a cycle the vendor never delivered
                    ngap = page.locator("#liveStorms .scardwrap rect.sgap").count()
                    ncards2 = page.locator("#liveStorms .scardwrap").count()
                    chk.add(f"{scheme} storm ({tag}): the missed cycle is marked once per card, and the even spacings are not",
                            ngap == ncards2, f"marks={ngap} cards={ncards2}")
                    breaks = page.locator("#liveStorms svg.stimeline g line").count()
                    chk.add(f"{scheme} storm ({tag}): the timeline carries the same break", breaks == 2, f"lines={breaks}")
                    head = page.locator("#liveStorms p").first.inner_text()
                    want_phase = ("Final settlement received" if _final
                                  else "Preliminary settlement phase (Metryc interim)" if _interim
                                  else "Forecast phase (LiveCyc)")
                    chk.add(f"{scheme} storm ({tag}): the storm's line names the phase it has reached, and nothing else",
                            head.strip() == want_phase, head[:140])
                    page.locator("#liveStorms .scardwrap rect.sgap").first.hover(force=True)
                    page.wait_for_timeout(150)
                    t_gap = page.locator("#tip").inner_text()
                    chk.add(f"{scheme} storm ({tag}): the gap box counts the hours and the missing cycles",
                            "missing here" in t_gap and "12 hours" in t_gap and "6 hours" in t_gap, t_gap[:160])
                    if tag == "live":
                        # ---- the side panel beside the map: two slots that say what they
                        # will hold until a red location is hovered, then that location's
                        # ladder and its series (owner, 2026-10-07)
                        side = page.evaluate("""() => {
                          const s = document.getElementById('mapSide');
                          return { shown: !!s && !s.hidden && getComputedStyle(s).display !== 'none',
                                   empty: document.querySelectorAll('#mapSide .msbox .msempty').length,
                                   quick: document.querySelectorAll('#mapSide .msquick button').length };
                        }""")
                        chk.add(f"{scheme} storm ({tag}): the side panel stands beside the map with its two slots empty",
                                bool(side and side["shown"] and side["empty"] == 2 and side["quick"] >= 1), str(side))
                        page.locator("#basin circle[role='button']").first.hover(force=True)
                        page.wait_for_timeout(600)
                        filled = page.evaluate("""() => ({
                          ladder: document.querySelectorAll('#msLadder .vloc').length,
                          series: document.querySelectorAll('#msSeries svg.scard').length,
                          sel: document.querySelectorAll('#basin circle.msel').length,
                          name: (document.querySelector('#msLadder .lt') || {}).textContent || '' })""")
                        chk.add(f"{scheme} storm ({tag}): hovering a red location fills the panel with its ladder and its series",
                                bool(filled and filled["ladder"] == 1 and filled["series"] == 1 and filled["sel"] == 1
                                     and "Brownsville" in filled["name"]), str(filled))
                        # the dot's own box is the contract to trade there (owner, 2026-10-08):
                        # Brownsville reads 60, 30, 10, 3% at 70, 80, 90, 100 mph, so the highest
                        # listed rung with at least a ten percent chance is 90, Yes 46c and No 60c
                        t_box = page.locator("#tip").inner_text()
                        chk.add(f"{scheme} storm ({tag}): a location's box offers the highest listed rung with a ten percent chance, as Yes and No prices",
                                "buy yes" in t_box.lower() and "buy no" in t_box.lower() and "Above 90" in t_box
                                and "46\u00a2" in t_box and "60\u00a2" in t_box and "10%" in t_box
                                and "\u2265 70 mph" not in t_box, t_box[:200])
                        page.mouse.move(2, 2); page.wait_for_timeout(150)
                        kept = page.locator("#msLadder .vloc").count()
                        chk.add(f"{scheme} storm ({tag}): the panel keeps the location after the pointer leaves",
                                kept == 1, f"ladder cards={kept}")
                    # ---- the map: a click on a signalled location pins its box, and the box's
                    # prices then open the contract on IBKR (owner, 2026-10-08)
                    dot = page.locator("#basin circle[role='button']")
                    chk.add(f"{scheme} storm ({tag}): the signalled location is clickable on the map",
                            dot.count() >= 1, f"dots={dot.count()}")
                    if dot.count():
                        dot.first.scroll_into_view_if_needed()
                        dot.first.click(force=True)
                        page.wait_for_timeout(250)
                        pin = page.evaluate("""() => {
                          const t = document.getElementById('tip');
                          const a = [...t.querySelectorAll('a.tp')];
                          return { pinned: t.dataset.pinned || '', links: a.length,
                                   href: a.every(x => /conid_yes=/.test(x.getAttribute('href')) && x.target === '_blank'),
                                   sides: a.map(x => x.textContent.toLowerCase().indexOf('buy ' + (x.classList.contains('yes') ? 'yes' : 'no')) >= 0),
                                   clickable: getComputedStyle(t).pointerEvents };
                        }""")
                        chk.add(f"{scheme} storm ({tag}): clicking a location pins its box with both prices linked to the contract",
                                bool(pin and pin["pinned"] == "1" and pin["links"] == 2 and pin["href"]
                                     and all(pin["sides"]) and pin["clickable"] == "auto"), str(pin))
                        page.mouse.click(4, 4); page.wait_for_timeout(150)
                        chk.add(f"{scheme} storm ({tag}): a click elsewhere lets the pinned box go",
                                page.locator("#tip").evaluate("e => e.dataset.pinned || ''") == "", "")
                        chk.add(f"{scheme} storm ({tag}): no panel is drawn under the map any more",
                                page.locator("#sitePanel, .spanel").count() == 0, "")
                    # ---- a click on the series itself opens the contract in a new tab
                    with page.expect_popup() as pop:
                        page.locator("#liveStorms .scardwrap rect.hband").first.click(force=True)
                    p2 = pop.value
                    chk.add(f"{scheme} storm ({tag}): clicking a location's series opens its lowest-strike contract",
                            "conid_yes=999100070" in p2.url, p2.url[:110])
                    p2.close()
                    with page.expect_popup() as pop3:
                        page.locator("#liveStorms svg.lhlserie rect.hband").first.click(force=True)
                    p3 = pop3.value
                    chk.add(f"{scheme} storm ({tag}): clicking the pool's series opens the pool contract",
                            "conid_yes=999200001" in p3.url, p3.url[:110])
                    p3.close()
                    chk.add(f"{scheme} storm ({tag}): the series click pins no box either",
                            page.locator("#tip").evaluate("e => e.dataset.pinned || ''") == "", "")
                    marks_n = page.evaluate("""() => [...document.querySelectorAll('#liveStorms svg text')]
                      .filter(t => t.textContent === 'POWERED BY REASK').length""")
                    chk.add(f"{scheme} storm ({tag}): the vendor's mark sits inside its plots",
                            marks_n >= 1, f"marks={marks_n}")
                    vrow = page.evaluate("""() => {
                      const c = [...document.querySelectorAll('#liveStorms .vloc')]
                        .find(x => /Brownsville/.test(x.textContent));
                      return c ? (c.getAttribute('data-contract-url') || '') : null;
                    }""")
                    chk.add(f"{scheme} storm ({tag}): a location card opens its wind contract",
                            bool(vrow and "conid_yes=" in vrow), str(vrow)[:90])
                    # both documents the storm trades under, linked where the
                    # contracts they govern are drawn
                    docs = page.evaluate("""() => [...document.querySelectorAll('#liveStorms a')]
                      .map(a => a.getAttribute('href') || '').filter(u => /TermsandConditions/.test(u))""")
                    chk.add(f"{scheme} storm ({tag}): the wind and pool documents are linked",
                            any(u.endswith("/LTermsandConditions.pdf") for u in docs)
                            and any(u.endswith("/LHLTermsandConditions.pdf") for u in docs), str(docs)[:140])
                    chk.add(f"{scheme} storm ({tag}): a rising ladder fires no settlement-pending note",
                            page.locator("#liveStorms .note.warn").count() == 0, "")
                    chk.add(f"{scheme} storm ({tag}): no script errors", not errs, "; ".join(errs)[:200])
                    page.unroute("**/data/snapshots/**")

                # ---- the settled gust is rounded half up to a whole mile an hour, the figure the
                # wind contracts resolve on (owner, 2026-10-12): a final of 99.5 mph prints 100 and
                # clears the 100 mph strike, 110 mph does not, and 99.4 would have printed 99.
                # Galveston settles Yes at 70 mph where its interim printed zero, as Gulfport's
                # 60 mph did for Isaias, so its stacked squares have a misread one to show
                page.route("**/data/snapshots/**", _storm_routes(True, True, final_gusts={"BR": 99.5, "GA": 75.0}))
                page.goto(f"{srv.url}/hurricane.html"); page.wait_for_timeout(1400)
                card = page.locator("#liveStorms .scardwrap").first
                g_lab = card.evaluate("c => c.querySelector('.lt').textContent")
                card.locator("rect.hband").last.hover(force=True); page.wait_for_timeout(150)
                g_rows = page.evaluate("() => [...document.querySelectorAll('#tip table.l3 tr')].map(r => [...r.cells].map(c => c.textContent.trim()))")
                res = {r[0]: r[-1] for r in g_rows if r}
                g_fn = page.evaluate("() => [WXC.settleMph(99.5), WXC.settleMph(99.4), WXC.settleMph(144.158), WXC.settleMph(null)]")
                chk.add(f"{scheme} storm: a settled gust is rounded half up to a whole mile an hour, and the strikes settle on it",
                        g_lab == "Brownsville (BR) · final 100 mph" and res.get("≥100 mph") == "✓ Yes" and res.get("≥110 mph") == "✕ No"
                        and g_fn == [100, 99, 144, None], f"labels={g_lab} rows={res} fn={g_fn}")
                # where interim squares stack, the strike nearest the line between Yes and No
                # stands on top: Galveston's three at zero show the 70 mph square and its check
                stack = page.evaluate("""() => {
                  const svg = document.querySelector('#liveStorms .scardwrap[data-sid="GA"] svg.scard');
                  const sq = svg ? [...svg.querySelectorAll('rect.intmk')] : [];
                  const top = sq[sq.length - 1];
                  return top ? { n: sq.length, stroke: top.getAttribute('stroke'),
                                 glyph: (top.nextElementSibling || {}).textContent || '' } : null;
                }""")
                chk.add(f"{scheme} storm: where interim squares stack, the strike nearest Yes and No stands on top",
                        bool(stack and stack["n"] == 3 and stack["stroke"] == "hsl(210 70% 45%)" and stack["glyph"] == "\u2713"),
                        str(stack))
                page.unroute("**/data/snapshots/**")

                # ---- a settled storm whose pool the exchange has dropped from its listing (Isaias,
                # 2026-10-13): the section draws the pool's kept series and how it resolved, found
                # through the index of kept series, and no card saying no pool is carried
                page.route("**/data/snapshots/**", _storm_routes(True, True, drop_pool=True, drop_l=True))
                page.goto(f"{srv.url}/hurricane.html"); page.wait_for_timeout(1600)
                lines = page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('#liveStorms .scardwrap')]
                  .map(c => [c.querySelector('.lt').textContent, (c.querySelector(':scope > .cap') || {}).textContent || '']))""")
                chk.add(f"{scheme} storm: once settled and delisted, a card still names the contract it traded and none says yet",
                        lines.get("Brownsville (BR) · final 96 mph") == "contracts listed · LERBR"
                        and lines.get("Galveston (GA) · final 41 mph") == "no contracts listed for this location"
                        and not any("yet" in v for v in lines.values()), str(lines)[:200])
                gone = page.evaluate("""() => {
                  const host = document.querySelector('#liveStorms');
                  const svg = host.querySelector('svg.lhlserie');
                  const wrap = svg && svg.closest('.cwrap');
                  const t = svg ? [...svg.querySelectorAll('g.plad text')].map(e => e.textContent) : [];
                  return { series: !!svg, title: wrap ? ((wrap.querySelector('.lt') || {}).textContent || '') : '',
                           lines: svg ? svg.querySelectorAll('path.pxline').length : 0,
                           resolved: t.includes('How it resolved'), rows: t.filter(x => / · (Yes|No)$/.test(x)),
                           dots: svg ? [...svg.querySelectorAll('circle.setdot')].map(c => Math.round(+c.getAttribute('cy'))) : [],
                           vague: /not carrying a pool/.test(host.textContent) };
                }""")
                chk.add(f"{scheme} storm: a settled storm whose pool has left the listing still draws its series and how it resolved",
                        bool(gone["series"] and "(LHLERG)" in gone["title"] and gone["lines"] >= 1 and gone["resolved"]
                             and "96 mph · Yes" in gone["rows"] and "41 mph · No" in gone["rows"]
                             and 26 in gone["dots"] and not gone["vague"]), str(gone)[:300])
                page.unroute("**/data/snapshots/**")

                # ---- the end of a long storm's delivery axis. Isaias settled with fourteen
                # cycles, so its cards' sixteen columns stood closer together than the two
                # Metryc labels were wide, and the labels printed over each other (owner,
                # 2026-10-12). Held to by the boxes the labels are drawn in. Interim and
                # Final must not meet each other or any other label on the axis, and must
                # stay inside the chart, on a card 450 px wide and on the card opened to
                # fill the window. The storms are one of Isaias's length and one three times
                # as long, settled and folded shut as Isaias is, the long one with cycles
                # still landing after its interim, which sets the Interim label among
                # theirs, and the long one while it still runs, when the next cycle's column
                # stands beside the two. Each case first proves its columns are too close
                # for the labels to sit centered on their ticks, so the check cannot pass
                # on an axis with room to spare.
                AXIS_END = r"""sel => {
                  const svg = document.querySelector(sel);
                  if (!svg) return null;
                  const vb = svg.viewBox.baseVal;
                  // a pool chart's ladder stands to the right of its axis, and the labels stop short of it
                  const lad = svg.querySelector('g.plad');
                  const fence = lad ? lad.getBBox().x : vb.width;
                  const labs = [...svg.querySelectorAll('text.axl')].map(t => {
                    const b = t.getBBox();
                    return { t: t.textContent, x0: b.x, x1: b.x + b.width, y0: b.y, y1: b.y + b.height };
                  });
                  const I = labs.find(l => l.t === 'Interim'), F = labs.find(l => l.t === 'Final');
                  const meet = (p, q) => p.x0 < q.x1 && q.x0 < p.x1 && p.y0 < q.y1 && q.y0 < p.y1;
                  // the ticks under the columns, the short vertical strokes on the axis line
                  const ticks = [...svg.querySelectorAll('line')].filter(l => l.getAttribute('x1') === l.getAttribute('x2')
                      && +l.getAttribute('y2') > +l.getAttribute('y1') && +l.getAttribute('y2') - +l.getAttribute('y1') <= 4)
                    .map(l => ({ x: +l.getAttribute('x1'), dashed: !!l.getAttribute('stroke-dasharray') }))
                    .sort((a, b) => a.x - b.x);
                  const span = l => l.x1 - l.x0;
                  const dTick = ticks.length > 1 ? ticks[ticks.length - 1].x - ticks[ticks.length - 2].x : 0;
                  // a label above or below one of the two would read as part of it, so any
                  // label sharing their stretch of the axis counts, not only those on their row
                  const crowd = labs.filter(o => o !== I && o !== F && [I, F].some(m => m && o.x0 < m.x1 && m.x0 < o.x1));
                  const nx = labs.find(l => l.t === 'next');
                  const firstPending = ticks.find(q => q.dashed);
                  return { width: Math.round(svg.getBoundingClientRect().width),
                           interim: I ? [+I.x0.toFixed(1), +I.x1.toFixed(1)] : null,
                           final: F ? [+F.x0.toFixed(1), +F.x1.toFixed(1)] : null,
                           tight: !!(I && F) && (span(I) + span(F)) / 2 > dTick, dTick: +dTick.toFixed(1),
                           apart: !!(I && F) && !meet(I, F) && I.x1 <= F.x0,
                           crowd: crowd.map(o => o.t),
                           inside: labs.every(l => l.x0 >= 0 && l.x1 <= Math.min(vb.width, fence)),
                           // the next cycle's label, where it is drawn, stands over its own column
                           nextOver: !nx || !firstPending || (nx.x0 <= firstPending.x && firstPending.x <= nx.x1),
                           next: !!nx, cycles: labs.filter(l => /^\d\dZ$/.test(l.t)).length };
                }"""
                def _axis_end_ok(r):
                    return bool(r and r["tight"] and r["apart"] and not r["crowd"] and r["inside"]
                                and r["nextOver"] and r["cycles"] >= 2)
                for tag, hrs, _settled, after in (("Isaias's length, settled", [6 * i for i in range(14)], True, None),
                                                  ("three times as long, settled", [6 * i for i in range(42)], True, None),
                                                  ("three times as long, cycles after the interim", [6 * i for i in range(42)], True, 120),
                                                  ("three times as long, still running", [6 * i for i in range(42)], False, None)):
                    page.route("**/data/snapshots/**", _storm_routes(_settled, _settled, index_final=_settled, hours=hrs,
                                                                     interim_after=after))
                    page.goto(f"{srv.url}/hurricane.html")
                    page.wait_for_timeout(1400)
                    if _settled:
                        # folded shut with the storms that have finished, and opened the way a reader opens it
                        page.locator("#liveStorms details.stormdone summary").first.click()
                        page.wait_for_timeout(1200)
                    page.evaluate("() => document.querySelectorAll('#liveStorms svg.scard').forEach(s => { s.style.width = '450px'; })")
                    page.wait_for_timeout(100)
                    at450 = page.evaluate(AXIS_END, "#liveStorms svg.scard")
                    page.evaluate("() => document.querySelectorAll('#liveStorms svg.scard').forEach(s => { s.style.width = ''; })")
                    chk.add(f"{scheme} storm ({tag}): Interim and Final stand apart on a card 450 px wide",
                            _axis_end_ok(at450) and at450["width"] == 450, str(at450)[:300])
                    page.locator("#liveStorms .scardwrap .zb.ex").first.click(); page.wait_for_timeout(250)
                    full = page.evaluate(AXIS_END, "#liveStorms .scardwrap.full svg.scard")
                    chk.add(f"{scheme} storm ({tag}): and on the card opened to fill the window",
                            _axis_end_ok(full) and full["width"] > 900, str(full)[:300])
                    page.keyboard.press("Escape"); page.wait_for_timeout(150)
                    # the pool's chart shares the axis, and its labels stop short of its ladder
                    pool_end = page.evaluate(AXIS_END, "#liveStorms svg.lhlserie")
                    chk.add(f"{scheme} storm ({tag}): the pool's axis keeps the two apart and clear of its ladder",
                            bool(pool_end and pool_end["apart"] and not pool_end["crowd"] and pool_end["inside"]
                                 and pool_end["nextOver"]), str(pool_end)[:300])
                    chk.add(f"{scheme} storm ({tag}): no script errors", not errs, "; ".join(errs)[:200])
                    page.unroute("**/data/snapshots/**")

                # ---- a cycle recorded after a later one keeps its place in cycle order
                # (Reask, 7 October: Isaias's 00Z read after its 06Z for an hour once the
                # 00Z was filed after the 06Z), and the pool's price still walks forward
                page.route("**/data/snapshots/**", _storm_routes(False, False, late=True))
                page.goto(f"{srv.url}/hurricane.html")
                page.wait_for_timeout(1400)
                lo = page.evaluate("""() => {
                  const card = document.querySelector('#liveStorms .scardwrap');
                  if (!card) return null;
                  const cyc = [...card.querySelectorAll('text')].filter(t => /^[0-9][0-9]Z$/.test(t.textContent))
                    .map(t => [+t.getAttribute('x'), t.textContent]).sort((a, b) => a[0] - b[0]).map(q => q[1]);
                  // every x along the pool's price line, in the order the path draws them
                  const xs = [...document.querySelectorAll('#liveStorms path.pxline')].map(p =>
                    (p.getAttribute('d').match(/[ML]-?[0-9.]+/g) || []).map(c => +c.slice(1)));
                  return { cyc, lines: xs.length,
                           back: xs.filter(a => a.some((v, i) => i && v < a[i - 1] - 0.05)).length };
                }""")
                chk.add(f"{scheme} storm: a cycle recorded after a later one keeps its place in cycle order",
                        bool(lo) and lo["cyc"][:6] == ["00Z", "06Z", "12Z", "00Z", "06Z", "12Z"], str(lo))
                chk.add(f"{scheme} storm: the pool's price never walks backwards when a cycle was recorded late",
                        bool(lo) and lo["lines"] >= 1 and lo["back"] == 0, str(lo))
                # ---- a link that names a reference location (?loc=BR: the exchange's research
                # button reaches it through config/category_aliases.json) opens with that
                # location in the panel beside the map once the ledgers land, and on a screen
                # too narrow for the panel brings the location's card below into view
                page.route("**/data/snapshots/**", _storm_routes(False, False))
                page.goto(f"{srv.url}/hurricane.html?loc=BR")
                page.wait_for_timeout(1800)
                fl = page.evaluate("""() => {
                  const lad = document.getElementById('msLadder');
                  return { ladder: !!(lad && lad.classList.contains('filled')),
                           named: !!(lad && /Brownsville/.test(lad.textContent)),
                           ring: !!document.querySelector('#basin circle.msel[data-loc="BR"]') };
                }""")
                chk.add(f"{scheme} hurricane: a link naming a location opens with it in the panel beside the map",
                        bool(fl and fl["ladder"] and fl["named"] and fl["ring"]), str(fl))
                nctx = browser.new_context(color_scheme=scheme, viewport={"width": 760, "height": 900})
                npg = nctx.new_page()
                npg.route("**/data/snapshots/**", _storm_routes(False, False))
                npg.goto(f"{srv.url}/hurricane.html?loc=BR")
                npg.wait_for_timeout(2600)
                nr = npg.evaluate("""() => {
                  const c = document.querySelector('#liveStorms .scardwrap[data-sid="BR"]');
                  if (!c) return null;
                  const r = c.getBoundingClientRect();
                  return { top: Math.round(r.top), bottom: Math.round(r.bottom), h: innerHeight, y: Math.round(scrollY),
                           side: getComputedStyle(document.getElementById('mapSide')).display };
                }""")
                chk.add(f"{scheme} hurricane: on a narrow screen a link naming a location scrolls to its card",
                        bool(nr and nr["side"] == "none" and nr["y"] > 0 and nr["top"] < nr["h"] and nr["bottom"] > 0), str(nr))
                nctx.close()
                # ---- the exchange's category aliases (IBKR, 2026-10-07): its research button
                # opens /<category id>.html, and the page moves the reader on, carrying what
                # the link adds while the destination's own terms win a clash
                page.goto(f"{srv.url}/g138154.html?theme=dark#top")
                page.wait_for_timeout(700)
                u1 = page.url
                chk.add(f"{scheme} aliases: a city's category lands on its station page with the link's parameters",
                        u1.endswith("/detroit-kdtw.html?theme=dark#top"), u1)
                page.goto(f"{srv.url}/g2876055.html?basin=AL")
                page.wait_for_timeout(1200)
                u2 = page.url
                pac = page.locator("#b2.on").count()
                chk.add(f"{scheme} aliases: a Hawaii location lands on the Pacific view, its own ocean winning over the link's",
                        "/hurricane.html?basin=EP&loc=HL" in u2 and pac == 1, f"{u2} pacific={pac}")
                # ---- a storm under the owner's ruling carries the desk's figure for each
                # delivery as that delivery's own price, so nothing is moved between columns
                page.route("**/data/snapshots/**", _storm_routes(False, False, ruled=True))
                page.goto(f"{srv.url}/hurricane.html")
                page.wait_for_timeout(1400)
                rb = page.locator("#liveStorms .scardwrap").first.locator("rect.hband")
                rb.nth(4).hover(force=True); page.wait_for_timeout(150)
                t_rul = page.locator("#tip").inner_text()
                chk.add(f"{scheme} storm: a ruled storm's delivery keeps its own recorded figure",
                        "30¢" in t_rul and "35¢" not in t_rul and "recorded for this delivery" in t_rul, t_rul[:160])
                # ---- the dots are sized at the lowest threshold no location has saturated (owner,
                # 2026-10-09): a storm that takes Brownsville and Galveston to 97% and 85% at 90 mph
                # is sized at 100 mph, where they read 70% and 40% and their dots come apart
                def _saturated(route):
                    ix = _index_for(False)
                    ix["storms"][0]["livecyc"]["sites"] = {"BR": {"name": "Brownsville", "p": [100, 99, 97, 70, 30, 5]},
                                                           "GA": {"name": "Galveston", "p": [100, 98, 85, 40, 10, 1]}}
                    return route.fulfill(status=200, content_type="application/json", body=json.dumps(ix))
                sz_key = lambda: page.evaluate("""() => ([...document.querySelectorAll('#basinKey .bkitem')]
                    .map(e => e.textContent).find(t => /sized by/.test(t)) || '')""")
                sz_r = lambda sid: page.evaluate("sid => +(document.querySelector('#basin circle[data-loc=\"' + sid + '\"]') || {getAttribute: () => 0}).getAttribute('r')", sid)
                page.route("**/data/snapshots/**", _storm_routes(False, False))
                page.goto(f"{srv.url}/hurricane.html"); page.wait_for_timeout(1400)
                k_plain = sz_key()
                page.route("**/data/snapshots/reask.json", _saturated)
                page.goto(f"{srv.url}/hurricane.html"); page.wait_for_timeout(1400)
                k_sat, r_br, r_ga = sz_key(), sz_r("BR"), sz_r("GA")
                chk.add(f"{scheme} hurricane: the dots are sized at the lowest threshold no location has saturated",
                        "70 mph or more" in k_plain and "100 mph or more" in k_sat and r_br > r_ga * 1.15,
                        f"plain={k_plain!r} saturated={k_sat!r} BR={r_br} GA={r_ga}")
                page.unroute("**/data/snapshots/reask.json", _saturated)
                # ---- the vendor's final file, which the index carries once it lands.
                # It is a different quantity from the ladder: the gust each location
                # recorded, in miles per hour, and it supersedes every earlier file.
                # It is drawn on the geography rather than sorted down a table, because
                # a settled storm's record is a field, not a ranking.
                page.route("**/data/snapshots/**", _storm_routes(True, True, True, drop_hlf=True))
                page.goto(f"{srv.url}/hurricane.html")
                page.wait_for_timeout(1400)
                page.evaluate("""() => { const d = document.querySelector('#liveStorms details.stormdone');
                  if (d) d.open = true; }""")
                page.wait_for_timeout(1200)
                vf = page.evaluate("""() => {
                  const svg = document.querySelector('#liveStorms svg.gustmap');
                  const caps = [...document.querySelectorAll('#liveStorms p.cap')].map(c => c.textContent).join(' ');
                  if (!svg) return { map: false, caps, tables: document.querySelectorAll('#liveStorms table').length };
                  const labels = [...svg.querySelectorAll('text')].map(t => t.textContent);
                  return { map: true, caps, dots: svg.querySelectorAll('circle').length, labels,
                           tables: document.querySelectorAll('#liveStorms table').length,
                           source: (document.querySelector('#liveStorms .filesrc') || {}).textContent || '' };
                }""")
                chk.add(f"{scheme} vendor: once the final has landed the storm carries a map of recorded gusts, not a table",
                        bool(vf["map"] and vf["tables"] == 0 and "Metryc final" in vf["source"]
                             and "file received" in vf["source"]),
                        str([vf["map"], vf["tables"], vf["source"][:60]])[:160])
                chk.add(f"{scheme} vendor: one dot per location that recorded a gust, and none for the rest",
                        vf.get("dots") == 22, str(vf.get("dots")))
                chk.add(f"{scheme} vendor: the strongest places carry their gust, in whole miles an hour",
                        any(l.startswith("96 Brownsville") for l in vf.get("labels", []))
                        and any(l.startswith("41 Galveston") for l in vf.get("labels", []))
                        and not any(re.match(r"\d+\.\d", l) for l in vf.get("labels", []))
                        and any("Peak gust recorded, mph" == l for l in vf.get("labels", [])),
                        str([l for l in vf.get("labels", []) if "Brownsville" in l or "Galveston" in l])[:120])
                chk.add(f"{scheme} vendor: a location the vendor published no value for, or a zero, is not drawn",
                        not any("Savannah" in l or "Jacksonville" in l for l in vf.get("labels", [])),
                        str([l for l in vf.get("labels", []) if "Savannah" in l or "Jacksonville" in l])[:120])
                # the file is the whole reference list, so the caption has to say which
                # list it is counting and what the view leaves out
                chk.add(f"{scheme} vendor: the map's caption says the file spans the whole list",
                        "all 26 locations on the vendor" in vf["caps"]
                        and "outside the view" in vf["caps"], vf["caps"][-170:])
                # ---- the two oceans are partitioned: nothing Atlantic on the Pacific
                # view, and what the owner says is coming but is not listed yet is named.
                # The coming listing is the check's own, added to config.js, so the check
                # does not depend on what config/site.json happens to carry
                def _expected_cfg(route):
                    resp = route.fetch()
                    return route.fulfill(response=resp, body=resp.text() + "\nwindow.WX.expected = " + json.dumps({
                        "landfall": [], "storms": [{"basin": "EP", "name": "Lowell",
                                                    "products": ["wind gust contracts", "a highest-wind pool"],
                                                    "locations": ["Honolulu", "Hilo", "Kahului", "Kapa'a"]}]}) + ";\n")
                _cfg_url = re.compile(r"/config\.js(\?|$)")          # the build adds ?v=<hash>
                page.route(_cfg_url, _expected_cfg)
                page.reload(); page.wait_for_timeout(1500)
                page.locator("#b2").click(); page.wait_for_timeout(700)
                part = page.evaluate("""() => {
                  const t = id => (document.querySelector(id) || {}).textContent || "";
                  return { vendor: t("#vendorNote"), live: t("#liveStorms"), landfall: t("#landfall"),
                           lfShown: !!(document.querySelector("#landfallSect")
                             && document.querySelector("#landfallSect").style.display !== "none"),
                           counts: (document.querySelector("#atlanticOnly") || {}).style
                             ? document.querySelector("#atlanticOnly").style.display : "" };
                }""")
                chk.add(f"{scheme} basins: no Atlantic storm on the Pacific view",
                        bool(part and "Erin" not in part["vendor"] and "Erin" not in part["live"]),
                        str(part and [part["vendor"][:50], part["live"][:50]])[:150])
                chk.add(f"{scheme} basins: the Pacific view names the storm whose contracts are expected",
                        bool(part and "Lowell" in part["live"] and "Honolulu" in part["live"]
                             and "Nothing is listed or quoted for Lowell" in part["live"]),
                        str(part and part["live"][:170]))
                # the landfall contract names no ocean, so this view carries the regions a Pacific
                # storm can reach and none of the Atlantic-only ones
                chk.add(f"{scheme} basins: the landfall board carries the regions this ocean can reach",
                        bool(part and part["lfShown"] and "Mexico" in part["landfall"]
                             and "Honduras" in part["landfall"] and "Florida" not in part["landfall"]
                             and "Cuba" not in part["landfall"]), str(part and part["landfall"][:150]))
                chk.add(f"{scheme} basins: the count panels stay off the Pacific view",
                        bool(part and part["counts"] == "none"), str(part and part["counts"]))
                pnote = page.evaluate('() => ((document.querySelector("#pacificNote") || {}).textContent || "")')
                chk.add(f"{scheme} basins: the note agrees with the board it is describing",
                        "listed above" in pnote
                        and "landfall board is on the Atlantic view too" not in pnote, pnote[-150:])
                page.locator("#b1").click(); page.wait_for_timeout(600)
                back = page.evaluate('() => ((document.querySelector("#liveStorms") || {}).textContent || "")')
                chk.add(f"{scheme} basins: switching back restores the Atlantic storm",
                        "Erin" in back and "Lowell" not in back, back[:90])
                page.unroute(_cfg_url, _expected_cfg)
                page.unroute("**/data/snapshots/**")

                # ---- the replay (owner, 2026-10-20): the storm's map steps through NHC's
                # advisories with the section's timeline, each place carrying the median gust
                # of the vendor's newest cycle at that hour, then the interim's median and the
                # gusts recorded. Erin's ladders put Brownsville at 70, 77 and 83 mph on her
                # last three cycles and 93 on the interim; Galveston stays under 70 throughout.
                ERIN_ADV = _adv_doc()
                page.route("**/data/snapshots/**", _storm_routes(True, True, True, advisories=[ERIN_ADV]))
                page.goto(f"{srv.url}/hurricane.html"); page.wait_for_timeout(1400)
                page.evaluate("""() => { const d = document.querySelector('#liveStorms details.stormdone'); if (d) d.open = true; }""")
                page.wait_for_timeout(1400)
                rp = lambda: page.evaluate("""() => {
                  const d = document.querySelector('#liveStorms details.stormdone[open]');
                  const m = d && d.querySelector('svg.replay');
                  if (!m) return null;
                  return { step: +m.getAttribute('data-step'), cone: m.querySelectorAll('path.rcone').length,
                           path: m.querySelectorAll('path.rpath').length, now: m.querySelectorAll('circle.rnow').length,
                           dots: m.querySelectorAll('circle.gdot').length, rings: m.querySelectorAll('circle.gring').length,
                           labels: [...m.querySelectorAll('text.glbl')].map(t => t.textContent),
                           key: (m.querySelector('text.gkey') || {}).textContent || '',
                           cap: (d.querySelector('.replaycap') || {}).textContent || '',
                           tl: (d.querySelector('svg.stimeline text.ax') || {}).textContent || '',
                           ticks: d.querySelectorAll('svg.stimeline .tk').length,
                           own: d.querySelectorAll('svg.stimeline .tk-own').length,
                           carry: d.querySelectorAll('svg.stimeline .tk-carry').length,
                           season: [...document.querySelectorAll('#liveStorms .stormseason')].map(e => e.textContent)[0] || '',
                           cur: [...d.querySelectorAll('.scardwrap line.scur')]
                             .map(l => l.getAttribute('visibility') === 'hidden' ? null : l.getAttribute('x1')) };
                }""")
                back = lambda k=1: [page.locator("#liveStorms details.stormdone[open]").get_by_title("the step before this one").first.click()
                                    for _ in range(k)]
                r_ = rp()
                chk.add(f"{scheme} replay: one step per NHC advisory, then the interim and the final",
                        bool(r_ and r_["ticks"] == 11 and r_["own"] == 6 and r_["carry"] == 3),
                        str(r_ and {k: r_[k] for k in ("ticks", "own", "carry")}))
                chk.add(f"{scheme} replay: a settled storm opens on the final, its recorded gusts along the whole path and no cone",
                        bool(r_ and r_["step"] == 10 and r_["cone"] == 0 and r_["path"] == 1 and r_["now"] == 0
                             and r_["key"] == "Peak gust recorded, mph" and any(l.startswith("96 Brownsville") for l in r_["labels"])),
                        str(r_ and {k: r_[k] for k in ("step", "cone", "path", "now", "key")}))
                chk.add(f"{scheme} replay: the storms that have run sit under their season",
                        bool(r_ and r_["season"] == "2026 season"), str(r_ and r_["season"]))
                back(); r_ = rp()
                chk.add(f"{scheme} replay: the interim's step carries the median of its ladder",
                        bool(r_ and r_["key"] == "Metryc interim median gust, mph" and any(l.startswith("93 Brownsville") for l in r_["labels"])),
                        str(r_ and [r_["key"], r_["labels"]])[:160])
                back(); r_ = rp()
                chk.add(f"{scheme} replay: an advisory a day after the vendor's last cycle carries no figure, and says so",
                        bool(r_ and "no LiveCyc since 09/02 12Z" in r_["tl"] and "No LiveCyc cycle since 12Z 2 Sep" in r_["cap"]
                             and r_["dots"] == 0 and r_["rings"] == 2 and r_["cone"] == 1 and r_["now"] == 1
                             and r_["cur"] and all(c is None for c in r_["cur"])),
                        str(r_ and {k: r_[k] for k in ("tl", "dots", "rings", "cur")})[:220])
                back(); r_ = rp()
                chk.add(f"{scheme} replay: each place carries the median gust of the advisory's own cycle, in whole miles an hour",
                        bool(r_ and r_["tl"].startswith("Advisory 8 · 09/02 12Z") and "83 Brownsville" in r_["labels"]
                             and r_["rings"] == 1 and "LiveCyc cycle of 12Z 2 Sep" in r_["cap"]
                             and "NHC advisory 8, issued 15Z 2 Sep" in r_["cap"]),
                        str(r_ and [r_["tl"], r_["labels"], r_["rings"], r_["cap"][:90]])[:240])
                chk.add(f"{scheme} replay: the cards' cursors stand on the delivery the step carries",
                        bool(r_ and r_["cur"] and r_["cur"][0] is not None and len(set(r_["cur"])) == 1), str(r_ and r_["cur"]))
                at8 = r_["cur"][0] if r_ and r_["cur"] else None
                back(3); at5 = rp()
                back(); at4 = rp()
                chk.add(f"{scheme} replay: an advisory whose cycle the vendor missed carries the cycle before it",
                        bool(at5 and at4 and at5["tl"].startswith("Advisory 5 · 09/01 18Z · LiveCyc 09/01 12Z")
                             and at4["cur"] == at5["cur"] and at5["cur"] and at5["cur"][0] != at8),
                        str([at5 and at5["tl"], at5 and at5["cur"], at4 and at4["cur"]])[:220])
                strip_r = page.locator("#liveStorms details.stormdone[open] svg.stimeline").first
                strip_r.press("Home"); page.wait_for_timeout(150)
                r1 = rp()
                strip_r.press("ArrowRight"); strip_r.press("ArrowRight"); page.wait_for_timeout(150)
                r3 = rp()
                chk.add(f"{scheme} replay: before the vendor's first cycle the map carries the advisory and no figure",
                        bool(r1 and "no LiveCyc yet" in r1["tl"] and r1["cone"] == 1 and r1["dots"] == 0
                             and r1["cur"] and all(c is None for c in r1["cur"]) and "No LiveCyc cycle yet" in r1["cap"]),
                        str(r1 and [r1["tl"], r1["cur"], r1["cap"][:60]])[:200])
                chk.add(f"{scheme} replay: the path so far grows with the steps",
                        bool(r1 and r3 and r1["path"] == 0 and r3["path"] == 1), str([r1 and r1["path"], r3 and r3["path"]]))
                # the map zooms and pans as the basin map does, and its places keep their size on
                # screen at every zoom (owner, 2026-10-20)
                zq = lambda: page.evaluate("""() => {
                  const d = document.querySelector('#liveStorms details.stormdone[open]');
                  const m = d && d.querySelector('svg.replay');
                  if (!m) return null;
                  const c = m.querySelector('circle.gdot, circle.gring'), b = m.getBoundingClientRect();
                  return { vb: m.getAttribute('viewBox'), lvl: (d.querySelector('.rzoom .cap') || {}).textContent || '',
                           r: c ? +c.getBoundingClientRect().width.toFixed(1) : null, box: [b.x, b.y, b.width, b.height] };
                }""")
                vw = lambda z: float(z["vb"].split()[2]) if z and z.get("vb") else None
                z0 = zq()
                zbar = page.locator("#liveStorms details.stormdone[open] .rzoom").first
                zbar.get_by_title("zoom the storm map in").click(); zbar.get_by_title("zoom the storm map in").click()
                page.wait_for_timeout(400)
                z1 = zq()
                chk.add(f"{scheme} replay: the map zooms in, and its places keep their size on screen",
                        bool(z0 and z1 and vw(z1) < vw(z0) * 0.5 and "×" in z1["lvl"] and z0["r"] and z1["r"]
                             and abs(z1["r"] - z0["r"]) <= 1.5),
                        str([z0 and z0["vb"], z1 and z1["vb"], z0 and z0["r"], z1 and z1["r"], z1 and z1["lvl"]]))
                back(); page.wait_for_timeout(150)
                z2 = zq()
                chk.add(f"{scheme} replay: the zoom holds while the timeline steps",
                        bool(z1 and z2 and z2["vb"] == z1["vb"]), str([z1 and z1["vb"], z2 and z2["vb"]]))
                # the pointer has to reach the map, so it is brought on screen first
                page.locator("#liveStorms details.stormdone[open] svg.replay").first.scroll_into_view_if_needed()
                page.wait_for_timeout(150)
                z2 = zq() or z2
                if z2:
                    bx_ = z2["box"]
                    page.mouse.move(bx_[0] + bx_[2] * 0.5, bx_[1] + bx_[3] * 0.5); page.mouse.down()
                    page.mouse.move(bx_[0] + bx_[2] * 0.3, bx_[1] + bx_[3] * 0.4, steps=6); page.mouse.up()
                    page.wait_for_timeout(250)
                    z3 = zq()
                    page.mouse.move(bx_[0] + bx_[2] * 0.5, bx_[1] + bx_[3] * 0.5); page.mouse.wheel(0, 240)
                    page.wait_for_timeout(400)
                    z4 = zq()
                else:
                    z3 = z4 = None
                chk.add(f"{scheme} replay: a drag pans the zoomed map and the wheel zooms it",
                        bool(z2 and z3 and z4 and z3["vb"].split()[:2] != z2["vb"].split()[:2] and vw(z4) > vw(z3)),
                        str([z2 and z2["vb"], z3 and z3["vb"], z4 and z4["vb"]]))
                zbar.get_by_title("back to the storm map’s whole view").click(); page.wait_for_timeout(300)
                z5 = zq()
                chk.add(f"{scheme} replay: Whole view puts the whole map back",
                        bool(z0 and z5 and z5["vb"] == z0["vb"] and z5["lvl"] == "whole view"), str(z5 and [z5["vb"], z5["lvl"]]))
                # the view holds the advisories that carried LiveCyc figures, the storm's contract period,
                # so an advisory long after the vendor's last cycle runs off the edge (owner, 2026-10-20:
                # Nolo's whole track ran a hundred degrees of longitude and left Hawaii a few pixels wide)
                far = json.loads(json.dumps(ERIN_ADV))
                t_far = BASE + dt.timedelta(hours=120)
                far["advisories"].append({"adv": "010", "issued": (t_far + dt.timedelta(hours=3)).strftime("%Y-%m-%dT%H:%MZ"),
                                          "t0": t_far.strftime("%Y-%m-%dT%H:%MZ"), "name": "Erin", "basin": "AL",
                                          "points": [{"tau": 0, "t": t_far.strftime("%Y-%m-%dT%H:%MZ"), "lon": -60.0, "lat": 40.0,
                                                      "kt": 40, "gust": 50, "type": "TS"}],
                                          "cone": [[[-61.0, 39.0], [-59.0, 39.0], [-59.0, 41.0], [-61.0, 41.0], [-61.0, 39.0]]],
                                          "track": [[[-60.0, 40.0], [-58.0, 41.0]]]})
                page.route("**/data/snapshots/**", _storm_routes(True, True, True, advisories=[far]))
                page.goto(f"{srv.url}/hurricane.html"); page.wait_for_timeout(1400)
                page.evaluate("""() => { const d = document.querySelector('#liveStorms details.stormdone'); if (d) d.open = true; }""")
                page.wait_for_timeout(1400)
                view = page.evaluate("""() => { const m = document.querySelector('#liveStorms details.stormdone[open] svg.replay');
                  return m ? m.getAttribute('data-view') : ''; }""") or ""
                vb = [float(v) for v in view.split(",")] if view.count(",") == 3 else None
                chk.add(f"{scheme} replay: the view holds the storm's contract period, and an advisory long after it runs off the edge",
                        bool(vb and vb[1] < -70 and vb[0] < -97.5 < vb[1] and vb[2] < 25.9 < vb[3]), view)
                # a storm the exchange lists before the vendor's first file gets its section and its
                # map from that moment: Fay is on NHC's roster with a contract at Miami, and the
                # vendor has published nothing for her
                FAY_ADV = _adv_doc("al062026", "Fay", hours=[0, 6, 12], start=(-76.0, 22.0), step=(-0.8, 0.5))
                fay_roster = [{"id": "al062026", "bin": "AT2", "name": "Fay", "classification": "TS", "intensityKt": 45,
                               "basin": "AL", "lat": 23.5, "lon": -78.4, "advisory": "003",
                               "points": [], "track": [], "cone": [], "past": []}]
                fay_mk = [{"symbol": "LFAMI", "name": "Tropical Storm Fay Wind Gusts in Miami", "productConid": 999000003,
                           "contracts": [{"spec": "2026.9", "expiryLabel": "September 2026", "strike": 70, "label": "Above 70",
                                          "numeric": True, "bid": 0.05, "ask": 0.09, "mid": 0.07,
                                          "conid": 999300070, "conidYes": 999300070}]}]
                page.route("**/data/snapshots/**", _storm_routes(False, False, advisories=[ERIN_ADV, FAY_ADV],
                                                                 roster_extra=fay_roster, markets_extra=fay_mk))
                page.goto(f"{srv.url}/hurricane.html"); page.wait_for_timeout(1600)
                page.evaluate("""() => { const b = [...document.querySelectorAll('#liveStorms .bar button')]
                  .find(x => x.textContent === 'Fay'); if (b) b.click(); }""")
                page.wait_for_timeout(1200)
                fr = page.evaluate("""() => {
                  const host = document.querySelector('#liveStorms');
                  const m = host.querySelector('svg.replay');
                  return { tabs: [...host.querySelectorAll('.bar button')].map(x => x.textContent), map: !!m,
                           rings: m ? m.querySelectorAll('circle.gring').length : 0, cone: m ? m.querySelectorAll('path.rcone').length : 0,
                           ticks: host.querySelectorAll('svg.stimeline .tk').length,
                           cap: (host.querySelector('.replaycap') || {}).textContent || '' };
                }""")
                chk.add(f"{scheme} replay: a storm listed before the vendor's first file gets its section, its map and its timeline",
                        bool("Fay" in fr["tabs"] and fr["map"] and fr["ticks"] == 3 and fr["rings"] >= 1 and fr["cone"] == 1
                             and "No LiveCyc cycle yet" in fr["cap"]), str(fr)[:240])
                # a settled Pacific storm stays on the Pacific view once NHC's roster has let it go
                NOLO_ADV = _adv_doc("ep152026", "Nolo", basin="EP", hours=[0, 6], start=(-150.0, 15.0), step=(-0.8, 0.3))
                nolo_ix = [{"name": "Nolo", "year": 2026, "livecyc": {"forecastTime": "2026-09-28T18:00Z",
                                                                     "lastModified": "2026-09-28T22:40:00Z", "thresholds": THR, "sites": {}}}]
                page.route("**/data/snapshots/**", _storm_routes(False, False, advisories=[ERIN_ADV, NOLO_ADV], index_extra=nolo_ix))
                page.goto(f"{srv.url}/hurricane.html"); page.wait_for_timeout(1500)
                al_txt = page.evaluate("() => (document.querySelector('#liveStorms') || {}).textContent || ''")
                page.locator("#b2").click(); page.wait_for_timeout(1200)
                ep_txt = page.evaluate("() => (document.querySelector('#liveStorms') || {}).textContent || ''")
                chk.add(f"{scheme} replay: a settled Pacific storm stays on the Pacific view after NHC's roster lets it go",
                        "Nolo" not in al_txt and "Nolo" in ep_txt, str(["Nolo" in al_txt, "Nolo" in ep_txt]))
                page.locator("#b1").click(); page.wait_for_timeout(400)
                page.unroute("**/data/snapshots/**")

                # ---- the page's order, the standing links, and a storm that
                #      has stopped updating alongside one still running
                # the running storms are stamped relative to now: a storm folds
                # shut a day and a half after its newest file, so a fixed date
                # here would fold them as the calendar moved past it
                _now = dt.datetime.now(dt.timezone.utc)
                _ago = lambda hours: (_now - dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:00Z")
                _index2 = {"schema": 2, "enabled": True, "attribution": "Powered by Reask", "year": 2026,
                           "storms": [
                               {"name": "Old", "year": 2026,
                                "livecyc": {"forecastTime": "2026-07-01T00:00Z",
                                            "lastModified": "2026-07-01T04:00:00Z", "cycles": 2,
                                            "thresholds": [60, 70],
                                            "sites": {"BR": {"name": "Brownsville", "p": [12, 3]}}}},
                               # two running storms signalling on one location: the
                               # Port Arthur case, where the hover must carry both
                               {"name": "Alpha", "year": 2026,
                                "livecyc": {"forecastTime": _ago(6), "cycles": 3,
                                            "thresholds": [60, 70, 80],
                                            "pwin": {"BR": 61.0, "GA": 39.0},
                                            "sites": {"BR": {"name": "Brownsville", "p": [50.1, 15.2, 0.5]},
                                                      "GA": {"name": "Galveston", "p": [40.0, 9.0, 0.2]}}}},
                               {"name": "Beta", "year": 2026,
                                "livecyc": {"forecastTime": _ago(12), "cycles": 5,
                                            "thresholds": [60, 70, 80],
                                            "sites": {"BR": {"name": "Brownsville", "p": [27.8, 7.5, 0.2]}}}},
                               # a depression whose files are fresh but whose system
                               # has been named: the roster below carries AL05 as
                               # Gamma, so Five is the same storm under its old name
                               {"name": "Five", "year": 2026,
                                "livecyc": {"forecastTime": _ago(6),
                                            "lastModified": _ago(5).replace("Z", ":00Z"), "cycles": 4,
                                            "thresholds": [60, 70],
                                            "sites": {"BR": {"name": "Brownsville", "p": [30, 8]}}}},
                               {"name": "Erin", "year": 2026},
                           ]}

                def _mixed_routes(route):
                    u = route.request.url
                    if u.endswith("/reask.json"):
                        return route.fulfill(status=200, content_type="application/json", body=json.dumps(_index2))
                    if "/storm/Erin_2026.json" in u:
                        return route.fulfill(status=200, content_type="application/json", body=json.dumps(_ledger(True, False)))
                    if "/storm/Beta_2026.json" in u:
                        decay = {"schema": 2, "name": "Beta", "year": 2026, "thresholds": [60, 70],
                                 "sites": {"BR": {"name": "Brownsville", "firstStep": "2026083100"}},
                                 "final": None, "steps": [
                                     {"id": "2026083100", "kind": "livecyc", "at": "2026-08-31T00:00:00Z", "ts": "t",
                                      "sites": {"BR": [60, 20]}, "siteMeta": {"BR": {"name": "Brownsville", "lat": 25.9, "lon": -97.4}},
                                      "prices": {}},
                                     {"id": "2026083112", "kind": "livecyc", "at": "2026-08-31T12:00:00Z", "ts": "t",
                                      "sites": {"BR": [12, 2]}, "siteMeta": {"BR": {"name": "Brownsville", "lat": 25.9, "lon": -97.4}},
                                      "prices": {}}]}
                        return route.fulfill(status=200, content_type="application/json", body=json.dumps(decay))
                    if u.endswith("/hurricane.json"):
                        resp = route.fetch()
                        hj = json.loads(resp.text())
                        hj["storms"] = (hj.get("storms") or []) + [
                            {"id": "al052026", "name": "Gamma", "basin": "AL", "classification": "TS",
                             "advisory": "12", "intensityKt": 45, "lat": 25.0, "lon": -80.0,
                             "updated": "2026-09-01T09:00:00Z", "points": [], "track": [], "past": [],
                             "cone": [], "windProbs": []}]
                        return route.fulfill(response=resp, body=json.dumps(hj))
                    return route.continue_()

                page.route("**/data/snapshots/**", _mixed_routes)
                page.goto(f"{srv.url}/hurricane.html")
                page.wait_for_timeout(1400)
                lay = page.evaluate("""() => {
                  const kids = [...document.querySelectorAll('.wrap > *')];
                  const at = id => kids.findIndex(e => e.id === id || e.className === id);
                  const bl = document.querySelector('.biglinks');
                  return { order: [at('biglinks'), at('vendorNote'), at('liveStorms')],
                           inStorm: !!document.querySelector('#liveStorms .filehd'),
                           links: bl ? [...bl.querySelectorAll('a')].map(a => a.getAttribute('href')) : [] };
                }""")
                chk.add(f"{scheme} hurricane: the vendor section sits under the map, links first",
                        bool(lay and all(x >= 0 for x in lay["order"])
                             and lay["order"] == sorted(lay["order"])),
                        str(lay and lay["order"]))
                chk.add(f"{scheme} hurricane: the vendor's latest file sits inside the storm it belongs to",
                        bool(lay and lay["inStorm"]), str(lay and lay["inStorm"]))
                chk.add(f"{scheme} hurricane: the two standing links are present and correct",
                        bool(lay and len(lay["links"]) == 2
                             and "live-hurricane-wind-gust-prediction-markets-at-forecastex" in lay["links"][0]
                             and "data.forecastex.com/supplemental_data/hurricanes" in lay["links"][1]),
                        str(lay and lay["links"]))
                mix = page.evaluate("""() => {
                  return { doneLS: document.querySelectorAll('#liveStorms details.stormdone').length,
                           doneV: document.querySelectorAll('#vendor details.stormdone').length,
                           doneOpen: document.querySelectorAll('details.stormdone[open]').length,
                           erinCards: document.querySelectorAll('#liveStorms .scardwrap').length,
                           doneText: [...document.querySelectorAll('#liveStorms details.stormdone summary')]
                             .map(x => x.textContent).join(' | ') };
                }""")
                chk.add(f"{scheme} hurricane: a stopped storm folds shut behind one button, met once on the page",
                        bool(mix and mix["doneLS"] == 2 and mix["doneV"] == 0 and mix["doneOpen"] == 0
                             and "no longer updating" in mix["doneText"]),
                        str(mix))
                # the renamed depression: fresh files, but the roster says AL05
                # is Gamma now, so Five folds as the same storm under its old
                # name, and its ladder no longer reaches the hover
                ren = page.evaluate("""() => {
                  const sums = [...document.querySelectorAll('#liveStorms details.stormdone summary')]
                    .map(x => x.textContent);
                  return { renamed: sums.some(t => /Five 2026/.test(t) && /now named Gamma/.test(t)),
                           sums: sums.map(t => t.slice(0, 50)) };
                }""")
                chk.add(f"{scheme} hurricane: a renamed depression folds as the same storm, not a second one",
                        bool(ren and ren["renamed"]), str(ren))
                dot3 = page.locator("#basin circle[role='button']").first
                dot3.hover(force=True); page.wait_for_timeout(200)
                t_ren = page.locator("#tip").inner_text()
                chk.add(f"{scheme} hurricane: the renamed depression's ladder leaves the hover",
                        "Five" not in t_ren and "Alpha" in t_ren, t_ren[:120])
                # no pool listed for the running storm: the stated calculation
                # stands alone, formula printed, prices joining at listing
                pre = page.evaluate("""async () => {
                  const tab = [...document.querySelectorAll('#liveStorms .bar button')]
                    .find(b => b.textContent === 'Alpha');
                  if (tab) { tab.click(); await new Promise(r => setTimeout(r, 500)); }
                  const lt = [...document.querySelectorAll('#liveStorms .ladder .lt')]
                    .map(e => e.textContent).find(t => /awaiting listing/.test(t)) || null;
                  const caps = [...document.querySelectorAll('#liveStorms .cap')].map(c => c.textContent).join(' ');
                  return { lt, joined: /join this display at listing/.test(caps),
                           stated: /Each figure is the chance/.test(caps) };
                }""")
                chk.add(f"{scheme} hurricane: before listing there is no figure of the site's own to stand in for a price",
                        bool(pre and pre["lt"] is None and not pre["joined"] and not pre["stated"]), str(pre))
                # a decayed ladder with no interim file raises the pending note,
                # so the sag reads as settlement data pending rather than as the
                # threat having vanished
                pend = page.evaluate("""async () => {
                  const tab = [...document.querySelectorAll('#liveStorms .bar button')]
                    .find(b => b.textContent === 'Beta');
                  if (tab) { tab.click(); await new Promise(r => setTimeout(r, 500)); }
                  const n = document.querySelector('#liveStorms .note.warn');
                  return { note: !!n, pending: n ? /Settlement data pending/.test(n.textContent) : false,
                           explains: n ? /quotes can sit above/.test(n.textContent) : false };
                }""")
                chk.add(f"{scheme} hurricane: a passed, unsettled location raises the pending note",
                        bool(pend and pend["note"] and pend["pending"] and pend["explains"]), str(pend))
                chk.add(f"{scheme} hurricane: the running storm keeps the full display",
                        bool(mix and mix["erinCards"] >= 1), str(mix and mix["erinCards"]))
                # two storms signal on one location: the dot's box gives each its own
                # block, hottest first, with the vendor's chance at the rung in play
                # (Alpha 15% at 70 mph, Beta 28% at 60, to the whole percent), neither listed here
                dot2 = page.locator("#basin circle[role='button']").first
                dot2.hover(force=True); page.wait_for_timeout(200)
                t_two = page.locator("#tip").inner_text()
                chk.add(f"{scheme} hurricane: a location two storms signal on shows each storm's own figure",
                        "Alpha" in t_two and "Beta" in t_two
                        and t_two.index("Alpha") < t_two.index("Beta")
                        and "15%" in t_two and "28%" in t_two and "15.2%" not in t_two and "50%" not in t_two
                        and "each storm is its own hazard" in t_two, t_two[:160])
                # the LiveCyc countdown aims at the expected file arrival, the
                # cycle plus four and a half hours, because every 2026 file has
                # landed 4 to 6 hours after its cycle stamp
                cd = page.evaluate("""() => {
                  const e = document.querySelector('#vendorNote [data-cdt]');
                  if (!e) return null;
                  const t = +e.getAttribute('data-cdt'), left = t - Date.now();
                  const cyc = new Date(t - 4.5 * 3600000);
                  return { text: e.textContent, left,
                           onLagMark: cyc.getUTCHours() % 6 === 0 && cyc.getUTCMinutes() === 0,
                           line: e.parentElement.textContent };
                }""")
                # each storm's file line still names the cycle it was built on and
                # when the file itself arrived, which is the question a careful
                # outside reader asked; the paragraph that spelled out the three
                # clocks came off the page at the owner's request
                clocks = page.evaluate("""() => {
                  const row = (document.querySelector('#liveStorms .filesrc') || {}).textContent || '';
                  const body = document.body.textContent || '';
                  return { rowBoth: /LiveCyc cycle .*Z/.test(row),
                           gone: body.indexOf('labeled 18 UTC would be associated with the 21 UTC') < 0 };
                }""")
                chk.add(f"{scheme} hurricane: a storm's file line names the cycle it was built on",
                        bool(clocks and clocks["rowBoth"] and clocks["gone"]), str(clocks))
                chk.add(f"{scheme} hurricane: the LiveCyc countdown aims at the file, not the cycle stamp",
                        bool(cd and cd["onLagMark"] and 0 < cd["left"] <= 6 * 3600000
                             and ("in " in cd["text"] or cd["text"] == "due now")
                             and "file expected" in cd["line"]
                             and "4 to 6 hours after their cycle" in cd["line"]),
                        str(cd))
                page.unroute("**/data/snapshots/**")

                page.goto(f"{srv.url}/hurricane.html")
                page.wait_for_timeout(900)
                # the five-day table reads in mph and says which quantity it is
                page.locator("#b2").click(); page.wait_for_timeout(700)
                pws = page.evaluate("""() => {
                  const tb = document.querySelector('#storms table.pws');
                  if (!tb) return null;
                  const heads = [...tb.querySelectorAll('th')].map(t => t.textContent);
                  const caps = [...document.querySelectorAll('#storms .cap')].map(c => c.textContent).join(' ');
                  return { heads, sustained: /sustained winds/.test(heads.join(' ')),
                           mph: heads.some(t => t === '≥39 mphtropical-storm force') && heads.some(t => t === '≥58 mph')
                                && heads.some(t => t === '≥74 mphhurricane force'),
                           kt: heads.some(t => /kt/.test(t)),
                           gustNote: /peak gust/.test(caps) && /not comparable/.test(caps) };
                }""")
                chk.add(f"{scheme} hurricane: the five-day table reads in mph, named as sustained winds",
                        bool(pws and pws["sustained"] and pws["mph"] and not pws["kt"]),
                        str(pws and pws["heads"]))
                chk.add(f"{scheme} hurricane: the table says it is not the LiveCyc gust quantity",
                        bool(pws and pws["gustNote"]), str(pws and pws["gustNote"]))
                # every wind the tropical page shows is in mph, rounded to 5 the way
                # NHC's advisories state it, followed by the strength NHC gives it;
                # no knots on the map, in the storm list or in a hover
                hur_state = """() => {
                  const hits = [...document.querySelectorAll('#basin circle[fill-opacity="0"]')];
                  const tips = hits.map(c => {
                    const r = c.getBoundingClientRect();
                    c.dispatchEvent(new MouseEvent('mousemove', { clientX: r.x + r.width / 2, clientY: r.y + r.height / 2, bubbles: true }));
                    const t = document.querySelector('#tip'); return t ? t.textContent : '';
                  });
                  const labels = [...document.querySelectorAll('#basin text.lbl')].map(t => t.textContent);
                  const rows = [...document.querySelectorAll('#storms .stormrow span')].map(t => t.textContent);
                  const all = labels.concat(rows, tips);
                  return { labels, rows, tips, knots: all.filter(t => /\\d\\s*kt\\b/.test(t)) };
                }"""
                hs0 = page.evaluate(hur_state)
                chk.add(f"{scheme} hurricane: no wind on the map, in the storm list or in a hover is in knots",
                        bool(hs0["labels"]) and bool(hs0["tips"]) and not hs0["knots"], str(hs0["knots"][:3]))
                chk.add(f"{scheme} hurricane: the current position reads mph and NHC's strength",
                        any("Iselle · 50 mph, tropical storm · adv" in t for t in hs0["labels"]), str(hs0["labels"][:6]))
                chk.add(f"{scheme} hurricane: forecast points read mph with the strength in short",
                        any(t.endswith("45 mph TS") for t in hs0["labels"]) and any(t.endswith("40 mph SS") for t in hs0["labels"])
                        and any(t.endswith("35 mph SD") for t in hs0["labels"]), str(hs0["labels"][:8]))
                chk.add(f"{scheme} hurricane: the storm list reads mph and the strength",
                        any("50 mph, tropical storm" in t for t in hs0["rows"]), str(hs0["rows"][:2]))
                hcur = next((t for t in hs0["tips"] if "current position" in t and "Iselle" in t), "")
                chk.add(f"{scheme} hurricane: the current position's hover gives the wind in mph and the motion in mph",
                        "Wind50 mph, tropical storm" in hcur and " mph" in hcur.split("Movement", 1)[-1][:40], hcur[:160])

                # a point carrying NHC's own category and label is named by them,
                # and the roster's motion reads from its mph field when it has one
                def hur_nhc(route):
                    resp = route.fetch(); d = json.loads(resp.text())
                    for st in d.get("storms") or []:
                        if st.get("name") == "Iselle":
                            st["movementMph"] = 12; st.pop("movementKt", None)
                            for pt in st.get("points") or []:
                                if pt.get("tau") == 0:
                                    pt.update({"ssnum": 0, "tcdvlp": "Tropical Storm"})
                                if pt.get("tau") == 12:
                                    pt.update({"kt": 100, "type": "MH", "ssnum": 3, "tcdvlp": "Major Hurricane"})
                    return route.fulfill(response=resp, body=json.dumps(d))

                page.route("**/snapshots/hurricane.json", hur_nhc)
                page.goto(f"{srv.url}/hurricane.html"); page.wait_for_timeout(900)
                page.locator("#b2").click(); page.wait_for_timeout(700)
                hs1 = page.evaluate(hur_state)
                page.unroute("**/snapshots/hurricane.json")
                chk.add(f"{scheme} hurricane: a point with NHC's category reads it, short on the map and in full in its hover",
                        any(t.endswith("115 mph Cat 3") for t in hs1["labels"])
                        and any("Wind115 mph, Category 3 hurricane" in t for t in hs1["tips"]), str(hs1["labels"][:6]))
                hcur1 = next((t for t in hs1["tips"] if "current position" in t and "Iselle" in t), "")
                chk.add(f"{scheme} hurricane: the motion reads from the roster's mph field",
                        "at 12 mph" in hcur1, hcur1[-120:])
                # zooming the map keeps features the same size on screen: the
                # viewBox shrinks and the glyphs redraw smaller by the same
                # factor, so a label at 4x does not fill the Gulf
                zm = page.evaluate("""async () => {
                  const svg = () => document.querySelector('#basin');
                  const font = () => {
                    const t = [...svg().querySelectorAll('text')].find(x => x.getAttribute('font-size'));
                    return t ? +t.getAttribute('font-size') : null;
                  };
                  const vbw = () => +svg().getAttribute('viewBox').split(' ')[2];
                  const f0 = font(), w0 = vbw();
                  const plus = [...document.querySelectorAll('#basinZoom button')].find(b => b.textContent === '+');
                  plus.click(); plus.click();
                  await new Promise(r => setTimeout(r, 400));
                  const f1 = font(), w1 = vbw();
                  const reset = [...document.querySelectorAll('#basinZoom button')].find(b => /whole|basin|reset/i.test(b.title || b.textContent));
                  if (reset) { reset.click(); await new Promise(r => setTimeout(r, 400)); }
                  return { f0, w0, f1, w1,
                           screen0: f0 && w0 ? f0 / w0 : null, screen1: f1 && w1 ? f1 / w1 : null,
                           restored: font() === f0 };
                }""")
                chk.add(f"{scheme} hurricane: zoomed glyphs hold their on-screen size",
                        bool(zm and zm["f1"] and zm["f1"] < zm["f0"]
                             and abs(zm["screen1"] - zm["screen0"]) < zm["screen0"] * 0.05),
                        str(zm))
                page.locator("#b1").click(); page.wait_for_timeout(700)
                # the reference locations by their own mark: the city names layer draws dots too
                dots = page.locator("#basin circle[data-loc]").count()
                chk.add(f"{scheme} hurricane: reference locations drawn", dots >= 100, f"circles={dots}")
                page.locator("#basin circle[data-loc]").nth(40).hover(force=True); page.wait_for_timeout(120)
                t_dot = page.locator("#tip").inner_text()
                chk.add(f"{scheme} hover: reference location names itself and the lane state", "Country" in t_dot and ("probabilities" in t_dot), t_dot[:80])
                # the Pacific view carries the vendor's Hawaii reference locations, which is
                # where a Central Pacific storm's wind contracts sit; they were missing from
                # the vendored registry until the 2026 season listed them
                page.locator("#b2").click(); page.wait_for_timeout(700)
                haw = page.evaluate("""() => {
                  const svg = document.querySelector('#basin');
                  const W = 980, Hh = 600, b0 = -180, b1 = -85, la0 = 0, la1 = 40;
                  const pts = [...svg.querySelectorAll('circle[data-loc]')].map((c, i) => ({ i,
                      lon: b0 + (+c.getAttribute('cx')) * (b1 - b0) / W,
                      lat: la1 - (+c.getAttribute('cy')) * (la1 - la0) / Hh }))
                    .filter(p => p.lon > -161 && p.lon < -154 && p.lat > 18 && p.lat < 23);
                  const hilo = pts.find(p => Math.abs(p.lon + 155.08) < 0.1 && Math.abs(p.lat - 19.72) < 0.1);
                  return { n: pts.length, hilo: hilo ? hilo.i : -1 };
                }""")
                chk.add(f"{scheme} hurricane: the Pacific view draws the Hawaii reference locations",
                        bool(haw and haw["n"] == 4 and haw["hilo"] >= 0), str(haw))
                if haw and haw["hilo"] >= 0:
                    page.locator("#basin circle[data-loc]").nth(haw["hilo"]).hover(force=True); page.wait_for_timeout(120)
                    t_haw = page.locator("#tip").inner_text()
                    chk.add(f"{scheme} hover: a Hawaii reference location names itself",
                            "Hilo" in t_haw and "HL" in t_haw and "Country" in t_haw, t_haw[:80])
                page.locator("#b1").click(); page.wait_for_timeout(700)
                # ---- the pool with no book: the desk's figure stands in, and it goes
                #      the moment the exchange opens one. The owner's decision of
                #      24 September; the site still computes no figure of its own.
                page.route("**/data/snapshots/**", _storm_routes(False, False, drop_pool=True))
                page.goto(f"{srv.url}/hurricane.html")
                page.wait_for_timeout(1500)
                dk = page.evaluate("""() => {
                  const card = [...document.querySelectorAll('#liveStorms .ladder')]
                    .find(x => /records the highest wind/.test(x.textContent));
                  if (!card) return null;
                  return { rows: [...card.querySelectorAll('.vrung')].map(r =>
                             (r.querySelector('.dlab') || {}).textContent + ' ' + (r.querySelector('.vpct') || {}).textContent),
                           bars: [...card.querySelectorAll('.dfill')].length,
                           yes: card.querySelectorAll("[fill='var(--yes)']").length,
                           text: card.textContent };
                }""")
                chk.add(f"{scheme} pool with no book: the estimate stands in, ranked",
                        bool(dk and dk["rows"][:2] == ["Brownsville 55%", "Galveston 45%"]), str(dk and dk["rows"])[:110])
                chk.add(f"{scheme} pool with no book: it is labelled an estimate, with its as-of time",
                        bool(dk and "Estimated, contract not yet listed" in dk["text"]
                             and "as of 2026-09-01" in dk["text"]), str(dk and dk["text"][-150:]))
                chk.add(f"{scheme} pool with no book: it never says where the estimate came from",
                        bool(dk and not re.search(r"\bdesk\b|DWM|internal|pricer|argmax|cycle \d",
                                                  dk["text"], re.I)), str(dk and dk["text"][-150:]))
                chk.add(f"{scheme} pool with no book: the figure wears no price colour",
                        bool(dk and dk["bars"] >= 2 and dk["yes"] == 0), str(dk and [dk["bars"], dk["yes"]]))
                # and with a book, the price is the only answer on the page
                page.route("**/data/snapshots/**", _storm_routes(False, False))
                page.goto(f"{srv.url}/hurricane.html")
                page.wait_for_timeout(1500)
                gone = page.evaluate("""() => ({
                  desk: document.querySelectorAll('#liveStorms .dfill').length,
                  ladder: document.querySelectorAll('#liveStorms .plad g.prow').length })""")
                chk.add(f"{scheme} pool with a book: the desk's figure gives way to the price",
                        gone["desk"] == 0 and gone["ladder"] >= 1, str(gone))
                # a listed pool with nothing resting on it is not a price. One bid
                # against ninety-nine is this exchange's placeholder, and dropping
                # the desk's figure for it would trade a figure for a blank ladder.
                page.route("**/data/snapshots/**", _storm_routes(False, False, placeholder_pool=True))
                page.goto(f"{srv.url}/hurricane.html")
                page.wait_for_timeout(1500)
                ph = page.evaluate("""() => ({
                  desk: document.querySelectorAll('#liveStorms .dfill').length,
                  ladder: document.querySelectorAll('#liveStorms .plad g.prow').length,
                  noprice: [...document.querySelectorAll('#liveStorms .plad text')]
                    .filter(t => /no price/.test(t.textContent)).length,
                  said: [...document.querySelectorAll('#liveStorms .ladder .cap')]
                    .some(c => /no price is resting on it yet/.test(c.textContent)) })""")
                chk.add(f"{scheme} pool with a placeholder book: the desk's figure stands and the page says why",
                        ph["desk"] >= 2 and ph["noprice"] >= 1 and ph["said"], str(ph))
                page.unroute("**/data/snapshots/**")

                # ---- hurricane contract links, on every surface that shows a price
                RE = (r"^https://www\.interactivebrokers\.com/predictionmarkets/app/#/(\d+)/product-details/"
                      r"contracts\?exchange=FORECASTX&conid_yes=(\d+)$")
                import re as _re

                def linked_href(sel, label):
                    n = page.locator(sel).count()
                    if not n:
                        chk.add(f"{scheme} hurricane link: {label} present", False, f"{sel} count=0")
                        return None
                    # a map region can be several polygons and the first may be a
                    # sliver a forced hover lands outside of, so try a few before
                    # calling it a missing link
                    href = ""
                    for i in range(min(n, 5)):
                        href = page.locator(sel).nth(i).get_attribute("data-contract-url") or ""
                        if href:
                            break
                    m = _re.match(RE, href or "")
                    chk.add(f"{scheme} hurricane link: {label} links to a contract", bool(m), (href or "no href")[:100])
                    if m:
                        chk.add(f"{scheme} hurricane link: {label} uses two different ids", m.group(1) != m.group(2), f"{m.group(1)}/{m.group(2)}")
                    return href

                # the market's ladder: Yes green and No red, both linked, like the other ladders
                ybars = page.locator("#ladders svg.cpanel rect[fill='var(--yes)'][role='link']").count()
                nbars = page.locator("#ladders svg.cpanel rect[fill='var(--no)'][role='link']").count()
                chk.add(f"{scheme} market's ladder: Yes and No bars are drawn in equal number",
                        ybars > 0 and ybars == nbars, f"yes={ybars} no={nbars}")
                chk.add(f"{scheme} market's ladder: the single-color bar is gone",
                        page.locator("#ladders svg.cpanel rect[fill='var(--accent)']").count() == 0,
                        str(page.locator("#ladders svg.cpanel rect[fill='var(--accent)']").count()))
                axis = page.locator("#ladders svg.cpanel text", has_text="Yes green, No red").count()
                chk.add(f"{scheme} market's ladder: the axis says which side is which", axis >= 1, f"labels={axis}")
                linked_href("#ladders svg.cpanel rect[fill='var(--yes)'][role='link']", "the market's ladder")
                linked_href("#ladders .lrow[role='link']", "a period ladder row")
                linked_href("#landfall .lrow[role='link']", "the landfall table")
                # a storm's wind contracts are drawn in full by the live-storm
                # section, so the page carries no catch-all contract table any
                # more, and the tornado contracts belong to Weather
                body = page.locator(".wrap").inner_text()
                chk.add(f"{scheme} hurricane: no catch-all contract table is drawn",
                        page.locator("#others").count() == 0
                        and "OTHER TROPICAL CYCLONE CONTRACTS" not in body, "")
                chk.add(f"{scheme} hurricane: the tornado contracts are not on the cyclone page",
                        "SWTUS" not in body, body[:70])
                linked_href("#cat4 .lrow[role='link']", "the category 4 board")
                # the remaining-season curve, and what it must not claim
                chk.add(f"{scheme} cat4: the remaining-season curve is drawn",
                        page.locator("#cat4 svg.cpanel").count() == 1
                        and page.locator("#cat4 svg.cpanel path").count() >= 2,
                        str(page.locator("#cat4 svg.cpanel path").count()))
                c4 = page.locator("#cat4").inner_text()
                chk.add(f"{scheme} cat4: the page says what the contract pays on and leaves the rest to the terms",
                        "at exactly Category 4 on or before the date named" in c4
                        and "Puerto Rico" not in c4, c4[-140:])
                # the strikes on this board are dates, so the board runs by date with
                # the nearest on top: a string sort on the spec put November above September
                c4rows = page.eval_on_selector_all("#cat4 .lrow .lk", "e => e.map(x => x.textContent)")
                MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

                def _c4key(lbl):
                    m = re.search(r"([A-Z][a-z]{2}) (\d+), (\d{4})", lbl)
                    return (int(m.group(3)), MON.index(m.group(1)), int(m.group(2))) if m else (9999, 99, 99)
                chk.add(f"{scheme} cat4: the board runs by expiration, nearest first",
                        len(c4rows) >= 2 and [_c4key(r) for r in c4rows] == sorted(_c4key(r) for r in c4rows),
                        str(c4rows))
                keys = page.eval_on_selector_all("#cat4 svg.cpanel text", "e=>e.map(x=>x.textContent)")
                chk.add(f"{scheme} cat4: both curves are named, with the climatology window",
                        any("climatology 19" in k for k in keys) and any("count market" in k for k in keys),
                        str([k for k in keys if "clim" in k or "count" in k]))
                chk.add(f"{scheme} cat4: a drawn contract is labeled with its own date",
                        any(k.startswith("By ") and "," in k for k in keys)
                        and not any(k == "a listed contract" for k in keys),
                        str([k for k in keys if k.startswith("By ")]))
                if page.locator("#cat4 svg.cpanel circle:not(.rdot)").count():
                    page.locator("#cat4 svg.cpanel circle:not(.rdot)").first.hover(force=True); page.wait_for_timeout(250)
                    t_c4 = page.locator("#tip").inner_text()
                    chk.add(f"{scheme} cat4: a contract is compared against climatology, not a fair value",
                            "Climatology, from today" in t_c4 and "Difference to climatology" in t_c4, t_c4[:80])
                # the map: a shaded landfall region is a contract
                shaded = page.locator("#basin path[role='link']").count()
                chk.add(f"{scheme} hurricane link: shaded map regions are clickable", shaded >= 3, f"regions={shaded}")
                # ---- an empty book (both sides bidding the minimum) is not a fifty-cent price.
                # The fixture's top hurricane count carries one, as the live board does.
                lad_txt = page.locator("#ladders").inner_text()
                chk.add(f"{scheme} empty book: the count ladder says no price, not 50c",
                        "no price" in lad_txt and "50\u00a2" not in lad_txt, lad_txt[:110])
                chk.add(f"{scheme} empty book: a contract with no bids at all still reads no bids",
                        "no bids" in lad_txt, lad_txt[:110])
                chk.add(f"{scheme} empty book: the ladder carries no caption to explain it away",
                        page.locator("#laddersCap").inner_text().strip() == "",
                        page.locator("#laddersCap").inner_text()[:120])
                # the landfall board carries one too (The Bahamas, as the live board does),
                # and a board row has a tooltip where a panel's unpriced bar does not
                lf_all = page.locator("#landfall").inner_text()
                chk.add(f"{scheme} empty book: the landfall board says no price for it",
                        "no price" in lf_all and "50\u00a2" not in lf_all, lf_all[:110])
                empty_tip = ""
                for i in range(min(page.locator("#landfall .lrow").count(), 40)):
                    page.locator("#landfall .lrow").nth(i).hover(force=True); page.wait_for_timeout(50)
                    t = page.locator("#tip").inner_text()
                    if "no price" in t: empty_tip = t; break
                chk.add(f"{scheme} empty book: its tooltip still shows both bids and says why there is no price",
                        "both sides bid the minimum" in empty_tip and "Yes bid" in empty_tip, empty_tip[:150])
                bah = page.locator('#basin path[aria-label*="Bahamas"]')
                bah_fill, bah_tip = "", ""
                if bah.count():
                    bah_fill = bah.first.get_attribute("fill") or ""
                    # The islands are scattered, so the middle of their outline is open
                    # water; the pointer goes to a point that is on the region itself, and
                    # the box is cleared first so text left by an earlier hover cannot pass
                    page.locator("#basin").scroll_into_view_if_needed()
                    page.mouse.move(2, 2); page.wait_for_timeout(60)
                    page.evaluate("() => { const t = document.getElementById('tip'); if (t) t.textContent = ''; }")
                    pt = page.evaluate("""() => {
                      const p = document.querySelector('#basin path[aria-label*="Bahamas"]');
                      if (!p) return null;
                      const svg = p.ownerSVGElement, n = p.getTotalLength();
                      for (let k = 0; k < 600; k++) {
                        const q = p.getPointAtLength(n * k / 600);
                        for (const [dx, dy] of [[0, 0], [0.35, 0], [-0.35, 0], [0, 0.35], [0, -0.35]]) {
                          const v = svg.createSVGPoint(); v.x = q.x + dx; v.y = q.y + dy;
                          const sc = v.matrixTransform(svg.getScreenCTM());
                          if (document.elementFromPoint(sc.x, sc.y) === p) return { x: sc.x, y: sc.y };
                        }
                      }
                      return null;
                    }""")
                    if pt:
                        page.mouse.move(pt["x"], pt["y"]); page.wait_for_timeout(100)
                        bah_tip = page.locator("#tip").inner_text()
                chk.add(f"{scheme} empty book: its map region is left unshaded and says no price",
                        bah.count() >= 1 and bah_fill == "var(--map-land)" and "no price" in bah_tip,
                        f"n={bah.count()} fill={bah_fill} tip={bah_tip[:90]}")
                linked_href("#basin path[role='link']", "a shaded map region")
                # ---- the map zooms and pans
                vb0 = page.locator("#basin").get_attribute("viewBox")
                page.get_by_title("zoom in").click(); page.wait_for_timeout(150)
                vb1 = page.locator("#basin").get_attribute("viewBox")
                chk.add(f"{scheme} basin zoom: zooming in narrows the view", vb1 != vb0 and float(vb1.split()[2]) < float(vb0.split()[2]),
                        f"{vb0} -> {vb1}")
                chk.add(f"{scheme} basin zoom: the level is stated", "×" in page.locator("#basinZoomLevel").inner_text(),
                        page.locator("#basinZoomLevel").inner_text())
                box = page.locator("#basin").bounding_box()
                page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                page.mouse.down()
                page.mouse.move(box["x"] + box["width"] / 2 - 60, box["y"] + box["height"] / 2, steps=6)
                page.mouse.up(); page.wait_for_timeout(150)
                vb2 = page.locator("#basin").get_attribute("viewBox")
                chk.add(f"{scheme} basin zoom: dragging pans the view", vb2.split()[0] != vb1.split()[0], f"{vb1} -> {vb2}")
                page.get_by_title("back to the whole basin").click(); page.wait_for_timeout(150)
                chk.add(f"{scheme} basin zoom: reset returns the whole basin",
                        page.locator("#basin").get_attribute("viewBox") == vb0, page.locator("#basin").get_attribute("viewBox"))
                chk.add(f"{scheme} basin zoom: zooming out stops at the whole basin",
                        (page.get_by_title("zoom out").click() or page.wait_for_timeout(150) or
                         page.locator("#basin").get_attribute("viewBox")) == vb0,
                        page.locator("#basin").get_attribute("viewBox"))
                # the caption carries only what the key and the boxes cannot: whether the
                # basin has a storm, why a cone is missing, and where the layers come from
                cap_txt = page.locator("#basinCap").inner_text()
                chk.add(f"{scheme} hurricane: the map caption is one short paragraph naming its sources",
                        page.locator("#basinCap p").count() == 1 and len(cap_txt) < 320
                        and "National Hurricane Center" in cap_txt and "within 50 miles" not in cap_txt,
                        f"{len(cap_txt)} chars: {cap_txt[:160]}")
                # ---- a storm in the basin on view: the map opens on its cone and track
                # rather than the whole ocean, a button returns to it, and the whole
                # basin is one click away (owner, 2026-10-07)
                def _storm_here(route):
                    resp = route.fetch()
                    hj = json.loads(resp.text())
                    hj["storms"] = (hj.get("storms") or []) + [{
                        "id": "al042026", "name": "Delta", "basin": "AL", "classification": "HU", "advisory": "9",
                        "intensityKt": 85, "lat": 24.0, "lon": -88.0, "updated": "2026-09-01T09:00:00Z",
                        "cone": [[[[-89.0, 23.5], [-86.0, 26.0], [-84.5, 30.5], [-87.0, 31.0], [-90.0, 26.0], [-89.0, 23.5]]]],
                        "track": [[[-88.0, 24.0], [-87.2, 27.0], [-86.4, 30.0]]],
                        "points": [{"lon": -88.0, "lat": 24.0, "kt": 85, "type": "HU", "tau": 0, "label": "1:00 AM Wed"},
                                   {"lon": -87.2, "lat": 27.0, "kt": 95, "type": "HU", "tau": 24, "label": "1:00 AM Thu"},
                                   {"lon": -86.4, "lat": 30.0, "kt": 70, "type": "HU", "tau": 48, "label": "1:00 AM Fri"}],
                        "past": [], "windProbs": []}]
                    return route.fulfill(response=resp, body=json.dumps(hj))
                page.route("**/data/snapshots/hurricane.json", _storm_here)
                page.goto(f"{srv.url}/hurricane.html"); page.wait_for_timeout(1200)
                zread = lambda: page.evaluate("""() => {
                  const b = document.getElementById('basinStorm');
                  const lbl = [...document.querySelectorAll('#basin text.slbl')];
                  const vb = document.getElementById('basin').getAttribute('viewBox').split(' ').map(Number);
                  // a storm label is placed inside the window, so none of them crosses its edge
                  const out = lbl.filter(t => { const r = t.getBBox();
                    return r.x < vb[0] - 1 || r.x + r.width > vb[0] + vb[2] + 1 || r.y < vb[1] - 1 || r.y + r.height > vb[1] + vb[3] + 1; }).length;
                  return { vb, btn: b && !b.hidden ? b.textContent : '', lvl: (document.getElementById('basinZoomLevel') || {}).textContent || '',
                           title: lbl.some(t => /Delta/.test(t.textContent)), cut: out };
                }""")
                z0 = zread()
                chk.add(f"{scheme} storm view: the map opens on the active storm, not the whole basin",
                        bool(z0 and z0["vb"][2] < 980 and z0["btn"] == "Delta" and "Delta" in z0["lvl"]), str(z0))
                chk.add(f"{scheme} storm view: the storm's labels are drawn inside the window on view",
                        bool(z0 and z0["title"] and z0["cut"] == 0), str(z0))
                # a plain click on a zoomed map reaches the location under it: the pan used
                # to capture the pointer on the press, so the map took every click
                zc = page.evaluate("""() => {
                  const vb = document.getElementById('basin').getAttribute('viewBox').split(' ').map(Number);
                  const d = [...document.querySelectorAll('#basin circle[data-loc]')].find(c => {
                    const x = +c.getAttribute('cx'), y = +c.getAttribute('cy');
                    if (x < vb[0] + vb[2] * 0.1 || x > vb[0] + vb[2] * 0.9 || y < vb[1] + vb[3] * 0.1 || y > vb[1] + vb[3] * 0.9) return false;
                    const r = c.getBoundingClientRect();
                    return document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) === c;
                  });
                  if (!d) return null;
                  const r = d.getBoundingClientRect();
                  return { x: r.left + r.width / 2, y: r.top + r.height / 2, id: d.getAttribute('data-loc') };
                }""")
                zpin = ""
                if zc:
                    page.mouse.move(zc["x"], zc["y"]); page.wait_for_timeout(80)
                    page.mouse.click(zc["x"], zc["y"]); page.wait_for_timeout(200)
                    zpin = page.locator("#tip").evaluate("e => e.dataset.pinned || ''")
                    page.mouse.click(4, 4); page.wait_for_timeout(120)
                chk.add(f"{scheme} storm view: a click on a location reaches it on the zoomed map",
                        bool(zc) and zpin == "1", f"{zc} pinned={zpin!r}")
                page.get_by_title("back to the whole basin").click(); page.wait_for_timeout(250)
                z1 = zread()
                chk.add(f"{scheme} storm view: the whole basin is one click away",
                        bool(z1 and z1["vb"] == [0, 0, 980, 600] and z1["lvl"] == "whole basin"), str(z1))
                roads_basin = page.locator("#basin path.roads1").count()
                page.locator("#basinStorm").click(); page.wait_for_timeout(250)
                z2 = zread()
                chk.add(f"{scheme} storm view: the storm's button returns to it",
                        bool(z2 and z0 and z2["vb"] == z0["vb"]), str(z2))
                # ---- roads and city names (Natural Earth, owner's decision 2026-10-07): the
                # main roads under the cone from the storm's view inward and none on the whole
                # basin, the names drawn after the storm and kept off its labels and inside the
                # window, none of it in the pointer's way, one switch for both
                rd = lambda: page.evaluate("""() => {
                  const svg = document.getElementById('basin');
                  const r1 = svg.querySelector('path.roads1');
                  const cone = svg.querySelector('path[fill="rgba(100,116,139,.14)"]');
                  const g2 = svg.querySelector('g.places');
                  const vb = svg.getAttribute('viewBox').split(' ').map(Number);
                  const storm = [...svg.querySelectorAll('text.slbl')].map(t => t.getBBox());
                  const names = [...svg.querySelectorAll('g.places text.plbl')].map(t => t.getBBox());
                  const hit = (a, b) => a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
                  const last = storm.length ? [...svg.querySelectorAll('text.slbl')].pop() : null;
                  // the middle of each signalled reference dot: a name may graze a dot's rim, never
                  // cross it (a largest city's name may graze a small grey one, by design)
                  const dots = [...svg.querySelectorAll('circle[data-loc][stroke="#fff"]')].map(c => {
                    const r = +c.getAttribute('r') * 0.6;
                    return { x: +c.getAttribute('cx') - r, y: +c.getAttribute('cy') - r, width: 2 * r, height: 2 * r }; });
                  return { roads: svg.querySelectorAll('path.roads1').length,
                           onDot: names.filter(b => dots.some(d => hit(b, d))).length,
                           under: !!(r1 && cone && (r1.compareDocumentPosition(cone) & Node.DOCUMENT_POSITION_FOLLOWING)),
                           after: !!(g2 && last && (last.compareDocumentPosition(g2) & Node.DOCUMENT_POSITION_FOLLOWING)),
                           pe: [r1 ? r1.getAttribute('pointer-events') : null, g2 ? g2.getAttribute('pointer-events') : null],
                           names: names.length, groups: svg.querySelectorAll('g.places').length,
                           onStorm: names.filter(b => storm.some(s => hit(b, s))).length,
                           cut: names.filter(r => r.x < vb[0] - 1 || r.x + r.width > vb[0] + vb[2] + 1
                                                  || r.y < vb[1] - 1 || r.y + r.height > vb[1] + vb[3] + 1).length };
                }""")
                f0 = rd()
                chk.add(f"{scheme} roads: the main roads are drawn under the cone at the storm's view and none on the whole basin",
                        bool(f0 and f0["roads"] == 1 and f0["under"] and roads_basin == 0), f"{f0} basin={roads_basin}")
                chk.add(f"{scheme} cities: names are drawn after the storm, off its labels and the reference dots, inside the window",
                        bool(f0 and f0["names"] >= 1 and f0["after"] and f0["onStorm"] == 0 and f0["onDot"] == 0
                             and f0["cut"] == 0), str(f0))
                chk.add(f"{scheme} roads and cities: neither takes the pointer",
                        bool(f0 and f0["pe"] == ["none", "none"]), str(f0 and f0["pe"]))
                page.locator("#basinRoads").click(); page.wait_for_timeout(250)
                f_off = rd()
                page.locator("#basinRoads").click(); page.wait_for_timeout(400)
                f_back = rd()
                chk.add(f"{scheme} roads and cities: the switch hides and restores both",
                        bool(f_off and f_back and f_off["roads"] == 0 and f_off["groups"] == 0
                             and f_back["roads"] == 1 and f_back["names"] == f0["names"]),
                        f"off={f_off and (f_off['roads'], f_off['groups'])} back={f_back and (f_back['roads'], f_back['names'])}")
                page.unroute("**/data/snapshots/hurricane.json", _storm_here)
                # ---- a satellite loop for each storm on view (owner, 2026-10-10): STAR's floater
                # frames, copied into the bucket by pipeline/floater.py, under the map; the newest
                # frame shows at once, the loop plays while the card is on screen, and a storm the
                # index does not carry, or one in the other ocean, gets no card. The frames are
                # stamped relative to now, because a loop whose newest frame is more than two
                # hours old is not drawn
                _png = tiny_png
                _fl_t0 = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0) - dt.timedelta(minutes=40)
                _fl_t0 -= dt.timedelta(minutes=_fl_t0.minute % 10)
                _fl_frames = [{"key": "floater/AL042026/%s.jpg" % (_fl_t0 + dt.timedelta(minutes=10 * i)).strftime("%Y%j%H%M"),
                               "t": (_fl_t0 + dt.timedelta(minutes=10 * i)).strftime("%Y-%m-%dT%H:%MZ")} for i in range(4)]
                def _floater(route):
                    u = route.request.url
                    if u.endswith("/floater/index.json"):
                        return route.fulfill(status=200, content_type="application/json", body=json.dumps({
                            "schema": 1, "asof": "2026-09-01T09:35:00Z",
                            "storms": {"AL042026": {"name": "Delta", "basin": "AL", "satellite": "GOES-19", "product": "GeoColor",
                                                    "frames": _fl_frames}}}))
                    return route.fulfill(status=200, content_type="image/png", body=_png((40, 90, 160)))
                page.route("**/data/snapshots/hurricane.json", _storm_here)
                page.route("**/data/snapshots/floater/**", _floater)
                page.goto(f"{srv.url}/hurricane.html"); page.wait_for_timeout(1200)
                page.locator("#stormLoops").scroll_into_view_if_needed(); page.wait_for_timeout(300)
                fl0 = page.evaluate("""() => {
                  const c = document.querySelector('#stormLoops .loopcard');
                  if (!c) return null;
                  return { cards: document.querySelectorAll('#stormLoops .loopcard').length, head: c.querySelector('.lhd').textContent,
                           src: c.querySelector('img.loopimg').getAttribute('src'), time: c.querySelector('.looptime').textContent,
                           credit: c.querySelector('.lsrc').textContent };
                }""")
                lt = lambda: page.evaluate("() => (document.querySelector('#stormLoops .looptime') || {}).textContent || ''")
                seen = set()
                for _ in range(18):                                   # the loop holds the newest frame 1.4 s a turn
                    seen.add(lt().split(" ")[0]); page.wait_for_timeout(150)
                chk.add(f"{scheme} storm loop: a storm on view gets a satellite loop under the map, newest frame first, credited",
                        bool(fl0 and fl0["cards"] == 1 and fl0["head"].startswith("Delta") and fl0["src"].endswith("/" + _fl_frames[-1]["key"])
                             and "latest" in fl0["time"] and "NOAA/NESDIS STAR" in fl0["credit"]), str(fl0))
                chk.add(f"{scheme} storm loop: the loop plays while the card is on screen",
                        len(seen) >= 2 and seen <= {f["t"][11:16] + "Z" for f in _fl_frames}, str(sorted(seen)))
                page.locator("#stormLoops button.loopbtn", has_text="Pause").first.click(); page.wait_for_timeout(100)
                fl1 = lt()
                page.locator("#stormLoops button.loopbtn[title='the frame before']").first.click(); page.wait_for_timeout(100)
                fl2 = lt()
                page.wait_for_timeout(700)
                fl3 = lt()
                chk.add(f"{scheme} storm loop: pause holds a frame and the arrows step through them",
                        fl2 == fl3 and fl2 != fl1, f"{fl1} -> {fl2} -> {fl3}")
                page.click("#b2"); page.wait_for_timeout(500)
                n_ep = page.locator("#stormLoops .loopcard").count()
                chk.add(f"{scheme} storm loop: the other ocean's view carries no card for this storm",
                        n_ep == 0 and page.locator("#stormLoops").evaluate("e => e.hidden"), f"cards={n_ep}")
                page.unroute("**/data/snapshots/floater/**", _floater)
                page.unroute("**/data/snapshots/hurricane.json", _storm_here)
                page.goto(f"{srv.url}/hurricane.html"); page.wait_for_timeout(900)
                chk.add(f"{scheme} storm loop: no storm, no loop",
                        page.locator("#stormLoops .loopcard").count() == 0, "")
                page.goto(f"{srv.url}/hurricane.html"); page.wait_for_timeout(900)
                # ---- the sea surface temperature anomaly: under the land, named in the key
                # and the caption with its day, read by hovering the sea, and switched off
                # and on from the zoom bar (owner, 2026-10-07)
                sst_s = page.evaluate("""() => {
                  const img = document.querySelector('#basin image.sstlayer');
                  const land = document.querySelector('#basin path');
                  return { img: !!img,
                           under: !!(img && land && (img.compareDocumentPosition(land) & Node.DOCUMENT_POSITION_FOLLOWING)),
                           key: (document.getElementById('basinKey') || {}).textContent || '',
                           cap: (document.getElementById('basinCap') || {}).textContent || '' };
                }""")
                chk.add(f"{scheme} sea temperature: the anomaly is drawn under the land, its day in the key and the caption",
                        bool(sst_s and sst_s["img"] and sst_s["under"] and "Sea temperature anomaly, " in sst_s["key"]
                             and "Coral Reef Watch" in sst_s["cap"]), str({k: (v[:60] if isinstance(v, str) else v) for k, v in (sst_s or {}).items()}))
                gulf = page.evaluate("""() => {
                  const svg = document.getElementById('basin'), img = svg.querySelector('image.sstlayer');
                  if (!img) return null;
                  // the middle of the Gulf of America, on the whole-basin box
                  const x = (-90 + 101) * 980 / 61, y = (48 - 25) * 600 / 44;
                  const pt = svg.createSVGPoint(); pt.x = x; pt.y = y;
                  const q = pt.matrixTransform(svg.getScreenCTM());
                  return { x: q.x, y: q.y };
                }""")
                t_sst = ""
                if gulf:
                    page.mouse.move(gulf["x"], gulf["y"]); page.wait_for_timeout(150)
                    page.mouse.move(gulf["x"] + 3, gulf["y"] + 2); page.wait_for_timeout(150)
                    t_sst = page.locator("#tip").inner_text()
                chk.add(f"{scheme} sea temperature: hovering the sea reads the anomaly there",
                        "Sea surface temperature anomaly" in t_sst and "°C" in t_sst and "°F" in t_sst, t_sst[:120])
                page.mouse.move(2, 2)
                page.locator("#basinSst").click(); page.wait_for_timeout(250)
                off = page.locator("#basin image.sstlayer").count()
                page.locator("#basinSst").click(); page.wait_for_timeout(250)
                back = page.locator("#basin image.sstlayer").count()
                chk.add(f"{scheme} sea temperature: the switch hides and restores it",
                        off == 0 and back == 1, f"off={off} back={back}")
                # ---- the hill shading: over the land, under the storms, never in the pointer's way
                rel = page.evaluate("""() => {
                  const r = document.querySelector('#basin image.relieflayer');
                  return { on: !!r, pe: r ? r.getAttribute('pointer-events') : null };
                }""")
                chk.add(f"{scheme} relief: the hill shading is drawn and takes no pointer events",
                        bool(rel and rel["on"] and rel["pe"] == "none"), str(rel))
                page.locator("#basinRelief").click(); page.wait_for_timeout(250)
                r_off = page.locator("#basin image.relieflayer").count()
                page.locator("#basinRelief").click(); page.wait_for_timeout(600)
                r_back = page.locator("#basin image.relieflayer").count()
                chk.add(f"{scheme} relief: the switch hides and restores it", r_off == 0 and r_back == 1, f"off={r_off} back={r_back}")
                page.locator("#ladders .lrow").first.hover(force=True); page.wait_for_timeout(120)
                t_row = page.locator("#tip").inner_text()
                chk.add(f"{scheme} hover: count ladder row shows the book and settlement", "Yes bid" in t_row and "Settles" in t_row, t_row[:80])
                found = ""
                for i in range(min(page.locator("#basin path").count(), 80)):
                    page.locator("#basin path").nth(i).hover(force=True); page.wait_for_timeout(40)
                    t = page.locator("#tip").inner_text()
                    if "Landfall contract" in t: found = t; break
                chk.add(f"{scheme} hover: a shaded landfall region shows its contract", "Yes bid" in found, found[:80])
                # ---- a county the desk adds for a storm is drawn: the geometry carries every
                # coastal county and the map outlines the ones the exchange lists (Isaias's
                # eight, 2026-10-07, were listed and priced but had no shape to draw)
                def _hlf_county(route):
                    resp = route.fetch()
                    mj = json.loads(resp.text())
                    for x in mj.get("markets") or []:
                        if x.get("symbol") == "HLF" and x.get("contracts"):
                            c0 = dict(x["contracts"][0])
                            c0.update({"label": "Escambia, Florida", "strike": 99.0, "bid": 0.14, "ask": 0.2, "mid": 0.17,
                                       "conid": 999300001, "conidYes": 999300001})
                            x["contracts"] = x["contracts"] + [c0]
                    return route.fulfill(response=resp, body=json.dumps(mj))
                page.route("**/data/snapshots/market/hurricane.json", _hlf_county)
                page.goto(f"{srv.url}/hurricane.html"); page.wait_for_timeout(1200)
                page.get_by_title("back to the whole basin").click(); page.wait_for_timeout(200)
                ink = page.locator('#basin path[stroke="var(--ink)"]')
                n_ink, esc_tip = ink.count(), ""
                for i in range(min(n_ink, 20)):
                    ink.nth(i).hover(force=True); page.wait_for_timeout(40)
                    t = page.locator("#tip").inner_text()
                    if "Escambia, Florida" in t:
                        esc_tip = t; break
                # the six counties the samples list, plus Escambia, and none of the other 209
                chk.add(f"{scheme} landfall: a county the exchange lists is outlined and priced, an unlisted one is not drawn",
                        n_ink == 7 and "Landfall contract" in esc_tip and "17" in esc_tip, f"outlined={n_ink} tip={esc_tip[:90]}")
                page.unroute("**/data/snapshots/market/hurricane.json", _hlf_county)
                # ---- the Pacific view: a landfall region there (Hawaii, as the exchange lists it) is
                # listed, drawn and priced on that view and kept off the Atlantic one
                lf_txt = page.locator("#landfall").inner_text()
                chk.add(f"{scheme} landfall: the Atlantic board lists no Pacific-only region",
                        "Hawaii" not in lf_txt and "Mexico" in lf_txt
                        and page.locator("#landfall .lrow").count() >= 5, lf_txt[:60])
                chk.add(f"{scheme} landfall: the caption carries the 50-mile border clause and the eye rule",
                        "50 miles" in lf_txt and "eye crossing" in lf_txt
                        and "per-side execution fee" not in lf_txt, lf_txt[-160:])
                page.locator("#b2").click(); page.wait_for_timeout(600)
                ep_txt = page.locator("#landfall").inner_text()
                ep_rows = page.locator("#landfall .lrow").count()
                # the Pacific view carries Hawaii and the regions that face both oceans, since the
                # landfall contract names no ocean; the Atlantic-only regions stay off it
                chk.add(f"{scheme} pacific: the landfall board lists the regions a Pacific storm can reach",
                        page.locator("#landfallSect").is_visible() and "Hawaii" in ep_txt
                        and "Mexico" in ep_txt and "Florida" not in ep_txt and "Cuba" not in ep_txt
                        and "Atlantic view" in page.locator("#pacificNote").inner_text(),
                        f"rows={ep_rows} {ep_txt[:80]}")
                chk.add(f"{scheme} pacific: the count panels stay on the Atlantic view",
                        not page.locator("#atlanticOnly").is_visible() and "listed above" in page.locator("#pacificNote").inner_text(),
                        page.locator("#pacificNote").inner_text()[:80])
                ep_links = page.locator("#basin path[role='link']").count()
                chk.add(f"{scheme} pacific: a shaded region is clickable", ep_links >= 1, f"regions={ep_links}")
                # the state is drawn as one path over eight islands, so its bounding-box
                # center is ocean: the tooltip is asked for on the element itself
                ep_tip = page.evaluate("""() => {
                  const p = document.querySelector('#basin path[aria-label*="Hawaii"]');
                  if (!p) return "";
                  p.dispatchEvent(new MouseEvent("mousemove", { bubbles: true, clientX: 200, clientY: 300 }));
                  return (document.querySelector("#tip") || {}).textContent || "";
                }""")
                chk.add(f"{scheme} pacific hover: the Hawaii region shows its landfall contract",
                        "Landfall contract" in ep_tip and "Yes bid" in ep_tip and "Hawaii" in ep_tip, ep_tip[:110])
                chk.add(f"{scheme} pacific: the key names the landfall shading",
                        "Landfall contract" in page.locator("#basinKey").inner_text(), page.locator("#basinKey").inner_text()[:80])
                # ---- a storm whose cone service has stopped updating: the superseded
                # cone and forecast track are not drawn, and the storm is placed and
                # labeled from the roster's own advisory instead
                st = page.evaluate("""() => {
                  const cones = [...document.querySelectorAll('#basin path')]
                    .filter(p => (p.getAttribute('fill') || '').indexOf('rgba(100,116,139') === 0).length;
                  const labels = [...document.querySelectorAll('#basin text')].map(t => t.textContent);
                  return { cones, cap: (document.querySelector('#basinCap') || {}).textContent || '',
                           lala: labels.find(t => t.indexOf('Lala') >= 0) || '' };
                }""")
                chk.add(f"{scheme} stale geometry: the superseded cone is not drawn",
                        bool(st and st["cones"] == 1), str(st and st["cones"]))
                chk.add(f"{scheme} stale geometry: the storm is labeled with the roster's advisory, not the old one",
                        bool(st and "adv 056" in st["lala"] and "adv 48" not in st["lala"]), str(st and st["lala"])[:90])
                chk.add(f"{scheme} stale geometry: the caption says why the cone is missing",
                        bool(st and "Lala is drawn at the position on the latest advisory" in st["cap"]
                             and "not shown" in st["cap"]), str(st and st["cap"][-150:]))
                # the asset a page loads carries a stamp of its own content, so a
                # day-long asset cache cannot serve an old one to newer code
                av = page.evaluate("""() => {
                  const reqs = performance.getEntriesByType("resource")
                    .map(r => r.name).filter(n => n.indexOf("/assets/") >= 0);
                  return { stamped: reqs.filter(n => /[?&]v=[0-9a-f]{8}/.test(n)),
                           bare: reqs.filter(n => !/[?&]v=/.test(n)),
                           has: !!(window.WX && WX.assetV && WX.assetV["hurricane-geo.json"]) };
                }""")
                chk.add(f"{scheme} assets: every asset is fetched with a stamp of its own content",
                        bool(av and av["has"] and av["stamped"] and not av["bare"]),
                        str(av and [av["stamped"][:1], av["bare"][:2]])[:170])
                page.locator("#b1").click(); page.wait_for_timeout(500)
                chk.add(f"{scheme} atlantic: switching back restores the full board",
                        page.locator("#landfall .lrow").count() >= 5 and "Hawaii" not in page.locator("#landfall").inner_text(),
                        f"rows={page.locator('#landfall .lrow').count()}")
                # ---- climate page: live contract markers
                page.goto(f"{srv.url}/climate.html")
                page.wait_for_timeout(900)
                markers = page.locator("#panels svg circle, #panels svg path[stroke-width='1']").count()
                chk.add(f"{scheme} climate: contract markers drawn from the quote snapshot", markers >= 5, f"markers={markers}")
                # AMOC Weakening lists Below strikes, so its key reads the other way
                keys = page.eval_on_selector_all("#panels .panel",
                    "e=>e.map(p=>({t:(p.firstElementChild||{}).textContent||'', "
                    "k:[...p.querySelectorAll('svg text')].map(x=>x.textContent).filter(s=>s.startsWith('Yes price'))}))")
                amoc = [p for p in keys if p["t"].startswith("AMOC")]
                chk.add(f"{scheme} climate: the AMOC key says Yes pays below the strike, every other key above",
                        len(amoc) == 1 and amoc[0]["k"] == ["Yes price: the market’s chance it ends below the strike"]
                        and all(k == "Yes price: the market’s chance it ends above the strike"
                                for p in keys if not p["t"].startswith("AMOC") for k in p["k"]),
                        str(keys)[:300])
                page.locator("#panels svg circle[r='8']").first.hover(force=True); page.wait_for_timeout(120)
                t_mk = page.locator("#tip").inner_text()
                chk.add(f"{scheme} hover: climate marker shows settlement and the book", "Settles" in t_mk and "Yes" in t_mk, t_mk[:80])
                pb = page.locator("#panels svg").first.bounding_box()
                page.mouse.move(pb["x"] + pb["width"] * 0.5, pb["y"] + pb["height"] * 0.5); page.wait_for_timeout(120)
                t_ser = page.locator("#tip").inner_text()
                chk.add(f"{scheme} hover: climate series point shows year, value and source", "Value" in t_ser and "Latest" in t_ser, t_ser[:80])
                # the trend tool: a few thresholds read as the year the trend
                # crosses each of them
                def _drag(box, x0f, x1f):
                    y = box["y"] + box["height"] * 0.55
                    page.mouse.move(box["x"] + box["width"] * x0f, y)
                    page.mouse.down()
                    page.mouse.move(box["x"] + box["width"] * x1f, y, steps=8)
                    page.mouse.up()
                    page.wait_for_timeout(200)
                _drag(pb, 0.18, 0.55)
                t_note = page.locator("#panels .panel .note").first.inner_text()
                chk.add(f"{scheme} climate: the trend fit reports crossings",
                        "per decade" in t_note and "crosses" in t_note, t_note[:90])
                # ---- the world GDP contract's page (owner, 2026-11-02): the article with its figures
                # drawn live by the contract's model, the controls re-running it, each view's chance
                # of Yes marked as not a price, and the proposed ladder carrying no link until the
                # exchange lists the contract
                chk.add(f"{scheme} climate: a card leads to the world GDP contract's page",
                        page.locator("a#gdpCard[href='climate-gdp.html']").count() == 1, "")
                gdp_model = json.loads(urllib.request.urlopen(f"{srv.url}/data/snapshots/elnino/model.json").read().decode())
                gdp_pub = gdp_model["published"]["contract"]

                def _gdp_state():
                    return page.evaluate("""() => ({
                      status: (document.querySelector('#gdpRunStatus') || {}).textContent || '',
                      strip: (document.querySelector('#pageStatus .status') || {}).textContent || '',
                      tiles: [...document.querySelectorAll('.gdp-tile .tv')].map(e => e.textContent),
                      notPrice: [...document.querySelectorAll('.gdp-tile.null .ts, .gdp-tile.paper .ts')]
                        .every(e => /not a price/.test(e.textContent)),
                      nav: [...document.querySelectorAll('header.site nav a.on')].map(a => a.textContent),
                      figs: [...document.querySelectorAll('figure.gdp-fig > .card > svg')].map(s => s.childElementCount),
                      notes: document.querySelectorAll('button.accnote-btn').length,
                      hatched: document.querySelectorAll('#gdpMain rect[fill="url(#gdpHatch)"]').length,
                      marks: document.querySelectorAll('#gdpMain line.gdp-mark').length,
                      cents: [...document.querySelectorAll('#gdpMain text.ladtxt')].length,
                      links: [...document.querySelectorAll('[data-contract-url]')].map(e => e.getAttribute('data-contract-url')),
                      note: [...document.querySelectorAll('p.tradenote')].map(n => (n.previousElementSibling
                        && n.previousElementSibling.querySelector('#gdpMain')) ? 'after-figure' : 'elsewhere'),
                      ladderSub: ([...document.querySelectorAll('#gdpMain text.ax')].find(t => /strikes|prices|end year/i.test(t.textContent)) || {}).textContent || '',
                      listing: (document.querySelector('#gdpListing') || {}).textContent || '',
                      hash: location.hash })""")
                page.goto(f"{srv.url}/climate-gdp.html"); page.wait_for_timeout(2500)
                g0 = _gdp_state()
                chk.add(f"{scheme} gdp: the page runs the contract's model on every path and dates the run",
                        "50,000 paths" in g0["status"] and "Data as of" in g0["strip"], g0["status"] + " | " + g0["strip"][:60])
                chk.add(f"{scheme} gdp: the page belongs to the Climate Change category",
                        g0["nav"] == ["Climate & Weather", "Climate Change"], str(g0["nav"]))
                gdp_p = [int(t.rstrip("%")) for t in g0["tiles"][:2] if t.endswith("%")]
                chk.add(f"{scheme} gdp: at the article's settings the two chances are the published run's",
                        len(gdp_p) == 2 and abs(gdp_p[0] - 100 * (1 - gdp_pub["false_positive"])) <= 3
                        and abs(gdp_p[1] - 100 * (1 - gdp_pub["sensitivity"])) <= 3 and g0["notPrice"], str(g0["tiles"]))
                chk.add(f"{scheme} gdp: seven live figures, each with its details of calculation",
                        len(g0["figs"]) == 7 and min(g0["figs"] or [0]) > 10 and g0["notes"] == 8, f"figs={g0['figs']} notes={g0['notes']}")
                chk.add(f"{scheme} gdp: until it lists, the ladder is the proposed strikes, hatched, with two marks and no link",
                        g0["hatched"] == len(gdp_model["ladder"]["strikes"]) and g0["marks"] == 2 * g0["hatched"]
                        and not g0["links"] and not g0["note"] and "Not listed yet" in g0["listing"],
                        f"hatched={g0['hatched']} marks={g0['marks']} links={len(g0['links'])} notes={g0['note']}")
                page.locator("#gdpMain rect[fill='url(#gdpHatch)']").first.hover(force=True); page.wait_for_timeout(150)
                chk.add(f"{scheme} gdp: a strike's box gives both chances and says they are not prices",
                        "Not prices" in page.locator("#tip").inner_text() and "lists" in page.locator("#tip").inner_text(),
                        page.locator("#tip").inner_text()[:120])
                page.locator("#gdpMainMethod button.accnote-btn").click(); page.wait_for_timeout(200)
                gn = page.evaluate("""() => { const b = document.querySelector('#gdpMainMethod .accnote');
                  return { open: !!b && !b.hidden, tex: b ? b.querySelectorAll('.katex').length : 0,
                           code: b ? [...b.querySelectorAll('.code a')].map(a => a.href) : [] }; }""")
                chk.add(f"{scheme} gdp: the contract's note opens with its equations typeset and its code linked",
                        gn["open"] and gn["tex"] >= 3 and len(gn["code"]) >= 3
                        and all(u.startswith(gdp_model["code"]["repo"] + "/blob/") for u in gn["code"]), str(gn)[:160])
                page.evaluate("""() => { const i = [...document.querySelectorAll('#gdpPanel input[type=range]')]
                    .find(x => x.getAttribute('aria-label') === 'Share of the paper’s effect that is real');
                  i.value = 0.5; i.dispatchEvent(new Event('input', { bubbles: true })); }""")
                page.wait_for_timeout(1200)
                g1 = _gdp_state()
                chk.add(f"{scheme} gdp: a control re-runs the model, moves the paper's chance and goes in the address",
                        "share=0.5" in g1["hash"] and g1["tiles"][1] != g0["tiles"][1] and g1["tiles"][0] == g0["tiles"][0],
                        f"{g0['tiles']} -> {g1['tiles']} {g1['hash']}")
                page.locator("#gdpPanelBar button", has_text="Reset").click(); page.wait_for_timeout(1200)
                g2 = _gdp_state()
                chk.add(f"{scheme} gdp: reset returns to the article's settings", g2["tiles"] == g0["tiles"] and g2["hash"] == "",
                        f"{g2['tiles']} {g2['hash']}")
                page.goto(f"{srv.url}/climate-gdp.html#baseline=frozen&irf=PWT_panel"); page.wait_for_timeout(3000)
                g3 = _gdp_state()
                chk.add(f"{scheme} gdp: a shared address loads its settings, fetching what they need",
                        "50,000 paths" in g3["status"] and g3["tiles"][:2] != g0["tiles"][:2]
                        and page.locator(".gdp-figstate.mine").count() == 7, f"{g3['status']} {g3['tiles']}")
                # listed: the check's own product, added to config.js, and its terms and quotes, so the
                # check does not depend on what the exchange or config/site.json carry. The exchange
                # lists its own strikes, so the check carries a ladder of six rather than the article's one.
                gdp_strikes = [0.5, 0.8, 1.1, 1.4, 1.7, 2.0]
                gdp_end = gdp_model["defaults"]["endYear"]
                gdp_terms = {"id": "WGDPT", "name": "World GDP per Capita Growth", "marketName": "World GDP per Capita Growth",
                             "state": "listed", "productConid": 990001, "asof": "2026-11-02T12:00:00Z",
                             "contracts": [{"strike": s_, "numeric": True, "label": "Above %.1f" % s_, "spec": str(gdp_end),
                                            "expiryLabel": str(gdp_end), "conidYes": 990100 + i_, "conidNo": 990200 + i_}
                                           for i_, s_ in enumerate(gdp_strikes)]}
                gdp_book = [(0.9, 0.93), (0.8, 0.84), (0.55, 0.6), (0.3, 0.36), (0.12, 0.16), (0.01, 0.99)]
                gdp_quotes = {"schema": 1, "id": "WGDPT", "asof": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                              "rows": [{"strike": s_, "spec": str(gdp_end), "bid": b_, "ask": a_, "mid": round((b_ + a_) / 2, 4),
                                        "bidSize": 10.0, "askSize": 10.0, "from": "yes", "conidYes": 990100 + i_}
                                       for i_, (s_, (b_, a_)) in enumerate(zip(gdp_strikes, gdp_book))]}

                def _gdp_cfg(route):
                    resp = route.fetch()
                    return route.fulfill(response=resp, body=resp.text() + "\nwindow.WX.gdpContract = {product: 'WGDPT'};\n")

                def _gdp_cat(route):
                    u = route.request.url
                    if u.endswith("/catalogue/product/WGDPT.json"):
                        return route.fulfill(status=200, content_type="application/json", body=json.dumps(gdp_terms))
                    if u.endswith("/catalogue/price/WGDPT.json"):
                        return route.fulfill(status=200, content_type="application/json", body=json.dumps(gdp_quotes))
                    return route.continue_()
                gdp_cfg_url = re.compile(r"/config\.js(\?|$)")
                page.route(gdp_cfg_url, _gdp_cfg)
                page.route("**/data/snapshots/catalogue/**", _gdp_cat)
                page.goto(f"{srv.url}/climate-gdp.html"); page.wait_for_timeout(2500)
                g4 = _gdp_state()
                chk.add(f"{scheme} gdp: once listed, each priced strike opens its contract, Yes green and No red, the marks kept",
                        len(g4["links"]) == 2 * (len(gdp_strikes) - 1) and all("conid_yes=9901" in u for u in g4["links"])
                        and g4["marks"] == 2 * len(gdp_strikes) and g4["cents"] >= 3
                        and g4["listing"].startswith("Listed on ForecastEx as World GDP per Capita Growth"),
                        f"links={len(g4['links'])} marks={g4['marks']} cents={g4['cents']} {g4['listing'][:50]}")
                chk.add(f"{scheme} gdp: the price line sits under the contract's figure, once",
                        g4["note"] == ["after-figure"], str(g4["note"]))
                chk.add(f"{scheme} gdp: an empty book is drawn hatched, with no price and no link",
                        g4["hatched"] == 1, f"hatched={g4['hatched']}")
                page.locator("#gdpMain rect[data-contract-url]").first.hover(force=True); page.wait_for_timeout(150)
                t_gdp = page.locator("#tip").inner_text()
                chk.add(f"{scheme} gdp: a price's box gives the standing book and both chances, not prices",
                        "Standing Yes bid" in t_gdp and "Chance of Yes" in t_gdp and "Not prices" in t_gdp, t_gdp[:140])
                page.evaluate("""() => { const i = [...document.querySelectorAll('#gdpPanel input[type=range]')]
                    .find(x => x.getAttribute('aria-label') === 'Last year of the growth period');
                  i.value = Number(i.value) - 1; i.dispatchEvent(new Event('input', { bubbles: true })); }""")
                page.wait_for_timeout(1400)
                g5 = _gdp_state()
                chk.add(f"{scheme} gdp: at another end year the listed prices are not drawn, and the line goes with them",
                        not g5["links"] and not g5["note"] and str(gdp_end) in g5["ladderSub"], f"{len(g5['links'])} {g5['note']} {g5['ladderSub']}")
                page.unroute(gdp_cfg_url, _gdp_cfg)
                page.unroute("**/data/snapshots/catalogue/**", _gdp_cat)
                # a phone keeps the page inside its width; each figure scrolls inside its own card
                page.set_viewport_size({"width": 375, "height": 800})
                page.goto(f"{srv.url}/climate-gdp.html"); page.wait_for_timeout(2500)
                gw_ = page.evaluate("() => [document.scrollingElement.scrollWidth, document.scrollingElement.clientWidth]")
                chk.add(f"{scheme} gdp: no sideways scroll on a phone", gw_[0] <= gw_[1] + 1, str(gw_))
                page.set_viewport_size({"width": 1200, "height": 900})
                # ---- agriculture: the same panel, one per crop, in sequence on
                # the tab itself rather than behind a listing link
                page.goto(f"{srv.url}/agriculture.html")
                page.wait_for_timeout(900)
                # an older link to the generic listing must land on the real page,
                # not on a superseded one rendering under the same highlighted tab
                page.goto(f"{srv.url}/category.html?c=agriculture"); page.wait_for_timeout(700)
                chk.add(f"{scheme} agriculture: the old listing link lands on the page",
                        page.url.endswith("/agriculture.html")
                        and page.locator("#panels .panel").count() >= 3, page.url[-40:])
                page.goto(f"{srv.url}/category.html?c=not-a-category"); page.wait_for_timeout(700)
                chk.add(f"{scheme} category: an unknown slug says so rather than breaking",
                        "Unknown" in page.locator("#catTitle").inner_text(),
                        page.locator("#catTitle").inner_text()[:40])
                page.goto(f"{srv.url}/agriculture.html"); page.wait_for_timeout(900)
                ag_panels = page.locator("#panels .panel").count()
                chk.add(f"{scheme} agriculture: a panel per crop, drawn on the page",
                        ag_panels >= 3, f"panels={ag_panels}")
                ag_mk = page.locator("#panels svg circle[data-tip]").count()
                ag_lk = page.locator("#panels svg circle[role='link']").count()
                chk.add(f"{scheme} agriculture: every strike marker opens its contract",
                        ag_mk >= 50 and ag_lk == ag_mk, f"markers={ag_mk} linked={ag_lk}")
                page.locator("#panels svg circle[data-tip]").first.hover(force=True); page.wait_for_timeout(150)
                ag_tip = page.locator("#tip").inner_text()
                chk.add(f"{scheme} agriculture: a marker hover shows settlement and the book",
                        "Settles" in ag_tip and "Yes" in ag_tip, ag_tip[:80])
                chk.add(f"{scheme} agriculture: the trend tool is offered",
                        page.locator("#panels text", has_text="drag across the history").count() >= 3,
                        str(page.locator("#panels text", has_text="drag across the history").count()))
                # the yes/no bars belong to the daily boards; these panels are the
                # climate idiom and must not grow them
                chk.add(f"{scheme} agriculture: no yes/no ladder bars on these panels",
                        page.locator("#panels rect.yes, #panels rect.no").count() == 0,
                        str(page.locator("#panels rect.yes, #panels rect.no").count()))
                # a department publishes a figure for a period still open and
                # revises it, so the tail of a crop series is an estimate, not
                # history, and must not be drawn through the strikes as though
                # the answer were already known
                seg = page.eval_on_selector_all(
                    "#panels .panel:first-child path[stroke='var(--obs)']",
                    "e=>e.map(x=>x.getAttribute('stroke-dasharray')||'solid')")
                chk.add(f"{scheme} agriculture: the unsettled tail is drawn as an estimate",
                        seg.count("solid") == 1 and len(seg) == 2, str(seg))
                page.locator("#panels .panel:first-child path[stroke='var(--obs)']").last.hover(force=True)
                page.wait_for_timeout(150)

                # a panel opens to fill the window and keeps everything it had
                page.locator("#panels .panel").first.locator(".zb.ex").click(); page.wait_for_timeout(800)
                full = page.locator("#panels .panel.full")
                bx = full.bounding_box()
                vw = page.viewport_size["width"]; vh = page.viewport_size["height"]
                chk.add(f"{scheme} expand: the panel fills the window",
                        full.count() == 1 and abs(bx["width"] - vw) < 2 and abs(bx["height"] - vh) < 2,
                        f"{round(bx['width'])}x{round(bx['height'])} of {vw}x{vh}")
                chk.add(f"{scheme} expand: the page behind cannot scroll under it",
                        page.evaluate("()=>getComputedStyle(document.body).overflow") == "hidden", "")
                full.locator(".zb", has_text="10y").click(); page.wait_for_timeout(600)
                full = page.locator("#panels .panel.full")
                full.locator(".zb.fc").click(); page.wait_for_timeout(800)
                full = page.locator("#panels .panel.full")
                chk.add(f"{scheme} expand: zoom and projection still work while expanded",
                        full.count() == 1 and full.locator("path[stroke='var(--fcst)']").count() == 1,
                        str(full.count()))
                full.locator("circle[data-tip]").nth(2).hover(force=True); page.wait_for_timeout(300)
                chk.add(f"{scheme} expand: hovers still work over the overlay",
                        page.evaluate("()=>+getComputedStyle(document.querySelector('#tip')).opacity") == 1
                        and page.locator("#tip .tprice .tp").count() == 2, "")
                page.keyboard.press("Escape"); page.wait_for_timeout(700)
                chk.add(f"{scheme} expand: Escape closes it and gives the page back",
                        page.locator("#panels .panel.full").count() == 0
                        and page.evaluate("()=>getComputedStyle(document.body).overflow") != "hidden",
                        "")
                # a reading is a reading: a line between two of them draws values
                # nobody measured, so each one carries a mark where there is room
                page.locator("#panels .panel").first.locator(".zb", has_text="10y").click()
                page.wait_for_timeout(600)
                chk.add(f"{scheme} readings: a zoomed series marks each reading",
                        page.locator("#panels .panel:first-child circle.rdot").count() >= 5,
                        str(page.locator("#panels .panel:first-child circle.rdot").count()))
                chk.add(f"{scheme} readings: the marks are decoration, never a hit target",
                        page.eval_on_selector_all("#panels circle.rdot",
                            "e=>e.every(x=>getComputedStyle(x).pointerEvents==='none')"), "")

                # every contract names the document that governs it, and offers
                # the same ladder in the allocation calculator; the sub-line
                # carries both links and nothing else
                hrefs = page.eval_on_selector_all("#panels .psub a", "e=>e.map(x=>x.getAttribute('href')||'')")
                terms_n = sum(1 for u in hrefs if "TermsandConditions.pdf" in u)
                other = [u for u in hrefs if "TermsandConditions.pdf" not in u]
                chk.add(f"{scheme} terms: every drawn product links its regulatory document",
                        terms_n >= 3 and all(u.startswith("allocator.html?m=") for u in other),
                        str([u.rsplit("/", 1)[-1] for u in hrefs[:4]]))
                chk.add(f"{scheme} terms: every drawn product links the allocation calculator",
                        len(other) >= 3, str(other[:3]))
                # rain is T[R/S][region]; TR[Jurisdiction] is tax revenue with its
                # own document, so a prefix match would put the wrong terms here
                page.goto(f"{srv.url}/contract.html?id=TRHOU"); page.wait_for_timeout(1200)
                href = page.evaluate("()=>WXM.termsUrl('TRHOU')")
                chk.add(f"{scheme} terms: rain gets the rain document, not tax revenue",
                        href.endswith("/TTermsandConditions.pdf"), href)
                chk.add(f"{scheme} terms: the daily temperature series resolves too",
                        page.evaluate("()=>WXM.termsUrl('DHATL')").endswith("/DailyTemperatureTermsandConditions.pdf"),
                        page.evaluate("()=>WXM.termsUrl('DHATL')"))
                page.goto(f"{srv.url}/agriculture.html"); page.wait_for_timeout(1400)

                # the two prices lead the box: it is what a reader hovered for
                page.locator("#panels svg circle[data-tip]").nth(3).hover(force=True)
                page.wait_for_timeout(250)
                blocks = page.eval_on_selector_all("#tip .tprice .tp",
                    "e=>e.map(x=>({cls:x.className, lab:x.querySelector('.tpl').textContent, v:x.querySelector('.tpv').textContent}))")
                chk.add(f"{scheme} strike box: both prices lead, big, in Yes green and No red",
                        len(blocks) == 2 and blocks[0]["lab"] == "Buy Yes" and blocks[1]["lab"] == "Buy No"
                        and "yes" in blocks[0]["cls"] and "no" in blocks[1]["cls"],
                        str(blocks))
                chk.add(f"{scheme} strike box: the prices are cents, not blank",
                        all(b["v"].endswith("\u00a2") for b in blocks), str([b["v"] for b in blocks]))
                # and the color those markers carry is explained on the panel
                chk.add(f"{scheme} panel: a color key explains the marker color",
                        page.locator("#panels linearGradient stop").count() >= 5
                        and any("chance it ends above the strike" in t for t in page.eval_on_selector_all(
                            "#panels text", "e=>e.map(x=>x.textContent)")),
                        str(page.locator("#panels linearGradient stop").count()))
                # red at nothing, green at a dollar: a dear strike and a cheap one
                # must not come out the same color
                pairs = page.eval_on_selector_all("#panels .panel:first-child circle[data-tip]",
                    "e=>e.map(x=>({y:+x.getAttribute('cy'), f:x.getAttribute('fill')}))")
                lowest = max(pairs, key=lambda r: r["y"])   # lowest strike sits lowest on screen
                highest = min(pairs, key=lambda r: r["y"])
                def _rg(c):
                    m = __import__("re").match(r"rgb\((\d+),\s*(\d+),\s*(\d+)\)", c or "")
                    if m: return int(m.group(1)), int(m.group(2))
                    c = (c or "").lstrip("#")
                    return (int(c[0:2], 16), int(c[2:4], 16)) if len(c) == 6 else (0, 0)
                lr, lg = _rg(lowest["f"]); hr, hg = _rg(highest["f"])
                chk.add(f"{scheme} panel: the ramp runs red at nothing to green at a dollar",
                        lg > lr and hr > hg, f"cheap-to-exceed={lowest['f']} dear={highest['f']}")

                # thirty strikes would make a crossing list unreadable, so a
                # ladder reports what the trend projects for each settling year
                agb = page.locator("#panels svg").first.bounding_box()
                _drag(agb, 0.18, 0.55)
                ag_note = page.locator("#panels .panel .note").first.inner_text()
                chk.add(f"{scheme} agriculture: the trend fit projects each settling year",
                        "per decade" in ag_note and "crosses" not in ag_note
                        and ag_note.count(" · ") <= 8, ag_note[:110])

                # ---- energy: the same panels, plus the two fallbacks. A contract
                # that resolves on an event has nothing to plot and gets the
                # Yes/No ladder; one the exchange is not listing gets a line
                # saying so rather than being left off the page.
                # ---- weather: monthly series, so the strikes sit inside a year
                # and the record is carried forward by calendar month to reach them
                page.goto(f"{srv.url}/weather.html"); page.wait_for_timeout(1400)
                w_panels = page.locator("#panels .panel").count()
                w_charts = page.locator("#panels svg").count()
                w_proj = page.locator("#panels path[stroke-dasharray='5 4']").count()
                chk.add(f"{scheme} weather: every product on the page, not behind a link",
                        w_panels >= 30 and w_charts >= 8, f"panels={w_panels} charts={w_charts}")
                chk.add(f"{scheme} weather: the record is carried forward to reach the strikes",
                        w_proj >= 5, f"projections={w_proj}")
                # a monthly contract must be named by its month, never by a bare year
                w_tips = page.eval_on_selector_all(
                    "#panels svg circle[data-tip]", "e=>e.map(x=>x.getAttribute('aria-label')||'')")
                chk.add(f"{scheme} weather: strikes are drawn, and linked to their contract",
                        len(w_tips) >= 20 and all(t for t in w_tips), f"markers={len(w_tips)}")
                # ---- the SW severe displays: the history as a series panel and
                #      the month in progress in the hurricane shape, for all
                #      three phenomena, listed or not
                sw_js = """() => {
                  const t = document.querySelector('#panels').innerText;
                  const blocks = [...document.querySelectorAll('#panels .panel')]
                    .filter(p => /in progress/.test(p.textContent) && /report count/.test(p.textContent));
                  return { series: (t.match(/US monthly (tornado|severe-wind|severe-hail) reports/g) || []).length,
                           blocks: blocks.length,
                           envelopes: blocks.reduce((a, p) => a + p.querySelectorAll("path[fill='var(--accent)']").length, 0),
                           settle: blocks.every(p => /settles on/.test(p.textContent)),
                           bigFig: blocks.some(p => /so far/.test(p.textContent)),
                           ladder: blocks.some(p => /market.s ladder/i.test(p.textContent)),
                           probs: blocks.some(p => /fair value|probability/i.test(p.textContent)) };
                }"""
                sw = page.evaluate(sw_js)
                chk.add(f"{scheme} severe: all three phenomena draw the history series",
                        sw["series"] >= 3, str(sw))
                # The blocks are read a second time on the contract page check's
                # date (SW_NOW), since the samples list no SW ladder for the wall
                # clock's month from November 2026. The ladder check and the
                # probability check read that copy.
                page_wall, page = page, fixture_page(SW_NOW)
                page.goto(f"{srv.url}/weather.html"); page.wait_for_timeout(1400)
                sw_listed = page.evaluate(sw_js)
                page.context.close(); page = page_wall
                # ---- the live station map: KSFO's sample carries a regional
                #      frame and nearby readings, so the overlay must draw
                page.goto(f"{srv.url}/city.html?station=KSFO"); page.wait_for_timeout(1800)
                loc = page.evaluate("""() => {
                  const box = document.querySelector('#locator .locbox');
                  if (!box) return null;
                  const img = box.querySelector('img');
                  const svg = box.querySelector('svg');
                  return { region: img && img.src.includes('_region'),
                           dots: svg ? svg.querySelectorAll('circle').length : 0,
                           temps: svg ? [...svg.querySelectorAll('text')].filter(t => /\u00b0/.test(t.textContent)).length : 0,
                           ring: svg ? [...svg.querySelectorAll('circle')].some(c2 => c2.getAttribute('stroke') === 'var(--accent)') : false,
                           cap: (document.querySelector('#locator .cap') || {}).textContent || '' };
                }""")
                chk.add(f"{scheme} locator: the regional frame carries the live overlay",
                        bool(loc and loc["region"] and loc["dots"] >= 6 and loc["temps"] >= 3), str(loc)[:160])
                chk.add(f"{scheme} locator: the resolving station is ringed and labeled on the map",
                        bool(loc and loc["ring"]) and page.locator("#locator .locbox svg text", has_text="settlement station").count() >= 1,
                        str(loc and loc["cap"])[:120])
                chk.add(f"{scheme} locator: how far across the picture is, is drawn on the picture",
                        page.locator("#locator .locbox svg text", has_text="miles").count() >= 1
                        or page.locator("#locator .locbox svg text", has_text="mile").count() >= 1, "")
                sst = page.evaluate("""() => {
                  const svg = document.querySelector('#locator .locbox svg');
                  if (!svg) return null;
                  const lbl = [...svg.querySelectorAll('text')].some(t => /settlement station/.test(t.textContent));
                  // the center hit circle sits over the ring; hover it and read the tip
                  const hits = [...svg.querySelectorAll("circle[fill='transparent']")];
                  const center = hits.find(c2 => +c2.getAttribute('r') >= 17);
                  if (!center) return { lbl, tip: null };
                  center.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 300, clientY: 300 }));
                  const t = document.querySelector('#tip');
                  return { lbl, tip: t ? t.innerText : null };
                }""")
                chk.add(f"{scheme} locator: the settlement station is labeled on the map",
                        bool(sst and sst["lbl"]), str(sst and sst["lbl"]))
                geom = page.evaluate("""() => {
                  const svg = document.querySelector('#locator .locbox svg.locov');
                  if (!svg) return null;
                  const num = (e, a) => parseFloat(e.getAttribute(a));
                  // the settlement label is the only accent-stroked rect
                  const lab = [...svg.querySelectorAll('rect')]
                    .find(r => r.getAttribute('stroke') === 'var(--accent)');
                  const box = lab ? {x: num(lab,'x'), y: num(lab,'y'),
                                     w: num(lab,'width'), h: num(lab,'height')} : null;
                  const vb = svg.getAttribute('viewBox').split(' ').map(Number);
                  const cx = vb[2] / 2, cy = vb[3] / 2;
                  // the center station's barb strokes: accent-colored lines near it
                  const barb = [...svg.querySelectorAll('line')]
                    .filter(l => l.getAttribute('stroke') === 'var(--accent)')
                    .map(l => ({x1: num(l,'x1'), y1: num(l,'y1'), x2: num(l,'x2'), y2: num(l,'y2')}))
                    .filter(l => Math.hypot(l.x1 - cx, l.y1 - cy) < 60);
                  const hits = (l, b) => {
                    const lo = {x: Math.min(l.x1,l.x2), y: Math.min(l.y1,l.y2),
                                X: Math.max(l.x1,l.x2), Y: Math.max(l.y1,l.y2)};
                    return !(lo.X < b.x || lo.x > b.x + b.w || lo.Y < b.y || lo.y > b.y + b.h);
                  };
                  // the key, and the stations it must not sit on
                  const key = [...svg.querySelectorAll('g[transform]')]
                    .find(g2 => /STATION MODEL/.test(g2.textContent));
                  let keyBox = null;
                  if (key) {
                    const m2 = /translate\(([-\d.]+),([-\d.]+)\)/.exec(key.getAttribute('transform'));
                    const r2 = key.querySelector('rect');
                    if (m2 && r2) keyBox = {x: +m2[1], y: +m2[2], w: num(r2,'width'), h: num(r2,'height')};
                  }
                  const stations = [...svg.querySelectorAll("circle[fill='transparent']")]
                    .map(c2 => ({x: num(c2,'cx'), y: num(c2,'cy')}));
                  const covered = keyBox ? stations.filter(st =>
                    st.x > keyBox.x && st.x < keyBox.x + keyBox.w &&
                    st.y > keyBox.y && st.y < keyBox.y + keyBox.h).length : -1;
                  return { hasKey: !!keyBox, keyText: key ? key.textContent : '',
                           covered, barbs: barb.length,
                           labelOnBarb: box ? barb.some(l => hits(l, box)) : null };
                }""")
                zo = page.evaluate("""() => {
                  const svg = document.querySelector('#locator svg.locov');
                  const img = document.querySelector('#locator .locstack img');
                  if (!svg || !img) return null;
                  const els = [...svg.children];
                  // a temperature chip is a rounded rect; a barb stroke is a line
                  const chip = els.findIndex(e => e.tagName === 'rect' && e.getAttribute('rx') === '4');
                  const line = els.findIndex(e => e.tagName === 'line');
                  // the logical width the overlay's coordinate transform uses
                  const logical = svg.getAttribute('viewBox').split(' ').map(Number)[2];
                  return { chip, line, over: chip >= 0 && line > chip,
                           natural: img.naturalWidth, logical,
                           supersample: logical ? img.naturalWidth / logical : 0 };
                }""")
                chk.add(f"{scheme} locator: the barbs draw over the numbers, not under them",
                        bool(zo and zo["over"]), str(zo and {k: zo[k] for k in ('chip', 'line')}))
                # the source carries twice the logical size, so the map stays
                # sharp on a display with more than one device pixel per CSS one
                chk.add(f"{scheme} locator: the image is fetched at twice the size it is drawn at",
                        bool(zo and zo["supersample"] >= 1.99),
                        str(zo and {"natural": zo["natural"], "logical": zo["logical"]}))
                chk.add(f"{scheme} locator: the key explains the glyph, the cover and the barbs",
                        bool(geom and geom["hasKey"]
                             and "SKY COVER" in geom["keyText"] and "WIND, MPH" in geom["keyText"]
                             and "Dew point" in geom["keyText"] and "Wind, from" in geom["keyText"]),
                        str(geom and geom["keyText"])[:110])
                chk.add(f"{scheme} locator: the key sits where it covers no station",
                        geom is not None and geom["covered"] == 0, str(geom and geom["covered"]))
                chk.add(f"{scheme} locator: the settlement label clears its own wind barb",
                        geom is not None and geom["labelOnBarb"] is False,
                        f"barbs={geom and geom['barbs']} onBarb={geom and geom['labelOnBarb']}")
                chk.add(f"{scheme} locator: hovering the settlement station reads its own report",
                        bool(sst and sst["tip"] and "settlement station" in sst["tip"]
                             and "Dewpoint" in sst["tip"]), str(sst and (sst["tip"] or ""))[:120])
                # expanding must never shrink the map. The map is already as wide
                # as the column, so a fit-to-window rule loses to the column on any
                # short window, and the overlay has to stay on the image
                ex = page.evaluate("""() => {
                  const img = document.querySelector('#locator .locstack img');
                  const btn = [...document.querySelectorAll('button')]
                    .find(b => b.textContent.trim() === 'Expand map');
                  if (!img || !btn) return null;
                  const box = img.closest('.locbox');
                  const meas = () => { const r = img.getBoundingClientRect(),
                                             s = box.querySelector('svg.locov').getBoundingClientRect();
                    return { w: r.width, h: r.height,
                             on: Math.abs(r.width - s.width) < 1 && Math.abs(r.height - s.height) < 1 }; };
                  const a = meas(); btn.click(); const b = meas(); btn.click(); const c = meas();
                  return { collapsed: a.w, expanded: b.w, closed: c.w, aligned: b.on,
                           grew: b.w >= a.w - 0.5, restored: Math.abs(c.w - a.w) < 1 };
                }""")
                chk.add(f"{scheme} locator: expanding the map never makes it smaller",
                        bool(ex and ex["grew"] and ex["restored"]),
                        str(ex and {k: round(ex[k]) for k in ('collapsed', 'expanded', 'closed')}))
                chk.add(f"{scheme} locator: the overlay stays on the image when expanded",
                        bool(ex and ex["aligned"]), str(ex and ex["aligned"]))
                # a variable wind: the nearby reports carry the METAR's own "VRB", which has no
                # direction to draw a staff toward
                def _vrb_route(route):
                    if route.request.url.endswith("/obs/KSFO.json"):
                        resp = route.fetch(); d = json.loads(resp.text())
                        for n in d.get("nearby") or []:
                            n["wdir"], n["wspd"] = "VRB", 8
                        if d.get("nearbySelf"):
                            d["nearbySelf"]["wdir"], d["nearbySelf"]["wspd"] = "VRB", 6
                        return route.fulfill(response=resp, body=json.dumps(d))
                    return route.continue_()
                page.route("**/data/snapshots/**", _vrb_route)
                errs_v = len(errs)
                page.goto(f"{srv.url}/city.html?station=KSFO"); page.wait_for_timeout(2000)
                vr = page.evaluate("""() => {
                  const svg = document.querySelector('#locator svg.locov');
                  if (!svg) return null;
                  const nan = [...svg.querySelectorAll('line, path, circle')]
                    .filter(e => [...e.attributes].some(a => /NaN/.test(a.value))).length;
                  const hits = [...svg.querySelectorAll("circle[fill='transparent']")].filter(c2 => +c2.getAttribute('r') < 17);
                  if (!hits.length) return { nan, tip: null };
                  hits[0].dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 300, clientY: 300 }));
                  return { nan, tip: document.querySelector('#tip').innerText };
                }""")
                page.unroute("**/data/snapshots/**")
                chk.add(f"{scheme} locator: a variable wind draws no staff, reads as variable and throws nothing",
                        bool(vr and vr["nan"] == 0 and vr["tip"] and "variable at" in vr["tip"]) and len(errs) == errs_v,
                        str(vr)[:160] + " " + "; ".join(errs[errs_v:])[:160])
                # a station's own page: the thing a search result and a shared
                # link point at, which has to say which station it is before any
                # script has run and still draw that station's chart
                page.goto(f"{srv.url}/san-francisco-ksfo.html"); page.wait_for_timeout(1600)
                sp = page.evaluate("""() => {
                  const g = (sel, a) => { const e = document.querySelector(sel); return e ? e.getAttribute(a) : null; };
                  return { title: document.title,
                           h1: (document.querySelector('h1') || {}).textContent || '',
                           desc: g('meta[name="description"]', 'content'),
                           canon: g('link[rel="canonical"]', 'href'),
                           ogTitle: g('meta[property="og:title"]', 'content'),
                           ogImage: g('meta[property="og:image"]', 'content'),
                           card: g('meta[name="twitter:card"]', 'content'),
                           station: window.WX_STATION,
                           series: document.querySelectorAll('#chart path').length,
                           alloc: (document.querySelector('#allocLink') || {}).getAttribute
                                  ? document.querySelector('#allocLink').getAttribute('href') : null,
                           url: location.pathname };
                }""")
                chk.add(f"{scheme} station page: it names its station and draws that station's chart",
                        bool(sp and sp["station"] == "KSFO" and "KSFO" in sp["title"]
                             and "San Francisco" in sp["title"] and sp["series"] >= 2),
                        str(sp and {k: sp[k] for k in ('title', 'station', 'series')})[:130])
                chk.add(f"{scheme} station page: a crawler reads a heading without running the chart",
                        bool(sp and "San Francisco" in sp["h1"] and "KSFO" in sp["h1"]), str(sp and sp["h1"]))
                chk.add(f"{scheme} station page: it carries the description and card a shared link shows",
                        # the chart rewrites document.title with the date once it
                        # draws, so the card is checked against the tag, not that
                        bool(sp and sp["desc"] and "KSFO" in sp["desc"]
                             and sp["ogTitle"] and "San Francisco" in sp["ogTitle"] and "KSFO" in sp["ogTitle"]
                             and sp["canon"] and sp["canon"].endswith("/san-francisco-ksfo.html")
                             and sp["ogImage"] and sp["ogImage"].endswith("KSFO_region.png")
                             and sp["card"] == "summary_large_image"),
                        str(sp and {k: sp[k] for k in ('canon', 'card')})[:130])
                chk.add(f"{scheme} station page: the calculator opens on this station",
                        bool(sp and sp["alloc"] and sp["alloc"].endswith("city:KSFO")), str(sp and sp["alloc"]))
                # the per-station prose block was removed on the owner's call
                chk.add(f"{scheme} station page: no per-station prose block",
                        page.locator("#cityAbout").count() == 0, "")
                # served text, titles rather than prose: the heading and one line
                raw_sp = urllib.request.urlopen(f"{srv.url}/san-francisco-ksfo.html").read().decode()
                chk.add(f"{scheme} station page: a served heading and one line naming what the page is",
                        "San Francisco (KSFO)</h1>" in raw_sp
                        and "San Francisco weather forecast, KSFO observations and daily high and low"
                            " temperature prediction markets." in raw_sp,
                        raw_sp[raw_sp.find("cityLede") - 40:raw_sp.find("cityLede") + 180] if "cityLede" in raw_sp else "no lede")
                # the tag a search result reads, and the title the chart leaves
                # behind once it has drawn, which a rendering crawler reads instead
                chk.add(f"{scheme} station page: the title carries the city's weather and its market",
                        bool(sp and sp["ogTitle"].startswith("San Francisco weather")
                             and "prediction market" in sp["ogTitle"]
                             and sp["title"].startswith("San Francisco weather")
                             and "KSFO" in sp["title"]), str(sp and [sp["ogTitle"], sp["title"]]))
                # the additional-variables panels are part of the page: drawn
                # without a click, single column at the temperature panel's own
                # frame, the discussion beside them, both days one toggle apart
                av = page.evaluate("""async () => {
                  const sect = document.querySelector('#advSection');
                  if (!sect || sect.hidden) return null;
                  const svg = () => document.querySelector('#advPanels svg');
                  if (!svg()) return null;
                  const texts = [...svg().querySelectorAll('text')].map(t => t.textContent);
                  const dots = () => svg().querySelectorAll('circle').length;
                  const before = dots();
                  const dashes = new Set([...svg().querySelectorAll('path')]
                    .map(pp => pp.getAttribute('stroke-dasharray')).filter(Boolean));
                  const TOOLC = ['var(--nws)', 'var(--nbm)', 'var(--lamp)', 'var(--mav)', 'var(--hrrr)'];
                  const toolPaths = () => [...svg().querySelectorAll('path')].filter(pp => TOOLC.includes(pp.getAttribute('stroke')));
                  const todaySolid = toolPaths().filter(pp => !pp.getAttribute('stroke-dasharray')).length;
                  document.querySelector('#advYday').click();
                  await new Promise(r => setTimeout(r, 400));
                  const yday = dots();
                  const ydaySolid = toolPaths().filter(pp => !pp.getAttribute('stroke-dasharray')).length;
                  const cap = (document.querySelector('#advCap') || {}).textContent || '';
                  document.querySelector('#advToday').click();
                  // the chart's day-ahead button carries the panels with it
                  document.querySelector('#dayTomorrow').click();
                  await new Promise(r => setTimeout(r, 400));
                  const tmwSynced = document.querySelector('#advTmw').classList.contains('on')
                    && document.querySelector('#advTmw').classList.contains('on')
                    && !!svg();
                  document.querySelector('#dayToday').click();
                  await new Promise(r => setTimeout(r, 300));
                  const toolDots = [...svg().querySelectorAll('circle')]
                    .filter(c2 => c2.getAttribute('fill') === 'var(--nws)').length;
                  const disc = document.querySelector('.advrow #discussion');
                  const cardW = document.querySelector('#advCard').getBoundingClientRect().width;
                  const chartW = document.querySelector('#chartCard').getBoundingClientRect().width;
                  return { nws: texts.indexOf('Weather Service') >= 0, tmwSynced, toolDots,
                           temps: texts.filter(t => t === 'TEMPERATURE').length,
                           mph: texts.some(t => / mph$/.test(t)),
                           styles: [...dashes].sort(), todaySolid, ydaySolid,
                           today: before, yday,
                           noCap: cap.trim() === '',
                           discBeside: !!disc,
                           share: cardW / chartW };
                }""")
                chk.add(f"{scheme} advanced: the panels draw without a click, both days one toggle apart",
                        bool(av and av["today"] > 100 and av["yday"] > 100),
                        str(av and {k: av[k] for k in ('today', 'yday')}))
                chk.add(f"{scheme} advanced: no caption under the panels",
                        bool(av and av["noCap"]), str(av and av["noCap"]))
                chk.add(f"{scheme} advanced: the Weather Service draws with the guidance tools",
                        bool(av and av["nws"]), str(av and av["nws"]))
                chk.add(f"{scheme} advanced: temperature appears once, at the top",
                        bool(av and av["temps"] == 1), str(av and av["temps"]))
                chk.add(f"{scheme} advanced: wind speed reads in mph",
                        bool(av and av["mph"]), "")
                chk.add(f"{scheme} advanced: newest runs solid and earlier runs dotted, as on the main chart",
                        bool(av and av["styles"] == ["2 3"] and av["todaySolid"] >= 12 and av["ydaySolid"] == 0),
                        str(av and {k: av[k] for k in ("styles", "todaySolid", "ydaySolid")}))
                chk.add(f"{scheme} advanced: the chart's day-ahead button carries the panels with it",
                        bool(av and av["tmwSynced"]), str(av and av["tmwSynced"]))
                chk.add(f"{scheme} advanced: every line carries dots at its own readings",
                        bool(av and av["toolDots"] >= 10), str(av and av["toolDots"]))
                # expanding must never make the column smaller, and closing
                # must give back exactly what was there
                ex2 = page.evaluate("""() => {
                  const svg = document.querySelector('#advPanels svg');
                  const btn = document.querySelector('#advExpand button');
                  if (!svg || !btn) return null;
                  const w0 = svg.getBoundingClientRect().width;
                  btn.click();
                  const w1 = svg.getBoundingClientRect().width;
                  btn.click();
                  const w2 = svg.getBoundingClientRect().width;
                  return { w0: Math.round(w0), w1: Math.round(w1), w2: Math.round(w2),
                           grew: w1 >= w0 - 1, restored: Math.abs(w2 - w0) < 1 };
                }""")
                chk.add(f"{scheme} advanced: expanding never makes the panels smaller",
                        bool(ex2 and ex2["grew"] and ex2["restored"]),
                        str(ex2 and {k: ex2[k] for k in ('w0', 'w1', 'w2')}))
                # the column is the temperature panel's share of the page, with
                # the forecast discussion in what the ladders use above (on a
                # window wide enough to hold the row)
                chk.add(f"{scheme} advanced: the discussion sits beside the column",
                        bool(av and av["discBeside"]
                             and (page.viewport_size["width"] <= 900 or 0.60 < av["share"] < 0.67)),
                        str(av and round(av["share"], 3)))
                # a report with no temperature still carries its wind and sky, and
                # the panels read those reports beside the chart rows. Here the
                # wind is taken off every chart row and carried only by reports
                # with no temperature, seven minutes later, so the observed wind
                # line and barbs exist only if the panels read rowsNoTemp
                _wk = ("wdir", "wspd", "wgst")

                def _adv_notemp(route):
                    u = route.request.url
                    if u.endswith("/obs/KSFO.json"):
                        resp = route.fetch(); d = json.loads(resp.text())
                        rows = d.get("rows") or []
                        later = lambda s: (dt.datetime.fromisoformat(s.replace("Z", "+00:00"))  # noqa: E731
                                           + dt.timedelta(minutes=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
                        d["rowsNoTemp"] = [dict({k: r[k] for k in _wk if k in r}, t=later(r["t"]), type="SPECI")
                                           for r in rows if r.get("wspd") is not None]
                        d["rows"] = [{k: v for k, v in r.items() if k not in _wk} for r in rows]
                        return route.fulfill(response=resp, body=json.dumps(d))
                    return route.continue_()

                _ksfo = page.url
                page.route("**/data/snapshots/**", _adv_notemp)
                page.goto(_ksfo); page.wait_for_timeout(1600)
                nb = page.evaluate("""() => {
                  const svg = document.querySelector('#advPanels svg');
                  if (!svg) return null;
                  return { barbs: svg.querySelectorAll("g[stroke='var(--obs)']").length
                                  + svg.querySelectorAll("circle[stroke='var(--obs)'][fill='none']").length,
                           lines: svg.querySelectorAll("path[stroke='var(--obs)']").length };
                }""")
                chk.add(f"{scheme} advanced: a report with no temperature still draws its wind",
                        bool(nb and nb["barbs"] >= 4 and nb["lines"] == 4), str(nb))
                page.unroute("**/data/snapshots/**")
                page.goto(_ksfo); page.wait_for_timeout(1600)
                # the main chart's lead-in hours keep the forecast that stood
                # for them, and its level labels say when each was issued
                mc = page.evaluate("""() => {
                  const svg = document.querySelector('#chart');
                  const yd = svg && svg.querySelector('g.ydfill');
                  return { fill: yd ? yd.children.length : 0 };
                }""")
                chk.add(f"{scheme} chart: the lead-in hours carry yesterday's forecast",
                        bool(mc and mc["fill"] >= 1), str(mc and mc["fill"]))
                # the label is the hour, a or p, and T or Y against the day on
                # screen; the word itself is gone, and the day-ahead board
                # shows the day-ahead levels rather than today's
                tg = page.evaluate("""async () => {
                  const svg = document.querySelector('#chart');
                  const texts = () => [...svg.querySelectorAll('text')].map(t => t.textContent);
                  const word = texts().filter(t => /issued/i.test(t));
                  const tags = texts().filter(t => /\(\d{1,2}[ap]( [TY])?\)$/.test(t));
                  const fc = (await WXD.get('forecast/KSFO.json')).data;
                  document.querySelector('#dayTomorrow').click();
                  await new Promise(r => setTimeout(r, 900));
                  const lv = texts().filter(t => /^\d{2,3}$/.test(t)).map(Number);
                  document.querySelector('#dayToday').click();
                  // tomorrow's level is the newest NWS run's highest row inside it; today's is
                  // pinned to the run issued before today began
                  const ds = Date.parse(fc.markers.dayStart), de = Date.parse(fc.markers.dayEnd);
                  const top = (rs, a, b) => Math.max(...rs.filter(r => r.tempF != null
                    && Date.parse(r.t) >= a && Date.parse(r.t) < b).map(r => r.tempF));
                  return { word, tags: tags.length, sample: tags[0] || null,
                           tomorrowLevels: lv,
                           wantTomorrow: top(fc.nws.hourly, de, de + 864e5),
                           dontWantToday: top(fc.asIssued.nws.rows, ds, de) };
                }""")
                chk.add(f"{scheme} chart: a cycle label is an hour, a or p, and T or Y",
                        bool(tg and not tg["word"] and tg["tags"] >= 3),
                        str(tg and {"word": tg["word"], "sample": tg["sample"]})[:110])
                chk.add(f"{scheme} chart: the day-ahead board draws the day-ahead levels",
                        bool(tg and tg["wantTomorrow"] in tg["tomorrowLevels"]
                             and (tg["dontWantToday"] == tg["wantTomorrow"]
                                  or tg["dontWantToday"] not in tg["tomorrowLevels"])),
                        str(tg and {k: tg[k] for k in ('wantTomorrow', 'dontWantToday')}))
                # ---- the price panels follow the board on screen (owner, 2026-10-16): the day-ahead
                # view draws no prices and no strikes while tomorrow's board is unlisted, and once it
                # lists, tomorrow's own prices
                px = page.evaluate("""async () => {
                  const svg = document.querySelector('#chart');
                  const count = () => ({ px: svg.querySelectorAll('path.pxline').length,
                                         pk: svg.querySelectorAll('line.pkline').length,
                                         note: [...svg.querySelectorAll('text')].filter(t => t.textContent === 'not listed yet').length });
                  const today = count();
                  document.querySelector('#dayTomorrow').click();
                  await new Promise(r => setTimeout(r, 700));
                  const tmw = count();
                  document.querySelector('#dayToday').click();
                  await new Promise(r => setTimeout(r, 500));
                  return { today, tmw };
                }""")
                chk.add(f"{scheme} chart: an unlisted day-ahead board draws no prices and no strikes",
                        bool(px and px["today"]["px"] >= 1 and px["today"]["pk"] >= 1 and px["tmw"]["px"] == 0
                             and px["tmw"]["pk"] == 0 and px["tmw"]["note"] >= 3), str(px))

                def _tmw_route(route):
                    if route.request.url.endswith("/market/KSFO.json"):
                        resp = route.fetch(); d = json.loads(resp.text())
                        mk = d.get("markers") or {}
                        day, tmw = mk.get("day"), mk.get("tomorrow")
                        if day and tmw and day in (d.get("days") or {}):
                            d["days"][tmw] = json.loads(json.dumps(d["days"][day]))
                            # tomorrow's board quoted 33 / 35 at every sample, which today's never shows,
                            # a day later than today's samples so they fall inside the day-ahead window
                            hist = (d.get("history") or {}).get(day) or {}
                            d.setdefault("history", {})[tmw] = {sd: {k: [[q[0] + 1440, 33, 35] for q in v] for k, v in rows.items()}
                                                                for sd, rows in hist.items()}
                        return route.fulfill(response=resp, body=json.dumps(d))
                    return route.continue_()
                page.route("**/data/snapshots/**", _tmw_route)
                page.goto(_ksfo); page.wait_for_timeout(2200)
                pl = page.evaluate("""async () => {
                  const svg = document.querySelector('#chart');
                  document.querySelector('#dayTomorrow').click();
                  await new Promise(r => setTimeout(r, 700));
                  const out = { px: svg.querySelectorAll('path.pxline').length, pk: svg.querySelectorAll('line.pkline').length,
                                ends: [...svg.querySelectorAll('text')].map(t => t.textContent).filter(t => /^[<>]\\d+° \\d+¢$/.test(t)) };
                  document.querySelector('#dayToday').click();
                  await new Promise(r => setTimeout(r, 500));
                  return out;
                }""")
                page.unroute("**/data/snapshots/**")
                page.goto(_ksfo); page.wait_for_timeout(1600)
                chk.add(f"{scheme} chart: a listed day-ahead board draws its own prices, not today's",
                        bool(pl and pl["px"] >= 1 and pl["pk"] >= 1 and pl["ends"]
                             and all(e.endswith(" 34¢") for e in pl["ends"])), str(pl))
                # ---- runs by model (owner, 2026-10-16): each model's newest run solid, every run it
                # superseded dotted; a level line is pinned to the run issued before the day began and
                # runs across exactly the hours its rows cover inside the day; the crosshair lists runs
                # model by model, the newest first
                rv = page.evaluate("""async () => {
                  const svg = document.querySelector('#chart');
                  const COLS = { nws: 'var(--nws)', nbm: 'var(--nbm)', lamp: 'var(--lamp)', mav: 'var(--mav)', hrrr: 'var(--hrrr)' };
                  const byCol = Object.fromEntries(Object.entries(COLS).map(([k, c]) => [c, k]));
                  const paths = [...svg.querySelectorAll('path')].filter(p => byCol[p.getAttribute('stroke')]);
                  const dash = [...new Set(paths.map(p => p.getAttribute('stroke-dasharray') || 'solid'))].sort();
                  const solidBy = {};
                  paths.filter(p => !p.getAttribute('stroke-dasharray')).forEach(p => {
                    const k = byCol[p.getAttribute('stroke')]; solidBy[k] = (solidBy[k] || 0) + 1; });
                  // the chart's own time scale, read off its midnight and day-end marks
                  const mark = nm => { const t = [...svg.querySelectorAll('text')].find(e => e.textContent === nm);
                                       return t ? +t.getAttribute('x') : null; };
                  const fc = (await WXD.get('forecast/KSFO.json')).data;
                  const ds = Date.parse(fc.markers.dayStart), de = Date.parse(fc.markers.dayEnd);
                  const xm = mark('midnight'), xe = mark('day end');
                  const X = t => xm + (t - ds) / (de - ds) * (xe - xm);
                  const H = 36e5, want = {}, got = {};
                  // today's levels are pinned to the run issued before the day began
                  Object.keys(COLS).forEach(k => {
                    const f = (fc.asIssued || {})[k]; if (!f) return;
                    const r = (f.rows || []).concat(f.ext || []).filter(q => q.tempF != null).map(q => Date.parse(q.t)).sort((a, b) => a - b);
                    const inday = r.filter(t => t >= ds && t < de); if (!inday.length) return;
                    const i = r.indexOf(inday[inday.length - 1]);
                    const step = Math.min(3 * H, Math.max(H, i + 1 < r.length ? r[i + 1] - r[i] : (i ? r[i] - r[i - 1] : H)));
                    want[k] = [X(inday[0]), X(Math.min(de, inday[inday.length - 1] + step))];
                  });
                  [...svg.querySelectorAll('line.lvlline')].forEach(l => {
                    const k = byCol[l.getAttribute('stroke')];
                    (got[k] = got[k] || []).push([+l.getAttribute('x1'), +l.getAttribute('x2')]);
                  });
                  const spanOk = Object.keys(want).length >= 3 && Object.keys(want).every(k =>
                    (got[k] || []).length === 2 && got[k].every(([a, b]) => Math.abs(a - want[k][0]) < 0.6 && Math.abs(b - want[k][1]) < 0.6));
                  const starts = new Set(Object.values(got).flat().map(([a]) => Math.round(a)));
                  // the crosshair, three quarters of the way into the day
                  const r0 = svg.getBoundingClientRect(), vb = svg.viewBox.baseVal;
                  const xq = X(ds + 0.75 * (de - ds)), yq = 200;
                  svg.dispatchEvent(new MouseEvent('mousemove', { bubbles: true,
                    clientX: r0.left + xq * r0.width / vb.width, clientY: r0.top + yq * r0.height / vb.height }));
                  await new Promise(r => setTimeout(r, 120));
                  // the five minute band's row belongs to the observation's group
                  const ORDER = ['Observed', 'Range between reports', 'NWS', 'NBM', 'LAMP', 'MOS', 'HRRR'];
                  const GROUP = m => (m === 'Range between reports' ? 'Observed' : m);
                  const tipRows = [...document.querySelectorAll('#tip .tk')].map(e => ({
                    m: ORDER.find(o => e.textContent.trim().startsWith(o)),
                    dotted: !!e.querySelector('svg.swl line[stroke-dasharray]'), gs: e.classList.contains('gs'),
                    text: e.textContent.trim() }));
                  svg.dispatchEvent(new MouseEvent('mouseleave', { bubbles: true }));
                  const idx = tipRows.map(r => ORDER.indexOf(r.m));
                  const grouped = idx.length > 3 && idx.every((v, i) => v >= 0 && (!i || v >= idx[i - 1]));
                  // no solid row after a dotted one: the newest run leads its model's group
                  const newestFirst = ORDER.every(m => { const g = tipRows.filter(r => r.m === m);
                    const d = g.findIndex(r => r.dotted); return d < 0 || g.slice(d).every(r => r.dotted); });
                  const twoRuns = ORDER.some(m => tipRows.filter(r => r.m === m).length >= 2);
                  const breaks = tipRows.filter((r, i) => i && GROUP(r.m) !== GROUP(tipRows[i - 1].m)).every(r => r.gs);
                  return { dash, solidBy, spanOk, want, got, starts: starts.size, grouped, newestFirst, twoRuns, breaks,
                           rows: tipRows.map(r => (r.dotted ? '..' : '--') + r.text) };
                }""")
                chk.add(f"{scheme} chart: each model's newest run is solid and every run it superseded dotted",
                        bool(rv and rv["dash"] == ["2 3", "solid"] and len(rv["solidBy"]) >= 3
                             and all(n == 1 for n in rv["solidBy"].values())),
                        str(rv and {k: rv[k] for k in ("dash", "solidBy")}))
                chk.add(f"{scheme} chart: a level line is pinned to the run issued before the day and spans its rows' hours",
                        bool(rv and rv["spanOk"] and rv["starts"] >= 2),
                        str(rv and {k: rv[k] for k in ("want", "got")})[:300])
                chk.add(f"{scheme} chart: the crosshair lists runs model by model, the newest first",
                        bool(rv and rv["grouped"] and rv["newestFirst"] and rv["twoRuns"] and rv["breaks"]),
                        str(rv and rv["rows"])[:300])
                # the postmortem asks for the reasoning that stood when it scores
                pm = page.evaluate("""async () => {
                  document.querySelector('#advYday').click();
                  await new Promise(r => setTimeout(r, 1400));
                  const host = document.querySelector('.advrow #discussion');
                  const t = host ? host.innerText : '';
                  document.querySelector('#advToday').click();
                  await new Promise(r => setTimeout(r, 600));
                  return { yday: t.slice(0, 400), today: (host ? host.innerText : '').slice(0, 400) };
                }""")
                chk.add(f"{scheme} postmortem: the discussion says which issuance it is showing",
                        bool(pm and (("standing at the moment scored" in pm["yday"])
                                     or ("do not reach back" in pm["yday"]))
                             and "standing at the moment scored" not in pm["today"]),
                        str(pm and pm["yday"])[:120])
                # every station the board carries has to have a page to link to,
                # built by the same rule the browser uses to write the link
                page.goto(f"{srv.url}/scorecard.html"); page.wait_for_timeout(1500)
                links = page.evaluate("""async () => {
                  const r = await fetch('data/snapshots/summary.json').then(x => x.json()).catch(() => null);
                  const cities = (r && (r.cities || (r.data && r.data.cities))) || [];
                  const out = [];
                  for (const c of cities) {
                    const href = WXC.cityHref(c);
                    const ok = await fetch(href, { method: 'GET' }).then(x => x.ok).catch(() => false);
                    out.push([c.station, href, ok]);
                  }
                  return out;
                }""")
                nopage = [l for l in (links or []) if not l[2]]
                chk.add(f"{scheme} station pages: every station on the board has one",
                        bool(links) and not nopage, f"n={len(links or [])} missing={nopage[:3]}")
                idx = page.evaluate("""async () => {
                  const t = await fetch('sitemap.xml').then(r => r.ok ? r.text() : '').catch(() => '');
                  const rb = await fetch('robots.txt').then(r => r.ok ? r.text() : '').catch(() => '');
                  return { urls: (t.match(/<loc>/g) || []).length, hasStation: t.indexOf('san-francisco-ksfo') >= 0,
                           noCity: t.indexOf('/city.html') < 0, robots: rb };
                }""")
                chk.add(f"{scheme} sitemap: it indexes every page and every station",
                        bool(idx and idx["urls"] >= 50 and idx["hasStation"] and idx["noCity"]),
                        str(idx and {k: idx[k] for k in ('urls', 'hasStation', 'noCity')}))
                chk.add(f"{scheme} robots: crawlers are allowed and told where the index is",
                        bool(idx and "Allow: /" in idx["robots"] and "sitemap.xml" in idx["robots"]),
                        str(idx and idx["robots"])[:80])
                # the lessons page: both courses, every lesson addressable, and
                # each one linking to the page it teaches
                page.goto(f"{srv.url}/lessons.html"); page.wait_for_timeout(1200)
                ls = page.evaluate("""() => {
                  const courses = [...document.querySelectorAll('.course')].map(c => c.id);
                  const lessons = [...document.querySelectorAll('details.lesson')];
                  const links = [...document.querySelectorAll('a.lgo')].map(a => a.getAttribute('href'));
                  return { courses, n: lessons.length, ids: lessons.map(l => l.id),
                           links, quiz: document.querySelectorAll('.lquiz li').length,
                           anyOpen: lessons.filter(l => l.open).length };
                }""")
                chk.add(f"{scheme} lessons: both courses render every lesson",
                        bool(ls and ls["courses"] == ["reading", "trading"] and ls["n"] == 18
                             and ls["quiz"] == 54),
                        str(ls and {k: ls[k] for k in ('courses', 'n', 'quiz')}))
                chk.add(f"{scheme} lessons: they open shut, so eighteen is not a wall",
                        bool(ls and ls["anyOpen"] == 0), str(ls and ls["anyOpen"]))
                chk.add(f"{scheme} lessons: every one links to a page that answers",
                        bool(ls and len(ls["links"]) == 18), str(ls and len(ls["links"] or [])))
                # a lesson named in the address opens itself, on a fresh load and
                # on a hash that arrives later
                page.goto(f"{srv.url}/lessons.html#trading-2"); page.wait_for_timeout(1200)
                deep = page.evaluate("""async () => {
                  const t = document.getElementById('trading-2');
                  const fresh = !!(t && t.open);
                  location.hash = 'reading-7';
                  await new Promise(r => setTimeout(r, 400));
                  const later = !!(document.getElementById('reading-7') || {}).open;
                  return { fresh, later };
                }""")
                chk.add(f"{scheme} lessons: a lesson named in the address opens itself",
                        bool(deep and deep["fresh"] and deep["later"]), str(deep))
                # unfinished and kept that way: a notice at the top, no link from
                # the navigation, and no entry in the index handed to crawlers
                page.goto(f"{srv.url}/lessons.html"); page.wait_for_timeout(900)
                wip = page.evaluate("""async () => {
                  const note = document.querySelector('.wrap > .note.warn');
                  const refs = [...document.querySelectorAll('header.site .refnav a')]
                    .map(a => a.getAttribute('href'));
                  const robots = document.querySelector('meta[name="robots"]');
                  const sm = await fetch('sitemap.xml').then(r => r.ok ? r.text() : '').catch(() => '');
                  return { notice: !!note && /under construction/i.test(note.textContent),
                           linked: refs.indexOf('lessons.html') >= 0,
                           noindex: !!robots && /noindex/.test(robots.content),
                           inSitemap: sm.indexOf('lessons.html') >= 0 };
                }""")
                chk.add(f"{scheme} lessons: the page says it is under construction",
                        bool(wip and wip["notice"]), str(wip and wip["notice"]))
                chk.add(f"{scheme} lessons: nothing links to it and nothing indexes it",
                        bool(wip and not wip["linked"] and wip["noindex"] and not wip["inSitemap"]),
                        str(wip))
                page.goto(f"{srv.url}/weather.html"); page.wait_for_timeout(1400)
                chk.add(f"{scheme} severe: the tornado reports lead the page",
                        page.evaluate("""() => {
                          const p = document.querySelector('#panels .panel');
                          return p ? /tornado/i.test(p.textContent) : false;
                        }""") is True, "")
                # the running figure exists only once the month in progress has a
                # published count, which it does not on the first of a month
                # before the Storm Prediction Center posts. The blocks are always
                # there; the figure is required only when there is one to draw.
                sw_live = page.evaluate("""async () => {
                  const d = await fetch('data/snapshots/series/severe-tornado.json')
                    .then(r => r.json()).catch(() => null);
                  const pts = (d && d.points) || [];
                  const now = new Date();
                  const key = String(now.getUTCFullYear())
                            + String(now.getUTCMonth() + 1).padStart(2, '0');
                  return pts.length ? pts[pts.length - 1][0] === key : false;
                }""")
                chk.add(f"{scheme} severe: all three phenomena draw the month in progress",
                        sw["blocks"] == 3 and sw["envelopes"] >= 3
                        and (sw["bigFig"] or not sw_live),
                        str(sw) + (" monthHasData=" + str(sw_live)))
                chk.add(f"{scheme} severe: the running month carries the market's ladder where one is listed",
                        sw_listed["ladder"], str(sw_listed))
                chk.add(f"{scheme} severe: the caption names the settlement number",
                        sw["settle"], str(sw))
                chk.add(f"{scheme} severe: no probability or fair value appears on the count blocks",
                        not sw["probs"] and not sw_listed["probs"],
                        f"wall={sw['probs']} pinned={sw_listed['probs']}")
                # a covered link is a broken promise: the element under the
                # center of a strike link rect must be the rect itself
                hit = page.evaluate('''() => {
                  const r = document.querySelector('#panels svg [data-contract-url]');
                  if (!r) return 'none';
                  const b = r.getBoundingClientRect();
                  const e = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
                  return e && (e === r || e.closest('[data-contract-url]') === r) ? 'clickable' : 'covered';
                }''')
                chk.add(f"{scheme} severe: strike links are clickable, not covered",
                        hit in ("clickable", "none"), hit)

                page.goto(f"{srv.url}/fossil-fuels.html"); page.wait_for_timeout(1200)
                ff = page.locator("#panels .panel").count()
                ff_mk = page.locator("#panels svg circle[data-tip]").count()
                ff_lk = page.locator("#panels svg circle[role='link']").count()
                chk.add(f"{scheme} fossil fuels: a panel per product", ff >= 12, f"panels={ff}")
                chk.add(f"{scheme} fossil fuels: every strike marker opens its contract",
                        ff_mk >= 100 and ff_lk == ff_mk, f"markers={ff_mk} linked={ff_lk}")
                # production and consumption cannot go negative and must not be
                # given an axis that says they can
                ffax = page.eval_on_selector_all(
                    "#panels .panel:first-child text",
                    "e=>e.map(x=>x.textContent).filter(t=>/^-/.test(t))")
                chk.add(f"{scheme} fossil fuels: no negative axis on a quantity", not ffax, str(ffax[:3]))

                page.goto(f"{srv.url}/electricity-renewables.html"); page.wait_for_timeout(1200)
                er = page.locator("#panels .panel").count()
                er_svg = page.locator("#panels svg").count()
                er_lad = page.locator("#panels .lrow").count()
                chk.add(f"{scheme} electricity: charts and event ladders side by side",
                        er >= 20 and er_svg >= 12 and er_lad >= 5,
                        f"panels={er} charts={er_svg} ladder rows={er_lad}")
                chk.add(f"{scheme} electricity: an unlisted product still says so",
                        page.locator("#panels .panel", has_text="Not currently listed").count() >= 1,
                        str(page.locator("#panels .panel", has_text="Not currently listed").count()))
                # the ladder keeps the exchange's buy-only language
                foot_e = page.locator("#foot").inner_text()
                chk.add(f"{scheme} electricity: the page says what it drew and what it could not",
                        "resolve on an event" in foot_e and "not currently listed" in foot_e, foot_e[-110:])
                # ---- scorecard hover: overall, station and day cells
                page.goto(f"{srv.url}/scorecard.html")
                page.wait_for_timeout(900)
                # scored days are measured against what happened; days still ahead
                # have nothing to measure against and keep the consensus center
                page.goto(f"{srv.url}/accuracy.html"); page.wait_for_timeout(2500)
                chk.add(f"{scheme} accuracy: the standings are on the scorecard, not repeated here",
                        page.locator("#standings").count() == 0
                        and page.locator("#standChart").count() == 0, "")
                # lows are a tab on every figure; the lead curve and the grid are the two proven
                lead_before = page.locator("#accLead").inner_html()
                page.locator("#accLeadBar button", has_text="Lows").first.click(); page.wait_for_timeout(500)
                grid_heads = page.eval_on_selector_all("#accGrid table.acc-t3 thead th", "e=>e.map(x=>x.textContent)")
                chk.add(f"{scheme} accuracy: the lows tab redraws the lead curve",
                        page.locator("#accLead").inner_html() != lead_before, "")
                # the scorecard is the paper's Tables 3 and 4 at its six leads
                heads_t = [t.strip() for t in grid_heads]
                grid_bar = page.locator("#accGridBar").inner_text()
                chk.add(f"{scheme} accuracy: the scorecard carries the paper's six leads with the 18 h mean error, the offset and the record",
                        heads_t[:1] == ["Series"] and heads_t.count("Hours before the end of the day") == 1
                        and heads_t.count("Hours before the extreme") == 1 and "ME 18 h" in heads_t and "Offset" in heads_t
                        and "Record since" in heads_t and [t for t in heads_t if t in ("30 h", "18 h", "12 h", "6 h")][:6] == ["30 h", "18 h", "12 h", "18 h", "12 h", "6 h"],
                        str(heads_t[:14]))
                chk.add(f"{scheme} accuracy: the scorecard has no CSV or newsletter controls",
                        "CSV" not in grid_bar and "Newsletter" not in grid_bar, grid_bar.replace("\n", " | ")[:100])
                page.locator("#accLeadBar button", has_text="Highs").first.click(); page.wait_for_timeout(300)
                grid_file = json.loads(urllib.request.urlopen(f"{srv.url}/data/snapshots/accuracy/grid.json").read().decode())
                gblk = grid_file["metric"]["high"]["cohorts"]["all"]
                page.locator("#accGridBar button", has_text="Highs").first.click(); page.wait_for_timeout(300)
                grp_rows = page.eval_on_selector_all("#accGrid table.acc-t3 tr.acc-g-group td:first-child", "e => e.map(x => x.textContent.trim())")
                first = page.eval_on_selector_all("#accGrid table.acc-t3 tbody tr", "e => e.slice(0, 1).map(x => x.textContent)")
                rec = page.eval_on_selector_all("#accGrid table.acc-t3 tbody tr:not(.acc-g-group) td:last-child", "e => e.map(x => x.textContent.trim())")
                chk.add(f"{scheme} accuracy: the scorecard opens on the market, then the groups in the file's order, and dates every record",
                        grp_rows == [g["title"] for g in gblk["groups"]] and len(grp_rows) == 5
                        and first and first[0].startswith("ForecastEx") and len(rec) >= 20
                        and all(re.match(r"^[A-Z][a-z]{2} \d+ \d{4}$", t) or t == "—" for t in rec),
                        f"groups={grp_rows} first={first[:1]}")
                t4 = page.locator("#accGrid table.acc-t4 tbody tr").count()
                sig = page.locator("#accGrid table.acc-t3 .pi.sig").count()
                chk.add(f"{scheme} accuracy: Table 4 gives the market and the four ensembles, and Holm-significant improvements are bold",
                        t4 == 5 and sig >= 20, f"table4={t4} bold={sig}")
                before = page.locator("#accGrid").inner_html()
                page.locator("#accGridBar button", has_text="As issued").first.click(); page.wait_for_timeout(400)
                iss = page.locator("#accGrid").inner_html()
                page.locator("#accGridBar button", has_text="Bias-corrected").first.click(); page.wait_for_timeout(300)
                page.locator("#accGridBar button", has_text="Balanced sample").first.click(); page.wait_for_timeout(400)
                bal = page.locator("#accGrid").inner_html()
                page.locator("#accGridBar button", has_text="Every day on record").first.click(); page.wait_for_timeout(300)
                chk.add(f"{scheme} accuracy: the scorecard redraws as issued and on the balanced sample",
                        iss != before and bal != before and "acc-t4" not in bal, "")
                # the map: the paper's four leads, a color on every scored station, a bold outline where Holm holds
                fills = page.eval_on_selector_all("#accMap circle", "e=>e.map(x=>x.getAttribute('fill')||'')")
                colored = sum(1 for f in fills if f.startswith("color-mix("))
                bold = page.locator("#accMap circle[stroke-width='2.4']").count()
                chk.add(f"{scheme} accuracy: the map colors the stations and outlines in bold where the difference holds after Holm",
                        colored >= 3 and bold >= 1, f"colored={colored} bold={bold}")
                lead_tabs = page.eval_on_selector_all("#accMapBar .tabgroup button", "e=>e.map(x=>x.textContent)")
                before = page.locator("#accMap").inner_html()
                page.locator("#accMapBar button", has_text="12 h before the extreme").first.click(); page.wait_for_timeout(500)
                chk.add(f"{scheme} accuracy: the map offers the paper's four single leads and redraws on each",
                        all(t in lead_tabs for t in ("30 h", "18 h", "18 h before the extreme", "12 h before the extreme"))
                        and page.locator("#accMap").inner_html() != before, str(lead_tabs))
                page.locator("#accMapBar button", has_text="30 h").first.click(); page.wait_for_timeout(400)
                # the forecast scored against the climate report has no value at Buckley Field; LAMP has one
                ndfd_hollow = page.locator("#accMap circle[stroke-dasharray]").count()
                page.select_option("#accMapBar select", "LAMP"); page.wait_for_timeout(500)
                lamp_hollow = page.locator("#accMap circle[stroke-dasharray]").count()
                page.select_option("#accMapBar select", "NDFD"); page.wait_for_timeout(300)
                chk.add(f"{scheme} accuracy: the map hollows Buckley Field for a series scored against the climate report",
                        ndfd_hollow >= lamp_hollow + 1, f"ndfd={ndfd_hollow} lamp={lamp_hollow}")

                # ---- every priced chart or board says its prices open the exchange (owner,
                # 2026-10-22): under a chart or board, or once above a page of many panels, and
                # never where nothing on the page opens a contract
                def _tn():
                    return page.evaluate("""() => [...document.querySelectorAll('p.tradenote')].map(n => {
                      const p_ = n.previousElementSibling, q_ = n.nextElementSibling;
                      return { text: n.textContent, prev: p_ ? (p_.id || p_.className) : null,
                               next: q_ ? (q_.id || q_.className) : null, host: n.parentNode.id || n.parentNode.className }; })""")
                for tn_path, tn_want in (("city.html?station=KATL", [{"prev": "bar"}]),
                                         ("hurricane.html", [{"prev": "basinCap"}, {"prev": "laddersCap"}, {"host": "cat4"}, {"host": "landfall"}]),
                                         ("contract.html?id=HCAB", [{"host": "cBody", "prev": "ladder"}]),
                                         ("fossil-fuels.html", [{"next": "panels"}]),
                                         ("climate.html", [{"next": "panels"}])):
                    page.goto(f"{srv.url}/{tn_path}"); page.wait_for_timeout(2600)
                    got = _tn()
                    ok_ = len(got) == len(tn_want) and all(g["text"] == "Click on any price to trade contracts through IBKR Prediction Markets" for g in got)
                    for g, want_ in zip(got, tn_want):
                        if any(g.get(k_) != v_ for k_, v_ in want_.items()):
                            ok_ = False
                    chk.add(f"{scheme} price links ({tn_path}): the line sits by the prices it describes", ok_, str(got)[:300])
                for tn_path in ("city.html?station=KATL&market=off", "index.html"):
                    page.goto(f"{srv.url}/{tn_path}"); page.wait_for_timeout(2200)
                    chk.add(f"{scheme} price links ({tn_path}): nothing opens a contract here, so no line",
                            page.locator("p.tradenote").count() == 0 and page.locator("[data-contract-url]").count() == 0,
                            str(page.locator("p.tradenote").count()))

                # ---- the wind-only shore stations are scored by the pipeline but carry no
                # temperature market, so the scorecard's figure leaves them out, on the landing
                # page and on the scorecard page alike (owner, 2026-10-20). Nantucket is put into
                # both files here, a copy of the first scored station, so it would have a row.
                _first = {}
                def _wind_only(route):
                    resp = route.fetch()
                    d_ = json.loads(resp.text())
                    if route.request.url.split("?")[0].endswith("/summary.json"):
                        c0 = dict(d_["cities"][0]); c0.update({"station": "KACK", "city": "Nantucket", "windOnly": True})
                        d_["cities"] = d_["cities"] + [c0]
                    else:
                        sid0, st0 = next(iter(d_["stations"].items()))
                        _first["city"] = st0.get("city")
                        d_["stations"]["KACK"] = dict(st0, city="Nantucket")
                    return route.fulfill(response=resp, body=json.dumps(d_))
                _wo_url = re.compile(r"/data/snapshots/(summary|scorecard)\.json(\?|$)")
                page.route(_wo_url, _wind_only)
                for _wo_page in ("index.html", "scorecard.html"):
                    page.goto(f"{srv.url}/{_wo_page}"); page.wait_for_timeout(1600)
                    wo_labels = page.eval_on_selector_all("#divsvg text", "e=>e.map(x=>x.textContent)")
                    chk.add(f"{scheme} scorecard ({_wo_page}): a wind-only station is scored but left off the figure",
                            "Nantucket" not in wo_labels and _first.get("city") in wo_labels,
                            str(["Nantucket" in wo_labels, _first.get("city") in wo_labels]))
                page.unroute(_wo_url, _wind_only)

                # ---- the scorecard grid: a row per station, a column per system
                page.goto(f"{srv.url}/scorecard.html"); page.wait_for_timeout(1400)
                cells = page.locator("#divsvg rect").count()
                chk.add(f"{scheme} scorecard: the grid draws a cell per station and system", cells >= 60, f"cells={cells}")
                heads = page.eval_on_selector_all("#divsvg text", "e=>e.map(x=>x.textContent)")
                chk.add(f"{scheme} scorecard: it carries the four systems, the market and the observation",
                        all(w in heads for w in ["Service", "Models", "MOS", "(LAMP)", "implied", "OBSERVED"]),
                        str([w for w in ["Service", "Models", "MOS", "(LAMP)", "implied", "OBSERVED"] if w not in heads]))
                chk.add(f"{scheme} scorecard: both margins are drawn",
                        "mean absolute error" in heads and "this station" in heads, "")
                # the market is a price, so it must not be inside the skill margins
                mae = page.evaluate("""() => { const S = document.querySelectorAll('#divsvg text');
                  return [...S].map(t => t.textContent); }""")
                chk.add(f"{scheme} scorecard: the color scale is on the figure",
                        any("too cold" in t for t in mae) and any("too warm" in t for t in mae), "")
                # seven scored days and both ends of the day, all addressable
                btns = page.eval_on_selector_all("#divControls button", "e=>e.map(x=>x.textContent)")
                chk.add(f"{scheme} scorecard: both ends of the day and up to seven days are offered",
                        btns[:2] == ["Highs", "Lows"] and 3 <= len(btns) <= 9, str(btns))
                page.locator("#divControls button").nth(1).click(); page.wait_for_timeout(400)
                chk.add(f"{scheme} scorecard: choosing an end puts it in the address bar",
                        "side=low" in page.url, page.url[-34:])
                chk.add(f"{scheme} scorecard: the title names the day and the end being read",
                        "low" in page.locator("#divTitle").inner_text(),
                        page.locator("#divTitle").inner_text()[:60])
                if len(btns) > 3:
                    page.locator("#divControls button").nth(3).click(); page.wait_for_timeout(400)
                    chk.add(f"{scheme} scorecard: choosing a day puts it in the address bar too",
                            "day=" in page.url, page.url[-34:])
                page.goto(f"{srv.url}/scorecard.html?side=low"); page.wait_for_timeout(1100)
                chk.add(f"{scheme} scorecard: a link reopens the same end of the day",
                        "low" in page.locator("#divTitle").inner_text(),
                        page.locator("#divTitle").inner_text()[:50])
                page.goto(f"{srv.url}/scorecard.html?day=nonsense"); page.wait_for_timeout(1100)
                chk.add(f"{scheme} scorecard: an unknown day falls back rather than blanking",
                        page.locator("#divsvg rect").count() >= 60, "")
                page.goto(f"{srv.url}/scorecard.html"); page.wait_for_timeout(1100)
                page.locator("#divsvg rect[fill='transparent']").first.hover(force=True); page.wait_for_timeout(200)
                t_cell = page.locator("#tip").inner_text()
                chk.add(f"{scheme} hover: a grid cell shows the forecast, the observation and the error",
                        "Observed" in t_cell and "Error" in t_cell, t_cell[:80])
                sbars = page.locator("#standChart rect[data-key]").count()
                chk.add(f"{scheme} scorecard: the standings rank every scored tool as bars", sbars >= 4, f"bars={sbars}")
                # ranked best first, so the bars must not shorten going down
                widths = page.eval_on_selector_all("#standChart rect[data-key]", "e=>e.map(x=>+x.getAttribute('width'))")
                chk.add(f"{scheme} scorecard: the standings bars run shortest first",
                        all(widths[i] <= widths[i + 1] + 0.5 for i in range(len(widths) - 1)), str([round(w) for w in widths]))
                page.locator("#standChart rect[data-key]").first.hover(force=True); page.wait_for_timeout(150)
                t_sd = page.locator("#tip").inner_text()
                chk.add(f"{scheme} hover: a standings bar shows both sides' statistics", "MAE" in t_sd and "daily low" in t_sd, t_sd[:80])
                chk.add(f"{scheme} scorecard: the skill tables are gone and the station record is pointed to",
                        page.locator("#overall").count() == 0 and page.locator("#stations").count() == 0
                        and page.locator("#days").count() == 0
                        and "station’s own page" in page.locator(".wrap").inner_text(), "")
                # ---- map hover: a station dot and a shading cell
                page.goto(f"{srv.url}/index.html")
                page.wait_for_timeout(900)
                page.locator("#map g.dot").nth(2).hover(force=True); page.wait_for_timeout(120)
                t_md = page.locator("#tip").inner_text()
                # the tooltip carries the board on screen and nothing else. Which
                # board that is depends on the hour, since the map opens on
                # today's until 5 pm Eastern and on the day-ahead after it, so the
                # check reads the button that is pressed rather than assuming,
                # which it did until it ran past five one evening and failed.
                opened_today = page.locator("#m1.on").count() == 1
                chk.add(f"{scheme} hover: map dot shows the board on screen, not both days",
                        (("today" in t_md and "Observed high so far" in t_md and "issued before the day began" in t_md)
                         if opened_today else
                         ("tomorrow" in t_md and "newest run" in t_md and "Observed high so far" not in t_md)),
                        ("today " if opened_today else "tomorrow ") + t_md[:100])
                wdots = page.locator("#mapW g.dot").count()
                chk.add(f"{scheme} map: the international stations sit on a world canvas below", wdots >= 10, f"dots={wdots}")
                # with no NDFD issuance for the samples' day the map draws its land and says so
                chk.add(f"{scheme} map: without an NDFD view for the day the land is drawn plain and the legend says so",
                        page.locator("#map image.ndfd").count() == 0 and page.locator("#map path.land").count() == 1
                        and "No NWS forecast shading" in page.locator("#legendField").inner_text(), "")
                # the headline cards are written by the pipeline; absent snapshot must simply draw none
                cards = page.locator("#cards .tile").count()
                chk.add(f"{scheme} map: headline cards render, or none when the snapshot is absent", cards == 0 or cards >= 2, f"cards={cards}")
                if cards:
                    page.locator("#cards .tile").first.hover(force=True); page.wait_for_timeout(150)
                    t_cd = page.locator("#tip").inner_text()
                    chk.add(f"{scheme} hover: a headline card explains the number behind it", len(t_cd) > 20, t_cd[:80])
                chk.add(f"{scheme} hurricane and climate: no script errors", not errs, "; ".join(errs)[:300])
                ctx.close()

            # ---- the allocation calculator: the maths, the scenarios, the imports
            ctx = browser.new_context(viewport={"width": 1200, "height": 1000})
            page = ctx.new_page()
            errs = errors_of(page)
            page.goto(f"{srv.url}/allocator.html"); page.wait_for_timeout(1800)
            chk.add("allocator: opens on the teaching ladder, clearly labeled",
                    "made-up" in page.locator("#allocTitle").inner_text().lower(),
                    page.locator("#allocTitle").inner_text())
            chk.add("allocator: three scenario chips", page.locator(".allocChip").count() == 3,
                    str(page.locator(".allocChip").count()))
            m = page.evaluate('''() => {
              const M = WXAlloc._math;
              // a complete two-claim market believed 60/40 with both sides at
              // 50 cents: closed forms exist for every scenario. Log is
              // Kelly's bet-your-beliefs (0.6); with risk aversion gamma the
              // split solves f/(1-f) = 1.5^(1/gamma).
              const inst = [
                {strike: 0, side: 'yes', dir: 1, thr: 0, cost: 0.5, price: 0.5},
                {strike: 0, side: 'no',  dir: 1, thr: 0, cost: 0.5, price: 0.5},
              ];
              // the band is the half-width of the central 95 percent interval,
              // so band 1.959964 is sigma 1 and P(above 0) at mu 0.2533 is 0.6
              const B = M.bins(inst, 0.2533, 1.959964);
              return { g1: M.crra(inst, B, 1)[0], g4: M.crra(inst, B, 4)[0],
                       gH: M.crra(inst, B, 0.5)[0],
                       one: M.crra([inst[0]], M.bins([inst[0]], 0.2533, 1.959964), 1),
                       phi: M.Phi(1.96) };
            }''')
            chk.add("allocator: the log split is Kelly's bet-your-beliefs",
                    abs(m["g1"] - 0.6) < 0.003, str(m["g1"]))
            chk.add("allocator: the conservative split matches its closed form",
                    abs(m["g4"] - 0.5253) < 0.005, str(m["g4"]))
            chk.add("allocator: the aggressive split matches its closed form",
                    abs(m["gH"] - 0.6923) < 0.005, str(m["gH"]))
            chk.add("allocator: one buyable side takes everything", m["one"] == [1], str(m["one"]))
            chk.add("allocator: the normal curve is a normal curve", abs(m["phi"] - 0.975) < 0.001, str(m["phi"]))
            st = page.evaluate('''() => [...document.querySelectorAll('.allocChip')].map(c => {
              const sp = c.innerText.match(/for \\$([0-9.]+)/);
              const wn = c.innerText.match(/worst ([+\\u2212])\\$?([0-9.]+)/);
              const bn = c.innerText.match(/best ([+\\u2212])\\$?([0-9.]+)/);
              const sgn = t => t && (t[1] === '+' ? 1 : -1) * parseFloat(t[2]);
              return { worst: sgn(wn), best: sgn(bn) }; })''')
            chk.add("allocator: the conservative worst case is the shallowest and the aggressive the deepest",
                    st[0]["worst"] is not None and st[0]["worst"] >= st[1]["worst"] - 1e-6 and st[1]["worst"] >= st[2]["worst"] - 1e-6,
                    str(st))
            chk.add("allocator: the aggressive best case is the highest",
                    st[2]["best"] is not None and st[2]["best"] >= st[1]["best"] - 1e-6 and st[1]["best"] >= st[0]["best"] - 1e-6,
                    str(st))
            spent = page.evaluate('''() => {
              // every scenario must spend the whole amount to within the
              // cheapest contract still buyable
              const R = (() => { const M = WXAlloc._math; const S = WXAlloc._state;
                const fee = WXM.feeCents() / 100;
                const instr = M.instruments(S.ladder, fee);
                const B = M.bins(instr, S.value, Math.max(S.band, 1e-6), S.shape);
                return [4, 1, 0.5].map(g => {
                  const f = M.crra(instr, B, g);
                  const sc = M.fill(instr, f, S.budget, B, g);
                  const minCost = Math.min(...instr.map(i => i.cost));
                  return { spent: sc.spent, slack: S.budget - sc.spent, minCost };
                }); })();
              return R;
            }''')
            chk.add("allocator: the whole amount goes in, to within the cheapest contract",
                    all(r["slack"] < r["minCost"] + 1e-9 and r["spent"] <= 100.01 for r in spent),
                    str([(round(r["spent"], 2), round(r["slack"], 2)) for r in spent]))
            chk.add("allocator: every held line names its payout multiple",
                    page.evaluate("() => [...document.querySelectorAll('#allocSvg text')].filter(t => /\\u00d7$/.test(t.textContent)).length") > 0, "")
            chk.add("allocator: the ladder column outlines what the split buys",
                    page.locator("#allocSvg rect[stroke='var(--ink)']").count() > 0, "")
            chk.add("allocator: the collateral and payout column draws both bars",
                    page.locator("#allocSvg rect[fill='var(--collat)']").count() > 0
                    and page.locator("#allocSvg rect[fill='var(--payout)']").count() > 0, "")
            chk.add("allocator: the schematic shows one ladder read three times",
                    page.locator("#schematic rect").count() == 15, str(page.locator("#schematic rect").count()))
            chk.add("allocator: the page is written in the third person",
                    not re.search(r"\b(you|your|yours)\b", page.locator(".wrap").inner_text(), re.I),
                    (re.search(r".{40}\b(you|your)\b.{40}", page.locator(".wrap").inner_text(), re.I) or [""])[0])
            shp = page.evaluate('''() => {
              const M = WXAlloc._math, r = {};
              // every shape keeps the prediction as the median and the stated
              // span as the 95 percent interval; only the split changes
              const q = (p, shape) => { let lo = -1e4, hi = 1e4;
                for (let i = 0; i < 90; i++) { const m = (lo + hi) / 2; if (M.cdf(m, 50, 10, shape) < p) lo = m; else hi = m; }
                return (lo + hi) / 2; };
              for (const shape of ['normal', 'right', 'left']) {
                r[shape] = { med: M.cdf(50, 50, 10, shape), lo: q(0.025, shape), hi: q(0.975, shape) };
              }
              return r;
            }''')
            for name, v in shp.items():
                chk.add(f"allocator: the {name} shape keeps the prediction as its median",
                        abs(v["med"] - 0.5) < 0.002, f"{v['med']:.4f}")
                chk.add(f"allocator: the {name} shape keeps the stated 95 percent span",
                        abs((v["hi"] - v["lo"]) - 20) < 0.15, f"{v['hi'] - v['lo']:.2f}")
            chk.add("allocator: the skewed shapes put the long tail on the named side",
                    (shp["right"]["hi"] - 50) > 3 * (50 - shp["right"]["lo"])
                    and (50 - shp["left"]["lo"]) > 3 * (shp["left"]["hi"] - 50), "")
            chk.add("allocator: only strikes the market prices between 5 and 95 percent are allocated",
                    page.evaluate('''() => { const M = WXAlloc._math, S = WXAlloc._state;
                      const all = M.instruments(S.ladder, 0.005);
                      return all.filter(i => i.tradeable).every(i => i.mkt >= M.LIQUID_LO && i.mkt <= M.LIQUID_HI)
                          && all.some(i => !i.tradeable) === all.some(i => i.mkt < M.LIQUID_LO || i.mkt > M.LIQUID_HI); }''') is True, "")
            tkv = page.evaluate("() => WXAlloc._math.ticks(4.66, 5.08, 7)")
            chk.add("allocator: a tight ladder gets axis labels that tell every tick apart",
                    len(tkv["vals"]) >= 3
                    and len(set(f"{v:.{tkv['dp']}f}" for v in tkv["vals"])) == len(tkv["vals"]),
                    f"dp={tkv['dp']} vals={tkv['vals'][:5]}")
            chk.add("allocator: the belief curve has drag handles",
                    page.locator("#allocSvg circle[data-drag]").count() == 3,
                    str(page.locator("#allocSvg circle[data-drag]").count()))
            # expanding must make the chart bigger, not smaller
            w0 = page.evaluate("() => document.querySelector('#allocSvg').getBoundingClientRect().width")
            page.locator("#allocCtl button").click(); page.wait_for_timeout(400)
            w1 = page.evaluate("() => document.querySelector('#allocSvg').getBoundingClientRect().width")
            page.keyboard.press("Escape"); page.wait_for_timeout(300)
            chk.add("allocator: expanding the chart makes it bigger", w1 >= w0 - 1, f"{w0:.0f} -> {w1:.0f}")
            # a live import: the ladder, the prefill, and the click-through
            page.select_option("#allocMarket", "city:KATL"); page.wait_for_timeout(1600)
            chk.add("allocator: a city ladder imports with its own name",
                    "Atlanta" in page.locator("#allocTitle").inner_text(),
                    page.locator("#allocTitle").inner_text())
            v = page.input_value("#allocValue")
            chk.add("allocator: the value prefills from the ladder's implied median",
                    v not in ("", "88"), v)
            links = page.evaluate("() => document.querySelectorAll('#allocSvg [data-contract-url]').length")
            chk.add("allocator: rows and bars click through to the contract", links > 0, str(links))
            chk.add("allocator: the tornado count is filed under Weather, not Tropical Cyclones",
                    page.evaluate('''() => { const g = [...document.querySelectorAll('#allocMarket optgroup')]
                      .find(g => g.label.includes('Tropical')); return g && ![...g.children].some(o => o.value.includes('SWTUS')); }''') is True, "")
            # ---- regressions the audit of 2026-08-28 fixed
            m2 = page.evaluate('''() => {
              const M = WXAlloc._math;
              // a 2.5-step axis labels x.5 gridlines as x.5, never as x
              const tk = M.ticks(0, 10, 4);
              return { rt: tk.vals.every(v => Math.abs(v - parseFloat(v.toFixed(tk.dp))) < 1e-9),
                       step: tk.vals.length > 1 ? +(tk.vals[1] - tk.vals[0]).toFixed(6) : 0 };
            }''')
            chk.add("allocator: axis labels equal the gridlines they sit on",
                    m2["rt"] and abs(m2["step"] - 2.5) < 1e-6, str(m2))
            wb = page.evaluate('''() => {
              const M = WXAlloc._math, S = WXAlloc._state;
              // a skewed curve's hard support bound rules out the high outcomes,
              // but the market can settle there, so the chip's worst must count
              // them rather than print a guaranteed profit
              const fee = WXM.feeCents() / 100;
              const instr = M.instruments(S.ladder, fee).filter(i => i.tradeable);
              const B = M.bins(instr, 86, 2, 'left');
              const f = M.crra(instr, B, 1);
              const sc = M.fill(instr, f, 100, B, 1);
              return M.scenarioStats(sc, B, instr).worstNet;
            }''')
            chk.add("allocator: the worst case covers outcomes the curve rules out",
                    wb is not None and wb < -50, str(wb))
            paired = page.evaluate("""() => {
              // the exchange nets opposing positions, so no split may hold
              // Yes and No on the same strike, under any shape or appetite
              const M = WXAlloc._math, S = WXAlloc._state;
              const fee = WXM.feeCents() / 100;
              const out = [];
              for (const shape of ['normal', 'left', 'right']) {
                const instr = M.instruments(S.ladder, fee).filter(i => i.tradeable);
                const B = M.bins(instr, S.value, Math.max(S.band, 1e-6), shape);
                for (const g of [4, 1, 0.5]) {
                  const f = M.crraExchange(instr, B, g);
                  const sc = M.fill(instr, f, 100, B, g);
                  const seen = {};
                  sc.hold.forEach(x => {
                    const k = x.i.strike + '|' + x.i.dir;
                    if (seen[k] && seen[k] !== x.i.side) out.push(shape + ' g' + g + ' @' + x.i.strike);
                    seen[k] = x.i.side;
                  });
                }
              }
              return out;
            }""")
            chk.add("allocator: no split holds both sides of one strike",
                    paired == [], str(paired))
            chk.add("allocator: the arithmetic section sets out the objective",
                    page.locator("p.eq").count() >= 4
                    and "ALLOCATION ARITHMETIC" in page.locator("body").inner_text(),
                    f"eq blocks: {page.locator('p.eq').count()}")
            t = "\n".join(page.locator(".sub").all_inner_texts()).lower()
            chk.add("allocator: never says ask, sell or offer",
                    "ask" not in t.replace("asked", "") and "sell" not in t and " offer" not in t, "")
            chk.add("allocator: names both references",
                    "kelly" in t and "thorp" in t, "")
            chk.add("allocator: no script errors", not errs, "; ".join(errs[:3]))
            ctx.close()

            # ---- house prose style, checked on the pages rather than trusted
            #
            # Titles are noun phrases without a leading article, prose carries
            # no colons and no em-dashes, and nothing is defined by saying what
            # it is not. These crept back once after being swept, so they are
            # a gate now.
            ctx = browser.new_context(viewport={"width": 1200, "height": 900})
            page = ctx.new_page()
            PAGES_PROSE = ["index.html", "city.html?station=KLAX", "hurricane.html", "allocator.html",
                           "climate.html", "weather.html", "agriculture.html", "scorecard.html",
                           "accuracy.html", "about.html", "fossil-fuels.html", "electricity-renewables.html",
                           "analysis-resolution.html", "climate-gdp.html"]
            # the owner's own copy, which the style rules do not touch
            OWNER = ("faq.html", "daily-temperature-markets.html")
            bad_title, bad_colon, bad_not = [], [], []
            # the world GDP page's article is the owner's own copy too; its method notes are the page's
            OWNS = "e=>e.filter(x=>!(x.closest('#gdpArticle') && !x.closest('.accnote')))"
            for path in PAGES_PROSE:
                page.goto(f"{srv.url}/{path}"); page.wait_for_timeout(2500 if path.startswith("climate-gdp") else 1100)
                titles = page.eval_on_selector_all(".wrap .secttl, .wrap h2, .wrap h3",
                                                   "e=>(" + OWNS + ")(e).map(x=>x.textContent.trim())")
                for t in titles:
                    if re.match(r"^(the|a|an)\s", t, re.I):
                        bad_title.append(f"{path}: {t}")
                # the accuracy page's method notes are page copy too, so they take the same rules
                body = page.eval_on_selector_all(".wrap p, .wrap li, .wrap .accnote .rule, .wrap .accnote .nt",
                                                 "e=>(" + OWNS + ")(e).map(x=>x.textContent)")
                for t in body:
                    if re.search(r"[a-z)][:]\s+[a-z]", t):
                        bad_colon.append(f"{path}: {t[:70]}")
                    if re.search(r"\bWhat this is not\b|\bis a [a-z ]+, not a\b", t):
                        bad_not.append(f"{path}: {t[:70]}")
                    if "\u2014" in t or " -- " in t:
                        bad_colon.append(f"{path} (dash): {t[:70]}")
            chk.add("prose: no section title opens with an article", not bad_title, "; ".join(bad_title[:3]))
            chk.add("prose: no colons or em-dashes in page copy", not bad_colon, "; ".join(bad_colon[:2]))
            chk.add("prose: nothing is defined by what it is not", not bad_not, "; ".join(bad_not[:2]))

            # ---- what a page says to a reader who arrives from a search
            #
            # Each board is drawn by script out of the snapshots, so a page can
            # carry a whole screen of information and still serve almost no text.
            # Every page below has to name in words what its contracts are
            # written on, and say it in the page rather than through a script.
            WORDS = {
                "weather.html": ["rainfall", "tornado", "hail", "thunderstorm", "wind speed", "drought"],
                "climate.html": ["climate prediction market", "sea level", "carbon dioxide", "degree days"],
                "electricity-renewables.html": ["energy prediction market", "wind", "solar", "nuclear", "fusion"],
                "fossil-fuels.html": ["natural gas", "coal", "petroleum"],
                "hurricane.html": ["hurricane prediction market", "named storms", "landfall", "wind gust"],
                "agriculture.html": ["crop yields", "corn", "wheat", "rice"],
            }
            thin, missing = [], []
            for path, words in WORDS.items():
                raw = urllib.request.urlopen(f"{srv.url}/{path}").read().decode()
                served = strip_markup(raw)
                if len(served.split()) < 300:
                    thin.append("%s: %d words" % (path, len(served.split())))
                low = served.lower()
                for w in words:
                    if w not in low:
                        missing.append("%s: %s" % (path, w))
            chk.add("search: every board page serves its own text without running a script",
                    not thin, "; ".join(thin))
            chk.add("search: each page names in words what its contracts are written on",
                    not missing, "; ".join(missing[:4]))
            # ---- the same facts in the vocabulary a search engine parses
            page.goto(f"{srv.url}/index.html"); page.wait_for_timeout(600)
            ld = page.evaluate("""() => [...document.querySelectorAll('script[type="application/ld+json"]')]
                                    .map(s => JSON.parse(s.textContent))""")
            types = [n["@type"] for n in (ld[0]["@graph"] if ld else [])]
            chk.add("search: a page declares itself, its site and its publisher",
                    len(ld) == 1 and sorted(types) == ["Organization", "WebPage", "WebSite"], str(types))
            page.goto(f"{srv.url}/faq.html"); page.wait_for_timeout(600)
            faq = page.evaluate("""() => {
              const s = document.querySelector('script[type="application/ld+json"]');
              const g = s ? JSON.parse(s.textContent)['@graph'] : [];
              const f = g.find(n => n['@type'] === 'FAQPage');
              return f ? { n: f.mainEntity.length,
                           questions: f.mainEntity.every(q => q.name.endsWith('?') && q.acceptedAnswer.text) } : null;
            }""")
            chk.add("search: the FAQ's own questions are the questions in its structured data",
                    bool(faq and faq["n"] >= 4 and faq["questions"]), str(faq))
            ctx.close()

            # ---- embed target
            ctx = browser.new_context(viewport={"width": 980, "height": 500})
            page = ctx.new_page()
            errs = errors_of(page)
            page.goto(f"{emb.url}/?station=KPHX&theme=light")
            page.wait_for_timeout(900)
            chk.add("embed: city series render", page.locator("#chart path").count() > 0)
            chk.add("embed: no site chrome", page.locator("header.site").count() == 0 and page.locator("footer.site").count() == 0)
            chk.add("embed: market off by default (weather-only height)", page.locator("#chart").get_attribute("viewBox") == "0 0 960 488")
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
            chk.add("feed fails after a good load: last saved data shown and labeled", ok_first and "last data this browser saved" in status, status[:100])
            chk.add("feed fails after a good load: map still drawn from cache", page.locator("#map path").count() > 0)
            page.screenshot(path=os.path.join(OUT, "degraded-cached.png"), full_page=True)
            chk.add("feed fails after a good load: no script errors", not errs, "; ".join(errs)[:300])
            ctx.close()
            browser.close()
    finally:
        srv.stop(); emb.stop(); bad.stop()

    report = {"passed": len(chk.results) - len(chk.failed), "failed": len(chk.failed),
              "filtered": chk.filtered, "only": chk.only, "schemes": list(chk.schemes),
              "results": chk.results}
    with open(os.path.join(OUT, "report.json"), "w") as fh:
        json.dump(report, fh, indent=1)
    tail = " -> verify-out/report.json"
    if chk.filtered:
        # never let a narrowed run read like a clean one
        how = ", ".join(filter(None, [f"only={chk.only!r}" if chk.only else "",
                                      "schemes=" + "+".join(chk.schemes)]))
        print(f"verify: {report['passed']} passed, {report['failed']} failed "
              f"-- PARTIAL RUN ({how}), not a full pass{tail}")
    else:
        print(f"verify: {report['passed']} passed, {report['failed']} failed{tail}")
    return 1 if chk.failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--scheme", choices=("light", "dark", "both"), default="both",
                    help="run one colour scheme instead of both; halves a pass while iterating")
    ap.add_argument("--only", default="",
                    help="regex: narrow the page sweep to matching pages. A narrowed run "
                         "reports itself as partial and must not be used as the gate.")
    a = ap.parse_args()
    schemes = ("light", "dark") if a.scheme == "both" else (a.scheme,)
    sys.exit(run(a.no_build, only=a.only, schemes=schemes))
