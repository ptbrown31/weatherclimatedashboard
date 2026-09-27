"""
desk.py — the desk's own numbers, fetched rather than derived.

This site computes no fair value, no model probability and no forecast of its
own, and this module does not change that. The desk computes those numbers on
its own systems. The owner's decision, 24 September for the hurricane pool and
25 September for the wind ladders, is that where the exchange is not yet
quoting a contract the desk's figure may stand in on the page, drawn so it
cannot be read as a price, and dropped the moment a real price exists.

Three things follow, enforced here rather than trusted to the caller:

  - The feed is fetched over https with redirects refused. It is another
    party's endpoint, and a redirect is the cheap way to send a fetcher
    somewhere it did not mean to go.
  - A missing or malformed feed is an ABSENCE, not a failure. The page has a
    state for a contract with no figure and had nothing else before this lane
    existed, so an unreadable file must never fail a pass.
  - The desk's rows never travel in the same field as the exchange's. The
    market job keeps them under `anticipated`, never under `days`, so nothing
    downstream can mistake one for the other by reading the wrong key. That
    separation is the whole safety property of this lane.

The desk serves the file; this site does not write into the desk's bucket and
the desk does not write into this one. That keeps a credential off the desk
box, and it keeps a file covering every station the desk prices out of a
bucket CloudFront serves whole.

The feed this module reads (the desk publishes it; nothing here produces it):

    {"asof": "2026-09-25T09:00:00Z",          # when the desk computed them
     "method": "short label for the page",     # optional
     "stations": {
        "KACY": {"2026-09-27": [{"strike": 35, "p": 0.62},
                                {"strike": 49, "p": 0.18}]}}}

`strike` is whole miles per hour, the same unit and convention the MG ladder
uses. `p` is the desk's probability that the day's strongest wind settles above
that strike, between 0 and 1. Anything else in the file is ignored.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Dict, List, Optional, Tuple

from . import gov_weather as gw


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect())


def fetch(url: str, api_key: str = "", timeout: int = 15) -> Optional[bytes]:
    """The desk's file, from wherever the desk publishes it. None when unset."""
    if not url:
        return None
    if not url.lower().startswith("https://"):
        raise ValueError("the desk feed URL must be https")
    headers = {"User-Agent": gw.USER_AGENT, "Accept": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    req = urllib.request.Request(url, headers=headers)
    with _OPENER.open(req, timeout=timeout) as r:
        return r.read()


def wind_ladders(raw: Optional[bytes]) -> Dict[Tuple[str, str], dict]:
    """The desk's wind ladders, keyed by (station, contract day).

    Every row is checked rather than trusted: a strike has to be a real number
    and a probability has to sit in [0, 1]. A row that fails is dropped, and a
    station left with no rows is dropped with it, because half a ladder drawn
    as if it were whole is worse than no ladder. Rows come back sorted by
    strike so the page can draw them in one order without re-sorting.
    """
    try:
        doc = json.loads(raw or b"null")
    except Exception:  # noqa: BLE001 - an unreadable feed is an absence, not a failed pass
        return {}
    if not isinstance(doc, dict):
        return {}
    asof = doc.get("asof") if isinstance(doc.get("asof"), str) else None
    method = doc.get("method") if isinstance(doc.get("method"), str) else None
    stations = doc.get("stations")
    if not isinstance(stations, dict):
        return {}
    out: Dict[Tuple[str, str], dict] = {}
    for sid, byday in stations.items():
        if not isinstance(sid, str) or not isinstance(byday, dict):
            continue
        for day, rows in byday.items():
            if not isinstance(day, str) or not isinstance(rows, list):
                continue
            keep: List[dict] = []
            for r in rows:
                if not isinstance(r, dict):
                    continue
                try:
                    k = float(r["strike"])
                    p = float(r["p"])
                except (KeyError, TypeError, ValueError):
                    continue
                if k != k or p != p or not (0.0 <= p <= 1.0):   # NaN fails both comparisons
                    continue
                keep.append({"strike": k, "p": round(p, 4)})
            if not keep:
                continue
            keep.sort(key=lambda r: r["strike"])
            out[(sid.strip().upper(), day)] = {"rows": keep, "asof": asof, "method": method}
    return out
