"""
climate.py — the long series behind the climate page, once a day.

All settlement-basis series as their publishers post them:

    NCEI Climate at a Glance   global land+ocean temperature anomaly, annual and
                               monthly, relative to the 1901-2000 mean. The
                               annual value is NOAA's own published number,
                               never recomputed from monthly means.
    NOAA GML                   Mauna Loa monthly mean CO2 (ppm).
    NOAA/NESDIS STAR           global mean sea level from satellite altimetry,
                               66S-66N, seasonal signals removed; one column per
                               mission, the last non-empty value per row taken
                               so the stitched record follows the newest mission.
    RAPID array (UK NERC)      AMOC overturning at 26N, annual means. The one
                               non-US series, kept by the owner's decision with
                               its acknowledgment; the project's direct download
                               sits behind a portal, so the vendored annual
                               means in site/assets are used and a live fetch
                               is attempted first.

The temperature series are also offered "above preindustrial" by adding a
constant: the convention used by the contracts is the NCEI anomaly plus
0.18 C. That constant is the one place the convention lives.

Writes snapshots/climate.json.
"""
from __future__ import annotations
import datetime as dt
import json
import os
import re
import time

from . import gov_weather as gw
from .storage import Storage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREINDUSTRIAL_OFFSET_C = 0.18
CAG = "https://www.ncei.noaa.gov/access/monitoring/climate-at-a-glance/global/time-series/globe/land_ocean/{m}/{n}/1850-{y}/data.csv"
GML_CO2 = "https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_mm_mlo.csv"
STAR_SLR = "https://www.star.nesdis.noaa.gov/socd/lsa/SeaLevelRise/slr/slr_sla_gbl_free_all_66.csv"
RAPID = "https://www.rapid.ac.uk/rapidmoc/rapid_data/moc_transports.ascii"


def _text(url: str, timeout: int = 90) -> str:
    return gw._get_text(url, timeout=timeout)


def cag_annual(year: int) -> list:
    rows = []
    for ln in _text(CAG.format(m=12, n=12, y=year)).splitlines():
        m = re.match(r"^(\d{4}),(-?\d+\.?\d*)$", ln.strip())
        if m:
            rows.append([int(m.group(1)), float(m.group(2))])
    return rows


def cag_monthly(year: int) -> list:
    rows = []
    for ln in _text(CAG.format(m=1, n=0, y=year)).splitlines():
        m = re.match(r"^(\d{4})(\d{2}),(-?\d+\.?\d*)$", ln.strip())
        if m:
            rows.append([round(int(m.group(1)) + (int(m.group(2)) - 0.5) / 12, 3), float(m.group(3))])
    return rows


def gml_co2() -> list:
    rows = []
    for ln in _text(GML_CO2, 60).splitlines():
        parts = ln.split(",")
        if len(parts) >= 4 and re.match(r"^\d{4}$", parts[0].strip()):
            try:
                rows.append([float(parts[2]), float(parts[3])])
            except ValueError:
                pass
    return rows


def star_sea_level() -> list:
    rows = []
    for ln in _text(STAR_SLR, 60).splitlines():
        if ln.startswith("#") or ln.startswith("year"):
            continue
        parts = ln.split(",")
        vals = [p for p in parts[1:] if p.strip()]
        if parts and vals:
            try:
                rows.append([round(float(parts[0]), 3), float(vals[-1])])
            except ValueError:
                pass
    return rows


def rapid_amoc() -> tuple:
    """Annual means of the 12-hourly overturning; live first, vendored fallback."""
    by_year: dict = {}
    try:
        for ln in _text(RAPID, 120).splitlines():
            parts = ln.split()
            if len(parts) < 14:
                continue
            try:
                yr, moc = int(parts[1]), float(parts[-1])
            except ValueError:
                continue
            if moc <= -1.0e4:
                continue
            by_year.setdefault(yr, []).append(moc)
    except Exception:
        by_year = {}
    rows = [[yr, round(sum(v) / len(v), 3)] for yr, v in sorted(by_year.items()) if len(v) >= 360]
    if len(rows) >= 15:
        return rows, "live"
    with open(os.path.join(ROOT, "geo", "rapid_amoc_annual.json")) as fh:
        return json.load(fh)["rows"], "vendored"


def climate_pass(cfg: dict, store: Storage) -> int:
    gw.set_user_agent(cfg.get("user_agent", ""))
    now = dt.datetime.now(dt.timezone.utc)
    t0 = time.time()
    series, errors, notes = {}, [], {}
    for key, fn in (("tempAnnual", lambda: cag_annual(now.year)), ("tempMonthly", lambda: cag_monthly(now.year)),
                    ("co2", gml_co2), ("seaLevel", star_sea_level)):
        try:
            rows = fn()
            if not rows:
                raise RuntimeError("empty series")
            series[key] = rows
        except Exception as e:
            errors.append(f"{key}: {type(e).__name__}: {e}")
    try:
        rows, how = rapid_amoc()
        series["amoc"] = rows
        notes["amoc"] = how
    except Exception as e:
        errors.append(f"amoc: {type(e).__name__}: {e}")
    # keep the previous snapshot's series for anything that failed today
    prev = store.get("snapshots/climate.json")
    if prev:
        old = json.loads(prev).get("series", {})
        for k, v in old.items():
            if k not in series and v:
                series[k] = v
                notes[k] = "carried from the previous snapshot"
    snap = {"schema": 1, "asof": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "offsetC": PREINDUSTRIAL_OFFSET_C, "series": series, "notes": notes, "errors": errors,
            "sources": {"tempAnnual": "NCEI Climate at a Glance, global land+ocean, annual, vs 1901-2000",
                        "tempMonthly": "NCEI Climate at a Glance, global land+ocean, monthly, vs 1901-2000",
                        "co2": "NOAA GML Mauna Loa monthly mean CO2, ppm",
                        "seaLevel": "NOAA/NESDIS STAR global mean sea level, mm, 66S-66N, seasonal signals removed",
                        "amoc": "RAPID AMOC monitoring project (UK NERC), annual mean overturning at 26N, Sv"}}
    store.put("snapshots/climate.json", json.dumps(snap, separators=(",", ":")).encode(), "application/json",
              "public, max-age=3600, stale-while-revalidate=86400, stale-if-error=2592000")
    print(json.dumps({"kind": "climate", "series": {k: len(v) for k, v in series.items()}, "notes": notes, "errors": errors,
                      "seconds": round(time.time() - t0, 1)}))
    return 1 if errors and not series else 0
