"""
exchange.py — every call to the exchange's public market-data endpoints, and
the pure functions that turn its payloads into the site's market snapshots.

ForecastEx (Interactive Brokers' event-contract venue) publishes three
unauthenticated JSON endpoints: the category tree (every market with its
symbol and underlying conid), a market's contract list, and a contract's top
of book. No key, no cookie; the same descriptive User-Agent the government
calls send. Everything measured 2026-08-23, directly from an AWS address:

  - all three answer 200 with no proxy; a pass that loses every request
    keeps the previous snapshot and counts a failure streak like any other
    source, because a CDN in front of the endpoints can block an address
    range without notice;
  - a quote is one request per contract: a multi-conid query returns 500;
  - four concurrent workers fetched 120 quotes in 7.6 s with no throttling,
    about 0.25 s each.

Payload shapes actually consumed:

  tree    {"categories": {id: {"name", "parent_id", "markets": [{"name",
          "symbol", "conid", ...}]}}}
  market  {"market_name", "symbol", "contracts": [{"conid", "side": "Y"|"N",
          "strike": 66.0, "strike_label": "Above 66", "time_specifier":
          "2026.8.22", "expiry_label": "August 22, 2026", "expiration":
          "20260823"}]}
  quote   {"bid", "bid_size", "ask", "ask_size"}, each key present only when
          that side of the book exists; {} when there is no book at all.

Daily temperature markets: symbol UH+code / UL+code for the US high and low
(Fahrenheit strikes, "Above K" = P(daily high > K), "Below K" = P(daily low
< K), the settlement convention being strict), SH+code for the international
highs (Celsius strikes, no low markets listed). `time_specifier` names the
weather day; `expiration` is the settlement date the exchange assigns (the
next calendar day for the US listings). Every strike is listed twice,
one Yes and one No contract; the No book mirrors the Yes book, so only Yes
is quoted and No is used only when Yes has no book at all.
"""
from __future__ import annotations
import datetime as dt
import json
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from . import gov_weather as gw

BASE_URL = "https://forecasttrader.interactivebrokers.com"
TREE_PATH = "/tws.proxy/public/forecasttrader/category/tree"
MARKET_PATH = "/tws.proxy/public/forecasttrader/contract/market?underlyingConid={conid}"
QUOTE_PATH = "/tws.proxy/public/mdfarm/event-contract/bid-ask?conid={conid}&exchange=FORECASTX"
RULES_PATH = "/tws.proxy/public/forecasttrader/contract/rules?conid={conid}"

# The exchange's three-letter city code is the settlement station's ICAO
# without its first letter, except for these two, whose codes predate the
# pairing with the station named in the contract specifications.
CODE_OVERRIDES = {"CYVR": "YHC", "LFPG": "FPO"}

# Category names (not ids, which could be renumbered) whose markets the
# market job quotes as groups, and the symbols the climate page joins.
HURRICANE_CATEGORY = "Major Weather Events"
CLIMATE_SYMBOLS = {
    "GTTA": "tempAnnual", "GTTM": "tempMonthly", "GSL": "seaLevel", "ACD": "co2", "AMOCW": "amoc",
}

_base = BASE_URL


def set_base_url(url: str) -> None:
    global _base
    _base = (url or BASE_URL).rstrip("/")


# ---------------------------------------------------------------- fetching
def _get_json(path: str, timeout: int = 12, tries: int = 2) -> Any:
    """GET JSON. Retries once on 429, 5xx and transport errors; a 4xx other
    than 429 will not change on retry and raises at once."""
    url = _base + path
    last: Optional[Exception] = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": gw.USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
            return json.loads(body) if body.strip() else None
        except urllib.error.HTTPError as e:
            last = e
            if 400 <= e.code < 500 and e.code != 429:
                raise
        except Exception as e:  # noqa: BLE001 - transport errors, retried once
            last = e
        if attempt < tries - 1:
            time.sleep(1.0 * (attempt + 1))
    raise last if last else RuntimeError("unreachable")


def fetch_tree() -> dict:
    return _get_json(TREE_PATH, timeout=30)


def fetch_market(conid: int) -> dict:
    return _get_json(MARKET_PATH.format(conid=conid)) or {}


