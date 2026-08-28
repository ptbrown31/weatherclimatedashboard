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
from . import energy as en
from . import gov_weather as gw
from .storage import Storage

SCHEMA = 1
KEY = "snapshots/series/{key}.json"
INDEX = "snapshots/series/index.json"
# These are written once a day, so the number that matters is not how often they
# change but how long a correction takes to reach a reader. At an hour, a fix to
# a series stayed invisible in browsers that already held it for the rest of that
# hour, with the corrected file sitting at the origin the whole time and no
# invalidation able to help. Five minutes of revalidation costs a conditional
# request the edge answers, and stale-while-revalidate means no reader waits on
# it. The long stale-if-error still covers an outage.
CACHE = "public, max-age=300, stale-while-revalidate=86400, stale-if-error=2592000"
# The index decides whether a contract page draws a chart at all, so a new series
# is invisible until it expires, and it is smaller still.
INDEX_CACHE = "public, max-age=120, stale-while-revalidate=86400, stale-if-error=2592000"

CAAG = ("https://www.ncei.noaa.gov/access/monitoring/climate-at-a-glance/city/time-series/"
        "{station}/{param}/1/0/{y0}-{y1}.json")
# the national and global panels of the same service; the scale and month in the
# path pick annual (12/12) or every month (1/0)
CAAG_NAT = ("https://www.ncei.noaa.gov/access/monitoring/climate-at-a-glance/national/time-series/"
            "110/tavg/{scale}/{month}/1895-{y1}.json")
CAAG_GLOBE = ("https://www.ncei.noaa.gov/access/monitoring/climate-at-a-glance/global/time-series/"
              "globe/land_ocean/{scale}/{month}/1850-{y1}.json")
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
    return {"points": caag_points(d), "title": desc.get("title") or "",
            "units": desc.get("units") or UNITS.get(param, "")}


def caag_points(d: dict) -> list:
    """The data block of any Climate at a Glance answer, as [period, value]."""
    pts = []
    for k, v in sorted((d.get("data") or {}).items()):
        # the city and national panels answer with 'value'; the global panel
        # answers with 'departure', because it publishes an anomaly
        val = (v or {}).get("value")
        if val is None:
            val = (v or {}).get("departure")
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
    return pts


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


# Contracts that read a national or global series rather than a city one. The
# unit that governs differs and is not cosmetic: the global temperature contract
# resolves on the Celsius anomaly against the twentieth-century average, and the
# US contract resolves on the Fahrenheit average of the contiguous states. Each
# entry says which, and the page repeats it.
WIDE = {
    "gt-annual": {"url": CAAG_GLOBE, "scale": "12", "month": "12", "products": ["GT", "GTTA", "RT"],
                  "source": "NOAA Climate at a Glance, global land and ocean",
                  "note": "The contract resolves on the Celsius value against the twentieth-century average; "
                          "Fahrenheit is shown by the exchange for convenience only."},
    "gt-monthly": {"url": CAAG_GLOBE, "scale": "1", "month": "0", "products": ["GTM", "GTTM", "MRT"],
                   "source": "NOAA Climate at a Glance, global land and ocean, monthly",
                   "note": "Celsius against the twentieth-century average."},
    "ust-annual": {"url": CAAG_NAT, "scale": "12", "month": "12", "products": ["UST"],
                   "source": "NOAA Climate at a Glance, contiguous United States",
                   "note": "The contract resolves on the Fahrenheit value; Celsius is shown by the exchange for "
                           "convenience only."},
    "ust-monthly": {"url": CAAG_NAT, "scale": "1", "month": "0", "products": ["USTM"],
                    "source": "NOAA Climate at a Glance, contiguous United States, monthly",
                    "note": "Fahrenheit, and an average rather than an anomaly."},
}
GML_CO2 = "https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_mm_mlo.csv"
PSD_ZIP = "https://apps.fas.usda.gov/psdonline/downloads/psd_grains_pulses_csv.zip"
# product -> the commodity as USDA names it. The two-letter code in the product
# id is the exchange's, not the department's.
CROPS = {"GCYCO": ("crop-corn", "Corn"), "GCYWH": ("crop-wheat", "Wheat"),
         "GCYRM": ("crop-rice", "Rice, Milled")}


