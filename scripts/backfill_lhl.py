"""
backfill_lhl.py — rebuild a highest-wind pool's price series from the archive.

The quote job keeps a small per-pool series so a page does not have to read a
season of gzipped passes, but that series only begins when the job first wrote
it. The passes themselves go back to the pool's listing, one every ten minutes,
each carrying every quote of that moment, so the real history is already held
and only needs assembling.

Rebuilt, not appended: each run reads the archive over the days asked for and
writes the series it finds, so running it twice leaves the same file. Points
are keyed by the contract's label, the place name, taken from the current
market snapshot, because the archived rows carry the strike index rather than
the name and a chart legend reading "2.0" names nothing.

    python3 scripts/backfill_lhl.py --days 3
    python3 scripts/backfill_lhl.py --days 3 --dry-run
"""
from __future__ import annotations
import argparse
import datetime as dt
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import config, storage    # noqa: E402
from pipeline.market import LHL_MAX_POINTS    # noqa: E402
from pipeline.snapshots import SNAP_CACHE     # noqa: E402

PREFIX = "archive/market/"


def labels(store) -> dict:
    """{symbol: {strike: label}} for every listed pool, from the live group."""
    try:
        doc = json.loads(store.get("snapshots/market/hurricane.json") or b"null") or {}
    except (ValueError, TypeError):
        return {}
    out = {}
    for m in doc.get("markets") or []:
        sym = str(m.get("symbol") or "")
        if not sym.startswith("LHL"):
            continue
        out[sym] = {str(c.get("strike")): (c.get("label") or str(c.get("strike")))
                    for c in m.get("contracts") or [] if c.get("strike") is not None}
    return out


def book(r: dict):
    """Both sides of an archived row, in cents: the Yes bid and the Yes ask,
    the second of which the pages show as one dollar less the No bid. Both,
    rather than a midpoint, so a price on a page can be checked against the
    exchange's own screen, which publishes the two bids."""
    b, a = r.get("bid"), r.get("ask")
    if b is None and a is None:
        return None
    return [None if b is None else round(float(b) * 100, 1),
            None if a is None else round(float(a) * 100, 1)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=3, help="how many days back to read")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    cfg = config.load()
    store = storage.from_config(cfg)
    names = labels(store)
    if not names:
        print(json.dumps({"kind": "backfill-lhl", "pools": 0, "note": "no pool listed"}))
        return 0

    today = dt.datetime.now(dt.timezone.utc).date()
    series = {sym: {} for sym in names}
    passes = 0
    for back in range(args.days):
        day = (today - dt.timedelta(days=back)).strftime("%Y%m%d")
        for key in sorted(store.list(f"{PREFIX}{day}/")):
            raw = store.get(key)
            if not raw:
                continue
            try:
                body = json.loads(gzip.decompress(raw))
            except (OSError, ValueError):
                continue
            passes += 1
            asof = body.get("asof")
            for r in body.get("rows") or []:
                sym = r.get("market")
                if sym not in names or r.get("strike") is None:
                    continue
                v = book(r)
                if v is None:
                    continue
                nm = names[sym].get(str(r["strike"]), str(r["strike"]))
                series[sym].setdefault(asof, {})[nm] = v

    out = {}
    for sym, byt in series.items():
        pts = [{"t": t, "p": p} for t, p in sorted(byt.items()) if p][-LHL_MAX_POINTS:]
        out[sym] = len(pts)
        if not pts or args.dry_run:
            continue
        store.put(f"snapshots/lhl/{sym}.json",
                  json.dumps({"schema": 1, "symbol": sym, "asof": pts[-1]["t"], "points": pts},
                             separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
    print(json.dumps({"kind": "backfill-lhl", "passesRead": passes, "points": out,
                      "dryRun": bool(args.dry_run)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
