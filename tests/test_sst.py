"""The sea surface temperature anomaly lane: ERDDAP's CSV becomes a compact
grid per basin, a pass that finds no newer day fetches nothing, a basin
that fails keeps its previous grid, and ERDDAP failing fails the chain only
after a streak of passes. No network."""
import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pipeline import sst, storage   # noqa: E402

U = dt.timezone.utc
CSV = """time,latitude,longitude,sea_surface_temperature_anomaly
UTC,degrees_north,degrees_east,degree_C
2026-10-05T12:00:00Z,30.025,-89.975,2.97
2026-10-05T12:00:00Z,30.025,-89.775,NaN
2026-10-05T12:00:00Z,30.025,-89.575,-1.04
2026-10-05T12:00:00Z,29.825,-89.975,15.6
2026-10-05T12:00:00Z,29.825,-89.775,0.04
2026-10-05T12:00:00Z,29.825,-89.575,-0.55
"""
LATEST = "time\nUTC\n2026-10-05T12:00:00Z\n"


class Grid(unittest.TestCase):
    def test_rows_run_north_to_south_and_land_is_missing(self):
        g = sst.parse_grid(CSV)
        self.assertEqual((g["nx"], g["ny"], g["step"], g["lat0"], g["lon0"], g["day"]),
                         (3, 2, 0.2, 30.025, -89.975, "2026-10-05"))
        # tenths of a degree, -128 for land, and an extreme clipped at the byte's range
        self.assertEqual(sst.decode(g["data"]), [3.0, None, -1.0, 12.7, 0.0, -0.6])

    def test_a_short_grid_is_refused(self):
        with self.assertRaises(ValueError):
            sst.parse_grid("\n".join(CSV.splitlines()[:-1]))

    def test_the_newest_date(self):
        self.assertEqual(sst.parse_latest(LATEST), "2026-10-05")
        self.assertIsNone(sst.parse_latest("time\nUTC\n"))

    def test_the_subset_asks_from_the_north_at_the_stride(self):
        u = sst.subset_url((-101.0, 4.0, -40.0, 48.0))
        self.assertIn("noaacrwsstanomalyDaily.csv?sea_surface_temperature_anomaly", u)
        self.assertIn("[(48.0):4:(4.0)][(-101.0):4:(-40.0)]", u.replace("%5B", "[").replace("%5D", "]"))


