"""
snapshots.py — the small JSON files the browser reads.

Three jobs. Each reads the archive (never a government endpoint, except the
observation job's own fresh pull) and writes objects under snapshots/ with a
short Cache-Control so the CDN serves them fresh but keeps serving the last
one if a job dies. Every object carries two clocks: `asof`, the time the
DATA is good to (the last successful fetch), and `written`, when the file
was built. A page reports freshness from `asof`, so a feed that has stopped
is visible as an ageing as-of even while the job keeps running.

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
contract day. "As issued" means, per source, the latest archived cycle at
or before local midnight; the level FOR the day (NWS official high and low,
NBM TXN, MAV N/X) is picked per extreme from the newest pre-day cycle that
carries it, because a source's last pre-midnight run often carries the
day's maximum but not its minimum (the 12Z column belongs to the previous
run). Every level records which cycle supplied it and whether that cycle
was pre-day.

Archive listings are bounded to the last LOOKBACK_H hours per station and
kind (keys sort by stamp within a kind), so the job's cost does not grow
with the archive.

Provenance travels with every number: a standing "today" level says whether
it came from the official product or from the remaining hourly rows, an
observed extreme says which decode produced it, and the summary carries the
local day each value refers to, so a consumer can see when the clocks
straddled a station's midnight.
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
LOOKBACK_H = 72                   # archive listing window per station and kind
LEVEL_MAX_LEAD_H = 24             # a level's cycle must be within this of local midnight to count as pre-day
SCHEMA = 2
SOURCES = (("nws", "hourly", "daily"), ("nbm", "nbh", "nbs"), ("lamp", "lamp", None), ("mav", "mav", None))


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_iso(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _stamp_of(key: str) -> str:
    """'archive/KLAX/hourly_20260821T195218Z.json.gz' -> '20260821T195218Z'."""
    base = key.rsplit("/", 1)[-1]
    return base.split("_", 1)[1].split(".", 1)[0]


def _norm_stamp(stamp: str) -> str:
    """Stamps come in two widths: NWS issuances carry seconds (YYYYMMDDTHHMMSSZ)
    while bulletin cycles carry minutes (YYYYMMDDTHHMMZ). Compared as strings
    the shorter one sorts AFTER a cutoff at the same instant ('Z' > '0'), which
    would exclude a cycle issued exactly at local midnight. Pad to seconds
    before any comparison."""
    return stamp if len(stamp) >= 16 else stamp[:13] + "00Z"


def _stamp_time(stamp: str) -> dt.datetime:
    return dt.datetime.strptime(_norm_stamp(stamp), "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)


def _kind_stamp(kind: str, t: dt.datetime) -> str:
    """A stamp in the width the archive uses for this kind, for listing bounds."""
    t = t.astimezone(dt.timezone.utc)
    if kind in ("hourly", "daily"):
        return t.strftime("%Y%m%dT%H%M%SZ")
    if kind == "lamp":
        return t.strftime("%Y%m%dT%H30Z")
    return t.strftime("%Y%m%dT%H00Z")


def list_recent(store: Storage, sid: str, kind: str, now: dt.datetime, hours: int = LOOKBACK_H) -> list:
    """Archive keys of one kind for one station from the last `hours`, in
    stamp order. Bounded by start_after so the cost is constant over time."""
    prefix = f"archive/{sid}/{kind}_"
    since = prefix + _kind_stamp(kind, now - dt.timedelta(hours=hours))
    return store.list(prefix, start_after=since)


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
    """The contract day's frame for one station, all instants as UTC ISO.
    tzOffset is the offset at `now`; pages format clock times through the
    IANA zone, never by adding this number, so a DST transition day labels
    correctly on both sides of the change."""
    tz = ZoneInfo(city["tz"])
    local_now = now.astimezone(tz)
    D = local_now.date()
    day_start = dt.datetime.combine(D, dt.time(0), tzinfo=tz)
    day_end = dt.datetime.combine(D + dt.timedelta(days=1), dt.time(0), tzinfo=tz)
    win_start = dt.datetime.combine(D - dt.timedelta(days=1), dt.time(12), tzinfo=tz)
    yday_start = dt.datetime.combine(D - dt.timedelta(days=1), dt.time(0), tzinfo=tz)
    sr, ss = sun_times(city["lat"], city["lon"], D)
    out = {"day": D.isoformat(), "tomorrow": (D + dt.timedelta(days=1)).isoformat(),
           "yesterday": (D - dt.timedelta(days=1)).isoformat(),
           "tzOffset": tz.utcoffset(local_now).total_seconds() / 3600.0,
           "winStart": _iso(win_start), "dayStart": _iso(day_start), "dayEnd": _iso(day_end),
           "ydayStart": _iso(yday_start),
           "sunrise": _iso(sr) if sr else None, "sunset": _iso(ss) if ss else None}
    # US daily contracts for local day D list at noon Eastern the day before;
    # a marker for the market overlay, drawn only when the overlay is on. The
    # convention for the non-US listings is not confirmed, so it is omitted there.
    if city.get("unit") == "F":
        out["listed"] = _iso(dt.datetime.combine(D - dt.timedelta(days=1), dt.time(12), tzinfo=ZoneInfo("America/New_York")))
    return out


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
    """The day's high and low with the provenance of each (report type and
    whether tenths were available), so a whole-degree SPECI that sets the
    extreme is visible as such."""
    key = "tempF" if unit == "F" else "tempC"
    day_rows = [r for r in rows if local_day_key(_parse_iso(r["t"]), tz) == day]
    if not day_rows:
        return None
    hi = max(day_rows, key=lambda r: r[key])
    lo = min(day_rows, key=lambda r: r[key])
    return {"date": day, "n": len(day_rows),
            "high": {"v": hi[key], "t": hi["t"], "type": hi["type"], "src": hi["src"]},
            "low": {"v": lo[key], "t": lo["t"], "type": lo["type"], "src": lo["src"]}}


def load_obs_record(store: Storage, days: list) -> dict:
    """Rows from the archive's UTC-day files, merged, keyed 'SID|obsTime'."""
    rows: dict = {}
    for day in days:
        raw = _read_gz(store, f"archive/obs/{day}.json.gz")
        if raw:
            rows.update(json.loads(raw).get("rows", {}))
    return rows


