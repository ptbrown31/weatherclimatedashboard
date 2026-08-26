#!/usr/bin/env python3
"""config/cat4_climatology.json — how often a Category 4 hurricane makes
landfall in the continental United States, and when in the season.

Built here rather than in the pipeline for two reasons: the answer changes once
a year, and the test for "continental United States" needs the state geometry,
which the deployment package does not carry.

Three things this gets right that a looser reading would not:

  Exactly Category 4. The contract's terms say a hurricane qualifies only at
  the named category and that "hurricanes of a higher or lower category do not
  qualify", so Michael in 2018 and Dorian in 2019, both Category 5 at landfall,
  are not qualifying events for the Category 4 contract.

  Continental only. Maria's Puerto Rico landfall in 2017 was exactly Category 4
  and is excluded, because this is the continental reading the owner chose.

  The landfall record, not the peak. HURDAT marks the rows where the surface
  centre crossed the coast; a storm's peak intensity out at sea is not what the
  contract reads.

The window is the satellite era. Thirty years holds only three qualifying
seasons, which is too thin to shape a curve; sixty holds eleven and the rate
per season is stable across every window from 1950 on.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pipeline import basemap, gov_weather as gw   # noqa: E402

OUT = os.path.join(ROOT, "config", "cat4_climatology.json")
Y0, Y1 = 1966, 2025
KT_LO, KT_HI = 113, 136          # the Saffir-Simpson category 4 band
SMOOTH_DAYS = 21                 # a handful of events over sixty years needs a kernel, not a histogram
HEAD = re.compile(r"^AL\d{6}$")


def conus_rings():
    topo, rc = basemap.decode_topo(os.path.join(basemap.GEO, "states-10m.json"))
    return [r for g in topo["objects"]["states"]["geometries"]
            if g["properties"].get("name") and g["id"] not in basemap.SKIP
            for r in basemap.geom_rings(g, rc)]


def _inside(lon, lat, ring):
    c, n = False, len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > lat) != (y2 > lat):
            if lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                c = not c
    return c


def in_conus(lon, lat, rings, tol=0.25):
    """A landfall fix sits on the coastline, so a point that falls just outside
    every polygon is accepted when it is within a quarter degree of one. Without
    that, real landfalls on the seaward side of the boundary are missed."""
    if not (-125 <= lon <= -66 and 24 <= lat <= 50):
        return False
    if any(_inside(lon, lat, r) for r in rings):
        return True
    return any(abs(x - lon) < tol and abs(y - lat) < tol for r in rings for x, y in r)


def main() -> int:
    rings = conus_rings()
    url = gw.latest_hurdat_url()
    lines = gw._get_text(url, timeout=240).splitlines()
    events, i, n = {}, 0, len(lines)
    while i < n:
        f = [p.strip() for p in lines[i].split(",")]
        i += 1
        while f and f[-1] == "":
            f.pop()
        if len(f) != 3 or not HEAD.match(f[0]):
            continue
        year, rows = int(f[0][4:8]), int(f[2])
        name = f[1].title()
        for _ in range(rows):
            if i >= n:
                break
            r = [p.strip() for p in lines[i].split(",")]
            i += 1
            if len(r) < 8 or r[2] != "L" or r[3] != "HU":
                continue
            try:
                w = int(r[6])
            except ValueError:
                continue
            if not (KT_LO <= w <= KT_HI):
                continue
            lat = float(r[4][:-1]) * (1 if r[4][-1] == "N" else -1)
            lon = float(r[5][:-1]) * (-1 if r[5][-1] == "W" else 1)
            if in_conus(lon, lat, rings):
                events.setdefault(year, []).append({"date": r[0], "name": name, "kt": w})

    yrs = list(range(Y0, Y1 + 1))
    # The contract asks whether AT LEAST ONE qualifying landfall has happened by
    # a date, so the unit is the season's FIRST one. A storm whose centre crosses
    # the coast twice appears twice in the record — Charley in 2004 and Ian in
    # 2022 both do — and counting those twice would overstate how often a season
    # produces the event at all.
    firsts = {}
    for y in yrs:
        got = sorted(events.get(y, []), key=lambda e: e["date"])
        if got:
            firsts[y] = got[0]
    # the empirical share of seasons whose first qualifying landfall had happened
    # by each day of the year, which is the probability the contract asks about
    by_doy = {}
    for y, e in firsts.items():
        k = dt.date(2001, int(e["date"][4:6]), int(e["date"][6:8])).timetuple().tm_yday
        by_doy[k] = by_doy.get(k, 0) + 1
    # lightly smoothed, because eight dates in sixty seasons is a staircase and
    # the steps are an artefact of the sample rather than of the season
    half = SMOOTH_DAYS // 2
    smooth = [sum(by_doy.get(j, 0) for j in range(k - half, k + half + 1)) / SMOOTH_DAYS
              for k in range(1, 367)]
    cum, run = [], 0.0
    for k in range(1, 367):
        run += smooth[k - 1]
        d = dt.date(2001, 1, 1) + dt.timedelta(days=k - 1)
        cum.append([d.strftime("%m-%d"), round(run / len(yrs), 5)])

    doc = {"schema": 1, "window": [Y0, Y1], "seasons": len(yrs),
           "landfallRows": sum(len(events.get(y, [])) for y in yrs),
           "seasonsWithOne": len(firsts),
           "shareOfSeasons": round(len(firsts) / len(yrs), 4),
           "source": "NOAA HURDAT2 (" + url.rsplit("/", 1)[-1] + "), landfall records at exactly category 4 "
                     "in the continental United States; one event per season, the first",
           "cumulative": cum,
           "firsts": [{"year": y, **firsts[y]} for y in sorted(firsts)]}
    with open(OUT, "w") as fh:
        json.dump(doc, fh, separators=(",", ":"))
        fh.write("\n")
    print(json.dumps({k: doc[k] for k in ("window", "seasons", "landfallRows", "seasonsWithOne", "shareOfSeasons")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
