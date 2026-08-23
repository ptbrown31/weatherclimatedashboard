"""
headline.py — the handful of numbers at the top of the landing page.

Four figures, the same four that open the owner's daily newsletter:

    accuracy       how each source did on the daily high over the last week,
                   best first
    largestError   the single biggest miss on the most recent scored day
    widestSpread   the station whose sources disagree most about tomorrow
    hurricane      the price of at least one major Atlantic hurricane this month

Nothing here fetches anything. Every number is read back out of snapshots
this pipeline has already written, which is what lets the job run at the end
of any chain for almost no cost. Size is the reason the file exists at all:
the landing page wants four numbers, and the scorecard it would otherwise
have to read is about seventy kilobytes, nearly all of it per-station detail
the top of a page never shows.

Numbers only, no display strings and no formatting. The page decides how to
write a temperature or a price, so one file serves both build targets and a
wording change needs no pipeline run.

Every section stands alone and every input may be missing. A section whose
inputs are not there is left out of the file rather than written as nulls, so
a page can test for the key. A pass never raises: a truncated or malformed
input costs one figure, not the file.

Degrees are Fahrenheit throughout. The scorecard covers only the US stations,
and a Celsius station's error would not pool with the rest even if one
appeared, so both temperature sections filter on the unit rather than assume it.

Writes snapshots/headline.json.
"""
from __future__ import annotations
import datetime as dt
import json
import time
from typing import Optional

from . import archive as arch
from .snapshots import SNAP_CACHE, _iso
from .storage import Storage

SCHEMA = 1
SNAP_KEY = "snapshots/headline.json"
CARD_KEY = "snapshots/scorecard.json"
SUMMARY_KEY = "snapshots/summary.json"
MARKET_KEY = "snapshots/market/summary.json"
HURRICANE_KEY = "snapshots/market/hurricane.json"

ACCURACY_DAYS = 7               # a week of scored days, enough to rank without a stale day dominating
SOURCES = ("nws", "nbm", "lamp", "mav")     # the forecast products, in the order the pages list them
FX = "fx"                       # the exchange's implied median, carried on a scored day by the scorecard
RANKED = SOURCES + (FX,)
MAJOR_MONTHLY = "MHCMA"         # Atlantic major hurricanes, one contract per calendar month
SEASON_COUNT = "HCAB"           # Atlantic hurricane count for the whole season


# ------------------------------------------------------------------ reading
def _read(store: Storage, key: str) -> Optional[dict]:
    """One snapshot, or None. Every input is optional, so a missing, empty or
    unreadable object is an ordinary outcome rather than an error."""
    try:
        raw = store.get(key)
    except Exception:  # noqa: BLE001 - one unreadable input costs its section, not the pass
        return None
    if not raw:
        return None
    try:
        body = json.loads(raw)
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _obj(v) -> dict:
    return v if isinstance(v, dict) else {}


def _num(v) -> Optional[float]:
    # booleans are integers in Python and several snapshot fields next to the
    # numbers are flags, so they are rejected here rather than counted as 0 or 1
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _rows(v) -> list:
    return [r for r in v if isinstance(r, dict)] if isinstance(v, list) else []


def _stations(card: Optional[dict]) -> list:
    """[(station id, station block)] for the Fahrenheit stations, in station
    order, so an exact tie anywhere below resolves to the same station twice."""
    sts = _obj(_obj(card).get("stations"))
    return [(sid, _obj(sts[sid])) for sid in sorted(sts) if _obj(sts[sid]).get("unit") == "F"]


def _days(st: dict) -> list:
    return _rows(st.get("days"))


# ------------------------------------------------------------------ accuracy
def scored_days(card: Optional[dict]) -> list:
    """Every local day the scorecard has scored, newest first. Stations sit in
    different zones, so their newest scored day is not always the same one;
    pooling the distinct dates keeps the window the same width for all of them."""
    seen = set()
    for _sid, st in _stations(card):
        for d in _days(st):
            if isinstance(d.get("date"), str):
                seen.add(d["date"])
    return sorted(seen, reverse=True)


