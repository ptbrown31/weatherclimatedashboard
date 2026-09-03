"""
build.py — the two build targets from one codebase.

    python3 scripts/build.py                  # both targets, local mode (sample snapshots bundled)
    python3 scripts/build.py --live           # bundle data/snapshots instead of samples
    python3 scripts/build.py --deploy         # no data bundled; pages read DATA_BASE_URL from config

Output: dist/standalone/ (the full site) and dist/embed/ (the single chart).
Each gets a generated config.js that carries the site configuration the
pages read (data base URL, target, market switch, disclosure, cadences).
Nothing in site/ is vendor-specific; the deploy step copies dist/ to the host.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
from html import unescape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pipeline import config    # noqa: E402

SITE = os.path.join(ROOT, "site")
DIST = os.path.join(ROOT, "dist")

SHARED = ["css/site.css", "js/theme.js", "js/common.js", "js/data.js", "js/market.js"]
STANDALONE = ["index.html", "city.html", "hurricane.html", "about.html", "scorecard.html", "climate.html",
              "faq.html", "accuracy.html", "daily-temperature-markets.html",
              "section.html", "category.html", "contract.html", "js/catalogue.js",
              "js/chart-city.js", "js/city-score.js", "js/city-days.js", "js/discussion.js", "js/advanced.js", "js/artmap.js", "js/map.js", "js/hurricane.js", "js/storm.js", "js/scorecard.js", "js/climate.js", "js/forecast.js", "js/accuracy.js",
              "agriculture.html", "js/agriculture.js", "js/panels.js",
              "fossil-fuels.html", "electricity-renewables.html", "js/energy.js",
              "weather.html", "js/weather.js", "js/severe.js",
              "allocator.html", "js/allocator.js",
              "lessons.html", "js/lessons.js", "assets/lessons.json",
              "assets/basemap.json", "assets/world.json", "assets/hurricane-geo.json"]
EMBED = ["embed/index.html", "js/chart-city.js"]


def asset_versions() -> dict:
    """{filename: short content hash} for every file in site/assets, so a page
    can ask for one by a URL that changes exactly when the file does."""
    out = {}
    adir = os.path.join(SITE, "assets")
    if not os.path.isdir(adir):
        return out
    for name in sorted(os.listdir(adir)):
        p = os.path.join(adir, name)
        if os.path.isfile(p):
            with open(p, "rb") as fh:
                out[name] = hashlib.md5(fh.read()).hexdigest()[:8]
    return out


def config_js(cfg: dict, target: str, data_base: str) -> str:
    market = (cfg.get("market_overlay") or {}).get(target, "off")
    wx = {
        "target": target,
        "siteTitle": cfg.get("site_title") or cfg.get("site_name") or "Weather tools",
        "domain": cfg.get("domain", ""),
        "dataBaseUrl": data_base,
        "market": market,
        "marketSource": (cfg.get("market_overlay") or {}).get("source", "placeholder"),
        "feePerSide": (cfg.get("exchange") or {}).get("fee_per_side", 0.005),
        "disclosure": cfg.get("disclosure", ""),
        "cadenceMinutes": cfg.get("cadence_minutes", {}),
        "decode": {"TEMP_SOURCE": None, "INCLUDE_SPECI": None},
        # listings the owner has said are coming but the exchange has not made:
        # the hurricane page draws them as pending on the matching basin view
        "expected": cfg.get("expected_listings") or {},
        # Every projected asset, with a short hash of its own content.
        #
        # The scripts are cached for an hour and the assets for a day, so a
        # deploy that changes both leaves a reader running today's code against
        # yesterday's geometry: the hurricane page did exactly that, losing the
        # Hawaii outline it needed and putting a Pacific contract on the
        # Atlantic board. A page asks for an asset by this stamp, so the URL
        # changes when the content does and never otherwise.
        "assetV": asset_versions(),
    }
    # the category hierarchy travels in config.js so the header can be drawn on
    # the first paint rather than after a fetch: it is small, it changes only
    # when the registry is rebuilt, and every page needs it
    reg = cfg.get("contracts") or {}
    if reg.get("categories"):
        slug_of = {c["l2"]: c["slug"] for c in reg["categories"]}
        wx["nav"] = {"l1": reg.get("l1") or [],
                     "categories": [{k: c[k] for k in ("l1", "l1slug", "l2", "slug", "page", "n", "active")}
                                    for c in reg["categories"]],
                     # product -> category slug, so a contract page knows which
                     # branch it belongs to before any data has been fetched
                     "product": {p["id"]: slug_of.get(p["l2"], "") for p in (reg.get("products") or [])},
                     # the regulatory document that governs each contract, as the
                     # document's own name rather than a full url, so 213 of them
                     # cost a couple of kilobytes. Which document governs which
                     # product is worked out in scripts/build_contracts.py from
                     # each document's stated product code.
                     "termsBase": "https://data.forecastex.com/regulatory/",
                     "terms": {p["id"]: (p["terms"].rsplit("/", 1)[-1].replace("TermsandConditions.pdf", ""))
                               for p in (reg.get("products") or []) if p.get("terms")}}
    return "/* generated by scripts/build.py; edit config/site.json instead */\nwindow.WX = " + json.dumps(wx, indent=1) + ";\n"


def slug(city: str, icao: str) -> str:
    """A station page's own name, as it appears in the address bar.

    The identifier a reader recognises is the city, and the one the contract is
    written against is the airport, so the name carries both. The airport code
    also makes the name unique, which matters where an exchange lists two fields
    in one city. site/js/common.js builds the same name for its links and
    tests/test_build.py holds both to the same table.
    """
    return re.sub(r"[^a-z0-9]+", "-", city.lower()).strip("-") + "-" + icao.lower()


def us_station(icao: str) -> bool:
    """Whether the locator has a picture of this station.

    The imagery is USGS, a United States government product covering the United
    States only, which the station-location caption says on the page itself. A
    station abroad gets no picture, so its shared link carries a text card.
    """
    return icao.startswith("K") or icao == "PHNL"


def meta_of(html: str, name: str) -> str:
    m = re.search(r'<meta name="%s" content="([^"]*)">' % re.escape(name), html)
    return m.group(1) if m else ""


def title_of(html: str) -> str:
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    return " ".join(m.group(1).split()) if m else ""


def strip_tags(s: str) -> str:
    """Readable text out of a fragment of the page's own markup."""
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", s)).split())


