"""
apply_overrides.py — bring what is already stored under the owner's price rulings.

The pipeline applies a ruling from pipeline/overrides/ on every write of a
storm's ledger and of a pool's series, so anything written from now on is
covered. What was stored before the ruling existed is not, until the next
delivery or quote pass rewrites it, and a storm that has stopped delivering
never gets that write. This applies every ruling once to the stored documents:

  * a storm the ruling absorbs is folded into the ruled storm's ledger, its
    own ledger copied under the archive and then removed, and it leaves the
    vendor index;
  * the ruled storm's recorded prices and pool figures become the ruling's;
  * the ruled pools' series become the ruling's points;
  * the vendor index shows the ruling's pool figure for the ruled storm.

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
    report = {"kind": "apply-overrides", "absorbed": {}, "ledgers": {}, "pools": {}, "index": {}, "dryRun": bool(args.dry_run)}
    quiet = lambda **k: None
    try:
        index = json.loads(store.get(reask.KEY) or b"null") or {}
    except (ValueError, TypeError):
        index = {}
    index_changed = False
    for path in sorted(glob.glob(os.path.join(OVERRIDES_DIR, "*.json"))):
        with open(path, encoding="utf-8") as f:
            ov = json.load(f)
        name, year = ov.get("storm"), ov.get("year")
        for nm in ov.get("absorbs") or []:
            src_key = reask.STORM_KEY.format(name=nm, year=year)
            if store.get(src_key):
                report["absorbed"][nm] = "folded into %s" % name if not args.dry_run else "would fold into %s" % name
                if not args.dry_run:
                    report["absorbed"][nm] = reask.absorb_ledger(store, nm, year, name, quiet)
            else:
                report["absorbed"][nm] = "already gone"
            before = len(index.get("storms") or [])
            index["storms"] = [s for s in index.get("storms") or []
                               if not (s.get("name") == nm and str(s.get("year")) == str(year))]
            index_changed = index_changed or len(index["storms"]) != before
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
        ruled_pw = reask.latest_override_pwin(ov)
        for s in index.get("storms") or []:
            if s.get("name") == name and str(s.get("year")) == str(year) and s.get("livecyc") and ruled_pw is not None:
                if s["livecyc"].get("pwin") != ruled_pw:
                    s["livecyc"]["pwin"] = ruled_pw
                    s["livecyc"]["pwinMethod"] = "override"
                    index_changed = True
    report["index"] = "changed" if index_changed else "already applied"
    if index_changed and not args.dry_run and index:
        store.put(reask.KEY, json.dumps(index, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
