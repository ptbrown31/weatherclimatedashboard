"""The series the monthly and weekly weather contracts settle on.

Each contract family here names its own source in its published terms, and this
module fetches exactly that source rather than a convenient stand-in:

  USDR    US Drought Monitor, percent of CONUS land area in drought. The terms
          say "the percentage of the US"; the Monitor's national view is the
          contiguous states, which is the reading the exchange confirmed, and
          the page says so. The settlement figure is 100 less the "None"
          column, which is the D0-through-D4 total the Monitor shows first.

  TR...   Total rain, from NOAA's Climate at a Glance city precipitation
          series. Settlement itself reads the NWS Climatological Report's
          month-to-date row on the morning after the month ends and ignores
          later revisions, so `metar`-style first publication is captured
          separately by the archive; this series is the history a chart needs.

  OA...   Average monthly temperature, from the same Climate at a Glance city
          series with the temperature parameter.

Two things the terms make load-bearing and this module does not paper over.
First, both families resolve on the value published at expiration and not on
any later revision, so a chart drawn from the live series can disagree with how
a past contract actually settled; the pages say which they are showing. Second,
NOAA's city series is one series per city, so a product code naming an airport
resolves against the city NOAA publishes: the Chicago contract carries the code
MDW while NOAA's Chicago series is O'Hare, and the page names the station it
actually drew.

No key is needed by anything here, and every source is the one named in the
contract's terms.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import time
from typing import Callable, Dict, List, Optional

from . import archive as arch
from . import gov_weather as gw
from .storage import Storage

SCHEMA = 1
KEY = "snapshots/series/{key}.json"
INDEX = "snapshots/series/index.json"
CACHE = "public, max-age=3600, stale-while-revalidate=86400, stale-if-error=2592000"

CAAG = ("https://www.ncei.noaa.gov/access/monitoring/climate-at-a-glance/city/time-series/"
        "{station}/{param}/1/0/{y0}-{y1}.json")
USDM = ("https://usdmdataservices.unl.edu/api/USStatistics/GetDroughtSeverityStatisticsByAreaPercent"
        "?aoi=us&startdate={start}&enddate={end}&statisticsType=1")
FIRST_YEAR = 1990

# station ids confirmed one at a time against the title NOAA returns, because a
# wrong id answers with a real series for the wrong place
CITY = {
    "HOU": ("USW00012918", "Houston, Texas"),
    "MDW": ("USW00094846", "Chicago, Illinois"),
    "MIA": ("USW00012839", "Miami, Florida"),
    "NYC": ("USW00094728", "New York (Central Park), New York"),
    "SEA": ("USW00024233", "Seattle, Washington"),
    "LAX": ("USW00023174", "Los Angeles, California"),
}
# product -> (series key, city code, Climate at a Glance parameter)
PRODUCTS = {
    "TRHOU": ("rain-hou", "HOU", "pcp"), "TRMDW": ("rain-mdw", "MDW", "pcp"),
    "TRMIA": ("rain-mia", "MIA", "pcp"), "TRNYC": ("rain-nyc", "NYC", "pcp"),
    "TRSEA": ("rain-sea", "SEA", "pcp"),
    "OALAX": ("tavg-lax", "LAX", "tavg"), "OAMDW": ("tavg-mdw", "MDW", "tavg"),
    "OANYC": ("tavg-nyc", "NYC", "tavg"),
}
UNITS = {"pcp": "inches", "tavg": "degrees Fahrenheit"}


def _json(url: str, timeout: int = 60) -> dict:
    return json.loads(gw._get_text(url, timeout=timeout))


def caag(station: str, param: str, y1: int, fetch: Optional[Callable] = None) -> dict:
    """One city series, monthly, as {'points': [[YYYYMM, value]], 'title':, 'units':}.

    NOAA answers with a titled description; the title is kept and shown, because
    it names the place the number is actually for and that is not always the
    place the product code suggests."""
    d = (fetch or _json)(CAAG.format(station=station, param=param, y0=FIRST_YEAR, y1=y1))
    desc = d.get("description") or {}
    pts = []
    for k, v in sorted((d.get("data") or {}).items()):
        val = (v or {}).get("value")
        if val is None or val == "":
            continue
        try:
            f = float(val)
        except (TypeError, ValueError):
            continue
        # NOAA marks a missing month with a large negative sentinel
        if f <= -99:
            continue
        pts.append([k, round(f, 2)])
    return {"points": pts, "title": desc.get("title") or "", "units": desc.get("units") or UNITS.get(param, "")}


def drought(now: dt.datetime, fetch: Optional[Callable] = None) -> dict:
    """Percent of CONUS in drought, weekly. The Monitor publishes the share of
    area in each severity class and a 'None' column; the figure the contract
    reads is everything that is not None, which is the D0-D4 total."""
    url = USDM.format(start=f"1/1/{FIRST_YEAR}", end=now.strftime("%m/%d/%Y"))
    raw = (fetch or gw._get_text)(url, timeout=90)
    # The feed answers with two areas for the same week: CONUS, and Total, which
    # adds Alaska, Hawaii and Puerto Rico. They differ by around ten points, so
    # taking whichever row came last would silently pick the wrong number. The
    # contract reads the Monitor's national view, which is the contiguous states.
    pts = []
    for row in csv.DictReader(io.StringIO(raw)):
        if (row.get("AreaOfInterest") or "").strip().upper() != "CONUS":
            continue
        d = (row.get("MapDate") or "").strip()
        none = row.get("None")
        if len(d) != 8 or none in (None, ""):
            continue
        try:
            pts.append([d, round(100.0 - float(none), 2)])
        except (TypeError, ValueError):
            continue
    pts.sort()
    return {"points": pts, "title": "Percent of the contiguous United States in drought (D0-D4)",
            "units": "percent", "area": "CONUS"}


def series_pass(cfg: dict, store: Storage, fetch: Optional[Callable] = None) -> int:
    t0 = time.time()
    now = dt.datetime.now(dt.timezone.utc)
    deadline = arch.Deadline(arch.remaining_budget(cfg))
    gw.set_user_agent(cfg.get("user_agent", ""))
    errors: List[str] = []
    written: Dict[str, int] = {}

    def put(key: str, doc: dict) -> None:
        prev = store.get(KEY.format(key=key))
        if not doc.get("points"):
            # a pass that fetched nothing keeps what is already published rather
            # than replacing a real series with an empty one
            if prev:
                errors.append(f"{key}: no points, previous series kept")
                return
        doc.update({"schema": SCHEMA, "asof": now.strftime("%Y-%m-%dT%H:%M:%SZ")})
        store.put(KEY.format(key=key), json.dumps(doc, separators=(",", ":")).encode(), "application/json", CACHE)
        written[key] = len(doc.get("points") or [])

    # ---- the city series, one fetch per distinct station and parameter
    seen = {}
    for pid, (key, city, param) in PRODUCTS.items():
        if deadline.over(15):
            errors.append(f"{pid}: deadline")
            continue
        station, expect = CITY[city]
        cache_key = (station, param)
        try:
            doc = seen.get(cache_key) or caag(station, param, now.year, fetch)
            seen[cache_key] = doc
        except Exception as e:  # noqa: BLE001
            errors.append(f"{pid}: {type(e).__name__}: {e}")
            continue
        put(key, {"key": key, "products": [pid], "station": station, "expected": expect,
                  "title": doc["title"], "units": doc["units"], "points": doc["points"],
                  "source": "NOAA Climate at a Glance, city time series",
                  "note": "The contract resolves on the value published at expiration; later revisions to this "
                          "series do not change how a contract settled."})

    # ---- drought
    if not deadline.over(20):
        try:
            d = drought(now, fetch)
            put("drought-us", {"key": "drought-us", "products": ["USDR"], "title": d["title"], "units": d["units"],
                               "points": d["points"], "area": d["area"],
                               "source": "US Drought Monitor (NDMC, USDA, NOAA)",
                               "note": "The national figure is the contiguous states. The Monitor publishes it every "
                                       "Thursday; the contract reads the share of land area in D0 through D4."})
        except Exception as e:  # noqa: BLE001
            errors.append(f"USDR: {type(e).__name__}: {e}")

    idx = {"schema": SCHEMA, "asof": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "series": [{"key": k, "points": n} for k, n in sorted(written.items())],
           "products": {pid: k for pid, (k, _, _) in PRODUCTS.items()}}
    idx["products"]["USDR"] = "drought-us"
    store.put(INDEX, json.dumps(idx, separators=(",", ":")).encode(), "application/json", CACHE)

    arch.LAST_STATUS = {"job": "series", "errors": len(errors), "alarms": []}
    print(json.dumps({"kind": "series", "written": written, "errors": errors[:6],
                      "seconds": round(time.time() - t0, 1)}))
    return 1 if errors and not written else 0
