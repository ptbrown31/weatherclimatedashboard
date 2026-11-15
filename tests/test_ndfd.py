"""The NDFD lane behind the landing map's shading (pipeline/ndfd.py)."""
import base64
import datetime as dt
import gzip
import json
import os
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import analysis, basemap, grib2, ndfd, storage      # noqa: E402

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

            ranges = []

            def range_fetch(u, start, end, meta):
                ranges.append(u)
                meta["lastModified"] = "2026-10-20T10:51:26Z"
                return msg[start:end + 1]

            out = ndfd.ndfd_job(self.st, self.now, fetch=fetch, meta_fetch=meta_fetch, range_fetch=range_fetch)
            # the two daily elements read whole, once each, and the hourly temperature by range alone
            self.assertEqual((out["added"], out["issuances"], len(calls)), (True, 1, 2))
            self.assertEqual(sorted(c.rsplit("/", 1)[1] for c in calls), ["ds.maxt.bin", "ds.mint.bin"])
            self.assertTrue(ranges and all(u.endswith("/ds.temp.bin") for u in ranges))
            # a MaxT message where the hourly temperature belongs is counted and left, and no hour is kept
            self.assertEqual((out["hours"]["added"], out["hours"]["read"]["skipped"], out["hours"]["frames"]), (True, 1, 0))
            self.assertNotIn("hoursError", out)
            n = len(ranges)
            out = ndfd.ndfd_job(self.st, self.now, fetch=fetch, meta_fetch=meta_fetch, range_fetch=range_fetch)
            self.assertEqual((out["added"], out["hours"]["added"], len(calls), len(ranges)), (False, False, 2, n))
            self.assertEqual(ndfd.archived(self.st), [ndfd.ARCHIVE + "conus_20261020T105126Z.json.gz"])
            hi_t = json.loads(self.st.get(ndfd.PREFIX + "hiT.json"))
            self.assertEqual((hi_t["day"], hi_t["kind"], hi_t["preDay"]), ("2026-10-20", "maxt", False))
            self.assertEqual(len(base64.b64decode(hi_t["b64"])), ndfd.NX * ndfd.NY)
        finally:
            ndfd.GRID = keep

    def test_a_failed_hourly_part_leaves_the_four_views_written_and_says_why(self):
        keep = ndfd.GRID
        ndfd.GRID = None
        try:
            def fetch(u, meta):
                meta["lastModified"] = "2026-10-20T10:51:26Z"
                return _fixture()

            def meta_fetch(u, meta):
                meta["lastModified"] = "2026-10-20T10:51:26Z"
                return None if u.endswith("ds.temp.bin") else b"GRIB"

            out = ndfd.ndfd_job(self.st, self.now, fetch=fetch, meta_fetch=meta_fetch)
            self.assertEqual(out["hoursError"], "RuntimeError: ds.temp.bin is not on NOAA Open Data")
            self.assertNotIn("hours", out)
            for view in ndfd.VIEWS:
                self.assertIsNotNone(self.st.get(f"{ndfd.PREFIX}{view}.json"), view)
        finally:
            ndfd.GRID = keep

    def test_the_hourly_part_never_fails_the_pass_and_its_own_streak_raises_the_alarm_at_three(self):
        keep = ndfd.ndfd_job
        try:
            seen = []
            for n in range(3):
                ndfd.ndfd_job = lambda store, now, **kw: {"added": False, "hoursError": "RuntimeError: x"}
                seen.append((ndfd.ndfd_pass({}, self.st), ndfd.arch.LAST_STATUS["alarms"], ndfd.arch.LAST_STATUS["errors"]))
            self.assertEqual(seen, [(0, [], 1), (0, [], 1), (0, ["ndfd-temp"], 1)])
            # a pass whose hourly part is read clears the streak
            ndfd.ndfd_job = lambda store, now, **kw: {"added": False, "hours": {}}
            self.assertEqual((ndfd.ndfd_pass({}, self.st), ndfd.arch.LAST_STATUS["alarms"]), (0, []))
            health = json.loads(self.st.get(ndfd.HOUR_HEALTH_KEY))
            self.assertEqual(health["ndfd-temp"]["fail_streak"], 0)
            # the four views failing fails the pass, and leaves the hourly part's streak as it was
            ndfd.ndfd_job = lambda store, now, **kw: {"added": False, "hoursError": "RuntimeError: x"}
            ndfd.ndfd_pass({}, self.st)

            def broken(store, now, **kw):
                raise RuntimeError("the views")
            ndfd.ndfd_job = broken
            self.assertEqual(ndfd.ndfd_pass({}, self.st), 1)
            self.assertEqual(json.loads(self.st.get(ndfd.HOUR_HEALTH_KEY))["ndfd-temp"]["fail_streak"], 1)
            # the pass hands the job a deadline from its budget
            seen = {}
            ndfd.ndfd_job = lambda store, now, **kw: seen.update(kw) or {"added": False, "hours": {}}
            ndfd.ndfd_pass({"pass_budget_seconds": 120}, self.st)
            self.assertIsInstance(seen.get("deadline"), ndfd.arch.Deadline)
            self.assertTrue(100 < seen["deadline"].remaining() <= 120, seen["deadline"].remaining())
        finally:
            ndfd.ndfd_job = keep


