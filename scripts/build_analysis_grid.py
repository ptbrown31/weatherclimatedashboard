"""
build_analysis_grid.py — the analysis lane's two derived config files.

Run once, by hand, when geo/population_centres.csv or the basemap changes;
the scheduled job reads the outputs and never projects anything itself.

    python3 scripts/build_analysis_grid.py

    config/analysis_locations.json   the fifty places with their nearest cell on
                                     the wexp grid and on G184, the cell's own
                                     centre and its distance from the Census
                                     point, and the screen position (px, py) on
                                     the site's CONUS map
    config/analysis_lattice.json     the map lattice of docs/analysis.md section
                                     1: for each of 320 x 200 screen points, the
                                     grid cell under it on each grid, or -1 off
                                     the grid

The cell is the nearest grid cell to the point, fractional i and j rounded half
up, which is what pipeline.grib2.nearest_cell does. The lattice inverts the
site's fitted screen transform and its Albers projection (pipeline/basemap.py)
to a longitude and latitude, then goes forward through the Lambert projection
to a cell; both projections are on a sphere, so the round trip is exact to the
precision of the floats.
"""
from __future__ import annotations
import csv
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pipeline import basemap, grib2  # noqa: E402

CSV_PATH = os.path.join(ROOT, "geo", "population_centres.csv")
LOCATIONS_PATH = os.path.join(ROOT, "config", "analysis_locations.json")
LATTICE_PATH = os.path.join(ROOT, "config", "analysis_lattice.json")
EARTH_KM = 6371.2                 # the grids' own sphere (6,371,200 m), so distances are on the same earth
PITCH, COLS, ROWS = 3, 320, 200   # docs/analysis.md section 1: 960 x 600 at 3 px


def read_centres(path: str = CSV_PATH) -> list:
    """The vendored list, comment lines (#) skipped, numbers parsed."""
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(line for line in fh if not line.startswith("#")))
    out = []
    for r in rows:
        out.append({"id": r["id"].strip(), "name": r["name"].strip(), "state": r["state"].strip(),
                    "geoid": r["geoid"].strip(), "pop2024": int(r["pop2024"]),
                    "lat": float(r["lat"]), "lon": float(r["lon"]), "tz": r["tz"].strip(),
                    "note": (r.get("note") or "").strip()})
    return out


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlam = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(a))


def wrap_lon(lon: float) -> float:
    """Longitude in (-180, 180]: the grids are defined in degrees east and the
    site keeps every position in signed degrees."""
    return ((lon + 180.0) % 360.0) - 180.0


def albers_inverse(x: float, y: float):
    """Projected Albers coordinates back to (lon, lat), or None when the point
    is outside the projection's domain (the corners of the screen are)."""
    n, c, rho0, lon0 = basemap.N, basemap.C, basemap.RHO0, basemap.LON0
    sgn = 1.0 if n >= 0 else -1.0
    rho = sgn * math.sqrt(x * x + (rho0 - y) ** 2)
    theta = math.atan2(sgn * x, sgn * (rho0 - y))
    s = (c - rho * rho * n * n) / (2 * n)
    if s < -1.0 or s > 1.0:
        return None
    return math.degrees(lon0 + theta / n), math.degrees(math.asin(s))


def screen_to_lonlat(tr: basemap.Transform, sx: float, sy: float):
    return albers_inverse((sx - tr.TX) / tr.S, (tr.TY - sy) / tr.S)


def build_locations(centres: list, tr: basemap.Transform) -> list:
    out = []
    for c in centres:
        wexp = grib2.nearest_cell(grib2.WEXP, c["lat"], c["lon"])
        g184 = grib2.nearest_cell(grib2.G184, c["lat"], c["lon"])
        if wexp is None or g184 is None:
            raise SystemExit(f"{c['id']} is off the analysis grid ({c['lat']}, {c['lon']})")
        clat, clon = grib2.lcc_latlon(grib2.WEXP, wexp[0], wexp[1])
        clon = wrap_lon(clon)
        px, py = tr.project(c["lon"], c["lat"])
        row = dict(c)
        row.update({"px": round(px, 1), "py": round(py, 1),
                    "cell": {"wexp": list(wexp), "g184": list(g184),
                             "centre": [round(clat, 4), round(clon, 4)],
                             "distanceKm": round(haversine_km(c["lat"], c["lon"], clat, clon), 2)}})
        out.append(row)
    return out


