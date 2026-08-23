"""
season.py — the cumulative Atlantic season count against an average pace, once a day.

The figure behind this snapshot answers one question: how many named storms,
hurricanes and major hurricanes have formed so far this year, on what dates,
and is that ahead of or behind a normal season.

Two feeds, because no single one covers both halves.

  HURDAT2       NHC's Atlantic best-track archive, every system back to 1851.
                Post-season reanalysed and quality controlled, which is what
                makes it the right basis for a climatological pace, and also
                why it stops at the last completed season: a year is not added
                until the following spring. About 7 MB, discovered by name.

  ATCF b-decks  The working best tracks for the season in progress, one file
                per cyclone, updated with each advisory. Small, so this half
                is recomputed every pass.

The two halves are never mixed for the same year. HURDAT2 supplies the
climatology and nothing else; the current year comes only from ATCF.

A system is counted at the first best-track row where it meets a threshold:

    named       a tropical or subtropical cyclone row (TS, SS, HU) at 34 kt
    hurricane   a hurricane row (HU) at 64 kt
    major       a hurricane row (HU) at 96 kt, which is category 3

Those are the thresholds hurricane.py's season_counts already applies to the
ATCF files, and the two jobs have to agree because both numbers appear on the
site. The wind is the one-minute maximum sustained wind; the type field is
what keeps a 35-kt extratropical or remnant low out of the count.

The climatology is the 1991-2020 base period, NOAA's current 30-year normals
window. This parse reproduces NOAA's published Atlantic normals exactly: 14.4
named storms, 7.2 hurricanes, 3.23 majors.

Writes snapshots/season.json.
"""
from __future__ import annotations
import datetime as dt
import json
import re
import time
from typing import Optional

from . import gov_weather as gw
from . import archive as arch
from . import hurricane
from .storage import Storage
from .snapshots import _iso

CLIM_KEY = "archive/_meta/hurdat_climatology.json"
SNAP_KEY = "snapshots/season.json"
SNAP_CACHE = "public, max-age=3600, stale-while-revalidate=86400, stale-if-error=2592000"

CLIM_FIRST_YEAR = 1991            # NOAA's current 30-year base period for tropical cyclone normals
CLIM_LAST_YEAR = 2020
CLIM_MAX_AGE_DAYS = 30            # re-read even an unchanged file this often, so a silent edit is picked up
SEASON_START = (5, 1)             # the curve's window: the official Atlantic season, June 1 - November 30,
SEASON_END = (11, 30)             # opened a month early because May storms have become routine

# Wind thresholds in knots, one-minute maximum sustained. These must stay equal
# to the values hurricane.py's season_counts applies to the same ATCF files;
# tests/test_season.py checks the two agree on identical input.
KT_NAMED = 34
KT_HURRICANE = 64
KT_MAJOR = 96
NAMED_TYPES = hurricane.NAMED_TYPES        # TS, SS, HU: tropical or subtropical cyclone
GROUPS = ("named", "hurricanes", "majors")

_HURDAT_HEADER = re.compile(r"^AL\d{6}$")


def _iso_date(yyyymmdd: str) -> Optional[str]:
    if len(yyyymmdd) != 8 or not yyyymmdd.isdigit():
        return None
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


def _blank(sid: str, name: str, year: int) -> dict:
    return {"id": sid, "name": name, "year": year, "named": None, "hurricanes": None, "majors": None}


def _mark(rec: dict, status: str, wind: int, date: str) -> None:
    """Record the first date this system met each threshold. Wind below zero
    is HURDAT2's -999 for a value the reanalysis could not establish; it is
    left out rather than read as a weak observation."""
    if wind < 0:
        return
    if status in NAMED_TYPES and wind >= KT_NAMED and rec["named"] is None:
        rec["named"] = date
    if status == "HU" and wind >= KT_HURRICANE and rec["hurricanes"] is None:
        rec["hurricanes"] = date
    if status == "HU" and wind >= KT_MAJOR and rec["majors"] is None:
        rec["majors"] = date


# ---------------------------------------------------------------- HURDAT2
def parse_hurdat(text: str) -> list:
    """Every system in the archive, with the date it first met each threshold.

    The format alternates a header line -- storm id, name, and the number of
    data rows that follow -- with exactly that many rows of
    YYYYMMDD, HHMM, record id, status, lat, lon, wind, pressure, ... Fields
    are space padded. The row count is what separates one storm from the next,
    so it is used rather than guessed at from field counts, but a line that
    looks like a header ends the current storm early: on a truncated download
    that keeps the parse in step instead of silently attributing the rest of
    the file to one system."""
    storms = []
    lines = text.splitlines()
    i, n = 0, len(lines)
    while i < n:
        fields = [p.strip() for p in lines[i].split(",")]
        i += 1
        while fields and fields[-1] == "":
            fields.pop()                       # the header's trailing comma
        if len(fields) != 3 or not _HURDAT_HEADER.match(fields[0]):
            continue
        try:
            rows = int(fields[2])
        except ValueError:
            continue
        rec = _blank(fields[0], fields[1].title(), int(fields[0][4:8]))
        for _ in range(rows):
            if i >= n:
                break
            row = [p.strip() for p in lines[i].split(",")]
            if len(row) == 4 and _HURDAT_HEADER.match(row[0]):
                break                          # next header: this storm's count was wrong
            i += 1
            if len(row) < 7:
                continue
            date = _iso_date(row[0])
            try:
                wind = int(row[6])
            except ValueError:
                continue
            if date:
                _mark(rec, row[3], wind, date)
        storms.append(rec)
    return storms


