"""
scorecard.py — how each forecast source did, day by day, from the archive.

Once a day. For every US station and every local day the archive can
score, the observed high and low come from the observation record (METAR
reports, bucketed through the station's IANA zone, decoded with the two
site-wide constants), and each source's forecast for the day comes from
the cycle that was standing at a common moment the evening before:

    NWS   the official day/night product (daytime high, night-ending low)
    NBM   the NBS bulletin's TXN row
    MAV   the GFS MOS N/X row
    LAMP  the max and min of the last pre-day hourly run (25 h horizon)

The exchange's own implied median for the day is carried alongside them,
read from the last quote pass before that same anchor, so the
market can be plotted on the same axis as the forecasts. It is not folded
into the skill statistics below: those describe forecast products, and a
market is not one.

THE ANCHOR. Every source is read as it stood at ANCHOR_LOCAL_HOUR on the
evening before the target day, in the station's own time. Taking instead
each source's last run before midnight — which this did until it was
measured — scored them at whatever hour they each happen to issue: the
Blend's cycle landed on midnight, the Service's about five hours earlier,
and the table compared a forecast that had seen five more hours of the
world against one that had not, without saying so.

A common local hour rather than a common instant, because a common instant
gives an eastern station seven hours to midnight and a western one ten,
and the comparison is meant to be across cities as well as across tools.
Six in the evening is late enough that every source has a run standing and
early enough to be a forecast rather than a nowcast.

Sources still differ in how stale their standing run is at that moment —
hourly guidance is half an hour old, a four-times-daily model can be six —
and that is a real difference in what each product offers, so the lead is
recorded with every score and the pages show it. A source is scored only
when its standing cycle is within MAX_LEAD_H of the anchor, which is up to
six hours further from midnight than that.

Error is forecast minus observed, so a positive bias means the source runs
warm. Per station and per source: n, mean absolute error, bias, and the
share of days within 1 and within 2 degrees (the unit is the station's,
Fahrenheit for US stations). NBM is the guidance forecasters start from
when editing the NWS grids, so NWS and NBM are related, not independent;
GFS MOS is a single-model statistical product with no forecaster editing.

Writes snapshots/scorecard.json.
"""
from __future__ import annotations
import datetime as dt
import gzip
import json
import time
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from . import gov_weather as gw
from . import basemap
from . import exchange as ex
from . import snapshots as sn
from .storage import Storage

MAX_LEAD_H = 24          # a cycle older than this AT THE ANCHOR is not scored for the day
# the common decision point: six in the evening, the station's own time, the day
# before. Every source is read as it stood then.
ANCHOR_LOCAL_HOUR = 18
RECENT_DAYS = 14         # per-day rows carried in the snapshot, newest first
SOURCES = ("nws", "nbm", "mav", "lamp")


def _local_midnight(day: dt.date, tz) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(0), tzinfo=tz)


def _cycle_time(stamp: str) -> dt.datetime:
    s = sn._norm_stamp(stamp)
    return dt.datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)