def build_lattice(tr: basemap.Transform) -> dict:
    wexp, g184 = [], []
    for r in range(ROWS):
        for col in range(COLS):
            ll = screen_to_lonlat(tr, PITCH / 2.0 + PITCH * col, PITCH / 2.0 + PITCH * r)
            cw = cg = None
            if ll is not None:
                lon, lat = ll
                cw = grib2.nearest_cell(grib2.WEXP, lat, lon)
                cg = grib2.nearest_cell(grib2.G184, lat, lon)
            wexp.append(cw[2] if cw else -1)
            g184.append(cg[2] if cg else -1)
    return {"pitch": PITCH, "cols": COLS, "rows": ROWS,
            "viewBox": f"0 0 {basemap.W:.0f} {basemap.H:.0f}", "wexp": wexp, "g184": g184}


def sanity(locations: list, lattice: dict, tr: basemap.Transform) -> dict:
    """The lattice point nearest each location's screen position must map to a
    cell near the location's own cell. The bound is geometric, not a guess: a
    lattice point lies at most half a pitch from the location on each screen
    axis, one screen pixel is EARTH_KM / S kilometres (about 5 km at this fit),
    and the grid axes are rotated against the screen's, so the worst case is
    the diagonal, half a pitch times root two, in cells of Dx (about 4 cells
    at 3 px), plus one for the two roundings. Anything further means one of
    the two projections is wrong."""
    ni, dx_km = grib2.WEXP["Ni"], grib2.WEXP["dx"] / 1000.0
    px_km = EARTH_KM / tr.S
    limit = int(math.ceil(math.hypot(PITCH / 2.0, PITCH / 2.0) * px_km / dx_km)) + 1
    worst = {"id": None, "cells": -1}
    for loc in locations:
        col = min(max(int((loc["px"] - PITCH / 2.0) / PITCH + 0.5), 0), COLS - 1)
        row = min(max(int((loc["py"] - PITCH / 2.0) / PITCH + 0.5), 0), ROWS - 1)
        k = lattice["wexp"][row * COLS + col]
        if k < 0:
            raise SystemExit(f"{loc['id']}: the lattice point nearest its screen position is off the grid")
        i, j = k % ni, k // ni
        li, lj = loc["cell"]["wexp"][0], loc["cell"]["wexp"][1]
        d = max(abs(i - li), abs(j - lj))
        if d > worst["cells"]:
            worst = {"id": loc["id"], "cells": d, "lattice": [i, j], "location": [li, lj], "limit": limit}
    if worst["cells"] > limit:
        raise SystemExit(f"lattice disagrees with the location cells: {worst}")
    return worst


def main() -> int:
    tr = basemap.Transform.from_json(basemap.load_field_grid()["transform"])
    centres = read_centres()
    if len(centres) != 50:
        raise SystemExit(f"expected 50 population centres, read {len(centres)}")
    locations = build_locations(centres, tr)
    lattice = build_lattice(tr)
    worst = sanity(locations, lattice, tr)
    with open(LOCATIONS_PATH, "w") as fh:
        json.dump(locations, fh, indent=1)
        fh.write("\n")
    with open(LATTICE_PATH, "w") as fh:
        json.dump(lattice, fh, separators=(",", ":"))
    on_grid = sum(1 for k in lattice["wexp"] if k >= 0)
    far = max(locations, key=lambda l: l["cell"]["distanceKm"])
    print(f"locations: {len(locations)}, farthest cell centre {far['cell']['distanceKm']} km ({far['id']}); "
          f"lattice: {on_grid} of {COLS * ROWS} points on the wexp grid, "
          f"{sum(1 for k in lattice['g184'] if k >= 0)} on G184; "
          f"worst lattice-to-location disagreement {worst['cells']} cells ({worst['id']}, bound {worst['limit']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