def obs_job(cfg: dict, store: Storage, log: Callable, now: dt.datetime, deadline=None) -> dict:
    stations = [c[0] for c in CITIES]
    roster = {c["station"]: c for c in basemap.load_roster()}

    # 1. fresh pull, upserted into the archive's observation record. The
    #    window reaches back per station to the newest observation on record,
    #    up to OBS_HOURS; longer gaps are reported and left to
    #    scripts/backfill_obs.py. aviationweather.gov holds 30 days.
    errors = arch.archive_observations(store, stations, now, log, min_hours=OBS_PULL_HOURS, max_hours=OBS_HOURS)
    fetch = json.loads(store.get(arch.OBS_FETCH_KEY) or "{}")
    data_asof = fetch.get("fetchedAt")          # the last SUCCESSFUL pull: what the data is good to
    health = arch.update_health(store, {"obs": {"ok": errors == 0, "error": "observation fetch failed" if errors else None}}, now)

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
        record_end = _iso(dt.datetime.fromtimestamp(latest["obsTime"], dt.timezone.utc)) if latest else None
        snap = {
            "schema": SCHEMA, "station": sid, "city": c["city"], "unit": c["unit"], "tz": c["tz"],
            "asof": data_asof, "written": _iso(now), "fetchOk": errors == 0, "recordEnd": record_end,
            "decode": {"TEMP_SOURCE": gw.TEMP_SOURCE, "INCLUDE_SPECI": gw.INCLUDE_SPECI},
            "source": "aviationweather.gov METAR (hourly reports; SPECI where counted)",
            "rows": rows,
            "today": day_extremes(rows, tz, mk["day"], c["unit"]),
            "yesterday": day_extremes(rows, tz, mk["yesterday"], c["unit"]),
            "latest": ({"t": record_end, "raw": latest.get("rawOb", ""), "type": latest.get("metarType"),
                        "src": latest.get("temp_source")} if latest else None),
        }
        store.put(f"snapshots/obs/{sid}.json", json.dumps(snap, separators=(",", ":")).encode(),
                  "application/json", SNAP_CACHE)
        summary_obs[sid] = {"today": snap["today"], "yesterday": snap["yesterday"], "latest": snap["latest"],
                            "n72h": len(rows), "asof": data_asof, "recordEnd": record_end}
    log(kind="obs-snapshots", stations=len(summary_obs), dataAsof=data_asof, fetchOk=errors == 0,
        unhealed=fetch.get("unhealed"))
    alarms = [s for s, h in health.items() if not s.startswith("_") and h.get("fail_streak", 0) >= arch.FAIL_STREAK_ALARM]
    return {"errors": errors, "obs": summary_obs, "alarms": alarms, "obsAsof": data_asof, "unhealed": fetch.get("unhealed")}


