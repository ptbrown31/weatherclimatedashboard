"""The sea surface temperature anomaly lane: ERDDAP's CSV becomes a compact
grid per basin, a pass that finds no newer day fetches nothing, and a basin
that fails keeps its previous grid. No network."""
import datetime as dt
import json
import os
import sys
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