# ------------------------------------------------------------------ breaker
QUOTE_BREAKER_TRIP = 50     # consecutive failures before a pass gives up on the exchange


class BreakerOpen(RuntimeError):
    """Raised in place of a request the breaker has decided not to make."""


class Breaker:
    """Stop asking once the exchange has clearly stopped answering.

    A quote that fails costs far more than one that succeeds: eight seconds to
    time out, a second of backoff, eight more for the retry. When the exchange
    is unreachable every call pays that, so a pass that normally makes 888
    calls in under a minute spends its entire budget failing them. Measured on
    27 August 2026: 871 seconds and 888 failures for the work that took 58
    seconds the hour before, and roughly 1,800 requests aimed at an exchange
    that was already refusing them.

    Nothing is learned from the 889th attempt after 888 have failed. Once
    `trip` calls have failed with no success between them, the breaker opens
    and every later call in the pass raises at once, ending it in seconds.
    Nothing else changes: the pass reports the same failure, keeps the previous
    ladders rather than writing half-read ones, and a pass that quoted nothing
    still raises. What changes is how long it takes to find out.

    One success closes it again. Scattered failures among working calls are an
    ordinary state — of 432 healthy passes 431 had none at all and one had 73 —
    and must never stop a pass that is otherwise working.

    One breaker belongs to one pass. It is deliberately not process-global: a
    warm Lambda container would carry an open breaker into the next
    invocation, which would turn one outage into a silent outage of its own.
    """

    def __init__(self, trip: int = QUOTE_BREAKER_TRIP):
        self.trip = trip
        self.consecutive = 0
        self.failures = 0
        self.opened = False
        self._lock = threading.Lock()

    def guard(self, fetch: Callable) -> Callable:
        """`fetch`, wrapped so the pass stops once the exchange is plainly down."""
        def wrapped(conid):
            with self._lock:
                if self.opened:
                    raise BreakerOpen(f"exchange unreachable: {self.failures} calls failed, pass abandoned")
            try:
                out = fetch(conid)
            except Exception:
                with self._lock:
                    self.failures += 1
                    self.consecutive += 1
                    if self.consecutive >= self.trip:
                        self.opened = True
                raise
            with self._lock:
                self.consecutive = 0
            return out
        return wrapped


def fetch_quote(conid: int) -> dict:
    """{} when the contract has no book; keys only for the sides that exist."""
    q = _get_json(QUOTE_PATH.format(conid=conid), timeout=8)
    return q if isinstance(q, dict) else {}


# ---------------------------------------------------------------- the tree
def markets_by_symbol(tree: dict) -> Dict[str, dict]:
    """symbol -> {"symbol", "name", "conid", "productConid", "category"} per market.

    Two ids, and they are not interchangeable. `conid` is the underlying, which
    is what the contract-list endpoint takes. `productConid` is what the
    exchange's own web app puts in the path of a product page, so it is the one
    a link to a contract needs. They are adjacent numbers for the same market
    and it is easy to assume they are the same; they are not."""
    out: Dict[str, dict] = {}
    cats = (tree or {}).get("categories") or {}
    items = cats.values() if isinstance(cats, dict) else cats
    for cat in items:
        for m in (cat or {}).get("markets") or []:
            sym = str(m.get("symbol") or "").upper()
            if sym and m.get("conid"):
                out.setdefault(sym, {"symbol": sym, "name": m.get("name"), "conid": m["conid"],
                                     "productConid": m.get("product_conid"),
                                     "category": (cat or {}).get("name")})
    return out


def category_markets(tree: dict, category_name: str) -> List[dict]:
    """Markets under the category of that name and all of its descendants,
    in tree order. Names are matched case-insensitively."""
    cats = (tree or {}).get("categories") or {}
    if not isinstance(cats, dict):
        return []
    want = category_name.strip().lower()
    roots = {k for k, v in cats.items() if str((v or {}).get("name", "")).strip().lower() == want}
    if not roots:
        return []
    grown = True
    while grown:
        grown = False
        for k, v in cats.items():
            if k not in roots and (v or {}).get("parent_id") in roots:
                roots.add(k)
                grown = True
    out = []
    for k in cats:
        if k in roots:
            for m in (cats[k] or {}).get("markets") or []:
                if m.get("conid"):
                    out.append({"symbol": str(m.get("symbol") or "").upper(), "name": m.get("name"),
                                "conid": m["conid"], "productConid": m.get("product_conid"),
                                "category": (cats[k] or {}).get("name")})
    return out


