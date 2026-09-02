"""
reparse_metryc.py — re-derive a storm's interim step from the archived file.

The vendor lane keeps every file it fetched under archive/reask/, and the
ledger's interim step is derived from that file by the parser current at the
time. When the parser learns to read something it used to drop, the stored
step stays as it was until the vendor re-issues the file, which for a storm
that has finished never happens. This reads the archived interim back through
the current parser and re-appends the step, keeping the prices, the stamps
and the ruling the stored step already carried, so the result is the step the
job would have written had it known then what it knows now. The index's
interim rows are refreshed from the same step.

Idempotent: a second run rewrites the same step and changes nothing.

    python3 scripts/reparse_metryc.py
    python3 scripts/reparse_metryc.py --dry-run
"""
from __future__ import annotations
import argparse
import copy
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import config, storage, reask    # noqa: E402
from pipeline.snapshots import SNAP_CACHE          # noqa: E402


def _as_written(name, year, doc: dict, st: dict) -> dict:
    """The candidate step as append_step would leave it: pruned to the ledger's
    sites and under the owner's ruling, so a dry run compares like with like."""
    st = copy.deepcopy(st)
    reask._prune_interim(st, set(doc.get("sites") or {}))
    tmp = {"steps": [st], "sites": doc.get("sites") or {}}
    reask.apply_price_override(tmp, reask.price_override(name, year))
    return {k: v for k, v in st.items() if k != "thresholds"}


def reparse(store, dry_run: bool = False) -> dict:
    quiet = lambda **k: None
    try:
        index = json.loads(store.get(reask.KEY) or b"null") or {}
    except (ValueError, TypeError):
        index = {}
    report = {"kind": "reparse-metryc", "storms": {}, "dryRun": bool(dry_run)}
    changed_index = False
    for s in index.get("storms") or []:
        name, year, im = s.get("name"), s.get("year"), s.get("interim")
        if not (name and im and im.get("lastModified")):
            continue
        akey = "archive/reask/%s_%s/interim_%s.csv.gz" % (name, year, reask._stamp(im["lastModified"]) or "latest")
        raw = store.get(akey)
        if not raw:
            report["storms"][name] = "no archived interim at " + akey
            continue
        key = reask.STORM_KEY.format(name=name, year=year)
        doc = json.loads(store.get(key) or b"null") or {}
        stored = next((x for x in doc.get("steps") or [] if x.get("kind") == "interim"), None)
        if not stored:
            report["storms"][name] = "ledger has no interim step"
            continue
        parsed = reask.parse_ladder_csv(gzip.decompress(raw).decode("utf-8", "replace"), keep_zero=True)
        st = reask._step(parsed, "interim", "INT", stored.get("at", im["lastModified"]), stored.get("ts"), stored.get("prices"))
        if dry_run:
            cand = _as_written(name, year, doc, st)
            same = json.dumps(cand, sort_keys=True) == json.dumps(stored, sort_keys=True)
            report["storms"][name] = {"would": "unchanged" if same else "rewrite", "sites": sorted(cand["sites"])}
            continue
        got = reask.append_step(store, name, year, st, quiet)
        kept = set(got.get("kept") or [])
        im["sites"] = {k: v for k, v in (parsed.get("sites") or {}).items() if k in kept}
        im["rows"] = parsed.get("rows")
        changed_index = True
        report["storms"][name] = {"sites": sorted(kept), "steps": got["steps"]}
    if changed_index and index:
        store.put(reask.KEY, json.dumps(index, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    print(json.dumps(reparse(storage.from_config(config.load()), dry_run=args.dry_run)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
