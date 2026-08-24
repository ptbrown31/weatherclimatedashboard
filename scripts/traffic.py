#!/usr/bin/env python3
"""Print the site's traffic summary.

The figures live under the archive prefix, which the bucket policy denies to
CloudFront, so they are readable from the account and never from the public
site. This script is how you read them.

    python3 scripts/traffic.py                 the last two weeks
    python3 scripts/traffic.py --days 90       as far back as the summary goes
    python3 scripts/traffic.py --day 2026-08-23   one day in full

With the s3 backend configured it reads the deployed bucket; with the local
backend it reads ./data, so a local run works offline.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import config, storage, traffic   # noqa: E402


def bar(n: int, most: int, width: int = 28) -> str:
    if most <= 0:
        return ""
    return "█" * max(1, round(width * n / most)) if n else ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=14, help="how many days of the trend to print (default 14)")
    ap.add_argument("--day", help="print one day in full instead of the trend")
    ap.add_argument("--json", action="store_true", help="print the raw document")
    args = ap.parse_args(argv)

    store = storage.from_config(config.load())

    if args.day:
        raw = store.get(traffic.DAY_KEY.format(day=args.day))
        if not raw:
            print(f"No traffic summary for {args.day}. Days are written the morning after, in UTC.")
            return 1
        doc = json.loads(raw)
        if args.json:
            print(json.dumps(doc, indent=2))
            return 0
        print(f"\n{doc['day']}   {doc['views']} views from {doc['visitors']} visitors"
              f"   ({doc['siteViews']} site, {doc['embedViews']} embed)")
        print(f"{'':13}{doc['requests']} requests, {doc['megabytes']} MB, {doc['botRequests']} bot requests filtered\n")
        if doc.get("pages"):
            most = doc["pages"][0]["views"]
            print("  PAGES")
            for p in doc["pages"]:
                print(f"    {p['views']:>5}  {bar(p['views'], most)}  {p['path']}")
        if doc.get("referrers"):
            print("\n  CAME FROM")
            most = doc["referrers"][0]["views"]
            for r in doc["referrers"]:
                print(f"    {r['views']:>5}  {bar(r['views'], most)}  {r['host']}")
        else:
            print("\n  CAME FROM\n    nothing: every view arrived typed, bookmarked, or from a client that sends no referrer")
        print(f"\n  {doc['note']}\n")
        return 0

    raw = store.get(traffic.SUMMARY_KEY)
    if not raw:
        print("No traffic summary yet.\n\n"
              "The first one is written by the daily job the morning after CDN logging is switched on.\n"
              "If that has just happened, wait for the next 09:30Z run, or force one:\n"
              "    aws lambda invoke --function-name weather-tools-site-pipeline \\\n"
              "        --payload '{\"job\": \"traffic\"}' --cli-binary-format raw-in-base64-out /dev/stdout")
        return 1
    s = json.loads(raw)
    if args.json:
        print(json.dumps(s, indent=2))
        return 0
    days = s.get("days", [])[-args.days:]
    if not days:
        print("The summary exists but holds no days yet.")
        return 1
    most = max(d.get("views") or 0 for d in days) or 1
    print(f"\n  {'DAY':<12}{'VIEWS':>7}{'VISITORS':>10}{'SITE':>7}{'EMBED':>7}   TREND")
    for d in days:
        v = d.get("views") or 0
        print(f"  {d.get('day',''):<12}{v:>7}{d.get('visitors') or 0:>10}"
              f"{d.get('siteViews') or 0:>7}{d.get('embedViews') or 0:>7}   {bar(v, most)}")
    t = s.get("totals", {})
    print(f"\n  {len(days)} days shown, {sum(d.get('views') or 0 for d in days)} views."
          f"  Summary holds {len(s.get('days', []))} days, {t.get('views', 0)} views in total.")
    latest = s.get("latest") or {}
    if latest.get("pages"):
        print(f"\n  MOST READ ON {latest.get('day','')}")
        for p in latest["pages"][:5]:
            print(f"    {p['views']:>5}  {p['path']}")
    print(f"\n  {s.get('note','')}")
    print("  One day in full: python3 scripts/traffic.py --day <YYYY-MM-DD>\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
