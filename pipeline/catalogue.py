"""What the exchange lists, for every product in the registry.

The quote job asks the exchange for prices one contract at a time and runs
every ten minutes, so it covers the handful of markets the displays actually
draw. This job is the other half: once a day it walks the whole product list
and records what each one *is* — its strikes, when they expire, and the ids a
link to the exchange needs — without asking for a single price.

That split is deliberate. A contract page needs terms far more often than it
needs a fresh quote, terms change about as often as the exchange relists, and
209-odd products at one call each is a minute of work rather than a permanent
tax on the ten-minute lane.

Products the registry names but the exchange does not list come back marked
unlisted rather than missing, because "the exchange has not opened this yet" is
a fact worth showing and is not the same as a failed fetch.

Output is layered so no page loads more than it needs: a small index of the
categories, one listing per category naming its products and their state, and
one file per product carrying that product's ladder. A category listing is a
few kilobytes; the ladders are only fetched when a contract is opened.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from typing import Callable, Dict, List, Optional

from . import archive as arch
from . import exchange as ex
from .storage import Storage

SCHEMA = 1
INDEX_KEY = "snapshots/catalogue/index.json"
CAT_KEY = "snapshots/catalogue/{slug}.json"
PRODUCT_KEY = "snapshots/catalogue/product/{pid}.json"
CACHE = "public, max-age=1800, stale-while-revalidate=86400, stale-if-error=2592000"
# same reasoning as the series index: this one decides what a category lists
INDEX_CACHE = "public, max-age=120, stale-while-revalidate=86400, stale-if-error=2592000"
MAX_CONTRACTS = 400          # a ladder longer than this is a listing error, not a ladder


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _contracts(market: dict) -> List[dict]:
    """The contract list as a page needs it: one row per strike, with the Yes
    and No ids kept apart. Strikes that carry a place name rather than a number
    are kept as names, which is what the pool contracts use."""
    out: Dict[str, dict] = {}
    for c in (market or {}).get("contracts") or []:
        key, numeric = ex.strike_key(c)
        if key is None:
            continue
        spec = str(c.get("time_specifier") or "")
        k = f"{spec}|{key}"
        row = out.setdefault(k, {"strike": key, "numeric": bool(numeric), "label": c.get("strike_label"),
                                 "expiration": c.get("expiration"), "expiryLabel": c.get("expiry_label"),
                                 "spec": spec})
        side = str(c.get("side") or "").upper()
        if side == "Y" and c.get("conid"):
            row["conidYes"] = c["conid"]
        elif side == "N" and c.get("conid"):
            row["conidNo"] = c["conid"]
    rows = list(out.values())
    rows.sort(key=lambda r: (str(r.get("spec") or ""), r["strike"] if r["numeric"] else 0, str(r["strike"])))
    return rows[:MAX_CONTRACTS]


def catalogue_pass(cfg: dict, store: Storage, fetch: Optional[Callable] = None) -> int:
    t0 = time.time()
    now = _iso(dt.datetime.now(dt.timezone.utc))
    fetch = fetch or ex.fetch_market
    reg = cfg.get("contracts") or {}
    products = reg.get("products") or []
    cats = reg.get("categories") or []
    if not products:
        # a missing registry is a packaging fault, not an empty day: reporting
        # success here made a job that wrote nothing look like a clean pass
        print(json.dumps({"kind": "catalogue", "written": False, "reason": "no contract registry",
                          "hint": "config/contracts.json is not on the path; check the deployment package"}))
        arch.LAST_STATUS = {"job": "catalogue", "errors": 1, "alarms": ["catalogue: no contract registry"]}
        return 1

    deadline = arch.Deadline(arch.remaining_budget(cfg))
    errors: List[str] = []
    try:
        tree = ex.fetch_tree()
        listed = ex.markets_by_symbol(tree)
    except Exception as e:  # noqa: BLE001
        errors.append(f"catalogue tree: {type(e).__name__}: {e}")
        listed = {}

    found = unlisted = failed = 0
    by_cat: Dict[str, List[dict]] = {}
    for p in products:
        row = {"id": p["id"], "name": p.get("name"), "active": bool(p.get("active")),
               "l1": p.get("l1"), "l2": p.get("l2"), "was": p.get("was")}
        m = listed.get(p["id"])
        if not m:
            # the registry knows it, the exchange has not opened it: a state to
            # show, not an error to report
            row["state"] = "unlisted"
            unlisted += 1
        elif deadline.over(20):
            row["state"] = "deferred"
        else:
            row.update({"symbol": m["symbol"], "conid": m.get("conid"), "productConid": m.get("productConid"),
                        "exchangeName": m.get("name"), "category": m.get("category")})
            try:
                mk = fetch(m["conid"])
                row["contracts"] = _contracts(mk)
                row["marketName"] = mk.get("market_name")
                row["state"] = "listed" if row["contracts"] else "no-contracts"
                found += 1
            except Exception as e:  # noqa: BLE001
                row["state"] = "error"
                failed += 1
                errors.append(f"{p['id']}: {type(e).__name__}: {e}")
        by_cat.setdefault(p.get("l2") or "other", []).append(row)

    # the ladder goes in the product's own file; the listing keeps only what a
    # category page draws, so opening a category costs kilobytes not hundreds
    written = 0
    for c in cats:
        rows = by_cat.get(c["l2"]) or []
        light = []
        for r in rows:
            cs = r.pop("contracts", None)
            # every product gets a file, listed or not: a contract the exchange
            # is not carrying still has a page, and that page has to be able to
            # say so rather than looking like a missing file
            store.put(PRODUCT_KEY.format(pid=r["id"]),
                      json.dumps(dict(r, contracts=cs or [], asof=now), separators=(",", ":")).encode(),
                      "application/json", CACHE)
            light.append(dict(r, strikes=len(cs or []),
                              expiries=sorted({x.get("expiryLabel") or x.get("spec") for x in (cs or []) if x})[:6]))
        doc = {"schema": SCHEMA, "asof": now, "l1": c["l1"], "l2": c["l2"], "slug": c["slug"],
               "page": c.get("page"), "products": light}
        store.put(CAT_KEY.format(slug=c["slug"]), json.dumps(doc, separators=(",", ":")).encode(),
                  "application/json", CACHE)
        written += 1
    index = {"schema": SCHEMA, "asof": now, "l1": reg.get("l1") or [],
             "categories": [dict(c, listed=sum(1 for r in (by_cat.get(c["l2"]) or []) if r.get("state") == "listed"))
                            for c in cats]}
    store.put(INDEX_KEY, json.dumps(index, separators=(",", ":")).encode(), "application/json", INDEX_CACHE)

    arch.LAST_STATUS = {"job": "catalogue", "errors": len(errors), "alarms": []}
    print(json.dumps({"kind": "catalogue", "products": len(products), "listed": found, "unlisted": unlisted,
                      "failed": failed, "categories": written, "errors": errors[:5],
                      "seconds": round(time.time() - t0, 1)}))
    return 1 if failed and not found else 0
