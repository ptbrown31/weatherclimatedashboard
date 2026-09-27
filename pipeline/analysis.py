"""
analysis.py — how a settlement framework built on NOAA's gridded analyses
would have resolved, at fifty population centres.

The contracts settle on station reports, and this site's other lanes read
those. This lane shows the alternative the owner asked to see (docs/analysis.md):
the daily and hourly values a contract would resolve on if it were written on
the Real-Time Mesoscale Analysis (RTMA) and its rerun with the late-arriving
observations, the Unrestricted Mesoscale Analysis (URMA), read from NOAA Open
Data on AWS. No contract settles on it, the page says so on every view, and
nothing here feeds any other lane.

Conventions, all from docs/analysis.md section 1 and fixed there:

    The value at a place is the nearest 2.5 km grid cell to the Census internal
    point of the place, never an interpolation, because a contract needs one
    number that two readers can recompute from the same file.

    The day is the local civil date at the place through its IANA zone. Every
    top-of-hour analysis whose local civil time falls on that date counts. That is
    24 analyses on an ordinary day, 23 on the spring-forward day (02:00 local
    does not occur) and 25 on the fall-back day (01:00 local occurs twice, and
    both analyses belong to the date). Complete means every one of them was
    read; the mean wind is over the hours read. RTMA is always provisional.
    URMA is the analysis of record: its high, low, gust and wind are final
    when every hourly analysis file of the day has been read; its
    precipitation is resolved at the first read that has a value for every
    hour of the day, and that total never changes. The River Forecast
    Centers rerun their gauge analyses for up to eight days, so the URMA
    precipitation of each of the last eight local days is re-read once a day
    and a total that differs from the resolved one is carried beside it as a
    revision, never in its place.

    Rounding is half up, away from zero (-20.5 F is -21, the way a published
    table shows it), once, on the aggregate itself (the highest hourly
    temperature, the mean wind, the summed accumulation); the exact value
    shown beside the whole one is that same aggregate to a tenth, the mean
    wind to a hundredth and precipitation to a ten-thousandth, so a reader
    can check the rounding and never sees it applied twice. Yes resolves on
    a strict inequality and equal to the strike is No. These mirror the
    exchange's daily contracts.

    A day still short of its hours 48 hours after its local end is closed
    incomplete: its value stands on the hours read, is never marked final,
    and the count is shown. The hourly analysis files are never rewritten
    upstream, so a missing hour stays missing. The precipitation files are
    rewritten upstream, so an hour's precipitation lives in its own key and
    is filled in as the files improve.

Reads   noaa-rtma-pds and noaa-urma-pds through pipeline/gov_weather.py; the
        .idx sidecar of each analysis file and a range request per message
        (TMP 2 m, WIND 10 m, GUST 10 m, about 6 MB each), the one-message
        precipitation file whole; GRIB2 decoded by pipeline/grib2.py.
Writes  archive/analysis/hours/<product>/<stamp>.json.gz   the fifty exact hourly temperature, wind and gust values, write once
        archive/analysis/precip/<product>/<stamp>.json.gz  the fifty exact hourly accumulations, rewritten as coverage fills in
        archive/analysis/_meta/state.json                  cursors, gaps, pending precipitation, re-read and prune stamps, revisions
        archive/_meta/health_analysis.json                 the lane's own failure streaks
        snapshots/analysis/grid/<product>/<var>/<stamp>.json   one map frame on the 320 x 200 lattice
        snapshots/analysis/grid/index.json                 which frames exist, rewritten after every hour's frames
        snapshots/analysis/days/YYYY-MM-DD.json            every place's product-days for that local date
        snapshots/analysis/loc/<id>/YYYY-MM-DD.json        one place-day at the hourly scale
        snapshots/analysis/index.json                      last, so a reader sees a consistent set

The pass runs on its own schedule (every ten minutes at :08) and is capped at
PASS_CAP_SECONDS so two passes never overlap on the state file. Each pass
retries the precipitation still pending, reads the live hours, rebuilds the
days those hours touch, re-reads one day's URMA precipitation when one is
due, and spends what budget remains walking backwards through the last
thirty days, newest first, so the page fills in from today back. Nothing a
NOAA object does (missing, short, refused by the decoder, a 5xx after the
retries) fails the pass: each hour's read is guarded, the failure is recorded
against its product, and the state and the indexes are written in every case.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import math
import os
import time
from decimal import Decimal
from typing import Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from . import archive as arch
from . import gov_weather as gw
from .storage import Storage

try:
    from . import grib2
except ImportError:  # the decoder is a separate module; without it the pass reports and writes nothing
    grib2 = None  # type: ignore

SCHEMA = "analysis/1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCATIONS_PATH = os.path.join(ROOT, "config", "analysis_locations.json")
LATTICE_PATH = os.path.join(ROOT, "config", "analysis_lattice.json")

PREFIX = "snapshots/analysis/"
INDEX_KEY = PREFIX + "index.json"
DAY_KEY = PREFIX + "days/{day}.json"
LOC_KEY = PREFIX + "loc/{loc}/{day}.json"
GRID_INDEX_KEY = PREFIX + "grid/index.json"
FRAME_KEY = PREFIX + "grid/{product}/{var}/{stamp}.json"
HOUR_KEY = "archive/analysis/hours/{product}/{stamp}.json.gz"
# the precipitation of a product-hour is its own key because NOAA rewrites
# the precipitation files (the RFC regions land in batches and the gauge
# reruns follow for days) while the analysis hour is written once
PRECIP_KEY = "archive/analysis/precip/{product}/{stamp}.json.gz"
STATE_KEY = "archive/analysis/_meta/state.json"
HEALTH_KEY = "archive/_meta/health_analysis.json"   # this lane's own failure streaks (see archive.update_health)
# frames never change and a final place-day changes only by a revision; everything else is live
CACHE_FINAL = "public, max-age=300, stale-while-revalidate=1800, stale-if-error=86400"
CACHE_LIVE = "public, max-age=60, stale-while-revalidate=300, stale-if-error=86400"

STATEMENT = "A proposed settlement framework. No contract settles on it."

# The two products and where each file lives: <bucket>/<dir>.<YYYYMMDD>/<file>.
# The analysis file is one per hour with a .idx sidecar; the precipitation
# file is one message. RTMA precipitation is on the smaller G184 grid, URMA's
# on the wexp grid of the analysis itself. lagMinutes is the measured landing
# time of the analysis after the valid hour (docs/analysis.md, 2026-09-27),
# and is what the live lane and the health check expect, not a promise NOAA
# makes. The URMA precipitation files appear hours ahead of their analysis,
# in a batch, with the western RFC region absent from the bitmap, and are
# rewritten later; the lane never waits on them before archiving the analysis.
NODD_DEFAULT = {"rtma": "https://noaa-rtma-pds.s3.amazonaws.com", "urma": "https://noaa-urma-pds.s3.amazonaws.com"}
PRODUCTS = {
    "rtma": {"bucket": "noaa-rtma-pds", "dir": "rtma2p5", "anl": "rtma2p5.t{hh}z.2dvaranl_ndfd.grb2_wexp",
             "pcp": "rtma2p5.{ymdh}.pcp.184.grb2", "pcpGrid": "g184", "lagMinutes": 47},
    "urma": {"bucket": "noaa-urma-pds", "dir": "urma2p5", "anl": "urma2p5.t{hh}z.2dvaranl_ndfd.grb2_wexp",
             "pcp": "urma2p5.{ymdh}.pcp_01h.wexp.grb2", "pcpGrid": "wexp", "lagMinutes": 414},
}
# the three messages read from the analysis file, by the .idx sidecar's names
MESSAGES = (("temp", "TMP", "2 m above ground"), ("wind", "WIND", "10 m above ground"),
            ("gust", "GUST", "10 m above ground"))
# GRIB2 discipline, parameter category and number of each variable; a message
# that carries anything else is refused whatever the sidecar called it
PARAM = {"temp": (0, 0, 0), "wind": (0, 2, 1), "gust": (0, 2, 22), "precip": (0, 1, 8)}
HOURLY_VARS = ("temp", "wind", "gust", "precip")
ANALYSIS_VARS = ("temp", "wind", "gust")     # what the write-once archive hour holds
DAILY_VARS = ("high", "low", "gust", "wind", "precip")
UNITS = {"temp": "°F", "wind": "mph", "gust": "mph", "precip": "in"}
# what a frame's int is divided by: tenths of a degree and of a mile per
# hour, hundredths of an inch
SCALE = {"temp": 10, "wind": 10, "gust": 10, "precip": 100}
# the exact hourly value kept in the archive: tenths, and precipitation to a
# ten-thousandth of an inch so a day's sum carries the thousandths the
# contract shows beside the rounded total
HOUR_UNIT = {"temp": 0.1, "wind": 0.1, "gust": 0.1, "precip": 0.0001}
# the whole value a strike is compared with, and the precision of the exact
# aggregate shown beside it (a mean of tenths is kept to a hundredth so the
# whole value is never the rounding of an already rounded number)
DAY_UNIT = {"high": 1, "low": 1, "gust": 1, "wind": 1, "precip": 0.01}
EXACT_UNIT = {"high": 0.1, "low": 0.1, "gust": 0.1, "wind": 0.01, "precip": 0.0001}
MPH_PER_MS = 2.2369362921
MM_PER_INCH = 25.4

LATTICE_PITCH = 3
LATTICE_COLS, LATTICE_ROWS = 320, 200
BACKFILL_POINT_DAYS = 30        # location values this far back, by random-access cell reads
BACKFILL_FRAME_DAYS = 7         # map frames this far back
FRAME_KEEP_DAYS = 30            # frames older than this are pruned
INDEX_DAYS = 60                 # index.json lists this many newest days; the day files themselves are kept
PRECIP_REREAD_DAYS = 8          # the RFC gauge reruns reach back about this far
CLOSE_AFTER_HOURS = 48          # a day still incomplete this long after its local end is closed
LIVE_HOURS_PER_PASS = 3         # new hours read per product per pass
# a product-hour whose precipitation file is absent, refused, or short of a
# location cell (the western RFC region lands late in the URMA files) has
# that file refetched every pass until this long past the analysis lag; after
# that the hour keeps whatever coverage it has and, for URMA, the daily
# re-read fills the rest
PRECIP_WAIT_HOURS = 3
# a product whose newest hour READ is this much or more behind what should
# have landed counts as a failed pass toward the lane's alarm; short of it,
# an empty pass is the normal wait between landings
OVERDUE_HOURS = 2
# a live cursor further behind than this stops walking hour by hour: it jumps
# to the present and the span it skipped is queued for the backfill
LIVE_CATCHUP_HOURS = 24
PASS_CAP_SECONDS = 330          # with FETCH_TRIES x FETCH_TIMEOUT bounding an hour near 212 s,
                                # a pass ends inside the ten-minute cadence, so passes never overlap
RESERVE_SECONDS = 45            # kept back from the lanes for the day rebuild, prune and index
MAX_MESSAGE_BYTES = 16 * 1024 * 1024   # a range for the last message of a file, whose end the sidecar cannot give
STATE_EVERY = 20                # backfill hours between state writes, bounding what a timeout loses
# every NOAA call from this lane: two tries of twenty seconds, so one hour's
# five fetches cannot hold the pass for more than a few minutes and the
# 900 s Lambda timeout is never the thing that stops it
FETCH_TRIES = 2
FETCH_TIMEOUT = 20

CONVENTIONS = {
    "day": "The local civil date at the place through its IANA zone. Every top-of-hour analysis whose local "
           "civil time falls on that date counts, 24 on an ordinary day, 23 on the spring-forward day (02:00 does "
           "not occur) and 25 on the fall-back day (both 01:00 analyses count). Complete means every one of "
           "them was read; the mean wind is over the hours read.",
    "high": "The highest hourly temperature of the day, whole degrees Fahrenheit rounded half up; "
            "Yes when the value is above the strike.",
    "low": "The lowest hourly temperature of the day, whole degrees Fahrenheit rounded half up; "
           "Yes when the value is below the strike.",
    "gust": "The highest hourly gust of the day, whole miles per hour rounded half up; "
            "Yes when the value is above the strike.",
    "wind": "The mean of the day's hourly sustained winds, whole miles per hour rounded half up; "
            "Yes when the value is above the strike.",
    "precip": "The sum of the day's hourly accumulations, inches to a hundredth rounded half up; "
              "Yes when the value is above the strike.",
    "rounding": "Half up, away from zero (-20.5 is -21), once, on the day's aggregate; the exact value beside "
                "the whole one is that aggregate to a tenth, the mean wind to a hundredth and precipitation to "
                "a ten-thousandth of an inch. Equal to the strike resolves No.",
    "provisional": "RTMA is always provisional. URMA is final when every hourly analysis file of the day has been "
                   "read, and its precipitation is resolved at the first read that has a value for every hour. "
                   "A later re-read that differs is shown beside the resolved value, which stands.",
    "closed": "A day still short of its hours 48 hours after its local end is closed incomplete. Its value "
              "stands on the hours read, it is never marked final, and the count is shown.",
    "hourly": "The hourly value is the analysis at the top of the hour, and for precipitation the accumulation "
              "over the hour ending then. Station report conventions (the last report in the hour, specials, "
              "the tenths group) have no analogue here.",
    "cell": "The nearest 2.5 km grid cell to the Census internal point of the place, fractional grid "
            "coordinates rounded half up. The cell's own centre and its distance from the point are shown.",
    "lattice": "The map samples each hourly field onto a 320 by 200 lattice at 3 screen pixels, about 14 km "
               "between points nationally. It is a subsample for display; the place values come from the "
               "full grid.",
    "units": "Kelvin to Fahrenheit exactly, metres per second to miles per hour by 2.2369362921, "
             "millimetres to inches by 1/25.4.",
}
VARIABLES = {
    "high": {"unit": "°F", "hourly": "temp", "rule": CONVENTIONS["high"]},
    "low": {"unit": "°F", "hourly": "temp", "rule": CONVENTIONS["low"]},
    "gust": {"unit": "mph", "hourly": "gust", "rule": CONVENTIONS["gust"]},
    "wind": {"unit": "mph", "hourly": "wind", "rule": CONVENTIONS["wind"]},
    "precip": {"unit": "in", "hourly": "precip", "rule": CONVENTIONS["precip"]},
}


# ------------------------------------------------------------------ small helpers
def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_iso(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _stamp(t: dt.datetime) -> str:
    """The file-name form of an hour, YYYYMMDDTHHZ."""
    return t.astimezone(dt.timezone.utc).strftime("%Y%m%dT%HZ")


def _from_stamp(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y%m%dT%HZ").replace(tzinfo=dt.timezone.utc)


def _floor_hour(t: dt.datetime) -> dt.datetime:
    return t.astimezone(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)


def _half_up_int(x: float) -> int:
    # snapshots._half_up, copied rather than imported: it is a private name of
    # another lane, and the rule has to read here where the daily values are
    # made. Round half away from zero (-20.5 is -21), which is what a
    # published table shows and is not what round() does.
    return int(math.floor(x + 0.5)) if x >= 0 else -int(math.floor(-x + 0.5))


def half_up(x: float, unit: float = 1):
    """x rounded half up, away from zero, to a multiple of unit (1, 0.1,
    0.01, 0.0001). The scaling goes through Decimal on the value's shortest
    repr, because 70.55 in binary is a hair under 70.55 and 70.55 / 0.1 lands
    at 705.4999, which would round the wrong way. Whole units return an int."""
    if x is None:
        return None
    if unit == 1:
        return _half_up_int(float(x))
    q = Decimal(repr(round(float(x), 9))) / Decimal(repr(unit))
    n = _half_up_int(float(q))
    return float(Decimal(n) * Decimal(repr(unit)))


def k_to_f(k: float) -> float:
    return (k - 273.15) * 9.0 / 5.0 + 32.0


def ms_to_mph(v: float) -> float:
    return v * MPH_PER_MS


def mm_to_inch(v: float) -> float:
    return v / MM_PER_INCH


CONVERT: Dict[str, Callable[[float], float]] = {"temp": k_to_f, "wind": ms_to_mph, "gust": ms_to_mph, "precip": mm_to_inch}


def _read_json(store: Storage, key: str) -> Optional[dict]:
    raw = store.get(key)
    if not raw:
        return None
    try:
        body = json.loads(raw)
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _read_gz(store: Storage, key: str) -> Optional[dict]:
    raw = store.get(key)
    if not raw:
        return None
    try:
        body = json.loads(gzip.decompress(raw))
    except (OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def _dump(doc) -> bytes:
    return json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _gz(doc) -> bytes:
    return gzip.compress(_dump(doc), compresslevel=6)


def _fail(status: dict, text: str) -> None:
    """Record one failure the pass continues past. Texts start with the
    product's name where one is to blame, which is how health_results counts
    them toward that product's streak."""
    status["errors"].append(text)
    status["failed"] += 1


