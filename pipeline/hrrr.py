"""
hrrr.py — the HRRR lane: NOAA's High-Resolution Rapid Refresh, read at the
grid point nearest each station, for the city page (owner's decision
2026-10-16).

The HRRR is NOAA's 3 km model. It runs every hour with the newest radar and
surface data, and that is why it is on the page: high resolution and a fast
response to new information. An hourly run reaches 18 hours and the 00, 06,
12 and 18Z runs reach 48. The page draws the newest run for its hours and
carries it on to the end of the next day with the newest 48-hour run, the way
the blend's hourly bulletin is carried on by its 3-hourly one. Each run's
station values are kept as read, so the page can draw the run that stood
before the day began, open the day after in the postmortem, and score it
later.

Data: NOAA Open Data Dissemination, bucket noaa-hrrr-bdp-pds, the 2-D surface
file hrrr.tHHz.wrfsfcfFF.grib2 of each run and its .idx sidecar, read by
range. Six messages an hour: temperature and dewpoint at 2 m, the two wind
components at 10 m, the surface gust and the total cloud cover. Decoded by
pipeline/grib2.py, which reads the station cells of a complex-packed field
without holding the field.

The value at a station is the grid point nearest it, as the file holds it, so
it can be checked against NOAA's file directly (owner's decision 2026-10-16).
A station further than MAX_POINT_KM from every grid point is off the grid and
gets no HRRR: Honolulu and the stations abroad.

The HRRR writes its winds along the grid's axes. They are turned to true
north before the direction is stored, by the angle between the grid's y axis
and the meridian at the point, which on a Lambert grid is the cone constant
times the longitude from the orientation meridian (NCEP's rotation). Speed
does not change with the turn.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import math
import time
from typing import Optional

from . import archive as arch
from . import basemap
from . import gov_weather as gw
from . import grib2
from .storage import Storage

FILE = "hrrr.{ymd}/conus/hrrr.t{hh}z.wrfsfcf{ff:02d}.grib2"
RUN_PREFIX = "archive/hrrr/run_"
HOURS_HOURLY = 18
HOURS_LONG = 48
LONG_CYCLES = (0, 6, 12, 18)
# A pass caps itself under the function's 900 s timeout. The newest run comes
# first, then the newest 48-hour run, then any hourly run of the last few
# hours a missed pass left unread, so the run that stood at each station's
# midnight is on file.
PASS_CAP_SECONDS = 780
HOUR_SECONDS = 20               # kept in hand before starting another forecast hour
BACKFILL_RUNS = 6
LOOKBACK_H = 10                 # how far back a pass looks for a published run
MAX_MESSAGE_BYTES = 16 * 1024 * 1024
MAX_POINT_KM = 3.0
MS_TO_KT = 1.9438444924406046
SCHEMA = 1
SOURCE = "NOAA HRRR, 2-D surface files on NOAA Open Data Dissemination (noaa-hrrr-bdp-pds)"
# the row field, the .idx name and level, and the GRIB2 discipline, parameter
# category and number a message must carry whatever the sidecar called it
FIELDS = (("temp", "TMP", "2 m above ground", (0, 0, 0)),
          ("dew", "DPT", "2 m above ground", (0, 0, 6)),
          ("u", "UGRD", "10 m above ground", (0, 2, 2)),
          ("v", "VGRD", "10 m above ground", (0, 2, 3)),
          ("gust", "GUST", "surface", (0, 2, 22)),
          ("sky", "TCDC", "entire atmosphere", (0, 6, 1)))


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def run_hours(cycle: dt.datetime) -> int:
    return HOURS_LONG if cycle.hour in LONG_CYCLES else HOURS_HOURLY


def run_stamp(cycle: dt.datetime) -> str:
    return cycle.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H00Z")


def run_key(cycle: dt.datetime) -> str:
    return f"{RUN_PREFIX}{run_stamp(cycle)}.json.gz"


def stamp_time(stamp: str) -> dt.datetime:
    return dt.datetime.strptime(stamp, "%Y%m%dT%H00Z").replace(tzinfo=dt.timezone.utc)


def file_url(cycle: dt.datetime, ff: int) -> str:
    c = cycle.astimezone(dt.timezone.utc)
    return gw.NODD_HRRR + "/" + FILE.format(ymd=f"{c:%Y%m%d}", hh=f"{c:%H}", ff=ff)


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = math.pi / 180.0
    a = (math.sin((lat2 - lat1) * r / 2) ** 2
         + math.cos(lat1 * r) * math.cos(lat2 * r) * math.sin((lon2 - lon1) * r / 2) ** 2)
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def station_points(roster: list, grid: dict = grib2.HRRR) -> dict:
    """{station: {"k", "i", "j", "lat", "lon", "km"}} for every station on the
    grid: the grid point nearest it, that point's centre, and how far the
    centre is from the station."""
    out = {}
    for c in roster:
        lat, lon = c.get("lat"), c.get("lon")
        if lat is None or lon is None:
            continue
        cell = grib2.nearest_cell(grid, lat, lon)
        if not cell:
            continue
        i, j, k = cell
        clat, clon = grib2.lcc_latlon(grid, i, j)
        km = _km(lat, lon, clat, clon)
        if km > MAX_POINT_KM:
            continue
        out[c["station"]] = {"k": k, "i": i, "j": j, "lat": round(clat, 5), "lon": round(clon, 5),
                             "km": round(km, 2)}
    return out


def to_earth(u: float, v: float, lon: float, grid: dict = grib2.HRRR) -> tuple:
    """u and v turned from the grid's axes to east and north at longitude
    lon. The grid's y axis leans from the meridian by the cone constant times
    the longitude from the orientation meridian (NCEP's rotation for a
    Lambert grid; west of that meridian true north leans toward +x)."""
    n = grib2._cone(grid)[0]
    dlon = (lon - grid["lov"] + 180.0) % 360.0 - 180.0
    a = math.radians(n * dlon)
    return math.cos(a) * u + math.sin(a) * v, -math.sin(a) * u + math.cos(a) * v


def wind_from(ue: float, ve: float) -> tuple:
    """Speed and the direction the wind blows from, in degrees true."""
    return math.hypot(ue, ve), (math.degrees(math.atan2(-ue, -ve)) + 360.0) % 360.0


def _k_to_f(k: float) -> float:
    return round((k - 273.15) * 9.0 / 5.0 + 32.0, 1)


def check_message(msg: bytes, param: tuple, cycle: dt.datetime, ff: int) -> Optional[str]:
    """Why a message is not the field and hour wanted, or None: the parameter,
    the HRRR grid, the run and the forecast hour must all match, so a misfiled
    message is refused rather than read as another field or another hour."""
    try:
        if not grib2.same_grid(grib2.grid_def(msg), grib2.HRRR):
            return "message is on another grid"
        pd = grib2.product_def(msg)
    except Exception as e:  # noqa: BLE001
        return f"unreadable ({type(e).__name__}: {e})"
    got = (pd.get("discipline"), pd.get("category"), pd.get("number"))
    if got != param:
        return f"parameter {got}, not {param}"
    rt = grib2.ref_time(msg)
    c = cycle.astimezone(dt.timezone.utc)
    if tuple(rt[:4]) != (c.year, c.month, c.day, c.hour):
        return f"run {tuple(rt[:4])}, not {(c.year, c.month, c.day, c.hour)}"
    if pd.get("forecast_hours") is not None and abs(pd["forecast_hours"] - ff) > 1e-6:
        return f"forecast hour {pd['forecast_hours']}, not {ff}"
    return None


def read_hour(cycle: dt.datetime, ff: int, points: dict) -> Optional[tuple]:
    """The stations' values at forecast hour ff of one run, as ({station:
    row}, the file's Last-Modified), or None while the hour is not published."""
    url = file_url(cycle, ff)
    meta: dict = {}
    idx = gw.fetch_bytes(url + ".idx", meta=meta)
    if idx is None:
        return None
    entries = grib2.parse_idx(idx.decode("ascii", "replace"))
    step = "anl" if ff == 0 else f"{ff} hour fcst"
    sids = sorted(points)
    ks = [points[s]["k"] for s in sids]
    vals, grid_rel = {}, True
    for name, var, level, param in FIELDS:
        hit = [e for e in entries if e["var"] == var and e["level"] == level and e["step"] == step]
        if len(hit) != 1:
            raise ValueError(f"{url}: {var} {level} {step} listed {len(hit)} times")
        e = hit[0]
        end = e["end"] if e["end"] is not None else e["start"] + MAX_MESSAGE_BYTES
        msg = gw.fetch_range(url, e["start"], end)
        if msg is None:
            return None
        why = check_message(msg, param, cycle, ff)
        if why:
            raise ValueError(f"{url} {var}: {why}")
        vals[name] = grib2.decode_points(msg, ks)
        if name == "u":
            grid_rel = grib2.uv_grid_relative(msg)
    valid = _iso(cycle + dt.timedelta(hours=ff))
    rows = {}
    for n, sid in enumerate(sids):
        u, v = vals["u"][n], vals["v"][n]
        if grid_rel:
            u, v = to_earth(u, v, points[sid]["lon"])
        spd, wdir = wind_from(u, v)
        rows[sid] = {"t": valid, "tempF": _k_to_f(vals["temp"][n]), "dewF": _k_to_f(vals["dew"][n]),
                     "sky": int(round(vals["sky"][n])), "wdir": int(round(wdir)) % 360,
                     "wspd": round(spd * MS_TO_KT, 1), "gust": round(vals["gust"][n] * MS_TO_KT, 1)}
    return rows, meta.get("lastModified")


