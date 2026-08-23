"""
basemap.py — the map geometry, projected once into static assets.

The national map uses an Albers equal-area conic projection, the standard
CONUS projection. The projection is hand-rolled here so the site has no
mapping dependency, and it is run ONCE by scripts/build_assets.py, not by the
scheduled jobs: the output is static (state outlines, the world picker, the
station screen positions, the grid of cells inside the CONUS outline that the
forecast shading is computed on). The jobs read the small derived files.

Inputs are vendored public-domain TopoJSON: us-atlas (Census Bureau
cartographic boundaries) and world-atlas (Natural Earth).

Outputs:
    site/assets/basemap.json        statePaths, statesByName, stateCenters, nationLonLat,
                                    worldPaths, viewBox
    site/assets/hurricane-geo.json  countries in the Atlantic box, coastal states, centroids
    config/cities.json              the roster with px/py (CONUS map) and wx/wy (world picker)
    config/field_grid.json          the shading cells and the screen transform
"""
from __future__ import annotations
import csv
import json
import math
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEO = os.path.join(ROOT, "geo")                  # vendored TopoJSON inputs; not published
ASSETS = os.path.join(ROOT, "site", "assets")   # the small projected outputs pages load
CONFIG = os.path.join(ROOT, "config")
# world-atlas abbreviates two names the pages and any future market roster use in full
CNAME = {"Dominican Rep.": "Dominican Republic", "Bahamas": "Bahamas, The"}

W, H, PAD = 960.0, 600.0, 12.0            # the CONUS viewBox
WW, WH = 960.0, 480.0                     # the world picker viewBox
STEP = 12.0                               # shading cell size, screen px
SKIP = {"02", "15", "60", "66", "69", "72", "78"}   # AK, HI, territories (Hawaii is on the picker)
HBOX = [-101.0, 4.0, -40.0, 48.0]         # the Atlantic hurricane box: lon0, lat0, lon1, lat1
# (state FIPS, county name) -> the label the landfall contract uses
COUNTIES = {("48", "Harris"): "Harris, Texas", ("12", "Broward"): "Broward, Florida",
            ("12", "Palm Beach"): "Palm Beach, Florida", ("12", "Miami-Dade"): "Miami-Dade, Florida",
            ("12", "Hillsborough"): "Hillsborough, Florida", ("12", "Lee"): "Lee, Florida"}
COASTAL = {"Maine", "New Hampshire", "Massachusetts", "Rhode Island", "Connecticut",
           "New York", "New Jersey", "Pennsylvania", "Delaware", "Maryland", "Virginia",
           "North Carolina", "South Carolina", "Georgia", "Florida", "Alabama",
           "Mississippi", "Louisiana", "Texas"}

# Albers equal-area conic, standard parallels 29.5 / 45.5, origin 37.5N 96W
P1, P2, LAT0, LON0 = map(math.radians, (29.5, 45.5, 37.5, -96.0))
N = (math.sin(P1) + math.sin(P2)) / 2
C = math.cos(P1) ** 2 + 2 * N * math.sin(P1)
RHO0 = math.sqrt(C - 2 * N * math.sin(LAT0)) / N


def albers(lon: float, lat: float) -> tuple:
    lam, phi = math.radians(lon), math.radians(lat)
    rho = math.sqrt(max(C - 2 * N * math.sin(phi), 1e-12)) / N
    th = N * (lam - LON0)
    return rho * math.sin(th), RHO0 - rho * math.cos(th)


def world_xy(lon: float, lat: float) -> tuple:
    return round((lon + 180.0) / 360.0 * WW, 1), round((90.0 - lat) / 180.0 * WH, 1)


class Transform:
    """Projected coordinates -> screen pixels, fitted to the CONUS extent."""
    def __init__(self, S: float, TX: float, TY: float):
        self.S, self.TX, self.TY = S, TX, TY

    def screen(self, x: float, y: float) -> tuple:
        return (x * self.S + self.TX, self.TY - y * self.S)

    def project(self, lon: float, lat: float) -> tuple:
        return self.screen(*albers(lon, lat))

    def to_json(self) -> dict:
        return {"S": self.S, "TX": self.TX, "TY": self.TY, "W": W, "H": H, "PAD": PAD, "STEP": STEP}

    @classmethod
    def from_json(cls, d: dict) -> "Transform":
        return cls(d["S"], d["TX"], d["TY"])


