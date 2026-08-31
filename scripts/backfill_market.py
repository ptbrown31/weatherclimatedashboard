"""
backfill_market.py — import quote passes captured before this site's own began.

The exchange lane starts the day the quote job was stood up, and the scorecard
compares tools on a matched sample, so that date bounded the window every tool
was judged over. An earlier capture of the same public endpoint exists; this
imports passes from it in the format the quote job writes, so the scorecard
reads them exactly as it reads its own.

The passes must be whole. A pass is what the exchange showed at one instant,
and a selection from one would let a strike that was unquoted look absent
instead of unquoted, which is the difference between no market and no capture.

Input is a gzipped JSON file, {"passes": [{"asof", "rows": [...]}, ...]}, with
rows carrying station, day, side, strike, conid, bid, ask. Export it from the
earlier system yourself; this script never names a host. Nothing is
overwritten: a pass this site captured itself always wins.

    python3 scripts/backfill_market.py passes.json.gz
    python3 scripts/backfill_market.py passes.json.gz --dry-run
"""
from __future__ import annotations
import argparse
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import config, storage        # noqa: E402
from scripts.scrub import needles, _real    # noqa: E402

FIELDS = ("station", "day", "side", "strike", "conid", "bid", "ask", "bidSize", "askSize")


def key_for(asof: str) -> str:
    """archive/market/{YYYYMMDD}/{HHMMSS}.json.gz, the quote job's own layout."""
    d = asof.replace("-", "").replace(":", "")
    return "archive/market/%s/%s.json.gz" % (d[:8], d[9:15])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="the exported passes, gzipped JSON")
    ap.add_argument("--dry-run", action="store_true", help="report what would be written")
    ap.add_argument("--overwrite", action="store_true", help="replace a pass this site already holds")
    args = ap.parse_args(argv)

    cfg = config.load()
    store = storage.from_config(cfg)
    with gzip.open(args.path, "rt") as fh:
        passes = json.load(fh)["passes"]

    # the same scan every other import runs, through the same test scrub.scan
    # applies. _real is what keeps a needle that looks like a port from matching
    # a number that merely follows a colon, which a quote size does
    builtin, ext, allowed = needles(require_external=True)
    blob = json.dumps(passes, separators=(",", ":"))
    hit = [n for n in builtin + ext
           if n in blob and _real(n, blob) and not any(a in blob for a in allowed)]
    if hit:
        print("refusing: the export carries %d forbidden string(s): %s"
              % (len(hit), ", ".join(repr(n) for n in hit[:3])), file=sys.stderr)
        return 2

    written = existed = 0
    rows_in = 0
    for p in passes:
        asof = p["asof"]
        key = key_for(asof)
        if not args.overwrite and store.get(key) is not None:
            existed += 1                    # this site's own capture of that instant stands
            continue
        rows = [{k: r.get(k) for k in FIELDS} for r in p["rows"]]
        rows_in += len(rows)
        body = {"asof": asof, "rows": rows, "backfilled": True}
        if args.dry_run:
            print(f"  would write {key}: {len(rows)} rows")
        else:
            store.put(key, gzip.compress(json.dumps(body, separators=(",", ":")).encode()), "application/gzip")
        written += 1

    print(json.dumps({"kind": "backfill-market", "passes": len(passes), "written": written,
                      "alreadyHeld": existed, "rows": rows_in, "dryRun": bool(args.dry_run)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