def crop_yields(fetch: Optional[Callable] = None) -> Dict[str, dict]:
    """World average yield per crop, in metric tons per hectare, by the year the
    contract names.

    Two things here are easy to get wrong and both change the answer.

    The bulk file carries no World row, only countries, so the world yield is
    world production over world area harvested, which is how the department
    derives it. Production is in thousands of tonnes and area in thousands of
    hectares, so the ratio is already tonnes per hectare.

    Which production, though, is the part that bites. Rice is reported milled,
    while the area harvested is paddy, so milled over area is a yield in neither
    basis: it came out around two thirds of the published figure and put the
    series a third below the strikes listed against it. The department reports
    Rough Production alongside, and its own Yield attribute is that over the same
    area, so where a crop carries a rough figure that is the one to divide. For
    corn and wheat the two are the same number.

    And the years differ by one. The terms say the reference year in the event
    question is the SECOND year of the marketing year while the year listed in
    the database is the first, so a contract for 2026 settles on the database's
    2025. The series is keyed by the contract's year, because that is what the
    strikes are labelled with and a chart that mixed the two would put every
    strike a year out of place.
    """
    import io
    import zipfile
    raw = fetch(PSD_ZIP) if fetch else gw._fetch(PSD_ZIP, timeout=180)
    z = zipfile.ZipFile(io.BytesIO(raw))
    name = next((n for n in z.namelist() if n.endswith(".csv")), None)
    if not name:
        raise ValueError("no csv in the PSD download")
    agg: Dict[str, Dict[int, Dict[str, float]]] = {}
    for r in csv.DictReader(io.StringIO(z.read(name).decode("utf-8-sig", "replace"))):
        crop = (r.get("Commodity_Description") or "").strip()
        att = (r.get("Attribute_Description") or "").strip()
        if att not in ("Production", "Rough Production", "Area Harvested"):
            continue
        try:
            my, v = int(r.get("Market_Year")), float(r.get("Value"))
        except (TypeError, ValueError):
            continue
        slot = agg.setdefault(crop, {}).setdefault(my, {"prod": 0.0, "rough": 0.0, "area": 0.0})
        slot[{"Production": "prod", "Rough Production": "rough"}.get(att, "area")] += v
    out = {}
    for pid, (key, crop) in CROPS.items():
        by = agg.get(crop) or {}
        pts = [[str(my + 1), round((d["rough"] or d["prod"]) / d["area"], 3)]
               for my, d in sorted(by.items()) if d["area"] > 0]
        if pts:
            out[key] = {"points": pts, "products": [pid], "crop": crop,
                        "title": "World average " + crop.lower() + " yield",
                        "units": "metric tons per hectare"}
    return out

# Things a reader arriving from the daily letter needs in order to recognise a
# contract, or would otherwise have to infer. Kept as plain facts: no
# probability, no view, nothing that could disagree with what is published
# elsewhere about the same contract.
PRODUCT_NOTES = {
    "GTTA": "This is the annual leg of the Paris Agreement Forecast Contracts, which the exchange lists as "
            "Annual Global Temperature Threshold. It asks whether any year breaches a target on or before an "
            "end date.",
    "GTTM": "This is the monthly leg of the Paris Agreement Forecast Contracts, which the exchange lists as "
            "Monthly Global Temperature Threshold. It asks whether any month breaches a target on or before an "
            "end date.",
    "RT": "This contract asks whether the year ranks warmest since 1850, not whether it passes a level. "
          "Year-to-year swings around the warming trend are driven substantially by El Nino and La Nina, so a "
          "record is far more likely in some years than the trend alone suggests.",
    "MRT": "This contract asks whether the month ranks warmest on record, not whether it passes a level. "
           "Month-to-month swings around the warming trend are driven substantially by El Nino and La Nina.",
    "GT": "The value is an anomaly against the twentieth-century average, not an absolute temperature.",
    "UST": "This is an absolute average temperature rather than an anomaly, so it is not comparable with the "
           "global temperature contracts on this site.",
}
# series whose contracts ask about a record, so the page states the mark to beat
RECORD_SERIES = ("gt-annual", "gt-monthly")


