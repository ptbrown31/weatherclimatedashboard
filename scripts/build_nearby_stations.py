"""Discover the METAR stations near each US roster station.

The city pages overlay live observations from the stations around the
resolving one, and this writes the roster those overlays read:
config/nearby_stations.json, one list per US contract station, from one
aviationweather.gov bbox query each. The frame matches the regional locator
image (locator.REGION_HALF_W_KM by REGION_HALF_H_KM), so every station on
the list can be drawn on the map.

Station lists change rarely (a field opens or closes); rerun when the
roster changes or yearly:

    python3 scripts/build_nearby_stations.py
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pipeline import basemap, locator  # noqa: E402

OUT = os.path.join(ROOT, "config", "nearby_stations.json")
API = "https://aviationweather.gov/api/data/metar"


def bbox(lat: float, lon: float) -> str:
    dlat = locator.REGION_HALF_H_KM / 110.574
    dlon = locator.REGION_HALF_W_KM / (111.320 * max(0.15, math.cos(math.radians(lat))))
    return f"{lat - dlat:.4f},{lon - dlon:.4f},{lat + dlat:.4f},{lon + dlon:.4f}"


def fetch(lat: float, lon: float) -> list:
    q = {"bbox": bbox(lat, lon), "format": "json"}
    req = urllib.request.Request(API + "?" + urllib.parse.urlencode(q),
                                 headers={"User-Agent": "weather-tools-site build (see config)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main() -> int:
    roster = [c for c in basemap.load_roster() if c.get("unit") == "F"]
    out = {}
    for c in roster:
        sid = c["station"]
        rows = fetch(c["lat"], c["lon"])
        near = []
        seen = set()
        for r in rows:
            nid = r.get("icaoId")
            if not nid or nid in seen or nid == sid:
                continue
            seen.add(nid)
            near.append({"id": nid, "name": (r.get("name") or "").split(",")[0][:40],
                         "lat": round(float(r.get("lat")), 4), "lon": round(float(r.get("lon")), 4)})
        near.sort(key=lambda n: (n["lat"] - c["lat"]) ** 2 + (n["lon"] - c["lon"]) ** 2)
        out[sid] = near
        print(f"{sid}: {len(near)} neighbours")
        time.sleep(0.4)
    doc = {"schema": 1, "source": "aviationweather.gov METAR bbox discovery",
           "frameHalfWKm": locator.REGION_HALF_W_KM, "frameHalfHKm": locator.REGION_HALF_H_KM,
           "stations": out}
    with open(OUT, "w") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    uniq = {n["id"] for v in out.values() for n in v}
    print(f"wrote {OUT}: {sum(len(v) for v in out.values())} rows, {len(uniq)} unique stations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
