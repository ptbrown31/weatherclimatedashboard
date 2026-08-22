"""
scorecard.py — how each forecast source did, day by day, from the archive.

Once a day. For every US station and every local day the archive can
score, the observed high and low come from the observation record (METAR
reports, bucketed through the station's IANA zone, decoded with the two
site-wide constants), and each source's forecast for the day comes from
the cycle that was standing before the day began:

    NWS   the official day/night product (daytime high, night-ending low)
    NBM   the NBS bulletin's TXN row
    MAV   the GFS MOS N/X row
    LAMP  the max and min of the last pre-day hourly run (25 h horizon)

A source is scored for a day only when its pre-day cycle was issued within
the 24 hours before local midnight; older cycles are longer-lead forecasts
and would not be a fair comparison. The lead (hours from issuance to
midnight) is recorded with every score.

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
import json
import time
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from . import gov_weather as gw
from . import basemap
from . import snapshots as sn
from .storage import Storage

MAX_LEAD_H = 24          # a pre-day cycle older than this is not scored for the day
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
    cut = midnight.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
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
            "sources": {"nws": "NWS day/night product", "nbm": "NBM NBS TXN", "mav": "GFS MOS N/X", "lamp": "LAMP hourly extremes"},
            "overall": overall, "stations": stations}
    store.put("snapshots/scorecard.json", json.dumps(snap, separators=(",", ":")).encode(), "application/json",
              "public, max-age=600, stale-while-revalidate=3600, stale-if-error=604800")
    scored = sum(v["daysScored"] for v in stations.values())
    print(json.dumps({"kind": "scorecard", "stations": len(stations), "stationDays": scored,
                      "overall": {s: (v["high"] or {}).get("n") for s, v in overall.items()}, "seconds": round(time.time() - t0, 1)}))
    return 0
