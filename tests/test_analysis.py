"""The analysis job on the local storage backend, with the decoder and the
fetchers replaced by fakes: a product-hour lands in the archive with its four
frames and the grid index; the day rules (half-up rounding away from zero,
strict inequalities, a partial day's count, a closed incomplete day, a
precipitation revision, the local day that spans two UTC directories, the
23- and 25-hour daylight-time days); index.json is the last write; a missing
object is an absence and never a raised error; a raising fetch or decode is
recorded against its product and the pass still writes everything else; the
alarm fires when NOAA stops landing hours; precipitation coverage fills in
through its own rewritable key; each frame carries the 21 x 21 cell window
round every place at the field's own values, on the grid the file is on, and
the day rebuild never reads a frame. The fake bucket holds synthetic GRIB2
"messages" that carry only their identity, and the fake fields answer by
cell index so no 3.7 million floats are ever built. No network, and no
dependence on pipeline/grib2.py being present except in the cell geometry
tests at the end, which run the real Lambert and Albers maths of
scripts/build_analysis_grid.py for one place."""
import contextlib
from decimal import Decimal
import datetime as dt
import gzip
import io
import json
import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pipeline import analysis, archive, gov_weather, storage   # noqa: E402

NOW = dt.datetime(2026, 9, 26, 19, 55, tzinfo=dt.timezone.utc)
UTC = dt.timezone.utc
WEXP = {"Ni": 2345, "Nj": 1597, "lat1": 19.228976, "lon1": 233.723448, "lov": 265.0, "latin1": 25.0,
        "latin2": 25.0, "lad": 25.0, "dx": 2539.703, "dy": 2539.703, "scan": 64, "radius": 6371200.0}
G184 = dict(WEXP, Ni=2145, Nj=1377, lat1=20.191999, lon1=238.445999)
OTHER = dict(WEXP, Ni=100, Nj=100)
# the sidecar's message list of a real analysis file, in its order
IDX_VARS = [("HGT", "surface"), ("PRES", "surface"), ("TMP", "2 m above ground"), ("DPT", "2 m above ground"),
            ("UGRD", "10 m above ground"), ("VGRD", "10 m above ground"), ("SPFH", "2 m above ground"),
            ("WDIR", "10 m above ground"), ("WIND", "10 m above ground"), ("GUST", "10 m above ground"),
            ("VIS", "surface"), ("CEIL", "cloud ceiling"), ("TCDC", "entire atmosphere")]
PARAM_OF = {"TMP": (0, 0, 0), "WIND": (0, 2, 1), "GUST": (0, 2, 22), "APCP": (0, 1, 8)}
VAR_OF = {"TMP": "temp", "WIND": "wind", "GUST": "gust", "APCP": "precip"}


def hour(y, m, d, h):
    return dt.datetime(y, m, d, h, tzinfo=UTC)


def iso(t):
    return analysis._iso(t)


class Field:
    """A decoded field that answers by cell index."""
    def __init__(self, fn, n):
        self.fn, self.n = fn, n

    def __getitem__(self, k):
        return self.fn(k)

    def __len__(self):
        return self.n


class Fakes:
    """The fake NOAA bucket and decoder. `values[(product, var, iso)]` is a
    function of the cell index (None for a cell the bitmap leaves out), or a
    number for a flat field; a missing entry is a flat default. `grids`
    overrides the grid a message claims. `raise_for` is a predicate on the
    URL: a matching fetch raises, the way gov_weather does after its tries."""
    def __init__(self):
        self.bucket = {}
        self.values = {}
        self.grids = {}
        self.fetched = []
        self.raise_for = None

    # ---- what a message is
    @staticmethod
    def msg(product, name, t, grid):
        return f"GRIB|{product}|{name}|{iso(t)}|{grid}".encode()

    @staticmethod
    def parts(msg):
        return msg.decode().split("|")

    def add_hour(self, product, t, precip=True):
        base = analysis.anl_url(product, t)
        body, lines, off = b"", [], 0
        for n, (name, level) in enumerate(IDX_VARS, 1):
            m = self.msg(product, name, t, "wexp")
            lines.append(f"{n}:{off}:d={t:%Y%m%d%H}:{name}:{level}:anl:")
            body += m
            off += len(m)
        self.bucket[base] = body
        self.bucket[base + ".idx"] = "\n".join(lines).encode() + b"\n"
        if precip:
            self.add_precip(product, t)

    def add_precip(self, product, t):
        grid = analysis.PRODUCTS[product]["pcpGrid"]
        self.bucket[analysis.pcp_url(product, t)] = self.msg(product, "APCP", t, grid)

    def drop_precip(self, product, t):
        self.bucket.pop(analysis.pcp_url(product, t), None)

    # ---- gov_weather
    def _maybe_raise(self, url):
        if self.raise_for and self.raise_for(url):
            raise RuntimeError("HTTP Error 503: Service Unavailable")

    def fetch_bytes(self, url, tries=3, timeout=60):
        self.fetched.append(url)
        self._maybe_raise(url)
        return self.bucket.get(url)

    def fetch_range(self, url, start, end, tries=3, timeout=60):
        self.fetched.append(f"{url}#{start}-{end}")
        self._maybe_raise(url)
        data = self.bucket.get(url)
        return None if data is None else data[start:end + 1]

    def set_user_agent(self, ua):
        pass

    # ---- grib2
    def grid_def(self, msg):
        p = self.parts(msg)
        name = self.grids.get((p[1], p[2], p[3]), p[4])
        return {"wexp": WEXP, "g184": G184}.get(name, OTHER)

    @staticmethod
    def same_grid(a, b):
        return a["Ni"] == b["Ni"] and a["Nj"] == b["Nj"] and abs(a["lat1"] - b["lat1"]) < 1e-4

    def product_def(self, msg):
        p = self.parts(msg)
        d, c, n = PARAM_OF.get(p[2], (0, 3, 5))
        out = {"discipline": d, "category": c, "number": n, "level_type": 103, "level_value": 2, "template": 0}
        if p[2] == "APCP":
            out.update({"template": 8, "acc_hours": 1})
        return out

    @staticmethod
    def random_access(msg):
        # the analysis fields are simple packing without a bitmap, the
        # precipitation is complex packing: the two paths the job takes
        return Fakes.parts(msg)[2] != "APCP"

    def _fn(self, msg):
        p = self.parts(msg)
        v = self.values.get((p[1], VAR_OF.get(p[2], p[2]), p[3]))
        if v is None:
            v = {"temp": 293.15, "wind": 5.0, "gust": 8.0, "precip": 0.0}.get(VAR_OF.get(p[2]), 0.0)
        return v if callable(v) else (lambda k, v=v: v)

    def decode(self, msg):
        g = self.grid_def(msg)
        return Field(self._fn(msg), g["Ni"] * g["Nj"])

    def decode_cells(self, msg, ks):
        fn = self._fn(msg)
        return [fn(k) for k in ks]

    @staticmethod
    def parse_idx(text, total_length=None):
        rows = []
        for line in text.splitlines():
            f = line.split(":")
            if len(f) < 6:
                continue
            rows.append({"n": int(f[0]), "start": int(f[1]), "date": f[2][2:], "var": f[3], "level": f[4],
                         "step": f[5], "end": None})
        for a, b in zip(rows, rows[1:]):
            a["end"] = b["start"] - 1
        if rows and total_length is not None:
            rows[-1]["end"] = total_length - 1
        return rows

    # ---- as the module attributes the job uses
    def install(self, case):
        fake_gw = types.SimpleNamespace(fetch_bytes=self.fetch_bytes, fetch_range=self.fetch_range,
                                        set_user_agent=self.set_user_agent,
                                        NODD_RTMA=analysis.NODD_DEFAULT["rtma"], NODD_URMA=analysis.NODD_DEFAULT["urma"])
        fake_grib2 = types.SimpleNamespace(WEXP=WEXP, G184=G184, grid_def=self.grid_def, same_grid=self.same_grid,
                                           product_def=self.product_def, decode=self.decode,
                                           decode_cells=self.decode_cells, parse_idx=self.parse_idx,
                                           random_access=self.random_access)
        keep = (analysis.gw, analysis.grib2)
        analysis.gw, analysis.grib2 = fake_gw, fake_grib2
        case.addCleanup(lambda: setattr(analysis, "gw", keep[0]))
        case.addCleanup(lambda: setattr(analysis, "grib2", keep[1]))


class Recording(storage.LocalStorage):
    """The local backend, counting reads, writes and deletes."""
    def __init__(self, root):
        super().__init__(root)
        self.puts, self.deletes, self.gets = [], [], []

    def get(self, key):
        self.gets.append(key)
        return super().get(key)

    def put(self, key, data, content_type="application/octet-stream", cache_control=None):
        self.puts.append((key, content_type, cache_control))
        super().put(key, data, content_type, cache_control)

    def delete(self, key):
        self.deletes.append(key)
        super().delete(key)


ALL_LOCS = analysis.load_locations()
LOCS = [l for l in ALL_LOCS if l["id"] in ("new-york-ny", "chicago-il", "los-angeles-ca")]
NY = next(l for l in LOCS if l["id"] == "new-york-ny")
LA = next(l for l in LOCS if l["id"] == "los-angeles-ca")
NY_K = NY["cell"]["wexp"][2]
NY_K184 = NY["cell"]["g184"][2]
LA_K = LA["cell"]["wexp"][2]
LA_K184 = LA["cell"]["g184"][2]
# a place three cells in from the western edge and two from the southern one
# on both grids, so its window runs off the grid on two sides
EDGE = {"id": "edge-xx", "name": "Edge", "state": "XX", "geoid": "0", "pop2024": 1, "lat": 20.0, "lon": -125.0,
        "tz": "America/Los_Angeles", "note": "", "px": 0.0, "py": 0.0,
        "cell": {"wexp": [3, 2, 2 * 2345 + 3], "g184": [3, 2, 2 * 2145 + 3], "centre": [20.0, -125.0], "distanceKm": 0.0}}
TINY = {"Ni": 5, "Nj": 4}


def wexp_window_value(k):
    """A temperature field whose Fahrenheit value names its cell: cell k is
    32 + (k mod 997) F, so a window entry is 320 + 10 * (k mod 997) in tenths
    and a value from any other cell shows."""
    return 273.15 + (k % 997) * 5.0 / 9.0
QUIET = {"user_agent": "test", "pass_budget_seconds": 120}


def hours_of(temps, wind=5.0, gust=8.0, precip=0.0, day="2026-09-26", tz="America/New_York"):
    """product_hours for day_summary: the first len(temps) labelled hours of
    the day, exact values in the archive's units."""
    hs = analysis.local_day_hours(day, tz)
    out = {}
    for h, t in zip(hs, temps):
        out[iso(h)] = {"temp": t, "wind": wind, "gust": gust, "precip": precip}
    return out


