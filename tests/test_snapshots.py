"""
Tests for the daily-extreme parsers, day bucketing and the snapshot builders.
Fixtures are cut from the 2026-08-21 bulletins. No network.
"""
import datetime as dt
import io
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
        # 10:30 am Eastern the day before, the schedule measured since the
        # 8/28 target (the exchange moved from noon on 2026-08-27)
        self.assertEqual(mk["listed"], "2026-08-20T14:30:00Z")
        # the chart's window opens when the board does. A Pacific city lists at
        # half past seven its own time, hours before the noon this used to use.
        self.assertEqual(mk["winStart"], mk["listed"])
        self.assertEqual(mk["tzOffset"], -7.0)
        self.assertIsNotNone(mk["sunrise"])
        sr, ss = snapshots._parse_iso(mk["sunrise"]), snapshots._parse_iso(mk["sunset"])
        self.assertLess(abs((sr - dt.datetime(2026, 8, 21, 13, 20, tzinfo=U)).total_seconds()), 20 * 60)
        self.assertLess(abs((ss - dt.datetime(2026, 8, 22, 2, 35, tzinfo=U)).total_seconds()), 20 * 60)

    def test_a_board_listing_after_noon_leaves_the_window_where_it_was(self):
        # the European pair list at 22:40Z the evening before, which is after
        # the window has already opened, so the window is not dragged forward
        city = {"station": "EDDF", "city": "Frankfurt", "lat": 50.03, "lon": 8.56,
                "tz": "Europe/Berlin", "unit": "C"}
        mk = snapshots.day_markers(city, dt.datetime(2026, 8, 21, 17, 30, tzinfo=U))
        self.assertEqual(mk["listed"], "2026-08-20T22:40:00Z")
        self.assertLess(mk["winStart"], mk["listed"])

    def test_listing_markers_per_region(self):
        # the three schedules the capture measures: US and Canada on the
        # Eastern clock, Europe on its own late-evening cycle, others unknown
        mk = lambda st, tz, unit: snapshots.day_markers(
            {"station": st, "city": st, "lat": 45.0, "lon": -73.0, "tz": tz, "unit": unit},
            dt.datetime(2026, 9, 2, 15, 0, tzinfo=U))
        self.assertEqual(mk("CYUL", "America/Toronto", "C")["listed"], "2026-09-01T15:10:00Z")
        self.assertEqual(mk("EDDF", "Europe/Berlin", "C")["listed"], "2026-09-01T22:40:00Z")
        self.assertNotIn("listed", mk("RJTT", "Asia/Tokyo", "C"))

    def test_official_high_low_assignment(self):
        daily = [
            {"start": "2026-08-21T13:00:00Z", "end": "2026-08-22T01:00:00Z", "isDay": True, "tempF": 79.0},
            {"start": "2026-08-22T01:00:00Z", "end": "2026-08-22T13:00:00Z", "isDay": False, "tempF": 69.0},
            {"start": "2026-08-22T13:00:00Z", "end": "2026-08-23T01:00:00Z", "isDay": True, "tempF": 80.0},
        ]
        hi, lo = snapshots._official_hi_lo(daily, LA)
        self.assertEqual(hi, {"2026-08-21": 79.0, "2026-08-22": 80.0})
        self.assertEqual(lo, {"2026-08-22": 69.0})           # the night ENDING on the 22nd

    def test_running_today_uses_the_record_and_the_hours_ahead(self):
        """KMSP, 2026-08-28. The station bottomed out at 59 before dawn; the
        forecast began at noon and its minimum for the rest of the day was 74.
        A day low of 74 is not reachable once 59 is recorded, and the market's
        implied 59.5 was right."""
        self.assertEqual(snapshots._running_today(74.0, 74.0, 59.0, min), 59.0)
        # KLGA the same week, the other direction. An official overnight period
        # that had already ended still said 64 while the station's actual low
        # was 66.9 and every hour still ahead was warmer.
        self.assertEqual(snapshots._running_today(64.0, 68.0, 66.9, min), 66.9)
        # the rest of the day is forecast colder than the morning managed, so
        # the forecast leads and the record does not bind
        self.assertEqual(snapshots._running_today(88.0, 88.0, 91.0, min), 88.0)
        # highs: the afternoon is still ahead, then it has been and gone
        self.assertEqual(snapshots._running_today(85.0, 85.0, 79.0, max), 85.0)
        self.assertEqual(snapshots._running_today(76.0, 76.0, 85.0, max), 85.0)
        # nothing recorded for the day yet, so the forecast's own figure stands
        self.assertEqual(snapshots._running_today(57.0, 74.0, None, min), 57.0)
        # no hours left in the day, so the record is the whole answer
        self.assertEqual(snapshots._running_today(74.0, None, 59.0, min), 59.0)
        self.assertIsNone(snapshots._running_today(None, None, None, min))

    def test_max_min_ahead_drops_the_hours_already_past(self):
        tz = ZoneInfo("America/Chicago")
        rows = [{"t": "2026-08-28T09:00:00Z", "tempF": 59.0},    # 4am local, the day's low
                {"t": "2026-08-28T18:00:00Z", "tempF": 78.0},
                {"t": "2026-08-29T01:00:00Z", "tempF": 74.0},    # 8pm local, still today
                {"t": "2026-08-29T06:00:00Z", "tempF": 66.0}]    # tomorrow local
        now = snapshots._parse_iso("2026-08-28T17:00:00Z")
        self.assertEqual(snapshots._max_min_ahead(rows, tz, "2026-08-28", now), (78.0, 74.0))
        early = snapshots._parse_iso("2026-08-28T05:00:00Z")
        self.assertEqual(snapshots._max_min_ahead(rows, tz, "2026-08-28", early), (78.0, 59.0))
        late = snapshots._parse_iso("2026-08-29T04:00:00Z")
        self.assertEqual(snapshots._max_min_ahead(rows, tz, "2026-08-28", late), (None, None))

    def test_only_the_nws_figures_become_the_day_running_extreme(self):
        """The map shades by the NWS figures and measures the market against
        them, so those become the day's running extreme. The other three stay
        each source's own forecast. Folding all four replaced every GFS MOS
        figure on the board with the observation."""
        src = io.open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "pipeline", "snapshots.py"), encoding="utf-8").read()
        self.assertIn('if fc_same and k == "nws":', src)

    def test_a_source_without_a_rest_of_day_figure_is_left_alone(self):
        """The fold rewrites a today figure only where a rest-of-day extreme
        was published. A missing key means the source does not report one,
        which is not the same as a day with no hours left."""
        def fold(src, row, k):
            for fld, obs_fld, rest_fld, pick in (("HighToday", "obsHighSoFar", "highRest", max),
                                                 ("LowToday", "obsLowSoFar", "lowRest", min)):
                if rest_fld not in src:
                    continue
                seen = row[obs_fld]
                row[f"{k}{fld}"] = snapshots._running_today(row[f"{k}{fld}"], src.get(rest_fld), seen, pick)
            return row

        row = {"mavHighToday": 81.0, "mavLowToday": 57.0, "obsHighSoFar": 79.0, "obsLowSoFar": 59.0}
        # no rest keys at all: the source's own figures survive untouched
        self.assertEqual(fold({}, dict(row), "mav"),
                         {"mavHighToday": 81.0, "mavLowToday": 57.0, "obsHighSoFar": 79.0, "obsLowSoFar": 59.0})
        # keys present but null: the day has no hours left, so the record answers
        out = fold({"highRest": None, "lowRest": None}, dict(row), "mav")
        self.assertEqual((out["mavHighToday"], out["mavLowToday"]), (79.0, 59.0))
        # keys present with values: the record and the hours ahead, as usual
        out = fold({"highRest": 74.0, "lowRest": 74.0}, dict(row), "mav")
        self.assertEqual((out["mavHighToday"], out["mavLowToday"]), (79.0, 59.0))

    def test_every_source_that_folds_publishes_a_rest_of_day_figure(self):
        """Every source that publishes a today figure also publishes a
        rest-of-day one, so a fold extended to another source later cannot
        silently skip it and hand back the bare observation."""
        import re
        src = io.open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "pipeline", "snapshots.py"), encoding="utf-8").read()
        body = src[src.index("def build_forecast_snapshot"):src.index("def forecast_job")]
        for name in ("nws", "nbm", "lamp", "mav"):
            block = body[body.index('# ---- ' + {"nws": "NWS", "nbm": "NBM", "lamp": "LAMP", "mav": "GFS MOS MAV"}[name]):]
            block = block[:block.index("# ----", 8)] if "# ----" in block[8:] else block
            self.assertIn("highRest", block, name + " publishes a today figure without a rest-of-day high")
            self.assertIn("lowRest", block, name + " publishes a today figure without a rest-of-day low")

    def test_obs_today_only_answers_for_the_day_asked(self):
        class FakeStore(object):
            def __init__(self, d):
                self.d = d

            def get(self, k):
                return self.d.get(k)

        body = json.dumps({"today": {"date": "2026-08-28",
                                     "high": {"v": 79.0}, "low": {"v": 59.0}}}).encode()
        st = FakeStore({"snapshots/obs/KMSP.json": body})
        self.assertEqual(snapshots._obs_today(st, "KMSP", "2026-08-28"), {"high": 79.0, "low": 59.0})
        self.assertEqual(snapshots._obs_today(st, "KMSP", "2026-08-27"), {})
        self.assertEqual(snapshots._obs_today(st, "KMSP", None), {})
        self.assertEqual(snapshots._obs_today(FakeStore({}), "KMSP", "2026-08-28"), {})

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


    def test_peak_wind_takes_the_larger_of_both_columns(self):
        """A wind contract settles on the largest value across the sustained and
        the gust column, so the peak comes from whichever is higher, and a day
        with no gust reported is decided by the sustained wind alone."""
        raw = [
            {"icaoId": "KLAX", "obsTime": 1787320380, "temp": 18.9, "metarType": "METAR",   # 13:53Z
             "temp_source": "tgroup", "wspd": 12, "wgst": None},
            {"icaoId": "KLAX", "obsTime": 1787349180, "temp": 26.1, "metarType": "METAR",   # 21:53Z
             "temp_source": "tgroup", "wspd": 19, "wgst": 27},
            {"icaoId": "KLAX", "obsTime": 1787352780, "temp": 25.0, "metarType": "SPECI",   # 22:53Z
             "temp_source": "body", "wspd": 22, "wgst": None},
        ]
        rows = snapshots.decode_rows(raw, LA)
        self.assertEqual([r.get("wgst") for r in rows], [None, 27, None])
        w = snapshots.day_peak_wind(rows, LA, "2026-08-21")
        self.assertEqual((w["peak"]["v"], w["peak"]["from"]), (27, "gust"))
        self.assertEqual((w["speed"]["v"], w["speed"]["from"]), (22, "speed"))
        self.assertEqual(w["gust"]["v"], 27)
        self.assertEqual((w["unit"], w["n"]), ("kt", 3))
        # the same day without any gust: the sustained wind decides it
        nogust = [dict(r, wgst=None) for r in raw]
        w2 = snapshots.day_peak_wind(snapshots.decode_rows(nogust, LA), LA, "2026-08-21")
        self.assertEqual((w2["peak"]["v"], w2["peak"]["from"]), (22, "speed"))
        self.assertIsNone(w2["gust"])
        # a day the station reported no wind at all has no peak rather than a zero
        calm = [{k: v for k, v in r.items() if k not in ("wspd", "wgst")} for r in raw]
        self.assertIsNone(snapshots.day_peak_wind(snapshots.decode_rows(calm, LA), LA, "2026-08-21"))
        self.assertIsNone(snapshots.day_peak_wind(rows, LA, "2026-08-20"))


    def test_wind_units_round_half_up(self):
        """Knots become the whole miles per hour the resolving table shows."""
        self.assertEqual(snapshots.wind_units(19)["mph"], 22)        # 21.86
        self.assertEqual(snapshots.wind_units(19)["mphExact"], 21.9)
        self.assertEqual(snapshots.wind_units(27)["mph"], 31)        # 31.07
        self.assertEqual(snapshots.wind_units(13)["mph"], 15)        # 14.96, half up
        self.assertEqual(snapshots.wind_units(30)["kmh"], 56)        # 55.56
        w = snapshots.day_peak_wind(snapshots.decode_rows([
            {"icaoId": "KLAX", "obsTime": 1787349180, "temp": 26.1, "metarType": "METAR",
             "temp_source": "tgroup", "wspd": 19, "wgst": 27}], LA), LA, "2026-08-21")
        self.assertEqual((w["peak"]["kt"], w["peak"]["mph"]), (27, 31))

    def test_hour_value_window_is_half_open(self):
        """The hour ending at the listed time: the report exactly an hour before
        belongs to the hour before, the report exactly at the time counts, and
        the last report in the window is the one that resolves it."""
        raw = [
            {"icaoId": "KLAX", "obsTime": 1787346000, "temp": 20.0, "metarType": "METAR",    # 21:00Z exactly
             "temp_source": "tgroup"},
            {"icaoId": "KLAX", "obsTime": 1787347980, "temp": 21.4, "metarType": "METAR",    # 21:33Z
             "temp_source": "tgroup"},
            {"icaoId": "KLAX", "obsTime": 1787349180, "temp": 22.6, "metarType": "SPECI",    # 21:53Z
             "temp_source": "body"},
            {"icaoId": "KLAX", "obsTime": 1787349600, "temp": 30.0, "metarType": "METAR",    # 22:00Z exactly
             "temp_source": "tgroup"},
        ]
        rows = snapshots.decode_rows(raw, LA)
        listed = dt.datetime(2026, 8, 21, 15, 0, tzinfo=LA)          # 22:00Z
        h = snapshots.hour_value(rows, LA, listed)
        self.assertEqual((h["n"], h["type"]), (3, "METAR"))          # 21:00Z excluded, 22:00Z included
        self.assertEqual((h["v"], h["whole"]), (86.0, 86))
        earlier = snapshots.hour_value(rows, LA, listed - dt.timedelta(hours=1))
        self.assertEqual((earlier["n"], earlier["v"]), (1, 68.0))    # only the 21:00Z report
        self.assertIsNone(snapshots.hour_value(rows, LA, listed + dt.timedelta(hours=3)))

    def test_whole_f_rounds_half_away_from_zero(self):
        self.assertEqual(snapshots.whole_f(86.5), 87)
        self.assertEqual(snapshots.whole_f(86.4), 86)
        self.assertEqual(snapshots.whole_f(-3.5), -4)
        self.assertIsNone(snapshots.whole_f(None))


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
        # the roster, which is no longer only the contract stations. Colorado
        # Springs left the board; Nantucket, Martha's Vineyard and Providence
        # joined on 24 September when the exchange listed a wind contract at
        # each, with no temperature one; the six New Jersey and Delaware shore
        # stations joined on 25 September carrying no contract at all, for
        # their observations ahead of the nor'easter
        self.assertEqual(len(roster), 49)
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


