"""A metro-scale map of where each station actually sits.

A contract settles on one thermometer, and where that thermometer is decides
what it reads. Temperature varies across a city with land cover, distance from
the centre, shade, hills and water, so a station on the apron of an airport
south-west of a downtown is not measuring the downtown. An outline of the state
with a dot on it does not carry that; a map with the runways, the interstates
and the tree cover on it does.

The imagery is the US Geological Survey's National Map, which is a work of the
United States government and in the public domain. It is fetched here rather
than in the browser: this site's pages read only its own snapshots, and a page
that called a government endpoint for every reader would be both a different
architecture and a different load on that endpoint.

It is fetched once. Aerial imagery of an airport does not change on a schedule
this site cares about, so a station that already has a picture is skipped, and
the job is cheap to leave in the daily chain.

The image is centred on the station, so the marker a page draws is the middle of
the picture and no coordinate transform is needed on the page.

US stations only. The National Map covers the United States; everywhere else
keeps the outline map, which is honest about being an outline.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import time
import urllib.parse
import urllib.request
from typing import Callable, Dict, Optional

from . import archive as arch
from . import basemap
from .storage import Storage

SCHEMA = 1
SERVICE = ("https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo"
           "/MapServer/export")
KEY = "snapshots/locator/{sid}.png"
INDEX_KEY = "snapshots/locator/index.json"
# the picture never changes; the browser and the edge may hold it for a month
CACHE = "public, max-age=2592000, stale-while-revalidate=2592000, stale-if-error=2592000"
# about forty kilometres across: a station is usually at an airport well outside
# the centre, and the question is where it sits relative to that centre, so the
# frame has to hold both
HALF_W_KM = 20.0
HALF_H_KM = 12.5
SIZE = (760, 475)
# the regional frame: wide enough to hold the neighbouring METAR stations the
# live overlay draws, about seventy kilometres across
REGION_HALF_W_KM = 35.0
REGION_HALF_H_KM = 25.0
REGION_SIZE = (760, 543)
REGION_KEY = "snapshots/locator/{sid}_region.png"
SOURCE = "USGS The National Map: US Topo (public domain)"


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def bbox(lat: float, lon: float, half_w: float = HALF_W_KM, half_h: float = HALF_H_KM) -> str:
    """A box of a fixed size on the ground, not a fixed number of degrees.

    A degree of longitude is a kilometre and a half wider in Miami than in
    Seattle, so a box in degrees would show a different amount of ground at each
    station and the maps would not be comparable with each other.
    """
    dlat = half_h / 110.574
    dlon = half_w / (111.320 * max(0.15, math.cos(math.radians(lat))))
    return f"{lon - dlon:.6f},{lat - dlat:.6f},{lon + dlon:.6f},{lat + dlat:.6f}"


def fetch_image(lat: float, lon: float, timeout: int = 60,
                half_w: float = HALF_W_KM, half_h: float = HALF_H_KM,
                size: tuple = SIZE) -> bytes:
    q = {"bbox": bbox(lat, lon, half_w, half_h), "bboxSR": "4326", "imageSR": "3857",
         "size": f"{size[0]},{size[1]}", "format": "png", "transparent": "false", "f": "image"}
    url = SERVICE + "?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={"Accept": "image/png"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    if not data.startswith(b"\x89PNG"):
        raise ValueError("not a png")
    return data


def locator_pass(cfg: dict, store: Storage, fetch: Optional[Callable] = None) -> int:
    t0 = time.time()
    now = dt.datetime.now(dt.timezone.utc)
    deadline = arch.Deadline(arch.remaining_budget(cfg))
    getter = fetch or fetch_image

    roster = [c for c in basemap.load_roster() if c.get("unit") == "F"]
    written, skipped, errors = 0, 0, []
    index: Dict[str, dict] = {}
    prev_raw = store.get(INDEX_KEY)
    prev = json.loads(prev_raw).get("stations", {}) if prev_raw else {}

    for c in roster:
        sid = c["station"]
        if deadline.over(20):
            errors.append("deadline")
            break
        have = prev.get(sid)
        entry = dict(have) if have else None
        if not (have and store.get(KEY.format(sid=sid))):
            try:
                png = getter(c["lat"], c["lon"])
            except Exception as e:  # noqa: BLE001
                errors.append(f"{sid}: {type(e).__name__}")
                continue
            store.put(KEY.format(sid=sid), png, "image/png", CACHE)
            entry = {"w": SIZE[0], "h": SIZE[1], "halfWKm": HALF_W_KM, "halfHKm": HALF_H_KM,
                     "lat": c["lat"], "lon": c["lon"], "bytes": len(png), "written": _iso(now)}
            written += 1
        else:
            skipped += 1
        # the regional frame, same one-time semantics, wide enough for the
        # neighbouring stations the live overlay draws
        if not (entry.get("region") and store.get(REGION_KEY.format(sid=sid))):
            try:
                rpng = getter(c["lat"], c["lon"], half_w=REGION_HALF_W_KM,
                              half_h=REGION_HALF_H_KM, size=REGION_SIZE)
                store.put(REGION_KEY.format(sid=sid), rpng, "image/png", CACHE)
                entry["region"] = {"w": REGION_SIZE[0], "h": REGION_SIZE[1],
                                   "halfWKm": REGION_HALF_W_KM, "halfHKm": REGION_HALF_H_KM,
                                   "bytes": len(rpng), "written": _iso(now)}
            except Exception as e:  # noqa: BLE001
                errors.append(f"{sid} region: {type(e).__name__}")
        index[sid] = entry

    store.put(INDEX_KEY, json.dumps({"schema": SCHEMA, "asof": _iso(now), "source": SOURCE,
                                     "service": SERVICE, "stations": index},
                                    separators=(",", ":")).encode(), "application/json", CACHE)
    arch.LAST_STATUS = {"job": "locator", "errors": len(errors), "alarms": []}
    print(json.dumps({"kind": "locator", "stations": len(roster), "written": written,
                      "kept": skipped, "errors": errors[:5], "seconds": round(time.time() - t0, 1)}))
    return 0
