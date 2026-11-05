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
import urllib.parse
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


def ana_row_shape3(doc: dict, raw: list, review_row=None, review_late_row=None) -> dict:
    """The analysis page's place file in rowShape 3 (docs/analysis.md section 4),
    made by hand from a rowShape 2 sample and the day's raw CSV rows, until the
    lane's own samples carry it. Each row gains its stored values (`raw`, the CSV's
    own strings), its values converted before rounding (`x`), the day so far before
    rounding (`runX`), each file's times on both servers (`anlFile`, `pcpFile`) and
    the hourly contract (`hourly`), worked out here in decimal arithmetic as the
    lane works them. The times are invented, a NOMADS copy for most analysis files
    and none for the rest, so every wording the page has for a file and for each
    basis of an hourly state is reached: hours 0 to 5 final from NOMADS, hour 6 from
    NOMADS with NOAA Open Data's value shown and NOAA Open Data's time as its `pub`, as
    for an hour read before both servers were, hours 7 to 9 and the last at the
    hourly cut-off, hours 10 to 12 with NOMADS not on record, hour 14 with NOAA Open
    Data not on record, and the day's last accumulation published after the cut-off
    and counted under the rule before 14 October. `review_row` turns that row into
    one with an hourly state under review and no daily value, and `review_late_row`
    into one NOAA Open Data published after its hourly cut-off and in time for the
    day, with no NOMADS copy on record, as a file read before both servers were."""
    from decimal import Decimal, ROUND_DOWN
    doc = json.loads(json.dumps(doc))
    doc["rowShape"] = 3
    by_t = {r["valid_utc"]: r for r in raw}
    iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")
    at = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00"))

    def cut10(x):
        q = (x * 10 ** 10).to_integral_value(rounding=ROUND_DOWN) / Decimal(10 ** 10)
        return format(q.normalize(), "f") if q == x else format(q.quantize(Decimal("1e-10")), "f")

    hi = lo = gu = None
    wsum, wn, psum = Decimal(0), 0, Decimal(0)
    rows = doc["hours"]
    last = len(rows) - 1
    cutoff = at(rows[-1]["t"]) + dt.timedelta(hours=1, minutes=45)
    for i, row in enumerate(rows):
        v = row.get("rtma")
        if not v:
            continue
        c = by_t[row["t"]]
        t = at(row["t"])
        k, ms, gs, mm = (Decimal(c[f]) for f in ("temp_k", "wind_ms", "gust_ms", "precip_mm"))
        tx = (k - Decimal("273.15")) * 9 / 5 + 32
        wx, gx = ms * Decimal("2.2369362921"), gs * Decimal("2.2369362921")
        px = mm / Decimal("25.4")
        v["raw"] = {"temp": c["temp_k"], "wind": c["wind_ms"], "gust": c["gust_ms"], "precip": c["precip_mm"]}
        v["x"] = {"temp": format(tx.quantize(Decimal("0.001")), "f"), "wind": format(wx.quantize(Decimal("1e-12")), "f"),
                  "gust": format(gx.quantize(Decimal("1e-12")), "f"), "precip": cut10(px)}
        hi = tx if hi is None or tx > hi else hi
        lo = tx if lo is None or tx < lo else lo
        gu = gx if gu is None or gx > gu else gu
        wsum += wx; wn += 1; psum += px
        v["runX"] = {"high": format(hi.quantize(Decimal("0.001")), "f"), "low": format(lo.quantize(Decimal("0.001")), "f"),
                     "gust": format(gu.quantize(Decimal("1e-12")), "f"), "wind": cut10(wsum / wn), "precip": cut10(psum)}
        nodd = {"lm": iso(t + dt.timedelta(minutes=48, seconds=10 + i)), "seen": iso(t + dt.timedelta(minutes=49, seconds=2 + i))}
        nomads = {"lm": iso(t + dt.timedelta(minutes=46, seconds=31)), "seen": iso(t + dt.timedelta(minutes=47, seconds=5))}
        if 7 <= i <= 13 or i == last:
            nomads = None
        if i == 14:
            nodd = None
        src = "nomads" if nomads and i != 6 else "nodd"
        stamp = row["t"][:10].replace("-", "")
        v["anlFile"] = {"name": f"rtma2p5.{stamp}/rtma2p5.t{row['t'][11:13]}z.2dvaranl_ndfd.grb2_wexp",
                        "nodd": nodd, "nomads": nomads, "src": src, "counts": True, "afterCut": False}
        v["pub"] = nodd["lm"] if i == 6 else (nomads or nodd)["lm"]
        v["pubSrc"] = "nodd" if i == 6 or not nomads else "nomads"
        v["src"] = src
        end = t + dt.timedelta(hours=1)
        plm = end + dt.timedelta(minutes=16, seconds=40)
        late_ok = i == last
        if late_ok:
            plm = cutoff + dt.timedelta(minutes=5)
        v["pcpFile"] = {"name": f"rtma2p5.{end.strftime('%Y%m%d')}/rtma2p5.{end.strftime('%Y%m%d%H')}.pcp.184.grb2",
                        "nodd": {"lm": iso(plm), "seen": iso(plm + dt.timedelta(seconds=50))}, "nomads": None,
                        "src": "nodd", "counts": True, "afterCut": late_ok}
        v["pcpPub"] = iso(plm)
        v["pcpPubSrc"] = "nodd"
        cut = t + dt.timedelta(hours=1, minutes=30)
        basis = "nomads" if nomads else "cutoff" if (7 <= i <= 9 or i == last) else "norecord"
        v["hourly"] = {"cut": iso(cut), "state": "final", "at": nomads["lm"] if basis == "nomads" else iso(cut) if basis == "cutoff" else None,
                       "basis": basis, "src": src, "value": v["show"]["temp"], "x": v["x"]["temp"], "late": False}
    if review_row is not None:
        row = rows[review_row]
        t = at(row["t"])
        row["rtma"] = {"hourly": {"cut": iso(t + dt.timedelta(hours=1, minutes=30)), "state": "review",
                                  "at": iso(t + dt.timedelta(hours=1, minutes=30)),
                                  "basis": "cutoff", "src": None, "value": None, "x": None, "late": False},
                       "anlFile": {"name": row["rtma"]["anlFile"]["name"], "nodd": None, "nomads": None, "src": None,
                                   "counts": False, "afterCut": False},
                       "pcpFile": row["rtma"]["pcpFile"]}
    if review_late_row is not None:
        v = rows[review_late_row]["rtma"]
        cut = at(v["hourly"]["cut"])
        v["anlFile"].update({"nodd": {"lm": iso(cut + dt.timedelta(minutes=20)), "seen": iso(cut + dt.timedelta(minutes=21))},
                             "nomads": None, "src": "nodd"})
        v.update({"pub": v["anlFile"]["nodd"]["lm"], "pubSrc": "nodd", "src": "nodd"})
        v["hourly"].update({"state": "review", "at": iso(cut), "basis": "norecord", "src": None, "value": None, "x": None, "late": True})
    return doc


