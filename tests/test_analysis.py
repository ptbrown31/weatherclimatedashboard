"""The analysis job on the local storage backend, with the decoder and the
fetchers replaced by fakes: a product-hour lands in the archive with its four
frames and the grid index; the day rules (half-up rounding away from zero,
the at-least strike, a partial day's count, a closed incomplete day, the
local day that spans two UTC directories, the 23- and 25-hour daylight-time
days); index.json is the last write; a missing
object is an absence and never a raised error; a raising fetch or decode is
recorded against its product and the pass still writes everything else; the
alarm fires when NOAA stops landing hours; each server's copy of each file
is kept once with its times and hashes, the file settles once on the copy
that counts, a later replacement is recorded and never used, and NOMADS is
called within each pass's budget; each frame carries the 21 x 21 cell window
round every place at the field's own values, on the grid the file is on, and
the day rebuild never reads a frame. The fake bucket holds synthetic GRIB2
"messages" that carry only their identity, and the fake fields answer by
cell index so no 3.7 million floats are ever built. No network, and no
dependence on pipeline/grib2.py being present except in the cell geometry
tests at the end, which run the real Lambert and Albers maths of
scripts/build_analysis_grid.py for one place."""
import contextlib
import csv
from decimal import Decimal
import datetime as dt
import gzip
import io
import json
import os
import sys
import tempfile
import time
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
    URL: a matching fetch raises, the way gov_weather does after its tries.
    `published[url]` is the Last-Modified time a fetch reports for an
    object: an analysis file 48 minutes after its hour and a precipitation
    file 17 minutes after its hour ends unless a test says otherwise, and
    None for a server that sends none. A file goes on both servers unless
    `servers` names one; a `variant` gives NOMADS's copy other bytes, and
    `values` keyed with the variant as a fourth item other values."""
    def __init__(self):
        self.bucket = {}
        self.published = {}
        self.values = {}
        self.grids = {}
        self.fetched = []
        self.raise_for = None

    # ---- what a message is
    @staticmethod
    def msg(product, name, t, grid, variant=""):
        return f"GRIB|{product}|{name}|{iso(t)}|{grid}|{variant}".encode()

    @staticmethod
    def parts(msg):
        return msg.decode().split("|")

    def add_hour(self, product, t, precip=True, servers=analysis.SERVERS, variant=""):
        for server in servers:
            base = analysis.anl_url(product, t, nomads=server == "nomads")
            body, lines, off = b"", [], 0
            for n, (name, level) in enumerate(IDX_VARS, 1):
                m = self.msg(product, name, t, "wexp", variant if server == "nomads" else "")
                lines.append(f"{n}:{off}:d={t:%Y%m%d%H}:{name}:{level}:anl:")
                body += m
                off += len(m)
            self.bucket[base] = body
            self.bucket[base + ".idx"] = "\n".join(lines).encode() + b"\n"
            self.published.setdefault(base, iso(t + dt.timedelta(minutes=48)))
        if precip:
            self.add_precip(product, t, servers=servers, variant=variant)

    def add_precip(self, product, t, servers=analysis.SERVERS, variant=""):
        grid = analysis.PRODUCTS[product]["pcpGrid"]
        for server in servers:
            url = analysis.pcp_url(product, t, nomads=server == "nomads")
            self.bucket[url] = self.msg(product, "APCP", t, grid, variant if server == "nomads" else "")
            self.published.setdefault(url, iso(t + dt.timedelta(minutes=17)))

    def drop_precip(self, product, t):
        for nomads in (False, True):
            self.bucket.pop(analysis.pcp_url(product, t, nomads=nomads), None)

    def drop(self, product, t, server):
        """Take one server's copies of an hour's two files away."""
        nomads = server == "nomads"
        for u in (analysis.anl_url(product, t, nomads=nomads), analysis.anl_url(product, t, nomads=nomads) + ".idx",
                  analysis.pcp_url(product, t, nomads=nomads)):
            self.bucket.pop(u, None)

    # ---- gov_weather
    def _maybe_raise(self, url):
        if self.raise_for and self.raise_for(url):
            raise RuntimeError("HTTP Error 503: Service Unavailable")

    def _meta(self, url, data, meta):
        if data is not None and meta is not None:
            meta["lastModified"], meta["etag"] = self.published.get(url), None

    def fetch_bytes(self, url, tries=3, timeout=60, meta=None):
        self.fetched.append(url)
        self._maybe_raise(url)
        data = self.bucket.get(url)
        self._meta(url, data, meta)
        return data

    def fetch_range(self, url, start, end, tries=3, timeout=60, meta=None):
        self.fetched.append(f"{url}#{start}-{end}")
        self._maybe_raise(url)
        data = self.bucket.get(url)
        self._meta(url, data, meta)
        return None if data is None else data[start:end + 1]

    def fetch_head(self, url, tries=3, timeout=60):
        self.fetched.append(f"HEAD {url}")
        self._maybe_raise(url)
        if url not in self.bucket:
            return None
        return {"lastModified": self.published.get(url), "etag": None}

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
        v = self.values.get((p[1], VAR_OF.get(p[2], p[2]), p[3], p[5])) if p[5] else None
        if v is None:
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
                                        fetch_head=self.fetch_head, set_user_agent=self.set_user_agent,
                                        NODD_RTMA=analysis.NODD_DEFAULT["rtma"],
                                        NOMADS_RTMA=analysis.NOMADS_DEFAULT["rtma"])
        # no pause between NOMADS calls in the tests; the budget itself stands
        keep_gap = analysis.NOMADS_MIN_INTERVAL
        analysis.NOMADS_MIN_INTERVAL = 0
        case.addCleanup(lambda: setattr(analysis, "NOMADS_MIN_INTERVAL", keep_gap))
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


def values_of(cell):
    """The four hourly values of a place file's row, without the fields
    beside them (the shown values, the running values, the file times)."""
    return None if cell is None else {k: cell.get(k) for k in ("temp", "wind", "gust", "precip")}


# what rowShape 3 added to a row with a value (2026-11-04); a row with no value
# for the day whose hourly contract has a state carries only HOURLY_ONLY
ROW_SHAPE_3 = ("runX", "raw", "x", "anlFile", "pcpFile", "hourly")
HOURLY_ONLY = ["anlFile", "hourly", "pcpFile"]


def as_row_shape_2(text):
    """A rowShape 3 place file as the lane wrote it before 2026-11-04: the new
    fields taken away, a row left with nothing null, rowShape 2, the bytes
    analysis._dump makes."""
    doc = json.loads(text)
    doc["rowShape"] = 2
    for r in doc["hours"]:
        c = r.get("rtma")
        if c is not None:
            for k in ROW_SHAPE_3:
                c.pop(k, None)
            if not c:
                r["rtma"] = None
    return analysis._dump(doc).decode("utf-8")


def blank_status():
    """The status a pass starts with, for a lane called on its own."""
    return {"read": [], "pcpRead": [], "copies": [], "absent": [], "waiting": [], "errors": [], "failed": 0,
            "backfilled": 0, "backfilledBy": {"rtma": 0}, "days": 0, "pruned": 0}


def raw_product_hours(raw_rows):
    """day_summary's input for one place-day, made afresh from the day's raw
    CSV rows (the archive's own numbers, which the lane writes beside every
    place file) rather than taken from the lane: each value converted exactly,
    the millimetres kept, the publication times as published."""
    out = {}
    for r in raw_rows:
        num = lambda c: float(r[c]) if r[c] != "" else None            # noqa: E731
        out[r["valid_utc"]] = {
            "temp": analysis.exact("temp", num("temp_k")), "wind": analysis.exact("wind", num("wind_ms")),
            "gust": analysis.exact("gust", num("gust_ms")), "precip": analysis.exact("precip", num("precip_mm")),
            "mm": num("precip_mm"), "anl": r["analysis_source"] != "",
            "pub": r.get("analysis_published") or None, "pcpPub": r.get("precip_published") or None}
    return out


def check_row_shape_3(case, cell, raw_row, where):
    """One row's rowShape 3 fields against its own figures of before: the
    stored strings are the raw CSV's numbers, the exact strings cut and round
    to the row's exact and shown values, the day so far before rounding cuts
    and rounds to `run`, and the hourly contract's value is its exact string
    rounded once."""
    D = Decimal
    for var, col in (("temp", "temp_k"), ("wind", "wind_ms"), ("gust", "gust_ms"), ("precip", "precip_mm")):
        stored, x = cell["raw"][var], cell["x"][var]
        case.assertEqual(stored is None, raw_row[col] == "", where + (var,))
        if stored is None:
            case.assertIsNone(x, where + (var,))
            continue
        case.assertEqual(D(stored), D(raw_row[col]), where + (var,))
        case.assertEqual(len(x.split(".")[1]), analysis.TEXT_PLACES[var], where + (var,))
        case.assertEqual(analysis.cut(D(x), analysis.HOUR_SHOW[var]), cell[var], where + (var,))
        case.assertEqual(analysis.half_up(D(x), analysis.HOUR_UNIT[var]), cell["show"][var], where + (var,))
    run, run_x = cell["run"], cell["runX"]
    for k in ("high", "low", "gust", "wind"):
        case.assertEqual(run_x[k] is None, run[k] is None, where + (k,))
        if run[k] is not None:
            case.assertEqual((analysis.cut(D(run_x[k]), 0.001), analysis.half_up(D(run_x[k]), 1)),
                             (run[k]["exact"], run[k]["value"]), where + (k,))
    case.assertEqual(run_x["precip"] is None, run["precip"] is None, where)
    if run["precip"] is not None:
        case.assertEqual((analysis.cut(D(run_x["precip"]), 0.0001), analysis.half_up(D(run_x["precip"]), 0.01)),
                         (run["precip"]["exact"], run["precip"]["value"]), where)
    h = cell["hourly"]
    case.assertEqual(list(h), ["cut", "state", "at", "basis", "src", "value", "x", "late"], where)
    case.assertEqual(h["value"] is None, h["x"] is None, where)
    if h["value"] is not None:
        case.assertEqual(analysis.half_up(D(h["x"]), 1), h["value"], where)
    for f in ("anlFile", "pcpFile"):
        if cell[f] is not None:
            case.assertEqual(list(cell[f]), ["name", "nodd", "nomads", "src", "counts", "afterCut"], where + (f,))
    case.assertEqual(cell["anlFile"]["counts"], raw_row["analysis_source"] != "" and not cell.get("late"), where)


def check_running(case, loc_doc, raw_rows):
    """Every hour of one place file against a fresh resolution of the hours
    so far: its running values equal day_summary over the raw rows up to it,
    its shown values are its own exact values rounded once, its rowShape 3
    fields agree with them (check_row_shape_3), and the day's last counted
    hour (the last analysis and the last accumulation that count) carries the
    day's own summary. Returns how many rows were checked."""
    tz, day, now = loc_doc["tz"], loc_doc["day"], analysis._parse_iso(loc_doc["written"])
    ph = raw_product_hours(raw_rows)
    by_t = {r["valid_utc"]: r for r in raw_rows}
    summary = loc_doc["summary"]["rtma"]
    case.assertEqual(loc_doc.get("rowShape"), analysis.ROW_SHAPE, (loc_doc["id"], day))
    last_anl = last_pcp = None
    checked = 0
    for row in loc_doc["hours"]:
        cell, where = row["rtma"], (loc_doc["id"], day, row["t"])
        if cell is None or "show" not in cell:
            # no value for the day, and at most a raw CSV row of a copy that
            # gave none; the hourly contract and its files where it has a state
            rec = ph.get(row["t"])
            case.assertTrue(rec is None or all(rec[v] is None for v in analysis.HOURLY_VARS), where)
            if cell is not None:
                case.assertEqual(sorted(cell), HOURLY_ONLY, where)
            continue
        rec = ph[row["t"]]
        fresh = analysis.day_summary({k: v for k, v in ph.items() if k <= row["t"]}, tz, day, now)
        run = cell["run"]
        case.assertEqual(run["hours"], fresh["hours"], where)
        for k in ("high", "low", "gust", "wind"):
            case.assertEqual(run[k], fresh[k], where + (k,))
        p = fresh["precip"] or {}
        case.assertEqual(run["precip"], None if p.get("value") is None
                         else {"value": p["value"], "exact": p["exact"], "hours": p["hours"]}, where + ("precip",))
        for var in analysis.HOURLY_VARS:
            case.assertEqual(cell["show"][var], analysis.half_up(rec[var], analysis.HOUR_UNIT[var]), where + (var,))
        check_row_shape_3(case, cell, by_t[row["t"]], where)
        if rec["anl"] and not cell.get("late"):
            last_anl = run
        if rec["precip"] is not None and not cell.get("pcpLate"):
            last_pcp = run
        checked += 1
    if last_anl is not None:
        case.assertEqual([last_anl[k] for k in ("hours", "high", "low", "gust", "wind")],
                         [summary[k] for k in ("hours", "high", "low", "gust", "wind")], (loc_doc["id"], day))
    if last_pcp is not None:
        sp = summary["precip"]
        case.assertEqual(last_pcp["precip"], {"value": sp["value"], "exact": sp["exact"], "hours": sp["hours"]},
                         (loc_doc["id"], day))
    return checked


def raw_rows_of(raw_csv_text, lid):
    """One place's rows of a raw CSV file, in hour order."""
    rows = [r for r in csv.DictReader(io.StringIO(raw_csv_text)) if r["location"] == lid]
    return sorted(rows, key=lambda r: r["valid_utc"])


def hours_of(temps, wind=5.0, gust=8.0, precip=0.0, day="2026-09-26", tz="America/New_York"):
    """product_hours for day_summary: the first len(temps) labelled hours of
    the day, exact values in the archive's units."""
    hs = analysis.local_day_hours(day, tz)
    out = {}
    for h, t in zip(hs, temps):
        out[iso(h)] = {"temp": t, "wind": wind, "gust": gust, "precip": precip}
    return out


GOLDEN = os.path.join(ROOT, "tests", "fixtures", "analysis_v2_day.json.gz")
GOLDEN_DAY = "2026-09-26"


def golden_scenario(case):
    """Three places through 2026-09-26 and into the next morning on hourly
    ten-minute passes (a Job case), with an analysis on NOAA Open Data only
    (09Z), one on neither server whose precipitation landed (12Z), one whose
    NOMADS copy, other bytes, lands after every cut-off of its day (15Z), and
    New York's 23:00 (03Z on the 27th), whose NOMADS copy, other bytes, lands
    after New York's cut-off and before Chicago's and the hourly cut. Returns
    the day's files as text by store key. GOLDEN holds these files as the lane
    at 9893f97 wrote them, before rowShape 3, from this same function."""
    fx = case.fx
    keep = analysis.load_lattice
    analysis.load_lattice = lambda path=None: {"wexp": [], "g184": []}
    case.addCleanup(lambda: setattr(analysis, "load_lattice", keep))
    state = case.caught_up(hour(2026, 9, 26, 3))
    state["precipSwept"] = "2026-10-30T00:00:00Z"            # no daily sweep in the way
    analysis.save_state(case.st, state, hour(2026, 9, 26, 3))
    nodd_only, neither, late, between = (hour(2026, 9, 26, 9), hour(2026, 9, 26, 12), hour(2026, 9, 26, 15),
                                         hour(2026, 9, 27, 3))
    for i, t in enumerate(hour(2026, 9, 26, 4) + dt.timedelta(hours=n) for n in range(28)):
        if t == neither:
            fx.add_precip("rtma", t)
        else:
            fx.add_hour("rtma", t, servers=("nodd",) if t in (nodd_only, late, between) else analysis.SERVERS)
        fx.values[("rtma", "temp", iso(t))] = lambda c, i=i: 280.0 + ((i * 37 + c % 101) % 2500) / 100.0
        fx.values[("rtma", "wind", iso(t))] = lambda c, i=i: ((i * 7 + c) % 900) / 100.0
        fx.values[("rtma", "gust", iso(t))] = lambda c, i=i: ((i * 7 + c) % 900) / 100.0 + ((i * 3 + c) % 500) / 100.0
        fx.values[("rtma", "precip", iso(t))] = lambda c, i=i: ((i * 5 + c) % 7) * 0.015625

    def nomads_copy(t, published):
        fx.published[analysis.anl_url("rtma", t, nomads=True)] = published
        fx.add_hour("rtma", t, servers=("nomads",), precip=False, variant="b")
        fx.values[("rtma", "temp", iso(t), "b")] = 300.0
    for n in range(30):
        at = hour(2026, 9, 26, 4) + dt.timedelta(hours=n, minutes=50)
        if at == hour(2026, 9, 27, 5) + dt.timedelta(minutes=50):
            nomads_copy(between, "2026-09-27T05:10:00Z")
        if at == hour(2026, 9, 27, 8) + dt.timedelta(minutes=50):
            nomads_copy(late, "2026-09-27T08:30:00Z")
        case.assertEqual(case.run_pass(at), 0)
    keys = [analysis.DAY_KEY.format(day=GOLDEN_DAY), analysis.CSV_RAW_KEY.format(day=GOLDEN_DAY),
            analysis.CSV_DAY_KEY.format(day=GOLDEN_DAY)]
    keys += [analysis.LOC_KEY.format(loc=loc["id"], day=GOLDEN_DAY) for loc in LOCS]
    return {k: case.st.get(k).decode("utf-8") for k in keys}


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
        # a complete day still runs until its cut-off, 12:45 AM local time the
        # next day (owner's ruling 2026-10-14), and is final from then
        self.assertEqual((s["final"], s["review"], s["closed"]), (False, False, False))
        cut = analysis.day_cutoff("2026-09-26", "America/New_York")
        s = analysis.day_summary(ph, "America/New_York", "2026-09-26", cut)
        self.assertEqual((s["final"], s["finalAt"], s["precip"]["resolved"]), (True, iso(cut), True))

    def test_an_extreme_is_stamped_at_its_first_hour(self):
        temps = [60.0, 70.0, 70.0, 65.0]
        hs = analysis.local_day_hours("2026-09-26", "America/New_York")
        s = analysis.day_summary(hours_of(temps), "America/New_York", "2026-09-26", NOW)
        self.assertEqual(s["high"]["at"], iso(hs[1]))

    def test_a_partial_day_counts_its_hours(self):
        s = analysis.day_summary(hours_of([60.0] * 7), "America/New_York", "2026-09-26", NOW)
        self.assertEqual(s["hours"], 7)
        self.assertFalse(s["complete"])
        self.assertFalse(s["final"])
        self.assertFalse(s["closed"])
        self.assertFalse(s["precip"]["resolved"])
        self.assertNotIn("revised", s["precip"])

    def test_a_short_day_is_final_at_its_cut_off_on_the_hours_available(self):
        # owner's ruling 2026-10-14: 12:45 AM local time on the day after
        end = analysis.day_end("2026-09-26", "America/New_York")
        self.assertEqual(end, hour(2026, 9, 27, 4))
        cut = analysis.day_cutoff("2026-09-26", "America/New_York")
        self.assertEqual(cut, end + dt.timedelta(minutes=45))
        ph = hours_of([60.0] * 20)
        s = analysis.day_summary(ph, "America/New_York", "2026-09-26", cut - dt.timedelta(seconds=1))
        self.assertEqual((s["final"], s["review"], s["closed"]), (False, False, False))
        s = analysis.day_summary(ph, "America/New_York", "2026-09-26", cut)
        self.assertEqual((s["final"], s["finalAt"], s["hours"], s["complete"], s["closed"]),
                         (True, iso(cut), 20, False, False))
        self.assertEqual(s["high"]["value"], 60)             # the value stands on the hours available
        # nothing about it changes later: no day is ever closed
        self.assertEqual(analysis.day_summary(ph, "America/New_York", "2026-09-26", cut + dt.timedelta(days=3)), s)

    def test_a_complete_day_is_final_at_its_cut_off_and_its_precipitation_total_stands(self):
        # owner's decision 2026-09-29: RTMA resolves, precipitation included;
        # owner's ruling 2026-10-14: final at the cut-off, stamped with it
        ph = hours_of([60.0] * 24, precip=0.01)
        s = analysis.day_summary(ph, "America/New_York", "2026-09-26", NOW)
        self.assertEqual((s["final"], s["precip"]["resolved"]), (False, False))
        cut = analysis.day_cutoff("2026-09-26", "America/New_York")
        s = analysis.day_summary(ph, "America/New_York", "2026-09-26", cut + dt.timedelta(minutes=3))
        self.assertTrue(s["final"])
        self.assertEqual((s["precip"]["resolved"], s["precip"]["resolvedAt"]), (True, iso(cut)))
        self.assertEqual(s["precip"]["exact"], 0.24)
        later = cut + dt.timedelta(hours=5)
        s2 = analysis.day_summary(ph, "America/New_York", "2026-09-26", later, prior=s)
        self.assertEqual(s2, s)

    def test_an_hour_with_only_its_precipitation_adds_rain_but_no_analysis(self):
        # RTMA 2026-09-23 19Z: the analysis never landed, the precipitation did
        ph = hours_of([60.0, 70.0], precip=0.1)
        h3 = analysis.local_day_hours("2026-09-26", "America/New_York")[2]
        ph[iso(h3)] = {"temp": None, "wind": None, "gust": None, "precip": 0.2, "anl": False}
        s = analysis.day_summary(ph, "America/New_York", "2026-09-26", NOW)
        self.assertEqual((s["hours"], s["of"], s["complete"]), (2, 24, False))
        self.assertEqual(s["high"]["value"], 70)
        self.assertEqual(s["precip"], {"value": 0.4, "exact": 0.4, "hours": 3, "resolved": False, "resolvedAt": None})
        # at the cut-off the day resolves on 23 analyses and 24 accumulations
        hs = analysis.local_day_hours("2026-09-26", "America/New_York")
        ph = {iso(h): {"temp": 60.0, "wind": 5.0, "gust": 8.0, "precip": 0.0} for h in hs[:-1]}
        ph[iso(hs[-1])] = {"temp": None, "wind": None, "gust": None, "precip": 0.05, "anl": False}
        s = analysis.day_summary(ph, "America/New_York", "2026-09-26",
                                 analysis.day_cutoff("2026-09-26", "America/New_York"))
        self.assertEqual((s["hours"], s["complete"], s["final"]), (23, False, True))
        self.assertEqual((s["precip"]["resolved"], s["precip"]["hours"]), (True, 24))
        self.assertEqual(s["precip"]["value"], 0.05)

    def test_a_day_resolves_at_its_cut_off_on_the_files_published_by_then(self):
        # owner's ruling 2026-10-14: the files on NOAA Open Data or NOMADS by
        # 12:45 AM local time the next day count, and no file published later
        hs = analysis.local_day_hours("2026-09-26", "America/New_York")
        cut = analysis.day_cutoff("2026-09-26", "America/New_York")
        ph = hours_of([60.0 + i for i in range(24)], precip=0.01)
        for h in hs:
            ph[iso(h)].update({"pub": iso(h + dt.timedelta(minutes=48)), "pcpPub": iso(h + dt.timedelta(minutes=78))})
        del ph[iso(hs[10])]                                  # 10:00 local on neither server
        s = analysis.day_summary(ph, "America/New_York", "2026-09-26", cut - dt.timedelta(minutes=1))
        self.assertEqual((s["final"], s["hours"], s["closed"]), (False, 23, False))    # still running
        s = analysis.day_summary(ph, "America/New_York", "2026-09-26", cut)
        self.assertEqual((s["final"], s["finalAt"], s["hours"], s["complete"]), (True, iso(cut), 23, False))
        self.assertEqual(s["high"]["value"], 83)
        self.assertEqual((s["precip"]["resolved"], s["precip"]["hours"], s["precip"]["value"]), (True, 23, 0.23))
        # the file turns up the next morning, published after the cut-off: shown elsewhere, never counted
        ph[iso(hs[10])] = {"temp": 99.0, "wind": 5.0, "gust": 8.0, "precip": 1.0,
                           "pub": iso(cut + dt.timedelta(hours=3)), "pcpPub": iso(cut + dt.timedelta(hours=3))}
        s2 = analysis.day_summary(ph, "America/New_York", "2026-09-26", cut + dt.timedelta(hours=4))
        self.assertEqual(s2, s)
        # an hour published by the cut-off counts, whenever it was read: the
        # summary goes by publication time, and which copy a row has is fixed
        # when its file settles, so a copy read after that never reaches it
        ph[iso(hs[10])].update({"pub": iso(cut), "pcpPub": iso(cut)})
        s3 = analysis.day_summary(ph, "America/New_York", "2026-09-26", cut + dt.timedelta(hours=4))
        self.assertEqual((s3["hours"], s3["high"]["value"], s3["precip"]["value"]), (24, 99, 1.23))
        # the day's last accumulation lands after the cut-off: the total leaves it out
        ph[iso(hs[23])]["pcpPub"] = iso(cut + dt.timedelta(minutes=17))
        s4 = analysis.day_summary(ph, "America/New_York", "2026-09-26", cut + dt.timedelta(hours=4))
        self.assertEqual((s4["precip"]["hours"], s4["precip"]["value"]), (23, 1.22))

    def test_a_place_with_nothing_published_by_the_cut_off_goes_to_event_review(self):
        cut = analysis.day_cutoff("2026-09-26", "America/New_York")
        ph = hours_of([60.0] * 3, precip=0.01)
        for rec in ph.values():
            rec.update({"pub": iso(cut + dt.timedelta(hours=1)), "pcpPub": iso(cut + dt.timedelta(hours=1))})
        s = analysis.day_summary(ph, "America/New_York", "2026-09-26", cut + dt.timedelta(hours=2))
        self.assertEqual((s["final"], s["review"], s["hours"], s["high"]), (False, True, 0, None))
        self.assertEqual((s["precip"]["value"], s["precip"]["review"], s["precip"]["resolved"]), (None, True, False))
        e = analysis.review_entry("2026-09-26", "America/New_York")
        self.assertEqual((e["review"], e["final"], e["hours"], e["of"], e["precip"]["review"]), (True, False, 0, 24, True))

    def test_a_replaced_precipitation_file_keeps_its_first_version(self):
        # owner's ruling 2026-10-14: the first published version counts
        first = {"values": {"new-york-ny": 1.0, "chicago-il": None}, "read": True, "source": "nodd",
                 "published": "2026-09-26T20:17:00Z", "written": "2026-09-26T20:48:00Z"}
        again = dict(first, values={"new-york-ny": 1.0, "chicago-il": 2.0}, written="2026-09-26T21:08:00Z")
        self.assertEqual(analysis.merge_precip(first, again)["values"]["chicago-il"], 2.0)    # the same file, re-read
        replaced = dict(again, values={"new-york-ny": 9.0, "chicago-il": 2.0}, published="2026-09-27T01:02:00Z")
        out = analysis.merge_precip(first, replaced)
        self.assertEqual(out["values"], first["values"])
        self.assertEqual((out["published"], out["replaced"]), ("2026-09-26T20:17:00Z", "2026-09-27T01:02:00Z"))

    def test_an_empty_day_is_absent(self):
        self.assertIsNone(analysis.day_summary({}, "America/New_York", "2026-09-26", NOW))

    def test_a_missing_hourly_value_is_skipped_not_zero(self):
        ph = hours_of([60.0, 61.0])
        hs = analysis.local_day_hours("2026-09-26", "America/New_York")
        ph[iso(hs[1])]["temp"] = None
        s = analysis.day_summary(ph, "America/New_York", "2026-09-26", NOW)
        self.assertEqual(s["hours"], 2)
        self.assertEqual(s["high"]["exact"], 60.0)

    def test_the_three_half_ties_show_the_way_the_contracts_round_them(self):
        # each value the file holds is converted exactly and rounded once,
        # half away from zero, never in binary floating point and never by a
        # rounding that sends a negative half toward zero (JavaScript's
        # Math.round makes -8.5 a -8)
        ties = [("temp", 280.65, Decimal("45.5"), 46), ("temp", 250.65, Decimal("-8.5"), -9),
                ("precip", 15.875, Decimal("0.625"), 0.63)]
        for var, raw, exact, shown in ties:
            self.assertEqual(analysis.exact(var, raw), exact, (var, raw))
            self.assertEqual(analysis.half_up(analysis.exact(var, raw), analysis.HOUR_UNIT[var]), shown, (var, raw))
        # the binary conversion lands a hair inside the negative half
        self.assertGreater(analysis.k_to_f(250.65), -8.5)
        # and the day of one such hour resolves the same way
        for raw, high in ((280.65, 46), (250.65, -9)):
            ph = {iso(analysis.local_day_hours("2026-01-10", "America/New_York")[0]): {
                "temp": analysis.exact("temp", raw), "wind": Decimal(0), "gust": Decimal(0), "precip": None}}
            s = analysis.day_summary(ph, "America/New_York", "2026-01-10", NOW)
            self.assertEqual((s["high"]["value"], s["low"]["value"]), (high, high))

    def test_a_split_precipitation_total_is_summed_in_millimetres_and_converted_once(self):
        # 15.875 mm is exactly 0.625 in and resolves 0.63; in three hours of
        # 0.015625, 2.546875 and 13.3125 mm the hours' inches, each a division
        # Decimal stops at its 28th digit, add to 0.62499999...9, which
        # resolved 0.62 before the millimetres were summed first. 2.54 mm is
        # 0.1000 in, and three hours of it showed an exact figure of 0.0999
        hs = analysis.local_day_hours("2026-09-26", "America/New_York")
        for parts, value, exact in (((0.015625, 2.546875, 13.3125), 0.63, 0.625), ((0.064, 0.253, 2.223), 0.1, 0.1)):
            ph = {iso(h): {"temp": Decimal(60), "wind": Decimal(5), "gust": Decimal(8),
                           "precip": analysis.exact("precip", mm), "mm": mm} for h, mm in zip(hs, parts)}
            by_hours = sum(rec["precip"] for rec in ph.values())
            self.assertLess(by_hours, Decimal(str(exact)))          # the sum of the hours' inches falls short
            s = analysis.day_summary(ph, "America/New_York", "2026-09-26", NOW)
            self.assertEqual((s["precip"]["value"], s["precip"]["exact"], s["precip"]["hours"]), (value, exact, 3))
            # the running total of the last hour is the day's, by the same code
            run = analysis.running(ph, "America/New_York", "2026-09-26", NOW, hs[2])
            self.assertEqual(run["precip"], {"value": value, "exact": exact, "hours": 3})

    def test_the_running_values_are_the_days_resolution_of_the_hours_so_far(self):
        # a day with an hour on neither server and an hour published after the
        # cut-off: each hour's running values are day_summary over the hours up
        # to it, so the missing hour leaves the values on the hours there are,
        # the late file never counts, and the last counted hour is the day
        tz, day = "America/New_York", "2026-09-26"
        hs = analysis.local_day_hours(day, tz)
        cut = analysis.day_cutoff(day, tz)
        ph = hours_of([60.0 + (i % 7) for i in range(24)], precip=0.01)
        for h in hs:
            ph[iso(h)].update({"pub": iso(h + dt.timedelta(minutes=48)), "pcpPub": iso(h + dt.timedelta(minutes=78))})
        del ph[iso(hs[5])]
        ph[iso(hs[23])].update({"temp": 99.0, "pub": iso(cut + dt.timedelta(hours=2)),
                                "pcpPub": iso(cut + dt.timedelta(hours=2))})
        later = cut + dt.timedelta(hours=3)
        s = analysis.day_summary(ph, tz, day, later)
        for k, h in enumerate(hs):
            run = analysis.running(ph, tz, day, later, h)
            fresh = analysis.day_summary({i: r for i, r in ph.items() if i <= iso(h)}, tz, day, later)
            self.assertEqual([run[x] for x in ("hours", "high", "low", "gust", "wind")],
                             [fresh[x] for x in ("hours", "high", "low", "gust", "wind")], k)
        self.assertEqual(analysis.running(ph, tz, day, later, hs[4])["hours"], 5)
        self.assertEqual(analysis.running(ph, tz, day, later, hs[5]), analysis.running(ph, tz, day, later, hs[4]))
        last = analysis.running(ph, tz, day, later, hs[22])
        self.assertEqual((last["hours"], last["high"], last["precip"]["value"]), (s["hours"], s["high"], s["precip"]["value"]))
        self.assertEqual(analysis.running(ph, tz, day, later, hs[23]), last)        # the late hour adds nothing
        self.assertEqual((s["hours"], s["high"]["value"], s["precip"]["value"]), (22, 66, 0.22))
        self.assertIsNone(analysis.running({}, tz, day, later, hs[3]))

    def test_the_exact_strings_are_the_stored_values_converted_in_rational_arithmetic(self):
        # rowShape 3 (owner's approval of display v3, 2026-11-04): the stored
        # value as the file holds it, and the value converted before
        # rounding, exact where its decimal ends (a thousandth of a degree,
        # twelve places of a mile an hour) and otherwise cut toward zero at
        # ten places, never rounded and never a Decimal division
        cases = [("temp", 288.54, "288.54", "59.702", 60), ("wind", 3.96, "3.96", "8.858267716716", 9),
                 ("gust", 7.3, "7.30", "16.329634932330", 16), ("precip", 0.0123, "0.0123", "0.0004842519", 0.0),
                 # the three half ties: 45.5 shows 46, -8.5 shows -9, 0.625 shows 0.63
                 ("temp", 280.65, "280.65", "45.500", 46), ("temp", 250.65, "250.65", "-8.500", -9),
                 ("precip", 15.875, "15.875", "0.6250000000", 0.63),
                 ("precip", 0.0, "0", "0.0000000000", 0.0), ("precip", 25.4, "25.4", "1.0000000000", 1.0)]
        for var, stored, raw, x, shown in cases:
            self.assertEqual((analysis.raw_text(var, stored), analysis.x_text(var, stored)), (raw, x), (var, stored))
            self.assertEqual(analysis.half_up(Decimal(x), analysis.HOUR_UNIT[var]), shown, (var, stored))
            self.assertEqual(analysis.half_up(analysis.exact(var, stored), analysis.HOUR_UNIT[var]), shown, (var, stored))
        # 0.0123 mm is 0.00048425196850... in, which rounds to 0.0004842520 at
        # ten places, and the string is cut and never rounded
        self.assertEqual(str(analysis.exact("precip", 0.0123).quantize(Decimal("1E-10"))), "0.0004842520")
        # a stored value finer than the file's precision is written exactly,
        # and a converted value that ends past ten places too
        self.assertEqual((analysis.raw_text("temp", 288.5401), analysis.x_text("temp", 288.5401)), ("288.5401", "59.70218"))
        self.assertEqual(analysis.x_text("wind", 3.961), "8.8605046530081")
        self.assertEqual((analysis.raw_text("precip", 0.0000127), analysis.x_text("precip", 0.0000127)),
                         ("0.0000127", "0.0000005000"))
        self.assertEqual(analysis.fixed_text(analysis.Fraction(5, 10 ** 11), 10), "0.00000000005")
        self.assertEqual(analysis.cut_text(analysis.Fraction(-1, 3), 3), "-0.333")
        self.assertIsNone(analysis.x_text("temp", None))

    def test_the_day_so_far_before_rounding_runs_on_the_hours_run_counts(self):
        # runX: the high and low to a thousandth and the peak gust to twelve
        # places, exact; the mean wind cut at ten places even where it ends
        # (2.244392746407 is written 2.2443927464); the total from the summed
        # millimetres, 15.875 mm in three hours being exactly 0.625 in. An
        # hour published after the cut-off adds nothing, as in `run`
        tz, day = "America/New_York", "2026-09-26"
        hs = analysis.local_day_hours(day, tz)
        cut = analysis.day_cutoff(day, tz)
        ph = {}
        for h, (k, w, mm) in zip(hs, ((280.65, 1.0, 0.015625), (250.65, 1.0, 2.546875), (290.09, 1.01, 13.3125),
                                      (310.0, 9.0, 5.0))):
            ph[iso(h)] = {"temp": analysis.exact("temp", k), "wind": analysis.exact("wind", w),
                          "gust": analysis.exact("gust", w + 2), "precip": analysis.exact("precip", mm), "mm": mm}
        ph[iso(hs[3])].update({"pub": iso(cut + dt.timedelta(hours=1)), "pcpPub": iso(cut + dt.timedelta(hours=1))})
        texts = analysis.running_texts(ph, tz, day)
        self.assertEqual(texts[iso(hs[0])], {"high": "45.500", "low": "45.500", "gust": "6.710808876300",
                                             "wind": "2.2369362921", "precip": "0.0006151574"})
        last = {"high": "62.492", "low": "-8.500", "gust": "6.733178239221", "wind": "2.2443927464", "precip": "0.6250000000"}
        self.assertEqual((texts[iso(hs[2])], texts[iso(hs[3])]), (last, last))
        later = cut + dt.timedelta(hours=2)
        for h in hs[:4]:
            run, run_x = analysis.running(ph, tz, day, later, h), texts[iso(h)]
            for k in ("high", "low", "gust", "wind"):
                self.assertEqual((analysis.cut(Decimal(run_x[k]), 0.001), analysis.half_up(Decimal(run_x[k]), 1)),
                                 (run[k]["exact"], run[k]["value"]), (h, k))
            self.assertEqual(analysis.half_up(Decimal(run_x["precip"]), 0.01), run["precip"]["value"], h)
        self.assertEqual(sorted(texts), sorted(ph))


