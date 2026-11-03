"""The NHC advisory lane behind the hurricane page's replay (pipeline/advisories.py)."""
import datetime as dt
import io
import json
import os
import struct
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import advisories, storage      # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
UTC = dt.timezone.utc
with open(os.path.join(FIXTURES, "nhc_al092026_5day_010.zip"), "rb") as _fh:
    PACKAGE = _fh.read()


def _set_field(dbf: bytes, field: str, value: str) -> bytes:
    """A dBASE table with one field set to `value` in every record."""
    n, hlen, rlen = struct.unpack("<IHH", dbf[4:12])
    pos, off = 32, 1
    while dbf[pos] != 0x0D:
        name, width = dbf[pos:pos + 11].split(b"\0")[0].decode(), dbf[pos + 16]
        if name == field:
            break
        off += width
        pos += 32
    else:
        raise KeyError(field)
    out = bytearray(dbf)
    for r in range(n):
        at = hlen + r * rlen + off
        out[at:at + width] = value.encode().ljust(width)[:width]
    return bytes(out)


def package(adv: int, name: str = None) -> bytes:
    """The fixture standing in for another advisory: its number, and the storm's name, changed."""
    src = zipfile.ZipFile(io.BytesIO(PACKAGE))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename.endswith(".dbf"):
                data = _set_field(data, "ADVISNUM", str(adv))
                if name and info.filename.endswith("_lin.dbf"):
                    data = _set_field(data, "STORMNAME", name)
            z.writestr(info.filename, data)
    return buf.getvalue()


class Package(unittest.TestCase):
    def test_an_advisory_reads_as_its_number_times_points_cone_and_track(self):
        a = advisories.parse_package(PACKAGE)
        self.assertEqual((a["adv"], a["issued"], a["t0"], a["name"], a["basin"]),
                         ("010", "2026-10-09T03:00Z", "2026-10-09T00:00Z", "Isaias", "AL"))
        self.assertEqual([p["tau"] for p in a["points"]], [0, 12, 24, 36, 48, 60, 72])
        self.assertEqual(a["points"][0], {"tau": 0, "t": "2026-10-09T00:00Z", "lon": -88.4, "lat": 25.4,
                                          "kt": 90, "gust": 110, "type": "HU"})
        self.assertEqual((a["points"][1]["t"], a["points"][1]["type"]), ("2026-10-09T12:00Z", "MH"))
        self.assertEqual(a["track"], [[[p["lon"], p["lat"]] for p in a["points"]]])

    def test_the_cone_keeps_nhcs_outline_within_two_kilometres_with_a_few_dozen_points(self):
        z = zipfile.ZipFile(io.BytesIO(PACKAGE))
        raw = advisories.shp_shapes(z.read([n for n in z.namelist() if n.endswith("_5day_pgn.shp")][0]))[0][0]
        cone = advisories.parse_package(PACKAGE)["cone"]
        self.assertEqual(len(cone), 1)
        self.assertGreater(len(raw), 1000)
        self.assertLess(len(cone[0]), 100)
        self.assertEqual(cone[0][0], [round(raw[0][0], 2), round(raw[0][1], 2)])

        def off(p, a, b):
            dx, dy = b[0] - a[0], b[1] - a[1]
            t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / ((dx * dx + dy * dy) or 1)))
            return ((p[0] - a[0] - t * dx) ** 2 + (p[1] - a[1] - t * dy) ** 2) ** 0.5
        worst = max(min(off(p, cone[0][i], cone[0][i + 1]) for i in range(len(cone[0]) - 1)) for p in raw)
        # the tolerance, plus the hundredth of a degree the kept points are rounded to
        self.assertLess(worst, advisories.CONE_TOL + 0.01)

    def test_nhcs_local_times_read_as_utc(self):
        self.assertEqual(advisories.issued_time("500 AM AST Mon Aug 31 2026"), dt.datetime(2026, 8, 31, 9, tzinfo=UTC))
        self.assertEqual(advisories.issued_time("1100 PM HST Sat Oct 03 2026"), dt.datetime(2026, 10, 4, 9, tzinfo=UTC))
        self.assertEqual(advisories.issued_time("1200 PM EDT Fri Sep 25 2026"), dt.datetime(2026, 9, 25, 16, tzinfo=UTC))
        self.assertEqual(advisories.issued_time("1200 AM CDT Fri Sep 25 2026"), dt.datetime(2026, 9, 25, 5, tzinfo=UTC))
        self.assertIsNone(advisories.issued_time("1000 PM XYZ Thu Oct 08 2026"))

    def test_a_valid_time_takes_the_month_nearest_its_advisory(self):
        near = dt.datetime(2026, 9, 1, 3, tzinfo=UTC)          # 10 PM CDT on 31 August
        self.assertEqual(advisories.valid_time("01/0000", near), dt.datetime(2026, 9, 1, 0, tzinfo=UTC))
        self.assertEqual(advisories.valid_time("31/1800", near), dt.datetime(2026, 8, 31, 18, tzinfo=UTC))
        self.assertEqual(advisories.valid_time("04/0000", dt.datetime(2026, 10, 30, 3, tzinfo=UTC)),
                         dt.datetime(2026, 11, 4, 0, tzinfo=UTC))

    def test_thinning_drops_points_on_a_straight_run_and_keeps_a_corner(self):
        line = [[x / 10, 0.0] for x in range(11)] + [[1.0, y / 10] for y in range(1, 11)]
        self.assertEqual(advisories.thin(line, 0.02), [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]])

    def test_a_pacific_storm_runs_on_across_the_date_line(self):
        # Nolo's late cones ran to 196.26 and back to 147 east; its positions stayed negative
        self.assertEqual([round(advisories.westward(x, b), 2) for x, b in
                          ((196.26, "CP"), (147.0, "EP"), (-176.7, "CP"), (5.0, "AL"), (-80.0, "AL"))],
                         [-163.74, -213.0, -176.7, 5.0, -80.0])

    def test_a_roster_number_names_its_full_advisory(self):
        self.assertEqual((advisories.full_number("010"), advisories.full_number("010A"), advisories.full_number(None)),
                         (10, 10, None))


