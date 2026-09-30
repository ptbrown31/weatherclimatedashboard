"""
build_analysis_grid.py — the analysis lane's two derived config files.

Run once, by hand, when geo/settlement_locations.csv or the basemap changes;
the scheduled job reads the outputs and never projects anything itself.

    python3 scripts/build_analysis_grid.py

    config/analysis_locations.json   the sixty-seven settlement locations with
                                     their frozen cell on the wexp grid and the
                                     same point on G184, the cell's own centre
                                     and its distance from the position (the
                                     City Hall), the screen position (px, py) on
                                     the site's CONUS map, and per grid the
                                     cell's screen geometry (docs/analysis.md
                                     section 1, cell geometry): its centre
                                     (<grid>Px), its outline (<grid>Box) and
                                     the local basis (<grid>Basis) the page
                                     lays the window cells out with
    config/analysis_lattice.json     the map lattice of docs/analysis.md section
                                     1: for each of 320 x 200 screen points, the
                                     grid cell under it on each grid, or -1 off
                                     the grid

The cell is the one the owner approved on 2026-09-29 and is read from the
list, never recomputed: a later change to the projection code must not move
a settlement point. It is the grid point whose square contains the position,
which is the nearest cell, fractional i and j rounded half up, as
pipeline.grib2.nearest_cell computes it; the script checks that for every
location and refuses to write a file where they differ, except where the
list records why (Miami, whose own square counts as water, settles at the
nearest land point). The G184 cell is (i - 200, j), the same point on
the ground: G184 is the wexp grid without its western expansion. The lattice inverts the
site's fitted screen transform and its Albers projection (pipeline/basemap.py)
to a longitude and latitude, then goes forward through the Lambert projection
to a cell; both projections are on a sphere, so the round trip is exact to the
precision of the floats.

The cell geometry goes the other way: integer and half-integer grid
coordinates through the Lambert inverse (grib2.lcc_latlon) and forward
through the Albers fit to the screen. The page draws the 21 x 21 window round
a place as centre + a*di + b*dj rather than projecting every cell, so the
sanity block checks that linear frame against the exact projection over the
whole window and refuses to write a file whose worst case is more than
FRAME_TOL viewBox units.
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

CSV_PATH = os.path.join(ROOT, "geo", "settlement_locations.csv")
LOCATIONS_PATH = os.path.join(ROOT, "config", "analysis_locations.json")
LATTICE_PATH = os.path.join(ROOT, "config", "analysis_lattice.json")
EARTH_KM = 6371.2                 # the grids' own sphere (6,371,200 m), so distances are on the same earth
PITCH, COLS, ROWS = 3, 320, 200   # docs/analysis.md section 1: 960 x 600 at 3 px
# the window the page draws round a place, WINDOW_HALF cells either way (the
# same constant as pipeline.analysis.WINDOW_HALF, whose windows this geometry
# lays out); the frame check below covers exactly this span
WINDOW_HALF = 10
# the linear frame may be this far from the exact projection anywhere in the
# window, in viewBox units. Measured over the fifty places of 2026-09-27 on both grids the
# worst cell centre is 0.0084 units out (Seattle, G184, offset -10, -10) and
# the worst drawn corner 0.0092, which is the curvature of the composed
# projection over ten cells rather than the basis (a central-difference
# basis lands within 0.0001 of it). A unit is
# about one screen pixel at the national extent (the 960-wide viewBox is
# drawn at about 960 px), so 0.01 units is under a hundredth of a pixel
# there and about one pixel at the 96x zoom the page allows; the tolerance
# is set just above the measured worst so a projection change shows up
FRAME_TOL = 0.02
GRIDS = {"wexp": grib2.WEXP, "g184": grib2.G184}
# G184 is the wexp grid without its western expansion: the same lattice with
# its first point 200 columns east, so (i, j) on wexp is (i - 200, j) on G184
G184_OFFSET = 200
G184_TOL_KM = 0.05
EXPECTED = 67
# locations whose approved cell is not the one containing the position, and
# why (the settlement list, 2026-09-29); anything else that differs stops the build
NOT_NEAREST = {"miami-fl": "the position's own square is water to the analysis; the nearest land point settles"}


def read_centres(path: str = CSV_PATH) -> list:
    """The vendored list, comment lines (#) skipped, numbers parsed."""
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(line for line in fh if not line.startswith("#")))
    out = []
    for r in rows:
        out.append({"id": r["id"].strip(), "name": r["name"].strip(), "state": r["state"].strip(),
                    "metro": r["metro"].strip(), "metroRank": int(r["metro_rank"]), "basis": r["basis"].strip(),
                    "position": r["position"].strip(), "lat": float(r["lat"]), "lon": float(r["lon"]),
                    "i": int(r["i"]), "j": int(r["j"]), "tz": r["tz"].strip(), "note": (r.get("note") or "").strip()})
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


