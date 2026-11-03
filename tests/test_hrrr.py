"""Tests for the HRRR lane (pipeline/hrrr.py) and its place in the forecast
and advanced snapshots. No network: where a test needs a run read, the hour
reader is replaced, and runs on file are written here."""
import datetime as dt
import gzip
import json
import math
import os
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import basemap, grib2, hrrr, snapshots, storage  # noqa: E402
from tests.test_snapshots import NBS_BLOCK  # noqa: E402

UTC = dt.timezone.utc
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _row(t, temp, **kw):
    return dict({"t": t, "tempF": temp, "dewF": temp - 20, "sky": 10, "wdir": 90, "wspd": 5.0, "gust": 9.0}, **kw)


def _put_run(st, cycle, hours, sid="KLAX", base=60.0):
    doc = {"schema": 1, "cycle": hrrr.run_stamp(cycle), "expected": hrrr.run_hours(cycle), "hours": list(hours),
           "points": {}, "files": {},
           "stations": {sid: [_row(hrrr._iso(cycle + dt.timedelta(hours=h)), base + h) for h in hours]}}
    doc["complete"] = len(doc["hours"]) >= doc["expected"]
    st.put(hrrr.run_key(cycle), gzip.compress(json.dumps(doc).encode()), "application/json")


class Wind(unittest.TestCase):
    def _north_in_grid(self, lat, lon):
        # true north as the grid sees it, from the projection itself: the step
        # from a point to one a little north of it, in grid units
        i0, j0 = grib2.lcc_ij(grib2.HRRR, lat, lon)
        i1, j1 = grib2.lcc_ij(grib2.HRRR, lat + 0.01, lon)
        n = math.hypot(i1 - i0, j1 - j0)
        return (i1 - i0) / n, (j1 - j0) / n

    def test_a_wind_toward_true_north_turns_to_north_on_either_side_of_the_orientation_meridian(self):
        for lat, lon in ((33.94, -118.40), (47.43, -122.30), (40.78, -73.88), (25.79, -80.29)):
            ug, vg = self._north_in_grid(lat, lon)
            u, v = hrrr.to_earth(ug, vg, lon)
            self.assertAlmostEqual(u, 0.0, places=5, msg=(lat, lon))
            self.assertAlmostEqual(v, 1.0, places=5, msg=(lat, lon))
        # and the lean is real: at Los Angeles the grid's north is 13 degrees off true north
        ug, _ = self._north_in_grid(33.94, -118.40)
        self.assertAlmostEqual(math.degrees(math.asin(ug)), 13.0, delta=0.2)

    def test_the_turn_keeps_the_speed(self):
        u, v = hrrr.to_earth(3.0, -4.0, -118.4)
        self.assertAlmostEqual(math.hypot(u, v), 5.0, places=9)

    def test_direction_is_where_the_wind_blows_from(self):
        for (u, v), want in (((0.0, -5.0), 0.0), ((-5.0, 0.0), 90.0), ((0.0, 5.0), 180.0), ((5.0, 0.0), 270.0)):
            spd, d = hrrr.wind_from(u, v)
            self.assertAlmostEqual(spd, 5.0)
            self.assertAlmostEqual(d % 360.0, want, msg=(u, v))


class Points(unittest.TestCase):
    def test_every_station_on_the_grid_gets_its_nearest_point_and_the_rest_none(self):
        pts = hrrr.station_points(basemap.load_roster())
        self.assertIn("KLAX", pts)
        self.assertLess(pts["KLAX"]["km"], hrrr.MAX_POINT_KM)
        self.assertEqual(pts["KLAX"]["k"], pts["KLAX"]["j"] * grib2.HRRR["Ni"] + pts["KLAX"]["i"])
        for off in ("PHNL", "EDDF", "RJTT"):
            self.assertNotIn(off, pts)


class Runs(unittest.TestCase):
    def test_run_lengths_and_names(self):
        self.assertEqual(hrrr.run_hours(dt.datetime(2026, 10, 16, 12, tzinfo=UTC)), 48)
        self.assertEqual(hrrr.run_hours(dt.datetime(2026, 10, 16, 13, tzinfo=UTC)), 18)
        c = dt.datetime(2026, 10, 16, 7, tzinfo=UTC)
        self.assertEqual(hrrr.run_key(c), "archive/hrrr/run_20261016T0700Z.json.gz")
        self.assertTrue(hrrr.file_url(c, 5).endswith("/hrrr.20261016/conus/hrrr.t07z.wrfsfcf05.grib2"))

    def test_a_message_on_another_grid_is_refused(self):
        with open(os.path.join(FIXTURES, "hrrr_t13z_f06_tmp_2m_subgrid_c3o2.grb2"), "rb") as fh:
            msg = fh.read()
        why = hrrr.check_message(msg, (0, 0, 0), dt.datetime(2026, 10, 16, 13, tzinfo=UTC), 6)
        self.assertEqual(why, "message is on another grid")