# ------------------------------------------------------------------ rules, no storage
class Rules(unittest.TestCase):
    def test_half_up_rounding(self):
        self.assertEqual(analysis.half_up(70.5, 1), 71)
        self.assertEqual(analysis.half_up(70.49, 1), 70)
        self.assertEqual(analysis.half_up(70.55, 0.1), 70.6)     # not the 70.5 that binary floating point gives
        self.assertEqual(analysis.half_up(0.4917, 0.01), 0.49)
        self.assertEqual(analysis.half_up(0.495, 0.01), 0.5)
        s = analysis.day_summary(hours_of([60.0, 70.5, 65.0]), "America/New_York", "2026-09-26", NOW)
        self.assertEqual((s["high"]["value"], s["high"]["exact"]), (71, 70.5))
        s = analysis.day_summary(hours_of([60.0, 70.49, 65.0]), "America/New_York", "2026-09-26", NOW)
        self.assertEqual((s["high"]["value"], s["high"]["exact"]), (70, 70.49))  # rounded once, and shown cut, not rounded
        s = analysis.day_summary(hours_of([60.0] * 4, wind=20.45), "America/New_York", "2026-09-26", NOW)
        self.assertEqual(s["wind"], {"value": 20, "exact": 20.45})
        # the shown value is cut, so 16.4996 beside its whole 16 never reads 16.500
        self.assertEqual(analysis.cut(16.4996, 0.001), 16.499)
        self.assertEqual(analysis.cut(-20.4996, 0.001), -20.499)

    def test_a_value_the_file_holds_exactly_is_converted_exactly(self):
        # 250.65 K is -8.5 F exactly; binary floating point makes it
        # -8.49999999999995, which rounds to -8 as it stands, and the exact
        # conversion gives the -9 a published table shows
        self.assertGreater(analysis.k_to_f(250.65), -8.5)
        self.assertEqual(round(analysis.k_to_f(250.65)), -8)
        self.assertEqual(analysis.exact("temp", 250.65), Decimal("-8.5"))
        self.assertEqual(analysis.half_up(analysis.exact("temp", 250.65), 1), -9)
        # the New York high of 2026-09-26: 290.09 K is 62.492 F, 62 rounded once
        # (a first rounding of the hour to 62.5 made it 63 before 2026-09-29)
        self.assertEqual(analysis.exact("temp", 290.09), Decimal("62.492"))
        self.assertEqual(analysis.half_up(analysis.exact("temp", 290.09), 1), 62)
        self.assertEqual(analysis.exact("wind", 10.0), Decimal("22.369362921"))
        self.assertEqual(analysis.exact("precip", 25.4), Decimal("1"))

    def test_negative_halves_round_away_from_zero(self):
        # owner's decision 2026-09-27: half up means half away from zero, the
        # way the published tables read, so a -20.5 low is -21 and resolves a
        # -21 strike No
        self.assertEqual(analysis.half_up(-20.5, 1), -21)
        self.assertEqual(analysis.half_up(-0.5, 1), -1)
        self.assertEqual(analysis.half_up(-20.55, 0.1), -20.6)
        self.assertEqual(analysis.half_up(-20.49, 1), -20)
        s = analysis.day_summary(hours_of([-20.5, -3.0], day="2026-01-10"), "America/New_York", "2026-01-10", NOW)
        self.assertEqual((s["low"]["value"], s["low"]["exact"]), (-21, -20.5))
        self.assertEqual(analysis.resolves("low", -21, -22), False)
        self.assertEqual(analysis.resolves("low", -21, -21), True)     # at the strike is Yes
        self.assertEqual(analysis.resolves("low", -21, -20), True)

    def test_units_are_exact(self):
        self.assertAlmostEqual(analysis.k_to_f(273.15), 32.0)
        self.assertAlmostEqual(analysis.k_to_f(310.15), 98.6)
        self.assertAlmostEqual(analysis.ms_to_mph(10.0), 22.369362921)
        self.assertAlmostEqual(analysis.mm_to_inch(25.4), 1.0)

    def test_at_least_on_a_ladder(self):
        # owner's decision 2026-09-28: a value equal to the strike resolves Yes
        # whichever way the contract runs
        # high 71: Yes at the value and below it, No above it
        self.assertEqual([analysis.resolves("high", 71, k) for k in (69, 70, 71, 72)], [True, True, True, False])
        # low 58: No below the value, Yes at it and above it
        self.assertEqual([analysis.resolves("low", 58, k) for k in (57, 58, 59, 60)], [False, True, True, True])
        self.assertEqual([analysis.resolves("precip", 0.49, k) for k in (0.25, 0.49, 0.5)], [True, True, False])
        self.assertEqual([analysis.resolves("gust", 40, k) for k in (40, 41)], [True, False])
        self.assertEqual([analysis.resolves("wind", 12, k) for k in (12, 13)], [True, False])
        self.assertIsNone(analysis.resolves("gust", None, 30))

    def test_daily_values_from_the_hours(self):
        temps = [60.0 + i * 0.4 for i in range(24)]         # rises through the day, peak at hour 23
        ph = hours_of(temps, wind=10.36, gust=20.7, precip=0.0205)
        hs = analysis.local_day_hours("2026-09-26", "America/New_York")
        ph[iso(hs[5])]["gust"] = 45.7                        # one gusty hour
        s = analysis.day_summary(ph, "America/New_York", "2026-09-26", NOW)
        self.assertEqual((s["hours"], s["of"]), (24, 24))
        self.assertTrue(s["complete"])
        self.assertEqual(s["low"], {"value": 60, "exact": 60.0, "at": iso(hs[0])})
        self.assertEqual(s["high"]["value"], 69)
        self.assertEqual(s["high"]["exact"], 69.2)
        self.assertEqual(s["high"]["at"], iso(hs[23]))
        self.assertEqual(s["gust"], {"value": 46, "exact": 45.7, "at": iso(hs[5])})
        self.assertEqual(s["wind"], {"value": 10, "exact": 10.36})
        self.assertEqual(s["precip"]["exact"], 0.492)
        self.assertEqual(s["precip"]["value"], 0.49)
        self.assertTrue(s["final"])                          # RTMA resolves, and every analysis was read
        self.assertFalse(s["closed"])

    def test_an_extreme_is_stamped_at_its_first_hour(self):
        temps = [60.0, 70.0, 70.0, 65.0]
        hs = analysis.local_day_hours("2026-09-26", "America/New_York")
        s = analysis.day_summary(hours_of(temps), "America/New_York", "2026-09-26", NOW)
        self.assertEqual(s["high"]["at"], iso(hs[1]))

    def test_a_partial_day_counts_its_hours(self):
        s = analysis.day_summary(hours_of([60.0] * 7), "America/New_York", "2026-09-26", NOW, product="urma")
        self.assertEqual(s["hours"], 7)
        self.assertFalse(s["complete"])
        self.assertFalse(s["final"])
        self.assertFalse(s["closed"])
        self.assertFalse(s["precip"]["resolved"])
        self.assertIsNone(s["precip"]["revised"])

    def test_a_day_still_short_after_48_hours_is_closed_and_never_final(self):
        end = analysis.day_end("2026-09-26", "America/New_York")
        self.assertEqual(end, hour(2026, 9, 27, 4))
        late = end + dt.timedelta(hours=49)
        s = analysis.day_summary(hours_of([60.0] * 20), "America/New_York", "2026-09-26", late, product="urma")
        self.assertEqual(s["hours"], 20)
        self.assertTrue(s["closed"])
        self.assertFalse(s["final"])
        self.assertFalse(s["complete"])
        self.assertEqual(s["high"]["value"], 60)             # the value stands on the hours read
        # an hour short of the close time it is only partial
        s = analysis.day_summary(hours_of([60.0] * 20), "America/New_York", "2026-09-26", end + dt.timedelta(hours=47), product="urma")
        self.assertFalse(s["closed"])

    def test_a_complete_rtma_day_is_final_and_urma_only_compares(self):
        # owner's decision 2026-09-29: RTMA resolves, precipitation included
        ph = hours_of([60.0] * 24, precip=0.01)
        s = analysis.day_summary(ph, "America/New_York", "2026-09-26", NOW)
        self.assertTrue(s["final"])
        self.assertEqual((s["precip"]["resolved"], s["precip"]["resolvedAt"], s["precip"]["revised"]),
                         (True, iso(NOW), None))                  # the pass time, not an analysis time
        self.assertEqual(s["precip"]["exact"], 0.24)
        later = NOW + dt.timedelta(hours=5)
        s2 = analysis.day_summary(ph, "America/New_York", "2026-09-26", later, prior=s)
        self.assertEqual(s2["precip"]["resolvedAt"], iso(NOW))
        # URMA is shown for comparison and is never final; its precipitation
        # keeps the first complete read and carries a later re-read beside it
        u = analysis.day_summary(ph, "America/New_York", "2026-09-26", NOW, product="urma")
        self.assertEqual((u["complete"], u["final"]), (True, False))
        self.assertEqual(u["precip"]["resolvedAt"], iso(NOW))
        u2 = analysis.day_summary(ph, "America/New_York", "2026-09-26", later, product="urma", prior=u,
                                  revised={"value": 0.26, "exact": 0.2604, "at": iso(later)})
        self.assertEqual((u2["precip"]["resolvedAt"], u2["precip"]["exact"]), (iso(NOW), 0.24))
        self.assertEqual(u2["precip"]["revised"]["value"], 0.26)

    def test_an_hour_with_only_its_precipitation_adds_rain_but_no_analysis(self):
        # RTMA 2026-09-23 19Z: the analysis never landed, the precipitation did
        ph = hours_of([60.0, 70.0], precip=0.1)
        h3 = analysis.local_day_hours("2026-09-26", "America/New_York")[2]
        ph[iso(h3)] = {"temp": None, "wind": None, "gust": None, "precip": 0.2, "anl": False}
        s = analysis.day_summary(ph, "America/New_York", "2026-09-26", NOW)
        self.assertEqual((s["hours"], s["of"], s["complete"]), (2, 24, False))
        self.assertEqual(s["high"]["value"], 70)
        self.assertEqual(s["precip"], {"value": 0.4, "exact": 0.4, "hours": 3, "resolved": False, "resolvedAt": None,
                                       "revised": None})
        # on URMA the precipitation of a whole day resolves while the analyses do not
        hs = analysis.local_day_hours("2026-09-26", "America/New_York")
        ph = {iso(h): {"temp": 60.0, "wind": 5.0, "gust": 8.0, "precip": 0.0} for h in hs[:-1]}
        ph[iso(hs[-1])] = {"temp": None, "wind": None, "gust": None, "precip": 0.05, "anl": False}
        s = analysis.day_summary(ph, "America/New_York", "2026-09-26", NOW, product="urma")
        self.assertEqual((s["hours"], s["complete"], s["final"]), (23, False, False))
        self.assertTrue(s["precip"]["resolved"])
        self.assertEqual(s["precip"]["value"], 0.05)

    def test_a_day_resolves_on_the_hours_available_once_the_rest_are_not_published(self):
        # owner's decision 2026-09-29
        hs = analysis.local_day_hours("2026-09-26", "America/New_York")
        ph = hours_of([60.0 + i for i in range(24)], precip=0.01)
        del ph[iso(hs[10])]                                  # 10:00 local on neither server
        s = analysis.day_summary(ph, "America/New_York", "2026-09-26", NOW, missing=set(), pmissing=set())
        self.assertEqual((s["final"], s["hours"], s["closed"]), (False, 23, False))    # still pending
        s = analysis.day_summary(ph, "America/New_York", "2026-09-26", NOW, missing={iso(hs[10])},
                                 pmissing={iso(hs[10])})
        self.assertEqual((s["final"], s["finalAt"], s["hours"], s["complete"]), (True, iso(NOW), 23, False))
        self.assertEqual(s["high"]["value"], 83)
        self.assertEqual((s["precip"]["resolved"], s["precip"]["hours"], s["precip"]["value"]), (True, 23, 0.23))
        # the file turns up later: the day stands on the hours it resolved on
        ph[iso(hs[10])] = {"temp": 99.0, "wind": 5.0, "gust": 8.0, "precip": 1.0}
        later = NOW + dt.timedelta(hours=3)
        s2 = analysis.day_summary(ph, "America/New_York", "2026-09-26", later, prior=s, missing=set(), pmissing=set())
        self.assertEqual((s2["final"], s2["finalAt"], s2["hours"], s2["high"]["value"]), (True, iso(NOW), 23, 83))
        self.assertEqual((s2["precip"]["value"], s2["precip"]["resolvedAt"]), (0.23, iso(NOW)))
        # never settled: 48 hours after the day it is closed, not final
        end = analysis.day_end("2026-09-26", "America/New_York")
        s3 = analysis.day_summary(ph, "America/New_York", "2026-09-26", end + dt.timedelta(hours=49),
                                  missing=set(), pmissing=set())
        del ph[iso(hs[10])]
        s3 = analysis.day_summary(ph, "America/New_York", "2026-09-26", end + dt.timedelta(hours=49),
                                  missing=set(), pmissing=set())
        self.assertEqual((s3["final"], s3["closed"]), (False, True))

    def test_an_empty_day_is_absent(self):
        self.assertIsNone(analysis.day_summary({}, "America/New_York", "2026-09-26", NOW))

    def test_a_missing_hourly_value_is_skipped_not_zero(self):
        ph = hours_of([60.0, 61.0])
        hs = analysis.local_day_hours("2026-09-26", "America/New_York")
        ph[iso(hs[1])]["temp"] = None
        s = analysis.day_summary(ph, "America/New_York", "2026-09-26", NOW)
        self.assertEqual(s["hours"], 2)
        self.assertEqual(s["high"]["exact"], 60.0)


class LocalDay(unittest.TestCase):
    def test_a_chicago_day_spans_two_utc_directories(self):
        hs = analysis.local_day_hours("2026-09-26", "America/Chicago")
        self.assertEqual(len(hs), 24)
        self.assertEqual(hs[0], hour(2026, 9, 26, 5))
        self.assertEqual(hs[23], hour(2026, 9, 27, 4))
        self.assertIn("/rtma2p5.20260926/rtma2p5.t05z.", analysis.anl_url("rtma", hs[0]))
        self.assertIn("/rtma2p5.20260927/rtma2p5.t04z.", analysis.anl_url("rtma", hs[23]))
        self.assertIn("/urma2p5.20260927/urma2p5.2026092704.pcp_01h.wexp.grb2", analysis.pcp_url("urma", hs[23]))
        self.assertIn("/rtma2p5.20260927/rtma2p5.2026092704.pcp.184.grb2", analysis.pcp_url("rtma", hs[23]))
        # the same UTC hour is the 26th in Chicago and the 27th in New York
        touched = analysis.touched_by(hour(2026, 9, 27, 4), LOCS)
        self.assertIn(("chicago-il", "2026-09-26"), touched)
        self.assertIn(("new-york-ny", "2026-09-27"), touched)
        self.assertIn(("los-angeles-ca", "2026-09-26"), touched)
        self.assertEqual(analysis.local_day_of(hour(2026, 9, 27, 4), "America/Chicago"), "2026-09-26")

    def test_the_spring_forward_day_has_23_distinct_hours(self):
        spring = analysis.local_day_hours("2026-03-08", "America/New_York")
        self.assertEqual(len(spring), 23)
        self.assertEqual(len(set(spring)), 23)
        self.assertEqual(spring[0], hour(2026, 3, 8, 5))          # 00:00 EST
        self.assertEqual(spring[-1], hour(2026, 3, 9, 3))         # 23:00 EDT
        labels = [analysis.hour_label(h, "America/New_York") for h in spring]
        self.assertEqual(labels[:4], ["00", "01", "03", "04"])    # 02:00 does not occur
        self.assertEqual(len(set(labels)), 23)
        # the aggregates run over the 23 real analyses, none counted twice:
        # 07Z (03:00 EDT) carries an inch and a 100 mph wind
        ph = {}
        for h in spring:
            ph[iso(h)] = {"temp": 40.0, "wind": 10.0 if h != hour(2026, 3, 8, 7) else 100.0,
                          "gust": 12.0, "precip": 0.01 if h != hour(2026, 3, 8, 7) else 1.0}
        s = analysis.day_summary(ph, "America/New_York", "2026-03-08", NOW)
        self.assertEqual((s["hours"], s["of"], s["complete"], s["final"]), (23, 23, True, True))
        self.assertEqual(s["precip"]["exact"], 1.22)               # 22 x 0.01 + 1.00
        self.assertEqual(s["precip"]["hours"], 23)
        self.assertTrue(s["precip"]["resolved"])
        self.assertEqual(s["wind"]["exact"], 13.913)               # 320 / 23 = 13.9130..., cut
        self.assertEqual(s["wind"]["value"], 14)
        # 22 hours read of 23 is not complete
        del ph[iso(spring[10])]
        s = analysis.day_summary(ph, "America/New_York", "2026-03-08", NOW, product="urma")
        self.assertEqual((s["hours"], s["complete"]), (22, False))

    def test_the_fall_back_day_has_25_distinct_hours(self):
        fall = analysis.local_day_hours("2026-11-01", "America/New_York")
        self.assertEqual(len(fall), 25)
        self.assertEqual(len(set(fall)), 25)
        self.assertEqual(fall[1], hour(2026, 11, 1, 5))           # the first 01:00, daylight time
        self.assertEqual(fall[2], hour(2026, 11, 1, 6))           # the second 01:00, standard time
        self.assertEqual(fall[3], hour(2026, 11, 1, 7))           # 02:00 EST
        self.assertEqual(fall[-1], hour(2026, 11, 2, 4))          # 23:00 EST
        labels = [analysis.hour_label(h, "America/New_York") for h in fall]
        self.assertEqual(labels[:4], ["00", "01", "01*", "02"])
        self.assertEqual(analysis.day_end("2026-11-01", "America/New_York"), hour(2026, 11, 2, 5))
        # both 01:00 analyses count: 06Z carries an inch and a 90 F reading
        ph = {}
        for h in fall:
            ph[iso(h)] = {"temp": 50.0 if h != hour(2026, 11, 1, 6) else 90.0, "wind": 10.0,
                          "gust": 12.0, "precip": 0.01 if h != hour(2026, 11, 1, 6) else 1.0}
        s = analysis.day_summary(ph, "America/New_York", "2026-11-01", NOW, product="urma")
        self.assertEqual((s["hours"], s["of"], s["complete"]), (25, 25, True))
        self.assertEqual(s["high"], {"value": 90, "exact": 90.0, "at": iso(hour(2026, 11, 1, 6))})
        self.assertEqual(s["precip"]["exact"], 1.24)               # 24 x 0.01 + 1.00
        self.assertEqual(s["wind"]["exact"], 10.0)                 # the mean over 25 hours
        del ph[iso(fall[2])]
        s = analysis.day_summary(ph, "America/New_York", "2026-11-01", NOW, product="urma")
        self.assertEqual((s["hours"], s["complete"], s["precip"]["resolved"]), (24, False, False))

    def test_arizona_keeps_standard_time(self):
        hs = analysis.local_day_hours("2026-07-04", "America/Phoenix")
        self.assertEqual(hs[0], hour(2026, 7, 4, 7))
        for day in ("2026-03-08", "2026-11-01"):
            hs = analysis.local_day_hours(day, "America/Phoenix")
            self.assertEqual(len(hs), 24)
            self.assertEqual([analysis.hour_label(h, "America/Phoenix") for h in hs], [f"{h:02d}" for h in range(24)])