# ================================================================ forecasts
def _as_issued_key(store: Storage, sid: str, kind: str, cutoff_stamp: str, keys: Optional[list] = None):
    """Latest archived cycle at or before the cutoff, else the earliest held.
    Returns (key, pre_day)."""
    keys = keys if keys is not None else store.list(f"archive/{sid}/{kind}_")
    if not keys:
        return None, False
    cut = _norm_stamp(cutoff_stamp)
    pre = [k for k in keys if _norm_stamp(_stamp_of(k)) <= cut]
    return (pre[-1], True) if pre else (keys[0], False)


def _cutoff(iso_utc: str) -> str:
    return _parse_iso(iso_utc).astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _nws_hourly_rows(body: dict, unit: str = "F") -> list:
    rows = []
    for p in body["properties"]["periods"]:
        f = gw.period_temp_f(p)          # both schema shapes; a null temperature skips the period
        if f is None:
            continue
        rows.append({"t": _iso(_parse_iso(p["startTime"])), "tempF": f, "tempC": round((f - 32) * 5 / 9, 1)})
    return rows


def _nws_daily_rows(body: dict) -> list:
    rows = []
    for p in body["properties"]["periods"]:
        f = gw.period_temp_f(p)
        if f is None:
            continue
        rows.append({"start": _iso(_parse_iso(p["startTime"])), "end": _iso(_parse_iso(p["endTime"])),
                     "isDay": bool(p.get("isDaytime")), "tempF": f, "tempC": round((f - 32) * 5 / 9, 1),
                     "name": p.get("name")})
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
    """{local day: {"max": v, "min": v}} from a TXN / N/X row. The convention
    (00Z column = the max of the day that ended there, 12Z column = that
    morning's min) is defined for NOAA's domain, every zone of which is west
    of Greenwich, where converting the column time to local time lands each
    extreme on its own day. For a zone east of Greenwich the 00Z max would
    land a day late, so such stations are refused rather than mis-filed."""
    out: dict = {}
    for e in parsed["extremes"]:
        local = e["time"].astimezone(tz)
        if local.utcoffset() and local.utcoffset().total_seconds() > 0:
            raise ValueError("NBM/MOS daily-extreme convention is not defined east of Greenwich")
        out.setdefault(local.date().isoformat(), {})[e["kind"]] = e["temp_f"]
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


