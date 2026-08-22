"""
normals.py — NCEI daily climate normals for the US stations, once a week.

NCEI's Access Data Service answers without a token. The station id the
service uses is the GHCN-Daily id (USW00023174 for Los Angeles Intl), not
the ICAO code, so the job resolves each airport once through the service's
station search (a bounding box around the station; the first-order USW
station nearest to the airport coordinates, with a current end date) and
caches the mapping in archive/_meta/ghcn.json with the distance, so the
match is auditable.

Normals are the 2006-2020 daily normals (NCEI's current 15-year set), one
row per day of the year: normal high, normal low, and their standard
deviations. They refresh weekly; they change once a decade.

Writes snapshots/normals/{STATION}.json = {"station", "ghcn", "name",
"asof", "days": {"MM-DD": {"tmax", "tmin", "tmaxSd", "tminSd"}}}.
"""
from __future__ import annotations
import datetime as dt
import json
import math
import time
import urllib.parse
from typing import Optional

from . import gov_weather as gw
from . import basemap
from .storage import Storage

GHCN_KEY = "archive/_meta/ghcn.json"
SEARCH = "https://www.ncei.noaa.gov/access/services/search/v1/data"
DATA = "https://www.ncei.noaa.gov/access/services/data/v1"
NORMALS_DATASET = "normals-daily-2006-2020"
REFRESH_DAYS = 7
BOX = 0.2                       # degrees around the airport for the station search


def _dist_km(lat1, lon1, lat2, lon2) -> float:
    p = math.pi / 180
    a = 0.5 - math.cos((lat2 - lat1) * p) / 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2
    return 12742 * math.asin(math.sqrt(a))


def resolve_ghcn(city: dict, today: dt.date) -> Optional[dict]:
    """The nearest first-order (USW) GHCN-Daily station with recent data."""
    q = urllib.parse.urlencode({"dataset": "daily-summaries", "limit": 25,
                                "bbox": f"{city['lat'] + BOX},{city['lon'] - BOX},{city['lat'] - BOX},{city['lon'] + BOX}"})
    j = gw._get(f"{SEARCH}?{q}") or {}
    best = None
    for r in j.get("results", []):
        for s in r.get("stations", []):
            sid = s.get("id", "")
            if not sid.startswith("USW"):
                continue
            end = (r.get("endDate") or "")[:10]
            try:
                if (today - dt.date.fromisoformat(end)).days > 45:
                    continue
            except ValueError:
                continue
            loc = (r.get("location") or {}).get("coordinates") or [None, None]
            if loc[0] is None:
                continue
            d = _dist_km(city["lat"], city["lon"], loc[1], loc[0])
            if best is None or d < best["distanceKm"]:
                best = {"ghcn": sid, "name": s.get("name"), "lat": loc[1], "lon": loc[0], "distanceKm": round(d, 2),
                        "endDate": end, "resolved": today.isoformat()}
    return best


def fetch_normals(ghcn: str, year: int) -> dict:
    q = urllib.parse.urlencode({"dataset": NORMALS_DATASET, "stations": ghcn, "startDate": f"{year}-01-01",
                                "endDate": f"{year}-12-31", "format": "json", "units": "standard"})
    rows = gw._get(f"{DATA}?{q}") or []
    out = {}
    for r in rows:
        d = r.get("DATE")
        if not d:
            continue
        def num(k):
            v = r.get(k)
            try:
                f = float(v)
            except (TypeError, ValueError):
                return None
            return None if f <= -9000 else round(f, 1)
        out[d] = {"tmax": num("DLY-TMAX-NORMAL"), "tmin": num("DLY-TMIN-NORMAL"),
                  "tmaxSd": num("DLY-TMAX-STDDEV"), "tminSd": num("DLY-TMIN-STDDEV")}
    return out


def normals_pass(cfg: dict, store: Storage) -> int:
    gw.set_user_agent(cfg.get("user_agent", ""))
    now = dt.datetime.now(dt.timezone.utc)
    today = now.date()
    t0 = time.time()
    raw = store.get(GHCN_KEY)
    ghcn = json.loads(raw) if raw else {}
    changed = False
    written = skipped = errors = 0
    for c in basemap.load_roster():
        if c["unit"] != "F":
            continue
        sid = c["station"]
        try:
            if sid not in ghcn:
                m = resolve_ghcn(c, today)
                if not m:
                    print(json.dumps({"kind": "normals", "station": sid, "warning": "no first-order GHCN station found nearby"}))
                    continue
                ghcn[sid] = m
                changed = True
                time.sleep(0.5)
            key = f"snapshots/normals/{sid}.json"
            old = store.get(key)
            if old:
                asof = json.loads(old).get("asof")
                if asof and (now - dt.datetime.fromisoformat(asof.replace("Z", "+00:00"))).days < REFRESH_DAYS:
                    skipped += 1
                    continue
            days = fetch_normals(ghcn[sid]["ghcn"], today.year)
            if len(days) < 300:
                raise RuntimeError(f"only {len(days)} normal days returned")
            snap = {"station": sid, "ghcn": ghcn[sid]["ghcn"], "name": ghcn[sid]["name"], "distanceKm": ghcn[sid]["distanceKm"],
                    "dataset": NORMALS_DATASET, "asof": now.isoformat(timespec="seconds").replace("+00:00", "Z"), "days": days}
            store.put(key, json.dumps(snap, separators=(",", ":")).encode(), "application/json",
                      "public, max-age=86400, stale-if-error=2592000")
            written += 1
            time.sleep(0.5)
        except Exception as e:
            errors += 1
            print(json.dumps({"kind": "normals", "station": sid, "error": f"{type(e).__name__}: {e}"}))
    if changed:
        store.put(GHCN_KEY, json.dumps(ghcn, indent=1).encode(), "application/json")
    print(json.dumps({"kind": "normals", "written": written, "skipped": skipped, "errors": errors,
                      "resolved": len(ghcn), "seconds": round(time.time() - t0, 1)}))
    return 1 if errors and not written and not skipped else 0