class Lane(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = storage.LocalStorage(self.tmp.name)
        self.now = dt.datetime(2026, 10, 9, 4, 0, tzinfo=UTC)
        self.asked = []
        self.posted = {}                     # (id, adv) -> package
        self.listed = {}                     # id -> the numbers NHC's archive lists

    def fetch(self, sid, adv):
        self.asked.append((sid, adv))
        return self.posted.get((sid, adv))

    def roster(self, *storms):
        self.st.put("snapshots/hurricane.json", json.dumps({"storms": list(storms)}).encode())

    def run_job(self, at=None, reask=None):
        if reask is not None:
            self.st.put("snapshots/reask.json", json.dumps({"storms": reask}).encode())
        return advisories.advisories_job(self.st, at or self.now, fetch=self.fetch,
                                         listing=lambda sid: self.listed.get(sid, []),
                                         year_index=lambda yr: {"al092026": "Hurricane ISAIAS", "al052026": "Tropical Storm EDOUARD"})

    def doc(self, sid):
        return json.loads(self.st.get(advisories.storm_key(sid)))

    def index(self):
        return json.loads(self.st.get(advisories.INDEX))["storms"]

    def test_a_roster_storm_keeps_every_full_advisory_and_its_names(self):
        for n, nm in ((1, "Nine"), (2, "Nine"), (3, "Isaias")):
            self.posted[("al092026", "%03d" % n)] = package(n, nm)
        self.roster({"id": "al092026", "name": "Isaias", "advisory": "003A"})
        out = self.run_job()
        self.assertEqual((out["added"], out["errors"]), (3, []))
        d = self.doc("al092026")
        self.assertEqual([a["adv"] for a in d["advisories"]], ["001", "002", "003"])
        self.assertEqual((d["names"], d["name"], d["basin"], d["year"]), (["Nine", "Isaias"], "Isaias", "AL", 2026))
        e = self.index()["al092026"]
        self.assertEqual((e["n"], e["names"], e["seen"], e["complete"]), (3, ["Nine", "Isaias"], "2026-10-09T04:00:00Z", False))
        # the next pass asks for nothing it already holds
        self.asked.clear()
        self.assertEqual(self.run_job()["added"], 0)
        self.assertEqual(self.asked, [])

    def test_an_advisory_not_yet_posted_is_asked_for_again_for_a_day_then_left(self):
        self.posted[("al092026", "001")] = package(1)
        self.roster({"id": "al092026", "name": "Isaias", "advisory": "002"})
        self.run_job()
        self.assertEqual(self.doc("al092026")["missing"], {"002": "2026-10-09T04:00:00Z"})
        self.asked.clear()
        self.run_job(at=self.now + dt.timedelta(hours=6))
        self.assertEqual(self.asked, [("al092026", "002")])
        self.asked.clear()
        self.run_job(at=self.now + dt.timedelta(hours=25))
        self.assertEqual(self.asked, [])
        # one posted late but inside the day is kept, and no longer counted missing
        self.posted[("al092026", "002")] = package(2)
        self.st.delete(advisories.storm_key("al092026"))
        self.run_job(at=self.now + dt.timedelta(hours=26))
        self.assertEqual(([a["adv"] for a in self.doc("al092026")["advisories"]], self.doc("al092026")["missing"]),
                         (["001", "002"], {}))

    def test_a_storm_the_live_hurricane_lane_carries_is_found_by_name_and_held_whole(self):
        self.roster()
        self.listed["al052026"] = ["001", "002"]
        self.posted.update({("al052026", "001"): package(1, "Five"), ("al052026", "002"): package(2, "Edouard")})
        out = self.run_job(reask=[{"name": "Edouard", "year": 2026}])
        self.assertEqual(out["added"], 2)
        e = self.index()["al052026"]
        self.assertEqual((e["names"], e["n"], e["complete"], e["seen"]), (["Five", "Edouard"], 2, True, None))
        self.asked.clear()
        self.run_job()
        self.assertEqual(self.asked, [])

    def test_a_storm_just_off_the_roster_is_looked_for_in_the_archive_for_two_days(self):
        self.posted[("al092026", "001")] = package(1)
        self.roster({"id": "al092026", "name": "Isaias", "advisory": "001"})
        self.run_job()
        self.roster()
        self.listed["al092026"] = ["001", "002"]
        self.posted[("al092026", "002")] = package(2)
        self.assertEqual(self.run_job(at=self.now + dt.timedelta(hours=7))["added"], 1)
        self.asked.clear()
        self.run_job(at=self.now + dt.timedelta(hours=49))
        self.assertEqual(self.asked, [])

    def test_a_package_that_is_not_the_advisory_asked_for_is_refused(self):
        self.posted[("al092026", "001")] = PACKAGE              # advisory 10's package
        self.roster({"id": "al092026", "name": "Isaias", "advisory": "001"})
        out = self.run_job()
        self.assertEqual(out["added"], 0)
        self.assertIn("asked NHC for advisory 001", out["errors"][0])


if __name__ == "__main__":
    unittest.main()