def load_locations(path: str = LOCATIONS_PATH) -> list:
    with open(path) as fh:
        return json.load(fh)


def load_lattice(path: str = LATTICE_PATH) -> dict:
    with open(path) as fh:
        return json.load(fh)


# ------------------------------------------------------------------ the local day
def day_end(day_iso: str, tz: str) -> dt.datetime:
    """The UTC instant the local day ends (the next day's 00:00 local)."""
    zone = ZoneInfo(tz)
    y, m, d = (int(p) for p in day_iso.split("-"))
    nxt = dt.date(y, m, d) + dt.timedelta(days=1)
    return dt.datetime(nxt.year, nxt.month, nxt.day, 0, 0, tzinfo=zone, fold=0).astimezone(dt.timezone.utc)


def local_day_hours(day_iso: str, tz: str) -> List[dt.datetime]:
    """The distinct UTC instants of every top-of-hour analysis whose local
    civil time falls on that date, in order.

    Built with ZoneInfo and never a fixed offset, so daylight time is right
    on every day of the year. The day runs from 00:00 local to the next
    day's 00:00 local, so it is 24 instants on an ordinary day, 23 on the
    spring-forward day (02:00 local does not occur, and no instant is
    counted twice for it) and 25 on the fall-back day (01:00 local occurs
    twice and both analyses fall on the date). The owner's decision of
    2026-09-27: the day is the civil date, and every analysis on it counts
    once.
    """
    zone = ZoneInfo(tz)
    y, m, d = (int(p) for p in day_iso.split("-"))
    t = dt.datetime(y, m, d, 0, 0, tzinfo=zone, fold=0).astimezone(dt.timezone.utc)
    end = day_end(day_iso, tz)
    out = []
    while t < end:
        out.append(t)
        t += dt.timedelta(hours=1)
    return out


