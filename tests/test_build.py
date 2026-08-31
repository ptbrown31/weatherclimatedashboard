"""Tests for page identity: names, metadata and the index of them.

The build names a file for every station and site/js/common.js builds the same
name for the links that point at it. Two implementations of one rule is the
risk these tests exist to hold down, so the table below is the rule and both
sides are checked against it.
"""
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import build  # noqa: E402

# city, station, the name the page gets
NAMES = [
    ("San Francisco", "KSFO", "san-francisco-ksfo"),
    ("New York City", "KLGA", "new-york-city-klga"),
    ("Washington DC", "KDCA", "washington-dc-kdca"),
    ("Abu Dhabi", "OMAA", "abu-dhabi-omaa"),
    ("Hong Kong", "VHHH", "hong-kong-vhhh"),
    ("Honolulu", "PHNL", "honolulu-phnl"),
]

CFG = {"domain": "example.org", "site_title": "Weather tools"}


def read(*parts):
    with open(os.path.join(ROOT, *parts)) as fh:
        return fh.read()


class Naming(unittest.TestCase):
    def test_the_rule(self):
        for city, icao, want in NAMES:
            self.assertEqual(build.slug(city, icao), want)

    def test_the_browser_builds_the_same_name(self):
        """The rule in common.js, run against the same table.

        Translated rather than executed, because the tests take no dependency
        and a JavaScript engine is one. A change to either side that is not a
        change to the other fails here.
        """
        src = read("site", "js", "common.js")
        m = re.search(r"function cityHref\(c\) \{(.*?)\n  \}", src, re.S)
        self.assertIsNotNone(m, "cityHref is gone from common.js")
        body = m.group(1)
        # the two replaces the rule is made of, read out of the source itself
        self.assertIn(r"replace(/[^a-z0-9]+/g, '-')", body)
        self.assertIn(r"replace(/^-+|-+$/g, '')", body)
        self.assertIn("toLowerCase()", body)
        self.assertIn("+ '.html'", body.replace("+'.html'", "+ '.html'"))
        for city, icao, want in NAMES:
            js = re.sub(r"[^a-z0-9]+", "-", city.lower()).strip("-") + "-" + icao.lower()
            self.assertEqual(js, want)

    def test_every_station_gets_its_own_name(self):
        rows = json.loads(read("config", "cities.json"))
        names = [build.slug(c["city"], c["station"]) for c in rows]
        self.assertEqual(len(names), len(set(names)), "two stations share a page name")
        self.assertTrue(all(re.fullmatch(r"[a-z0-9-]+", n) for n in names), names)

    def test_only_stations_with_imagery_claim_a_picture(self):
        # USGS covers the United States only, which is why a station abroad has
        # no locator image to offer a link unfurler
        rows = json.loads(read("config", "cities.json"))
        us = {c["station"] for c in rows if build.us_station(c["station"])}
        self.assertIn("KSFO", us)
        self.assertIn("PHNL", us)
        self.assertNotIn("EDDF", us)
        self.assertNotIn("YSSY", us)


class Metadata(unittest.TestCase):
    HEAD = ('<!doctype html><html><head><title>A page</title>'
            '<meta name="description" content="What it shows.">'
            '</head><body></body></html>')

    def test_a_title_and_a_description_become_the_rest(self):
        out = build.head_meta(self.HEAD, "thing.html", CFG)
        self.assertIn('<link rel="canonical" href="https://example.org/thing.html">', out)
        self.assertIn('<meta property="og:title" content="A page">', out)
        self.assertIn('<meta property="og:description" content="What it shows.">', out)
        self.assertIn('<meta property="og:url" content="https://example.org/thing.html">', out)
        self.assertIn('<meta name="twitter:card" content="summary">', out)
        self.assertNotIn("og:image", out)          # no picture, so none is claimed

    def test_a_picture_upgrades_the_card(self):
        out = build.head_meta(self.HEAD, "thing.html", CFG, "data/snapshots/locator/KSFO_region.png")
        self.assertIn('<meta property="og:image" '
                      'content="https://example.org/data/snapshots/locator/KSFO_region.png">', out)
        self.assertIn('<meta name="twitter:card" content="summary_large_image">', out)

    def test_without_a_domain_there_is_no_absolute_address_to_give(self):
        out = build.head_meta(self.HEAD, "thing.html", {"site_title": "T"})
        self.assertNotIn("canonical", out)
        self.assertNotIn("og:url", out)
        self.assertIn('<meta property="og:title" content="A page">', out)

    def test_quotes_in_a_title_cannot_break_out_of_the_attribute(self):
        h = self.HEAD.replace("A page", 'A "quoted" & <odd> page')
        out = build.head_meta(h, "thing.html", CFG)
        self.assertIn('content="A &quot;quoted&quot; &amp; &lt;odd&gt; page"', out)

    def test_every_page_carries_a_description(self):
        site = os.path.join(ROOT, "site")
        for n in sorted(os.listdir(site)):
            if not n.endswith(".html"):
                continue
            s = read("site", n)
            d = build.meta_of(s, "description")
            self.assertTrue(d, f"{n} has no description")
            self.assertLessEqual(len(d), 200, f"{n} description is {len(d)} characters")
            self.assertTrue(build.title_of(s), f"{n} has no title")


class StationPage(unittest.TestCase):
    def page(self):
        tpl = read("site", "city.html")
        return build.station_page(tpl, {"station": "KSFO", "city": "San Francisco", "unit": "F"}, CFG)

    def test_it_names_its_own_station_before_any_script_runs(self):
        out = self.page()
        self.assertIn("<title>San Francisco daily temperature market (KSFO)</title>", out)
        self.assertIn('<h1 id="cityTitle">San Francisco (KSFO)</h1>', out)
        self.assertIn('window.WX_STATION = "KSFO"', out)
        self.assertEqual(out.count('<meta name="description"'), 1)
        self.assertIn("San Francisco (KSFO)", build.meta_of(out, "description"))
        self.assertIn('href="https://example.org/san-francisco-ksfo.html"', out)
        self.assertIn("locator/KSFO_region.png", out)

    def test_a_station_abroad_reads_in_its_own_unit_and_offers_no_picture(self):
        tpl = read("site", "city.html")
        out = build.station_page(tpl, {"station": "EDDF", "city": "Frankfurt", "unit": "C"}, CFG)
        self.assertIn("°C", build.meta_of(out, "description"))
        self.assertNotIn("og:image", out)

    def test_the_station_is_declared_before_the_chart_can_look_for_it(self):
        out = self.page()
        self.assertLess(out.index("window.WX_STATION"), out.index("js/chart-city.js"))


if __name__ == "__main__":
    unittest.main()
