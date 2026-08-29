"""Tests for the SPC severe-report count job. Fixtures inline, no network."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import severe  # noqa: E402

DOC = {
    "month": {str(m): {"torn": m, "wind": 10 * m, "hail": 2 * m} for m in range(1, 13)},
    "daily": {"0801": {"torn": 3, "wind": 40, "hail": 5},
              "0802": {"torn": 0, "wind": 12, "hail": 0},
              "0804": {"torn": 2, "wind": 8, "hail": 1}},
}


class YearBlock(unittest.TestCase):
    def test_month_table_is_carried_verbatim(self):
        b = severe.year_block(DOC)
        self.assertEqual(b["months"]["8"], {"torn": 8, "wind": 80, "hail": 16})
        self.assertEqual(b["months"]["2"]["wind"], 20)

    def test_daily_cumulative_fills_missing_days(self):
        b = severe.year_block(DOC)
        # day 3 has no reports; the cumulative carries through it
        self.assertEqual(b["daily"]["8"]["torn"], [3, 3, 3, 5])
        self.assertEqual(b["through"]["8"], 4)

    def test_months_without_daily_rows_are_absent(self):
        b = severe.year_block(DOC)
        self.assertNotIn("7", b["daily"])
        self.assertNotIn("7", b["through"])


class ClimoBundle(unittest.TestCase):
    def test_bundle_is_complete_and_consistent(self):
        with open(severe.CLIMO_PATH) as fh:
            climo = json.load(fh)
        self.assertEqual(climo["yearsFrom"], 2005)
        for m in range(1, 13):
            ml = climo["months"][str(m)]
            for ph in severe.PHENOMENA:
                env = ml[ph]["env"]
                self.assertEqual(len(env["p50"]), 31)
                # quantile order holds at every day and on the totals
                for d in range(31):
                    self.assertLessEqual(env["p10"][d], env["p50"][d] + 1e-9)
                    self.assertLessEqual(env["p50"][d], env["p90"][d] + 1e-9)
                t = ml[ph]["totals"]
                self.assertLessEqual(t["min"], t["p10"] + 1e-9)
                self.assertLessEqual(t["p90"], t["max"] + 1e-9)
                # cumulative curves never decrease
                for name in ("p10", "p50", "p90"):
                    for a, b in zip(env[name], env[name][1:]):
                        self.assertLessEqual(a, b + 1e-9)

    def test_envelope_ends_near_the_total_quantiles(self):
        # each curve is rescaled to its year's settlement total, so the last
        # day of the median curve sits at the median total's scale
        with open(severe.CLIMO_PATH) as fh:
            climo = json.load(fh)
        ml = climo["months"]["9"]["torn"]
        self.assertAlmostEqual(ml["env"]["p50"][-1], ml["totals"]["p50"], delta=max(2.0, ml["totals"]["p50"] * 0.1))


class SampleSnapshot(unittest.TestCase):
    def test_sample_matches_the_schema(self):
        path = os.path.join(os.path.dirname(severe.ROOT), "weather-tools-site")
        with open(os.path.join(severe.ROOT, "samples", "snapshots", "severe.json")) as fh:
            snap = json.load(fh)
        self.assertEqual(snap["schema"], severe.SCHEMA)
        self.assertIn("2026", snap["years"])
        self.assertIn("months", snap["climo"])
        b = snap["years"]["2026"]
        for m, cum in b["daily"].items():
            for ph in severe.PHENOMENA:
                self.assertEqual(len(cum[ph]), b["through"][m])


if __name__ == "__main__":
    unittest.main()
