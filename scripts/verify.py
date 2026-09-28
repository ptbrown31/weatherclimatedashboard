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


def errors_of(page):
    """Collect uncaught exceptions and console errors, ignoring the browser's
    own 'Failed to load resource' lines, which are network status, not script
    faults (the forced-failure pass produces them on purpose)."""
    errs = []
    page.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
    page.on("console", lambda m: errs.append("console: " + m.text)
            if m.type == "error" and "Failed to load resource" not in m.text else None)
    return errs


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
                         ("allocator.html", "#allocSvg", "the allocation chart")]
                # a narrowed run walks only the pages it asked for; the sweep is
                # the part of a pass that is pure page loading, so this is where
                # skipping actually buys time rather than only hiding output
                pages = [pg for pg in pages if chk.wants(pg[0] + " " + pg[2])]
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
                # ---- the accuracy page: five figures from the record builder's files
                #
                # Every number on the page is the builder's (docs/accuracy.md); the
                # gates prove the figures draw, each carries its estimator and its
                # sample, the strip names the window and the build, and the copy
                # keeps the exchange's language.
                page.goto(f"{srv.url}/accuracy.html"); page.wait_for_timeout(2500)
                n_lead = page.locator("#accLead path").count()
                chk.add(f"{scheme} accuracy: the lead curve draws the market and every system", n_lead >= 3, f"paths={n_lead}")
                # ---- deterministic skill: the error curve with time to converge under it
                n_conv = page.locator("#accConv path").count()
                chk.add(f"{scheme} accuracy: time to converge sits under the error curve and draws every system",
                        n_conv >= 6 and page.locator("#accDyn, #accCal").count() == 0
                        and "CRPS" not in page.locator("#accLeadBar").inner_text(), f"paths={n_conv}")
                # ---- the second axis on the error curve, and lead counted back from the extreme
                sec_day = page.locator("#accLead path.acc-sec").count()
                lead_svg = page.locator("#accLead").text_content()
                page.locator("#accLead rect[fill='transparent']").nth(26).hover(); page.wait_for_timeout(300)
                day_tip = page.locator("#tip").inner_text() if page.locator("#tip").count() else ""
                chk.add(f"{scheme} accuracy: the error curve carries the share of city-days whose extreme was already observed on a second axis",
                        sec_day == 1 and "already observed" in lead_svg and "100%" in lead_svg
                        and "had already seen the day" in day_tip, f"sec={sec_day} tip={day_tip[-120:]!r}")
                seg_of = """() => {
                  // stroked series only: the market's band is a filled path with two points per bin
                  const ps = [...document.querySelectorAll('#accLead path')].filter(
                    p => p.getAttribute('fill') === 'none' && !p.classList.contains('acc-sec'));
                  return Math.max.apply(null, ps.map(p => (p.getAttribute('d') || '').split(/[ML]/).length - 1)); }"""
                day_seg = page.evaluate(seg_of)
                frame_grp = page.locator("#accLeadBar .tabgroup", has_text="METAR settle")
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
                chk.add(f"{scheme} accuracy: Before the extreme redraws the error curve by hours before the extreme, with its own second axis and no frame tabs",
                        "Hours before the day’s extreme was observed" in rel_txt and "target day begins" not in rel_txt
                        and rel_paths >= 6 and sec_rel == 1 and "forecast this far ahead" in rel_txt
                        and frame_grp.is_hidden() and "before the extreme" in rel_tip
                        and "earlier report had already reached" in rel_tip,
                        f"paths={rel_paths} sec={sec_rel} framehidden={frame_grp.is_hidden()} tip={rel_tip[:80]!r}")
                page.locator("#accLeadBar button", has_text="Before the day ends").first.click(); page.wait_for_timeout(400)
                chk.add(f"{scheme} accuracy: Before the day ends restores the lead from the end of the day and the frame tabs",
                        "Hours before the end of the target day" in page.locator("#accLead").text_content() and frame_grp.is_visible(), "")
                conv_before = page.locator("#accConv").inner_html()
                page.locator("#accLeadBar button", has_text="Within 2").first.click(); page.wait_for_timeout(500)
                chk.add(f"{scheme} accuracy: the tolerance tab redraws time to converge",
                        page.locator("#accConv").inner_html() != conv_before, "")
                page.locator("#accLeadBar button", has_text="Within 1").first.click(); page.wait_for_timeout(300)
                page.locator("#accConv rect[fill='transparent']").nth(16).hover(); page.wait_for_timeout(300)
                conv_tip = page.locator("#tip").inner_text().lower() if page.locator("#tip").count() else ""
                chk.add(f"{scheme} accuracy: the convergence hover ranks the systems and gives each one's half-way lead",
                        "converged" in conv_tip and "half by" in conv_tip, conv_tip.replace("\n", " | ")[:140])
                # the market's row in a hover table reads in the hover box's own ink
                tip_ink = page.evaluate("""() => { const t = document.querySelector('#tip'); const r = t && t.querySelector('tr.tfx td');
                                                   return r ? [getComputedStyle(t).color, getComputedStyle(r).color] : null; }""")
                chk.add(f"{scheme} accuracy: the ForecastEx row in a hover table is set in the hover box's ink",
                        bool(tip_ink) and tip_ink[0] == tip_ink[1], str(tip_ink))
                # ---- probabilistic skill: CRPS and the Brier score at full width, then the diagrams
                cal_file = json.loads(urllib.request.urlopen(f"{srv.url}/data/snapshots/accuracy/calibration.json").read().decode())
                cal_own = (((cal_file.get("metric", {}).get("high", {}) or {}).get("cohorts", {}) or {}).get("own", {}) or {})
                ensb = cal_own.get("ensembles", {})
                brier_sys = set(((((cal_own.get("price", {}).get("mid", {}) or {}).get("brier", {}) or {}).get("strikes", {}) or {})
                                 .get("nearMoney", {}).get("cli", {}) or {}).get("systems", {}))
                widths = page.evaluate("""() => ['#accLead', '#accCrps', '#accBrier'].map(s => {
                    const e = document.querySelector(s); return e ? Math.round(e.getBoundingClientRect().width) : 0; })""")
                prob_key = page.eval_on_selector_all("#accProbKey > span[data-id]", "e => e.map(x => x.getAttribute('data-id'))")
                prob_txt = " ".join(page.eval_on_selector_all("#accCrps text, #accBrier text", "e => e.map(x => x.textContent)"))
                chk.add(f"{scheme} accuracy: CRPS and the Brier score each take the full width of the lead curve, and nothing else is charted by lead",
                        widths[0] > 0 and all(abs(w - widths[0]) <= 2 for w in widths)
                        and "CRPS" in prob_txt and "brier score as root mean square, cents" in prob_txt.lower()
                        and all(page.locator(f"{sel} path[stroke-dasharray]").count() >= 3 for sel in ("#accCrps", "#accBrier"))
                        and page.locator("#accRel, #accRes, #accSpans, ul.conv").count() == 0, f"widths={widths}")
                brier_all = ((((cal_own.get("price", {}).get("mid", {}) or {}).get("brier", {}) or {}).get("strikes", {}) or {})
                             .get("all", {}).get("metar", {}) or {}).get("systems", {})
                same_n = len({tuple(v.get("n", [])) for v in brier_all.values()}) == 1
                prob_note = page.locator("#accProbKey").inner_text()
                chk.add(f"{scheme} accuracy: the probabilistic figures draw the market against the four ensembles on the same contracts and days",
                        prob_key[:1] == ["FX"] and set(prob_key[1:]) == {"AIFS", "GEFS", "GEM_ENS", "ICON_ENS"}
                        and len(ensb) >= 4 and brier_sys == {"FX", "AIFS", "GEFS", "GEM", "ICON"} and same_n
                        and "drawn on the city-days all five share" in prob_note,
                        f"key={prob_key} brier={sorted(brier_sys)} same_n={same_n}")
                diag_txt = page.eval_on_selector_all("#accDiag svg.acc-cal-rel text", "e => e.map(x => x.textContent)")
                lead_titles = [t for t in diag_txt if t.endswith("before the day ends")]
                n_calc = page.locator("#accDiag circle").count()
                chk.add(f"{scheme} accuracy: the reliability diagrams follow the charts, one per lead bin to six hours out, titled by lead and sample only",
                        lead_titles == ["36 to 24 h before the day ends", "24 to 12 h before the day ends", "12 to 6 h before the day ends"]
                        and n_calc >= 10 and not any(t.lower().startswith(("reliability", "resolution")) for t in diag_txt),
                        str(lead_titles))
                for tab in ("Fixed sample", "Two-sided books only", "NWS climate report", "Near-money strikes"):
                    before = page.locator("#accBrier").inner_html()
                    page.locator("#accProbBar button", has_text=tab).first.click(); page.wait_for_timeout(500)
                    chk.add(f"{scheme} accuracy: the {tab} tab redraws the Brier score",
                            page.locator("#accBrier").inner_html() != before, "")
                for tab in ("Every shared day", "Yes price midpoint", "METAR settle", "Every quoted strike"):
                    page.locator("#accProbBar button", has_text=tab).first.click(); page.wait_for_timeout(300)
                page.locator("#accCrps rect[fill='transparent']").nth(24).hover(); page.wait_for_timeout(300)
                crps_tip = page.locator("#tip").inner_text() if page.locator("#tip").count() else ""
                chk.add(f"{scheme} accuracy: the CRPS hover ranks the distributions against ForecastEx",
                        "crps" in crps_tip.lower() and "vs forecastex" in crps_tip.lower() and "Amer. Ens." in crps_tip,
                        crps_tip.replace("\n", " | ")[-160:])
                page.locator("#accBrier rect[fill='transparent']").nth(20).hover(); page.wait_for_timeout(300)
                brier_tip = page.locator("#tip").inner_text() if page.locator("#tip").count() else ""
                chk.add(f"{scheme} accuracy: the Brier hover ranks the distributions in cents against ForecastEx",
                        "rms" in brier_tip.lower() and "vs forecastex" in brier_tip.lower() and "Amer. Ens." in brier_tip
                        and re.search(r"\d+\.\d c", brier_tip) is not None, brier_tip.replace("\n", " | ")[-160:])
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
                n_grid = page.locator("#accGrid tbody tr").count()
                chk.add(f"{scheme} accuracy: the scorecard grid draws its rows", n_grid >= 6, f"rows={n_grid}")
                notes = page.eval_on_selector_all(".accnote", """e => e.map(x => ({
                    eq: (x.querySelector('.eq') || {textContent: ''}).textContent,
                    n: (x.querySelector('.rule.n') || {textContent: ''}).textContent }))""")
                bad_notes = [i for i, nt in enumerate(notes)
                             if "=" not in nt["eq"] or not re.search(r"\b(?:Sample|n)\s*=?\s*[\d,]*\d", nt["n"])]
                chk.add(f"{scheme} accuracy: every method note holds an estimator and a counted sample",
                        len(notes) == 4 and not bad_notes, f"notes={len(notes)} bad={bad_notes}")
                # ---- how far back each record goes: every note names its days, and the
                # systems table dates every record now that the coverage strip is gone
                spans = page.eval_on_selector_all(".accnote", "e => e.map(x => (x.querySelector('.rule.span') || {textContent: ''}).textContent)")
                chk.add(f"{scheme} accuracy: every method note says which days its figure drew on",
                        len(spans) == 4 and all((t or "").strip() for t in spans),
                        str([(t or "")[:40] for t in spans]))
                lead_key = page.locator("#accLeadKey .ks").count()
                chk.add(f"{scheme} accuracy: the lead curve's key dates every system's record",
                        lead_key >= 6, f"entries={lead_key}")
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
                # the working paper sits at one fixed URL that each new version replaces
                paper_sel = "a[href='papers/Brown_2026_Improvement_of_Daily_Temperature_Forecasts_from_a_Prediction_Market.pdf']"
                acc_paper = page.locator(f".wrap {paper_sel}").count()
                no_wilson = "wilson" not in page.locator("#accProbKey").inner_text().lower()
                page.goto(f"{srv.url}/faq.html"); page.wait_for_timeout(400)
                chk.add(f"{scheme} accuracy: the FAQ still reaches the page",
                        page.locator(".prose a[href='accuracy.html']").count() >= 1, "")
                chk.add(f"{scheme} accuracy: the page and the FAQ link the working paper at its fixed URL, and the diagrams claim no per-bin interval",
                        acc_paper == 1 and page.locator(f".prose {paper_sel}").count() == 1 and no_wilson,
                        f"accuracy={acc_paper} wilson_free={no_wilson}")
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
                # dated off the clock: "the hours still to come" is measured against
                # now, so a fixed date would leave nothing ahead and the comparison
                # the panel draws would never be exercised
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
                _past = [_now - dt.timedelta(hours=k) for k in (5, 4, 3, 2)]
                w_rows = [{"t": t.strftime("%Y-%m-%dT%H:54:00Z"), "tempF": 66.0, "tempC": 18.9, "type": "METAR",
                           "wspd": 9.0 + i, "wgst": (24.0 if i == 3 else None)} for i, t in enumerate(_past)]
                _ahead = [_now + dt.timedelta(hours=k) for k in (1, 2, 3)]

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
                        d["nbm"] = {"cycle": _iso_z(_now), "hourlyFrom": _iso_z(_ahead[0]), "txn": {},
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
                chk.add(f"{scheme} wind panel: the plot draws a hover band per reading",
                        bool(wp and wp["bands"] >= 6), str(wp and wp["bands"]))
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
                _nt = max(_now - dt.timedelta(minutes=50),
                          dt.datetime.combine(dt.date.fromisoformat(DAY), dt.time(0, 5),
                                              tzinfo=_Z("America/New_York")).astimezone(dt.timezone.utc))
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
                            land["y"] > 0 and abs(land["top"] - 12) <= 20, str(land))
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
                                land["y"] > 0 and abs(land["top"] - 12) <= 20 and "wind-markets" in land["path"], str(land))
                page.unroute("**/data/snapshots/**")

                # ---- the analysis resolution page: a proposed settlement
                #      framework, unlisted, drawn from samples/snapshots/analysis
                #
                # The page computes nothing, so the checks are that each control
                # moves what it says it moves, that a place opens with its day in
                # full, and that the hypothetical ladder can never be read as a
                # market: hatched, Yes or No and nothing else, no cents, no link.
                ANA = "analysis-resolution.html"
                ANA_SNAP = os.path.join(ROOT, "samples", "snapshots", "analysis")
                # The fixtures are a real local run of the job, so the day and
                # the hours the checks drive come from the fixture index files
                # rather than from dates written into this script: the newest
                # day whose New York URMA day is complete (final pills), the
                # oldest day (routed away for the hollow-dot check), and the
                # last two URMA temperature frames listed on a day the page
                # can select (the stepper).
                with open(os.path.join(ANA_SNAP, "index.json")) as fh:
                    ana_index = json.load(fh)
                ana_days = list(ana_index["days"])
                ana_day = None
                ana_entries = {}          # New York's entry per fixture day
                for d_ in reversed(ana_days):
                    with open(os.path.join(ANA_SNAP, "days", d_ + ".json")) as fh:
                        e_ = (json.load(fh).get("locations") or {}).get("new-york-ny") or {}
                    ana_entries[d_] = e_
                    if ana_day is None and (e_.get("urma") or {}).get("complete") and (e_.get("urma") or {}).get("precip"):
                        ana_day = d_
                chk.add(f"{scheme} analysis fixtures: a day with a complete New York URMA entry", ana_day is not None, str(ana_days))
                ana_day = ana_day or ana_days[-1]
                with open(os.path.join(ANA_SNAP, "grid", "index.json")) as fh:
                    ana_ut = (json.load(fh).get("frames") or {}).get("urma", {}).get("temp", {})
                ana_fday = next((d_ for d_ in sorted(ana_ut, reverse=True) if d_ in ana_days and len(ana_ut[d_]) >= 2), None)
                chk.add(f"{scheme} analysis fixtures: two URMA temperature frames on a selectable day", ana_fday is not None, str(ana_ut))
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

                def ana_no_resolved(route):
                    resp = route.fetch(); d = json.loads(resp.text())
                    d.pop("lastResolvedDay", None); d.pop("dayStatus", None)
                    return route.fulfill(response=resp, body=json.dumps(d))

                page.route("**/analysis/index.json", ana_no_resolved)
                page.goto(f"{srv.url}/{ANA}"); page.wait_for_timeout(2200)
                chk.add(f"{scheme} analysis: an index without the field opens on the newest day",
                        page.evaluate("document.querySelector('#anaDay').value") == ana_days[-1],
                        page.evaluate("document.querySelector('#anaDay').value"))
                page.unroute("**/analysis/index.json")
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
                chk.add(f"{scheme} analysis: fifty places and a frame under them",
                        a0["dots"] == 50 and a0["href"].startswith("data:image/png"), str([a0["dots"], a0["href"][:22]]))
                page.click('button[data-var="gust"]'); page.wait_for_timeout(900)
                a1 = page.evaluate(ana_state)
                chk.add(f"{scheme} analysis: the variable button changes the legend and the frame",
                        "Peak gust" in a1["legend"] and a1["legend"] != a0["legend"]
                        and a1["href"].startswith("data:image/png") and a1["href"] != a0["href"]
                        and "var=gust" in a1["url"], a1["legend"][:60])
                page.click('button[data-var="high"]'); page.click('button[data-product="rtma"]'); page.wait_for_timeout(900)
                a2 = page.evaluate(ana_state)
                chk.add(f"{scheme} analysis: the product button changes the legend and the frame",
                        "RTMA" in a2["legend"] and a2["legend"] != a0["legend"]
                        and a2["href"].startswith("data:image/png") and a2["href"] != a0["href"]
                        and "product=rtma" in a2["url"], a2["legend"][:60])
                # the stepper walks the last two URMA temperature frames the
                # fixtures list; the oldest day's file is routed away so the
                # day select lands on a day with no file
                ana_gone = ana_days[0] if ana_days[0] != ana_fday else ana_days[-1]

                def ana_routes(route):
                    u = route.request.url
                    if u.endswith(f"/analysis/days/{ana_gone}.json"):
                        return route.fulfill(status=404, body="not there")
                    return route.continue_()

                page.route("**/data/snapshots/**", ana_routes)
                page.goto(f"{srv.url}/{ANA}?var=high&product=urma&day={ana_fday}&hour={ana_hours[1]}"); page.wait_for_timeout(2200)
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
                        page.locator("#vdots circle.absent").count() == 50, str(page.locator("#vdots circle.absent").count()))
                page.unroute("**/data/snapshots/**")
                # a dot opens the place, with its day in full
                page.goto(f"{srv.url}/{ANA}?var=high&product=urma&day={ana_day}"); page.wait_for_timeout(2200)
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
                chk.add(f"{scheme} analysis: both analyses and the resolved value are drawn",
                        pn["series"] == 2 and pn["resolved"] == 1, str([pn["series"], pn["resolved"]]))
                chk.add(f"{scheme} analysis: the hypothetical rungs are hatched and say Yes or No only",
                        pn["hatched"] >= 5 and pn["fills"] == ["url(#wxHatch)"] and pn["rungs"] == ["No", "Yes"],
                        str([pn["hatched"], pn["fills"], pn["rungs"]]))
                chk.add(f"{scheme} analysis: no rung carries a link or a price, and the panel keeps the exchange's language",
                        pn["links"] == 0 and "¢" not in pn["text"]
                        and not re.search(r"\b(ask|sell|offer|bid)\b", pn["text"], re.I)
                        and "No contract settles on it" in pn["text"], str(pn["links"]))
                # the provisional pill, on a day whose URMA is short of 24 hours
                def ana_partial(route):
                    u = route.request.url
                    if u.endswith(f"/analysis/days/{ana_day}.json"):
                        resp = route.fetch(); d = json.loads(resp.text())
                        e = d["locations"]["new-york-ny"]["urma"]
                        e.update({"hours": 17, "complete": False, "final": False})
                        # precipitation resolves at the first COMPLETE pass, so a
                        # partial day has not resolved either
                        if e.get("precip"):
                            e["precip"].update({"resolved": False, "resolvedAt": None, "revised": None})
                        return route.fulfill(response=resp, body=json.dumps(d))
                    return route.continue_()

                page.route("**/data/snapshots/**", ana_partial)
                page.goto(f"{srv.url}/{ANA}?product=urma&day={ana_day}&loc=new-york-ny"); page.wait_for_timeout(2200)
                pills = page.eval_on_selector_all("#locPanel .anapill", "e => e.map(x => x.textContent)")
                chk.add(f"{scheme} analysis: ?loc= opens the panel on load",
                        page.evaluate("!document.querySelector('#locPanel').hidden") and len(pills) == 5, str(len(pills)))
                chk.add(f"{scheme} analysis: the provisional pill appears when URMA is incomplete, with the count",
                        all("provisional" in p_ and "17 of 24 hours" in p_ for p_ in pills)
                        and page.locator('#vdots g.dot[data-loc="new-york-ny"] circle.prov').count() == 1, str(pills[:2]))
                page.unroute("**/data/snapshots/**")
                # the wording follows the state: a value that can still move is
                # running, one that cannot is resolved, a closed day's is closed.
                # The partial route above is still on for the first read
                ana_rule = "() => (document.querySelector('#locPanel text.anareslbl') || {}).textContent || ''"
                page.route("**/data/snapshots/**", ana_partial)
                page.goto(f"{srv.url}/{ANA}?var=high&product=urma&day={ana_day}&loc=new-york-ny"); page.wait_for_timeout(2200)
                r_part = page.evaluate(ana_rule)
                page.unroute("**/data/snapshots/**")
                page.goto(f"{srv.url}/{ANA}?var=precip&product=urma&day={ana_day}&loc=new-york-ny"); page.wait_for_timeout(2200)
                r_final = page.evaluate(ana_rule)
                # the running precipitation curve ends on the file's own total
                # once the day is complete: the last point of the solid line
                # sits on the resolved rule
                ana_end = page.evaluate("""() => {
                  const p = document.querySelector('#locPanel');
                  const d = p.querySelectorAll('svg.ts path.anaseries')[1].getAttribute('d').trim();
                  const last = d.split(/[ML]/).filter(Boolean).pop().trim().split(' ');
                  const tip = p.querySelectorAll('rect.hband');
                  return { y: +last[1], rule: +p.querySelector('line.anares').getAttribute('y1'), bands: tip.length };
                }""")
                page.locator("#locPanel rect.hband").last.hover(); page.wait_for_timeout(300)
                ana_tip = page.evaluate("() => [...document.querySelectorAll('.tip')].map(t => t.textContent).join(' ')")
                ana_cards = page.evaluate("() => [...document.querySelectorAll('#locPanel .anacard')].map(c => [c.dataset.var, c.querySelectorAll('.ae')[0].textContent])")
                page.click('button[data-product="rtma"]'); page.wait_for_timeout(900)
                r_run = page.evaluate(ana_rule)
                chk.add(f"{scheme} analysis: the chart rule says resolved only when final, running while provisional with the count",
                        r_final.startswith("resolved ") and "(URMA, final)" in r_final
                        and r_part.startswith("running ") and "provisional, 17 of 24 hours" in r_part
                        and r_run.startswith("running ") and "(RTMA, provisional, 24 of 24 hours)" in r_run,
                        str([r_final, r_part, r_run]))
                chk.add(f"{scheme} analysis: the complete URMA precipitation curve ends on the resolved rule and the tooltip names the running sum",
                        abs(ana_end["y"] - ana_end["rule"]) < 0.6 and "Running sum of the hourly analyses" in ana_tip,
                        str([ana_end, ana_tip[:120]]))
                ana_dec = {k: (re.search(r"exact -?\d+\.(\d+)", v) or [None, ""])[1] for k, v in ana_cards}
                chk.add(f"{scheme} analysis: the exact figure keeps the file's decimals per variable",
                        [len(ana_dec.get(k, "")) for k in ("high", "low", "gust", "wind", "precip")] == [1, 1, 1, 2, 4], str(ana_cards))
                # a closed day in the fixtures, if one is there: the RTMA day is
                # closed too, not provisional
                ana_closed = next((d_ for d_ in ana_days if ((ana_entries.get(d_) or {}).get("rtma") or {}).get("closed")), None)
                if ana_closed:
                    page.goto(f"{srv.url}/{ANA}?var=high&product=rtma&day={ana_closed}&loc=new-york-ny"); page.wait_for_timeout(2200)
                    r_closed = page.evaluate(ana_rule)
                    pills_c = page.eval_on_selector_all("#locPanel .anapill", "e => e.map(x => x.textContent)")
                    chk.add(f"{scheme} analysis: a closed RTMA day says closed, with its count",
                            r_closed.startswith("closed ") and all(p_.startswith("closed, ") and "of" in p_ for p_ in pills_c), str([r_closed, pills_c[:2]]))
                # the freshness pill judges the job by the index's written time,
                # not by the analysis valid hour NOAA's lag keeps behind the clock;
                # the valid hours stand beside it
                def ana_fresh(route):
                    resp = route.fetch(); d = json.loads(resp.text())
                    d["written"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 120))
                    d["asof"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3 * 3600))
                    return route.fulfill(response=resp, body=json.dumps(d))

                page.route("**/analysis/index.json", ana_fresh)
                page.goto(f"{srv.url}/{ANA}"); page.wait_for_timeout(2200)
                fr = page.evaluate("""() => ({ cls: document.querySelector('.status').className, text: document.querySelector('.status').textContent,
                                              newest: (document.querySelector('#anaNewest') || {}).textContent || '' })""")
                page.unroute("**/analysis/index.json")
                ana_latest = {k: (v.get("latest") or "").replace("T", " ").replace(":00Z", " UTC") for k, v in (ana_index.get("sources") or {}).items()}
                chk.add(f"{scheme} analysis: the freshness pill reads the index's written time against the job cadence",
                        "live" in fr["cls"] and "behind" not in fr["text"] and "every 10 minutes" in fr["text"], str(fr)[:160])
                chk.add(f"{scheme} analysis: the newest analysis valid hours stay visible per product",
                        fr["newest"].startswith("newest analysis RTMA ") and ana_latest.get("rtma", "x") in fr["newest"]
                        and "URMA " + ana_latest.get("urma", "x") in fr["newest"], fr["newest"])
                # the place select and the keyboard both open the panel; the
                # picked dot is drawn last so it is on top where dots overlap
                page.goto(f"{srv.url}/{ANA}?var=high&product=urma&day={ana_fday}&hour={ana_hours[0]}"); page.wait_for_timeout(2200)
                page.select_option("#anaLoc", "chicago-il"); page.wait_for_timeout(1400)
                sel = page.evaluate("""() => [document.querySelector('#locPanel').hidden, (document.querySelector('#locPanel h2') || {}).textContent || '',
                                            location.search, document.querySelector('#vdots g.dot:last-child').dataset.loc,
                                            [...document.querySelectorAll('#anaLoc option')].length]""")
                chk.add(f"{scheme} analysis: the place select opens the panel and raises the dot",
                        sel[0] is False and sel[1].startswith("Chicago") and "loc=chicago-il" in sel[2] and sel[3] == "chicago-il" and sel[4] == 51, str(sel))
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
                ls_keys = page.evaluate("() => Object.keys(localStorage).filter(k => /^wx:analysis\\/grid\\/(rtma|urma)\\//.test(k))")
                chk.add(f"{scheme} analysis: no grid frame is written to localStorage after stepping", ls_keys == [], str(ls_keys)[:120])
                # a day with no frames listed carries no hour in the address
                ana_noframes = next((d_ for d_ in ana_days if not ana_ut.get(d_)), None)
                if ana_noframes:
                    page.select_option("#anaDay", ana_noframes); page.wait_for_timeout(900)
                    nf = page.evaluate("() => [location.search, document.querySelector('#anaCap').textContent]")
                    chk.add(f"{scheme} analysis: a day with no frames clears hour= and names the variable in words",
                            "hour=" not in nf[0] and "no URMA temperature frames" in nf[1], str(nf))
                # a day or place file that could not be read is said to be
                # unreadable, not a day with no hours; a fresh context so no
                # cached copy stands in
                ana_ctx2 = browser.new_context(color_scheme=scheme, viewport={"width": 1200, "height": 900})
                ana_p2 = ana_ctx2.new_page()
                ana_p2.route("**/analysis/days/**", lambda route: route.fulfill(status=404, body="gone"))
                ana_p2.route("**/analysis/loc/**", lambda route: route.fulfill(status=404, body="gone"))
                ana_p2.goto(f"{srv.url}/{ANA}?var=high&product=urma&day={ana_day}&loc=new-york-ny"); ana_p2.wait_for_timeout(2200)
                ur = ana_p2.evaluate("""() => [[...document.querySelectorAll('#locPanel .anacard .ae')].map(x => x.textContent),
                                              [...document.querySelectorAll('#locPanel p.cap')].map(x => x.textContent).join(' ')]""")
                chk.add(f"{scheme} analysis: an unreadable day file is reported as unreadable, not as no hours",
                        len(ur[0]) == 5 and all("could not be read" in t for t in ur[0]) and "could not be read" in ur[1]
                        and "No hours read yet" not in ur[1], str(ur)[:160])
                ana_ctx2.close()
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
                for fn_ in sorted(os.listdir(os.path.join(ANA_SNAP, "grid", "urma", "temp"))):
                    with open(os.path.join(ANA_SNAP, "grid", "urma", "temp", fn_)) as fh:
                        if "new-york-ny" in (json.load(fh).get("windows") or {}):
                            ana_wf = fn_
                chk.add(f"{scheme} analysis fixtures: a URMA temperature frame with windows", ana_wf is not None, str(ana_wf))
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
                           census: ny ? ny.querySelectorAll('circle.census').length : 0,
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
                page.goto(f"{srv.url}/{ANA}?var=high&product=urma&day={ana_wday}&hour={ana_whh}&loc=new-york-ny"); page.wait_for_timeout(2200)
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
                chk.add(f"{scheme} analysis cell mode: the label carries the dot's value with a leader and the Census point",
                        z1["label"] == z0["dotval"] and z1["leaders"] == 1 and z1["census"] == 1, str([z1["label"], z0["dotval"], z1["leaders"]]))
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
                        and re.search(r"-?\d+\.\d°F", ana_wtip) is not None, ana_wtip[:120])
                # hover on the mark reads the resolving cell, with no row about
                # the hidden field (the cell is what is under the pointer)
                page.hover('#vdots g.dot[data-loc="new-york-ny"] circle.census'); page.wait_for_timeout(300)
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

                page.route("**/analysis/index.json", ana_nokeys)
                page.goto(f"{srv.url}/{ANA}{z1['url']}"); page.wait_for_timeout(2200)
                zk = page.evaluate(ana_cell_state)
                page.unroute("**/analysis/index.json")
                chk.add(f"{scheme} analysis cell mode: a place without the geometry keys keeps its plain dot and value in cell mode",
                        zk["hidden"] and zk["census"] == 0 and zk["label"] == "" and zk["dotval"] == z0["dotval"] and zk["wcells"] == 0
                        and zk["outlines"] >= 1 and zk["zbtn"] is True, str([zk["dotval"], zk["census"], zk["outlines"], zk["zbtn"]]))
                # a dry precipitation window: every zero cell is drawn clear
                # and still answers hover with 0.00 in, a null cell inside the
                # grid says it is missing, and the caption says dry cells are
                # left clear. The fixture frame is routed to all zeros with
                # one null at (3, -2).
                ana_pf = None
                ana_pdir = os.path.join(ANA_SNAP, "grid", "urma", "precip")
                for fn_ in sorted(os.listdir(ana_pdir)) if os.path.isdir(ana_pdir) else []:
                    with open(os.path.join(ana_pdir, fn_)) as fh:
                        if "new-york-ny" in (json.load(fh).get("windows") or {}):
                            ana_pf = fn_
                chk.add(f"{scheme} analysis fixtures: a URMA precipitation frame with a New York window", ana_pf is not None, str(ana_pf))
                if ana_pf:
                    def ana_dry(route):
                        resp = route.fetch(); d = json.loads(resp.text())
                        w_ = d["windows"]["new-york-ny"]; half_ = w_.get("half", 10); n_ = 2 * half_ + 1
                        w_["values"] = [0] * (n_ * n_)
                        w_["values"][(-2 + half_) * n_ + (3 + half_)] = None
                        return route.fulfill(response=resp, body=json.dumps(d))

                    page.route(f"**/analysis/grid/urma/precip/{ana_pf}", ana_dry)
                    page.goto(f"{srv.url}/{ANA}?var=precip&product=urma&day={ana_pf[:4]}-{ana_pf[4:6]}-{ana_pf[6:8]}&hour={ana_pf[9:11]}&loc=new-york-ny")
                    page.wait_for_timeout(2200)
                    page.click("#anaZoomCell"); page.wait_for_timeout(600)
                    page.unroute(f"**/analysis/grid/urma/precip/{ana_pf}")
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

                page.route(f"**/analysis/grid/urma/temp/{ana_wf}", ana_nowin)
                page.goto(f"{srv.url}/{ANA}{z1['url']}"); page.wait_for_timeout(2200)
                z3 = page.evaluate(ana_cell_state)
                page.unroute(f"**/analysis/grid/urma/temp/{ana_wf}")
                chk.add(f"{scheme} analysis cell mode: a frame without windows still draws the outline and the label, with no window cells",
                        z3["outlines"] >= 1 and z3["label"] == z0["dotval"] and z3["wcells"] == 0 and z3["hidden"], str([z3["outlines"], z3["label"], z3["wcells"]]))
                # Reset brings the raster and the dots back
                page.click("#anaZoom button[title='back to the whole country']"); page.wait_for_timeout(600)
                z4 = page.evaluate(ana_cell_state)
                chk.add(f"{scheme} analysis cell mode: Reset returns the raster and the dots and clears the view from the address",
                        z4["outlines"] == 0 and not z4["hidden"] and z4["dotval"] == z0["dotval"] and "z=" not in z4["url"], str([z4["outlines"], z4["url"]]))
                # the tooltip is hidden when the wheel carries the map into cell
                # mode, rather than keeping the lattice value from before the flip
                page.evaluate("() => window.scrollTo(0, 0)"); page.wait_for_timeout(200)
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
                        body[0] == "mapTitle" and body.index("card") < body.index("dotKey"),
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
                page.goto(f"{srv.url}/index.html"); page.wait_for_timeout(1600)

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
                chk.add(f"{scheme} sources: every forecast system is listed with a link, the market first, then the four families",
                        groups == ["Prediction market", "Raw numerical weather prediction models",
                                   "Numerical weather prediction with model output statistics",
                                   "Human forecasting systems", "AI systems"]
                        and sys_links >= 21 and any(k.startswith("Deterministic") for k in kinds)
                        and any(k.startswith("Probabilistic") for k in kinds),
                        f"groups={groups} links={sys_links}")
                heads = page.eval_on_selector_all("table.acc-sources:not(.acc-feeds) thead th", "e => e.map(x => x.textContent.trim())")
                recs = page.eval_on_selector_all("table.acc-sources td.rec", "e => e.map(x => x.textContent.trim())")
                chk.add(f"{scheme} sources: the table carries grid, time step, updates and a filled record for every row",
                        heads == ["System", "What it is", "Kind", "Grid", "Time step", "Updates", "Record"]
                        and len(recs) >= 21 and all(r.startswith("From ") for r in recs),
                        f"heads={heads} unfilled={[r for r in recs if not r.startswith('From ')][:3]}")
                chk.add(f"{scheme} sources: the feed table moved to the accuracy page",
                        page.locator("#sources").count() == 1 and "aviationweather.gov METAR" in acc_src, "")
                # every name in the table is a name the figures use
                names = page.eval_on_selector_all("table.acc-sources td.sys a", "e => e.map(x => x.textContent.trim())")
                known = set(page.evaluate("Object.values(WXAcc.NAME)")) | {"American Ensemble", "Canadian Ensemble", "German Ensemble"}
                chk.add(f"{scheme} sources: the table names each system as the figures do",
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
                chk.add(f"{scheme} map: the color key sits under the map and carries nothing else",
                        page.evaluate("""() => { const m = document.querySelector('#map').getBoundingClientRect();
                          const k = document.querySelector('#dotKey');
                          if (!k || document.querySelector('.how')) return false;
                          const t = k.textContent;
                          return k.getBoundingClientRect().top - m.bottom < 200
                                 && t.includes('running warmer') && t.includes('running cooler'); }"""), "")
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
                # month in progress, then the classic ladder listing
                page.goto(f"{srv.url}/contract.html?id=SWTUS"); page.wait_for_timeout(1500)
                cb = page.locator("#cBody").inner_text()
                chk.add(f"{scheme} severe contract: history then the month in progress",
                        "SETTLEMENT BASIS" in cb and "MONTH IN PROGRESS" in cb
                        and cb.index("SETTLEMENT BASIS") < cb.index("MONTH IN PROGRESS")
                        and page.locator("#cBody svg").count() >= 3, str(page.locator("#cBody svg").count()))
                chk.add(f"{scheme} severe contract: no script errors", not errs, "; ".join(errs)[:300])
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
                        "typical gap today" in leg.lower() and "°" in leg, leg[:110])
                chk.add(f"{scheme} map color: the legend says color is distance from typical, not from zero",
                        "not from zero" in leg and "raw gap" not in leg, leg[-90:])
                page.locator("#map g.dot").first.hover(force=True); page.wait_for_timeout(200)
                t_enc = page.locator("#tip").inner_text()
                chk.add(f"{scheme} map hover: no dot-encoding block and nothing claiming to expect a high",
                        "Dot encoding" not in t_enc and "Expected high" not in t_enc
                        and "Expected low" not in t_enc and "Forecasts" in t_enc, t_enc[-140:])
                # m1 is today's highs, m2 tomorrow's. The current-day hover names the
                # forecast the office issued and what the station has recorded, and
                # attributes neither to an expectation of the page's own.
                chk.add(f"{scheme} map: the current-day view names the issued forecast and the record so far",
                        "NWS high issued for today" in t_enc and "Observed high so far" in t_enc, t_enc[-140:])
                page.locator("#m2").click(); page.wait_for_timeout(400)
                page.locator("#map g.dot").first.hover(force=True); page.wait_for_timeout(200)
                t_tmw = page.locator("#tip").inner_text()
                chk.add(f"{scheme} map: the day-ahead view names the forecasts standing for that day",
                        "NWS high" in t_tmw and "Blend of Models" in t_tmw
                        and "Expected" not in t_tmw, t_tmw[-140:])
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
                        tt["todayHigh"].split("\n")[1].startswith("Implied high"), tt["todayHigh"][:90])
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
                chk.add(f"{scheme} map: today's lows name the issued forecast and what has been recorded",
                        "NWS low issued for today" in t_lo and "Observed low so far" in t_lo
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
                t_today = page.locator("#mapTitle").inner_text()
                chk.add(f"{scheme} map today view: the current day, against the NWS forecast rather than what was issued",
                        "TODAY" in t_today.upper() and "observed" not in t_today.lower(), t_today[:80])
                dots_today = page.locator("#map circle").count()
                chk.add(f"{scheme} map today view: dots are drawn", dots_today > 0, f"circles={dots_today}")
                page.locator("#m2").click(); page.wait_for_timeout(400)
                chk.add(f"{scheme} map tomorrow view: still available and shaded",
                        "TOMORROW" in page.locator("#mapTitle").inner_text().upper(), page.locator("#mapTitle").inner_text()[:60])

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

                def _ledger(interim, final):
                    # a distinct recorded price per delivery, so scrubbing back has to
                    # move the square rather than leave today's price under an old ladder
                    steps = []
                    for k, hh in enumerate(HOURS):
                        t0 = BASE + dt.timedelta(hours=hh)
                        steps.append({"id": t0.strftime("%Y%m%d%H"), "kind": "livecyc",
                                      "at": t0.strftime("%Y-%m-%dT%H:%M:%SZ"), "ts": "t",
                                      "sites": _lad(0.2 + 0.1 * k),
                                      "siteMeta": {"BR": {"name": "Brownsville"}, "GA": {"name": "Galveston"}},
                                      "pwin": {"BR": 80.0, "GA": 20.0},
                                      "prices": {"BR": {str(t): 10.0 + 5 * k for t in THR}}})
                    # stamped the way the pipeline stamps it, the vendor's time and the
                    # moment it was recorded, so it takes its place on the axis by arrival
                    if interim:
                        steps.append({"id": "INT", "kind": "interim", "at": "2026-09-02T14:50:56Z", "ts": "2026-09-02T14:54:48Z",
                                      "sites": _lad(0.95, [0, 0, 0, 0, 0, 0]),
                                      "siteMeta": {"BR": {"name": "Brownsville", "covered": True},
                                                   "GA": {"name": "Galveston", "covered": True}},
                                      "pwin": {"BR": 99.0, "GA": 1.0},
                                      "prices": {"BR": {str(t): 44.0 for t in THR}}})
                    return {"schema": 2, "name": "Erin", "year": 2026, "attribution": "Powered by Reask", "thresholds": THR,
                            "steps": steps, "sites": {"BR": {"name": "Brownsville", "firstStep": "2026090100"},
                                                      "GA": {"name": "Galveston", "firstStep": "2026090100"}},
                            "final": {"BR": 96.0, "GA": 41.0} if final else None}

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

                def _storm_routes(interim, final, index_final=False, drop_hlf=False, drop_pool=False,
                                  placeholder_pool=False):
                    def handler(route):
                        u = route.request.url
                        if u.endswith("/reask.json"):
                            return route.fulfill(status=200, content_type="application/json", body=json.dumps(_index_ff(interim, index_final)))
                        if "/storm/Erin_2026.json" in u:
                            return route.fulfill(status=200, content_type="application/json", body=json.dumps(_ledger(interim, final)))
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
                            if drop_pool:
                                m["markets"] = [x for x in m["markets"] if x.get("symbol") != "LHLERG"]
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
                    n_del = 6 + (1 if _interim else 0)          # deliveries on the timeline
                    ncards = page.locator("#liveStorms .scardwrap").count()
                    chk.add(f"{scheme} storm ({tag}): a card per signalled location", ncards >= 1, f"cards={ncards}")
                    labels = page.eval_on_selector_all("#liveStorms .scardwrap text", "els => els.map(e => e.textContent)")
                    # the pending columns: the next file, the interim and the final
                    # hold their places while the storm runs; once settled the final
                    # column is real and nothing is still to come
                    pend = [t for t in labels if t in ("next", "Metryc", "Interim", "final")]
                    if _final:
                        chk.add(f"{scheme} storm ({tag}): the settled axis ends on the real settlement columns",
                                "Metryc" in labels and "Interim" in labels and "final" in labels and "next" not in labels, str(pend))
                    elif _interim:
                        # once the interim has landed the storm waits on the final and nothing else
                        chk.add(f"{scheme} storm ({tag}): after the interim only the final is still to come",
                                "Interim" in labels and "final" in labels and "next" not in labels, str(pend))
                    else:
                        chk.add(f"{scheme} storm ({tag}): the next file, the interim and the final hold their columns",
                                "next" in labels and "Metryc" in labels and "Interim" in labels and "final" in labels, str(pend))
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
                    chk.add(f"{scheme} storm ({tag}): the pool ladder draws the site's Yes/No bars and no tick of the site's own",
                            bool(plad and plad["rows"] >= 1 and plad["yes"] >= 1 and plad["no"] >= 1
                                 and plad["calcTicks"] == 0), str(plad))
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
                            leg.count() == 1 and "≥70" in leg_t and "Forecasts from Reask" in leg_t
                            and "exchange price" in leg_t
                            and "NHC cycle" in leg_t and "ET" in leg_t, leg_t[:100])
                    tog = page.locator("#liveStorms div:not(.xhdr) > .emphrow .emphtog button")
                    note_l = page.locator("#liveStorms div:not(.xhdr) > .emphrow .emphnote")
                    note_t = note_l.first.inner_text() if note_l.count() else ""
                    chk.add(f"{scheme} storm ({tag}): the switch offers the two series and the note explains why they differ",
                            tog.count() == 2 and "forward-looking forecast only" in note_t
                            and "entire course of the storm" in note_t, f"buttons={tog.count()} {note_t[:60]}")
                    emph = lambda: page.evaluate("() => [...document.querySelectorAll('#liveStorms svg.scard, #liveStorms svg.lhlserie')].map(s => s.classList.contains('emph-livecyc') ? 'L' : s.classList.contains('emph-exchange') ? 'X' : '?').join('')")
                    chk.add(f"{scheme} storm ({tag}): the exchange's price stands forward by default",
                            emph() and set(emph()) == {"X"}, emph())
                    tog.nth(1).click(); page.wait_for_timeout(120)
                    chk.add(f"{scheme} storm ({tag}): the switch brings LiveCyc forward on every chart at once",
                            emph() and set(emph()) == {"L"} and tog.nth(1).evaluate("b => b.classList.contains('on')"), emph())
                    tog.nth(0).click(); page.wait_for_timeout(120)
                    chk.add(f"{scheme} storm ({tag}): and back", emph() and set(emph()) == {"X"}, emph())
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
                    # ---- the hover: the strikes down the side, both series across
                    # six cycles and the interim are delivered columns; once settled the
                    # final is a delivered column too. The table's headers render in
                    # capitals, so the text is compared lower-cased.
                    bands = page.locator("#liveStorms .scardwrap").first.locator("rect.hband")
                    chk.add(f"{scheme} storm ({tag}): a hover band per delivered column, none for a pending one",
                            bands.count() == n_del + (1 if _final else 0), f"bands={bands.count()}")
                    bands.nth(5).hover(force=True)
                    page.wait_for_timeout(150)
                    t_px = page.locator("#tip").inner_text()
                    low = t_px.lower()
                    chk.add(f"{scheme} storm ({tag}): the hover reads LiveCyc and the exchange's price side by side",
                            "livecyc" in low and "exchange" in low and "≥70 mph" in t_px and "%" in t_px and "35¢" in t_px, t_px[:120])
                    # the price now belongs beside the newest delivery: the last cycle while
                    # cycles are coming, the interim once it has landed
                    if not _interim:
                        chk.add(f"{scheme} storm ({tag}): the newest cycle carries the price now",
                                "now" in low and "43¢" in t_px and "(latest)" in t_px, t_px[:160])
                    else:
                        chk.add(f"{scheme} storm ({tag}): a cycle before the newest delivery carries no price now",
                                "now" not in low, t_px[:160])
                        bands.nth(6).hover(force=True)
                        page.wait_for_timeout(150)
                        t_int = page.locator("#tip").inner_text()
                        chk.add(f"{scheme} storm ({tag}): the interim column is named and priced as it stood",
                                "metryc interim" in t_int.lower() and "44¢" in t_int and "(latest)" in t_int, t_int[:120])
                        chk.add(f"{scheme} storm ({tag}): the newest delivery also carries the price now",
                                "now" in t_int.lower() and "43¢" in t_int, t_int[:160])
                        if _final:
                            chk.add(f"{scheme} storm ({tag}): once settled the hover says how each strike resolved",
                                    "settled" in t_int.lower() and "✓ Yes" in t_int, t_int[:160])
                    # ---- the interim's zero is a figure: Galveston's dashed line runs on
                    # to the interim column at zero rather than stopping at the last
                    # cycle, the hover names the file and prints the zero, and the
                    # vendor table carries the interim's row under the cycle's
                    ga = page.evaluate(r"""() => {
                      const card = document.querySelector('#liveStorms .scardwrap[data-sid="GA"]');
                      if (!card) return null;
                      const svg = card.querySelector('svg.scard');
                      const bands = [...svg.querySelectorAll('rect.hband')];
                      const labels = [...svg.querySelectorAll('text')].map(t => t.textContent);
                      const order = labels.filter(t => /^(next|Interim|final)$/.test(t)).join(' ');
                      const int = bands[6], line = svg.querySelector('path.lcline');
                      if (!int || !line) return { bands: bands.length, line: !!line, order };
                      const colX = +int.getAttribute('x') + (+int.getAttribute('width')) / 2;
                      const d = line.getAttribute('d');
                      const m = d.match(/([\d.]+),([\d.]+)$/) || [0, NaN, NaN];
                      return { endX: +m[1], endY: +m[2], colX, bands: bands.length, order };
                    }""")
                    want = "next Interim final" if not _interim else "Interim final"
                    chk.add(f"{scheme} storm ({tag}): the columns still to come follow the deliveries in order",
                            bool(ga and ga["order"] == want), str(ga and ga["order"]))
                    if _interim:
                        chk.add(f"{scheme} storm ({tag}): the dashed line runs on to the interim column at the zero it published",
                                bool(ga and abs(ga["endX"] - ga["colX"]) < 1.5 and abs(ga["endY"] - 150) < 0.6), str(ga))
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
                        # the vendor publishes a full float; the settled figure reads to a tenth
                        gust = page.evaluate("""() => {
                          const t = [...document.querySelectorAll('#liveStorms svg.scard text')]
                            .map(e => e.textContent).filter(x => / mph$/.test(x));
                          return t;
                        }""")
                        one_dp = lambda g: (g.endswith(" mph") and g.count(".") == 1
                                            and len(g[:-4].split(".")[1]) == 1
                                            and g[:-4].replace(".", "").isdigit())
                        chk.add(f"{scheme} storm ({tag}): the settled gust reads to one decimal place",
                                bool(gust) and all(one_dp(g) for g in gust), str(gust)[:100])
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
                    # ---- the cursor, and scrubbing across deliveries
                    ticks = page.locator("#liveStorms svg.stimeline circle, #liveStorms svg.stimeline rect").count()
                    chk.add(f"{scheme} storm ({tag}): one tick per delivery on the timeline", ticks == n_del, f"ticks={ticks}")
                    strip = page.locator("#liveStorms svg.stimeline").first
                    read = lambda: page.locator("#liveStorms svg.stimeline text").first.text_content()
                    chk.add(f"{scheme} storm ({tag}): the cursor starts at the newest delivery",
                            f"delivery {n_del} of {n_del}" in read() and "(latest)" in read(), read())
                    cx = lambda: page.eval_on_selector("#liveStorms .scardwrap line.scur", "e => e.getAttribute('x1')")
                    was = cx()
                    page.get_by_title("the delivery before this one").first.click()
                    page.wait_for_timeout(120)
                    chk.add(f"{scheme} storm ({tag}): stepping back names the delivery it lands on",
                            f"delivery {n_del - 1} of {n_del}" in read() and "(latest)" not in read(), read())
                    chk.add(f"{scheme} storm ({tag}): the cursor line moves with the step", cx() != was, f"{was} -> {cx()}")
                    # a past delivery is priced as it stood then: the cursor's hollow square
                    # sits at that delivery's recorded price, and the dot on the vendor's line
                    cur = page.evaluate("""() => {
                      const svg = document.querySelector('#liveStorms svg.scard');
                      const g = svg.querySelector('line.scur').nextElementSibling;
                      return { dots: g.querySelectorAll('circle').length, squares: g.querySelectorAll('rect').length };
                    }""")
                    chk.add(f"{scheme} storm ({tag}): the cursor marks both series at the delivery it stands on",
                            cur["dots"] >= 1 and cur["squares"] >= 1, str(cur))
                    box = strip.bounding_box()
                    page.mouse.move(box["x"] + box["width"] * 0.05, box["y"] + box["height"] / 2)
                    page.mouse.down()
                    page.mouse.move(box["x"] + box["width"] * 0.30, box["y"] + box["height"] / 2, steps=6)
                    page.mouse.up()
                    page.wait_for_timeout(120)
                    chk.add(f"{scheme} storm ({tag}): dragging the strip scrubs to another delivery",
                            f"of {n_del}" in read() and f"delivery {n_del} of {n_del}" not in read(), read())
                    strip.press("ArrowLeft"); page.wait_for_timeout(100)
                    mid = read()
                    strip.press("End"); page.wait_for_timeout(120)
                    chk.add(f"{scheme} storm ({tag}): the arrow keys step and End returns to the latest",
                            mid != read() and f"delivery {n_del} of {n_del}" in read(), f"{mid} -> {read()}")
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
                    # ---- the map: a signalled location goes to its card in the storm section
                    dot = page.locator("#basin circle[role='button']")
                    chk.add(f"{scheme} storm ({tag}): the signalled location is clickable on the map",
                            dot.count() >= 1, f"dots={dot.count()}")
                    if dot.count():
                        dot.first.scroll_into_view_if_needed()
                        dot.first.click(force=True)
                        # the scroll is animated, so the card is given a moment to arrive
                        inview = False
                        for _ in range(12):
                            page.wait_for_timeout(200)
                            inview = page.evaluate("""() => {
                              const c = document.querySelector('#liveStorms .scardwrap.flash');
                              if (!c) return false;
                              const r = c.getBoundingClientRect();
                              return r.top >= 0 && r.bottom <= innerHeight;
                            }""")
                            if inview:
                                break
                        flash = page.locator("#liveStorms .scardwrap.flash")
                        chk.add(f"{scheme} storm ({tag}): clicking a location scrolls to its card below and marks it",
                                flash.count() == 1 and inview, f"flash={flash.count()} inview={inview}")
                        chk.add(f"{scheme} storm ({tag}): the card it goes to is that location's",
                                flash.first.get_attribute("data-sid") == "BR" if flash.count() else False, "")
                        chk.add(f"{scheme} storm ({tag}): clicking the dot pins no box on the way",
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
                chk.add(f"{scheme} vendor: the strongest places carry their gust, to a tenth of a mile an hour",
                        any(l.startswith("96.0 Brownsville") for l in vf.get("labels", []))
                        and any(l.startswith("41.0 Galveston") for l in vf.get("labels", []))
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
                # view, and what the owner says is coming but is not listed yet is named
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
                # two storms signal on one location: the dot's box carries both
                # ladders, each under its own name and cycle, hottest first
                dot2 = page.locator("#basin circle[role='button']").first
                dot2.hover(force=True); page.wait_for_timeout(200)
                t_two = page.locator("#tip").inner_text()
                chk.add(f"{scheme} hurricane: a location two storms signal on shows both ladders",
                        "Alpha" in t_two and "Beta" in t_two
                        and t_two.index("Alpha") < t_two.index("Beta")
                        and "50.1%" in t_two and "27.8%" in t_two
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
                dots = page.locator("#basin circle").count()
                chk.add(f"{scheme} hurricane: reference locations drawn", dots >= 100, f"circles={dots}")
                page.locator("#basin circle").nth(40).hover(force=True); page.wait_for_timeout(120)
                t_dot = page.locator("#tip").inner_text()
                chk.add(f"{scheme} hover: reference location names itself and the lane state", "Country" in t_dot and ("probabilities" in t_dot), t_dot[:80])
                # the Pacific view carries the vendor's Hawaii reference locations, which is
                # where a Central Pacific storm's wind contracts sit; they were missing from
                # the vendored registry until the 2026 season listed them
                page.locator("#b2").click(); page.wait_for_timeout(700)
                haw = page.evaluate("""() => {
                  const svg = document.querySelector('#basin');
                  const W = 980, Hh = 600, b0 = -180, b1 = -85, la0 = 0, la1 = 40;
                  const pts = [...svg.querySelectorAll('circle')].map((c, i) => ({ i,
                      lon: b0 + (+c.getAttribute('cx')) * (b1 - b0) / W,
                      lat: la1 - (+c.getAttribute('cy')) * (la1 - la0) / Hh }))
                    .filter(p => p.lon > -161 && p.lon < -154 && p.lat > 18 && p.lat < 23);
                  const hilo = pts.find(p => Math.abs(p.lon + 155.08) < 0.1 && Math.abs(p.lat - 19.72) < 0.1);
                  return { n: pts.length, hilo: hilo ? hilo.i : -1 };
                }""")
                chk.add(f"{scheme} hurricane: the Pacific view draws the Hawaii reference locations",
                        bool(haw and haw["n"] == 4 and haw["hilo"] >= 0), str(haw))
                if haw and haw["hilo"] >= 0:
                    page.locator("#basin circle").nth(haw["hilo"]).hover(force=True); page.wait_for_timeout(120)
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
                    try:
                        bah.first.hover(force=True, timeout=2000); page.wait_for_timeout(80)
                        bah_tip = page.locator("#tip").inner_text()
                    except Exception:
                        pass
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
                chk.add(f"{scheme} hurricane link: the caption says the map is clickable",
                        "clicking a shaded region" in page.locator("#basinCap").inner_text(),
                        page.locator("#basinCap").inner_text()[:80])
                cap_txt = page.locator("#basinCap").inner_text()
                chk.add(f"{scheme} hurricane: the map caption says what a click does",
                        ("opens its probability series below the" in cap_txt
                         and "opens its wind contract" in cap_txt
                         and "00, 06, 12, and 18 UTC" not in cap_txt),
                        cap_txt[-120:])
                page.locator("#ladders .lrow").first.hover(force=True); page.wait_for_timeout(120)
                t_row = page.locator("#tip").inner_text()
                chk.add(f"{scheme} hover: count ladder row shows the book and settlement", "Yes bid" in t_row and "Settles" in t_row, t_row[:80])
                found = ""
                for i in range(min(page.locator("#basin path").count(), 80)):
                    page.locator("#basin path").nth(i).hover(force=True); page.wait_for_timeout(40)
                    t = page.locator("#tip").inner_text()
                    if "Landfall contract" in t: found = t; break
                chk.add(f"{scheme} hover: a shaded landfall region shows its contract", "Yes bid" in found, found[:80])
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
                chk.add(f"{scheme} pacific: the map caption says the regions are shaded",
                        "within 50 miles of its border" in page.locator("#basinCap").inner_text(), page.locator("#basinCap").inner_text()[:80])
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
                sw = page.evaluate("""() => {
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
                }""")
                chk.add(f"{scheme} severe: all three phenomena draw the history series",
                        sw["series"] >= 3, str(sw))
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
                  document.querySelector('#advYday').click();
                  await new Promise(r => setTimeout(r, 400));
                  const yday = dots();
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
                           styles: [...dashes].sort(),
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
                chk.add(f"{scheme} advanced: the tools keep the main chart's line styles",
                        bool(av and "5 4" in av["styles"] and "1 3" in av["styles"]),
                        str(av and av["styles"]))
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
                  return { word, tags: tags.length, sample: tags[0] || null,
                           tomorrowLevels: lv,
                           wantTomorrow: fc.nws.highTomorrow, dontWantToday: fc.nws.highToday };
                }""")
                chk.add(f"{scheme} chart: a cycle label is an hour, a or p, and T or Y",
                        bool(tg and not tg["word"] and tg["tags"] >= 3),
                        str(tg and {"word": tg["word"], "sample": tg["sample"]})[:110])
                chk.add(f"{scheme} chart: the day-ahead board draws the day-ahead levels",
                        bool(tg and tg["wantTomorrow"] in tg["tomorrowLevels"]
                             and (tg["dontWantToday"] == tg["wantTomorrow"]
                                  or tg["dontWantToday"] not in tg["tomorrowLevels"])),
                        str(tg and {k: tg[k] for k in ('wantTomorrow', 'dontWantToday')}))
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
                        sw["ladder"], str(sw))
                chk.add(f"{scheme} severe: the caption names the settlement number",
                        sw["settle"], str(sw))
                chk.add(f"{scheme} severe: no probability or fair value appears on the count blocks",
                        not sw["probs"], "")
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
                grid_heads = page.eval_on_selector_all("#accGrid thead th", "e=>e.map(x=>x.textContent)")
                chk.add(f"{scheme} accuracy: the lows tab redraws the lead curve",
                        page.locator("#accLead").inner_html() != lead_before, "")
                # the scorecard needs no lows tab: it prints both metrics at once
                heads_t = [t.strip() for t in grid_heads]
                grid_bar = page.locator("#accGridBar").inner_text()
                chk.add(f"{scheme} accuracy: the scorecard puts MAE and mean error, high and low, under each of three leads",
                        heads_t[:1] == ["System"] and [t for t in heads_t if t.endswith(" h")] == ["30 h", "18 h", "12 h"]
                        and heads_t.count("MAE") == 3 and heads_t.count("Mean error") == 3
                        and heads_t.count("High") == 6 and heads_t.count("Low") == 6
                        and page.locator("#accGridBar button", has_text="Lows").count() == 0, str(heads_t[:12]))
                chk.add(f"{scheme} accuracy: the scorecard has no CSV or newsletter controls",
                        "CSV" not in grid_bar and "Newsletter" not in grid_bar, grid_bar.replace("\n", " | ")[:100])
                # every deterministic section opens in the own-target frame, and the METAR
                # settle frame moves only the National Weather Service forecast's line
                on_target = [page.locator(f"{bar} button.vbtn.on", has_text="Own target").count() for bar in ("#accLeadBar", "#accGridBar", "#accMapBar")]
                chk.add(f"{scheme} accuracy: the error curve, the scorecard and the map open in the own-target frame",
                        on_target == [1, 1, 1], str(on_target))
                lead_target = page.locator("#accLead").inner_html()
                page.locator("#accLeadBar button", has_text="METAR settle").first.click(); page.wait_for_timeout(600)
                lead_settle = page.locator("#accLead").inner_html()
                page.locator("#accLeadBar button", has_text="Own target").first.click(); page.wait_for_timeout(600)
                chk.add(f"{scheme} accuracy: the METAR settle frame redraws the error curve and Own target restores it",
                        lead_settle != lead_target and page.locator("#accLead").inner_html() == lead_target, "")
                def nws_cells():
                    row = page.locator("#accGrid tr", has_text="National Weather Service").first
                    return row.inner_text() if row.count() else ""
                nws_target = nws_cells()
                page.locator("#accGridBar button", has_text="METAR settle").first.click(); page.wait_for_timeout(500)
                nws_settle = nws_cells()
                lamp_settle = page.locator("#accGrid tr", has_text="Aviation Forecast").first.inner_text()
                page.locator("#accGridBar button", has_text="Own target").first.click(); page.wait_for_timeout(500)
                lamp_target = page.locator("#accGrid tr", has_text="Aviation Forecast").first.inner_text()
                chk.add(f"{scheme} accuracy: the scorecard's National Weather Service row changes with the frame and the Aviation Forecast's does not",
                        bool(nws_target) and nws_target != nws_settle and lamp_target == lamp_settle,
                        f"nws target={nws_target[:60]!r} settle={nws_settle[:60]!r}")
                # the climate-report frame is offered on the lead curve as it is on the map
                lead_metar = page.locator("#accLead").inner_html()
                page.locator("#accLeadBar button", has_text="NWS climate report").first.click(); page.wait_for_timeout(600)
                chk.add(f"{scheme} accuracy: the lead curve redraws in the climate-report frame",
                        page.locator("#accLead").inner_html() != lead_metar, "")
                page.locator("#accLeadBar button", has_text="METAR settle").first.click(); page.wait_for_timeout(400)
                # the map earns a color only where the paired interval clears zero
                fills = page.eval_on_selector_all("#accMap circle", "e=>e.map(x=>x.getAttribute('fill')||'')")
                colored = sum(1 for f in fills if f.startswith("color-mix("))
                chk.add(f"{scheme} accuracy: the map colors at least three stations", colored >= 3, f"colored={colored}")
                page.locator("#accMapBar button", has_text="NWS climate report").first.click(); page.wait_for_timeout(500)
                fills = page.eval_on_selector_all("#accMap circle", "e=>e.map(x=>x.getAttribute('fill')||'')")
                dashed = page.locator("#accMap circle[stroke-dasharray]").count()
                grey = sum(1 for f in fills if f == "var(--line)")
                chk.add(f"{scheme} accuracy: the map greys or hollows a station whose interval covers zero or that the frame excludes",
                        grey + dashed >= 1, f"grey={grey} hollow={dashed}")
                # in the own-target frame the forecast is not scored at Buckley Field, which has no report of its own
                page.locator("#accMapBar button", has_text="Own target").first.click(); page.wait_for_timeout(500)
                map_target_hollow = page.locator("#accMap circle[stroke-dasharray]").count()
                page.locator("#accMapBar button", has_text="METAR settle").first.click(); page.wait_for_timeout(500)
                map_settle_hollow = page.locator("#accMap circle[stroke-dasharray]").count()
                page.locator("#accMapBar button", has_text="Own target").first.click(); page.wait_for_timeout(500)
                chk.add(f"{scheme} accuracy: the own-target map hollows Buckley Field for the National Weather Service forecast",
                        map_target_hollow >= map_settle_hollow + 1, f"target={map_target_hollow} settle={map_settle_hollow}")
                # a row per system in the matched cohort, the market first
                grid_file = json.loads(urllib.request.urlopen(f"{srv.url}/data/snapshots/accuracy/grid.json").read().decode())
                matched = [r["id"] for r in grid_file.get("cohorts", {}).get("own", []) if r.get("id")]
                grid_sys = page.eval_on_selector_all("#accGrid tbody td.acc-grid-sys", "e=>e.map(x=>x.textContent)")
                head = page.locator("table.acc-grid-table thead th").all_inner_texts()
                col = page.locator("table.acc-grid-table tbody td.acc-grid-start").all_inner_texts()
                chk.add(f"{scheme} accuracy: the grid dates every row's own record in every cohort",
                        any(t.strip().lower() == "record since" for t in head) and len(col) >= 6
                        and all(re.match(r"^[A-Z][a-z]{2} \d+", t.strip()) for t in col),
                        f"head={'Record since' in head} rows={len(col)}")
                chk.add(f"{scheme} accuracy: the grid carries a row per system, the market first",
                        len(grid_sys) == len(matched) and len(matched) >= 6 and grid_sys[:1] == ["ForecastEx"],
                        f"rows={len(grid_sys)} file={len(matched)} first={grid_sys[:1]}")
                # every system with a distribution carries a CRPS under both error cells at every lead
                crps_rows = page.eval_on_selector_all("#accGrid tbody tr:not(.acc-grid-grp)", """e => e.map(tr => [
                    tr.querySelector('td.acc-grid-sys').firstChild.nextSibling.textContent.trim(),
                    Array.from(tr.querySelectorAll('.acc-grid-n')).filter(x => x.textContent.startsWith('CRPS')).length])""")
                with_crps = {n: k for n, k in crps_rows if k}
                chk.add(f"{scheme} accuracy: the scorecard gives every probabilistic system a CRPS, and only those",
                        set(with_crps) == {"ForecastEx", "European AI Ensemble Mean", "American Ensemble", "Canadian Ensemble", "German Ensemble"}
                        and all(k == 6 for k in with_crps.values()), str(with_crps))
                # the grid and the systems table share one grouping and one order
                grid_groups = page.eval_on_selector_all("#accGrid tr.acc-grid-grp th", "e=>e.map(x=>x.textContent.trim())")
                table_groups = page.eval_on_selector_all("table.acc-sources tr.grp th", "e => e.map(x => x.firstChild.textContent.trim())")
                table_names = page.eval_on_selector_all("table.acc-sources td.sys a", "e => e.map(x => x.textContent.trim())")
                grid_names = [re.sub(r"reference row$", "", n).strip() for n in grid_sys]
                chk.add(f"{scheme} accuracy: the scorecard groups and orders its rows as the systems table does",
                        grid_groups == [g for g in table_groups if g in grid_groups]
                        and grid_names == [n for n in table_names if n in grid_names],
                        f"grid={grid_groups} names={grid_names[:4]}")

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
                        (("today" in t_md and "Observed high so far" in t_md
                          and "Blend of Models" not in t_md) if opened_today
                         else ("tomorrow" in t_md and "Blend of Models" in t_md
                               and "Observed high so far" not in t_md)),
                        ("today " if opened_today else "tomorrow ") + t_md[:100])
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
                           "analysis-resolution.html"]
            # the owner's own copy, which the style rules do not touch
            OWNER = ("faq.html", "daily-temperature-markets.html")
            bad_title, bad_colon, bad_not = [], [], []
            for path in PAGES_PROSE:
                page.goto(f"{srv.url}/{path}"); page.wait_for_timeout(1100)
                titles = page.eval_on_selector_all(".wrap .secttl, .wrap h2, .wrap h3",
                                                   "e=>e.map(x=>x.textContent.trim())")
                for t in titles:
                    if re.match(r"^(the|a|an)\s", t, re.I):
                        bad_title.append(f"{path}: {t}")
                # the accuracy page's method notes are page copy too, so they take the same rules
                body = page.eval_on_selector_all(".wrap p, .wrap li, .wrap .accnote .rule, .wrap .accnote .nt",
                                                 "e=>e.map(x=>x.textContent)")
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