def jsonld(obj: dict) -> str:
    """One structured-data block. The only sequence that can close a script
    element early is escaped, which is what embedding JSON in one asks for."""
    return ('<script type="application/ld+json">%s</script>'
            % json.dumps(obj, separators=(",", ":")).replace("</", "<\\/"))


def faq_questions(html: str) -> list:
    """The page's own questions, in the shape a search engine reads them.

    Taken from the headings rather than from a list kept beside them, so a
    question is in the structured data by being on the page and one that goes
    cannot linger after it. A heading that asks nothing is not a question, and
    a page carrying one or two is not a list of them, so both are passed over.
    """
    items = []
    for part in re.split(r"<h2[^>]*>", html)[1:]:
        q, _, rest = part.partition("</h2>")
        q = strip_tags(q)
        if not q.endswith("?"):
            continue
        answer = strip_tags(re.split(r"<h2[^>]*>", rest)[0])
        if not answer:
            continue
        items.append({"@type": "Question", "name": q,
                      "acceptedAnswer": {"@type": "Answer", "text": answer[:1500]}})
    return items if len(items) >= 3 else []


def head_meta(html: str, rel: str, cfg: dict, image: str = "") -> str:
    """Give a page the identity a search result and a shared link need.

    A page carries its own title and description, written by hand next to the
    content they describe. Everything below is the same two facts repeated in
    the shapes the crawlers and the link unfurlers read, so a new page gets all
    of it by having a title and a description and nothing else.

    Without a domain in config there is nothing to build an absolute address
    from, so an offline build gets the description and stops there, which is
    what a local checkout needs and all it can honestly say.
    """
    desc, title = meta_of(html, "description"), title_of(html)
    site = cfg.get("site_title") or "Weather tools"
    domain = (cfg.get("domain") or "").strip().rstrip("/")
    base = "https://" + domain if domain else ""
    def esc(s):
        for a, b in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ('"', "&quot;")):
            s = s.replace(a, b)
        return s
    tags = []
    if base:
        url = base + "/" + rel
        tags.append('<link rel="canonical" href="%s">' % esc(url))
        tags.append('<meta property="og:url" content="%s">' % esc(url))
    tags += ['<meta property="og:type" content="website">',
             '<meta property="og:site_name" content="%s">' % esc(site),
             '<meta property="og:title" content="%s">' % esc(title)]
    if desc:
        tags.append('<meta property="og:description" content="%s">' % esc(desc))
    if image and base:
        tags += ['<meta property="og:image" content="%s">' % esc(base + "/" + image.lstrip("/")),
                 '<meta name="twitter:card" content="summary_large_image">']
    else:
        tags.append('<meta name="twitter:card" content="summary">')
    tags.append('<meta name="twitter:title" content="%s">' % esc(title))
    if desc:
        tags.append('<meta name="twitter:description" content="%s">' % esc(desc))
    # the same two facts once more, in the vocabulary a search engine parses
    # rather than reads. Without a domain there is no identity to assert and
    # this is skipped, as the link tags above are
    if base:
        pub = {"@type": "Organization", "@id": base + "/#publisher", "name": site, "url": base + "/"}
        site_node = {"@type": "WebSite", "@id": base + "/#site", "name": site, "url": base + "/",
                     "publisher": {"@id": base + "/#publisher"}}
        page = {"@type": "WebPage", "@id": url + "#page", "url": url, "name": title,
                "isPartOf": {"@id": base + "/#site"}, "publisher": {"@id": base + "/#publisher"}}
        if desc:
            page["description"] = desc
        graph = [pub, site_node, page]
        questions = faq_questions(html)
        if questions:
            graph.append({"@type": "FAQPage", "@id": url + "#faq", "url": url, "mainEntity": questions})
        tags.append(jsonld({"@context": "https://schema.org", "@graph": graph}))
    return html.replace("</head>", "\n".join(tags) + "\n</head>", 1)


