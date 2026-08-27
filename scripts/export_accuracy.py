#!/usr/bin/env python3
"""Push the lead-time accuracy curve to the site.

The comparison behind the accuracy page is between two forecasts of the same
thing: the National Weather Service's LAMP bulletin high for a station's day,
and the high the exchange's prices imply for the same day, both scored against
the temperature the station actually recorded.

The records come from a separate system that has been capturing both since June.
That system is not on the network and is not named here, because this repository
is public; a reader who has it produces the records with the extractor that sits
outside this tree, and this script aggregates and publishes them:

    ~/.weather-tools-site-accuracy/run.sh > records.jsonl
    python3 scripts/export_accuracy.py --records records.jsonl

    python3 scripts/export_accuracy.py --records records.jsonl --dry-run

Each record is one city, one target day, one capture: the lead in hours, the
bulletin's forecast high at that moment, the strike where the Yes price crosses
fifty cents, and the recorded high.

TWO DERIVATIONS DECIDE WHAT THIS CURVE SAYS, so both live here rather than in the
extractor, where they could not be read or tested.

  The forecast for a day is the highest the bulletin has forecast for that day,
  not the highest still to come. A bulletin covers a rolling window, so once the
  afternoon has passed its remaining window holds only the night, and reading it
  literally says the Service forecast 70 for a day it had called 88 and that
  reached 88. Scored that way the Service's error grew to eighteen degrees an
  hour before midnight, which is an artefact of the window and not a forecast
  anyone made. Carrying the day's highest figure forward is a no-op at long lead,
  where nothing has expired: against the capture system the two agree exactly at
  every lead beyond about thirty hours.

  The market's whole-degree high is the degree above the crossing, not the
  nearest one. These contracts pay when the recorded high is STRICTLY above the
  strike, so a market certain the day reaches 92 bids the 91 strike to a dollar
  and the 92 strike to nothing, and the crossing lands at 91.5 — which is not a
  temperature anyone thinks possible, it is the midpoint of a step. Rounding to
  the nearest degree is a coin flip that names the settle 55% of the time; taking
  the degree above names it 98%. Eighty-eight per cent of crossings inside four
  hours of settlement sit exactly on a half, which is that step.

Nothing here reads or publishes a model forecast, a fitted probability or a fair
value. The records carry the exchange's own prices and a public NWS bulletin.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pipeline import config, storage   # noqa: E402

KEY = "snapshots/accuracy/lead-curve.json"
CACHE = "public, max-age=1800, stale-while-revalidate=86400, stale-if-error=2592000"
# below this a bin's mean is noise and its improvement figure swings on one day
MIN_CITY_DAYS = 30
MAX_LEAD = 48


def carry_forward(records):
    """The day's forecast, not the rest of the day's forecast.

    Records for one city and day are walked earliest first and the bulletin's
    high is replaced by the highest it has been. Each record gains `nwsDay`; one
    that precedes any bulletin keeps None.
    """
    by = collections.defaultdict(list)
    for r in records:
        by[(r.get("city"), r.get("day"))].append(r)
    for rs in by.values():
        rs.sort(key=lambda r: -float(r.get("lead") or 0))
        best = None
        for r in rs:
            v = r.get("nws")
            if v is not None:
                best = float(v) if best is None else max(best, float(v))
            r["nwsDay"] = best
    return records


def settle_degree(v):
    """The whole degree a crossing implies, given a strictly-greater settlement."""
    if v is None:
        return None
    return float(math.ceil(float(v) - 1e-9))


def curve(records) -> dict:
    records = carry_forward(records)
    bins = collections.defaultdict(lambda: {"nws": [], "fx": [], "cityDays": set()})
    days, cities = set(), set()
    for r in records:
        obs = r.get("obs")
        if obs is None:
            continue
        try:
            lb = int(round(float(r.get("lead"))))
        except (TypeError, ValueError):
            continue
        if not (0 <= lb <= MAX_LEAD):
            continue
        b = bins[lb]
        b["cityDays"].add((r.get("city"), r.get("day")))
        days.add(r.get("day"))
        cities.add(r.get("city"))
        if r.get("nwsDay") is not None:
            b["nws"].append(abs(round(float(r["nwsDay"])) - float(obs)))
        fx = settle_degree(r.get("fx"))
        if fx is not None:
            b["fx"].append(abs(fx - float(obs)))

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
    return {"schema": 2, "asof": now,
            "from": min(days) if days else None, "to": max(days) if days else None,
            "cities": len(cities), "minCityDays": MIN_CITY_DAYS, "points": out,
            "source": "hourly capture of the exchange's implied daily high against the National Weather "
                      "Service LAMP bulletin, scored on the station's recorded high"}


def read_records(path):
    opener = open
    if path.endswith(".gz"):
        import gzip
        opener = gzip.open
    out = []
    with opener(path, "rt") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--records", required=True, help="the records file (json lines, optionally gzipped)")
    ap.add_argument("--dry-run", action="store_true", help="print the curve and write nothing")
    args = ap.parse_args(argv)

    if not os.path.exists(args.records):
        print(json.dumps({"kind": "accuracy", "written": False, "reason": "no records at " + args.records}))
        return 1
    doc = curve(read_records(args.records))
    if not doc["points"]:
        print(json.dumps({"kind": "accuracy", "written": False,
                          "reason": "no lead bin reached %d city-days" % MIN_CITY_DAYS}))
        return 1
    if args.dry_run:
        print(f"{'lead':>5}{'NWS':>7}{'FX':>7}{'impr':>8}{'N':>7}")
        for p in sorted(doc["points"], key=lambda r: -r["lead"]):
            print(f"{p['lead']:>5}{p['nws']:>7.2f}{p['fx']:>7.2f}{p['improvement']:>+7.0f}%{p['cityDays']:>7}")
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