def hour_label(t: dt.datetime, tz: str) -> str:
    """The local hour of an instant as the two-digit label the hour table
    shows; the second 01:00 of the fall-back night is '01*' so the two rows
    that share a label can be told apart (the UTC stamp sits beside it)."""
    lt = t.astimezone(ZoneInfo(tz))
    return f"{lt:%H}" + ("*" if lt.fold else "")


def local_day_of(t: dt.datetime, tz: str) -> str:
    """The local date a UTC instant falls on in a zone: exactly one."""
    return t.astimezone(ZoneInfo(tz)).date().isoformat()


def day_summary(product_hours: dict, tz: str, day_iso: str, now: dt.datetime, product: str = "rtma",
                prior: Optional[dict] = None, revised: Optional[dict] = None) -> Optional[dict]:
    """One product-day at a place, from its exact hourly values.

    product_hours maps the hour's UTC ISO string to {temp, wind, gust, precip}
    exact values (None where the product had no value at that hour). An hour
    is read when it has an entry at all. The result is the contract's
    product-day shape, or None when no hour of the day has been read.
    `prior` is the summary published before, which keeps the resolved
    precipitation and its stamp once set; `revised` is the re-read record
    (None for none, or a withdrawn one)."""
    hours = local_day_hours(day_iso, tz)
    read = []
    for h in hours:
        rec = product_hours.get(_iso(h))
        if rec is not None:
            read.append((h, rec))
    if not read:
        return None
    n = len(read)
    expected = len(hours)
    complete = n == expected

    def series(var):
        return [(h, rec[var]) for h, rec in read if rec.get(var) is not None]

    def extreme(var, daily, pick):
        s = series(var)
        if not s:
            return None
        # the first hour at which the extreme stands, which is what a
        # reader checking the hour table expects to find
        if pick is max:
            at, v = max(s, key=lambda p: (p[1], -p[0].timestamp()))
        else:
            at, v = min(s, key=lambda p: (p[1], p[0].timestamp()))
        return {"value": half_up(v, DAY_UNIT[daily]), "exact": half_up(v, EXACT_UNIT[daily]), "at": _iso(at)}

    winds = series("wind")
    precip = series("precip")
    out: dict = {"hours": n, "of": expected, "complete": complete}
    out["high"] = extreme("temp", "high", max)
    out["low"] = extreme("temp", "low", min)
    out["gust"] = extreme("gust", "gust", max)
    if winds:
        mean = sum(v for _, v in winds) / len(winds)
        out["wind"] = {"value": half_up(mean, DAY_UNIT["wind"]), "exact": half_up(mean, EXACT_UNIT["wind"])}
    else:
        out["wind"] = None
    if precip:
        total = sum(v for _, v in precip)
        out["precip"] = {"value": half_up(total, DAY_UNIT["precip"]), "exact": half_up(total, EXACT_UNIT["precip"]),
                         "hours": len(precip)}
    else:
        out["precip"] = None
    # closed incomplete: the local day ended CLOSE_AFTER_HOURS ago and hours are still missing
    closed = (not complete) and now >= day_end(day_iso, tz) + dt.timedelta(hours=CLOSE_AFTER_HOURS)
    out["closed"] = closed
    # RTMA is provisional by definition; URMA is the analysis of record
    out["final"] = bool(product == "urma" and complete)
    if product == "urma" and out["precip"] is not None:
        p = out["precip"]
        was = (prior or {}).get("precip") or {}
        if was.get("resolved"):
            # the resolved total stands; a differing re-read is a revision beside it
            p.update({"value": was["value"], "exact": was["exact"], "resolved": True,
                      "resolvedAt": was.get("resolvedAt")})
        elif len(precip) == expected:
            # resolvedAt is the pass that first saw every hour, not an analysis time
            p.update({"resolved": True, "resolvedAt": _iso(now)})
        else:
            p.update({"resolved": False, "resolvedAt": None})
        p["revised"] = revised if (p["resolved"] and revised) else None
    return out


def resolves(var: str, value, strike) -> Optional[bool]:
    """Whether a hypothetical contract on a daily variable resolves Yes at a
    strike: the low resolves on value below the strike, everything else on
    value above it, and equal to the strike is No on both. The rule the page
    draws its ladders with; kept here so a test can pin it and a reader can
    find it in one place. None while the day has no value."""
    if value is None:
        return None
    return value < strike if var == "low" else value > strike


def _frame_ints(sampled, scale: int, convert: Optional[Callable]) -> list:
    """Lattice values (None off the grid or missing) as ints, value times
    scale rounded half up. The product is rounded to six places first so a
    value that is x.5 in decimal but a hair under in binary (68.05 * 10 is
    680.4999...) rounds the way half_up would."""
    out = []
    for v in sampled:
        if v is None:
            out.append(None)
            continue
        if convert is not None:
            v = convert(v)
        out.append(_half_up_int(round(v * scale, 6)))
    return out


def frame_from_field(values, lattice_indices: list, scale: int, convert: Optional[Callable] = None) -> list:
    """The lattice sample of a decoded field as ints (value times scale,
    half up), None where the lattice point is off the grid (-1) or the cell
    is missing in the bitmap. Only the sampled cells are converted, not the
    3.7 million of the field."""
    return _frame_ints([None if k < 0 else values[k] for k in lattice_indices], scale, convert)


# ------------------------------------------------------------------ NOAA objects
def _bucket(product: str) -> str:
    # gov_weather owns the bucket constants; the default is the same string,
    # so this module imports whether or not that file has them yet
    return getattr(gw, "NODD_" + product.upper(), NODD_DEFAULT[product])


def anl_url(product: str, t: dt.datetime) -> str:
    p = PRODUCTS[product]
    t = t.astimezone(dt.timezone.utc)
    return f"{_bucket(product)}/{p['dir']}.{t:%Y%m%d}/{p['anl'].format(hh=f'{t:%H}')}"


def pcp_url(product: str, t: dt.datetime) -> str:
    """The precipitation file whose hour HH covers HH-1 to HH UTC, so the
    accumulation for the hour ending at t carries t's own stamp."""
    p = PRODUCTS[product]
    t = t.astimezone(dt.timezone.utc)
    return f"{_bucket(product)}/{p['dir']}.{t:%Y%m%d}/{p['pcp'].format(ymdh=f'{t:%Y%m%d%H}')}"


def precip_due(product: str, t: dt.datetime) -> dt.datetime:
    """When the lane stops refetching an hour's precipitation file and keeps
    whatever coverage it has."""
    return t + dt.timedelta(minutes=PRODUCTS[product]["lagMinutes"], hours=PRECIP_WAIT_HOURS)


def check_message(msg: bytes, var: str, grid: dict, t: "Optional[dt.datetime]" = None) -> Optional[str]:
    """Why a message is not the one wanted, or None. Section 3 must be the
    grid the cells were computed on, since a cell index on any other grid is
    a different place; the parameter must be the variable the sidecar named;
    and when the hour is given the message must be valid then (the analysis
    reference time, or the end of the precipitation window), so a misfiled
    object is refused rather than read as another hour."""
    try:
        g = grib2.grid_def(msg)
    except Exception as e:  # noqa: BLE001
        return f"grid definition unreadable ({type(e).__name__})"
    if not grib2.same_grid(g, grid):
        return "message is on another grid"
    try:
        pd = grib2.product_def(msg)
    except Exception as e:  # noqa: BLE001
        return f"product definition unreadable ({type(e).__name__})"
    want = PARAM[var]
    got = (pd.get("discipline"), pd.get("category"), pd.get("number"))
    if got != want:
        return f"parameter {got} is not {var} {want}"
    if var == "precip" and pd.get("acc_hours") not in (None, 1):
        return f"accumulation is {pd.get('acc_hours')} h, not 1"
    if t is not None:
        got = None
        if var == "precip":
            got = pd.get("end")
        else:
            rt = getattr(grib2, "ref_time", None)
            if rt is not None:
                try:
                    got = rt(msg)
                except Exception as e:  # noqa: BLE001
                    return f"reference time unreadable ({type(e).__name__})"
        if got is not None and tuple(got[:4]) != (t.year, t.month, t.day, t.hour):
            return f"message is valid {tuple(got[:4])}, not {(t.year, t.month, t.day, t.hour)}"
    return None


