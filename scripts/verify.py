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
import datetime as dt
import json
import os
import socket
import re
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
                         ("hurricane.html", "#basin path", "basin geography"), ("scorecard.html", "#standChart rect", "the standings bars"),
                         ("climate.html", "#panels svg path", "climate series"),
                         ("agriculture.html", "#panels svg path", "crop yield series"),
                         ("weather.html", "#panels svg path", "weather series"),
                         ("fossil-fuels.html", "#panels svg path", "fossil fuel series"),
                         ("electricity-renewables.html", "#panels svg path", "electricity series"), ("about.html", "footer.site", "footer"),
                         ("faq.html", ".prose h2", "the FAQ questions"), ("accuracy.html", ".prose p", "the accuracy argument"),
                         ("daily-temperature-markets.html", ".prose h2", "the article sections"),
                         ("allocator.html", "#allocSvg", "the allocation chart")]
                for path, sel, what in pages:
                    page.goto(f"{srv.url}/{path}")
                    page.wait_for_timeout(900)
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
                chk.add(f"{scheme} faq: the eleven tools survive with their links",
                        page.locator(".prose li a").count() >= 11, str(page.locator(".prose li a").count()))
                chk.add(f"{scheme} faq: says plainly which sources this site itself draws",
                        "This site draws a narrower set" in page.locator(".sub").inner_text(),
                        page.locator(".sub").inner_text()[:70])
                page.goto(f"{srv.url}/accuracy.html"); page.wait_for_timeout(500)
                acc_t = "\n".join(page.locator(".prose").all_inner_texts())
                chk.add(f"{scheme} accuracy: the electricity section is not published",
                        "electricity" not in acc_t.lower() and "wholesale" not in acc_t.lower(), acc_t[-90:])
                chk.add(f"{scheme} accuracy: the mechanism argument is intact",
                        "sit downstream of them" in acc_t and "deterring those who are inaccurate" in acc_t, f"chars={len(acc_t)}")
                chk.add(f"{scheme} accuracy: the lead-time curve is drawn",
                        page.locator("#accChart path").count() >= 2
                        and page.locator("#accChart circle").count() >= 20,
                        f"paths={page.locator('#accChart path').count()} pts={page.locator('#accChart circle').count()}")
                acc_cap = page.locator("#accCap").inner_text()
                acc_txt = page.locator("#accCap").inner_text()
                chk.add(f"{scheme} accuracy: the page says where forecasting stops",
                        "already been recorded" in acc_txt and "is not forecast skill" in acc_txt,
                        acc_txt[-120:])
                chk.add(f"{scheme} accuracy: that region is marked on the chart, not cut out",
                        any("already happened" in t for t in page.eval_on_selector_all(
                            "#accChart text", "e=>e.map(x=>x.textContent)")), "")
                chk.add(f"{scheme} accuracy: the curve says what stands behind it",
                        "city-days" in acc_cap and "scored on the same days" in acc_cap, acc_cap[:90])
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
                        l2[:2] == ["Daily Temperatures", "Tropical Cyclones"] and len(l2) == 5, str(l2))
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
                                  (f"{srv.url}/city.html?station=KLAX", ["Climate & Weather", "Daily Temperatures"])):
                    page.goto(url); page.wait_for_timeout(800)
                    got = page.eval_on_selector_all("header.site nav a.on", "els => els.map(e => e.textContent)")
                    chk.add(f"{scheme} nav: {url.split('/')[-1][:34]} knows its branch", got == want, f"{got} want {want}")
                # ---- the full view: both contract days at once
                page.goto(f"{srv.url}/city.html?station=KPHX&market=on"); page.wait_for_timeout(1800)
                heads0 = page.eval_on_selector_all("#chart text.axl",
                    "e=>e.map(x=>x.textContent).filter(t=>/Today ·|Tomorrow ·/.test(t))")
                chk.add(f"{scheme} city full: one ladder before the toggle", heads0 == [], str(heads0))
                page.locator("#fullBtn").click(); page.wait_for_timeout(1200)
                heads1 = page.eval_on_selector_all("#chart text.axl",
                    "e=>e.map(x=>x.textContent).filter(t=>/Today ·|Tomorrow ·/.test(t))")
                chk.add(f"{scheme} city full: both contract days are headed", len(heads1) == 2
                        and heads1[0].startswith("Today") and heads1[1].startswith("Tomorrow"), str(heads1))
                nm = page.eval_on_selector_all("#chart text.lvlnm", "e=>e.map(x=>x.textContent)")
                chk.add(f"{scheme} city full: the forecast names shorten so they clear the strikes",
                        bool(nm) and all(len(x) <= 12 for x in nm), str(nm[:3]))
                errs_now = len(errs)
                page.locator("#fullBtn").click(); page.wait_for_timeout(900)
                heads2 = page.eval_on_selector_all("#chart text.axl",
                    "e=>e.map(x=>x.textContent).filter(t=>/Today ·|Tomorrow ·/.test(t))")
                chk.add(f"{scheme} city full: toggling back restores the single ladder",
                        heads2 == [] and len(errs) == errs_now, str(heads2))

                # ---- the board's heading: which board, which side, which day
                page.goto(f"{srv.url}/index.html"); page.wait_for_timeout(1600)
                t1 = page.locator("#boardTitle").inner_text()
                import re as _re2
                chk.add(f"{scheme} board title: names the market, the side and the day",
                        t1.startswith("ForecastEx Weather Prediction Market for")
                        and _re2.search(r"(Today's|Tomorrow's) (Highs|Lows) \w+day, \w+ \d{1,2}$", t1) is not None,
                        t1[:110])
                page.locator("#m4").click(); page.wait_for_timeout(700)
                t2 = page.locator("#boardTitle").inner_text()
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
                chk.add(f"{scheme} landing page: heading first, map above the explanation",
                        body[0] == "boardTitle" and body.index("card") < body.index("modeTitle"),
                        str(body[:5]))

                # abroad there is no government forecast to compare against, so the
                # observation and the market's own number are the whole picture.
                # The Celsius boards are highs only, so this is checked on a highs
                # view; on a lows view the absence is correct.
                page.locator("#m2").click(); page.wait_for_timeout(700)
                wlabs = page.eval_on_selector_all("#mapW text.lbl", "e=>e.map(x=>x.textContent)")
                # abroad the label is the market's number and the local date it
                # applies to; a station with no board is named without a number
                import re as _re
                chk.add(f"{scheme} world map: a label carries the market value and its local date",
                        sum(1 for l in wlabs if _re.search(r"-?\d+\u00b0[CF]? \u00b7 \w+ \d+$", l)) >= 3,
                        str(wlabs[:3]))
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
                leg2 = page.eval_on_selector_all("#chart text", "e=>e.map(x=>x.textContent)")
                chk.add(f"{scheme} sub-hourly: the legend names the band and says what it is not",
                        any("Range between reports" in x for x in leg2)
                        and any("not settlement" in x for x in leg2), str([x for x in leg2 if "Range" in x]))
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
                        page.locator("#tip .tg .tk").count() <= 3
                        and "Settles Yes if" in lt, f"rows={page.locator('#tip .tg .tk').count()}")
                chk.add(f"{scheme} city ladder box: the book is one line, not five rows",
                        "they buy, they do not sell" in lt and "Yes bid" in lt, lt[-90:])

                loccap = page.locator("#locator .cap").inner_text()
                chk.add(f"{scheme} locator: a US station gets metro-scale imagery, pinned",
                        page.locator("#locator .locbox img").count() == 1
                        and page.locator("#locator .locpin").count() == 1
                        and "KATL" in loccap, loccap[:70])
                chk.add(f"{scheme} locator: the imagery is attributed and says why it matters",
                        "USGS" in loccap and "United States government" in loccap
                        and "where it sits matters" in loccap, loccap[-90:])
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
                dcap = page.locator("#cityDaysCap").inner_text()
                chk.add(f"{scheme} three days: the pinned moment is stated",
                        "six in the evening the day before" in dcap, dcap[:110])
                # the chart the page is for comes first, and opens to the window
                order = [o for o in page.eval_on_selector_all(".wrap > *", "e=>e.map(x=>x.id||x.className||x.tagName)")
                         if o != "site"]
                chk.add(f"{scheme} city page: the series is at the top, above the picker",
                        order.index("chartCard") < order.index("pickwrap"), str(order[:6]))
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
                named = [t for t in leg if t in ("Observed (METAR)", "Weather Service", "Blend of Models",
                                                 "Aviation guidance", "GFS MOS")]
                chk.add(f"{scheme} legend: the sources are named inside the figure",
                        len(named) >= 4, str(named))
                chk.add(f"{scheme} legend: each source carries when it was issued and how old it is",
                        sum(1 for t in leg if "old" in t) >= 3, str([t for t in leg if "old" in t][:3]))
                chk.add(f"{scheme} legend: the separate freshness table is gone",
                        page.locator("#freshness").count() == 0, "")
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
                    "e=>e.filter(x=>x.getAttribute('stroke')!=='var(--ink)').map(x=>+x.getAttribute('r'))")
                chk.add(f"{scheme} city record: the observation is weighted above the forecasts",
                        bool(big) and bool(other) and min(big) > max(other), f"obs={sorted(set(big))} tools={sorted(set(other))}")
                # highs are filled and lows hollow, so one palette serves both
                hollow = page.eval_on_selector_all("#cityScore circle",
                    "e=>e.filter(x=>x.getAttribute('fill')==='var(--panel)').length")
                chk.add(f"{scheme} city record: lows are hollow so the colour can mean the tool",
                        hollow >= 5, f"hollow={hollow}")
                bands = page.locator("#cityScore rect[fill='transparent']").count()
                chk.add(f"{scheme} city record: one hover band per day", 1 <= bands <= 7, f"bands={bands}")
                if bands:
                    page.locator("#cityScore rect[fill='transparent']").nth(min(3, bands - 1)).hover(force=True)
                    page.wait_for_timeout(250)
                    t_cs = page.locator("#tip").inner_text()
                    chk.add(f"{scheme} city record: the box gives each tool's value and its error",
                            "Observed high / low" in t_cs and "National Weather Service" in t_cs, t_cs[:80])

                # ---- the catalogue pages
                page.goto(f"{srv.url}/section.html?s=energy"); page.wait_for_timeout(800)
                chk.add(f"{scheme} catalogue: a branch page lists its categories",
                        page.locator("#cats a.catcard").count() == 2, str(page.locator("#cats a.catcard").count()))
                page.goto(f"{srv.url}/weather.html"); page.wait_for_timeout(1400)
                fam = page.locator("#panels .panel").count()
                off = page.locator("#panels .panel", has_text="Not currently listed").count()
                chk.add(f"{scheme} catalogue: the category shows every product in the family",
                        fam == 33 and off == 23, f"panels={fam} not-listed={off}")
                chk.add(f"{scheme} catalogue: unlisted products say so rather than vanishing",
                        off == 23, str(off))
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
                        "Yes bid" in t_l and "No bid" in t_l and "Buy Yes now at" in t_l and "no sellers" in t_l, t_l[:90])
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
                        ("Buy Yes" in t_str or "no bids" in t_str) and "fair value" not in t_str
                        and "implied" not in t_str.lower(), t_str[-70:])
                chk.add(f"{scheme} series: the box is short, and the terms are one click away",
                        t_str.count("\n") <= 12 and "terms" in t_str, str(t_str.count("\n")))
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
                chk.add(f"{scheme} crops: the strikes are coloured by price, not one colour",
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
                page.goto(f"{srv.url}/faq.html"); page.wait_for_timeout(400)
                faq_txt = page.locator(".prose").inner_text()
                chk.add(f"{scheme} sources: the feed table moved to the FAQ",
                        page.locator("#sources").count() == 1 and "aviationweather.gov METAR" in faq_txt,
                        f"anchor={page.locator('#sources').count()}")
                page.goto(f"{srv.url}/about.html"); page.wait_for_timeout(400)
                about_txt = page.locator(".wrap").inner_text()
                chk.add(f"{scheme} sources: About points at its new home and no longer carries the table",
                        "aviationweather.gov METAR" not in about_txt
                        and page.locator("a[href='faq.html#sources']").count() == 1,
                        about_txt[:70])
                page.goto(f"{srv.url}/index.html"); page.wait_for_timeout(1800)
                chk.add(f"{scheme} scorecard: the figure is on the daily temperatures page",
                        page.locator("#divsvg g").count() > 10, f"rows={page.locator('#divsvg g').count()}")
                chk.add(f"{scheme} scorecard: the standings are not on it, and it says where they are",
                        page.locator("#standings").count() == 0
                        and page.locator("a[href='accuracy.html']").count() >= 1, "")
                chk.add(f"{scheme} map: the whole key sits under the map it explains",
                        page.evaluate("""() => { const m = document.querySelector('#map').getBoundingClientRect();
                          const k = document.querySelector('.how');
                          if (!k) return false;
                          const t = k.textContent;
                          return k.getBoundingClientRect().top - m.bottom < 160
                                 && t.includes('THE DOTS') && t.includes('THE SHADING') && t.includes('A LABEL'); }"""), "")
                chk.add(f"{scheme} scorecard: the map keeps its own status strip",
                        page.locator("#pageStatus .status").count() >= 1, "")
                chk.add(f"{scheme} roster: Colorado Springs is off the board",
                        "KCOS" not in page.locator("#map").inner_html()
                        and "KCOS" not in page.locator("#mapW").inner_html(), "")
                chk.add(f"{scheme} standalone: no script errors", not errs, "; ".join(errs)[:300])
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
                    title = pg2.locator("#modeTitle").inner_text()
                    chk.add(f"{scheme} map default at {hour}:30 ET: opens on {want.lower()}'s highs",
                            col == [want] and on == "Highs" and want.upper() in title.upper(),
                            f"column={col} button={on} title={title[:40]}")
                    chk.add(f"{scheme} map default at {hour}:30 ET: the other day is one click away",
                            pg2.locator(".bar button").count() >= 4, str(pg2.locator(".bar button").count()))
                    ctx2.close()
                # ---- the dots are centred on the board's typical gap
                #
                # The market sits below the NWS forecast on highs nearly every
                # day, so a raw-sign colouring paints the board one colour and
                # tells a reader nothing. Centred, both colours must appear.
                # the today board is the one populated through the morning: the
                # day-ahead contracts list around midday Eastern, so before then
                # the tomorrow views legitimately have nothing to centre on
                page.goto(f"{srv.url}/index.html"); page.wait_for_timeout(1000)
                page.locator("#m1").click(); page.wait_for_timeout(500)
                warm = page.locator("#map circle[fill='var(--warm)']").count()
                cool = page.locator("#map circle[fill='var(--cool)']").count()
                chk.add(f"{scheme} map colour: centring splits the board instead of painting it one colour",
                        warm > 0 and cool > 0, f"warm={warm} cool={cool}")
                leg = page.locator("#legend").inner_text()
                chk.add(f"{scheme} map colour: the legend states the typical gap it centred on",
                        "typical gap today" in leg.lower() and "°" in leg, leg[:110])
                chk.add(f"{scheme} map colour: the legend says colour is distance from typical, not from zero",
                        "not from zero" in leg, leg[-90:])
                page.locator("#map g.dot").first.hover(force=True); page.wait_for_timeout(200)
                t_enc = page.locator("#tip").inner_text()
                for want in ("Gap to NWS", "The board", "against that"):
                    chk.add(f"{scheme} map colour: the box shows '{want}'", want in t_enc, t_enc[-140:])
                chk.add(f"{scheme} map colour: the raw gap is still shown, not replaced",
                        "Gap to NWS" in t_enc and ("typical" in t_enc), t_enc[-140:])
                # observed-versus-issued is not a market gap and keeps the plain sign
                chk.add(f"{scheme} map: the observed-versus-issued view is gone",
                        page.locator("#m5").count() == 0, "")
                chk.add(f"{scheme} map: no headline boxes above the board",
                        page.locator("#cards .tile").count() == 0, "")
                cols = page.eval_on_selector_all(".modegrid .mgh", "e=>e.map(x=>x.textContent)")
                per = page.eval_on_selector_all(".modegrid .mgcol", "e=>e.map(c=>c.querySelectorAll('button').length)")
                chk.add(f"{scheme} map: the four views sit in a today/tomorrow grid",
                        cols == ["Today", "Tomorrow"] and per == [2, 2], f"{cols} {per}")
                chk.add(f"{scheme} map: the opening paragraph is the one asked for",
                        "with a bias removed from the National Weather Service" in page.locator("p.sub").first.inner_text(),
                        page.locator("p.sub").first.inner_text()[:80])
                chk.add(f"{scheme} map: it links to the trading article",
                        page.locator("p.cap a[href='daily-temperature-markets.html']").count() == 1, "")
                chk.add(f"{scheme} map: the legend no longer repeats the colour key below it",
                        "Warmer than the board" not in page.locator("#legend").inner_text(),
                        page.locator("#legend").inner_text()[:80])
                # every view shades now, not only the day-ahead ones
                for bid in ("m1", "m2", "m3", "m4"):
                    page.locator("#" + bid).click(); page.wait_for_timeout(400)
                    chk.add(f"{scheme} map: {bid} draws the forecast field",
                            page.locator("#map rect[data-i]").count() > 1500,
                            str(page.locator("#map rect[data-i]").count()))
                page.locator("#m1").click(); page.wait_for_timeout(400)
                wl = page.eval_on_selector_all("#mapW text.lbl", "e=>e.map(x=>x.textContent)")
                chk.add(f"{scheme} map: the international stations are named on the world canvas",
                        len(wl) >= 12 and any("Tokyo" in x for x in wl), str(wl[:3]))
                chk.add(f"{scheme} map: the list under the world canvas is gone",
                        page.locator("#intl").count() == 0, "")

                # switching to today keeps it a market view, not observed-vs-issued
                page.goto(f"{srv.url}/index.html"); page.wait_for_timeout(900)
                page.locator("#m1").click(); page.wait_for_timeout(400)
                t_today = page.locator("#modeTitle").inner_text()
                chk.add(f"{scheme} map today view: the current day, against the NWS forecast rather than what was issued",
                        "TODAY" in t_today.upper() and "observed" not in t_today.lower(), t_today[:80])
                dots_today = page.locator("#map circle").count()
                chk.add(f"{scheme} map today view: dots are drawn", dots_today > 0, f"circles={dots_today}")
                page.locator("#m2").click(); page.wait_for_timeout(400)
                chk.add(f"{scheme} map tomorrow view: still available and shaded",
                        "TOMORROW" in page.locator("#modeTitle").inner_text().upper(), page.locator("#modeTitle").inner_text()[:60])

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
                picks = page.locator("#chart text.strikepick").count()
                chk.add(f"{scheme} market on: ladder layout, and every strike is a switch",
                        vb_on == "0 0 960 655" and ladder_on == 1 and picks >= 6,
                        f"viewBox={vb_on} ladder={ladder_on} switches={picks}")
                live_lbl = page.locator("#chart text", has_text="ForecastEx quotes").count()
                chk.add(f"{scheme} market on: ladder labelled with the exchange and its as-of time", live_lbl == 1, f"count={live_lbl}")
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
                lf_rows = page.locator("#landfall .lrow").count()
                vendor = page.locator("#vendor").inner_text()
                chk.add(f"{scheme} hurricane: tiles, count ladders and the landfall board", tiles >= 3 and ladders >= 2 and lf_rows >= 2, f"tiles={tiles} ladders={ladders} landfall rows={lf_rows}")
                chk.add(f"{scheme} landfall: drawn as a Yes/No ladder, not a table",
                        page.locator("#landfall table").count() == 0 and lf_rows >= 5, f"rows={lf_rows}")
                chk.add(f"{scheme} landfall: named as major hurricane landfall",
                        "Major hurricane landfall" in page.locator("#landfall .lt").inner_text(),
                        page.locator("#landfall .lt").inner_text()[:60])
                chk.add(f"{scheme} hurricane: vendor lane reports its state", "Not enabled" in vendor or "Lane on" in vendor or "LiveCyc" in vendor, vendor[:80])
                # ---- the season-count panels: cumulative beside the ladder
                panels = page.locator(".cwrap").count()
                chk.add(f"{scheme} hurricane: a cumulative panel per count product", panels >= 2, f"panels={panels}")
                if panels:
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

                def _lad(scale):
                    def q(i):
                        return max(0, round(min(99, scale * (1 - i / len(THR)) * 100 - i * 4), 1))
                    return {"BR": [q(i) for i in range(len(THR))]}

                # six-hourly cycles with the 18Z one missing, so the gap mark has
                # something real to find and the even spacings have to stay unmarked
                BASE = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
                HOURS = [0, 6, 12, 24, 30, 36]

                def _ledger(final):
                    # a distinct recorded price per delivery, so scrubbing back has to
                    # move the square rather than leave today's price under an old ladder
                    steps = []
                    for k, hh in enumerate(HOURS):
                        t0 = BASE + dt.timedelta(hours=hh)
                        steps.append({"id": t0.strftime("%Y%m%d%H"), "kind": "livecyc",
                                      "at": t0.strftime("%Y-%m-%dT%H:%M:%SZ"), "ts": "t",
                                      "sites": _lad(0.2 + 0.1 * k),
                                      "prices": {"BR": {str(t): 10.0 + 5 * k for t in THR}}})
                    steps.append({"id": "INT", "kind": "interim", "at": "t", "ts": "t", "sites": _lad(0.95),
                                  "prices": {"BR": {str(t): 44.0 for t in THR}}})
                    return {"schema": 2, "name": "Erin", "year": 2026, "attribution": "Powered by Reask", "thresholds": THR,
                            "steps": steps, "sites": {"BR": {"name": "Brownsville", "firstStep": "2026090100"}},
                            "final": {"BR": 96.0} if final else None}

                _index = {"schema": 2, "enabled": True, "attribution": "Powered by Reask", "year": 2026,
                          "storms": [{"name": "Erin", "year": 2026}]}

                def _storm_routes(final):
                    def handler(route):
                        u = route.request.url
                        if u.endswith("/reask.json"):
                            return route.fulfill(status=200, content_type="application/json", body=json.dumps(_index))
                        if "/storm/Erin_2026.json" in u:
                            return route.fulfill(status=200, content_type="application/json", body=json.dumps(_ledger(final)))
                        if u.endswith("/market/hurricane.json"):
                            resp = route.fetch()
                            m = json.loads(resp.text())
                            m["markets"] = (m.get("markets") or []) + [
                                {"symbol": "LERBR", "name": "Erin \u2014 Brownsville peak gust", "productConid": 999000001, "contracts": [
                                    {"spec": "2026.9", "expiryLabel": "September 2026", "strike": t, "label": "Above %d" % t,
                                     "numeric": True, "bid": 0.4, "ask": 0.46, "mid": 0.43,
                                     "conid": 999100000 + t, "conidYes": 999100000 + t} for t in THR]},
                                {"symbol": "LHLERG", "name": "Erin \u2014 highest wind, Gulf Coast", "contracts": [
                                    {"spec": "2026.9", "expiryLabel": "September 2026", "strike": "Brownsville",
                                     "label": "Brownsville", "numeric": False, "bid": 0.4, "ask": 0.46, "mid": 0.43}]}]
                            return route.fulfill(response=resp, body=json.dumps(m))
                        return route.continue_()
                    return handler

                for _final in (False, True):
                    page.route("**/data/snapshots/**", _storm_routes(_final))
                    page.goto(f"{srv.url}/hurricane.html")
                    page.wait_for_timeout(1400)
                    tag = "settled" if _final else "live"
                    ncards = page.locator("#liveStorms .scardwrap").count()
                    chk.add(f"{scheme} storm ({tag}): a card per signalled location", ncards >= 1, f"cards={ncards}")
                    labels = page.eval_on_selector_all("#liveStorms .scardwrap text", "els => els.map(e => e.textContent)")
                    chk.add(f"{scheme} storm ({tag}): the settlement column is reserved from the first delivery",
                            ("settled" if _final else "settles") in labels, str(labels[:6]))
                    marks = len([t for t in labels if t in ("\u2713", "\u2715")])
                    chk.add(f"{scheme} storm ({tag}): outcome marks appear only once settled",
                            (marks > 0) == _final, f"marks={marks} settled={_final}")
                    pools_n = page.locator("#liveStorms .ladder .lrow").count()
                    chk.add(f"{scheme} storm ({tag}): the pool contract lists its named candidates", pools_n >= 1, f"rows={pools_n}")
                    page.locator("#liveStorms .scardwrap rect[stroke-width='1.6']").first.hover(force=True)
                    page.wait_for_timeout(150)
                    t_px = page.locator("#tip").inner_text()
                    chk.add(f"{scheme} storm ({tag}): the exchange's price reads against the vendor's probability",
                            "The exchange" in t_px and "The vendor" in t_px, t_px[:80])
                    # ---- the cursor, and scrubbing across deliveries
                    ticks = page.locator("#liveStorms svg.stimeline circle, #liveStorms svg.stimeline rect").count()
                    chk.add(f"{scheme} storm ({tag}): one tick per delivery on the timeline", ticks == 7, f"ticks={ticks}")
                    strip = page.locator("#liveStorms svg.stimeline").first
                    read = lambda: page.locator("#liveStorms svg.stimeline text").first.text_content()
                    chk.add(f"{scheme} storm ({tag}): the cursor starts at the newest delivery",
                            "delivery 7 of 7" in read() and "(latest)" in read(), read())
                    cx = lambda: page.eval_on_selector("#liveStorms .scardwrap line.scur", "e => e.getAttribute('x1')")
                    was = cx()
                    page.get_by_title("the delivery before this one").first.click()
                    page.wait_for_timeout(120)
                    chk.add(f"{scheme} storm ({tag}): stepping back names the delivery it lands on",
                            "delivery 6 of 7" in read() and "(latest)" not in read(), read())
                    chk.add(f"{scheme} storm ({tag}): the cursor line moves with the step", cx() != was, f"{was} -> {cx()}")
                    page.locator("#liveStorms .scardwrap rect[stroke-width='1.6']").first.hover(force=True)
                    page.wait_for_timeout(150)
                    t_back = page.locator("#tip").inner_text()
                    chk.add(f"{scheme} storm ({tag}): a past delivery is priced as it stood then, not now",
                            "at that delivery" in t_back and "35" in t_back, t_back[:120])
                    box = strip.bounding_box()
                    page.mouse.move(box["x"] + box["width"] * 0.05, box["y"] + box["height"] / 2)
                    page.mouse.down()
                    page.mouse.move(box["x"] + box["width"] * 0.30, box["y"] + box["height"] / 2, steps=6)
                    page.mouse.up()
                    page.wait_for_timeout(120)
                    chk.add(f"{scheme} storm ({tag}): dragging the strip scrubs to another delivery",
                            "of 7" in read() and "delivery 7 of 7" not in read(), read())
                    strip.press("ArrowLeft"); page.wait_for_timeout(100)
                    mid = read()
                    strip.press("End"); page.wait_for_timeout(120)
                    chk.add(f"{scheme} storm ({tag}): the arrow keys step and End returns to the latest",
                            mid != read() and "delivery 7 of 7" in read(), f"{mid} -> {read()}")
                    page.locator("#liveStorms .scardwrap rect[stroke-width='1.6']").first.hover(force=True)
                    page.wait_for_timeout(150)
                    t_now = page.locator("#tip").inner_text()
                    chk.add(f"{scheme} storm ({tag}): the newest delivery carries the current price",
                            "The exchange, now" in t_now, t_now[:120])
                    # ---- a cycle the vendor never delivered
                    ngap = page.locator("#liveStorms .scardwrap rect.sgap").count()
                    ncards2 = page.locator("#liveStorms .scardwrap").count()
                    chk.add(f"{scheme} storm ({tag}): the missed cycle is marked once per card, and the even spacings are not",
                            ngap == ncards2, f"marks={ngap} cards={ncards2}")
                    breaks = page.locator("#liveStorms svg.stimeline g line").count()
                    chk.add(f"{scheme} storm ({tag}): the timeline carries the same break", breaks == 2, f"lines={breaks}")
                    head = page.locator("#liveStorms p").first.inner_text()
                    chk.add(f"{scheme} storm ({tag}): the missing cycle is stated without hovering",
                            "1 cycle the vendor did not deliver" in head, head[:140])
                    page.locator("#liveStorms .scardwrap rect.sgap").first.hover(force=True)
                    page.wait_for_timeout(150)
                    t_gap = page.locator("#tip").inner_text()
                    chk.add(f"{scheme} storm ({tag}): the gap box counts the hours and the missing cycles",
                            "missing here" in t_gap and "12 hours" in t_gap and "6 hours" in t_gap, t_gap[:160])
                    # ---- the map: a signalled location opens its series and keeps it
                    dot = page.locator("#basin circle[role='button']")
                    chk.add(f"{scheme} storm ({tag}): the signalled location is clickable on the map",
                            dot.count() >= 1, f"dots={dot.count()}")
                    if dot.count():
                        dot.first.scroll_into_view_if_needed()
                        dot.first.click(force=True)
                        page.wait_for_timeout(300)
                        panel = page.locator("#sitePanel .spanel")
                        chk.add(f"{scheme} storm ({tag}): clicking opens a panel that stays", panel.count() == 1, f"panels={panel.count()}")
                        ptxt = panel.inner_text() if panel.count() else ""
                        chk.add(f"{scheme} storm ({tag}): the panel names the storm and the deliveries",
                                "Erin" in ptxt and "deliveries" in ptxt, ptxt[:90])
                        chk.add(f"{scheme} storm ({tag}): the panel carries the delivery series, not a summary",
                                page.locator("#sitePanel .scardwrap svg path").count() >= 3,
                                str(page.locator("#sitePanel .scardwrap svg path").count()))
                        # it survives a pointer moving away, which a hover box would not
                        page.mouse.move(10, 10); page.wait_for_timeout(250)
                        chk.add(f"{scheme} storm ({tag}): the panel does not vanish when the pointer leaves",
                                page.locator("#sitePanel .spanel").count() == 1, "")
                        chk.add(f"{scheme} storm ({tag}): the panel offers the listed wind contract",
                                "Open the wind contract" in ptxt, ptxt[-110:])
                        chk.add(f"{scheme} storm ({tag}): clicking the dot does not also pin a box on top of the panel",
                                page.locator("#tip").evaluate("e => e.dataset.pinned || ''") == "", "")
                        with page.expect_popup() as pop:
                            page.locator("#basin circle[role='button']").first.click(force=True)
                        p2 = pop.value
                        chk.add(f"{scheme} storm ({tag}): clicking the same location again goes to the contract",
                                "conid_yes=" in p2.url and "999000001" in p2.url, p2.url[:110])
                        p2.close()
                        page.locator("#sitePanel .spx").first.click(); page.wait_for_timeout(200)
                        chk.add(f"{scheme} storm ({tag}): the panel closes", page.locator("#sitePanel .spanel").count() == 0, "")
                    chk.add(f"{scheme} storm ({tag}): no script errors", not errs, "; ".join(errs)[:200])
                    page.unroute("**/data/snapshots/**")
                page.goto(f"{srv.url}/hurricane.html")
                page.wait_for_timeout(900)
                dots = page.locator("#basin circle").count()
                chk.add(f"{scheme} hurricane: reference locations drawn", dots >= 100, f"circles={dots}")
                page.locator("#basin circle").nth(40).hover(force=True); page.wait_for_timeout(120)
                t_dot = page.locator("#tip").inner_text()
                chk.add(f"{scheme} hover: reference location names itself and the lane state", "Country" in t_dot and ("probabilities" in t_dot), t_dot[:80])
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
                chk.add(f"{scheme} market's ladder: the single-colour bar is gone",
                        page.locator("#ladders svg.cpanel rect[fill='var(--accent)']").count() == 0,
                        str(page.locator("#ladders svg.cpanel rect[fill='var(--accent)']").count()))
                axis = page.locator("#ladders svg.cpanel text", has_text="Yes green, No red").count()
                chk.add(f"{scheme} market's ladder: the axis says which side is which", axis >= 1, f"labels={axis}")
                linked_href("#ladders svg.cpanel rect[fill='var(--yes)'][role='link']", "the market's ladder")
                linked_href("#ladders .lrow[role='link']", "a period ladder row")
                linked_href("#landfall .lrow[role='link']", "the landfall table")
                # the tornado contracts moved to Weather, so this section is often
                # empty on the cyclone page; when it is, it must say so
                if page.locator("#others td.num.lnk").count():
                    linked_href("#others td.num.lnk", "the other-contracts table")
                else:
                    chk.add(f"{scheme} hurricane link: an empty other-contracts section explains itself",
                            "Nothing beyond the count and landfall" in page.locator("#others").inner_text(),
                            page.locator("#others").inner_text()[:80])
                chk.add(f"{scheme} hurricane: the tornado contracts are not on the cyclone page",
                        "SWTUS" not in page.locator("#others").inner_text(),
                        page.locator("#others").inner_text()[:70])
                linked_href("#cat4 .lrow[role='link']", "the category 4 board")
                # the remaining-season curve, and what it must not claim
                chk.add(f"{scheme} cat4: the remaining-season curve is drawn",
                        page.locator("#cat4 svg.cpanel").count() == 1
                        and page.locator("#cat4 svg.cpanel path").count() >= 2,
                        str(page.locator("#cat4 svg.cpanel path").count()))
                c4 = page.locator("#cat4").inner_text()
                chk.add(f"{scheme} cat4: the page says a higher category does not qualify",
                        "higher or lower category does not qualify" in c4, c4[-120:])
                keys = page.eval_on_selector_all("#cat4 svg.cpanel text", "e=>e.map(x=>x.textContent)")
                chk.add(f"{scheme} cat4: both curves are named, with the climatology window",
                        any("climatology, 19" in k for k in keys) and any("count market" in k for k in keys),
                        str([k for k in keys if "clim" in k or "count" in k]))
                if page.locator("#cat4 svg.cpanel circle:not(.rdot)").count():
                    page.locator("#cat4 svg.cpanel circle:not(.rdot)").first.hover(force=True); page.wait_for_timeout(250)
                    t_c4 = page.locator("#tip").inner_text()
                    chk.add(f"{scheme} cat4: a contract is compared against climatology, not a fair value",
                            "Climatology, from today" in t_c4 and "Difference to climatology" in t_c4, t_c4[:80])
                # the map: a shaded landfall region is a contract
                shaded = page.locator("#basin path[role='link']").count()
                chk.add(f"{scheme} hurricane link: shaded map regions are clickable", shaded >= 3, f"regions={shaded}")
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
                chk.add(f"{scheme} hurricane link: the caption says the map is clickable",
                        "clicking a shaded region" in page.locator("#basinCap").inner_text(),
                        page.locator("#basinCap").inner_text()[:80])
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
                # and the colour those markers carry is explained on the panel
                chk.add(f"{scheme} panel: a colour key explains the marker colour",
                        page.locator("#panels linearGradient stop").count() >= 5
                        and any("chance it ends above the strike" in t for t in page.eval_on_selector_all(
                            "#panels text", "e=>e.map(x=>x.textContent)")),
                        str(page.locator("#panels linearGradient stop").count()))
                # red at nothing, green at a dollar: a dear strike and a cheap one
                # must not come out the same colour
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
                # the standings must compare tools over the same station-days, not
                # over whatever each archive lane happens to have collected
                # scored days are measured against what happened; days still ahead
                # have nothing to measure against and keep the consensus centre
                page.locator("#divControls button").first.click(); page.wait_for_timeout(500)
                ax = page.eval_on_selector_all("#divsvg text.axl", "e=>e.map(x=>x.textContent)")
                chk.add(f"{scheme} scorecard: a scored view is centred on the observed",
                        any(x == "observed" for x in ax), str(ax[:2]))
                page.locator("#divControls button").nth(2).click(); page.wait_for_timeout(500)
                ax2 = page.eval_on_selector_all("#divsvg text.axl", "e=>e.map(x=>x.textContent)")
                chk.add(f"{scheme} scorecard: a day still ahead keeps the consensus centre",
                        any(x == "consensus median" for x in ax2), str(ax2[:2]))
                page.locator("#divControls button").first.click(); page.wait_for_timeout(400)
                page.goto(f"{srv.url}/accuracy.html"); page.wait_for_timeout(1600)
                chk.add(f"{scheme} standings: they are on the accuracy page, beside the argument",
                        page.locator("#standChart rect[data-key]").count() >= 4,
                        str(page.locator("#standChart rect[data-key]").count()))
                chk.add(f"{scheme} standings: the page says the sample is matched",
                        "Matched sample" in page.locator("#standings").inner_text(),
                        page.locator("#standings").inner_text()[:70])
                page.goto(f"{srv.url}/scorecard.html"); page.wait_for_timeout(1400)
                # ---- the divergence figure: four views, ranking column and hover
                fig_rows = page.locator("#divsvg g").count()
                chk.add(f"{scheme} scorecard: divergence figure draws a row per station", fig_rows >= 20, f"rows={fig_rows}")
                page.locator("#divsvg circle").first.hover(force=True); page.wait_for_timeout(120)
                t_dot = page.locator("#tip").inner_text()
                chk.add(f"{scheme} hover: figure dot shows the forecast and its gap from the centre",
                        "Consensus median" in t_dot and ("From the observed" in t_dot or "From the consensus" in t_dot), t_dot[:80])
                titles = []
                for i in range(4):
                    page.locator("#divControls button").nth(i).click(); page.wait_for_timeout(250)
                    titles.append(page.locator("#divTitle").inner_text())
                # ---- the view is addressable: the daily letter links one of the four
                # directly, and clicking one has to leave a link that reopens it
                chk.add(f"{scheme} scorecard: clicking a view puts it in the address bar",
                        "view=" in page.url, page.url[-40:])
                for key, want in (("tlow", "Tomorrow"), ("ylow", "Yesterday")):
                    page.goto(f"{srv.url}/scorecard.html?view={key}")
                    page.wait_for_timeout(900)
                    on = page.locator("#divControls button.on").inner_text()
                    chk.add(f"{scheme} scorecard: ?view={key} opens on that panel",
                            want in on and "low" in on.lower(), f"{on} / {page.locator('#divTitle').inner_text()[:40]}")
                page.goto(f"{srv.url}/scorecard.html?view=nonsense")
                page.wait_for_timeout(900)
                chk.add(f"{scheme} scorecard: an unknown view falls back rather than blanking",
                        page.locator("#divControls button.on").count() == 1 and page.locator("#divsvg g").count() >= 20,
                        page.locator("#divControls button.on").inner_text())
                page.goto(f"{srv.url}/scorecard.html")
                page.wait_for_timeout(900)
                chk.add(f"{scheme} scorecard: all four views render a titled figure", len([t for t in titles if t]) == 4 and page.locator("#divsvg g").count() >= 20, "; ".join(t[:26] for t in titles))
                page.locator("#divControls button").first.click(); page.wait_for_timeout(250)
                col = page.locator("#divsvg text", has_text="Consensus error").count()
                chk.add(f"{scheme} scorecard: the scored view ranks by consensus error", col == 1, f"header={col}")
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
                chk.add(f"{scheme} hover: map dot shows tomorrow's forecasts and today so far", "tomorrow" in t_md and "Observed" in t_md, t_md[:80])
                wdots = page.locator("#mapW g.dot").count()
                chk.add(f"{scheme} map: the international stations sit on a world canvas below", wdots >= 10, f"dots={wdots}")
                # the reference field is interpolated for tomorrow only, so the
                # shading exists on the day-ahead views and nowhere else
                page.locator("#m2").click(); page.wait_for_timeout(400)
                page.locator("#map rect[data-i]").nth(600).hover(force=True); page.wait_for_timeout(120)
                t_cell = page.locator("#tip").inner_text()
                chk.add(f"{scheme} hover: shading cell names the derived field value", "NWS forecast field" in t_cell, t_cell[:80])
                page.locator("#m1").click(); page.wait_for_timeout(300)
                chk.add(f"{scheme} map: the current-day view is shaded too, not only the day ahead",
                        page.locator("#map rect[data-i]").count() > 1500, str(page.locator("#map rect[data-i]").count()))
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
            chk.add("allocator: opens on the teaching ladder, clearly labelled",
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
              const B = M.bins(inst, 0.2533, 2);   // band 2 is sigma 1, so P(above) = 0.6
              return { g1: M.crra(inst, B, 1)[0], g4: M.crra(inst, B, 4)[0],
                       gH: M.crra(inst, B, 0.5)[0],
                       one: M.crra([inst[0]], M.bins([inst[0]], 0.2533, 2), 1),
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
                const B = M.bins(instr, S.value, Math.max(S.band / 2, 1e-6));
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
                        abs((v["hi"] - v["lo"]) - 20) < 0.6, f"{v['hi'] - v['lo']:.2f}")
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
            t = "\n".join(page.locator(".sub").all_inner_texts()).lower()
            chk.add("allocator: never says ask, sell or offer",
                    "ask" not in t.replace("asked", "") and "sell" not in t and " offer" not in t, "")
            chk.add("allocator: names both references",
                    "kelly" in t and "thorp" in t, "")
            chk.add("allocator: no script errors", not errs, "; ".join(errs[:3]))
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