class Frames(unittest.TestCase):
    def test_frame_from_field_scales_and_keeps_holes(self):
        field = Field(lambda k: None if k == 2 else 293.15 + k, 10)
        out = analysis.frame_from_field(field, [0, 1, 2, -1, 4], 10, analysis.k_to_f)
        self.assertEqual(out, [680, 698, None, None, 752])
        self.assertEqual(analysis.frame_from_field([0.254, 12.7], [0, 1, 1], 100, analysis.mm_to_inch), [1, 50, 50])

    def test_a_frame_half_is_not_lost_to_float_noise(self):
        # 68.05 * 10 is 680.4999999999999 in binary; the frame int is 681
        self.assertEqual(analysis.frame_from_field([68.05, 20.45], [0, 1], 10), [681, 205])


class Windows(unittest.TestCase):
    def test_window_indices_walk_row_major_and_are_null_off_the_grid(self):
        # a 5 x 4 grid, one cell either way: dj outer, di inner, -1 off the grid
        self.assertEqual(analysis.window_indices((2, 1, 7), TINY, half=1), [1, 2, 3, 6, 7, 8, 11, 12, 13])
        self.assertEqual(analysis.window_indices((0, 0, 0), TINY, half=1), [-1, -1, -1, -1, 0, 1, -1, 5, 6])
        self.assertEqual(analysis.window_indices((4, 3, 19), TINY, half=1), [13, 14, -1, 18, 19, -1, -1, -1, -1])
        self.assertEqual(analysis.WINDOW_HALF, 10)
        ks = analysis.window_indices(NY["cell"]["wexp"], WEXP)
        self.assertEqual(len(ks), 441)
        self.assertEqual(ks[220], NY_K)                                   # the centre entry is the resolving cell
        self.assertEqual(ks[0], (NY["cell"]["wexp"][1] - 10) * 2345 + NY["cell"]["wexp"][0] - 10)
        self.assertEqual(ks[440], (NY["cell"]["wexp"][1] + 10) * 2345 + NY["cell"]["wexp"][0] + 10)

    def test_window_from_field_takes_the_field_at_k_and_keeps_holes(self):
        field = Field(lambda k: None if k == 7 else 273.15 + k, 20)
        ks = analysis.window_indices((2, 1, 7), TINY, half=1)
        out = analysis.window_from_field(field, ks, 10, analysis.k_to_f)
        self.assertEqual(out, [338, 356, 374, 428, None, 464, 518, 536, 554])
        self.assertEqual(analysis.window_from_field(field, [-1, 0], 10, analysis.k_to_f), [None, 320])
        self.assertEqual(analysis.window_doc("g184", [1, None]), {"grid": "g184", "half": 10, "values": [1, None]})