def _season_days() -> list:
    """The curve's x axis as MM-DD, May 1 through November 30 inclusive: 214
    days. Any non-leap year serves as the calendar here, and the window holds
    no February, so the leap day never arises."""
    out = []
    d = dt.date(2001, SEASON_START[0], SEASON_START[1])
    end = dt.date(2001, SEASON_END[0], SEASON_END[1])
    while d <= end:
        out.append(d.strftime("%m-%d"))
        d += dt.timedelta(days=1)
    return out


def climatology(storms: list, source: str, computed: str,
                first: int = CLIM_FIRST_YEAR, last: int = CLIM_LAST_YEAR) -> dict:
    """The average season's pace: for each day of the window, the mean number
    of systems that had reached each threshold by then.

    Counting runs from January 1, not from the start of the window, so the
    handful of systems that form in the winter months on either side of the
    season are handled honestly. One that forms before May 1 is already on the
    board when the curve opens, which is why the curves start slightly above
    zero. One that forms in December falls past the right-hand end, which is
    why a curve can finish a little below the season total beside it: the
    totals are full calendar years."""
    years = last - first + 1
    days = _season_days()
    picked = [s for s in storms if first <= s["year"] <= last]
    out = {"source": f"NHC HURDAT2 ({source})", "period": f"{first}-{last}", "years": years,
           "computed": computed, "start": days[0]}
    totals = {}
    for group in GROUPS:
        per_day: dict = {}
        for s in picked:
            if s[group]:
                md = s[group][5:]
                per_day[md] = per_day.get(md, 0) + 1
        run = sum(n for md, n in per_day.items() if md < days[0])
        curve = []
        for md in days:
            run += per_day.get(md, 0)
            curve.append(round(run / years, 3))
        out[group] = curve
        totals[group] = round(sum(1 for s in picked if s[group]) / years, 2)
    out["totals"] = totals
    # Majors by month of formation. Every month of the window above is present,
    # zero or not, so the split lines up with the curves beside it. A month
    # outside the window joins it only if something actually formed then, so
    # the split still adds up to the total: no major forms outside May-November
    # in 1991-2020 -- the earliest in the base period is in July -- but a base
    # period that rolls forward is not promised to stay that way.
    months: dict = {"%02d" % m: 0 for m in range(SEASON_START[0], SEASON_END[0] + 1)}
    for s in picked:
        if s["majors"]:
            m = s["majors"][5:7]
            months[m] = months.get(m, 0) + 1
    out["monthlyMajors"] = {m: round(n / years, 3) for m, n in sorted(months.items())}
    return out


# ------------------------------------------------------------------- ATCF
def parse_atcf(body: str, sid: str, num: int, year: int) -> Optional[dict]:
    """One ATCF best-track file, read the way hurricane.py reads it: field 2 is
    the synoptic time YYYYMMDDHH in UTC, field 8 the maximum sustained wind in
    knots, field 10 the system type and field 27 the name. A b-deck repeats the
    same time once per wind-radius threshold, so the first matching row is the
    first time the threshold was met, not the first radius line.

    The name is taken from the latest cyclone row, because early rows carry the
    working label (INVEST, then ONE) before the system is named. Returns None
    for anything that never reaches 34 kt as a cyclone: invests and depressions
    are in the directory but are not part of the count."""
    rec = _blank(sid, "", year)
    name = ""
    for ln in body.splitlines():
        f = [p.strip() for p in ln.split(",")]
        if len(f) <= 27:
            continue
        try:
            wind = int(f[8])
        except ValueError:
            continue
        date = _iso_date(f[2][:8])
        if not date:
            continue
        if f[10] in NAMED_TYPES and f[27]:
            name = f[27]
        _mark(rec, f[10], wind, date)
    if not rec["named"]:
        return None
    rec["name"] = name.title() or f"#{num}"
    return rec


