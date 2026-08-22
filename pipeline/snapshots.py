"""
snapshots.py — the small JSON files the browser reads.

Three jobs. Each reads the archive (never a government endpoint, except the
observation job's own fresh pull) and writes objects under snapshots/ with a
short Cache-Control so the CDN serves them fresh but keeps serving the last
one if a job dies. Every object carries `asof`, the time it was written, and
the cycle times of the forecasts it contains, so a page can always say how
old what it shows is.

  obs       every 10 min   pulls the last hours of METARs for every station,
                           upserts the archive's observation record, then writes
                           snapshots/obs/{STATION}.json, snapshots/summary.json
                           and snapshots/manifest.json
  forecast  every 30 min   after the archive job: reads the newest NWS, NBM,
                           LAMP and MAV cycles per station from the archive,
                           plus the as-issued cycles for today and yesterday,
                           and writes snapshots/forecast/{STATION}.json and
                           snapshots/field.json
  (hurricane lives in hurricane.py)

Day bucketing is always through the station's IANA zone. api.weather.gov
period times carry a LOCAL offset while aviationweather.gov times are Zulu;
adding a fixed offset double-shifts the former and mis-buckets the hours
around a DST transition. Joins between series go through UTC instants.

The contract day is the local calendar day in progress at the station. The
window a city chart shows runs from noon the day before to the end of the
contract day; the "as issued" forecast for the day is the latest archived
cycle at or before local midnight, or, when the archive holds nothing that
old for the station, the earliest cycle it has, labelled as such.
"""
from __future__ import annotations
import datetime as dt
import gzip
import json
import math
import time
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from . import gov_weather as gw
from . import archive as arch
from . import basemap
from .cities import CITIES
from .storage import Storage

