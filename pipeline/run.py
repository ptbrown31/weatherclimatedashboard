"""
Command-line entrypoint for every scheduled job.

    python3 -m pipeline.run --job archive
    python3 -m pipeline.run --job archive --backend local --root ./data
    WX_STORAGE_BACKEND=s3 WX_STORAGE_BUCKET=my-bucket python3 -m pipeline.run --job archive

Jobs:
    archive      every forecast cycle, stored raw                  (every 30 min, first)
    forecast     per-station forecast snapshots + the map field    (after archive)
    obs          observation record + per-station obs snapshots
                 + summary.json + manifest.json                    (every 10 min)
    hurricane    storms, cones, outlook, season counts             (every 30 min)
    half-hourly  archive, then forecast, then hurricane: one scheduled invocation
    all          everything once, in order (local runs)
"""
from __future__ import annotations
import argparse
import os
import sys

from . import config, storage


JOBS = {}


def _register():
    from . import archive, snapshots, hurricane, scorecard, normals, climate
    JOBS["archive"] = archive.one_pass
    JOBS["forecast"] = snapshots.forecast_pass
    JOBS["obs"] = snapshots.obs_pass
    JOBS["hurricane"] = hurricane.hurricane_pass
    JOBS["scorecard"] = scorecard.scorecard_pass
    JOBS["normals"] = normals.normals_pass
    JOBS["climate"] = climate.climate_pass

    def chain(*names):
        def run(cfg, store):
            worst = 0
            for n in names:
                worst = max(worst, JOBS[n](cfg, store))
            return worst
        return run

    JOBS["half-hourly"] = chain("archive", "forecast", "hurricane")
    JOBS["daily"] = chain("scorecard", "normals", "climate")
    JOBS["all"] = chain("archive", "forecast", "obs", "hurricane", "scorecard", "normals", "climate")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline.run", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--job", required=True, help="archive | forecast | obs | hurricane | half-hourly | all")
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
            # a --root given on the command line is relative to the working
            # directory, like any other tool; only site.json's value is repo-relative
            os.environ[var] = os.path.abspath(val) if flag == "root" else val

    _register()
    if args.job not in JOBS:
        ap.error(f"unknown job {args.job!r}; known: {', '.join(sorted(JOBS))}")
    cfg = config.load(args.config)
    store = storage.from_config(cfg)
    return JOBS[args.job](cfg, store)


if __name__ == "__main__":
    sys.exit(main())