class WxElements(unittest.TestCase):
    """The upstream-variable parsing behind the advanced panels."""

    LAMP = """\
 KSFO   GFS LAMP GUIDANCE   8/23/2026  1030 UTC
 UTC  11 12 13
 TMP  57 57 56
 DPT  55 55 54
 CLD  BK BK OV
 WDR  30 30 30
 WSP  11 10  8
"""

    def test_lamp_elements_with_categorical_cover(self):
        p = gw.parse_wx_block(self.LAMP, "lamp")
        r = p["rows"][0]
        self.assertEqual(r["dew_f"], 55.0)
        self.assertEqual(r["cover"], "BK")
        self.assertEqual(r["sky_pct"], 75)          # the band midpoint for broken
        self.assertEqual(r["wdir"], 300)            # published in tens of degrees
        self.assertEqual(r["wspd"], 11.0)
        self.assertEqual(p["rows"][2]["cover"], "OV")
        self.assertEqual(p["rows"][2]["sky_pct"], 100)

    def test_nbm_sky_is_percent_and_cover_is_none(self):
        block = ("KSFO    NBM V4.1 NBH GUIDANCE    8/23/2026  1000 UTC\n"
                 " UTC  11 12 13\n"
                 " TMP  57 57 56\n"
                 " DPT  55 55 55\n"
                 " SKY  64 64 69\n"
                 " WDR  27 26 26\n"
                 " WSP   6  6  5\n")
        p = gw.parse_wx_block(block, "nbh")
        r = p["rows"][0]
        self.assertEqual(r["sky_pct"], 64)
        self.assertIsNone(r["cover"])
        self.assertEqual(r["wdir"], 270)

    def test_a_blank_cell_is_a_gap_not_a_shift(self):
        block = self.LAMP.replace(" DPT  55 55 54", " DPT  55    54")
        p = gw.parse_wx_block(block, "lamp")
        self.assertEqual([r["dew_f"] for r in p["rows"]], [55.0, None, 54.0])

    def test_nws_hourly_conversions(self):
        # wind arrives as words and mph, sky as a phrase; each converts once
        self.assertEqual(gw.compass_deg("WSW"), 247.5)
        self.assertIsNone(gw.compass_deg(""))
        self.assertEqual(gw.mph_to_kt("8 mph"), 7.0)
        self.assertEqual(gw.mph_to_kt("5 to 10 mph"), 6.5)   # a range becomes its midpoint
        self.assertIsNone(gw.mph_to_kt(None))
        self.assertEqual(gw.short_forecast_sky("Mostly Sunny"), 19)
        self.assertEqual(gw.short_forecast_sky("Sunny"), 0)
        self.assertEqual(gw.short_forecast_sky("Partly Cloudy then Patchy Fog"), 44)
        self.assertEqual(gw.short_forecast_sky("Mostly Cloudy"), 75)
        self.assertIsNone(gw.short_forecast_sky("Slight Chance Rain Showers"))