def read_run(store: Storage, key: str) -> Optional[dict]:
    raw = store.get(key)
    if not raw:
        return None
    try:
        return json.loads(gzip.decompress(raw))
    except (OSError, ValueError):
        return None


def _write(store: Storage, key: str, doc: dict) -> None:
    for sid in doc["stations"]:
        doc["stations"][sid].sort(key=lambda r: r["t"])
    doc["hours"] = sorted(set(doc["hours"]))
    doc["complete"] = len(doc["hours"]) >= doc["expected"]
    store.put(key, gzip.compress(json.dumps(doc, separators=(",", ":")).encode()), "application/json")


def process_run(store: Storage, cycle: dt.datetime, points: dict, deadline, status: dict) -> bool:
    """Read the hours of one run that are not on file yet, writing as it goes
    so a pass cut short keeps what it read. Returns True when it stopped for
    time, False when it reached the run's end or its first unpublished hour."""
    key = run_key(cycle)
    doc = read_run(store, key) or {
        "schema": SCHEMA, "cycle": run_stamp(cycle), "expected": run_hours(cycle), "hours": [],
        "points": points, "stations": {}, "files": {}, "source": SOURCE}
    if doc.get("complete"):
        return False
    done, fresh = set(doc["hours"]), 0
    for ff in range(1, doc["expected"] + 1):
        if ff in done:
            continue
        if deadline.over(HOUR_SECONDS):
            status["stopped"] = f"{doc['cycle']} f{ff:02d}"
            if fresh:
                _write(store, key, doc)
            return True
        got = read_hour(cycle, ff, points)
        if got is None:
            status["waiting"].append(f"{doc['cycle']} f{ff:02d}")
            break
        rows, lm = got
        for sid, r in rows.items():
            doc["stations"].setdefault(sid, []).append(r)
        doc["hours"].append(ff)
        doc["files"][str(ff)] = lm
        status["hours"] += 1
        fresh += 1
        if fresh % 6 == 0:
            _write(store, key, doc)
    if fresh:
        doc["written"] = _iso(dt.datetime.now(dt.timezone.utc))
        _write(store, key, doc)
        status["runs"].append(doc["cycle"])
    return False