def storm_code(name: str) -> str:
    """The exchange builds a storm's live-wind symbols from the first two
    letters of its name, so Erin becomes ER."""
    letters = "".join(ch for ch in str(name or "") if ch.isalpha())
    return letters[:2].upper()


def storm_wind_markets(tree: dict, names: list, locations: Optional[set] = None) -> List[dict]:
    """Every live-wind market belonging to one of the named storms.

    Two products appear once a storm is active: L<storm><location>, a gust
    threshold ladder for one reference location, and LHL<storm><pool letter>,
    "which location in this pool records the highest wind", whose strikes are
    place names rather than numbers. They are matched by shape rather than by
    category, because the category they are listed under is not known until the
    exchange lists them.

    The shape alone is not enough in either direction, which two live symbols
    show. The tree carries LEGCA, "California Legalization Decision", and
    LOFUS, "US Total Layoffs"; both fit the ladder's shape exactly, and a storm
    named Egon or Ofelia would have claimed one as its own board. In the other
    direction the storm's two letters are NOT reliably the first two of its
    name: Nolo's instruments were uploaded as LNL and LHLNL, so a rule built on
    the first two letters would look for LNO and find nothing on the day the
    board opened.

    So the test is the exchange's own product name, which carries the storm's
    name: the one real pool this site has recorded, LHLED, is "Hurricane
    Edouard Peak Wind Location". That reads through whatever two letters the
    exchange chose. It costs us a market the exchange lists without naming the
    storm in it, which is a miss we would notice, rather than a wrong market
    attached to a storm, which we would not.

    `locations` is the reference-location registry, used only on the nameless
    path, where a ladder must also end in a real place. Left out, that check is
    skipped rather than failing everything. Nothing reaches that path today.
    """
    if not names:
        return []
    out, seen = [], set()
    for sym, m in markets_by_symbol(tree).items():
        nm = str(m.get("name") or "").lower()
        storm = next((n for n in names if n and str(n).lower() in nm), None)
        if storm is None and not nm:
            # Nothing to read. A market the tree gave no name for cannot be a
            # decoy by name either, so the derived code is what is left, and on
            # the ladder form the last two letters must also be a real
            # reference location.
            #
            # This branch is dead against the API as it stands: all 880 symbol
            # nodes carried a name on 24 September, counted rather than assumed,
            # and the quote job logs any node that ever arrives without one so
            # the day that changes is a day someone hears about. It is kept
            # because a matcher whose only guard is a field the exchange
            # happens to populate is one bad deployment away from wrong.
            storm = next((n for n in names if storm_code(n) and sym[3:5] == storm_code(n)
                          and sym.startswith("LHL")), None) \
                or next((n for n in names if storm_code(n) and sym[1:3] == storm_code(n)
                         and (locations is None or sym[3:5] in locations)), None)
        if not storm or sym in seen:
            continue
        # the pool is LHL<storm> with an optional pool letter: Edouard's
        # listed as bare LHLED, Erin's fixture as LHLERG, and both are pools
        if re.match(r"^LHL[A-Z]{2}[A-Z]?$", sym):
            out.append({**m, "product": "LHL", "storm": sym[3:5], "stormName": storm})
            seen.add(sym)
        elif re.match(r"^L[A-Z]{2}[A-Z]{2}$", sym):
            out.append({**m, "product": "L", "storm": sym[1:3], "location": sym[3:5], "stormName": storm})
            seen.add(sym)
    return out


# Which side of a station's board a product id belongs to, by its family
# prefix. The letter pairs are the exchange's own: U and S are the Fahrenheit
# and Celsius daily series, D is a parallel daily series the exchange carries
# but has never opened, H and L are high and low.
SIDE_FAMILIES = {"high": ("SH", "UH", "DH"), "low": ("SL", "UL", "DL"),
                 "hourly": ("HRS", "HRU"), "wind": ("MG",)}


