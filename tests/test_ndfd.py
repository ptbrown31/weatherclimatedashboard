"""The NDFD lane behind the landing map's shading (pipeline/ndfd.py)."""
import base64
import datetime as dt
import gzip
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import basemap, grib2, ndfd, storage      # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
UTC = dt.timezone.utc


def _fixture() -> bytes:
    with open(os.path.join(FIXTURES, "ndfd_maxt_conus_cut_scan80.grb2"), "rb") as fh:
        return fh.read()


def _doc(issued: str, maxt: dict, mint: dict) -> bytes:
    return gzip.compress(json.dumps({"schema": 1, "issued": issued, "ref": {"maxt": issued, "mint": issued},
                                     "lastModified": {"maxt": issued, "mint": issued},
                                     "maxt": maxt, "mint": mint}).encode())


class Sampling(unittest.TestCase):
    def setUp(self):
        self.keep = ndfd.GRID
        ndfd.GRID = None            # the fixture is a 40 by 30 cut, not the whole CONUS grid

    def tearDown(self):
        ndfd.GRID = self.keep

    def test_each_map_cell_takes_the_grid_point_nearest_its_centre(self):
        msg = _fixture()
        cells = base64.b64decode(ndfd.sample(msg))
        self.assertEqual(len(cells), ndfd.NX * ndfd.NY)
        g = grib2.grid_def(msg)
        field = grib2.decode(msg)
        tr = basemap.Transform.from_json(basemap.load_field_grid()["transform"])
        seen = 0
        for n, byte in enumerate(cells):
            j, i = divmod(n, ndfd.NX)
            lon, lat = tr.unscreen(i * ndfd.STEP + ndfd.STEP / 2, j * ndfd.STEP + ndfd.STEP / 2)
            hit = grib2.nearest_cell(g, lat, lon)
            want = ndfd.to_byte(field[hit[2]]) if hit else 0
            self.assertEqual(byte, want, (i, j))
            seen += byte != 0
        # a few cells of the map fall on the cut, some of them on its missing sea
        self.assertGreater(seen, 3)

    def test_a_byte_is_whole_degrees_fahrenheit_half_up(self):
        self.assertEqual(ndfd.to_byte(None), 0)
        self.assertEqual(ndfd.to_byte(273.15), 32 + ndfd.OFFSET)
        self.assertEqual(ndfd.to_byte(273.15 + 0.5 * 5 / 9), 33 + ndfd.OFFSET)     # 32.5 F rounds up


class Views(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = storage.LocalStorage(self.tmp.name)
        # 16:00 Eastern on 20 October; the day began at 04Z
        self.now = dt.datetime(2026, 10, 20, 20, 0, tzinfo=UTC)

    def tearDown(self):
        self.tmp.cleanup()

    def _put(self, issued: str, maxt: dict, mint: dict):
        stamp = issued.replace("-", "").replace(":", "")
        self.st.put(f"{ndfd.ARCHIVE}conus_{stamp}.json.gz", _doc(issued, maxt, mint))

    def test_today_holds_the_issuance_from_before_midnight_eastern_and_tomorrow_the_newest(self):
        self._put("2026-10-20T03:51:00Z", {"2026-10-21T00:00:00Z": "QQ=="}, {"2026-10-20T12:00:00Z": "Qg=="})
        self._put("2026-10-20T04:51:00Z", {"2026-10-21T00:00:00Z": "Qw=="}, {"2026-10-20T12:00:00Z": "RA=="})
        self._put("2026-10-20T18:51:00Z", {"2026-10-21T00:00:00Z": "RQ==", "2026-10-22T00:00:00Z": "Rg=="},
                  {"2026-10-21T12:00:00Z": "Rw=="})
        v = ndfd.build_views(self.st, self.now, ndfd.archived(self.st))
        self.assertEqual((v["hiT"]["b64"], v["hiT"]["issued"], v["hiT"]["preDay"]), ("QQ==", "2026-10-20T03:51:00Z", True))
        self.assertEqual((v["loT"]["b64"], v["loT"]["end"], v["loT"]["day"]), ("Qg==", "2026-10-20T12:00:00Z", "2026-10-20"))
        self.assertEqual((v["hi"]["b64"], v["hi"]["end"], v["hi"]["day"]), ("Rg==", "2026-10-22T00:00:00Z", "2026-10-21"))
        self.assertEqual((v["lo"]["b64"], v["lo"]["start"]), ("Rw==", "2026-10-21T00:00:00Z"))

    def test_with_nothing_from_before_the_day_the_first_issuance_of_the_day_stands_in(self):
        self._put("2026-10-20T10:51:00Z", {"2026-10-21T00:00:00Z": "SA=="}, {})
        self._put("2026-10-20T11:51:00Z", {"2026-10-21T00:00:00Z": "SQ=="}, {})
        v = ndfd.build_views(self.st, self.now, ndfd.archived(self.st))
        self.assertEqual((v["hiT"]["b64"], v["hiT"]["preDay"]), ("SA==", False))
        # a period the issuance does not carry is no shading, not another period's
        self.assertIsNone(v["loT"]["b64"])

    def test_an_issuance_is_read_once_and_old_ones_are_dropped(self):
        keep = ndfd.GRID
        ndfd.GRID = None
        try:
            self._put("2026-10-16T03:51:00Z", {}, {})          # older than KEEP_DAYS
            msg = _fixture()
            calls = []

            def fetch(u, meta):
                calls.append(u)
                meta["lastModified"] = "2026-10-20T10:51:26Z"
                return msg

            def meta_fetch(u, meta):
                meta["lastModified"] = "2026-10-20T10:51:26Z"
                return b"GRIB"

            out = ndfd.ndfd_job(self.st, self.now, fetch=fetch, meta_fetch=meta_fetch)
            self.assertEqual((out["added"], out["issuances"], len(calls)), (True, 1, 2))
            out = ndfd.ndfd_job(self.st, self.now, fetch=fetch, meta_fetch=meta_fetch)
            self.assertEqual((out["added"], len(calls)), (False, 2))
            self.assertEqual(ndfd.archived(self.st), [ndfd.ARCHIVE + "conus_20261020T105126Z.json.gz"])
            hi_t = json.loads(self.st.get(ndfd.PREFIX + "hiT.json"))
            self.assertEqual((hi_t["day"], hi_t["kind"], hi_t["preDay"]), ("2026-10-20", "maxt", False))
            self.assertEqual(len(base64.b64decode(hi_t["b64"])), ndfd.NX * ndfd.NY)
        finally:
            ndfd.GRID = keep


if __name__ == "__main__":
    unittest.main()