def co2_monthly(fetch: Optional[Callable] = None) -> dict:
    """Mauna Loa monthly mean CO2, as [YYYYMM, ppm]. The file carries a decimal
    year, the monthly mean, and a de-seasonalised column; the contract reads the
    monthly mean, so that is the column taken."""
    raw = (fetch or gw._get_text)(GML_CO2, timeout=90)
    pts = []
    for line in raw.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            y, m, mean = int(float(parts[0])), int(float(parts[1])), float(parts[3])
        except (TypeError, ValueError):
            continue
        if mean <= 0 or not (1 <= m <= 12):
            continue
        pts.append([f"{y}{m:02d}", round(mean, 2)])
    pts.sort()
    return {"points": pts, "title": "Mauna Loa monthly mean carbon dioxide", "units": "parts per million"}


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

    # ---- the national and global temperature series
    for key, spec in WIDE.items():
        if deadline.over(15):
            errors.append(f"{key}: deadline")
            continue
        try:
            url = spec["url"].format(scale=spec["scale"], month=spec["month"], y1=now.year)
            d = (fetch or _json)(url) if fetch else _json(url)
            desc = d.get("description") or {}
            doc = caag_points(d)
            put(key, {"key": key, "products": spec["products"], "title": desc.get("title") or key,
                      "units": desc.get("units") or "", "base": desc.get("base_period"),
                      "points": doc, "source": spec["source"], "note": spec["note"]})
        except Exception as e:  # noqa: BLE001
            errors.append(f"{key}: {type(e).__name__}: {e}")

    # ---- Mauna Loa carbon dioxide
    if not deadline.over(15):
        try:
            c = co2_monthly(fetch)
            put("co2-monthly", {"key": "co2-monthly", "products": ["MACD", "ACD"], "title": c["title"],
                                "units": c["units"], "points": c["points"],
                                "source": "NOAA Global Monitoring Laboratory, Mauna Loa",
                                "note": "The monthly mean, which is the column the contract reads, not the "
                                        "de-seasonalised trend published beside it."})
        except Exception as e:  # noqa: BLE001
            errors.append(f"co2: {type(e).__name__}: {e}")

    # ---- world crop yields
    if not deadline.over(25):
        try:
            for key, doc in crop_yields(fetch).items():
                put(key, {"key": key, "products": doc["products"], "title": doc["title"], "units": doc["units"],
                          "points": doc["points"],
                          "source": "USDA Foreign Agricultural Service, Production Supply and Distribution",
                          "note": "World production over world area harvested, which is how the department derives "
                                  "the world yield; the bulk file carries countries only. Years follow the contract, so "
                                  "the marketing year the database labels 2025 is the 2026 contract, because the "
                                  "event question names the second year of the marketing year. A contract resolves "
                                  "on the first report after the marketing year ends, and surpassing a threshold "
                                  "means strictly greater than it."})
        except Exception as e:  # noqa: BLE001
            errors.append(f"crops: {type(e).__name__}: {e}")

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

    # ---- the energy series, which need a key and are skipped without one
    if not deadline.over(40):
        if not en.api_key(cfg):
            errors.append("energy: no EIA key configured, so the energy series were not fetched")
        else:
            try:
                for key, doc in en.energy_series(cfg, fetch).items():
                    put(key, doc)
            except Exception as e:  # noqa: BLE001
                errors.append(f"energy: {type(e).__name__}: {e}")

    products = {pid: k for pid, (k, _, _) in PRODUCTS.items()}
    for key, spec in WIDE.items():
        for pid in spec["products"]:
            products[pid] = key
    for pid in ("MACD", "ACD"):
        products[pid] = "co2-monthly"
    for pid, (key, _) in CROPS.items():
        products[pid] = key
    products["USDR"] = "drought-us"
    products.update(en.product_keys())
    # only advertise a series that this pass actually wrote or that already
    # exists, so a page never fetches a key that is not there
    products = {pid: k for pid, k in products.items() if k in written or store.get(KEY.format(key=k))}
    idx = {"schema": SCHEMA, "asof": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "series": [{"key": k, "points": n} for k, n in sorted(written.items())],
           "products": products, "productNotes": PRODUCT_NOTES, "record": list(RECORD_SERIES)}
    store.put(INDEX, json.dumps(idx, separators=(",", ":")).encode(), "application/json", INDEX_CACHE)

    arch.LAST_STATUS = {"job": "series", "errors": len(errors), "alarms": []}
    print(json.dumps({"kind": "series", "written": written, "errors": errors[:6],
                      "seconds": round(time.time() - t0, 1)}))
    return 1 if errors and not written else 0