def _levels_from_key(store: Storage, kind: str, key: str, c: dict, tz, day: str) -> dict:
    """{"high", "low"} for one local day from one archived object of `kind`."""
    raw = _read_gz(store, key)
    if kind == "daily":
        hi, lo = _official_hi_lo(_nws_daily_rows(json.loads(raw)), tz)
        return {"high": _u(hi.get(day), c["unit"]), "low": _u(lo.get(day), c["unit"])}
    text = raw.decode("ascii", "replace")
    if kind in ("nbs", "mav"):
        parsed = gw.parse_nbs_block(text) if kind == "nbs" else gw.parse_mav_block(text)
        byday = _extremes_by_day(parsed, tz).get(day, {})
        return {"high": _u(byday.get("max"), c["unit"]), "low": _u(byday.get("min"), c["unit"])}
    rows = _hourly_rows(gw.parse_hourly_block(text), c["unit"])
    h, l = _max_min_in_day(rows, tz, day)
    return {"high": _u(h, c["unit"]), "low": _u(l, c["unit"]), "fromHourly": True}


def pick_levels(store: Storage, kind: str, keys: list, cutoff_stamp: str, c: dict, tz, day: str,
                max_lead_h: Optional[float] = LEVEL_MAX_LEAD_H) -> dict:
    """
    The level FOR the day from the cycles at or before the cutoff, chosen
    per extreme: walk newest first and take the first cycle that carries the
    high, and separately the first that carries the low. A source's last
    pre-midnight run usually carries the day's maximum but not its minimum
    (the 12Z column belongs to the previous run), so one cycle cannot serve
    both. Cycles older than max_lead_h before the cutoff are not used (a
    longer-lead forecast is not the same comparison); with max_lead_h None
    the earliest held cycle is used and flagged preDay=False.

        -> {"highToday", "lowToday", "levelCycleHigh", "levelCycleLow", "levelPreDay", "fromHourly"?}
    """
    cut = _norm_stamp(cutoff_stamp)
    cut_t = _stamp_time(cut)
    pre = [k for k in keys if _norm_stamp(_stamp_of(k)) <= cut]
    if max_lead_h is not None:
        pre = [k for k in pre if (cut_t - _stamp_time(_stamp_of(k))).total_seconds() / 3600 <= max_lead_h]
    out = {"highToday": None, "lowToday": None, "levelCycleHigh": None, "levelCycleLow": None, "levelPreDay": bool(pre)}
    candidates = list(reversed(pre)) if pre else ([keys[0]] if keys and max_lead_h is None else [])
    for key in candidates[:6]:
        try:
            lv = _levels_from_key(store, kind, key, c, tz, day)
        except Exception:
            continue
        if lv.get("fromHourly"):
            out["fromHourly"] = True
        if out["highToday"] is None and lv.get("high") is not None:
            out["highToday"], out["levelCycleHigh"] = lv["high"], _stamp_of(key)
        if out["lowToday"] is None and lv.get("low") is not None:
            out["lowToday"], out["levelCycleLow"] = lv["low"], _stamp_of(key)
        if out["highToday"] is not None and out["lowToday"] is not None:
            break
    return out


def _trace_rows(store: Storage, kind: str, key: str, unit: str) -> list:
    raw = _read_gz(store, key)
    if kind == "hourly":
        return _nws_hourly_rows(json.loads(raw), unit)
    if kind == "mav":
        return _hourly_rows(gw.parse_mav_block(raw.decode("ascii", "replace")), unit)
    return _hourly_rows(gw.parse_hourly_block(raw.decode("ascii", "replace")), unit)