class Series(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = storage.LocalStorage(self.tmp.name)
        hrrr.reset_cache()

    def tearDown(self):
        self.tmp.cleanup()
        hrrr.reset_cache()

    def keys(self):
        return self.st.list(hrrr.RUN_PREFIX)

    def test_the_newest_run_is_carried_on_by_the_newest_48_hour_run(self):
        _put_run(self.st, dt.datetime(2026, 10, 16, 6, tzinfo=UTC), range(1, 49), base=40.0)
        _put_run(self.st, dt.datetime(2026, 10, 16, 12, tzinfo=UTC), range(1, 49), base=50.0)
        _put_run(self.st, dt.datetime(2026, 10, 16, 13, tzinfo=UTC), range(1, 19), base=60.0)
        s = hrrr.station_series(self.st, "KLAX", self.keys())
        self.assertEqual(s["cycle"], "20261016T1300Z")
        self.assertEqual([r["t"] for r in s["rows"]][0], "2026-10-16T14:00:00Z")
        self.assertEqual(len(s["rows"]), 18)
        self.assertEqual(s["extCycle"], "20261016T1200Z")
        # the 12Z run takes over after the 13Z run's last hour, 07Z on the 17th
        self.assertEqual(s["ext"][0]["t"], "2026-10-17T08:00:00Z")
        self.assertEqual(s["ext"][-1]["t"], "2026-10-18T12:00:00Z")
        self.assertEqual(s["ext"][0]["tempF"], 50.0 + 20)
        self.assertEqual(set(s["rows"][0]), {"t", "tempF"})

    def test_a_cut_takes_the_runs_that_stood_then(self):
        _put_run(self.st, dt.datetime(2026, 10, 16, 6, tzinfo=UTC), range(1, 49), base=40.0)
        _put_run(self.st, dt.datetime(2026, 10, 16, 7, tzinfo=UTC), range(1, 19), base=45.0)
        _put_run(self.st, dt.datetime(2026, 10, 16, 8, tzinfo=UTC), range(1, 19), base=70.0)
        s = hrrr.station_series(self.st, "KLAX", self.keys(), cut="20261016T070000Z",
                                fields=("tempF", "dewF", "wdir"))
        self.assertEqual(s["cycle"], "20261016T0700Z")
        self.assertEqual(s["extCycle"], "20261016T0600Z")
        self.assertEqual(s["ext"][0]["t"], "2026-10-17T02:00:00Z")
        self.assertEqual(set(s["rows"][0]), {"t", "tempF", "dewF", "wdir"})
        self.assertIsNone(hrrr.station_series(self.st, "KLAX", self.keys(), cut="20261016T050000Z"))

    def test_a_station_no_run_carries_has_no_series(self):
        _put_run(self.st, dt.datetime(2026, 10, 16, 13, tzinfo=UTC), range(1, 19))
        self.assertIsNone(hrrr.station_series(self.st, "PHNL", self.keys()))


class _Deadline:
    """Over after `n` checks."""
    def __init__(self, n):
        self.n = n

    def over(self, need=0.0):
        self.n -= 1
        return self.n < 0


class Processing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = storage.LocalStorage(self.tmp.name)
        self.published = 0
        self.real = hrrr.read_hour
        hrrr.read_hour = self.fake

    def tearDown(self):
        hrrr.read_hour = self.real
        self.tmp.cleanup()

    def fake(self, cycle, ff, points):
        if ff > self.published:
            return None
        t = hrrr._iso(cycle + dt.timedelta(hours=ff))
        return {sid: _row(t, 60.0 + ff) for sid in points}, f"lm-{ff}"

    def status(self):
        return {"runs": [], "hours": 0, "waiting": [], "errors": [], "stopped": None}

    def test_a_run_is_read_as_it_is_published_and_finished_later(self):
        c, pts = dt.datetime(2026, 10, 16, 12, tzinfo=UTC), {"KLAX": {"k": 1}, "KSEA": {"k": 2}}
        self.published = 10
        st = self.status()
        self.assertFalse(hrrr.process_run(self.st, c, pts, _Deadline(1000), st))
        doc = hrrr.read_run(self.st, hrrr.run_key(c))
        self.assertEqual(doc["hours"], list(range(1, 11)))
        self.assertFalse(doc["complete"])
        self.assertEqual(st["waiting"], ["20261016T1200Z f11"])
        self.published = 48
        hrrr.process_run(self.st, c, pts, _Deadline(1000), self.status())
        doc = hrrr.read_run(self.st, hrrr.run_key(c))
        self.assertTrue(doc["complete"])
        self.assertEqual(len(doc["stations"]["KSEA"]), 48)
        self.assertEqual(doc["files"]["48"], "lm-48")
        # a finished run is not read again
        st = self.status()
        hrrr.process_run(self.st, c, pts, _Deadline(1000), st)
        self.assertEqual(st["hours"], 0)

    def test_a_pass_out_of_time_keeps_what_it_read(self):
        c, pts = dt.datetime(2026, 10, 16, 13, tzinfo=UTC), {"KLAX": {"k": 1}}
        self.published = 18
        st = self.status()
        self.assertTrue(hrrr.process_run(self.st, c, pts, _Deadline(3), st))
        doc = hrrr.read_run(self.st, hrrr.run_key(c))
        self.assertEqual(doc["hours"], [1, 2, 3])
        self.assertEqual(st["stopped"], "20261016T1300Z f04")


class InTheSnapshots(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = storage.LocalStorage(self.tmp.name)
        hrrr.reset_cache()
        self.c = next(c for c in basemap.load_roster() if c["station"] == "KLAX")
        # LA's day of 16 October begins at 07Z; the 06Z run was issued before it
        # and the 07Z run, issued at midnight, belongs to the day
        for h, n in ((0, 48), (6, 48), (7, 18), (12, 48), (13, 18)):
            _put_run(self.st, dt.datetime(2026, 10, 16, h, tzinfo=UTC), range(1, n + 1), base=50.0 + h)
        self.now = dt.datetime(2026, 10, 16, 15, 0, tzinfo=UTC)

    def tearDown(self):
        self.tmp.cleanup()
        hrrr.reset_cache()

    def test_the_forecast_snapshot_carries_the_newest_run_and_the_one_that_stood_before_the_day(self):
        snap = snapshots.build_forecast_snapshot(self.st, self.c, self.now)
        self.assertEqual(snap["hrrr"]["cycle"], "20261016T1300Z")
        self.assertEqual(snap["hrrr"]["extCycle"], "20261016T1200Z")
        self.assertIn("tempC", snap["hrrr"]["hourly"][0])
        # the run the chart pins its levels to was issued before midnight (owner,
        # 2026-10-16): the 06Z run, a 48-hour one that needs nothing to carry it on
        ai = snap["asIssued"]["hrrr"]
        self.assertEqual((ai["cycle"], ai["extCycle"], ai["preDay"]), ("20261016T0600Z", None, True))
        # the day before began at 07Z on the 15th, before any run on file
        self.assertNotIn("hrrr", snap["yesterday"])

    def test_with_no_run_from_before_the_day_the_first_run_of_the_day_stands_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = storage.LocalStorage(tmp)
            hrrr.reset_cache()
            for h, n in ((7, 18), (8, 18), (12, 48)):
                _put_run(st, dt.datetime(2026, 10, 16, h, tzinfo=UTC), range(1, n + 1), base=50.0 + h)
            snap = snapshots.build_forecast_snapshot(st, self.c, self.now)
            ai = snap["asIssued"]["hrrr"]
            self.assertEqual((ai["cycle"], ai["preDay"]), ("20261016T0700Z", False))
            self.assertNotIn("hrrr", snap["yesterday"])
        hrrr.reset_cache()

    def test_the_advanced_snapshot_carries_every_element(self):
        adv = snapshots.build_advanced_snapshot(self.st, self.c, self.now)
        row = adv["current"]["hrrr"]["rows"][0]
        self.assertEqual(set(row), {"t", "tempF", "dewF", "sky", "wdir", "wspd", "gust"})
        self.assertEqual(adv["current"]["hrrr"]["cycle"], "20261016T1300Z")

    def test_the_blend_is_carried_on_by_its_3_hourly_rows(self):
        st = self.st
        st.put("archive/KLAX/nbs_20260821T2300Z.txt.gz", gzip.compress(NBS_BLOCK.encode()))
        rows = [{"t": "2026-08-22T20:00:00Z", "tempF": 80.0}]
        ext = snapshots._nbs_ext(st, st.list("archive/KLAX/nbs_"), "20260822T000000Z", rows, "F")
        self.assertTrue(ext and all(r["t"] > "2026-08-22T20:00:00Z" for r in ext))
        self.assertEqual(ext[0]["t"], "2026-08-22T21:00:00Z")
        # nothing at or before the cut, nothing to carry on with
        self.assertEqual(snapshots._nbs_ext(st, st.list("archive/KLAX/nbs_"), "20260821T000000Z", rows, "F"), [])


if __name__ == "__main__":
    unittest.main()
