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
  archive/_done/{source}_{cycle}         markers for the bulk bulletins, one per cycle
  archive/_runs/{pulled}.json            one record per pass: counts, errors, timings

The stamp in a forecast filename is the forecast's own issuance time
(`updateTime`), not the pull time. `generatedAt` is the render time and
changes on every request; deduplicating on it would store every pull.
A cycle already stored is never written twice, which also makes a retried
scheduled run harmless. Observation day files are the one exception to
write-once: they are re-read and upserted each pass, because a corrected
report (COR) replaces the original under the same obsTime and the file must
carry the correction while keeping what it superseded.

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
GRID_MAX_AGE_H = 24            # the docs ask that the points mapping be re-checked periodically
OBS_WINDOW_H = 12              # each pass re-reads this much history so corrections land


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


# ---------------------------------------------------------------- grids
def load_grids(store: Storage, now: dt.datetime, log: Callable) -> dict:
    """Station -> forecast URLs, resolved through the points endpoint and
    cached; re-resolved once a day because grid and office can change."""
    raw = store.get(GRIDS_KEY)
    grids = json.loads(raw) if raw else {}
    resolved = grids.get("_resolved")
    stale = True
    if resolved:
        age = now - dt.datetime.fromisoformat(resolved)
        stale = age > dt.timedelta(hours=GRID_MAX_AGE_H)
    changed = False
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
        grids[sid] = {"hourly": g["hourly_url"], "daily": g["daily_url"],
                      "timezone": g["timezone"], "wfo": g["wfo"], "x": g["x"], "y": g["y"]}
        changed = True
    if changed or stale:
        grids["_resolved"] = now.isoformat()
        store.put(GRIDS_KEY, json.dumps(grids, indent=1).encode(), "application/json")
    return {k: v for k, v in grids.items() if not k.startswith("_")}


# ---------------------------------------------------------------- NWS
def archive_nws(store: Storage, grids: dict, log: Callable) -> tuple[int, int]:
    """Both NWS forecast products per US station, stored raw, one object per
    issuance. Returns (requests, errors)."""
    requests = errors = 0
    for sid, g in grids.items():
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
                log(station=sid, kind=kind, cycle=stamp, **({"file": key} if written else {"skipped": True}))
            except Exception as e:            # one station failing must not end the pass
                errors += 1
                log(station=sid, kind=kind, error=f"{type(e).__name__}: {e}")
    return requests, errors


# ---------------------------------------------------------------- bulletins
BULLETINS = {
    # source: (latest-cycle function, stamp format)
    "nbh":  (lambda: gw.latest_nbm_cycle(kind="nbh"), "%Y%m%dT%H00Z"),
    "nbs":  (lambda: gw.latest_nbm_cycle(kind="nbs"), "%Y%m%dT%H00Z"),
    "lamp": (gw.latest_lamp_cycle,                     "%Y%m%dT%H30Z"),
    "mav":  (gw.latest_mav_cycle,                      "%Y%m%dT%H00Z"),
}


def archive_bulletin(store: Storage, source: str, stations: list, log: Callable) -> int:
    """One bulk text bulletin: find the newest cycle, skip it if its marker
    exists, otherwise download once, cut every roster station's block and
    store each verbatim. Coverage varies by station (NBM has the Canadian
    stations, LAMP and MAV do not), so the cycle is deduplicated by a marker
    written after the whole bulletin was processed, not by counting files.
    Returns 1 on error, 0 otherwise."""
    latest, fmt = BULLETINS[source]
    try:
        cycle, url = latest()
        stamp = cycle.strftime(fmt)
        done = f"archive/_done/{source}_{stamp}"
        if store.exists(done):
            log(kind=source, cycle=stamp, skipped=True)
            return 0
        t0 = time.time()
        text = gw.fetch_bulletin_text(url)
        blocks = gw.station_blocks(text, stations, gw.BULLETIN_MARKER[source])
        for sid, block in blocks.items():
            store.put_if_absent(f"archive/{sid}/{source}_{stamp}.txt.gz", _gz(block.encode("ascii", "replace")),
                                "application/gzip")
        store.put(done, b"", "text/plain")
        log(kind=source, cycle=stamp, stations=len(blocks), bytes=len(text), seconds=round(time.time() - t0, 1))
        return 0
    except Exception as e:
        log(kind=source, error=f"{type(e).__name__}: {e}")
        return 1


# ---------------------------------------------------------------- observations
def merge_obs_rows(existing: dict, new_rows: list) -> tuple[dict, int, int]:
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


def archive_observations(store: Storage, stations: list, now: dt.datetime, log: Callable) -> int:
    """Re-read the last OBS_WINDOW_H hours for every station and upsert into
    the UTC-day files. Returns 1 on error, 0 otherwise."""
    truncated: list = []
    try:
        rows = gw.fetch_observations_raw(stations, OBS_WINDOW_H, on_truncated=truncated.append)
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
        existing = json.loads(raw) if raw else {
            "utc_day": day,
            "source": "aviationweather.gov /api/data/metar, format=json",
            "decode": {"TEMP_SOURCE": gw.TEMP_SOURCE, "INCLUDE_SPECI": gw.INCLUDE_SPECI,
                       "note": "temp is tenths C when temp_source=tgroup, whole degrees C when body"},
            "rows": {},
        }
        merged, added, updated = merge_obs_rows(existing, day_rows)
        if added or updated:
            merged["updated"] = now.isoformat()
            store.put(key, _gz(json.dumps(merged, separators=(",", ":")).encode()), "application/gzip")
        log(kind="obs", day=day, rows=len(merged["rows"]), added=added, updated=updated)
    return 0


# ---------------------------------------------------------------- one pass
def one_pass(cfg: dict, store: Storage, sources: Optional[dict] = None) -> int:
    """Run everything once. Exit status is nonzero only when nothing at all
    succeeded, so the scheduler flags a real outage but one flaky endpoint
    does not mark the run failed."""
    gw.set_user_agent(cfg.get("user_agent", ""))
    sources = sources or cfg.get("sources", {})
    now = dt.datetime.now(dt.timezone.utc)
    pulled = _stamp(now)
    entries: list = []
    t0 = time.time()

    def log(**kw):
        kw["pulled"] = now.isoformat()
        entries.append(kw)
        print(json.dumps(kw, default=str))

    stations = [c[0] for c in CITIES]
    requests = errors = 0

    if sources.get("nws", True):
        grids = load_grids(store, now, log)
        r, e = archive_nws(store, grids, log)
        requests += r
        errors += e

    for source in ("nbh", "nbs", "lamp", "mav"):
        enabled = sources.get("nbm", True) if source in ("nbh", "nbs") else sources.get(source, True)
        if enabled:
            requests += 1
            errors += archive_bulletin(store, source, stations, log)

    if sources.get("obs_record", True):
        requests += 1
        errors += archive_observations(store, stations, now, log)

    summary = {"pulled": now.isoformat(), "requests": requests, "errors": errors,
               "seconds": round(time.time() - t0, 1), "storage": store.describe(),
               "entries": entries}
    store.put(f"archive/_runs/{pulled}.json", json.dumps(summary, default=str).encode(), "application/json")
    print(f"archive: {requests} requests, {errors} errors, {summary['seconds']}s -> {store.describe()}")
    return 1 if (errors == requests and requests) else 0


if __name__ == "__main__":
    from . import config, storage
    cfg = config.load()
    sys.exit(one_pass(cfg, storage.from_config(cfg)))