def published(cycle: dt.datetime, ff: int) -> bool:
    return gw.fetch_bytes(file_url(cycle, ff) + ".idx") is not None


def candidates(now: dt.datetime) -> list:
    """The runs a pass should hold, newest first: the newest run with a
    first hour published, the newest 48-hour run likewise, and the hourly
    runs of the last BACKFILL_RUNS hours."""
    top = now.astimezone(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    hours = [top - dt.timedelta(hours=h) for h in range(0, LOOKBACK_H + 1)]
    newest = next((c for c in hours if published(c, 1)), None)
    if newest is None:
        return []
    out = [newest]
    long_run = next((c for c in hours if c.hour in LONG_CYCLES and c <= newest and published(c, 1)), None)
    if long_run is not None and long_run not in out:
        out.append(long_run)
    out += [newest - dt.timedelta(hours=h) for h in range(1, BACKFILL_RUNS + 1)]
    seen, keep = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            keep.append(c)
    return keep


def hrrr_pass(cfg: dict, store: Storage, now: Optional[dt.datetime] = None) -> int:
    """Entry point. Returns 1 only when the pass found nothing published and
    could read nothing at all, so the scheduler flags an outage."""
    t0 = time.time()
    now = now or dt.datetime.now(dt.timezone.utc)
    gw.set_user_agent(cfg.get("user_agent", ""))
    budget = arch.remaining_budget(cfg)
    deadline = arch.Deadline(min(budget, PASS_CAP_SECONDS) if budget else PASS_CAP_SECONDS)
    status = {"kind": "hrrr", "runs": [], "hours": 0, "waiting": [], "errors": [], "stopped": None}
    points = station_points(basemap.load_roster())
    runs = []
    try:
        runs = candidates(now)
    except Exception as e:  # noqa: BLE001
        status["errors"].append(f"listing: {type(e).__name__}: {e}")
    for cycle in runs:
        try:
            if process_run(store, cycle, points, deadline, status):
                break
        except Exception as e:  # noqa: BLE001
            status["errors"].append(f"{run_stamp(cycle)}: {type(e).__name__}: {e}")
    status["seconds"] = round(time.time() - t0, 1)
    status["stations"] = len(points)
    arch.LAST_STATUS = {"job": "hrrr", "errors": len(status["errors"]), "alarms": []}
    print(json.dumps(status))
    return 1 if (not runs and status["errors"]) else 0


# ------------------------------------------------------------------ for the snapshots
_CACHE: dict = {}


def reset_cache() -> None:
    """Called at the start of a snapshot pass: run files grow while a run is
    read, so a container's earlier reading is not reused."""
    _CACHE.clear()


def recent_keys(store: Storage, now: dt.datetime, hours: int = 72) -> list:
    since = RUN_PREFIX + run_stamp(now - dt.timedelta(hours=hours))
    return [k for k in store.list(RUN_PREFIX, start_after=since) if k.endswith(".json.gz")]


def _doc(store: Storage, key: str) -> Optional[dict]:
    if key not in _CACHE:
        _CACHE[key] = read_run(store, key)
    return _CACHE[key]


def _stamp_of(key: str) -> str:
    return key[len(RUN_PREFIX):].split(".")[0]


def station_series(store: Storage, sid: str, keys: list, cut: Optional[str] = None,
                   fields: tuple = ("tempF",)) -> Optional[dict]:
    """The HRRR as the page draws it for one station, as it stood at the cut
    (a stamp, YYYYMMDDTHHMMSSZ or the run form) or now: the newest run on file
    at or before it for its own hours, then the newest 48-hour runs at or
    before it for the hours past those, each contributing only hours later
    than any already covered. None when no run carries the station."""
    usable = [k for k in keys if cut is None or _stamp_of(k) <= cut[:11] + "00Z"]
    if not usable:
        return None
    rows, cycle, ext, ext_cycle = None, None, [], None
    for key in reversed(usable):
        d = _doc(store, key)
        if not d or not (d.get("stations") or {}).get(sid):
            continue
        if rows is None:
            rows, cycle = d["stations"][sid], d["cycle"]
            continue
        if stamp_time(d["cycle"]).hour not in LONG_CYCLES:
            continue
        end = (ext or rows)[-1]["t"] if (ext or rows) else ""
        more = [r for r in d["stations"][sid] if r["t"] > end]
        if more:
            ext += more
            ext_cycle = ext_cycle or d["cycle"]
        if stamp_time(d["cycle"]) <= stamp_time(cycle) - dt.timedelta(hours=HOURS_LONG):
            break
    if rows is None:
        return None
    keep = ("t",) + tuple(fields)
    pick = lambda rs: [{k: r.get(k) for k in keep} for r in rs]  # noqa: E731
    return {"cycle": cycle, "rows": pick(rows), "extCycle": ext_cycle, "ext": pick(ext)}