def _grid(name: str) -> dict:
    return grib2.WEXP if name == "wexp" else grib2.G184


def _sample(msg: bytes, var: str, grid_name: str, ks: list, lattice: Optional[dict]) -> Tuple[list, Optional[list]]:
    """The values at the location cells ks and, when a lattice is given, the
    map frame. A simple-packed message without a bitmap (every analysis
    field) is read cell by cell: the 64,000 lattice cells and the fifty
    places, never the 3.7 million floats of the field, which is what keeps
    an hour with frames inside the Lambda's memory. Anything else (the
    complex-packed precipitation) is decoded whole and sampled."""
    if lattice is None:
        return grib2.decode_cells(msg, ks), None
    lat = lattice[grid_name]
    if grib2.random_access(msg):
        want = [k for k in lat if k >= 0]
        vals = grib2.decode_cells(msg, want + ks)
        it = iter(vals)
        sampled = [next(it) if k >= 0 else None for k in lat]
        return vals[len(want):], _frame_ints(sampled, SCALE[var], CONVERT[var])
    field = grib2.decode(msg)
    frame = frame_from_field(field, lat, SCALE[var], CONVERT[var])
    cells = [field[k] for k in ks]
    del field
    return cells, frame


class HourResult:
    """What reading one product-hour came to: `status` is ok, absent (the
    analysis file is not on the bucket) or error; `doc` is the write-once
    archive hour, `precip` the precipitation document (None when its file
    was absent or refused, with `precip_error` saying why), and `frames` the
    lattice samples when they were asked for."""
    def __init__(self, status: str, doc: Optional[dict] = None, frames: Optional[dict] = None,
                 error: Optional[str] = None, precip: Optional[dict] = None, precip_error: Optional[str] = None):
        self.status, self.doc, self.frames, self.error = status, doc, frames, error
        self.precip, self.precip_error = precip, precip_error


def read_precip(product: str, t: dt.datetime, locs: list, lattice: Optional[dict], now: dt.datetime) -> HourResult:
    """Fetch and decode one product-hour's precipitation file: `ok` with the
    precipitation document (and the frame when a lattice is given), `absent`
    when the file is not on the bucket, `error` with the reason otherwise.
    Nothing raises out of here: a short body, a refused packing or a 5xx
    after the retries is an error the caller records."""
    p = PRODUCTS[product]
    grid_name = p["pcpGrid"]
    ks = [loc["cell"][grid_name][2] for loc in locs]
    try:
        raw = gw.fetch_bytes(pcp_url(product, t), tries=FETCH_TRIES, timeout=FETCH_TIMEOUT)
        if raw is None:
            return HourResult("absent")
        why = check_message(raw, "precip", _grid(grid_name), t)
        if why:
            return HourResult("error", error=why)
        cells, frame = _sample(raw, "precip", grid_name, ks, lattice)
    except Exception as e:  # noqa: BLE001
        return HourResult("error", error=f"{type(e).__name__}: {e}")
    values = {loc["id"]: (None if v is None else half_up(mm_to_inch(v), HOUR_UNIT["precip"]))
              for loc, v in zip(locs, cells)}
    doc = precip_doc(product, t, values, now, read=True)
    return HourResult("ok", doc=doc, frames={"precip": frame} if lattice is not None else None)


def precip_doc(product: str, t: dt.datetime, values: dict, now: dt.datetime, read: bool) -> dict:
    """The precipitation document of a product-hour: `read` when a file was
    decoded, `complete` when every place has a value, `missing` the places
    that do not (their cells were absent from the bitmap)."""
    missing = sorted(lid for lid, v in values.items() if v is None)
    return {"schema": SCHEMA, "product": product, "valid": _iso(t), "written": _iso(now),
            "read": read, "complete": not missing, "missing": missing, "values": values}


def read_hour(product: str, t: dt.datetime, locs: list, lattice: dict, frames: bool, now: dt.datetime) -> HourResult:
    """Fetch and decode one product-hour: the three analysis messages, then
    the precipitation file. With `frames` every field is sampled onto the
    lattice as well as at the fifty cells. Nothing raises out of here: any
    failure in a fetch or a decode is an `error` result the lane records
    against the product and moves past."""
    ks_wexp = [loc["cell"]["wexp"][2] for loc in locs]
    values: Dict[str, dict] = {loc["id"]: {} for loc in locs}
    out_frames: dict = {}
    try:
        url = anl_url(product, t)
        idx_raw = gw.fetch_bytes(url + ".idx", tries=FETCH_TRIES, timeout=FETCH_TIMEOUT)
        if idx_raw is None:
            return HourResult("absent")
        entries = grib2.parse_idx(idx_raw.decode("ascii", "replace"))
        for var, name, level in MESSAGES:
            hit = [e for e in entries if e.get("var") == name and e.get("level") == level]
            if not hit:
                return HourResult("error", error=f"no {name} message in the sidecar")
            e = hit[0]
            end = e.get("end")
            if end is None:
                # the last message of the file: the sidecar cannot give its
                # end, and fetch_range accepts the clamped 206 S3 answers
                end = e["start"] + MAX_MESSAGE_BYTES
            msg = gw.fetch_range(url, e["start"], end, tries=FETCH_TRIES, timeout=FETCH_TIMEOUT)
            if msg is None:
                return HourResult("error", error=f"{name} message missing")
            why = check_message(msg, var, _grid("wexp"), t)
            if why:
                return HourResult("error", error=f"{name}: {why}")
            cells, frame = _sample(msg, var, "wexp", ks_wexp, lattice if frames else None)
            del msg
            if frames:
                out_frames[var] = frame
            for loc, v in zip(locs, cells):
                values[loc["id"]][var] = None if v is None else half_up(CONVERT[var](v), HOUR_UNIT[var])
    except Exception as e:  # noqa: BLE001
        return HourResult("error", error=f"{type(e).__name__}: {e}")
    prec = read_precip(product, t, locs, lattice if frames else None, now)
    if frames:
        out_frames["precip"] = (prec.frames or {}).get("precip") if prec.status == "ok" else None
    doc = {"schema": SCHEMA, "product": product, "valid": _iso(t), "written": _iso(now), "values": values}
    return HourResult("ok", doc=doc, frames=out_frames if frames else None,
                      precip=prec.doc if prec.status == "ok" else None,
                      precip_error=prec.error if prec.status == "error" else None)


# ------------------------------------------------------------------ writing
def hour_key(product: str, t: dt.datetime) -> str:
    return HOUR_KEY.format(product=product, stamp=_stamp(t))


def precip_key(product: str, t: dt.datetime) -> str:
    return PRECIP_KEY.format(product=product, stamp=_stamp(t))


def _merged(doc: dict, pdoc: Optional[dict]) -> dict:
    """The archive hour with its precipitation folded in, the shape the day
    rebuild reads: values[place].precip from the precipitation key (None
    where no value has been read) and precipRead when every place has one.
    An hour archived before the precipitation key existed carries its own
    precip values and precipRead, and those are used when no key is stored."""
    out = dict(doc)
    pvals = (pdoc or {}).get("values") or {}
    vals = {}
    for lid, v in (doc.get("values") or {}).items():
        v = dict(v)
        v["precip"] = pvals.get(lid) if pdoc is not None else v.get("precip")
        vals[lid] = v
    out["values"] = vals
    out["precipRead"] = bool(pdoc.get("complete")) if pdoc is not None else bool(doc.get("precipRead", False))
    return out


def get_precip(store: Storage, product: str, t: dt.datetime, cache: dict) -> Optional[dict]:
    ck = ("precip", product, _iso(t))
    if ck not in cache:
        cache[ck] = _read_gz(store, precip_key(product, t))
    return cache[ck]


def get_hour(store: Storage, product: str, t: dt.datetime, cache: dict) -> Optional[dict]:
    """The archived product-hour merged with its precipitation, or None."""
    ck = (product, _iso(t))
    if ck in cache:
        return cache[ck]
    doc = _read_gz(store, hour_key(product, t))
    if doc is not None:
        doc = _merged(doc, get_precip(store, product, t, cache))
    cache[ck] = doc
    return doc


def merge_precip(old: Optional[dict], new: dict) -> dict:
    """The stored precipitation document brought up to date by a new read.
    A place's value, once stored, stands: the resolved daily total is the
    sum of the stored hourlies and has to stay reproducible from the hour
    table, so a rerun that changes a value is a revision beside the total
    (reread_precip), never a rewrite of the hour. Only places still without
    a value take the new read's."""
    if old is None:
        return new
    values = dict(old.get("values") or {})
    for lid, v in (new.get("values") or {}).items():
        if values.get(lid) is None and v is not None:
            values[lid] = v
    missing = sorted(lid for lid, v in values.items() if v is None)
    out = dict(old)
    out.update({"written": new.get("written", old.get("written")), "read": bool(old.get("read")) or bool(new.get("read")),
                "complete": not missing, "missing": missing, "values": values})
    return out


