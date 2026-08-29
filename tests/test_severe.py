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


class YearCarry(unittest.TestCase):
    """The pass carries any year it could not fetch from the previous
    snapshot, so December's block survives the New Year while its contract
    settles, and a current-year outage cannot be clobbered by a successful
    next-year fetch."""

    class Store:
        def __init__(self, prev):
            self.d = {"snapshots/severe.json": json.dumps(prev).encode()} if prev else {}

        def get(self, k):
            return self.d.get(k)

        def put(self, k, body, *a):
            self.d[k] = body

    def run_pass(self, fetches, prev, year=2027):
        import datetime as dt
        from unittest import mock
        store = self.Store(prev)
        def fake_fetch(url):
            y = url.split("/")[-4]
            if y in fetches:
                return json.dumps(fetches[y]).encode()
            raise RuntimeError("404")
        class FakeDate(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return dt.datetime(year, 1, 2, 12, tzinfo=tz)
        with mock.patch.object(severe.gw, "_fetch", side_effect=fake_fetch), \
             mock.patch.object(severe.dt, "datetime", FakeDate):
            rc = severe.severe_pass({"user_agent": "t"}, store)
        raw = store.d.get("snapshots/severe.json")
        return rc, (json.loads(raw) if raw else None)

    def test_december_survives_the_new_year(self):
        prev = {"schema": 1, "years": {"2026": {"months": {"12": {"torn": 40}}, "daily": {}, "through": {}}}}
        rc, snap = self.run_pass({"2027": DOC}, prev)
        self.assertEqual(rc, 0)
        self.assertIn("2026", snap["years"])          # December still open
        self.assertIn("2027", snap["years"])          # the fresh fetch won its slot
        self.assertEqual(snap["years"]["2026"]["months"]["12"]["torn"], 40)

    def test_current_year_outage_is_carried_and_marked(self):
        prev = {"schema": 1, "years": {"2027": {"months": {"1": {"torn": 7}}, "daily": {}, "through": {}}}}
        rc, snap = self.run_pass({}, prev)
        self.assertEqual(rc, 0)
        self.assertEqual(snap["years"]["2027"]["months"]["1"]["torn"], 7)
        self.assertTrue(any("previous snapshot" in e for e in snap["errors"]))
        self.assertIn("staleSince", snap)

    def test_nothing_fetched_and_nothing_to_carry_returns_one(self):
        rc, snap = self.run_pass({}, None)
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
