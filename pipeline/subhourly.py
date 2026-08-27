"""What happened between the hourly reports.

The contracts settle on hourly METARs and this site reads those from
aviationweather.gov. That record is twenty-four readings of a day that had
nearly three hundred, and a brief afternoon peak falling between two reports is
invisible in it — while being exactly the thing a reader wonders about when a
market and a forecast disagree by a degree.

So the five minute ASOS stream is carried alongside, from api.weather.gov, and
drawn as a band around the hourly trace rather than as a line through it. The
distinction is the point:

    THE HOURLY REPORTS ARE THE RECORD A CONTRACT SETTLES ON.
    THIS IS CONTEXT, AND IT IS LABELLED AS CONTEXT EVERYWHERE IT APPEARS.

A daily maximum taken over five minute data lands at or above one taken over the
hourly reports and effectively never below, so treating the two as
interchangeable would move the number a contract settles on. Nothing here feeds
the observation snapshot, the scorecard, or any settled value.

US stations only: the five minute stream is an ASOS product and api.weather.gov
serves the United States.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional

from . import archive as arch
from . import basemap
from . import gov_weather as gw
from .storage import Storage

SCHEMA = 1
KEY = "snapshots/subhourly/{sid}.json"
INDEX_KEY = "snapshots/subhourly/index.json"
CACHE = "public, max-age=300, stale-while-revalidate=1800, stale-if-error=86400"
HOURS = 30          # enough to cover the chart's window with room either side
WORKERS = 4


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def envelope(rows: List[dict]) -> List[dict]:
    """Per clock hour: the lowest and highest reading inside it, and how many
    there were.

    The band a page draws is this, not the raw points: three hundred dots is a
    smear, and what is worth seeing is how far the truth ranged inside each hour
    the record has a single number for. The count travels with it because a band
    built from two readings means something different from one built from twelve.
    """
    by: Dict[str, List[float]] = {}
    for r in rows:
        t, v = r.get("t"), r.get("tempF")
        if not t or v is None:
            continue
        by.setdefault(str(t)[:13], []).append(float(v))
    out = []
    for hour in sorted(by):
        vs = by[hour]
        out.append({"h": hour + ":00:00Z", "lo": round(min(vs), 1),
                    "hi": round(max(vs), 1), "n": len(vs)})
    return out


def subhourly_pass(cfg: dict, store: Storage, fetch: Optional[Callable] = None) -> int:
    t0 = time.time()
    now = dt.datetime.now(dt.timezone.utc)
    gw.set_user_agent(cfg.get("user_agent", ""))
    deadline = arch.Deadline(arch.remaining_budget(cfg))
    getter = fetch or (lambda sid: gw.fetch_subhourly(sid, HOURS))

    # US stations only: this is an ASOS product on a US government endpoint
    stations = [c["station"] for c in basemap.load_roster() if c.get("unit") == "F"]
    if not stations:
        print(json.dumps({"kind": "subhourly", "written": 0, "reason": "no station roster"}))
        return 0

    written, errors, total = 0, [], 0
    index: Dict[str, dict] = {}

    def one(sid):
        if deadline.over(15):
            return sid, None, "deadline"
        try:
            return sid, getter(sid), None
        except Exception as e:  # noqa: BLE001
            return sid, None, f"{type(e).__name__}"

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for sid, rows, err in pool.map(one, stations):
            if err:
                errors.append(f"{sid}: {err}")
                continue
            rows = rows or []
            if not rows:
                errors.append(f"{sid}: empty")
                continue
            env = envelope(rows)
            doc = {"schema": SCHEMA, "station": sid, "asof": _iso(now), "hours": HOURS,
                   "rows": rows, "hourly": env,
                   "source": "api.weather.gov five minute ASOS stream",
                   "note": "Context only. The contracts settle on the hourly METAR record, which this site "
                           "reads from aviationweather.gov; a maximum taken over five minute data lands at or "
                           "above one taken over the hourly reports and effectively never below."}
            store.put(KEY.format(sid=sid), json.dumps(doc, separators=(",", ":")).encode(),
                      "application/json", CACHE)
            written += 1
            total += len(rows)
            index[sid] = {"n": len(rows), "hours": len(env), "asof": _iso(now)}

    store.put(INDEX_KEY, json.dumps({"schema": SCHEMA, "asof": _iso(now), "stations": index},
                                    separators=(",", ":")).encode(), "application/json", CACHE)
    arch.LAST_STATUS = {"job": "subhourly", "errors": len(errors),
                        "alarms": ["subhourly: no station answered"] if (stations and not written) else []}
    print(json.dumps({"kind": "subhourly", "stations": len(stations), "written": written,
                      "readings": total, "errors": errors[:5], "seconds": round(time.time() - t0, 1)}))
    return 1 if (stations and not written) else 0