def write_precip(store: Storage, product: str, t: dt.datetime, pdoc: dict, cache: dict) -> bool:
    """Store a product-hour's precipitation document, merged over what is
    already stored (this key may be rewritten). Returns True when the stored
    document changed, which is when the days it touches need rebuilding."""
    old = get_precip(store, product, t, cache)
    merged = merge_precip(old, pdoc)
    changed = old is None or merged.get("values") != old.get("values") or merged.get("read") != old.get("read")
    if changed:
        store.put(precip_key(product, t), _gz(merged), "application/gzip")
    cache[("precip", product, _iso(t))] = merged
    hk = (product, _iso(t))
    if hk in cache and cache[hk] is not None:
        cache[hk] = _merged(cache[hk], merged)
    return changed


def write_hour(store: Storage, product: str, t: dt.datetime, res: HourResult, grid_index: dict,
               cache: dict, now: dt.datetime) -> None:
    """One archive hour (write once), its precipitation (merged), and, when
    they were made, its frames with the grid index rewritten behind them so
    a frame on the bucket is never one the page cannot find."""
    store.put_if_absent(hour_key(product, t), _gz(res.doc), "application/gzip")
    cache[(product, _iso(t))] = _merged(res.doc, get_precip(store, product, t, cache))
    if res.precip is not None:
        write_precip(store, product, t, res.precip, cache)
    if res.frames:
        write_frames(store, product, t, res.frames, grid_index, now)
        write_grid_index(store, grid_index, now)


def write_frames(store: Storage, product: str, t: dt.datetime, frames: dict, grid_index: dict, now: dt.datetime) -> None:
    stamp = _stamp(t)
    day, hh = f"{t:%Y-%m-%d}", f"{t:%H}"
    for var in HOURLY_VARS:
        vals = frames.get(var)
        if vals is None:
            continue
        doc = {"schema": SCHEMA, "product": product, "var": var, "valid": _iso(t), "asof": _iso(t),
               "written": _iso(now), "unit": UNITS[var], "scale": SCALE[var],
               "cols": LATTICE_COLS, "rows": LATTICE_ROWS, "pitch": LATTICE_PITCH, "values": vals}
        store.put(FRAME_KEY.format(product=product, var=var, stamp=stamp), _dump(doc), "application/json", CACHE_FINAL)
        hours = grid_index.setdefault(product, {}).setdefault(var, {}).setdefault(day, [])
        if hh not in hours:
            hours.append(hh)
            hours.sort()


def frames_indexed(grid_index: dict, product: str, t: dt.datetime) -> bool:
    """Whether the hour's analysis frames (temperature, wind, gust) are in
    the grid index; the precipitation frame follows its file and may lag."""
    day, hh = f"{t:%Y-%m-%d}", f"{t:%H}"
    return all(hh in grid_index.get(product, {}).get(var, {}).get(day, []) for var in ANALYSIS_VARS)


def touched_by(t: dt.datetime, locs: list) -> set:
    """The (location, local day) pairs one UTC hour belongs to."""
    days_by_tz = {}
    out = set()
    for loc in locs:
        tz = loc["tz"]
        if tz not in days_by_tz:
            days_by_tz[tz] = local_day_of(t, tz)
        out.add((loc["id"], days_by_tz[tz]))
    return out


def build_day(store: Storage, day_iso: str, locs: list, cache: dict, now: dt.datetime, state: dict,
              only: Optional[set] = None) -> Tuple[dict, dict]:
    """The days/ document for one local date and the loc/ documents of the
    places in `only` (all of them when None), from the archive hours."""
    prior = _read_json(store, DAY_KEY.format(day=day_iso)) or {}
    prior_locs = prior.get("locations") or {}
    revisions = (state.get("revisions") or {}).get(day_iso)
    day_doc: dict = {"schema": SCHEMA, "day": day_iso, "asof": None, "written": _iso(now), "locations": {}}
    loc_docs: dict = {}
    asof = None
    for loc in locs:
        lid, tz = loc["id"], loc["tz"]
        hours = local_day_hours(day_iso, tz)
        entry: dict = {}
        rows = [{"local": hour_label(h, tz), "t": _iso(h)} for h in hours]
        for product in PRODUCTS:
            product_hours = {}
            for h, row in zip(hours, rows):
                doc = get_hour(store, product, h, cache)
                vals = (doc or {}).get("values", {}).get(lid) if doc else None
                row[product] = None
                if vals is not None:
                    product_hours[_iso(h)] = vals
                    # the hourly values as stored: tenths, and precipitation to
                    # the ten-thousandth of an inch the day's sum is made of, so
                    # the hour table adds up to the exact total (owner's
                    # decision 2026-09-27)
                    row[product] = {"temp": vals.get("temp"), "wind": vals.get("wind"), "gust": vals.get("gust"),
                                    "precip": vals.get("precip")}
                    if asof is None or h > asof:
                        asof = h
            was = (prior_locs.get(lid) or {}).get(product)
            # the re-read record in the state wins (None there is a withdrawn
            # one); a day the state no longer carries keeps what it published
            if revisions is not None and lid in revisions:
                revised = revisions[lid]
            else:
                revised = ((was or {}).get("precip") or {}).get("revised")
            s = day_summary(product_hours, tz, day_iso, now, product=product, prior=was, revised=revised)
            if s is not None:
                entry[product] = s
        if not entry:
            continue
        day_doc["locations"][lid] = entry
        if only is None or lid in only:
            loc_asof = max((r["t"] for r in rows if r.get("rtma") or r.get("urma")), default=None)
            loc_docs[lid] = {"schema": SCHEMA, "id": lid, "day": day_iso, "tz": tz, "asof": loc_asof,
                             "written": _iso(now), "hours": rows, "summary": entry}
    day_doc["asof"] = _iso(asof) if asof else None
    return day_doc, loc_docs


def rebuild_days(store: Storage, touched: set, locs: list, cache: dict, now: dt.datetime, state: dict) -> int:
    """Rewrite every days/ file a touched (location, day) pair names and the
    loc/ files of the touched pairs. Returns the number of files written."""
    by_day: Dict[str, set] = {}
    for lid, day in touched:
        by_day.setdefault(day, set()).add(lid)
    written = 0
    for day_iso in sorted(by_day):
        day_doc, loc_docs = build_day(store, day_iso, locs, cache, now, state, only=by_day[day_iso])
        if not day_doc["locations"]:
            continue
        store.put(DAY_KEY.format(day=day_iso), _dump(day_doc), "application/json", CACHE_LIVE)
        written += 1
        for lid, doc in loc_docs.items():
            u = (doc["summary"].get("urma") or {})
            final = bool(u.get("final")) and bool((u.get("precip") or {}).get("resolved"))
            store.put(LOC_KEY.format(loc=lid, day=day_iso), _dump(doc), "application/json",
                      CACHE_FINAL if final else CACHE_LIVE)
            written += 1
    return written