def decode_topo(path: str):
    T = json.load(open(path))
    trf = T["transform"]

    def dec(arc):
        ax = ay = 0
        out = []
        for dx, dy in arc:
            ax += dx
            ay += dy
            out.append((ax * trf["scale"][0] + trf["translate"][0],
                        ay * trf["scale"][1] + trf["translate"][1]))
        return out

    arcs = [dec(a) for a in T["arcs"]]

    def ring(idxs):
        pts = []
        for i in idxs:
            a = arcs[~i][::-1] if i < 0 else arcs[i]
            pts.extend(a if not pts else a[1:])
        return pts

    return T, ring


def geom_rings(g, ring_fn):
    arcsets = [g["arcs"]] if g["type"] == "Polygon" else g["arcs"]
    return [ring_fn(r) for poly in arcsets for r in poly]


def simplify(ring, tol=0.05):
    out, last = [], None
    for lon, lat in ring:
        if last is None or abs(lon - last[0]) + abs(lat - last[1]) >= tol:
            out.append([round(lon, 2), round(lat, 2)])
            last = (lon, lat)
    return out


def path_of(tr: Transform, ring, tol=0.35) -> str:
    d, last = [], None
    for x, y in ring:
        sx, sy = tr.screen(x, y)
        if last and abs(sx - last[0]) < tol and abs(sy - last[1]) < tol:
            continue
        d.append(f"{'M' if not d else 'L'}{sx:.1f},{sy:.1f}")
        last = (sx, sy)
    return "".join(d) + "Z" if len(d) > 2 else ""