def build_forecast_snapshot(store: Storage, c: dict, now: dt.datetime) -> dict:
    sid, tz, unit = c["station"], ZoneInfo(c["tz"]), c["unit"]
    mk = day_markers(c, now)
    by_kind = {kind: list_recent(store, sid, kind, now) for kind in ("hourly", "daily", "nbh", "nbs", "lamp", "mav")}
    snap = {"schema": SCHEMA, "station": sid, "city": c["city"], "unit": unit, "tz": c["tz"],
            "asof": _iso(now), "written": _iso(now), "markers": mk,
            "nws": None, "nbm": None, "lamp": None, "mav": None, "asIssued": {}, "yesterday": {}}
    day, tmw = mk["day"], mk["tomorrow"]

    # ---- NWS: the standing hourly forecast and the official day/night highs and lows
    if by_kind["hourly"]:
        hourly = _trace_rows(store, "hourly", by_kind["hourly"][-1], unit)
        daily_rows = _nws_daily_rows(json.loads(_read_gz(store, by_kind["daily"][-1]))) if by_kind["daily"] else []
        hi, lo = _official_hi_lo(daily_rows, tz)
        h_today, l_today = _max_min_in_day(hourly, tz, day)
        h_tmw, l_tmw = _max_min_in_day(hourly, tz, tmw)
        snap["nws"] = {"cycle": _stamp_of(by_kind["hourly"][-1]), "dailyCycle": _stamp_of(by_kind["daily"][-1]) if by_kind["daily"] else None,
                       "hourly": hourly, "daily": daily_rows,
                       "highToday": _u(hi.get(day, h_today), unit), "lowToday": _u(lo.get(day, l_today), unit),
                       "highTomorrow": _u(hi.get(tmw, h_tmw), unit), "lowTomorrow": _u(lo.get(tmw, l_tmw), unit),
                       "officialHighToday": day in hi, "officialLowToday": day in lo,
                       "officialHighTomorrow": tmw in hi, "officialLowTomorrow": tmw in lo,
                       "officialToday": day in hi and day in lo, "officialTomorrow": tmw in hi and tmw in lo}
    # ---- NBM: hourly from NBH, the blend's own daily max/min from NBS
    if by_kind["nbh"]:
        parsed = gw.parse_hourly_block(_read_gz(store, by_kind["nbh"][-1]).decode("ascii", "replace"))
        rows = _hourly_rows(parsed, unit)
        nbm = {"cycle": _iso(parsed["cycle"]), "hourly": rows, "txn": {}, "nbsCycle": None}
        if by_kind["nbs"]:
            nbs = gw.parse_nbs_block(_read_gz(store, by_kind["nbs"][-1]).decode("ascii", "replace"))
            nbm["nbsCycle"] = _iso(nbs["cycle"])
            nbm["txn"] = _levels_by_day(_extremes_by_day(nbs, tz), unit)
        h, l = _max_min_in_day(rows, tz, day)
        t = nbm["txn"].get(day, {})
        nbm["highToday"] = t.get("max", _u(h, unit)); nbm["highTodayFrom"] = "txn" if "max" in t else "hourly"
        nbm["lowToday"] = t.get("min", _u(l, unit)); nbm["lowTodayFrom"] = "txn" if "min" in t else "hourly"
        nbm["highTomorrow"] = nbm["txn"].get(tmw, {}).get("max")
        nbm["lowTomorrow"] = nbm["txn"].get(tmw, {}).get("min")
        nbm["hourlyFrom"] = rows[0]["t"] if rows else None
        snap["nbm"] = nbm
    # ---- LAMP: the same-day hourly trace; its "today" extremes cover only the hours it has left
    if by_kind["lamp"]:
        parsed = gw.parse_hourly_block(_read_gz(store, by_kind["lamp"][-1]).decode("ascii", "replace"))
        rows = _hourly_rows(parsed, unit)
        h, l = _max_min_in_day(rows, tz, day)
        snap["lamp"] = {"cycle": _iso(parsed["cycle"]), "hourly": rows, "hourlyFrom": rows[0]["t"] if rows else None,
                        "highToday": _u(h, unit), "lowToday": _u(l, unit), "partialDay": True}
    # ---- GFS MOS MAV: an independent daily max/min, 3-hourly trace
    if by_kind["mav"]:
        parsed = gw.parse_mav_block(_read_gz(store, by_kind["mav"][-1]).decode("ascii", "replace"))
        nx = _levels_by_day(_extremes_by_day(parsed, tz), unit)
        snap["mav"] = {"cycle": _iso(parsed["cycle"]), "rows": _hourly_rows(parsed, unit), "nx": nx,
                       "highToday": nx.get(day, {}).get("max"), "lowToday": nx.get(day, {}).get("min"),
                       "highTomorrow": nx.get(tmw, {}).get("max"), "lowTomorrow": nx.get(tmw, {}).get("min")}
    # ---- as issued before the day began: the hourly trace from each source's
    #      last pre-day cycle, and the level for the day picked per extreme
    cut, ycut = _cutoff(mk["dayStart"]), _cutoff(mk["ydayStart"])
    for label, kind, level_kind in SOURCES:
        ks = by_kind.get(kind, [])
        key, pre = _as_issued_key(store, sid, kind, cut, ks)
        if key:
            entry = {"cycle": _stamp_of(key), "preDay": pre, "rows": _trace_rows(store, kind, key, unit)}
            entry.update(pick_levels(store, level_kind or kind, by_kind.get(level_kind or kind, []), cut, c, tz, day, max_lead_h=None))
            snap["asIssued"][label] = entry
        ykey, ypre = _as_issued_key(store, sid, kind, ycut, ks)
        if ykey and ypre:
            yentry = {"cycle": _stamp_of(ykey), "rows": _trace_rows(store, kind, ykey, unit)}
            lv = pick_levels(store, level_kind or kind, by_kind.get(level_kind or kind, []), ycut, c, tz, mk["yesterday"])
            yentry.update({"high": lv["highToday"], "low": lv["lowToday"], "levelCycleHigh": lv["levelCycleHigh"],
                           "levelCycleLow": lv["levelCycleLow"], "levelPreDay": lv["levelPreDay"]})
            snap["yesterday"][label] = yentry
    return snap


