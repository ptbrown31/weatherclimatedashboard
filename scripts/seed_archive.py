"""
seed_archive.py — one-time import of an existing forecast archive.

An archive written by the earlier reference archiver has the same per-station
layout this pipeline uses (hourly_*.json.gz, daily_*.json.gz, nbh_*.txt.gz),
so seeding is a copy into the configured storage backend. Run-log and marker
files are not copied. Every file is decompressed and scanned with the scrub
needles before it is written, and nothing is ever overwritten. Bulletin
cycles that arrive with full station coverage get their completion marker,
so the archive job does not try to fetch them again.

    python3 scripts/seed_archive.py /path/to/archive
    python3 scripts/seed_archive.py /path/to/archive --backend s3 --bucket my-bucket

Fetch the source directory yourself first (for example with rsync over ssh);
this script never names a host.
"""
from __future__ import annotations
import argparse
import gzip
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import archive, config, storage    # noqa: E402
from scripts.scrub import needles                 # noqa: E402

KEEP_PREFIX = ("hourly_", "daily_", "nbh_", "nbs_", "lamp_", "mav_")
BULLETINS = ("nbh", "nbs", "lamp", "mav")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="directory holding {STATION}/hourly_*.json.gz etc.")
    ap.add_argument("--backend", choices=["local", "s3"])
    ap.add_argument("--root")
    ap.add_argument("--bucket")
    ap.add_argument("--endpoint")
    ap.add_argument("--prefix")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-external", action="store_true", help="proceed without the external scrub list")
    args = ap.parse_args(argv)
    for flag, var in (("backend", "WX_STORAGE_BACKEND"), ("root", "WX_STORAGE_ROOT"),
                      ("bucket", "WX_STORAGE_BUCKET"), ("endpoint", "WX_STORAGE_ENDPOINT"),
                      ("prefix", "WX_STORAGE_PREFIX")):
        if getattr(args, flag) is not None:
            os.environ[var] = os.path.abspath(getattr(args, flag)) if flag == "root" else getattr(args, flag)

    cfg = config.load()
    store = storage.from_config(cfg)
    try:
        builtin, ext, allowed = needles(require_external=not args.no_external)
    except FileNotFoundError as e:
        print(f"refusing to seed without the external scrub list {e}; pass --no-external deliberately", file=sys.stderr)
        return 2
    ndl = builtin + ext
    copied = skipped = rejected = 0
    coverage: dict = {}
    for sid in sorted(os.listdir(args.source)):
        sdir = os.path.join(args.source, sid)
        if not os.path.isdir(sdir) or not sid.isalnum() or len(sid) != 4:
            continue
        for fn in sorted(os.listdir(sdir)):
            if not fn.startswith(KEEP_PREFIX) or not fn.endswith(".gz"):
                continue
            path = os.path.join(sdir, fn)
            with open(path, "rb") as fh:
                data = fh.read()
            try:
                text = gzip.decompress(data).decode("utf-8", "replace")
            except Exception:
                rejected += 1
                print(f"  reject (not gzip): {sid}/{fn}")
                continue
            bad = [] if any(a in text for a in allowed) else [n for n in ndl if n in text]
            if bad:
                rejected += 1
                print(f"  reject (scrub {bad[0]!r}): {sid}/{fn}")
                continue
            key = f"archive/{sid}/{fn}"
            source, stamp = fn.split("_", 1)[0], fn.split("_", 1)[1].split(".", 1)[0]
            if source in BULLETINS:
                coverage.setdefault((source, stamp), set()).add(sid)
            if args.dry_run:
                if store.exists(key):
                    skipped += 1
                else:
                    copied += 1
                continue
            if store.put_if_absent(key, data, "application/gzip"):
                copied += 1
            else:
                skipped += 1
    markers = 0
    for (source, stamp), sids in sorted(coverage.items()):
        if len(sids) >= archive.expected_coverage(source):
            key = f"archive/_done/{source}_{stamp}"
            if not args.dry_run and store.put_if_absent(key, archive.marker_body(len(sids)), "application/json"):
                markers += 1
            elif args.dry_run and not store.exists(key):
                markers += 1
    print(f"seed: {copied} copied, {skipped} already present, {rejected} rejected, "
          f"{markers} cycle markers -> {store.describe()}" + (" (dry run)" if args.dry_run else ""))
    return 1 if rejected else 0


if __name__ == "__main__":
    sys.exit(main())
