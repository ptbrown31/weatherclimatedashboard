"""
exchange.py — every call to the exchange's public market-data endpoints, and
the pure functions that turn its payloads into the site's market snapshots.

ForecastEx (Interactive Brokers' event-contract venue) publishes three
unauthenticated JSON endpoints: the category tree (every market with its
symbol and underlying conid), a market's contract list, and a contract's top
of book. No key, no cookie; the same descriptive User-Agent the government
calls send. Everything measured 2026-08-23, directly from an AWS address:

  - all three answer 200 with no proxy (the internal system once needed one
    after a CDN block, so a pass that loses every request keeps the previous
    snapshot and counts a failure streak like any other source);
  - a quote is one request per contract: a multi-conid query returns 500;
  - four concurrent workers fetched 120 quotes in 7.6 s with no throttling,
    about 0.25 s each; the internal capture runs ten workers every 10 min.

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
weather day, `expiration` the day after it. Every strike is listed twice,
one Yes and one No contract; the No book mirrors the Yes book, so only Yes
is quoted and No is used only when Yes has no book at all.
"""
from __future__ import annotations
import datetime as dt
import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from . import gov_weather as gw

BASE_URL = "https://forecasttrader.interactivebrokers.com"
TREE_PATH = "/tws.proxy/public/forecasttrader/category/tree"
MARKET_PATH = "/tws.proxy/public/forecasttrader/contract/market?underlyingConid={conid}"
QUOTE_PATH = "/tws.proxy/public/mdfarm/event-contract/bid-ask?conid={conid}&exchange=FORECASTX"

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


def fetch_quote(conid: int) -> dict:
    """{} when the contract has no book; keys only for the sides that exist."""
    q = _get_json(QUOTE_PATH.format(conid=conid), timeout=8)
    return q if isinstance(q, dict) else {}


# ---------------------------------------------------------------- the tree
def markets_by_symbol(tree: dict) -> Dict[str, dict]:
    """symbol -> {"symbol", "name", "conid", "category"} for every market."""
    out: Dict[str, dict] = {}
    cats = (tree or {}).get("categories") or {}
    items = cats.values() if isinstance(cats, dict) else cats
    for cat in items:
        for m in (cat or {}).get("markets") or []:
            sym = str(m.get("symbol") or "").upper()
            if sym and m.get("conid"):
                out.setdefault(sym, {"symbol": sym, "name": m.get("name"), "conid": m["conid"],
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
                                "conid": m["conid"], "category": (cats[k] or {}).get("name")})
    return out


def symbols_for(city: dict) -> Dict[str, str]:
    """The daily temperature symbols a station can have: high and low for the
    Fahrenheit (US) listings, high only for the Celsius ones."""
    sid = city["station"]
    code = CODE_OVERRIDES.get(sid, sid[1:])
    if city.get("unit") == "C":
        return {"high": "SH" + code}
    return {"high": "UH" + code, "low": "UL" + code}


# ---------------------------------------------------------------- contracts
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


def yes_quote(qy: Optional[dict], qn: Optional[dict]) -> dict:
    """Top of book in Yes terms. The Yes book is used when it has either
    side; otherwise the No book is complemented (yes bid = 1 - no ask). Sizes
    are contracts. `from` records which book the numbers came from."""
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
    """The Yes midpoint when both sides exist, else the one side that does.
    This is the 'implied probability' the internal dashboard shows; it is not
    fee-adjusted (the exchange charges half a cent a side at execution)."""
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