def days_to_close(store: Storage, locs: list, now: dt.datetime) -> set:
    """(location, day) pairs of recent days that are still open on the page
    but have passed their close time, so a day nobody adds an hour to still
    gets marked closed."""
    out = set()
    zones = sorted({loc["tz"] for loc in locs})
    days = set()
    for tz in zones:
        for back in range(1, int(CLOSE_AFTER_HOURS // 24) + 3):
            days.add(local_day_of(now - dt.timedelta(days=back), tz))
    for day_iso in sorted(days):
        doc = _read_json(store, DAY_KEY.format(day=day_iso))
        if not doc:
            continue
        for lid, entry in (doc.get("locations") or {}).items():
            loc = next((l for l in locs if l["id"] == lid), None)
            if loc is None:
                continue
            for product, s in entry.items():
                if s.get("complete") or s.get("closed"):
                    continue
                if now >= day_end(day_iso, loc["tz"]) + dt.timedelta(hours=CLOSE_AFTER_HOURS):
                    out.add((lid, day_iso))
    return out


# ------------------------------------------------------------------ state
def _product_state() -> dict:
    # newest: the newest hour READ (what asof and the alarm go by);
    # scanned: how far the live walk has looked, absent hours included;
    # since: when the lane first looked, so a product that has never landed
    # can still become overdue; gaps: hours the walk passed without reading;
    # precipPending: archived hours whose precipitation is still being refetched
    return {"newest": None, "scanned": None, "since": None, "gaps": [], "precipPending": [], "reads": 0}


def new_state() -> dict:
    return {"schema": SCHEMA, "products": {p: _product_state() for p in PRODUCTS},
            "backfill": {p: {"cursor": None, "oldest": None, "done": False, "queue": []} for p in PRODUCTS},
            "rereads": {}, "revisions": {}, "pruned": None}


def load_state(store: Storage) -> dict:
    s = _read_json(store, STATE_KEY) or {}
    base = new_state()
    for k, v in base.items():
        if k not in s:
            s[k] = v
    for p in PRODUCTS:
        ps = s["products"].setdefault(p, {})
        for k, v in _product_state().items():
            ps.setdefault(k, v)
        s["backfill"].setdefault(p, base["backfill"][p])
    return s


def save_state(store: Storage, state: dict, now: dt.datetime) -> None:
    state["updated"] = _iso(now)
    store.put(STATE_KEY, json.dumps(state, indent=1).encode(), "application/json")


def prune_state(state: dict, now: dt.datetime) -> None:
    """Drop re-read stamps and revision records of days past the re-read
    window; a revision lives on in the day file it was published in (the
    rebuild keeps a prior's revision when the state has no record)."""
    cutoff = (now - dt.timedelta(days=PRECIP_REREAD_DAYS + 1)).date().isoformat()
    for k in ("rereads", "revisions"):
        d = state.get(k) or {}
        for day in list(d):
            if day < cutoff:
                del d[day]


# ------------------------------------------------------------------ the lanes of a pass
def expected_latest(product: str, now: dt.datetime) -> dt.datetime:
    """The newest hour whose file should have landed by now."""
    return _floor_hour(now - dt.timedelta(minutes=PRODUCTS[product]["lagMinutes"]))


def _scan_cursor(ps: dict) -> Optional[dt.datetime]:
    """How far the live walk has looked: the later of the newest hour read
    and the newest hour scanned."""
    stamps = [ps.get("newest"), ps.get("scanned")]
    ts = [_parse_iso(s) for s in stamps if s]
    return max(ts) if ts else None


def _advance(ps: dict, key: str, t: dt.datetime) -> None:
    if not ps.get(key) or t > _parse_iso(ps[key]):
        ps[key] = _iso(t)


def live_candidates(product: str, state: dict, now: dt.datetime) -> List[dt.datetime]:
    """The hours the live lane tries this pass, oldest first: the gaps still
    worth retrying, then the hours after the walk's cursor up to what should
    have landed. A first pass starts at the present; the backfill walks back."""
    ps = state["products"][product]
    latest = expected_latest(product, now)
    out: List[dt.datetime] = []
    keep_gaps = []
    for g in ps.get("gaps") or []:
        t = _parse_iso(g)
        if now - t <= dt.timedelta(hours=CLOSE_AFTER_HOURS):
            keep_gaps.append(g)
            out.append(t)
    ps["gaps"] = keep_gaps
    cursor = _scan_cursor(ps)
    if cursor is None:
        start = latest
    elif latest - cursor > dt.timedelta(hours=LIVE_CATCHUP_HOURS):
        # too far behind to walk: jump to the present, queue the span for the
        # backfill (once, and no older than the backfill reaches), and move
        # the walk to the jump so the next pass carries on from there
        start = latest - dt.timedelta(hours=LIVE_HOURS_PER_PASS - 1)
        a = max(cursor + dt.timedelta(hours=1), _floor_hour(now) - dt.timedelta(days=BACKFILL_POINT_DAYS))
        b = start - dt.timedelta(hours=1)
        span = [_iso(a), _iso(b)]
        queue = state["backfill"][product].setdefault("queue", [])
        if b >= a and span not in queue:
            queue.append(span)
        ps["scanned"] = _iso(b)
    else:
        start = cursor + dt.timedelta(hours=1)
    t = start
    while t <= latest:
        if t not in out:
            out.append(t)
        t += dt.timedelta(hours=1)
    return out


def retry_precip(store: Storage, product: str, state: dict, locs: list, lattice: dict, cache: dict,
                 grid_index: dict, now: dt.datetime, deadline: arch.Deadline, status: dict) -> set:
    """Refetch the precipitation of the archived hours still pending: an
    hour whose file was absent, refused, or short of a place's cell at the
    last read. A read that adds values rewrites the precipitation key and
    (within the frame window) the precipitation frame; an hour past its
    wait is dropped from the list with whatever coverage it has. Returns
    the (location, day) pairs whose values changed."""
    ps = state["products"][product]
    touched: set = set()
    keep: List[str] = []
    frame_cutoff = _floor_hour(now) - dt.timedelta(days=BACKFILL_FRAME_DAYS)
    for iso in sorted(set(ps.get("precipPending") or [])):
        t = _parse_iso(iso)
        if now >= precip_due(product, t):
            continue
        if deadline.over(RESERVE_SECONDS):
            keep.append(iso)
            continue
        try:
            prec = read_precip(product, t, locs, lattice if t >= frame_cutoff else None, now)
            if prec.status == "ok":
                if write_precip(store, product, t, prec.doc, cache):
                    touched |= touched_by(t, locs)
                    if prec.frames and prec.frames.get("precip") is not None:
                        write_frames(store, product, t, prec.frames, grid_index, now)
                        write_grid_index(store, grid_index, now)
                stored = get_precip(store, product, t, cache) or {}
                if stored.get("complete"):
                    continue
            elif prec.status == "error":
                _fail(status, f"{product} {iso} precipitation: {prec.error}")
        except Exception as e:  # noqa: BLE001
            _fail(status, f"{product} {iso} precipitation: {type(e).__name__}: {e}")
        keep.append(iso)
        status["waiting"].append(f"{product} {iso}")
    ps["precipPending"] = keep
    return touched


def _note_precip(ps: dict, product: str, t: dt.datetime, res: HourResult, now: dt.datetime, status: dict) -> None:
    """After an hour is archived: record a refused precipitation file, and
    queue the hour for refetching when its precipitation is short and the
    wait has not run out."""
    iso = _iso(t)
    if res.precip_error:
        _fail(status, f"{product} {iso} precipitation: {res.precip_error}")
    if (res.precip is None or not res.precip.get("complete")) and now < precip_due(product, t):
        if iso not in ps["precipPending"]:
            ps["precipPending"].append(iso)
        status["waiting"].append(f"{product} {iso}")


def live_lane(store: Storage, product: str, state: dict, locs: list, lattice: dict, cache: dict,
              grid_index: dict, now: dt.datetime, deadline: arch.Deadline, status: dict) -> set:
    """Up to LIVE_HOURS_PER_PASS new hours of one product, after the pending
    precipitation retries. Returns the (location, day) pairs the hours
    touched. An hour that fails to read is recorded against the product and
    left as a gap for the next pass; the walk never stops on it."""
    ps = state["products"][product]
    if not ps.get("since"):
        ps["since"] = _iso(now)
    touched: set = retry_precip(store, product, state, locs, lattice, cache, grid_index, now, deadline, status)
    reads = 0
    frame_cutoff = _floor_hour(now) - dt.timedelta(days=BACKFILL_FRAME_DAYS)
    for t in live_candidates(product, state, now):
        if reads >= LIVE_HOURS_PER_PASS or deadline.over(RESERVE_SECONDS):
            break
        iso = _iso(t)
        is_gap = iso in (ps.get("gaps") or [])
        try:
            # a crash between the archive write and the state write leaves an
            # hour archived but unrecorded; it counts as read, not fetched
            # again, unless its frames never reached the grid index
            have = get_hour(store, product, t, cache)
            if have is not None and (frames_indexed(grid_index, product, t) or t < frame_cutoff):
                res = HourResult("ok", doc=have)
            else:
                res = read_hour(product, t, locs, lattice, frames=True, now=now)
                # an archived hour whose frames could not be made is left as
                # the gap the next passes retry; the archive already holds its
                # values, and a read that keeps failing is a failure to report
                if res.status == "ok":
                    write_hour(store, product, t, res, grid_index, cache, now)
                    _note_precip(ps, product, t, res, now, status)
        except Exception as e:  # noqa: BLE001
            _fail(status, f"{product} {iso}: {type(e).__name__}: {e}")
            break
        if res.status == "absent":
            status["absent"].append(f"{product} {iso}")
            if not is_gap:
                # an hour the bucket has not got: remembered and retried, and
                # the walk moves on so one missing file cannot stall the lane
                ps["gaps"].append(iso)
                _advance(ps, "scanned", t)
                _seed_backfill(state, product, iso)
            continue
        if res.status == "error":
            _fail(status, f"{product} {iso}: {res.error}")
            if is_gap:
                continue
            # an unreadable object is skipped like a missing one: a gap the
            # next passes retry, and the walk moves past it; one such error
            # per pass bounds what an outage costs the pass
            ps["gaps"].append(iso)
            _advance(ps, "scanned", t)
            _seed_backfill(state, product, iso)
            break
        reads += 1
        status["read"].append(f"{product} {iso}")
        touched |= touched_by(t, locs)
        if is_gap:
            ps["gaps"].remove(iso)
        _advance(ps, "newest", t)
        _advance(ps, "scanned", t)
        ps["reads"] = ps.get("reads", 0) + 1
        _seed_backfill(state, product, iso)
    return touched


def _seed_backfill(state: dict, product: str, iso: str) -> None:
    """The backfill starts just behind the first hour the live walk looked
    at, whether or not that hour was there. On 2026-09-27 both products'
    newest hours were late by half an hour when a fresh lane first looked,
    and a cursor seeded only by a read left the backfill idle until one
    landed; the absent hour itself stays a gap of the live lane."""
    bf = state["backfill"][product]
    if bf.get("cursor") is None:
        bf["cursor"] = iso


def reread_due(store: Storage, state: dict, locs: list, now: dt.datetime) -> Optional[str]:
    """The one local day whose URMA precipitation is re-read this pass, or
    None: the oldest of the last PRECIP_REREAD_DAYS days with a complete
    URMA day at any place (resolved, or complete and still short of a
    precipitation value somewhere) that has not been re-read in the last
    day."""
    zones = sorted({loc["tz"] for loc in locs})
    days = set()
    for tz in zones:
        for back in range(1, PRECIP_REREAD_DAYS + 1):
            days.add(local_day_of(now - dt.timedelta(days=back), tz))
    stamps = state.get("rereads") or {}
    for day_iso in sorted(days):
        last = stamps.get(day_iso)
        if last and now - _parse_iso(last) < dt.timedelta(hours=24):
            continue
        doc = _read_json(store, DAY_KEY.format(day=day_iso))
        if not doc:
            continue
        if any((e.get("urma") or {}).get("complete") for e in (doc.get("locations") or {}).values()):
            return day_iso
    return None


def reread_precip(store: Storage, day_iso: str, state: dict, locs: list, now: dt.datetime,
                  deadline: arch.Deadline, status: dict, cache: Optional[dict] = None) -> set:
    """Re-read the URMA precipitation of one local day at every place. Two
    things come of it: places still without a stored value for an hour take
    the re-read's (the precipitation key is rewritten, so a day short of a
    value can still resolve), and for a resolved place a re-read total that
    differs from the resolved one, by a hundredth of an inch or in the
    rounded hundredth, is recorded as a revision beside it (a re-read back
    within that is a withdrawal). Returns the (location, day) pairs whose
    values or revision changed, for the day rebuild."""
    cache = {} if cache is None else cache
    doc = _read_json(store, DAY_KEY.format(day=day_iso)) or {}
    entries = doc.get("locations") or {}
    hours_by_loc = {loc["id"]: local_day_hours(day_iso, loc["tz"]) for loc in locs}
    wanted = sorted({h for hs in hours_by_loc.values() for h in hs})
    totals: Dict[str, dict] = {}   # hour iso -> {loc: inches}
    changed: set = set()
    for h in wanted:
        if deadline.over(RESERVE_SECONDS + 30):
            # not stamped, so the day is due again next pass
            status["errors"].append(f"reread {day_iso}: deadline")
            status["reread"] = {"day": day_iso, "hours": len(totals), "revisions": 0}
            return changed
        prec = read_precip("urma", h, locs, None, now)
        if prec.status == "absent":
            continue
        if prec.status == "error":
            status["errors"].append(f"reread {day_iso} {_iso(h)}: {prec.error}")
            continue
        totals[_iso(h)] = prec.doc["values"]
        try:
            if write_precip(store, "urma", h, prec.doc, cache):
                changed |= touched_by(h, locs)
        except Exception as e:  # noqa: BLE001
            status["errors"].append(f"reread {day_iso} {_iso(h)}: {type(e).__name__}: {e}")
    state.setdefault("rereads", {})[day_iso] = _iso(now)
    revs = state.setdefault("revisions", {}).setdefault(day_iso, {})
    n_rev = 0
    for loc in locs:
        lid = loc["id"]
        resolved = ((entries.get(lid) or {}).get("urma") or {}).get("precip") or {}
        if not resolved.get("resolved"):
            continue
        vals = [totals.get(_iso(h), {}).get(lid) for h in hours_by_loc[lid]]
        if any(v is None for v in vals):
            continue   # a re-read short of the day's hours says nothing about the total
        total = half_up(sum(vals), HOUR_UNIT["precip"])
        value = half_up(total, DAY_UNIT["precip"])
        # owner's decision 2026-09-27: a revision when the exact totals differ
        # by a hundredth of an inch or more OR the rounded hundredths differ
        differs = abs(total - float(resolved["exact"])) >= 0.01 - 1e-9 or value != resolved.get("value")
        before = revs.get(lid)
        if differs:
            rec = {"value": value, "exact": total, "at": _iso(now)}
            if before is None or before.get("exact") != rec["exact"]:
                revs[lid] = rec
                changed.add((lid, day_iso))
                n_rev += 1
        elif before is not None:
            # the rerun came back to the resolved total: the revision is
            # withdrawn, and None stays as the record of that so a rebuild
            # does not resurrect it from the day file
            revs[lid] = None
            changed.add((lid, day_iso))
            n_rev += 1
    if not revs:
        state["revisions"].pop(day_iso, None)
    status["reread"] = {"day": day_iso, "hours": len(totals), "revisions": n_rev}
    return changed


def backfill_next(product: str, state: dict, now: dt.datetime) -> Optional[dt.datetime]:
    """The next hour the backfill reads for a product, or None when it is
    done: a queued span first (newest hour first), then the walk back from
    the cursor to BACKFILL_POINT_DAYS ago. Nothing older than that is read
    whatever a span says."""
    bf = state["backfill"][product]
    oldest = _floor_hour(now) - dt.timedelta(days=BACKFILL_POINT_DAYS)
    queue = bf.get("queue") or []
    for span in list(queue):
        a, b = _parse_iso(span[0]), _parse_iso(span[1])
        if b < oldest or b < a:
            queue.remove(span)
            continue
        if a < oldest:
            span[0] = _iso(oldest)
        return b
    bf["queue"] = queue
    if bf.get("cursor") is None:
        return None
    t = _parse_iso(bf["cursor"]) - dt.timedelta(hours=1)
    if t < oldest:
        return None
    return t


def backfill_advance(product: str, state: dict, t: dt.datetime) -> None:
    """Move the cursor past t, only ever after t is fully written or known absent."""
    bf = state["backfill"][product]
    iso = _iso(t)
    queue = bf.get("queue") or []
    for span in list(queue):
        if span[1] == iso:
            nb = t - dt.timedelta(hours=1)
            if nb < _parse_iso(span[0]):
                queue.remove(span)
            else:
                span[1] = _iso(nb)
            bf["queue"] = queue
            return
    bf["cursor"] = iso
    bf["oldest"] = iso


def backfill_lane(store: Storage, state: dict, locs: list, lattice: dict, cache: dict, grid_index: dict,
                  now: dt.datetime, deadline: arch.Deadline, status: dict) -> set:
    """With the remaining budget, walk both products backwards, newest
    first, alternating so the two fill in together. Hours within
    BACKFILL_FRAME_DAYS get frames; older ones are point reads only. An
    hour that fails to read is skipped, like a missing one."""
    touched: set = set()
    frame_cutoff = _floor_hour(now) - dt.timedelta(days=BACKFILL_FRAME_DAYS)
    since_save = 0
    while not deadline.over(RESERVE_SECONDS):
        progressed = False
        for product in PRODUCTS:
            if deadline.over(RESERVE_SECONDS):
                break
            t = backfill_next(product, state, now)
            if t is None:
                state["backfill"][product]["done"] = True
                continue
            progressed = True
            iso = _iso(t)
            try:
                want_frames = t >= frame_cutoff
                have = get_hour(store, product, t, cache)
                if have is not None and (not want_frames or frames_indexed(grid_index, product, t)):
                    res = HourResult("ok", doc=have)
                else:
                    res = read_hour(product, t, locs, lattice, frames=want_frames, now=now)
                    if have is not None and res.status != "ok":
                        # the archive holds the hour and only its frames are
                        # missing; the backfill does not retry, so it says so
                        # rather than counting the hour as read in silence
                        _fail(status, f"{product} {iso}: frames not made ({res.error or res.status})")
                        res = HourResult("ok", doc=have)
                    elif res.status == "ok":
                        write_hour(store, product, t, res, grid_index, cache, now)
                        _note_precip(state["products"][product], product, t, res, now, status)
            except Exception as e:  # noqa: BLE001
                _fail(status, f"{product} {iso}: {type(e).__name__}: {e}")
                backfill_advance(product, state, t)
                continue
            if res.status == "absent":
                status["absent"].append(f"{product} {iso}")
                backfill_advance(product, state, t)
                continue
            if res.status == "error":
                _fail(status, f"{product} {iso}: {res.error}")
                backfill_advance(product, state, t)
                continue
            status["backfilled"] += 1
            status["backfilledBy"][product] += 1
            touched |= touched_by(t, locs)
            backfill_advance(product, state, t)
            since_save += 1
            if since_save >= STATE_EVERY:
                save_state(store, state, now)
                since_save = 0
        if not progressed:
            break
    return touched


def prune_frames(store: Storage, grid_index: dict, now: dt.datetime) -> int:
    """Remove frames older than FRAME_KEEP_DAYS and their index entries."""
    cutoff = f"{now - dt.timedelta(days=FRAME_KEEP_DAYS):%Y%m%dT%HZ}"
    removed = 0
    for product in PRODUCTS:
        for var in HOURLY_VARS:
            prefix = PREFIX + f"grid/{product}/{var}/"
            for key in store.list(prefix):
                stamp = key[len(prefix):-len(".json")]
                if len(stamp) == len(cutoff) and stamp < cutoff:
                    store.delete(key)
                    removed += 1
            days = grid_index.get(product, {}).get(var, {})
            for day in list(days):
                if day.replace("-", "") < cutoff[:8]:
                    del days[day]
    return removed


def write_index(store: Storage, state: dict, locs: list, grid_index: dict, now: dt.datetime) -> None:
    days = sorted({k[len(PREFIX + "days/"):-len(".json")] for k in store.list(PREFIX + "days/") if k.endswith(".json")})
    days = days[-INDEX_DAYS:]
    newest = [_parse_iso(state["products"][p]["newest"]) for p in PRODUCTS if state["products"][p].get("newest")]
    asof = _iso(max(newest)) if newest else None
    sources = {p: {"bucket": PRODUCTS[p]["bucket"], "lagMinutes": PRODUCTS[p]["lagMinutes"],
                   "latest": state["products"][p].get("newest")} for p in PRODUCTS}
    bf = state["backfill"]
    index = {"schema": SCHEMA, "asof": asof, "written": _iso(now), "statement": STATEMENT,
             "conventions": CONVENTIONS, "sources": sources, "locations": locs, "days": days,
             "lattice": {"pitch": LATTICE_PITCH, "cols": LATTICE_COLS, "rows": LATTICE_ROWS, "viewBox": "0 0 960 600"},
             "variables": VARIABLES,
             "backfill": {"pointsDays": BACKFILL_POINT_DAYS, "frameDays": BACKFILL_FRAME_DAYS,
                          "done": all(bf[p].get("done") for p in PRODUCTS),
                          "cursor": {p: bf[p].get("cursor") for p in PRODUCTS}}}
    store.put(INDEX_KEY, _dump(index), "application/json", CACHE_LIVE)


def _grid_asof(grid_index: dict) -> Optional[str]:
    """The newest hour that has a frame, from the index itself."""
    newest = None
    for vars_ in grid_index.values():
        for days in vars_.values():
            for day, hhs in days.items():
                for hh in hhs:
                    stamp = f"{day}T{hh}:00:00Z"
                    if newest is None or stamp > newest:
                        newest = stamp
    return newest


def write_grid_index(store: Storage, grid_index: dict, now: dt.datetime, asof: Optional[str] = None) -> None:
    doc = {"schema": SCHEMA, "asof": asof or _grid_asof(grid_index), "written": _iso(now), "frames": grid_index}
    store.put(GRID_INDEX_KEY, _dump(doc), "application/json", CACHE_LIVE)


def health_results(state: dict, status: dict, now: dt.datetime) -> dict:
    """Per product, what this pass says to the lane's failure streak. A read
    is a success. An empty pass is only a failure when a file failed to read
    or the newest hour READ is overdue, because between landings there is
    nothing to read and that is not a fault. A product nothing has ever
    been read for is overdue once the lane has looked for it that long."""
    out = {}
    for p in PRODUCTS:
        ps = state["products"][p]
        ok = any(s.startswith(p + " ") for s in status["read"]) or status["backfilledBy"].get(p, 0) > 0
        failed = any(s.startswith(p + " ") for s in status["errors"])
        # a failure outside the product lanes (the rebuild, a re-read, the
        # prune, a refused snapshot write) is the lane's failure as well
        failed = failed or any(not s.startswith(("rtma ", "urma ")) for s in status["errors"])
        newest, since = ps.get("newest"), ps.get("since")
        overdue = False
        if newest:
            overdue = expected_latest(p, now) - _parse_iso(newest) >= dt.timedelta(hours=OVERDUE_HOURS)
        elif since:
            overdue = expected_latest(p, now) - expected_latest(p, _parse_iso(since)) >= dt.timedelta(hours=OVERDUE_HOURS)
        out[p] = {"ok": ok, "attempted": bool(ok or failed or overdue),
                  "error": None if ok else ("read failed" if failed else
                                            ("no hour since %s" % (newest or since) if overdue else None))}
    return out


def _health(store: Storage, state: dict, status: dict, now: dt.datetime) -> list:
    """Advance the lane's failure streaks and name the alarms they raise."""
    try:
        health = arch.update_health(store, health_results(state, status, now), now, key=HEALTH_KEY)
        return ["analysis: no %s hour readable for %d passes" % (p, health[p]["fail_streak"]) for p in arch.alarms_in(health)]
    except Exception as e:  # noqa: BLE001
        status["errors"].append(f"health: {type(e).__name__}: {e}")
        return []


# ------------------------------------------------------------------ the pass
def analysis_pass(cfg: dict, store: Storage, now: Optional[dt.datetime] = None) -> int:
    """Entry point. Returns 1 only when every read this pass failed, so the
    scheduler flags an outage; a missing object is an absence, reported and
    never a failure. Each lane is guarded on its own so one product's
    trouble never costs the other product, the day rebuild, the state or
    the indexes, which are written in every case."""
    t0 = time.time()
    now = now or dt.datetime.now(dt.timezone.utc)
    gw.set_user_agent(cfg.get("user_agent", ""))
    budget = arch.remaining_budget(cfg)
    deadline = arch.Deadline(min(budget, PASS_CAP_SECONDS) if budget else PASS_CAP_SECONDS)
    status: dict = {"kind": "analysis", "read": [], "absent": [], "waiting": [], "errors": [], "failed": 0,
                    "backfilled": 0, "backfilledBy": {p: 0 for p in PRODUCTS}, "days": 0, "pruned": 0}
    alarms: list = []
    if grib2 is None:
        status["errors"].append("pipeline.grib2 is not available; nothing read")
        arch.LAST_STATUS = {"job": "analysis", "errors": 1, "alarms": ["analysis: grib2 decoder missing"]}
        print(json.dumps(status))
        return 1
    locs = load_locations()
    lattice = load_lattice()
    state = load_state(store)
    cache: dict = {}
    grid_index = (_read_json(store, GRID_INDEX_KEY) or {}).get("frames") or {}
    touched: set = set()
    try:
        # 1. live hours (each product on its own), then 2. the days they
        # touch (and any day due to close)
        for product in PRODUCTS:
            try:
                touched |= live_lane(store, product, state, locs, lattice, cache, grid_index, now, deadline, status)
            except Exception as e:  # noqa: BLE001
                _fail(status, f"{product} live lane: {type(e).__name__}: {e}")
        try:
            touched |= days_to_close(store, locs, now)
        except Exception as e:  # noqa: BLE001
            _fail(status, f"days to close: {type(e).__name__}: {e}")
        status["days"] += rebuild_days(store, touched, locs, cache, now, state)
        save_state(store, state, now)
        # 3. one day's URMA precipitation re-read, when one is due
        if not deadline.over(RESERVE_SECONDS + 120):
            try:
                day_iso = reread_due(store, state, locs, now)
                if day_iso:
                    changed = reread_precip(store, day_iso, state, locs, now, deadline, status, cache)
                    status["days"] += rebuild_days(store, changed, locs, cache, now, state)
                    save_state(store, state, now)
            except Exception as e:  # noqa: BLE001
                _fail(status, f"urma reread: {type(e).__name__}: {e}")
        # 4. backfill with what remains, then the days it touched
        try:
            bt = backfill_lane(store, state, locs, lattice, cache, grid_index, now, deadline, status)
            status["days"] += rebuild_days(store, bt, locs, cache, now, state)
        except Exception as e:  # noqa: BLE001
            _fail(status, f"backfill: {type(e).__name__}: {e}")
        # 5. prune once a day
        pruned_at = state.get("pruned")
        if not pruned_at or now - _parse_iso(pruned_at) >= dt.timedelta(hours=24):
            try:
                status["pruned"] = prune_frames(store, grid_index, now)
                prune_state(state, now)
                state["pruned"] = _iso(now)
            except Exception as e:  # noqa: BLE001
                _fail(status, f"prune: {type(e).__name__}: {e}")
    except Exception as e:  # noqa: BLE001 - the last resort; the lanes guard their own hours
        _fail(status, f"pass: {type(e).__name__}: {e}")
    finally:
        # the grid index, the state, the health file and the index are
        # written whatever happened above, the index last so a reader sees
        # a consistent set and every frame on the bucket is listed
        try:
            write_grid_index(store, grid_index, now)
            save_state(store, state, now)
            alarms = _health(store, state, status, now)
            write_index(store, state, locs, grid_index, now)
        except Exception as e:  # noqa: BLE001
            _fail(status, f"index: {type(e).__name__}: {e}")
    reads = len(status["read"]) + status["backfilled"]
    errors = 1 if (status["failed"] and reads == 0) else 0
    status["seconds"] = round(time.time() - t0, 1)
    # the lists are for the log; the counts are what the status carries
    for k in ("read", "absent", "waiting", "errors"):
        status[k + "Count"] = len(status[k])
        status[k] = status[k][:12]
    arch.LAST_STATUS = {"job": "analysis", "errors": errors, "alarms": alarms, "seconds": status["seconds"],
                        "read": len(status["read"]), "backfilled": status["backfilled"], "days": status["days"],
                        "absent": len(status["absent"]), "failed": status["failed"],
                        "newest": {p: state["products"][p].get("newest") for p in PRODUCTS}}
    print(json.dumps(status, ensure_ascii=False))
    return errors
