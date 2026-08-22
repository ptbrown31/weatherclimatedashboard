"""
Command-line entrypoint for every scheduled job.

    python3 -m pipeline.run --job archive
    python3 -m pipeline.run --job archive --backend local --root ./data
    WX_STORAGE_BACKEND=s3 WX_STORAGE_BUCKET=my-bucket python3 -m pipeline.run --job archive

Jobs: archive (every forecast cycle and the observation record). The snapshot
jobs (obs, forecast, summary, hurricane, daily) are added by later tasks and
register here.
"""
from __future__ import annotations
import argparse
import os
import sys

from . import config, storage


JOBS = {}


def _register():
    from . import archive
    JOBS["archive"] = archive.one_pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline.run", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--job", required=True, help="archive")
    ap.add_argument("--config", help="path to site.json (default config/site.json)")
    ap.add_argument("--backend", choices=["local", "s3"], help="override storage backend")
    ap.add_argument("--root", help="local backend: data directory")
    ap.add_argument("--bucket", help="s3 backend: bucket name")
    ap.add_argument("--endpoint", help="s3 backend: endpoint URL (R2)")
    ap.add_argument("--prefix", help="s3 backend: key prefix")
    args = ap.parse_args(argv)

    # Command-line flags become the same environment overrides config.py reads,
    # so there is exactly one precedence order: flag > env > site.json.
    for flag, var in (("backend", "WX_STORAGE_BACKEND"), ("root", "WX_STORAGE_ROOT"),
                      ("bucket", "WX_STORAGE_BUCKET"), ("endpoint", "WX_STORAGE_ENDPOINT"),
                      ("prefix", "WX_STORAGE_PREFIX")):
        val = getattr(args, flag)
        if val is not None:
            os.environ[var] = val

    _register()
    if args.job not in JOBS:
        ap.error(f"unknown job {args.job!r}; known: {', '.join(sorted(JOBS))}")
    cfg = config.load(args.config)
    store = storage.from_config(cfg)
    return JOBS[args.job](cfg, store)


if __name__ == "__main__":
    sys.exit(main())
