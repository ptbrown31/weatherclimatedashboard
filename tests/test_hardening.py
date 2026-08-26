"""
Tests added after the first review of the archive job: the X/N MOS label,
both NWS temperature shapes and null periods, bulletin catch-up cycles and
coverage checks, the storage adapter's key checks, and the pass deadline.
No network.
"""
import datetime as dt
import gzip
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import gov_weather as gw          # noqa: E402
from pipeline import archive, storage            # noqa: E402

U = dt.timezone.utc

MAV_06Z = """\
 KLAX   GFS MOS GUIDANCE    8/21/2026  0600 UTC
 DT /AUG  21                  /AUG  22                /AUG  23
 HR   12 15 18 21 00 03 06 09 12 15 18 21 00 03 06 09 12 15 18 00 06
 X/N              81          68          79          68       81
 TMP  70 74 78 78 76 72 71 70 70 73 79 79 77 72 71 70 70 74 79 76 71
"""


class MosLabels(unittest.TestCase):
    def test_x_n_label_is_accepted(self):
        p = gw.parse_mav_block(MAV_06Z)
        ex = [(e["time"].day, e["time"].hour, e["kind"], e["temp_f"]) for e in p["extremes"]]
        self.assertEqual(ex[:3], [(22, 0, "max", 81.0), (22, 12, "min", 68.0), (23, 0, "max", 79.0)])
        self.assertEqual(len(p["rows"]), 21)

    def test_missing_rows_raise(self):
        with self.assertRaises(ValueError):
            gw.parse_hourly_block(" KLAX   NBM V5.0 NBH GUIDANCE    8/21/2026  2200 UTC\n UTC  23 00 01\n")
        with self.assertRaises(ValueError):
            gw.parse_mav_block(" KLAX   GFS MOS GUIDANCE    8/21/2026  1800 UTC\n HR   00 03\n TMP  77 72\n")


class PeriodTemperature(unittest.TestCase):
    def test_scalar_shapes(self):
        self.assertEqual(gw.period_temp_f({"temperature": 78, "temperatureUnit": "F"}), 78.0)
        self.assertEqual(gw.period_temp_f({"temperature": 25, "temperatureUnit": "C"}), 77.0)
        self.assertIsNone(gw.period_temp_f({"temperature": None, "temperatureUnit": "F"}))
        with self.assertRaises(ValueError):
            gw.period_temp_f({"temperature": 78})

    def test_qv_shape_is_celsius(self):
        self.assertEqual(gw.period_temp_f({"temperature": {"unitCode": "wmoUnit:degC", "value": 25}}), 77.0)
        self.assertEqual(gw.period_temp_f({"temperature": {"unitCode": "wmoUnit:degF", "value": 78}}), 78.0)
        self.assertIsNone(gw.period_temp_f({"temperature": {"unitCode": "wmoUnit:degC", "value": None}}))
        with self.assertRaises(ValueError):
            gw.period_temp_f({"temperature": {"unitCode": "wmoUnit:K", "value": 300}})


class BulletinCycles(unittest.TestCase):
    def test_cycles_newest_first(self):
        now = dt.datetime(2026, 8, 22, 4, 30, tzinfo=U)
        nbh = gw.bulletin_cycles("nbh", 3, now)
        self.assertEqual([c.isoformat() for c, _ in nbh],
                         ["2026-08-22T04:00:00+00:00", "2026-08-22T03:00:00+00:00",
                          "2026-08-22T02:00:00+00:00", "2026-08-22T01:00:00+00:00"])
        self.assertTrue(nbh[0][1].endswith("blend.20260822/04/text/blend_nbhtx.t04z"))
        lamp = gw.bulletin_cycles("lamp", 1, now)
        self.assertEqual(lamp[0][0].minute, 30)
        self.assertTrue(lamp[0][1].endswith("lmp.20260822/lmp.t0430z.lavtxt.ascii"))
        mav = gw.bulletin_cycles("mav", 12, now)
        self.assertEqual([c.hour for c, _ in mav], [0, 18, 12])
        self.assertEqual(mav[1][0].day, 21)
        with self.assertRaises(ValueError):
            gw.bulletin_cycles("nope")

    def test_expected_coverage(self):
        # hardcoded on purpose: these are the counts a healthy pass must reach,
        # so a roster change has to be a deliberate edit here rather than a
        # silent drop in coverage
        self.assertEqual(archive.expected_coverage("nbh"), 28)
        self.assertEqual(archive.expected_coverage("lamp"), 25)
        self.assertEqual(archive.expected_coverage("mav"), 25)

    def test_block_ok(self):
        good = (" KLAX   NBM V5.0 NBH GUIDANCE    8/21/2026  2200 UTC\n"
                " UTC  23 00 01 02 03 04 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19 20 21 22 23\n"
                " TMP  78 77 76 74 73 71 71 71 70 70 69 69 69 69 69 70 73 75 78 80 79 79 79 79 78\n")
        self.assertTrue(archive._block_ok("nbh", good))
        self.assertFalse(archive._block_ok("nbh", good.split("\n")[0] + "\n"))
        self.assertTrue(archive._block_ok("mav", MAV_06Z))
        self.assertFalse(archive._block_ok("nbs", MAV_06Z))


