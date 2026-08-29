"""Rebuild config/severe_climo.json from the SPC preliminary yearly JSONs.

The SW contracts settle on the month totals of the SPC Annual Preliminary
Report Summary, National tables, and one JSON per year drives that page:
climo/summary/{year}/ruf/NAT/NAT.json ("ruf" is the page's Preliminary
source; "smooth" is Final Storm Data, which the contracts do not use). This
script fetches the complete years and writes the day-of-month cumulative
quantile envelope and settlement-total quantiles the severe job serves.

The month table and the daily table are different tabulations, a median 1.4
percent apart across 2005-2025 with tails past 100 percent in either
direction, so each year's daily shape is rescaled to its own settlement
total before the quantiles are taken.

Run once a year when the prior year completes:

    python3 scripts/build_severe_climo.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "config", "severe_climo.json")
URL = "https://www.spc.noaa.gov/climo/summary/{year}/ruf/NAT/NAT.json"
PHENOMENA = ("torn", "wind", "hail")
FIRST_YEAR = 2005          # preliminary data begins 2004; 2005 starts the complete run


def fetch(year: int) -> dict:
    req = urllib.request.Request(URL.format(year=year),
                                 headers={"User-Agent": "weather-tools-site build (see config)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def quantile(sorted_vals: list, p: float) -> float:
    n = len(sorted_vals)
    if n == 1:
        return float(sorted_vals[0])
    i = p * (n - 1)
    lo = int(i)
    hi = min(lo + 1, n - 1)
    f = i - lo
    return sorted_vals[lo] * (1 - f) + sorted_vals[hi] * f


def main() -> int:
    last = dt.date.today().year - 1
    years = {}
    for y in range(FIRST_YEAR, last + 1):
        years[y] = fetch(y)
        time.sleep(0.3)
    months = {}
    for m in range(1, 13):
        ml = {}
        for ph in PHENOMENA:
            curves, totals = [], []
            for d in years.values():
                byday = {int(k[2:]): d["daily"][k][ph] for k in d["daily"] if int(k[:2]) == m}
                run, cum = 0, []
                for dom in range(1, 32):
                    run += byday.get(dom, 0)
                    cum.append(run)
                tot = d["month"][str(m)][ph]
                scale = tot / cum[-1] if cum[-1] > 0 else 0.0
                curves.append([c * scale for c in cum])
                totals.append(tot)
            env = {}
            for name, p in (("p10", 0.10), ("p50", 0.50), ("p90", 0.90)):
                env[name] = [round(quantile(sorted(c[d0] for c in curves), p), 1) for d0 in range(31)]
            st = sorted(totals)
            ml[ph] = {"env": env,
                      "totals": {"min": st[0], "p10": round(quantile(st, 0.10), 1),
                                 "p50": round(quantile(st, 0.50), 1),
                                 "p90": round(quantile(st, 0.90), 1), "max": st[-1]}}
        months[str(m)] = ml
    history = {str(y): {str(m): {ph: years[y]["month"][str(m)][ph] for ph in PHENOMENA}
                        for m in range(1, 13)} for y in years}
    doc = {"schema": 1, "yearsFrom": FIRST_YEAR, "yearsTo": last, "history": history,
           "source": "SPC Annual Preliminary Report Summary (ruf), climo/summary/{year}/ruf/NAT/NAT.json",
           "note": "day-of-month cumulative quantiles across the years, each year rescaled to its settlement month total",
           "months": months}
    with open(OUT, "w") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    print(f"wrote {OUT} ({os.path.getsize(OUT)} bytes, {FIRST_YEAR}-{last})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
