"""The exchange's category aliases: /<category id>.html for every category in
the Environmental branch of its tree, each a page that sends the reader on to
the page config/category_aliases.json names. The mapping is checked against
what the build produces and against the hurricane page's location registry, so
a renamed page or a retired location shows up here rather than as a link that
quietly lands on the home page."""
import json
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import build  # noqa: E402

CFG = {"domain": "example.org"}


def pages_built():
    """The pages a standalone build carries: the hand-written ones and one per station."""
    names = {n for n in os.listdir(os.path.join(ROOT, "site")) if n.endswith(".html")}
    with open(os.path.join(ROOT, "config", "cities.json")) as fh:
        names |= {build.slug(c["city"], c["station"]) + ".html" for c in json.load(fh)}
    return names


class Mapping(unittest.TestCase):
    def setUp(self):
        self.aliases = build.load_aliases()

    def test_every_id_is_a_category_id_and_appears_once(self):
        ids = [a["id"] for a in self.aliases]
        self.assertTrue(all(re.fullmatch(r"g[0-9]+", i) for i in ids), ids)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("g137800", ids)                      # the Environmental branch itself

    def test_every_destination_is_a_page_the_site_builds(self):
        have = pages_built()
        bad = [(a["id"], a["to"]) for a in self.aliases if a["to"].split("?")[0].split("#")[0] not in have]
        self.assertEqual(bad, [])

    def test_a_hurricane_location_opens_its_own_ocean_and_its_own_location(self):
        with open(os.path.join(ROOT, "site", "assets", "hurricane-geo.json")) as fh:
            geo = {L["id"]: L for L in json.load(fh)["locations"]}
        n = 0
        for a in self.aliases:
            m = re.fullmatch(r"hurricane\.html\?basin=(AL|EP)&loc=([A-Z]{2})", a["to"])
            if not m:
                continue
            n += 1
            L = geo.get(m.group(2))
            self.assertIsNotNone(L, a)
            # Hawaii is drawn on the Pacific view and every other location on the Atlantic one
            self.assertEqual(m.group(1), "EP" if L["state"] == "HI" else "AL", a)
            # the category is named for the place the location is
            self.assertEqual(a["category"].split(" / ")[-1].split(",")[0].lower(),
                             L["name"].lower().replace("í", "i"), a)
        self.assertGreaterEqual(n, 28)


class Pages(unittest.TestCase):
    def test_a_page_moves_the_reader_on_and_carries_the_link_s_parameters(self):
        html = build.alias_page("hurricane.html?basin=AL&loc=PC", "Major Weather Events / Panama City Beach, FL",
                                "https://example.org")
        self.assertIn('<meta name="robots" content="noindex">', html)
        self.assertIn('<link rel="canonical" href="https://example.org/hurricane.html">', html)
        self.assertIn('new URL("hurricane.html?basin=AL&loc=PC", location.href)', html)
        self.assertIn("if (!to.searchParams.has(k))", html)            # the destination's own terms win
        self.assertIn('content="0; url=hurricane.html?basin=AL&amp;loc=PC"', html)
        self.assertLess(html.index("location.replace"), html.index('http-equiv="refresh"'))

    def test_the_home_page_is_canonical_at_the_root(self):
        self.assertIn('href="https://example.org/"', build.alias_page("index.html", "Environmental", "https://example.org"))

    def test_nothing_in_a_destination_can_close_the_script(self):
        html = build.alias_page("x.html?</script><script>", "x")
        self.assertNotIn("</script><script>", html.split("<script>", 1)[1].split("</script>", 1)[0])

    def test_a_bad_entry_is_reported_and_not_written(self):
        with tempfile.TemporaryDirectory() as out:
            open(os.path.join(out, "index.html"), "w").close()
            cfg_path = os.path.join(out, "aliases.json")
            with open(cfg_path, "w") as fh:
                json.dump({"aliases": [{"id": "g1", "category": "A", "to": "index.html"},
                                       {"id": "g1", "category": "A again", "to": "index.html"},
                                       {"id": "h2", "category": "B", "to": "index.html"},
                                       {"id": "g3", "category": "C", "to": "gone.html"}]}, fh)
            n, problems = build.write_aliases(out, CFG, cfg_path)
            self.assertEqual(n, 1)
            self.assertEqual(len(problems), 3, problems)
            self.assertTrue(os.path.exists(os.path.join(out, "g1.html")))
            self.assertFalse(os.path.exists(os.path.join(out, "g3.html")))


if __name__ == "__main__":
    unittest.main()
