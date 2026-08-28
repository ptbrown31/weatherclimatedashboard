"""The daily traffic report. No network."""
import datetime as dt
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import archive as arch, report, storage, traffic   # noqa: E402

NOW = dt.datetime(2026, 8, 28, 9, 40, tzinfo=dt.timezone.utc)


def summary(days, latest=None, totals=None):
    return {"days": days, "latest": latest or (days[-1] if days else {}),
            "totals": totals or {"views": sum(d["views"] for d in days)}}


def series(n, start=10):
    return [{"day": "2026-08-%02d" % (d + 1), "views": start + d, "visitors": 3} for d in range(n)]


class Bars(unittest.TestCase):
    def test_the_longest_bar_fills_the_width(self):
        b = report.bars([{"day": "a", "views": 5}, {"day": "b", "views": 10}], width=10)
        self.assertTrue(b[1].endswith("#" * 10))
        self.assertTrue(b[0].endswith("#" * 5))

    def test_it_scales_to_the_series_not_a_fixed_ceiling(self):
        # a quiet week must be as readable as a busy one
        quiet = report.bars([{"day": "a", "views": 1}, {"day": "b", "views": 2}], width=10)
        self.assertTrue(quiet[1].endswith("#" * 10))

    def test_the_count_is_printed_as_well_as_drawn(self):
        self.assertIn("42", report.bars([{"day": "a", "views": 42}], width=4)[0])

    def test_no_days_draws_nothing(self):
        self.assertEqual(report.bars([]), [])

    def test_a_day_with_no_views_does_not_divide_by_zero(self):
        self.assertEqual(len(report.bars([{"day": "a", "views": 0}])), 1)


class Trend(unittest.TestCase):
    def test_a_fortnight_is_needed_before_a_trend_is_claimed(self):
        self.assertIsNone(report.trend(series(13)))
        self.assertIsNotNone(report.trend(series(14)))

    def test_it_names_the_direction_and_the_size(self):
        days = [{"day": str(i), "views": 10} for i in range(7)] + [{"day": str(i), "views": 20} for i in range(7)]
        t = report.trend(days)
        self.assertIn("up 100 per cent", t)

    def test_a_fall_reads_as_a_fall(self):
        days = [{"day": str(i), "views": 20} for i in range(7)] + [{"day": str(i), "views": 10} for i in range(7)]
        self.assertIn("down 50 per cent", report.trend(days))

    def test_a_week_from_nothing_does_not_divide_by_zero(self):
        days = [{"day": str(i), "views": 0} for i in range(7)] + [{"day": str(i), "views": 5} for i in range(7)]
        self.assertIn("against none", report.trend(days))


class Compose(unittest.TestCase):
    def test_the_subject_carries_the_day_and_the_count(self):
        m = report.compose(summary(series(3)), NOW)
        self.assertIn("2026-08-03", m["subject"])
        self.assertIn("12", m["subject"])

    def test_it_says_so_when_nothing_has_been_counted(self):
        m = report.compose({}, NOW)
        self.assertIn("not counted a finished day", m["body"])
        self.assertIn("no traffic", m["subject"])

    def test_the_caveats_travel_with_the_numbers(self):
        m = report.compose(summary(series(3)), NOW)
        # a visitor count that is really a client-address count must say so
        self.assertIn("distinct client address", m["body"])
        self.assertIn("not for the dozen objects a page pulls", m["body"])
        self.assertIn("no cookies", m["body"])

    def test_only_the_last_thirty_days_are_drawn(self):
        m = report.compose(summary(series(40)), NOW)
        self.assertEqual(m["body"].count("#" * 1 + " "), 0)   # bars end the line
        drawn = [ln for ln in m["body"].split("\n") if ln.startswith("  2026-")]
        self.assertEqual(len(drawn), 30)

    def test_crawlers_are_named_and_kept_out_of_the_count(self):
        s = summary(series(3))
        s["latest"] = dict(s["latest"], botRequests=99)
        self.assertIn("99 requests", report.compose(s, NOW)["body"])


class Pass(unittest.TestCase):
    def setUp(self):
        self.st = storage.LocalStorage(tempfile.mkdtemp())
        arch.LAST_REPORT = {}

    def test_it_writes_the_message_and_leaves_it_for_the_sender(self):
        self.st.put(traffic.SUMMARY_KEY, json.dumps(summary(series(3))).encode())
        rc = report.report_pass({}, self.st)
        self.assertEqual(rc, 0)
        # written to the archive, so a report that failed to send is recoverable
        doc = json.loads(self.st.get(report.KEY))
        self.assertIn("Daily visits", doc["body"])
        # and handed to the caller, which is the only thing that knows how to send
        self.assertEqual(arch.LAST_REPORT["subject"], doc["subject"])

    def test_no_traffic_yet_is_not_a_failure(self):
        self.assertEqual(report.report_pass({}, self.st), 0)
        self.assertIn("Nothing to report", json.loads(self.st.get(report.KEY))["body"])


if __name__ == "__main__":
    unittest.main()
