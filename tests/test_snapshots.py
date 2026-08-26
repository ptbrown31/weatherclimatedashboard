"""
Tests for the daily-extreme parsers, day bucketing and the snapshot builders.
Fixtures are cut from the 2026-08-21 bulletins. No network.
"""
import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import gov_weather as gw      # noqa: E402
from pipeline import snapshots, storage      # noqa: E402

NBS_BLOCK = """\
 KLAX    NBM V5.0 NBS GUIDANCE    8/21/2026  2300 UTC
 DT /AUG  22               /AUG  23                /AUG  24
 UTC  03 06 09 12 15 18 21 00 03 06 09 12 15 18 21 00 03 06 09 12 15 18 21
 FHR  05 08 11 14 17 20 23 26 29 32 35 38 41 44 47 50 53 56 59 62 65 68 71
 TXN           69          82          69          83          70
 XND            1           2           2           2           2
 TMP  73 71 69 69 73 80 79 77 74 72 70 69 73 81 80 78 74 72 70 70 74 81 80
"""

MAV_BLOCK = """\
 KLAX   GFS MOS GUIDANCE    8/21/2026  1800 UTC
 DT /AUG  22                  /AUG  23                /AUG  24
 HR   00 03 06 09 12 15 18 21 00 03 06 09 12 15 18 21 00 03 06 12 18
 N/X              69          81          68          81       68
 TMP  77 72 71 71 70 72 78 79 76 72 72 70 70 72 78 78 76 72 71 69 80
"""

LA = ZoneInfo("America/Los_Angeles")
U = dt.timezone.utc


class DailyExtremes(unittest.TestCase):
    def test_nbs_txn_columns_and_kinds(self):
        p = gw.parse_nbs_block(NBS_BLOCK)
        self.assertEqual(p["cycle"], dt.datetime(2026, 8, 21, 23, tzinfo=U))
        ex = [(e["time"].isoformat(), e["kind"], e["temp_f"]) for e in p["extremes"]]
        self.assertEqual(ex, [
            ("2026-08-22T12:00:00+00:00", "min", 69.0),
            ("2026-08-23T00:00:00+00:00", "max", 82.0),
            ("2026-08-23T12:00:00+00:00", "min", 69.0),
            ("2026-08-24T00:00:00+00:00", "max", 83.0),
            ("2026-08-24T12:00:00+00:00", "min", 70.0)])
        self.assertEqual(len(p["rows"]), 23)
        self.assertEqual(p["rows"][0]["time"], dt.datetime(2026, 8, 22, 3, tzinfo=U))   # 03 < 23: next day
        self.assertEqual(p["rows"][0]["temp_f"], 73.0)
        self.assertEqual(p["rows"][-1]["time"], dt.datetime(2026, 8, 24, 21, tzinfo=U))

    def test_mav_nx_and_six_hourly_tail(self):
        p = gw.parse_mav_block(MAV_BLOCK)
        self.assertEqual(p["cycle"], dt.datetime(2026, 8, 21, 18, tzinfo=U))
        ex = [(e["time"].day, e["time"].hour, e["kind"], e["temp_f"]) for e in p["extremes"]]
        self.assertEqual(ex, [(22, 12, "min", 69.0), (23, 0, "max", 81.0), (23, 12, "min", 68.0),
                              (24, 0, "max", 81.0), (24, 12, "min", 68.0)])
        times = [(r["time"].day, r["time"].hour) for r in p["rows"]]
        self.assertEqual(times[:3], [(22, 0), (22, 3), (22, 6)])
        self.assertEqual(times[-3:], [(24, 6), (24, 12), (24, 18)])      # 6-hourly tail keeps its day
        self.assertEqual(p["rows"][-1]["temp_f"], 80.0)

    def test_extremes_land_on_local_days(self):
        p = gw.parse_nbs_block(NBS_BLOCK)
        byday = snapshots._extremes_by_day(p, LA)
        # 00Z Aug 23 is 17:00 PDT Aug 22: the daytime max of the 22nd
        self.assertEqual(byday["2026-08-22"], {"min": 69.0, "max": 82.0})
        self.assertEqual(byday["2026-08-23"], {"min": 69.0, "max": 83.0})
        self.assertEqual(byday["2026-08-24"], {"min": 70.0})


