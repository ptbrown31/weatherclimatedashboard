"""severe.py — the SPC severe-weather report counts behind the SW contracts.

SWTUS, SWWUS and SWHUS resolve on the month's national count of tornado,
severe-wind and severe-hail storm reports in the SPC Annual Preliminary
Report Summary. One JSON per year drives that page
(climo/summary/{year}/ruf/NAT/NAT.json, the Preliminary source), carrying
the month totals the contracts settle on and a daily table for the running
month. The job fetches the current year (and the next, once its months are
listed), joins the bundled 2005-2025 climatology envelope from
config/severe_climo.json, and writes snapshots/severe.json.

The month table is the settlement number; the daily table is a separate
tabulation kept for the in-month trajectory and labelled as such. All
counting happens at SPC; nothing here computes a probability.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time

from . import gov_weather as gw
from .storage import Storage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIMO_PATH = os.path.join(ROOT, "config", "severe_climo.json")
URL = "https://www.spc.noaa.gov/climo/summary/{year}/ruf/NAT/NAT.json"
PHENOMENA = ("torn", "wind", "hail")
SNAP_CACHE = "public, max-age=1800, stale-while-revalidate=21600, stale-if-error=2592000"
SCHEMA = 1


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def year_block(doc: dict) -> dict:
    """{months: {m: {torn,wind,hail}}, daily: {m: {ph: [cum by day]}}, through: {m: day}}."""
    months = {str(m): {ph: doc.get("month", {}).get(str(m), {}).get(ph) for ph in PHENOMENA}
              for m in range(1, 13)}
    daily: dict = {}
    through: dict = {}
    for m in range(1, 13):
        keys = [k for k in doc.get("daily", {}) if int(k[:2]) == m]
        if not keys:
            continue
        last = max(int(k[2:]) for k in keys)
        through[str(m)] = last
        byday = {int(k[2:]): doc["daily"][k] for k in keys}
        per = {}
        for ph in PHENOMENA:
            run, cum = 0, []
            for dom in range(1, last + 1):
                run += byday.get(dom, {}).get(ph, 0)
                cum.append(run)
            per[ph] = cum
        daily[str(m)] = per
    return {"months": months, "daily": daily, "through": through}


def severe_pass(cfg: dict, store: Storage) -> int:
    gw.set_user_agent(cfg.get("user_agent", ""))
    now = dt.datetime.now(dt.timezone.utc)
    t0 = time.time()
    errors = []
    years: dict = {}
    for year in (now.year, now.year + 1):
        try:
            raw = gw._fetch(URL.format(year=year))
            years[str(year)] = year_block(json.loads(raw))
        except Exception as e:  # noqa: BLE001 - next year usually has no file yet
            if year == now.year:
                errors.append(f"{year}: {type(e).__name__}: {e}")
    try:
        with open(CLIMO_PATH) as fh:
            climo = json.load(fh)
    except Exception as e:  # noqa: BLE001
        climo = {}
        errors.append(f"climo: {type(e).__name__}: {e}")
    if not years:
        # keep the previous snapshot rather than publishing nothing
        prev = store.get("snapshots/severe.json")
        if prev:
            snap = json.loads(prev)
            snap["error"] = "; ".join(errors)
            snap["staleSince"] = snap.get("staleSince") or _iso(now)
            store.put("snapshots/severe.json", json.dumps(snap, separators=(",", ":")).encode(),
                      "application/json", SNAP_CACHE)
        print(json.dumps({"kind": "severe", "written": False, "errors": errors,
                          "seconds": round(time.time() - t0, 1)}))
        return 1
    snap = {"schema": SCHEMA, "asof": _iso(now), "written": _iso(now),
            "source": "SPC Annual Preliminary Report Summary (Preliminary source); "
                      "the month totals are what the SW contracts settle on",
            "years": years,
            "climo": {"yearsFrom": climo.get("yearsFrom"), "yearsTo": climo.get("yearsTo"),
                      "months": climo.get("months", {})},
            "errors": errors}
    store.put("snapshots/severe.json", json.dumps(snap, separators=(",", ":")).encode(),
              "application/json", SNAP_CACHE)
    cur = years.get(str(now.year), {})
    print(json.dumps({"kind": "severe", "written": True,
                      "months": {m: v for m, v in (cur.get("through") or {}).items()},
                      "errors": errors, "seconds": round(time.time() - t0, 1)}))
    return 0