# ------------------------------------------------------------------ the job on storage
class Job(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = Recording(self.tmp.name)
        self.fx = Fakes()
        self.fx.install(self)
        keep = analysis.load_locations
        analysis.load_locations = lambda path=None: LOCS
        self.addCleanup(lambda: setattr(analysis, "load_locations", keep))
        self.cfg = {}

    def run_pass(self, now=NOW):
        """One pass, its printed status kept in self.errors for the assertions."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = analysis.analysis_pass(self.cfg, self.st, now)
        line = buf.getvalue().strip().splitlines()[-1]
        self.errors = json.loads(line).get("errors", [])
        return rc

    def read(self, key):
        raw = self.st.get(key)
        if raw is None:
            return None
        return json.loads(gzip.decompress(raw) if key.endswith(".gz") else raw)

    def state(self):
        return self.read(analysis.STATE_KEY)

    def health(self):
        return self.read(analysis.HEALTH_KEY)

    def test_a_product_hour_writes_the_archive_the_frames_and_the_grid_index(self):
        t_r, t_u = hour(2026, 9, 26, 19), hour(2026, 9, 26, 13)   # what should have landed by 19:55Z
        self.fx.add_hour("rtma", t_r)
        self.fx.add_hour("urma", t_u)
        self.fx.values[("rtma", "temp", iso(t_r))] = lambda k: 300.15 if k == NY_K else 293.15
        self.fx.values[("rtma", "precip", iso(t_r))] = lambda k: 2.54 if k == NY["cell"]["g184"][2] else 0.0
        self.assertEqual(self.run_pass(), 0)
        # the archive hour, write once, in the file's own units (kelvin and
        # metres per second), nothing converted or rounded (2026-09-29)
        arc = self.read(analysis.hour_key("rtma", t_r))
        self.assertEqual(arc["schema"], "analysis/2")
        self.assertEqual((arc["valid"], arc["source"]), ("2026-09-26T19:00:00Z", "nodd"))
        self.assertEqual(arc["values"]["new-york-ny"], {"temp": 300.15, "wind": 5.0, "gust": 8.0})
        self.assertEqual(arc["values"]["chicago-il"]["temp"], 293.15)
        # the precipitation in its own key, in millimetres
        pre = self.read(analysis.precip_key("rtma", t_r))
        self.assertEqual((pre["read"], pre["complete"], pre["missing"], pre["source"]), (True, True, [], "nodd"))
        self.assertEqual(pre["values"]["new-york-ny"], 2.54)
        merged = analysis.get_hour(self.st, "rtma", t_r, {})
        self.assertEqual(merged["values"]["new-york-ny"], {"temp": 300.15, "wind": 5.0, "gust": 8.0, "precip": 2.54})
        self.assertTrue(merged["precipRead"])
        self.assertIsNotNone(self.read(analysis.hour_key("urma", t_u)))
        # four frames per product, on the lattice, as scaled ints
        for product, t in (("rtma", t_r), ("urma", t_u)):
            for var in analysis.HOURLY_VARS:
                fr = self.read(analysis.FRAME_KEY.format(product=product, var=var, stamp=analysis._stamp(t)))
                self.assertEqual((fr["cols"], fr["rows"], fr["pitch"], len(fr["values"])), (320, 200, 3, 64000), var)
                self.assertEqual(fr["scale"], analysis.SCALE[var])
                self.assertEqual(fr["valid"], iso(t))
        temp = self.read(analysis.FRAME_KEY.format(product="rtma", var="temp", stamp="20260926T19Z"))
        self.assertEqual(temp["unit"], "°F")
        self.assertIn(680, temp["values"])                     # 293.15 K is 68.0 F, in tenths
        pcp = self.read(analysis.FRAME_KEY.format(product="rtma", var="precip", stamp="20260926T19Z"))
        self.assertEqual(pcp["values"].count(None), 15)        # the lattice points off the G184 grid
        gi = self.read(analysis.GRID_INDEX_KEY)
        self.assertEqual(gi["frames"]["rtma"]["temp"], {"2026-09-26": ["19"]})
        self.assertEqual(gi["frames"]["urma"]["precip"], {"2026-09-26": ["13"]})
        self.assertEqual(gi["asof"], iso(t_r))
        # the days the hour touched, at both scales
        day = self.read(analysis.DAY_KEY.format(day="2026-09-26"))
        ny = day["locations"]["new-york-ny"]
        self.assertEqual(ny["rtma"]["hours"], 1)
        self.assertEqual(ny["rtma"]["high"], {"value": 81, "exact": 80.6, "at": "2026-09-26T19:00:00Z"})
        self.assertEqual(ny["urma"]["hours"], 1)
        loc = self.read(analysis.LOC_KEY.format(loc="new-york-ny", day="2026-09-26"))
        self.assertEqual(len(loc["hours"]), 24)
        row = next(r for r in loc["hours"] if r["t"] == "2026-09-26T19:00:00Z")
        self.assertEqual(row["local"], "15")
        self.assertEqual(row["rtma"], {"temp": 80.6, "wind": 11.184, "gust": 17.895, "precip": None})
        self.assertIsNone(row["urma"])
        # the accumulation NOAA files under 19Z is the rain of the hour that starts at 18Z
        before = next(r for r in loc["hours"] if r["t"] == "2026-09-26T18:00:00Z")
        self.assertEqual(before["rtma"], {"temp": None, "wind": None, "gust": None, "precip": 0.1})
        self.assertEqual(loc["summary"], ny)
        # the state and the index, which is the last write
        state = self.state()
        self.assertEqual(state["products"]["rtma"]["newest"], iso(t_r))
        self.assertEqual(state["products"]["urma"]["newest"], iso(t_u))
        self.assertEqual(state["products"]["rtma"]["precipPending"], [])
        idx = self.read(analysis.INDEX_KEY)
        self.assertEqual(idx["days"], ["2026-09-26"])
        self.assertEqual(idx["sources"]["rtma"]["latest"], iso(t_r))
        self.assertEqual(idx["asof"], iso(t_r))
        self.assertEqual(idx["statement"], analysis.STATEMENT)
        self.assertEqual(len(idx["locations"]), 3)
        self.assertEqual(self.st.puts[-1][0], analysis.INDEX_KEY)
        for key, ctype, cache in self.st.puts:
            if key.startswith("snapshots/"):
                self.assertEqual(ctype, "text/csv; charset=utf-8" if key.endswith(".csv") else "application/json", key)
                self.assertIn(cache, (analysis.CACHE_FINAL, analysis.CACHE_LIVE), key)
        self.assertEqual(archive.LAST_STATUS["job"], "analysis")
        self.assertEqual(archive.LAST_STATUS["errors"], 0)
        self.assertEqual(archive.LAST_STATUS["read"], 2)
        # the archive hour is written once: a second pass reads nothing new and rewrites no frame
        n = len(self.st.puts)
        self.assertEqual(self.run_pass(), 0)
        self.assertFalse(any(k.startswith("snapshots/analysis2/grid/rtma/") for k, _, _ in self.st.puts[n:]))
        self.assertFalse(any(k.startswith("archive/analysis2/") and not k.endswith("state.json") for k, _, _ in self.st.puts[n:]))
        self.assertEqual(self.st.puts[-1][0], analysis.INDEX_KEY)

    def test_frames_carry_each_places_window_on_the_grid_the_file_is_on(self):
        t_r, t_u = hour(2026, 9, 26, 19), hour(2026, 9, 26, 13)
        analysis.load_locations = lambda path=None: LOCS + [EDGE]
        self.fx.add_hour("rtma", t_r)
        self.fx.add_hour("urma", t_u)
        self.fx.values[("rtma", "temp", iso(t_r))] = wexp_window_value
        # one cell beside New York's is missing from the bitmap
        self.fx.values[("rtma", "gust", iso(t_r))] = lambda k: None if k == NY_K + 1 else 10.0
        # 1 inch at New York's G184 cell and half an inch at its wexp index: the
        # RTMA precipitation window must read the G184 one, the URMA the wexp one
        def pcp(k):
            return 25.4 if k == NY_K184 else (12.7 if k == NY_K else 0.0)
        self.fx.values[("rtma", "precip", iso(t_r))] = pcp
        self.fx.values[("urma", "precip", iso(t_u))] = pcp
        self.assertEqual(self.run_pass(), 0)
        temp = self.read(analysis.FRAME_KEY.format(product="rtma", var="temp", stamp="20260926T19Z"))
        # the frame is otherwise the shape it was
        self.assertEqual(sorted(temp), sorted(["schema", "product", "var", "valid", "asof", "written", "unit", "scale",
                                               "cols", "rows", "pitch", "values", "windows"]))
        self.assertEqual(len(temp["values"]), 64000)
        self.assertEqual(set(temp["windows"]), {"new-york-ny", "chicago-il", "los-angeles-ca", "edge-xx"})
        w = temp["windows"]["new-york-ny"]
        self.assertEqual((w["grid"], w["half"], len(w["values"])), ("wexp", 10, 441))
        i, j = NY["cell"]["wexp"][0], NY["cell"]["wexp"][1]
        for a, b in ((0, 0), (-10, -10), (10, -10), (10, 10), (-10, 10), (3, -7)):
            k = (j + b) * 2345 + (i + a)
            self.assertEqual(w["values"][(b + 10) * 21 + (a + 10)], 320 + 10 * (k % 997), (a, b))
        self.assertEqual(w["values"][220], 320 + 10 * (NY_K % 997))
        # the archive hour is the value at the resolving cell, unchanged in shape
        arc = self.read(analysis.hour_key("rtma", t_r))
        self.assertEqual(sorted(arc), ["product", "schema", "source", "valid", "values", "written"])
        self.assertLess(abs(float(analysis.exact("temp", arc["values"]["new-york-ny"]["temp"])) - (32 + NY_K % 997)), 0.001)
        # a cell the bitmap leaves out is null in the window
        gust = self.read(analysis.FRAME_KEY.format(product="rtma", var="gust", stamp="20260926T19Z"))["windows"]["new-york-ny"]
        self.assertIsNone(gust["values"][221])
        self.assertEqual(gust["values"].count(None), 1)
        self.assertEqual(gust["values"][220], 224)                        # 10 m/s is 22.4 mph
        # off the grid is null: a place 3 cells in from the west and 2 from the
        # south has 14 x 13 cells of its window on the grid
        edge = temp["windows"]["edge-xx"]
        self.assertEqual(edge["values"].count(None), 441 - 14 * 13)
        self.assertIsNone(edge["values"][0])
        self.assertIsNotNone(edge["values"][220])
        self.assertEqual(edge["values"][(0 + 10) * 21 + (-3 + 10)], 320 + 10 * ((2 * 2345 + 0) % 997))
        # the precipitation window is on G184 for RTMA and on wexp for URMA
        pr = self.read(analysis.FRAME_KEY.format(product="rtma", var="precip", stamp="20260926T19Z"))["windows"]
        self.assertEqual(pr["new-york-ny"]["grid"], "g184")
        self.assertEqual(pr["new-york-ny"]["values"][220], 100)
        self.assertEqual(pr["edge-xx"]["values"].count(None), 441 - 14 * 13)
        pu = self.read(analysis.FRAME_KEY.format(product="urma", var="precip", stamp="20260926T13Z"))["windows"]
        self.assertEqual(pu["new-york-ny"]["grid"], "wexp")
        self.assertEqual(pu["new-york-ny"]["values"][220], 50)
        for var in analysis.HOURLY_VARS:
            fr = self.read(analysis.FRAME_KEY.format(product="urma", var=var, stamp="20260926T13Z"))
            self.assertEqual(fr["windows"]["los-angeles-ca"]["grid"], "wexp", var)
            self.assertEqual(len(fr["windows"]["los-angeles-ca"]["values"]), 441, var)

    def test_a_precipitation_refetch_rewrites_the_frame_with_its_window(self):
        t = hour(2026, 9, 26, 19)
        self.fx.add_hour("rtma", t, precip=False)
        self.assertEqual(self.run_pass(), 0)
        self.assertIsNone(self.read(analysis.FRAME_KEY.format(product="rtma", var="precip", stamp="20260926T19Z")))
        self.fx.add_precip("rtma", t)
        self.fx.values[("rtma", "precip", iso(t))] = lambda k: 2.54 if k == NY_K184 else 0.0
        self.assertEqual(self.run_pass(NOW + dt.timedelta(minutes=10)), 0)
        fr = self.read(analysis.FRAME_KEY.format(product="rtma", var="precip", stamp="20260926T19Z"))
        self.assertEqual(fr["windows"]["new-york-ny"]["grid"], "g184")
        self.assertEqual(fr["windows"]["new-york-ny"]["values"][220], 10)

    def test_the_day_rebuild_never_reads_a_frame_so_a_frame_without_windows_is_fine(self):
        t_r, t_u = hour(2026, 9, 26, 19), hour(2026, 9, 26, 13)
        self.fx.add_hour("rtma", t_r)
        self.fx.add_hour("urma", t_u)
        self.fx.values[("rtma", "temp", iso(t_r))] = lambda k: 300.15 if k == NY_K else 293.15
        self.assertEqual(self.run_pass(), 0)
        day_before = self.read(analysis.DAY_KEY.format(day="2026-09-26"))
        loc_before = self.read(analysis.LOC_KEY.format(loc="new-york-ny", day="2026-09-26"))
        # rewrite every frame in the shape the lane wrote before windows
        # existed, and one of them as it would be after a page-side tool
        # touched it, then rebuild the day from scratch
        frames = [k for k, _, _ in self.st.puts if k.startswith("snapshots/analysis2/grid/") and not k.endswith("index.json")]
        self.assertEqual(len(frames), 8)
        for key in frames:
            doc = self.read(key)
            self.assertIn("windows", doc)
            del doc["windows"]
            self.st.put(key, analysis._dump(doc), "application/json", analysis.CACHE_FINAL)
        self.st.gets = []
        later = NOW + dt.timedelta(minutes=10)
        cache = {}
        day_doc, loc_docs = analysis.build_day(self.st, "2026-09-26", LOCS, cache, later, analysis.load_state(self.st))
        self.assertEqual(day_doc["locations"], day_before["locations"])
        self.assertEqual(loc_docs["new-york-ny"]["hours"], loc_before["hours"])
        self.assertFalse([k for k in self.st.gets if k.startswith("snapshots/analysis2/grid/")])
        # and a whole second pass, which rebuilds the closing days, still reads
        # no frame and rewrites none
        n = len(self.st.puts)
        self.assertEqual(self.run_pass(later), 0)
        self.assertFalse([k for k in self.st.gets if k.startswith("snapshots/analysis2/grid/") and not k.endswith("index.json")])
        self.assertFalse([k for k, _, _ in self.st.puts[n:] if k.startswith("snapshots/analysis2/grid/") and not k.endswith("index.json")])
        for key in frames:
            self.assertNotIn("windows", self.read(key))
        self.assertEqual(self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"], day_before["locations"])

    def test_the_place_file_carries_the_precipitation_in_the_row_of_the_hour_it_starts(self):
        # owner's decision 2026-09-29: the day's precipitation runs midnight to
        # midnight, so the accumulation NOAA files under 19Z (18Z to 19Z) is
        # in the 18Z row, cut to a ten-thousandth of an inch
        t = hour(2026, 9, 26, 19)
        self.fx.add_hour("rtma", t)
        self.fx.values[("rtma", "precip", iso(t))] = lambda k: 1.0      # 1 mm is 0.03937 in
        self.assertEqual(self.run_pass(), 0)
        rows = {r["t"]: r for r in self.read(analysis.LOC_KEY.format(loc="new-york-ny", day="2026-09-26"))["hours"]}
        self.assertEqual(rows[iso(t - dt.timedelta(hours=1))]["rtma"], {"temp": None, "wind": None, "gust": None, "precip": 0.0393})
        self.assertEqual(rows[iso(t)]["rtma"], {"temp": 68.0, "wind": 11.184, "gust": 17.895, "precip": None})
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"]["rtma"]
        self.assertEqual(ny["precip"], {"value": 0.04, "exact": 0.0393, "hours": 1, "resolved": False, "resolvedAt": None,
                                        "revised": None})
        self.assertEqual(ny["hours"], 1)

    def test_a_missing_object_is_an_absence_not_an_error(self):
        self.assertEqual(self.run_pass(), 0)
        self.assertEqual(archive.LAST_STATUS["errors"], 0)
        self.assertEqual(archive.LAST_STATUS["alarms"], [])
        self.assertIsNone(self.read(analysis.hour_key("rtma", hour(2026, 9, 26, 19))))
        self.assertEqual(self.read(analysis.INDEX_KEY)["days"], [])
        self.assertEqual(self.st.puts[-1][0], analysis.INDEX_KEY)
        state = self.state()
        self.assertIsNone(state["products"]["rtma"]["newest"])
        self.assertEqual(state["products"]["rtma"]["scanned"], iso(hour(2026, 9, 26, 19)))
        self.assertEqual(state["products"]["rtma"]["since"], iso(NOW))

    def test_an_hour_whose_analysis_never_lands_still_adds_its_precipitation(self):
        # RTMA 2026-09-23 19Z never reached NOAA Open Data; its precipitation file did
        t = hour(2026, 9, 26, 19)
        self.fx.add_precip("rtma", t)
        self.fx.values[("rtma", "precip", iso(t))] = 2.54          # 0.1 in
        self.assertEqual(self.run_pass(), 0)
        self.assertIsNone(self.read(analysis.hour_key("rtma", t)))
        self.assertTrue(self.read(analysis.precip_key("rtma", t))["complete"])
        self.assertEqual(self.state()["products"]["rtma"]["gaps"], [iso(t)])
        self.assertIsNone(self.state()["products"]["rtma"]["newest"])
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"]["rtma"]
        self.assertEqual((ny["hours"], ny["complete"], ny["final"]), (0, False, False))
        self.assertIsNone(ny["high"])
        self.assertEqual(ny["precip"], {"value": 0.1, "exact": 0.1, "hours": 1, "resolved": False, "resolvedAt": None,
                                        "revised": None})
        # the accumulation filed under 19Z is the 18Z row's
        row = next(r for r in self.read(analysis.LOC_KEY.format(loc="new-york-ny", day="2026-09-26"))["hours"]
                   if r["t"] == iso(t - dt.timedelta(hours=1)))
        self.assertEqual(row["rtma"], {"temp": None, "wind": None, "gust": None, "precip": 0.1})
        fr = self.read(analysis.FRAME_KEY.format(product="rtma", var="precip", stamp="20260926T19Z"))
        self.assertIsNotNone(fr)
        # the gap is retried for its analysis, and the whole precipitation is not fetched again
        n = len(self.fx.fetched)
        self.assertEqual(self.run_pass(NOW + dt.timedelta(minutes=10)), 0)
        self.assertNotIn(analysis.pcp_url("rtma", t), self.fx.fetched[n:])
        self.assertIn(analysis.anl_url("rtma", t) + ".idx", self.fx.fetched[n:])
        # the analysis lands late: the hour is read and the stored precipitation stands
        self.fx.add_hour("rtma", t, precip=False)
        self.fx.values[("rtma", "precip", iso(t))] = 5.08
        self.assertEqual(self.run_pass(NOW + dt.timedelta(minutes=20)), 0)
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"]["rtma"]
        self.assertEqual((ny["hours"], ny["high"]["value"], ny["precip"]["value"]), (1, 68, 0.1))
        self.assertEqual(self.state()["products"]["rtma"]["gaps"], [])

    def test_the_backfill_keeps_the_precipitation_of_an_hour_without_its_analysis(self):
        t = hour(2026, 9, 26, 19)
        self.fx.add_hour("rtma", t)
        t0 = t - dt.timedelta(hours=1)
        self.fx.add_precip("rtma", t0)
        self.fx.values[("rtma", "precip", iso(t0))] = 2.54
        self.assertEqual(self.run_pass(), 0)
        self.assertIsNone(self.read(analysis.hour_key("rtma", t0)))
        self.assertTrue(self.read(analysis.precip_key("rtma", t0))["complete"])
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"]["rtma"]
        self.assertEqual((ny["hours"], ny["precip"]["hours"], ny["precip"]["value"]), (1, 2, 0.1))

    def test_the_daily_sweep_reads_the_precipitation_of_an_hour_both_walks_passed(self):
        old = hour(2026, 9, 23, 19)
        self.fx.add_precip("rtma", old)
        self.fx.values[("rtma", "precip", iso(old))] = 2.54
        kept = hour(2026, 9, 22, 19)                   # an hour whose analysis is archived
        self.fx.add_hour("rtma", kept)
        res = analysis.read_hour("rtma", kept, LOCS, {"wexp": [], "g184": []}, frames=False, now=NOW)
        analysis.write_hour(self.st, "rtma", kept, res, {}, {}, NOW)
        queued = hour(2026, 9, 21, 7)                  # an hour of a span the backfill has yet to read
        self.fx.add_precip("rtma", queued)
        state = analysis.new_state()
        state["products"]["rtma"]["newest"] = iso(hour(2026, 9, 26, 19))
        state["backfill"]["rtma"].update({"oldest": iso(hour(2026, 8, 28, 0)), "done": True,
                                          "queue": [[iso(hour(2026, 9, 21, 0)), iso(hour(2026, 9, 21, 12))]]})
        status = {"errors": [], "failed": 0}
        n = len(self.fx.fetched)
        touched = analysis.precip_sweep(self.st, state, LOCS, analysis.load_lattice(), {}, {}, NOW,
                                        archive.Deadline(600), status)
        self.assertIn(("new-york-ny", "2026-09-23"), touched)
        self.assertTrue(self.read(analysis.precip_key("rtma", old))["complete"])
        self.assertIsNotNone(self.read(analysis.FRAME_KEY.format(product="rtma", var="precip", stamp="20260923T19Z")))
        self.assertNotIn(analysis.pcp_url("rtma", kept), self.fx.fetched[n:])
        self.assertNotIn(analysis.pcp_url("rtma", queued), self.fx.fetched[n:])
        # nothing newer than the live lane's own 48 hours of retries
        self.assertNotIn(analysis.pcp_url("rtma", hour(2026, 9, 25, 19)), self.fx.fetched[n:])
        self.assertEqual((status["swept"]["filled"], status["swept"]["done"]), (1, True))
        self.assertEqual(state["precipSwept"], iso(NOW))
        self.assertFalse(analysis.precip_sweep_due(state, NOW + dt.timedelta(hours=23)))
        self.assertTrue(analysis.precip_sweep_due(state, NOW + dt.timedelta(hours=24)))
        # the rebuilt day adds the swept hour to the day's precipitation
        cache = {}
        day, _ = analysis.build_day(self.st, "2026-09-23", LOCS, cache, NOW, state)
        self.assertEqual(day["locations"]["new-york-ny"]["rtma"]["precip"]["value"], 0.1)
        self.assertEqual(day["locations"]["new-york-ny"]["rtma"]["hours"], 0)

    def test_a_refused_file_ends_the_products_sweep_for_the_day(self):
        a, b = hour(2026, 9, 23, 19), hour(2026, 9, 23, 20)
        self.fx.add_precip("rtma", a)
        self.fx.add_precip("rtma", b)
        self.fx.grids[("rtma", "APCP", iso(a))] = "other"          # a file on another grid is refused
        state = analysis.new_state()
        state["products"]["rtma"]["newest"] = iso(hour(2026, 9, 26, 19))
        state["backfill"]["rtma"].update({"oldest": iso(hour(2026, 9, 23, 0)), "done": True})
        status = {"errors": [], "failed": 0}
        n = len(self.fx.fetched)
        analysis.precip_sweep(self.st, state, LOCS, analysis.load_lattice(), {}, {}, NOW, archive.Deadline(600), status)
        self.assertEqual(status["failed"], 1)
        self.assertNotIn(analysis.pcp_url("rtma", b), self.fx.fetched[n:])
        self.assertIsNone(self.read(analysis.precip_key("rtma", b)))
        self.assertEqual(state["precipSwept"], iso(NOW))        # tried again tomorrow

    def _to_nomads(self, product, t, precip=True):
        """Move an hour's files in the fake from NOAA Open Data to NOMADS."""
        pairs = [(analysis.anl_url(product, t), analysis.anl_url(product, t, nomads=True)),
                 (analysis.anl_url(product, t) + ".idx", analysis.anl_url(product, t, nomads=True) + ".idx")]
        if precip:
            pairs.append((analysis.pcp_url(product, t), analysis.pcp_url(product, t, nomads=True)))
        for a, b in pairs:
            self.fx.bucket[b] = self.fx.bucket.pop(a)

    def test_a_file_missing_from_open_data_is_read_from_nomads_half_an_hour_after_it_is_due(self):
        # RTMA 2026-09-23 19Z reached NOMADS and never NOAA Open Data; a file
        # on NOMADS counts as published (owner's decision 2026-09-29). RTMA
        # usually lands 47 minutes after its hour, so NOMADS is asked from 77
        t = hour(2026, 9, 26, 12)
        self.fx.add_hour("rtma", t)
        self._to_nomads("rtma", t)
        empty = {"wexp": [], "g184": []}
        self.assertEqual(analysis.settled_after("rtma", t), t + dt.timedelta(minutes=77))
        res = analysis.read_hour("rtma", t, LOCS, empty, frames=False, now=t + dt.timedelta(minutes=70))
        self.assertEqual((res.status, res.precip_status), ("absent", "absent"))
        self.assertFalse(any("nomads" in u for u in self.fx.fetched))       # not asked before it is due
        self.assertFalse(analysis.absence_final("rtma", t, t + dt.timedelta(minutes=70)))
        res = analysis.read_hour("rtma", t, LOCS, empty, frames=False, now=t + dt.timedelta(minutes=77))
        self.assertEqual((res.status, res.doc["source"], res.precip["source"]), ("ok", "nomads", "nomads"))
        self.assertEqual(res.doc["values"]["new-york-ny"]["temp"], 293.15)
        n = len(self.fx.fetched)
        res = analysis.read_hour("rtma", t, LOCS, empty, frames=False, now=t + dt.timedelta(days=13, hours=1))
        self.assertEqual(res.status, "absent")                              # past what NOMADS keeps
        self.assertFalse(any("nomads" in u for u in self.fx.fetched[n:]))

    def test_the_days_precipitation_runs_midnight_to_midnight(self):
        # New York: 04Z is midnight EDT, so the accumulation NOAA files under
        # 04Z on the 26th (23:00 to 24:00 on the 25th) is the 25th's, and the
        # one under 04Z on the 27th is the last hour of the 26th
        later = hour(2026, 9, 27, 12)
        first, last = hour(2026, 9, 26, 4), hour(2026, 9, 27, 4)
        for t, mm in ((first, 25.4), (last, 2.54)):
            self.fx.add_precip("rtma", t)
            self.fx.values[("rtma", "precip", iso(t))] = mm
            analysis.write_precip(self.st, "rtma", t, analysis.read_precip("rtma", t, LOCS, None, later).doc, {})
        self.assertTrue({("new-york-ny", "2026-09-25"), ("new-york-ny", "2026-09-26")} <= analysis.touched_by(first, LOCS))
        d26, locs26 = analysis.build_day(self.st, "2026-09-26", LOCS, {}, later, analysis.new_state())
        d25, _ = analysis.build_day(self.st, "2026-09-25", LOCS, {}, later, analysis.new_state())
        self.assertEqual(d26["locations"]["new-york-ny"]["rtma"]["precip"]["value"], 0.1)
        self.assertEqual(d25["locations"]["new-york-ny"]["rtma"]["precip"]["value"], 1.0)
        rows = locs26["new-york-ny"]["hours"]
        self.assertEqual((rows[-1]["local"], rows[-1]["rtma"]["precip"]), ("23", 0.1))
        self.assertIsNone(rows[0]["rtma"])

    def test_the_sweep_reads_a_missing_analysis_from_nomads(self):
        old = hour(2026, 9, 23, 19)
        self.fx.add_hour("rtma", old)
        self._to_nomads("rtma", old, precip=False)     # the analysis on NOMADS only, the precipitation on Open Data
        state = analysis.new_state()
        state["products"]["rtma"]["newest"] = iso(hour(2026, 9, 26, 19))
        state["backfill"]["rtma"].update({"oldest": iso(hour(2026, 9, 23, 0)), "done": True})
        status = {"errors": [], "failed": 0}
        touched = analysis.precip_sweep(self.st, state, LOCS, analysis.load_lattice(), {}, {}, NOW,
                                        archive.Deadline(600), status)
        arc = self.read(analysis.hour_key("rtma", old))
        self.assertEqual((arc["source"], arc["values"]["new-york-ny"]["temp"]), ("nomads", 293.15))
        self.assertEqual(self.read(analysis.precip_key("rtma", old))["source"], "nodd")
        self.assertIn(("new-york-ny", "2026-09-23"), touched)
        self.assertEqual(status["failed"], 0)

    def test_an_hour_that_turns_up_after_the_day_resolved_is_shown_and_not_counted(self):
        # New York 2026-09-26 with 12:00 local (16Z) on neither server when
        # the day's last file lands: the day resolves on 23 hours; the file
        # turns up the next morning and the row shows it, marked late
        hs = analysis.local_day_hours("2026-09-26", "America/New_York")
        state, cache, gi = analysis.new_state(), {}, {}
        # after the midnight precipitation file is due (it is read with the next day's first hour)
        now = hs[-1] + dt.timedelta(minutes=140)
        gone = hs[12]
        for h in hs + [hs[-1] + dt.timedelta(hours=1)]:
            if h != gone:
                self.fx.add_hour("rtma", h)
                res = analysis.read_hour("rtma", h, LOCS, {"wexp": [], "g184": []}, frames=False, now=now)
                analysis.write_hour(self.st, "rtma", h, res, gi, cache, now)
        analysis._mark(state["products"]["rtma"], "missing", gone)
        analysis._mark(state["products"]["rtma"], "pmissing", gone)       # the 11:00 row's accumulation
        analysis.rebuild_days(self.st, {("new-york-ny", "2026-09-26")}, LOCS, cache, now, state)
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"]["rtma"]
        self.assertEqual((ny["final"], ny["hours"], ny["finalAt"]), (True, 23, iso(now)))
        self.assertEqual((ny["precip"]["resolved"], ny["precip"]["hours"]), (True, 23))
        # the late file
        later = now + dt.timedelta(hours=8)
        self.fx.add_hour("rtma", gone)
        self.fx.values[("rtma", "temp", iso(gone))] = 310.15                # 98.6 F, which would move the high
        res = analysis.read_hour("rtma", gone, LOCS, {"wexp": [], "g184": []}, frames=False, now=later)
        analysis.write_hour(self.st, "rtma", gone, res, gi, cache, later)
        analysis._unmark(state["products"]["rtma"], "missing", gone)
        analysis.rebuild_days(self.st, {("new-york-ny", "2026-09-26")}, LOCS, cache, later, state)
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"]["rtma"]
        self.assertEqual((ny["hours"], ny["high"]["value"]), (23, 68))
        row = next(r for r in self.read(analysis.LOC_KEY.format(loc="new-york-ny", day="2026-09-26"))["hours"]
                   if r["t"] == iso(gone))
        self.assertEqual((row["rtma"]["temp"], row["rtma"].get("late")), (98.6, True))

    def test_the_day_is_written_as_raw_and_processed_csv(self):
        t = hour(2026, 9, 26, 19)
        self.fx.add_hour("rtma", t)
        self.fx.add_precip("rtma", t + dt.timedelta(hours=1))
        self.fx.values[("rtma", "precip", iso(t + dt.timedelta(hours=1)))] = 2.54
        self.assertEqual(self.run_pass(t + dt.timedelta(hours=1, minutes=50)), 0)
        raw = self.st.get(analysis.CSV_RAW_KEY.format(day="2026-09-26")).decode().splitlines()
        self.assertEqual(raw[0], ",".join(analysis.RAW_HEADER))
        ny = [l.split(",") for l in raw[1:] if l.split(",")[1] == "new-york-ny" and l.split(",")[2] == "rtma"]
        row = next(r for r in ny if r[4] == iso(t))
        self.assertEqual(row[3:10], ["15", iso(t), "293.15", "5.0", "8.0", "2.54", iso(t + dt.timedelta(hours=1))])
        self.assertEqual(row[10:], ["nodd", "nodd"])
        day = self.st.get(analysis.CSV_DAY_KEY.format(day="2026-09-26")).decode().splitlines()
        self.assertEqual(day[0], ",".join(analysis.DAY_HEADER))
        p = next(l.split(",") for l in day[1:] if l.startswith("2026-09-26,new-york-ny,New York,NY,rtma,"))
        self.assertEqual(p[9:11], ["68", "68.0"])                               # high, whole and exact

    def test_listed_days_without_their_csv_files_get_them_a_few_a_pass(self):
        t = hour(2026, 9, 26, 19)
        self.fx.add_hour("rtma", t)
        self.assertEqual(self.run_pass(), 0)
        # a day written before the CSV files existed: its day file is there, its CSVs are not
        self.st.put(analysis.DAY_KEY.format(day="2026-09-24"), b'{"locations": {}}', "application/json")
        for k in (analysis.CSV_RAW_KEY, analysis.CSV_DAY_KEY):
            self.assertIsNone(self.st.get(k.format(day="2026-09-24")))
        status = {"errors": [], "failed": 0}
        analysis.csv_fill(self.st, analysis.load_state(self.st), LOCS, {}, NOW, archive.Deadline(600), status)
        self.assertEqual(status["csvFilled"], {"days": 1, "left": 0})
        # a day with no hours has no rows, so no files are written for it, and it is left to the next pass
        self.assertIsNone(self.st.get(analysis.CSV_RAW_KEY.format(day="2026-09-24")))
        self.assertIsNotNone(self.st.get(analysis.CSV_RAW_KEY.format(day="2026-09-26")))

    def test_a_message_on_another_grid_is_refused_and_reported(self):
        t = hour(2026, 9, 26, 19)
        self.fx.add_hour("rtma", t)
        self.fx.grids[("rtma", "TMP", iso(t))] = "other"
        self.assertEqual(self.run_pass(), 1)                   # the only read this pass failed
        self.assertIsNone(self.read(analysis.hour_key("rtma", t)))
        self.assertEqual(archive.LAST_STATUS["failed"], 1)
        self.assertEqual(self.st.puts[-1][0], analysis.INDEX_KEY)
        # skipped like a missing file: a gap for the next passes, the walk past it
        state = self.state()
        self.assertEqual(state["products"]["rtma"]["gaps"], [iso(t)])
        self.assertEqual(state["products"]["rtma"]["scanned"], iso(t))

    def test_a_raising_fetch_on_one_product_costs_nothing_else(self):
        # a NOAA 5xx that outlives the retries raises out of gov_weather; the
        # urma hour is recorded against urma and everything else is written
        t_r, t_u = hour(2026, 9, 26, 19), hour(2026, 9, 26, 13)
        self.fx.add_hour("rtma", t_r)
        self.fx.add_hour("urma", t_u)
        self.fx.raise_for = lambda url: "urma2p5" in url and not url.endswith(".idx")
        self.assertEqual(self.run_pass(), 0)
        self.assertIsNotNone(self.read(analysis.hour_key("rtma", t_r)))
        self.assertIsNone(self.read(analysis.hour_key("urma", t_u)))
        self.assertIsNotNone(self.read(analysis.DAY_KEY.format(day="2026-09-26")))
        self.assertIsNotNone(self.state())
        self.assertIsNotNone(self.read(analysis.INDEX_KEY))
        self.assertEqual(self.st.puts[-1][0], analysis.INDEX_KEY)
        self.assertEqual(archive.LAST_STATUS["errors"], 0)     # something was read
        # the hour's read, and the precipitation of the first absent hour the
        # backfill reaches; the absent hours after it are walked past
        # without their precipitation, so an outage costs one of each
        self.assertEqual(archive.LAST_STATUS["failed"], 2)
        self.assertEqual(self.health()["urma"]["fail_streak"], 1)
        self.assertEqual(self.health()["rtma"]["fail_streak"], 0)
        self.assertEqual(self.state()["products"]["urma"]["gaps"], [iso(t_u)])
        # persistently failing, the product reaches the alarm streak
        alarms = None
        for i in range(1, archive.FAIL_STREAK_ALARM):
            self.run_pass(NOW + dt.timedelta(minutes=10 * i))
            alarms = archive.LAST_STATUS["alarms"]
        self.assertEqual(self.health()["urma"]["fail_streak"], archive.FAIL_STREAK_ALARM)
        self.assertEqual(alarms, ["analysis: no urma hour readable for %d passes" % archive.FAIL_STREAK_ALARM])
        self.assertEqual(self.st.puts[-1][0], analysis.INDEX_KEY)
        self.assertEqual(self.read(analysis.INDEX_KEY)["sources"]["rtma"]["latest"], iso(t_r))

    def test_a_raising_decode_is_the_hours_error_not_the_pass(self):
        t = hour(2026, 9, 26, 19)
        self.fx.add_hour("rtma", t)
        keep = self.fx.decode_cells

        def refuse(msg, ks):
            if Fakes.parts(msg)[2] == "TMP":
                raise ValueError("data representation template 5.40 is not supported")
            return keep(msg, ks)
        analysis.grib2.decode_cells = refuse
        self.assertEqual(self.run_pass(), 1)
        self.assertIsNotNone(self.state())
        self.assertIsNotNone(self.read(analysis.INDEX_KEY))
        self.assertIsNotNone(self.read(analysis.GRID_INDEX_KEY))
        self.assertEqual(self.health()["rtma"]["fail_streak"], 1)
        self.assertIn("rtma 2026-09-26T19:00:00Z: ValueError: data representation template 5.40 is not supported",
                      self.errors)
        self.assertEqual(self.st.puts[-1][0], analysis.INDEX_KEY)

    def test_the_alarm_fires_when_nothing_lands_and_asof_stays_on_the_hour_read(self):
        t_r, t_u = hour(2026, 9, 26, 19), hour(2026, 9, 26, 13)
        self.fx.add_hour("rtma", t_r)
        self.fx.add_hour("urma", t_u)
        self.assertEqual(self.run_pass(), 0)
        alarms = []
        for i in range(1, 8):
            self.run_pass(NOW + dt.timedelta(hours=i))
            alarms = archive.LAST_STATUS["alarms"]
        self.assertEqual(sorted(alarms), ["analysis: no rtma hour readable for 6 passes",
                                          "analysis: no urma hour readable for 6 passes"])
        state = self.state()
        self.assertEqual(state["products"]["rtma"]["newest"], iso(t_r))
        self.assertEqual(state["products"]["rtma"]["scanned"], iso(analysis.expected_latest("rtma", NOW + dt.timedelta(hours=7))))
        idx = self.read(analysis.INDEX_KEY)
        self.assertEqual(idx["asof"], iso(t_r))
        self.assertEqual(idx["sources"]["rtma"]["latest"], iso(t_r))
        self.assertEqual(idx["sources"]["urma"]["latest"], iso(t_u))
        self.assertEqual(self.read(analysis.GRID_INDEX_KEY)["asof"], iso(t_r))
        # the hours that never landed are gaps, retried first when they do
        self.assertEqual(len(state["products"]["rtma"]["gaps"]), 7)
        self.fx.add_hour("rtma", t_r + dt.timedelta(hours=1))
        self.run_pass(NOW + dt.timedelta(hours=8))
        self.assertEqual(self.state()["products"]["rtma"]["newest"], iso(t_r + dt.timedelta(hours=1)))
        self.assertEqual(self.health()["rtma"]["fail_streak"], 0)

    def test_a_fresh_lane_is_overdue_once_it_has_looked_long_enough(self):
        for i in range(3):
            self.run_pass(NOW + dt.timedelta(hours=i))
        self.assertEqual(self.health()["rtma"]["fail_streak"], 1)     # two hours of looking, nothing read
        self.assertIsNone(self.read(analysis.INDEX_KEY)["asof"])

    def test_frames_are_indexed_as_soon_as_they_are_written(self):
        h1, h2 = hour(2026, 9, 26, 18), hour(2026, 9, 26, 19)
        self.fx.add_hour("rtma", h1)
        self.fx.add_hour("rtma", h2)
        state = analysis.new_state()
        state["products"]["rtma"]["newest"] = iso(hour(2026, 9, 26, 17))
        analysis.save_state(self.st, state, NOW)
        bad = analysis.anl_url("rtma", h2) + ".idx"
        self.fx.raise_for = lambda url: url == bad
        self.assertEqual(self.run_pass(), 0)
        gi = self.read(analysis.GRID_INDEX_KEY)
        self.assertEqual(gi["frames"]["rtma"]["temp"], {"2026-09-26": ["18"]})
        puts = [k for k, _, _ in self.st.puts]
        first_frame = next(i for i, k in enumerate(puts) if k.startswith("snapshots/analysis2/grid/rtma/"))
        self.assertIn(analysis.GRID_INDEX_KEY, puts[first_frame:first_frame + 6])
        self.assertEqual(self.state()["products"]["rtma"]["newest"], iso(h1))
        self.fx.raise_for = None
        self.run_pass(NOW + dt.timedelta(minutes=10))
        self.assertEqual(self.read(analysis.GRID_INDEX_KEY)["frames"]["rtma"]["temp"], {"2026-09-26": ["18", "19"]})

    def test_an_archived_hour_without_frames_gets_them_from_the_live_lane(self):
        t = hour(2026, 9, 26, 19)
        self.fx.add_hour("rtma", t)
        res = analysis.read_hour("rtma", t, LOCS, {"wexp": [], "g184": []}, frames=False, now=NOW)
        self.st.put(analysis.hour_key("rtma", t), gzip.compress(analysis._dump(res.doc)), "application/gzip")
        self.assertEqual(self.run_pass(), 0)
        self.assertIsNotNone(self.read(analysis.FRAME_KEY.format(product="rtma", var="temp", stamp="20260926T19Z")))
        self.assertEqual(self.read(analysis.GRID_INDEX_KEY)["frames"]["rtma"]["temp"], {"2026-09-26": ["19"]})
        self.assertEqual(self.state()["products"]["rtma"]["newest"], iso(t))

    def test_a_live_hour_is_archived_before_its_precipitation_which_is_retried_then_left(self):
        t = hour(2026, 9, 26, 19)
        self.fx.add_hour("rtma", t, precip=False)
        self.assertEqual(self.run_pass(), 0)
        # the analysis is archived at once and the precipitation is pending
        arc = analysis.get_hour(self.st, "rtma", t, {})
        self.assertEqual(arc["values"]["new-york-ny"]["temp"], 293.15)          # kelvin, as the file holds it
        self.assertIsNone(arc["values"]["new-york-ny"]["precip"])
        self.assertFalse(arc["precipRead"])
        self.assertIsNone(self.read(analysis.precip_key("rtma", t)))
        self.assertEqual(self.state()["products"]["rtma"]["newest"], iso(t))
        self.assertEqual(self.state()["products"]["rtma"]["precipPending"], [iso(t)])
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"]["rtma"]
        self.assertEqual(ny["hours"], 1)
        self.assertIsNone(ny["precip"])
        # the file lands: the next pass fills the precipitation key and the day
        self.fx.add_precip("rtma", t)
        self.fx.values[("rtma", "precip", iso(t))] = 2.54
        self.assertEqual(self.run_pass(NOW + dt.timedelta(minutes=10)), 0)
        self.assertTrue(self.read(analysis.precip_key("rtma", t))["complete"])
        self.assertEqual(self.state()["products"]["rtma"]["precipPending"], [])
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"]["rtma"]
        self.assertEqual(ny["precip"], {"value": 0.1, "exact": 0.1, "hours": 1, "resolved": False, "resolvedAt": None,
                                        "revised": None})
        # an hour whose file never lands is left with none once the wait runs out
        t2 = t + dt.timedelta(hours=1)
        self.fx.add_hour("rtma", t2, precip=False)
        self.assertEqual(self.run_pass(t2 + dt.timedelta(minutes=55)), 0)
        self.assertEqual(self.state()["products"]["rtma"]["precipPending"], [iso(t2)])
        later = t2 + dt.timedelta(minutes=47, hours=analysis.PRECIP_WAIT_HOURS + 1)
        self.assertEqual(self.run_pass(later), 0)
        self.assertEqual(self.state()["products"]["rtma"]["precipPending"], [])
        self.assertFalse(analysis.get_hour(self.st, "rtma", t2, {})["precipRead"])

    def test_a_partial_bitmap_is_retried_and_a_later_read_fills_the_place(self):
        # the western RFC region is absent from the first URMA precipitation
        # file (2026-09-27, 13Z to 19Z): Los Angeles has no value, the hour is
        # archived, and the retries fill it when the file is rewritten
        t = hour(2026, 9, 26, 13)
        self.fx.add_hour("urma", t)
        self.fx.values[("urma", "precip", iso(t))] = lambda k: None if k == LA_K else 0.254
        self.assertEqual(self.run_pass(), 0)
        pre = self.read(analysis.precip_key("urma", t))
        self.assertEqual((pre["read"], pre["complete"], pre["missing"]), (True, False, ["los-angeles-ca"]))
        self.assertEqual(pre["values"]["new-york-ny"], 0.254)                 # millimetres
        self.assertEqual(self.state()["products"]["urma"]["precipPending"], [iso(t)])
        self.assertEqual(self.state()["products"]["urma"]["newest"], iso(t))
        fr = self.read(analysis.FRAME_KEY.format(product="urma", var="precip", stamp="20260926T13Z"))
        self.assertEqual(fr["values"].count(None), 64000 - sum(1 for k in analysis.load_lattice()["wexp"] if k >= 0 and k != LA_K))
        day = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]
        self.assertIsNone(day["los-angeles-ca"]["urma"]["precip"])
        self.assertEqual(day["new-york-ny"]["urma"]["precip"]["value"], 0.01)
        # the same file again: nothing changes and nothing is rewritten
        n = len(self.st.puts)
        self.run_pass(NOW + dt.timedelta(minutes=10))
        self.assertFalse(any(k.startswith("archive/analysis2/precip/") for k, _, _ in self.st.puts[n:]))
        # the rewrite lands with the region: the key, the frame and the day are rewritten
        self.fx.values[("urma", "precip", iso(t))] = lambda k: 0.508 if k == LA_K else 0.254
        self.run_pass(NOW + dt.timedelta(minutes=20))
        pre = self.read(analysis.precip_key("urma", t))
        self.assertEqual((pre["complete"], pre["missing"], pre["values"]["los-angeles-ca"]), (True, [], 0.508))
        self.assertEqual(pre["values"]["new-york-ny"], 0.254)         # a stored value stands
        self.assertEqual(self.state()["products"]["urma"]["precipPending"], [])
        fr = self.read(analysis.FRAME_KEY.format(product="urma", var="precip", stamp="20260926T13Z"))
        self.assertEqual(fr["values"].count(None), 64000 - sum(1 for k in analysis.load_lattice()["wexp"] if k >= 0))
        day = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]
        self.assertEqual(day["los-angeles-ca"]["urma"]["precip"]["value"], 0.02)

    def test_the_backfill_walks_back_from_the_first_live_hour(self):
        t = hour(2026, 9, 26, 19)
        for back in range(0, 4):
            self.fx.add_hour("rtma", t - dt.timedelta(hours=back))
        self.assertEqual(self.run_pass(), 0)
        for back in range(1, 4):
            self.assertIsNotNone(self.read(analysis.hour_key("rtma", t - dt.timedelta(hours=back))), back)
        state = self.state()
        self.assertTrue(state["backfill"]["rtma"]["done"])
        self.assertEqual(archive.LAST_STATUS["backfilled"], 3)
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"]["rtma"]
        self.assertEqual(ny["hours"], 4)

    def test_the_backfill_starts_behind_a_late_newest_hour(self):
        # 2026-09-27: a fresh lane's first look found both products' newest
        # hours late; the backfill must start behind that hour, not wait for it
        t = hour(2026, 9, 26, 19)
        for back in range(1, 4):
            self.fx.add_hour("rtma", t - dt.timedelta(hours=back))
        self.assertEqual(self.run_pass(), 0)
        state = self.state()
        self.assertIsNone(state["products"]["rtma"]["newest"])
        self.assertEqual(state["products"]["rtma"]["gaps"], [iso(t)])
        # the walk went the whole thirty days back through the absent hours
        self.assertTrue(state["backfill"]["rtma"]["done"])
        self.assertEqual(archive.LAST_STATUS["backfilled"], 3)
        for back in range(1, 4):
            self.assertIsNotNone(self.read(analysis.hour_key("rtma", t - dt.timedelta(hours=back))), back)
        # the late hour lands: the live lane reads it as a gap
        self.fx.add_hour("rtma", t)
        self.assertEqual(self.run_pass(NOW + dt.timedelta(minutes=10)), 0)
        state = self.state()
        self.assertEqual(state["products"]["rtma"]["newest"], iso(t))
        self.assertEqual(state["products"]["rtma"]["gaps"], [])
        self.assertIsNotNone(self.read(analysis.hour_key("rtma", t)))

    def test_a_catch_up_span_is_queued_once_and_clamped_to_the_backfill(self):
        state = analysis.new_state()
        state["products"]["rtma"]["newest"] = iso(NOW - dt.timedelta(days=3))
        for _ in range(3):
            analysis.live_candidates("rtma", state, NOW)
        queue = state["backfill"]["rtma"]["queue"]
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0][1], iso(analysis.expected_latest("rtma", NOW) - dt.timedelta(hours=analysis.LIVE_HOURS_PER_PASS)))
        self.assertEqual(state["products"]["rtma"]["scanned"], queue[0][1])
        state = analysis.new_state()
        state["products"]["rtma"]["newest"] = iso(NOW - dt.timedelta(days=40))
        analysis.live_candidates("rtma", state, NOW)
        oldest = analysis._floor_hour(NOW) - dt.timedelta(days=analysis.BACKFILL_POINT_DAYS)
        self.assertEqual(state["backfill"]["rtma"]["queue"][0][0], iso(oldest))
        state["backfill"]["rtma"]["queue"] = [[iso(NOW - dt.timedelta(days=40)), iso(NOW - dt.timedelta(days=35))]]
        self.assertIsNone(analysis.backfill_next("rtma", state, NOW))
        self.assertEqual(state["backfill"]["rtma"]["queue"], [])

    def _resolve_ny_day(self, day="2026-09-20", precip_mm=0.254):
        """Archive all 24 URMA hours of a New York day and rebuild it, so the
        precipitation resolves."""
        state = analysis.new_state()
        cache, gi, touched = {}, {}, set()
        hs = analysis.local_day_hours(day, "America/New_York")
        for h in hs:
            self.fx.add_hour("urma", h)
            self.fx.values[("urma", "precip", iso(h))] = precip_mm
            res = analysis.read_hour("urma", h, LOCS, {"wexp": [], "g184": []}, frames=False, now=NOW)
            self.assertEqual(res.status, "ok")
            analysis.write_hour(self.st, "urma", h, res, gi, cache, NOW)
            touched |= analysis.touched_by(h, LOCS)
        # the last row carries the accumulation that ends at midnight, which
        # NOAA files under the next day's first hour
        end = hs[-1] + dt.timedelta(hours=1)
        self.fx.add_precip("urma", end)
        self.fx.values[("urma", "precip", iso(end))] = precip_mm
        analysis.write_precip(self.st, "urma", end, analysis.read_precip("urma", end, LOCS, None, NOW).doc, cache)
        touched |= analysis.touched_by(end, LOCS)
        analysis.rebuild_days(self.st, touched, LOCS, cache, NOW, state)
        return state

    def test_a_precipitation_reread_that_differs_by_a_hundredth_is_a_revision(self):
        state = self._resolve_ny_day()
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-20"))["locations"]["new-york-ny"]["urma"]
        self.assertEqual((ny["complete"], ny["final"]), (True, False))      # URMA compares, never final
        self.assertTrue(ny["precip"]["resolved"])
        self.assertEqual(ny["precip"]["exact"], 0.24)          # 24 hours of 0.254 mm
        self.assertIsNone(ny["precip"]["revised"])
        # the RFC rerun adds a hundredth of an inch at New York only
        for h in analysis.local_day_hours("2026-09-20", "America/New_York"):
            self.fx.values[("urma", "precip", iso(h))] = lambda k, h=h: 0.254 + (0.2645 if k == NY_K and h.hour == 12 else 0.0)
        later = NOW + dt.timedelta(days=1)
        status = {"errors": [], "read": [], "backfilled": 0}
        changed = analysis.reread_precip(self.st, "2026-09-20", state, LOCS, later, archive.Deadline(300), status)
        self.assertEqual(changed, {("new-york-ny", "2026-09-20")})
        rev = state["revisions"]["2026-09-20"]["new-york-ny"]
        self.assertEqual((rev["value"], rev["exact"], rev["at"]), (0.25, 0.2504, iso(later)))
        self.assertEqual(state["rereads"]["2026-09-20"], iso(later))
        analysis.rebuild_days(self.st, changed, LOCS, {}, later, state)
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-20"))["locations"]["new-york-ny"]["urma"]
        self.assertEqual(ny["precip"]["exact"], 0.24)          # the resolved total stands
        self.assertEqual(ny["precip"]["revised"], rev)
        loc = self.read(analysis.LOC_KEY.format(loc="new-york-ny", day="2026-09-20"))
        self.assertEqual(loc["summary"]["urma"]["precip"]["revised"], rev)
        # the stored hourly values stand too, so the rows still sum to the resolved total
        self.assertEqual(round(sum(r["urma"]["precip"] for r in loc["hours"]), 4), 0.24)
        # a rerun that differs by less than a hundredth is not a revision
        for h in analysis.local_day_hours("2026-09-20", "America/New_York"):
            self.fx.values[("urma", "precip", iso(h))] = lambda k, h=h: 0.254 + (0.12 if k == NY_K and h.hour == 12 else 0.0)
        changed = analysis.reread_precip(self.st, "2026-09-20", state, LOCS, later + dt.timedelta(days=1),
                                         archive.Deadline(300), status)
        self.assertEqual(changed, {("new-york-ny", "2026-09-20")})   # withdrawn: back within a hundredth
        self.assertIsNone(state["revisions"]["2026-09-20"]["new-york-ny"])   # kept as a withdrawal
        analysis.rebuild_days(self.st, changed, LOCS, {}, later, state)
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-20"))["locations"]["new-york-ny"]["urma"]
        self.assertIsNone(ny["precip"]["revised"])

    def test_a_reread_whose_rounded_hundredth_differs_is_a_revision(self):
        # owner's decision 2026-09-27: 0.2449 resolved (0.24) against 0.2451
        # re-read (0.25) is a revision though the exact totals differ by 0.0002
        state = self._resolve_ny_day(precip_mm=0.25918)          # 24 x 0.0102 in = 0.2448
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-20"))["locations"]["new-york-ny"]["urma"]
        self.assertEqual((ny["precip"]["value"], ny["precip"]["exact"]), (0.24, 0.2448))
        for h in analysis.local_day_hours("2026-09-20", "America/New_York"):
            self.fx.values[("urma", "precip", iso(h))] = lambda k, h=h: 0.25918 + (0.0055 if k == NY_K and h.hour == 12 else 0.0)
        status = {"errors": [], "read": [], "backfilled": 0}
        changed = analysis.reread_precip(self.st, "2026-09-20", state, LOCS, NOW + dt.timedelta(days=1), archive.Deadline(300), status)
        self.assertEqual(changed, {("new-york-ny", "2026-09-20")})
        rev = state["revisions"]["2026-09-20"]["new-york-ny"]
        self.assertEqual((rev["value"], rev["exact"]), (0.25, 0.2451))       # the exact total, cut

    def test_a_revision_survives_the_state_prune_through_the_day_file(self):
        state = self._resolve_ny_day()
        for h in analysis.local_day_hours("2026-09-20", "America/New_York"):
            self.fx.values[("urma", "precip", iso(h))] = lambda k, h=h: 0.254 + (0.2645 if k == NY_K and h.hour == 12 else 0.0)
        later = NOW + dt.timedelta(days=1)
        status = {"errors": [], "read": [], "backfilled": 0}
        changed = analysis.reread_precip(self.st, "2026-09-20", state, LOCS, later, archive.Deadline(300), status)
        analysis.rebuild_days(self.st, changed, LOCS, {}, later, state)
        analysis.prune_state(state, NOW + dt.timedelta(days=12))
        self.assertEqual((state["rereads"], state["revisions"]), ({}, {}))
        analysis.rebuild_days(self.st, {("new-york-ny", "2026-09-20")}, LOCS, {}, NOW + dt.timedelta(days=12), state)
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-20"))["locations"]["new-york-ny"]["urma"]
        self.assertEqual(ny["precip"]["revised"]["value"], 0.25)

    def test_a_reread_short_of_the_days_hours_says_nothing(self):
        state = self._resolve_ny_day()
        hs = analysis.local_day_hours("2026-09-20", "America/New_York")
        for h in hs:
            self.fx.values[("urma", "precip", iso(h))] = 2.54
        self.fx.drop_precip("urma", hs[3])
        status = {"errors": [], "read": [], "backfilled": 0}
        changed = analysis.reread_precip(self.st, "2026-09-20", state, LOCS, NOW + dt.timedelta(days=1),
                                         archive.Deadline(300), status)
        self.assertEqual(changed, set())
        self.assertEqual(state.get("revisions"), {})

    def test_a_reread_fills_a_place_the_first_read_had_no_cell_for(self):
        # the west-coast cell was outside the bitmap at every hour of the
        # first read: Los Angeles has no total and the day cannot resolve
        # until a later read supplies it
        state = analysis.new_state()
        cache, gi, touched = {}, {}, set()
        day = "2026-09-20"
        hs = analysis.local_day_hours(day, "America/Los_Angeles")
        for h in hs:
            self.fx.add_hour("urma", h)
            self.fx.values[("urma", "precip", iso(h))] = lambda k: None if k == LA_K else 0.254
            res = analysis.read_hour("urma", h, LOCS, {"wexp": [], "g184": []}, frames=False, now=NOW)
            self.assertEqual(res.status, "ok")
            self.assertEqual(res.precip["missing"], ["los-angeles-ca"])
            analysis.write_hour(self.st, "urma", h, res, gi, cache, NOW)
            touched |= analysis.touched_by(h, LOCS)
        end = hs[-1] + dt.timedelta(hours=1)          # the accumulation that ends at midnight
        self.fx.add_precip("urma", end)
        self.fx.values[("urma", "precip", iso(end))] = lambda k: None if k == LA_K else 0.254
        analysis.write_precip(self.st, "urma", end, analysis.read_precip("urma", end, LOCS, None, NOW).doc, cache)
        analysis.rebuild_days(self.st, touched, LOCS, cache, NOW, state)
        la = self.read(analysis.DAY_KEY.format(day=day))["locations"]["los-angeles-ca"]["urma"]
        self.assertTrue(la["complete"])
        self.assertIsNone(la["precip"])
        self.assertEqual(analysis.reread_due(self.st, state, LOCS, NOW), day)   # complete but not resolved
        # the rerun carries the region; the re-read fills the keys and the day resolves
        for h in hs + [end]:
            self.fx.values[("urma", "precip", iso(h))] = lambda k: 0.508 if k == LA_K else 0.254
        later = NOW + dt.timedelta(days=1)
        status = {"errors": [], "read": [], "backfilled": 0}
        changed = analysis.reread_precip(self.st, day, state, LOCS, later, archive.Deadline(300), status)
        self.assertIn(("los-angeles-ca", day), changed)
        analysis.rebuild_days(self.st, changed, LOCS, {}, later, state)
        la = self.read(analysis.DAY_KEY.format(day=day))["locations"]["los-angeles-ca"]["urma"]
        self.assertEqual((la["precip"]["value"], la["precip"]["exact"], la["precip"]["resolved"]), (0.48, 0.48, True))
        self.assertEqual(la["precip"]["resolvedAt"], iso(later))
        self.assertEqual(state["revisions"], {})               # nothing was resolved before, so nothing is revised
        for h in hs[1:] + [end]:
            self.assertTrue(self.read(analysis.precip_key("urma", h))["complete"])

    def test_a_reread_is_due_once_a_day_for_a_complete_day(self):
        now = hour(2026, 9, 26, 19).replace(minute=55)
        state = self._resolve_ny_day("2026-09-24")
        self.assertEqual(analysis.reread_due(self.st, state, LOCS, now), "2026-09-24")
        state["rereads"]["2026-09-24"] = iso(now - dt.timedelta(hours=2))
        self.assertIsNone(analysis.reread_due(self.st, state, LOCS, now))
        state["rereads"]["2026-09-24"] = iso(now - dt.timedelta(hours=25))
        self.assertEqual(analysis.reread_due(self.st, state, LOCS, now), "2026-09-24")

    def test_a_day_whose_other_files_are_not_published_resolves_on_the_hours_available(self):
        # owner's decision 2026-09-29: a missing file does not hold a day open.
        # One hour lands; the pass two days later walks the rest of the day,
        # finds every other file on neither NOAA Open Data nor NOMADS, and the
        # day resolves on the one hour it has, never closed
        t = hour(2026, 9, 26, 19)
        self.fx.add_hour("rtma", t)
        self.assertEqual(self.run_pass(), 0)
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"]["rtma"]
        self.assertEqual((ny["final"], ny["closed"]), (False, False))
        later = analysis.day_end("2026-09-26", "America/Los_Angeles") + dt.timedelta(hours=49)
        self.assertEqual(self.run_pass(later), 0)
        day = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]
        self.assertEqual((day["new-york-ny"]["rtma"]["final"], day["new-york-ny"]["rtma"]["closed"]), (True, False))
        self.assertEqual((day["new-york-ny"]["rtma"]["hours"], day["new-york-ny"]["rtma"]["high"]["value"]), (1, 68))
        self.assertIn(iso(t - dt.timedelta(hours=1)), self.state()["products"]["rtma"]["missing"])

    def test_old_frames_are_pruned_once_a_day(self):
        old = NOW - dt.timedelta(days=analysis.FRAME_KEEP_DAYS + 1)
        gi = {"rtma": {"temp": {f"{old:%Y-%m-%d}": [f"{old:%H}"], "2026-09-26": ["19"]}}}
        for stamp in (analysis._stamp(old), "20260926T19Z"):
            self.st.put(analysis.FRAME_KEY.format(product="rtma", var="temp", stamp=stamp), b"{}", "application/json")
        removed = analysis.prune_frames(self.st, gi, NOW)
        self.assertEqual(removed, 1)
        self.assertEqual(self.st.deletes, [analysis.FRAME_KEY.format(product="rtma", var="temp", stamp=analysis._stamp(old))])
        self.assertEqual(gi["rtma"]["temp"], {"2026-09-26": ["19"]})

    def test_the_index_lists_the_newest_sixty_days_and_keeps_the_files(self):
        for i in range(70):
            d = (dt.date(2026, 9, 26) - dt.timedelta(days=i)).isoformat()
            self.st.put(analysis.DAY_KEY.format(day=d), b"{}", "application/json")
        analysis.write_index(self.st, analysis.new_state(), LOCS, {}, NOW)
        days = self.read(analysis.INDEX_KEY)["days"]
        self.assertEqual(len(days), analysis.INDEX_DAYS)
        self.assertEqual((days[0], days[-1]), ("2026-07-29", "2026-09-26"))
        self.assertEqual(len(self.st.list(analysis.PREFIX + "days/")), 70)

    def test_a_days_status_counts_every_place_and_only_resolved_ones_resolve_it(self):
        # the resolving product's entries (RTMA, owner's decision 2026-09-29)
        final = {"rtma": {"final": True, "precip": {"resolved": True}}}
        waiting = {"rtma": {"final": True, "precip": {"resolved": False}}}
        closed = {"rtma": {"final": False, "closed": True, "precip": {"resolved": False}}}
        doc = {"locations": {"new-york-ny": final, "chicago-il": waiting}}
        # URMA says nothing about resolution however final it looks
        doc["locations"]["los-angeles-ca"] = {"urma": {"final": True, "precip": {"resolved": True}}}
        # Los Angeles has no RTMA hours yet: it counts toward the places and nothing else
        self.assertEqual(analysis.day_status(doc, LOCS), {"places": 3, "final": 2, "resolved": 1, "closed": 0})
        doc["locations"]["los-angeles-ca"] = closed
        self.assertEqual(analysis.day_status(doc, LOCS), {"places": 3, "final": 2, "resolved": 1, "closed": 1})
        self.assertEqual(analysis.day_status({}, LOCS), {"places": 3, "final": 0, "resolved": 0, "closed": 0})

    def test_the_index_names_the_newest_day_every_place_has_resolved(self):
        final = {"rtma": {"final": True, "precip": {"resolved": True}}}
        waiting = {"rtma": {"final": True, "precip": {"resolved": False}}}
        docs = {"2026-09-23": {lid: final for lid in ("new-york-ny", "chicago-il", "los-angeles-ca")},
                "2026-09-24": {lid: final for lid in ("new-york-ny", "chicago-il", "los-angeles-ca")},
                # the West Coast's precipitation still waiting: not fully resolved
                "2026-09-25": {"new-york-ny": final, "chicago-il": final, "los-angeles-ca": waiting},
                "2026-09-26": {"new-york-ny": final}}
        for d, locs in docs.items():
            self.st.put(analysis.DAY_KEY.format(day=d), json.dumps({"locations": locs}).encode(), "application/json")
        state = analysis.new_state()
        state["dayStatus"]["2026-01-01"] = {"places": 3, "final": 3, "resolved": 3, "closed": 0}   # no longer listed
        days = analysis.index_days(self.st)
        self.assertEqual(analysis.refresh_day_status(self.st, state, LOCS, days), 4)
        self.assertNotIn("2026-01-01", state["dayStatus"])
        self.assertEqual(analysis.refresh_day_status(self.st, state, LOCS, days), 0)     # nothing read twice
        analysis.write_index(self.st, state, LOCS, {}, NOW, days=days)
        idx = self.read(analysis.INDEX_KEY)
        self.assertEqual(idx["lastResolvedDay"], "2026-09-24")
        self.assertEqual(idx["dayStatus"]["2026-09-25"], {"places": 3, "final": 3, "resolved": 2, "closed": 0})
        self.assertEqual(idx["dayStatus"]["2026-09-26"], {"places": 3, "final": 1, "resolved": 1, "closed": 0})
        self.assertEqual(sorted(idx["dayStatus"]), days)
        # no day resolved at all: the page falls back to the newest day
        self.assertIsNone(analysis.last_resolved_day(analysis.new_state(), days))

    def test_a_pass_keeps_the_status_of_the_days_it_rebuilds(self):
        t_r, t_u = hour(2026, 9, 26, 19), hour(2026, 9, 26, 13)
        self.fx.add_hour("rtma", t_r)
        self.fx.add_hour("urma", t_u)
        self.assertEqual(self.run_pass(), 0)
        st = self.state()["dayStatus"]
        idx = self.read(analysis.INDEX_KEY)
        self.assertEqual(idx["dayStatus"], {d: st[d] for d in idx["days"]})
        self.assertEqual(idx["dayStatus"]["2026-09-26"]["places"], 3)
        self.assertEqual(idx["dayStatus"]["2026-09-26"]["resolved"], 0)    # one URMA hour is not a day
        self.assertIsNone(idx["lastResolvedDay"])

    def test_the_lane_alarms_only_when_a_product_is_overdue_or_unreadable(self):
        state = analysis.new_state()
        status = {"read": [], "errors": [], "backfilled": 0, "backfilledBy": {"rtma": 0, "urma": 0}}
        h = analysis.health_results(state, status, NOW)
        self.assertFalse(h["rtma"]["attempted"])               # nothing expected yet: not a failure
        state["products"]["rtma"]["newest"] = iso(hour(2026, 9, 26, 15))
        h = analysis.health_results(state, status, NOW)
        self.assertTrue(h["rtma"]["attempted"] and not h["rtma"]["ok"])
        state["products"]["rtma"]["newest"] = iso(hour(2026, 9, 26, 18))   # an hour behind is the normal wait
        self.assertFalse(analysis.health_results(state, status, NOW)["rtma"]["attempted"])
        status["errors"] = ["rtma 2026-09-26T19:00:00Z: ValueError: refused"]
        self.assertTrue(analysis.health_results(state, status, NOW)["rtma"]["attempted"])
        status["read"] = ["rtma 2026-09-26T19:00:00Z"]
        self.assertTrue(analysis.health_results(state, status, NOW)["rtma"]["ok"])

    def test_a_failure_outside_the_product_lanes_counts_against_the_lane(self):
        # a refused snapshot write or a failed rebuild is not prefixed by a
        # product, and used to be invisible to the streak
        state = analysis.new_state()
        state["products"]["rtma"]["newest"] = iso(hour(2026, 9, 26, 18))
        status = {"read": [], "errors": ["pass: OSError: S3 PutObject refused: SlowDown"],
                  "backfilled": 0, "backfilledBy": {"rtma": 0, "urma": 0}}
        h = analysis.health_results(state, status, NOW)
        self.assertTrue(h["rtma"]["attempted"] and not h["rtma"]["ok"])
        self.assertTrue(h["urma"]["attempted"] and not h["urma"]["ok"])

    def test_a_message_valid_at_another_hour_is_refused(self):
        # NOAA names the objects by hour; a misfiled one would otherwise be
        # read as the hour asked for
        f = Fakes()
        f.install(self)
        orig = analysis.grib2.product_def
        analysis.grib2.product_def = lambda m: dict(orig(m), end=(2026, 9, 26, 12, 0, 0))
        msg = Fakes.msg("urma", "APCP", hour(2026, 9, 26, 12), "wexp")
        self.assertIsNone(analysis.check_message(msg, "precip", WEXP, hour(2026, 9, 26, 12)))
        why = analysis.check_message(msg, "precip", WEXP, hour(2026, 9, 26, 13))
        self.assertIn("valid", why or "")


