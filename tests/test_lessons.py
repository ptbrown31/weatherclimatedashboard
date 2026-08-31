"""Tests for the lessons data the site's courses page renders.

The words are the two Traders' Academy scripts, parsed rather than retyped, so
what matters here is that the file stays a faithful, complete copy: every
lesson present and numbered, every one carrying cues, a link to the page it
teaches, and answers to its own questions.
"""
import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PAGES = {n for n in os.listdir(os.path.join(ROOT, "site")) if n.endswith(".html")}


def load():
    with open(os.path.join(ROOT, "site", "assets", "lessons.json")) as fh:
        return json.load(fh)


class Shape(unittest.TestCase):
    def setUp(self):
        self.doc = load()

    def test_two_courses_with_the_lessons_they_claim(self):
        slugs = [c["slug"] for c in self.doc["courses"]]
        self.assertEqual(slugs, ["reading", "trading"])
        self.assertEqual([len(c["lessons"]) for c in self.doc["courses"]], [10, 8])
        self.assertEqual([c["level"] for c in self.doc["courses"]], ["Beginner", "Intermediate"])

    def test_lessons_are_numbered_from_one_without_gaps(self):
        for c in self.doc["courses"]:
            self.assertEqual([l["n"] for l in c["lessons"]], list(range(1, len(c["lessons"]) + 1)))

    def test_every_lesson_carries_its_script(self):
        for c in self.doc["courses"]:
            for l in c["lessons"]:
                where = c["slug"] + "-" + str(l["n"])
                self.assertTrue(l["title"], where)
                self.assertRegex(l["dur"], r"^\d+:\d\d$", where)
                self.assertTrue(l["aim"], where)
                self.assertGreaterEqual(len(l["parts"]), 3, where)
                for p in l["parts"]:
                    self.assertTrue(p["cue"], where)
                    self.assertTrue(p["text"], where)

    def test_every_lesson_links_to_a_page_that_exists(self):
        for c in self.doc["courses"]:
            for l in c["lessons"]:
                href = l["link"]["href"].split("#")[0]
                self.assertIn(href, PAGES, c["slug"] + "-" + str(l["n"]))
                self.assertTrue(l["link"]["label"])

    def test_every_question_has_an_answer(self):
        n = 0
        for c in self.doc["courses"]:
            for l in c["lessons"]:
                self.assertGreaterEqual(len(l["quiz"]), 1, l["title"])
                for q in l["quiz"]:
                    self.assertTrue(q["q"] and q["a"], l["title"])
                    n += 1
        self.assertEqual(n, 54)


class Language(unittest.TestCase):
    """The exchange's own vocabulary, held to across every word the site
    publishes here. A course that teaches the wrong verb teaches it to
    everyone who takes it."""

    def setUp(self):
        self.words = " ".join(
            " ".join(p["cue"] + " " + " ".join(p["text"]) for p in l["parts"])
            + " " + l["aim"] + " " + " ".join(q["q"] + " " + q["a"] for q in l["quiz"])
            for c in load()["courses"] for l in c["lessons"]).lower()

    def test_selling_is_named_only_to_deny_it(self):
        """The exchange has no sellers, which the course has to be able to say.

        So the word is allowed in that denial and nowhere else, rather than
        banned outright, which would forbid the one sentence that teaches the
        mechanic. `ask` is left alone because it is the ordinary English verb
        throughout and the market sense is caught by the phrases below.
        """
        for m in re.finditer(r"\b(sell\w*)\b", self.words):
            around = self.words[max(0, m.start() - 12):m.end() + 4]
            self.assertIn("no seller", around, "selling named outside the denial: " + around)

    def test_no_order_book_vocabulary(self):
        for bad in ("the ask", "bid-ask", "bid/ask", "asking price", "offer",
                    "short the", "go short", "sold at"):
            self.assertNotIn(bad, self.words, bad)

    def test_the_settlement_rule_is_stated_strictly(self):
        self.assertIn("strictly above", self.words)
        self.assertIn("strictly below", self.words)

    def test_the_netting_rule_is_taught(self):
        self.assertIn("nets them", self.words)

    def test_no_fair_value_is_claimed(self):
        self.assertNotIn("our fair value", self.words)
        self.assertIn("no forecast of its own", self.words)


if __name__ == "__main__":
    unittest.main()
