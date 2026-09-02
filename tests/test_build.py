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


class StationIndex(unittest.TestCase):
    """The board links every station by name, for readers and crawlers that
    cannot use the map."""

    ROWS = [{"station": "KSFO", "city": "San Francisco", "unit": "F"},
            {"station": "KATL", "city": "Atlanta", "unit": "F"},
            {"station": "YSSY", "city": "Sydney", "unit": "C"}]

    def test_every_station_is_linked_by_the_name_it_would_be_looked_up_under(self):
        out = build.station_index(self.ROWS)
        self.assertIn('<a href="san-francisco-ksfo.html">San Francisco weather (KSFO)</a>', out)
        self.assertIn('<a href="atlanta-katl.html">Atlanta weather (KATL)</a>', out)
        self.assertLess(out.index("Atlanta"), out.index("San Francisco"))      # by city name

    def test_stations_abroad_are_listed_apart_from_the_board(self):
        out = build.station_index(self.ROWS)
        self.assertLess(out.index("Stations on this board"), out.index("Stations abroad"))
        self.assertLess(out.index("San Francisco"), out.index("Sydney"))
        self.assertNotIn("Sydney", out[:out.index("Stations abroad")])

    def test_the_board_has_somewhere_to_put_it(self):
        self.assertIn('<div class="prose" id="stationIndex"', read("site", "index.html"))


class StructuredData(unittest.TestCase):
    """The same facts once more, in the vocabulary a search engine parses."""

    HEAD = ('<!doctype html><html><head><title>A page</title>'
            '<meta name="description" content="What it shows.">'
            '</head><body>%s</body></html>')

    def graph(self, out):
        m = re.search(r'<script type="application/ld\+json">(.*?)</script>', out, re.S)
        self.assertTrue(m, "no structured data")
        return json.loads(m.group(1))["@graph"]

    def test_a_page_declares_itself_its_site_and_its_publisher(self):
        g = self.graph(build.head_meta(self.HEAD % "", "thing.html", CFG))
        by_type = {x["@type"]: x for x in g}
        self.assertEqual(sorted(by_type), ["Organization", "WebPage", "WebSite"])
        self.assertEqual(by_type["WebPage"]["url"], "https://example.org/thing.html")
        self.assertEqual(by_type["WebPage"]["name"], "A page")
        self.assertEqual(by_type["WebPage"]["description"], "What it shows.")
        self.assertEqual(by_type["WebPage"]["isPartOf"]["@id"], by_type["WebSite"]["@id"])
        self.assertEqual(by_type["WebSite"]["publisher"]["@id"], by_type["Organization"]["@id"])

    def test_without_a_domain_nothing_is_asserted(self):
        out = build.head_meta(self.HEAD % "", "thing.html", {"site_title": "T"})
        self.assertNotIn("application/ld+json", out)

    def test_a_closing_script_tag_in_the_content_cannot_end_the_block(self):
        h = self.HEAD % ""
        h = h.replace("A page", "A </script> page")
        out = build.head_meta(h, "thing.html", CFG)
        self.assertIn("<\\/script>", out)
        self.assertNotIn("A </script> page</script>", out)

    def test_questions_on_the_page_become_the_questions_in_the_data(self):
        body = "".join("<h2>Question %d?</h2><p>Answer %d.</p>" % (i, i) for i in range(1, 4))
        g = self.graph(build.head_meta(self.HEAD % body, "faq.html", CFG))
        faq = next(x for x in g if x["@type"] == "FAQPage")
        self.assertEqual([q["name"] for q in faq["mainEntity"]],
                         ["Question 1?", "Question 2?", "Question 3?"])
        self.assertEqual(faq["mainEntity"][0]["acceptedAnswer"]["text"], "Answer 1.")

    def test_headings_that_ask_nothing_are_not_questions(self):
        body = "<h2>Question 1?</h2><p>A.</p><h2>Data sources</h2><p>B.</p><h2>Question 2?</h2><p>C.</p>"
        self.assertEqual([q["name"] for q in build.faq_questions(body)], [])   # two questions is not a list
        body += "<h2>Question 3?</h2><p>D.</p>"
        got = build.faq_questions(body)
        self.assertEqual([q["name"] for q in got], ["Question 1?", "Question 2?", "Question 3?"])

    def test_the_site_faq_carries_its_questions(self):
        out = build.head_meta(read("site", "faq.html"), "faq.html", CFG)
        faq = next(x for x in self.graph(out) if x["@type"] == "FAQPage")
        self.assertGreaterEqual(len(faq["mainEntity"]), 4)
        self.assertTrue(all(q["name"].endswith("?") for q in faq["mainEntity"]))
        self.assertTrue(all(q["acceptedAnswer"]["text"] for q in faq["mainEntity"]))

    def test_a_board_page_full_of_statements_is_not_an_faq(self):
        out = build.head_meta(read("site", "index.html"), "index.html", CFG)
        self.assertNotIn("FAQPage", out)


