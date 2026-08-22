"""
hurricane.py — the hurricane snapshot, every 30 minutes.

Two feeds. CurrentStorms.json is NHC's roster of active cyclones with
advisory metadata and links. The geometry comes from NOAA's public ArcGIS
service as GeoJSON (mapservices.weather.noaa.gov, an NWS host): forecast
points, track, cone, past track, and the seven-day development regions with
formation odds. NHC's own GIS products are shapefile and KMZ only, so this
service is what keeps the page decoder-free.

Checked 2026-08-21: the service's advisory number can trail the roster's by
one (36A against 038 for Hurricane Lala at 23Z), so the snapshot records both
and the page shows the geometry's own advisory. Product files land 25-30
minutes before the advisory's nominal issuance time.

Season-to-date counts come from the ATCF best-track files: a storm counts as
named once its best-track peak reaches 34 kt, a hurricane at 64 kt, a major
at 96 kt. Cyclone numbers 01-49 are real systems; 80-89 are test entries and
90+ are invest areas.

Writes snapshots/hurricane.json.
"""
from __future__ import annotations
import datetime as dt
import json
import re
import time
from typing import Callable

from . import gov_weather as gw
from .storage import Storage
from .snapshots import SNAP_CACHE, SCHEMA, _iso

ATCF_BTK = "https://ftp.nhc.noaa.gov/atcf/btk/"


def _round(x, nd=2):
    if isinstance(x, (int, float)):
        return round(x, nd)
    return [_round(v, nd) for v in x]


def season_counts(year: int) -> dict:
    """Atlantic season so far from the best tracks. The directory listing
    names each file twice (href and text), so collect a set."""
    idx = gw._get_text(ATCF_BTK, timeout=60)
    nums = sorted({int(m.group(1)) for m in re.finditer(rf"bal(\d\d){year}\.dat", idx)})
    out = {"named": 0, "hurricanes": 0, "majors": 0, "names": [], "year": year}
    for num in nums:
        if not 1 <= num <= 49:
            continue
        body = gw._get_text(f"{ATCF_BTK}bal{num:02d}{year}.dat", timeout=60)
        vmax, name = 0, ""
        for ln in body.splitlines():
            f = [p.strip() for p in ln.split(",")]
            if len(f) > 27:
                try:
                    vmax = max(vmax, int(f[8]))
                except ValueError:
                    pass
                if f[27]:
                    name = f[27]
        if vmax >= 34:
            out["named"] += 1
            out["names"].append(name.title() or f"#{num}")
        if vmax >= 64:
            out["hurricanes"] += 1
        if vmax >= 96:
            out["majors"] += 1
    return out


def hurricane_pass(cfg: dict, store: Storage) -> int:
    gw.set_user_agent(cfg.get("user_agent", ""))
    now = dt.datetime.now(dt.timezone.utc)
    t0 = time.time()
    snap = {"schema": SCHEMA, "asof": _iso(now), "storms": [], "outlook": [], "season": None, "errors": []}
    try:
        for s in gw.fetch_current_storms():
            b = s["binNumber"]
            pts = gw.fetch_nhc_layer(f"{b} Forecast Points")
            trk = gw.fetch_nhc_layer(f"{b} Forecast Track")
            cone = gw.fetch_nhc_layer(f"{b} Forecast Cone")
            past = gw.fetch_nhc_layer(f"{b} Past Track")
            geom_adv = next((f["properties"].get("advisnum") for f in cone + trk if f.get("properties")), None)
            snap["storms"].append({
                "id": s["id"], "bin": b, "name": s["name"], "classification": s["classification"],
                "intensityKt": int(s["intensity"]), "pressureMb": s.get("pressure"),
                "lat": s.get("latitudeNumeric"), "lon": s.get("longitudeNumeric"),
                "movementDir": s.get("movementDir"), "movementKt": s.get("movementSpeed"),
                "basin": s["id"][:2].upper(), "updated": s.get("lastUpdate"),
                "advisory": (s.get("publicAdvisory") or {}).get("advNum"),
                "advisoryUrl": (s.get("publicAdvisory") or {}).get("url"),
                "geometryAdvisory": geom_adv,
                "points": [{"lon": _round(f["geometry"]["coordinates"][0]), "lat": _round(f["geometry"]["coordinates"][1]),
                            "kt": f["properties"].get("maxwind"), "type": f["properties"].get("stormtype"),
                            "label": f["properties"].get("datelbl"), "tau": f["properties"].get("tau")}
                           for f in pts if f.get("geometry")],
                "track": [_round(f["geometry"]["coordinates"]) for f in trk if f.get("geometry")],
                "cone": [_round(f["geometry"]["coordinates"]) for f in cone if f.get("geometry")],
                "past": [_round(f["geometry"]["coordinates"]) for f in past if f.get("geometry")],
            })
        for f in gw.fetch_nhc_layer("Seven-Day: Potential Development Region"):
            p = f["properties"]
            snap["outlook"].append({"basin": p.get("basin"), "prob2": p.get("prob2day"), "prob7": p.get("prob7day"),
                                    "region": _round(f["geometry"]["coordinates"])})
    except Exception as e:
        snap["errors"].append(f"nhc: {type(e).__name__}: {e}")
    try:
        snap["season"] = season_counts(now.year)
    except Exception as e:
        snap["errors"].append(f"season: {type(e).__name__}: {e}")
    store.put("snapshots/hurricane.json", json.dumps(snap, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
    print(json.dumps({"kind": "hurricane", "storms": len(snap["storms"]), "outlook": len(snap["outlook"]),
                      "season": snap["season"], "errors": snap["errors"], "seconds": round(time.time() - t0, 1)}))
    return 1 if len(snap["errors"]) == 2 else 0