def atcf_season(year: int, deadline: Optional[arch.Deadline] = None) -> list:
    """This season's systems from the live best tracks. The directory listing
    names each file twice (href and text), so collect a set. Cyclone numbers
    01-49 are real systems; 80-89 are test entries and 90+ are invest areas."""
    idx = gw._get_text(hurricane.ATCF_BTK, timeout=60)
    nums = sorted({int(m.group(1)) for m in re.finditer(rf"bal(\d\d){year}\.dat", idx)})
    out = []
    for num in nums:
        if not 1 <= num <= 49:
            continue
        if deadline is not None and deadline.over(20):
            raise RuntimeError(f"out of time before cyclone {num:02d}")
        sid = f"AL{num:02d}{year}"
        rec = parse_atcf(gw._get_text(f"{hurricane.ATCF_BTK}bal{num:02d}{year}.dat", timeout=60), sid, num, year)
        if rec:
            out.append(rec)
    return out


def season_lists(storms: list) -> dict:
    """The three formation lists the figure plots, each in date order."""
    out = {}
    for group in GROUPS:
        rows = [{"date": s[group], "name": s["name"], "id": s["id"]} for s in storms if s[group]]
        rows.sort(key=lambda r: (r["date"], r["id"]))
        out[group] = rows
    return out


# ------------------------------------------------------------------- pass
def load_climatology(store: Storage, source: str, today: dt.date) -> Optional[dict]:
    """The cached climatology, if it was computed from this same file and is
    not stale. HURDAT2 is 7 MB and changes about once a year, so the file name
    is the cache key; the age limit is only a backstop against an edit that
    reuses a name."""
    raw = store.get(CLIM_KEY)
    if not raw:
        return None
    try:
        cached = json.loads(raw)
    except ValueError:
        return None
    if cached.get("file") != source:
        return None
    try:
        age = (today - dt.date.fromisoformat(cached.get("computed", ""))).days
    except ValueError:
        return None
    if age < 0 or age > CLIM_MAX_AGE_DAYS:
        return None
    return cached.get("climatology")


def season_pass(cfg: dict, store: Storage) -> int:
    gw.set_user_agent(cfg.get("user_agent", ""))
    now = dt.datetime.now(dt.timezone.utc)
    today = now.date()
    deadline = arch.Deadline(arch.remaining_budget(cfg))
    t0 = time.time()
    raw_prev = store.get(SNAP_KEY)
    prev = json.loads(raw_prev) if raw_prev else {}
    errors: list = []

    # 1. the climatology, from cache unless the published file has changed
    clim, recomputed = None, False
    try:
        url = gw.latest_hurdat_url()
        clim_source = url.rsplit("/", 1)[-1]
        clim = load_climatology(store, clim_source, today)
        if clim is None:
            if deadline.over(120):
                raise RuntimeError("not enough time left in the pass to read HURDAT2")
            storms = parse_hurdat(gw.fetch_hurdat(url))
            if len(storms) < 1000:
                raise RuntimeError(f"HURDAT2 parsed to only {len(storms)} systems")
            clim = climatology(storms, clim_source, today.isoformat())
            store.put(CLIM_KEY, json.dumps({"file": clim_source, "computed": today.isoformat(),
                                            "climatology": clim}, separators=(",", ":")).encode(),
                      "application/json")
            recomputed = True
    except Exception as e:
        errors.append(f"climatology: {type(e).__name__}: {e}")
        clim = None

    # 2. the season in progress, every pass
    year = now.year
    season, found, season_ok = None, 0, False
    try:
        storms = atcf_season(year, deadline)
        season = season_lists(storms)
        found = len(storms)
        season_ok = True
    except Exception as e:
        errors.append(f"season: {type(e).__name__}: {e}")

    # A pass that lost one half keeps the previous snapshot's half rather than
    # publishing a figure with a missing curve. With no previous snapshot to
    # fall back on there is nothing safe to write, so the old object stands.
    if clim is None:
        clim = prev.get("climatology")
    if season is None:
        season = prev.get("season")
    if clim is None or season is None:
        arch.LAST_STATUS = {"job": "season", "errors": len(errors), "alarms": []}
        print(json.dumps({"kind": "season", "written": False, "errors": errors,
                          "seconds": round(time.time() - t0, 1)}))
        return 1

    fc = cfg.get("season_forecast") or {}
    snap = {"schema": 1,
            # the season list is only as fresh as the last good ATCF pass
            "asof": _iso(now) if season_ok else prev.get("asof", _iso(now)),
            "written": _iso(now), "year": year, "climatology": clim, "season": season,
            "forecast": {"named": fc.get("named"), "hurricanes": fc.get("hurricanes"),
                         "majors": fc.get("majors"), "source": fc.get("source", ""),
                         "label": fc.get("label", "")}}
    store.put(SNAP_KEY, json.dumps(snap, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
    arch.LAST_STATUS = {"job": "season", "errors": len(errors), "alarms": []}
    print(json.dumps({"kind": "season", "year": year, "systems": found, "stale": not season_ok,
                      "counts": {g: len(season[g]) for g in GROUPS},
                      "totals": clim.get("totals"), "climatology": clim.get("source"),
                      "recomputed": recomputed, "errors": errors,
                      "seconds": round(time.time() - t0, 1)}))
    return 1 if errors else 0
