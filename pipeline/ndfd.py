"""
ndfd.py — the National Digital Forecast Database's daytime high and overnight
low behind the landing map (owner, 2026-10-20).

NOAA Open Data on AWS carries NDFD's newest CONUS issuance as one GRIB2 file per
element (noaa-ndfd-pds, opnl/AR.conus/VP.001-003/ds.maxt.bin and ds.mint.bin,
three 12-hour periods each), written again every hour; earlier issuances are not
kept there. This lane reads each new issuance once and keeps it sampled at the
map's own pixels: the centre of every 4-pixel cell of the landing map's 960 by
600 frame, taken back through the map's projection to the NDFD grid point
nearest it. The numbers are NDFD's as issued, rounded to whole degrees
Fahrenheit like the NWS's own maps; nothing is interpolated or blended.

  archive/ndfd/conus_<issued>.json.gz   {issued, ref, grid, maxt: {end: b64}, mint: {end: b64}}
  snapshots/ndfd/<view>.json            {view, day, kind, issued, ref, start, end, grid, b64}

The four views are the map's four buttons. Today's high and low come from the
last issuance published before midnight Eastern on the day, which is before the
day began everywhere in the lower 48, so the shading holds still through the
day, as the city chart's level lines do (owner, 2026-10-20). With none on file
from before the day, the first issuance of the day stands in and says so.
Tomorrow's come from the newest issuance.

The daytime high is NDFD's MaxT for the 12-hour period ending at 00Z after the
day; the overnight low is MinT for the period ending at 12Z on the day, the
night that ends on the morning of the day, which is the low the NWS forecasts
for that day. A value is one byte, degrees Fahrenheit plus 60, with 0 where NDFD
has no value (the open ocean, beyond the borders), base64 encoded, rows from the
top of the map down.

The lane also keeps NDFD's hourly temperature (Patrick, 2026-11-15), so that the
landing can set RTMA's newest hour beside the forecast for it. ds.temp.bin is
written again about every 30 minutes and held 44 messages, some 45 MB, on 15
November 2026: one an hour to 35 hours ahead, then two and three hours apart to
the third day, in forecast order. Its reference time can fall after its write,
the file of 12:21 holding 12:30 and its hours at 30 minutes past it, so the
first hour it carries can be the one after next. Each message follows a 40-byte
WMO header, and the file opens with one more, so a message is found by its
"GRIB". The lane reads by range only what it keeps: each message's head for its
hour, then the messages of the AHEAD_HOURS hours after the hour the issuance
was published in. A Last-Modified that changes between those reads leaves the
read for the next pass, so two issuances never mix, and so does a pass with too
little time left for the next read. Each kept hour is sampled where the analysis
lane samples its temperature frames: at each point's own wexp cell in
config/analysis_lattice.json and at each settlement place's own wexp cell in
config/analysis_locations.json, taken to NDFD's grid as (i - 200, j), since
grid 184 is the wexp grid without its western 200 columns. So a frame of the
forecast and the analysis frame of the same hour hold values for the same 2.5
km cells, in the same tenths of a degree, and their difference is cell for cell.
The lattice file's own g184 list is not used for this. Each of its lists holds
its own grid's nearest cell, and grid 184's first point sits a few metres from
wexp's 200 columns in, so a point that close to a cell's edge rounds to the next
cell on one grid and not the other: 261 of the 64,000, spread through the grid.

  archive/ndfd-hour/temp_<issued>.json.gz  {issued, ref, hours: {valid: {values, own}}}
  snapshots/ndfd/hour/<YYYYMMDDTHH>Z.json  {kind, valid, issued, ref, unit, scale, pitch, cols, rows, values, own}
  snapshots/ndfd/hour/index.json           {hours: {<YYYYMMDDTHH>Z: {issued, ref}}}

Each hour that has begun, back BUILD_HOURS, takes the newest issuance that was
published before the hour began and carries the hour: the forecast in hand as
the hour started. An hour that no such issuance carries has no file, and a
file once written stands, since no issuance published before its hour can
arrive later. Frames are kept FRAME_KEEP_DAYS, as the analysis lane keeps its
own, so the three maps cover the same hours, and the index lists the frames
kept. The values are the analysis frames' own encoding: degrees Fahrenheit
times SCALE rounded half away from zero, None off the grid, on the frames'
lattice, rows from the top of the map down; `own` gives each place's value at
its own cell the same way.

The hourly part runs after the four views are written and cannot fail the pass.
A failure is in the pass's status line and in a streak of its own, which raises
the alarm once FAIL_STREAK_ALARM passes in a row have failed, as the SST and
advisory lanes do.
"""
from __future__ import annotations
import base64
import datetime as dt
import gzip
import json
import math
import struct
import time
import urllib.error
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from . import archive as arch
from . import basemap
from . import grib2
from . import gov_weather as gw
from .analysis import (FRAME_KEEP_DAYS, LATTICE_COLS, LATTICE_PATH, LATTICE_PITCH, LATTICE_ROWS, LOCATIONS_PATH,
                       SCALE, UNITS, k_to_f)
