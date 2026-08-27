"""Tests added after the second review: per-extreme as-issued levels,
bounded archive listings, per-station observation watermarks, provenance in
the observation extremes, the chain deadline budget, the NHC season filter,
and the hurricane job's reuse of unchanged geometry. No network."""
import datetime as dt
import gzip
import json
import os
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import snapshots, storage, archive, hurricane, gov_weather as gw   # noqa: E402

U = dt.timezone.utc
LA = ZoneInfo("America/Los_Angeles")
CITY = {"station": "KLAX", "city": "Los Angeles", "lat": 33.9381, "lon": -118.3889, "tz": "America/Los_Angeles", "unit": "F"}

MAV_18Z = """\
 KLAX   GFS MOS GUIDANCE    8/20/2026  1800 UTC
 DT /AUG  21                  /AUG  22                /AUG  23
 HR   00 03 06 09 12 15 18 21 00 03 06 09 12 15 18 21 00 03 06 12 18
 N/X              69          81          68          81       68
 TMP  77 72 71 71 70 72 78 79 76 72 72 70 70 72 78 78 76 72 71 69 80
"""
MAV_06Z = """\
 KLAX   GFS MOS GUIDANCE    8/21/2026  0600 UTC
 DT /AUG  21                  /AUG  22                /AUG  23
 HR   12 15 18 21 00 03 06 09 12 15 18 21 00 03 06 09 12 15 18 00 06
 X/N              83          68          79          68       81
 TMP  70 74 78 78 76 72 71 70 70 73 79 79 77 72 71 70 70 74 79 76 71
"""


def gz(s):
    return gzip.compress(s.encode())