def cell_px(grid: dict, i: float, j: float, tr: basemap.Transform) -> tuple:
    """The screen point of fractional grid coordinates: the Lambert inverse
    to latitude and longitude, then the site's Albers fit. lcc_latlon already
    returns the longitude in -180 to 180."""
    lat, lon = grib2.lcc_latlon(grid, i, j)
    return tr.project(lon, lat)


def cell_geometry(grid: dict, i: int, j: int, tr: basemap.Transform) -> dict:
    """The resolving cell on the screen: its centre, its outline (the four
    corners at i +- 0.5, j +- 0.5 in order round the cell, starting at
    (i - 0.5, j - 0.5)) and the local basis, the screen displacement from the
    centre to the centres of cells (i + 1, j) and (i, j + 1). The grid is a
    Lambert conformal square lattice and the screen is an Albers fit, so at
    one place the composed map is a rotation and a slightly anisotropic
    scale: the basis is that local linear map, and the page places cell
    (i + a, j + b) at centre + a * di + b * dj. Centre and corners are kept
    to a thousandth of a viewBox unit; the basis to a ten-thousandth, since
    the window multiplies its rounding by up to WINDOW_HALF."""
    cx, cy = cell_px(grid, i, j, tr)
    corners = [cell_px(grid, i + a, j + b, tr) for a, b in ((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5))]
    ix, iy = cell_px(grid, i + 1, j, tr)
    jx, jy = cell_px(grid, i, j + 1, tr)
    return {"px": [round(cx, 3), round(cy, 3)],
            "box": [[round(x, 3), round(y, 3)] for x, y in corners],
            "basis": {"di": [round(ix - cx, 4), round(iy - cy, 4)], "dj": [round(jx - cx, 4), round(jy - cy, 4)]}}


def frame_error(grid: dict, i: int, j: int, tr: basemap.Transform, geom: dict, half: int = WINDOW_HALF) -> dict:
    """How far the linear frame (the rounded centre and basis of `geom`)
    strays from the exact projection: over every cell centre of the window
    (i +- half, j +- half), and over the four box corners against
    centre +- di/2 +- dj/2. Returns the worst of each with where it was, in
    viewBox units. The error grows with distance from the centre (it is the
    curvature of the composed map times the square of the offset), so the
    window's corners are where it peaks, and the whole window is checked
    anyway because it is cheap."""
    cx, cy = geom["px"]
    di, dj = geom["basis"]["di"], geom["basis"]["dj"]
    worst = {"cells": 0.0, "at": None, "corners": 0.0, "corner": None}
    for b in range(-half, half + 1):
        for a in range(-half, half + 1):
            ex, ey = cell_px(grid, i + a, j + b, tr)
            err = math.hypot(cx + a * di[0] + b * dj[0] - ex, cy + a * di[1] + b * dj[1] - ey)
            if err > worst["cells"]:
                worst["cells"], worst["at"] = err, [a, b]
    for n, (sa, sb) in enumerate(((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5))):
        bx, by = geom["box"][n]
        err = math.hypot(cx + sa * di[0] + sb * dj[0] - bx, cy + sa * di[1] + sb * dj[1] - by)
        if err > worst["corners"]:
            worst["corners"], worst["corner"] = err, n
    return worst