def station_intro(c: dict) -> str:
    """The station's own facts, written into its page as text.

    Everything else on a station page is drawn by script out of the snapshots,
    so until that runs the page says almost nothing about the station it is
    for. This is what a reader arriving from a search wants first and the only
    part a crawler can read without running anything.
    """
    icao, city = c["station"], c["city"]
    unit = "Celsius" if c.get("unit") == "C" else "Fahrenheit"
    drawn = ("The chart above carries the National Weather Service forecast, the National Blend of Models "
             "and the LAMP guidance against the readings the station has published so far, with the "
             "exchange's prices for the same day on the same scale."
             if us_station(icao) else
             "The chart above carries the readings the station has published so far for the day, with the "
             "exchange's prices for the same day on the same scale.")
    return ('<h2>%s weather and the daily temperature contracts</h2>\n'
            '<p>%s (%s) is the weather station the %s daily temperature contracts settle on, and everything '
            'on this page is drawn against its record. ForecastEx lists a high contract and a low contract '
            'for each day. Each one asks whether the day’s high, or the day’s low, will finish above or '
            'below a stated whole-degree threshold, and it settles on the station’s own METAR record over '
            'the local calendar day, in whole degrees %s. A high contract pays Yes only when the settled '
            'value is strictly above its threshold, and a low contract only when it is strictly below. '
            '%s</p>\n'
            '<p>How a ladder of strikes is put together and what settles it is set out on the '
            '<a href="daily-temperature-markets.html">daily temperature markets page</a>. How each forecast '
            'tool has scored against the market at this station and the others is on the '
            '<a href="accuracy.html">accuracy page</a>.</p>' % (city, city, icao, city, unit, drawn))


def station_index(cities: list) -> str:
    """Every station's page linked by name.

    A station is reached by its dot on the map, which serves a reader who is
    already looking at the map and nobody else. This is the same set of pages
    as a list of names, for a reader who knows which city they want and for
    anything that cannot read a map at all.
    """
    def links(rows):
        return " · ".join('<a href="%s">%s weather (%s)</a>'
                          % (slug(c["city"], c["station"]) + ".html", c["city"], c["station"])
                          for c in sorted(rows, key=lambda r: r["city"]))
    us = [c for c in cities if us_station(c["station"])]
    intl = [c for c in cities if not us_station(c["station"])]
    return ('<h2>Stations on this board</h2>\n<p>%s</p>\n'
            '<h2>Stations abroad</h2>\n<p>%s</p>' % (links(us), links(intl)))