class PerExtremeLevels(unittest.TestCase):
    """The 06Z run carries the day's max (83, under 00Z Aug 22) but not its
    min; the 18Z run the evening before carries the min (69, under 12Z Aug
    21). The level for Aug 21 must come from both."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = storage.LocalStorage(self.tmp.name)
        self.st.put("archive/KLAX/mav_20260820T1800Z.txt.gz", gz(MAV_18Z))
        self.st.put("archive/KLAX/mav_20260821T0600Z.txt.gz", gz(MAV_06Z))

    def tearDown(self):
        self.tmp.cleanup()

    def test_high_and_low_from_different_cycles(self):
        keys = self.st.list("archive/KLAX/mav_")
        lv = snapshots.pick_levels(self.st, "mav", keys, "20260821T070000Z", CITY, LA, "2026-08-21")
        self.assertEqual((lv["highToday"], lv["levelCycleHigh"]), (83.0, "20260821T0600Z"))
        self.assertEqual((lv["lowToday"], lv["levelCycleLow"]), (69.0, "20260820T1800Z"))
        self.assertTrue(lv["levelPreDay"])

    def test_lead_limit_excludes_old_cycles(self):
        keys = self.st.list("archive/KLAX/mav_")
        lv = snapshots.pick_levels(self.st, "mav", keys, "20260821T070000Z", CITY, LA, "2026-08-21", max_lead_h=6)
        self.assertEqual(lv["highToday"], 83.0)
        self.assertIsNone(lv["lowToday"])            # the 18Z run is 13 h before midnight

    def test_post_day_fallback_is_flagged(self):
        keys = self.st.list("archive/KLAX/mav_")
        lv = snapshots.pick_levels(self.st, "mav", keys, "20260819T070000Z", CITY, LA, "2026-08-21", max_lead_h=None)
        self.assertFalse(lv["levelPreDay"])
        lv2 = snapshots.pick_levels(self.st, "mav", keys, "20260819T070000Z", CITY, LA, "2026-08-21")
        self.assertIsNone(lv2["highToday"])


class BoundedListing(unittest.TestCase):
    def test_list_recent_and_start_after(self):
        tmp = tempfile.TemporaryDirectory()
        st = storage.LocalStorage(tmp.name)
        for stamp in ("20260801T0000Z", "20260819T0000Z", "20260821T2300Z"):
            st.put(f"archive/KLAX/nbh_{stamp}.txt.gz", b"x")
        st.put("archive/KLAX/nbs_20260821T2300Z.txt.gz", b"x")
        st.put("archive/KLGA/nbh_20260821T2300Z.txt.gz", b"x")
        now = dt.datetime(2026, 8, 22, 0, 30, tzinfo=U)
        recent = snapshots.list_recent(st, "KLAX", "nbh", now, 72)
        self.assertEqual([snapshots._stamp_of(k) for k in recent], ["20260819T0000Z", "20260821T2300Z"])
        self.assertEqual(st.list("archive/KLAX/nbh_", start_after="archive/KLAX/nbh_20260821T0000Z"),
                         ["archive/KLAX/nbh_20260821T2300Z.txt.gz"])
        self.assertEqual(snapshots._kind_stamp("hourly", now), "20260822T003000Z")
        self.assertEqual(snapshots._kind_stamp("lamp", now), "20260822T0030Z")
        tmp.cleanup()


class Watermarks(unittest.TestCase):
    def test_per_station(self):
        tmp = tempfile.TemporaryDirectory()
        st = storage.LocalStorage(tmp.name)
        now = dt.datetime(2026, 8, 22, 6, 0, tzinfo=U)
        day = {"rows": {"KLAX|1787349180": {"icaoId": "KLAX", "obsTime": 1787349180},     # 21:53Z 8/21
                        "KLGA|1787300000": {"icaoId": "KLGA", "obsTime": 1787300000}}}   # 08:13Z 8/21
        st.put("archive/obs/20260821.json.gz", gzip.compress(json.dumps(day).encode()))
        wms = archive.obs_watermarks(st, now)
        self.assertEqual(wms["KLAX"], dt.datetime(2026, 8, 21, 21, 53, tzinfo=U))
        self.assertNotIn("KPHX", wms)
        self.assertEqual(archive.obs_watermark(st, now), wms["KLAX"])
        tmp.cleanup()


class ObsProvenance(unittest.TestCase):
    def test_extremes_carry_type_and_src(self):
        raw = [{"icaoId": "KLAX", "obsTime": 1787349180, "temp": 26.1, "metarType": "METAR", "temp_source": "tgroup"},
               {"icaoId": "KLAX", "obsTime": 1787352780, "temp": 27.0, "metarType": "SPECI", "temp_source": "body"}]
        rows = snapshots.decode_rows(raw, LA)
        ex = snapshots.day_extremes(rows, LA, "2026-08-21", "F")
        self.assertEqual((ex["high"]["v"], ex["high"]["type"], ex["high"]["src"]), (80.6, "SPECI", "body"))
        self.assertEqual(ex["low"]["src"], "tgroup")


class Budget(unittest.TestCase):
    def test_remaining_budget(self):
        self.assertIsNone(archive.remaining_budget({}))
        self.assertEqual(archive.remaining_budget({"pass_budget_seconds": 600}, reserve=240), 360)
        self.assertEqual(archive.remaining_budget({"pass_budget_seconds": 100}, reserve=240), 30)
        import time
        cfg = {"_deadline_end": time.time() + 500}
        self.assertLess(abs(archive.remaining_budget(cfg, reserve=100) - 400), 2)


class SeasonFilter(unittest.TestCase):
    def test_named_requires_cyclone_type(self):
        calls = {}
        lines = ["AL, 06, 2026081012,   , BEST,   0, 300N,  600W,  35, 1008, LO,  34, NEQ, 0, 0, 0, 0, 1010, 80, 60, 40, 0, L, 0, , 0, 0, SIX, S, 0",
                 "AL, 06, 2026081018,   , BEST,   0, 305N,  610W,  30, 1009, TD,   0,    , 0, 0, 0, 0, 1010, 80, 60, 40, 0, L, 0, , 0, 0, SIX, S, 0"]
        orig = gw._get_text
        gw._get_text = lambda url, timeout=60: ('<a href="bal062026.dat">bal062026.dat</a>' if url.endswith("/btk/") else "\n".join(lines))
        try:
            s = hurricane.season_counts(2026)
        finally:
            gw._get_text = orig
        self.assertEqual((s["named"], s["hurricanes"], s["names"]), (0, 0, []))   # 35 kt as LO does not count


class HurricaneReuse(unittest.TestCase):
    def test_unchanged_storm_reuses_geometry(self):
        tmp = tempfile.TemporaryDirectory()
        st = storage.LocalStorage(tmp.name)
        # fresh counts, so the pass reuses them instead of reaching for the best
        # tracks; without a stamp inside the window this test went to the network
        fresh = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        prev = {"asof": "2026-08-22T00:00:00Z", "storms": [{"id": "cp012026", "advisory": "038", "updated": "2026-08-21T21:00:00.000Z",
                "geometryAdvisory": "38", "cone": [[[0, 0]]], "track": [], "past": [], "points": [], "geometryFetched": "x"}],
                "outlook": [], "season": {"computed": fresh[:10], "computedAt": fresh, "named": 3, "names": ["Arthur"],
                           "events": {"named": [], "hurricanes": [], "majors": []}}}
        st.put("snapshots/hurricane.json", json.dumps(prev).encode())
        roster = [{"id": "cp012026", "binNumber": "CP2", "name": "Lala", "classification": "HU", "intensity": "80",
                   "lastUpdate": "2026-08-21T21:00:00.000Z", "publicAdvisory": {"advNum": "038", "url": "u"}}]
        fetched = []
        o1, o2, o3 = gw.fetch_current_storms, gw.fetch_nhc_layer, gw.reset_nhc_layers
        gw.fetch_current_storms = lambda: roster
        gw.fetch_nhc_layer = lambda name: fetched.append(name) or []
        gw.reset_nhc_layers = lambda: None
        try:
            rc = hurricane.hurricane_pass({"user_agent": "t"}, st)
        finally:
            gw.fetch_current_storms, gw.fetch_nhc_layer, gw.reset_nhc_layers = o1, o2, o3
        snap = json.loads(st.get("snapshots/hurricane.json"))
        self.assertEqual(rc, 0)
        self.assertEqual(snap["reused"], 1)
        self.assertEqual(snap["storms"][0]["cone"], [[[0, 0]]])
        self.assertEqual(fetched, ["Seven-Day: Potential Development Region"])   # only the outlook was fetched
        self.assertEqual(snap["season"]["named"], 3)                               # counted recently: reused
        tmp.cleanup()


class SeasonFreshness(unittest.TestCase):
    """When the season-to-date counts have to be worked out again.

    They were recomputed once a UTC day, so a storm named after that pass left
    the roster saying "Dolly, tropical storm" above a counter that had never
    heard of it. The count contracts settle on that counter.
    """
    def setUp(self):
        self.now = dt.datetime(2026, 8, 27, 17, 42, tzinfo=dt.timezone.utc)

    def at(self, hours):
        t = self.now - dt.timedelta(hours=hours)
        return t.isoformat(timespec="seconds").replace("+00:00", "Z")

    def season(self, hours, names=("Arthur", "Bertha", "Cristobal")):
        return {"named": len(names), "names": list(names), "computedAt": self.at(hours),
                "events": {"named": [], "hurricanes": [], "majors": []}}

    def storm(self, name, cls="TS", sid="al042026"):
        return {"id": sid, "name": name, "classification": cls, "basin": sid[:2].upper()}

    def test_recent_counts_stand(self):
        self.assertTrue(hurricane.season_fresh(self.season(1), [self.storm("Cristobal")], self.now))

    def test_old_counts_are_redone(self):
        self.assertFalse(hurricane.season_fresh(self.season(9), [], self.now))

    def test_a_named_storm_the_counts_have_not_got_to(self):
        # the live case: Dolly named at 12Z, counts last taken at 00Z
        self.assertFalse(hurricane.season_fresh(self.season(1), [self.storm("Dolly")], self.now))

    def test_a_depression_is_not_a_missing_name(self):
        # an unnamed system has no name to be missing, and asking again every
        # pass for the days one sits offshore would be a fetch for nothing
        self.assertTrue(hurricane.season_fresh(self.season(1), [self.storm("Four", cls="TD")], self.now))

    def test_other_basins_do_not_move_an_atlantic_count(self):
        self.assertTrue(hurricane.season_fresh(
            self.season(1), [self.storm("Lala", sid="cp012026")], self.now))

    def test_a_block_from_an_older_build_is_redone(self):
        # it counted correctly but carried no formation dates, and the figure
        # beside the count would have fallen back to the once-a-day lane
        stale = self.season(1)
        stale.pop("events")
        self.assertFalse(hurricane.season_fresh(stale, [], self.now))

    def test_no_stamp_means_redo(self):
        self.assertFalse(hurricane.season_fresh(
            {"named": 3, "computed": "2026-08-27", "events": {}}, [], self.now))
        self.assertFalse(hurricane.season_fresh(None, [], self.now))

    def test_a_stamp_from_the_future_is_not_trusted(self):
        self.assertFalse(hurricane.season_fresh(self.season(-5), [], self.now))

    def test_unreadable_stamp_means_redo(self):
        self.assertFalse(hurricane.season_fresh(
            {"names": [], "computedAt": "whenever", "events": {}}, [], self.now))

    def test_counts_carry_the_time_they_were_taken(self):
        # the date alone cannot say whether a storm named at midday is in them
        # a best-track row is read by position: 8 is the wind, 10 the type and
        # 27 the name, so it is built by index rather than by eye
        f = ["" for _ in range(28)]
        f[0], f[1], f[2], f[4] = "AL", "04", "2026082712", "BEST"
        f[6], f[7], f[8], f[10], f[27] = "136N", "387W", "35", "TS", "DOLLY"
        body = ", ".join(f) + ","
        idx = '<a href="bal042026.dat">bal042026.dat</a>'
        pages = {hurricane.ATCF_BTK: idx, hurricane.ATCF_BTK + "bal042026.dat": body}
        orig = hurricane.gw._get_text
        hurricane.gw._get_text = lambda url, **k: pages[url]
        try:
            out = hurricane.season_counts(2026)
        finally:
            hurricane.gw._get_text = orig
        self.assertEqual(out["names"], ["Dolly"])          # the parser title-cases them
        self.assertEqual(out["named"], 1)
        self.assertTrue(out["computedAt"].endswith("Z"))
        self.assertTrue(out["computedAt"].startswith(out["computed"]))
        tail = dt.datetime.fromisoformat(out["computedAt"].replace("Z", "+00:00"))
        self.assertLess(abs((dt.datetime.now(dt.timezone.utc) - tail).total_seconds()), 120)


if __name__ == "__main__":
    unittest.main()