class RangeFetch(unittest.TestCase):
    """gov_weather.fetch_range against a stand-in for urlopen: the exact
    body, a whole object sliced, and the clamped 206 S3 answers for a range
    past the end of the object (the last message of an analysis file)."""
    class Resp:
        def __init__(self, body, headers):
            self.body, self.headers = body, headers

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def serve(self, body, headers=None):
        calls = []

        def urlopen(req, timeout=None):
            calls.append((req.get_header("Range"), timeout))
            return self.Resp(body, headers or {})
        keep = gov_weather.urllib.request.urlopen
        gov_weather.urllib.request.urlopen = urlopen
        self.addCleanup(lambda: setattr(gov_weather.urllib.request, "urlopen", keep))
        return calls

    def test_the_exact_range_and_a_whole_object(self):
        calls = self.serve(b"3456789")
        self.assertEqual(gov_weather.fetch_range("https://x/y", 3, 9, tries=2, timeout=20), b"3456789")
        self.assertEqual(calls, [("bytes=3-9", 20)])
        self.serve(b"0123456789" * 2, {})                      # a 200 with the whole object is sliced
        self.assertEqual(gov_weather.fetch_range("https://x/y", 2, 4), b"234")

    def test_a_clamped_206_past_the_end_is_accepted(self):
        # the object is 100 bytes; the last message starts at 80 and the
        # caller asks for 80 to 80 + 16 MiB
        self.serve(b"m" * 20, {"Content-Range": "bytes 80-99/100"})
        self.assertEqual(gov_weather.fetch_range("https://x/y", 80, 80 + 16 * 1024 * 1024), b"m" * 20)

    def test_a_short_body_without_the_header_is_an_error(self):
        self.serve(b"m" * 20)
        with self.assertRaises(RuntimeError):
            gov_weather.fetch_range("https://x/y", 80, 200)
        self.serve(b"m" * 19, {"Content-Range": "bytes 80-99/100"})
        with self.assertRaises(RuntimeError):
            gov_weather.fetch_range("https://x/y", 80, 200)