def _family_order(side: str, celsius: bool) -> tuple:
    """The families to try for a side, best first.

    A Celsius station's listing is the S series and a Fahrenheit one's is the U
    series, so that family leads; the other follows because a station can carry
    both (Abu Dhabi has SHMAA and UHMAA) and only one of them is ever open. The
    D series comes last: it names the same product at the same place and the
    exchange has never listed it.
    """
    fams = SIDE_FAMILIES[side]
    if side in ("high", "low"):
        return (fams[0], fams[1], fams[2]) if celsius else (fams[1], fams[0], fams[2])
    if side == "hourly":
        return fams if celsius else (fams[1], fams[0])
    return fams


def symbols_for(city: dict, registry: Optional[set] = None, products: Optional[list] = None,
                listed: Optional[set] = None) -> Dict[str, str]:
    """The symbols a station can have: the daily temperature high and low, the
    hourly temperature, and the peak wind.

    With `products`, the registry rows from config/contracts.json, the symbol
    comes from the registry's own city column. That is what a product is
    written against, and it removes the derivation from the answer: the
    exchange's three-letter code is the station's ICAO minus its first letter
    at most places and is not at Vancouver (YHC for CYVR) or Paris (FPO for
    LFPG), and nobody outside the exchange knows which of MBD and MDB it uses
    for Dubai, which is why the registry carries Dubai twice. A city with more
    than one candidate for a side is decided by `listed`, the symbols the tree
    is carrying, and where the tree carries neither the first candidate stands
    so the pass still names something it looked for.

    Without `products` the symbol is derived from the ICAO as it was before,
    with CODE_OVERRIDES for the two known exceptions. That path still serves
    the stations the registry does not cover.

    High and low are the Fahrenheit (US) listings, high only for the Celsius
    ones. The hourly temperature and the wind are listed at a subset of
    stations, so `registry` (the product ids in config/contracts.json) keeps a
    symbol out of the derivation entirely rather than deriving one the exchange
    has never had and reporting it unmatched every pass. Without a registry the
    daily pair is returned alone, which is what this did before the two extra
    families existed."""
    sid = city["station"]
    celsius = city.get("unit") == "C"
    # A product belongs to this station when the registry names the same place
    # OR when its three-letter code is the station's ICAO without its first
    # letter. Neither signal is sufficient alone. The code is wrong at
    # Vancouver and Paris, where only the city agrees; the city is wrong at
    # Minneapolis, which the roster calls Minneapolis and the registry calls
    # Minneapolis-Saint Paul, and where only the code agrees. Together they
    # cover the whole roster the registry carries.
    nm, tail = (city.get("city") or "\0"), sid[1:]
    rows = [p for p in (products or [])
            if (p.get("city") or "") == nm or p["id"].endswith(tail)]
    if rows:
        out = {}
        for side in ("high", "low", "hourly", "wind"):
            if side == "low" and celsius:
                continue
            cands = []
            for fam in _family_order(side, celsius):
                cands += sorted(p["id"] for p in rows
                                if p["id"].startswith(fam) and len(p["id"]) == len(fam) + 3)
            # the derived symbol stays on the end as a safety net for the daily
            # pair, which every station has: the registry is missing Honolulu's
            # low, and a gap in it should not drop a board the exchange opens.
            # Hourly and wind stay registry-only, because deriving those where
            # the exchange has never had them is what the registry gate is for.
            if side in ("high", "low"):
                der = ("SH" if celsius else ("UL" if side == "low" else "UH")) + CODE_OVERRIDES.get(sid, tail)
                if der not in cands:
                    cands.append(der)
            if not cands:
                continue
            out[side] = next((c for c in cands if c in (listed or ())), cands[0])
        if out:
            return out
    code = CODE_OVERRIDES.get(sid, sid[1:])
    out = {"high": "SH" + code} if celsius else {"high": "UH" + code, "low": "UL" + code}
    if registry:
        for side, sym in (("hourly", ("HRS" if celsius else "HRU") + code), ("wind", "MG" + code)):
            if sym in registry:
                out[side] = sym
    return out


# the sides whose ladder is a strike ladder for a whole weather day, which is
# every side except the hourly one, where a day holds a ladder per hour
DAY_SIDES = ("high", "low", "wind")


