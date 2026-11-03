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
"""
from __future__ import annotations
import base64
import datetime as dt
import gzip
import json
import math
import struct
import time
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from . import archive as arch
from . import basemap
from . import grib2
from . import gov_weather as gw
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


def ndfd_job(store: Storage, now: dt.datetime, fetch: Optional[Callable] = None,
             meta_fetch: Optional[Callable] = None) -> dict:
    """Archive the newest issuance when it is new, write the four views, and
    drop issuances older than KEEP_DAYS."""
    fetch = fetch or (lambda u, meta: gw.fetch_bytes(u, timeout=120, meta=meta))
    meta_fetch = meta_fetch or (lambda u, meta: gw.fetch_range(u, 0, 15, meta=meta))
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
    return {"added": added, "issuances": len(keys), "views": {v: bool(s["b64"]) for v, s in views.items()}}


def ndfd_pass(cfg: dict, store: Storage) -> int:
    gw.set_user_agent(cfg.get("user_agent", ""))
    now = dt.datetime.now(dt.timezone.utc)
    t0 = time.time()
    try:
        out = ndfd_job(store, now)
        errors = 0
    except Exception as e:  # noqa: BLE001 - a missed pass leaves the last views standing
        out = {"error": f"{type(e).__name__}: {e}"}
        errors = 1
    out.update({"kind": "ndfd", "seconds": round(time.time() - t0, 1)})
    arch.LAST_STATUS = {"job": "ndfd", "errors": errors, "alarms": []}
    print(json.dumps(out))
    return errors