def station_page(tpl: str, c: dict, cfg: dict) -> str:
    """One station's own page, from the shared city template.

    Every station shared the address of every other one, so a search engine saw
    thirty-seven copies of a page titled "City chart" whose heading was a
    non-breaking space, and a reader who shared a link sent an address that said
    nothing about where it went. The chart is the same chart; what is added here
    is a name, a description, and a heading that is already in the file before
    any script runs.
    """
    icao, city = c["station"], c["city"]
    unit = "°C" if c.get("unit") == "C" else "°F"
    # the title carries the two things a search for this station can be after,
    # the city's weather and the contracts written on it, and the station code
    # that names which weather is meant
    title = "%s weather and temperature prediction market (%s)" % (city, icao)
    desc = ("%s weather at %s, with %sthe station's own observations and ForecastEx prices for the daily "
            "high and low temperature contracts that settle on its record, in %s."
            % (city, icao, "the National Weather Service forecast, " if us_station(icao) else "", unit))
    out = re.sub(r"<title>.*?</title>", "<title>%s</title>" % title, tpl, count=1, flags=re.S)
    out = re.sub(r'<meta name="description" content="[^"]*">',
                 '<meta name="description" content="%s">' % desc, out, count=1)
    # the heading a crawler reads; the chart replaces it with the dated one.
    # By pattern, not exact string, so a styling change on the h1 cannot
    # silently stop the heading being injected
    out, n = re.subn(r'(<h1 id="cityTitle"[^>]*>)[^<]*(</h1>)',
                     r'\g<1>%s (%s)\g<2>' % (city, icao), out, count=1)
    if not n:
        raise ValueError("city.html has no cityTitle h1 to fill")
    # the station's own paragraph, in the page rather than in a snapshot
    out, n = re.subn(r'(<div class="prose" id="cityAbout"[^>]*>)(</div>)',
                     lambda m: m.group(1) + station_intro(c) + m.group(2), out, count=1)
    if not n:
        raise ValueError("city.html has no cityAbout block to fill")
    # which station this page is, for a page that has no query string to read
    out = out.replace("<script src=\"config.js\">",
                      "<script>window.WX_STATION = %s;</script>\n<script src=\"config.js\">"
                      % json.dumps(icao), 1)
    img = "data/snapshots/locator/%s_region.png" % icao if us_station(icao) else ""
    return head_meta(out, slug(city, icao) + ".html", cfg, img)