def _hrrr() -> bytes:
    with open(os.path.join(FIXTURES, "hrrr_t13z_f06_tmp_2m_subgrid_c3.grb2"), "rb") as fh:
        return fh.read()


def _tmp(ref: dt.datetime, hours: int) -> bytes:
    """The fixture's temperature message with its reference time and forecast hours rewritten."""
    msg = bytearray(_hrrr())
    secs = grib2.sections(bytes(msg))
    o1, o4 = secs[1][0], secs[4][0]
    msg[o1 + 12:o1 + 19] = ref.year.to_bytes(2, "big") + bytes([ref.month, ref.day, ref.hour, ref.minute, ref.second])
    msg[o4 + 18:o4 + 22] = hours.to_bytes(4, "big")
    return bytes(msg)


def _blob(ref: dt.datetime, hours) -> bytes:
    return b"".join(_tmp(ref, n) for n in hours)


def _tmp_min(ref: dt.datetime, minutes: int) -> bytes:
    """A temperature message whose forecast time is in minutes, as ds.temp.bin's are when its reference time falls
    on the half hour."""
    msg = bytearray(_tmp(ref, minutes))
    o4 = grib2.sections(bytes(msg))[4][0]
    msg[o4 + 17] = 0                                     # code table 4.4: minute
    return bytes(msg)


def _wmo(n: int, heading: bytes) -> bytes:
    """A 40-byte WMO header as NOAA Open Data's ds.temp.bin carries one before the file and before each message."""
    head = b"****%010d****\n" % n + heading
    assert len(head) == 40, len(head)
    return head


def _framed(msgs: list) -> bytes:
    """Messages framed as the real file frames them: a header of its own, then each message after its own."""
    # each count is the message's length and its 21-byte heading, as NOAA's are; the walk never reads them
    body = b"".join(_wmo(len(m) + 21, b"YEUB13 KWBN 151216\r\r\n") + m for m in msgs)
    return _wmo(40 + len(body), b"YEUZ98 KWBN 151216\r\r\n") + body


def _ranged(blob: bytes, lm: str, calls: list = None, change_at: int = None):
    """A range read of `blob` as NOAA Open Data answers one: Last-Modified `lm` (another from read `change_at` on), and
    a 416 for a range that starts past the end."""
    def fetch(u, start, end, meta):
        if calls is not None:
            calls.append((start, end))
        meta["lastModified"] = lm if change_at is None or len(calls or []) < change_at else "2026-11-15T11:41:00Z"
        if start >= len(blob):
            raise urllib.error.HTTPError(u, 416, "Range Not Satisfiable", {}, None)
        return blob[start:end + 1]
    return fetch


