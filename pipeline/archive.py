"""
archive.py — the append-only record. Runs before anything visible is built.

api.weather.gov serves the forecast standing right now and will not return
what it said earlier. The city chart compares observed against the forecast
as issued before the contract day began, and the scorecard scores each source
against what happened; neither can be reconstructed after the fact. Data from
days when this was not running cannot be recovered, and NOMADS keeps NBM text
for only about two days, so this job is the first thing deployed and the
first thing watched.

What one pass stores, through the storage adapter (local directory or bucket):

  archive/{SID}/hourly_{cycle}.json.gz   NWS hourly periods, raw response body
  archive/{SID}/daily_{cycle}.json.gz    NWS day/night periods: the official high and low
  archive/{SID}/nbh_{cycle}.txt.gz       NBM hourly block, 25 h
  archive/{SID}/nbs_{cycle}.txt.gz       NBM short-range block, 3-hourly to 71 h, TXN daily max/min
  archive/{SID}/lamp_{cycle}.txt.gz      LAMP hourly block, 25 h (the :30 run)
  archive/{SID}/mav_{cycle}.txt.gz       GFS MOS MAV block, N/X daily max/min, 3-hourly TMP
  archive/obs/{YYYYMMDD}.json.gz         every station's METAR and SPECI rows for one UTC day,
                                         raw as served, keyed by obsTime, corrections kept
  archive/_meta/grids.json               station -> NWS forecast URLs, re-resolved daily
  archive/_meta/health.json              per-source failure streaks, for alarms
  archive/_done/{source}_{cycle}         markers for the bulk bulletins, one per complete cycle
  archive/_runs/{pulled}.json, latest.json   one record per pass, and the newest

The stamp in a forecast filename is the forecast's own issuance time
(`updateTime`), not the pull time. `generatedAt` is the render time and
changes on every request; deduplicating on it would store every pull.
A cycle already stored is never written twice, which also makes a retried
scheduled run harmless. Observation day files are the one exception to
write-once: they are re-read and upserted each pass, because a corrected
report (COR) replaces the original under the same obsTime and the file must
carry the correction while keeping what it superseded.

Bulletins are caught up, not just sampled: every cycle in the lookback
window that has no completion marker is fetched, newest first, a few per
pass, so a scheduler gap is recovered from the bucket while it is still
there. A marker is written only when the bulletin covered the stations it
should, so a half-written file is retried on the next pass.

Known limit, by design: the NWS forecast is served through a CDN with an
hour's cache, and the job honours that cache. Two issuances less than an
hour apart can therefore reach the archive as one. The archive records what
was served, and the hour-old render is the one any reader would have seen.

Observations are filed by UTC day on purpose. aviationweather.gov times are
Zulu; local-day bucketing through the IANA zone happens when the data is
read, and a local day spans at most two UTC-day files.

Run one pass:          python3 -m pipeline.run --job archive
On a schedule:         every 30 minutes (config cadence_minutes.archive)
"""
from __future__ import annotations
import datetime as dt
import gzip
import json
import sys
import time
from typing import Callable, Optional

from . import gov_weather as gw
from .cities import CITIES
from .storage import Storage

GRIDS_KEY = "archive/_meta/grids.json"
HEALTH_KEY = "archive/_meta/health.json"
OBS_FETCH_KEY = "archive/_meta/obs_fetch.json"   # the last successful observation pull: what obs data is good to
GRID_MAX_AGE_H = 24            # the docs ask that the points mapping be re-checked periodically
OBS_MIN_H, OBS_MAX_H = 6, 72   # fresh pull per pass, and the most a pass will reach back to heal a gap
BULLETIN_LOOKBACK_H = {"nbh": 36, "nbs": 36, "lamp": 24, "mav": 36}
BULLETIN_MAX_NEW = 4           # new cycles fetched per source per pass (a catch-up spreads over passes)
FAIL_STREAK_ALARM = 6          # passes in a row before a source's failure is an alarm (3 h at 30 min)

LAST_STATUS: dict = {}         # set by one_pass: per-source outcome of the latest pass


def _gz(data: bytes) -> bytes:
    return gzip.compress(data, compresslevel=6)


def _ungz(data: Optional[bytes]) -> Optional[bytes]:
    return gzip.decompress(data) if data else None