def day_of(spec: str) -> Optional[str]:
    """'2026.8.22' -> '2026-08-22'. Monthly and yearly products carry shorter
    specifiers ('2026.8', '2026.12'); those return None here."""
    parts = str(spec or "").split(".")
    if len(parts) != 3:
        return None
    try:
        return dt.date(int(parts[0]), int(parts[1]), int(parts[2])).isoformat()
    except ValueError:
        return None


def strike_key(contract: dict):
    """(key, numeric) for one contract's strike. Temperature and count contracts
    carry a number; the "which location records the highest wind" contracts carry
    a place name instead, so those key on the strike's label and are marked
    non-numeric. Returns (None, None) when there is nothing to key on."""
    raw = contract.get("strike")
    try:
        return float(raw), True
    except (TypeError, ValueError):
        pass
    label = contract.get("strike_label") or (str(raw).strip() if raw not in (None, "") else "")
    return (label, False) if label else (None, None)


def group_contracts(market: dict, days: Optional[set] = None) -> Dict[str, Dict[float, dict]]:
    """{day: {strike: {"label", "expiration", "Y": conid, "N": conid}}} for
    the weather days wanted (all days when `days` is None)."""
    out: Dict[str, Dict[float, dict]] = {}
    for c in (market or {}).get("contracts") or []:
        day = day_of(c.get("time_specifier"))
        if day is None or (days is not None and day not in days):
            continue
        try:
            strike = float(c.get("strike"))
        except (TypeError, ValueError):
            continue
        slot = out.setdefault(day, {}).setdefault(strike, {"label": c.get("strike_label"), "expiration": c.get("expiration")})
        side = str(c.get("side") or "").upper()
        if side in ("Y", "N") and c.get("conid"):
            slot[side] = c["conid"]
    return out


def fetch_contract_rules(conid: int) -> dict:
    """One contract's rules: the period it measures, its threshold, and the
    times it trades to and pays at. The market listing does not carry the
    measured period, so for an hourly product this is the only published place
    the contract's hour appears."""
    return _get_json(RULES_PATH.format(conid=conid))


_MEASURED = re.compile(r"([A-Z][a-z]{2})\s*(\d{1,2})'(\d{2})\s+(\d{1,2}):(\d{2})\s*([AP])M", re.I)
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def hour_of(rules: dict, tz) -> tuple:
    """(day, hour, how) for one hourly contract, from its rules, in the
    station's own local time, or (None, None, None).

    The hour is the end of the period the contract measures, which is the time
    it stops trading and the time its question names. `last_trade_time` is a
    unix epoch and is used first, because it needs no time zone of its own;
    `measured_period` ("Sep23'26 2:00 PM ET") is the written form of the same
    instant and is parsed only if the epoch is missing, in the exchange's own
    zone. `how` records which answered, so an hour read from text is never
    mistaken for one read from an epoch."""
    for field in ("last_trade_time", "release_time"):
        ep = rules.get(field)
        if isinstance(ep, (int, float)) and ep > 0:
            local = dt.datetime.fromtimestamp(float(ep), dt.timezone.utc).astimezone(tz)
            return local.date().isoformat(), local.hour, field
    m = _MEASURED.search(str(rules.get("measured_period") or ""))
    if m:
        mon, day, yy, hh, mi, ap = m.groups()
        hour = int(hh) % 12 + (12 if ap.upper() == "P" else 0)
        try:
            zone = ZoneInfo(rules.get("exchange_timezone") or "America/New_York")
        except Exception:  # noqa: BLE001
            zone = ZoneInfo("America/New_York")
        t = dt.datetime(2000 + int(yy), _MONTHS[mon.lower()], int(day), hour, int(mi), tzinfo=zone)
        local = t.astimezone(tz)
        return local.date().isoformat(), local.hour, "measured_period"
    return (None, None, None)


