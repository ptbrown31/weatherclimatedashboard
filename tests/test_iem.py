"""Tests for the IEM backfill lane: parsing, day mapping, and precedence.

The risk in a backfill is that it stops being a last resort: values from the
outside archive shadowing what this site captured, or entering unmarked so a
scored row cannot say where its level came from. Both are held down here.
No network; iem.rows is never called.
"""
import datetime as dt
import gzip
import json
import os
import sys
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import iem, snapshots as sn  # noqa: E402

TZ = ZoneInfo("America/Chicago")
CITY = {"station": "KMDW", "unit": "F", "lat": 41.8, "lon": -87.8}


class Extremes(unittest.TestCase):
    DATA = [
        {"ftime": "2026-08-18 18:00", "n_x": None, "tmp": 80},
        {"ftime": "2026-08-19 00:00", "n_x": 84.0, "tmp": 78},   # max of the day ending 00Z
        {"ftime": "2026-08-19 12:00", "n_x": 63.0, "tmp": 65},   # that morning's min
        {"ftime": "2026-08-20 00:00", "n_x": 82.0, "tmp": 76},
    ]

    def test_the_nx_columns_map_by_the_bulletin_convention(self):
        byday = sn._extremes_by_day(iem.extremes(self.DATA), TZ)
        # 00Z on the 19th is 7 pm Central on the 18th: the 18th's max
        self.assertEqual(byday["2026-08-18"]["max"], 84.0)
        # 12Z on the 19th is 7 am Central that day: the 19th's min
        self.assertEqual(byday["2026-08-19"]["min"], 63.0)
        self.assertEqual(byday["2026-08-19"]["max"], 82.0)

    def test_rows_without_a_level_or_a_time_are_passed_over(self):
        out = iem.extremes([{"ftime": None, "n_x": 80.0}, {"ftime": "bad", "n_x": 80.0},
                            {"ftime": "2026-08-19 00:00", "n_x": None}])
        self.assertEqual(out["extremes"], [])


class Hourly(unittest.TestCase):
    def test_lamp_hours_become_the_site_row_shape(self):
        rows = iem.hourly([{"ftime": "2026-08-18 15:00", "tmp": 88},
                           {"ftime": "2026-08-18 16:00", "tmp": None},
                           {"ftime": "2026-08-18 17:00", "tmp": 90}])
        self.assertEqual(rows, [{"t": "2026-08-18T15:00:00Z", "tempF": 88.0},
                                {"t": "2026-08-18T17:00:00Z", "tempF": 90.0}])
        hi, lo = sn._max_min_in_day(rows, TZ, "2026-08-18")
        self.assertEqual((hi, lo), (90.0, 88.0))


class Store:
    def __init__(self, objects):
        self.objects = objects

    def get(self, key):
        return self.objects.get(key)

    def list(self, prefix, start_after=None):
        return sorted(k for k in self.objects if k.startswith(prefix))


def _gz(body: dict) -> bytes:
    return gzip.compress(json.dumps(body).encode())


class Precedence(unittest.TestCase):
    """The backfilled kind is read only where the archived one is empty,
    which pipeline/scorecard.py enforces by trying 'mav' before 'mavx'; here
    the record itself is checked to carry the mark that makes a scored row
    able to say so."""

    def test_a_backfilled_level_reads_and_names_its_source(self):
        store = Store({"archive/KMDW/mavx_20260817T0000Z.json.gz": _gz(
            {"source": "IEM", "days": {"2026-08-17": {"high": 84.0, "low": 63.0}}})})
        lv = sn.pick_levels(store, "mavx", store.list("archive/KMDW/mavx_"),
                            "20260817T0100Z", CITY, TZ, "2026-08-17")
        self.assertEqual((lv["highToday"], lv["lowToday"]), (84.0, 63.0))
        self.assertEqual(lv["backfill"], "IEM")

    def test_a_record_without_a_source_field_is_still_marked(self):
        # the mark must not depend on the writer remembering the field: the
        # kind itself is enough to say the level did not come off a bulletin
        store = Store({"archive/KMDW/mavx_20260817T0000Z.json.gz": _gz(
            {"days": {"2026-08-17": {"high": 84.0, "low": 63.0}}})})
        lv = sn.pick_levels(store, "mavx", store.list("archive/KMDW/mavx_"),
                            "20260817T0100Z", CITY, TZ, "2026-08-17")
        self.assertTrue(lv.get("backfill"))

    MAV = """\
 KMDW   GFS MOS GUIDANCE    8/16/2026  1800 UTC
 DT /AUG  17                  /AUG  18                /AUG  19
 HR   00 03 06 09 12 15 18 21 00 03 06 09 12 15 18 21 00 03 06 12 18
 N/X              63          84          62          83       61
 TMP  77 72 71 71 70 72 78 79 76 72 72 70 70 72 78 78 76 72 71 69 80
"""

    def test_a_native_bulletin_level_carries_no_mark(self):
        store = Store({"archive/KMDW/mav_20260816T1800Z.txt.gz": gzip.compress(self.MAV.encode())})
        lv = sn.pick_levels(store, "mav", store.list("archive/KMDW/mav_"),
                            "20260817T0100Z", CITY, TZ, "2026-08-17")
        self.assertEqual(lv["highToday"], 84.0)
        self.assertNotIn("backfill", lv)


if __name__ == "__main__":
    unittest.main()
