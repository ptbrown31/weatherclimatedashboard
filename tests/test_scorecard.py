"""Scorecard pure functions: pre-day cycle selection with the lead limit,
and the error summaries. No network."""
import datetime as dt
import os
import sys
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import scorecard, snapshots, normals    # noqa: E402

LA = ZoneInfo("America/Los_Angeles")


class PreDay(unittest.TestCase):
    def test_latest_within_24h_before_midnight(self):
        midnight = dt.datetime(2026, 8, 21, 0, 0, tzinfo=LA)          # 07:00Z
        keys = ["archive/KLAX/daily_20260819T190000Z.json.gz", "archive/KLAX/daily_20260820T120000Z.json.gz",
                "archive/KLAX/daily_20260821T062647Z.json.gz", "archive/KLAX/daily_20260821T190000Z.json.gz"]
        key, lead = scorecard.pre_day_cycle(keys, "daily", midnight)
        self.assertEqual(snapshots._stamp_of(key), "20260821T062647Z")
        self.assertAlmostEqual(lead, 0.6, places=1)

    def test_cycle_too_old_is_not_scored(self):
        midnight = dt.datetime(2026, 8, 21, 0, 0, tzinfo=LA)
        keys = ["archive/KLAX/daily_20260819T190000Z.json.gz"]
        self.assertIsNone(scorecard.pre_day_cycle(keys, "daily", midnight))
        self.assertIsNone(scorecard.pre_day_cycle([], "daily", midnight))

    def test_bulletin_stamp_at_midnight_counts(self):
        midnight = dt.datetime(2026, 8, 21, 0, 0, tzinfo=LA)
        keys = ["archive/KLAX/nbs_20260821T0600Z.txt.gz", "archive/KLAX/nbs_20260821T0700Z.txt.gz"]
        key, lead = scorecard.pre_day_cycle(keys, "nbs", midnight)
        self.assertEqual(snapshots._stamp_of(key), "20260821T0700Z")
        self.assertEqual(lead, 0.0)


class Summaries(unittest.TestCase):
    def test_summarise(self):
        rows = [{"e": 1.0}, {"e": -2.0}, {"e": 0.5}, {"e": None}, {"e": 3.0}]
        s = scorecard.summarise(rows, "e")
        self.assertEqual(s["n"], 4)
        self.assertEqual(s["mae"], 1.62)
        self.assertEqual(s["bias"], 0.62)
        self.assertEqual(s["within1"], 0.5)
        self.assertEqual(s["within2"], 0.75)
        self.assertIsNone(scorecard.summarise([{"e": None}], "e"))


class Normals(unittest.TestCase):
    def test_distance(self):
        self.assertLess(abs(normals._dist_km(33.9381, -118.3889, 33.93816, -118.3866) - 0.21), 0.05)
        self.assertLess(abs(normals._dist_km(33.9381, -118.3889, 34.0236, -118.2911) - 13.3), 0.5)


if __name__ == "__main__":
    unittest.main()