def point_in_rings(x: float, y: float, rings) -> bool:
    hit = False
    for r in rings:
        b = r["bbox"]
        if not (b[0] <= x <= b[2] and b[1] <= y <= b[3]):
            continue
        inn = False
        pts = r["pts"]
        j = len(pts) - 1
        for i in range(len(pts)):
            xi, yi = pts[i]
            xj, yj = pts[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inn = not inn
            j = i
        if inn:
            hit = not hit
    return hit


def build_assets(cities: list) -> dict:
    """Project everything and write the four output files. `cities` is the
    roster as (sid, name, lat, lon, tz, unit) tuples."""
    topo, ring_coords = decode_topo(os.path.join(GEO, "states-10m.json"))
    polys = []
    for g in topo["objects"]["states"]["geometries"]:
        if g["properties"].get("name") is None or g["id"] in SKIP:
            continue
        for ring in geom_rings(g, ring_coords):
            polys.append((g["properties"]["name"], [albers(x, y) for x, y in ring]))

    allp = [p for _, r in polys for p in r]
    minx, maxx = min(p[0] for p in allp), max(p[0] for p in allp)
    miny, maxy = min(p[1] for p in allp), max(p[1] for p in allp)
    S = min((W - 2 * PAD) / (maxx - minx), (H - 2 * PAD) / (maxy - miny))
    TX = PAD + (W - 2 * PAD - (maxx - minx) * S) / 2 - minx * S
    TY = PAD + (H - 2 * PAD - (maxy - miny) * S) / 2 + maxy * S
    tr = Transform(S, TX, TY)

    paths = [p for _, r in polys if (p := path_of(tr, r))]
    state_paths = " ".join(paths)

    by_state, centers = {}, {}
    for nm, ring in polys:
        p = path_of(tr, ring)
        if p:
            by_state[nm] = by_state.get(nm, "") + p
            if nm not in centers or len(ring) > centers[nm][0]:
                xs = [tr.screen(x, y)[0] for x, y in ring]
                ys = [tr.screen(x, y)[1] for x, y in ring]
                centers[nm] = (len(ring), round(sum(xs) / len(xs), 1), round(sum(ys) / len(ys), 1))
    state_centers = {nm: [v[1], v[2]] for nm, v in centers.items()}

    # the cells the forecast shading is computed on: every STEP-sized cell
    # whose centre lies inside a state outline
    state_rings = {}
    for nm, ring in polys:
        pts = [tr.screen(x, y) for x, y in ring]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        state_rings.setdefault(nm, []).append({"pts": pts, "bbox": (min(xs), min(ys), max(xs), max(ys))})

    def inside_conus(x, y):
        return any(point_in_rings(x, y, rings) for rings in state_rings.values())

    cells = []
    gy = PAD
    while gy < H - PAD:
        gx = PAD
        while gx < W - PAD:
            if inside_conus(gx + STEP / 2, gy + STEP / 2):
                cells.append([round(gx, 1), round(gy, 1)])
            gx += STEP
        gy += STEP

    # nation coastline in lon/lat for the hurricane panel (CONUS and Hawaii)
    nation = []
    for g in topo["objects"]["nation"]["geometries"]:
        for ring in geom_rings(g, ring_coords):
            lons = [p[0] for p in ring]
            lats = [p[1] for p in ring]
            conus = max(lons) > -130 and max(lats) < 51 and min(lats) > 22
            hawaii = -162 < min(lons) and max(lons) < -154 and max(lats) < 23.5
            if (conus or hawaii) and len(ring) > 8:
                r = simplify(ring)
                if len(r) > 6:
                    nation.append(r)

    # world picker (Natural Earth via world-atlas), coarse: it is a click target
    wtopo, wring = decode_topo(os.path.join(GEO, "countries-50m.json"))
    wpaths = []
    for g in wtopo["objects"]["countries"]["geometries"]:
        for ring in geom_rings(g, wring):
            rr = simplify(ring, 0.5)
            if len(rr) < 4:
                continue
            d, last = [], None
            for lon, lat in rr:
                x, y = (lon + 180.0) / 360.0 * WW, (90.0 - lat) / 180.0 * WH
                if last and abs(x - last[0]) < 0.8 and abs(y - last[1]) < 0.8:
                    continue
                d.append(f"{'M' if not d else 'L'}{x:.1f},{y:.1f}")
                last = (x, y)
            if len(d) > 3:
                wpaths.append("".join(d) + "Z")

    # hurricane panel geography: countries in the Atlantic box and the coastal states, lon/lat
    def in_hbox(ring):
        return (max(p[0] for p in ring) >= HBOX[0] and min(p[0] for p in ring) <= HBOX[2]
                and max(p[1] for p in ring) >= HBOX[1] and min(p[1] for p in ring) <= HBOX[3])

    def centroid(rings_ll):
        big = max(rings_ll, key=len)
        return [round(sum(p[0] for p in big) / len(big), 2), round(sum(p[1] for p in big) / len(big), 2)]

    countries, ccent = {}, {}
    for g in wtopo["objects"]["countries"]["geometries"]:
        nm = g["properties"]["name"]
        if nm == "United States of America":
            continue
        rr = [simplify(r, 0.04) for r in geom_rings(g, wring) if in_hbox(r)]
        rr = [r for r in rr if len(r) > 5]
        if rr:
            nm = CNAME.get(nm, nm)
            countries[nm] = rr
            ccent[nm] = centroid(rr)
    states_ll, scent = {}, {}
    for g in topo["objects"]["states"]["geometries"]:
        nm = g["properties"].get("name")
        if nm not in COASTAL:
            continue
        rr = [simplify(r, 0.03) for r in geom_rings(g, ring_coords)]
        rr = [r for r in rr if len(r) > 5]
        if rr:
            states_ll[nm] = rr
            scent[nm] = centroid(rr)

    # the counties the landfall contracts name, from the Census county boundaries
    # (us-atlas counties-10m; ids are FIPS codes, the state being the first two digits)
    ctopo, cring = decode_topo(os.path.join(GEO, "counties-10m.json"))
    counties, cocent = {}, {}
    for g in ctopo["objects"]["counties"]["geometries"]:
        fid = str(g["id"]).zfill(5)
        label = COUNTIES.get((fid[:2], g["properties"].get("name")))
        if not label:
            continue
        rr = [simplify(r, 0.01) for r in geom_rings(g, cring)]
        rr = [r for r in rr if len(r) > 4]
        if rr:
            counties[label] = rr
            cocent[label] = centroid(rr)

    # the exchange's hurricane wind reference locations (the vendor's registry,
    # vendored as geo/reask_locations.csv): id, name and position only
    locations = []
    with open(os.path.join(GEO, "reask_locations.csv"), newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                locations.append({"id": row["ID"].strip(), "name": row["Display Location"].strip(),
                                  "lat": round(float(row["Latitude"]), 4), "lon": round(float(row["Longitude"]), 4),
                                  "region": row.get("Region Group", "").strip(), "country": row.get("Country / Territory", "").strip(),
                                  "state": row.get("Admin / State", "").strip()})
            except (KeyError, ValueError):
                continue

    roster = []
    for sid, name, lat, lon, tzname, unit in cities:
        px, py = tr.project(lon, lat)
        wx, wy = world_xy(lon, lat)
        roster.append({"station": sid, "city": name, "lat": lat, "lon": lon, "tz": tzname, "unit": unit,
                       "px": round(px, 1), "py": round(py, 1), "wx": wx, "wy": wy,
                       "onConus": unit == "F" and sid != "PHNL"})

    os.makedirs(ASSETS, exist_ok=True)
    os.makedirs(CONFIG, exist_ok=True)
    # three assets, each loaded only by the pages that need it: the CONUS
    # outline (map and city pickers), the world picker (city page), and the
    # hurricane geography with the nation coastline (hurricane page)
    with open(os.path.join(ASSETS, "basemap.json"), "w") as fh:
        json.dump({"viewBox": f"0 0 {W:.0f} {H:.0f}", "statePaths": state_paths}, fh, separators=(",", ":"))
    with open(os.path.join(ASSETS, "world.json"), "w") as fh:
        json.dump({"viewBox": f"0 0 {WW:.0f} {WH:.0f}", "worldPaths": " ".join(wpaths)}, fh, separators=(",", ":"))
    with open(os.path.join(ASSETS, "hurricane-geo.json"), "w") as fh:
        json.dump({"bbox": HBOX, "asof": "Census Bureau cartographic boundaries (us-atlas) and Natural Earth (world-atlas), public domain",
                   "countries": countries, "states": states_ll, "counties": counties,
                   "centroids": {**ccent, **scent, **cocent}, "nation": nation,
                   "locations": locations}, fh, separators=(",", ":"))
    with open(os.path.join(CONFIG, "cities.json"), "w") as fh:
        json.dump(roster, fh, indent=1)
    with open(os.path.join(CONFIG, "field_grid.json"), "w") as fh:
        json.dump({"transform": tr.to_json(), "cells": cells}, fh, separators=(",", ":"))
    return {"rings": len(paths), "statePathsKB": round(len(state_paths) / 1024), "cells": len(cells),
            "nationRings": len(nation), "worldKB": round(len(" ".join(wpaths)) / 1024),
            "countries": len(countries), "coastalStates": len(states_ll), "counties": len(counties),
            "locations": len(locations)}


def load_roster() -> list:
    with open(os.path.join(CONFIG, "cities.json")) as fh:
        return json.load(fh)


def load_field_grid() -> dict:
    with open(os.path.join(CONFIG, "field_grid.json")) as fh:
        return json.load(fh)


def idw_field(grid: dict, stations: list, key_hi: str, key_lo: str) -> dict:
    """
    The landing map's pale shading: inverse-distance weighting of the listed
    stations' NWS forecasts in screen space, over the precomputed CONUS cells.
    It is DERIVED content and labelled so on the page. The NDFD grid itself
    (NOAA Open Data, GRIB2) would be the official field; this keeps the site
    to the calls already made.
        -> {"step", "cells": [[x, y, high, low]], "domain": [lo, hi]}
    """
    pts = [s for s in stations if s.get("onConus") and s.get(key_hi) is not None]
    cells = []
    for gx, gy in grid["cells"]:
        cx, cy = gx + STEP / 2, gy + STEP / 2
        num_h = num_l = den = 0.0
        for s in pts:
            d2 = (s["px"] - cx) ** 2 + (s["py"] - cy) ** 2 + 900.0   # soften near stations
            w = 1.0 / d2
            num_h += w * s[key_hi]
            lo = s.get(key_lo)
            num_l += w * (lo if lo is not None else s[key_hi] - 18)
            den += w
        if den:
            cells.append([gx, gy, round(num_h / den, 1), round(num_l / den, 1)])
    vals = [v for c in cells for v in c[2:]]
    dom = [5 * math.floor(min(vals) / 5), 5 * math.ceil(max(vals) / 5)] if vals else [40, 100]
    return {"step": STEP, "cells": cells, "domain": dom, "stations": len(pts)}