def accuracy(card: Optional[dict], days: list) -> Optional[dict]:
    """Mean absolute error on the daily high per source, pooled over the last
    ACCURACY_DAYS scored days and every Fahrenheit station, ranked best first.
    The exchange's implied median is ranked alongside the forecast products
    here: at the top of the page the question is which number was closest, not
    which of them is a forecast product."""
    window = set(days[:ACCURACY_DAYS])
    if not window:
        return None
    errs: dict = {}
    for _sid, st in _stations(card):
        for d in _days(st):
            if d.get("date") not in window:
                continue
            for src in RANKED:
                e = _num(_obj(d.get(src)).get("errHigh"))
                if e is not None:
                    errs.setdefault(src, []).append(abs(e))
    rank = [{"src": s, "mae": round(sum(v) / len(v), 2), "n": len(v)} for s, v in errs.items() if v]
    if not rank:
        return None
    rank.sort(key=lambda r: (r["mae"], r["src"]))       # the source name breaks an exact tie
    return {"days": len(window), "side": "high", "scoredDay": days[0], "rank": rank}


def largest_error(card: Optional[dict], day: Optional[str]) -> Optional[dict]:
    """The biggest miss on the daily high on one scored day, across every
    Fahrenheit station and every source. The error is signed, because whether a
    source ran warm or cold is the interesting half of a large miss."""
    if not day:
        return None
    best, key = None, None
    for sid, st in _stations(card):
        for d in _days(st):
            if d.get("date") != day:
                continue
            obs = _num(d.get("obsHigh"))
            for src in RANKED:
                q = _obj(d.get(src))
                e = _num(q.get("errHigh"))
                if e is None:
                    continue
                # equal magnitudes go to the warm miss; a still-exact tie keeps
                # the station already held, and the stations arrive in name
                # order, so the same pass over the same data always picks the same row
                k = (abs(e), e)
                if key is None or k > key:
                    key = k
                    best = {"station": sid, "city": st.get("city"), "date": day, "side": "high",
                            "observed": obs, "forecast": _num(q.get("high")), "source": src, "error": e}
    return best


# ------------------------------------------------------------- disagreement
def widest_spread(summary: Optional[dict], market: Optional[dict]) -> Optional[dict]:
    """The Fahrenheit station whose sources disagree most about tomorrow's
    high: the widest gap between any two of them. Two sources is the minimum
    for a spread to mean anything, and a station showing one number is skipped
    rather than reported as agreeing with itself."""
    implied = {r["station"]: r for r in _rows(_obj(market).get("cities")) if isinstance(r.get("station"), str)}
    best, top = None, None
    for c in sorted(_rows(_obj(summary).get("cities")), key=lambda r: str(r.get("station") or "")):
        if c.get("unit") != "F":
            continue
        day = _obj(c.get("markers")).get("tomorrow")
        if not day:
            continue
        vals, used = [], []
        for src in SOURCES:
            v = _num(c.get(src + "HighTomorrow"))
            if v is not None:
                vals.append(v)
                used.append(src)
        m = _obj(implied.get(c.get("station")))
        # the quote job stamps the local day its "tomorrow" figures belong to.
        # The two snapshots are written by different jobs on different
        # schedules, so around a station's local midnight they can name
        # different days; the market number only joins this comparison when it
        # is for the day the rest of the row is about.
        if m.get("tomorrow") == day:
            v = _num(m.get("impliedHighTomorrow"))
            if v is not None:
                vals.append(v)
                used.append(FX)
        if len(vals) < 2:
            continue
        lo, hi = min(vals), max(vals)
        spread = round(hi - lo, 1)
        if top is None or spread > top:     # not >=, so an exact tie keeps the first station by name
            top = spread
            best = {"station": c.get("station"), "city": c.get("city"), "day": day, "side": "high",
                    "spread": spread, "low": lo, "high": hi, "sources": used}
    return best


# ---------------------------------------------------------------- hurricane
def _this_month(spec, now: dt.datetime) -> bool:
    """A monthly contract's spec is the year and month it settles in, written
    without a leading zero: '2026.8' is August 2026."""
    try:
        year, month = str(spec).split(".")
        return (int(year), int(month)) == (now.year, now.month)
    except (TypeError, ValueError):
        return False


