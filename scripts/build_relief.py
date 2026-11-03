"""
build_relief.py — the hill shading under the hurricane map's land.

Reads NOAA NCEI's ETOPO 2022 global relief (60 arc-second bedrock elevation)
through NCEI's OPeNDAP service as a text subset of each basin's box at every
ninth cell (0.15 degrees), and writes site/assets/relief-<basin>.json: the
shading of each land cell under a sun in the north-west, as a signed byte
(base64), rows north to south and columns west to east. Zero is flat ground or
sea, positive a slope facing the light and negative one facing away, so the page
can lay it over the land as light and shadow without touching the colours of the
contracts drawn there. Elevation does not change, so this runs once by hand and
its output is checked in, like the other generated assets.

    python3 scripts/build_relief.py            # both basins
    python3 scripts/build_relief.py AL

Standard library only.
"""
from __future__ import annotations
import base64
import json
import math
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = ("https://www.ngdc.noaa.gov/thredds/dodsC/global/ETOPO2022/60s/60s_bed_elev_netcdf/"
       "ETOPO_2022_v1_60s_N90W180_bed.nc")
SOURCE = "NOAA NCEI ETOPO 2022, 60 arc-second bedrock elevation"
CELLS_PER_DEGREE = 60
STRIDE = 9                                   # 0.15 degrees
# (west, south, east, north), the boxes site/js/hurricane.js draws
BOXES = {"AL": (-101.0, 4.0, -40.0, 48.0), "EP": (-180.0, 0.0, -85.0, 40.0)}
SUN_AZIMUTH = 315.0                          # degrees clockwise from north
SUN_ALTITUDE = 45.0
# The relief is exaggerated so that a range a few hundred metres high still
# shows at a scale where one cell is about fifteen kilometres across.
EXAGGERATION = 12.0


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "weather-tools-site relief build"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read().decode("ascii", "replace")


def index_range(box):
    """The grid's index ranges for one box: the 60 arc-second cells are centred
    on half-minutes from -90 and -180, so cell i holds latitude -90 + (i + 0.5) / 60."""
    w, s, e, n = box
    i0 = int(round((s + 90) * CELLS_PER_DEGREE))
    i1 = int(round((n + 90) * CELLS_PER_DEGREE))
    j0 = int(round((w + 180) * CELLS_PER_DEGREE))
    j1 = min(int(round((e + 180) * CELLS_PER_DEGREE)), 360 * CELLS_PER_DEGREE - 1)
    return i0, i1, j0, j1


def parse_ascii_vector(text: str) -> list:
    """The numbers after the dashed separator of a one-dimensional OPeNDAP ASCII answer."""
    body = text.split("---------------------------------------------", 1)[1]
    lines = [ln for ln in body.splitlines() if ln.strip()]
    return [float(x) for x in lines[1].split(",")]


def parse_ascii_grid(text: str) -> list:
    """Rows of floats from a two-dimensional OPeNDAP ASCII answer ("[i], v, v, ...")."""
    body = text.split("---------------------------------------------", 1)[1]
    rows = []
    for ln in body.splitlines():
        ln = ln.strip()
        if not ln.startswith("["):
            continue
        rows.append([float(x) for x in ln.split(",")[1:]])
    return rows


def hillshade(z: list, lats: list, step: float) -> list:
    """Signed shading per cell, rows as given (south to north). A land cell's
    slope and aspect come from its four neighbours; the light falls from
    SUN_AZIMUTH at SUN_ALTITUDE, and the shading is the illumination less that
    of flat ground, scaled to -127..127. Sea and coast (z <= 0) are 0."""
    ny, nx = len(z), len(z[0])
    zen = math.radians(90.0 - SUN_ALTITUDE)
    az = math.radians(360.0 - SUN_AZIMUTH + 90.0)        # to the maths convention
    flat = math.cos(zen)
    out = [[0] * nx for _ in range(ny)]
    for i in range(ny):
        dy = step * 110540.0
        dx = step * 111320.0 * max(math.cos(math.radians(lats[i])), 0.05)
        for j in range(nx):
            if z[i][j] <= 0:
                continue
            zl = z[i][max(j - 1, 0)]
            zr = z[i][min(j + 1, nx - 1)]
            zd = z[max(i - 1, 0)][j]                     # rows run south to north
            zu = z[min(i + 1, ny - 1)][j]
            gx = EXAGGERATION * (zr - zl) / (2 * dx)
            gy = EXAGGERATION * (zu - zd) / (2 * dy)
            slope = math.atan(math.hypot(gx, gy))
            aspect = math.atan2(gy, -gx)
            lit = math.cos(zen) * math.cos(slope) + math.sin(zen) * math.sin(slope) * math.cos(az - aspect)
            v = (lit - flat) / (1 - flat) if lit >= flat else (lit - flat) / flat
            out[i][j] = max(-127, min(127, int(round(v * 127))))
    return out


def build(basin: str) -> dict:
    box = BOXES[basin]
    i0, i1, j0, j1 = index_range(box)
    sl = "%5B{}:{}:{}%5D"
    lats = parse_ascii_vector(_get(URL + ".ascii?lat" + sl.format(i0, STRIDE, i1)))
    lons = parse_ascii_vector(_get(URL + ".ascii?lon" + sl.format(j0, STRIDE, j1)))
    z = parse_ascii_grid(_get(URL + ".ascii?z" + sl.format(i0, STRIDE, i1) + sl.format(j0, STRIDE, j1)))
    if len(z) != len(lats) or any(len(r) != len(lons) for r in z):
        raise ValueError("grid is %d rows for %d latitudes" % (len(z), len(lats)))
    step = round(STRIDE / CELLS_PER_DEGREE, 6)
    shade = hillshade(z, lats, step)
    cells = bytearray()
    for row in reversed(shade):                          # north to south, as the map draws
        cells.extend((v & 0xFF) for v in row)
    land = sum(1 for row in z for v in row if v > 0)
    return {"basin": basin, "box": list(box), "lat0": round(lats[-1], 6), "lon0": round(lons[0], 6), "step": step,
            "nx": len(lons), "ny": len(lats), "land": land, "source": SOURCE,
            "data": base64.b64encode(bytes(cells)).decode("ascii")}


def main(argv=None) -> int:
    basins = (argv if argv is not None else sys.argv[1:]) or sorted(BOXES)
    for b in basins:
        doc = build(b)
        path = os.path.join(ROOT, "site", "assets", "relief-%s.json" % b)
        with open(path, "w") as f:
            json.dump(doc, f, separators=(",", ":"))
        print("%s: %d x %d at %.2f degrees, %d land cells, %d bytes -> %s"
              % (b, doc["nx"], doc["ny"], doc["step"], doc["land"], os.path.getsize(path), os.path.relpath(path, ROOT)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
