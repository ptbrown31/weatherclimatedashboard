"""
apply_overrides.py — bring what is already stored under the owner's price rulings.

The pipeline applies a ruling from pipeline/overrides/ on every write of a
storm's ledger and of a pool's series, so anything written from now on is
covered. What was stored before the ruling existed is not, until the next
delivery or quote pass rewrites it, and a storm that has stopped delivering
never gets that write. This applies every ruling once to the stored documents.

Idempotent: a second run finds nothing to change and writes nothing.

    python3 scripts/apply_overrides.py
    python3 scripts/apply_overrides.py --dry-run
"""
from __future__ import annotations
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import config, storage, reask    # noqa: E402
from pipeline.market import OVERRIDES_DIR         # noqa: E402
from pipeline.snapshots import SNAP_CACHE          # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    cfg = config.load()
    store = storage.from_config(cfg)
    report = {"kind": "apply-overrides", "ledgers": {}, "pools": {}, "dryRun": bool(args.dry_run)}
    for path in sorted(glob.glob(os.path.join(OVERRIDES_DIR, "*.json"))):
        with open(path, encoding="utf-8") as f:
            ov = json.load(f)
        name, year = ov.get("storm"), ov.get("year")
        key = reask.STORM_KEY.format(name=name, year=year)
        raw = store.get(key)
        if raw:
            doc = json.loads(raw)
            changed = reask.apply_price_override(doc, ov)
            report["ledgers"][key] = "changed" if changed else "already applied"
            if changed and not args.dry_run:
                store.put(key, json.dumps(doc, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
        else:
            report["ledgers"][key] = "no ledger stored"
        for sym, pts in (ov.get("pools") or {}).items():
            skey = f"snapshots/lhl/{sym}.json"
            try:
                cur = json.loads(store.get(skey) or b"null") or {}
            except (ValueError, TypeError):
                cur = {}
            same = cur.get("override") is True and cur.get("points") == pts
            report["pools"][sym] = "already applied" if same else "written"
            if same or args.dry_run:
                continue
            store.put(skey, json.dumps({"schema": 1, "symbol": sym, "name": cur.get("name"),
                                        "asof": pts[-1]["t"] if pts else None, "override": True, "points": pts},
                                       separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
