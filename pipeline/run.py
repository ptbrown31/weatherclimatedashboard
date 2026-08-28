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
    quotes       exchange top of book: station ladders, hurricane
                 and climate products, market archive              (every 10 min)
    reask        vendor live-storm wind probabilities (gated off)  (with quotes)
    scorecard    forecast errors scored against the observed day   (once a day)
    normals      NCEI daily climate normals per station            (once a day, refreshed weekly)
    climate      the long global series behind the climate page    (once a day)
    season       Atlantic season so far against the average pace   (once a day)
    headline     the landing page's few numbers, from the snapshots
                 already written; no network                       (last in a chain)
    market       quotes, then reask, then headline: one scheduled invocation
    half-hourly  archive, then forecast, then hurricane: one scheduled invocation
    traffic      yesterday's page views, from the CDN access logs      (once a day)
    catalogue    what the exchange lists for every product in the registry (once a day)
    series       the underlying series the monthly weather contracts settle on (once a day)
    discussion   the forecast office's own written reasoning, per office   (once a day)
    subhourly    the five minute stream between the hourly reports    (every 10 min)
    locator      a metro-scale map of each station, fetched once        (once a day)
    catquotes    prices for the catalogue's monthly and annual contracts   (every 30 min)
    daily        scorecard, normals, climate, season, catalogue, headline, traffic: one scheduled invocation
    all          everything once, in order (local runs)
"""
from __future__ import annotations
import argparse
import os
import sys

from . import config, storage


JOBS = {}


def _register():
    from . import archive, snapshots, hurricane, scorecard, normals, climate, season, market, reask, headline, traffic, catalogue, series, catquotes, discussion, subhourly, locator, report
    JOBS["archive"] = archive.one_pass
    JOBS["forecast"] = snapshots.forecast_pass
    JOBS["obs"] = snapshots.obs_pass
    JOBS["hurricane"] = hurricane.hurricane_pass
    JOBS["scorecard"] = scorecard.scorecard_pass
    JOBS["normals"] = normals.normals_pass
    JOBS["climate"] = climate.climate_pass
    JOBS["season"] = season.season_pass
    JOBS["quotes"] = market.quotes_pass
    JOBS["reask"] = reask.reask_pass
    JOBS["headline"] = headline.headline_pass
    JOBS["traffic"] = traffic.traffic_pass
    JOBS["report"] = report.report_pass
    JOBS["catalogue"] = catalogue.catalogue_pass
    JOBS["series"] = series.series_pass
    JOBS["discussion"] = discussion.discussion_pass
    JOBS["subhourly"] = subhourly.subhourly_pass
    JOBS["locator"] = locator.locator_pass
    JOBS["catquotes"] = catquotes.catquotes_pass

    def chain(*names):
        # one absolute deadline for the whole chain; the archive step (the
        # only one that downloads bulk bulletins) reserves time for the rest
        def run(cfg, store):
            import time
            budget = cfg.get("pass_budget_seconds")
            if budget:
                cfg["_deadline_end"] = time.time() + budget
            worst = 0
            alarms: list = []
            statuses: list = []
            for i, n in enumerate(names):
                cfg["_reserve_seconds"] = 240.0 if (n == "archive" and i < len(names) - 1) else 0.0
                worst = max(worst, JOBS[n](cfg, store))
                # each job leaves its own status; the chain keeps every alarm raised along the way
                last = archive.LAST_STATUS or {}
                alarms += [a for a in (last.get("alarms") or []) if a not in alarms]
                statuses.append({k: v for k, v in last.items() if k != "entries"})
            archive.LAST_STATUS = {"job": "+".join(names), "alarms": alarms, "jobs": statuses}
            return worst
        return run

    # headline reads snapshots the steps before it have just written, so it
    # goes last in both chains: after quotes for fresh prices, after the
    # scorecard for the day just scored
    JOBS["half-hourly"] = chain("archive", "forecast", "hurricane", "catquotes", "subhourly")
    # report last, so it reads the day traffic has just counted
    JOBS["daily"] = chain("scorecard", "normals", "climate", "season", "catalogue", "series", "discussion", "locator", "headline", "traffic", "report")
    JOBS["market"] = chain("quotes", "reask", "headline")
    JOBS["all"] = chain("archive", "forecast", "obs", "hurricane", "quotes", "reask", "scorecard", "normals", "climate", "season", "headline")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline.run", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--job", required=True, help="archive | forecast | obs | hurricane | quotes | reask | scorecard | normals | climate | season | headline | market | half-hourly | daily | all")
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
