"""
make_samples.py — copy the current snapshots into samples/ for offline local mode.

A clean checkout must render both build targets with no network and no
credentials, so a real set of snapshots is checked in. Run after the jobs
have written data/snapshots/ (or point --from at another data root).

    python3 scripts/make_samples.py [--from data]
"""
import argparse
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src", default=os.path.join(ROOT, "data"))
    args = ap.parse_args(argv)
    src = os.path.join(args.src, "snapshots")
    dst = os.path.join(ROOT, "samples", "snapshots")
    if not os.path.isdir(src):
        print(f"no snapshots at {src}; run the jobs first", file=sys.stderr)
        return 1
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    n = sum(len(f) for _, _, f in os.walk(dst))
    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(dst) for f in fs)
    print(f"samples: {n} files, {size / 1e6:.1f} MB -> {os.path.relpath(dst, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