class DayBucketing(unittest.TestCase):
    def test_markers_use_iana_zone(self):
        city = {"station": "KLAX", "city": "Los Angeles", "lat": 33.9381, "lon": -118.3889,
                "tz": "America/Los_Angeles", "unit": "F"}
        mk = snapshots.day_markers(city, dt.datetime(2026, 8, 22, 2, 30, tzinfo=U))   # 19:30 PDT Aug 21
        self.assertEqual(mk["day"], "2026-08-21")
        self.assertEqual(mk["dayStart"], "2026-08-21T07:00:00Z")
        self.assertEqual(mk["dayEnd"], "2026-08-22T07:00:00Z")
        self.assertEqual(mk["winStart"], "2026-08-20T19:00:00Z")
        self.assertEqual(mk["listed"], "2026-08-20T16:00:00Z")      # noon Eastern the day before
        self.assertEqual(mk["tzOffset"], -7.0)
        self.assertIsNotNone(mk["sunrise"])
        sr, ss = snapshots._parse_iso(mk["sunrise"]), snapshots._parse_iso(mk["sunset"])
        self.assertLess(abs((sr - dt.datetime(2026, 8, 21, 13, 20, tzinfo=U)).total_seconds()), 20 * 60)
        self.assertLess(abs((ss - dt.datetime(2026, 8, 22, 2, 35, tzinfo=U)).total_seconds()), 20 * 60)

    def test_official_high_low_assignment(self):
        daily = [
            {"start": "2026-08-21T13:00:00Z", "end": "2026-08-22T01:00:00Z", "isDay": True, "tempF": 79.0},
            {"start": "2026-08-22T01:00:00Z", "end": "2026-08-22T13:00:00Z", "isDay": False, "tempF": 69.0},
            {"start": "2026-08-22T13:00:00Z", "end": "2026-08-23T01:00:00Z", "isDay": True, "tempF": 80.0},
        ]
        hi, lo = snapshots._official_hi_lo(daily, LA)
        self.assertEqual(hi, {"2026-08-21": 79.0, "2026-08-22": 80.0})
        self.assertEqual(lo, {"2026-08-22": 69.0})           # the night ENDING on the 22nd

    def test_obs_decode_and_extremes(self):
        raw = [
            {"icaoId": "KLAX", "obsTime": 1787349180, "temp": 26.1, "metarType": "METAR", "temp_source": "tgroup"},   # 21:53Z
            {"icaoId": "KLAX", "obsTime": 1787352780, "temp": 25.0, "metarType": "SPECI", "temp_source": "body"},     # 22:53Z
            {"icaoId": "KLAX", "obsTime": 1787320380, "temp": 18.9, "metarType": "METAR", "temp_source": "tgroup"},   # 13:53Z
            {"icaoId": "KLAX", "obsTime": None, "temp": 99},
        ]
        rows = snapshots.decode_rows(raw, LA)
        self.assertEqual([r["tempF"] for r in rows], [66.0, 79.0, 77.0])
        self.assertEqual(rows[0]["src"], "tgroup")
        ex = snapshots.day_extremes(rows, LA, "2026-08-21", "F")
        self.assertEqual((ex["high"]["v"], ex["low"]["v"], ex["n"]), (79.0, 66.0, 3))
        old = gw.INCLUDE_SPECI
        try:
            gw.INCLUDE_SPECI = False
            self.assertEqual(len(snapshots.decode_rows(raw, LA)), 2)
        finally:
            gw.INCLUDE_SPECI = old


class AsIssued(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = storage.LocalStorage(self.tmp.name)
        for stamp in ("20260820T195218Z", "20260821T025900Z", "20260821T120000Z"):
            self.st.put(f"archive/KLAX/hourly_{stamp}.json.gz", b"x")

    def tearDown(self):
        self.tmp.cleanup()

    def test_latest_at_or_before_cutoff(self):
        key, pre = snapshots._as_issued_key(self.st, "KLAX", "hourly", "20260821T070000Z")
        self.assertEqual((snapshots._stamp_of(key), pre), ("20260821T025900Z", True))

    def test_earliest_when_nothing_pre_day(self):
        key, pre = snapshots._as_issued_key(self.st, "KLAX", "hourly", "20260819T070000Z")
        self.assertEqual((snapshots._stamp_of(key), pre), ("20260820T195218Z", False))

    def test_missing(self):
        self.assertEqual(snapshots._as_issued_key(self.st, "KPHX", "hourly", "20260821T070000Z"), (None, False))

    def test_bulletin_cycle_at_exactly_midnight_counts_as_pre_day(self):
        # NBM stamps carry minutes only; a 07:00Z cycle against a 07:00:00Z cutoff is pre-day
        for stamp in ("20260821T0600Z", "20260821T0700Z", "20260821T0800Z"):
            self.st.put(f"archive/KLAX/nbh_{stamp}.txt.gz", b"x")
        key, pre = snapshots._as_issued_key(self.st, "KLAX", "nbh", "20260821T070000Z")
        self.assertEqual((snapshots._stamp_of(key), pre), ("20260821T0700Z", True))


class FieldAndRoster(unittest.TestCase):
    def test_assets_exist_and_field_is_deterministic(self):
        from pipeline import basemap
        roster = basemap.load_roster()
        self.assertEqual(len(roster), 37)      # the 37 contract stations; Colorado Springs left the board
        klax = next(c for c in roster if c["station"] == "KLAX")
        self.assertTrue(0 < klax["px"] < 200 and 300 < klax["py"] < 600)   # left, lower half of the canvas
        grid = basemap.load_field_grid()
        self.assertGreater(len(grid["cells"]), 1500)
        pts = [{**c, "hi": 80.0 + i, "lo": 60.0} for i, c in enumerate(roster)]
        f1 = basemap.idw_field(grid, pts, "hi", "lo")
        f2 = basemap.idw_field(grid, pts, "hi", "lo")
        self.assertEqual(f1, f2)
        self.assertEqual(len(f1["cells"]), len(grid["cells"]))
        self.assertTrue(all(60 <= c[2] <= 120 for c in f1["cells"]))


if __name__ == "__main__":
    unittest.main()