def pre_day_cycle(keys: list, kind: str, midnight: dt.datetime) -> Optional[tuple]:
    """The latest key of one kind issued in the 24 h before midnight, or
    None. Returns (key, lead_hours). Kept for tests and tools; the scorer
    itself picks per extreme through snapshots.pick_levels."""
    cut = sn._norm_stamp(midnight.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    pre = [k for k in keys if sn._norm_stamp(sn._stamp_of(k)) <= cut]
    if not pre:
        return None
    key = pre[-1]
    lead = (midnight - _cycle_time(sn._stamp_of(key))).total_seconds() / 3600
    if lead > MAX_LEAD_H:
        return None
    return key, round(lead, 1)


def forecast_for_day(store: Storage, by_kind: dict, source: str, c: dict, tz, day: dt.date) -> Optional[dict]:
    """{high, low, cycleHigh, cycleLow, lead} for one source and one local
    day, or None. The high and the low are each taken from the newest
    pre-day cycle that carries them (see snapshots.pick_levels): a source's
    last pre-midnight run usually holds the day's maximum but not its
    minimum, which sits in the run before."""
    midnight = _local_midnight(day, tz)
    anchor = midnight - dt.timedelta(hours=24 - ANCHOR_LOCAL_HOUR)
    cut = anchor.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    kind = {"nws": "daily", "nbm": "nbs", "mav": "mav", "lamp": "lamp"}[source]
    lv = sn.pick_levels(store, kind, by_kind.get(kind, []), cut, c, tz, day.isoformat(), max_lead_h=MAX_LEAD_H)
    from_hourly = False
    if lv["highToday"] is None and lv["lowToday"] is None and source == "nbm":
        # days before the short-range bulletin was archived: the hourly
        # bulletin's extremes over the day stand in, and the row says so
        lv = sn.pick_levels(store, "nbh", by_kind.get("nbh", []), cut, c, tz, day.isoformat(), max_lead_h=MAX_LEAD_H)
        from_hourly = True
    if lv["highToday"] is None and lv["lowToday"] is None:
        return None
    cyc = lv["levelCycleHigh"] or lv["levelCycleLow"]
    lead = round((midnight - _cycle_time(cyc)).total_seconds() / 3600, 1) if cyc else None
    out = {"high": lv["highToday"], "low": lv["lowToday"], "cycle": cyc, "cycleHigh": lv["levelCycleHigh"],
           "cycleLow": lv["levelCycleLow"], "lead": lead}
    if from_hourly or lv.get("fromHourly"):
        out["fromHourly"] = True
    return out


# ------------------------------------------------------- the exchange's median
MARKET_PREFIX = "archive/market/"


def market_pass_before(store: Storage, when: dt.datetime, cache: dict) -> Optional[dict]:
    """The newest quote-archive pass at or before `when` (UTC), parsed, or None.
    Keys are archive/market/{YYYYMMDD}/{HHMMSS}.json.gz, stamped in UTC, so the
    newest one is the last key that sorts at or before the wanted time. Listings
    and bodies are cached because every station in a zone shares one instant."""
    for back in (0, 1):
        ymd = (when - dt.timedelta(days=back)).strftime("%Y%m%d")
        if ymd not in cache["list"]:
            cache["list"][ymd] = sorted(store.list(f"{MARKET_PREFIX}{ymd}/"))
        keys = cache["list"][ymd]
        if back == 0:
            cut = when.strftime("%H%M%S")
            keys = [k for k in keys if k.rsplit("/", 1)[-1][:6] <= cut]
        if keys:
            key = keys[-1]
            if key not in cache["body"]:
                raw = store.get(key)
                try:
                    cache["body"][key] = json.loads(gzip.decompress(raw)) if raw else None
                except (OSError, ValueError):
                    cache["body"][key] = None
            return cache["body"][key]
    return None


def market_levels(store: Storage, c: dict, tz, day: dt.date, cache: dict) -> Optional[dict]:
    """{high, low, asof} — the exchange's implied median for one station and
    local day, from the last quote pass before the same evening anchor the model
    cycles are read at. None when the quote archive does
    not reach back that far or the day was not listed."""
    anchor = _local_midnight(day, tz) - dt.timedelta(hours=24 - ANCHOR_LOCAL_HOUR)
    body = market_pass_before(store, anchor.astimezone(dt.timezone.utc), cache)
    if not body:
        return None
    diso, out = day.isoformat(), {}
    for side in ("high", "low"):
        rows = [{"strike": r.get("strike"), "mid": ex.mid(r)} for r in body.get("rows") or []
                if r.get("station") == c["station"] and r.get("day") == diso and r.get("side") == side
                and r.get("strike") is not None]
        m = ex.implied_median(rows, side) if rows else None
        if m and m.get("value") is not None:
            out[side] = m["value"]
    if not out:
        return None
    out["asof"] = body.get("asof")
    return out


def observed_days(store: Storage, sid: str, tz, unit: str, first: dt.date, last: dt.date) -> dict:
    """{local day: {high, low, n}} from the observation record for one station."""
    days = []
    d = first - dt.timedelta(days=1)
    while d <= last + dt.timedelta(days=1):
        days.append(d.strftime("%Y%m%d"))
        d += dt.timedelta(days=1)
    record = sn.load_obs_record(store, days)
    raw = [ob for k, ob in record.items() if k.startswith(sid + "|")]
    rows = sn.decode_rows(raw, tz)
    out = {}
    d = first
    while d <= last:
        ex = sn.day_extremes(rows, tz, d.isoformat(), unit)
        if ex and ex["n"] >= 12:          # a day with fewer than half its hourly reports is not scored
            out[d.isoformat()] = {"high": ex["high"]["v"], "low": ex["low"]["v"], "n": ex["n"]}
        d += dt.timedelta(days=1)
    return out


def summarise(scores: list, field: str) -> Optional[dict]:
    errs = [s[field] for s in scores if s.get(field) is not None]
    if not errs:
        return None
    n = len(errs)
    return {"n": n, "mae": round(sum(abs(e) for e in errs) / n, 2), "bias": round(sum(errs) / n, 2),
            "within1": round(sum(1 for e in errs if abs(e) <= 1) / n, 3),
            "within2": round(sum(1 for e in errs if abs(e) <= 2) / n, 3)}


def scorecard_pass(cfg: dict, store: Storage) -> int:
    gw.set_user_agent(cfg.get("user_agent", ""))
    t0 = time.time()
    now = dt.datetime.now(dt.timezone.utc)
    roster = [c for c in basemap.load_roster() if c["unit"] == "F"]
    obs_days = sorted(k.rsplit("/", 1)[-1][:8] for k in store.list("archive/obs/"))
    if not obs_days:
        print("scorecard: no observation record yet")
        return 0
    first = dt.datetime.strptime(obs_days[0], "%Y%m%d").date()
    stations = {}
    pooled = {s: {"high": [], "low": []} for s in SOURCES}
    mcache = {"list": {}, "body": {}}       # shared across stations: one pass covers every station
    for c in roster:
        sid, tz = c["station"], ZoneInfo(c["tz"])
        local_today = now.astimezone(tz).date()
        last = local_today - dt.timedelta(days=1)
        obs = observed_days(store, sid, tz, c["unit"], first, last)
        # the scorecard reaches back to the first observed day, so its listing
        # window is the record's age (bounded by the 30-day backfill horizon)
        hours = int((now - dt.datetime.combine(first, dt.time(0), tzinfo=dt.timezone.utc)).total_seconds() / 3600) + 48
        by_kind = {k: sn.list_recent(store, sid, k, now, hours) for k in ("daily", "nbs", "nbh", "mav", "lamp")}
        days = []
        for diso, o in sorted(obs.items()):
            d = dt.date.fromisoformat(diso)
            row = {"date": diso, "obsHigh": o["high"], "obsLow": o["low"], "n": o["n"]}
            for s in SOURCES:
                f = forecast_for_day(store, by_kind, s, c, tz, d)
                if f:
                    row[s] = {"high": f["high"], "low": f["low"], "cycle": f["cycle"], "lead": f["lead"],
                              "errHigh": round(f["high"] - o["high"], 1) if f["high"] is not None else None,
                              "errLow": round(f["low"] - o["low"], 1) if f["low"] is not None else None}
            mk = market_levels(store, c, tz, d, mcache)
            if mk:
                row["fx"] = {"high": mk.get("high"), "low": mk.get("low"), "asof": mk.get("asof"),
                             "errHigh": round(mk["high"] - o["high"], 1) if mk.get("high") is not None else None,
                             "errLow": round(mk["low"] - o["low"], 1) if mk.get("low") is not None else None}
            days.append(row)
        summary = {}
        for s in SOURCES:
            rows = [r[s] for r in days if s in r]
            hi, lo = summarise(rows, "errHigh"), summarise(rows, "errLow")
            if hi or lo:
                summary[s] = {"high": hi, "low": lo}
            pooled[s]["high"] += [r["errHigh"] for r in rows if r.get("errHigh") is not None]
            pooled[s]["low"] += [r["errLow"] for r in rows if r.get("errLow") is not None]
        stations[sid] = {"city": c["city"], "tz": c["tz"], "unit": c["unit"], "daysScored": len(days),
                         "summary": summary, "days": list(reversed(days))[:RECENT_DAYS]}
    overall = {}
    for s in SOURCES:
        h = summarise([{"e": e} for e in pooled[s]["high"]], "e")
        l = summarise([{"e": e} for e in pooled[s]["low"]], "e")
        if h or l:
            overall[s] = {"high": h, "low": l}
    snap = {"schema": sn.SCHEMA, "asof": sn._iso(now), "firstDay": first.isoformat(),
            "decode": {"TEMP_SOURCE": gw.TEMP_SOURCE, "INCLUDE_SPECI": gw.INCLUDE_SPECI},
            "method": "error = forecast minus observed (positive runs warm); pre-day cycle within 24 h of local midnight; "
                      "observed = METAR extreme over the local day; days with fewer than 12 reports not scored",
            "sources": {"nws": "NWS day/night product", "nbm": "NBM NBS TXN", "mav": "GFS MOS N/X", "lamp": "LAMP hourly extremes",
                        "fx": "ForecastEx implied median, from the last quote before local midnight"},
            "overall": overall, "stations": stations}
    store.put("snapshots/scorecard.json", json.dumps(snap, separators=(",", ":")).encode(), "application/json",
              "public, max-age=600, stale-while-revalidate=3600, stale-if-error=604800")
    scored = sum(v["daysScored"] for v in stations.values())
    print(json.dumps({"kind": "scorecard", "stations": len(stations), "stationDays": scored,
                      "overall": {s: (v["high"] or {}).get("n") for s, v in overall.items()}, "seconds": round(time.time() - t0, 1)}))
    return 0
