"""The catalogue's quote lane skips a pass that comes within twenty minutes of the last one, which only a rerun of a
failed half-hourly chain does (owner, 2026-11-07), and asks the exchange nothing then. No network."""
import contextlib
import datetime as dt
import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pipeline import catquotes, storage   # noqa: E402

CFG = {"contracts": {"categories": [{"slug": "climate", "l2": "Climate"}]}}


class Rerun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = storage.LocalStorage(self.tmp.name)
        self.calls = []

    def fetch(self, conid):
        self.calls.append(conid)
        return {}

    def last_at(self, minutes_ago):
        t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes_ago)
        self.st.put(catquotes.INDEX_KEY, json.dumps({"schema": 1, "asof": catquotes._iso(t), "products": {}}).encode())

    def run_pass(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = catquotes.catquotes_pass(CFG, self.st, fetch=self.fetch)
        return rc, json.loads(out.getvalue().strip().splitlines()[-1])

    def test_a_pass_minutes_after_the_last_skips_and_asks_nothing(self):
        self.last_at(3)
        rc, line = self.run_pass()
        self.assertEqual((rc, line.get("written")), (0, False))
        self.assertIn("the last pass was 3 minutes ago", line["skipped"])
        self.assertEqual(self.calls, [])

    def test_the_half_hourly_pass_runs(self):
        self.last_at(30)
        rc, line = self.run_pass()
        self.assertNotIn("skipped", line)

    def test_a_first_pass_or_an_unreadable_index_runs(self):
        self.assertIsNone(catquotes.last_pass(self.st))
        self.st.put(catquotes.INDEX_KEY, b"not json")
        self.assertIsNone(catquotes.last_pass(self.st))
        rc, line = self.run_pass()
        self.assertNotIn("skipped", line)


if __name__ == "__main__":
    unittest.main()
