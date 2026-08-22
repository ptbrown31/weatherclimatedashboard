"""
backfill_obs.py — fill the archive's observation record from aviationweather.gov.

The record normally grows from the 10-minute job. aviationweather.gov holds
30 days of history, so a gap (the days before the site went live, or a
stretch when the job was down) can be filled once from the feed, in 24-hour
windows ending at each UTC midnight, upserted exactly as the job does. Run
it soon after deploying: the 30-day window slides, and the scorecard can
only score days that have observations.

    python3 scripts/backfill_obs.py --from 2026-08-13 [--to 2026-08-21] [--backend s3 --bucket ...]
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import archive, config, gov_weather as gw, storage    # noqa: E402
from pipeline.cities import CITIES                                   # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", required=True, help="first UTC day, YYYY-MM-DD")
    ap.add_argument("--to", dest="end", help="last UTC day (default: yesterday)")
    ap.add_argument("--backend", choices=["local", "s3"])
    ap.add_argument("--root")
    ap.add_argument("--bucket")
    ap.add_argument("--endpoint")
    ap.add_argument("--prefix")
    args = ap.parse_args(argv)
    for flag, var in (("backend", "WX_STORAGE_BACKEND"), ("root", "WX_STORAGE_ROOT"),
                      ("bucket", "WX_STORAGE_BUCKET"), ("endpoint", "WX_STORAGE_ENDPOINT"),
                      ("prefix", "WX_STORAGE_PREFIX")):
        if getattr(args, flag) is not None:
            os.environ[var] = os.path.abspath(getattr(args, flag)) if flag == "root" else getattr(args, flag)

    cfg = config.load()
    store = storage.from_config(cfg)
    gw.set_user_agent(cfg.get("user_agent", ""))
    stations = [c[0] for c in CITIES]
    today = dt.datetime.now(dt.timezone.utc).date()
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end) if args.end else today - dt.timedelta(days=1)
    if (today - start).days > 30:
        print("aviationweather.gov holds 30 days; start is older than that", file=sys.stderr)
        return 1

    day = start
    total_added = total_updated = 0
    while day <= end:
        window_end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0), tzinfo=dt.timezone.utc)
        truncated: list = []
        rows = gw.fetch_observations_raw(stations, 24, on_truncated=truncated.append, end=window_end)
        for ids in truncated:
            print(f"  WARNING {day}: batch {ids} hit the 400-row cap", file=sys.stderr)
        key = f"archive/obs/{day.strftime('%Y%m%d')}.json.gz"
        raw = store.get(key)
        existing = json.loads(archive._ungz(raw)) if raw else {
            "utc_day": day.strftime("%Y%m%d"),
            "source": "aviationweather.gov /api/data/metar, format=json (backfill)",
            "decode": {"TEMP_SOURCE": gw.TEMP_SOURCE, "INCLUDE_SPECI": gw.INCLUDE_SPECI,
                       "note": "temp is tenths C when temp_source=tgroup, whole degrees C when body"},
            "rows": {}}
        # a 24-hour window ending at midnight can include the previous day's
        # last report; keep only rows whose obsTime falls on this UTC day
        day_rows = [ob for ob in rows if ob.get("obsTime") is not None
                    and dt.datetime.fromtimestamp(ob["obsTime"], dt.timezone.utc).date() == day]
        merged, added, updated = archive.merge_obs_rows(existing, day_rows)
        if added or updated:
            merged["updated"] = dt.datetime.now(dt.timezone.utc).isoformat()
            store.put(key, archive._gz(json.dumps(merged, separators=(",", ":")).encode()), "application/gzip")
        total_added += added
        total_updated += updated
        print(f"  {day}: {len(day_rows)} rows fetched, {added} added, {updated} updated, "
              f"{len({r.get('icaoId') for r in day_rows})} stations")
        day += dt.timedelta(days=1)
        time.sleep(1.0)                   # well under the documented 100 requests a minute
    print(f"backfill: {total_added} added, {total_updated} updated -> {store.describe()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