class HourlyTemperature(unittest.TestCase):
    def setUp(self):
        self.keep = (ndfd.GRID, ndfd.HOUR_CELLS)
        ndfd.GRID = None            # the fixture is a 33 by 35 cut of the HRRR's grid
        # four lattice points, one of them off the grid, and two places
        ndfd.HOUR_CELLS = ([0, 7, -1, 1154], ["a", "b"], [7, 100])
        self.tmp = tempfile.TemporaryDirectory()
        self.st = storage.LocalStorage(self.tmp.name)

    def tearDown(self):
        ndfd.GRID, ndfd.HOUR_CELLS = self.keep
        self.tmp.cleanup()

    def _issuance(self, published: str, hours: list):
        """An archived issuance published at `published` that carries `hours`; its place `a` holds its hour of
        publication, so a frame says which issuance it came from."""
        doc = {"schema": 1, "element": "temp", "issued": published, "lastModified": published,
               "ref": published[:13] + ":00:00Z", "hours": {t: {"values": [700, None, 712, 698], "own": {"a": int(published[11:13])}}
                                                           for t in hours}}
        stamp = published.replace("-", "").replace(":", "")
        self.st.put(f"{ndfd.HOUR_ARCHIVE}temp_{stamp}.json.gz", gzip.compress(json.dumps(doc).encode()))

    def test_an_hour_is_sampled_at_the_lattice_and_at_each_places_own_cell_in_the_frames_tenths(self):
        msg = _hrrr()
        values, own = ndfd.hour_sample(msg)
        field = grib2.decode(msg)
        self.assertEqual(values, [ndfd.to_tenths(field[0]), ndfd.to_tenths(field[7]), None, ndfd.to_tenths(field[1154])])
        self.assertEqual(own, {"a": ndfd.to_tenths(field[7]), "b": ndfd.to_tenths(field[100])})
        # the analysis frames' own encoding, for every value of the field
        self.assertEqual([ndfd.to_tenths(v) for v in field], analysis._frame_ints(field, analysis.SCALE["temp"], analysis.k_to_f))

    def test_a_tenth_is_rounded_half_away_from_zero(self):
        self.assertIsNone(ndfd.to_tenths(None))
        self.assertEqual(ndfd.to_tenths(273.15), 320)
        self.assertEqual(ndfd.to_tenths(273.15 + 0.05 * 5 / 9), 321)          # 32.05 F, up
        self.assertEqual(ndfd.to_tenths(273.15 - 32.05 * 5 / 9), -1)          # -0.05 F, away from zero

    def test_the_hour_is_the_reference_time_and_the_forecast_hours(self):
        self.assertEqual(ndfd.valid_time(_hrrr()), dt.datetime(2026, 10, 16, 19, 0, tzinfo=UTC))
        ref = dt.datetime(2026, 11, 15, 10, 0, tzinfo=UTC)
        self.assertEqual(ndfd.valid_time(_tmp(ref, 3)), dt.datetime(2026, 11, 15, 13, 0, tzinfo=UTC))
        self.assertTrue(ndfd.is_hourly_temp(_tmp(ref, 3)))
        self.assertFalse(ndfd.is_hourly_temp(_fixture()))       # MaxT, over a 12-hour window

    def test_a_messages_head_gives_its_length_and_hour_and_product(self):
        msg = _tmp(dt.datetime(2026, 11, 15, 10, 0, tzinfo=UTC), 3)
        h = ndfd.message_head(msg[:ndfd.HEAD_BYTES])
        self.assertEqual((h["length"], h["ref"], h["hours"], h["discipline"], h["template"], h["category"], h["number"]),
                         (len(msg), dt.datetime(2026, 11, 15, 10, 0, tzinfo=UTC), 3.0, 0, 0, 0, 0))
        m = ndfd.message_head(_fixture()[:ndfd.HEAD_BYTES])
        self.assertEqual((m["length"], m["template"], m["number"]), (len(_fixture()), 8, 4))
        with self.assertRaises(ValueError):
            ndfd.message_head(msg[:60])                    # section 4 is past these bytes
        with self.assertRaises(ValueError):
            ndfd.message_head(b"GRIX" + msg[4:ndfd.HEAD_BYTES])

    def test_an_issuance_is_read_by_range_keeping_the_hours_after_the_one_it_was_published_in(self):
        ref = dt.datetime(2026, 11, 15, 10, 0, tzinfo=UTC)
        blob = _fixture() + _blob(ref, range(0, 7))            # a MaxT message, then 10Z to 16Z
        calls = []
        doc = ndfd.read_hours(_ranged(blob, "2026-11-15T10:41:07Z", calls), ref)
        # not 10Z, which began before it was published, and not past AHEAD_HOURS
        self.assertEqual(sorted(doc["hours"]), ["2026-11-15T11:00:00Z", "2026-11-15T12:00:00Z", "2026-11-15T13:00:00Z"])
        self.assertEqual((doc["issued"], doc["ref"], doc["messages"], doc["skipped"]),
                         ("2026-11-15T10:41:07Z", "2026-11-15T10:00:00Z", 6, 1))
        # six heads, the MaxT message's and 10Z to 14Z, and the three kept messages whole; 15Z and 16Z are never read
        heads = [c for c in calls if c[1] - c[0] + 1 == ndfd.HEAD_BYTES]
        wholes = [c for c in calls if c not in heads]
        n = len(_hrrr())
        self.assertEqual(len(heads), 6)
        self.assertEqual(wholes, [(len(_fixture()) + k * n, len(_fixture()) + (k + 1) * n - 1) for k in (1, 2, 3)])
        self.assertLess(max(st for st, _ in calls), len(_fixture()) + 5 * n)     # no read reaches 15Z's message
        self.assertIsNone(ndfd.read_hours(lambda u, s, e, meta: None, ref))

    def test_the_real_files_framing_is_walked_message_by_message(self):
        ref = dt.datetime(2026, 11, 15, 10, 0, tzinfo=UTC)
        msgs = [_tmp(ref, n) for n in range(0, 6)]
        blob = _framed(msgs)
        self.assertEqual(blob.find(b"GRIB"), 80)
        calls = []
        doc = ndfd.read_hours(_ranged(blob, "2026-11-15T10:41:07Z", calls), ref)
        self.assertEqual(sorted(doc["hours"]), ["2026-11-15T11:00:00Z", "2026-11-15T12:00:00Z", "2026-11-15T13:00:00Z"])
        self.assertEqual(doc["messages"], 5)
        # each whole read starts at the message's own GRIB, 40 bytes past the end of the one before
        starts = [80 + sum(40 + len(m) for m in msgs[:k]) for k in range(6)]
        wholes = [c for c in calls if c[1] - c[0] + 1 != ndfd.HEAD_BYTES]
        self.assertEqual(wholes, [(starts[k], starts[k] + len(msgs[k]) - 1) for k in (1, 2, 3)])
        self.assertTrue(all(blob[a:a + 4] == b"GRIB" for a, _ in wholes))
        # a head with no message in it is refused, by that check and not by a later one
        with self.assertRaisesRegex(ValueError, "^no GRIB2 message in the 512 bytes at byte 0 of ds.temp.bin$"):
            ndfd.read_hours(_ranged(b"*" * 600, "2026-11-15T10:41:07Z"), ref)

    def test_the_issuances_of_15_november_give_each_hour_from_the_one_that_carries_it(self):
        # written at 12:21:04 with its reference time at 12:30 and its hours at 30 minutes past, and at 12:51:26 with
        # 13:30 and its first hour 14Z, so it does not carry 13Z
        a = _framed([_tmp_min(dt.datetime(2026, 11, 15, 12, 30, tzinfo=UTC), 30 + 60 * k) for k in range(6)])
        b = _framed([_tmp_min(dt.datetime(2026, 11, 15, 13, 30, tzinfo=UTC), 30 + 60 * k) for k in range(6)])
        state = {"lm": "2026-11-15T12:21:04Z", "blob": a}

        def range_fetch(u, start, end, meta):
            return _ranged(state["blob"], state["lm"])(u, start, end, meta)

        def meta_fetch(u, meta):
            meta["lastModified"] = state["lm"]
            return b"GRIB"

        out = ndfd.hour_job(self.st, dt.datetime(2026, 11, 15, 12, 26, tzinfo=UTC), range_fetch, meta_fetch)
        self.assertEqual(out["read"]["hours"], ["2026-11-15T13:00:00Z", "2026-11-15T14:00:00Z", "2026-11-15T15:00:00Z"])
        state.update(lm="2026-11-15T12:51:26Z", blob=b)
        out = ndfd.hour_job(self.st, dt.datetime(2026, 11, 15, 12, 56, tzinfo=UTC), range_fetch, meta_fetch)
        self.assertEqual(out["read"]["hours"], ["2026-11-15T14:00:00Z", "2026-11-15T15:00:00Z"])
        # 13Z begun: the 12:51 file is the newer, but it does not carry 13Z, so the 12:21 file gives it
        out = ndfd.hour_job(self.st, dt.datetime(2026, 11, 15, 13, 26, tzinfo=UTC), range_fetch, meta_fetch)
        self.assertEqual(out["written"], ["20261115T13Z"])
        self.assertEqual(json.loads(self.st.get(ndfd.HOUR_PREFIX + "20261115T13Z.json"))["issued"], "2026-11-15T12:21:04Z")
        out = ndfd.hour_job(self.st, dt.datetime(2026, 11, 15, 14, 26, tzinfo=UTC), range_fetch, meta_fetch)
        self.assertEqual(json.loads(self.st.get(ndfd.HOUR_PREFIX + "20261115T14Z.json"))["issued"], "2026-11-15T12:51:26Z")

    def test_too_little_of_the_pass_left_leaves_the_read_for_the_next_pass(self):
        ref = dt.datetime(2026, 11, 15, 10, 0, tzinfo=UTC)
        calls = []
        with self.assertRaises(ndfd.OutOfTime):
            ndfd.read_hours(_ranged(_framed([_tmp(ref, 1)]), "2026-11-15T10:41:07Z", calls), ref,
                            ndfd.arch.Deadline(ndfd.STEP_NEED - 1))
        self.assertEqual(calls, [])
        # with enough left, the same read goes through
        doc = ndfd.read_hours(_ranged(_framed([_tmp(ref, 1)]), "2026-11-15T10:41:07Z"), ref, ndfd.arch.Deadline(3600))
        self.assertEqual(sorted(doc["hours"]), ["2026-11-15T11:00:00Z"])
        # the job hands its deadline to the read, and the read's stop is the hourly part's failure, nothing archived
        def meta_fetch(u, meta):
            meta["lastModified"] = "2026-11-15T10:41:07Z"
            return b"GRIB"
        with self.assertRaises(ndfd.OutOfTime):
            ndfd.hour_job(self.st, ref, _ranged(_framed([_tmp(ref, 1)]), "2026-11-15T10:41:07Z"), meta_fetch,
                          ndfd.arch.Deadline(ndfd.STEP_NEED - 1))
        self.assertEqual(ndfd.hour_archived(self.st), [])

    def test_a_file_read_through_ends_the_walk(self):
        ref = dt.datetime(2026, 11, 15, 10, 0, tzinfo=UTC)
        doc = ndfd.read_hours(_ranged(_blob(ref, (1, 2)), "2026-11-15T10:41:07Z"), ref)
        self.assertEqual(sorted(doc["hours"]), ["2026-11-15T11:00:00Z", "2026-11-15T12:00:00Z"])

    def test_a_file_written_again_during_the_read_leaves_it_for_the_next_pass(self):
        ref = dt.datetime(2026, 11, 15, 10, 0, tzinfo=UTC)
        with self.assertRaises(ndfd.IssuanceChanged):
            ndfd.read_hours(_ranged(_blob(ref, range(0, 7)), "2026-11-15T10:41:07Z", [], change_at=3), ref)

    def test_each_hour_takes_the_newest_issuance_published_before_it_began(self):
        self._issuance("2026-11-15T09:41:00Z", ["2026-11-15T10:00:00Z", "2026-11-15T11:00:00Z", "2026-11-15T12:00:00Z"])
        self._issuance("2026-11-15T10:41:00Z", ["2026-11-15T11:00:00Z", "2026-11-15T12:00:00Z", "2026-11-15T13:00:00Z"])
        self._issuance("2026-11-15T11:05:00Z", ["2026-11-15T12:00:00Z", "2026-11-15T13:00:00Z", "2026-11-15T14:00:00Z"])
        # published as 12Z began, so not before it
        self._issuance("2026-11-15T12:00:00Z", ["2026-11-15T12:00:00Z", "2026-11-15T13:00:00Z"])
        got = ndfd.build_hours(self.st, dt.datetime(2026, 11, 15, 12, 30, tzinfo=UTC), ndfd.hour_archived(self.st))
        # nothing for 13Z, which has not begun, or for 09Z, which no issuance before it carries
        self.assertEqual({s: f["issued"] for s, f in got.items()},
                         {"20261115T12Z": "2026-11-15T11:05:00Z", "20261115T11Z": "2026-11-15T10:41:00Z",
                          "20261115T10Z": "2026-11-15T09:41:00Z"})
        f = got["20261115T12Z"]
        self.assertEqual((f["kind"], f["valid"], f["ref"], f["unit"], f["scale"], f["pitch"], f["cols"], f["rows"],
                          f["values"], f["own"]),
                         ("temp", "2026-11-15T12:00:00Z", "2026-11-15T11:00:00Z", "°F", 10, 3, 320, 200,
                          [700, None, 712, 698], {"a": 11}))
        # an hour already written is left
        again = ndfd.build_hours(self.st, dt.datetime(2026, 11, 15, 12, 30, tzinfo=UTC), ndfd.hour_archived(self.st),
                                 frozenset({"20261115T12Z", "20261115T11Z"}))
        self.assertEqual(sorted(again), ["20261115T10Z"])

    def _two_days(self, until: dt.datetime, down: tuple = ()):
        """Issuances at :21 and :51 from 00:21 on 14 November until `until`, each carrying the AHEAD_HOURS hours
        after the hour it was published in, none published in the outage `down` (start, end)."""
        p = dt.datetime(2026, 11, 14, 0, 21, tzinfo=UTC)
        n = 0
        while p < until:
            if not (down and down[0] <= p < down[1]):
                begun = p.replace(minute=0)
                self._issuance(p.strftime("%Y-%m-%dT%H:%M:%SZ"),
                               [(begun + dt.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ") for h in range(1, ndfd.AHEAD_HOURS + 1)])
                n += 1
            p += dt.timedelta(minutes=30)
        return n

    def test_an_hour_no_issuance_carries_loads_none_and_the_search_holds_one_hours_reach(self):
        # the hourly part down from 09:00 to 14:00 on the second day, so no issuance carries 12Z, 13Z or 14Z
        now = dt.datetime(2026, 11, 15, 14, 5, tzinfo=UTC)
        archived = self._two_days(now, (dt.datetime(2026, 11, 15, 9, 0, tzinfo=UTC), dt.datetime(2026, 11, 15, 14, 0, tzinfo=UTC)))
        self.assertEqual(archived, 66)
        stats = {}
        got = ndfd.build_hours(self.st, now, ndfd.hour_archived(self.st), stats=stats)
        self.assertNotIn("20261115T14Z", got)
        self.assertNotIn("20261115T12Z", got)
        self.assertEqual(got["20261115T11Z"]["issued"], "2026-11-15T08:51:00Z")
        # the three hours no issuance carries load nothing; each issuance is loaded once at most, and the most held at
        # once is one hour's reach, two issuances an hour for AHEAD_HOURS hours
        self.assertLessEqual(stats["held"], 2 * ndfd.AHEAD_HOURS)
        self.assertLessEqual(stats["loaded"], 2 * (ndfd.BUILD_HOURS + ndfd.AHEAD_HOURS))
        # with no frame on record, all twenty four hours are built and the same bound holds
        stats = {}
        self._two_days(dt.datetime(2026, 11, 16, 0, 0, tzinfo=UTC))
        got = ndfd.build_hours(self.st, dt.datetime(2026, 11, 15, 23, 59, tzinfo=UTC), ndfd.hour_archived(self.st), stats=stats)
        self.assertEqual(len(got), ndfd.BUILD_HOURS)
        self.assertLessEqual(stats["held"], 2 * ndfd.AHEAD_HOURS)
        self.assertLessEqual(stats["loaded"], 2 * (ndfd.BUILD_HOURS + ndfd.AHEAD_HOURS))

    def test_an_hour_the_newest_issuance_before_it_leaves_out_comes_from_the_one_before(self):
        self._issuance("2026-11-15T09:41:00Z", ["2026-11-15T11:00:00Z"])
        self._issuance("2026-11-15T10:41:00Z", ["2026-11-15T12:00:00Z"])
        got = ndfd.build_hours(self.st, dt.datetime(2026, 11, 15, 11, 10, tzinfo=UTC), ndfd.hour_archived(self.st))
        self.assertEqual({s: f["issued"] for s, f in got.items()}, {"20261115T11Z": "2026-11-15T09:41:00Z"})

    def test_each_frame_is_written_once_kept_as_long_as_the_analysis_frames_and_then_pruned(self):
        self._issuance("2026-11-13T09:41:00Z", [])            # older than HOUR_KEEP_DAYS
        state = {"lm": "2026-11-15T10:41:07Z", "blob": _blob(dt.datetime(2026, 11, 15, 10, 0, tzinfo=UTC), range(0, 5))}
        reads = []

        def range_fetch(u, start, end, meta):
            reads.append(start)
            return _ranged(state["blob"], state["lm"])(u, start, end, meta)

        def meta_fetch(u, meta):
            meta["lastModified"] = state["lm"]
            return b"GRIB"

        out = ndfd.hour_job(self.st, dt.datetime(2026, 11, 15, 11, 5, tzinfo=UTC), range_fetch, meta_fetch)
        self.assertEqual((out["added"], out["issuances"], out["written"], out["frames"]), (True, 1, ["20261115T11Z"], 1))
        self.assertEqual(out["read"]["hours"], ["2026-11-15T11:00:00Z", "2026-11-15T12:00:00Z", "2026-11-15T13:00:00Z"])
        self.assertEqual(ndfd.hour_archived(self.st), [ndfd.HOUR_ARCHIVE + "temp_20261115T104107Z.json.gz"])
        idx = json.loads(self.st.get(ndfd.HOUR_PREFIX + "index.json"))
        self.assertEqual(idx["hours"], {"20261115T11Z": {"issued": "2026-11-15T10:41:07Z", "ref": "2026-11-15T10:00:00Z"}})
        frame = json.loads(self.st.get(ndfd.HOUR_PREFIX + "20261115T11Z.json"))
        self.assertEqual((frame["valid"], len(frame["values"]), frame["scale"]), ("2026-11-15T11:00:00Z", 4, 10))
        # ten minutes on, the same issuance: nothing read, nothing written again, and no issuance loaded to know it
        n = len(reads)
        loads, keep_load = [], ndfd._load
        ndfd._load = lambda store, key: loads.append(key) or keep_load(store, key)
        try:
            out = ndfd.hour_job(self.st, dt.datetime(2026, 11, 15, 11, 15, tzinfo=UTC), range_fetch, meta_fetch)
        finally:
            ndfd._load = keep_load
        self.assertEqual((out["added"], out["written"], len(reads), loads), (False, [], n, []))
        # the next hour begun: its frame from the same issuance, the 11Z frame left as it was
        out = ndfd.hour_job(self.st, dt.datetime(2026, 11, 15, 12, 2, tzinfo=UTC), range_fetch, meta_fetch)
        self.assertEqual((out["written"], out["frames"]), (["20261115T12Z"], 2))
        self.assertEqual(json.loads(self.st.get(ndfd.HOUR_PREFIX + "20261115T11Z.json")), frame)
        # a day on, a new issuance: its hour is written, and the day-old frames stay, as the analysis frames do
        state.update(lm="2026-11-16T12:41:00Z", blob=_blob(dt.datetime(2026, 11, 16, 12, 0, tzinfo=UTC), range(0, 4)))
        out = ndfd.hour_job(self.st, dt.datetime(2026, 11, 16, 13, 3, tzinfo=UTC), range_fetch, meta_fetch)
        self.assertEqual((out["added"], out["written"], out["frames"], out["pruned"]), (True, ["20261116T13Z"], 3, 0))
        self.assertEqual(json.loads(self.st.get(ndfd.HOUR_PREFIX + "20261115T11Z.json")), frame)
        # FRAME_KEEP_DAYS on from the first, it and its index line are gone, and so is a frame the index lost
        self.st.put(ndfd.HOUR_PREFIX + "20261115T09Z.json", b"{}")
        later = dt.datetime(2026, 11, 15, 12, 30, tzinfo=UTC) + dt.timedelta(days=ndfd.FRAME_KEEP_DAYS)
        out = ndfd.hour_job(self.st, later, range_fetch, meta_fetch)
        self.assertIsNone(self.st.get(ndfd.HOUR_PREFIX + "20261115T11Z.json"))
        self.assertIsNone(self.st.get(ndfd.HOUR_PREFIX + "20261115T09Z.json"))
        self.assertEqual((out["pruned"], list(json.loads(self.st.get(ndfd.HOUR_PREFIX + "index.json"))["hours"])),
                         (2, ["20261115T12Z", "20261116T13Z"]))


class Lattice(unittest.TestCase):
    def test_the_hourly_lattice_is_the_analysis_frames_own_points_on_the_ground(self):
        keep = ndfd.HOUR_CELLS
        ndfd.HOUR_CELLS = None
        try:
            lat, ids, own = ndfd.hour_cells()
        finally:
            ndfd.HOUR_CELLS = keep
        with open(ndfd.LATTICE_PATH) as fh:
            frames = json.load(fh)
        with open(ndfd.LOCATIONS_PATH) as fh:
            locs = json.load(fh)
        self.assertEqual((len(lat), ids), (ndfd.COLS * ndfd.ROWS, [loc["id"] for loc in locs]))
        wi, gi = grib2.WEXP["Ni"], grib2.G184["Ni"]

        def ground(grid, k):
            return grib2.lcc_latlon(grid, k % grid["Ni"], k // grid["Ni"])

        # each forecast cell is the same point on the ground as the analysis frame's own cell at that point
        both = 0
        for kw, kg in zip(frames["wexp"], lat):
            if kg < 0:
                continue
            both += 1
            (a, b), (c, d) = ground(grib2.WEXP, kw), ground(grib2.G184, kg)
            # a cell is about 0.02 degrees across, so one cell out is twenty times this
            self.assertLess(max(abs(a - c), abs(b - d)), 1e-3, (kw, kg))
        self.assertGreater(both, 60000)
        # and each place's is its own cell on grid 184 as the settlement list records it
        self.assertEqual(own, [loc["cell"]["g184"][2] for loc in locs])
        # a wexp cell west of grid 184, or north of it, is off it
        self.assertEqual((ndfd.ndfd_cell(199), ndfd.ndfd_cell(200), ndfd.ndfd_cell(1377 * wi + 300), ndfd.ndfd_cell(-1)),
                         (-1, 0, -1, -1))
        self.assertEqual(ndfd.ndfd_cell(1376 * wi + 200 + gi - 1), 1376 * gi + gi - 1)


if __name__ == "__main__":
    unittest.main()