def group_hourly_contracts(market: dict, place_hour: Callable, days: Optional[set] = None) -> tuple:
    """{day: {hour: {strike: {"label", "expiration", "how", "Y", "N"}}}} and the
    number of contracts whose hour could not be placed.

    An hourly market lists every hour of a day under one date-only time
    specifier, with the strikes repeating, so the hour is not in the listing at
    all: it is in each contract's own rules. `place_hour(conid)` returns
    (day, hour, how) for one contract and is the caller's to cache, since a
    contract's hour never changes and the rules cost a request each. A contract
    it cannot place is left out and counted rather than filed under a guess."""
    out: Dict[str, Dict[int, Dict[float, dict]]] = {}
    unplaced = 0
    for c in (market or {}).get("contracts") or []:
        try:
            strike = float(c.get("strike"))
        except (TypeError, ValueError):
            continue
        if not c.get("conid"):
            continue
        if days is not None and day_of(c.get("time_specifier")) not in days:
            continue
        day, hour, how = place_hour(c["conid"])
        if day is None or hour is None or (days is not None and day not in days):
            unplaced += 1
            continue
        slot = out.setdefault(day, {}).setdefault(hour, {}).setdefault(
            strike, {"label": c.get("strike_label"), "expiration": c.get("expiration"), "how": how})
        side = str(c.get("side") or "").upper()
        if side in ("Y", "N"):
            slot[side] = c["conid"]
    return out, unplaced


def yes_quote(qy: Optional[dict], qn: Optional[dict]) -> dict:
    """Top of book in Yes terms. The Yes book is used when it has either
    side; otherwise the No contract's bids are complemented (yes bid = 1 - no
    ask). Sizes are contracts. `from` records which contract the numbers came
    from. Language note: the exchange has no sellers, only bids to buy Yes or
    No that sum to one dollar; the feed's "ask" on a Yes contract is one
    dollar less the best No bid, and the pages say "No bid"."""
    def num(d, k):
        v = (d or {}).get(k)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None
    if qy and (num(qy, "bid") is not None or num(qy, "ask") is not None):
        return {"bid": num(qy, "bid"), "ask": num(qy, "ask"), "bidSize": num(qy, "bid_size"), "askSize": num(qy, "ask_size"), "from": "yes"}
    if qn and (num(qn, "bid") is not None or num(qn, "ask") is not None):
        nb, na = num(qn, "bid"), num(qn, "ask")
        return {"bid": round(1 - na, 4) if na is not None else None, "ask": round(1 - nb, 4) if nb is not None else None,
                "bidSize": num(qn, "ask_size"), "askSize": num(qn, "bid_size"), "from": "no"}
    return {"bid": None, "ask": None, "bidSize": None, "askSize": None, "from": None}


def mid(q: dict) -> Optional[float]:
    """The Yes price: midway between the Yes bid and one dollar less the No
    bid when both exist, else the one side that does. This is what the pages
    call the implied probability; it is not fee-adjusted (the exchange charges
    $0.01 per contract to each side at execution, per its FAQ)."""
    b, a = q.get("bid"), q.get("ask")
    if b is not None and a is not None:
        return round((b + a) / 2, 4)
    return b if b is not None else a


def implied_median(rows: List[dict], side: str) -> dict:
    """The market-implied median temperature: the strike where P(Yes) crosses
    0.5, linearly interpolated between the two neighbouring strikes. High
    markets have P falling with the strike, low markets rising. With no
    crossing inside the ladder the result is None and `edge` names the side
    the ladder sits on, so a page can say 'above the top strike' instead of
    inventing a number."""
    pts = sorted((r["strike"], r["mid"]) for r in rows if r.get("mid") is not None)
    if len(pts) < 2:
        return {"value": None, "edge": None, "n": len(pts)}
    if side == "low":
        pts = [(k, 1 - p) for k, p in pts]        # P(low < K) rises with K: flip to a falling curve
    for (k0, p0), (k1, p1) in zip(pts, pts[1:]):
        if p0 >= 0.5 >= p1 and p0 != p1:
            return {"value": round(k0 + (p0 - 0.5) / (p0 - p1) * (k1 - k0), 2), "edge": None, "n": len(pts)}
        if p0 >= 0.5 >= p1:
            return {"value": round((k0 + k1) / 2, 2), "edge": None, "n": len(pts)}
    if pts[0][1] < 0.5:
        return {"value": None, "edge": "below", "n": len(pts)}
    return {"value": None, "edge": "above", "n": len(pts)}