def build_locations(centres: list, tr: basemap.Transform) -> list:
    out = []
    for c in centres:
        i, j = c["i"], c["j"]
        wexp = (i, j, j * grib2.WEXP["Ni"] + i)
        g184 = (i - G184_OFFSET, j, j * grib2.G184["Ni"] + i - G184_OFFSET)
        near = grib2.nearest_cell(grib2.WEXP, c["lat"], c["lon"])
        if near is None or tuple(near[:2]) != (i, j):
            if c["id"] not in NOT_NEAREST:
                raise SystemExit(f"{c['id']}: the approved cell {i}, {j} is not the one containing the position "
                                 f"({near}) and the list gives no reason")
        clat, clon = grib2.lcc_latlon(grib2.WEXP, i, j)
        clon = wrap_lon(clon)
        glat, glon = grib2.lcc_latlon(grib2.G184, g184[0], g184[1])
        # the two grids' first points are published to a millionth of a
        # degree, so the same point differs by a few metres between them
        # (8.3 m at worst over the list); a kilometre would mean a wrong offset
        if haversine_km(glat, wrap_lon(glon), clat, clon) > G184_TOL_KM:
            raise SystemExit(f"{c['id']}: the G184 point is not the wexp point on the ground")
        px, py = tr.project(c["lon"], c["lat"])
        row = {k: v for k, v in c.items() if k not in ("i", "j")}
        cell = {"wexp": list(wexp), "g184": list(g184),
                "centre": [round(clat, 4), round(clon, 4)],
                "distanceKm": round(haversine_km(c["lat"], c["lon"], clat, clon), 2)}
        # the screen geometry per grid, keyed <grid>Px, <grid>Box, <grid>Basis
        # (docs/analysis.md section 1); a page built before these existed
        # reads the keys above and ignores these
        for name, ij in (("wexp", wexp), ("g184", g184)):
            g = cell_geometry(GRIDS[name], ij[0], ij[1], tr)
            cell[name + "Px"], cell[name + "Box"], cell[name + "Basis"] = g["px"], g["box"], g["basis"]
        # three decimals: the page draws the position at (px, py) inside
        # the outlined resolving cell, and a cell is only about 0.47 units
        # across, so rounding to a tenth (up to 0.07 units) put the dot across
        # the outline at three places; a thousandth is 0.002 cells
        row.update({"px": round(px, 3), "py": round(py, 3), "cell": cell})
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


def frame_sanity(locations: list, tr: basemap.Transform) -> dict:
    """Every location's linear frame, on both grids, must reproduce the exact
    projected centre of every window cell out to (i +- WINDOW_HALF,
    j +- WINDOW_HALF) and the box corners within FRAME_TOL. Returns the worst
    case over the list; anything past the tolerance means the page would
    draw a window cell in the wrong place, so the file is not written."""
    worst = {"id": None, "grid": None, "cells": -1.0, "at": None, "corners": -1.0}
    for loc in locations:
        for name in GRIDS:
            i, j = loc["cell"][name][0], loc["cell"][name][1]
            geom = {"px": loc["cell"][name + "Px"], "box": loc["cell"][name + "Box"], "basis": loc["cell"][name + "Basis"]}
            e = frame_error(GRIDS[name], i, j, tr, geom)
            if e["cells"] > worst["cells"]:
                worst.update({"id": loc["id"], "grid": name, "cells": e["cells"], "at": e["at"]})
            if e["corners"] > worst["corners"]:
                worst.update({"corners": e["corners"], "cornerId": loc["id"], "cornerGrid": name})
    if worst["cells"] > FRAME_TOL or worst["corners"] > FRAME_TOL:
        raise SystemExit(f"the linear cell frame strays past {FRAME_TOL} viewBox units: {worst}")
    return worst


def main() -> int:
    tr = basemap.Transform.from_json(basemap.load_field_grid()["transform"])
    centres = read_centres()
    if len(centres) != EXPECTED:
        raise SystemExit(f"expected {EXPECTED} settlement locations, read {len(centres)}")
    locations = build_locations(centres, tr)
    lattice = build_lattice(tr)
    worst = sanity(locations, lattice, tr)
    frame = frame_sanity(locations, tr)
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
          f"worst lattice-to-location disagreement {worst['cells']} cells ({worst['id']}, bound {worst['limit']}); "
          f"cell frame: worst window-cell error {frame['cells']:.4f} viewBox units at offset {frame['at']} "
          f"({frame['id']}, {frame['grid']}), worst box-corner error {frame['corners']:.4f} "
          f"({frame['cornerId']}, {frame['cornerGrid']}), tolerance {FRAME_TOL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