class Config(unittest.TestCase):
    def test_the_sixty_seven_locations_and_the_lattice_are_checked_in(self):
        # the settlement list the owner approved on 2026-09-29
        self.assertEqual(len(ALL_LOCS), 67)
        ids = [l["id"] for l in ALL_LOCS]
        for want in ("new-york-ny", "los-angeles-ca", "nashville-tn", "louisville-ky", "indianapolis-in", "washington-dc",
                     "norfolk-va", "charleston-sc", "huntington-wv", "portland-or", "portland-me", "cheyenne-wy"):
            self.assertIn(want, ids)
        self.assertEqual(len(set(ids)), 67)
        ny = next(l for l in ALL_LOCS if l["id"] == "new-york-ny")
        self.assertEqual(ny["cell"]["wexp"], [2010, 857, 857 * 2345 + 2010])
        self.assertEqual(ny["cell"]["g184"], [1810, 857, 857 * 2145 + 1810])
        self.assertIn("City Hall", ny["position"])
        mia = next(l for l in ALL_LOCS if l["id"] == "miami-fl")
        self.assertEqual(mia["cell"]["wexp"][:2], [1870, 169])           # the nearest land cell, by ruling
        self.assertIn("water", mia["note"])
        for l in ALL_LOCS:
            self.assertEqual(len(l["cell"]["wexp"]), 3, l["id"])
            self.assertEqual(l["cell"]["g184"][:2], [l["cell"]["wexp"][0] - 200, l["cell"]["wexp"][1]], l["id"])
            self.assertLess(l["cell"]["distanceKm"], 2.0, l["id"])
            self.assertEqual(l["cell"]["wexp"][2], l["cell"]["wexp"][1] * 2345 + l["cell"]["wexp"][0])
            # the cell geometry of docs/analysis.md section 1, per grid
            for g in ("wexp", "g184"):
                px, box, basis = l["cell"][g + "Px"], l["cell"][g + "Box"], l["cell"][g + "Basis"]
                self.assertEqual((len(px), len(box), sorted(basis)), (2, 4, ["di", "dj"]), (l["id"], g))
                self.assertLess(abs(px[0] - l["px"]) + abs(px[1] - l["py"]), 1.0, (l["id"], g))   # the cell is at the dot
                for corner in box:
                    self.assertLess(abs(corner[0] - px[0]) + abs(corner[1] - px[1]), 1.0, (l["id"], g))
                for v in ("di", "dj"):
                    step = (basis[v][0] ** 2 + basis[v][1] ** 2) ** 0.5
                    self.assertTrue(0.44 < step < 0.53, (l["id"], g, v, step))   # 2.5 km at the fit's 5 km per unit
            # the two grids' nearest cells are the same ground to within a cell
            self.assertLess(abs(l["cell"]["wexpPx"][0] - l["cell"]["g184Px"][0])
                            + abs(l["cell"]["wexpPx"][1] - l["cell"]["g184Px"][1]), 0.6, l["id"])
        lat = analysis.load_lattice()
        self.assertEqual((lat["pitch"], lat["cols"], lat["rows"], lat["viewBox"]), (3, 320, 200, "0 0 960 600"))
        self.assertEqual(len(lat["wexp"]), 64000)
        self.assertEqual(len(lat["g184"]), 64000)
        self.assertTrue(all(-1 <= k < 2345 * 1597 for k in lat["wexp"]))

    def test_the_window_convention_is_stated_in_the_page_prose_rules(self):
        """docs/analysis.md section 1: the index's conventions carry an entry
        for the 21 x 21 window, and the page prints every entry as it is, so
        it takes the rules scripts/verify.py holds page copy to."""
        import re
        w = analysis.CONVENTIONS["window"]
        self.assertIn("21 by 21", w)
        self.assertIn("resolving cell", w)
        self.assertIn("ten cells either way", w)
        for k, t in analysis.CONVENTIONS.items():
            self.assertIsNone(re.search(r"[a-z)][:]\s+[a-z]", t), (k, t))     # verify.py's colon rule
            self.assertNotIn("\u2014", t, k)                                    # and its dash rule
            self.assertNotIn(" -- ", t, k)
            self.assertIsNone(re.search(r"\bWhat this is not\b|\bis a [a-z ]+, not a\b", t), (k, t))

    def test_the_job_is_registered_and_packaged(self):
        from pipeline import run
        run._register()
        self.assertIs(run.JOBS["analysis"], analysis.analysis_pass)
        with open(os.path.join(ROOT, "scripts", "package_lambda.py")) as fh:
            src = fh.read()
        for item in ("config/analysis_locations.json", "config/analysis_lattice.json", "geo/settlement_locations.csv"):
            self.assertIn(item, src)
        with open(os.path.join(ROOT, "ops", "aws", "template.yaml")) as fh:
            tpl = fh.read()
        self.assertIn("cron(8/10 * * * ? *)", tpl)
        self.assertIn('{"job": "analysis"}', tpl)
        self.assertIn("five EventBridge schedules", tpl)