def _contract(symbol: str, c: dict) -> dict:
    # `yes` is the exchange's own midpoint, in dollars per contract and not fee
    # adjusted, which is what the rest of the market layer calls the implied
    # probability. Either side of the book may be missing on a thin contract.
    return {"symbol": symbol, "label": c.get("label"), "expiryLabel": c.get("expiryLabel"),
            "yes": _num(c.get("mid")), "bid": _num(c.get("bid")), "ask": _num(c.get("ask"))}


def hurricane(group: Optional[dict], now: dt.datetime) -> Optional[dict]:
    """At least one major Atlantic hurricane before this month is out: the
    current month's major-hurricane contract at a strike of zero, whose Yes
    price is the probability of the count exceeding none.

    Months at the shoulders of the season are sometimes not listed at all. The
    season count market stands in then, at its lowest strike, which is the
    nearest thing the exchange lists to the same question. A contract with no
    price is no use at the top of a page, so both lanes require a midpoint."""
    markets = {m["symbol"]: _rows(m.get("contracts")) for m in _rows(_obj(group).get("markets"))
               if isinstance(m.get("symbol"), str)}
    for c in markets.get(MAJOR_MONTHLY, []):
        if _this_month(c.get("spec"), now) and _num(c.get("strike")) == 0 and _num(c.get("mid")) is not None:
            return _contract(MAJOR_MONTHLY, c)
    quoted = [c for c in markets.get(SEASON_COUNT, [])
              if _num(c.get("mid")) is not None and _num(c.get("strike")) is not None]
    if quoted:
        return _contract(SEASON_COUNT, min(quoted, key=lambda c: _num(c["strike"])))
    return None


# -------------------------------------------------------------------- build
def _section(fn, *args):
    """A section that raises loses itself and nothing else."""
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001 - a malformed input costs one figure, not the file
        print(json.dumps({"kind": "headline", "section": fn.__name__, "error": f"{type(e).__name__}: {e}"}))
        return None


def build(store: Storage, now: dt.datetime) -> dict:
    card = _read(store, CARD_KEY)
    summary = _read(store, SUMMARY_KEY)
    market = _read(store, MARKET_KEY)
    group = _read(store, HURRICANE_KEY)

    snap = {"schema": SCHEMA, "asof": None, "written": _iso(now)}
    clocks = []
    days = _section(scored_days, card) or []
    acc = _section(accuracy, card, days)
    big = _section(largest_error, card, days[0] if days else None)
    if acc:
        snap["accuracy"] = acc
    if big:
        snap["largestError"] = big
    if acc or big:
        clocks.append(card)
    spread = _section(widest_spread, summary, market)
    if spread:
        snap["widestSpread"] = spread
        clocks.append(summary)
        if FX in spread["sources"]:
            clocks.append(market)
    hur = _section(hurricane, group, now)
    if hur:
        snap["hurricane"] = hur
        clocks.append(group)

    # The file is only as fresh as its oldest input, so a page reading `asof`
    # sees a feed that has stopped even while this job keeps writing. Every
    # producer stamps UTC seconds through snapshots._iso, one fixed width, so
    # the strings sort in the order of the instants they name.
    stamps = [c["asof"] for c in clocks if isinstance(_obj(c).get("asof"), str)]
    snap["asof"] = min(stamps) if stamps else snap["written"]
    return snap


def headline_pass(cfg: dict, store: Storage) -> int:
    """Entry point: read the snapshots on hand, write the small file. No
    network, so the only thing that can fail is the write itself."""
    now = dt.datetime.now(dt.timezone.utc)
    t0 = time.time()
    errors = 0
    snap: dict = {}
    body = b""
    try:
        snap = build(store, now)
        body = json.dumps(snap, separators=(",", ":")).encode()
        store.put(SNAP_KEY, body, "application/json", SNAP_CACHE)
    except Exception as e:  # noqa: BLE001 - recorded like any other job's failure
        errors = 1
        print(json.dumps({"kind": "headline", "error": f"{type(e).__name__}: {e}"}))
    arch.LAST_STATUS = {"job": "headline", "errors": errors, "alarms": [], "seconds": round(time.time() - t0, 1)}
    print(json.dumps({"kind": "headline", "bytes": len(body), "asof": snap.get("asof"),
                      "sections": [k for k in ("accuracy", "largestError", "widestSpread", "hurricane") if k in snap],
                      "errors": errors, "seconds": round(time.time() - t0, 1)}))
    return errors
