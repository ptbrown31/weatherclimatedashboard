"""The series the energy contracts settle on, from the Energy Information Administration.

Same shape as the other underlying series and written into the same place, so the
contract pages and the category pages draw them with the machinery that already
exists. What is different is that this source wants a key. It is free and it is
per-person, so it is not in the repo and not in the config file: it comes from
the environment as WX_EIA_API_KEY, and without it this lane writes nothing and
says so rather than failing the daily pass.

Every mapping below was checked against the strikes the exchange has listed
rather than inferred from the contract's name, because several of the names are
ambiguous and the wrong reading is not obviously wrong:

  Natural gas production is DRY production. Marketed production for the same
  month is about ten percent higher and lands outside every listed ladder.

  Oil production is thousand barrels PER DAY, not the month's total. The total
  is thirty times larger.

  The fuel shares are of all-fuels generation across all sectors, and solar is
  the plain solar figure rather than the estimated total that includes
  small-scale rooftop.

  The consumption contracts are consumption for electric power, not consumption
  across the whole economy. The tell is the seasonality: the listed December
  ladder for natural gas sits BELOW the August one, which is a power-sector
  cooling pattern; economy-wide gas consumption peaks in December for heating.

Two of these series have fallen through the bottom of their ladders and that is
real: California burned essentially no coal from early 2026, having run about
twenty thousand megawatt-hours a month through 2025. The charts show it.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import urllib.parse
import urllib.request
from typing import Callable, Dict, List, Optional

BASE = "https://api.eia.gov/v2/"
EPO = "electricity/electric-power-operational-data/data"
RET = "electricity/retail-sales/data"

# how far back to ask for; these are monthly or annual series and the charts want
# a run of history, not the latest point
START_MONTH = "2001-01"
START_YEAR = "2001"
START_WEEK = "2015-01-01"


def _gen(loc: str, fuel: str) -> dict:
    return {"route": EPO, "col": "generation", "freq": "monthly",
            "params": {"facets[location][]": loc, "facets[sectorid][]": "99",
                       "facets[fueltypeid][]": fuel}}


def _con(loc: str, fuel: str) -> dict:
    return {"route": EPO, "col": "total-consumption", "freq": "monthly",
            "params": {"facets[location][]": loc, "facets[sectorid][]": "99",
                       "facets[fueltypeid][]": fuel}}


def _ret(state: str, sector: str, col: str) -> dict:
    return {"route": RET, "col": col, "freq": "monthly",
            "params": {"facets[stateid][]": state, "facets[sectorid][]": sector}}


def _ser(route: str, series: str, freq: str) -> dict:
    return {"route": route, "col": "value", "freq": freq,
            "params": {"facets[series][]": series}}


# pid -> (series key, title, spec). One key per product: none of these share an
# underlying the way the temperature contracts do.
SPECS: Dict[str, tuple] = {
    # generation, thousand megawatthours
    "EMUSA": ("en-gen-us-all", "US total electricity generation", _gen("US", "ALL")),
    "EMUSC": ("en-gen-us-coal", "US coal electricity generation", _gen("US", "COW")),
    "EMUSR": ("en-gen-us-nuclear", "US nuclear electricity generation", _gen("US", "NUC")),
    "EMUSX": ("en-gen-us-renew", "US renewable electricity generation", _gen("US", "AOR")),
    "EMCAC": ("en-gen-ca-coal", "California coal electricity generation", _gen("CA", "COW")),
    "EMCAR": ("en-gen-ca-nuclear", "California nuclear electricity generation", _gen("CA", "NUC")),
    "EMCAX": ("en-gen-ca-renew", "California renewable electricity generation", _gen("CA", "AOR")),
    # consumption for electric power
    "CMUSC": ("en-con-us-coal", "US coal consumed for electricity", _con("US", "COW")),
    "CMUSN": ("en-con-us-gas", "US natural gas consumed for electricity", _con("US", "NG")),
    "CMCAC": ("en-con-ca-coal", "California coal consumed for electricity", _con("CA", "COW")),
    "CMCAN": ("en-con-ca-gas", "California natural gas consumed for electricity", _con("CA", "NG")),
    # retail sales: price in cents per kilowatt-hour, revenue in million dollars
    "AEUSA": ("en-price-us-all", "US average electricity price, all sectors", _ret("US", "ALL", "price")),
    "AEUSC": ("en-price-us-com", "US average electricity price, commercial", _ret("US", "COM", "price")),
    "AEUSI": ("en-price-us-ind", "US average electricity price, industrial", _ret("US", "IND", "price")),
    "AEUSR": ("en-price-us-res", "US average electricity price, residential", _ret("US", "RES", "price")),
    "AEUST": ("en-price-us-tra", "US average electricity price, transportation", _ret("US", "TRA", "price")),
    "REUSA": ("en-rev-us-all", "US electricity sales revenue, all sectors", _ret("US", "ALL", "revenue")),
    "REUSC": ("en-rev-us-com", "US electricity sales revenue, commercial", _ret("US", "COM", "revenue")),
    "RECAC": ("en-rev-ca-com", "California electricity sales revenue, commercial", _ret("CA", "COM", "revenue")),
    "RENYA": ("en-rev-ny-all", "New York electricity sales revenue, all sectors", _ret("NY", "ALL", "revenue")),
    "RENYC": ("en-rev-ny-com", "New York electricity sales revenue, commercial", _ret("NY", "COM", "revenue")),
    "RETXC": ("en-rev-tx-com", "Texas electricity sales revenue, commercial", _ret("TX", "COM", "revenue")),
    # fuels
    "NGP": ("en-gas-prod-us", "US dry natural gas production",
            _ser("natural-gas/prod/sum/data", "N9070US2", "monthly")),
    "OP": ("en-oil-prod-us", "US crude oil production",
           _ser("petroleum/crd/crpdn/data", "MCRFPUS2", "monthly")),
    "USGP": ("en-gasoline-us", "US regular retail gasoline price",
             _ser("petroleum/pri/gnd/data", "EMM_EPMR_PTE_NUS_DPG", "weekly")),
}

# share of all-fuels generation, in percent, annual. Computed rather than served:
# the agency publishes the generation, and the contract asks for the ratio.
SHARES: Dict[str, tuple] = {
    "ELGPC": ("en-share-coal", "Coal as a share of US electricity generation", "COW"),
    "ELGPN": ("en-share-gas", "Natural gas as a share of US electricity generation", "NG"),
    "ELGPP": ("en-share-petroleum", "Petroleum as a share of US electricity generation", "PEL"),
    "ELGPR": ("en-share-nuclear", "Nuclear as a share of US electricity generation", "NUC"),
    "ELGPH": ("en-share-hydro", "Conventional hydro as a share of US electricity generation", "HYC"),
    "ELGPS": ("en-share-solar", "Solar as a share of US electricity generation", "SUN"),
    "ELGPW": ("en-share-wind", "Wind as a share of US electricity generation", "WND"),
}

SOURCE = "US Energy Information Administration"
NOTE_MONTHLY = ("The agency revises recent months as more complete data arrives; a contract settles on the "
                "figure published at its resolution, and a later revision does not change how it settled.")
NOTE_SHARE = ("The share is this fuel's generation over all-fuels generation across all sectors, both as the "
              "agency publishes them. " + NOTE_MONTHLY)


def api_key(cfg: dict) -> str:
    """The key, from the environment first. It is never read from the repo."""
    return (os.environ.get("WX_EIA_API_KEY")
            or ((cfg.get("eia") or {}).get("api_key") or "")).strip()


def _get(route: str, params: dict, key: str, fetch: Optional[Callable]) -> dict:
    p = dict(params)
    p["api_key"] = key
    url = BASE + route.lstrip("/") + "?" + urllib.parse.urlencode(p, doseq=True)
    if fetch:
        raw = fetch(url)
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def _rows(spec: dict, key: str, fetch: Optional[Callable], length: int = 5000) -> List[dict]:
    freq = spec["freq"]
    start = {"monthly": START_MONTH, "annual": START_YEAR, "weekly": START_WEEK}[freq]
    p = dict(spec["params"])
    p.update({"frequency": freq, "data[0]": spec["col"], "start": start,
              "sort[0][column]": "period", "sort[0][direction]": "asc", "length": str(length)})
    d = _get(spec["route"], p, key, fetch)
    return (d.get("response") or {}).get("data") or []


def _x(period: str) -> Optional[float]:
    """A period as a number on a year axis.

    An annual figure is its year. A month is placed at its middle, which is the
    convention the climate series already use, so a monthly and an annual series
    can share an axis without one of them being drawn a year out.
    """
    s = str(period or "")
    try:
        if len(s) == 4:
            return float(int(s))
        if len(s) == 7:
            y, m = int(s[:4]), int(s[5:7])
            return round(y + (m - 0.5) / 12, 4)
        if len(s) == 10:
            d = dt.date(int(s[:4]), int(s[5:7]), int(s[8:10]))
            return round(d.year + (d.timetuple().tm_yday - 0.5) / 366, 4)
    except (TypeError, ValueError):
        return None
    return None


def _points(rows: List[dict], col: str) -> List[list]:
    out = []
    for r in rows:
        x, v = _x(r.get("period")), r.get(col)
        if x is None or v in (None, ""):
            continue
        try:
            out.append([x, round(float(v), 4)])
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda q: q[0])
    return out


def energy_series(cfg: dict, fetch: Optional[Callable] = None) -> Dict[str, dict]:
    """Every energy series this site can draw, keyed by series key.

    Returns an empty mapping when no key is configured; that is a state to
    report, not an error to raise, because the rest of the daily pass does not
    depend on it.
    """
    key = api_key(cfg)
    if not key:
        return {}
    out: Dict[str, dict] = {}

    for pid, (skey, title, spec) in SPECS.items():
        rows = _rows(spec, key, fetch)
        pts = _points(rows, spec["col"])
        if not pts:
            continue
        units = ""
        for r in rows:
            units = r.get(spec["col"] + "-units") or r.get("units") or ""
            if units:
                break
        out[skey] = {"key": skey, "products": [pid], "title": title, "units": units,
                     "points": pts, "source": SOURCE, "note": NOTE_MONTHLY,
                     "frequency": spec["freq"]}

    # the shares need the same denominator, so all-fuels generation is fetched once
    if SHARES:
        tot = {q[0]: q[1] for q in _points(
            _rows({"route": EPO, "col": "generation", "freq": "annual",
                   "params": {"facets[location][]": "US", "facets[sectorid][]": "99",
                              "facets[fueltypeid][]": "ALL"}}, key, fetch), "generation")}
        for pid, (skey, title, fuel) in SHARES.items():
            num = _points(_rows({"route": EPO, "col": "generation", "freq": "annual",
                                 "params": {"facets[location][]": "US", "facets[sectorid][]": "99",
                                            "facets[fueltypeid][]": fuel}}, key, fetch), "generation")
            pts = [[x, round(v / tot[x] * 100, 3)] for x, v in num if tot.get(x)]
            if not pts:
                continue
            out[skey] = {"key": skey, "products": [pid], "title": title, "units": "percent of total generation",
                         "points": pts, "source": SOURCE, "note": NOTE_SHARE, "frequency": "annual"}
    return out


def product_keys() -> Dict[str, str]:
    """pid -> series key, for the index the pages read."""
    m = {pid: k for pid, (k, _t, _s) in SPECS.items()}
    m.update({pid: k for pid, (k, _t, _f) in SHARES.items()})
    return m
