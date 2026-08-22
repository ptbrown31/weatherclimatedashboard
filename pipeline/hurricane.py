"""
hurricane.py — the hurricane snapshot, every 30 minutes.

Two feeds. CurrentStorms.json is NHC's roster of active cyclones with
advisory metadata and links. The geometry comes from NOAA's public ArcGIS
service as GeoJSON (mapservices.weather.noaa.gov, an NWS host): forecast
points, track, cone, past track, and the seven-day development regions with
formation odds. NHC's own GIS products are shapefile and KMZ only, so this
service is what keeps the page decoder-free.

Checked 2026-08-21: the service can lag the roster by several advisories
(Lala 36A on the service against 038 in the roster; Moke 4 against 006), so
each storm records both numbers and the page labels the drawn position from
the geometry (points[0]), naming the geometry's advisory. Product files land
25-30 minutes before the advisory's nominal issuance time.

Geometry is re-fetched only when it can have changed: when the roster's
advisory number or last-update time differs from what the previous snapshot
holds, or when the service was still trailing the roster last time. A storm
whose fetch fails keeps its previous geometry, flagged; the outlook and each
storm are fetched independently, so one failure cannot blank the rest. The
layer index is re-read every pass because NHC has renumbered layer ids.

Season-to-date counts come from the ATCF best-track files, computed once a
day: a system counts as named once a best-track row with a tropical or
subtropical cyclone type (TS, SS, HU) reaches 34 kt, a hurricane at 64 kt
and a major at 96 kt on a HU row. Cyclone numbers 01-49 are real systems;
80-89 are test entries and 90+ are invest areas.

Writes snapshots/hurricane.json.
"""
from __future__ import annotations
import datetime as dt
import json
import re
import time
from typing import Optional

from . import gov_weather as gw
from . import archive as arch
from .storage import Storage
from .snapshots import SNAP_CACHE, SCHEMA, _iso

ATCF_BTK = "https://ftp.nhc.noaa.gov/atcf/btk/"
NAMED_TYPES = {"TS", "SS", "HU"}


def _round(x, nd=2):
    if isinstance(x, (int, float)):
        return round(x, nd)
    return [_round(v, nd) for v in x]


def season_counts(year: int) -> dict:
    """Atlantic season so far from the best tracks. The directory listing
    names each file twice (href and text), so collect a set."""
    idx = gw._get_text(ATCF_BTK, timeout=60)
    nums = sorted({int(m.group(1)) for m in re.finditer(rf"bal(\d\d){year}\.dat", idx)})
    out = {"named": 0, "hurricanes": 0, "majors": 0, "names": [], "year": year,
           "computed": dt.datetime.now(dt.timezone.utc).date().isoformat()}
    for num in nums:
        if not 1 <= num <= 49:
            continue
        body = gw._get_text(f"{ATCF_BTK}bal{num:02d}{year}.dat", timeout=60)
        vmax_named = vmax_hu = 0
        name = ""
        for ln in body.splitlines():
            f = [p.strip() for p in ln.split(",")]
            if len(f) <= 27:
                continue
            try:
                v = int(f[8])
            except ValueError:
                continue
            ty = f[10]
            if ty in NAMED_TYPES:
                vmax_named = max(vmax_named, v)
                if f[27]:
                    name = f[27]
            if ty == "HU":
                vmax_hu = max(vmax_hu, v)
        if vmax_named >= 34:
            out["named"] += 1
            out["names"].append(name.title() or f"#{num}")
        if vmax_hu >= 64:
            out["hurricanes"] += 1
        if vmax_hu >= 96:
            out["majors"] += 1
    return out


def _fetch_geometry(b: str) -> dict:
    pts = gw.fetch_nhc_layer(f"{b} Forecast Points")
    trk = gw.fetch_nhc_layer(f"{b} Forecast Track")
    cone = gw.fetch_nhc_layer(f"{b} Forecast Cone")
    past = gw.fetch_nhc_layer(f"{b} Past Track")
    geom_adv = next((f["properties"].get("advisnum") for f in cone + trk if f.get("properties")), None)
    return {
        "geometryAdvisory": geom_adv, "geometryFetched": _iso(dt.datetime.now(dt.timezone.utc)),
        "points": [{"lon": _round(f["geometry"]["coordinates"][0]), "lat": _round(f["geometry"]["coordinates"][1]),
                    "kt": f["properties"].get("maxwind"), "type": f["properties"].get("stormtype"),
                    "label": f["properties"].get("datelbl"), "tau": f["properties"].get("tau")}
                   for f in pts if f.get("geometry")],
        "track": [_round(f["geometry"]["coordinates"]) for f in trk if f.get("geometry")],
        "cone": [_round(f["geometry"]["coordinates"]) for f in cone if f.get("geometry")],
        "past": [_round(f["geometry"]["coordinates"]) for f in past if f.get("geometry")],
    }