def forecast_job(cfg: dict, store: Storage, log: Callable, now: dt.datetime, deadline=None) -> dict:
    roster = basemap.load_roster()
    grid = basemap.load_field_grid()
    written = kept = skipped = 0
    summary_fc = {}
    for c in roster:
        sid = c["station"]
        key = f"snapshots/forecast/{sid}.json"
        if deadline is not None and deadline.over(15):
            skipped += 1
            continue
        try:
            snap = build_forecast_snapshot(store, c, now)
        except Exception as e:
            # keep the previous good snapshot rather than publishing a partial one
            log(station=sid, kind="forecast", error=f"{type(e).__name__}: {e}")
            prev = store.get(key)
            if prev:
                snap = json.loads(prev)
                snap["error"] = f"{type(e).__name__}: {e}"
                snap["staleSince"] = snap.get("staleSince") or _iso(now)
                store.put(key, json.dumps(snap, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
                kept += 1
            continue
        store.put(key, json.dumps(snap, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
        written += 1
        fc = {"day": snap["markers"]["day"], "tomorrow": snap["markers"]["tomorrow"]}
        for k in ("nws", "nbm", "lamp", "mav"):
            s = snap[k] or {}
            fc[k] = {kk: s.get(kk) for kk in ("cycle", "highToday", "lowToday", "highTomorrow", "lowTomorrow",
                                                "officialHighToday", "officialLowToday", "officialHighTomorrow", "officialLowTomorrow",
                                                "highTodayFrom", "lowTodayFrom", "partialDay")} if snap[k] else None
            ai = snap["asIssued"].get(k)
            fc[k + "Issued"] = ({kk: ai.get(kk) for kk in ("cycle", "preDay", "highToday", "lowToday", "levelCycleHigh",
                                                            "levelCycleLow", "levelPreDay", "fromHourly")} if ai else None)
        summary_fc[sid] = fc
    if skipped:
        log(kind="forecast", warning=f"deadline: {skipped} stations not rebuilt")

    # ---- the shading field for the national map, from tomorrow's NWS highs and lows
    pts, for_dates = [], {}
    for c in roster:
        f = summary_fc.get(c["station"], {})
        n = f.get("nws") or {}
        pts.append({**c, "hi": n.get("highTomorrow"), "lo": n.get("lowTomorrow")})
        if f.get("tomorrow"):
            for_dates[c["station"]] = f["tomorrow"]
    if written:
        field = basemap.idw_field(grid, pts, "hi", "lo")
        field.update({"schema": SCHEMA, "asof": _iso(now), "for": "tomorrow", "forDates": for_dates,
                      "note": "derived: inverse-distance interpolation of the listed stations' NWS forecasts, not the NDFD grid"})
        store.put("snapshots/field.json", json.dumps(field, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
    log(kind="forecast-snapshots", written=written, keptPrevious=kept, skipped=skipped)
    return {"forecast": summary_fc, "written": written, "kept": kept, "skipped": skipped}


# ================================================================ summary + manifest
def summary_job(cfg: dict, store: Storage, log: Callable, now: dt.datetime, obs: Optional[dict] = None) -> dict:
    """One file for the map and navigation: every station's identity, screen
    position, observed extremes so far, and the forecast numbers, read back
    from the per-station snapshots so this job needs no network. Each value
    carries the local day it refers to; `asof` clocks are read from the
    files that produced the data, never from this job's own clock."""
    roster = basemap.load_roster()
    rows = []
    fc_asof, obs_asof = None, None
    for c in roster:
        sid = c["station"]
        o = (obs or {}).get(sid)
        if o is None:
            raw = store.get(f"snapshots/obs/{sid}.json")
            if raw:
                s = json.loads(raw)
                o = {"today": s.get("today"), "yesterday": s.get("yesterday"), "latest": s.get("latest"),
                     "asof": s.get("asof"), "recordEnd": s.get("recordEnd")}
        if o and o.get("asof"):
            obs_asof = max(obs_asof or "", o["asof"])
        raw = store.get(f"snapshots/forecast/{sid}.json")
        f = json.loads(raw) if raw else {}
        if f.get("asof"):
            fc_asof = max(fc_asof or "", f["asof"])
        mk = day_markers(c, now)
        # observed extremes only when the obs snapshot's day is the day the markers name
        today = (o or {}).get("today") or {}
        same_day = today.get("date") == mk["day"]
        row = {**c, "markers": mk, "obsDay": today.get("date"), "obsAsof": (o or {}).get("asof"),
               "obsHighSoFar": (today.get("high") or {}).get("v") if same_day else None,
               "obsLowSoFar": (today.get("low") or {}).get("v") if same_day else None,
               "obsHighSrc": (today.get("high") or {}).get("src") if same_day else None,
               "obsLowSrc": (today.get("low") or {}).get("src") if same_day else None,
               "obsLatest": (o or {}).get("latest"),
               "forecastDay": (f.get("markers") or {}).get("day"), "forecastAsof": f.get("asof")}
        fmk = f.get("markers") or {}
        fc_same = fmk.get("day") == mk["day"]
        for k in ("nws", "nbm", "lamp", "mav"):
            src = f.get(k) or {}
            for fld in ("highToday", "lowToday", "highTomorrow", "lowTomorrow"):
                row[f"{k}{fld[0].upper()}{fld[1:]}"] = src.get(fld) if fc_same else None
            row[f"{k}Cycle"] = src.get("cycle")
            if k == "nws":
                for fld in ("officialHighToday", "officialLowToday", "officialHighTomorrow", "officialLowTomorrow"):
                    row[f"nws{fld[0].upper()}{fld[1:]}"] = src.get(fld) if fc_same else None
            else:
                row[f"{k}HighTodayFrom"] = src.get("highTodayFrom", "hourly" if src.get("partialDay") else None)
            ai = (f.get("asIssued") or {}).get(k) or {}
            pre = bool(ai.get("levelPreDay")) and fc_same
            # an "issued" level is published only when its cycle was pre-day
            row[f"{k}IssuedHigh"] = ai.get("highToday") if pre else None
            row[f"{k}IssuedLow"] = ai.get("lowToday") if pre else None
            row[f"{k}IssuedPreDay"] = ai.get("levelPreDay") if fc_same else None
            row[f"{k}IssuedCycle"] = ai.get("levelCycleHigh") or ai.get("levelCycleLow")
            row[f"{k}IssuedFromHourly"] = ai.get("fromHourly")
        rows.append(row)
    raw_health = store.get(arch.HEALTH_KEY)
    health = json.loads(raw_health) if raw_health else {}
    alarms = [s for s, h in health.items() if not s.startswith("_") and isinstance(h, dict) and h.get("fail_streak", 0) >= arch.FAIL_STREAK_ALARM]
    summary = {"schema": SCHEMA, "asof": obs_asof, "written": _iso(now), "forecastAsof": fc_asof, "obsAsof": obs_asof,
               "decode": {"TEMP_SOURCE": gw.TEMP_SOURCE, "INCLUDE_SPECI": gw.INCLUDE_SPECI},
               "alarms": alarms, "cities": rows}
    store.put("snapshots/summary.json", json.dumps(summary, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)

    depth = store.list("archive/obs/")
    hur = store.get("snapshots/hurricane.json")
    fetch = json.loads(store.get(arch.OBS_FETCH_KEY) or "{}")
    manifest = {"schema": SCHEMA, "written": _iso(now),
                "asof": {"obs": obs_asof, "summary": obs_asof, "forecast": fc_asof,
                         "hurricane": json.loads(hur).get("asof") if hur else None},
                "cadenceMinutes": cfg.get("cadence_minutes", {}),
                "archiveDays": len(depth), "stations": [c["station"] for c in roster],
                "decode": summary["decode"], "alarms": alarms, "obsUnhealed": fetch.get("unhealed")}
    store.put("snapshots/manifest.json", json.dumps(manifest, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
    log(kind="summary", stations=len(rows), archiveDays=len(depth), obsAsof=obs_asof, forecastAsof=fc_asof, alarms=alarms)
    return {"summary": len(rows), "alarms": alarms}


# ================================================================ entrypoints
def _run(cfg: dict, store: Storage, steps: list) -> int:
    gw.set_user_agent(cfg.get("user_agent", ""))
    now = dt.datetime.now(dt.timezone.utc)
    t0 = time.time()
    entries: list = []
    deadline = arch.Deadline(arch.remaining_budget(cfg))

    def log(**kw):
        kw["t"] = _iso(dt.datetime.now(dt.timezone.utc))
        entries.append(kw)
        print(json.dumps(kw, default=str))

    errors = 0
    obs_out = None
    alarms: list = []
    for name in steps:
        try:
            if name == "obs":
                obs_out = obs_job(cfg, store, log, now, deadline)
                errors += obs_out.get("errors", 0)
                alarms = obs_out.get("alarms", [])
            elif name == "forecast":
                forecast_job(cfg, store, log, now, deadline)
            elif name == "summary":
                out = summary_job(cfg, store, log, now, (obs_out or {}).get("obs"))
                alarms = out.get("alarms", alarms)
        except Exception as e:
            errors += 1
            log(kind=name, error=f"{type(e).__name__}: {e}")
    arch.LAST_STATUS = {"job": "+".join(steps), "errors": errors, "alarms": alarms, "seconds": round(time.time() - t0, 1),
                        "entries": entries}
    print(f"{'+'.join(steps)}: {errors} errors, alarms {alarms or 'none'}, {round(time.time() - t0, 1)}s -> {store.describe()}")
    return 1 if errors >= len(steps) else 0


def obs_pass(cfg: dict, store: Storage) -> int:
    return _run(cfg, store, ["obs", "summary"])


def forecast_pass(cfg: dict, store: Storage) -> int:
    return _run(cfg, store, ["forecast", "summary"])
