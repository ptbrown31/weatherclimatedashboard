"""
backfill_guidance.py — fill the MOS and LAMP lanes for days before the archive.

The scorecard compares tools on a matched sample, so the window it can report
is the shortest lane's, and two lanes are short: this site began archiving the
GFS MOS and LAMP bulletins when it was stood up, while the Service and Blend
lanes were seeded from an older archive and reach further back. NOMADS cannot
supply the missing cycles, because it keeps about two days.

What is written is a level record per cycle, not a bulletin. The values come
from IEM's copy of the same bulletins, the day mapping is the site's own, and
each record names IEM as its source, so the scorecard can prefer what this
site captured and fall back to these only where it captured nothing. A row
scored from one says so, and the page says so.

    python3 scripts/backfill_guidance.py --from 2026-08-17 --to 2026-08-23
    python3 scripts/backfill_guidance.py --from 2026-08-17 --to 2026-08-23 --dry-run

Days the archive already covers are skipped; pass --force to write anyway.
"""
from __future__ import annotations
import argparse
import datetime as dt
import gzip
import json
import os
import sys
import time
import zoneinfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import config, iem, snapshots as sn, storage    # noqa: E402
from pipeline.scorecard import ANCHOR_LOCAL_HOUR              # noqa: E402

# how many cycles before the anchor to fetch. pick_levels takes the high from
# the newest cycle carrying one and the low from the newest carrying that, which
# is often the run before, so one cycle is not always enough
DEPTH = {"mav": 3, "lamp": 2}
MAV_HOURS = (0, 6, 12, 18)
PACE = 0.15          # a free academic service; ask for one cycle at a time


def us_cities(cfg: dict) -> list:
    rows = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                       "config", "cities.json")))
    return [c for c in rows if c["station"].startswith("K") or c["station"] == "PHNL"]


def anchor_utc(day: dt.date, tz) -> dt.datetime:
    midnight = dt.datetime.combine(day, dt.time(0), tzinfo=tz)
    return (midnight - dt.timedelta(hours=24 - ANCHOR_LOCAL_HOUR)).astimezone(dt.timezone.utc)


def cycles_before(kind: str, at: dt.datetime) -> list:
    """The cycles of one kind standing at `at`, newest first."""
    if kind == "lamp":                      # hourly, on the hour
        top = at.replace(minute=0, second=0, microsecond=0)
        return [top - dt.timedelta(hours=i) for i in range(DEPTH[kind])]
    out, t = [], at.replace(minute=0, second=0, microsecond=0)
    while len(out) < DEPTH[kind]:
        if t.hour in MAV_HOURS and t <= at:
            out.append(t)
        t -= dt.timedelta(hours=1)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="d0", required=True, help="first target day, YYYY-MM-DD")
    ap.add_argument("--to", dest="d1", required=True, help="last target day, YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true", help="fetch and report, write nothing")
    ap.add_argument("--force", action="store_true", help="write even where the archive already has the cycle")
    args = ap.parse_args(argv)

    cfg = config.load()
    store = storage.from_config(cfg)
    ua = cfg.get("user_agent", "")
    d0, d1 = dt.date.fromisoformat(args.d0), dt.date.fromisoformat(args.d1)
    cities = us_cities(cfg)

    wanted = {}                              # (station, kind, cycle) -> set of target days
    for c in cities:
        tz = zoneinfo.ZoneInfo(c["tz"])
        d = d0
        while d <= d1:
            at = anchor_utc(d, tz)
            for kind in ("mav", "lamp"):
                for cyc in cycles_before(kind, at):
                    wanted.setdefault((c["station"], kind, cyc), set()).add(d.isoformat())
            d += dt.timedelta(days=1)

    have = {}
    for c in cities:
        for kind in ("mav", "lamp"):
            for k in store.list(f"archive/{c['station']}/{kind}_"):
                have.setdefault((c["station"], kind), set()).add(sn._stamp_of(k))

    written = skipped = empty = 0
    for (sid, kind, cyc) in sorted(wanted):
        stamp = cyc.strftime("%Y%m%dT%H%MZ")
        if not args.force and stamp in have.get((sid, kind), ()):
            skipped += 1                     # this site captured it; its own copy wins
            continue
        data = iem.rows(sid, kind, cyc, ua)
        time.sleep(PACE)
        if not data:
            empty += 1
            continue
        c = next(x for x in cities if x["station"] == sid)
        tz = zoneinfo.ZoneInfo(c["tz"])
        try:
            if kind == "mav":
                byday = sn._extremes_by_day(iem.extremes(data), tz)
                days = {d: {"high": v.get("max"), "low": v.get("min")} for d, v in byday.items()}
            else:
                rows = iem.hourly(data)
                days = {}
                for d in sorted({r["t"][:10] for r in rows}):
                    hi, lo = sn._max_min_in_day(rows, tz, d)
                    if hi is not None:
                        days[d] = {"high": hi, "low": lo}
        except ValueError:
            empty += 1                       # the convention is not defined for this station
            continue
        if not days:
            empty += 1
            continue
        body = {"source": "IEM", "product": kind, "station": sid,
                "cycle": stamp, "days": days,
                "note": "National Weather Service guidance, from Iowa State University's archive"}
        key = f"archive/{sid}/{kind}x_{stamp}.json.gz"
        if args.dry_run:
            print(f"  would write {key}: {json.dumps(days)[:110]}")
        else:
            store.put(key, gzip.compress(json.dumps(body, separators=(",", ":")).encode()), "application/gzip")
        written += 1

    print(json.dumps({"kind": "backfill-guidance", "from": args.d0, "to": args.d1,
                      "cyclesWanted": len(wanted), "written": written,
                      "skippedAlreadyArchived": skipped, "emptyAtSource": empty,
                      "dryRun": bool(args.dry_run)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