SNAP_CACHE = "public, max-age=60, stale-while-revalidate=300, stale-if-error=86400"
OBS_HOURS = 72                    # what the city chart's window plus the yesterday overlay need
OBS_PULL_HOURS = 6                # fresh pull per pass; deeper history comes from the archive record
SCHEMA = 1


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_iso(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _stamp_of(key: str) -> str:
    """'archive/KLAX/hourly_20260821T195218Z.json.gz' -> '20260821T195218Z'."""
    base = key.rsplit("/", 1)[-1]
    return base.split("_", 1)[1].split(".", 1)[0]


def _read_gz(store: Storage, key: str) -> Optional[bytes]:
    raw = store.get(key)
    return gzip.decompress(raw) if raw else None


def local_day_key(t: dt.datetime, tz) -> str:
    return t.astimezone(tz).date().isoformat()


def sun_times(lat: float, lon: float, d: dt.date):
    """Sunrise and sunset (UTC) for local date d, NOAA's approximation, within
    a few minutes. Used only to shade the chart, never for settlement."""
    n = d.toordinal() - dt.date(2000, 1, 1).toordinal()
    jstar = n - lon / 360.0
    M = math.radians((357.5291 + 0.98560028 * jstar) % 360)
    Cc = 1.9148 * math.sin(M) + 0.02 * math.sin(2 * M) + 0.0003 * math.sin(3 * M)
    lam = math.radians((math.degrees(M) + Cc + 180 + 102.9372) % 360)
    jtransit = jstar + 0.0053 * math.sin(M) - 0.0069 * math.sin(2 * lam)
    delta = math.asin(math.sin(lam) * math.sin(math.radians(23.44)))
    cosw = ((math.sin(math.radians(-0.83)) - math.sin(math.radians(lat)) * math.sin(delta))
            / (math.cos(math.radians(lat)) * math.cos(delta)))
    if not -1 <= cosw <= 1:
        return None, None
    w = math.degrees(math.acos(cosw)) / 360.0
    base = dt.datetime(2000, 1, 1, 12, tzinfo=dt.timezone.utc)
    return base + dt.timedelta(days=jtransit - w), base + dt.timedelta(days=jtransit + w)


def day_markers(city: dict, now: dt.datetime) -> dict:
    """The contract day's frame for one station, all instants as UTC ISO."""
    tz = ZoneInfo(city["tz"])
    local_now = now.astimezone(tz)
    D = local_now.date()
    day_start = dt.datetime.combine(D, dt.time(0), tzinfo=tz)
    day_end = dt.datetime.combine(D + dt.timedelta(days=1), dt.time(0), tzinfo=tz)
    win_start = dt.datetime.combine(D - dt.timedelta(days=1), dt.time(12), tzinfo=tz)
    yday_start = dt.datetime.combine(D - dt.timedelta(days=1), dt.time(0), tzinfo=tz)
    # daily contracts for local day D list at noon Eastern the day before; a
    # marker for the market overlay, drawn only when the overlay is on
    listed = dt.datetime.combine(D - dt.timedelta(days=1), dt.time(12), tzinfo=ZoneInfo("America/New_York"))
    sr, ss = sun_times(city["lat"], city["lon"], D)
    return {"day": D.isoformat(), "tomorrow": (D + dt.timedelta(days=1)).isoformat(),
            "yesterday": (D - dt.timedelta(days=1)).isoformat(),
            "tzOffset": tz.utcoffset(local_now).total_seconds() / 3600.0,
            "winStart": _iso(win_start), "dayStart": _iso(day_start), "dayEnd": _iso(day_end),
            "ydayStart": _iso(yday_start), "listed": _iso(listed),
            "sunrise": _iso(sr) if sr else None, "sunset": _iso(ss) if ss else None}


# ================================================================ observations
def decode_rows(raw_rows: list, tz) -> list:
    """Archive rows for one station -> chart rows, applying the two decode
    constants. `src` records whether tenths were available for the row."""
    out = []
    for ob in raw_rows:
        if ob.get("temp") is None or ob.get("obsTime") is None:
            continue
        if not gw.INCLUDE_SPECI and ob.get("metarType") == "SPECI":
            continue
        temp_c = ob["temp"] if gw.TEMP_SOURCE == "remarks" else gw._body_temp_c(ob.get("rawOb", "") or "")
        if temp_c is None:
            continue
        t = dt.datetime.fromtimestamp(ob["obsTime"], dt.timezone.utc)
        out.append({"t": _iso(t), "tempF": round(gw.c_to_f(temp_c), 1), "tempC": round(float(temp_c), 1),
                    "type": ob.get("metarType"), "src": ob.get("temp_source")})
    out.sort(key=lambda r: r["t"])
    return out


def day_extremes(rows: list, tz, day: str, unit: str) -> Optional[dict]:
    key = "tempF" if unit == "F" else "tempC"
    day_rows = [r for r in rows if local_day_key(_parse_iso(r["t"]), tz) == day]
    if not day_rows:
        return None
    hi = max(day_rows, key=lambda r: r[key])
    lo = min(day_rows, key=lambda r: r[key])
    return {"date": day, "n": len(day_rows),
            "high": {"v": hi[key], "t": hi["t"]}, "low": {"v": lo[key], "t": lo["t"]}}


def load_obs_record(store: Storage, days: list) -> dict:
    """Rows from the archive's UTC-day files, merged, keyed 'SID|obsTime'."""
    rows: dict = {}
    for day in days:
        raw = _read_gz(store, f"archive/obs/{day}.json.gz")
        if raw:
            rows.update(json.loads(raw).get("rows", {}))
    return rows


def obs_job(cfg: dict, store: Storage, log: Callable, now: dt.datetime) -> dict:
    stations = [c[0] for c in CITIES]
    roster = {c["station"]: c for c in basemap.load_roster()}

    # 1. fresh pull, upserted into the archive's observation record. The
    #    window reaches back to the newest observation on record, so a gap
    #    (first run, or the job was down) heals itself up to OBS_HOURS;
    #    aviationweather.gov holds 30 days, and longer gaps are for
    #    scripts/backfill_obs.py.
    errors = arch.archive_observations(store, stations, now, log, min_hours=OBS_PULL_HOURS, max_hours=OBS_HOURS)

    # 2. the last OBS_HOURS from the record, per station
    days = [(now - dt.timedelta(hours=h)).strftime("%Y%m%d") for h in range(0, OBS_HOURS + 24, 24)]
    record = load_obs_record(store, sorted(set(days)))
    since = now - dt.timedelta(hours=OBS_HOURS)
    by_station: dict = {}
    for k, ob in record.items():
        sid = k.split("|", 1)[0]
        if ob.get("obsTime") is not None and dt.datetime.fromtimestamp(ob["obsTime"], dt.timezone.utc) >= since:
            by_station.setdefault(sid, []).append(ob)

    summary_obs = {}
    for sid in stations:
        c = roster.get(sid)
        if not c:
            continue
        tz = ZoneInfo(c["tz"])
        rows = decode_rows(by_station.get(sid, []), tz)
        mk = day_markers(c, now)
        latest = by_station.get(sid, [])
        latest = max(latest, key=lambda o: o["obsTime"]) if latest else None
        snap = {
            "schema": SCHEMA, "station": sid, "city": c["city"], "unit": c["unit"], "tz": c["tz"],
            "asof": _iso(now),
            "decode": {"TEMP_SOURCE": gw.TEMP_SOURCE, "INCLUDE_SPECI": gw.INCLUDE_SPECI},
            "source": "aviationweather.gov METAR (hourly reports; SPECI where counted)",
            "rows": rows,
            "today": day_extremes(rows, tz, mk["day"], c["unit"]),
            "yesterday": day_extremes(rows, tz, mk["yesterday"], c["unit"]),
            "latest": ({"t": _iso(dt.datetime.fromtimestamp(latest["obsTime"], dt.timezone.utc)),
                        "raw": latest.get("rawOb", ""), "type": latest.get("metarType"),
                        "src": latest.get("temp_source")} if latest else None),
        }
        store.put(f"snapshots/obs/{sid}.json", json.dumps(snap, separators=(",", ":")).encode(),
                  "application/json", SNAP_CACHE)
        summary_obs[sid] = {"today": snap["today"], "yesterday": snap["yesterday"], "latest": snap["latest"],
                            "n72h": len(rows)}
    log(kind="obs-snapshots", stations=len(summary_obs))
    return {"errors": errors, "obs": summary_obs}


# ================================================================ forecasts
def _latest(store: Storage, sid: str, kind: str) -> Optional[str]:
    keys = store.list(f"archive/{sid}/{kind}_")
    return keys[-1] if keys else None


def _as_issued_key(store: Storage, sid: str, kind: str, cutoff_stamp: str, keys: Optional[list] = None):
    """Latest archived cycle at or before the cutoff, else the earliest held.
    Returns (key, pre_day)."""
    keys = keys if keys is not None else store.list(f"archive/{sid}/{kind}_")
    if not keys:
        return None, False
    pre = [k for k in keys if _stamp_of(k) <= cutoff_stamp]
    return (pre[-1], True) if pre else (keys[0], False)


def _cutoff(iso_utc: str) -> str:
    return _parse_iso(iso_utc).astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _nws_hourly_rows(body: dict) -> list:
    rows = []
    for p in body["properties"]["periods"]:
        f = gw.period_temp_f(p)          # both schema shapes; a null temperature skips the period
        if f is None:
            continue
        rows.append({"t": _iso(_parse_iso(p["startTime"])), "tempF": f})
    return rows


def _nws_daily_rows(body: dict) -> list:
    rows = []
    for p in body["properties"]["periods"]:
        f = gw.period_temp_f(p)
        if f is None:
            continue
        rows.append({"start": _iso(_parse_iso(p["startTime"])), "end": _iso(_parse_iso(p["endTime"])),
                     "isDay": bool(p.get("isDaytime")), "tempF": f, "name": p.get("name")})
    return rows


def _official_hi_lo(daily_rows: list, tz) -> tuple:
    """Official high = the daytime period of the local day; official low = the
    night period that ENDS on the local day (a calendar-day low usually
    happens in the morning, inside the period that began the prior evening)."""
    hi, lo = {}, {}
    for per in daily_rows:
        if per["isDay"]:
            hi[local_day_key(_parse_iso(per["start"]), tz)] = per["tempF"]
        else:
            lo[local_day_key(_parse_iso(per["end"]), tz)] = per["tempF"]
    return hi, lo


def _hourly_rows(parsed: dict, unit: str) -> list:
    return [{"t": _iso(r["time"]), "tempF": r["temp_f"], "tempC": round((r["temp_f"] - 32) * 5 / 9, 1)}
            for r in parsed["rows"]]


def _extremes_by_day(parsed: dict, tz) -> dict:
    """{local day: {"max": v, "min": v}} from a TXN / N/X row, converting each
    column time to the station's zone (see parse_daily_extremes)."""
    out: dict = {}
    for e in parsed["extremes"]:
        d = local_day_key(e["time"], tz)
        out.setdefault(d, {})[e["kind"]] = e["temp_f"]
    return out


def _max_min_in_day(rows: list, tz, day: str) -> tuple:
    vals = [r["tempF"] for r in rows if local_day_key(_parse_iso(r["t"]), tz) == day]
    return (round(max(vals), 1), round(min(vals), 1)) if vals else (None, None)


def _u(v, unit: str):
    """A Fahrenheit level in the station's native unit. International
    contracts quote Celsius, and their pages show Celsius directly rather
    than a converted Fahrenheit number."""
    if v is None:
        return None
    return round((v - 32) * 5 / 9, 1) if unit == "C" else v


def _levels_by_day(extremes_by_day: dict, unit: str) -> dict:
    return {d: {k: _u(v, unit) for k, v in kv.items()} for d, kv in extremes_by_day.items()}


def _as_issued_levels(store: Storage, kind: str, key: Optional[str], c: dict, tz, day: str) -> dict:
    """The forecast high and low FOR the contract day from the cycle that was
    standing before it began: the official day/night product for NWS, the TXN
    row for NBM, the N/X row for MAV, the max and min of the hourly rows for
    LAMP. This is what the city chart draws as each source's level for the
    day and what the scorecard scores."""
    if not key:
        return {}
    raw = _read_gz(store, key)
    if kind == "daily":
        hi, lo = _official_hi_lo(_nws_daily_rows(json.loads(raw)), tz)
        return {"highToday": _u(hi.get(day), c["unit"]), "lowToday": _u(lo.get(day), c["unit"])}
    text = raw.decode("ascii", "replace")
    if kind in ("nbs", "mav"):
        parsed = gw.parse_nbs_block(text) if kind == "nbs" else gw.parse_mav_block(text)
        byday = _extremes_by_day(parsed, tz).get(day, {})
        return {"highToday": _u(byday.get("max"), c["unit"]), "lowToday": _u(byday.get("min"), c["unit"])}
    rows = _hourly_rows(gw.parse_hourly_block(text), c["unit"])
    h, l = _max_min_in_day(rows, tz, day)
    return {"highToday": _u(h, c["unit"]), "lowToday": _u(l, c["unit"]), "fromHourly": True}


def forecast_job(cfg: dict, store: Storage, log: Callable, now: dt.datetime) -> dict:
    roster = basemap.load_roster()
    grid = basemap.load_field_grid()
    written = 0
    summary_fc = {}
    for c in roster:
        sid, tz = c["station"], ZoneInfo(c["tz"])
        mk = day_markers(c, now)
        keys = store.list(f"archive/{sid}/")
        by_kind: dict = {}
        for k in keys:
            base = k.rsplit("/", 1)[-1]
            by_kind.setdefault(base.split("_", 1)[0], []).append(k)
        snap = {"schema": SCHEMA, "station": sid, "city": c["city"], "unit": c["unit"], "tz": c["tz"],
                "asof": _iso(now), "markers": mk, "nws": None, "nbm": None, "lamp": None, "mav": None,
                "asIssued": {}, "yesterday": {}}
        try:
            # ---- NWS: the standing hourly forecast and the official day/night highs and lows
            if by_kind.get("hourly"):
                body = json.loads(_read_gz(store, by_kind["hourly"][-1]))
                hourly = _nws_hourly_rows(body)
                daily_rows = []
                if by_kind.get("daily"):
                    daily_rows = _nws_daily_rows(json.loads(_read_gz(store, by_kind["daily"][-1])))
                hi, lo = _official_hi_lo(daily_rows, tz)
                h_today, l_today = _max_min_in_day(hourly, tz, mk["day"])
                h_tmw, l_tmw = _max_min_in_day(hourly, tz, mk["tomorrow"])
                snap["nws"] = {"cycle": _stamp_of(by_kind["hourly"][-1]), "hourly": hourly, "daily": daily_rows,
                               "highToday": hi.get(mk["day"], h_today), "lowToday": lo.get(mk["day"], l_today),
                               "highTomorrow": hi.get(mk["tomorrow"], h_tmw), "lowTomorrow": lo.get(mk["tomorrow"], l_tmw),
                               "officialToday": mk["day"] in hi, "officialTomorrow": mk["tomorrow"] in hi}
            # ---- NBM: hourly from NBH, the blend's own daily max/min from NBS
            if by_kind.get("nbh"):
                parsed = gw.parse_hourly_block(_read_gz(store, by_kind["nbh"][-1]).decode("ascii", "replace"))
                rows = _hourly_rows(parsed, c["unit"])
                nbm = {"cycle": _iso(parsed["cycle"]), "hourly": rows}
                txn = {}
                if by_kind.get("nbs"):
                    nbs = gw.parse_nbs_block(_read_gz(store, by_kind["nbs"][-1]).decode("ascii", "replace"))
                    txn = _extremes_by_day(nbs, tz)
                    nbm["nbsCycle"] = _iso(nbs["cycle"])
                    nbm["txn"] = txn
                h, l = _max_min_in_day(rows, tz, mk["day"])
                txn_u = _levels_by_day(txn, c["unit"])
                nbm["txn"] = txn_u
                nbm["highToday"] = txn_u.get(mk["day"], {}).get("max", _u(h, c["unit"]))
                nbm["lowToday"] = txn_u.get(mk["day"], {}).get("min", _u(l, c["unit"]))
                nbm["highTomorrow"] = txn_u.get(mk["tomorrow"], {}).get("max")
                nbm["lowTomorrow"] = txn_u.get(mk["tomorrow"], {}).get("min")
                snap["nbm"] = nbm
            # ---- LAMP: the same-day hourly trace
            if by_kind.get("lamp"):
                parsed = gw.parse_hourly_block(_read_gz(store, by_kind["lamp"][-1]).decode("ascii", "replace"))
                rows = _hourly_rows(parsed, c["unit"])
                h, l = _max_min_in_day(rows, tz, mk["day"])
                snap["lamp"] = {"cycle": _iso(parsed["cycle"]), "hourly": rows,
                                "highToday": _u(h, c["unit"]), "lowToday": _u(l, c["unit"])}
            # ---- GFS MOS MAV: an independent daily max/min, 3-hourly trace
            if by_kind.get("mav"):
                parsed = gw.parse_mav_block(_read_gz(store, by_kind["mav"][-1]).decode("ascii", "replace"))
                nx = _levels_by_day(_extremes_by_day(parsed, tz), c["unit"])
                snap["mav"] = {"cycle": _iso(parsed["cycle"]), "rows": _hourly_rows(parsed, c["unit"]), "nx": nx,
                               "highToday": nx.get(mk["day"], {}).get("max"), "lowToday": nx.get(mk["day"], {}).get("min"),
                               "highTomorrow": nx.get(mk["tomorrow"], {}).get("max"),
                               "lowTomorrow": nx.get(mk["tomorrow"], {}).get("min")}
            # ---- as issued before the day began: the hourly trace from each
            #      source's last pre-day cycle, plus that cycle's level for the
            #      day (NWS official, NBM TXN, MAV N/X, LAMP hourly extremes),
            #      and the same for yesterday
            cut, ycut = _cutoff(mk["dayStart"]), _cutoff(mk["ydayStart"])
            SOURCES = (("nws", "hourly", "daily"), ("nbm", "nbh", "nbs"), ("lamp", "lamp", None), ("mav", "mav", None))
            for label, kind, level_kind in SOURCES:
                ks = by_kind.get(kind, [])
                key, pre = _as_issued_key(store, sid, kind, cut, ks)
                if key:
                    raw = _read_gz(store, key)
                    entry = {"cycle": _stamp_of(key), "preDay": pre}
                    if kind == "hourly":
                        entry["rows"] = _nws_hourly_rows(json.loads(raw))
                    elif kind == "mav":
                        entry["rows"] = _hourly_rows(gw.parse_mav_block(raw.decode("ascii", "replace")), c["unit"])
                    else:
                        entry["rows"] = _hourly_rows(gw.parse_hourly_block(raw.decode("ascii", "replace")), c["unit"])
                    if level_kind:
                        # the level comes from a sibling product (day/night, NBS)
                        # with its own cycles; record which cycle supplied it
                        lkey, lpre = _as_issued_key(store, sid, level_kind, cut, by_kind.get(level_kind, []))
                        entry.update(_as_issued_levels(store, level_kind, lkey, c, tz, mk["day"]))
                        entry["levelCycle"] = _stamp_of(lkey) if lkey else None
                        entry["levelPreDay"] = lpre
                    else:
                        entry.update(_as_issued_levels(store, kind, key, c, tz, mk["day"]))
                    snap["asIssued"][label] = entry
                ykey, ypre = _as_issued_key(store, sid, kind, ycut, ks)
                if ykey and ypre:
                    raw = _read_gz(store, ykey)
                    if kind == "hourly":
                        rows = _nws_hourly_rows(json.loads(raw))
                    elif kind == "mav":
                        rows = _hourly_rows(gw.parse_mav_block(raw.decode("ascii", "replace")), c["unit"])
                    else:
                        rows = _hourly_rows(gw.parse_hourly_block(raw.decode("ascii", "replace")), c["unit"])
                    yentry = {"cycle": _stamp_of(ykey), "rows": rows}
                    if level_kind:
                        lkey, lpre = _as_issued_key(store, sid, level_kind, ycut, by_kind.get(level_kind, []))
                        if lkey and lpre:
                            yentry.update(_as_issued_levels(store, level_kind, lkey, c, tz, mk["yesterday"]))
                    else:
                        yentry.update(_as_issued_levels(store, kind, ykey, c, tz, mk["yesterday"]))
                    snap["yesterday"][label] = yentry
        except Exception as e:
            log(station=sid, kind="forecast", error=f"{type(e).__name__}: {e}")
        store.put(f"snapshots/forecast/{sid}.json", json.dumps(snap, separators=(",", ":")).encode(),
                  "application/json", SNAP_CACHE)
        written += 1
        summary_fc[sid] = {k: (snap[k] and {kk: snap[k].get(kk) for kk in
                                            ("cycle", "highToday", "lowToday", "highTomorrow", "lowTomorrow")})
                           for k in ("nws", "nbm", "lamp", "mav")}
        summary_fc[sid]["asIssued"] = {k: {kk: v.get(kk) for kk in ("cycle", "preDay", "highToday", "lowToday")}
                                      for k, v in snap["asIssued"].items()}

    # ---- the shading field for the national map, from tomorrow's NWS highs and lows
    pts = []
    for c in roster:
        f = summary_fc.get(c["station"], {}).get("nws") or {}
        pts.append({**c, "hi": f.get("highTomorrow"), "lo": f.get("lowTomorrow")})
    field = basemap.idw_field(grid, pts, "hi", "lo")
    field.update({"schema": SCHEMA, "asof": _iso(now), "for": "tomorrow",
                  "note": "derived: inverse-distance interpolation of the listed stations' NWS forecasts, not the NDFD grid"})
    store.put("snapshots/field.json", json.dumps(field, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
    log(kind="forecast-snapshots", stations=written, fieldCells=len(field["cells"]))
    return {"forecast": summary_fc}


# ================================================================ summary + manifest
def summary_job(cfg: dict, store: Storage, log: Callable, now: dt.datetime, obs: Optional[dict] = None) -> dict:
    """One file for the map and navigation: every station's identity, screen
    position, observed extremes so far, and the forecast numbers, read back
    from the per-station snapshots so this job needs no network."""
    roster = basemap.load_roster()
    rows = []
    fc_asof = None
    for c in roster:
        sid = c["station"]
        o = (obs or {}).get(sid)
        if o is None:
            raw = store.get(f"snapshots/obs/{sid}.json")
            if raw:
                s = json.loads(raw)
                o = {"today": s.get("today"), "yesterday": s.get("yesterday"), "latest": s.get("latest")}
        raw = store.get(f"snapshots/forecast/{sid}.json")
        f = json.loads(raw) if raw else {}
        fc_asof = fc_asof or f.get("asof")
        mk = day_markers(c, now)
        today = (o or {}).get("today") or {}
        row = {**c, "markers": mk,
               "obsHighSoFar": (today.get("high") or {}).get("v"),
               "obsLowSoFar": (today.get("low") or {}).get("v"),
               "obsLatest": (o or {}).get("latest")}
        for k in ("nws", "nbm", "lamp", "mav"):
            src = f.get(k) or {}
            for fld in ("highToday", "lowToday", "highTomorrow", "lowTomorrow"):
                row[f"{k}{fld[0].upper()}{fld[1:]}"] = src.get(fld)
            row[f"{k}Cycle"] = src.get("cycle")
            ai = (f.get("asIssued") or {}).get(k) or {}
            row[f"{k}IssuedHigh"] = ai.get("highToday")
            row[f"{k}IssuedLow"] = ai.get("lowToday")
            row[f"{k}IssuedPreDay"] = ai.get("preDay")
        rows.append(row)
    summary = {"schema": SCHEMA, "asof": _iso(now), "forecastAsof": fc_asof,
               "decode": {"TEMP_SOURCE": gw.TEMP_SOURCE, "INCLUDE_SPECI": gw.INCLUDE_SPECI},
               "cities": rows}
    store.put("snapshots/summary.json", json.dumps(summary, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)

    depth = store.list("archive/obs/")
    hur = store.get("snapshots/hurricane.json")
    manifest = {"schema": SCHEMA, "asof": {"obs": _iso(now), "summary": _iso(now), "forecast": fc_asof,
                                           "hurricane": json.loads(hur).get("asof") if hur else None},
                "cadenceMinutes": cfg.get("cadence_minutes", {}),
                "archiveDays": len(depth), "stations": [c["station"] for c in roster],
                "decode": summary["decode"]}
    store.put("snapshots/manifest.json", json.dumps(manifest, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
    log(kind="summary", stations=len(rows), archiveDays=len(depth))
    return {"summary": len(rows)}


# ================================================================ entrypoints
def _run(cfg: dict, store: Storage, steps: list) -> int:
    gw.set_user_agent(cfg.get("user_agent", ""))
    now = dt.datetime.now(dt.timezone.utc)
    t0 = time.time()
    entries: list = []

    def log(**kw):
        kw["t"] = _iso(dt.datetime.now(dt.timezone.utc))
        entries.append(kw)
        print(json.dumps(kw, default=str))

    errors = 0
    obs_out = None
    for name in steps:
        try:
            if name == "obs":
                obs_out = obs_job(cfg, store, log, now)
                errors += obs_out.get("errors", 0)
            elif name == "forecast":
                forecast_job(cfg, store, log, now)
            elif name == "summary":
                summary_job(cfg, store, log, now, (obs_out or {}).get("obs"))
        except Exception as e:
            errors += 1
            log(kind=name, error=f"{type(e).__name__}: {e}")
    print(f"{'+'.join(steps)}: {errors} errors, {round(time.time() - t0, 1)}s -> {store.describe()}")
    return 1 if errors >= len(steps) else 0


def obs_pass(cfg: dict, store: Storage) -> int:
    return _run(cfg, store, ["obs", "summary"])


def forecast_pass(cfg: dict, store: Storage) -> int:
    return _run(cfg, store, ["forecast", "summary"])