class LocalDay(unittest.TestCase):
    def test_a_chicago_day_spans_two_utc_directories(self):
        hs = analysis.local_day_hours("2026-09-26", "America/Chicago")
        self.assertEqual(len(hs), 24)
        self.assertEqual(hs[0], hour(2026, 9, 26, 5))
        self.assertEqual(hs[23], hour(2026, 9, 27, 4))
        self.assertIn("/rtma2p5.20260926/rtma2p5.t05z.", analysis.anl_url("rtma", hs[0]))
        self.assertIn("/rtma2p5.20260927/rtma2p5.t04z.", analysis.anl_url("rtma", hs[23]))
        self.assertIn("/rtma2p5.20260927/rtma2p5.2026092704.pcp.184.grb2", analysis.pcp_url("rtma", hs[23]))
        # the same UTC hour is the 26th in Chicago and the 27th in New York
        touched = analysis.touched_by(hour(2026, 9, 27, 4), LOCS)
        self.assertIn(("chicago-il", "2026-09-26"), touched)
        self.assertIn(("new-york-ny", "2026-09-27"), touched)
        self.assertIn(("los-angeles-ca", "2026-09-26"), touched)
        self.assertEqual(analysis.local_day_of(hour(2026, 9, 27, 4), "America/Chicago"), "2026-09-26")

    def test_a_files_name_is_ncep_s_and_ends_both_servers_urls(self):
        # the names the prototype's CSV files give, under the day directory
        # of the analysis's hour or of the hour the accumulation ends
        self.assertEqual(analysis.file_name("rtma", "anl", hour(2026, 11, 4, 11)),
                         "rtma2p5.20261104/rtma2p5.t11z.2dvaranl_ndfd.grb2_wexp")
        self.assertEqual(analysis.file_name("rtma", "pcp", hour(2026, 11, 4, 12)),
                         "rtma2p5.20261104/rtma2p5.2026110412.pcp.184.grb2")
        self.assertEqual(analysis.file_name("rtma", "pcp", hour(2026, 11, 5, 0)),
                         "rtma2p5.20261105/rtma2p5.2026110500.pcp.184.grb2")
        for n in range(48):
            t = hour(2026, 11, 3, 0) + dt.timedelta(hours=n)
            for nomads in (False, True):
                self.assertTrue(analysis.anl_url("rtma", t, nomads=nomads).endswith("/" + analysis.file_name("rtma", "anl", t)))
                self.assertTrue(analysis.pcp_url("rtma", t, nomads=nomads).endswith("/" + analysis.file_name("rtma", "pcp", t)))

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
        s = analysis.day_summary(ph, "America/New_York", "2026-03-08", NOW)
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
        s = analysis.day_summary(ph, "America/New_York", "2026-11-01", NOW)
        self.assertEqual((s["hours"], s["of"], s["complete"]), (25, 25, True))
        self.assertEqual(s["high"], {"value": 90, "exact": 90.0, "at": iso(hour(2026, 11, 1, 6))})
        self.assertEqual(s["precip"]["exact"], 1.24)               # 24 x 0.01 + 1.00
        self.assertEqual(s["wind"]["exact"], 10.0)                 # the mean over 25 hours
        del ph[iso(fall[2])]
        s = analysis.day_summary(ph, "America/New_York", "2026-11-01", NOW)
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

    def run_pass(self, now=NOW, window=False):
        """One pass (the windowed one with `window`), its printed status kept
        in self.status and its errors in self.errors for the assertions."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = (analysis.window_pass if window else analysis.analysis_pass)(self.cfg, self.st, now)
        line = buf.getvalue().strip().splitlines()[-1]
        self.status = json.loads(line)
        self.errors = self.status.get("errors", [])
        return rc

    def run_window(self, now):
        return self.run_pass(now, window=True)

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
        t_r = hour(2026, 9, 26, 19)                              # what should have landed by 19:55Z
        self.fx.add_hour("rtma", t_r)
        self.fx.values[("rtma", "temp", iso(t_r))] = lambda k: 300.15 if k == NY_K else 293.15
        self.fx.values[("rtma", "precip", iso(t_r))] = lambda k: 2.54 if k == NY["cell"]["g184"][2] else 0.0
        self.assertEqual(self.run_pass(), 0)
        # each server's first copy of each file, kept on its own, write once,
        # in the file's own units (kelvin and metres per second, millimetres),
        # nothing converted or rounded (owner's ruling 2026-11-03)
        for kind in analysis.KINDS:
            for server in analysis.SERVERS:
                c = self.read(analysis.copy_key("rtma", kind, server, t_r))
                landed = t_r + dt.timedelta(minutes=48 if kind == "anl" else 17)
                self.assertEqual((c["kind"], c["server"], c["valid"], c["seen"], c["published"]),
                                 (kind, server, iso(t_r), iso(NOW), iso(landed)), (kind, server))
                self.assertEqual(sorted(c["sha256"]), ["gust", "temp", "wind"] if kind == "anl" else ["precip"])
        nomads = self.read(analysis.copy_key("rtma", "anl", "nomads", t_r))
        nodd = self.read(analysis.copy_key("rtma", "anl", "nodd", t_r))
        self.assertEqual(nomads["values"]["new-york-ny"], {"temp": 300.15, "wind": 5.0, "gust": 8.0})
        # NOMADS's copy was read first and decoded; NOAA Open Data's is the
        # same bytes, so its values were taken and nothing decoded
        self.assertEqual((nomads["decoded"], nodd["decoded"], nodd["values"], nodd["sha256"]),
                         (["temp", "wind", "gust"], [], nomads["values"], nomads["sha256"]))
        # NOMADS has the hour, so it settled at once: the archive hour and the
        # precipitation key, each written once with the copy each place took
        arc = self.read(analysis.hour_key("rtma", t_r))
        self.assertEqual(arc["schema"], "analysis/2")
        self.assertEqual((arc["valid"], arc["source"], arc["settled"], arc["hashMatch"]),
                         ("2026-09-26T19:00:00Z", "nomads", iso(NOW), True))
        self.assertEqual(set(arc["picks"].values()), {"nomads"})
        self.assertEqual(arc["values"]["new-york-ny"], {"temp": 300.15, "wind": 5.0, "gust": 8.0})
        self.assertEqual(arc["values"]["chicago-il"]["temp"], 293.15)
        pre = self.read(analysis.precip_key("rtma", t_r))
        self.assertEqual((pre["read"], pre["complete"], pre["missing"], pre["source"]), (True, True, [], "nomads"))
        self.assertEqual(pre["values"]["new-york-ny"], 2.54)
        # four frames, on the lattice, as scaled ints
        for var in analysis.HOURLY_VARS:
            fr = self.read(analysis.FRAME_KEY.format(product="rtma", var=var, stamp=analysis._stamp(t_r)))
            self.assertEqual((fr["cols"], fr["rows"], fr["pitch"], len(fr["values"])), (320, 200, 3, 64000), var)
            self.assertEqual(fr["scale"], analysis.SCALE[var])
            self.assertEqual(fr["valid"], iso(t_r))
        temp = self.read(analysis.FRAME_KEY.format(product="rtma", var="temp", stamp="20260926T19Z"))
        self.assertEqual(temp["unit"], "°F")
        self.assertIn(680, temp["values"])                     # 293.15 K is 68.0 F, in tenths
        pcp = self.read(analysis.FRAME_KEY.format(product="rtma", var="precip", stamp="20260926T19Z"))
        self.assertEqual(pcp["values"].count(None), 15)        # the lattice points off the G184 grid
        gi = self.read(analysis.GRID_INDEX_KEY)
        self.assertEqual(gi["frames"]["rtma"]["temp"], {"2026-09-26": ["19"]})
        self.assertEqual(gi["frames"]["rtma"]["precip"], {"2026-09-26": ["19"]})
        self.assertEqual(list(gi["frames"]), ["rtma"])
        self.assertEqual(gi["asof"], iso(t_r))
        # the days the hour touched, at both scales
        day = self.read(analysis.DAY_KEY.format(day="2026-09-26"))
        ny = day["locations"]["new-york-ny"]
        self.assertEqual(ny["rtma"]["hours"], 1)
        self.assertEqual(ny["rtma"]["high"], {"value": 81, "exact": 80.6, "at": "2026-09-26T19:00:00Z"})
        self.assertEqual(list(ny), ["rtma"])
        loc = self.read(analysis.LOC_KEY.format(loc="new-york-ny", day="2026-09-26"))
        self.assertEqual(len(loc["hours"]), 24)
        row = next(r for r in loc["hours"] if r["t"] == "2026-09-26T19:00:00Z")
        self.assertEqual(row["local"], "15")
        self.assertEqual(values_of(row["rtma"]), {"temp": 80.6, "wind": 11.184, "gust": 17.895, "precip": None})
        self.assertEqual(sorted(row), ["local", "rtma", "t"])
        # each row also carries its values as the contracts show them, the
        # day's values so far, and when and where its file was published
        self.assertEqual(loc["rowShape"], analysis.ROW_SHAPE)
        self.assertEqual(row["rtma"]["show"], {"temp": 81, "wind": 11, "gust": 18, "precip": None})
        self.assertEqual(row["rtma"]["run"]["high"], ny["rtma"]["high"])
        self.assertEqual(row["rtma"]["run"]["hours"], 1)
        self.assertEqual((row["rtma"]["src"], row["rtma"]["pub"]), ("nomads", iso(t_r + dt.timedelta(minutes=48))))
        # the accumulation NOAA files under 19Z is the rain of the hour that starts at 18Z
        before = next(r for r in loc["hours"] if r["t"] == "2026-09-26T18:00:00Z")
        self.assertEqual(values_of(before["rtma"]), {"temp": None, "wind": None, "gust": None, "precip": 0.1})
        self.assertEqual(before["rtma"]["run"], {"hours": 0, "high": None, "low": None, "gust": None, "wind": None,
                                                 "precip": {"value": 0.1, "exact": 0.1, "hours": 1}})
        self.assertEqual(loc["summary"], ny)
        # the state and the index, which is the last write
        state = self.state()
        self.assertEqual(state["products"]["rtma"]["newest"], iso(t_r))
        self.assertEqual((list(state["products"]), list(state["backfill"])), (["rtma"], ["rtma"]))
        self.assertEqual(state["layout"], analysis.LAYOUT)
        self.assertNotIn("relayout", state)
        self.assertNotIn(iso(t_r), state["products"]["rtma"]["open"])        # both files settled
        idx = self.read(analysis.INDEX_KEY)
        self.assertEqual(idx["days"], ["2026-09-26"])
        self.assertEqual(idx["sources"]["rtma"]["latest"], iso(t_r))
        self.assertEqual(list(idx["sources"]), ["rtma"])
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
        self.assertEqual((archive.LAST_STATUS["read"], archive.LAST_STATUS["pcpRead"], archive.LAST_STATUS["copies"]),
                         (1, 1, 2))
        # the archive hour is written once: a second pass reads nothing new for
        # the hour and rewrites no frame (the backfill walks on behind it)
        n = len(self.st.puts)
        self.assertEqual(self.run_pass(), 0)
        self.assertFalse(any(k.startswith("snapshots/analysis2/grid/rtma/") for k, _, _ in self.st.puts[n:]))
        self.assertFalse([k for k, _, _ in self.st.puts[n:] if k.startswith("archive/analysis2/") and analysis._stamp(t_r) in k])
        self.assertEqual(self.st.puts[-1][0], analysis.INDEX_KEY)

    def test_frames_carry_each_places_window_on_the_grid_the_file_is_on(self):
        t_r = hour(2026, 9, 26, 19)
        analysis.load_locations = lambda path=None: LOCS + [EDGE]
        self.fx.add_hour("rtma", t_r)
        self.fx.values[("rtma", "temp", iso(t_r))] = wexp_window_value
        # one cell beside New York's is missing from the bitmap
        self.fx.values[("rtma", "gust", iso(t_r))] = lambda k: None if k == NY_K + 1 else 10.0
        # 1 inch at New York's G184 cell and half an inch at its wexp index: the
        # precipitation window must read the G184 one, never the wexp one
        def pcp(k):
            return 25.4 if k == NY_K184 else (12.7 if k == NY_K else 0.0)
        self.fx.values[("rtma", "precip", iso(t_r))] = pcp
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
        # the archive hour, now the settled record, holds the value at the resolving cell
        arc = self.read(analysis.hour_key("rtma", t_r))
        self.assertEqual(sorted(arc), ["cutoffs", "hashMatch", "hourly", "kind", "picks", "product", "published",
                                       "schema", "servers", "settled", "source", "valid", "values", "written"])
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
        # the precipitation window is on G184, every other on wexp
        pr = self.read(analysis.FRAME_KEY.format(product="rtma", var="precip", stamp="20260926T19Z"))["windows"]
        self.assertEqual(pr["new-york-ny"]["grid"], "g184")
        self.assertEqual(pr["new-york-ny"]["values"][220], 100)
        self.assertEqual(pr["edge-xx"]["values"].count(None), 441 - 14 * 13)
        for var in ("temp", "wind", "gust"):
            fr = self.read(analysis.FRAME_KEY.format(product="rtma", var=var, stamp="20260926T19Z"))
            self.assertEqual(fr["windows"]["los-angeles-ca"]["grid"], "wexp", var)
            self.assertEqual(len(fr["windows"]["los-angeles-ca"]["values"]), 441, var)

    def test_a_precipitation_file_read_after_its_analysis_writes_its_frame_with_its_window(self):
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
        t_r = hour(2026, 9, 26, 19)
        self.fx.add_hour("rtma", t_r)
        self.fx.values[("rtma", "temp", iso(t_r))] = lambda k: 300.15 if k == NY_K else 293.15
        self.assertEqual(self.run_pass(), 0)
        day_before = self.read(analysis.DAY_KEY.format(day="2026-09-26"))
        loc_before = self.read(analysis.LOC_KEY.format(loc="new-york-ny", day="2026-09-26"))
        # rewrite every frame in the shape the lane wrote before windows
        # existed, and one of them as it would be after a page-side tool
        # touched it, then rebuild the day from scratch, inside the same hour,
        # since at 20Z the 19Z row's precipitation file is due and its row
        # gains the file (pcpFile) with no times
        frames = [k for k, _, _ in self.st.puts if k.startswith("snapshots/analysis2/grid/") and not k.endswith("index.json")]
        self.assertEqual(len(frames), 4)
        for key in frames:
            doc = self.read(key)
            self.assertIn("windows", doc)
            del doc["windows"]
            self.st.put(key, analysis._dump(doc), "application/json", analysis.CACHE_FINAL)
        self.st.gets = []
        later = NOW + dt.timedelta(minutes=4)
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
        self.assertEqual(values_of(rows[iso(t - dt.timedelta(hours=1))]["rtma"]), {"temp": None, "wind": None, "gust": None, "precip": 0.0393})
        self.assertEqual(values_of(rows[iso(t)]["rtma"]), {"temp": 68.0, "wind": 11.184, "gust": 17.895, "precip": None})
        # the row's precipitation as the contract shows it, a hundredth of an inch
        self.assertEqual(rows[iso(t - dt.timedelta(hours=1))]["rtma"]["show"]["precip"], 0.04)
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"]["rtma"]
        self.assertEqual(ny["precip"], {"value": 0.04, "exact": 0.0393, "hours": 1, "resolved": False, "resolvedAt": None})
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
        self.caught_up(t - dt.timedelta(hours=1))
        self.fx.add_precip("rtma", t)
        self.fx.values[("rtma", "precip", iso(t))] = 2.54          # 0.1 in
        self.assertEqual(self.run_pass(), 0)
        self.assertIsNone(self.read(analysis.hour_key("rtma", t)))
        self.assertTrue(self.read(analysis.precip_key("rtma", t))["complete"])
        # the precipitation settled; the analysis stays open, asked for again
        self.assertEqual(self.state()["products"]["rtma"]["open"], {iso(t): {"anl": {}}})
        self.assertEqual(self.state()["products"]["rtma"]["newest"], iso(t - dt.timedelta(hours=1)))
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"]["rtma"]
        self.assertEqual((ny["hours"], ny["complete"], ny["final"]), (0, False, False))
        self.assertIsNone(ny["high"])
        self.assertEqual(ny["precip"], {"value": 0.1, "exact": 0.1, "hours": 1, "resolved": False, "resolvedAt": None})
        # the accumulation filed under 19Z is the 18Z row's
        row = next(r for r in self.read(analysis.LOC_KEY.format(loc="new-york-ny", day="2026-09-26"))["hours"]
                   if r["t"] == iso(t - dt.timedelta(hours=1)))
        self.assertEqual(values_of(row["rtma"]), {"temp": None, "wind": None, "gust": None, "precip": 0.1})
        fr = self.read(analysis.FRAME_KEY.format(product="rtma", var="precip", stamp="20260926T19Z"))
        self.assertIsNotNone(fr)
        # the analysis is asked for again on both servers, and the settled precipitation is not fetched again
        n = len(self.fx.fetched)
        self.assertEqual(self.run_pass(NOW + dt.timedelta(minutes=10)), 0)
        self.assertFalse([u for u in self.fx.fetched[n:] if ".pcp." in u])
        self.assertIn(analysis.anl_url("rtma", t) + ".idx", self.fx.fetched[n:])
        self.assertIn(analysis.anl_url("rtma", t, nomads=True) + ".idx", self.fx.fetched[n:])
        # the analysis lands late: the hour is read and the stored precipitation stands
        self.fx.add_hour("rtma", t, precip=False)
        self.fx.values[("rtma", "precip", iso(t))] = 5.08
        self.assertEqual(self.run_pass(NOW + dt.timedelta(minutes=20)), 0)
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"]["rtma"]
        self.assertEqual((ny["hours"], ny["high"]["value"], ny["precip"]["value"]), (1, 68, 0.1))
        self.assertNotIn(iso(t), self.state()["products"]["rtma"]["open"])

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
        status = blank_status()
        n = len(self.fx.fetched)
        # a budget past the span's NOMADS calls: what is swept is under test here, the budget below
        touched = analysis.precip_sweep(self.st, state, LOCS, analysis.load_lattice(), {}, {}, NOW,
                                        archive.Deadline(600), status, analysis.NomadsBudget(10000))
        self.assertIn(("new-york-ny", "2026-09-23"), touched)
        # recorded from both servers and never used, since the hour's days were
        # final without it (owner's ruling 2026-11-04)
        pre = self.read(analysis.precip_key("rtma", old))
        self.assertEqual((pre["complete"], pre["source"]), (False, None))
        for server in analysis.SERVERS:
            self.assertEqual(self.read(analysis.copy_key("rtma", "pcp", server, old))["values"]["new-york-ny"], 2.54)
        self.assertIsNone(self.read(analysis.FRAME_KEY.format(product="rtma", var="precip", stamp="20260923T19Z")))
        for server in analysis.SERVERS:
            self.assertNotIn(analysis.file_url("rtma", "pcp", server, kept), self.fx.fetched[n:])
            self.assertNotIn(analysis.file_url("rtma", "pcp", server, queued), self.fx.fetched[n:])
            # nothing newer than the live lane's own 48 hours of asking
            self.assertNotIn(analysis.file_url("rtma", "pcp", server, hour(2026, 9, 25, 19)), self.fx.fetched[n:])
        self.assertEqual((status["swept"]["filled"], status["swept"]["done"]), (1, True))
        # both files of the swept hour settled on no copy
        self.assertIn(iso(old), state["products"]["rtma"]["missing"])
        self.assertIn(iso(old), state["products"]["rtma"]["pmissing"])
        self.assertEqual(state["precipSwept"], iso(NOW))
        self.assertFalse(analysis.precip_sweep_due(state, NOW + dt.timedelta(hours=23)))
        self.assertTrue(analysis.precip_sweep_due(state, NOW + dt.timedelta(hours=24)))
        # the rebuilt day has no hour from it, and the raw CSV records the late copies
        raw: list = []
        day, _ = analysis.build_day(self.st, "2026-09-23", LOCS, {}, NOW, state, raw=raw)
        self.assertEqual(day["locations"], {})
        r = next(dict(zip(analysis.RAW_HEADER, x)) for x in raw
                 if x[1] == "new-york-ny" and x[3] == iso(old - dt.timedelta(hours=1)))
        self.assertEqual((r["precip_mm"], r["precip_nomads_mm"], r["precip_late_copy"]), (None, 2.54, True))

    def test_a_refused_file_ends_the_products_sweep_for_the_day(self):
        a, b = hour(2026, 9, 23, 19), hour(2026, 9, 23, 20)
        self.fx.add_precip("rtma", a)
        self.fx.add_precip("rtma", b)
        self.fx.grids[("rtma", "APCP", iso(a))] = "other"          # a file on another grid is refused
        state = analysis.new_state()
        state["products"]["rtma"]["newest"] = iso(hour(2026, 9, 26, 19))
        state["backfill"]["rtma"].update({"oldest": iso(hour(2026, 9, 23, 0)), "done": True})
        status = blank_status()
        n = len(self.fx.fetched)
        analysis.precip_sweep(self.st, state, LOCS, analysis.load_lattice(), {}, {}, NOW, archive.Deadline(600), status,
                              analysis.NomadsBudget())
        self.assertEqual(status["failed"], 2)                           # refused on each server
        self.assertFalse([u for u in self.fx.fetched[n:] if analysis._stamp(b)[:8] in u and "2026092320" in u])
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
        # read_hour, the reader of the archive's earlier shape, which the
        # tests still make hours with; since 2026-11-03 the passes read every
        # file from both servers (read_copy).
        # RTMA 2026-09-23 19Z reached NOMADS and never NOAA Open Data; a file
        # on NOMADS counts as published (owner's decision 2026-09-29). RTMA
        # usually lands 47 minutes after its hour, so NOMADS is asked from 77
        t = hour(2026, 9, 26, 12)
        self.fx.add_hour("rtma", t)
        self._to_nomads("rtma", t)
        empty = {"wexp": [], "g184": []}
        self.assertEqual(analysis.settled_after("rtma", t), t + dt.timedelta(minutes=77))
        res = analysis.read_hour("rtma", t, LOCS, empty, frames=False, now=t + dt.timedelta(minutes=20))
        self.assertEqual((res.status, res.precip_status), ("absent", "absent"))
        self.assertFalse(any("nomads" in u for u in self.fx.fetched))       # neither asked for before it is due
        # a precipitation file is looked for there 27 minutes after its hour
        # ends, so the day's last accumulation is looked for before the 12:45 AM
        # cut-off (owner's ruling 2026-10-14); the analysis from 77 minutes
        res = analysis.read_hour("rtma", t, LOCS, empty, frames=False, now=t + dt.timedelta(minutes=70))
        self.assertEqual((res.status, res.precip_status, res.precip["source"]), ("absent", "ok", "nomads"))
        self.assertFalse(any("nomads" in u and "2dvaranl" in u for u in self.fx.fetched))
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

    def test_the_sweep_records_a_file_found_after_its_days_were_final_and_never_uses_it(self):
        old = hour(2026, 9, 23, 19)
        # the analysis on NOMADS only, the precipitation on NOAA Open Data only
        self.fx.add_hour("rtma", old, precip=False, servers=("nomads",))
        self.fx.add_precip("rtma", old, servers=("nodd",))
        state = analysis.new_state()
        state["products"]["rtma"]["newest"] = iso(hour(2026, 9, 26, 19))
        state["backfill"]["rtma"].update({"oldest": iso(hour(2026, 9, 23, 0)), "done": True})
        status = blank_status()
        touched = analysis.precip_sweep(self.st, state, LOCS, analysis.load_lattice(), {}, {}, NOW,
                                        archive.Deadline(600), status, analysis.NomadsBudget(10000))
        # the hour's days were final without the files (owner's ruling
        # 2026-11-04): each settles on no copy first, and what the sweep then
        # finds is recorded, flagged as late, and never used or drawn
        for kind, server in (("anl", "nomads"), ("pcp", "nodd")):
            self.assertIsNotNone(self.read(analysis.copy_key("rtma", kind, server, old)), kind)
            self.assertEqual(self.read((analysis.hour_key if kind == "anl" else analysis.precip_key)("rtma", old))["picks"],
                             {"new-york-ny": None, "chicago-il": None, "los-angeles-ca": None}, kind)
        self.assertIsNone(self.read(analysis.FRAME_KEY.format(product="rtma", var="temp", stamp="20260923T19Z")))
        self.assertIn(("new-york-ny", "2026-09-23"), touched)
        self.assertEqual(status["failed"], 0)
        raw: list = []
        analysis.build_day(self.st, "2026-09-23", LOCS, {}, NOW, state, raw=raw)
        r = next(dict(zip(analysis.RAW_HEADER, x)) for x in raw if x[1] == "new-york-ny" and x[3] == iso(old))
        self.assertEqual((r["temp_k"], r["analysis_nomads_temp_k"], r["analysis_late_copy"], r["analysis_settled"]),
                         (None, 293.15, True, iso(NOW)))

    def test_each_read_carries_its_files_publication_time(self):
        t = hour(2026, 9, 26, 12)
        self.fx.add_hour("rtma", t)
        self.fx.published[analysis.anl_url("rtma", t)] = "2026-09-26T12:48:31Z"
        self.fx.published[analysis.pcp_url("rtma", t)] = "2026-09-26T12:17:02Z"
        res = analysis.read_hour("rtma", t, LOCS, {"wexp": [], "g184": []}, frames=False, now=t + dt.timedelta(hours=1))
        self.assertEqual((res.doc["published"], res.precip["published"]), ("2026-09-26T12:48:31Z", "2026-09-26T12:17:02Z"))

    def test_a_place_with_no_hour_by_the_cut_off_goes_to_review_on_a_day_others_have(self):
        # New York and Chicago have the day's hours; Los Angeles has none. At
        # each place's cut-off the pass makes the day final where hours count
        # and marks Los Angeles for event review; a day nobody has hours for
        # is not written
        hs = analysis.local_day_hours("2026-09-26", "America/New_York")
        cache, gi, state = {}, {}, analysis.new_state()
        others = [l for l in LOCS if l["id"] != "los-angeles-ca"]
        for h in hs[:3]:
            self.fx.add_hour("rtma", h)
            res = analysis.read_hour("rtma", h, others, {"wexp": [], "g184": []}, frames=False, now=h + dt.timedelta(hours=1))
            analysis.write_hour(self.st, "rtma", h, res, gi, cache, h + dt.timedelta(hours=1))
        before = analysis.day_cutoff("2026-09-26", "America/New_York") - dt.timedelta(minutes=5)
        analysis.rebuild_days(self.st, {(l["id"], "2026-09-26") for l in LOCS}, LOCS, cache, before, state)
        doc = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]
        self.assertNotIn("los-angeles-ca", doc)
        self.assertFalse(doc["new-york-ny"]["rtma"]["final"])
        after = analysis.day_cutoff("2026-09-26", "America/Los_Angeles") + dt.timedelta(minutes=3)
        pairs = analysis.days_past_cutoff(self.st, LOCS, after)
        self.assertIn(("los-angeles-ca", "2026-09-26"), pairs)
        analysis.rebuild_days(self.st, pairs, LOCS, cache, after, state)
        doc = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]
        self.assertTrue(doc["new-york-ny"]["rtma"]["final"])
        self.assertEqual((doc["los-angeles-ca"]["rtma"]["review"], doc["los-angeles-ca"]["rtma"]["high"]), (True, None))
        self.assertEqual(state["dayStatus"]["2026-09-26"]["review"], 1)
        self.assertEqual(self.read(analysis.LOC_KEY.format(loc="los-angeles-ca", day="2026-09-26"))["summary"]["rtma"]["review"], True)
        self.assertEqual(analysis.days_past_cutoff(self.st, LOCS, after), set())       # nothing left to settle
        # a day with nothing read anywhere writes nothing
        analysis.rebuild_days(self.st, {(l["id"], "2026-09-20") for l in LOCS}, LOCS, cache, after, state)
        self.assertIsNone(self.st.get(analysis.DAY_KEY.format(day="2026-09-20")))

    def test_an_hour_published_after_the_cut_off_is_shown_and_not_counted(self):
        # New York 2026-09-26 with 12:00 local (16Z) on neither server by the
        # cut-off: the day resolves on 23 hours at 12:45 AM; the file is
        # published the next morning and the row shows it, marked late
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
        cut = analysis.day_cutoff("2026-09-26", "America/New_York")
        self.assertEqual((ny["final"], ny["hours"], ny["finalAt"]), (True, 23, iso(cut)))
        self.assertEqual((ny["precip"]["resolved"], ny["precip"]["hours"]), (True, 23))
        # the late file
        later = now + dt.timedelta(hours=8)
        self.fx.add_hour("rtma", gone)
        for u in (analysis.anl_url("rtma", gone), analysis.pcp_url("rtma", gone)):
            self.fx.published[u] = iso(later - dt.timedelta(minutes=30))       # well after the cut-off
        self.fx.values[("rtma", "temp", iso(gone))] = 310.15                # 98.6 F, which would move the high
        res = analysis.read_hour("rtma", gone, LOCS, {"wexp": [], "g184": []}, frames=False, now=later)
        analysis.write_hour(self.st, "rtma", gone, res, gi, cache, later)
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
        at = t + dt.timedelta(hours=1, minutes=50)
        self.assertEqual(self.run_pass(at), 0)
        text = self.st.get(analysis.CSV_RAW_KEY.format(day="2026-09-26")).decode()
        raw = text.splitlines()
        self.assertEqual(raw[0], ",".join(analysis.RAW_HEADER))
        # the columns before 2026-11-03 keep their order and meaning, and each
        # server's copy follows them in a fixed order (owner's ruling 2026-11-03)
        self.assertEqual(analysis.RAW_HEADER, [
            "date", "location", "local_hour", "valid_utc", "temp_k", "wind_ms", "gust_ms", "precip_mm", "precip_file_utc",
            "analysis_source", "precip_source", "analysis_published", "precip_published",
            "analysis_settled", "precip_settled",
            "analysis_nodd_first_seen", "analysis_nodd_last_modified", "analysis_nodd_temp_k", "analysis_nodd_wind_ms",
            "analysis_nodd_gust_ms", "analysis_nodd_temp_sha256", "analysis_nodd_wind_sha256", "analysis_nodd_gust_sha256",
            "analysis_nomads_first_seen", "analysis_nomads_last_modified", "analysis_nomads_temp_k", "analysis_nomads_wind_ms",
            "analysis_nomads_gust_ms", "analysis_nomads_temp_sha256", "analysis_nomads_wind_sha256", "analysis_nomads_gust_sha256",
            "analysis_hash_match", "analysis_value_match", "analysis_late_copy",
            "analysis_nodd_replacements", "analysis_nodd_replacement_seen", "analysis_nodd_replacement_last_modified",
            "analysis_nodd_replacement_temp_sha256", "analysis_nodd_replacement_wind_sha256",
            "analysis_nodd_replacement_gust_sha256",
            "analysis_nomads_replacements", "analysis_nomads_replacement_seen", "analysis_nomads_replacement_last_modified",
            "analysis_nomads_replacement_temp_sha256", "analysis_nomads_replacement_wind_sha256",
            "analysis_nomads_replacement_gust_sha256",
            "precip_nodd_first_seen", "precip_nodd_last_modified", "precip_nodd_mm", "precip_nodd_sha256",
            "precip_nomads_first_seen", "precip_nomads_last_modified", "precip_nomads_mm", "precip_nomads_sha256",
            "precip_hash_match", "precip_value_match", "precip_late_copy",
            "precip_nodd_replacements", "precip_nodd_replacement_seen", "precip_nodd_replacement_last_modified",
            "precip_nodd_replacement_sha256",
            "precip_nomads_replacements", "precip_nomads_replacement_seen", "precip_nomads_replacement_last_modified",
            "precip_nomads_replacement_sha256"])
        rows = [r for r in csv.DictReader(io.StringIO(text)) if r["location"] == "new-york-ny"]
        self.assertEqual(len(rows), 2)                         # the 18Z row's rain and the 19Z row's analysis
        r = next(x for x in rows if x["valid_utc"] == iso(t))
        landed, rained = iso(t + dt.timedelta(minutes=48)), iso(t + dt.timedelta(hours=1, minutes=17))
        self.assertEqual([r[k] for k in analysis.RAW_HEADER[2:13]],
                         ["15", iso(t), "293.15", "5.0", "8.0", "2.54", iso(t + dt.timedelta(hours=1)), "nomads", "nomads",
                          landed, rained])
        # each file's copy became final at the pass both servers' copies were in
        self.assertEqual((r["analysis_settled"], r["precip_settled"]), (iso(at), iso(at)))
        for server in analysis.SERVERS:
            a_, p_ = f"analysis_{server}_", f"precip_{server}_"
            self.assertEqual((r[a_ + "first_seen"], r[a_ + "last_modified"], r[a_ + "temp_k"], r[a_ + "wind_ms"], r[a_ + "gust_ms"]),
                             (iso(at), landed, "293.15", "5.0", "8.0"), server)
            self.assertEqual((r[p_ + "first_seen"], r[p_ + "last_modified"], r[p_ + "mm"]), (iso(at), rained, "2.54"), server)
            self.assertTrue(all(len(r[a_ + v + "_sha256"]) == 64 for v in ("temp", "wind", "gust")) and len(r[p_ + "sha256"]) == 64)
            self.assertEqual((r[a_ + "replacements"], r[a_ + "replacement_seen"], r[p_ + "replacements"]), ("0", "", "0"))
        self.assertEqual((r["analysis_hash_match"], r["analysis_value_match"], r["analysis_late_copy"],
                          r["precip_hash_match"], r["precip_value_match"], r["precip_late_copy"]),
                         ("true", "true", "false", "true", "true", "false"))
        day = self.st.get(analysis.CSV_DAY_KEY.format(day="2026-09-26")).decode().splitlines()
        self.assertEqual(day[0], ",".join(analysis.DAY_HEADER))
        self.assertTrue(day[0].startswith("date,location,name,state,final,final_at,hours,of,high_f,"))
        self.assertEqual(len(day), 1 + len(LOCS))              # one row per place
        p = next(l.split(",") for l in day[1:] if l.startswith("2026-09-26,new-york-ny,New York,NY,"))
        self.assertEqual(p[8:10], ["68", "68.0"])                               # high, whole and exact

    def test_listed_days_without_their_csv_files_get_them_a_few_a_pass(self):
        t = hour(2026, 9, 26, 19)
        self.fx.add_hour("rtma", t)
        self.assertEqual(self.run_pass(), 0)
        # a day written before the CSV files existed: its day file is there, its CSVs are not
        self.st.put(analysis.DAY_KEY.format(day="2026-09-24"), b'{"locations": {}}', "application/json")
        for k in (analysis.CSV_RAW_KEY, analysis.CSV_DAY_KEY):
            self.assertIsNone(self.st.get(k.format(day="2026-09-24")))
        status = {"errors": [], "failed": 0}
        analysis.refresh_days(self.st, analysis.load_state(self.st), LOCS, {}, NOW, archive.Deadline(600), status)
        self.assertEqual(status["refreshed"], {"days": 1, "left": 0})
        # a day with no hours has no rows, so no files are written for it, and it is left to the next pass
        self.assertIsNone(self.st.get(analysis.CSV_RAW_KEY.format(day="2026-09-24")))
        self.assertIsNotNone(self.st.get(analysis.CSV_RAW_KEY.format(day="2026-09-26")))

    def test_days_written_before_urma_was_dropped_are_rewritten_a_few_a_pass(self):
        # 2026-09-30: the lane reads RTMA only. A state from before carries
        # URMA's lanes and re-read records and no layout, and the published
        # days, their place files and their CSV files carry URMA
        keep = analysis.REFRESH_DAYS_PER_PASS
        analysis.REFRESH_DAYS_PER_PASS = 1
        self.addCleanup(lambda: setattr(analysis, "REFRESH_DAYS_PER_PASS", keep))
        t1, t2 = hour(2026, 9, 25, 19), hour(2026, 9, 26, 19)
        for t in (t1, t2):
            self.fx.add_hour("rtma", t)
            res = analysis.read_hour("rtma", t, LOCS, {"wexp": [], "g184": []}, frames=False, now=NOW)
            analysis.write_hour(self.st, "rtma", t, res, {}, {}, NOW)
        old = analysis.new_state()
        del old["layout"]
        old["products"]["urma"] = analysis._product_state()
        old["backfill"]["urma"] = {"cursor": None, "oldest": None, "done": True, "queue": []}
        old.update({"rereads": {"2026-09-25": iso(NOW)}, "revisions": {"2026-09-25": {}}})
        self.st.put(analysis.STATE_KEY, json.dumps(old).encode(), "application/json")
        urma = {"hours": 3, "of": 24, "complete": False, "final": False, "closed": False}
        for d in ("2026-09-25", "2026-09-26"):
            self.st.put(analysis.DAY_KEY.format(day=d), json.dumps(
                {"schema": analysis.SCHEMA, "day": d, "locations": {"new-york-ny": {"urma": urma}}}).encode(), "application/json")
            self.st.put(analysis.LOC_KEY.format(loc="new-york-ny", day=d), json.dumps(
                {"hours": [{"t": iso(t1), "rtma": None, "urma": {"temp": 60.0}}], "summary": {"urma": urma}}).encode(),
                "application/json")
            for k in (analysis.CSV_RAW_KEY, analysis.CSV_DAY_KEY):
                self.st.put(k.format(day=d), b"date,location,product\n", "text/csv; charset=utf-8")
        state = analysis.load_state(self.st)
        self.assertEqual((list(state["products"]), list(state["backfill"]), state["layout"]), (["rtma"], ["rtma"], None))
        self.assertNotIn("rereads", state)
        self.assertNotIn("revisions", state)
        status = {"errors": [], "failed": 0}
        analysis.refresh_days(self.st, state, LOCS, {}, NOW, archive.Deadline(600), status)
        self.assertEqual(status["refreshed"], {"days": 1, "left": 1})
        self.assertEqual((state["layout"], state["relayout"]), (analysis.LAYOUT, ["2026-09-25"]))
        # the newest day first
        self.assertEqual(list(self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"]), ["rtma"])
        self.assertEqual(list(self.read(analysis.DAY_KEY.format(day="2026-09-25"))["locations"]["new-york-ny"]), ["urma"])
        analysis.refresh_days(self.st, state, LOCS, {}, NOW, archive.Deadline(600), status)
        self.assertEqual(status["refreshed"], {"days": 1, "left": 0})
        self.assertNotIn("relayout", state)
        for d in ("2026-09-25", "2026-09-26"):
            self.assertEqual(list(self.read(analysis.DAY_KEY.format(day=d))["locations"]["new-york-ny"]), ["rtma"], d)
            loc = self.read(analysis.LOC_KEY.format(loc="new-york-ny", day=d))
            self.assertEqual(list(loc["summary"]), ["rtma"], d)
            self.assertEqual([r for r in loc["hours"] if "urma" in r], [], d)
            self.assertTrue(self.st.get(analysis.CSV_RAW_KEY.format(day=d)).decode().startswith("date,location,local_hour,"), d)
            self.assertNotIn(b"urma", self.st.get(analysis.CSV_DAY_KEY.format(day=d)), d)
        # nothing is left, so a later pass rewrites no day for the layout
        analysis.refresh_days(self.st, state, LOCS, {}, NOW, archive.Deadline(600), status)
        self.assertEqual(status["refreshed"], {"days": 0, "left": 0})

    def test_the_grid_index_drops_the_frames_of_a_product_the_lane_no_longer_reads(self):
        # the URMA frames leave the index; the files stay where they are
        stale = analysis.FRAME_KEY.format(product="urma", var="temp", stamp="20260926T13Z")
        self.st.put(stale, b"{}", "application/json")
        self.st.put(analysis.GRID_INDEX_KEY, json.dumps({"frames": {"urma": {"temp": {"2026-09-26": ["13"]}},
                                                                    "rtma": {"temp": {"2026-09-26": ["18"]}}}}).encode(),
                    "application/json")
        self.fx.add_hour("rtma", hour(2026, 9, 26, 19))
        self.assertEqual(self.run_pass(), 0)
        gi = self.read(analysis.GRID_INDEX_KEY)
        self.assertEqual(list(gi["frames"]), ["rtma"])
        self.assertEqual(gi["frames"]["rtma"]["temp"], {"2026-09-26": ["18", "19"]})
        self.assertIsNotNone(self.st.get(stale))
        self.assertNotIn(stale, self.st.deletes)

    def test_a_message_on_another_grid_is_refused_and_reported(self):
        t = hour(2026, 9, 26, 19)
        # no precipitation file, which is read on its own as it lands and
        # would be a read of its own (test_a_precipitation_read_is_a_read)
        self.fx.add_hour("rtma", t, precip=False)
        self.fx.grids[("rtma", "TMP", iso(t))] = "other"
        self.assertEqual(self.run_pass(), 1)                   # the only reads this pass failed
        self.assertIsNone(self.read(analysis.hour_key("rtma", t)))
        self.assertEqual(archive.LAST_STATUS["failed"], 2)     # refused on each server
        self.assertEqual(self.st.puts[-1][0], analysis.INDEX_KEY)
        # the file stays open for the next passes, and the walk is past it
        state = self.state()
        self.assertIn("anl", state["products"]["rtma"]["open"][iso(t)])
        self.assertEqual(state["products"]["rtma"]["scanned"], iso(t))

    def test_a_raising_fetch_is_the_hours_failure_and_the_pass_writes_everything_else(self):
        # a NOAA 5xx that outlives the retries raises out of gov_weather; the
        # hour is recorded against the product and the state, the grid index
        # and the index are written as on any pass
        t = hour(2026, 9, 26, 19)
        self.fx.add_hour("rtma", t)
        self.fx.raise_for = lambda url: "rtma2p5" in url and not url.endswith(".idx")
        self.assertEqual(self.run_pass(), 1)                   # nothing was read
        self.assertIsNone(self.read(analysis.hour_key("rtma", t)))
        self.assertIsNotNone(self.state())
        self.assertIsNotNone(self.read(analysis.GRID_INDEX_KEY))
        self.assertIsNotNone(self.read(analysis.INDEX_KEY))
        self.assertEqual(self.st.puts[-1][0], analysis.INDEX_KEY)
        # each lane stops asking a server for a kind of file at its first
        # refusal, so an outage costs one failing read of each kind from each
        # server in each lane: the live hour's analysis and precipitation from
        # both servers, and the precipitation of the first hour the backfill
        # reaches from both
        self.assertEqual(archive.LAST_STATUS["failed"], 6)
        self.assertEqual(self.health()["rtma"]["fail_streak"], 1)
        self.assertIn(iso(t), self.state()["products"]["rtma"]["open"])
        # persistently failing, the product reaches the alarm streak
        alarms = None
        for i in range(1, archive.FAIL_STREAK_ALARM):
            self.run_pass(NOW + dt.timedelta(minutes=10 * i))
            alarms = archive.LAST_STATUS["alarms"]
        self.assertEqual(self.health()["rtma"]["fail_streak"], archive.FAIL_STREAK_ALARM)
        self.assertEqual(alarms, ["analysis: no rtma hour readable for %d passes" % archive.FAIL_STREAK_ALARM])
        self.assertEqual(self.st.puts[-1][0], analysis.INDEX_KEY)
        # the file reads again: the streak ends
        self.fx.raise_for = None
        self.run_pass(NOW + dt.timedelta(minutes=10 * archive.FAIL_STREAK_ALARM))
        self.assertEqual((self.health()["rtma"]["fail_streak"], archive.LAST_STATUS["alarms"]), (0, []))
        self.assertEqual(self.read(analysis.INDEX_KEY)["sources"]["rtma"]["latest"], iso(t))

    def test_a_product_the_lane_no_longer_reads_raises_no_alarm(self):
        # the health file keeps URMA's last streak after 2026-09-30; it is
        # never advanced again and must not alarm
        self.st.put(analysis.HEALTH_KEY, json.dumps({"urma": {"fail_streak": 99, "last_ok": None,
                                                              "last_error": "read failed"}}).encode(), "application/json")
        self.fx.add_hour("rtma", hour(2026, 9, 26, 19))
        self.assertEqual(self.run_pass(), 0)
        self.assertEqual(archive.LAST_STATUS["alarms"], [])
        self.assertEqual(self.health()["urma"]["fail_streak"], 99)
        self.assertEqual(self.health()["rtma"]["fail_streak"], 0)

    def test_a_raising_decode_is_the_hours_error_not_the_pass(self):
        t = hour(2026, 9, 26, 19)
        self.fx.add_hour("rtma", t, precip=False)             # the analysis is the pass's only read
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
        # a message that will not decode fails the check, so neither download is a copy yet
        for server in analysis.SERVERS:
            self.assertIn(f"rtma 2026-09-26T19:00:00Z anl {server}: TMP: undecodable (ValueError: data representation "
                          "template 5.40 is not supported)", self.errors)
            self.assertIsNone(self.read(analysis.copy_key("rtma", "anl", server, t)))
        self.assertEqual(self.st.puts[-1][0], analysis.INDEX_KEY)

    def test_the_alarm_fires_when_nothing_lands_and_asof_stays_on_the_hour_read(self):
        t_r = hour(2026, 9, 26, 19)
        self.fx.add_hour("rtma", t_r)
        self.assertEqual(self.run_pass(), 0)
        alarms = []
        for i in range(1, 8):
            self.run_pass(NOW + dt.timedelta(hours=i))
            alarms = archive.LAST_STATUS["alarms"]
        self.assertEqual(alarms, ["analysis: no rtma hour readable for 6 passes"])
        state = self.state()
        self.assertEqual(state["products"]["rtma"]["newest"], iso(t_r))
        self.assertEqual(state["products"]["rtma"]["scanned"], iso(analysis.expected_latest("rtma", NOW + dt.timedelta(hours=7))))
        idx = self.read(analysis.INDEX_KEY)
        self.assertEqual(idx["asof"], iso(t_r))
        self.assertEqual(idx["sources"]["rtma"]["latest"], iso(t_r))
        self.assertEqual(self.read(analysis.GRID_INDEX_KEY)["asof"], iso(t_r))
        # the hours that never landed stay open, asked for again until they land
        opened = state["products"]["rtma"]["open"]
        self.assertTrue(all(iso(t_r + dt.timedelta(hours=k)) in opened for k in range(1, 8)), sorted(opened))
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
        self.fx.add_hour("rtma", h2, servers=("nodd",))
        state = analysis.new_state()
        state["products"]["rtma"]["newest"] = iso(hour(2026, 9, 26, 17))
        analysis.save_state(self.st, state, NOW)
        # 19Z is not on NOMADS yet and NOAA Open Data refuses it, which keeps
        # NOAA Open Data's analyses out of the rest of the pass; 18Z comes
        # from NOMADS, its frames with it
        bad = analysis.file_url("rtma", "anl", "nodd", h2) + ".idx"
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

    def test_a_file_stays_open_until_it_settles_and_one_on_neither_server_settles_on_no_copy(self):
        t = hour(2026, 9, 26, 19)
        self.caught_up(t - dt.timedelta(hours=1))
        self.fx.add_hour("rtma", t, precip=False)
        self.assertEqual(self.run_pass(), 0)
        # both servers have the analysis, so it settled at once; the precipitation stays open
        self.assertEqual(self.read(analysis.hour_key("rtma", t))["values"]["new-york-ny"]["temp"], 293.15)
        self.assertIsNone(self.read(analysis.precip_key("rtma", t)))
        self.assertEqual(self.state()["products"]["rtma"]["newest"], iso(t))
        self.assertEqual(self.state()["products"]["rtma"]["open"], {iso(t): {"pcp": {}}})
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"]["rtma"]
        self.assertEqual((ny["hours"], ny["precip"]), (1, None))
        # the file lands on both servers: the next pass keeps both copies and settles it
        self.fx.add_precip("rtma", t)
        self.fx.values[("rtma", "precip", iso(t))] = 2.54
        self.assertEqual(self.run_pass(NOW + dt.timedelta(minutes=10)), 0)
        self.assertEqual(self.read(analysis.precip_key("rtma", t))["source"], "nomads")
        self.assertEqual(self.state()["products"]["rtma"]["open"], {})
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"]["rtma"]
        self.assertEqual(ny["precip"], {"value": 0.1, "exact": 0.1, "hours": 1, "resolved": False, "resolvedAt": None})
        # a precipitation file on neither server settles on no copy, for each
        # day it fills at the first ten-minute pass after that day's cut-off
        # (owner's ruling 2026-11-04), and its record is written with the last
        t2 = t + dt.timedelta(hours=1)
        self.fx.add_hour("rtma", t2, precip=False)
        self.assertEqual(self.run_pass(t2 + dt.timedelta(minutes=55)), 0)
        last_cut = max(analysis.day_cutoff(analysis.row_day("pcp", t2, l["tz"]), l["tz"]) for l in LOCS)
        self.assertEqual(last_cut, hour(2026, 9, 27, 7) + dt.timedelta(minutes=45))       # Los Angeles's 12:45 AM
        self.assertEqual(self.run_pass(last_cut - dt.timedelta(minutes=10)), 0)
        settled = self.state()["products"]["rtma"]["open"][iso(t2)]["pcp"]["cutoffs"]
        self.assertEqual(sorted(settled), ["2026-09-27T04:45:00Z", "2026-09-27T05:45:00Z"])     # New York's, Chicago's
        self.assertEqual({e["with"] == [] and e["src"] is None for e in settled.values()}, {True})
        self.assertIsNone(self.read(analysis.precip_key("rtma", t2)))
        # past two hours a file is asked for once an hour, and again at the
        # first pass after each cut-off, which then settles that cut-off's days
        urls = {analysis.file_url("rtma", "pcp", s, t2) for s in analysis.SERVERS}
        k = len(self.fx.fetched)
        self.assertEqual(self.run_pass(last_cut - dt.timedelta(minutes=5)), 0)
        self.assertFalse(urls & set(self.fx.fetched[k:]))
        self.assertIn(iso(t2), self.state()["products"]["rtma"]["open"])
        k = len(self.fx.fetched)
        self.assertEqual(self.run_pass(last_cut + dt.timedelta(minutes=3)), 0)
        self.assertEqual(urls & set(self.fx.fetched[k:]), urls)
        state = self.state()["products"]["rtma"]
        self.assertIn(iso(t2), state["pmissing"])
        pre = self.read(analysis.precip_key("rtma", t2))
        self.assertEqual((pre["source"], pre["complete"], pre["settled"], set(pre["picks"].values())),
                         (None, False, iso(last_cut + dt.timedelta(minutes=3)), {None}))
        # it is still looked for until a day after its hour, and a copy that
        # comes is recorded and flagged and never used
        self.assertIn(iso(t2), state["open"])

    def test_when_the_two_copies_differ_nomads_counts_and_the_hash_and_value_differences_are_kept_apart(self):
        # NOAA Open Data's precipitation copy leaves Los Angeles's cell out of
        # its bitmap and NOMADS's has it, and the two analysis copies are other
        # bytes holding the same values
        t = hour(2026, 9, 26, 19)
        self.fx.add_hour("rtma", t, variant="b")
        self.fx.values[("rtma", "precip", iso(t))] = lambda k: None if k == LA_K184 else 0.254
        self.fx.values[("rtma", "precip", iso(t), "b")] = lambda k: 0.508 if k == LA_K184 else 0.254
        self.assertEqual(self.run_pass(), 0)
        nod = self.read(analysis.copy_key("rtma", "pcp", "nodd", t))
        nom = self.read(analysis.copy_key("rtma", "pcp", "nomads", t))
        self.assertNotEqual(nod["sha256"], nom["sha256"])
        # the hashes differ, so both copies were decoded at the places
        self.assertEqual((nod["decoded"], nod["missing"], nod["values"]["los-angeles-ca"]), (["precip"], ["los-angeles-ca"], None))
        self.assertEqual((nom["decoded"], nom["missing"], nom["values"]["los-angeles-ca"]), (["precip"], [], 0.508))
        pre = self.read(analysis.precip_key("rtma", t))
        self.assertEqual((pre["source"], pre["hashMatch"], pre["complete"], pre["values"]["los-angeles-ca"]),
                         ("nomads", False, True, 0.508))
        arc = self.read(analysis.hour_key("rtma", t))
        self.assertEqual((arc["source"], arc["hashMatch"]), ("nomads", False))
        # the map follows NOMADS's copy, read first, and NOAA Open Data's adds no frame
        frames = [k for k, _, _ in self.st.puts if k.startswith("snapshots/analysis2/grid/rtma/")]
        self.assertEqual(len(frames), 4)
        # the raw CSV keeps the hash and the value differences apart
        raw = list(csv.DictReader(io.StringIO(self.st.get(analysis.CSV_RAW_KEY.format(day="2026-09-26")).decode())))
        la = next(r for r in raw if r["location"] == "los-angeles-ca" and r["valid_utc"] == iso(t - dt.timedelta(hours=1)))
        ny = next(r for r in raw if r["location"] == "new-york-ny" and r["valid_utc"] == iso(t - dt.timedelta(hours=1)))
        self.assertEqual((la["precip_mm"], la["precip_source"], la["precip_nodd_mm"], la["precip_nomads_mm"],
                          la["precip_hash_match"], la["precip_value_match"]), ("0.508", "nomads", "", "0.508", "false", "false"))
        self.assertEqual((ny["precip_hash_match"], ny["precip_value_match"]), ("false", "true"))
        at = next(r for r in raw if r["location"] == "new-york-ny" and r["valid_utc"] == iso(t))
        self.assertEqual((at["analysis_source"], at["analysis_hash_match"], at["analysis_value_match"], at["analysis_late_copy"]),
                         ("nomads", "false", "true", "false"))
        self.assertNotEqual(at["analysis_nodd_temp_sha256"], at["analysis_nomads_temp_sha256"])

    # ---- each server's copy (owner's ruling 2026-11-03)
    def raw_row(self, day, lid, t):
        """The raw CSV row of one place and hour."""
        text = self.st.get(analysis.CSV_RAW_KEY.format(day=day)).decode()
        return next(r for r in raw_rows_of(text, lid) if r["valid_utc"] == iso(t))

    def frame_puts(self, t):
        return [k for k, _, _ in self.st.puts if k.startswith(analysis.PREFIX + "grid/rtma/") and analysis._stamp(t) in k]

    def test_which_copy_counts_and_when_it_settles(self):
        t = hour(2026, 9, 26, 19)
        cut = "2026-09-27T04:45:00Z"                     # New York's cut-off for the hour

        def c(pub, sha="a", seen=None):
            return {"published": pub, "seen": seen or pub, "sha256": {"temp": sha}}
        nod, nom, late = c("2026-09-26T19:47:00Z"), c("2026-09-26T19:49:00Z", "b"), c("2026-09-27T05:10:00Z", "b")
        # NOMADS's copy counts when it was published by the cut-off, whatever
        # NOAA Open Data's holds, then NOAA Open Data's; neither by then, and the
        # row shows NOMADS's, which never counts
        self.assertEqual(analysis.choose({"nodd": nod, "nomads": nom}, cut), "nomads")
        self.assertEqual(analysis.choose({"nodd": nod, "nomads": None}, cut), "nodd")
        self.assertEqual(analysis.choose({"nodd": nod, "nomads": late}, cut), "nodd")
        self.assertEqual(analysis.choose({"nodd": c("2026-09-27T05:00:00Z"), "nomads": late}, cut), "nomads")
        self.assertIsNone(analysis.choose({"nodd": None, "nomads": None}, cut))
        # a copy without a Last-Modified is timed by when the lane first saw it
        self.assertEqual(analysis.choose({"nodd": None, "nomads": c(None, seen="2026-09-27T04:40:00Z")}, cut), "nomads")
        self.assertEqual((analysis.first_published({"nodd": nod, "nomads": nom}), analysis.first_server({"nodd": nod, "nomads": nom})),
                         ("2026-09-26T19:47:00Z", "nodd"))
        self.assertEqual((analysis.hash_match({"nodd": nod, "nomads": nom}), analysis.hash_match({"nodd": nod, "nomads": None})),
                         (False, None))
        # settling (owner's ruling 2026-11-04), cut-off by cut-off: New York's,
        # Chicago's and Los Angeles's for this hour
        cuts = sorted(set(analysis.place_cuts("anl", t, LOCS).values()))
        self.assertEqual(cuts, ["2026-09-27T04:45:00Z", "2026-09-27T05:45:00Z", "2026-09-27T07:45:00Z"])

        def put(server, pub, seen, temp):
            values = {loc["id"]: {"temp": temp, "wind": 5.0, "gust": 8.0} for loc in LOCS}
            analysis.write_copy(self.st, "rtma", "anl", server, t, {"server": server, "seen": seen, "published": pub,
                                                                    "sha256": {"temp": server}, "values": values}, {})
        put("nodd", "2026-09-26T19:47:00Z", "2026-09-26T19:50:00Z", 290.0)
        slots: dict = {}
        # one copy in hand settles nothing before a cut-off, nor on a windowed pass after one
        self.assertFalse(analysis.settle_cuts(self.st, "rtma", "anl", t, slots, LOCS, NOW, {}, True))
        ny_pass = analysis._parse_iso(cuts[0]) + dt.timedelta(minutes=3)
        self.assertFalse(analysis.settle_cuts(self.st, "rtma", "anl", t, slots, LOCS, ny_pass, {}, False))
        self.assertEqual(slots, {})
        # the first ten-minute pass after New York's cut-off settles its rows on the copy in hand
        self.assertTrue(analysis.settle_cuts(self.st, "rtma", "anl", t, slots, LOCS, ny_pass, {}, True))
        self.assertEqual(slots["cutoffs"], {cuts[0]: {"at": iso(ny_pass), "with": ["nodd"], "src": "nodd",
                                                      "published": "2026-09-26T19:47:00Z", "pubSrc": "nodd"}})
        self.assertIsNone(self.read(analysis.hour_key("rtma", t)))           # two cut-offs still to come
        # NOMADS's copy, published before every cut-off, comes after New York's
        # rows settled: both copies are in, so the other cut-offs settle at once
        # on it, and New York's rows keep NOAA Open Data's
        later = ny_pass + dt.timedelta(minutes=10)
        put("nomads", "2026-09-26T19:49:00Z", iso(later), 291.0)
        self.assertTrue(analysis.settle_cuts(self.st, "rtma", "anl", t, slots, LOCS, later, {}, False))
        arc = self.read(analysis.hour_key("rtma", t))
        self.assertEqual(arc["picks"], {"new-york-ny": "nodd", "chicago-il": "nomads", "los-angeles-ca": "nomads"})
        self.assertEqual((arc["values"]["new-york-ny"]["temp"], arc["values"]["chicago-il"]["temp"]), (290.0, 291.0))
        self.assertEqual([arc["cutoffs"][c_]["at"] for c_ in cuts], [iso(ny_pass), iso(later), iso(later)])
        self.assertEqual(arc["source"], None)
        ny = analysis.file_view(self.st, "rtma", "anl", t, "new-york-ny", cuts[0], later, None, {})
        chi = analysis.file_view(self.st, "rtma", "anl", t, "chicago-il", cuts[1], later, None, {})
        self.assertEqual((ny["src"], ny["settled"], ny["late"]), ("nodd", iso(ny_pass), True))
        self.assertEqual((chi["src"], chi["settled"], chi["late"], chi["pub"], chi["pubSrc"]),
                         ("nomads", iso(later), False, "2026-09-26T19:47:00Z", "nodd"))
        # a file carried over from before 2026-11-03 settles on no copy for each
        # cut-off that had passed by then, its days being final already; Los
        # Angeles's, still to come then, settles at the next ten-minute pass
        t2 = hour(2026, 9, 25, 19)
        carried = {"migrated": "2026-09-26T06:00:00Z"}
        self.assertTrue(analysis.settle_cuts(self.st, "rtma", "anl", t2, carried, LOCS, NOW, {}, False))
        self.assertEqual(carried["cutoffs"], {c_: {"at": "2026-09-26T06:00:00Z", "with": [], "src": None, "published": None,
                                                   "pubSrc": None}
                                              for c_ in ("2026-09-26T04:45:00Z", "2026-09-26T05:45:00Z")})
        self.assertTrue(analysis.settle_cuts(self.st, "rtma", "anl", t2, carried, LOCS, NOW, {}, True))
        self.assertEqual(self.read(analysis.hour_key("rtma", t2))["picks"],
                         {"new-york-ny": None, "chicago-il": None, "los-angeles-ca": None})

    def test_both_copies_are_kept_with_their_times_and_hashes_and_the_hour_takes_the_first_publication(self):
        # rule 2: the publication time the cut-off is judged on is the first
        # time the file was on either server; the values are NOMADS's copy,
        # here the same bytes, so NOAA Open Data's copy takes NOMADS's values
        # without decoding them
        t = hour(2026, 9, 26, 19)
        self.caught_up(t - dt.timedelta(hours=1))
        self.fx.published[analysis.anl_url("rtma", t)] = "2026-09-26T19:47:00Z"
        self.fx.published[analysis.anl_url("rtma", t, nomads=True)] = "2026-09-26T19:49:00Z"
        self.fx.add_hour("rtma", t)
        self.assertEqual(self.run_pass(), 0)
        nod = self.read(analysis.copy_key("rtma", "anl", "nodd", t))
        nom = self.read(analysis.copy_key("rtma", "anl", "nomads", t))
        self.assertEqual((nom["decoded"], nod["decoded"], nod["sha256"] == nom["sha256"]), (["temp", "wind", "gust"], [], True))
        self.assertEqual(nod["values"], nom["values"])
        self.assertEqual((nod["seen"], nod["published"], nom["published"]), (iso(NOW), "2026-09-26T19:47:00Z", "2026-09-26T19:49:00Z"))
        arc = self.read(analysis.hour_key("rtma", t))
        self.assertEqual((arc["source"], arc["published"], arc["hashMatch"], sorted(arc["servers"])),
                         ("nomads", "2026-09-26T19:47:00Z", True, ["nodd", "nomads"]))
        self.assertEqual(self.state()["products"]["rtma"]["open"], {})
        row = next(r for r in self.read(analysis.LOC_KEY.format(loc="new-york-ny", day="2026-09-26"))["hours"]
                   if r["t"] == iso(t))["rtma"]
        self.assertEqual((row["pub"], row["src"], row["pubSrc"]), ("2026-09-26T19:47:00Z", "nomads", "nodd"))
        self.assertNotIn("lateCopy", row)
        r = self.raw_row("2026-09-26", "new-york-ny", t)
        # both copies came in at this pass, so the row's copy became final then
        self.assertEqual((r["temp_k"], r["analysis_source"], r["analysis_published"], r["analysis_settled"]),
                         ("293.15", "nomads", "2026-09-26T19:47:00Z", iso(NOW)))
        self.assertEqual(row["settled"], iso(NOW))
        self.assertEqual((r["analysis_nodd_first_seen"], r["analysis_nodd_last_modified"], r["analysis_nodd_temp_k"],
                          r["analysis_nomads_first_seen"], r["analysis_nomads_last_modified"], r["analysis_nomads_temp_k"]),
                         (iso(NOW), "2026-09-26T19:47:00Z", "293.15", iso(NOW), "2026-09-26T19:49:00Z", "293.15"))
        self.assertEqual((r["analysis_nodd_temp_sha256"], r["analysis_nomads_gust_sha256"]),
                         (nod["sha256"]["temp"], nom["sha256"]["gust"]))
        self.assertEqual((r["analysis_hash_match"], r["analysis_value_match"], r["analysis_late_copy"],
                          r["analysis_nodd_replacements"], r["analysis_nomads_replacement_seen"]),
                         ("true", "true", "false", "0", ""))

    def test_a_file_settles_once_both_copies_are_in_and_a_missing_copy_is_looked_for_until_a_day_after_its_hour(self):
        t = hour(2026, 9, 26, 19)
        self.caught_up(t - dt.timedelta(hours=1))
        self.fx.add_hour("rtma", t, servers=("nomads",))
        self.assertEqual(self.run_pass(), 0)
        # NOMADS's copy alone settles nothing before the cut-off, since NOAA
        # Open Data's may still come; the row shows NOMADS's, not yet final
        self.assertIsNone(self.read(analysis.hour_key("rtma", t)))
        self.assertEqual(self.state()["products"]["rtma"]["open"], {iso(t): {"anl": {}, "pcp": {}}})
        r = self.raw_row("2026-09-26", "new-york-ny", t)
        self.assertEqual((r["temp_k"], r["analysis_source"], r["analysis_settled"]), ("293.15", "nomads", ""))
        # NOAA Open Data's copy comes: both are in, and the file settles at that pass
        self.fx.add_hour("rtma", t, servers=("nodd",))
        at = NOW + dt.timedelta(minutes=10)
        self.assertEqual(self.run_pass(at), 0)
        self.assertEqual(self.state()["products"]["rtma"]["open"], {})
        arc = self.read(analysis.hour_key("rtma", t))
        self.assertEqual((arc["source"], sorted(arc["servers"]), arc["settled"]), ("nomads", ["nodd", "nomads"], iso(at)))
        self.assertEqual(self.read(analysis.copy_key("rtma", "anl", "nodd", t))["decoded"], [])
        r = self.raw_row("2026-09-26", "new-york-ny", t)
        self.assertEqual((r["analysis_source"], r["analysis_nodd_temp_k"], r["analysis_hash_match"], r["analysis_settled"],
                          r["analysis_late_copy"]), ("nomads", "293.15", "true", iso(at), "false"))
        # the precipitation file that ends at 19Z is the 18Z row's
        self.assertEqual(self.raw_row("2026-09-26", "new-york-ny", t - dt.timedelta(hours=1))["precip_hash_match"], "true")
        # a file NOAA Open Data never has settles on NOMADS's copy at the first
        # ten-minute pass after its cut-offs, is looked for on NOAA Open Data
        # until a day after its hour, and then leaves the open list
        t2 = t + dt.timedelta(hours=1)
        self.fx.add_hour("rtma", t2, servers=("nomads",))
        self.assertEqual(self.run_pass(t2 + dt.timedelta(minutes=55)), 0)
        self.assertIsNone(self.read(analysis.hour_key("rtma", t2)))
        froze = max(analysis._cutoffs("anl", t2, LOCS)) + dt.timedelta(minutes=3)
        self.assertEqual(self.run_pass(froze), 0)
        arc2 = self.read(analysis.hour_key("rtma", t2))
        self.assertEqual((arc2["source"], arc2["settled"]), ("nomads", iso(froze)))
        self.assertIn(iso(t2), self.state()["products"]["rtma"]["open"])
        s = self.state()
        s["precipSwept"] = iso(t2 + dt.timedelta(hours=20))                    # no sweep in the way
        analysis.save_state(self.st, s, t2)
        self.assertEqual(self.run_pass(t2 + dt.timedelta(hours=analysis.RECHECK_AFTER_HOURS, minutes=8)), 0)
        self.assertNotIn(iso(t2), self.state()["products"]["rtma"]["open"])
        self.assertIsNone(self.read(analysis.copy_key("rtma", "anl", "nodd", t2)))
        self.assertEqual(self.raw_row("2026-09-26", "new-york-ny", t2)["analysis_hash_match"], "")

    def test_a_nomads_copy_that_differs_and_lands_after_the_cut_off_is_recorded_and_flagged_and_changes_nothing(self):
        # rule 3: NOAA Open Data's copy was on file by the cut-off and NOMADS's,
        # other bytes and other values, lands after it. The closed day keeps the
        # copy that was on file; the later one is recorded, and the hour is
        # flagged in the raw CSV and the place file, which changes no value,
        # count or state. The owner rules on this case
        t = hour(2026, 9, 26, 19)
        self.caught_up(t - dt.timedelta(hours=1))
        self.fx.add_hour("rtma", t, servers=("nodd",))
        self.assertEqual(self.run_pass(), 0)
        self.assertIsNone(self.read(analysis.hour_key("rtma", t)))             # NOMADS may still bring it
        last_cut = max(analysis._cutoffs("anl", t, LOCS))
        self.assertEqual(self.run_pass(last_cut + dt.timedelta(minutes=3)), 0)
        arc = self.read(analysis.hour_key("rtma", t))
        self.assertEqual((arc["source"], arc["values"]["new-york-ny"]["temp"]), ("nodd", 293.15))
        self.assertIn("anl", self.state()["products"]["rtma"]["open"][iso(t)])
        day_key = analysis.DAY_KEY.format(day="2026-09-26")
        loc_key = analysis.LOC_KEY.format(loc="new-york-ny", day="2026-09-26")
        before = self.read(day_key)["locations"]
        self.assertTrue(before["new-york-ny"]["rtma"]["final"])
        frames = len(self.frame_puts(t))
        landed = iso(last_cut + dt.timedelta(minutes=20))
        self.fx.published[analysis.anl_url("rtma", t, nomads=True)] = landed
        self.fx.add_hour("rtma", t, servers=("nomads",), precip=False, variant="b")
        self.fx.values[("rtma", "temp", iso(t), "b")] = 300.0
        self.assertEqual(self.run_pass(last_cut + dt.timedelta(minutes=70)), 0)
        nom = self.read(analysis.copy_key("rtma", "anl", "nomads", t))
        self.assertEqual((nom["published"], nom["values"]["new-york-ny"]["temp"]), (landed, 300.0))
        self.assertEqual(self.read(analysis.hour_key("rtma", t)), arc)
        self.assertEqual(sorted(self.state()["products"]["rtma"]["open"][iso(t)]), ["pcp"])
        self.assertEqual(self.read(day_key)["locations"], before)
        self.assertEqual(len(self.frame_puts(t)), frames)                      # the map keeps the copy that counts
        row = next(r for r in self.read(loc_key)["hours"] if r["t"] == iso(t))["rtma"]
        self.assertEqual((row["temp"], row["src"], row["lateCopy"]), (analysis.cut(analysis.exact("temp", 293.15), 0.001),
                                                                     "nodd", True))
        self.assertNotIn("late", row)
        r = self.raw_row("2026-09-26", "new-york-ny", t)
        # the row settled at the first ten-minute pass after its cut-off, before
        # NOMADS's copy was first seen, which is therefore never used
        froze = iso(last_cut + dt.timedelta(minutes=3))
        self.assertEqual((r["temp_k"], r["analysis_source"], r["analysis_settled"], r["analysis_nomads_temp_k"],
                          r["analysis_nomads_last_modified"], r["analysis_hash_match"], r["analysis_value_match"],
                          r["analysis_late_copy"]),
                         ("293.15", "nodd", froze, "300.0", landed, "false", "false", "true"))
        self.assertGreater(r["analysis_nomads_first_seen"], r["analysis_settled"])
        self.assertEqual(row["settled"], froze)

    def test_nomads_copies_found_unsettled_before_the_cut_off_redraw_the_map(self):
        # NOAA Open Data's copy comes first and is drawn; NOMADS's, other
        # bytes published by the cut-off, counts and redraws the messages
        # whose bytes differ
        t = hour(2026, 9, 26, 19)
        self.caught_up(t - dt.timedelta(hours=1))
        self.fx.add_hour("rtma", t, servers=("nodd",))
        self.assertEqual(self.run_pass(), 0)
        self.assertEqual(len(self.frame_puts(t)), 4)
        self.fx.add_hour("rtma", t, servers=("nomads",), precip=False, variant="b")
        self.fx.values[("rtma", "temp", iso(t), "b")] = 300.0
        self.assertEqual(self.run_pass(NOW + dt.timedelta(minutes=10)), 0)
        self.assertEqual(len(self.frame_puts(t)), 7)
        arc = self.read(analysis.hour_key("rtma", t))
        self.assertEqual((arc["source"], arc["values"]["new-york-ny"]["temp"]), ("nomads", 300.0))

    def test_a_later_replacement_is_recorded_beside_the_first_copy_and_never_used(self):
        t = hour(2026, 9, 26, 19)
        self.caught_up(t - dt.timedelta(hours=1))
        self.fx.add_hour("rtma", t)
        self.assertEqual(self.run_pass(), 0)
        first = self.read(analysis.copy_key("rtma", "anl", "nomads", t))
        arc = self.read(analysis.hour_key("rtma", t))
        s = self.state()
        s["precipSwept"] = iso(t + dt.timedelta(hours=20))                     # no sweep in the way
        s["products"]["rtma"]["rechecked"] = iso(t - dt.timedelta(hours=1))    # the recheck is up to the hour before
        analysis.save_state(self.st, s, t)
        # NOMADS writes the analysis again, other bytes, three hours later
        url = analysis.anl_url("rtma", t, nomads=True)
        self.fx.add_hour("rtma", t, servers=("nomads",), precip=False, variant="c")
        self.fx.published[url] = iso(t + dt.timedelta(hours=3))
        # it is looked at again a day after the hour, when every cut-off has passed
        at = t + dt.timedelta(hours=analysis.RECHECK_AFTER_HOURS, minutes=8)
        k = len(self.fx.fetched)
        self.assertEqual(self.run_pass(at), 0)
        heads = [u for u in self.fx.fetched[k:] if u.startswith("HEAD ")]
        self.assertEqual(sorted(heads), sorted("HEAD " + analysis.file_url("rtma", kind, s_, t)
                                               for kind in analysis.KINDS for s_ in analysis.SERVERS))
        later = self.read(analysis.later_key("rtma", "anl", "nomads", t))["later"]
        self.assertEqual(len(later), 1)
        self.assertEqual((later[0]["seen"], later[0]["published"]), (iso(at), iso(t + dt.timedelta(hours=3))))
        self.assertNotEqual(later[0]["sha256"]["temp"], first["sha256"]["temp"])
        self.assertIsNone(self.read(analysis.later_key("rtma", "anl", "nodd", t)))
        # never used: the first copy and the archive hour stand
        self.assertEqual(self.read(analysis.copy_key("rtma", "anl", "nomads", t)), first)
        self.assertEqual(self.read(analysis.hour_key("rtma", t)), arc)
        self.assertEqual(self.state()["products"]["rtma"]["rechecked"], iso(t))
        r = self.raw_row("2026-09-26", "new-york-ny", t)
        self.assertEqual((r["temp_k"], r["analysis_nomads_replacements"], r["analysis_nomads_replacement_seen"],
                          r["analysis_nomads_replacement_last_modified"], r["analysis_nomads_replacement_temp_sha256"],
                          r["analysis_nodd_replacements"]),
                         ("293.15", "1", iso(at), iso(t + dt.timedelta(hours=3)), later[0]["sha256"]["temp"], "0"))
        # the same replacement seen again is not recorded twice
        self.assertFalse(analysis.note_later(self.st, "rtma", "anl", "nomads", t,
                                             dict(later[0], sha256=later[0]["sha256"]), {}))

    def test_nomads_calls_stay_inside_the_pass_budget_and_a_skipped_call_records_nothing(self):
        budget = analysis.NomadsBudget(3)
        keep = analysis.NOMADS_MIN_INTERVAL
        analysis.NOMADS_MIN_INTERVAL = 0.05
        self.addCleanup(lambda: setattr(analysis, "NOMADS_MIN_INTERVAL", keep))
        times = []
        while budget.take():
            times.append(time.time())
        self.assertEqual((len(times), budget.left, budget.used), (3, 0, 3))
        self.assertTrue(all(b - a >= 0.045 for a, b in zip(times, times[1:])), times)
        analysis.NOMADS_MIN_INTERVAL = 0
        # four calls a pass: the analysis takes them all (the sidecar and three
        # messages), so the precipitation file waits for the next pass on
        # NOMADS without a call, and nothing says NOMADS lacks it
        keep_calls = analysis.NOMADS_CALLS_PER_PASS
        analysis.NOMADS_CALLS_PER_PASS = 4
        self.addCleanup(lambda: setattr(analysis, "NOMADS_CALLS_PER_PASS", keep_calls))
        t = hour(2026, 9, 26, 19)
        self.caught_up(t - dt.timedelta(hours=1))
        self.fx.add_hour("rtma", t)
        k = len(self.fx.fetched)
        self.assertEqual(self.run_pass(), 0)
        nomads = [u for u in self.fx.fetched[k:] if u.startswith(analysis.NOMADS_DEFAULT["rtma"])]
        self.assertEqual((len(nomads), self.status["nomadsCalls"]), (4, 4))
        self.assertNotIn(analysis.pcp_url("rtma", t, nomads=True), nomads)
        self.assertEqual(self.read(analysis.hour_key("rtma", t))["source"], "nomads")
        self.assertIsNotNone(self.read(analysis.copy_key("rtma", "pcp", "nodd", t)))
        self.assertIsNone(self.read(analysis.precip_key("rtma", t)))
        self.assertEqual(self.state()["products"]["rtma"]["open"], {iso(t): {"pcp": {}}})
        self.assertEqual(self.errors, [])
        self.assertEqual(self.run_pass(NOW + dt.timedelta(minutes=10)), 0)
        self.assertEqual(self.read(analysis.precip_key("rtma", t))["source"], "nomads")
        self.assertEqual(self.state()["products"]["rtma"]["open"], {})

    def test_the_backfill_stops_where_the_nomads_budget_runs_out_and_carries_on_next_pass(self):
        keep_calls = analysis.NOMADS_CALLS_PER_PASS
        analysis.NOMADS_CALLS_PER_PASS = 10
        self.addCleanup(lambda: setattr(analysis, "NOMADS_CALLS_PER_PASS", keep_calls))
        h16, h17, h18, h19 = (hour(2026, 9, 26, k) for k in (16, 17, 18, 19))
        for t in (h16, h17, h18):
            self.fx.add_hour("rtma", t)
        state = analysis.new_state()
        state["products"]["rtma"].update({"newest": iso(h19), "scanned": iso(h19), "since": iso(h19)})
        state["backfill"]["rtma"].update({"cursor": iso(h19), "oldest": iso(h19)})
        state["precipSwept"] = state["pruned"] = iso(h19)
        analysis.save_state(self.st, state, h19)
        # each hour takes five NOMADS calls, so 18Z and 17Z take the ten and 16Z
        # keeps only NOAA Open Data's copies, unsettled, where the walk stops
        self.assertEqual(self.run_pass(), 0)
        s = self.state()
        self.assertEqual((s["backfill"]["rtma"]["cursor"], self.status["nomadsCalls"]), (iso(h17), 10))
        for t in (h17, h18):
            self.assertEqual(self.read(analysis.hour_key("rtma", t))["source"], "nomads")
        self.assertIsNone(self.read(analysis.hour_key("rtma", h16)))
        self.assertIsNotNone(self.read(analysis.copy_key("rtma", "anl", "nodd", h16)))
        self.assertNotIn(iso(h16), s["products"]["rtma"]["open"])
        self.assertEqual(self.run_pass(NOW + dt.timedelta(minutes=10)), 0)
        self.assertLessEqual(self.state()["backfill"]["rtma"]["cursor"], iso(h16))
        self.assertEqual(self.read(analysis.hour_key("rtma", h16))["source"], "nomads")

    def test_no_copy_is_started_once_the_pass_is_inside_its_reserve(self):
        # the deadline is looked at before each copy, so a pass overruns it by
        # one copy at most
        t = hour(2026, 9, 26, 19)
        self.fx.add_hour("rtma", t)
        entry, k = {"anl": {}, "pcp": {}}, len(self.fx.fetched)
        for kind in analysis.KINDS:
            got = analysis.work_file(self.st, "rtma", kind, t, entry, LOCS, None, {}, {}, NOW, blank_status(),
                                     analysis.NomadsBudget(), set(), analysis.new_state(), deadline=archive.Deadline(1))
            self.assertEqual(got, set())
        self.assertEqual((entry, self.fx.fetched[k:]), ({"anl": {}, "pcp": {}}, []))
        got = analysis.work_file(self.st, "rtma", "anl", t, entry, LOCS, None, {}, {}, NOW, blank_status(),
                                 analysis.NomadsBudget(), set(), analysis.new_state(), deadline=archive.Deadline(600))
        self.assertTrue(got)
        self.assertNotIn("anl", entry)

    # ---- the freeze and broken transfers (owner's ruling 2026-11-04)
    @staticmethod
    def broken(body, name):
        """An analysis file's bytes with one message claiming another grid, the
        same length, as a transfer that broke might give."""
        i = body.index(f"|{name}|".encode())
        j = body.index(b"|wexp|", i)
        return body[:j] + b"|oops|" + body[j + 6:]

    def test_a_download_that_fails_the_check_is_no_copy_until_it_comes_whole(self):
        # NOAA Open Data's analysis comes broken once, its TMP message claiming
        # another grid, and whole the next pass; NOMADS's comes whole both times
        t = hour(2026, 9, 26, 19)
        self.caught_up(t - dt.timedelta(hours=1))
        self.fx.add_hour("rtma", t, precip=False)
        url = analysis.anl_url("rtma", t)
        good = self.fx.bucket[url]
        self.fx.bucket[url] = self.broken(good, "TMP")
        self.assertEqual(self.run_pass(), 0)
        self.assertIsNone(self.read(analysis.copy_key("rtma", "anl", "nodd", t)))
        self.assertIn("rtma 2026-09-26T19:00:00Z anl nodd: TMP: message is on another grid", self.errors)
        self.assertEqual(sorted(self.state()["products"]["rtma"]["open"][iso(t)]["anl"]["nodd"]["bad"]), ["temp"])
        self.assertIsNone(self.read(analysis.hour_key("rtma", t)))              # NOMADS's copy alone, not final
        r = self.raw_row("2026-09-26", "new-york-ny", t)
        self.assertEqual((r["temp_k"], r["analysis_source"], r["analysis_nodd_first_seen"]), ("293.15", "nomads", ""))
        # the next pass downloads it whole: that is NOAA Open Data's copy, and the hour settles
        self.fx.bucket[url] = good
        at = NOW + dt.timedelta(minutes=10)
        self.assertEqual(self.run_pass(at), 0)
        nod = self.read(analysis.copy_key("rtma", "anl", "nodd", t))
        self.assertEqual((nod.get("refused"), nod["seen"], nod["values"]["new-york-ny"]["temp"]), (None, iso(at), 293.15))
        self.assertEqual((self.read(analysis.hour_key("rtma", t))["hashMatch"], self.state()["products"]["rtma"]["open"]),
                         (True, {iso(t): {"pcp": {}}}))                   # the precipitation is not out yet

    def test_the_same_failing_bytes_twice_are_noaas_own_file_and_can_count(self):
        # NOMADS serves other bytes than NOAA Open Data, and its TMP message
        # fails the check the same way twice: NOAA published that file, so it is
        # NOMADS's copy, the message refused, and NOMADS's copy counts. Per file
        # (owner's ruling 2026-11-04): the hour has no temperature at any place
        t = hour(2026, 9, 26, 19)
        self.caught_up(t - dt.timedelta(hours=1))
        self.fx.add_hour("rtma", t, precip=False, variant="b")
        url = analysis.anl_url("rtma", t, nomads=True)
        self.fx.bucket[url] = self.broken(self.fx.bucket[url], "TMP")
        self.assertEqual(self.run_pass(), 0)
        self.assertIsNone(self.read(analysis.copy_key("rtma", "anl", "nomads", t)))
        self.assertEqual(self.raw_row("2026-09-26", "new-york-ny", t)["temp_k"], "293.15")    # NOAA Open Data's, for now
        self.assertEqual(self.run_pass(NOW + dt.timedelta(minutes=10)), 0)
        nom = self.read(analysis.copy_key("rtma", "anl", "nomads", t))
        self.assertEqual((sorted(nom["refused"]), nom["decoded"], sorted(nom["sha256"])),
                         (["temp"], ["wind", "gust"], ["gust", "temp", "wind"]))
        arc = self.read(analysis.hour_key("rtma", t))
        self.assertEqual((arc["source"], {v["temp"] for v in arc["values"].values()}, arc["values"]["new-york-ny"]["wind"]),
                         ("nomads", {None}, 5.0))
        r = self.raw_row("2026-09-26", "new-york-ny", t)
        self.assertEqual((r["temp_k"], r["wind_ms"], r["analysis_source"], r["analysis_nodd_temp_k"]),
                         ("", "5.0", "nomads", "293.15"))

    def test_a_message_with_no_value_at_any_place_is_no_copy_until_it_comes_again(self):
        t = hour(2026, 9, 26, 19)
        self.fx.add_precip("rtma", t)
        self.fx.values[("rtma", "precip", iso(t))] = lambda k: None          # every place out of the bitmap
        first = analysis.read_copy("rtma", "pcp", "nodd", t, LOCS, None, NOW)
        self.assertEqual((first.status, first.bad["precip"]["why"]), ("suspect", "APCP: no value at any place"))
        again = analysis.read_copy("rtma", "pcp", "nodd", t, LOCS, None, NOW, prior_bad=first.bad)
        self.assertEqual((again.status, again.doc["refused"], again.doc["missing"]),
                         ("ok", {"precip": "APCP: no value at any place"}, sorted(l["id"] for l in LOCS)))

    def test_a_day_is_final_only_once_every_file_of_it_has_settled(self):
        # a day shown final never changes: past its cut-off it waits for the
        # first ten-minute pass, which settles the files still open, and a
        # windowed pass in between leaves it provisional
        t = hour(2026, 9, 27, 3)                         # 23:00 in New York, its day's last analysis
        self.caught_up(t - dt.timedelta(hours=1))
        self.fx.add_hour("rtma", t, precip=False, servers=("nodd",))         # NOMADS never has it
        self.assertEqual(self.run_pass(t + dt.timedelta(minutes=55)), 0)
        cut = analysis.day_cutoff("2026-09-26", "America/New_York")
        self.assertEqual(cut, hour(2026, 9, 27, 4) + dt.timedelta(minutes=45))
        # the day's last accumulation lands after the cut-off; a windowed pass
        # reads it and rebuilds the day, past its cut-off and still provisional
        self.fx.add_precip("rtma", t + dt.timedelta(hours=1))
        self.assertEqual(self.run_window(cut + dt.timedelta(minutes=1)), 0)
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"]["rtma"]
        self.assertEqual((ny["final"], ny["hours"]), (False, 1))
        # the first ten-minute pass after it settles the analysis on NOAA Open
        # Data's copy, NOMADS having none, and the day is final
        froze = cut + dt.timedelta(minutes=3)
        self.assertEqual(self.run_pass(froze), 0)
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"]["rtma"]
        self.assertEqual((ny["final"], ny["finalAt"], ny["hours"]), (True, iso(cut), 1))
        self.assertEqual(self.state()["products"]["rtma"]["open"][iso(t)]["anl"]["cutoffs"][iso(cut)],
                         {"at": iso(froze), "with": ["nodd"], "src": "nodd", "published": iso(t + dt.timedelta(minutes=48)),
                          "pubSrc": "nodd"})
        row = next(r for r in self.read(analysis.LOC_KEY.format(loc="new-york-ny", day="2026-09-26"))["hours"]
                   if r["t"] == iso(t))["rtma"]
        self.assertEqual((row["settled"], row["pcpSettled"]), (iso(froze), iso(cut + dt.timedelta(minutes=1))))
        # a NOMADS copy found later is recorded and flagged, and the day stays as it was
        before = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"]
        self.fx.add_hour("rtma", t, precip=False, servers=("nomads",), variant="b")
        self.fx.values[("rtma", "temp", iso(t), "b")] = 305.0
        self.assertEqual(self.run_pass(cut + dt.timedelta(minutes=63)), 0)
        self.assertIsNotNone(self.read(analysis.copy_key("rtma", "anl", "nomads", t)))
        self.assertEqual(self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"], before)
        r = self.raw_row("2026-09-26", "new-york-ny", t)
        self.assertEqual((r["temp_k"], r["analysis_late_copy"], r["analysis_settled"]), ("293.15", "true", iso(froze)))
        self.assertGreater(r["analysis_nomads_first_seen"], r["analysis_settled"])

    def test_a_newly_listed_day_gives_each_place_without_an_hour_a_file_with_no_rows(self):
        # between Eastern and Pacific midnight a western place has no hour of the
        # new day, and its page found no file to read: the day is listed with a
        # file of no rows for each such place, and its first hour replaces it
        t = hour(2026, 9, 27, 4)                         # midnight in New York, 23:00 in Chicago, 21:00 in Los Angeles
        day = "2026-09-27"
        self.caught_up(t - dt.timedelta(hours=1))
        self.fx.add_hour("rtma", t, precip=False)
        at = t + dt.timedelta(minutes=55)
        self.assertEqual(self.run_pass(at), 0)
        self.assertEqual(sorted(self.read(analysis.DAY_KEY.format(day=day))["locations"]), ["new-york-ny"])
        ny = self.read(analysis.LOC_KEY.format(loc="new-york-ny", day=day))
        self.assertTrue(any(r.get("rtma") for r in ny["hours"]))
        for lid, tz in (("chicago-il", "America/Chicago"), ("los-angeles-ca", "America/Los_Angeles")):
            self.assertEqual(self.read(analysis.LOC_KEY.format(loc=lid, day=day)), analysis.empty_loc_doc(lid, tz, day, at))
        # Chicago's first hour replaces its file, and the listed day's rebuild
        # leaves Los Angeles's as it was
        t2 = t + dt.timedelta(hours=1)
        self.fx.add_hour("rtma", t2, precip=False)
        self.assertEqual(self.run_pass(t2 + dt.timedelta(minutes=55)), 0)
        self.assertTrue(any(r.get("rtma") for r in self.read(analysis.LOC_KEY.format(loc="chicago-il", day=day))["hours"]))
        la = analysis.LOC_KEY.format(loc="los-angeles-ca", day=day)
        self.assertEqual(([k for k, _, _ in self.st.puts].count(la), self.read(la)["hours"]), (1, []))

    def test_a_file_carried_over_from_before_settles_on_no_copy_for_days_already_final(self):
        # in a state from before 2026-11-03 the analysis of 19Z was a gap, and
        # the deploy comes after every cut-off of its days, which were final
        # without it; the file turns up then on both servers
        t = hour(2026, 9, 26, 19)
        old = analysis.new_state()
        ps = old["products"]["rtma"]
        del ps["open"], ps["rechecked"]
        ps.update({"newest": iso(t), "scanned": iso(t), "since": iso(t - dt.timedelta(days=2)), "gaps": [iso(t)]})
        back = iso(t - dt.timedelta(days=31))
        old["backfill"]["rtma"].update({"cursor": back, "oldest": back, "done": True})
        old["precipSwept"] = old["pruned"] = iso(t)
        self.st.put(analysis.STATE_KEY, json.dumps(old).encode(), "application/json")
        self.fx.add_hour("rtma", t)
        at = analysis.day_end("2026-09-26", "America/Los_Angeles") + dt.timedelta(hours=2)
        self.assertEqual(self.run_pass(at), 0)
        arc = self.read(analysis.hour_key("rtma", t))
        self.assertEqual((set(arc["picks"].values()), {e["with"] == [] for e in arc["cutoffs"].values()}), ({None}, {True}))
        self.assertEqual(self.frame_puts(t), [])                                 # nor drawn on the map
        # the copies are recorded beside it, flagged, and never used
        raw: list = []
        analysis.build_day(self.st, "2026-09-26", LOCS, {}, at, self.state(), raw=raw)
        r = next(dict(zip(analysis.RAW_HEADER, x)) for x in raw if x[1] == "new-york-ny" and x[3] == iso(t))
        self.assertEqual((r["temp_k"], r["analysis_nomads_temp_k"], r["analysis_late_copy"], r["analysis_settled"]),
                         (None, 293.15, True, iso(at)))

    def test_a_state_from_before_the_copies_carries_its_gaps_and_pending_precipitation_into_the_open_list(self):
        h1, h2 = hour(2026, 9, 26, 17), hour(2026, 9, 26, 18)
        old = analysis.new_state()
        ps = old["products"]["rtma"]
        del ps["open"], ps["rechecked"]
        ps.update({"newest": iso(h2), "scanned": iso(h2), "since": iso(h1), "gaps": [iso(h1)],
                   "precipPending": [iso(h1), iso(h2)]})
        old["backfill"]["rtma"].update({"cursor": iso(h1), "oldest": iso(h1), "done": True})
        old["precipSwept"] = old["pruned"] = iso(h2)
        self.st.put(analysis.STATE_KEY, json.dumps(old).encode(), "application/json")
        ps = analysis.load_state(self.st)["products"]["rtma"]
        self.assertNotIn("gaps", ps)
        self.assertNotIn("precipPending", ps)
        self.assertEqual((ps["open"], ps["rechecked"]), ({iso(h1): {"anl": {}, "pcp": {}}, iso(h2): {"pcp": {}}}, None))
        # 18Z's precipitation was stored short of a place before 2026-11-03: it
        # stands, and the file is not fetched again
        self.st.put(analysis.precip_key("rtma", h2),
                    analysis._gz({"schema": analysis.SCHEMA, "values": {"new-york-ny": 0.0, "chicago-il": 0.0,
                                                                         "los-angeles-ca": None},
                                  "read": True, "complete": False}), "application/gzip")
        k = len(self.fx.fetched)
        self.assertEqual(self.run_pass(), 0)
        self.assertFalse([u for u in self.fx.fetched[k:] if "2026092618" in u])
        self.assertNotIn(iso(h2), self.state()["products"]["rtma"]["open"])
        self.assertIn(iso(h1), self.state()["products"]["rtma"]["open"])

    def test_the_backfill_walks_back_from_the_first_live_hour(self):
        t = hour(2026, 9, 26, 19)
        for back in range(0, 4):
            self.fx.add_hour("rtma", t - dt.timedelta(hours=back))
        self.assertEqual(self.run_pass(), 0)
        for back in range(1, 4):
            self.assertIsNotNone(self.read(analysis.hour_key("rtma", t - dt.timedelta(hours=back))), back)
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
        self.assertIn("anl", state["products"]["rtma"]["open"][iso(t)])
        # the walk starts behind the late hour and reads the three hours there;
        # the absent hours past them take NOMADS calls, and once the pass's
        # budget is spent the walk carries on next pass
        self.assertEqual(archive.LAST_STATUS["backfilled"], 3)
        self.assertLessEqual(state["backfill"]["rtma"]["cursor"], iso(t - dt.timedelta(hours=3)))
        for back in range(1, 4):
            self.assertIsNotNone(self.read(analysis.hour_key("rtma", t - dt.timedelta(hours=back))), back)
        # the late hour lands: the live lane reads it from the open list
        self.fx.add_hour("rtma", t)
        self.assertEqual(self.run_pass(NOW + dt.timedelta(minutes=10)), 0)
        state = self.state()
        self.assertEqual(state["products"]["rtma"]["newest"], iso(t))
        self.assertNotIn(iso(t), state["products"]["rtma"]["open"])
        self.assertIsNotNone(self.read(analysis.hour_key("rtma", t)))

    def test_a_catch_up_span_is_queued_once_and_clamped_to_the_backfill(self):
        state = analysis.new_state()
        state["products"]["rtma"]["newest"] = iso(NOW - dt.timedelta(days=3))
        for _ in range(3):
            analysis.walk_candidates("rtma", state, NOW)
        queue = state["backfill"]["rtma"]["queue"]
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0][1], iso(analysis.expected_latest("rtma", NOW) - dt.timedelta(hours=analysis.LIVE_HOURS_PER_PASS)))
        self.assertEqual(state["products"]["rtma"]["scanned"], queue[0][1])
        state = analysis.new_state()
        state["products"]["rtma"]["newest"] = iso(NOW - dt.timedelta(days=40))
        analysis.walk_candidates("rtma", state, NOW)
        oldest = analysis._floor_hour(NOW) - dt.timedelta(days=analysis.BACKFILL_POINT_DAYS)
        self.assertEqual(state["backfill"]["rtma"]["queue"][0][0], iso(oldest))
        state["backfill"]["rtma"]["queue"] = [[iso(NOW - dt.timedelta(days=40)), iso(NOW - dt.timedelta(days=35))]]
        self.assertIsNone(analysis.backfill_next("rtma", state, NOW))
        self.assertEqual(state["backfill"]["rtma"]["queue"], [])

    def test_a_day_whose_other_files_are_not_published_resolves_on_the_hours_available(self):
        # owner's decision 2026-09-29: a missing file does not hold a day open.
        # One hour lands; the first ten-minute pass after the last cut-off
        # walks the rest of the day, finds every other file on neither NOAA
        # Open Data nor NOMADS, settles each on no copy, and the day resolves on
        # the one hour it has, never closed
        t = hour(2026, 9, 26, 19)
        self.fx.add_hour("rtma", t)
        self.assertEqual(self.run_pass(), 0)
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"]["rtma"]
        self.assertEqual((ny["final"], ny["closed"]), (False, False))
        later = analysis.day_end("2026-09-26", "America/Los_Angeles") + dt.timedelta(minutes=48)
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
        # Los Angeles has no RTMA hours yet: it counts toward the places and nothing else
        self.assertEqual(analysis.day_status(doc, LOCS),
                         {"places": 3, "final": 2, "resolved": 1, "closed": 0, "review": 0})
        doc["locations"]["los-angeles-ca"] = closed
        self.assertEqual(analysis.day_status(doc, LOCS),
                         {"places": 3, "final": 2, "resolved": 1, "closed": 1, "review": 0})
        doc["locations"]["los-angeles-ca"] = {"rtma": analysis.review_entry("2026-09-26", "America/Los_Angeles")}
        self.assertEqual(analysis.day_status(doc, LOCS),
                         {"places": 3, "final": 2, "resolved": 1, "closed": 0, "review": 1})
        self.assertEqual(analysis.day_status({}, LOCS),
                         {"places": 3, "final": 0, "resolved": 0, "closed": 0, "review": 0})

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
        self.assertEqual(idx["dayStatus"]["2026-09-25"],
                         {"places": 3, "final": 3, "resolved": 2, "closed": 0, "review": 0})
        self.assertEqual(idx["dayStatus"]["2026-09-26"],
                         {"places": 3, "final": 1, "resolved": 1, "closed": 0, "review": 0})
        self.assertEqual(sorted(idx["dayStatus"]), days)
        # no day resolved at all: the page falls back to the newest day
        self.assertIsNone(analysis.last_resolved_day(analysis.new_state(), days))

    def test_a_pass_keeps_the_status_of_the_days_it_rebuilds(self):
        self.fx.add_hour("rtma", hour(2026, 9, 26, 19))
        self.assertEqual(self.run_pass(), 0)
        st = self.state()["dayStatus"]
        idx = self.read(analysis.INDEX_KEY)
        self.assertEqual(idx["dayStatus"], {d: st[d] for d in idx["days"]})
        self.assertEqual(idx["dayStatus"]["2026-09-26"]["places"], 3)
        self.assertEqual(idx["dayStatus"]["2026-09-26"]["resolved"], 0)    # one hour is not a day
        self.assertIsNone(idx["lastResolvedDay"])

    def test_the_lane_alarms_only_when_a_product_is_overdue_or_unreadable(self):
        state = analysis.new_state()
        status = {"read": [], "errors": [], "backfilled": 0, "backfilledBy": {"rtma": 0}}
        h = analysis.health_results(state, status, NOW)
        self.assertEqual(list(h), ["rtma"])
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
                  "backfilled": 0, "backfilledBy": {"rtma": 0}}
        h = analysis.health_results(state, status, NOW)
        self.assertTrue(h["rtma"]["attempted"] and not h["rtma"]["ok"])

    def test_a_message_valid_at_another_hour_is_refused(self):
        # NOAA names the objects by hour; a misfiled one would otherwise be
        # read as the hour asked for
        f = Fakes()
        f.install(self)
        orig = analysis.grib2.product_def
        analysis.grib2.product_def = lambda m: dict(orig(m), end=(2026, 9, 26, 12, 0, 0))
        msg = Fakes.msg("rtma", "APCP", hour(2026, 9, 26, 12), "g184")
        self.assertIsNone(analysis.check_message(msg, "precip", G184, hour(2026, 9, 26, 12)))
        why = analysis.check_message(msg, "precip", G184, hour(2026, 9, 26, 13))
        self.assertIn("valid", why or "")

    # ---- the settlement display's figures (2026-11-03): every row of a place
    #      file carries its values as the contracts show them and the day's
    #      values so far, each made by the code that resolves the day
    def archive(self, hours, read_now, gone=()):
        """Read and archive each hour through the lane's own read_hour and
        write_hour, as the passes do; an hour in `gone` has its precipitation
        stored without an analysis."""
        cache, gi = {}, {}
        for t in hours:
            res = analysis.read_hour("rtma", t, LOCS, {"wexp": [], "g184": []}, frames=False, now=read_now)
            if res.status == "ok":
                analysis.write_hour(self.st, "rtma", t, res, gi, cache, read_now)
            else:
                self.assertIn(t, gone)
                analysis.write_precip_only(self.st, "rtma", t, res, gi, cache, read_now)
        return cache

    def check_store(self, pairs):
        """check_running over the place files of the (place, day) pairs, with
        each day's raw CSV as the fresh source. Returns the rows checked."""
        n = 0
        for lid, day in sorted(pairs):
            doc = self.read(analysis.LOC_KEY.format(loc=lid, day=day))
            raw = self.st.get(analysis.CSV_RAW_KEY.format(day=day)).decode()
            n += check_running(self, doc, raw_rows_of(raw, lid))
        return n

    def test_the_half_ties_reach_the_place_file_as_the_contracts_show_them(self):
        # New York on 2026-01-10 (EST): 280.65 K at 00:00 local is 45.5 F and
        # shows 46, 250.65 K at 01:00 is -8.5 F and shows -9, and the 15.875 mm
        # of the hour from 00:00 is exactly 0.625 in and shows 0.63
        h0, h1 = hour(2026, 1, 10, 5), hour(2026, 1, 10, 6)
        for t, k in ((h0, 280.65), (h1, 250.65)):
            self.fx.add_hour("rtma", t)
            self.fx.values[("rtma", "temp", iso(t))] = lambda c, k=k: k if c == NY_K else 280.0
        self.fx.values[("rtma", "precip", iso(h1))] = lambda c: 15.875 if c == NY_K184 else 0.0
        now = hour(2026, 1, 11, 6)
        self.archive([h0, h1], now)
        analysis.rebuild_days(self.st, {("new-york-ny", "2026-01-10")}, LOCS, {}, now, analysis.new_state())
        rows = {r["t"]: r["rtma"] for r in self.read(analysis.LOC_KEY.format(loc="new-york-ny", day="2026-01-10"))["hours"]}
        self.assertEqual((rows[iso(h0)]["temp"], rows[iso(h0)]["show"]["temp"]), (45.5, 46))
        self.assertEqual((rows[iso(h1)]["temp"], rows[iso(h1)]["show"]["temp"]), (-8.5, -9))
        self.assertEqual((rows[iso(h0)]["precip"], rows[iso(h0)]["show"]["precip"]), (0.625, 0.63))
        self.assertEqual(rows[iso(h0)]["run"]["high"], {"value": 46, "exact": 45.5, "at": iso(h0)})
        self.assertEqual(rows[iso(h1)]["run"]["low"], {"value": -9, "exact": -8.5, "at": iso(h1)})
        self.assertEqual(rows[iso(h0)]["run"]["precip"], {"value": 0.63, "exact": 0.625, "hours": 1})
        ny = self.read(analysis.DAY_KEY.format(day="2026-01-10"))["locations"]["new-york-ny"]["rtma"]
        self.assertEqual((ny["high"]["value"], ny["low"]["value"], ny["precip"]["value"]), (46, -9, 0.63))

    def test_every_hour_of_every_place_day_built_from_fixtures_runs_on_the_days_resolution(self):
        # Three places over 2026-09-26 and into the next day, read through the
        # lane: an analysis on neither server whose precipitation landed, an
        # analysis published after New York's and Chicago's cut-off but before
        # Los Angeles's, an accumulation published after New York's cut-off
        # only, the two temperature halves and a split 0.625 in at New York;
        # then the fall-back day of 2026-11-01, 25 hours at New York. Every
        # row of every place file must carry what a fresh resolution of the
        # hours up to it gives, and the last counted hour the day's summary.
        CHI = next(l for l in LOCS if l["id"] == "chicago-il")
        chi184 = CHI["cell"]["g184"][2]
        gone, late_anl, late_pcp = hour(2026, 9, 26, 16), hour(2026, 9, 26, 20), hour(2026, 9, 26, 23)
        rain = {hour(2026, 9, 26, 10): 0.015625, hour(2026, 9, 26, 11): 2.546875, hour(2026, 9, 26, 12): 13.3125}

        def plant(i, t):
            if t == gone:
                self.fx.add_precip("rtma", t)
            else:
                self.fx.add_hour("rtma", t)
            self.fx.published[analysis.anl_url("rtma", t)] = iso(t + dt.timedelta(minutes=48))
            self.fx.published[analysis.pcp_url("rtma", t)] = iso(t + dt.timedelta(minutes=17))
            ties = {hour(2026, 9, 26, 12): 280.65, hour(2026, 9, 26, 13): 250.65}
            self.fx.values[("rtma", "temp", iso(t))] = (
                lambda c, i=i, t=t: ties[t] if (c == NY_K and t in ties) else 270.0 + ((i * 53 + c % 97) % 4000) / 100.0)
            self.fx.values[("rtma", "wind", iso(t))] = lambda c, i=i: ((i * 7 + c) % 900) / 100.0
            self.fx.values[("rtma", "gust", iso(t))] = lambda c, i=i: ((i * 7 + c) % 900) / 100.0 + ((i * 3 + c) % 500) / 100.0
            self.fx.values[("rtma", "precip", iso(t))] = (
                lambda c, i=i, t=t: rain.get(t, 0.0) if c == NY_K184 else ((i % 5) * 0.015625 if c == chi184 else 0.0))

        hours = [hour(2026, 9, 26, 4) + dt.timedelta(hours=n) for n in range(28)]
        for i, t in enumerate(hours):
            plant(i, t)
        self.fx.published[analysis.anl_url("rtma", late_anl)] = "2026-09-27T06:00:00Z"
        self.fx.published[analysis.pcp_url("rtma", late_pcp)] = "2026-09-27T05:30:00Z"
        now = hour(2026, 9, 27, 9)
        self.archive(hours, now, gone=(gone,))
        pairs = {(l["id"], d) for l in LOCS for d in ("2026-09-26", "2026-09-27")}
        analysis.rebuild_days(self.st, pairs, LOCS, {}, now, analysis.new_state())
        self.assertGreater(self.check_store(pairs), 3 * 24)
        rows = {r["t"]: r["rtma"] for r in self.read(analysis.LOC_KEY.format(loc="new-york-ny", day="2026-09-26"))["hours"]}
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"]["rtma"]
        self.assertEqual((ny["final"], ny["hours"], ny["precip"]["value"], ny["precip"]["exact"]), (True, 22, 0.63, 0.625))
        self.assertEqual((ny["low"]["value"], ny["low"]["exact"]), (-9, -8.5))
        # the hour whose analysis never landed adds its rain and no analysis
        before, at = rows[iso(gone - dt.timedelta(hours=1))]["run"], rows[iso(gone)]["run"]
        self.assertEqual((rows[iso(gone)]["temp"], rows[iso(gone)]["show"]["temp"]), (None, None))
        self.assertEqual((at["hours"], at["high"], at["precip"]["hours"]), (before["hours"], before["high"], before["precip"]["hours"] + 1))
        self.assertTrue(rows[iso(late_anl)]["late"])
        self.assertEqual(rows[iso(late_anl)]["run"]["hours"], rows[iso(late_anl - dt.timedelta(hours=1))]["run"]["hours"])
        self.assertTrue(rows[iso(late_pcp - dt.timedelta(hours=1))]["pcpLate"])
        self.assertEqual(rows[iso(hour(2026, 9, 26, 12))]["show"]["temp"], 46)
        self.assertEqual(rows[iso(hour(2026, 9, 26, 12))]["pub"], "2026-09-26T12:48:00Z")
        self.assertEqual(rows[iso(hour(2026, 9, 26, 11))]["pcpPub"], "2026-09-26T12:17:00Z")
        la = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["los-angeles-ca"]["rtma"]
        chi = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["chicago-il"]["rtma"]
        self.assertEqual((la["hours"], chi["hours"], chi["precip"]["hours"], ny["precip"]["hours"]), (23, 22, 24, 23))
        # the fall-back day: 25 rows at New York, the second 01:00 labelled 01*
        fall = [hour(2026, 11, 1, 4) + dt.timedelta(hours=n) for n in range(26)]
        for i, t in enumerate(fall):
            plant(i, t)
        now = hour(2026, 11, 2, 10)
        self.archive(fall, now)
        pairs = {(l["id"], "2026-11-01") for l in LOCS}
        analysis.rebuild_days(self.st, pairs, LOCS, {}, now, analysis.new_state())
        self.assertGreater(self.check_store(pairs), 25)
        doc = self.read(analysis.LOC_KEY.format(loc="new-york-ny", day="2026-11-01"))
        self.assertEqual((len(doc["hours"]), doc["hours"][2]["local"], doc["hours"][-1]["rtma"]["run"]["hours"]),
                         (25, "01*", 25))

    def test_older_place_files_without_the_running_values_still_load_and_are_rewritten_a_few_a_pass(self):
        # place files written before 2026-11-03 carry no rowShape, show, run
        # or publication times. They stay readable where they are, the lane
        # reads nothing from them, and the LAYOUT change rewrites the listed
        # days newest first, REFRESH_DAYS_PER_PASS a pass, inside the pass's
        # deadline like any other refresh
        keep = analysis.REFRESH_DAYS_PER_PASS
        analysis.REFRESH_DAYS_PER_PASS = 1
        self.addCleanup(lambda: setattr(analysis, "REFRESH_DAYS_PER_PASS", keep))
        days = ("2026-09-24", "2026-09-25", "2026-09-26")
        hours = [hour(2026, 9, 24, 4) + dt.timedelta(hours=n) for n in range(3 * 24 + 4)]
        for t in hours:
            self.fx.add_hour("rtma", t)
        now = hour(2026, 9, 27, 9)
        self.archive(hours, now)
        state = analysis.new_state()
        pairs = {(l["id"], d) for l in LOCS for d in days}
        analysis.rebuild_days(self.st, pairs, LOCS, {}, now, state)
        # strip what this layout added, as the files of the layout before it were
        for lid, d in pairs:
            key = analysis.LOC_KEY.format(loc=lid, day=d)
            doc = self.read(key)
            doc.pop("rowShape")
            for r in doc["hours"]:
                for k in ("show", "run", "pub", "pcpPub", "src", "pcpSrc", "pubSrc", "pcpPubSrc") + ROW_SHAPE_3:
                    (r["rtma"] or {}).pop(k, None)
            self.st.put(key, analysis._dump(doc), "application/json")
        old = {d: self.read(analysis.LOC_KEY.format(loc="new-york-ny", day=d)) for d in days}
        for d in days:
            self.assertNotIn("rowShape", old[d])
            self.assertEqual(sorted(old[d]["hours"][0]["rtma"]), ["gust", "precip", "temp", "wind"])
        state["layout"] = "cutoff-2026-10-14"
        status = {"errors": [], "failed": 0}
        for n, left in ((1, 2), (2, 1), (3, 0)):
            analysis.refresh_days(self.st, state, LOCS, {}, now, archive.Deadline(600), status)
            self.assertEqual(status["refreshed"], {"days": 1, "left": left})
            shaped = [d for d in days if self.read(analysis.LOC_KEY.format(loc="new-york-ny", day=d)).get("rowShape")]
            self.assertEqual(shaped, list(days[-n:]))          # newest first
            # a day not yet rewritten is the old file, unchanged and still read whole
            for d in days[:-n]:
                self.assertEqual(self.read(analysis.LOC_KEY.format(loc="new-york-ny", day=d)), old[d])
        self.assertNotIn("relayout", state)
        self.assertEqual(state["layout"], analysis.LAYOUT)
        # rewritten, each carries the running values the display steps through
        self.check_store(pairs)
        for d in days:
            doc = self.read(analysis.LOC_KEY.format(loc="new-york-ny", day=d))
            self.assertEqual([values_of(r["rtma"]) for r in doc["hours"]], [values_of(r["rtma"]) for r in old[d]["hours"]])
        # a refresh that runs out of time leaves the rest for the next pass
        state["layout"] = "cutoff-2026-10-14"
        analysis.refresh_days(self.st, state, LOCS, {}, now, archive.Deadline(1), status)
        self.assertEqual(status["refreshed"]["days"], 0)
        self.assertEqual(sorted(state["relayout"]), list(days))

    # ---- rowShape 3 and the hourly contract (owner's approval of display v3
    #      and Patrick's ruling of 4 November): each hour's temperature is the
    #      hourly contract's value, with its own cut three hours after the hour
    def row_of(self, lid, day, t):
        """One place file's rtma cell for the hour t."""
        return next(r for r in self.read(analysis.LOC_KEY.format(loc=lid, day=day))["hours"] if r["t"] == iso(t))["rtma"]

    def test_the_hourly_contract_is_final_at_nomadss_last_modified_once_its_copy_is_in(self):
        # final as soon as NOMADS's copy is in, published by the hourly cut,
        # stamped with that copy's Last-Modified and not when the lane read it,
        # and before the hourly cut has settled
        t = hour(2026, 9, 26, 19)
        self.caught_up(t - dt.timedelta(hours=1))
        self.fx.published[analysis.anl_url("rtma", t, nomads=True)] = "2026-09-26T19:46:10Z"
        self.fx.add_hour("rtma", t, servers=("nomads",))
        self.fx.values[("rtma", "temp", iso(t))] = lambda c: 280.65 if c == NY_K else 293.15
        self.assertEqual(self.run_pass(), 0)
        want = {"cut": "2026-09-26T22:00:00Z", "state": "final", "at": "2026-09-26T19:46:10Z", "basis": "nomads",
                "src": "nomads", "value": 46, "x": "45.500", "late": False}
        self.assertEqual(self.row_of("new-york-ny", "2026-09-26", t)["hourly"], want)
        self.assertEqual(self.row_of("chicago-il", "2026-09-26", t)["hourly"], dict(want, value=68, x="68.000"))
        self.assertNotIn("hourly", self.state()["products"]["rtma"]["open"][iso(t)]["anl"])
        # NOAA Open Data's copy comes ten minutes on, every cut settles at
        # once, and the hourly contract stays as it was
        self.fx.add_hour("rtma", t, servers=("nodd",))
        at = NOW + dt.timedelta(minutes=10)
        self.assertEqual(self.run_pass(at), 0)
        arc = self.read(analysis.hour_key("rtma", t))
        self.assertEqual(arc["hourly"], {"at": iso(at), "with": ["nodd", "nomads"], "src": "nomads",
                                         "published": "2026-09-26T19:46:10Z", "pubSrc": "nomads"})
        cell = self.row_of("new-york-ny", "2026-09-26", t)
        self.assertEqual(cell["hourly"], want)
        self.assertEqual(cell["anlFile"], {"name": "rtma2p5.20260926/rtma2p5.t19z.2dvaranl_ndfd.grb2_wexp",
                                           "nodd": {"lm": "2026-09-26T19:48:00Z", "seen": iso(at)},
                                           "nomads": {"lm": "2026-09-26T19:46:10Z", "seen": iso(NOW)},
                                           "src": "nomads", "counts": True, "afterCut": False})

    def test_the_hourly_contract_on_noaa_open_data_alone_is_final_at_its_cut_from_the_first_ten_minute_pass(self):
        t = hour(2026, 9, 26, 19)
        cut = hour(2026, 9, 26, 22)
        self.caught_up(t - dt.timedelta(hours=1))
        self.fx.add_hour("rtma", t, servers=("nodd",))
        self.assertEqual(self.run_pass(), 0)
        h = self.row_of("new-york-ny", "2026-09-26", t)["hourly"]
        self.assertEqual((h["state"], h["at"], h["basis"], h["src"], h["value"], h["x"]),
                         ("provisional", None, None, "nodd", 68, "68.000"))
        # the first ten-minute pass after the cut settles it on the copy in
        # hand, and it is final at the cut itself, as a day's finalAt is its cut-off
        froze = cut + dt.timedelta(minutes=8)
        self.assertEqual(self.run_pass(froze), 0)
        cell = self.row_of("new-york-ny", "2026-09-26", t)
        self.assertEqual(cell["hourly"], {"cut": iso(cut), "state": "final", "at": iso(cut), "basis": "cutoff",
                                          "src": "nodd", "value": 68, "x": "68.000", "late": False})
        self.assertEqual(self.state()["products"]["rtma"]["open"][iso(t)]["anl"]["hourly"],
                         {"at": iso(froze), "with": ["nodd"], "src": "nodd", "published": iso(t + dt.timedelta(minutes=48)),
                          "pubSrc": "nodd"})
        # the day's own rules are untouched: the row still waits for its cut-off
        self.assertNotIn("settled", cell)
        self.assertFalse(self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"]["rtma"]["final"])

    def test_the_hourly_contract_with_neither_copy_by_its_cut_is_under_review(self):
        # 19Z on neither server, nor its precipitation. The row has no value
        # for the day, and once the hourly cut settles it carries the review
        # and its files and nothing else
        t = hour(2026, 9, 26, 19)
        cut = hour(2026, 9, 26, 22)
        self.caught_up(t - dt.timedelta(hours=2))
        self.fx.add_hour("rtma", t - dt.timedelta(hours=1))             # 18Z, so New York's day has a value
        self.assertEqual(self.run_pass(t + dt.timedelta(minutes=55)), 0)
        self.assertIsNone(self.row_of("new-york-ny", "2026-09-26", t))     # nothing known yet
        froze = cut + dt.timedelta(minutes=8)
        self.assertEqual(self.run_pass(froze), 0)
        cell = self.row_of("new-york-ny", "2026-09-26", t)
        self.assertEqual(sorted(cell), HOURLY_ONLY)
        self.assertEqual(cell["hourly"], {"cut": iso(cut), "state": "review", "at": iso(cut), "basis": "cutoff",
                                          "src": None, "value": None, "x": None, "late": False})
        self.assertEqual(cell["anlFile"], {"name": "rtma2p5.20260926/rtma2p5.t19z.2dvaranl_ndfd.grb2_wexp",
                                           "nodd": None, "nomads": None, "src": None, "counts": False, "afterCut": False})
        self.assertEqual((cell["pcpFile"]["name"], cell["pcpFile"]["nodd"]),
                         ("rtma2p5.20260926/rtma2p5.2026092620.pcp.184.grb2", None))
        self.assertEqual(self.state()["products"]["rtma"]["open"][iso(t)]["anl"]["hourly"],
                         {"at": iso(froze), "with": [], "src": None, "published": None, "pubSrc": None})
        self.assertEqual(self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"]["rtma"]["hours"], 1)
        # the next hour, not yet past its cut, stays a row with nothing
        self.assertIsNone(self.row_of("new-york-ny", "2026-09-26", t + dt.timedelta(hours=1)))

    def test_a_nomads_copy_read_after_the_hourly_cut_settled_is_flagged_and_changes_nothing(self):
        # NOMADS's copy, other bytes, published before the hourly cut but read
        # only after it settled on NOAA Open Data's, is recorded, flagged late
        # and never used for the hourly contract. The day, still before its
        # cut-offs, takes it by its own rules, so the two can differ
        t = hour(2026, 9, 26, 19)
        cut = hour(2026, 9, 26, 22)
        self.caught_up(t - dt.timedelta(hours=1))
        self.fx.add_hour("rtma", t, servers=("nodd",))
        self.assertEqual(self.run_pass(), 0)
        self.assertEqual(self.run_pass(cut + dt.timedelta(minutes=8)), 0)
        before = self.row_of("new-york-ny", "2026-09-26", t)["hourly"]
        self.assertEqual((before["state"], before["basis"], before["src"], before["late"]), ("final", "cutoff", "nodd", False))
        self.fx.published[analysis.anl_url("rtma", t, nomads=True)] = "2026-09-26T21:30:00Z"
        self.fx.add_hour("rtma", t, servers=("nomads",), precip=False, variant="b")
        self.fx.values[("rtma", "temp", iso(t), "b")] = 300.0
        seen = cut + dt.timedelta(minutes=68)
        self.assertEqual(self.run_pass(seen), 0)
        cell = self.row_of("new-york-ny", "2026-09-26", t)
        self.assertEqual(cell["hourly"], dict(before, late=True))
        self.assertEqual(self.read(analysis.hour_key("rtma", t))["hourly"]["with"], ["nodd"])
        self.assertEqual(cell["anlFile"]["nomads"], {"lm": "2026-09-26T21:30:00Z", "seen": iso(seen)})
        self.assertEqual((cell["src"], cell["show"]["temp"], cell["hourly"]["value"]), ("nomads", 80, 68))

    def test_a_file_between_the_days_cut_off_and_the_hourly_cut_counts_for_the_hourly_contract_and_not_the_day(self):
        # New York's 23:00 (03Z) has its day's cut-off at 04:45Z and the
        # hourly cut at 06:00Z. The analysis lands on both servers at 05:10,
        # after New York's rows settled on no copy and before Chicago's
        # cut-off, so the hourly contract counts it at every place, Chicago's
        # day counts it, and New York's day does not, its row carrying the
        # hourly contract alone
        t = hour(2026, 9, 27, 3)
        self.caught_up(t - dt.timedelta(hours=2))
        self.fx.add_hour("rtma", t - dt.timedelta(hours=1))             # New York's 22:00, so its day has a value
        self.assertEqual(self.run_pass(t + dt.timedelta(minutes=55)), 0)
        cut = analysis.day_cutoff("2026-09-26", "America/New_York")
        self.assertEqual(self.run_pass(cut + dt.timedelta(minutes=3)), 0)
        day_key = analysis.DAY_KEY.format(day="2026-09-26")
        ny_day = self.read(day_key)["locations"]["new-york-ny"]
        self.assertTrue(ny_day["rtma"]["final"])
        for nomads in (False, True):
            self.fx.published[analysis.anl_url("rtma", t, nomads=nomads)] = "2026-09-27T05:10:00Z"
        self.fx.add_hour("rtma", t, precip=False)
        self.fx.values[("rtma", "temp", iso(t))] = 280.65
        read_at = hour(2026, 9, 27, 5) + dt.timedelta(minutes=18)
        self.assertEqual(self.run_pass(read_at), 0)
        hourly = {"cut": "2026-09-27T06:00:00Z", "state": "final", "at": "2026-09-27T05:10:00Z", "basis": "nomads",
                  "src": "nomads", "value": 46, "x": "45.500", "late": False}
        ny = self.row_of("new-york-ny", "2026-09-26", t)
        self.assertEqual(sorted(ny), HOURLY_ONLY)
        self.assertEqual(ny["hourly"], hourly)
        self.assertEqual((ny["anlFile"]["src"], ny["anlFile"]["counts"], ny["anlFile"]["afterCut"]), (None, False, True))
        self.assertEqual(self.read(day_key)["locations"]["new-york-ny"], ny_day)
        chi = self.row_of("chicago-il", "2026-09-26", t)
        self.assertEqual((chi["temp"], chi["src"], chi["hourly"]), (45.5, "nomads", hourly))
        self.assertEqual((chi["anlFile"]["counts"], chi["anlFile"]["afterCut"]), (True, False))

    def test_a_file_after_the_hourly_cut_and_before_the_days_cut_off_counts_for_the_day_and_not_the_hourly_contract(self):
        # New York's 06:00 (10Z) has its hourly cut at 13Z and its day's
        # cut-off at 04:45Z the next day. The analysis lands at 14:00 on both
        # servers, after the hourly cut settled on no copy, so the day counts
        # it and the hourly contract stays under review, the copies flagged late
        t = hour(2026, 9, 26, 10)
        hc = hour(2026, 9, 26, 13)
        self.caught_up(t - dt.timedelta(hours=1))
        self.assertEqual(self.run_pass(t + dt.timedelta(minutes=55)), 0)
        self.assertEqual(self.run_pass(hc + dt.timedelta(minutes=8)), 0)
        for nomads in (False, True):
            self.fx.published[analysis.anl_url("rtma", t, nomads=nomads)] = "2026-09-26T14:00:00Z"
        self.fx.add_hour("rtma", t, precip=False)
        self.assertEqual(self.run_pass(hour(2026, 9, 26, 14) + dt.timedelta(minutes=8)), 0)
        cell = self.row_of("new-york-ny", "2026-09-26", t)
        self.assertEqual((cell["temp"], cell["show"]["temp"], cell.get("late")), (68.0, 68, None))
        self.assertEqual((cell["anlFile"]["counts"], cell["anlFile"]["afterCut"]), (True, False))
        self.assertEqual(cell["hourly"], {"cut": iso(hc), "state": "review", "at": iso(hc), "basis": "cutoff", "src": None,
                                          "value": None, "x": None, "late": True})
        self.assertEqual(self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"]["rtma"]["hours"], 1)

    def test_the_hourly_cut_settles_apart_from_the_days_and_the_record_waits_for_it(self):
        # Los Angeles alone, at its 23:00 (06Z), has the day's cut-off at
        # 07:45Z and the hourly cut at 09:00Z, so the record waits past the day's
        t = hour(2026, 9, 27, 6)
        cut, hc = "2026-09-27T07:45:00Z", "2026-09-27T09:00:00Z"
        values = {LA["id"]: {"temp": 290.0, "wind": 5.0, "gust": 8.0}}
        analysis.write_copy(self.st, "rtma", "anl", "nodd", t,
                            {"server": "nodd", "seen": "2026-09-27T06:50:00Z", "published": "2026-09-27T06:48:00Z",
                             "sha256": {"temp": "a"}, "values": values}, {})
        slots: dict = {}
        p1 = analysis._parse_iso(cut) + dt.timedelta(minutes=3)
        self.assertTrue(analysis.settle_cuts(self.st, "rtma", "anl", t, slots, [LA], p1, {}, True))
        self.assertEqual(list(slots), ["cutoffs"])
        self.assertIsNone(self.read(analysis.hour_key("rtma", t)))
        entry, state = {"anl": slots}, analysis.new_state()
        analysis.settle_file(self.st, "rtma", "anl", t, entry, [LA], p1, {}, state, True)
        self.assertIn("anl", entry)
        # a windowed pass after the hourly cut settles nothing on one copy
        self.assertFalse(analysis.settle_cuts(self.st, "rtma", "anl", t, slots, [LA],
                                              analysis._parse_iso(hc) + dt.timedelta(minutes=14), {}, False))
        p3 = analysis._parse_iso(hc) + dt.timedelta(minutes=18)
        self.assertTrue(analysis.settle_cuts(self.st, "rtma", "anl", t, slots, [LA], p3, {}, True))
        self.assertEqual(slots["hourly"], {"at": iso(p3), "with": ["nodd"], "src": "nodd",
                                           "published": "2026-09-27T06:48:00Z", "pubSrc": "nodd"})
        arc = self.read(analysis.hour_key("rtma", t))
        self.assertEqual((arc["hourly"], arc["cutoffs"], arc["settled"]), (slots["hourly"], slots["cutoffs"], iso(p3)))
        # it stays open for NOMADS's copy until a day after its hour
        analysis.settle_file(self.st, "rtma", "anl", t, entry, [LA], p3, {}, state, True)
        self.assertIn("anl", entry)
        later = t + dt.timedelta(hours=analysis.RECHECK_AFTER_HOURS)
        analysis.settle_file(self.st, "rtma", "anl", t, entry, [LA], later, {}, state, True)
        self.assertNotIn("anl", entry)
        # with the three zones the later cut-offs are past the hourly cut, so
        # the record is written when the days' last cut settles, as before
        t2 = hour(2026, 9, 27, 3)
        analysis.write_copy(self.st, "rtma", "anl", "nodd", t2, {"server": "nodd", "seen": "2026-09-27T03:50:00Z",
                                                                 "published": "2026-09-27T03:48:00Z", "sha256": {"temp": "a"},
                                                                 "values": {l["id"]: values[LA["id"]] for l in LOCS}}, {})
        last = max(analysis._cutoffs("anl", t2, LOCS)) + dt.timedelta(minutes=3)
        self.assertGreater(last, analysis.hourly_cut(t2))
        slots2: dict = {}
        self.assertTrue(analysis.settle_cuts(self.st, "rtma", "anl", t2, slots2, LOCS, last, {}, True))
        self.assertEqual(self.read(analysis.hour_key("rtma", t2))["settled"], iso(last))
        # a file carried over from before the copies settles its hourly cut
        # on no copy when the cut had passed by then; a precipitation file has none
        t3 = hour(2026, 9, 25, 19)
        carried = {"migrated": "2026-09-26T06:00:00Z"}
        self.assertTrue(analysis.settle_cuts(self.st, "rtma", "anl", t3, carried, LOCS, NOW, {}, False))
        self.assertEqual(carried["hourly"], {"at": "2026-09-26T06:00:00Z", "with": [], "src": None, "published": None,
                                             "pubSrc": None})
        pslots: dict = {}
        analysis.settle_cuts(self.st, "rtma", "pcp", t, pslots, [LA], p3, {}, True)
        self.assertNotIn("hourly", pslots)

    def put_read_before_the_copies(self, t, temp=293.15, mm=0.254, published=None):
        """Hour t's analysis and the precipitation file that ends an hour on,
        as the lane archived them before it kept each server's copy: read
        from NOAA Open Data, no copies, and no Last-Modified before 2026-10-14."""
        vals = {loc["id"]: {"temp": temp, "wind": 5.0, "gust": 8.0} for loc in LOCS}
        self.st.put(analysis.hour_key("rtma", t), analysis._gz(
            {"schema": analysis.SCHEMA, "product": "rtma", "valid": iso(t), "written": iso(t + dt.timedelta(hours=1)),
             "source": "nodd", "published": published, "values": vals}), "application/gzip")
        end = t + dt.timedelta(hours=1)
        doc = analysis.precip_doc("rtma", end, {loc["id"]: mm for loc in LOCS}, end, read=True)
        doc.update({"source": "nodd", "published": None})
        self.st.put(analysis.precip_key("rtma", end), analysis._gz(doc), "application/gzip")

    def test_files_read_before_the_copies_take_the_tables_times_and_their_hourly_contract_follows_them(self):
        day, tz = "2026-09-26", "America/New_York"
        hs = analysis.local_day_hours(day, tz)
        for h in hs:
            self.put_read_before_the_copies(h, published=iso(h + dt.timedelta(minutes=50)) if h == hs[4] else None)
        m = lambda h, n: iso(h + dt.timedelta(minutes=n))                   # noqa: E731
        table = {"anl": {iso(hs[1]): {"nodd": {"lm": m(hs[1], 48), "seen": None}},
                         iso(hs[2]): {"nodd": {"lm": m(hs[2], 49), "seen": None},
                                      "nomads": {"lm": m(hs[2], 46), "seen": m(hs[2], 47)}},
                         iso(hs[3]): {"nodd": {"lm": m(hs[3], 186), "seen": None}},
                         iso(hs[4]): {"nodd": {"lm": m(hs[4], 40), "seen": None}}},
                 "pcp": {iso(hs[1] + dt.timedelta(hours=1)): {"nodd": {"lm": m(hs[1], 77), "seen": None}}}}
        now = hour(2026, 9, 28, 12)
        day0, docs0 = analysis.build_day(self.st, day, LOCS, {}, now, analysis.new_state(), times={})
        day1, docs1 = analysis.build_day(self.st, day, LOCS, {}, now, analysis.new_state(), times=table)
        rows = {r["t"]: r["rtma"] for r in docs1["new-york-ny"]["hours"]}
        hc = lambda h: iso(h + dt.timedelta(hours=3))                       # noqa: E731
        final = {"state": "final", "basis": "norecord", "src": "nodd", "value": 68, "x": "68.000", "late": False}
        # with no time on record none is shown, and it is final at the cut on the copy read
        r0 = rows[iso(hs[0])]
        self.assertEqual((r0["anlFile"]["nodd"], r0["anlFile"]["nomads"], r0["anlFile"]["src"], r0["anlFile"]["counts"],
                          r0["anlFile"]["afterCut"]), (None, None, "nodd", True, False))
        self.assertEqual(r0["hourly"], dict(final, cut=hc(hs[0]), at=hc(hs[0])))
        # NOAA Open Data's time from the table, shown, and nothing else moves
        r1 = rows[iso(hs[1])]
        self.assertEqual((r1["anlFile"]["nodd"], r1["pcpFile"]["nodd"]),
                         ({"lm": m(hs[1], 48), "seen": None}, {"lm": m(hs[1], 77), "seen": None}))
        self.assertEqual(r1["hourly"], dict(final, cut=hc(hs[1]), at=hc(hs[1])))
        # NOMADS's time by the cut, from the engine's probe, makes it final
        # there, the copy read still the one used
        r2 = rows[iso(hs[2])]
        self.assertEqual(r2["anlFile"]["nomads"], {"lm": m(hs[2], 46), "seen": m(hs[2], 47)})
        self.assertEqual(r2["hourly"], dict(final, cut=hc(hs[2]), at=m(hs[2], 46), basis="nomads"))
        # NOAA Open Data's time after the hourly cut puts it under review,
        # while the day, which never reads the table, still counts the hour
        r3 = rows[iso(hs[3])]
        self.assertEqual(r3["hourly"], {"cut": hc(hs[3]), "state": "review", "at": hc(hs[3]), "basis": "norecord",
                                        "src": None, "value": None, "x": None, "late": False})
        self.assertEqual((r3["anlFile"]["counts"], r3["show"]["temp"]), (True, 68))
        # the record's own Last-Modified stands over the table's
        self.assertEqual(rows[iso(hs[4])]["anlFile"]["nodd"], {"lm": m(hs[4], 50), "seen": None})
        # before its cut an hour is provisional on the copy read
        _, docs2 = analysis.build_day(self.st, day, LOCS, {}, hs[-1] + dt.timedelta(minutes=30), analysis.new_state(),
                                      times=table)
        h = docs2["new-york-ny"]["hours"][-1]["rtma"]["hourly"]
        self.assertEqual((h["state"], h["at"], h["basis"], h["src"], h["value"]), ("provisional", None, None, "nodd", 68))
        # and the day is the same with the table and without it
        self.assertEqual(day1, day0)
        self.assertEqual([values_of(r["rtma"]) for r in docs1["new-york-ny"]["hours"]],
                         [values_of(r["rtma"]) for r in docs0["new-york-ny"]["hours"]])

    def test_a_table_time_after_the_cut_off_changes_nothing_but_the_times_shown(self):
        # the precipitation of the hour ending 2026-09-30T07Z reached NOAA Open
        # Data at 08:01:44Z, after the 07:45Z cut-off of Los Angeles's 29
        # September, and the lane counted it under the rule before 14 October.
        # So here it stays counted, and only its time and afterCut say so. An
        # analysis whose time falls between the day's cut-off and its hourly
        # cut moves its times alone too
        day, tz = "2026-09-26", "America/Los_Angeles"
        hs = analysis.local_day_hours(day, tz)
        for h in hs:
            self.put_read_before_the_copies(h)
        last, cut = hs[-1], analysis.day_cutoff(day, tz)
        late_pcp, late_anl = iso(cut + dt.timedelta(minutes=16, seconds=44)), iso(cut + dt.timedelta(minutes=30))
        table = {"pcp": {iso(last + dt.timedelta(hours=1)): {"nodd": {"lm": late_pcp, "seen": None}}},
                 "anl": {iso(last): {"nodd": {"lm": late_anl, "seen": None}}}}
        self.assertLess(late_anl, iso(analysis.hourly_cut(last)))
        now = hour(2026, 9, 28, 12)
        runs = []
        for times in ({}, table):
            raw: list = []
            d, docs = analysis.build_day(self.st, day, [LA], {}, now, analysis.new_state(), raw=raw, times=times)
            runs.append((d, docs["los-angeles-ca"], analysis.raw_csv(raw), analysis.processed_csv(day, d, [LA])))
        (d0, l0, raw0, pro0), (d1, l1, raw1, pro1) = runs
        self.assertEqual((analysis._dump(d1), raw1, pro1), (analysis._dump(d0), raw0, pro0))
        self.assertTrue(d1["locations"]["los-angeles-ca"]["rtma"]["final"])
        r0, r1 = l0["hours"][-1]["rtma"], l1["hours"][-1]["rtma"]
        self.assertEqual(r1["pcpFile"], dict(r0["pcpFile"], nodd={"lm": late_pcp, "seen": None}, afterCut=True))
        self.assertEqual(r1["anlFile"], dict(r0["anlFile"], nodd={"lm": late_anl, "seen": None}, afterCut=True))
        self.assertEqual((r1["pcpFile"]["counts"], r1["anlFile"]["counts"]), (True, True))
        rest = lambda r: {k: v for k, v in r.items() if k not in ("anlFile", "pcpFile")}     # noqa: E731
        self.assertEqual(rest(r1), rest(r0))
        self.assertEqual(r1["hourly"]["basis"], "norecord")
        self.assertEqual(l1["hours"][:-1], l0["hours"][:-1])
        self.assertEqual({k: v for k, v in l1.items() if k != "hours"}, {k: v for k, v in l0.items() if k != "hours"})

    # ---- the windowed passes and the lease (owner's ruling 2026-11-03): every
    #      two minutes about when NOAA publishes each file, an hour's
    #      precipitation read as soon as it lands, and never two passes at once
    def caught_up(self, newest, since=None):
        """A saved state whose live walk has read `newest` and whose backfill is done."""
        state = analysis.new_state()
        state["products"]["rtma"].update({"newest": iso(newest), "scanned": iso(newest), "since": iso(since or newest)})
        back = iso(newest - dt.timedelta(days=31))
        state["backfill"]["rtma"].update({"cursor": back, "oldest": back, "done": True})
        state["precipSwept"] = state["pruned"] = iso(newest)
        analysis.save_state(self.st, state, newest)
        return state

    def test_precipitation_is_read_as_soon_as_it_lands_not_with_the_next_analysis(self):
        t = hour(2026, 9, 26, 19)                    # the file of the hour 18Z to 19Z, on the bucket at 19:17
        self.fx.add_precip("rtma", t)
        self.fx.values[("rtma", "precip", iso(t))] = lambda k: 2.54 if k == NY_K184 else 0.0
        # NOMADS has it first: the file's published time is the first on either server
        self.fx.published[analysis.pcp_url("rtma", t)] = "2026-09-26T19:17:02Z"
        self.fx.published[analysis.pcp_url("rtma", t, nomads=True)] = "2026-09-26T19:16:40Z"
        at = t + dt.timedelta(minutes=18)
        self.assertLess(analysis.expected_latest("rtma", at), t)     # the analysis it came with is not due till 19:47
        self.assertEqual(self.run_window(at), 0)
        self.assertEqual((self.status["pcpReadCount"], self.status["copiesCount"]), (1, 1))
        pre = self.read(analysis.precip_key("rtma", t))
        self.assertEqual((pre["complete"], pre["published"], pre["source"], pre["values"]["new-york-ny"]),
                         (True, "2026-09-26T19:16:40Z", "nomads", 2.54))
        self.assertEqual((pre["servers"]["nodd"]["published"], pre["servers"]["nomads"]["published"]),
                         ("2026-09-26T19:17:02Z", "2026-09-26T19:16:40Z"))
        self.assertIsNotNone(self.read(analysis.FRAME_KEY.format(product="rtma", var="precip", stamp="20260926T19Z")))
        # in the place file and the day at once, in the row of the hour it starts
        row = next(r for r in self.read(analysis.LOC_KEY.format(loc="new-york-ny", day="2026-09-26"))["hours"]
                   if r["t"] == iso(t - dt.timedelta(hours=1)))
        self.assertEqual((row["rtma"]["precip"], row["rtma"]["show"]["precip"], row["rtma"]["pcpPub"], row["rtma"]["pcpSrc"]),
                         (0.1, 0.1, "2026-09-26T19:16:40Z", "nomads"))
        self.assertEqual(row["rtma"]["run"]["precip"], {"value": 0.1, "exact": 0.1, "hours": 1})
        ny = self.read(analysis.DAY_KEY.format(day="2026-09-26"))["locations"]["new-york-ny"]["rtma"]
        self.assertEqual(ny["precip"], {"value": 0.1, "exact": 0.1, "hours": 1, "resolved": False, "resolvedAt": None})
        # the analysis lands at 19:49 and is read on its own, without the
        # precipitation file being fetched again, and nothing waits on it
        self.fx.add_hour("rtma", t, precip=False)
        k = len(self.fx.fetched)
        self.assertEqual(self.run_window(t + dt.timedelta(minutes=50)), 0)
        self.assertFalse([u for u in self.fx.fetched[k:] if ".pcp." in u])
        self.assertIn(analysis.anl_url("rtma", t, nomads=True) + ".idx", self.fx.fetched[k:])
        ps = self.state()["products"]["rtma"]
        self.assertEqual((ps["newest"], ps["open"]), (iso(t), {}))
        self.assertEqual(self.read(analysis.hour_key("rtma", t))["values"]["new-york-ny"]["temp"], 293.15)

    def test_the_days_last_accumulation_is_in_before_its_cut_off_and_resolves_by_the_same_rules(self):
        # New York's 2026-09-26: its last hour's accumulation, 23:00 to
        # midnight EDT, is NOAA's file for 04Z on the 27th, on the bucket at
        # about 04:17Z, and the cut-off is 04:45Z. The windowed pass at 04:18
        # has it in the day's total before the cut-off, and the ten-minute pass
        # after the cut-off resolves the day on it, by its publication time
        day, tz = "2026-09-26", "America/New_York"
        hs = analysis.local_day_hours(day, tz)
        for h in hs:
            self.fx.add_hour("rtma", h)
            self.fx.published[analysis.pcp_url("rtma", h)] = iso(h + dt.timedelta(minutes=17))
        self.archive(hs, hs[-1] + dt.timedelta(minutes=50))
        last = hs[-1] + dt.timedelta(hours=1)
        self.caught_up(hs[-1], hs[0])
        self.fx.add_precip("rtma", last)
        self.fx.values[("rtma", "precip", iso(last))] = lambda k: 2.54 if k == NY_K184 else 0.0
        self.fx.published[analysis.pcp_url("rtma", last)] = iso(last + dt.timedelta(minutes=17))
        cut = analysis.day_cutoff(day, tz)
        self.assertEqual(cut, last + dt.timedelta(minutes=45))
        self.assertEqual(self.run_window(last + dt.timedelta(minutes=18)), 0)
        ny = self.read(analysis.DAY_KEY.format(day=day))["locations"]["new-york-ny"]["rtma"]
        self.assertEqual((ny["final"], ny["precip"]["hours"], ny["precip"]["value"]), (False, 24, 0.1))
        rows = self.read(analysis.LOC_KEY.format(loc="new-york-ny", day=day))["hours"]
        self.assertEqual((rows[-1]["local"], rows[-1]["rtma"]["precip"], rows[-1]["rtma"]["pcpPub"]),
                         ("23", 0.1, iso(last + dt.timedelta(minutes=17))))
        self.assertEqual(self.run_pass(cut + dt.timedelta(minutes=3)), 0)
        ny = self.read(analysis.DAY_KEY.format(day=day))["locations"]["new-york-ny"]["rtma"]
        self.assertEqual((ny["final"], ny["hours"], ny["precip"]["resolved"], ny["precip"]["hours"], ny["precip"]["value"]),
                         (True, 24, True, 24, 0.1))

    def test_a_windowed_pass_with_nothing_new_writes_nothing_and_exits_at_once(self):
        t = hour(2026, 9, 26, 19)
        self.caught_up(t - dt.timedelta(hours=1))
        self.fx.add_hour("rtma", t)
        self.assertEqual(self.run_pass(), 0)
        # 20:14 opens the stamp whose precipitation is now due, which is new
        self.assertEqual(self.run_window(hour(2026, 9, 26, 20) + dt.timedelta(minutes=14)), 0)
        self.assertIn(iso(hour(2026, 9, 26, 20)), self.state()["products"]["rtma"]["open"])
        health = self.health()
        n, k = len(self.st.puts), len(self.fx.fetched)
        t0 = time.time()
        self.assertEqual(self.run_window(hour(2026, 9, 26, 20) + dt.timedelta(minutes=16)), 0)
        self.assertLess(time.time() - t0, 5)
        self.assertEqual(self.st.puts[n:], [])                 # no state, no index, no day, no health file
        self.assertEqual(self.health(), health)
        self.assertEqual((self.status["mode"], self.status["lease"], self.status["readCount"], self.status["pcpReadCount"]),
                         ("window", "released", 0, 0))
        self.assertEqual((archive.LAST_STATUS["mode"], archive.LAST_STATUS["errors"], archive.LAST_STATUS["alarms"]),
                         ("window", 0, []))
        # it asks both servers for the one file newly due, and nothing else
        got = self.fx.fetched[k:]
        self.assertEqual(sorted(got), sorted(analysis.file_url("rtma", "pcp", s, hour(2026, 9, 26, 20)) for s in analysis.SERVERS))

    def test_a_windowed_pass_looks_for_the_analysis_from_its_windows_opening(self):
        t = hour(2026, 9, 26, 19)
        self.caught_up(t - dt.timedelta(hours=1))
        k = len(self.fx.fetched)
        self.run_window(t + dt.timedelta(minutes=44))
        for server in analysis.SERVERS:
            self.assertIn(analysis.file_url("rtma", "anl", server, t) + ".idx", self.fx.fetched[k:])
        self.assertIn("anl", self.state()["products"]["rtma"]["open"][iso(t)])
        k = len(self.fx.fetched)
        self.run_window(t + dt.timedelta(minutes=46))              # still not there, fetched again
        self.assertIn(analysis.anl_url("rtma", t, nomads=True) + ".idx", self.fx.fetched[k:])
        self.fx.add_hour("rtma", t)                                # NOAA publishes it at 19:47
        self.assertEqual(self.run_window(t + dt.timedelta(minutes=50)), 0)
        ps = self.state()["products"]["rtma"]
        self.assertEqual((ps["newest"], ps["open"]), (iso(t), {}))
        self.assertIsNotNone(self.read(analysis.FRAME_KEY.format(product="rtma", var="temp", stamp="20260926T19Z")))

    def test_a_windowed_pass_leaves_the_older_open_files_and_the_heavy_work_to_the_ten_minute_pass(self):
        h15, h16, h19, h20 = (hour(2026, 9, 26, k) for k in (15, 16, 19, 20))
        self.fx.add_hour("rtma", h15)                    # an older open file that has landed since
        self.fx.add_hour("rtma", h16, precip=False)      # an hour archived before 2026-11-03 ...
        res = analysis.read_hour("rtma", h16, LOCS, {"wexp": [], "g184": []}, frames=False, now=h19)
        analysis.write_hour(self.st, "rtma", h16, res, {}, {}, h19)
        self.fx.add_precip("rtma", h16)                  # ... whose precipitation is still open
        state = analysis.new_state()
        state["products"]["rtma"].update({"newest": iso(h19), "scanned": iso(h19), "since": iso(h15),
                                          "open": {iso(h15): {"anl": {}}, iso(h16): {"pcp": {}}}})
        state["backfill"]["rtma"].update({"cursor": iso(h19), "oldest": iso(h19)})
        state["layout"] = "cutoff-2026-10-14"
        analysis.save_state(self.st, state, h19)
        k = len(self.fx.fetched)
        self.assertEqual(self.run_window(h20 + dt.timedelta(minutes=46)), 0)
        got = self.fx.fetched[k:]
        self.assertFalse([u for u in got if "t15z" in u or "2026092615" in u or "2026092616" in u])
        self.assertIn(analysis.anl_url("rtma", h20) + ".idx", got)       # the hour now due, not there yet
        s = self.state()
        self.assertEqual(sorted(s["products"]["rtma"]["open"]), [iso(h15), iso(h16), iso(h20)])
        self.assertEqual((s["backfill"]["rtma"]["cursor"], s["precipSwept"], s["pruned"], s["layout"]),
                         (iso(h19), None, None, "cutoff-2026-10-14"))
        self.assertIsNone(self.read(analysis.hour_key("rtma", h15)))
        k = len(self.fx.fetched)
        self.run_window(h20 + dt.timedelta(minutes=50))
        self.assertIn(analysis.anl_url("rtma", h20) + ".idx", self.fx.fetched[k:])
        self.assertNotIn(analysis.anl_url("rtma", h15) + ".idx", self.fx.fetched[k:])
        # the ten-minute pass does all of it
        at = h20 + dt.timedelta(minutes=58)
        self.assertEqual(self.run_pass(at), 0)
        s = self.state()
        self.assertIsNotNone(self.read(analysis.hour_key("rtma", h15)))
        self.assertTrue(self.read(analysis.precip_key("rtma", h16))["complete"])
        opened = s["products"]["rtma"]["open"]
        self.assertEqual((iso(h15) in opened, iso(h16) in opened, iso(h20) in opened), (False, False, True))
        self.assertEqual((s["precipSwept"], s["pruned"], s["layout"]), (iso(at), iso(at), analysis.LAYOUT))
        self.assertNotEqual(s["backfill"]["rtma"]["cursor"], iso(h19))

    def test_a_pass_that_finds_the_lease_held_writes_nothing_and_is_not_a_failure(self):
        t = hour(2026, 9, 26, 19)
        self.fx.add_hour("rtma", t)
        other = storage.Lease(self.st, analysis.LEASE_KEY)
        self.assertTrue(other.acquire(NOW - dt.timedelta(minutes=1), 900, "baseline"))
        for window in (False, True):
            n, k = len(self.st.puts), len(self.fx.fetched)
            self.assertEqual(self.run_pass(NOW, window=window), 0)
            self.assertEqual((self.st.puts[n:], self.fx.fetched[k:]), ([], []))
            self.assertEqual((self.status["lease"], self.status["holder"]["mode"]), ("held", "baseline"))
            self.assertEqual((archive.LAST_STATUS["lease"], archive.LAST_STATUS["errors"], archive.LAST_STATUS["alarms"]),
                             ("held", 0, []))
        self.assertIsNone(self.state())
        self.assertIsNone(self.health())
        # the holder lets go and the next pass runs. It holds the lease while it
        # reads the state, until past the longest it can run, its budget plus 90 s
        self.assertTrue(other.release(NOW))
        seen = {}
        keep = analysis.load_state

        def peek(store, now=None):
            seen.update(json.loads(store.get(analysis.LEASE_KEY)))
            return keep(store, now)
        analysis.load_state = peek
        self.addCleanup(lambda: setattr(analysis, "load_state", keep))
        self.cfg = {"pass_budget_seconds": 840}
        at = NOW + dt.timedelta(minutes=1)
        self.assertEqual(self.run_pass(at), 0)
        self.assertEqual((seen["mode"], seen["released"], seen["acquired"], seen["expires"]),
                         ("baseline", None, iso(at), iso(at + dt.timedelta(seconds=840 + 90))))
        self.assertEqual(self.state()["products"]["rtma"]["newest"], iso(t))
        self.assertTrue(json.loads(self.st.get(analysis.LEASE_KEY))["released"])
        # a pass that died holding the lease holds the lane only until it expires
        dead = storage.Lease(self.st, analysis.LEASE_KEY)
        self.assertTrue(dead.acquire(NOW + dt.timedelta(minutes=5), 60, "window"))
        self.run_window(NOW + dt.timedelta(minutes=5, seconds=30))
        self.assertEqual(self.status["lease"], "held")
        self.run_window(NOW + dt.timedelta(minutes=6))
        self.assertEqual(self.status["lease"], "released")

    def test_a_read_by_a_windowed_pass_counts_for_the_next_ten_minute_pass(self):
        # the windowed passes never touch the failure streak, so the alarm still
        # counts passes at the ten-minute cadence; an hour they read since the
        # last ten-minute pass is a read there
        t19, t20, t21 = (hour(2026, 9, 26, k) for k in (19, 20, 21))
        self.fx.add_hour("rtma", t19)
        self.assertEqual(self.run_pass(), 0)
        self.st.put(analysis.HEALTH_KEY, json.dumps({"rtma": {"fail_streak": 3, "last_ok": None,
                                                              "last_error": "read failed"}}).encode(), "application/json")
        self.fx.add_hour("rtma", t20)
        self.assertEqual(self.run_window(t20 + dt.timedelta(minutes=50)), 0)
        self.assertEqual(self.status["readCount"], 1)
        self.assertEqual(self.health()["rtma"]["fail_streak"], 3)             # untouched
        self.assertEqual(self.run_pass(t20 + dt.timedelta(minutes=58)), 0)
        self.assertEqual(archive.LAST_STATUS["read"], 0)                     # nothing new to read itself
        self.assertEqual(self.health()["rtma"]["fail_streak"], 0)
        # a windowed pass whose read fails adds nothing to the streak; the
        # ten-minute pass that fails on the same file, with nothing read since, does
        self.fx.add_hour("rtma", t21, precip=False)
        bad = {analysis.anl_url("rtma", t21, nomads=n) for n in (False, True)}
        self.fx.raise_for = lambda url: url in bad                         # every message read fails, on both
        self.assertEqual(self.run_window(t21 + dt.timedelta(minutes=50)), 1)
        self.assertEqual(self.health()["rtma"]["fail_streak"], 0)
        self.run_pass(t21 + dt.timedelta(minutes=58))
        self.assertEqual(self.health()["rtma"]["fail_streak"], 1)

    def test_a_precipitation_read_is_a_read(self):
        # the analysis refused and the hour's precipitation file read on its
        # own. The pass read something, so it is no outage, and the refusal is
        # still a failure on the record and on the streak
        t = hour(2026, 9, 26, 19)
        self.fx.add_hour("rtma", t)
        self.fx.grids[("rtma", "TMP", iso(t))] = "other"
        self.assertEqual(self.run_pass(), 0)
        self.assertEqual((archive.LAST_STATUS["failed"], archive.LAST_STATUS["pcpRead"], archive.LAST_STATUS["read"]), (2, 1, 0))
        self.assertTrue(self.read(analysis.precip_key("rtma", t))["complete"])
        self.assertEqual(self.health()["rtma"]["fail_streak"], 1)

    def test_live_files_carry_no_stale_while_revalidate(self):
        # the settlement display fetches a live day's place file and day file
        # again each minute; the CDN keeps the query string out of its cache
        # key, so a stale-while-revalidate answer after the minute could be
        # minutes old (2026-11-03). stale-if-error stays, and a final place-day
        # keeps its longer life
        self.assertNotIn("stale-while-revalidate", analysis.CACHE_LIVE)
        self.assertEqual(analysis.CACHE_LIVE, "public, max-age=60, stale-if-error=86400")
        self.assertEqual(analysis.CACHE_FINAL, "public, max-age=300, stale-while-revalidate=1800, stale-if-error=86400")
        self.fx.add_hour("rtma", hour(2026, 9, 26, 19))
        self.assertEqual(self.run_pass(), 0)
        cc = {k: c for k, _, c in self.st.puts}
        for key in (analysis.DAY_KEY.format(day="2026-09-26"), analysis.LOC_KEY.format(loc="new-york-ny", day="2026-09-26"),
                    analysis.INDEX_KEY):
            self.assertEqual(cc[key], analysis.CACHE_LIVE, key)
        # at the first ten-minute pass past every place's cut-off the place-day
        # is final with its precipitation resolved and goes to CACHE_FINAL; the
        # day file stays live
        n = len(self.st.puts)
        self.assertEqual(self.run_pass(analysis.day_end("2026-09-26", "America/Los_Angeles") + dt.timedelta(minutes=48)), 0)
        cc = {k: c for k, _, c in self.st.puts[n:]}
        self.assertEqual(cc[analysis.LOC_KEY.format(loc="new-york-ny", day="2026-09-26")], analysis.CACHE_FINAL)
        self.assertEqual(cc[analysis.DAY_KEY.format(day="2026-09-26")], analysis.CACHE_LIVE)


class PassTiming(unittest.TestCase):
    def test_every_pass_ends_before_the_next_minute_in_a_window(self):
        self.assertEqual(analysis.WINDOW_MINUTES, (14, 16, 18, 20, 44, 46, 48, 50, 52, 54))
        self.assertEqual(analysis.LOOK_MINUTES, {"anl": 44, "pcp": 14})
        at = lambda m, s=0: dt.datetime(2026, 11, 3, 14, m, s, tzinfo=UTC)      # noqa: E731
        self.assertEqual(analysis.next_window_minute(at(8, 3)), at(14))
        self.assertEqual(analysis.next_window_minute(at(47, 10)), at(48))
        self.assertEqual(analysis.next_window_minute(at(20)), at(44))
        self.assertEqual(analysis.next_window_minute(at(54, 30)), dt.datetime(2026, 11, 3, 15, 14, tzinfo=UTC))
        # the ten-minute pass outside the windows keeps its cap
        for m in (8, 28, 38, 58):
            self.assertEqual(analysis.pass_seconds(at(m), None), analysis.PASS_CAP_SECONDS, m)
        # at :48 and :18 it has until ten seconds before the next windowed pass
        self.assertEqual(analysis.pass_seconds(at(48), None), 110)
        self.assertEqual(analysis.pass_seconds(at(18, 5), None), 105)
        # never under the floor, and never past the invocation's budget
        self.assertEqual(analysis.pass_seconds(at(47, 30), None), analysis.PASS_FLOOR_SECONDS)
        self.assertEqual(analysis.pass_seconds(at(8), 200), 200)
        self.assertEqual(analysis.pass_seconds(at(47, 30), 60), 60)

    def test_the_analysis_is_looked_for_from_its_windows_opening(self):
        # at the ten-minute pass's own minutes the walk looks for the hour the
        # health check expects, as it did; inside :44 to :46 it looks one hour on
        at = lambda m: dt.datetime(2026, 11, 3, 14, m, tzinfo=UTC)               # noqa: E731
        for m in range(60):
            same = analysis.due_latest("rtma", at(m)) == analysis.expected_latest("rtma", at(m))
            self.assertEqual(same, m not in (44, 45, 46), m)
        self.assertEqual(analysis.due_latest("rtma", at(44)), at(0))


class Samples(unittest.TestCase):
    """The checked-in samples (samples/snapshots/analysis2), which the page
    renders offline and scripts/verify.py drives: rebuilt with the lane's own
    code, so every place file carries the running values, and each must equal
    a fresh resolution of its raw CSV."""
    SNAP = os.path.join(ROOT, "samples", "snapshots", "analysis2")

    def test_every_hour_of_every_sample_place_day_runs_on_the_days_resolution(self):
        with open(os.path.join(self.SNAP, "index.json")) as fh:
            index = json.load(fh)
        raws, files, rows = {}, 0, 0
        for d in index["days"]:
            with open(os.path.join(self.SNAP, "csv", d + "-raw.csv")) as fh:
                raws[d] = fh.read()
        for loc in index["locations"]:
            for d in index["days"]:
                path = os.path.join(self.SNAP, "loc", loc["id"], d + ".json")
                with open(path) as fh:
                    doc = json.load(fh)
                rows += check_running(self, doc, raw_rows_of(raws[d], loc["id"]))
                files += 1
        self.assertEqual(files, 67 * 6)
        self.assertGreater(rows, 67 * 5 * 24)

    def test_the_wet_boston_day_resolves_on_its_total_not_on_its_rounded_hours(self):
        # Boston on 2026-09-28: the hours as shown add to 0.83 in and the day
        # resolved on 0.84, summed from the files' own numbers and rounded once
        with open(os.path.join(self.SNAP, "loc", "boston-ma", "2026-09-28.json")) as fh:
            doc = json.load(fh)
        shown = sum(Decimal(str(r["rtma"]["show"]["precip"])) for r in doc["hours"])
        self.assertEqual(shown, Decimal("0.83"))
        self.assertEqual((doc["summary"]["rtma"]["precip"]["value"], doc["hours"][-1]["rtma"]["run"]["precip"]["value"]),
                         (0.84, 0.84))


class FullDay(unittest.TestCase):
    """golden_scenario run once on the lane as it stands: the day's files
    byte-identical to GOLDEN once rowShape 3's fields are taken away, and the
    place files of the full day in rowShape 3."""
    @classmethod
    def setUpClass(cls):
        cls.job = Job("run_pass")
        cls.job.setUp()
        try:
            cls.files = golden_scenario(cls.job)
        except Exception:
            cls.job.doCleanups()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.job.doCleanups()

    def test_the_days_figures_states_and_csv_files_are_byte_identical_to_before(self):
        # the hourly cut settles beside the daily cut-offs in this day (an
        # analysis on one server, one on neither, a late NOMADS copy, and
        # New York's 23:00 counting for the hourly contract and not the day),
        # and the day file, both CSV files and every place file's figures and
        # states are the bytes the lane wrote before rowShape 3
        with gzip.open(GOLDEN, "rt", encoding="utf-8") as fh:
            golden = json.load(fh)
        self.assertEqual(sorted(golden), sorted(self.files))
        for key, text in sorted(golden.items()):
            got = as_row_shape_2(self.files[key]) if "/loc/" in key else self.files[key]
            self.assertEqual(got, text, key)

    def test_the_place_file_of_a_full_day_in_row_shape_3(self):
        raw = self.files[analysis.CSV_RAW_KEY.format(day=GOLDEN_DAY)]
        docs = {loc["id"]: json.loads(self.files[analysis.LOC_KEY.format(loc=loc["id"], day=GOLDEN_DAY)]) for loc in LOCS}
        for lid, doc in docs.items():
            self.assertEqual(check_running(self, doc, raw_rows_of(raw, lid)), 24, lid)
            for r in doc["hours"]:
                t = analysis._parse_iso(r["t"])
                self.assertEqual(sorted(k for k in r["rtma"] if k in ROW_SHAPE_3), sorted(ROW_SHAPE_3), (lid, r["t"]))
                self.assertEqual(r["rtma"]["anlFile"]["name"], analysis.file_name("rtma", "anl", t))
                self.assertEqual(r["rtma"]["pcpFile"]["name"], analysis.file_name("rtma", "pcp", t + dt.timedelta(hours=1)))
        rows = {r["t"]: r["rtma"] for r in docs["new-york-ny"]["hours"]}
        summary = docs["new-york-ny"]["summary"]["rtma"]
        # the day so far after its last hour is the day's own figure before rounding
        end = rows[iso(hour(2026, 9, 27, 3))]["runX"]
        self.assertEqual((analysis.cut(Decimal(end["high"]), 0.001), analysis.cut(Decimal(end["precip"]), 0.0001)),
                         (summary["high"]["exact"], summary["precip"]["exact"]))

        def states(t):
            h = rows[iso(t)]["hourly"]
            return h["state"], h["at"], h["basis"], h["src"], h["late"]
        self.assertEqual(states(hour(2026, 9, 26, 4)), ("final", "2026-09-26T04:48:00Z", "nomads", "nomads", False))
        self.assertEqual(states(hour(2026, 9, 26, 9)), ("final", "2026-09-26T12:00:00Z", "cutoff", "nodd", False))
        self.assertEqual(states(hour(2026, 9, 26, 12)), ("review", "2026-09-26T15:00:00Z", "cutoff", None, False))
        self.assertEqual(states(hour(2026, 9, 26, 15)), ("final", "2026-09-26T18:00:00Z", "cutoff", "nodd", True))
        # at New York's 23:00 the day keeps NOAA Open Data's copy, read by its
        # cut-off, and the hourly contract takes NOMADS's, in by the hourly cut
        between = rows[iso(hour(2026, 9, 27, 3))]
        self.assertEqual(states(hour(2026, 9, 27, 3)), ("final", "2026-09-27T05:10:00Z", "nomads", "nomads", False))
        self.assertEqual((between["src"], between["raw"]["temp"], between["hourly"]["value"], between["hourly"]["x"]),
                         ("nodd", "289.09", 80, "80.330"))
        self.assertEqual((between["anlFile"]["counts"], between["anlFile"]["afterCut"], between["anlFile"]["nomads"]),
                         (True, False, {"lm": "2026-09-27T05:10:00Z", "seen": "2026-09-27T05:50:00Z"}))
        chi = next(r["rtma"] for r in docs["chicago-il"]["hours"] if r["t"] == iso(hour(2026, 9, 27, 3)))
        self.assertEqual((chi["src"], chi["show"]["temp"], chi["hourly"]["value"]), ("nomads", 80, 80))


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

    def test_a_fetch_reports_the_objects_last_modified_time(self):
        # the cut-off of the draft terms is judged on when the server wrote the file
        self.serve(b"3456789", {"Last-Modified": "Wed, 14 Oct 2026 04:17:09 GMT", "ETag": '"abc"'})
        meta = {}
        self.assertEqual(gov_weather.fetch_range("https://x/y", 3, 9, meta=meta), b"3456789")
        self.assertEqual(meta, {"lastModified": "2026-10-14T04:17:09Z", "etag": '"abc"'})
        meta = {}
        self.assertEqual(gov_weather.fetch_bytes("https://x/y", meta=meta), b"3456789")
        self.assertEqual(meta["lastModified"], "2026-10-14T04:17:09Z")
        self.assertIsNone(gov_weather.last_modified({"Last-Modified": "not a date"}))

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
        self.assertIs(run.JOBS["analysis-window"], analysis.window_pass)
        with open(os.path.join(ROOT, "scripts", "package_lambda.py")) as fh:
            src = fh.read()
        for item in ("config/analysis_locations.json", "config/analysis_lattice.json", "config/analysis_file_times.json",
                     "geo/settlement_locations.csv"):
            self.assertIn(item, src)
        with open(os.path.join(ROOT, "ops", "aws", "template.yaml")) as fh:
            tpl = fh.read()
        self.assertIn("cron(8/10 * * * ? *)", tpl)
        self.assertIn('{"job": "analysis"}', tpl)
        self.assertIn('{"job": "analysis-window"}', tpl)
        self.assertIn("eight EventBridge schedules", tpl)

    def test_the_file_times_table_is_built_from_the_engines_csv(self):
        # one file to a line, {kind: {file_utc: {server: {lm, seen}}}}, and a
        # row that is not one file on one server at the top of an hour, or a
        # file given twice, refused with nothing written
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import build_analysis_file_times as b   # noqa: E402
        head = "kind,file_utc,server,last_modified,first_seen"
        good = ["anl,2026-11-03T16:00:00Z,nomads,2026-11-03T16:46:12Z,2026-11-03T16:47:00Z",
                "anl,2026-11-03T16:00:00Z,nodd,2026-11-03T16:48:00Z,", "pcp,2026-11-03T17:00:00Z,nodd,2026-11-03T17:16:40Z,"]
        with tempfile.TemporaryDirectory() as tmp:
            src, out = os.path.join(tmp, "times.csv"), os.path.join(tmp, "times.json")

            def run(lines):
                with open(src, "w") as fh:
                    fh.write("\n".join(lines) + "\n")
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    return b.main([src, out])
            self.assertEqual(run([head] + good), 0)
            self.assertEqual(analysis.load_file_times(out), {
                "anl": {"2026-11-03T16:00:00Z": {"nodd": {"lm": "2026-11-03T16:48:00Z", "seen": None},
                                                 "nomads": {"lm": "2026-11-03T16:46:12Z", "seen": "2026-11-03T16:47:00Z"}}},
                "pcp": {"2026-11-03T17:00:00Z": {"nodd": {"lm": "2026-11-03T17:16:40Z", "seen": None}}}})
            with open(out) as fh:
                self.assertEqual(len(fh.read().splitlines()), 8)
            os.remove(out)
            for bad in ([head] + good + good[1:2], [head, "anl,2026-11-03T16:30:00Z,nodd,2026-11-03T16:48:00Z,"],
                        [head, "anl,2026-11-03T16:00:00Z,aws,2026-11-03T16:48:00Z,"],
                        [head, "anl,2026-11-03T16:00:00Z,nodd,3 Nov 16:48,"], [head.replace("first_seen", "seen")] + good):
                self.assertEqual(run(bad), 1, bad)
                self.assertFalse(os.path.exists(out), bad)
        # the checked-in table is the engine's of 2026-11-04: the analysis
        # that never reached NOAA Open Data, and the precipitation file that
        # reached it after Los Angeles's cut-off and counted under the earlier rule
        t = analysis.load_file_times()
        self.assertEqual(t["anl"]["2026-09-23T19:00:00Z"], {"nomads": {"lm": "2026-09-23T19:46:54Z", "seen": None}})
        self.assertEqual(t["pcp"]["2026-09-30T07:00:00Z"]["nodd"]["lm"], "2026-09-30T08:01:44Z")

    def test_the_two_schedules_take_each_minute_in_a_window_once(self):
        """The ten-minute pass keeps cron(8/10) and the windowed pass takes the
        window minutes it does not, so no minute starts two passes, and the
        ten-minute pass at :48 and :18 is the windowed pass of its minute."""
        import re
        with open(os.path.join(ROOT, "ops", "aws", "template.yaml")) as fh:
            tpl = fh.read()
        found = {}
        for m in re.finditer(r'Name: !Sub "\$\{SiteName\}-([a-z-]+)"\n\s+ScheduleExpression: cron\(([0-9,/]+) \* \* \* \? \*\)'
                             r".*?Input: '(\{[^']*\})'\n\s+RetryPolicy: \{ MaximumRetryAttempts: (\d+)", tpl, re.S):
            field = m.group(2)
            if "/" in field:
                first, step = (int(x) for x in field.split("/"))
                minutes = set(range(first, 60, step))
            else:
                minutes = {int(x) for x in field.split(",")}
            found[m.group(1)] = (minutes, json.loads(m.group(3)), int(m.group(4)))
        base, base_in, _ = found["analysis"]
        win, win_in, win_retry = found["analysis-windows"]
        self.assertEqual((base_in, win_in, win_retry), ({"job": "analysis"}, {"job": "analysis-window"}, 0))
        self.assertEqual(base & win, set())
        self.assertEqual(win, set(analysis.WINDOW_MINUTES) - base)
        self.assertEqual(set(analysis.WINDOW_MINUTES) & base, {18, 48})


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

    def test_a_rebuilt_config_keeps_each_places_code_and_no_two_share_one(self):
        # ForecastEx's location codes (2026-11-04) are a column of the
        # settlement list, so a rerun of the generator writes the checked-in
        # file byte for byte, codes included, and refuses a list with a place
        # missing its code or two places sharing one
        centres = self.bag.read_centres()
        rebuilt = self.bag.build_locations(centres, self.tr)
        with open(analysis.LOCATIONS_PATH) as fh:
            self.assertEqual(json.dumps(rebuilt, indent=1) + "\n", fh.read())
        codes = [l.get("code") for l in rebuilt]
        self.assertTrue(all(codes))
        self.assertEqual(len(set(codes)), len(rebuilt))
        self.assertEqual(next(l["code"] for l in rebuilt if l["id"] == "new-york-ny"), "NYC")
        self.bag.check_codes(centres)
        for broken in ([dict(c, code="") if c["id"] == "boise-id" else c for c in centres],
                       [dict(c, code="NYC") if c["id"] == "los-angeles-ca" else c for c in centres]):
            with self.assertRaises(SystemExit):
                self.bag.check_codes(broken)

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