class Job(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = storage.LocalStorage(self.tmp.name)
        self.calls = []
        self.fail = set()

    def fetch(self, url):
        self.calls.append(url)
        if "time" in url and "sea_surface" not in url:
            return LATEST
        if any(b in url for b in self.fail):
            raise OSError("down")
        return CSV

    def test_a_first_pass_fetches_both_basins(self):
        now = dt.datetime(2026, 10, 7, 12, tzinfo=U)
        out = sst.sst_job({}, self.st, now, fetch=self.fetch)
        self.assertEqual((out["fetched"], out["asof"]), (2, "2026-10-05"))
        snap = json.loads(self.st.get(sst.KEY))
        self.assertEqual(sorted(snap["basins"]), ["AL", "EP"])
        self.assertEqual(snap["basins"]["AL"]["box"], [-101.0, 4.0, -40.0, 48.0])
        self.assertEqual((snap["scale"], snap["missing"], snap["units"]), (0.1, -128, "degC"))
        self.assertIn("Coral Reef Watch", snap["source"])

    def test_a_recent_check_asks_nothing_and_no_newer_day_fetches_nothing(self):
        now = dt.datetime(2026, 10, 7, 12, tzinfo=U)
        sst.sst_job({}, self.st, now, fetch=self.fetch)
        n = len(self.calls)
        self.assertFalse(sst.sst_job({}, self.st, now + dt.timedelta(hours=1), fetch=self.fetch)["written"])
        self.assertEqual(len(self.calls), n)
        out = sst.sst_job({}, self.st, now + dt.timedelta(hours=4), fetch=self.fetch)
        self.assertEqual((out["fetched"], out["note"]), (0, "no newer day"))
        self.assertEqual(len(self.calls), n + 1)              # only the date was asked for

    def test_a_basin_that_fails_keeps_its_previous_grid(self):
        now = dt.datetime(2026, 10, 7, 12, tzinfo=U)
        sst.sst_job({}, self.st, now, fetch=self.fetch)
        prev = json.loads(self.st.get(sst.KEY))["basins"]["EP"]
        snap = json.loads(self.st.get(sst.KEY))
        snap["asof"] = "2026-10-04"                           # a newer day has landed since
        snap["checked"] = "2026-10-07T00:00:00Z"
        self.st.put(sst.KEY, json.dumps(snap).encode())
        self.fail = {"(-180.0)"}
        out = sst.sst_job({}, self.st, now + dt.timedelta(hours=4), fetch=self.fetch)
        self.assertEqual(out["fetched"], 1)
        self.assertTrue(any(e.startswith("EP:") for e in out["errors"]))
        self.assertEqual(json.loads(self.st.get(sst.KEY))["basins"]["EP"], prev)

    def test_a_date_erddap_does_not_answer_or_two_failed_basins_are_a_failed_read(self):
        now = dt.datetime(2026, 10, 7, 12, tzinfo=U)

        def down(url):
            raise OSError("HTTP Error 502: Proxy Error")
        with self.assertRaises(sst.ReadFailed) as cm:
            sst.sst_job({}, self.st, now, fetch=down)
        self.assertIn("502", str(cm.exception))
        self.fail = {"(-180.0)", "(-101.0)"}
        with self.assertRaises(sst.ReadFailed) as cm:
            sst.sst_job({}, self.st, now, fetch=self.fetch)
        self.assertIn("AL:", str(cm.exception))
        self.assertIsNone(self.st.get(sst.KEY))


class Pass(unittest.TestCase):
    """The pass's status: ERDDAP failing fails the chain only once it has failed
    FAIL_STREAK_ALARM passes in a row (an hour of half-hourly passes)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = storage.LocalStorage(self.tmp.name)
        self.result = {"written": True, "fetched": 0, "asof": "2026-11-12", "note": "no newer day"}
        p = mock.patch.object(sst, "sst_job", self.job)
        p.start()
        self.addCleanup(p.stop)

    def job(self, cfg, store, now, deadline=None):
        if isinstance(self.result, Exception):
            raise self.result
        return dict(self.result)

    def run_pass(self):
        rc = sst.sst_pass({"user_agent": "test"}, self.st)
        return rc, dict(sst.arch.LAST_STATUS)

    def streak(self):
        raw = self.st.get(sst.HEALTH_KEY)
        return None if raw is None else json.loads(raw)["erddap"]["fail_streak"]

    def test_erddap_failing_once_is_reported_and_does_not_fail_the_pass(self):
        self.result = sst.ReadFailed("HTTPError: HTTP Error 502: Proxy Error")
        rc, status = self.run_pass()
        self.assertEqual((rc, status["errors"], status["alarms"]), (0, 1, []))
        health = json.loads(self.st.get(sst.HEALTH_KEY))
        self.assertEqual(health["erddap"]["fail_streak"], 1)
        self.assertIn("502", health["erddap"]["last_error"])

    def test_erddap_failing_for_an_hour_of_passes_raises_the_alarm(self):
        self.result = sst.ReadFailed("HTTPError: HTTP Error 503: Service Unavailable")
        for _ in range(sst.FAIL_STREAK_ALARM - 1):
            self.assertEqual(self.run_pass()[1]["alarms"], [])
        rc, status = self.run_pass()
        self.assertEqual((rc, status["alarms"]), (0, ["sst"]))

    def test_a_check_erddap_answers_ends_the_streak(self):
        self.result = sst.ReadFailed("HTTPError: HTTP Error 502: Proxy Error")
        for _ in range(sst.FAIL_STREAK_ALARM - 1):
            self.run_pass()
        self.result = {"written": True, "fetched": 0, "asof": "2026-11-12", "note": "no newer day"}
        self.assertEqual(self.run_pass(), (0, mock.ANY))
        self.assertEqual(self.streak(), 0)
        self.result = sst.ReadFailed("HTTPError: HTTP Error 502: Proxy Error")
        self.assertEqual(self.run_pass()[1]["alarms"], [])

    def test_a_pass_inside_the_check_interval_leaves_the_streak_as_it_is(self):
        self.result = {"written": False, "note": "checked 0.5 h ago", "asked": False}
        self.assertEqual(self.run_pass(), (0, mock.ANY))
        self.assertIsNone(self.st.get(sst.HEALTH_KEY))
        self.result = sst.ReadFailed("HTTPError: HTTP Error 502: Proxy Error")
        for _ in range(sst.FAIL_STREAK_ALARM - 1):
            self.run_pass()
        self.result = {"written": False, "note": "checked 0.5 h ago", "asked": False}
        self.run_pass()
        self.assertEqual(self.streak(), sst.FAIL_STREAK_ALARM - 1)

    def test_a_basin_that_fails_while_the_other_is_fetched_still_fails_the_pass(self):
        self.result = {"written": True, "fetched": 1, "asof": "2026-11-12", "errors": ["EP: OSError: down"]}
        rc, status = self.run_pass()
        self.assertEqual((rc, status["errors"], status["alarms"]), (1, 1, []))
        self.assertEqual(self.streak(), 0)

    def test_the_jobs_own_failure_still_fails_the_pass(self):
        self.result = OSError("the snapshot could not be written")
        rc, status = self.run_pass()
        self.assertEqual((rc, status["alarms"]), (1, []))
        self.assertIsNone(self.st.get(sst.HEALTH_KEY))


if __name__ == "__main__":
    unittest.main()
