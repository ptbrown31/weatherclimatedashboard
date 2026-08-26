#!/usr/bin/env python3
"""Push the lead-time accuracy curve to the site.

The comparison of the exchange's implied daily high against the National
Weather Service's tailored station forecast is captured hourly by a separate
system on the owner's machine, into a SQLite file. That machine is where the
data is, so that machine is what publishes it: this reads the local database,
reduces it to a few hundred numbers, and writes one snapshot to the site's
bucket. The site's own pipeline cannot do it — the database is not on the
network and the site's archive only reaches back to the day it started, which
is a fraction of this record.

    python3 scripts/export_accuracy.py                  # write to the configured storage
    python3 scripts/export_accuracy.py --dry-run        # print the curve, write nothing

WHAT IT COMPUTES, precisely, because the page states it and a reader may check:

  A row is one city, one target day, one capture. Lead is the hours from the
  capture to the end of the target day in the station's own time, rounded to
  the hour, which is the bin.

  Error is the absolute difference between a forecast and the high the station
  actually recorded. The two forecasts are averaged separately over whatever
  rows carry them, so a capture holding one and not the other still counts for
  the one it has, and the count reported is city-days rather than rows.

  A bin is kept only when it holds at least thirty city-days. Below that the
  mean is noise and the improvement figure swings on single days.

  The improvement is the exchange's error against the Service's, as a share of
  the Service's, at the same lead. It is not annualised, weighted or adjusted.

Nothing here reads or writes a price, a model or a fair value.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pipeline import config, storage   # noqa: E402

DEFAULT_DB = os.path.expanduser("~/ForecastEx_vs_NWS/runtime/db/fx_vs_lamp.sqlite")
KEY = "snapshots/accuracy/lead-curve.json"
CACHE = "public, max-age=1800, stale-while-revalidate=86400, stale-if-error=2592000"
MIN_CITY_DAYS = 30
MAX_LEAD = 48


def curve(db_path: str) -> dict:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            """
            SELECT station_id, city_label, target_date_local, lead_hours,
                   lamp_high_f, forecastex_high_f, observed_high_f
            FROM hourly_predictions
            WHERE observed_high_f IS NOT NULL
              AND (lamp_high_f IS NOT NULL OR forecastex_high_f IS NOT NULL)
            """
        ).fetchall()
    finally:
        con.close()

    bins = collections.defaultdict(lambda: {"nws": [], "fx": [], "cityDays": set()})
    days, cities = set(), {}
    for sid, label, day, lead, nws, fx, obs in rows:
        try:
            lb = int(round(float(lead)))
        except (TypeError, ValueError):
            continue
        if not (0 <= lb <= MAX_LEAD):
            continue
        b = bins[lb]
        b["cityDays"].add((sid, day))
        days.add(day)
        cities[sid] = label or sid
        if nws is not None:
            b["nws"].append(abs(float(nws) - float(obs)))
        if fx is not None:
            b["fx"].append(abs(float(fx) - float(obs)))

    out = []
    for lb in sorted(bins):
        b = bins[lb]
        n = len(b["cityDays"])
        if n < MIN_CITY_DAYS or not b["nws"] or not b["fx"]:
            continue
        nws = sum(b["nws"]) / len(b["nws"])
        fx = sum(b["fx"]) / len(b["fx"])
        out.append({"lead": lb, "nws": round(nws, 3), "fx": round(fx, 3), "cityDays": n,
                    "improvement": round((nws - fx) / nws * 100, 1) if nws > 0 else None})
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"schema": 1, "asof": now,
            "from": min(days) if days else None, "to": max(days) if days else None,
            "cities": len(cities), "cityNames": sorted(cities.values()),
            "minCityDays": MIN_CITY_DAYS, "points": out,
            "source": "hourly capture of the exchange's implied daily high against the National Weather "
                      "Service tailored station forecast, scored on the station's recorded high"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB, help="the capture database (default: the owner's local file)")
    ap.add_argument("--dry-run", action="store_true", help="print the curve and write nothing")
    args = ap.parse_args(argv)

    if not os.path.exists(args.db):
        print(json.dumps({"kind": "accuracy", "written": False,
                          "reason": "no capture database at " + args.db}))
        return 1
    doc = curve(args.db)
    if not doc["points"]:
        print(json.dumps({"kind": "accuracy", "written": False,
                          "reason": "no lead bin reached " + str(MIN_CITY_DAYS) + " city-days"}))
        return 1
    if args.dry_run:
        print(f"{'lead':>5}{'NWS':>7}{'FX':>7}{'impr':>8}{'N':>6}")
        for p in sorted(doc["points"], key=lambda r: -r["lead"]):
            print(f"{p['lead']:>5}{p['nws']:>7.2f}{p['fx']:>7.2f}{p['improvement']:>+7.0f}%{p['cityDays']:>6}")
        print(json.dumps({k: doc[k] for k in ("from", "to", "cities", "minCityDays")}))
        return 0

    store = storage.from_config(config.load())
    store.put(KEY, json.dumps(doc, separators=(",", ":")).encode(), "application/json", CACHE)
    print(json.dumps({"kind": "accuracy", "written": True, "bins": len(doc["points"]),
                      "from": doc["from"], "to": doc["to"], "cities": doc["cities"],
                      "storage": store.kind()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
