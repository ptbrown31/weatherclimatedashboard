"""
build_map_features.py — the roads and city names on the hurricane map.

Reads Natural Earth's 1:10m roads and populated places (public domain, from
the Natural Earth project's own repository) and writes
site/assets/features-<basin>.json for each basin box the hurricane map draws:

  roads   [[class, [[lon, lat], ...]], ...]   1 = expressways and major
          highways, 2 = secondary highways; ferry routes and unclassed roads are
          left out. Lines are simplified to about two kilometres, which is finer
          than the map draws them at any zoom it allows.
  places  [[name, lon, lat, rank, capital], ...]   rank is Natural Earth's
          scalerank, 0 for the largest cities, which the page uses to decide
          which names a zoom level has room for.

The owner chose Natural Earth for roads and places on 2026-10-07. Like the
other generated assets this runs by hand and its output is checked in.

    python3 scripts/build_map_features.py            # both basins
    python3 scripts/build_map_features.py AL

Standard library only.
"""
from __future__ import annotations
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
ROADS = BASE + "ne_10m_roads.geojson"
PLACES = BASE + "ne_10m_populated_places_simple.geojson"
SOURCE = "Natural Earth 1:10m roads and populated places (public domain)"
BOXES = {"AL": (-101.0, 4.0, -40.0, 48.0), "EP": (-180.0, 0.0, -85.0, 40.0)}
TOLERANCE = 0.02          # degrees, the Douglas-Peucker tolerance
MAX_PLACE_RANK = 8        # names below this rank are too small for this map at any zoom


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "weather-tools-site map build"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode("utf-8"))


def road_class(p: dict):
    t, sr = p.get("type"), p.get("scalerank") or 99
    if t in ("Ferry Route", "Unknown") or t is None:
        return None
    if p.get("expressway") == 1 or t in ("Major Highway", "Beltway"):
        return 1 if sr <= 7 else None
    if t in ("Secondary Highway", "Bypass", "Road"):
        return 2 if sr <= 7 else None
    return None


def _perp(p, a, b):
    """Distance in degrees from p to the segment a-b."""
    (x, y), (x1, y1), (x2, y2) = p, a, b
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return ((x - x1) ** 2 + (y - y1) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
    return ((x - x1 - t * dx) ** 2 + (y - y1 - t * dy) ** 2) ** 0.5


def simplify(pts: list, tol: float) -> list:
    """Douglas-Peucker, iterative so a long road cannot exhaust the stack."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        far, k = -1.0, None
        for m in range(i + 1, j):
            d = _perp(pts[m], pts[i], pts[j])
            if d > far:
                far, k = d, m
        if k is not None and far > tol:
            keep[k] = True
            stack.append((i, k))
            stack.append((k, j))
    return [p for p, f in zip(pts, keep) if f]


def clip_runs(line: list, box) -> list:
    """The runs of a line that lie in the box, a vertex past each end kept so
    a road leaves the map at its edge instead of short of it."""
    w, s, e, n = box
    inside = [w <= x <= e and s <= y <= n for x, y in line]
    runs, cur = [], []
    for k, p in enumerate(line):
        near = inside[k] or (k > 0 and inside[k - 1]) or (k + 1 < len(line) and inside[k + 1])
        if near:
            cur.append(p)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return [r for r in runs if len(r) >= 2]


def build(basin: str, roads: dict, places: dict) -> dict:
    box = BOXES[basin]
    out_roads = []
    for ft in roads.get("features") or []:
        g, p = ft.get("geometry"), ft.get("properties") or {}
        cls = road_class(p)
        if not g or cls is None:
            continue
        lines = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
        for line in lines:
            for run in clip_runs(line, box):
                pts = simplify([(round(x, 3), round(y, 3)) for x, y in run], TOLERANCE)
                pts = [[round(x, 2), round(y, 2)] for x, y in pts]
                dedup = [pts[0]] + [q for a, q in zip(pts, pts[1:]) if q != a]
                if len(dedup) >= 2:
                    out_roads.append([cls, dedup])
    out_places = []
    w, s, e, n = box
    for ft in places.get("features") or []:
        p = ft.get("properties") or {}
        lon, lat = p.get("longitude"), p.get("latitude")
        rank = p.get("scalerank")
        if lon is None or lat is None or rank is None or rank > MAX_PLACE_RANK:
            continue
        if not (w <= lon <= e and s <= lat <= n):
            continue
        out_places.append([p.get("nameascii") or p.get("name"), round(lon, 3), round(lat, 3), int(rank),
                           1 if p.get("adm0cap") else 0])
    out_places.sort(key=lambda r: (r[3], r[0]))
    return {"basin": basin, "box": list(box), "source": SOURCE, "roads": out_roads, "places": out_places}


def main(argv=None) -> int:
    basins = (argv if argv is not None else sys.argv[1:]) or sorted(BOXES)
    roads, places = _get_json(ROADS), _get_json(PLACES)
    for b in basins:
        doc = build(b, roads, places)
        path = os.path.join(ROOT, "site", "assets", "features-%s.json" % b)
        with open(path, "w") as f:
            json.dump(doc, f, separators=(",", ":"))
        print("%s: %d road runs (%d major), %d places, %d bytes -> %s"
              % (b, len(doc["roads"]), sum(1 for r in doc["roads"] if r[0] == 1), len(doc["places"]),
                 os.path.getsize(path), os.path.relpath(path, ROOT)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