from .snapshots import SNAP_CACHE, _iso, _parse_iso
from .storage import Storage

SCHEMA = 1
ELEMENTS = ("maxt", "mint")
ARCHIVE = "archive/ndfd/"
PREFIX = "snapshots/ndfd/"
STEP = 4                                    # map pixels per sampled cell
NX, NY = int(basemap.W // STEP), int(basemap.H // STEP)
OFFSET = 60                                 # a byte is degrees F plus this; 0 is no value
KEEP_DAYS = 3
EASTERN = ZoneInfo("America/New_York")
SOURCE = "NWS National Digital Forecast Database (NDFD), CONUS 2.5 km, from NOAA Open Data"
VIEWS = {"hiT": ("maxt", 0), "loT": ("mint", 0), "hi": ("maxt", 1), "lo": ("mint", 1)}
GRID = grib2.NDFD                           # the grid every message must be on (tests set None)

# the hourly temperature, kept apart from ARCHIVE, whose listing the four views read
HOURLY = "temp"
HOUR_ARCHIVE = "archive/ndfd-hour/"
HOUR_PREFIX = PREFIX + "hour/"
AHEAD_HOURS = 3                             # hours kept of each issuance, after the hour it was published in
BUILD_HOURS = 24                            # hours back a missing frame is written, the newest the hour now begun
HOUR_KEEP_DAYS = 2                          # issuances kept, enough for every hour BUILD_HOURS reaches
# the analysis lane's map frames: their lattice, their tenths of a degree and their unit
PITCH, COLS, ROWS = LATTICE_PITCH, LATTICE_COLS, LATTICE_ROWS
HOUR_SCALE, HOUR_UNIT = SCALE[HOURLY], UNITS[HOURLY]
HEAD_BYTES = 512                            # a message's sections 0 to 4, enough for its hour
MAX_MESSAGES = 200                          # a walk past this many messages is refused; ds.temp.bin held 44
HOUR_HEALTH_KEY = "archive/_meta/health_ndfd_hour.json"    # the hourly part's own failure streak (archive.update_health)
FAIL_STREAK_ALARM = 3                       # passes in a row, as the SST and advisory lanes count them
READ_TRIES, READ_TIMEOUT = 2, 30            # a range read's tries and seconds a try
STEP_NEED = 90                              # seconds kept free before each read: its two tries, and a decode
HOUR_CELLS: Optional[tuple] = None          # (lattice cells, place ids, place cells), read once; tests set their own


def url(element: str) -> str:
    return f"{gw.NODD_NDFD}/ds.{element}.bin"


def messages(blob: bytes) -> list:
    """The GRIB2 messages of a file, in order."""
    out, off = [], 0
    while True:
        i = blob.find(b"GRIB", off)
        if i < 0 or i + 16 > len(blob):
            return out
        n = struct.unpack(">Q", blob[i + 8:i + 16])[0]
        out.append(blob[i:i + n])
        off = i + n


_CELLS: dict = {}


def cells(grid: dict) -> list:
    """For every sampled cell, rows from the top of the map, the index of the
    grid point nearest its centre, or None off the grid."""
    key = (grid["Ni"], grid["Nj"], round(grid["lat1"], 4), round(grid["lon1"], 4))
    if key not in _CELLS:
        tr = basemap.Transform.from_json(basemap.load_field_grid()["transform"])
        out = []
        for j in range(NY):
            for i in range(NX):
                lon, lat = tr.unscreen(i * STEP + STEP / 2, j * STEP + STEP / 2)
                hit = grib2.nearest_cell(grid, lat, lon)
                out.append(hit[2] if hit else None)
        _CELLS[key] = out
    return _CELLS[key]


def to_byte(v: Optional[float]) -> int:
    """A value in kelvin as the cell's byte: whole degrees F, half up, plus OFFSET; 0 for none."""
    if v is None:
        return 0
    # to six places first, so a half degree that float arithmetic leaves a hair short still rounds up
    return max(1, min(255, math.floor(round((v - 273.15) * 9 / 5 + 32, 6) + 0.5) + OFFSET))


def sample(msg: bytes) -> str:
    """One message at the map's cells, as base64 bytes."""
    g = grib2.grid_def(msg)
    if GRID is not None and not grib2.same_grid(g, GRID):
        raise ValueError("not the NDFD CONUS grid")
    ks = cells(g)
    want = sorted({k for k in ks if k is not None})
    got = dict(zip(want, grib2.decode_points(msg, want)))
    return base64.b64encode(bytes(to_byte(None if k is None else got[k]) for k in ks)).decode("ascii")


def _end_iso(msg: bytes) -> str:
    y, mo, d, h, mi, s = grib2.product_def(msg)["end"]
    return dt.datetime(y, mo, d, h, mi, s, tzinfo=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_issuance(fetch: Callable, now: dt.datetime) -> Optional[dict]:
    """The newest issuance, both elements sampled, or None when a file is not there."""
    out = {"schema": SCHEMA, "grid": {"step": STEP, "nx": NX, "ny": NY}, "lastModified": {}, "ref": {}}
    for el in ELEMENTS:
        meta: dict = {}
        blob = fetch(url(el), meta)
        if not blob:
            return None
        msgs = messages(blob)
        if not msgs:
            raise ValueError(f"ds.{el}.bin carries no GRIB2 message")
        y, mo, d, h, mi, s = grib2.ref_time(msgs[0])
        out["ref"][el] = dt.datetime(y, mo, d, h, mi, s, tzinfo=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out["lastModified"][el] = meta.get("lastModified")
        out[el] = {_end_iso(m): sample(m) for m in msgs}
    # the issuance was in hand once both files were written
    seen = [v for v in out["lastModified"].values() if v]
    out["issued"] = max(seen) if len(seen) == len(ELEMENTS) else _iso(now)
    return out


def _stamp(iso: str) -> str:
    return _parse_iso(iso).strftime("%Y%m%dT%H%M%SZ")


def archived(store: Storage) -> list:
    """The archived issuances' keys, oldest first."""
    return sorted(k for k in store.list(ARCHIVE) if k.endswith(".json.gz"))


def _load(store: Storage, key: str) -> Optional[dict]:
    raw = store.get(key)
    return json.loads(gzip.decompress(raw)) if raw else None


def eastern_day(now: dt.datetime) -> dt.date:
    return now.astimezone(EASTERN).date()


def build_views(store: Storage, now: dt.datetime, keys: list) -> dict:
    """The four views from the archive: today's from the last issuance before
    midnight Eastern (else the first of the day), tomorrow's from the newest."""
    day = eastern_day(now)
    cutoff = dt.datetime.combine(day, dt.time(0), tzinfo=EASTERN).astimezone(dt.timezone.utc)
    stamps = [(k, dt.datetime.strptime(k[len(ARCHIVE) + 6:-8], "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc))
              for k in keys]
    before = [k for k, t in stamps if t < cutoff]
    after = [k for k, t in stamps if t >= cutoff]
    picks = {"today": (before[-1], True) if before else ((after[0], False) if after else (None, False)),
             "tomorrow": (keys[-1], True) if keys else (None, False)}
    docs: dict = {}
    out = {}
    for view, (el, plus) in VIEWS.items():
        d = day + dt.timedelta(days=plus)
        key, pre = picks["today" if plus == 0 else "tomorrow"]
        # MaxT for the day ends at 00Z after it; MinT ends at 12Z on it
        end = dt.datetime.combine(d + dt.timedelta(days=1) if el == "maxt" else d, dt.time(0 if el == "maxt" else 12),
                                  tzinfo=dt.timezone.utc)
        end_iso = end.strftime("%Y-%m-%dT%H:%M:%SZ")
        snap = {"schema": SCHEMA, "view": view, "day": d.isoformat(), "kind": el, "asof": _iso(now),
                "start": _iso(end - dt.timedelta(hours=12)), "end": end_iso, "grid": {"step": STEP, "nx": NX, "ny": NY},
                "offset": OFFSET, "source": SOURCE, "issued": None, "ref": None, "preDay": None, "b64": None}
        if key:
            if key not in docs:
                docs[key] = _load(store, key)
            doc = docs[key] or {}
            b64 = (doc.get(el) or {}).get(end_iso)
            if b64:
                snap.update(issued=doc.get("issued"), ref=(doc.get("ref") or {}).get(el), b64=b64,
                            preDay=pre if plus == 0 else None)
        out[view] = snap
    return out


# ---------------------------------------------------------------- the hourly temperature
WEST = 200                                  # the wexp grid's columns west of grid 184's first


def ndfd_cell(kw: int) -> int:
    """A wexp cell as the same cell of NDFD's grid 184, (i - 200, j) with k =
    j * Ni + i on each, or -1 for one off grid 184 (or off wexp, given -1)."""
    if kw < 0:
        return -1
    i, j = kw % grib2.WEXP["Ni"], kw // grib2.WEXP["Ni"]
    if not (WEST <= i < WEST + grib2.G184["Ni"] and j < grib2.G184["Nj"]):
        return -1
    return j * grib2.G184["Ni"] + i - WEST


def hour_cells() -> tuple:
    """The analysis frames' lattice on the NDFD grid and the places' own cells
    on it, each the same cell as the analysis lane samples: every point's wexp
    cell in config/analysis_lattice.json (-1 off the grid) and every place's
    wexp cell in config/analysis_locations.json, with the places' ids, in the
    files' order."""
    global HOUR_CELLS
    if HOUR_CELLS is None:
        with open(LATTICE_PATH) as fh:
            lattice = json.load(fh)
        with open(LOCATIONS_PATH) as fh:
            locs = json.load(fh)
        if (lattice["pitch"], lattice["cols"], lattice["rows"]) != (PITCH, COLS, ROWS) or len(lattice["wexp"]) != COLS * ROWS:
            raise ValueError("config/analysis_lattice.json is not the analysis frames' lattice")
        HOUR_CELLS = ([ndfd_cell(k) for k in lattice["wexp"]], [loc["id"] for loc in locs],
                      [ndfd_cell(loc["cell"]["wexp"][2]) for loc in locs])
    return HOUR_CELLS


def to_tenths(v: Optional[float]) -> Optional[int]:
    """A temperature in kelvin as the analysis frames keep one: degrees
    Fahrenheit times HOUR_SCALE, rounded to six places and then half away from
    zero (analysis._frame_ints), and None for none."""
    if v is None:
        return None
    x = round(k_to_f(v) * HOUR_SCALE, 6)
    return int(math.floor(x + 0.5)) if x >= 0 else -int(math.floor(-x + 0.5))


def is_hourly_temp(msg: bytes) -> bool:
    """A temperature at a point in time (discipline 0, TMP, template 4.0), as ds.temp.bin's messages are."""
    p = grib2.product_def(msg)
    return p["discipline"] == 0 and (p["category"], p["number"], p["template"]) == (0, 0, 0)


def valid_time(msg: bytes) -> dt.datetime:
    """The hour a forecast message is for: its reference time and its forecast hours."""
    y, mo, d, h, mi, s = grib2.ref_time(msg)
    hours = grib2.product_def(msg)["forecast_hours"]
    if not math.isfinite(hours):
        raise ValueError("forecast time in a unit the lane does not read")
    return dt.datetime(y, mo, d, h, mi, s, tzinfo=dt.timezone.utc) + dt.timedelta(hours=hours)


def hour_sample(msg: bytes) -> tuple:
    """One hourly message at the frames' lattice and at each place's own cell,
    in tenths: (values, {id: value})."""
    g = grib2.grid_def(msg)
    if GRID is not None and not grib2.same_grid(g, GRID):
        raise ValueError("not the NDFD CONUS grid")
    lat, ids, own = hour_cells()
    want = sorted({k for k in lat + own if k >= 0})
    got = dict(zip(want, grib2.decode_points(msg, want)))
    values = [to_tenths(got[k]) if k >= 0 else None for k in lat]
    places = {i: to_tenths(got[k]) for i, k in zip(ids, own) if k >= 0 and got[k] is not None}
    return values, places


def message_head(head: bytes) -> dict:
    """A message's length and, from sections 1 and 4 among its first bytes,
    its reference time, product and forecast hours. Sections 2 and 3 are
    stepped over by their lengths."""
    if len(head) < 21 or head[:4] != b"GRIB" or head[7] != 2:
        raise ValueError("not the head of a GRIB2 message")
    out = {"length": struct.unpack(">Q", head[8:16])[0], "discipline": head[6]}
    p = 16
    while p + 5 <= len(head):
        ln, sec = struct.unpack(">I", head[p:p + 4])[0], head[p + 4]
        if ln < 5:
            raise ValueError(f"a section of length {ln} at offset {p}")
        b = head[p:p + ln]
        if sec == 1 and len(b) >= 19:
            out["ref"] = dt.datetime(struct.unpack(">H", b[12:14])[0], b[14], b[15], b[16], b[17], b[18],
                                     tzinfo=dt.timezone.utc)
        elif sec == 4 and len(b) >= 22:
            out.update(template=struct.unpack(">H", b[7:9])[0], category=b[9], number=b[10],
                       hours=struct.unpack(">I", b[18:22])[0] * grib2._TIME_UNIT_HOURS.get(b[17], float("nan")))
            return out
        p += ln
    raise ValueError(f"section 4 is not within the message's first {len(head)} bytes")


class IssuanceChanged(RuntimeError):
    """ds.temp.bin was written again between two of one pass's reads."""


class OutOfTime(RuntimeError):
    """Too little of the pass is left for the next read and its decode."""


def read_hours(range_fetch: Callable, now: dt.datetime, deadline: Optional[arch.Deadline] = None) -> Optional[dict]:
    """The newest issuance of the hourly temperature, read by range: each
    message's head for its hour, and in full the messages of the AHEAD_HOURS
    hours after the hour the issuance was published in, sampled. Each head is
    read where the last message ended and the message is found in it by its
    "GRIB", past the WMO header before it. The file runs in forecast order, so
    the walk ends at the first hour past those, or at the file's end. None when
    the file is not there. A message that is not an hour's temperature is
    counted and left; a head with no message in it raises; a Last-Modified that
    changes between the reads raises IssuanceChanged; and less than STEP_NEED
    seconds left of the pass before a read raises OutOfTime."""
    def fetch(start: int, end: int, meta: dict):
        if deadline is not None and deadline.over(STEP_NEED):
            raise OutOfTime(f"not enough time left in the pass to read ds.{HOURLY}.bin at byte {start}")
        return range_fetch(u, start, end, meta)

    u = url(HOURLY)
    meta: dict = {}
    head = fetch(0, HEAD_BYTES - 1, meta)
    if not head:
        return None
    lm = meta.get("lastModified")
    issued = lm or _iso(now)
    begun = _parse_iso(issued).replace(minute=0, second=0, microsecond=0)
    last = begun + dt.timedelta(hours=AHEAD_HOURS)
    out = {"schema": SCHEMA, "element": HOURLY, "issued": issued, "lastModified": lm, "ref": None,
           "messages": 0, "skipped": 0, "hours": {}}

    def same(m: dict) -> None:
        if m.get("lastModified") != lm:
            raise IssuanceChanged(f"ds.{HOURLY}.bin was written again during the read: {lm}, then {m.get('lastModified')}")

    off = 0
    while head:
        i = head.find(b"GRIB")
        if i < 0:
            raise ValueError(f"no GRIB2 message in the {len(head)} bytes at byte {off} of ds.{HOURLY}.bin")
        if i > HEAD_BYTES // 2:
            m = {}
            off, head, i = off + i, fetch(off + i, off + i + HEAD_BYTES - 1, m), 0     # a header longer than any yet
            same(m)
        off += i
        h = message_head(head[i:])
        out["messages"] += 1
        if out["messages"] > MAX_MESSAGES:
            raise ValueError(f"more than {MAX_MESSAGES} messages in ds.{HOURLY}.bin")
        temp = h["discipline"] == 0 and (h.get("category"), h.get("number"), h.get("template")) == (0, 0, 0)
        if not temp or "ref" not in h or not math.isfinite(h["hours"]):
            out["skipped"] += 1
        else:
            out["ref"] = out["ref"] or h["ref"].strftime("%Y-%m-%dT%H:%M:%SZ")
            t = h["ref"] + dt.timedelta(hours=h["hours"])
            if t > last:
                break
            if t > begun:
                m: dict = {}
                msg = fetch(off, off + h["length"] - 1, m)
                same(m)
                if not msg or valid_time(msg) != t:
                    raise ValueError(f"the message at byte {off} is not the one its head describes")
                values, own = hour_sample(msg)
                out["hours"][t.strftime("%Y-%m-%dT%H:%M:%SZ")] = {"values": values, "own": own}
        off += h["length"]
        m = {}
        try:
            head = fetch(off, off + HEAD_BYTES - 1, m)
        except urllib.error.HTTPError as e:
            if e.code != 416:
                raise
            break                                   # a range that starts past the end: the file is read through
        if head:
            same(m)
    return out


def hour_archived(store: Storage) -> list:
    """The archived issuances of the hourly temperature, oldest first."""
    return sorted(k for k in store.list(HOUR_ARCHIVE) if k.endswith(".json.gz"))


def _hour_published(key: str) -> dt.datetime:
    return dt.datetime.strptime(key[len(HOUR_ARCHIVE) + 5:-8], "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)


def hour_stamp(t: dt.datetime) -> str:
    """An hour's name, as the analysis lane names its frames and the landing asks for them."""
    return t.strftime("%Y%m%dT%HZ")


def build_hours(store: Storage, now: dt.datetime, keys: list, have: frozenset = frozenset(),
                stats: Optional[dict] = None) -> dict:
    """{stamp: frame} for each hour that has begun, back BUILD_HOURS, that is
    not in `have`, from the newest issuance published before the hour began
    that carries the hour. An issuance published at P carries only the hours
    in (floor(P), floor(P) + AHEAD_HOURS], so an hour's search ends at the
    first issuance older than that reach. The hours are built newest first and
    an older hour can use only issuances published before it, so an issuance
    is let go once the hours reach its publication. The issuances held at once
    are one hour's reach, whatever the archive holds. `stats`, when given,
    gets the issuances loaded and the most held at once."""
    top = now.astimezone(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    pubs = [(k, _hour_published(k)) for k in keys]
    docs: dict = {}
    loaded = held = 0
    out = {}
    for n in range(BUILD_HOURS):
        t = top - dt.timedelta(hours=n)
        for k in [k for k, published in pubs if published >= t and k in docs]:
            del docs[k]                             # published at or after this hour: no older hour can use it
        stamp, valid = hour_stamp(t), t.strftime("%Y-%m-%dT%H:%M:%SZ")
        if stamp in have:
            continue
        reach = t - dt.timedelta(hours=AHEAD_HOURS)
        for k, published in reversed(pubs):
            if published >= t:
                continue
            if published.replace(minute=0, second=0, microsecond=0) < reach:
                break                               # it and every older issuance end before this hour
            if k not in docs:
                docs[k] = _load(store, k) or {}
                loaded += 1
                held = max(held, len(docs))
            doc = docs[k]
            fr = (doc.get("hours") or {}).get(valid)
            if fr:
                out[stamp] = {"schema": SCHEMA, "kind": HOURLY, "valid": valid, "issued": doc.get("issued"),
                              "ref": doc.get("ref"), "asof": _iso(now), "source": SOURCE, "unit": HOUR_UNIT,
                              "scale": HOUR_SCALE, "pitch": PITCH, "cols": COLS, "rows": ROWS,
                              "values": fr["values"], "own": fr.get("own") or {}}
                break
    if stats is not None:
        stats.update(loaded=loaded, held=held)
    return out


def hour_job(store: Storage, now: dt.datetime, range_fetch: Callable, meta_fetch: Callable,
             deadline: Optional[arch.Deadline] = None) -> dict:
    """Archive the hourly temperature's newest issuance when it is new, write
    a frame for each hour begun that has none, keep the index, and drop frames
    older than FRAME_KEEP_DAYS and issuances older than HOUR_KEEP_DAYS."""
    keys = hour_archived(store)
    meta: dict = {}
    if meta_fetch(url(HOURLY), meta) is None:
        raise RuntimeError(f"ds.{HOURLY}.bin is not on NOAA Open Data")
    added, read = False, None
    # an issuance's key is named by its Last-Modified, so one already archived is known without loading it
    fresh = meta.get("lastModified")
    if fresh is None or _stamp(fresh) not in {k[len(HOUR_ARCHIVE) + 5:-8] for k in keys}:
        doc = read_hours(range_fetch, now, deadline)
        if doc:
            read = {"messages": doc["messages"], "skipped": doc["skipped"], "hours": sorted(doc["hours"])}
            key = f"{HOUR_ARCHIVE}temp_{_stamp(doc['issued'])}.json.gz"
            store.put(key, gzip.compress(json.dumps(doc, separators=(",", ":")).encode()), "application/gzip")
            keys = hour_archived(store)
            added = True
    old = (now - dt.timedelta(days=HOUR_KEEP_DAYS)).strftime("%Y%m%dT%H%M%SZ")
    for k in keys:
        if k[len(HOUR_ARCHIVE) + 5:-8] < old:
            store.delete(k)
    keys = [k for k in keys if k[len(HOUR_ARCHIVE) + 5:-8] >= old]
    raw = store.get(HOUR_PREFIX + "index.json")
    hours = dict((json.loads(raw) if raw else {}).get("hours") or {})
    top = now.astimezone(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    window = {hour_stamp(top - dt.timedelta(hours=n)) for n in range(BUILD_HOURS)}
    frames = build_hours(store, now, keys, frozenset(s for s in hours if s in window))
    for stamp, fr in frames.items():
        store.put(f"{HOUR_PREFIX}{stamp}.json", json.dumps(fr, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
        hours[stamp] = {"issued": fr["issued"], "ref": fr["ref"]}
    # frames kept as long as the analysis lane keeps its own, by their names as its prune reads them, so a frame
    # the index lost is pruned too
    cutoff = f"{now - dt.timedelta(days=FRAME_KEEP_DAYS):%Y%m%dT%HZ}"
    pruned = 0
    for key in store.list(HOUR_PREFIX):
        stamp = key[len(HOUR_PREFIX):-len(".json")]
        if len(stamp) == len(cutoff) and stamp < cutoff and key.endswith(".json"):
            store.delete(key)
            pruned += 1
    hours = {s: v for s, v in hours.items() if s >= cutoff}
    index = {"schema": SCHEMA, "asof": _iso(now), "source": SOURCE, "hours": dict(sorted(hours.items()))}
    store.put(HOUR_PREFIX + "index.json", json.dumps(index, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
    return {"added": added, "issuances": len(keys), "read": read, "written": sorted(frames), "frames": len(hours),
            "pruned": pruned}


def ndfd_job(store: Storage, now: dt.datetime, fetch: Optional[Callable] = None,
             meta_fetch: Optional[Callable] = None, range_fetch: Optional[Callable] = None,
             deadline: Optional[arch.Deadline] = None) -> dict:
    """Archive the newest issuance when it is new, write the four views, and
    drop issuances older than KEEP_DAYS; then the hourly temperature, whose
    failure is the hourly part's alone."""
    fetch = fetch or (lambda u, meta: gw.fetch_bytes(u, timeout=120, meta=meta))
    meta_fetch = meta_fetch or (lambda u, meta: gw.fetch_range(u, 0, 15, meta=meta))
    range_fetch = range_fetch or (lambda u, start, end, meta: gw.fetch_range(u, start, end, tries=READ_TRIES,
                                                                          timeout=READ_TIMEOUT, meta=meta))
    keys = archived(store)
    newest = _load(store, keys[-1]) if keys else None
    fresh = {}
    for el in ELEMENTS:
        meta: dict = {}
        if meta_fetch(url(el), meta) is None:
            raise RuntimeError(f"ds.{el}.bin is not on NOAA Open Data")
        fresh[el] = meta.get("lastModified")
    added = False
    if not newest or any(fresh[el] != (newest.get("lastModified") or {}).get(el) for el in ELEMENTS):
        doc = read_issuance(fetch, now)
        if doc:
            key = f"{ARCHIVE}conus_{_stamp(doc['issued'])}.json.gz"
            store.put(key, gzip.compress(json.dumps(doc, separators=(",", ":")).encode()), "application/gzip")
            keys = archived(store)
            added = True
    old = (now - dt.timedelta(days=KEEP_DAYS)).strftime("%Y%m%dT%H%M%SZ")
    for k in keys:
        if k[len(ARCHIVE) + 6:-8] < old:
            store.delete(k)
    keys = [k for k in keys if k[len(ARCHIVE) + 6:-8] >= old]
    views = build_views(store, now, keys)
    for view, snap in views.items():
        store.put(f"{PREFIX}{view}.json", json.dumps(snap, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
    out = {"added": added, "issuances": len(keys), "views": {v: bool(s["b64"]) for v, s in views.items()}}
    # the hourly temperature after the four views, so that its failure leaves them written; the pass's streak
    # counts it (ndfd_pass)
    try:
        out["hours"] = hour_job(store, now, range_fetch, meta_fetch, deadline)
    except Exception as e:  # noqa: BLE001 - in the status line and the hourly part's streak, never the pass's failure
        out["hoursError"] = f"{type(e).__name__}: {e}"
    return out


def ndfd_pass(cfg: dict, store: Storage) -> int:
    gw.set_user_agent(cfg.get("user_agent", ""))
    now = dt.datetime.now(dt.timezone.utc)
    t0 = time.time()
    deadline = arch.Deadline(arch.remaining_budget(cfg))
    try:
        out = ndfd_job(store, now, deadline=deadline)
        failed = 0
    except Exception as e:  # noqa: BLE001 - a missed pass leaves the last views standing
        out = {"error": f"{type(e).__name__}: {e}"}
        failed = 1
    # The hourly temperature never fails the pass, which would page and have Lambda run it twice more for a read a
    # later pass makes good. Its failure is in this line and in its own streak, which raises the alarm once
    # FAIL_STREAK_ALARM passes in a row have failed, as the SST and advisory lanes do. A pass whose four views failed
    # never reached the hourly part, and leaves the streak as it is
    alarms: list = []
    if "hours" in out or "hoursError" in out:
        try:
            health = arch.update_health(store, {"ndfd-temp": {"ok": "hoursError" not in out, "error": out.get("hoursError")}},
                                        now, key=HOUR_HEALTH_KEY)
            out["hoursFailStreak"] = (health.get("ndfd-temp") or {}).get("fail_streak", 0)
            if out["hoursFailStreak"] >= FAIL_STREAK_ALARM:
                alarms = ["ndfd-temp"]
        except Exception as e:  # noqa: BLE001 - a streak that cannot be kept is the job's own failure
            out["healthError"] = f"{type(e).__name__}: {e}"
            failed = 1
    out.update({"kind": "ndfd", "seconds": round(time.time() - t0, 1)})
    arch.LAST_STATUS = {"job": "ndfd", "errors": failed + (1 if out.get("hoursError") else 0), "alarms": alarms}
    print(json.dumps(out))
    return failed
