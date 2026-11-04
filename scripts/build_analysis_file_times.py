"""
build_analysis_file_times.py writes config/analysis_file_times.json from the engine's CSV of NOAA's
publication times.

The analysis lane kept no Last-Modified time before 2026-10-14 and no copy from NOMADS before it kept
each server's copy, so the settlement display had no publication time for those files. The engine
read them from its own read log (NOAA Open Data's earliest Last-Modified for 2026-08-29 to 2026-10-14,
and NOMADS's for the analysis of 2026-09-23 19Z, which never reached the bucket) and from its NOMADS
timing probe (NOMADS's Last-Modified and first sighting from 2026-11-03 15:13 UTC to the deploy of
2026-11-04 11:29 UTC). The CSV has one row per file and server, `kind` (anl, or pcp keyed by the hour
it ends, as the lane keys it), `file_utc`, `server` (nodd or nomads), `last_modified` and
`first_seen`, all ISO in UTC, `first_seen` empty where unknown.

The table is {kind: {file_utc: {server: {"lm": ..., "seen": ...}}}}, one file to a line. The lane only
shows it (build_day's anlFile and pcpFile, and the hourly state of an hour read before the copies,
docs/analysis.md); no day's figure, count, late flag or state reads it. A row that is not one file on
one server at the top of an hour is refused, so a malformed CSV writes nothing.

    python3 scripts/build_analysis_file_times.py FILE_TIMES.csv [OUT.json]
"""
from __future__ import annotations
import csv
import datetime as dt
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "config", "analysis_file_times.json")
COLUMNS = ["kind", "file_utc", "server", "last_modified", "first_seen"]
KINDS, SERVERS = ("anl", "pcp"), ("nodd", "nomads")


def _stamp(s: str, what: str) -> str:
    """An ISO UTC time as the lane writes it, YYYY-MM-DDTHH:MM:SSZ, or the reason it is not one."""
    try:
        t = dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise ValueError(f"{what} {s!r} is not YYYY-MM-DDTHH:MM:SSZ")
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def build(rows) -> dict:
    out: dict = {k: {} for k in KINDS}
    for n, r in enumerate(rows, 2):
        kind, server = r["kind"], r["server"]
        if kind not in KINDS or server not in SERVERS:
            raise ValueError(f"line {n}: kind {kind!r} or server {server!r} unknown")
        t = _stamp(r["file_utc"], f"line {n}: file_utc")
        if not t.endswith(":00:00Z"):
            raise ValueError(f"line {n}: file_utc {t} is not the top of an hour")
        entry = out[kind].setdefault(t, {})
        if server in entry:
            raise ValueError(f"line {n}: {kind} {t} {server} given twice")
        entry[server] = {"lm": _stamp(r["last_modified"], f"line {n}: last_modified"),
                         "seen": _stamp(r["first_seen"], f"line {n}: first_seen") if r["first_seen"] else None}
    return out


def dump(table: dict) -> str:
    parts = []
    for kind in KINDS:
        lines = [f"  {json.dumps(t)}: {json.dumps(table[kind][t], sort_keys=True)}" for t in sorted(table[kind])]
        parts.append(f" {json.dumps(kind)}: {{\n" + ",\n".join(lines) + ("\n }" if lines else "}"))
    return "{\n" + ",\n".join(parts) + "\n}\n"


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) not in (1, 2):
        print(__doc__)
        return 2
    with open(argv[0], newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != COLUMNS:
            print(f"columns {reader.fieldnames} are not {COLUMNS}", file=sys.stderr)
            return 1
        try:
            table = build(reader)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 1
    text = dump(table)
    json.loads(text)
    out = argv[1] if len(argv) == 2 else OUT
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text)
    counts = {k: {s: sum(1 for v in table[k].values() if s in v) for s in SERVERS} for k in KINDS}
    print(f"{os.path.relpath(out, ROOT)}: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
