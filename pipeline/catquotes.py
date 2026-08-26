"""Prices for the catalogue's contracts.

The ten-minute quote lane covers the markets the displays draw minute to
minute: the daily temperature board, the hurricane group, the climate group.
Everything else in the catalogue is monthly or annual — a crop yield, a year's
carbon emissions, a month of electricity generation — and none of it moves on a
ten-minute clock. So those are quoted on the half-hour instead, in their own
lane, and the fast lane is left alone.

That split is what makes the coverage affordable: about 2,500 contracts at one
call each is around two and a half minutes, which is unremarkable every thirty
minutes and would be a permanent drag every ten.

Prices are written beside the catalogue rather than into it, because terms and
prices have different lifetimes: the ladder is read once a day and a quote every
half hour, and a page fetches whichever it needs.

A pass that runs out of budget writes what it has and says how much it left,
rather than replacing a full set of prices with a partial one.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional

from . import archive as arch
from . import exchange as ex
from .storage import Storage

SCHEMA = 1
PRICE_KEY = "snapshots/catalogue/price/{pid}.json"
INDEX_KEY = "snapshots/catalogue/price/index.json"
CACHE = "public, max-age=300, stale-while-revalidate=3600, stale-if-error=86400"

# categories the fast lane already covers; quoting them again would be the same
# numbers fetched twice
SKIP_CATEGORIES = ("Daily Temperatures", "Tropical Cyclones")
# the longest real ladder is 132 strikes, so this is a runaway guard rather than
# a routine cap; a product that hits it is quoted to the cap and the page says so
MAX_PER_PRODUCT = 150
# a run of this many consecutive empty books means the exchange has stopped
# answering, not that the market is empty; measured against a full pass, where
# throttling produced 2,477 consecutive empties and would have published every
# ladder as "no bids"
EMPTY_RUN_LIMIT = 250
# The exchange stops answering after roughly twelve hundred quote calls in a
# burst, so a pass takes a slice and the next pass carries on from where it
# stopped. The whole catalogue comes round every few passes, which on monthly
# and annual contracts is far more freshness than they need, and no pass ever
# publishes a throttle as an absence of bids.
BUDGET_PER_PASS = 900
# the same four the fast lane measured as safe against this exchange; serially
# this pass took ten minutes to reach forty products, which is not a lane


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


KEEP = ("strike", "numeric", "label", "expiration", "expiryLabel", "spec")


def quote_one(job, fetch: Callable, deadline: arch.Deadline):
    """One strike. Yes and No come from whichever contract has a book, the same
    way the rest of the site does it: the Yes book when it has either side,
    otherwise the No contract's bids complemented. Nothing is fee adjusted and
    nothing is interpolated."""
    pid, c = job
    base = {k: c.get(k) for k in KEEP}
    if deadline.over(10):
        return pid, None
    qy = qn = None
    try:
        if c.get("conidYes"):
            qy = fetch(c["conidYes"])
        if (not qy or qy.get("bid") is None) and c.get("conidNo"):
            qn = fetch(c["conidNo"])
    except Exception:  # noqa: BLE001
        return pid, {**base, "error": "quote failed"}
    q = ex.yes_quote(qy, qn)
    # the Yes price the pages draw. yes_quote returns the book; the midpoint is
    # a separate step, and it is the same one the fast lane uses so the two
    # lanes cannot drift apart on what a price means.
    q["mid"] = ex.mid(q)
    return pid, {**base, **q, "conidYes": c.get("conidYes")}


def catquotes_pass(cfg: dict, store: Storage, fetch: Optional[Callable] = None) -> int:
    t0 = time.time()
    now = _iso(dt.datetime.now(dt.timezone.utc))
    fetch = fetch or ex.fetch_quote
    reg = cfg.get("contracts") or {}
    cats = reg.get("categories") or []
    if not cats:
        print(json.dumps({"kind": "catquotes", "written": False, "reason": "no contract registry",
                          "hint": "config/contracts.json is not on the path"}))
        arch.LAST_STATUS = {"job": "catquotes", "errors": 1, "alarms": ["catquotes: no contract registry"]}
        return 1

    deadline = arch.Deadline(arch.remaining_budget(cfg))
    workers = int(((cfg.get("exchange") or {}).get("quote_workers")) or 4)
    errors: List[str] = []
    done = quoted = skipped = partial = 0
    index: Dict[str, dict] = {}
    jobs: List[tuple] = []
    meta: Dict[str, dict] = {}
    order: List[str] = []
    pending: Dict[str, dict] = {}

    for c in cats:
        if c["l2"] in SKIP_CATEGORIES:
            continue
        raw = store.get("snapshots/catalogue/{slug}.json".format(slug=c["slug"]))
        if not raw:
            errors.append(f"{c['slug']}: no catalogue listing yet")
            continue
        try:
            listing = json.loads(raw)
        except ValueError:
            errors.append(f"{c['slug']}: unreadable listing")
            continue
        for light in listing.get("products") or []:
            pid = light.get("id")
            if light.get("state") != "listed":
                skipped += 1
                continue
            praw = store.get("snapshots/catalogue/product/{pid}.json".format(pid=pid))
            if not praw:
                continue
            try:
                prod = json.loads(praw)
            except ValueError:
                continue
            cs = (prod.get("contracts") or [])[:MAX_PER_PRODUCT]
            order.append(pid)
            pending[pid] = {"contracts": cs,
                            "dropped": max(0, len(prod.get("contracts") or []) - len(cs))}

    # take the slice that starts after whatever the last pass finished on, so
    # every product comes round without any pass running into the throttle
    prev = {}
    praw = store.get(INDEX_KEY)
    if praw:
        try:
            prev = json.loads(praw)
        except ValueError:
            prev = {}
    start = 0
    after = prev.get("cursor")
    if after in order:
        start = order.index(after) + 1
    rotated = order[start:] + order[:start]
    taken, budget = [], 0
    for pid in rotated:
        n = len(pending[pid]["contracts"])
        if taken and budget + n > BUDGET_PER_PASS:
            break
        taken.append(pid)
        budget += n
    for pid in taken:
        meta[pid] = {"dropped": pending[pid]["dropped"]}
        jobs.extend((pid, c) for c in pending[pid]["contracts"])
    cursor = taken[-1] if taken else after

    # one pool over every strike in the catalogue, rather than a pool per product:
    # the work is one small request each and the win is in keeping four in flight
    #
    # The exchange answers an empty book and a throttled request identically, so
    # a long unbroken run of empty answers is treated as the exchange saying
    # stop rather than as thousands of contracts genuinely having no bids. A
    # whole category priced at "no bids" is a far worse thing to publish than a
    # short pass, so the run ends and keeps what it had.
    results: Dict[str, List[dict]] = {}
    empties = 0
    throttled = False
    for pid, row in _mapped(jobs, fetch, deadline, workers):
        if row is None:
            meta.setdefault(pid, {}).setdefault("dropped", 0)
            meta[pid]["dropped"] += 1
            continue
        if row.get("mid") is None and not row.get("error"):
            empties += 1
            if empties >= EMPTY_RUN_LIMIT:
                throttled = True
                break
        else:
            empties = 0
        results.setdefault(pid, []).append(row)

    for pid, rows in results.items():
        if not rows:
            continue
        dropped = meta.get(pid, {}).get("dropped", 0)
        with_bids = sum(1 for r in rows if r.get("mid") is not None)
        doc = {"schema": SCHEMA, "id": pid, "asof": now, "rows": rows,
               "dropped": dropped, "quoted": len(rows), "withBids": with_bids}
        store.put(PRICE_KEY.format(pid=pid), json.dumps(doc, separators=(",", ":")).encode(),
                  "application/json", CACHE)
        done += 1
        quoted += len(rows)
        if dropped:
            partial += 1
        index[pid] = {"asof": now, "quoted": len(rows), "withBids": with_bids}

    if throttled:
        errors.append(f"stopped after {EMPTY_RUN_LIMIT} consecutive empty books; the exchange was not answering")
    # the index accumulates: this pass refreshed a slice, and the products it did
    # not touch keep the times they were last quoted at
    merged = dict(prev.get("products") or {})
    merged.update(index)
    store.put(INDEX_KEY, json.dumps({"schema": SCHEMA, "asof": now, "products": merged,
                                     "throttled": throttled, "cursor": cursor,
                                     "passProducts": len(taken), "totalProducts": len(order)},
                                    separators=(",", ":")).encode(), "application/json", CACHE)
    arch.LAST_STATUS = {"job": "catquotes", "errors": len(errors),
                        "alarms": ["catquotes: exchange stopped answering"] if throttled else []}
    print(json.dumps({"kind": "catquotes", "products": done, "contracts": quoted, "unlisted": skipped,
                      "partial": partial, "throttled": throttled, "errors": errors[:5],
                      "seconds": round(time.time() - t0, 1)}))
    return 1 if (throttled or (errors and not done)) else 0


def _mapped(jobs, fetch, deadline, workers):
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for out in pool.map(lambda j: quote_one(j, fetch, deadline), jobs):
            yield out