# ------------------------------------------------------------------ the build script's geometry
class CellGeometry(unittest.TestCase):
    """The real Lambert inverse and Albers fit for one place: the linear
    frame the page draws the window with must land on the exact projected
    cell centres out to ten cells either way, and the box on the exact
    corners, within FRAME_TOL viewBox units (0.02; the measured worst over
    the fifty is 0.0092)."""
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import build_analysis_grid   # noqa: E402
        from pipeline import basemap, grib2   # noqa: E402
        cls.bag, cls.grib2 = build_analysis_grid, grib2
        cls.tr = basemap.Transform.from_json(basemap.load_field_grid()["transform"])

    def test_the_basis_reproduces_the_projected_window_cells_for_new_york(self):
        i, j = NY["cell"]["wexp"][0], NY["cell"]["wexp"][1]
        geom = self.bag.cell_geometry(self.grib2.WEXP, i, j, self.tr)
        self.assertEqual(geom["px"], NY["cell"]["wexpPx"])
        self.assertEqual(geom["box"], NY["cell"]["wexpBox"])
        self.assertEqual(geom["basis"], NY["cell"]["wexpBasis"])
        cx, cy = geom["px"]
        di, dj = geom["basis"]["di"], geom["basis"]["dj"]
        worst = 0.0
        for a in (-10, 0, 10):
            for b in (-10, 0, 10):
                ex, ey = self.bag.cell_px(self.grib2.WEXP, i + a, j + b, self.tr)
                err = ((cx + a * di[0] + b * dj[0] - ex) ** 2 + (cy + a * di[1] + b * dj[1] - ey) ** 2) ** 0.5
                worst = max(worst, err)
                self.assertLess(err, self.bag.FRAME_TOL, (a, b, err))
        for n, (sa, sb) in enumerate(((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5))):
            bx, by = geom["box"][n]
            err = ((cx + sa * di[0] + sb * dj[0] - bx) ** 2 + (cy + sa * di[1] + sb * dj[1] - by) ** 2) ** 0.5
            self.assertLess(err, self.bag.FRAME_TOL, (n, err))
        e = self.bag.frame_error(self.grib2.WEXP, i, j, self.tr, geom)
        self.assertLess(e["cells"], self.bag.FRAME_TOL)
        self.assertLess(e["corners"], self.bag.FRAME_TOL)
        self.assertGreaterEqual(e["cells"], worst - 1e-9)                # the whole window is at least the corners
        # the centre of the resolving cell is where the Lambert inverse puts it
        lat, lon = self.grib2.lcc_latlon(self.grib2.WEXP, i, j)
        self.assertEqual([round(lat, 4), round(lon, 4)], NY["cell"]["centre"])
        self.assertEqual([round(v, 3) for v in self.tr.project(lon, lat)], geom["px"])
        # j runs north on this grid, so dj points up the screen; i runs east
        self.assertLess(dj[1], 0)
        self.assertGreater(di[0], 0)

    def test_the_checked_in_geometry_is_what_the_script_computes(self):
        for l in ALL_LOCS:
            for name, grid in (("wexp", self.grib2.WEXP), ("g184", self.grib2.G184)):
                geom = self.bag.cell_geometry(grid, l["cell"][name][0], l["cell"][name][1], self.tr)
                self.assertEqual(geom["px"], l["cell"][name + "Px"], (l["id"], name))
                self.assertEqual(geom["box"], l["cell"][name + "Box"], (l["id"], name))
                self.assertEqual(geom["basis"], l["cell"][name + "Basis"], (l["id"], name))

    def test_the_tolerance_is_just_above_the_measured_worst_case(self):
        self.assertEqual(self.bag.FRAME_TOL, 0.02)
        worst = self.bag.frame_sanity(ALL_LOCS, self.tr)
        self.assertLess(worst["cells"], 0.01)          # the comments say about 0.01 units
        self.assertLess(worst["corners"], 0.002)

    def test_the_position_dot_sits_inside_the_outlined_resolving_cell(self):
        """The page draws the position (the City Hall) at (px, py) and the
        resolving cell round its centre; written to a tenth such a dot crossed
        the outline, so px and py carry three decimals and the dot's offset
        from the centre, solved through the basis, stays inside the half-cell
        box at every place on both grids, except where the settlement list
        records why the cell is another (Miami's own square is water)."""
        for l in ALL_LOCS:
            self.assertEqual(l["px"], round(l["px"], 3), l["id"])
            self.assertEqual(l["py"], round(l["py"], 3), l["id"])
            if l["id"] in self.bag.NOT_NEAREST:
                continue
            for g in ("wexp", "g184"):
                cx, cy = l["cell"][g + "Px"]
                di, dj = l["cell"][g + "Basis"]["di"], l["cell"][g + "Basis"]["dj"]
                ex, ey = l["px"] - cx, l["py"] - cy
                det = di[0] * dj[1] - di[1] * dj[0]
                a = (ex * dj[1] - ey * dj[0]) / det
                b = (di[0] * ey - di[1] * ex) / det
                self.assertLessEqual(max(abs(a), abs(b)), 0.5, (l["id"], g, a, b))


if __name__ == "__main__":
    unittest.main()
