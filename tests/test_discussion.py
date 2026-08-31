"""Tests for the forecast-discussion lane and its rolling history.

The history exists so the city page's postmortem can show the reasoning that
stood at the moment it scores rather than today's words over yesterday's
numbers. What is held down here is that the window only grows on a real new
issuance, that it keeps the newest first, and that it never grows without
bound. No network: discussion_pass takes its fetch as an argument.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import discussion  # noqa: E402


def product(body: str, issued: str = "517 AM MDT Thu Aug 27 2026") -> str:
    return ("000\nFXUS65 KBOU 271117\nAFDBOU\n\n"
            "Area Forecast Discussion\nNational Weather Service Denver/Boulder CO\n"
            + issued + "\n\n" + body + "\n")


class Store:
    """The two calls the pass makes, and a record of what it wrote."""

    def __init__(self, seed=None):
        self.o = dict(seed or {})
        self.puts = 0

    def get(self, key):
        return self.o.get(key)

    def put(self, key, body, *a, **k):
        self.o[key] = body
        self.puts += 1

    def past(self, wfo="BOU"):
        raw = self.o.get(discussion.PAST_KEY.format(wfo=wfo))
        return json.loads(raw)["issuances"] if raw else []


CFG = {"stations": ["KDEN"]}


def run(store, text, now_iso=None):
    """One pass over a single office, with the mapping the archive would hold."""
    store.o.setdefault("archive/_meta/grids.json",
                       json.dumps({"KDEN": {"wfo": "BOU"}}).encode())
    return discussion.discussion_pass(CFG, store, fetch=lambda office: text)


class History(unittest.TestCase):
    def test_a_new_issuance_is_kept_newest_first(self):
        store = Store()
        run(store, product("First run of the day."))
        run(store, product("Second run, the front slowed."))
        past = store.past()
        self.assertEqual(len(past), 2)
        self.assertIn("front slowed", past[0]["body"])
        self.assertIn("First run", past[1]["body"])
        self.assertGreaterEqual(past[0]["seen"], past[1]["seen"])

    def test_an_unchanged_discussion_does_not_grow_the_window(self):
        store = Store()
        same = product("Nothing has changed since the last run.")
        run(store, same)
        run(store, same)
        run(store, same)
        self.assertEqual(len(store.past()), 1)

    def test_the_window_is_bounded(self):
        store = Store()
        for i in range(discussion.PAST_KEEP + 5):
            run(store, product("Issuance number %d." % i))
        past = store.past()
        self.assertEqual(len(past), discussion.PAST_KEEP)
        self.assertIn("number %d" % (discussion.PAST_KEEP + 4), past[0]["body"])

    def test_each_entry_carries_what_a_page_needs_to_show_it(self):
        store = Store()
        run(store, product("The marine layer deepened overnight."))
        e = store.past()[0]
        for k in ("seen", "issued", "office", "body", "url"):
            self.assertIn(k, e, k)
        self.assertEqual(e["issued"], "517 AM MDT Thu Aug 27 2026")
        self.assertIn("Denver", e["office"])
        self.assertIn("marine layer", e["body"])

    def test_a_corrupt_history_is_replaced_rather_than_raising(self):
        store = Store({discussion.PAST_KEY.format(wfo="BOU"): b"not json"})
        run(store, product("A fresh discussion."))
        self.assertEqual(len(store.past()), 1)


if __name__ == "__main__":
    unittest.main()