class ArchiveWithStubbedNetwork(unittest.TestCase):
    """archive_bulletins against a stubbed fetch: catch-up order, the
    per-pass cap, markers only on full coverage."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = storage.LocalStorage(self.tmp.name)
        self.calls = []
        self._head, self._fetch = gw._head, gw.fetch_bulletin_blocks
        self.now = dt.datetime(2026, 8, 22, 4, 30, tzinfo=U)
        stations = [c[0] for c in archive.CITIES if c[5] == "F"]
        block = lambda sid: (f" {sid}   GFS LAMP GUIDANCE   8/22/2026  0030 UTC\n"
                             " UTC  01 02 03 04 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19 20 21 22 23 00 01\n"
                             " TMP  78 77 76 74 73 71 71 71 70 70 69 69 69 69 69 70 73 75 78 80 79 79 79 79 78\n")
        self.full = {sid: block(sid) for sid in stations}
        self.partial = {sid: block(sid) for sid in stations[:5]}

        def head(url):
            self.calls.append(("head", url))
            return "t0330z" not in url        # one cycle not published

        def fetch(url, sts, marker, timeout=180, tries=2):
            self.calls.append(("fetch", url))
            return (self.partial if "t0130z" in url else self.full), 1000, 2313

        gw._head, gw.fetch_bulletin_blocks = head, fetch

    def tearDown(self):
        gw._head, gw.fetch_bulletin_blocks = self._head, self._fetch
        self.tmp.cleanup()

    def test_catch_up_caps_and_marks(self):
        log = lambda **kw: None
        out = archive.archive_bulletins(self.st, "lamp", [c[0] for c in archive.CITIES], log,
                                        archive.Deadline(None), self.now)
        fetched = [u for k, u in self.calls if k == "fetch"]
        self.assertEqual(out["new"], archive.BULLETIN_MAX_NEW)
        self.assertEqual(out["missing"], 1)                       # the 03:30 run was not there
        self.assertTrue(fetched[0].endswith("t0430z.lavtxt.ascii"))  # newest first
        self.assertTrue(self.st.exists("archive/_done/lamp_20260822T0430Z"))
        self.assertFalse(self.st.exists("archive/_done/lamp_20260822T0130Z"))   # partial: no marker
        self.assertEqual(out["incomplete"], 1)
        self.assertEqual(len(self.st.list("archive/KATL/lamp_")), 4)   # in every fetched bulletin
        self.assertEqual(len(self.st.list("archive/KLAX/lamp_")), 3)   # absent from the partial one
        # a second pass skips the marked cycles and retries the partial one
        self.calls.clear()
        out2 = archive.archive_bulletins(self.st, "lamp", [c[0] for c in archive.CITIES], log,
                                         archive.Deadline(None), self.now)
        self.assertGreaterEqual(out2["skipped"], 3)
        self.assertIn("t0130z", " ".join(u for k, u in self.calls if k == "fetch"))

    def test_deadline_stops_fetching(self):
        out = archive.archive_bulletins(self.st, "lamp", [c[0] for c in archive.CITIES], lambda **kw: None,
                                        archive.Deadline(10), self.now)     # 10 s left < the 90 s floor
        self.assertEqual(out["new"], 0)


class Watermark(unittest.TestCase):
    def test_window_from_record(self):
        tmp = tempfile.TemporaryDirectory()
        st = storage.LocalStorage(tmp.name)
        now = dt.datetime(2026, 8, 22, 6, 0, tzinfo=U)
        self.assertIsNone(archive.obs_watermark(st, now))
        day = {"rows": {"KLAX|1787349180": {"icaoId": "KLAX", "obsTime": 1787349180}}}   # 2026-08-21T21:53Z
        st.put("archive/obs/20260821.json.gz", gzip.compress(json.dumps(day).encode()))
        wm = archive.obs_watermark(st, now)
        self.assertEqual(wm, dt.datetime(2026, 8, 21, 21, 53, tzinfo=U))
        tmp.cleanup()


class StorageKeys(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = storage.LocalStorage(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_sibling_and_root_rejected(self):
        base = os.path.basename(self.tmp.name)
        for bad in ("../" + base + "-sibling/x", "..", "", "a//b", "archive/../x"):
            with self.assertRaises(ValueError, msg=bad):
                self.st.put(bad, b"x")

    def test_zero_length_counts_as_absent(self):
        self.st.put("a/b", b"")
        self.assertFalse(self.st.exists("a/b"))
        self.assertTrue(self.st.put_if_absent("a/b", b"1"))

    def test_list_is_a_prefix_scan(self):
        self.st.put("archive/KLAX/hourly_1.gz", b"1")
        self.st.put("archive/KLGA/hourly_2.gz", b"2")
        self.st.put("archive/_done/lamp_x", b"m")
        self.assertEqual(self.st.list("archive/K"), ["archive/KLAX/hourly_1.gz", "archive/KLGA/hourly_2.gz"])
        self.assertEqual(self.st.list("archive/KLAX/hourly_"), ["archive/KLAX/hourly_1.gz"])
        self.assertEqual(self.st.list("archive/_done/"), ["archive/_done/lamp_x"])


class DeadlineTests(unittest.TestCase):
    def test_deadline(self):
        self.assertFalse(archive.Deadline(None).over(1e9))
        d = archive.Deadline(5)
        self.assertFalse(d.over(1))
        self.assertTrue(d.over(10))


if __name__ == "__main__":
    unittest.main()
