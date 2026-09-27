"""The analysis job on the local storage backend, with the decoder and the
fetchers replaced by fakes: a product-hour lands in the archive with its four
frames and the grid index; the day rules (half-up rounding away from zero,
strict inequalities, a partial day's count, a closed incomplete day, a
precipitation revision, the local day that spans two UTC directories, the
23- and 25-hour daylight-time days); index.json is the last write; a missing
object is an absence and never a raised error; a raising fetch or decode is
recorded against its product and the pass still writes everything else; the
alarm fires when NOAA stops landing hours; precipitation coverage fills in
through its own rewritable key. The fake bucket holds synthetic GRIB2
"messages" that carry only their identity, and the fake fields answer by
cell index so no 3.7 million floats are ever built. No network, and no
dependence on pipeline/grib2.py being present."""
import contextlib
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
    """The local backend, counting writes and deletes."""
    def __init__(self, root):
        super().__init__(root)
        self.puts, self.deletes = [], []

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
LA_K = LA["cell"]["wexp"][2]
LA_K184 = LA["cell"]["g184"][2]
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
        self.assertEqual((s["high"]["value"], s["high"]["exact"]), (70, 70.5))   # rounded once, from the value itself
        s = analysis.day_summary(hours_of([60.0] * 4, wind=20.45), "America/New_York", "2026-09-26", NOW)
        self.assertEqual(s["wind"], {"value": 20, "exact": 20.45})

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
        self.assertEqual(analysis.resolves("low", -21, -21), False)
        self.assertEqual(analysis.resolves("low", -21, -20), True)

    def test_units_are_exact(self):
        self.assertAlmostEqual(analysis.k_to_f(273.15), 32.0)
        self.assertAlmostEqual(analysis.k_to_f(310.15), 98.6)
        self.assertAlmostEqual(analysis.ms_to_mph(10.0), 22.369362921)
        self.assertAlmostEqual(analysis.mm_to_inch(25.4), 1.0)

    def test_strict_inequalities_on_a_ladder(self):
        # high 71: Yes below the value, No at it and above it
        self.assertEqual([analysis.resolves("high", 71, k) for k in (69, 70, 71, 72)], [True, True, False, False])
        # low 58: No at and below the value, Yes above it
        self.assertEqual([analysis.resolves("low", 58, k) for k in (57, 58, 59, 60)], [False, False, True, True])
        self.assertEqual([analysis.resolves("precip", 0.49, k) for k in (0.25, 0.49, 0.5)], [True, False, False])
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
        self.assertEqual(s["wind"], {"value": 10, "exact": 10.36})   # the mean is kept to a hundredth
        self.assertEqual(s["precip"]["exact"], 0.492)
        self.assertEqual(s["precip"]["value"], 0.49)
        self.assertFalse(s["final"])                         # RTMA is always provisional
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

    def test_a_complete_urma_day_is_final_and_its_precipitation_resolved_once(self):
        ph = hours_of([60.0] * 24, precip=0.01)
        s = analysis.day_summary(ph, "America/New_York", "2026-09-26", NOW, product="urma")
        self.assertTrue(s["final"])
        self.assertEqual(s["precip"]["resolved"], True)
        self.assertEqual(s["precip"]["resolvedAt"], iso(NOW))     # the pass time, not an analysis time
        self.assertEqual(s["precip"]["exact"], 0.24)
        # rebuilt later with a prior: the stamp and the total are kept
        later = NOW + dt.timedelta(hours=5)
        s2 = analysis.day_summary(ph, "America/New_York", "2026-09-26", later, product="urma", prior=s,
                                  revised={"value": 0.26, "exact": 0.2604, "at": iso(later)})
        self.assertEqual(s2["precip"]["resolvedAt"], iso(NOW))
        self.assertEqual(s2["precip"]["exact"], 0.24)
        self.assertEqual(s2["precip"]["revised"]["value"], 0.26)

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
        s = analysis.day_summary(ph, "America/New_York", "2026-03-08", NOW, product="urma")
        self.assertEqual((s["hours"], s["of"], s["complete"], s["final"]), (23, 23, True, True))
        self.assertEqual(s["precip"]["exact"], 1.22)               # 22 x 0.01 + 1.00
        self.assertEqual(s["precip"]["hours"], 23)
        self.assertTrue(s["precip"]["resolved"])
        self.assertEqual(s["wind"]["exact"], round((22 * 10.0 + 100.0) / 23, 2))
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
        # the archive hour, write once, with the exact values in the site's units
        arc = self.read(analysis.hour_key("rtma", t_r))
        self.assertEqual(arc["schema"], "analysis/1")
        self.assertEqual(arc["valid"], "2026-09-26T19:00:00Z")
        self.assertEqual(arc["values"]["new-york-ny"], {"temp": 80.6, "wind": 11.2, "gust": 17.9})
        self.assertEqual(arc["values"]["chicago-il"]["temp"], 68.0)
        # the precipitation in its own key, and the two merged for the day rebuild
        pre = self.read(analysis.precip_key("rtma", t_r))
        self.assertEqual((pre["read"], pre["complete"], pre["missing"]), (True, True, []))
        self.assertEqual(pre["values"]["new-york-ny"], 0.1)
        merged = analysis.get_hour(self.st, "rtma", t_r, {})
        self.assertEqual(merged["values"]["new-york-ny"], {"temp": 80.6, "wind": 11.2, "gust": 17.9, "precip": 0.1})
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
        self.assertEqual(row["rtma"]["precip"], 0.1)
        self.assertIsNone(row["urma"])
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
                self.assertEqual(ctype, "application/json", key)
                self.assertIn(cache, (analysis.CACHE_FINAL, analysis.CACHE_LIVE), key)
        self.assertEqual(archive.LAST_STATUS["job"], "analysis")
        self.assertEqual(archive.LAST_STATUS["errors"], 0)
        self.assertEqual(archive.LAST_STATUS["read"], 2)
        # the archive hour is written once: a second pass reads nothing new and rewrites no frame
        n = len(self.st.puts)
        self.assertEqual(self.run_pass(), 0)
        self.assertFalse(any(k.startswith("snapshots/analysis/grid/rtma/") for k, _, _ in self.st.puts[n:]))
        self.assertFalse(any(k.startswith("archive/analysis/") and not k.endswith("state.json") for k, _, _ in self.st.puts[n:]))
        self.assertEqual(self.st.puts[-1][0], analysis.INDEX_KEY)

    def test_the_place_file_carries_the_hourly_precipitation_to_a_ten_thousandth(self):
        # owner's decision 2026-09-27: the hour table adds up to the exact total
        t = hour(2026, 9, 26, 19)
        self.fx.add_hour("rtma", t)
        self.fx.values[("rtma", "precip", iso(t))] = lambda k: 1.0      # 1 mm is 0.03937 in
        self.assertEqual(self.run_pass(), 0)
        row = next(r for r in self.read(analysis.LOC_KEY.format(loc="new-york-ny", day="2026-09-26"))["hours"]
                   if r["t"] == iso(t))
        self.assertEqual(row["rtma"]["precip"], 0.0394)
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"]["rtma"]
        self.assertEqual(ny["precip"], {"value": 0.04, "exact": 0.0394, "hours": 1})

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
        self.assertEqual(archive.LAST_STATUS["failed"], 1)
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
        first_frame = next(i for i, k in enumerate(puts) if k.startswith("snapshots/analysis/grid/rtma/"))
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
        self.assertEqual(arc["values"]["new-york-ny"]["temp"], 68.0)
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
        self.assertEqual(ny["precip"], {"value": 0.1, "exact": 0.1, "hours": 1})
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
        self.assertEqual(pre["values"]["new-york-ny"], 0.01)
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
        self.assertFalse(any(k.startswith("archive/analysis/precip/") for k, _, _ in self.st.puts[n:]))
        # the rewrite lands with the region: the key, the frame and the day are rewritten
        self.fx.values[("urma", "precip", iso(t))] = lambda k: 0.508 if k == LA_K else 0.254
        self.run_pass(NOW + dt.timedelta(minutes=20))
        pre = self.read(analysis.precip_key("urma", t))
        self.assertEqual((pre["complete"], pre["missing"], pre["values"]["los-angeles-ca"]), (True, [], 0.02))
        self.assertEqual(pre["values"]["new-york-ny"], 0.01)          # a stored value stands
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
        for h in analysis.local_day_hours(day, "America/New_York"):
            self.fx.add_hour("urma", h)
            self.fx.values[("urma", "precip", iso(h))] = precip_mm
            res = analysis.read_hour("urma", h, LOCS, {"wexp": [], "g184": []}, frames=False, now=NOW)
            self.assertEqual(res.status, "ok")
            analysis.write_hour(self.st, "urma", h, res, gi, cache, NOW)
            touched |= analysis.touched_by(h, LOCS)
        analysis.rebuild_days(self.st, touched, LOCS, cache, NOW, state)
        return state

    def test_a_precipitation_reread_that_differs_by_a_hundredth_is_a_revision(self):
        state = self._resolve_ny_day()
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-20"))["locations"]["new-york-ny"]["urma"]
        self.assertTrue(ny["final"])
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
        self.assertEqual((rev["value"], rev["exact"]), (0.25, 0.245))

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
        for h in analysis.local_day_hours(day, "America/Los_Angeles"):
            self.fx.add_hour("urma", h)
            self.fx.values[("urma", "precip", iso(h))] = lambda k: None if k == LA_K else 0.254
            res = analysis.read_hour("urma", h, LOCS, {"wexp": [], "g184": []}, frames=False, now=NOW)
            self.assertEqual(res.status, "ok")
            self.assertEqual(res.precip["missing"], ["los-angeles-ca"])
            analysis.write_hour(self.st, "urma", h, res, gi, cache, NOW)
            touched |= analysis.touched_by(h, LOCS)
        analysis.rebuild_days(self.st, touched, LOCS, cache, NOW, state)
        la = self.read(analysis.DAY_KEY.format(day=day))["locations"]["los-angeles-ca"]["urma"]
        self.assertTrue(la["complete"])
        self.assertIsNone(la["precip"])
        self.assertEqual(analysis.reread_due(self.st, state, LOCS, NOW), day)   # complete but not resolved
        # the rerun carries the region; the re-read fills the keys and the day resolves
        for h in analysis.local_day_hours(day, "America/Los_Angeles"):
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
        for h in analysis.local_day_hours(day, "America/Los_Angeles"):
            self.assertTrue(self.read(analysis.precip_key("urma", h))["complete"])

    def test_a_reread_is_due_once_a_day_for_a_complete_day(self):
        now = hour(2026, 9, 26, 19).replace(minute=55)
        state = self._resolve_ny_day("2026-09-24")
        self.assertEqual(analysis.reread_due(self.st, state, LOCS, now), "2026-09-24")
        state["rereads"]["2026-09-24"] = iso(now - dt.timedelta(hours=2))
        self.assertIsNone(analysis.reread_due(self.st, state, LOCS, now))
        state["rereads"]["2026-09-24"] = iso(now - dt.timedelta(hours=25))
        self.assertEqual(analysis.reread_due(self.st, state, LOCS, now), "2026-09-24")

    def test_an_open_day_past_its_close_time_is_closed_without_a_new_hour(self):
        t = hour(2026, 9, 26, 19)
        self.fx.add_hour("rtma", t)
        self.assertEqual(self.run_pass(), 0)
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"]["rtma"]
        self.assertFalse(ny["closed"])
        later = analysis.day_end("2026-09-26", "America/Los_Angeles") + dt.timedelta(hours=49)
        self.assertEqual(self.run_pass(later), 0)
        day = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]
        self.assertTrue(day["new-york-ny"]["rtma"]["closed"])
        self.assertTrue(day["los-angeles-ca"]["rtma"]["closed"])
        self.assertEqual(day["new-york-ny"]["rtma"]["hours"], 1)

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
    def test_the_fifty_locations_and_the_lattice_are_checked_in(self):
        self.assertEqual(len(ALL_LOCS), 50)
        ids = [l["id"] for l in ALL_LOCS]
        for want in ("new-york-ny", "los-angeles-ca", "nashville-tn", "louisville-ky", "indianapolis-in", "washington-dc"):
            self.assertIn(want, ids)
        self.assertEqual(len(set(ids)), 50)
        sf = next(l for l in ALL_LOCS if l["id"] == "san-francisco-ca")
        self.assertEqual((sf["lat"], sf["lon"]), (37.7793, -122.4193))
        self.assertIn("City Hall", sf["note"])
        for l in ALL_LOCS:
            self.assertEqual(len(l["cell"]["wexp"]), 3, l["id"])
            self.assertLess(l["cell"]["distanceKm"], 2.0, l["id"])
            self.assertEqual(l["cell"]["wexp"][2], l["cell"]["wexp"][1] * 2345 + l["cell"]["wexp"][0])
        lat = analysis.load_lattice()
        self.assertEqual((lat["pitch"], lat["cols"], lat["rows"], lat["viewBox"]), (3, 320, 200, "0 0 960 600"))
        self.assertEqual(len(lat["wexp"]), 64000)
        self.assertEqual(len(lat["g184"]), 64000)
        self.assertTrue(all(-1 <= k < 2345 * 1597 for k in lat["wexp"]))

    def test_the_job_is_registered_and_packaged(self):
        from pipeline import run
        run._register()
        self.assertIs(run.JOBS["analysis"], analysis.analysis_pass)
        with open(os.path.join(ROOT, "scripts", "package_lambda.py")) as fh:
            src = fh.read()
        for item in ("config/analysis_locations.json", "config/analysis_lattice.json", "geo/population_centres.csv"):
            self.assertIn(item, src)
        with open(os.path.join(ROOT, "ops", "aws", "template.yaml")) as fh:
            tpl = fh.read()
        self.assertIn("cron(8/10 * * * ? *)", tpl)
        self.assertIn('{"job": "analysis"}', tpl)
        self.assertIn("five EventBridge schedules", tpl)


if __name__ == "__main__":
    unittest.main()