class Indexing(unittest.TestCase):
    def test_a_page_that_asks_not_to_be_indexed_says_so(self):
        # the lessons page is unfinished and deliberately kept out of the index
        s = read("site", "lessons.html")
        self.assertIn('<meta name="robots" content="noindex">', s)
        self.assertIn("Under construction", s)

    def test_nothing_links_to_it_from_the_navigation(self):
        nav = read("site", "js", "common.js")
        ref = re.search(r"const REF = \[(.*?)\];", nav, re.S).group(1)
        self.assertNotIn("lessons.html", ref)


class StationPage(unittest.TestCase):
    def page(self):
        tpl = read("site", "city.html")
        return build.station_page(tpl, {"station": "KSFO", "city": "San Francisco", "unit": "F"}, CFG)

    def test_it_names_its_own_station_before_any_script_runs(self):
        out = self.page()
        self.assertIn("<title>San Francisco weather and temperature prediction market (KSFO)</title>", out)
        self.assertTrue(re.search(r'<h1 id="cityTitle"[^>]*>San Francisco \(KSFO\)</h1>', out))
        self.assertIn('window.WX_STATION = "KSFO"', out)
        self.assertEqual(out.count('<meta name="description"'), 1)
        self.assertIn("San Francisco weather at KSFO", build.meta_of(out, "description"))
        self.assertIn('href="https://example.org/san-francisco-ksfo.html"', out)
        self.assertIn("locator/KSFO_region.png", out)

    def test_a_station_abroad_reads_in_its_own_unit_and_offers_no_picture(self):
        tpl = read("site", "city.html")
        out = build.station_page(tpl, {"station": "EDDF", "city": "Frankfurt", "unit": "C"}, CFG)
        self.assertIn("°C", build.meta_of(out, "description"))
        self.assertNotIn("og:image", out)

    def test_the_page_says_what_the_station_is_before_the_chart_is_drawn(self):
        # everything else here is drawn from the snapshots by script, so this
        # paragraph is all a crawler and a first-time reader have
        out = build.strip_tags(self.page())
        self.assertIn("San Francisco weather and the daily temperature contracts", out)
        self.assertIn("KSFO) is the weather station the San Francisco daily temperature contracts settle on", out)
        self.assertIn("whole degrees Fahrenheit", out)
        self.assertIn("National Weather Service forecast", out)      # a US station has forecast tools drawn

    def test_a_station_abroad_reads_in_celsius_and_claims_no_forecast_tools(self):
        tpl = read("site", "city.html")
        out = build.strip_tags(build.station_page(tpl, {"station": "YSSY", "city": "Sydney", "unit": "C"}, CFG))
        self.assertIn("Sydney weather and the daily temperature contracts", out)
        self.assertIn("whole degrees Celsius", out)
        self.assertNotIn("National Blend of Models", out)

    def test_the_station_is_declared_before_the_chart_can_look_for_it(self):
        out = self.page()
        self.assertLess(out.index("window.WX_STATION"), out.index("js/chart-city.js"))


if __name__ == "__main__":
    unittest.main()