class ForecastWind(unittest.TestCase):
    """The wind contracts settle on the larger of the sustained wind and the
    gust, so the forecast trace has to carry both. The bulletin families' gusts
    were parsed but never reached the snapshot, because the hourly trace is
    built by a temperature-only parser."""

    BULLETIN = "\n".join([
        " KBOS   NBM V5.0 NBH GUIDANCE    8/18/2026  1800 UTC",
        " UTC  19 20 21 22 23 00 01 02 03 04 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19 ",
        " TMP  74 74 73 73 72 71 70 70 70 70 70 69 69 69 69 68 68 71 73 76 77 79 80 81 82",
        " DPT  67 67 67 66 65 65 66 66 67 67 67 67 67 67 67 67 67 67 67 67 66 64 64 63 62",
        " SKY  27 30 30 21 19 15  9 21 27 33 35 42 50 47 55 62 67 47 27 19 20 20 30 24 13",
        " WDR   9  9  9  9 10 11 13 14 15 15 15 17 19 21 24 24 25 25 22 20 18 19 19 21 21",
        " WSP   9  9  9  8  7  7  6  5  5  4  3  3  2  2  3  3  4  4  3  4  4  6  7  8  9",
        " GST  20 19 18 17 16 15 14 13 12 11 10  9  8  7  8  9 10 11 12 13 14 15 16 17 18",
        " GSD   3  3  3  3  3  3  3  3  3  3  3  3  3  3  3  3  3  3  3  3  3  3  3  3  3",
    ])

    def test_the_wind_columns_reach_the_hourly_trace(self):
        from pipeline import snapshots as sn, gov_weather as gw
        rows = sn._with_wind(sn._hourly_rows(gw.parse_hourly_block(self.BULLETIN), "F"),
                             self.BULLETIN, "nbh")
        self.assertEqual(len(rows), 25)
        self.assertTrue(all("gust" in r for r in rows))
        self.assertEqual(rows[0]["gust"], 20.0)
        self.assertEqual(rows[0]["wspd"], 9.0)
        self.assertEqual(rows[0]["gustSd"], 3.0)
        self.assertEqual(rows[0]["tempF"], 74.0)      # the temperature is untouched

    def test_a_bulletin_the_element_parser_cannot_read_leaves_the_trace_alone(self):
        from pipeline import snapshots as sn, gov_weather as gw
        rows = sn._with_wind(sn._hourly_rows(gw.parse_hourly_block(self.BULLETIN), "F"), "not a bulletin", "nbh")
        self.assertEqual(len(rows), 25)
        self.assertTrue(all("gust" not in r for r in rows))
        self.assertEqual(rows[0]["tempF"], 74.0)

    def test_the_forecast_offices_hourly_carries_wind_in_knots(self):
        from pipeline import snapshots as sn
        body = {"properties": {"periods": [
            {"startTime": "2026-09-24T12:00:00+00:00", "temperature": 70, "temperatureUnit": "F",
             "windSpeed": "23 mph", "windGust": "41 mph"},
            {"startTime": "2026-09-24T13:00:00+00:00", "temperature": 71, "temperatureUnit": "F",
             "windSpeed": "8 mph"}]}}
        rows = sn._nws_hourly_rows(body)
        self.assertEqual(rows[0]["gust"], 35.6)       # 41 mph is 35.6 kt
        self.assertEqual(rows[0]["wspd"], 20.0)
        self.assertNotIn("gust", rows[1])             # no gust forecast is not a zero