def _stamp(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def cycle_stamp(props: dict) -> Optional[str]:
    """Filename stamp for one NWS forecast cycle, from the forecast's own
    update time. None if the response carries none."""
    raw = props.get("updateTime") or props.get("updated")
    if not raw:
        return None
    return _stamp(dt.datetime.fromisoformat(raw.replace("Z", "+00:00")))


class Deadline:
    """A wall-clock budget for one pass. Lambda stops a function at its
    timeout, so the pass stops itself first and records what it skipped."""
    def __init__(self, seconds: Optional[float]):
        self.end = time.time() + seconds if seconds else None

    def remaining(self) -> float:
        return float("inf") if self.end is None else self.end - time.time()

    def over(self, need: float = 0.0) -> bool:
        return self.remaining() < need


def remaining_budget(cfg: dict, reserve: float = 0.0) -> Optional[float]:
    """Seconds this job may use. A chain of jobs shares one absolute end
    (cfg['_deadline_end'], set by run.chain from the invocation budget);
    `reserve` holds back time for the jobs that follow."""
    end = cfg.get("_deadline_end")
    if end is not None:
        return max(30.0, end - time.time() - reserve)
    b = cfg.get("pass_budget_seconds")
    return max(30.0, b - reserve) if b else None


# ---------------------------------------------------------------- grids
def load_grids(store: Storage, now: dt.datetime, log: Callable) -> dict:
    """Station -> forecast URLs, resolved through the points endpoint and
    cached; re-resolved once a day because grid and office can change. A
    failed refresh is not stamped as a fresh one, so it is retried next pass."""
    raw = store.get(GRIDS_KEY)
    grids = json.loads(raw) if raw else {}
    resolved = grids.get("_resolved")
    stale = True
    if resolved:
        stale = (now - dt.datetime.fromisoformat(resolved)) > dt.timedelta(hours=GRID_MAX_AGE_H)
    changed = ok = 0
    for sid, name, lat, lon, tzname, unit in CITIES:
        if unit != "F":                      # api.weather.gov covers US stations only
            continue
        if sid in grids and not stale:
            continue
        try:
            g = gw.resolve_grid(lat, lon)
        except Exception as e:               # keep the cached mapping on a bad day
            log(station=sid, kind="points", error=f"{type(e).__name__}: {e}")
            continue
        new = {"hourly": g["hourly_url"], "daily": g["daily_url"], "timezone": g["timezone"],
               "wfo": g["wfo"], "x": g["x"], "y": g["y"]}
        if grids.get(sid) != new:
            if sid in grids:
                log(station=sid, kind="points", note="grid mapping changed", old=grids[sid], new=new)
            grids[sid] = new
            changed += 1
        ok += 1
    if ok and (changed or stale):
        grids["_resolved"] = now.isoformat()
        store.put(GRIDS_KEY, json.dumps(grids, indent=1).encode(), "application/json")
    return {k: v for k, v in grids.items() if not k.startswith("_")}


# ---------------------------------------------------------------- NWS
def archive_nws(store: Storage, grids: dict, log: Callable, deadline: Deadline) -> tuple:
    """Both NWS forecast products per US station, stored raw, one object per
    issuance. Returns (requests, errors, skipped)."""
    requests = errors = skipped = 0
    for sid, g in grids.items():
        if deadline.over(20):
            skipped += 2
            continue
        for kind in ("hourly", "daily"):
            requests += 1
            try:
                body = gw._get(g[kind])
                stamp = cycle_stamp(body.get("properties", {}))
                # No update time -> stamp by pull time and keep it: at worst a
                # duplicate cycle, never a lost one.
                fname = f"{kind}_{stamp or _stamp(dt.datetime.now(dt.timezone.utc))}.json.gz"
                key = f"archive/{sid}/{fname}"
                written = store.put_if_absent(key, _gz(json.dumps(body, separators=(",", ":")).encode()),
                                              "application/gzip")
                log(station=sid, kind=kind, cycle=stamp, grid=f"{g.get('wfo')}/{g.get('x')},{g.get('y')}",
                    **({"file": key} if written else {"skipped": True}))
            except Exception as e:            # one station failing must not end the pass
                errors += 1
                log(station=sid, kind=kind, error=f"{type(e).__name__}: {e}")
    if skipped:
        log(kind="nws", warning=f"deadline: {skipped} requests not attempted")
    return requests, errors, skipped


# ---------------------------------------------------------------- bulletins
def expected_coverage(source: str) -> int:
    """How many roster stations a bulletin should carry: NBM has the US and
    Canadian stations, LAMP and MAV the US stations only."""
    us = sum(1 for c in CITIES if c[5] == "F")
    ca = sum(1 for c in CITIES if c[0].startswith("CY"))
    return us + ca if source in ("nbh", "nbs") else us


def marker_body(stations: int, nbytes: int = 0) -> bytes:
    return json.dumps({"marked": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                       "stations": stations, "bytes": nbytes}).encode()


def _block_ok(source: str, block: str) -> bool:
    try:
        if source in ("nbh", "lamp"):
            return len(gw.parse_hourly_block(block)["rows"]) >= 20
        if source == "nbs":
            return bool(gw.parse_nbs_block(block)["extremes"])
        if source == "mav":
            return bool(gw.parse_mav_block(block)["extremes"])
    except Exception:
        return False
    return True


def archive_bulletins(store: Storage, source: str, stations: list, log: Callable, deadline: Deadline,
                      now: Optional[dt.datetime] = None) -> dict:
    """
    Catch-up loop for one bulletin family. Every cycle in the lookback
    window without a completion marker is a candidate; newest first, at most
    BULLETIN_MAX_NEW fetched per pass. Each fetched bulletin is streamed,
    every roster station's block stored verbatim, and the marker written
    only when coverage is complete and the blocks parse, so a truncated file
    is retried next pass. Returns {"new", "skipped", "missing", "errors"}.
    """
    fmt = "%Y%m%dT%H30Z" if source == "lamp" else "%Y%m%dT%H00Z"
    want = expected_coverage(source)
    out = {"new": 0, "skipped": 0, "missing": 0, "errors": 0, "incomplete": 0}
    for cycle, url in gw.bulletin_cycles(source, BULLETIN_LOOKBACK_H[source], now):
        stamp = cycle.strftime(fmt)
        done = f"archive/_done/{source}_{stamp}"
        if store.exists(done):
            out["skipped"] += 1
            continue
        if out["new"] >= BULLETIN_MAX_NEW or deadline.over(90):
            break
        try:
            if not gw._head(url):
                out["missing"] += 1          # not published yet, or already gone
                continue
            t0 = time.time()
            timeout = 180 if deadline.end is None else min(180, max(30, int(deadline.remaining() - 30)))
            blocks, nbytes, nblocks = gw.fetch_bulletin_blocks(url, stations, gw.BULLETIN_MARKER[source],
                                                               timeout=timeout)
            good = {sid: b for sid, b in blocks.items() if _block_ok(source, b)}
            for sid, block in good.items():
                store.put_if_absent(f"archive/{sid}/{source}_{stamp}.txt.gz", _gz(block.encode("ascii", "replace")),
                                    "application/gzip")
            complete = len(good) >= want
            if complete:
                # markers carry content: a zero-length object reads as absent
                store.put(done, marker_body(len(good), nbytes), "application/json")
            else:
                out["incomplete"] += 1
            out["new"] += 1
            log(kind=source, cycle=stamp, stations=len(good), expected=want, complete=complete,
                bytes=nbytes, blocks=nblocks, seconds=round(time.time() - t0, 1))
        except Exception as e:
            out["errors"] += 1
            log(kind=source, cycle=stamp, error=f"{type(e).__name__}: {e}")
    return out


# ---------------------------------------------------------------- observations
def merge_obs_rows(existing: dict, new_rows: list) -> tuple:
    """
    Upsert raw aviationweather.gov rows into a day file keyed by
    "{icaoId}|{obsTime}". A row whose rawOb differs from the stored one is a
    correction (or a re-decode); the stored version moves into the new row's
    `superseded` list with its receipt time, so the file carries the current
    text and the history of what it replaced. Returns (merged, added, updated).
    """
    rows = dict(existing.get("rows", {}))
    added = updated = 0
    for ob in new_rows:
        sid, t = ob.get("icaoId"), ob.get("obsTime")
        if not sid or t is None:
            continue
        k = f"{sid}|{t}"
        old = rows.get(k)
        if old is None:
            rows[k] = ob
            added += 1
        elif old.get("rawOb") != ob.get("rawOb"):
            prior = list(old.get("superseded", []))
            prior.append({kk: vv for kk, vv in old.items() if kk != "superseded"})
            ob = dict(ob)
            ob["superseded"] = prior
            rows[k] = ob
            updated += 1
    merged = dict(existing)
    merged["rows"] = rows
    return merged, added, updated


def _new_day_file(day: str) -> dict:
    return {"utc_day": day,
            "source": "aviationweather.gov /api/data/metar, format=json",
            "decode": {"TEMP_SOURCE": gw.TEMP_SOURCE, "INCLUDE_SPECI": gw.INCLUDE_SPECI,
                       "note": "temp is tenths C when temp_source=tgroup, whole degrees C when body"},
            "rows": {}}


def obs_watermarks(store: Storage, now: dt.datetime) -> dict:
    """The newest observation time already on record PER STATION, from the
    three most recent day files. A station missing from the result has
    nothing on record in that window."""
    newest: dict = {}
    for h in (0, 24, 48):
        raw = _ungz(store.get(f"archive/obs/{(now - dt.timedelta(hours=h)).strftime('%Y%m%d')}.json.gz"))
        if not raw:
            continue
        for k, ob in json.loads(raw).get("rows", {}).items():
            sid, t = k.split("|", 1)[0], ob.get("obsTime")
            if t is not None and t > newest.get(sid, 0):
                newest[sid] = t
    return {sid: dt.datetime.fromtimestamp(t, dt.timezone.utc) for sid, t in newest.items()}


def obs_watermark(store: Storage, now: dt.datetime) -> Optional[dt.datetime]:
    """The newest observation time on record across all stations."""
    wms = obs_watermarks(store, now)
    return max(wms.values()) if wms else None


def archive_observations(store: Storage, stations: list, now: dt.datetime, log: Callable,
                         min_hours: int = OBS_MIN_H, max_hours: int = OBS_MAX_H) -> int:
    """
    Re-read the recent hours for every station and upsert into the UTC-day
    files. The window reaches back to the STALEST station's newest stored
    observation (plus an hour), between min_hours and max_hours, so a gap
    heals itself while the feed still holds the data, and a single station
    that fell behind is caught up with the rest. A gap longer than max_hours
    is reported (run record and manifest) and is for scripts/backfill_obs.py.
    On success the pull time is recorded as what the observation data is
    good to. Returns 1 on error, 0 otherwise.
    """
    wms = obs_watermarks(store, now)
    gaps = {sid: (now - wms[sid]).total_seconds() / 3600 for sid in stations if sid in wms}
    missing = [sid for sid in stations if sid not in wms]
    stalest = max(gaps.values()) if gaps else None
    if missing or stalest is None:
        hours = max_hours
    else:
        hours = int(max(min_hours, min(max_hours, stalest + 1)))
    unhealed = sorted([sid for sid, g in gaps.items() if g > max_hours])
    if hours > min_hours:
        log(kind="obs", note=f"stalest station gap {round(stalest or 0, 1)}h, {len(missing)} without record; pulling {hours}h")
    if unhealed:
        log(kind="obs", warning=f"gap longer than {max_hours}h at {unhealed}; run scripts/backfill_obs.py", stations=unhealed)
    truncated: list = []
    try:
        rows = gw.fetch_observations_raw(stations, hours, on_truncated=truncated.append)
    except Exception as e:
        log(kind="obs", error=f"{type(e).__name__}: {e}")
        return 1
    for ids in truncated:
        log(kind="obs", warning="batch hit the 400-row cap; history may be truncated", stations=ids)
    by_day: dict = {}
    for ob in rows:
        if ob.get("obsTime") is None:
            continue
        day = dt.datetime.fromtimestamp(ob["obsTime"], dt.timezone.utc).strftime("%Y%m%d")
        by_day.setdefault(day, []).append(ob)
    for day, day_rows in sorted(by_day.items()):
        key = f"archive/obs/{day}.json.gz"
        raw = _ungz(store.get(key))
        existing = json.loads(raw) if raw else _new_day_file(day)
        merged, added, updated = merge_obs_rows(existing, day_rows)
        if added or updated:
            merged["updated"] = now.isoformat()
            store.put(key, _gz(json.dumps(merged, separators=(",", ":")).encode()), "application/gzip")
        log(kind="obs", day=day, rows=len(merged["rows"]), added=added, updated=updated)
    store.put(OBS_FETCH_KEY, json.dumps({"fetchedAt": _iso_z(now), "hours": hours, "rows": len(rows),
                                         "unhealed": unhealed or None}).encode(), "application/json")
    return 0


def _iso_z(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------- health
def update_health(store: Storage, results: dict, now: dt.datetime) -> dict:
    """Per-source failure streaks, kept across passes so a source that fails
    quietly every pass becomes an alarm (see handler.py)."""
    raw = store.get(HEALTH_KEY)
    health = json.loads(raw) if raw else {}
    for source, r in results.items():
        h = health.setdefault(source, {"fail_streak": 0, "last_ok": None, "last_error": None})
        if r.get("ok"):
            h["fail_streak"] = 0
            h["last_ok"] = now.isoformat()
            h["last_error"] = None
        elif r.get("attempted", True):
            h["fail_streak"] = h.get("fail_streak", 0) + 1
            h["last_error"] = r.get("error")
    health["_updated"] = now.isoformat()
    store.put(HEALTH_KEY, json.dumps(health, indent=1).encode(), "application/json")
    return health


# ---------------------------------------------------------------- one pass
def one_pass(cfg: dict, store: Storage, sources: Optional[dict] = None,
             deadline_seconds: Optional[float] = None) -> int:
    """
    Run everything once. Exit status is nonzero only when nothing at all
    succeeded, so the scheduler flags a real outage but one flaky endpoint
    does not mark the run failed; per-source failure streaks are kept in
    health.json for the alarm path. The run record is written even when the
    pass stops early.
    """
    global LAST_STATUS
    gw.set_user_agent(cfg.get("user_agent", ""))
    sources = sources or cfg.get("sources", {})
    deadline = Deadline(deadline_seconds or remaining_budget(cfg, reserve=cfg.get("_reserve_seconds", 0.0)))
    now = dt.datetime.now(dt.timezone.utc)
    pulled = _stamp(now)
    entries: list = []
    t0 = time.time()

    def log(**kw):
        kw["pulled"] = now.isoformat()
        entries.append(kw)
        print(json.dumps(kw, default=str))

    stations = [c[0] for c in CITIES]
    results: dict = {}
    requests = errors = 0
    try:
        if sources.get("nws", True):
            grids = load_grids(store, now, log)
            r, e, s = archive_nws(store, grids, log, deadline)
            requests += r
            errors += e
            results["nws"] = {"ok": r > e and r > 0, "requests": r, "errors": e, "skipped": s,
                              "error": None if r > e else "all NWS requests failed"}
        for source in ("nbh", "nbs", "lamp", "mav"):
            enabled = sources.get("nbm", True) if source in ("nbh", "nbs") else sources.get(source, True)
            if not enabled:
                continue
            if deadline.over(90):
                results[source] = {"ok": False, "attempted": False, "error": "deadline"}
                log(kind=source, warning="deadline: not attempted")
                continue
            requests += 1
            out = archive_bulletins(store, source, stations, log, deadline, now)
            failed = out["errors"] and not out["new"]
            errors += 1 if failed else 0
            newest_done = store.exists(f"archive/_done/{source}_{gw.bulletin_cycles(source, 0, now)[0][0].strftime('%Y%m%dT%H30Z' if source == 'lamp' else '%Y%m%dT%H00Z')}")
            results[source] = {"ok": not failed, **out, "newestCycleDone": newest_done,
                               "error": "fetch failed" if failed else None}
        if sources.get("obs_record", False):
            requests += 1
            e = archive_observations(store, stations, now, log)
            errors += e
            results["obs"] = {"ok": e == 0, "error": "fetch failed" if e else None}
    finally:
        health = update_health(store, results, now)
        summary = {"pulled": now.isoformat(), "requests": requests, "errors": errors,
                   "seconds": round(time.time() - t0, 1), "storage": store.kind(),
                   "deadlineRemaining": None if deadline.end is None else round(deadline.remaining(), 1),
                   "sources": results,
                   "alarms": [s for s, h in health.items() if not s.startswith("_") and h.get("fail_streak", 0) >= FAIL_STREAK_ALARM],
                   "entries": entries}
        body = json.dumps(summary, default=str).encode()
        store.put(f"archive/_runs/{pulled}.json", body, "application/json")
        store.put("archive/_runs/latest.json", body, "application/json")
        LAST_STATUS = summary
    print(f"archive: {requests} requests, {errors} errors, {summary['seconds']}s, "
          f"alarms {summary['alarms'] or 'none'} -> {store.describe()}")
    return 1 if (errors == requests and requests) else 0


if __name__ == "__main__":
    from . import config, storage
    cfg = config.load()
    sys.exit(one_pass(cfg, storage.from_config(cfg)))