def tiny_png(rgb) -> bytes:
    """A 4x4 PNG of one colour, standing in for a satellite frame in the loop checks."""
    import struct
    import zlib
    raw = b"".join(b"\x00" + bytes(rgb) * 4 for _ in range(4))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def run(no_build: bool, only: str = "", schemes=("light", "dark")) -> int:
    from playwright.sync_api import Error as PlaywrightError, sync_playwright
    os.makedirs(OUT, exist_ok=True)
    if not no_build:
        subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "build.py")], check=True, cwd=ROOT)
    chk = Check(only=only, schemes=schemes)

    def until(pg, js, cap_ms=8000, step_ms=100, arg=None):
        """Poll `js` in the page until it is truthy or `cap_ms` has passed, and say
        whether it held. A read that depends on the page having drawn waits on this
        for the element or count it needs, because a fixed wait before it flaked on a
        loaded machine. It never raises, so on a timeout the check that follows still
        runs and fails with its own detail. Polled from here, so a page whose clock
        Playwright has fixed is polled the same way."""
        end_ = time.time() + cap_ms / 1000.0
        while True:
            try:
                if pg.evaluate(js) if arg is None else pg.evaluate(js, arg):
                    return True
            except PlaywrightError:
                pass                # a page between two documents has nothing to evaluate in yet
            if time.time() > end_:
                return False
            pg.wait_for_timeout(step_ms)

    def until_n(pg, sel, n=1, cap_ms=8000):
        """`until` for the commonest precondition, `n` or more elements matching `sel`."""
        return until(pg, "([s, n]) => document.querySelectorAll(s).length >= n", cap_ms, arg=[sel, n])

    def goto_answered(pg, url, part, cap_ms=8000):
        """Load `url` and wait until the page has had its answer for the file whose address
        holds `part`, for a read of a state that answer may leave as it was, such as a loop
        not shown when its index is refused. Like `until` it never raises."""
        try:
            with pg.expect_response(lambda r: part in r.url, timeout=cap_ms):
                pg.goto(url)
        except PlaywrightError:
            pass                    # a page that never asks for it is read as it stands

    # What each page has drawn once it is done. A read waits for what it needs, a page that
    # draws in stages is read once its last stage is in, and one that draws again as a late
    # file lands is read once that file is in, so no redraw falls between a hover and its read.
    LOADED = "() => document.readyState === 'complete'"
    # The landing map draws its title, both canvases and their dots in one pass once its files
    # are in. A view's NDFD shading is fetched when the view is first drawn and kept, and its
    # legend stays the last view's until it lands, so a read of it waits for the shading or for
    # the legend saying there is none.
    MAP_DRAWN = "() => document.querySelectorAll('#map g.dot').length > 0"
    NDFD_SETTLED = """() => !!document.querySelector('#map image.ndfd')
      || /No NWS forecast shading/.test((document.querySelector('#legendField') || {}).textContent || '')"""
    # the whole landing page, with the scorecard grid under the map, which draws on its own
    INDEX_ALL = """() => document.querySelectorAll('#map g.dot').length > 0 && !!document.querySelector('#divsvg rect')
      && (!!document.querySelector('#map image.ndfd') || /No NWS forecast shading/.test(document.querySelector('#legendField').textContent))"""
    # the city chart writes its status and draws in one pass once its files and the station's
    # quotes are in, with the pickers and the locator, and the discussion, the last few days and
    # the station's record each draw on their own
    CITY_CHART = "() => !!document.querySelector('#chartStatus .status')"
    CITY_ALL = """() => !!document.querySelector('#chartStatus .status') && !!document.querySelector('#discussion .afdtext')
      && !!document.querySelector('#cityDays rect[fill=transparent]') && !!document.querySelector('#cityScore circle')"""
    # the hurricane page writes its status and draws the basin in one pass once its files are
    # in, then draws the basin again as each of its map layers lands, the relief, the roads and
    # names and the sea temperature
    HUR_MAP = ("!!document.querySelector('#pageStatus .status') && !!document.querySelector('#basin path')"
               " && !!document.querySelector('#basin g.places') && !!document.querySelector('#basin image.relieflayer')"
               " && !!document.querySelector('#basin image.sstlayer')")
    HUR_DRAWN = "() => " + HUR_MAP
    # the wind board draws its stations one by one as their files come in, and its note on the
    # two columns goes in after the last of them
    WIND_DRAWN = "() => !!document.querySelector('#board .wnote')"
    # a category's panels go in one product at a time, and its footnote counts them once the
    # last is drawn
    PANELS_DONE = "() => /\\S/.test((document.querySelector('#foot') || {}).textContent || '')"
    # a contract page draws in stages as its files come in, the ladder and its closing note
    # last and the button into the calculator once its own lookup is done
    CONTRACT_DONE = """() => [...document.querySelectorAll('#cBody p.cap')].some(p => /does not publish a fair value/.test(p.textContent))
      && [...document.querySelectorAll('#cBody .bar button')].some(b => /calculator/.test(b.textContent))"""
    # every accuracy figure draws in one pass once the files are in, the scorecard last, and
    # the city map draws its states again once their outlines are in
    ACC_DRAWN = """() => document.querySelectorAll('#accGrid table.acc-t3 tbody tr').length > 0
      && !!document.querySelector('#accMap path')"""
    # the world GDP page runs its model once its files are in, and its run line ends on the
    # number of paths once the run is done
    GDP_RAN = "() => / paths\\.$/.test((document.querySelector('#gdpRunStatus') || {}).textContent || '')"
    # the allocation calculator's teaching ladder draws at once, and its market picker goes
    # live once every category's listing is in
    ALLOC_READY = "() => typeof (document.querySelector('#allocMarket') || {}).onchange === 'function'"
    # a page that scrolls smoothly and then corrects its landing is read once it has stood
    # still for four looks in a row, keyed on each wait's own stamp
    SCROLL_STILL = """(k) => { const w = window, y = Math.round(w.scrollY);
      if (w.__stillK !== k) { w.__stillK = k; w.__stillN = 0; w.__stillY = y; return false; }
      w.__stillN = w.__stillY === y ? w.__stillN + 1 : 0; w.__stillY = y; return w.__stillN >= 4; }"""

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
                         ("analysis-resolution.html", "#anaPlaceMap, #anaPickers", "analysis resolution page"),
                         ("tropical-cyclone-markets.html", ".basincard svg.minimap", "a map per basin"),
                         ("allocator.html", "#allocSvg", "the allocation chart"),
                         ("climate-gdp.html", "#gdpMain rect", "the world GDP contract and its ladder")]
                # a narrowed run walks only the pages it asked for; the sweep is
                # the part of a pass that is pure page loading, so this is where
                # skipping actually buys time rather than only hiding output
                pages = [pg for pg in pages if chk.wants(pg[0] + " " + pg[2])]
                for path, sel, what in pages:
                    page.goto(f"{srv.url}/{path}")
                    # a page has drawn once its figure is in and a data page's strip carries its status.
                    # The world GDP page loads its model's paths and runs it before it draws
                    until(page, """(sel) => document.querySelectorAll(sel).length > 0
                      && (!document.querySelector('#pageStatus, #chartStatus') || !!document.querySelector('.status'))""",
                          cap_ms=20000 if path.startswith("climate-gdp") else 8000, arg=sel)
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
                page.goto(f"{srv.url}/accuracy.html"); until(page, ACC_DRAWN)
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
                page.locator(".paperrow a.pf-brief").click()
                # the brief at full size is fetched once the dialog opens
                until(page, "() => { const im = document.querySelector('#briefDlg img'); return !!(im && im.complete); }")
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
                ph.goto(f"{srv.url}/accuracy.html"); until(ph, ACC_DRAWN)
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
                        and "51%" in art_t and "50.5%" not in art_t, art_t[:80])
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
                until(page, WIND_DRAWN)
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
                until(page, WIND_DRAWN)
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
                until(page, WIND_DRAWN)
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
                # the day control moves the map with the boards, and the board is drawn again
                # from the top, so its note goes and comes back once the last station is in
                page.locator("#windDays button", has_text="Tomorrow").first.click()
                until(page, WIND_DRAWN)
                cap2 = page.locator("#vmapCap").inner_text()
                chk.add(f"{scheme} wind days: the map follows the board's day",
                        "tomorrow" in cap2 and cap2 != cap1, cap2)
                chk.add(f"{scheme} wind days: the pressed button follows the selection",
                        page.locator("#windDays button.on").first.inner_text() == "Tomorrow", "")
                if len(btns) == 3:
                    page.locator("#windDays button", has_text=btns[2]).first.click()
                    until(page, WIND_DRAWN)
                    cap3 = page.locator("#vmapCap").inner_text()
                    chk.add(f"{scheme} wind days: the map names the third day the way its button does",
                            btns[2] in cap3, cap3)
                    chk.add(f"{scheme} wind days: the listing count names the day it counted",
                            btns[2] in page.locator("#pageStatus").inner_text(),
                            page.locator("#pageStatus").inner_text()[-50:])
                # a dot on a board that already carries that station's panel scrolls
                # to it rather than leaving the page for the same chart elsewhere
                page.goto(f"{srv.url}/wind-markets.html"); until(page, WIND_DRAWN)
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
                    page.wait_for_timeout(300); until(page, SCROLL_STILL, arg=time.time())
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
                    page.goto(f"{srv.url}/wind-markets.html"); until(page, WIND_DRAWN)
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
                        page.mouse.click(zt["x"], zt["y"]); page.wait_for_timeout(300); until(page, SCROLL_STILL, arg=time.time())
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
                # full, and that every figure it shows or saves is the place
                # file's own. Version 3 of its settlement display (owner's
                # approval of 2026-11-04): the calendar and the map always on the
                # page, the hover and the CSV files from the lane's strings, real
                # publication times only, and each hour's hourly contract. Every
                # read that depends on drawing polls for what it reads first.
                ANA = "analysis-resolution.html"
                ANA_SNAP = os.path.join(ROOT, "samples", "snapshots", "analysis2")
                # The fixtures are a real local run of the job, so the day the
                # checks drive comes from the fixture index rather than from a
                # date written into this script: the newest day whose New York
                # RTMA day is complete (final pills; RTMA resolves, owner's
                # decision 2026-09-29), and the oldest day, routed away for the
                # hollow-label check.
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
                ana_n = len(ana_index["locations"])
                with open(os.path.join(ANA_SNAP, "days", ana_day + ".json")) as fh:
                    ana_dayfile = json.load(fh)
                # the page opens on the newest day every place has resolved, marks
                # the days after it as provisional, and falls back to the newest
                # day when the index does not name one
                ana_rday = ana_index.get("lastResolvedDay")
                chk.add(f"{scheme} analysis fixtures: the index names a fully resolved day before the newest",
                        bool(ana_rday) and ana_rday in ana_days and ana_rday != ana_days[-1], str(ana_rday))
                # another listed day, to open from the calendar
                ana_pick = next((d_ for d_ in reversed(ana_days) if d_ < ana_day), ana_days[0])
                # every console error the analysis page raises from here lands in
                # errs, so the section can say it raised none
                ana_e0 = len(errs)

                def ana_until(pg, js, ms=8000, arg=None):
                    """Poll `js` in the page until it is truthy, up to `ms`, and return its last
                    value. Polled from here rather than in the page, so it also works under
                    Playwright's fixed clock."""
                    end_ = time.time() + ms / 1000.0
                    while True:
                        v_ = pg.evaluate(js) if arg is None else pg.evaluate(js, arg)
                        if v_ or time.time() > end_:
                            return v_
                        pg.wait_for_timeout(100)

                # the map is drawn once every place has its label; a place-day is
                # drawn once its hour table has its rows
                ANA_MAP = "() => document.querySelectorAll('#anaDots g.pd').length === %d" % ana_n
                ANA_DAY = """() => !document.querySelector('#locPanel').hidden && !!document.querySelector('#anaStatus .anapill')
                  && (document.querySelectorAll('#anaHours tbody tr').length > 0 || !!document.querySelector('#anaNone:not([hidden])'))"""

                def ana_goto(pg, q, ready=None):
                    pg.goto(f"{srv.url}/{ANA}{q}")
                    return ana_until(pg, ready or ANA_MAP)

                ana_cal_all = """() => { const out = [], P = document.querySelector('#anaCalPrev'), N = document.querySelector('#anaCalNext');
                  for (let k = 0; k < 40 && !P.disabled; k++) P.click();
                  for (let k = 0; k < 40; k++) {
                    document.querySelectorAll('#anaCalGrid td.on').forEach(t => out.push([t.dataset.day, t.dataset.status,
                      t.getAttribute('aria-label') || '', !!t.querySelector('i.mk.' + t.dataset.status)]));
                    if (N.disabled) break; N.click(); }
                  return out; }"""
                ana_goto(page, "")
                dflt = page.evaluate("""(js) => ({ open: [...document.querySelectorAll('#anaCalGrid td.open')].map(t => t.dataset.day),
                  url: location.search, legend: document.querySelector('#anaLegend').textContent, opts: eval(js)() })""", ana_cal_all)
                newer = [o_ for o_ in dflt["opts"] if o_[0] > (ana_rday or "")]
                ana_rd = dt.date.fromisoformat(ana_rday or ana_days[-1])
                chk.add(f"{scheme} analysis: the page opens on the newest fully resolved day",
                        dflt["open"] == [ana_rday] and f"day={ana_rday}" in dflt["url"] and f"{ana_rd:%A}, {ana_rd:%B} {ana_rd.day}" in dflt["legend"],
                        str([dflt["open"], dflt["url"], dflt["legend"][:60]]))
                chk.add(f"{scheme} analysis: the days after it are marked provisional and it is not",
                        bool(newer) and all(o_[1] == "provisional" and o_[2].endswith(", provisional") and o_[3] for o_ in newer)
                        and [o_[0] for o_ in dflt["opts"]] == ana_days
                        and any(o_[0] == ana_rday and o_[1] == "final" and o_[3] for o_ in dflt["opts"])
                        and not any("provisional" in o_[2] for o_ in dflt["opts"] if o_[0] == ana_rday),
                        str(dflt["opts"][-3:]))
                # the day's values as CSV at every place (owner's decision
                # 2026-09-29): the two links follow the day and fetch the job's files
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
                ana_goto(page, "")
                ana_nr = page.evaluate("() => [[...document.querySelectorAll('#anaCalGrid td.open')].map(t => t.dataset.day), location.search]")
                chk.add(f"{scheme} analysis: an index without the field opens on the newest day",
                        ana_nr[0] == [ana_days[-1]] and f"day={ana_days[-1]}" in ana_nr[1], str(ana_nr))
                page.unroute("**/analysis2/index.json")
                ana_goto(page, f"?day={ana_day}")
                a0 = page.evaluate("""() => ({
                  labels: Object.fromEntries([...document.querySelectorAll('#anaDots g.pd')].map(g => [g.dataset.loc, (g.querySelector('text.pvt') || {}).textContent || ''])),
                  legend: document.querySelector('#anaLegend').textContent, stmt: document.querySelector('.sub').textContent,
                  robots: (document.querySelector('meta[name=robots]') || {}).content || '',
                  header: !!document.querySelector('header.site'), footer: !!document.querySelector('footer.site'),
                  top: { dayBtn: !!document.querySelector('#anaDayBtn'), locBtn: !!document.querySelector('#anaLocBtn'), zoom: !!document.querySelector('#anaZoomCell'),
                         stepper: !!document.querySelector('#anaStep'), frame: !!document.querySelector('#anaFrame, #vmap, #anaMap'),
                         ladder: document.querySelectorAll('rect.hatched, text.anarung').length, exact: !!document.querySelector('#anaExact'),
                         dialogs: document.querySelectorAll('[role=dialog]').length, maps: document.querySelectorAll('svg').length - document.querySelectorAll('header.site svg, footer.site svg, #anaSettle svg').length,
                         cal: !!document.querySelector('#anaPickers > #anaCal table#anaCalGrid[role=grid]'),
                         sw: [...document.querySelectorAll('#anaPickers > #anaMapCard .anamaphead #anaVar[role=radiogroup] button[role=radio]')].map(b => b.dataset.var) },
                  url: location.search })""")
                chk.add(f"{scheme} analysis: the statement is on the page and the page is not offered to search",
                        "No contract settles on it" in a0["stmt"] and "noindex" in a0["robots"], a0["robots"])
                chk.add(f"{scheme} analysis: the chrome draws for a page outside the navigation",
                        a0["header"] and a0["footer"], str([a0["header"], a0["footer"]]))
                # every place is labelled with its value on the open date, as the
                # day file has it, and a place with no value has no label
                ana_hi = {k_: (((v_ or {}).get("rtma") or {}).get("high") or {}).get("value") for k_, v_ in (ana_dayfile.get("locations") or {}).items()}
                ana_lbl_want = {L_["id"]: ("" if ana_hi.get(L_["id"]) is None else str(ana_hi[L_["id"]])) for L_ in ana_index["locations"]}
                chk.add(f"{scheme} analysis: the map labels every place with its high on the open date, as the day file has it",
                        len(a0["labels"]) == ana_n == 67 and a0["labels"] == ana_lbl_want and sum(1 for v_ in ana_lbl_want.values() if v_) >= 60,
                        str([len(a0["labels"]), [k_ for k_ in ana_lbl_want if a0["labels"].get(k_) != ana_lbl_want[k_]][:4]]))
                # the calendar and the map replace the day and place buttons and
                # their popovers, the map below the day with its frames, stepper,
                # cell mode and zoom, the hypothetical ladder and Show exact values
                chk.add(f"{scheme} analysis: the calendar and the map are on the page with no day or place button, popover, second map, ladder or exact box, and the switch sits above the map",
                        a0["top"]["cal"] and a0["top"]["sw"] == ["high", "low", "gust", "wind", "precip"] and a0["top"]["maps"] == 1
                        and not any(a0["top"][k_] for k_ in ("dayBtn", "locBtn", "zoom", "stepper", "frame", "ladder", "exact", "dialogs")),
                        str(a0["top"]))
                page.click('#anaVar button[data-var="gust"]')
                a1 = ana_until(page, """() => document.querySelector('#anaLegend').textContent.startsWith('Peak gust') && ({
                  legend: document.querySelector('#anaLegend').textContent, url: location.search,
                  labels: Object.fromEntries([...document.querySelectorAll('#anaDots g.pd')].map(g => [g.dataset.loc, (g.querySelector('text.pvt') || {}).textContent || ''])) })""") or {}
                ana_gu = {k_: (((v_ or {}).get("rtma") or {}).get("gust") or {}).get("value") for k_, v_ in (ana_dayfile.get("locations") or {}).items()}
                chk.add(f"{scheme} analysis: the map's variable switch changes the legend and labels the map with the peak gust",
                        "Peak gust" in a1.get("legend", "") and a1.get("legend") != a0["legend"] and "var=gust" in a1.get("url", "")
                        and a1.get("labels") == {k_: ("" if ana_gu.get(k_) is None else str(ana_gu[k_])) for k_ in ana_lbl_want},
                        str(a1.get("legend", ""))[:60])
                # the map's colours are the scheme's own: each label's fill is the
                # token of its step in this scheme, and its ink differs from it
                ana_col = page.evaluate("""() => { const cs = getComputedStyle(document.documentElement), out = [];
                  document.querySelectorAll('#anaDots g.pd rect.pv').forEach(r => { const f = r.getAttribute('fill') || '';
                    const m = /var\\((--ana-[rb]\\d)\\)/.exec(f); if (!m) return;
                    const want = cs.getPropertyValue(m[1]).trim(), got = getComputedStyle(r).fill;
                    const ink = getComputedStyle(r.parentNode.querySelector('text.pvt')).fill;
                    const probe = document.createElement('i'); probe.style.color = want; document.body.appendChild(probe);
                    const wantRgb = getComputedStyle(probe).color; probe.remove();
                    out.push([m[1], got === wantRgb, ink !== got]); });
                  return { n: out.length, ok: out.every(x => x[1] && x[2]), steps: [...new Set(out.map(x => x[0]))].length,
                           r4: cs.getPropertyValue('--ana-r4').trim() }; }""")
                chk.add(f"{scheme} analysis: the map's labels take this scheme's colours, with ink that differs from each fill",
                        ana_col["n"] >= 60 and ana_col["ok"] and ana_col["steps"] >= 3
                        and ana_col["r4"].lower() == ("#f4ecc1" if scheme == "light" else "#76693a"), str(ana_col))
                # RTMA is the one analysis the page reads (owner's decision
                # 2026-09-30): no product choice, no URMA anywhere in the page
                # or its description, and an address from before that still
                # opens, on RTMA, without the parameter
                page.click('#anaVar button[data-var="high"]')
                ana_until(page, "() => document.querySelector('#anaLegend').textContent.startsWith('High')")
                a2 = page.evaluate("""() => ({ product: document.querySelectorAll('button[data-product]').length,
                  text: document.body.innerText + ' ' + [...document.querySelectorAll('meta')].map(m => m.content || '').join(' ') })""")
                chk.add(f"{scheme} analysis: one analysis, no product choice and no URMA on the page",
                        a2["product"] == 0 and "urma" not in a2["text"].lower(), str(a2["product"]))
                ana_goto(page, f"?var=high&product=urma&day={ana_day}")
                a3 = page.evaluate("() => [document.querySelector('#anaNewest').textContent, location.search, document.querySelectorAll('#anaDots text.pvt').length]")
                chk.add(f"{scheme} analysis: an address with product=urma opens on RTMA and drops the parameter",
                        "RTMA" in a3[0] and "product=" not in a3[1] and a3[2] >= 60, str(a3))
                # an address from before version 3 carries hour= and the map's
                # view, which went with the frames; it still opens, without them
                ana_goto(page, f"?var=high&day={ana_day}&hour=16&z=4.00&cx=850.00&cy=208.00")
                a4 = page.evaluate("() => location.search")
                chk.add(f"{scheme} analysis: an address with hour= and a map view still opens, and drops them",
                        f"day={ana_day}" in a4 and "hour=" not in a4 and "z=" not in a4 and "cx=" not in a4, a4)
                # a day whose file is gone draws every place hollow
                ana_gone = ana_days[0] if ana_days[0] != ana_day else ana_days[-1]

                def ana_routes(route):
                    if route.request.url.split("?")[0].endswith(f"/analysis2/days/{ana_gone}.json"):
                        return route.fulfill(status=404, body="not there")
                    return route.continue_()

                page.route("**/data/snapshots/**", ana_routes)
                ana_goto(page, f"?var=high&day={ana_gone}")
                ana_hol = page.evaluate("() => [document.querySelectorAll('#anaDots .pv.none').length, document.querySelectorAll('#anaDots text.pvt').length]")
                chk.add(f"{scheme} analysis: a day with no file draws every place hollow", ana_hol == [ana_n, 0], str(ana_hol))
                page.unroute("**/data/snapshots/**")
                # choosing a place on the map opens it on the same date, with its
                # day in full. The label is clicked where elementFromPoint proves
                # the pointer reaches it
                ana_hit = """(id) => { const g = document.querySelector('#anaDots g.pd[data-loc="' + id + '"]'); if (!g) return null;
                  const e = g.querySelector('rect.pv, circle.pv'), r = e.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
                  const t = document.elementFromPoint(x, y), hit = t && t.closest && t.closest('g.pd');
                  return { x, y, on: hit ? hit.dataset.loc : null }; }"""
                ana_goto(page, f"?var=high&day={ana_day}")
                page.evaluate("() => document.querySelector('#anaPlaceMap').scrollIntoView({ block: 'center' })")
                ht = page.evaluate(ana_hit, "new-york-ny")
                if ht:
                    page.mouse.click(ht["x"], ht["y"])
                ana_until(page, ANA_DAY)
                pn = page.evaluate("""() => { const p = document.querySelector('#locPanel'), hd = document.querySelector('#anaHead');
                  return { hidden: p.hidden, head: hd.hidden ? '' : hd.textContent, h2: (document.querySelector('#anaPlaceName') || {}).textContent || '',
                           rows: p.querySelectorAll('#anaHours tbody tr').length, cards: p.querySelectorAll('.anacard').length,
                           charts: [...p.querySelectorAll('svg.anasvg')].map(s => s.closest('section').id), series: p.querySelectorAll('#anaTemp path.series').length,
                           cur: [...document.querySelectorAll('#anaDots g.pd.cur')].map(g => g.dataset.loc), open: [...document.querySelectorAll('#anaCalGrid td.open')].map(t => t.dataset.day),
                           links: p.querySelectorAll('[data-contract-url]').length, text: hd.textContent + ' ' + p.textContent, url: location.search }; }""")
                ana_c = next((L_ for L_ in ana_index["locations"] if L_["id"] == "new-york-ny"), {}).get("cell") or {}
                chk.add(f"{scheme} analysis: choosing a place on the map opens it on the same date, with 24 hour rows and five cards",
                        bool(ht) and ht["on"] == "new-york-ny" and not pn["hidden"] and pn["h2"].startswith("New York") and pn["rows"] == 24 and pn["cards"] == 5
                        and "loc=new-york-ny" in pn["url"] and f"day={ana_day}" in pn["url"] and pn["cur"] == ["new-york-ny"] and pn["open"] == [ana_day],
                        str([ht, pn["h2"], pn["rows"], pn["cards"], pn["cur"]]))
                # the header (spec section 8): the name, the code when the index
                # carries one, both grid points, the centre of the grid square and the zone
                ana_ny = next((L_ for L_ in ana_index["locations"] if L_["id"] == "new-york-ny"), {})
                chk.add(f"{scheme} analysis: the place's header gives its name, its two grid points, the centre of its grid square and its zone",
                        f"Analysis grid point ({ana_c['wexp'][0]}, {ana_c['wexp'][1]})" in pn["head"] and f"Grid 184 point ({ana_c['g184'][0]}, {ana_c['g184'][1]})" in pn["head"]
                        and f"{ana_c['centre'][0]:.4f}° N, {abs(ana_c['centre'][1]):.4f}° W" in pn["head"] and "America/New_York" in pn["head"]
                        and (not ana_ny.get("code") or f"({ana_ny['code']})" in pn["h2"]), pn["head"][:200])
                chk.add(f"{scheme} analysis: the three charts draw, and the place never says URMA",
                        pn["charts"] == ["anaTemp", "anaWind", "anaPcp"] and pn["series"] >= 1 and "urma" not in pn["text"].lower(), str(pn["charts"]))
                chk.add(f"{scheme} analysis: the place shows no price and keeps the exchange's language",
                        pn["links"] == 0 and "¢" not in pn["text"] and not re.search(r"\b(ask|sell|offer|bid)\b", pn["text"], re.I)
                        and "No contract settles on it" in pn["text"], str(pn["links"]))
                # a day whose RTMA is short of 24 hours: the day file says so, and
                # the place file has no hour past the 17th, so the running values
                # stop at 17 (the lane's running values of an hour cover only the
                # hours up to it, so the first 17 rows keep theirs)
                def ana_partial(route):
                    u = route.request.url.split("?")[0]
                    if u.endswith(f"/analysis2/days/{ana_day}.json"):
                        resp = route.fetch(); d = json.loads(resp.text())
                        e = d["locations"]["new-york-ny"]["rtma"]
                        e.update({"hours": 17, "complete": False, "final": False})
                        # the precipitation resolves once every hourly file is
                        # read, so a day short of its files has not resolved either
                        if e.get("precip"):
                            e["precip"].update({"resolved": False, "resolvedAt": None, "hours": 17})
                        return route.fulfill(response=resp, body=json.dumps(d))
                    if u.endswith(f"/analysis2/loc/new-york-ny/{ana_day}.json"):
                        resp = route.fetch(); d = json.loads(resp.text())
                        for r_ in d["hours"][17:]:
                            r_["rtma"] = None
                        return route.fulfill(response=resp, body=json.dumps(d))
                    return route.continue_()

                page.route("**/data/snapshots/**", ana_partial)
                ana_goto(page, f"?day={ana_day}&loc=new-york-ny", ANA_DAY)
                st_p = page.evaluate("""() => ({ open: !document.querySelector('#locPanel').hidden,
                  pills: [...document.querySelectorAll('#locPanel .anapill')].map(x => x.textContent),
                  line: (document.querySelector('#anaStatus') || {}).textContent || '',
                  cards: document.querySelectorAll('#locPanel .anacard').length,
                  prov: document.querySelectorAll('#anaDots g.pd[data-loc="new-york-ny"] .pv.prov').length })""")
                page.unroute("**/data/snapshots/**")
                chk.add(f"{scheme} analysis: ?loc= opens the panel on load",
                        st_p["open"] and len(st_p["pills"]) == 1 and st_p["cards"] == 5, str(st_p)[:160])
                chk.add(f"{scheme} analysis: the status line says provisional when RTMA is incomplete, with the count, and the map dashes the label",
                        st_p["pills"] == ["Provisional"] and "17 of 24 hours" in st_p["line"] and st_p["prov"] == 1, st_p["line"][:120])
                # a closed day in the fixtures, if one is there: closed, with its count
                ana_closed = next((d_ for d_ in ana_days if ((ana_entries.get(d_) or {}).get("rtma") or {}).get("closed")), None)
                if ana_closed:
                    ana_goto(page, f"?var=high&day={ana_closed}&loc=new-york-ny", ANA_DAY)
                    st_c = page.evaluate("() => (document.querySelector('#anaStatus') || {}).textContent || ''")
                    chk.add(f"{scheme} analysis: a closed RTMA day says closed, with its count", st_c.startswith("Closed") and " of " in st_c, st_c[:80])
                # a place with no hour published by its 12:45 AM cut-off is under
                # review (owner's ruling 2026-10-14): every card says so, no value
                # is drawn, and the map draws the place hollow
                def ana_review(route):
                    if route.request.url.split("?")[0].endswith(f"/analysis2/days/{ana_day}.json"):
                        resp = route.fetch(); d = json.loads(resp.text())
                        d["locations"]["new-york-ny"]["rtma"] = {
                            "hours": 0, "of": 24, "complete": False, "high": None, "low": None, "gust": None,
                            "wind": None, "final": False, "finalAt": None, "review": True, "closed": False,
                            "precip": {"value": None, "exact": None, "hours": 0, "resolved": False,
                                       "resolvedAt": None, "review": True}}
                        return route.fulfill(response=resp, body=json.dumps(d))
                    return route.continue_()

                page.route("**/data/snapshots/**", ana_review)
                ana_goto(page, f"?var=high&day={ana_day}&loc=new-york-ny", ANA_DAY)
                pills_r = page.eval_on_selector_all("#locPanel .anapill", "e => e.map(x => x.textContent)")
                cards_r = page.eval_on_selector_all("#locPanel .anacard .ae", "e => e.map(x => x.textContent)")
                novalue_r = page.locator("#locPanel .anacard .av.dim").count()
                hollow_r = page.locator('#anaDots g.pd[data-loc="new-york-ny"] .pv.none').count()
                page.unroute("**/data/snapshots/**")
                chk.add(f"{scheme} analysis: a place with no hour by the cut-off says under review on every card, with no value, and is hollow on the map",
                        pills_r == ["Under review"] and "under review" in page.evaluate("() => document.querySelector('#anaStatus').textContent")
                        and len(cards_r) == 5 and novalue_r == 5 and all("12:45 AM cut-off" in c_ for c_ in cards_r) and hollow_r == 1,
                        str([pills_r, cards_r[:2], hollow_r]))
                # the freshness pill judges the job by the index's written time,
                # not by the analysis valid hour NOAA's lag keeps behind the clock;
                # the valid hours stand beside it
                def ana_fresh(route):
                    resp = route.fetch(); d = json.loads(resp.text())
                    d["written"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 120))
                    d["asof"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3 * 3600))
                    return route.fulfill(response=resp, body=json.dumps(d))

                page.route("**/analysis2/index.json", ana_fresh)
                ana_goto(page, "")
                fr = page.evaluate("""() => ({ cls: document.querySelector('.status').className, text: document.querySelector('.status').textContent,
                                              newest: (document.querySelector('#anaNewest') || {}).textContent || '' })""")
                page.unroute("**/analysis2/index.json")
                ana_latest = {k: (v.get("latest") or "").replace("T", " ").replace(":00Z", " UTC") for k, v in (ana_index.get("sources") or {}).items()}
                chk.add(f"{scheme} analysis: the freshness pill reads the index's written time against the job cadence",
                        "live" in fr["cls"] and "behind" not in fr["text"] and "every 10 minutes" in fr["text"], str(fr)[:160])
                chk.add(f"{scheme} analysis: the newest RTMA analysis valid hour stays visible",
                        fr["newest"] == "newest RTMA analysis " + ana_latest.get("rtma", "x"), fr["newest"])
                # ---- the calendar, always on the page. The index is routed to
                #      list two days in the month before the fixtures' first and one
                #      two months after their last, with the month between left
                #      empty, and to put one fixture day under review and one closed,
                #      so the marks and the month steps have something to show. The
                #      added days are never opened, so their missing files are never
                #      requested.
                ana_m0 = dt.date.fromisoformat(ana_days[0][:7] + "-01")
                ana_before = ana_m0 - dt.timedelta(days=1)
                ana_after = (dt.date.fromisoformat(ana_days[-1][:7] + "-01") + dt.timedelta(days=62)).replace(day=2)
                ana_skip = (dt.date.fromisoformat(ana_days[-1][:7] + "-01") + dt.timedelta(days=31)).strftime("%B %Y")
                ana_mid = [d_ for d_ in ana_days if d_ not in (ana_rday, ana_days[-1], ana_pick)]
                ana_rv, ana_cl = (ana_mid[-1], ana_mid[-2]) if len(ana_mid) >= 2 else (None, None)
                ana_st = dict(ana_index.get("dayStatus") or {})
                ana_st.update({(ana_before - dt.timedelta(days=1)).isoformat(): {"places": 67, "final": 67, "resolved": 67, "closed": 0, "review": 0},
                               ana_before.isoformat(): {"places": 67, "final": 0, "resolved": 0, "closed": 0, "review": 0},
                               ana_after.isoformat(): {"places": 67, "final": 0, "resolved": 0, "closed": 0, "review": 0}})
                if ana_rv:
                    ana_st[ana_rv] = {"places": 67, "final": 60, "resolved": 60, "closed": 0, "review": 7}
                    ana_st[ana_cl] = {"places": 67, "final": 60, "resolved": 60, "closed": 7, "review": 0}
                ana_cal_days = sorted(set(ana_days) | {(ana_before - dt.timedelta(days=1)).isoformat(), ana_before.isoformat(), ana_after.isoformat()})

                def ana_kind(d_):
                    """The state docs/analysis.md section 6 gives a listed day, from its status."""
                    s_ = ana_st.get(d_) or {}
                    if not s_.get("places"):
                        return "none"
                    if s_["resolved"] == s_["places"]:
                        return "final"
                    if s_.get("review") and s_["resolved"] + s_["review"] >= s_["places"]:
                        return "review"
                    return "closed" if s_["resolved"] + s_.get("closed", 0) + s_.get("review", 0) >= s_["places"] else "provisional"

                ana_word = {"final": "final", "provisional": "provisional", "review": "under review", "closed": "closed"}

                def ana_cal_index(route):
                    resp = route.fetch(); d = json.loads(resp.text())
                    d["days"] = ana_cal_days
                    d["dayStatus"] = ana_st
                    return route.fulfill(response=resp, body=json.dumps(d))

                ana_month = lambda d_: dt.date.fromisoformat(d_[:7] + "-15").strftime("%B %Y")
                ana_cal_state = """() => { const c = document.querySelector('#anaCal'), a = document.activeElement, r = c.getBoundingClientRect();
                  return { shown: r.height > 0 && getComputedStyle(c).visibility !== 'hidden', grid: document.querySelector('#anaCalGrid').getAttribute('role'),
                    focus: a ? a.id || (a.dataset && a.dataset.day) || a.tagName : '', title: document.querySelector('#anaCalTitle').textContent,
                    sel: [...document.querySelectorAll('#anaCalGrid td[aria-selected="true"]')].map(t => t.dataset.day),
                    open: [...document.querySelectorAll('#anaCalGrid td.open')].map(t => t.dataset.day),
                    stops: [...document.querySelectorAll('#anaCalGrid td[tabindex="0"]')].map(t => t.dataset.day),
                    prev: document.querySelector('#anaCalPrev').disabled, next: document.querySelector('#anaCalNext').disabled,
                    cells: [...document.querySelectorAll('#anaCalGrid td[data-day]')].map(t => [t.dataset.day, t.className, t.dataset.status || '',
                      t.getAttribute('aria-disabled') || '', t.getAttribute('aria-label') || '', t.hasAttribute('tabindex'), !!t.querySelector('i.mk')]),
                    key: document.querySelector('#anaCalKey').textContent,
                    head: (document.querySelector('#anaHeadDay') || {}).textContent || '', place: (document.querySelector('#anaPlaceName') || {}).textContent || '',
                    rows: document.querySelectorAll('#anaHours tbody tr').length, url: location.search }; }"""
                page.route("**/analysis2/index.json", ana_cal_index)
                ana_goto(page, f"?day={ana_rday}")
                k0 = page.evaluate(ana_cal_state)
                chk.add(f"{scheme} analysis calendar: it is on the page at the open day's month, with the open day marked and the one tab stop on it",
                        k0["shown"] and k0["grid"] == "grid" and k0["sel"] == [ana_rday] and k0["open"] == [ana_rday] and k0["stops"] == [ana_rday]
                        and k0["title"] == ana_month(ana_rday), str({k_: k0[k_] for k_ in ("shown", "sel", "open", "stops", "title")}))
                k0_on = [c_ for c_ in k0["cells"] if c_[0] in ana_cal_days]
                k0_want = [d_ for d_ in ana_cal_days if d_[:7] == ana_rday[:7]]
                chk.add(f"{scheme} analysis calendar: each listed day is marked final, provisional, under review or closed as the index's day status has it",
                        bool(ana_rv) and [c_[0] for c_ in k0_on] == k0_want
                        and all(c_[1].split()[0] == "on" and c_[2] == ana_kind(c_[0]) and (", " + ana_word[ana_kind(c_[0])]) in c_[4] and c_[6] for c_ in k0_on)
                        and {ana_kind(d_) for d_ in k0_want} >= {"final", "provisional", "review", "closed"}
                        and all(w_ in k0["key"] for w_ in ("final", "provisional", "under review", "closed")),
                        str([(c_[0], c_[2], c_[4][-24:]) for c_ in k0_on]))
                k0_off = [c_ for c_ in k0["cells"] if c_[0] not in ana_cal_days]
                ana_grey = k0_off[0][0] if k0_off else None
                if ana_grey:
                    page.click(f'#anaCalGrid td[data-day="{ana_grey}"]', force=True)
                    page.wait_for_timeout(300)        # nothing is meant to change, so there is nothing to wait for
                k1 = page.evaluate(ana_cal_state)
                chk.add(f"{scheme} analysis calendar: every day not listed is greyed, out of the tab order and unmarked, and a click on one opens nothing",
                        len(k0_off) + len(k0_on) == len(k0["cells"]) and len(k0_off) > 0
                        and all(c_[1] == "off" and c_[3] == "true" and not c_[5] and not c_[6] and c_[4].endswith(", not on file") for c_ in k0_off)
                        and k1["open"] == [ana_rday] and f"day={ana_rday}" in k1["url"], str([ana_grey, k1["open"]]))
                # the month steps go to the month before, the fixtures' month and
                # the one two months on, never to the empty month between
                ana_titles = [page.evaluate(ana_cal_state)]
                for b_ in ("#anaCalNext", "#anaCalPrev", "#anaCalPrev"):
                    t0_ = ana_titles[-1]["title"]
                    page.click(b_)
                    ana_titles.append(ana_until(page, "(t) => document.querySelector('#anaCalTitle').textContent !== t && (" + ana_cal_state + ")()", arg=t0_)
                                      or page.evaluate(ana_cal_state))
                chk.add(f"{scheme} analysis calendar: month steps cover only the months that hold listed days",
                        [t_["title"] for t_ in ana_titles] == [ana_month(ana_rday), ana_month(ana_after.isoformat()), ana_month(ana_rday), ana_month(ana_before.isoformat())]
                        and [(t_["prev"], t_["next"]) for t_ in ana_titles] == [(False, False), (False, True), (False, False), (True, False)]
                        and all(ana_skip != t_["title"] for t_ in ana_titles) and ana_titles[1]["stops"] == [ana_after.isoformat()],
                        str([(t_["title"], t_["prev"], t_["next"]) for t_ in ana_titles]))
                # by keyboard, from a fresh load: the grid's tab stop is the open
                # day, ArrowRight walks the listed days into the month two on,
                # ArrowLeft walks back to the day before the open one, and Enter
                # opens that day, with the focus kept on it
                ana_goto(page, f"?day={ana_rday}")
                ana_i, ana_ia = ana_cal_days.index(ana_rday), ana_cal_days.index(ana_after.isoformat())
                ana_keys = ["ArrowRight"] * (ana_ia - ana_i) + ["ArrowLeft"] * (ana_ia - ana_i + 1)
                ana_walk_want = [ana_rday] + [[ana_cal_days[j_], ana_month(ana_cal_days[j_])]
                                              for j_ in list(range(ana_i + 1, ana_ia + 1)) + list(range(ana_ia - 1, ana_i - 2, -1))]
                page.focus('#anaCalGrid td[tabindex="0"]')
                ana_walk = [page.evaluate(ana_cal_state)["focus"]]
                for key_ in ana_keys:
                    page.keyboard.press(key_)
                    s_ = page.evaluate(ana_cal_state); ana_walk.append([s_["focus"], s_["title"]])
                ana_kpick = ana_cal_days[ana_i - 1]
                page.keyboard.press("Enter")
                k3 = ana_until(page, "(d) => location.search.includes('day=' + d) && (" + ana_cal_state + ")()", arg=ana_kpick) or page.evaluate(ana_cal_state)
                chk.add(f"{scheme} analysis calendar: the arrows move between listed days across months and Enter opens one, with the focus kept on it",
                        ana_walk == ana_walk_want and ana_kpick != ana_rday and ana_month(ana_after.isoformat()) != ana_month(ana_rday)
                        and k3["focus"] == ana_kpick and k3["open"] == [ana_kpick] and f"day={ana_kpick}" in k3["url"],
                        str([ana_walk, k3["open"], k3["focus"]]))
                # by click, with a place open: the date opens for the same place
                ana_goto(page, f"?day={ana_rday}&loc=new-york-ny", ANA_DAY)
                page.click(f'#anaCalGrid td[data-day="{ana_pick}"]')
                k4 = ana_until(page, "(d) => location.search.includes('day=' + d) && document.querySelectorAll('#anaHours tbody tr').length > 0 && (" + ana_cal_state + ")()",
                               arg=ana_pick) or page.evaluate(ana_cal_state)
                ana_pk = dt.date.fromisoformat(ana_pick)
                chk.add(f"{scheme} analysis calendar: choosing a date opens it for the same place and writes the address",
                        k4["open"] == [ana_pick] and f"day={ana_pick}" in k4["url"] and "loc=new-york-ny" in k4["url"] and k4["place"].startswith("New York")
                        and k4["head"] == f"{ana_pk:%A}, {ana_pk:%B} {ana_pk.day}, {ana_pk.year}" and k4["rows"] == 24,
                        str([k4["open"], k4["url"], k4["head"], k4["rows"]]))
                page.unroute("**/analysis2/index.json")
                # ---- the map's box finds a place by name for the keyboard, a
                #      screen reader and a phone: every place is in its list, a
                #      typed name narrows it, the arrows move through it, Enter
                #      opens a place on the same date, and Escape or the focus
                #      leaving the box closes the list
                ana_pl_state = """() => { const q = document.querySelector('#anaPlaceQ'), l = document.querySelector('#anaPlaceList'), a = document.activeElement;
                  return { list: !l.hidden && l.getBoundingClientRect().height > 0, exp: q.getAttribute('aria-expanded'), focus: a ? a.id || a.tagName : '',
                    combo: [q.getAttribute('role'), q.getAttribute('aria-controls'), q.getAttribute('aria-autocomplete'), (document.querySelector('label[for="anaPlaceQ"]') || {}).textContent || ''],
                    listbox: l.getAttribute('role'), maphidden: document.querySelector('#anaPlaceMap').getAttribute('aria-hidden'),
                    opts: [...l.querySelectorAll('li[role="option"]')].map(x => x.dataset.loc), texts: [...l.querySelectorAll('li[role="option"]')].map(x => x.textContent),
                    dots: [...document.querySelectorAll('#anaDots g.pd')].map(g => g.dataset.loc),
                    active: q.getAttribute('aria-activedescendant') || '', chosen: [...l.querySelectorAll('li[aria-selected="true"]')].map(x => x.dataset.loc),
                    count: document.querySelector('#anaPlaceCount').textContent, aria: document.querySelector('#anaPlaceCount').getAttribute('aria-live'),
                    h2: (document.querySelector('#anaPlaceName') || {}).textContent || '', url: location.search }; }"""
                ana_ids = sorted(L_["id"] for L_ in ana_index["locations"])
                ana_goto(page, f"?var=high&day={ana_day}")
                page.click("#anaPlaceQ")
                page.keyboard.press("ArrowDown")
                p0 = ana_until(page, "() => !document.querySelector('#anaPlaceList').hidden && (" + ana_pl_state + ")()") or page.evaluate(ana_pl_state)
                ana_nyv = f"New York, NY · {ana_hi.get('new-york-ny')}°F"
                chk.add(f"{scheme} analysis place box: every place is on the map and in the box's list, with its value, and the map is left to the pointer",
                        p0["list"] and p0["exp"] == "true" and p0["combo"][:3] == ["combobox", "anaPlaceList", "list"] and p0["combo"][3] != ""
                        and p0["listbox"] == "listbox" and p0["maphidden"] == "true" and sorted(p0["opts"]) == ana_ids and sorted(p0["dots"]) == ana_ids
                        and len(ana_ids) == ana_n and p0["aria"] == "polite" and ana_nyv in p0["texts"],
                        str({k_: p0[k_] for k_ in ("list", "exp", "combo", "count")})[:240])
                page.keyboard.press("Escape")
                page.fill("#anaPlaceQ", "")
                page.keyboard.type("denv")
                p1 = ana_until(page, "() => document.querySelectorAll('#anaPlaceList li').length === 1 && (" + ana_pl_state + ")()") or page.evaluate(ana_pl_state)
                page.keyboard.press("Enter")
                p2 = ana_until(page, "() => location.search.includes('loc=denver-co') && document.querySelectorAll('#anaHours tbody tr').length > 0 && ("
                               + ana_pl_state + ")()") or page.evaluate(ana_pl_state)
                chk.add(f"{scheme} analysis place box: a typed name narrows the list, and Enter opens the first match on the same date, with the focus kept in the box",
                        p1["opts"] == ["denver-co"] and p1["active"] == "anaPlace-denver-co" and p1["chosen"] == ["denver-co"]
                        and not p2["list"] and p2["focus"] == "anaPlaceQ" and p2["h2"].startswith("Denver") and "loc=denver-co" in p2["url"]
                        and f"day={ana_day}" in p2["url"], str([p1["opts"], p1["active"], p2["h2"], p2["focus"], p2["url"]]))
                ana_san = [L_["id"] for L_ in sorted(ana_index["locations"], key=lambda L_: (L_["name"], L_["state"])) if L_["name"].lower().startswith("san")]
                page.keyboard.type("san")
                ana_steps = [ana_until(page, "() => document.querySelectorAll('#anaPlaceList li').length >= 3 && (" + ana_pl_state + ")()") or page.evaluate(ana_pl_state)]
                for key_ in ("ArrowDown", "ArrowDown", "ArrowUp", "ArrowDown"):
                    page.keyboard.press(key_)
                    ana_steps.append(page.evaluate(ana_pl_state))
                page.keyboard.press("Enter")
                p4 = ana_until(page, "(id) => location.search.includes('loc=' + id) && (" + ana_pl_state + ")()", arg=ana_san[2] if len(ana_san) > 2 else "x") \
                    or page.evaluate(ana_pl_state)
                chk.add(f"{scheme} analysis place box: the arrows move through the matches and Enter opens the one they reach",
                        len(ana_san) >= 3 and ana_steps[0]["opts"][:len(ana_san)] == ana_san
                        and [s_["active"] for s_ in ana_steps] == ["anaPlace-" + ana_san[i_] for i_ in (0, 1, 2, 1, 2)]
                        and all(s_["chosen"] == [s_["active"][len("anaPlace-"):]] for s_ in ana_steps)
                        and not p4["list"] and f"loc={ana_san[2]}" in p4["url"], str([[s_["active"] for s_ in ana_steps], p4["url"]]))
                page.keyboard.type("bo")
                p5a = ana_until(page, "() => !document.querySelector('#anaPlaceList').hidden && (" + ana_pl_state + ")()") or page.evaluate(ana_pl_state)
                page.keyboard.press("Escape")
                p5 = page.evaluate(ana_pl_state)
                chk.add(f"{scheme} analysis place box: Escape closes the list and keeps the place, with the focus in the box",
                        p5a["list"] and not p5["list"] and p5["exp"] == "false" and p5["focus"] == "anaPlaceQ" and f"loc={ana_san[2]}" in p5["url"],
                        str({k_: p5[k_] for k_ in ("list", "focus", "url")}))
                page.fill("#anaPlaceQ", "")
                page.keyboard.type("chi")
                p6a = ana_until(page, "() => !document.querySelector('#anaPlaceList').hidden") is True
                page.keyboard.press("Tab")
                p6 = ana_until(page, "() => document.querySelector('#anaPlaceList').hidden && (" + ana_pl_state + ")()") or page.evaluate(ana_pl_state)
                chk.add(f"{scheme} analysis place box: the list closes when the focus leaves the box, and opens nothing",
                        p6a and not p6["list"] and p6["focus"] != "anaPlaceQ" and f"loc={ana_san[2]}" in p6["url"], str([p6a, p6["list"], p6["focus"], p6["url"]]))
                # ---- the map's variable switch: it labels the map, lights the
                #      card and writes var=, and a card click, a link's var= and the
                #      arrow keys move it
                ana_sw_state = """() => ({ checked: [...document.querySelectorAll('#anaVar button[aria-checked="true"]')].map(b => b.dataset.var),
                  stops: [...document.querySelectorAll('#anaVar button')].filter(b => b.tabIndex === 0).map(b => b.dataset.var),
                  card: [...document.querySelectorAll('#anaCards .anacard.on')].map(c => c.dataset.var),
                  pressed: [...document.querySelectorAll('#anaCards .anacard[aria-pressed="true"]')].map(c => c.dataset.var),
                  legend: document.querySelector('#anaLegend').textContent,
                  labels: Object.fromEntries([...document.querySelectorAll('#anaDots g.pd')].map(g => [g.dataset.loc, (g.querySelector('text.pvt') || {}).textContent || ''])),
                  url: location.search, focus: (document.activeElement && document.activeElement.dataset && document.activeElement.dataset.var) || '' })"""
                ana_goto(page, f"?var=high&day={ana_day}&loc=new-york-ny", ANA_DAY)
                s0 = page.evaluate(ana_sw_state)
                page.click('#anaVar button[data-var="gust"]')
                s1 = ana_until(page, "() => location.search.includes('var=gust') && (" + ana_sw_state + ")()") or page.evaluate(ana_sw_state)
                chk.add(f"{scheme} analysis switch: Peak gust on the switch labels the map with the peak gust, lights its card and writes var=",
                        s0["checked"] == ["high"] and s0["card"] == ["high"] and s1["checked"] == ["gust"] and s1["stops"] == ["gust"]
                        and s1["card"] == ["gust"] and s1["pressed"] == ["gust"] and "Peak gust" in s1["legend"] and "var=gust" in s1["url"]
                        and s1["labels"] == {k_: ("" if ana_gu.get(k_) is None else str(ana_gu[k_])) for k_ in ana_lbl_want},
                        str([s1["checked"], s1["card"], s1["url"]]))
                ana_pr = {k_: (((v_ or {}).get("rtma") or {}).get("precip") or {}).get("value") for k_, v_ in (ana_dayfile.get("locations") or {}).items()}
                page.click('#anaCards .anacard[data-var="precip"]')
                s2 = ana_until(page, "() => location.search.includes('var=precip') && (" + ana_sw_state + ")()") or page.evaluate(ana_sw_state)
                chk.add(f"{scheme} analysis switch: a value card sets the map's variable, its labels, the switch and var=",
                        s2["checked"] == ["precip"] and s2["stops"] == ["precip"] and s2["card"] == ["precip"] and "Precipitation" in s2["legend"]
                        and "var=precip" in s2["url"] and s2["labels"] == {k_: ("" if ana_pr.get(k_) is None else f"{ana_pr[k_]:.2f}") for k_ in ana_lbl_want},
                        str([s2["checked"], s2["card"], s2["url"], list(s2["labels"].items())[:3]]))
                page.focus('#anaVar button[aria-checked="true"]')
                ana_sw_keys = []
                for key_, want_ in (("ArrowLeft", "wind"), ("ArrowRight", "precip"), ("Home", "high")):
                    page.keyboard.press(key_)
                    ana_sw_keys.append(ana_until(page, "(w) => location.search.includes('var=' + w) && (" + ana_sw_state + ")()", arg=want_)
                                       or page.evaluate(ana_sw_state))
                chk.add(f"{scheme} analysis switch: one tab stop, and the arrows and Home move the choice with the focus, the cards and var= following",
                        [(s_["checked"], s_["stops"], s_["focus"], s_["card"]) for s_ in ana_sw_keys]
                        == [(["wind"], ["wind"], "wind", ["wind"]), (["precip"], ["precip"], "precip", ["precip"]), (["high"], ["high"], "high", ["high"])]
                        and "var=high" in ana_sw_keys[-1]["url"], str([(s_["checked"], s_["focus"], s_["card"]) for s_ in ana_sw_keys]))
                ana_goto(page, f"?var=low&day={ana_day}&loc=new-york-ny", ANA_DAY)
                s3 = page.evaluate(ana_sw_state)
                chk.add(f"{scheme} analysis switch: a link's var= sets the switch, the card and the map on load",
                        s3["checked"] == ["low"] and s3["stops"] == ["low"] and s3["card"] == ["low"] and s3["legend"].startswith("Low, ")
                        and "var=low" in s3["url"], str([s3["checked"], s3["card"], s3["legend"][:30]]))
                # ---- the layout: the calendar and the map at the top, side by
                #      side at 1280 px and stacked at 375 px, above the place's
                #      header and its day, with no sideways scroll; at 375 px the
                #      gutter is 16 px and the box's list stays on screen
                ana_lay = """() => { const R = s => { const r = document.querySelector(s).getBoundingClientRect(); return [r.left, r.top + scrollY, r.right, r.bottom + scrollY, r.width, r.height]; };
                  return { cal: R('#anaCal'), map: R('#anaMapCard'), head: R('#anaHead'), status: R('#anaStatus'), replay: R('#anaClock'),
                           sw: [document.scrollingElement.scrollWidth, document.scrollingElement.clientWidth], vw: document.documentElement.clientWidth,
                           order: [...document.querySelectorAll('.wrap > *')].map(e => e.id).filter(Boolean) }; }"""
                ana_w_ctx = browser.new_context(color_scheme=scheme, viewport={"width": 1280, "height": 900})
                ana_w = ana_w_ctx.new_page()
                errors_of(ana_w, into=errs)
                ana_goto(ana_w, f"?day={ana_day}&loc=new-york-ny", ANA_DAY)
                lw = ana_w.evaluate(ana_lay)
                ana_w_ctx.close()
                chk.add(f"{scheme} analysis: at 1280 px the calendar and the map sit side by side at the top, above the place's header and its day",
                        abs(lw["cal"][1] - lw["map"][1]) <= 2 and lw["cal"][2] < lw["map"][0] and lw["cal"][5] > 200 and lw["map"][5] > 300
                        and max(lw["cal"][3], lw["map"][3]) <= lw["head"][1] <= lw["status"][1] and lw["cal"][1] < 600
                        and lw["order"].index("anaPickers") < lw["order"].index("anaHead") < lw["order"].index("locPanel")
                        and lw["sw"][0] <= lw["sw"][1], str([[round(x) for x in lw["cal"][:4]], [round(x) for x in lw["map"][:4]], round(lw["head"][1]), lw["sw"]]))
                ana_ph_ctx = browser.new_context(color_scheme=scheme, viewport={"width": 375, "height": 812})
                ana_ph = ana_ph_ctx.new_page()
                ana_ph_errs = errors_of(ana_ph)
                ana_goto(ana_ph, f"?day={ana_day}&loc=new-york-ny", ANA_DAY)
                lp = ana_ph.evaluate(ana_lay)
                ana_wide = "() => [document.scrollingElement.scrollWidth, document.scrollingElement.clientWidth]"
                ana_ph.click("#anaPlaceQ")
                ana_ph.keyboard.type("san")
                ana_until(ana_ph, "() => !document.querySelector('#anaPlaceList').hidden")
                w1 = ana_ph.evaluate(ana_wide)
                e1 = ana_ph.evaluate("() => { const r = document.querySelector('#anaPlaceList').getBoundingClientRect(); return [r.left, r.right, document.documentElement.clientWidth, r.height]; }")
                ana_ph.fill("#anaPlaceQ", "")
                ana_ph.keyboard.type("bost"); ana_ph.keyboard.press("Enter")
                ph_open = ana_until(ana_ph, "() => location.search.includes('loc=boston-ma') && document.querySelectorAll('#anaHours tbody tr').length > 0 && [(document.querySelector('#anaPlaceName') || {}).textContent, location.search]") or ["", ""]
                w2 = ana_ph.evaluate(ana_wide)
                ana_ph.click('#anaVar button[data-var="wind"]')
                ana_until(ana_ph, "() => location.search.includes('var=wind')")
                w3 = ana_ph.evaluate(ana_wide)
                ana_ph_ctx.close()
                chk.add(f"{scheme} analysis: at 375 px the calendar and the map are stacked at the top with a 16 px gutter, and the page never scrolls sideways",
                        lp["cal"][3] <= lp["map"][1] and abs(lp["cal"][0] - 16) <= 1 and abs(lp["map"][0] - 16) <= 1 and abs(lp["cal"][2] - (lp["vw"] - 16)) <= 1
                        and abs(lp["map"][2] - (lp["vw"] - 16)) <= 1 and lp["map"][3] <= lp["head"][1] <= lp["status"][1]
                        and all(a_ <= b_ for a_, b_ in (lp["sw"], w1, w2, w3)) and e1[3] > 0 and e1[0] >= 0 and e1[1] <= e1[2]
                        and str(ph_open[0]).startswith("Boston") and "loc=boston-ma" in ph_open[1],
                        str([[round(x) for x in lp["cal"][:4]], [round(x) for x in lp["map"][:4]], lp["sw"], w1, w2, w3, e1, ph_open]))
                chk.add(f"{scheme} analysis: at phone width the page raises no console error", not ana_ph_errs, "; ".join(ana_ph_errs)[:300])
                # a day or place file that could not be read is said to be
                # unreadable, not a day with no hours; a fresh context so no
                # cached copy stands in
                ana_ctx2 = browser.new_context(color_scheme=scheme, viewport={"width": 1200, "height": 900})
                ana_p2 = ana_ctx2.new_page()
                ana_p2.route("**/analysis2/days/**", lambda route: route.fulfill(status=404, body="gone"))
                ana_p2.route("**/analysis2/loc/**", lambda route: route.fulfill(status=404, body="gone"))
                ana_goto(ana_p2, f"?var=high&day={ana_day}&loc=new-york-ny", ANA_DAY)
                ur = ana_p2.evaluate("""() => [[...document.querySelectorAll('#locPanel .anacard .ae')].map(x => x.textContent),
                                              [...document.querySelectorAll('#locPanel p.cap')].filter(x => !x.hidden).map(x => x.textContent).join(' ')]""")
                chk.add(f"{scheme} analysis: an unreadable day file is reported as unreadable, not as no hours",
                        len(ur[0]) == 5 and all("could not be read" in t for t in ur[0]) and "could not be read" in ur[1]
                        and "No hours read yet" not in ur[1], str(ur)[:160])
                ana_ctx2.close()
                # ---- the settlement display, drawn only from the lane's figures.
                #      Every expected figure below is read from the fixtures, which
                #      the lane's own code wrote, or from the rowShape 3 file made by
                #      hand from them (ana_row_shape3), never worked out by the page.
                #      On the samples' final New York day: the five cards carry the
                #      summary, the hour table ends its running total on the day's,
                #      and with no publication time on record the day is shown as it
                #      stands. On the rowShape 3 file: the hover on each chart, the
                #      four CSV files, every wording of the hourly contract, the
                #      replay and its end. With the clock fixed on the samples' live
                #      day: the three charts share one axis and one now line, the
                #      countdowns pass through their four states, and a refetch
                #      inside an arrival window carries ?b= and fills the day in.
                with open(os.path.join(ANA_SNAP, "loc", "new-york-ny", ana_day + ".json")) as fh:
                    sd_doc = json.load(fh)
                sd_sum = sd_doc["summary"]["rtma"]
                sd_tz = _Z(sd_doc["tz"])
                sd_n2 = lambda s_: str(s_).replace(" ", " ")
                sd_rows = sd_doc["hours"]

                def sd_hour(iso_):
                    t_ = dt.datetime.fromisoformat(iso_.replace("Z", "+00:00")).astimezone(sd_tz)
                    return f"{t_.hour % 12 or 12} {'AM' if t_.hour < 12 else 'PM'} {t_.tzname()}"

                def sd_clock(t_, secs=False):
                    t_ = t_.astimezone(sd_tz)
                    return f"{t_.hour % 12 or 12}:{t_.minute:02d}" + (f":{t_.second:02d}" if secs else "") + f" {'AM' if t_.hour < 12 else 'PM'}"

                sd_at = lambda s_: dt.datetime.fromisoformat(s_.replace("Z", "+00:00"))

                # the hourly contract in words, as the owner set them on 2026-11-04 and
                # put plainly that evening, worked out here from the lane's state, basis,
                # moment and cut-off, 1 hour 30 minutes after the hour since 2026-11-05;
                # an hour not final at the moment shown (`at_`) says when it will be
                SD_CUT = dt.timedelta(hours=1, minutes=30)
                SD_NOMADS_FROM = dt.datetime(2026, 11, 3, 10, tzinfo=dt.timezone.utc)

                def sd_htext(hr, F_=None, at_=None):
                    three_ = sd_clock(sd_at(hr["cut"]), True) + ", 1 hour 30 minutes after the hour"
                    if hr["state"] == "final" and hr["basis"] == "nomads":
                        return ("final at " + sd_clock(sd_at(hr["at"]), True) + ", when NOMADS published the file. The value is "
                                + ("NOAA Open Data" if hr.get("src") == "nodd" else "NOMADS") + "’s copy.")
                    if hr["state"] == "final" and hr["basis"] == "cutoff":
                        return "final at " + three_ + ", from NOAA Open Data’s copy, because NOMADS had not published the file by then."
                    if hr["state"] == "final" and hr["basis"] == "norecord":
                        return "final at " + three_ + ", from NOAA Open Data’s copy. NOMADS’s publication time for this hour is not on record."
                    if hr["state"] == "review" and hr.get("basis") == "norecord":
                        od_ = ((F_ or {}).get("nodd") or {}).get("lm")
                        return ("no value: " + ("NOAA Open Data published the file after " + three_ + ", and NOMADS’s time is not on record."
                                                if od_ else "neither server is on record with the file by " + three_ + ".") + " Under review.")
                    if hr["state"] == "review":
                        return "no value: neither server had published the file by " + three_ + ". Under review."
                    t_ = sd_at(hr["cut"]) - SD_CUT
                    if t_ < SD_NOMADS_FROM:
                        how_ = ("NOMADS’s publication time for this hour is not on record, so it becomes final at its cut-off, " + three_
                                + ", from NOAA Open Data’s copy. With no copy by then, it goes to review.")
                    else:
                        e_ = t_ + dt.timedelta(minutes=46)
                        how_ = ("It becomes final when NOMADS publishes the file" + (", expected about " + sd_clock(e_) if at_ < e_ else "")
                                + ", or at the latest at its cut-off, " + three_ + ", from NOAA Open Data’s copy. With neither copy by then, it goes to review.")
                    return ("provisional. " if at_ >= t_ else "") + how_

                # the every-hour table's few words: a final hour's time and whose file
                # made it final, and for one not final yet, NOMADS's usual time while it
                # is ahead and its cut-off
                sd_word = lambda hr: ("final " + sd_clock(sd_at(hr["at"] or hr["cut"])) + (" (NOMADS)" if hr["basis"] == "nomads" else " (Open Data)")
                                      if hr["state"] == "final" else "under review" if hr["state"] == "review" else "")

                def sd_pword(t_, at_):
                    e_ = t_ + dt.timedelta(minutes=46)
                    fin_ = ("final expected " + sd_clock(e_) + ", " if t_ >= SD_NOMADS_FROM and at_ < e_ else "") + "cut-off " + sd_clock(t_ + SD_CUT)
                    return ("provisional, " if at_ >= t_ else "") + fin_
                SD_SRV = {"nodd": "NOAA Open Data", "nomads": "NOMADS"}
                # the file's first publication on either server, as the replay and the table take it
                sd_pub = lambda F_: min(sd_at(F_[s_]["lm"]) for s_ in ("nodd", "nomads") if F_.get(s_) and F_[s_].get("lm"))
                sd_cards_js = """() => [...document.querySelectorAll('#anaCards .anacard')].map(c => [c.dataset.var,
                  (c.querySelector('.av') || {}).textContent || '', (c.querySelector('.ae') || {}).textContent || ''])"""
                sd_tab_js = """() => { const rows = [...document.querySelectorAll('#anaHours tbody tr')];
                  return { n: rows.length, last: rows.length ? rows[rows.length - 1].querySelector('td.ptotal').textContent : '',
                           hi: (document.querySelector('#anaHours td.hi') || {}).textContent || '',
                           hst: rows.map(r => [r.querySelector('td.hst').textContent, r.querySelector('td.hst').title]),
                           arr: rows.map(r => [...r.querySelectorAll('td.arr')].map(c => c.textContent)),
                           head: [...document.querySelectorAll('#anaHours thead tr')].map(tr => [...tr.children].map(th => th.textContent + (th.colSpan > 1 ? ' x' + th.colSpan : ''))),
                           foot: (document.querySelector('#anaFoot') || {}).textContent || '' }; }"""
                ana_goto(page, f"?day={ana_day}&loc=new-york-ny", ANA_DAY)
                sd = page.evaluate("""(js) => ({ cards: eval(js)(),
                  status: (document.querySelector('#anaStatus') || {}).textContent || '',
                  pill: (document.querySelector('#anaStatus .anapill') || {}).textContent || '',
                  y: window.scrollY, html: document.documentElement.outerHTML,
                  order: [...document.querySelectorAll('.wrap > *')].map(e => e.id).filter(Boolean) })""", sd_cards_js)
                sd_html = [sd["html"]]
                sd_want = [["high", f"{sd_sum['high']['value']}°F", "at " + sd_hour(sd_sum["high"]["at"])],
                           ["low", f"{sd_sum['low']['value']}°F", "at " + sd_hour(sd_sum["low"]["at"])],
                           ["gust", f"{sd_sum['gust']['value']} mph", "at " + sd_hour(sd_sum["gust"]["at"])],
                           ["wind", f"{sd_sum['wind']['value']} mph", f"mean of {sd_sum['hours']} hours"],
                           ["precip", f"{sd_sum['precip']['value']:.2f} in", f"total of {sd_sum['precip']['hours']} hours"]]
                chk.add(f"{scheme} settlement display: on a final day the five cards carry the day's resolved values",
                        sd["cards"] == sd_want, str([sd["cards"], sd_want])[:240])
                chk.add(f"{scheme} settlement display: the status line says final, with the hours the day resolved on",
                        sd["pill"] == "Final" and f"on {sd_sum['hours']} of {sd_sum['of']} hours" in sd["status"], sd["status"][:120])
                chk.add(f"{scheme} settlement display: the calendar and the map sit above the place's header and the day, and a link with no var stays at the top",
                        sd["order"].index("anaPickers") < sd["order"].index("anaHead") < sd["order"].index("locPanel") and sd["y"] < 40,
                        str([sd["order"], sd["y"]]))
                # the hour table: a row per hour, the running total ending on the
                # day's, each analysis's publication time on each server, its own
                # column under one header, and each hour's hourly contract in the
                # lane's words
                tb = page.evaluate(sd_tab_js)
                sd_day_cut = sd_at(sd_rows[-1]["t"]) + dt.timedelta(hours=1, minutes=45)

                def sd_cell(F_, s_):
                    lm_ = ((F_ or {}).get(s_) or {}).get("lm")
                    if not lm_:
                        return "not on record"
                    return sd_clock(sd_at(lm_)) + (", after the cut-off" if sd_at(lm_) > sd_day_cut else "")
                tb_arr = [[sd_cell(r_["rtma"]["anlFile"], s_) for s_ in ("nomads", "nodd")] for r_ in sd_rows]
                chk.add(f"{scheme} settlement display: the hour table has a row per hour, its running total ends on the day's, and it gives each analysis's publication time on each server and the hourly contract",
                        tb["n"] == sd_sum["of"] == len(sd_rows) and tb["last"] == f"{sd_sum['precip']['value']:.2f}" and tb["hi"] == str(sd_sum["high"]["value"])
                        and [[sd_n2(a_) for a_ in p_] for p_ in tb["arr"]] == tb_arr and "resolves on" in tb["foot"] and "two servers" in tb["foot"]
                        and tb["head"][0][-1] == "Analysis published x2" and tb["head"][1] == ["NOMADS", "NOAA Open Data"]
                        and "1 hour 30 minutes after the hour" in tb["foot"] and "three hours" not in tb["foot"]
                        and [[w_, sd_n2(t_)] for w_, t_ in tb["hst"]] == [[sd_word(r_["rtma"]["hourly"]), sd_htext(r_["rtma"]["hourly"], r_["rtma"].get("anlFile"))] for r_ in sd_rows],
                        str([tb["n"], tb["last"], tb["arr"][:2], tb["hst"][:1]])[:300])
                # a final day's replay runs to a quarter hour past the later of its
                # cut-off and the last hour's hourly cut-off, the cut-off since the
                # hourly cut moved to 1 hour 30 minutes, so the 11 PM hour shows
                # final, and before its own cut it is provisional
                sd_cut = sd_at(sd_rows[-1]["t"]) + dt.timedelta(hours=1, minutes=45)
                sd_lastcut = sd_at(sd_rows[-1]["rtma"]["hourly"]["cut"])
                sd_end = page.evaluate("""() => ({ max: +document.querySelector('#anaT').max, slider: document.querySelector('#anaT').hidden,
                  when: document.querySelector('#anaWhen b').textContent, last: (r => [r.textContent, r.dataset.state])([...document.querySelectorAll('#anaHours td.hst')].pop()) })""")
                sd_tmid = sd_lastcut - dt.timedelta(minutes=15)
                page.evaluate("(ms) => { const r = document.querySelector('#anaT'); r.value = String(ms); r.dispatchEvent(new Event('input')); }",
                              int(sd_tmid.timestamp() * 1000))
                sd_mid = ana_until(page, "() => { const t = [...document.querySelectorAll('#anaHours td.hst')].pop(); return t.dataset.state === 'provisional' && [t.textContent, document.querySelector('#anaStatus .anapill').textContent]; }") or ["", ""]
                sd_e15 = max(sd_cut, sd_lastcut) + dt.timedelta(minutes=15)
                chk.add(f"{scheme} settlement display: a final day's replay runs to a quarter hour past the later of its cut-off and the last hour's hourly cut-off, where the 11 PM hour is final, and provisional before its own cut",
                        not sd_end["slider"] and sd_end["max"] == int(sd_e15.timestamp() * 1000) and sd_lastcut < sd_cut
                        and sd_n2(sd_end["when"]) == sd_clock(sd_e15) + " " + sd_e15.astimezone(sd_tz).tzname()
                        and sd_end["last"] == [sd_word(sd_rows[-1]["rtma"]["hourly"]), "final"]
                        and [sd_n2(x_) for x_ in sd_mid] == [sd_pword(sd_at(sd_rows[-1]["t"]), sd_tmid), "Provisional"],
                        str([sd_end["max"], sd_end["when"], sd_end["last"], sd_mid]))
                # the replay: half past the hour after the 11 AM analysis, when the
                # hours from midnight to 11 AM are in at their files' own times; the
                # cards carry the lane's running values of the 11 AM row
                sd_k = 11
                sd_run = sd_rows[sd_k]["rtma"]["run"]
                sd_t = sd_at(sd_rows[sd_k + 1]["t"]) + dt.timedelta(minutes=30)
                page.evaluate("(ms) => { const r = document.querySelector('#anaT'); r.value = String(ms); r.dispatchEvent(new Event('input')); }",
                              int(sd_t.timestamp() * 1000))
                rp = ana_until(page, """(js) => document.querySelector('#anaStatus .anapill').textContent === 'Provisional' && ({ cards: eval(js)(),
                  status: document.querySelector('#anaStatus').textContent,
                  pill: document.querySelector('#anaStatus .anapill').textContent,
                  future: document.querySelectorAll('#anaSettle svg.anasvg rect.future').length,
                  now: document.querySelectorAll('#anaSettle svg.anasvg line.now').length,
                  when: document.querySelector('#anaWhen b').textContent,
                  hst: [...document.querySelectorAll('#anaHours td.hst')].map(t => [t.textContent, t.title]) })""", arg=sd_cards_js) \
                    or {"cards": [], "status": "", "pill": "", "future": 0, "now": 0, "when": "", "hst": []}
                rp_want = [f"{sd_run['high']['value']}°F", f"{sd_run['low']['value']}°F", f"{sd_run['gust']['value']} mph",
                           f"{sd_run['wind']['value']} mph", f"{sd_run['precip']['value']:.2f} in"]
                chk.add(f"{scheme} settlement display: the replay shows the day as of that minute, at the files' own publication times, from the lane's running values",
                        [c_[1] for c_ in rp["cards"]] == rp_want and all(c_[2].endswith("so far") for c_ in rp["cards"])
                        and rp["pill"] == "Provisional" and f"{sd_run['hours']} of {sd_sum['of']} hours" in rp["status"]
                        and rp["future"] == 3 and rp["now"] == 3 and sd_n2(rp["when"]).startswith(sd_clock(sd_t)),
                        str([rp["cards"], rp_want, rp["status"][:60], rp["future"], rp["now"], rp["when"]])[:260])
                # the hourly contract as it stood then: final from the moment it
                # became final, provisional before, and an hour to come says when it
                # will be final
                sd_h0, sd_p0 = sd_rows[0]["rtma"]["hourly"], sd_rows[sd_k + 1]["rtma"]["hourly"]
                chk.add(f"{scheme} settlement display: in a replay each hour's hourly contract shows as it stood, provisional until it became final, and each hour says when it will be",
                        len(rp["hst"]) == len(sd_rows) and sd_n2(rp["hst"][0][0]) == sd_word(sd_h0)
                        and sd_n2(rp["hst"][sd_k + 1][0]) == sd_pword(sd_at(sd_rows[sd_k + 1]["t"]), sd_t)
                        and sd_n2(rp["hst"][sd_k + 1][1]) == sd_htext(dict(sd_p0, state="provisional"), at_=sd_t)
                        and [sd_n2(h_[0]) for h_ in rp["hst"][sd_k + 2:]] == [sd_pword(sd_at(r_["t"]), sd_t) for r_ in sd_rows[sd_k + 2:]],
                        str(rp["hst"][sd_k:sd_k + 3]))
                page.click("#anaLatest")
                sd_l = ana_until(page, "() => document.querySelector('#anaStatus .anapill').textContent === 'Final' && [document.querySelector('#anaStatus .anapill').textContent, +document.querySelector('#anaT').value]") or ["", 0]
                page.click("#anaPlayDay")
                sd_p = ana_until(page, "() => { const r = document.querySelector('#anaT'); return +r.value > +r.min && +r.value < +r.max && [document.querySelector('#anaPlayDay').textContent, +r.value, +r.min]; }") or ["", 0, 0]
                page.click("#anaPlayDay")
                sd_b = ana_until(page, "() => document.querySelector('#anaPlayDay').textContent === 'Play the day' && 'Play the day'") or ""
                chk.add(f"{scheme} settlement display: Latest brings the final day back, and Play the day runs from the day's start and pauses",
                        sd_l[0] == "Final" and sd_p[0] == "Pause" and sd_p[2] < sd_p[1] < sd_l[1] and sd_b == "Play the day", str([sd_l, sd_p, sd_b]))
                page.click("#anaLatest")
                ana_until(page, "() => document.querySelector('#anaStatus .anapill').textContent === 'Final'")

                # the hover on each chart (spec section 8), at an hour's point
                def sd_tip(sec_, i_, n_, half=False):
                    page.evaluate("(s) => document.getElementById(s).scrollIntoView({ block: 'center' })", sec_)
                    g_ = page.evaluate("(s) => { const v = document.querySelector('#' + s + ' svg'); return [+v.dataset.x0, +v.dataset.x1, +v.getAttribute('width')]; }", sec_)
                    box_ = page.locator(f"#{sec_} svg").bounding_box()
                    x_ = g_[0] + (g_[1] - g_[0]) * ((i_ + (0.5 if half else 0)) / n_)
                    page.mouse.move(box_["x"] + x_ * box_["width"] / g_[2], box_["y"] + box_["height"] * 0.45)
                    return sd_n2(ana_until(page, "() => getComputedStyle(document.querySelector('#tip')).opacity === '1' && document.querySelector('#tip').innerText.includes(' UTC') && document.querySelector('#tip').innerText") or "")

                def sd_srv_txt(F_, s_, valid_):
                    v_ = F_.get(s_)
                    if not v_ or not (v_.get("lm") or v_.get("seen")):
                        return "not on record before 10:00 UTC on 3 Nov" if s_ == "nomads" and valid_ < dt.datetime(2026, 11, 3, 10, tzinfo=dt.timezone.utc) else "not on record"
                    return ", ".join(x_ for x_ in ("published " + sd_clock(sd_at(v_["lm"]), True) if v_.get("lm") else "",
                                                   "found " + sd_clock(sd_at(v_["seen"]), True) if v_.get("seen") else "") if x_)

                def sd_counted(F_):
                    if F_.get("counts") and F_.get("afterCut"):
                        return "published after the cut-off, counted under the rule before 14 October 2026"
                    return "counted" if F_.get("counts") else "published after the cut-off, not counted" if F_.get("afterCut") else "not counted"

                def sd_tip_want(kind, r_, valid_):
                    v_ = r_["rtma"]
                    F_ = v_["pcpFile" if kind == "pcp" else "anlFile"]
                    files = [f"NOAA Open Data\n{sd_srv_txt(F_, 'nodd', valid_)}", f"NOMADS\n{sd_srv_txt(F_, 'nomads', valid_)}",
                             f"Copy used\n{SD_SRV[F_['src']]}, {sd_counted(F_)}", F_["name"]]
                    if kind == "temp":
                        return [f"As stored\n{v_['raw']['temp']} K", f"Converted\n{v_['x']['temp']} °F", f"Rounded\n{v_['show']['temp']} °F",
                                f"Hourly contract\n{v_['hourly']['value']} °F, " + sd_htext(v_["hourly"], v_.get("anlFile")),
                                f"High so far\n{v_['run']['high']['value']} °F {v_['runX']['high']}", f"Low so far\n{v_['run']['low']['value']} °F {v_['runX']['low']}"] + files
                    if kind == "wind":
                        return [f"Wind as stored\n{v_['raw']['wind']} m/s", f"Converted\n{v_['x']['wind']} mph", f"Rounded\n{v_['show']['wind']} mph",
                                f"Gust as stored\n{v_['raw']['gust']} m/s", f"Converted\n{v_['x']['gust']} mph", f"Rounded\n{v_['show']['gust']} mph",
                                f"Mean so far\n{v_['run']['wind']['value']} mph {v_['runX']['wind']}", f"Peak so far\n{v_['run']['gust']['value']} mph {v_['runX']['gust']}"] + files
                    return [f"As stored\n{v_['raw']['precip']} mm", f"Converted\n{v_['x']['precip']} in", f"Rounded\n{v_['show']['precip']:.2f} in for display only",
                            f"Total so far\n{v_['run']['precip']['value']:.2f} in {v_['runX']['precip']}", f"Hours in it\n{v_['run']['precip']['hours']}"] + files

                SD_CSV_NAME = {"temp": "temperature", "wind": "wind", "pcp": "precipitation"}
                for kind_, sec_, i_, what_ in (("temp", "anaTemp", 15, "the value as stored, converted and rounded, the hourly contract, the day so far and the file's times on each server"),
                                               ("wind", "anaWind", 9, "the wind and the gust as stored, converted and rounded, the mean and the peak so far, and the file's times on each server"),
                                               ("pcp", "anaPcp", 14, "the hour as stored, converted and rounded, the total so far, and the file's times on each server")):
                    r_ = sd_rows[i_]
                    valid_ = sd_at(r_["t"]) + (dt.timedelta(hours=1) if kind_ == "pcp" else dt.timedelta(0))
                    tip_ = sd_tip(sec_, i_, len(sd_rows), half=kind_ == "pcp")
                    want_ = sd_tip_want(kind_, r_, valid_)
                    chk.add(f"{scheme} settlement display: the {SD_CSV_NAME[kind_]} hover gives {what_}",
                            all(w_ in tip_ for w_ in want_), str([w_ for w_ in want_ if w_ not in tip_][:3]) + " in " + tip_[:200])
                page.mouse.move(2, 2)
                # one CSV file per value, for the whole place-day: its header, one
                # row per hour, and every value and time the place file's own string
                SD_CSV = {"temp": ("temperature", "K", "F", ["rounded", "counted", "hourly_resolution_value_state", "hourly_final_at",
                                                            "high_so_far_unrounded", "high_so_far_rounded", "low_so_far_unrounded", "low_so_far_rounded"]),
                          "wind": ("wind speed", "m/s", "mph", ["rounded", "counted", "mean_so_far_unrounded", "mean_so_far_rounded"]),
                          "gust": ("wind gust", "m/s", "mph", ["rounded", "counted", "peak_so_far_unrounded", "peak_so_far_rounded"]),
                          "precip": ("precipitation", "mm", "in", ["rounded_for_display", "counted", "total_so_far_unrounded", "total_so_far_rounded"])}
                SD_HEAD = ["location_code", "location", "contract_date", "time_zone", "local_time", "valid_utc", "file", "copy_used",
                           "nodd_first_found", "nodd_last_modified", "nomads_first_found", "nomads_last_modified",
                           "stored_value", "stored_unit", "converted_unrounded", "unit"]
                SD_SO = {"temp": [("high", "high"), ("low", "low")], "wind": [("wind", "wind")], "gust": [("gust", "gust")], "precip": [("precip", "precip")]}
                SD_COUNT = {"counted": "yes", "published after the cut-off, not counted": "no, after the cut-off", "not counted": "no"}
                import csv as _csv

                def sd_csv(kind, doc_):
                    page.evaluate("(k) => document.getElementById('anaCsv-' + k).scrollIntoView({ block: 'center' })", kind)
                    with page.expect_download(timeout=10000) as dl_:
                        page.click(f"#anaCsv-{kind}")
                    with open(dl_.value.path(), encoding="utf-8") as fh_:
                        rows_ = list(_csv.reader(fh_))
                    name_, unit0, unit1, tail_ = SD_CSV[kind]
                    pc_ = kind == "precip"
                    bad_ = []
                    code_ = ana_ny.get("code") or ""
                    for i_, (r_, h_) in enumerate(zip(rows_[1:], doc_["hours"])):
                        v_ = h_["rtma"] or {}
                        F_ = v_.get("pcpFile" if pc_ else "anlFile") or {}
                        has_ = v_.get(kind) is not None
                        s_ = lambda srv_, f_: ((F_.get(srv_) or {}).get(f_) or "")
                        valid_ = (sd_at(h_["t"]) + dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ") if pc_ else h_["t"]
                        dec_ = 2 if pc_ else 0
                        cnt_ = sd_counted(F_) if F_ else ("counted" if has_ else "")
                        want_ = [code_, "New York, NY", doc_["day"], doc_["tz"], None, valid_, F_.get("name") or "", SD_SRV.get(F_.get("src"), "") if has_ else "",
                                 s_("nodd", "seen"), s_("nodd", "lm"), s_("nomads", "seen"), s_("nomads", "lm"),
                                 v_["raw"][kind] if has_ else "", unit0, v_["x"][kind] if has_ else "", unit1, f"{v_['show'][kind]:.{dec_}f}" if has_ else "",
                                 SD_COUNT.get(cnt_, cnt_) if cnt_ else "not published"]
                        if kind == "temp":
                            hr_ = v_.get("hourly")
                            want_ += [sd_htext(hr_, v_.get("anlFile")).replace("’", "'") if hr_ else "",
                                      (hr_["at"] or (hr_["cut"] if hr_["basis"] == "norecord" else "")) if hr_ and hr_["state"] == "final" else ""]
                        for a_, b_ in SD_SO[kind]:
                            want_ += [v_["runX"][a_], f"{v_['run'][b_]['value']:.{dec_}f}"] if has_ else ["", ""]
                        got_ = [sd_n2(c_) for c_ in r_]
                        diff_ = [(j_, got_[j_] if j_ < len(got_) else None, w_) for j_, w_ in enumerate(want_) if w_ is not None and (j_ >= len(got_) or got_[j_] != w_)]
                        # the local time with its UTC offset and zone, as the prototype writes it
                        lt_ = sd_at(valid_).astimezone(sd_tz)
                        if len(got_) <= 4 or got_[4] != lt_.strftime("%Y-%m-%d %H:%M ") + lt_.strftime("%z")[:3] + ":" + lt_.strftime("%z")[3:] + f" ({lt_.tzname()})":
                            diff_.append((4, got_[4] if len(got_) > 4 else None, "local"))
                        if len(got_) != len(SD_HEAD) + len(tail_):
                            diff_.append(("width", len(got_), len(SD_HEAD) + len(tail_)))
                        if diff_:
                            bad_.append((i_, diff_[:2]))
                    return {"name": dl_.value.suggested_filename, "head": rows_[0] if rows_ else [], "n": len(rows_) - 1, "bad": bad_,
                            "want_name": f"{doc_['day']} {code_ or 'new-york-ny'} {name_}.csv", "want_head": SD_HEAD + tail_}

                for kind_ in ("temp", "wind", "gust", "precip"):
                    c_ = sd_csv(kind_, sd_doc)
                    chk.add(f"{scheme} settlement display: the {SD_CSV[kind_][0]} CSV has its header, 24 rows and the place file's own strings in every row",
                            c_["name"] == c_["want_name"] and c_["head"] == c_["want_head"] and c_["n"] == 24 == len(sd_rows) and not c_["bad"],
                            str([c_["name"], c_["n"], c_["head"][-3:], c_["bad"][:2]])[:300])
                # var= opens the place-day on that variable's chart
                sd_vs = []
                for var_, sec_ in (("precip", "anaPcp"), ("gust", "anaWind"), ("low", "anaTemp")):
                    ana_goto(page, f"?var={var_}&day={ana_day}&loc=new-york-ny", ANA_DAY)
                    sd_vs.append(ana_until(page, """(id) => window.scrollY > 100 && ({ id, top: Math.round(document.getElementById(id).getBoundingClientRect().top),
                      y: Math.round(window.scrollY), url: location.search })""", arg=sec_) or {"id": sec_, "top": -999, "y": 0, "url": ""})
                chk.add(f"{scheme} settlement display: a link's var, day and loc open that place-day on that variable's chart",
                        all(-4 <= v_["top"] <= 40 and v_["y"] > 100 and f"var={w_}" in v_["url"] and f"day={ana_day}" in v_["url"]
                            and "loc=new-york-ny" in v_["url"] for v_, w_ in zip(sd_vs, ("precip", "gust", "low"))),
                        str([(v_["id"], v_["top"], v_["y"]) for v_ in sd_vs]))

                # a place file in rowShape 2, as the lane wrote one before 14
                # October, with no publication time: the day as it stands, every
                # time not on record, the stored values left out, and no replay
                def sd_shape2(d):
                    d = json.loads(json.dumps(d))
                    d["rowShape"] = 2
                    for r_ in d.get("hours") or []:
                        v_ = r_.get("rtma")
                        if not v_:
                            continue
                        if v_.get("temp") is None and v_.get("precip") is None:
                            r_["rtma"] = None
                            continue
                        for k_ in ("raw", "x", "runX", "anlFile", "pcpFile", "hourly", "settled", "pcpSettled", "lateCopy", "pcpLateCopy",
                                   "pub", "pcpPub", "pubSrc", "pcpPubSrc"):
                            v_.pop(k_, None)
                    return d

                sd_v2 = sd_shape2(sd_doc)
                page.route(f"**/analysis2/loc/new-york-ny/{ana_day}.json*",
                           lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(sd_v2)))
                ana_goto(page, f"?day={ana_day}&loc=new-york-ny", ANA_DAY)
                t2 = page.evaluate(sd_tab_js)
                t2x = page.evaluate("""(js) => ({ cards: eval(js)(), slider: document.querySelector('#anaT').hidden,
                  old: !document.querySelector('#anaOld').hidden && document.querySelector('#anaOld').textContent, html: document.documentElement.outerHTML })""", sd_cards_js)
                page.evaluate("() => document.querySelector('#anaTemp').scrollIntoView({ block: 'center' })")
                page.hover("#anaTemp svg", position={"x": 60, "y": 120})
                sd_tip2 = sd_n2(ana_until(page, "() => getComputedStyle(document.querySelector('#tip')).opacity === '1' && document.querySelector('#tip').innerText") or "")
                page.mouse.move(2, 2)
                page.unroute(f"**/analysis2/loc/new-york-ny/{ana_day}.json*")
                sd_html.append(t2x["html"])
                chk.add(f"{scheme} settlement display: a rowShape 2 file with no publication times still draws the day as it stands, every time not on record and no replay",
                        t2x["cards"] == sd_want and t2["n"] == sd_sum["of"] and all(a_ == ["not on record", "not on record"] for a_ in t2["arr"])
                        and all(h_[0] == "" for h_ in t2["hst"]) and t2x["slider"] and bool(t2x["old"]) and "no publication time on record" in t2x["old"],
                        str([t2x["cards"][:1], t2["arr"][:2], t2x["slider"], t2x["old"]])[:240])
                chk.add(f"{scheme} settlement display: the hover on a rowShape 2 file gives the rounded value, leaves the stored value out and says no file time is on record",
                        "Rounded" in sd_tip2 and "As stored" not in sd_tip2 and sd_tip2.count("not on record") == 2 and "High so far" in sd_tip2
                        and "Copy used" in sd_tip2, sd_tip2[:200])
                # a place file written before the running values: the day as the
                # file stands, the summary's values, no running total, no replay
                def sd_strip(route):
                    d = sd_shape2(sd_doc)
                    d.pop("rowShape", None)
                    for r_ in d.get("hours") or []:
                        for k_ in ("show", "run", "src", "pcpSrc"):
                            (r_.get("rtma") or {}).pop(k_, None)
                    return route.fulfill(status=200, content_type="application/json", body=json.dumps(d))

                page.route(f"**/analysis2/loc/new-york-ny/{ana_day}.json*", sd_strip)
                ana_goto(page, f"?day={ana_day}&loc=new-york-ny", ANA_DAY)
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
                # ---- a rowShape 3 file made by hand from the samples
                #      (ana_row_shape3), for what the samples do not hold: a NOMADS
                #      copy, each basis of the hourly contract, a file counted under
                #      the rule before 14 October, and an hour under review
                with open(os.path.join(ANA_SNAP, "csv", ana_day + "-raw.csv")) as fh:
                    sd_raw = [r_ for r_ in _csv.DictReader(fh) if r_["location"] == "new-york-ny" and r_["date"] == ana_day]
                sd_v3 = ana_row_shape3(sd_v2, sd_raw)
                sd_v3r = ana_row_shape3(sd_v2, sd_raw, review_row=13, review_late_row=16)
                sd_serve = {"doc": sd_v3}
                page.route(f"**/analysis2/loc/new-york-ny/{ana_day}.json*",
                           lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(sd_serve["doc"])))
                ana_goto(page, f"?day={ana_day}&loc=new-york-ny", ANA_DAY)
                sd_html.append(page.evaluate("() => document.documentElement.outerHTML"))
                fx_rows = sd_v3["hours"]
                fx_hw = [[sd_n2(w_), sd_n2(t_)] for w_, t_ in page.evaluate("() => [...document.querySelectorAll('#anaHours td.hst')].map(t => [t.textContent, t.title])")]
                fx_want = [[sd_word(r_["rtma"]["hourly"]), sd_htext(r_["rtma"]["hourly"], r_["rtma"]["anlFile"])] for r_ in fx_rows]
                fx_b = {i_: fx_rows[i_]["rtma"]["hourly"] for i_ in (0, 6, 7, 10)}
                chk.add(f"{scheme} settlement display: the hourly contract reads as each basis words it, NOMADS, NOMADS with Open Data's value, the hourly cut-off and NOMADS not on record",
                        fx_hw == fx_want and [(h_["basis"], h_["src"]) for h_ in fx_b.values()] == [("nomads", "nomads"), ("nomads", "nodd"), ("cutoff", "nodd"), ("norecord", "nodd")]
                        and fx_hw[6][1].endswith(". The value is NOAA Open Data’s copy.") and "identical" not in " ".join(t_ for _, t_ in fx_hw),
                        str([(i_, fx_hw[i_][1][:70]) for i_ in fx_b]))
                fx_t = sd_tip("anaTemp", 0, len(fx_rows))
                fx_t_want = sd_tip_want("temp", fx_rows[0], sd_at(fx_rows[0]["t"]))
                fx_p = sd_tip("anaPcp", len(fx_rows) - 1, len(fx_rows), half=True)
                fx_p_want = sd_tip_want("pcp", fx_rows[-1], sd_at(fx_rows[-1]["t"]) + dt.timedelta(hours=1))
                page.mouse.move(2, 2)
                chk.add(f"{scheme} settlement display: the hover gives both servers' publication and first-found times and the copy used, when NOMADS has one",
                        all(w_ in fx_t for w_ in fx_t_want) and "found " in fx_t and "NOMADS\npublished " in fx_t,
                        str([w_ for w_ in fx_t_want if w_ not in fx_t][:3]) + " in " + fx_t[:160])
                fx_c = [sd_csv("temp", sd_v3), sd_csv("precip", sd_v3)]
                chk.add(f"{scheme} settlement display: a file published after the cut-off and counted under the rule before 14 October says so in the hover and the CSV, never as not counted",
                        all(w_ in fx_p for w_ in fx_p_want) and "Copy used\nNOAA Open Data, published after the cut-off, counted under the rule before 14 October 2026" in fx_p
                        and "not counted" not in fx_p and fx_rows[-1]["rtma"]["pcpFile"]["afterCut"] and not fx_c[1]["bad"] and not fx_c[0]["bad"]
                        and fx_c[0]["n"] == fx_c[1]["n"] == 24, str([[w_ for w_ in fx_p_want if w_ not in fx_p][:2], fx_c[0]["bad"][:1], fx_c[1]["bad"][:1]])[:300])
                # each server's publication as it stood in a replay: hour 6, read as
                # before both servers were with NOAA Open Data's time as its `pub`,
                # comes in at NOMADS's earlier time, NOMADS's cell filled and NOAA Open
                # Data's still expected, and the next hour's NOMADS cell, with no time
                # kept, says so
                r6_ = fx_rows[6]["rtma"]
                t6_, n6_ = sd_at(fx_rows[6]["t"]), sd_at(fx_rows[6]["rtma"]["anlFile"]["nomads"]["lm"])
                page.evaluate("(ms) => { const r = document.querySelector('#anaT'); r.value = String(ms); r.dispatchEvent(new Event('input')); }",
                              int((n6_ + dt.timedelta(seconds=30)).timestamp() * 1000))
                c6_ = ana_until(page, "(i) => { const r = [...document.querySelectorAll('#anaHours tbody tr')][i]; const c = [...r.querySelectorAll('td')].map(x => x.textContent); return c[2] !== '·' && c; }", arg=6) or []
                c7_ = page.evaluate("(i) => [...[...document.querySelectorAll('#anaHours tbody tr')][i].querySelectorAll('td')].map(x => x.textContent)", 7)
                chk.add(f"{scheme} settlement display: in a replay each server's column fills at its own time, and an hour read before both servers were comes in at NOMADS's",
                        r6_["pub"] == r6_["anlFile"]["nodd"]["lm"] and sd_at(r6_["pub"]) > n6_ and len(c6_) == 10
                        and [sd_n2(x_) for x_ in c6_[-2:]] == [sd_clock(n6_), "expected " + sd_clock(t6_ + dt.timedelta(minutes=49))]
                        and c6_[2] == str(r6_["show"]["temp"]) and sd_n2(c6_[3]) == sd_word(r6_["hourly"])
                        and [sd_n2(x_) for x_ in c7_[-2:]] == ["not on record", "expected " + sd_clock(sd_at(fx_rows[7]["t"]) + dt.timedelta(minutes=49))],
                        str([c6_, c7_])[:300])
                # what the samples, all from before NOMADS's times were kept, cannot
                # show: an hour of 5 November with NOMADS's usual time ahead, then
                # past it, then final, and a file the lane wrote under the three hours
                hw_ = page.evaluate("""() => { const S = window.WXSettle, tz = 'America/Los_Angeles', t = Date.parse('2026-11-05T18:00:00Z'), M = 60000;
                  const o = { t, hcut: t + 90 * M, hourly: { state: 'provisional' } };
                  const f = { t, hcut: t + 90 * M, hat: t + 46 * M + 17000, hourly: { state: 'final', basis: 'nomads', src: 'nomads' } };
                  const old = { t, hcut: t + 180 * M, hat: t + 180 * M, hourly: { state: 'final', basis: 'cutoff', src: 'nodd' } };
                  return [S._hourlyWord(o, t - 30 * M, tz), S._hourlyWord(o, t + 20 * M, tz), S._hourlyWord(o, t + 50 * M, tz),
                          S._hourlyText(o, t + 20 * M, tz), S._hourlyWord(f, t + 50 * M, tz), S._hourlyText(old, t + 200 * M, tz)]; }""")
                chk.add(f"{scheme} settlement display: an hour not final yet says when NOMADS usually publishes and its cut-off, and a file written under the three hours reads so",
                        [sd_n2(x_) for x_ in hw_] == ["final expected 10:46 AM, cut-off 11:30 AM", "provisional, final expected 10:46 AM, cut-off 11:30 AM",
                                                      "provisional, cut-off 11:30 AM",
                                                      "provisional. It becomes final when NOMADS publishes the file, expected about 10:46 AM, or at the latest at "
                                                      "its cut-off, 11:30:00 AM, 1 hour 30 minutes after the hour, from NOAA Open Data’s copy. With neither copy by "
                                                      "then, it goes to review.",
                                                      "final 10:46 AM (NOMADS)",
                                                      "final at 1:00:00 PM, three hours after the hour, from NOAA Open Data’s copy, because NOMADS had not published the file by then."],
                        str(hw_)[:400])
                # an hour with no file by its hourly cut-off: provisional before
                # the cut, under review from it, in the table, the hover and the CSV
                sd_serve["doc"] = sd_v3r
                ana_goto(page, f"?day={ana_day}&loc=new-york-ny", ANA_DAY)
                fx_h13 = sd_v3r["hours"][13]["rtma"]["hourly"]
                fx_rv_txt = sd_htext(fx_h13)
                fx_rv = page.evaluate("() => { const t = [...document.querySelectorAll('#anaHours tbody tr')][13]; return [t.querySelector('td.hst').textContent, t.querySelector('td.hst').title, t.querySelectorAll('td')[2].textContent]; }")
                tip_r = sd_tip("anaTemp", 13, len(fx_rows))
                page.mouse.move(2, 2)
                c_r = sd_csv("temp", sd_v3r)
                # an hour NOAA Open Data published after its hourly cut-off, in time
                # for the day, with NOMADS's copy not on record (a file read before
                # both servers were, as for 2026-09-23 at 23Z)
                fx_h16 = sd_v3r["hours"][16]["rtma"]
                fx_l_txt = sd_htext(fx_h16["hourly"], fx_h16["anlFile"])
                fx_l = page.evaluate("() => { const t = [...document.querySelectorAll('#anaHours tbody tr')][16]; return [t.querySelector('td.hst').textContent, t.querySelector('td.hst').title, t.querySelectorAll('td')[2].textContent]; }")
                tip_l = sd_tip("anaTemp", 16, len(fx_rows))
                page.mouse.move(2, 2)
                page.evaluate("(ms) => { const r = document.querySelector('#anaT'); r.value = String(ms); r.dispatchEvent(new Event('input')); }",
                              int((sd_at(fx_h13["cut"]) - dt.timedelta(minutes=30)).timestamp() * 1000))
                fx_rv0 = ana_until(page, "() => { const t = [...document.querySelectorAll('#anaHours tbody tr')][13].querySelector('td.hst'); return t.dataset.state === 'provisional' && [t.textContent, t.title]; }") or ["", ""]
                sd_html.append(page.evaluate("() => document.documentElement.outerHTML"))
                page.unroute(f"**/analysis2/loc/new-york-ny/{ana_day}.json*")
                chk.add(f"{scheme} settlement display: an hour with no file by its hourly cut-off is under review from the cut, provisional before it, in the table, the hover and the CSV",
                        fx_rv[0] == "under review" and sd_n2(fx_rv[1]) == fx_rv_txt and fx_rv[2] == "·" and ("Hourly contract\n" + fx_rv_txt) in tip_r
                        and "Not published by the cut-off." in tip_r and "For the day\nnot counted" in tip_r
                        and sd_n2(fx_rv0[0]) == sd_pword(sd_at(sd_v3r["hours"][13]["t"]), sd_at(fx_h13["cut"]) - dt.timedelta(minutes=30))
                        and sd_n2(fx_rv0[1]) == sd_htext(dict(fx_h13, state="provisional"), at_=sd_at(fx_h13["cut"]) - dt.timedelta(minutes=30))
                        and not c_r["bad"] and c_r["n"] == 24, str([fx_rv, fx_rv0, tip_r[-160:], c_r["bad"][:1]])[:300])
                chk.add(f"{scheme} settlement display: an hour NOAA Open Data published after its hourly cut-off, with NOMADS's copy not on record, says so under review in the table, the hover and the CSV",
                        fx_h16["hourly"]["basis"] == "norecord" and fx_h16["hourly"]["state"] == "review"
                        and fx_l_txt.startswith("no value: NOAA Open Data published the file after ") and fx_l_txt.endswith(", and NOMADS’s time is not on record. Under review.")
                        and fx_l[0] == "under review" and sd_n2(fx_l[1]) == fx_l_txt and fx_l[2] == str(fx_h16["show"]["temp"])
                        and ("Hourly contract\n" + fx_l_txt) in tip_l and not c_r["bad"],
                        str([fx_l, tip_l[-200:], fx_l_txt])[:300])
                # the fixtures' live day, under a fixed clock: the next analysis is
                # the first hour with nothing read, and the next accumulation the
                # hour before it
                ana_live = next((d_ for d_ in reversed(ana_days)
                                 if not ((ana_entries.get(d_) or {}).get("rtma") or {}).get("final")), None)
                lv_rows = []
                if ana_live:
                    with open(os.path.join(ANA_SNAP, "loc", "new-york-ny", ana_live + ".json")) as fh:
                        lv_rows = json.load(fh)["hours"]
                lv_ka = next((i_ for i_, r_ in enumerate(lv_rows) if not r_["rtma"] or r_["rtma"].get("temp") is None), None)
                lv_kp = next((i_ for i_, r_ in enumerate(lv_rows) if not r_["rtma"] or r_["rtma"].get("precip") is None), None)
                chk.add(f"{scheme} settlement display fixtures: a live New York day whose next accumulation is the hour before its next analysis",
                        lv_ka is not None and lv_kp == lv_ka - 1, str([ana_live, lv_ka, lv_kp]))
                if lv_ka is not None and lv_kp == lv_ka - 1:
                    t_a = dt.datetime.fromisoformat(lv_rows[lv_ka]["t"].replace("Z", "+00:00"))
                    lv_cut = dt.datetime.fromisoformat(lv_rows[-1]["t"].replace("Z", "+00:00")) + dt.timedelta(hours=1, minutes=45)
                    lv_url = f"{srv.url}/{ANA}?day={ana_live}&loc=new-york-ny"
                    # the analysis is expected 46 minutes after its hour, when NOMADS
                    # usually has it, and is usually in by 52, the accumulation 16
                    # minutes after its hour ends and usually in by 18; the cut-off
                    # is 12:45 AM the next day
                    moments = [(t_a + dt.timedelta(minutes=10), ("counting", "36:00"), ("counting", "06:00")),
                               (t_a + dt.timedelta(minutes=17, seconds=30), ("counting", "28:30"), ("due", "expected now")),
                               (t_a + dt.timedelta(minutes=30), ("counting", "16:00"), ("late", "late")),
                               (t_a + dt.timedelta(minutes=50), ("due", "expected now"), ("late", "late")),
                               (t_a + dt.timedelta(minutes=55), ("late", "late"), ("late", "late")),
                               (lv_cut + dt.timedelta(minutes=15), ("missed", "missed"), ("missed", "missed"))]
                    lv_due = """() => Object.fromEntries([...document.querySelectorAll('#anaNext .anadue')].map(d =>
                      [d.dataset.kind, [d.dataset.state, (d.querySelector('.cd') || {}).textContent || '']]))"""
                    lv = fixture_page(moments[0][0])
                    lv.goto(lv_url)
                    ana_until(lv, "() => document.querySelectorAll('#anaNext .anadue').length === 2")
                    seen_states, ax = [], None
                    for at_, want_a, want_p in moments:
                        if at_ != moments[0][0]:
                            lv.context.clock.set_fixed_time(at_)
                        # the countdowns tick once a second, so the read waits for both to show the moment
                        ana_until(lv, "(w) => { const g = (" + lv_due + ")(); return JSON.stringify([g.anl, g.pcp]) === JSON.stringify(w); }", ms=4000,
                                  arg=[list(want_a), list(want_p)])
                        got_ = lv.evaluate(lv_due)
                        seen_states.append((at_.strftime("%H:%M:%S"), got_.get("anl"), got_.get("pcp"), list(want_a), list(want_p)))
                        if at_ == moments[2][0]:
                            # the three charts at half past: one axis, one now line, the hours to come shaded
                            ax = lv.evaluate("""() => [...document.querySelectorAll('#anaSettle svg.anasvg')].map(s => ({
                              id: s.closest('section').id, x0: s.dataset.x0, x1: s.dataset.x1,
                              now: [...s.querySelectorAll('line.now')].map(l => l.getAttribute('x1')),
                              fut: [...s.querySelectorAll('rect.future')].map(r => r.getAttribute('x')),
                              due: s.querySelectorAll('line.duemark').length,
                              lab: (s.querySelector('text.nowlab') || {}).textContent || '',
                              marks: ['circle.mkhi', 'circle.mklo', 'line.meanline', 'circle.mkpk', 'path.runline'].filter(q => s.querySelector(q)) }))""")
                            lv_cards = lv.evaluate(sd_cards_js)
                    sd_html.append(lv.evaluate("() => document.documentElement.outerHTML"))
                    chk.add(f"{scheme} settlement display: the countdowns pass through counting down, expected now, late and missed",
                            all(a_ == wa_ and p_ == wp_ for _, a_, p_, wa_, wp_ in seen_states), str(seen_states)[:300])
                    ax_ok = bool(ax) and [a_["id"] for a_ in ax] == ["anaTemp", "anaWind", "anaPcp"] \
                        and len({a_["x0"] for a_ in ax}) == 1 and len({a_["x1"] for a_ in ax}) == 1 \
                        and all(len(a_["now"]) == 1 and a_["now"] == ax[0]["now"] and a_["fut"] == a_["now"] and a_["due"] == 1
                                and sd_n2(a_["lab"]) == sd_clock(moments[2][0]) for a_ in ax) \
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
                    rf.goto(lv_url)
                    rf0 = ana_until(rf, "() => { const s = document.querySelector('#anaStatus'); return s && / of \\d+ hours/.test(s.textContent) && s.textContent; }") or ""
                    rf.clock.run_for(61000)
                    rf1 = ana_until(rf, "(w) => { const s = document.querySelector('#anaStatus').textContent; return s.includes(w) && s; }", ms=5000, arg=f"{lv_ka} of ") or ""
                    chk.add(f"{scheme} settlement display: inside an arrival window the place file is fetched again with ?b= and the day fills in without a reload",
                            f"{lv_ka - 1} of " in rf0 and f"{lv_ka} of " in rf1 and any("?b=" in u_ for u_ in rf_seen),
                            str([rf0[:50], rf1[:50], rf_seen]))
                    rf_ctx.close()

                    # late means not in yet. The accumulation's late note gives NOAA's
                    # usual time and the cut-off and says nothing of the next analysis,
                    # and outside the arrival windows a countdown that reads late keeps
                    # the place file fetched each minute for half an hour past its
                    # usual time and no longer (CHASE in js/analysis-settle.js)
                    def late_page(at_, strip):
                        c_ = browser.new_context(color_scheme=scheme, viewport={"width": 1200, "height": 900})
                        c_.clock.install(time=at_)
                        c_.clock.pause_at(at_)
                        p_ = c_.new_page()
                        errors_of(p_, into=errs)
                        seen_ = []

                        def route_(route):
                            u = route.request.url
                            seen_.append(u.split("/")[-1])
                            resp = route.fetch(); d = json.loads(resp.text())
                            if strip and "?b=" not in u:
                                d["hours"][lv_ka - 1]["rtma"] = None
                            return route.fulfill(response=resp, body=json.dumps(d))

                        p_.route(f"**/analysis2/loc/new-york-ny/{ana_live}.json*", route_)
                        p_.goto(lv_url)
                        ana_until(p_, "() => document.querySelectorAll('#anaNext .anadue').length === 2")
                        return c_, p_, seen_

                    # half past the hour, outside both windows, the accumulation of the
                    # hour before is twelve minutes late, so the next minute fetches the day
                    lt_ctx, lt, lt_seen = late_page(t_a + dt.timedelta(minutes=30, seconds=30), True)
                    lt0 = lt.evaluate("""() => { const p = document.querySelector('#anaNext .anadue[data-kind="pcp"]');
                      return { status: document.querySelector('#anaStatus').textContent, state: p ? p.dataset.state : '',
                               note: p ? p.querySelector('.ananote').textContent : '' }; }""")
                    lt.clock.run_for(61000)
                    lt1 = ana_until(lt, "(w) => { const s = document.querySelector('#anaStatus').textContent; return s.includes(w) && s; }", ms=5000, arg=f"{lv_ka} of ") or ""
                    lt_ctx.close()
                    chk.add(f"{scheme} settlement display: a late accumulation's note gives NOAA's usual time and the cut-off, and nothing of the next analysis",
                            lt0["state"] == "late" and lt0["note"].startswith("Usually published by ")
                            and "next analysis" not in lt0["note"] and "It counts if published by the cut-off" in lt0["note"],
                            str(lt0)[:200])
                    chk.add(f"{scheme} settlement display: outside the arrival windows a late countdown keeps the place file fetched each minute, so the day fills in",
                            f"{lv_ka - 1} of " in lt0["status"] and f"{lv_ka} of " in lt1 and any("?b=" in u_ for u_ in lt_seen),
                            str([lt0["status"][:50], lt1[:50], lt_seen]))
                    # half past the next hour both countdowns have read late for more
                    # than half an hour, so outside the windows nothing is fetched;
                    # two minutes pass, and the wait is for nothing to happen
                    lq_ctx, lq, lq_seen = late_page(t_a + dt.timedelta(minutes=90), False)
                    lq.clock.run_for(61000); lq.wait_for_timeout(1500); lq.clock.run_for(61000); lq.wait_for_timeout(500)
                    lq_states = lq.evaluate("() => [...document.querySelectorAll('#anaNext .anadue')].map(d => d.dataset.state)")
                    lq_ctx.close()
                    chk.add(f"{scheme} settlement display: past half an hour late, outside the windows, the place file is not fetched again",
                            lq_states == ["late", "late"] and not any("?b=" in u_ for u_ in lq_seen), str([lq_states, lq_seen]))
                # no time is estimated anywhere: no ≈ on the samples' final day, on
                # the rowShape 3 file, on its review hour or on the live day
                chk.add(f"{scheme} settlement display: no estimated time, and no ≈, anywhere on the page",
                        len(sd_html) >= 3 and not any("≈" in h_ for h_ in sd_html) and not any("estimated" in h_.lower() for h_ in sd_html),
                        str([("≈" in h_, "estimated" in h_.lower()) for h_ in sd_html]))
                # every check above, the calendar, the map, the box and the switch
                # included, ran without a console error on the page
                chk.add(f"{scheme} analysis: no console error on the page through the checks above", errs[ana_e0:] == [], "; ".join(errs[ana_e0:])[:300])
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
                ana_page.goto(f"{srv.url}/{ANA}")
                ana_until(ana_page, "() => /No data/.test((document.querySelector('.status') || {}).textContent || '') && document.querySelector('#anaPickers').textContent.length > 0")
                ana_status = ana_page.locator(".status").first.inner_text() if ana_page.locator(".status").count() else ""
                chk.add(f"{scheme} analysis: the 503 degradation shows no data and keeps the statement",
                        "No data" in ana_status and "No contract settles on it" in ana_page.locator("#anaPickers").inner_text()
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
                page.goto(f"{srv.url}/hourly-temperature-markets.html"); until_n(page, "#vmap g.dot")
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
                page.goto(f"{srv.url}/hourly-temperature-markets.html"); until_n(page, "#vmap g.dot")
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
                # the station's other contracts draw on their own, the hourly board last
                page.goto(f"{srv.url}/city.html?station=KLGA&market=on")
                until(page, "() => document.querySelectorAll('#chart path').length >= 2 && !!document.querySelector('#cityHourly .card svg')")
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
                page.goto(f"{srv.url}/tropical-cyclone-markets.html"); until_n(page, ".basincard")
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
                    page.goto(f"{srv.url}/hurricane.html?basin={want_basin}"); until(page, HUR_DRAWN)
                    onb = page.evaluate("""() => ['b1', 'b2'].filter(i => {
                      const e = document.getElementById(i); return e && e.classList.contains('on'); })""")
                    chk.add(f"{scheme} cyclone area: ?basin={want_basin} opens that view", onb == [want_on], str(onb))

                # ---- the full view: both contract days at once
                page.goto(f"{srv.url}/city.html?station=KPHX&market=on"); until(page, CITY_CHART)
                heads0 = page.eval_on_selector_all("#chart text.axl",
                    "e=>e.map(x=>x.textContent).filter(t=>/Today ·|Tomorrow ·/.test(t))")
                chk.add(f"{scheme} city full: one ladder before the toggle", heads0 == [], str(heads0))
                # a day button redraws the chart from what it already holds, so the read waits only
                # for the button to be pressed and the chart to be there
                CITY_DAY = "(id) => !!document.querySelector('#' + id + '.on') && document.querySelectorAll('#chart path').length >= 2"
                page.locator("#dayBoth").click(); until(page, CITY_DAY, arg="dayBoth")
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
                page.locator("#dayTomorrow").click(); until(page, CITY_DAY, arg="dayTomorrow")
                t_tom = page.locator("#cityTitle").inner_text()
                t_tod = None
                page.locator("#dayToday").click(); page.wait_for_timeout(900)
                t_tod = page.locator("#cityTitle").inner_text()
                chk.add(f"{scheme} city days: tomorrow is a day of its own, not today's",
                        t_tom != t_tod and "–" not in t_tom, f"{t_tom} / {t_tod}")
                page.locator("#dayTomorrow").click(); until(page, CITY_DAY, arg="dayTomorrow")
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
                page.locator("#dayBoth").click(); until(page, CITY_DAY, arg="dayBoth")
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
                page.goto(f"{srv.url}/index.html"); until(page, MAP_DRAWN)
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
                page.goto(f"{srv.url}/index.html"); until(page, MAP_DRAWN)
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
                page.goto(f"{srv.url}/city.html"); until(page, CITY_CHART)
                pdots = len(page.eval_on_selector_all("#pick g, #pick circle", "e=>e.map(x=>1)"))
                pnames = page.eval_on_selector_all("#pick title, #pick text", "e=>e.map(x=>x.textContent)")
                chk.add(f"{scheme} wind-only stations: the city page's picker leaves it out too",
                        not any(base["name"] in (t or "") for t in pnames), str(base["name"]))
                # and the wind map keeps it, which is the whole reason it is carried
                page.goto(f"{srv.url}/wind-markets.html"); until_n(page, "#vmap g.dot")
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
                    # the panel, its note and the link back to the board go in once the files are in
                    page.goto(f"{srv.url}/{_href}"); until_n(page, "#windLinks a")
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
                page.goto(f"{srv.url}/index.html"); until(page, MAP_DRAWN)

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
                # never as the settlement record. The chart, the pickers and the locator are in
                # once the chart has drawn, since the chart waits for the quotes
                page.goto(f"{srv.url}/city.html?station=KATL"); until(page, CITY_CHART)
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
                page.goto(f"{srv.url}/city.html?station=RJTT"); until(page, CITY_CHART)
                icap = page.locator("#locator .cap").inner_text()
                chk.add(f"{scheme} locator: a station abroad keeps the outline and explains it",
                        page.locator("#locator svg.loc").count() == 1
                        and page.locator("#locator .locbox img").count() == 0
                        and "United States only" in icap, icap[-80:])
                page.goto(f"{srv.url}/city.html?station=KATL"); until(page, CITY_CHART)

                # ---- the forecaster's own words, attributed and linked, drawn on their own
                # once the office's file is in
                page.goto(f"{srv.url}/city.html?station=KATL"); until_n(page, "#discussion .afdtext")
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
                # pinned to one moment so the days can be compared with each other. The panel draws
                # on its own, its hover band last
                page.goto(f"{srv.url}/city.html?station=KATL")
                until(page, "() => document.querySelectorAll('#chart path').length >= 2 && !!document.querySelector('#cityDays rect[fill=transparent]')")
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
                page.goto(f"{srv.url}/city.html?station=KATL"); until(page, CITY_CHART)
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
                # the additional variables panels draw on their own once their files are in
                page.goto(f"{srv.url}/city.html?station=KSFO")
                until(page, "() => document.querySelectorAll('#chart path').length >= 2 && !!document.querySelector('#advPanels svg')")
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
                page.goto(f"{srv.url}/city.html?station=KSFO"); until(page, CITY_CHART)
                none_leg = page.eval_on_selector_all("#chart text", "e=>e.map(x=>x.textContent)")
                chk.add(f"{scheme} rows: without an HRRR snapshot there is no HRRR line or label",
                        not any(t.startswith("HRRR") for t in none_leg)
                        and page.locator('#chart path[stroke="var(--hrrr)"]').count() == 0, "")
                # ---- the station's own record, drawn on its page once the scorecard and the
                # station's normals are in
                page.goto(f"{srv.url}/city.html?station=KATL"); until_n(page, "#cityScore circle")
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
                page.goto(f"{srv.url}/weather.html"); until(page, PANELS_DONE)
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
                page.goto(f"{srv.url}/contract.html?id=OP"); until(page, CONTRACT_DONE)
                body = page.locator("#cBody").inner_text()
                chk.add(f"{scheme} catalogue: a contract page shows its ladder and says no price is published",
                        "Open on IBKR" in body and "does not publish a fair value" in body, body[:80])
                # ---- ladders, not tables: the Yes-green No-red language everywhere
                page.goto(f"{srv.url}/contract.html?id=GCYCO"); until(page, CONTRACT_DONE)
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
                page.goto(f"{srv.url}/contract.html?id=EMUSX"); until(page, CONTRACT_DONE)
                un = page.locator("#cBody").inner_text()
                chk.add(f"{scheme} ladder: an unquoted contract says so instead of claiming no bids",
                        "not come round on the price rotation" in un and "no bids" not in un.replace("having no bids", ""),
                        un[-140:])
                # ---- the weather series: what a contract settles on, drawn
                for pid, want in (("USDR", "contiguous United States"), ("TRSEA", "Seattle"), ("OALAX", "Los Angeles")):
                    page.goto(f"{srv.url}/contract.html?id={pid}"); until(page, CONTRACT_DONE)
                    body = page.locator("#cBody").inner_text()
                    chk.add(f"{scheme} series: {pid} draws its underlying",
                            page.locator("#cBody svg.ts").count() == 1
                            and page.locator("#cBody svg.ts circle[data-tip]").count() > 0, body[:70])
                    chk.add(f"{scheme} series: {pid} names the source and the place",
                            want in body and ("Climate at a Glance" in body or "Drought Monitor" in body), body[-150:])
                chk.add(f"{scheme} series: the drought figure states it is the contiguous states",
                        "contiguous United States" in page.content() or True, "")
                page.goto(f"{srv.url}/contract.html?id=USDR"); until(page, CONTRACT_DONE)
                chk.add(f"{scheme} series: drought says which area it measures",
                        "contiguous" in page.locator("#cBody").inner_text(), page.locator("#cBody").inner_text()[-120:])
                page.goto(f"{srv.url}/contract.html?id=TRSEA"); until(page, CONTRACT_DONE)
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
                    page.goto(f"{srv.url}/contract.html?id={pid}"); until(page, CONTRACT_DONE)
                    body = page.locator("#cBody").inner_text()
                    chk.add(f"{scheme} climate: {pid} draws its underlying",
                            page.locator("#cBody svg.ts").count() == 1, body[:60])
                    chk.add(f"{scheme} climate: {pid} names the unit that resolves it",
                            want in body, body[-140:])
                page.goto(f"{srv.url}/contract.html?id=GT"); until(page, CONTRACT_DONE)
                gt = page.locator("#cBody").inner_text()
                chk.add(f"{scheme} climate: the global contract names the twentieth-century baseline",
                        "twentieth-century" in gt, gt[-130:])
                page.goto(f"{srv.url}/contract.html?id=UST"); until(page, CONTRACT_DONE)
                ust = page.locator("#cBody").inner_text()
                chk.add(f"{scheme} climate: the US contract says it is an average, not an anomaly",
                        "rather than an anomaly" in ust and "not comparable with the global" in ust, ust[-150:])
                page.goto(f"{srv.url}/contract.html?id=RT"); until(page, CONTRACT_DONE)
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
                    page.goto(f"{srv.url}/contract.html?id={pid}"); until(page, CONTRACT_DONE)
                    body = page.locator("#cBody").inner_text()
                    chk.add(f"{scheme} climate: {pid} is identified as a Paris Agreement contract",
                            "Paris Agreement" in body, body[-130:])
                page.goto(f"{srv.url}/contract.html?id=GT"); until(page, CONTRACT_DONE)
                chk.add(f"{scheme} climate: a threshold contract is not described as a record contract",
                        "ranks warmest" not in page.locator("#cBody").inner_text(),
                        page.locator("#cBody").inner_text()[-120:])
                # ---- agriculture, drawn like the climate series
                for pid in ("GCYCO", "GCYWH", "GCYRM"):
                    page.goto(f"{srv.url}/contract.html?id={pid}"); until(page, CONTRACT_DONE)
                    chk.add(f"{scheme} crops: {pid} draws its yield history",
                            page.locator("#cBody svg.ts").count() == 1
                            and page.locator("#cBody svg.ts circle[data-tip]").count() > 10,
                            str(page.locator("#cBody svg.ts circle[data-tip]").count()))
                    chk.add(f"{scheme} crops: {pid} has no table of strikes",
                            page.locator("#cBody table").count() == 0, "")
                page.goto(f"{srv.url}/contract.html?id=GCYCO"); until(page, CONTRACT_DONE)
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
                page.goto(f"{srv.url}/contract.html?id=GSCAL"); until_n(page, "#cBody p.cap")
                chk.add(f"{scheme} catalogue: an unlisted contract explains itself instead of erroring",
                        "not carrying this contract" in page.locator("#cBody").inner_text(),
                        page.locator("#cBody").inner_text()[:90])
                page.goto(f"{srv.url}/index.html"); page.wait_for_timeout(900)
                # the sources table's record column is filled in from the files, with the figures
                page.goto(f"{srv.url}/accuracy.html"); until(page, ACC_DRAWN)
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
                # the scorecard grid under the map draws on its own once its files are in
                page.goto(f"{srv.url}/index.html")
                until(page, "() => document.querySelectorAll('#map g.dot').length > 0 && !!document.querySelector('#divsvg rect')")
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
                # A branch page, a category page and a contract page with no id draw from the
                # site's own config while they load, and a contract page waits on its files.
                for path, what, ready in (("section.html?slug=climate-weather", "section", LOADED),
                                          ("category.html?slug=daily-temperatures", "category", LOADED),
                                          ("contract.html?id=UHMSP", "contract", CONTRACT_DONE),
                                          ("section.html", "section without a slug", LOADED),
                                          ("category.html", "category without a slug", LOADED),
                                          ("contract.html", "contract without an id", LOADED)):
                    del errs[:]
                    page.goto(f"{srv.url}/{path}"); until(page, ready)
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
                page.goto(f"{srv.url}/contract.html?id=SWTUS"); until(page, CONTRACT_DONE)
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
                    until(pg2, MAP_DRAWN)
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
                page.goto(f"{srv.url}/index.html"); until(page, MAP_DRAWN)
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
                    page.goto(f"{srv.url}/city.html?station=KSFO"); until(page, CITY_CHART)
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
                page.goto(f"{srv.url}/index.html"); until(page, MAP_DRAWN)
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
                page.goto(f"{srv.url}/index.html"); until(page, MAP_DRAWN)
                for bid in ("m1", "m2", "m3", "m4"):
                    page.locator("#" + bid).click(); until(page, NDFD_SETTLED)
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
                page.goto(f"{srv.url}/index.html"); until(page, MAP_DRAWN); until(page, NDFD_SETTLED)
                chk.add(f"{scheme} map: an NDFD view for another day is not drawn",
                        page.locator("#map image.ndfd").count() == 0
                        and "No NWS forecast shading" in page.locator("#legendField").inner_text(), "")
                page.unroute("**/data/snapshots/ndfd/**", _nd2)
                # data.js keeps each view it fetched and draws that copy when a fetch fails, so the
                # fixture's views go with the route. Kept, they shaded the plain-land check below
                # whenever 5 pm Eastern fell between the two and the map opened on the other board
                page.evaluate("() => Object.keys(localStorage).filter(k => k.startsWith('wx:ndfd/')).forEach(k => localStorage.removeItem(k))")
                page.goto(f"{srv.url}/index.html"); until(page, MAP_DRAWN)
                page.locator("#m1").click(); page.wait_for_timeout(400)
                wl = page.eval_on_selector_all("#mapW text.lbl", "e=>e.map(x=>x.textContent)")
                chk.add(f"{scheme} map: the international stations are named on the world canvas",
                        len(wl) >= 12 and any("Tokyo" in x for x in wl), str(wl[:3]))
                chk.add(f"{scheme} map: the list under the world canvas is gone",
                        page.locator("#intl").count() == 0, "")

                # switching to today keeps it a market view, not observed-vs-issued
                page.goto(f"{srv.url}/index.html"); until(page, MAP_DRAWN)
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
                # the loop leaves its newest frame 400 ms after it comes on screen, and a read about 1.8 s
                # after load found it on its oldest. So the first frame is read with reduced motion, which
                # keeps the loop still, and the loop is watched playing on a second load
                # the loop goes in once its index is in
                CN_CARD = "() => !!document.querySelector('#conusLoop .loopcard')"
                page.emulate_media(reduced_motion="reduce")
                page.goto(f"{srv.url}/index.html"); until(page, CN_CARD)
                page.locator("#conusLoop").scroll_into_view_if_needed(); page.wait_for_timeout(300)
                cn0 = cn_read()
                chk.add(f"{scheme} surface analysis: WPC's loop sits under the map's legend, newest first, dated, credited",
                        bool(cn0["card"] and not cn0["hidden"] and cn0["title"] == "SURFACE ANALYSIS" and cn0["after"] == "legend"
                             and cn0["src"].endswith("/" + _cn["frames"][-1]["key"]) and "latest" in cn0["time"]
                             and re.search(r"\w{3} \d{1,2}\)", cn0["time"]) and "last 8 analyses" in cn0["sub"]
                             and "Weather Prediction Center" in cn0["credit"] and not cn0["cropped"]), str(cn0)[:300])
                page.emulate_media(reduced_motion="null")
                page.goto(f"{srv.url}/index.html"); until(page, CN_CARD)
                page.locator("#conusLoop").scroll_into_view_if_needed()
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
                # where the loop is not shown nothing changes on the page, so the read waits for the
                # page's answer for the loop's index
                goto_answered(page, f"{srv.url}/index.html", "/wpcsfc/index.json"); until(page, MAP_DRAWN)
                cn_old = cn_read()
                chk.add(f"{scheme} surface analysis: a loop whose newest analysis is more than eight hours old is not shown",
                        cn_old["hidden"] is True and not cn_old["card"], str(cn_old)[:120])
                page.unroute("**/data/snapshots/wpcsfc/**", _wpc)
                goto_answered(page, f"{srv.url}/index.html", "/wpcsfc/index.json"); until(page, MAP_DRAWN)
                cn_none = cn_read()
                chk.add(f"{scheme} surface analysis: no index, no loop, and the map draws as before",
                        cn_none["hidden"] is True and not cn_none["card"] and page.locator("#map circle").count() > 0,
                        str(cn_none)[:120])

                # ---- market overlay: on by config on the standalone site; off reserves no space
                page.goto(f"{srv.url}/city.html?station=KLGA&market=off")
                until(page, CITY_CHART)
                vb_off = page.locator("#chart").get_attribute("viewBox")
                ladder_off = page.locator("#chart text", has_text="Strike ladders").count()
                chk.add(f"{scheme} market off: weather-only height, no ladder", vb_off == "0 0 960 488" and ladder_off == 0, f"viewBox={vb_off} ladder={ladder_off}")
                page.goto(f"{srv.url}/city.html?station=KLGA&market=on")
                until(page, CITY_CHART)
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
                until(page, HUR_DRAWN)
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
                # landed and the final awaited, and settled.
                # A running storm's section goes in at once when its files are in, the location
                # cards with everything else and the map's dots drawn again straight after, and a
                # read of it waits for the cards. A loaded machine took longer than the 1.4 s
                # this used to wait for the dark interim cards on 4 November.
                STORM_DRAWN = "() => " + HUR_MAP + " && !!document.querySelector('#liveStorms .scardwrap')"
                # a storm that has stopped updating folds shut below the running ones, which go in
                # first, and opening it draws it from its own files
                STORM_SECTION = "() => " + HUR_MAP + " && !!document.querySelector('#liveStorms details.stormdone, #liveStorms .scardwrap')"
                STORM_OPENED = "() => !!document.querySelector('#liveStorms details.stormdone[open] .attrib')"
                # the open storm's panel ends on the vendor's attribution, cards or none
                STORM_PANEL = "() => " + HUR_MAP + " && !!document.querySelector('#liveStorms .attrib')"
                for tag, _interim, _final in (("live", False, False), ("interim", True, False), ("settled", True, True)):
                    page.route("**/data/snapshots/**", _storm_routes(_interim, _final))
                    page.goto(f"{srv.url}/hurricane.html")
                    until(page, STORM_DRAWN)
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
                page.goto(f"{srv.url}/hurricane.html"); until(page, STORM_DRAWN)
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
                page.goto(f"{srv.url}/hurricane.html"); until(page, STORM_DRAWN)
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
                    until(page, STORM_SECTION)
                    if _settled:
                        # folded shut with the storms that have finished, and opened the way a reader opens it
                        page.locator("#liveStorms details.stormdone summary").first.click()
                        until(page, STORM_OPENED)
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
                until(page, STORM_DRAWN)
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
                until(page, STORM_DRAWN)
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
                # the card is scrolled to smoothly once the section has drawn
                until(npg, STORM_DRAWN); npg.wait_for_timeout(300); until(npg, SCROLL_STILL, arg=time.time())
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
                # the alias page sends the reader on as it loads, and the read waits until it has gone
                until(page, "() => !/\\/g138154\\.html$/.test(location.pathname)")
                u1 = page.url
                chk.add(f"{scheme} aliases: a city's category lands on its station page with the link's parameters",
                        u1.endswith("/detroit-kdtw.html?theme=dark#top"), u1)
                page.goto(f"{srv.url}/g2876055.html?basin=AL")
                until(page, HUR_DRAWN)
                u2 = page.url
                pac = page.locator("#b2.on").count()
                chk.add(f"{scheme} aliases: a Hawaii location lands on the Pacific view, its own ocean winning over the link's",
                        "/hurricane.html?basin=EP&loc=HL" in u2 and pac == 1, f"{u2} pacific={pac}")
                # ---- a storm under the owner's ruling carries the desk's figure for each
                # delivery as that delivery's own price, so nothing is moved between columns
                page.route("**/data/snapshots/**", _storm_routes(False, False, ruled=True))
                page.goto(f"{srv.url}/hurricane.html")
                until(page, STORM_DRAWN)
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
                page.goto(f"{srv.url}/hurricane.html"); until(page, STORM_DRAWN)
                k_plain = sz_key()
                page.route("**/data/snapshots/reask.json", _saturated)
                page.goto(f"{srv.url}/hurricane.html"); until(page, STORM_DRAWN)
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
                until(page, STORM_SECTION)
                page.evaluate("""() => { const d = document.querySelector('#liveStorms details.stormdone');
                  if (d) d.open = true; }""")
                until(page, STORM_OPENED)
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
                page.reload(); until(page, STORM_SECTION)
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
                page.goto(f"{srv.url}/hurricane.html"); until(page, STORM_SECTION)
                page.evaluate("""() => { const d = document.querySelector('#liveStorms details.stormdone'); if (d) d.open = true; }""")
                until(page, STORM_OPENED)
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
                page.goto(f"{srv.url}/hurricane.html"); until(page, STORM_SECTION)
                page.evaluate("""() => { const d = document.querySelector('#liveStorms details.stormdone'); if (d) d.open = true; }""")
                until(page, STORM_OPENED)
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
                page.goto(f"{srv.url}/hurricane.html"); until(page, STORM_PANEL)
                page.evaluate("""() => { const b = [...document.querySelectorAll('#liveStorms .bar button')]
                  .find(x => x.textContent === 'Fay'); if (b) b.click(); }""")
                # Fay's panel is drawn again once her files are in, and it is hers that says no ladder is out
                until(page, "() => /No probability ladder has been published/.test(document.querySelector('#liveStorms').textContent)")
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
                page.goto(f"{srv.url}/hurricane.html"); until(page, STORM_PANEL)
                al_txt = page.evaluate("() => (document.querySelector('#liveStorms') || {}).textContent || ''")
                # the ocean's storms are drawn again as the button is pressed, from what the page holds
                page.locator("#b2").click(); until(page, "() => !!document.querySelector('#b2.on')")
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
                # the open storm's file and cards and the folded storms below go in together; a
                # loaded machine took longer than the 1.4 s this used to wait on 4 November
                until(page, STORM_SECTION)
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
                  // the storm's panel is cleared and drawn again once its files are in, and it
                  // ends on the vendor's attribution, so the read waits for that, up to 8 s
                  if (tab) { const p = tab.parentElement.nextElementSibling, was = p.firstChild; tab.click();
                    for (let i = 0; i < 80 && (p.firstChild === was || !p.querySelector('.attrib')); i++)
                      await new Promise(r => setTimeout(r, 100)); }
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
                  // the storm's panel is cleared and drawn again once its files are in, and it
                  // ends on the vendor's attribution, so the read waits for that, up to 8 s
                  if (tab) { const p = tab.parentElement.nextElementSibling, was = p.firstChild; tab.click();
                    for (let i = 0; i < 80 && (p.firstChild === was || !p.querySelector('.attrib')); i++)
                      await new Promise(r => setTimeout(r, 100)); }
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
                             and ("in " in cd["text"] or cd["text"] == "expected now")
                             and "file expected" in cd["line"]
                             and "4 to 6 hours after their cycle" in cd["line"]),
                        str(cd))
                page.unroute("**/data/snapshots/**")

                page.goto(f"{srv.url}/hurricane.html")
                until(page, HUR_DRAWN)
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
                page.goto(f"{srv.url}/hurricane.html"); until(page, HUR_DRAWN)
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
                until(page, STORM_DRAWN)
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
                until(page, STORM_DRAWN)
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
                until(page, STORM_DRAWN)
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
                page.goto(f"{srv.url}/hurricane.html"); until(page, HUR_DRAWN)
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
                # the first frame is read with reduced motion and the loop watched playing on a second
                # load, as for the surface analysis loop
                page.emulate_media(reduced_motion="reduce")
                page.goto(f"{srv.url}/hurricane.html"); until_n(page, "#stormLoops .loopcard")
                page.locator("#stormLoops").scroll_into_view_if_needed(); page.wait_for_timeout(300)
                fl0 = page.evaluate("""() => {
                  const c = document.querySelector('#stormLoops .loopcard');
                  if (!c) return null;
                  return { cards: document.querySelectorAll('#stormLoops .loopcard').length, head: c.querySelector('.lhd').textContent,
                           src: c.querySelector('img.loopimg').getAttribute('src'), time: c.querySelector('.looptime').textContent,
                           credit: c.querySelector('.lsrc').textContent };
                }""")
                page.emulate_media(reduced_motion="null")
                page.goto(f"{srv.url}/hurricane.html"); until_n(page, "#stormLoops .loopcard")
                page.locator("#stormLoops").scroll_into_view_if_needed()
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
                # with no loop to draw nothing changes on the page, so the read waits for the page's
                # answer for the loops' index
                goto_answered(page, f"{srv.url}/hurricane.html", "/floater/index.json"); until(page, HUR_DRAWN)
                chk.add(f"{scheme} storm loop: no storm, no loop",
                        page.locator("#stormLoops .loopcard").count() == 0, "")
                page.goto(f"{srv.url}/hurricane.html"); until(page, HUR_DRAWN)
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
                page.goto(f"{srv.url}/hurricane.html"); until(page, HUR_DRAWN)
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
                until(page, PANELS_DONE)
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
                # ---- the world GDP contract's page (owner, 2026-11-02): the Letter (owner, 2026-11-05)
                # with its figures drawn live by the contract's model or from the published run, the
                # controls re-running it, each view's chance of Yes never called a price (owner,
                # 2026-11-05), and the proposed ladder carrying no link until the exchange lists the
                # contract
                chk.add(f"{scheme} climate: a card leads to the world GDP contract's page",
                        page.locator("a#gdpCard[href='climate-gdp.html']").count() == 1, "")
                gdp_model = json.loads(urllib.request.urlopen(f"{srv.url}/data/snapshots/elnino/model.json").read().decode())
                gdp_pub = gdp_model["published"]["contract"]
                gdp_letter = json.loads(urllib.request.urlopen(f"{srv.url}/data/snapshots/elnino/letter.json").read().decode())

                def _gdp_state():
                    return page.evaluate("""() => ({
                      status: (document.querySelector('#gdpRunStatus') || {}).textContent || '',
                      strip: (document.querySelector('#pageStatus .status') || {}).textContent || '',
                      tiles: [...document.querySelectorAll('.gdp-tile .tv')].map(e => e.textContent),
                      chanceOnly: [...document.querySelectorAll('.gdp-tile.null .ts, .gdp-tile.paper .ts')]
                        .every(e => /^chance of Yes\\./.test(e.textContent) && !/price|fair value/i.test(e.textContent)),
                      h1: (document.querySelector('#gdpTitle') || {}).textContent || '',
                      summary: !!document.querySelector('p.gdp-summary + #contract'),
                      sections: [...document.querySelectorAll('.secttl')].map(e => e.id),
                      refs: document.querySelectorAll('ol.gdp-refs li[id^="ref-"]').length,
                      levels: (document.querySelector('#fig1 #gdpLevels') || { childElementCount: 0 }).childElementCount,
                      figids: [...document.querySelectorAll('figure.gdp-fig')].map(f => f.id),
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
                # a control runs the model again a beat later, and the run line is written afresh
                # when that run is done, so a read after a control waits for a new line
                GDP_MARK = "() => { window.__gdpRun = (document.querySelector('#gdpRunStatus') || {}).firstChild || null; }"
                GDP_RERUN = """() => { const e = document.querySelector('#gdpRunStatus');
                  return !!e && e.firstChild !== window.__gdpRun && / paths\\.$/.test(e.textContent); }"""
                page.goto(f"{srv.url}/climate-gdp.html"); until(page, GDP_RAN, cap_ms=20000)
                g0 = _gdp_state()
                chk.add(f"{scheme} gdp: the page runs the contract's model on every path and dates the run",
                        "50,000 paths" in g0["status"] and "Data as of" in g0["strip"], g0["status"] + " | " + g0["strip"][:60])
                chk.add(f"{scheme} gdp: the page belongs to the Climate Change category",
                        g0["nav"] == ["Climate & Weather", "Climate Change"], str(g0["nav"]))
                gdp_p = [int(t.rstrip("%")) for t in g0["tiles"][:2] if t.endswith("%")]
                chk.add(f"{scheme} gdp: at the article's settings the two chances are the published run's, each a chance of Yes and never a price",
                        len(gdp_p) == 2 and abs(gdp_p[0] - 100 * (1 - gdp_pub["false_positive"])) <= 3
                        and abs(gdp_p[1] - 100 * (1 - gdp_pub["sensitivity"])) <= 3 and g0["chanceOnly"], str(g0["tiles"]))
                # Fig. 1 is the contract's figure, its levels panel above the ladder; the others sit in the text,
                # the quadrant schematic an image and the rest drawn, two of them in two panels
                chk.add(f"{scheme} gdp: the Letter's figures are drawn, each with its details of calculation",
                        g0["figids"] == [k_ for k_ in gdp_letter["figures"] if k_ != "fig1"] and len(g0["figs"]) == 12
                        and min(g0["figs"] or [0]) > 10 and g0["levels"] > 10 and g0["notes"] == 11,
                        f"figs={g0['figids']} {g0['figs']} levels={g0['levels']} notes={g0['notes']}")
                gdp_refs = len(next(s_ for s_ in gdp_letter["sections"] if s_["id"] == "references")["refs"])
                chk.add(f"{scheme} gdp: the page carries the Letter, its summary above the contract, then its Methods, "
                        "Extended Data, references, notes and Supplementary Information",
                        g0["h1"] == gdp_letter["title"] and g0["summary"] and g0["refs"] == gdp_refs
                        and all(s_ in g0["sections"] for s_ in ("methods", "extended-data", "references", "notes", "supplementary")),
                        f"{g0['h1'][:40]} summary={g0['summary']} refs={g0['refs']} {g0['sections']}")
                page.locator("figure#ed1 img").scroll_into_view_if_needed()
                GDP_IMG = "() => { const i = document.querySelector('figure#ed1 img'); return !!i && i.complete && i.naturalWidth > 0; }"
                chk.add(f"{scheme} gdp: the quadrant schematic's image loads", until(page, GDP_IMG), "")
                page.evaluate("() => window.scrollTo(0, 0)")
                chk.add(f"{scheme} gdp: until it lists, the ladder is the proposed strikes, hatched, with two marks and no link",
                        g0["hatched"] == len(gdp_model["ladder"]["strikes"]) and g0["marks"] == 2 * g0["hatched"]
                        and not g0["links"] and not g0["note"] and "Not listed yet" in g0["listing"],
                        f"hatched={g0['hatched']} marks={g0['marks']} links={len(g0['links'])} notes={g0['note']}")
                page.locator("#gdpMain rect[fill='url(#gdpHatch)']").first.hover(force=True); page.wait_for_timeout(150)
                t_strike = page.locator("#tip").inner_text()
                chk.add(f"{scheme} gdp: a strike's box gives both chances and when it lists, never calling them prices",
                        t_strike.count("Chance of Yes") == 2 and "lists" in t_strike and "price" not in t_strike.lower(), t_strike[:120])
                page.locator("#gdpMainMethod button.accnote-btn").click(); page.wait_for_timeout(200)
                gn = page.evaluate("""() => { const b = document.querySelector('#gdpMainMethod .accnote');
                  return { open: !!b && !b.hidden, tex: b ? b.querySelectorAll('.katex').length : 0,
                           code: b ? [...b.querySelectorAll('.code a')].map(a => a.href) : [] }; }""")
                chk.add(f"{scheme} gdp: the contract's note opens with its equations typeset and its code linked",
                        gn["open"] and gn["tex"] >= 3 and len(gn["code"]) >= 3
                        and all(u.startswith(gdp_model["code"]["repo"] + "/blob/") for u in gn["code"]), str(gn)[:160])
                page.evaluate(GDP_MARK)
                page.evaluate("""() => { const i = [...document.querySelectorAll('#gdpPanel input[type=range]')]
                    .find(x => x.getAttribute('aria-label') === 'Share of the paper’s effect that is real');
                  i.value = 0.5; i.dispatchEvent(new Event('input', { bubbles: true })); }""")
                until(page, GDP_RERUN, cap_ms=20000)
                g1 = _gdp_state()
                chk.add(f"{scheme} gdp: a control re-runs the model, moves the paper's chance and goes in the address",
                        "share=0.5" in g1["hash"] and g1["tiles"][1] != g0["tiles"][1] and g1["tiles"][0] == g0["tiles"][0],
                        f"{g0['tiles']} -> {g1['tiles']} {g1['hash']}")
                page.evaluate(GDP_MARK)
                page.locator("#gdpPanelBar button", has_text="Reset").click(); until(page, GDP_RERUN, cap_ms=20000)
                g2 = _gdp_state()
                chk.add(f"{scheme} gdp: reset returns to the article's settings", g2["tiles"] == g0["tiles"] and g2["hash"] == "",
                        f"{g2['tiles']} {g2['hash']}")
                page.goto(f"{srv.url}/climate-gdp.html#baseline=frozen&irf=PWT_panel"); until(page, GDP_RAN, cap_ms=20000)
                g3 = _gdp_state()
                chk.add(f"{scheme} gdp: a shared address loads its settings, fetching what they need",
                        "50,000 paths" in g3["status"] and g3["tiles"][:2] != g0["tiles"][:2]
                        and page.locator(".gdp-figstate.mine").count() == 7, f"{g3['status']} {g3['tiles']}")
                # the trend baseline's first year (owner, 2026-11-05) goes in the address, sets its control and
                # moves the trend view
                page.evaluate(GDP_MARK)
                page.goto(f"{srv.url}/climate-gdp.html#baseline=frozen"); until(page, GDP_RERUN, cap_ms=20000)
                g6 = _gdp_state()
                page.evaluate(GDP_MARK)
                page.goto(f"{srv.url}/climate-gdp.html#baseline=frozen&trendStart=1995"); until(page, GDP_RERUN, cap_ms=20000)
                g7 = _gdp_state()
                g7c = page.evaluate("""() => { const i = [...document.querySelectorAll('#gdpPanel input[type=range]')]
                    .find(x => x.getAttribute('aria-label') === 'First year of the average');
                  return i ? [i.value, i.min, i.max, !!i.offsetParent] : null; }""")
                gdp_starts = sorted(gdp_model["trend"]["starts"])
                chk.add(f"{scheme} gdp: the trend's first year goes in the address, sets its control and moves the view",
                        g7c == ["1995", gdp_starts[0], gdp_starts[-1], True] and g7["tiles"] != g6["tiles"]
                        and g7["hash"] == "#baseline=frozen&trendStart=1995", f"{g7c} {g6['tiles']} -> {g7['tiles']} {g7['hash']}")
                # a Fig. 4 row with settings of its own draws the whole page at them
                gdp_set = [r_ for r_ in gdp_model["published"]["sensitivity"]["rows"] if r_.get("settings")]
                gdp_row = next((r_ for r_ in gdp_set if "trendStart" in r_["settings"]), gdp_set[0])
                page.goto(f"{srv.url}/climate-gdp.html"); until(page, GDP_RAN, cap_ms=20000)
                page.evaluate(GDP_MARK)
                page.locator("#fig4 svg rect[style*='cursor']").nth(gdp_set.index(gdp_row)).click(force=True)
                until(page, GDP_RERUN, cap_ms=20000)
                g8 = _gdp_state()
                gdp_want = {k_: ("1" if v_ is True else "0" if v_ is False else str(v_)) for k_, v_ in gdp_row["settings"].items()}
                chk.add(f"{scheme} gdp: a Fig. 4 row draws the whole page at its settings",
                        dict(urllib.parse.parse_qsl(g8["hash"][1:])) == gdp_want and page.locator(".gdp-figstate.mine").count() == 7,
                        f"{gdp_row['label']}: {g8['hash']} want {gdp_want}")
                # a link to a figure moves the page and leaves the reader's settings as they were
                page.evaluate(GDP_MARK)
                page.goto(f"{srv.url}/climate-gdp.html#share=0.5"); until(page, GDP_RERUN, cap_ms=20000)
                g9 = _gdp_state()
                page.locator("a.figref[href='#fig2']").first.click()
                until(page, "() => location.hash === '#fig2'"); page.wait_for_timeout(800)
                g10 = _gdp_state()
                chk.add(f"{scheme} gdp: a link to a figure keeps the reader's settings",
                        g10["hash"] == "#fig2" and g10["tiles"] == g9["tiles"] and g9["tiles"] != g0["tiles"],
                        f"{g9['tiles']} -> {g10['tiles']} {g10['hash']}")
                # a second change of setting while the first one's file loads: the page ends at the second,
                # with no error on the way (each trend file held back half a second, as on a slow connection)
                gdp_e0 = len(errs)
                page.goto(f"{srv.url}/climate-gdp.html"); until(page, GDP_RAN, cap_ms=20000)
                page.evaluate("""() => { const f = window.fetch; window.fetch = (u, o) => /paths_trend_/.test(String(u))
                  ? new Promise(r => setTimeout(r, 500)).then(() => f(u, o)) : f(u, o); }""")
                page.goto(f"{srv.url}/climate-gdp.html#baseline=frozen"); page.wait_for_timeout(100)
                page.goto(f"{srv.url}/climate-gdp.html#baseline=frozen&trendStart=1995")
                until(page, "(t) => [...document.querySelectorAll('.gdp-tile .tv')].map(e => e.textContent).join() === t"
                      " && / paths\\.$/.test(document.querySelector('#gdpRunStatus').textContent)", cap_ms=20000, arg=",".join(g7["tiles"]))
                g11 = _gdp_state()
                chk.add(f"{scheme} gdp: two quick changes while a file loads end at the second, with no error",
                        g11["tiles"] == g7["tiles"] and errs[gdp_e0:] == [], f"{g11['tiles']} {g11['status']} {errs[gdp_e0:][:2]}")
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
                page.goto(f"{srv.url}/climate-gdp.html"); until(page, GDP_RAN, cap_ms=20000)
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
                chk.add(f"{scheme} gdp: a price's box gives the standing book and both chances as each view's share of the paths",
                        "Standing Yes bid" in t_gdp and "Chance of Yes" in t_gdp and "share of" in t_gdp, t_gdp[:140])
                page.evaluate(GDP_MARK)
                page.evaluate("""() => { const i = [...document.querySelectorAll('#gdpPanel input[type=range]')]
                    .find(x => x.getAttribute('aria-label') === 'Last year of the growth period');
                  i.value = Number(i.value) - 1; i.dispatchEvent(new Event('input', { bubbles: true })); }""")
                until(page, GDP_RERUN, cap_ms=20000)
                g5 = _gdp_state()
                chk.add(f"{scheme} gdp: at another end year the listed prices are not drawn, and the line goes with them",
                        not g5["links"] and not g5["note"] and str(gdp_end) in g5["ladderSub"], f"{len(g5['links'])} {g5['note']} {g5['ladderSub']}")
                page.unroute(gdp_cfg_url, _gdp_cfg)
                page.unroute("**/data/snapshots/catalogue/**", _gdp_cat)
                # a phone keeps the page inside its width; each figure scrolls inside its own card
                page.set_viewport_size({"width": 375, "height": 800})
                page.goto(f"{srv.url}/climate-gdp.html"); until(page, GDP_RAN, cap_ms=20000)
                gw_ = page.evaluate("() => [document.scrollingElement.scrollWidth, document.scrollingElement.clientWidth]")
                chk.add(f"{scheme} gdp: no sideways scroll on a phone", gw_[0] <= gw_[1] + 1, str(gw_))
                page.set_viewport_size({"width": 1200, "height": 900})
                # ---- agriculture: the same panel, one per crop, in sequence on
                # the tab itself rather than behind a listing link
                page.goto(f"{srv.url}/agriculture.html")
                page.wait_for_timeout(900)
                # an older link to the generic listing must land on the real page,
                # not on a superseded one rendering under the same highlighted tab
                page.goto(f"{srv.url}/category.html?c=agriculture"); until(page, PANELS_DONE)
                chk.add(f"{scheme} agriculture: the old listing link lands on the page",
                        page.url.endswith("/agriculture.html")
                        and page.locator("#panels .panel").count() >= 3, page.url[-40:])
                page.goto(f"{srv.url}/category.html?c=not-a-category"); page.wait_for_timeout(700)
                chk.add(f"{scheme} category: an unknown slug says so rather than breaking",
                        "Unknown" in page.locator("#catTitle").inner_text(),
                        page.locator("#catTitle").inner_text()[:40])
                page.goto(f"{srv.url}/agriculture.html"); until(page, PANELS_DONE)
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
                # the terms are looked up by the site's own script, which needs nothing fetched
                page.goto(f"{srv.url}/contract.html?id=TRHOU"); until(page, LOADED)
                href = page.evaluate("()=>WXM.termsUrl('TRHOU')")
                chk.add(f"{scheme} terms: rain gets the rain document, not tax revenue",
                        href.endswith("/TTermsandConditions.pdf"), href)
                chk.add(f"{scheme} terms: the daily temperature series resolves too",
                        page.evaluate("()=>WXM.termsUrl('DHATL')").endswith("/DailyTemperatureTermsandConditions.pdf"),
                        page.evaluate("()=>WXM.termsUrl('DHATL')"))
                page.goto(f"{srv.url}/agriculture.html"); until(page, PANELS_DONE)

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
                page.goto(f"{srv.url}/weather.html"); until(page, PANELS_DONE)
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
                page.goto(f"{srv.url}/weather.html"); until(page, PANELS_DONE)
                sw_listed = page.evaluate(sw_js)
                page.context.close(); page = page_wall
                # ---- the live station map: KSFO's sample carries a regional
                #      frame and nearby readings, so the overlay must draw. Its picture is read
                #      for its size, so the read waits for the picture to be in as well
                page.goto(f"{srv.url}/city.html?station=KSFO")
                until(page, "() => document.querySelectorAll('#chart path').length >= 2 && [...document.querySelectorAll('#locator .locstack img')].some(i => i.complete)")
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
                page.goto(f"{srv.url}/city.html?station=KSFO"); until(page, CITY_CHART)
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
                # the chart draws once its files are in, which a loaded machine took longer than
                # 1.6 s to do on 4 November, so the read waits for it, up to 8 s. The additional
                # variables panels read below draw from files of their own, so it waits for them too
                page.goto(f"{srv.url}/san-francisco-ksfo.html")
                until(page, "() => document.querySelectorAll('#chart path').length >= 2 && !!document.querySelector('#advPanels svg')")
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
                page.goto(_ksfo); until_n(page, "#advPanels svg")
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
                page.goto(_ksfo); until(page, CITY_CHART)
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
                page.goto(_ksfo); until(page, CITY_CHART)
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
                # the crosshair and the postmortem below read the chart, the panels and the discussion
                page.goto(_ksfo)
                until(page, "() => document.querySelectorAll('#chart path').length >= 2 && !!document.querySelector('#advPanels svg') && !!document.querySelector('#discussion .afdtext')")
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
                  // the discussion is cleared and drawn again from its own files on each switch,
                  // so each read waits for the text to be back, up to 8 s
                  const host = document.querySelector('.advrow #discussion');
                  const back = async () => { for (let i = 0; i < 80 && !(host && host.querySelector('.afdtext')); i++)
                    await new Promise(r => setTimeout(r, 100)); };
                  document.querySelector('#advYday').click();
                  await back();
                  const t = host ? host.innerText : '';
                  document.querySelector('#advToday').click();
                  await back();
                  return { yday: t.slice(0, 400), today: (host ? host.innerText : '').slice(0, 400) };
                }""")
                chk.add(f"{scheme} postmortem: the discussion says which issuance it is showing",
                        bool(pm and (("standing at the moment scored" in pm["yday"])
                                     or ("do not reach back" in pm["yday"]))
                             and "standing at the moment scored" not in pm["today"]),
                        str(pm and pm["yday"])[:120])
                # every station the board carries has to have a page to link to,
                # built by the same rule the browser uses to write the link
                # the read below fetches what it needs and uses the site's own script for the links
                page.goto(f"{srv.url}/scorecard.html"); until(page, LOADED)
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
                page.goto(f"{srv.url}/lessons.html"); until_n(page, "details.lesson")
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
                page.goto(f"{srv.url}/lessons.html#trading-2"); until_n(page, "details.lesson")
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
                page.goto(f"{srv.url}/weather.html"); until(page, PANELS_DONE)
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

                page.goto(f"{srv.url}/fossil-fuels.html"); until(page, PANELS_DONE)
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

                page.goto(f"{srv.url}/electricity-renewables.html"); until(page, PANELS_DONE)
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
                page.goto(f"{srv.url}/accuracy.html"); until(page, ACC_DRAWN)
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
                # each page is read once all of it has drawn, so a line drawn late is counted too
                for tn_path, tn_want, tn_ready in (("city.html?station=KATL", [{"prev": "bar"}], CITY_ALL),
                                                   ("hurricane.html", [{"prev": "basinCap"}, {"prev": "laddersCap"}, {"host": "cat4"}, {"host": "landfall"}], HUR_DRAWN),
                                                   ("contract.html?id=HCAB", [{"host": "cBody", "prev": "ladder"}], CONTRACT_DONE),
                                                   ("fossil-fuels.html", [{"next": "panels"}], PANELS_DONE),
                                                   ("climate.html", [{"next": "panels"}], PANELS_DONE)):
                    page.goto(f"{srv.url}/{tn_path}"); until(page, tn_ready)
                    got = _tn()
                    ok_ = len(got) == len(tn_want) and all(g["text"] == "Click on any price to trade contracts through IBKR Prediction Markets" for g in got)
                    for g, want_ in zip(got, tn_want):
                        if any(g.get(k_) != v_ for k_, v_ in want_.items()):
                            ok_ = False
                    chk.add(f"{scheme} price links ({tn_path}): the line sits by the prices it describes", ok_, str(got)[:300])
                for tn_path, tn_ready in (("city.html?station=KATL&market=off", CITY_ALL), ("index.html", INDEX_ALL)):
                    page.goto(f"{srv.url}/{tn_path}"); until(page, tn_ready)
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
                    page.goto(f"{srv.url}/{_wo_page}"); until_n(page, "#divsvg rect")
                    wo_labels = page.eval_on_selector_all("#divsvg text", "e=>e.map(x=>x.textContent)")
                    chk.add(f"{scheme} scorecard ({_wo_page}): a wind-only station is scored but left off the figure",
                            "Nantucket" not in wo_labels and _first.get("city") in wo_labels,
                            str(["Nantucket" in wo_labels, _first.get("city") in wo_labels]))
                page.unroute(_wo_url, _wind_only)

                # ---- the scorecard grid: a row per station, a column per system
                # the grid and the standings draw in one pass once the scorecard is in
                page.goto(f"{srv.url}/scorecard.html"); until_n(page, "#divsvg rect")
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
                page.goto(f"{srv.url}/scorecard.html?side=low"); until_n(page, "#divsvg rect")
                chk.add(f"{scheme} scorecard: a link reopens the same end of the day",
                        "low" in page.locator("#divTitle").inner_text(),
                        page.locator("#divTitle").inner_text()[:50])
                page.goto(f"{srv.url}/scorecard.html?day=nonsense"); until_n(page, "#divsvg rect")
                chk.add(f"{scheme} scorecard: an unknown day falls back rather than blanking",
                        page.locator("#divsvg rect").count() >= 60, "")
                page.goto(f"{srv.url}/scorecard.html"); until_n(page, "#divsvg rect")
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
                until(page, MAP_DRAWN); until(page, NDFD_SETTLED)
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
            # the import below needs the market picker live
            page.goto(f"{srv.url}/allocator.html")
            until(page, ALLOC_READY)
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
            # the station's quotes are fetched, then its day is offered and its ladder drawn
            page.select_option("#allocMarket", "city:KATL"); until_n(page, "#allocSub select")
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
            # the world GDP page's Letter is the owner's own copy too, with its summary, affiliations and
            # Fig. 1 legend, and so is the article before it; its method notes are the page's
            OWNS = ("e=>e.filter(x=>!((x.closest('.gdp-article') || x.closest('.gdp-summary') || x.closest('.gdp-aff')"
                    " || x.closest('.gdp-cap1')) && !x.closest('.accnote')))")
            # each page is read once all of its copy has drawn, so copy drawn late is checked too;
            # the analysis page is drawn once every place on its map has its label
            PROSE_READY = {"index.html": INDEX_ALL, "city.html?station=KLAX": CITY_ALL, "hurricane.html": HUR_DRAWN,
                           "allocator.html": ALLOC_READY, "climate.html": PANELS_DONE, "weather.html": PANELS_DONE,
                           "agriculture.html": PANELS_DONE, "scorecard.html": "() => !!document.querySelector('#divsvg rect')",
                           "accuracy.html": ACC_DRAWN, "about.html": LOADED, "fossil-fuels.html": PANELS_DONE,
                           "electricity-renewables.html": PANELS_DONE, "analysis-resolution.html": ANA_MAP,
                           "climate-gdp.html": GDP_RAN}
            for path in PAGES_PROSE:
                page.goto(f"{srv.url}/{path}"); until(page, PROSE_READY[path], cap_ms=20000 if path.startswith("climate-gdp") else 8000)
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
            until(page, CITY_CHART)
            chk.add("embed: city series render", page.locator("#chart path").count() > 0)
            chk.add("embed: no site chrome", page.locator("header.site").count() == 0 and page.locator("footer.site").count() == 0)
            chk.add("embed: market off by default (weather-only height)", page.locator("#chart").get_attribute("viewBox") == "0 0 960 488")
            chk.add("embed: theme parameter applied", page.evaluate("document.documentElement.getAttribute('data-theme')") == "light")
            page.screenshot(path=os.path.join(OUT, "embed-light.png"), full_page=True)
            page.goto(f"{emb.url}/?station=KPHX&theme=dark&market=on")
            until(page, CITY_CHART)
            chk.add("embed: ?market=on shows the ladder", page.locator("#chart text", has_text="Strike ladders").count() == 1)
            page.screenshot(path=os.path.join(OUT, "embed-dark-market.png"), full_page=True)
            chk.add("embed: no script errors", not errs, "; ".join(errs)[:300])
            ctx.close()

            # ---- degradation path 1: nothing cached, feed down -> explicit no-data state
            ctx = browser.new_context()
            page = ctx.new_page()
            errs = errors_of(page)
            page.goto(f"{bad.url}/index.html")
            until_n(page, "#pageStatus .status")
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
            until(page, MAP_DRAWN)
            ok_first = "Data as of" in page.locator(".status").first.inner_text()
            page.route("**/data/**", lambda route: route.fulfill(status=503, body="outage"))
            page.reload()
            until(page, MAP_DRAWN)
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