def copy(rel: str, dst_root: str, flatten_embed: bool = False) -> bool:
    src = os.path.join(SITE, rel)
    if not os.path.exists(src):
        return False
    out_rel = "index.html" if (flatten_embed and rel == "embed/index.html") else rel
    dst = os.path.join(dst_root, out_rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return True


def stamp_assets(out: str) -> int:
    """Point every page at its scripts and stylesheet by content.

    The pages are served with a short max-age and the code with a long one, so
    a returning visitor gets today's HTML running last hour's JavaScript, and a
    CDN invalidation cannot help: the stale copy is in their browser. Appending
    a content hash to each reference means a changed file is a changed URL, so
    the short-lived page carries the new code with it, while a file that did not
    change keeps its cached copy and its long max-age.

    Only what the pages reference by a plain relative path is stamped. Anything
    fetched at runtime is a snapshot, and those carry their own freshness.
    """
    digests = {}
    for base, _, names in os.walk(out):
        for n in names:
            if not (n.endswith(".js") or n.endswith(".css")):
                continue
            full = os.path.join(base, n)
            rel = os.path.relpath(full, out).replace(os.sep, "/")
            with open(full, "rb") as fh:
                digests[rel] = hashlib.sha256(fh.read()).hexdigest()[:8]
    n_rewritten = 0
    for base, _, names in os.walk(out):
        for n in names:
            if not n.endswith(".html"):
                continue
            full = os.path.join(base, n)
            with open(full) as fh:
                html = fh.read()
            before = html
            for rel, dig in digests.items():
                for attr in ('src="', 'href="'):
                    html = html.replace(attr + rel + '"', attr + rel + "?v=" + dig + '"')
            if html != before:
                with open(full, "w") as fh:
                    fh.write(html)
                n_rewritten += 1
    return n_rewritten


def noindex(out: str, name: str) -> bool:
    """Whether a page asks search engines to leave it alone."""
    with open(os.path.join(out, name)) as fh:
        return 'name="robots" content="noindex"' in fh.read(4096)


def identity(out: str, cfg: dict) -> tuple:
    """Write the station pages, give every page its metadata, and index the lot.

    Runs before the assets are stamped, so the generated pages get the same
    content-hashed references every hand-written one does.
    """
    tpl_path = os.path.join(out, "city.html")
    cities = []
    if os.path.exists(tpl_path):
        with open(tpl_path) as fh:
            tpl = fh.read()
        with open(os.path.join(ROOT, "config", "cities.json")) as fh:
            cities = json.load(fh)
        for c in cities:
            with open(os.path.join(out, slug(c["city"], c["station"]) + ".html"), "w") as fh:
                fh.write(station_page(tpl, c, cfg))
        # and the board links to every one of them by name, from the same list
        # the pages were written from, so the two cannot disagree
        idx = os.path.join(out, "index.html")
        with open(idx) as fh:
            board = fh.read()
        board, n_idx = re.subn(r'(<div class="prose" id="stationIndex"[^>]*>)(</div>)',
                               lambda m: m.group(1) + station_index(cities) + m.group(2), board, count=1)
        if not n_idx:
            raise ValueError("index.html has no stationIndex block to fill")
        with open(idx, "w") as fh:
            fh.write(board)
    n = 0
    for name in sorted(os.listdir(out)):
        if not name.endswith(".html") or "og:title" in open(os.path.join(out, name)).read():
            continue
        full = os.path.join(out, name)
        with open(full) as fh:
            html = fh.read()
        with open(full, "w") as fh:
            fh.write(head_meta(html, name, cfg))
        n += 1
    domain = (cfg.get("domain") or "").strip().rstrip("/")
    if domain:
        base = "https://" + domain
        day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
        # city.html carries no station of its own, so it is left out of the index
        # in favour of the thirty-seven pages that each name one. A page that
        # asks not to be indexed is left out too, which is how an unfinished
        # page stays unfinished in public rather than being handed to a crawler
        urls = sorted(n2 for n2 in os.listdir(out)
                      if n2.endswith(".html") and n2 != "city.html" and not noindex(out, n2))
        body = "".join('<url><loc>%s/%s</loc><lastmod>%s</lastmod></url>\n'
                       % (base, "" if u == "index.html" else u, day) for u in urls)
        with open(os.path.join(out, "sitemap.xml"), "w") as fh:
            fh.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                     '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + body + "</urlset>\n")
        # nothing is disallowed. The snapshots under /data are what the pages
        # draw from, and a link unfurler has to fetch the preview image there
        with open(os.path.join(out, "robots.txt"), "w") as fh:
            fh.write("User-agent: *\nAllow: /\nSitemap: %s/sitemap.xml\n" % base)
    return n, len(cities)


def build(target: str, cfg: dict, data_mode: str) -> dict:
    out = os.path.join(DIST, target)
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    files = SHARED + (STANDALONE if target == "standalone" else EMBED)
    missing = [f for f in files if not copy(f, out, flatten_embed=True)]
    data_base = cfg.get("data_base_url", "/data") if data_mode == "deploy" else "data"
    with open(os.path.join(out, "config.js"), "w") as fh:
        fh.write(config_js(cfg, target, data_base))
    pages = stations = 0
    if target == "standalone":
        pages, stations = identity(out, cfg)
    stamped = stamp_assets(out)
    bundled = 0
    if data_mode != "deploy":
        src = os.path.join(ROOT, "data" if data_mode == "live" else "samples", "snapshots")
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(out, "data", "snapshots"))
            bundled = sum(len(f) for _, _, f in os.walk(src))
    return {"target": target, "files": len(files) - len(missing), "missing": missing, "snapshots": bundled,
            "dataBaseUrl": data_base, "stamped": stamped, "pages": pages, "stations": stations}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="bundle data/snapshots instead of samples/")
    ap.add_argument("--deploy", action="store_true", help="bundle no data; pages read data_base_url from config")
    ap.add_argument("--target", choices=["standalone", "embed"], help="build one target only")
    args = ap.parse_args(argv)
    cfg = config.load()
    mode = "deploy" if args.deploy else ("live" if args.live else "samples")
    for target in ([args.target] if args.target else ["standalone", "embed"]):
        r = build(target, cfg, mode)
        print(f"{target}: {r['files']} files, {r['snapshots']} snapshots bundled, data at {r['dataBaseUrl']!r}"
              + (f", {r['stations']} station pages" if r["stations"] else "")
              + (f", missing {r['missing']}" if r["missing"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