def hurricane_pass(cfg: dict, store: Storage) -> int:
    gw.set_user_agent(cfg.get("user_agent", ""))
    gw.reset_nhc_layers()
    now = dt.datetime.now(dt.timezone.utc)
    deadline = arch.Deadline(arch.remaining_budget(cfg))
    t0 = time.time()
    raw_prev = store.get("snapshots/hurricane.json")
    prev = json.loads(raw_prev) if raw_prev else {}
    prev_storms = {s["id"]: s for s in prev.get("storms", [])}
    snap = {"schema": SCHEMA, "asof": _iso(now), "written": _iso(now), "storms": [], "outlook": prev.get("outlook", []),
            "season": prev.get("season"), "errors": [], "reused": 0, "fetched": 0}
    ok_roster = True
    try:
        roster = gw.fetch_current_storms()
    except Exception as e:
        snap["errors"].append(f"roster: {type(e).__name__}: {e}")
        roster, ok_roster = [], False
        snap["storms"] = prev.get("storms", [])     # keep what was known
    for s in roster:
        b = s["binNumber"]
        adv = (s.get("publicAdvisory") or {}).get("advNum")
        storm = {
            "id": s["id"], "bin": b, "name": s["name"], "classification": s["classification"],
            "intensityKt": int(s["intensity"]), "pressureMb": s.get("pressure"),
            "lat": s.get("latitudeNumeric"), "lon": s.get("longitudeNumeric"),
            "movementDir": s.get("movementDir"), "movementKt": s.get("movementSpeed"),
            "basin": s["id"][:2].upper(), "updated": s.get("lastUpdate"), "advisory": adv,
            "advisoryUrl": (s.get("publicAdvisory") or {}).get("url"),
        }
        old = prev_storms.get(s["id"])
        roster_same = bool(old and old.get("advisory") == adv and old.get("updated") == s.get("lastUpdate") and old.get("cone"))
        in_sync = bool(old and old.get("geometryAdvisory") and str(old.get("geometryAdvisory")).lstrip("0") == str(adv or "").lstrip("0"))
        # while the service trails the roster, look again at most once an hour
        fetched_recently = False
        if old and old.get("geometryFetched"):
            try:
                fetched_recently = (now - dt.datetime.fromisoformat(old["geometryFetched"].replace("Z", "+00:00"))).total_seconds() < 3600
            except ValueError:
                fetched_recently = False
        unchanged = roster_same and (in_sync or fetched_recently)
        if unchanged and not deadline.over(0):
            for k in ("geometryAdvisory", "geometryFetched", "points", "track", "cone", "past"):
                storm[k] = old.get(k)
            snap["reused"] += 1
        else:
            try:
                storm.update(_fetch_geometry(b))
                snap["fetched"] += 1
            except Exception as e:
                snap["errors"].append(f"{s['id']}: {type(e).__name__}: {e}")
                if old:
                    for k in ("geometryAdvisory", "geometryFetched", "points", "track", "cone", "past"):
                        storm[k] = old.get(k)
                    storm["geometryStale"] = True
                else:
                    storm.update({"geometryAdvisory": None, "points": [], "track": [], "cone": [], "past": [], "geometryStale": True})
        snap["storms"].append(storm)
    if ok_roster:
        try:
            snap["outlook"] = [{"basin": f["properties"].get("basin"), "prob2": f["properties"].get("prob2day"),
                                "prob7": f["properties"].get("prob7day"), "region": _round(f["geometry"]["coordinates"])}
                               for f in gw.fetch_nhc_layer("Seven-Day: Potential Development Region") if f.get("geometry")]
            snap["outlookAsof"] = _iso(now)
        except Exception as e:
            snap["errors"].append(f"outlook: {type(e).__name__}: {e}")
            snap["outlookAsof"] = prev.get("outlookAsof")
    season_ok = snap["season"] and snap["season"].get("computed") == now.date().isoformat()
    if not season_ok and not deadline.over(60):
        try:
            snap["season"] = season_counts(now.year)
        except Exception as e:
            snap["errors"].append(f"season: {type(e).__name__}: {e}")
    if not ok_roster:
        snap["asof"] = prev.get("asof")            # the data is only as fresh as the last good roster
    store.put("snapshots/hurricane.json", json.dumps(snap, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
    health = arch.update_health(store, {"nhc": {"ok": ok_roster, "error": None if ok_roster else "roster fetch failed"}}, now)
    alarms = [s for s, h in health.items() if not s.startswith("_") and h.get("fail_streak", 0) >= arch.FAIL_STREAK_ALARM]
    arch.LAST_STATUS = {"job": "hurricane", "errors": len(snap["errors"]), "alarms": alarms}
    print(json.dumps({"kind": "hurricane", "storms": len(snap["storms"]), "fetched": snap["fetched"], "reused": snap["reused"],
                      "outlook": len(snap["outlook"]), "season": snap["season"], "errors": snap["errors"],
                      "seconds": round(time.time() - t0, 1)}))
    return 1 if not ok_roster else 0
