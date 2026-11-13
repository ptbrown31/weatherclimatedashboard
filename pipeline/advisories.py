"""
advisories.py — every NHC forecast advisory of a storm, kept, so the hurricane
page can step back through them (owner, 2026-10-20).

The hurricane job reads the advisory in force from NHC's GIS service and
overwrites it each pass, so a storm's earlier forecasts were nowhere once it
had moved on. NHC keeps them: its GIS archive holds every full advisory's
five-day package (the cone, the forecast track and the forecast points as a
zipped shapefile set) for every storm of the season, and posts each new one as
it is issued. This lane copies them into the bucket, one file per storm:

  snapshots/advisories/<id>.json    {id, basin, year, name, names, advisories: [entry], missing}
  snapshots/advisories/index.json   {storms: {id: {name, names, year, basin, n, first, last, seen, complete}}}

An entry is one full advisory: its number, when NHC issued it, its synoptic
time t0 (the forecast's hour zero, the 00, 06, 12 or 18Z that the Live
Hurricane vendor's cycle of the same stamp is built on), the storm's name as
the advisory gave it, the forecast points with their valid times, and the
cone and the forecast track. An intermediate advisory moves the storm between
full ones and issues no new cone, so it is not kept.

Which storms: every storm on NHC's current roster (snapshots/hurricane.json,
which the hurricane job writes just before this one runs), each advisory as it
is posted and any earlier one not yet held; a storm off the roster for less
than two days, whose last advisories can be posted after it has left; and
every storm the Live Hurricane lane carries (snapshots/reask.json), found by
name in NHC's index of its year, so a settled storm can be stepped through
after it has gone. An advisory the archive has not posted is asked for again
on later passes for a day, then left.

The shapefiles are read here in the standard library, so nothing is added to
the package. NHC draws a cone's outline with about 1,400 points; it is thinned
to the few dozen that keep it within CONE_TOL of NHC's line (Douglas and
Peucker), and every position is rounded to a hundredth of a degree. Nothing is
interpolated or redrawn.

A Pacific storm can cross the date line, and NHC then writes its cone in
longitudes past 180 (Nolo's ran to 196.26 and back to 147 east) while its
positions stay west of Greenwich. Every longitude of an eastern or central
Pacific storm is written west of Greenwich here, so 196.26 is -163.74 and 147
east is -213: one storm's coordinates run on without a jump at the date line.
"""
from __future__ import annotations
import datetime as dt
import io
import json
import re
import struct
import time
import zipfile
from typing import Callable, Optional

from . import archive as arch
from . import gov_weather as gw
from .snapshots import SNAP_CACHE, _iso
from .storage import Storage

SCHEMA = 1
PREFIX = "snapshots/advisories/"
INDEX = PREFIX + "index.json"
SOURCE = "NWS National Hurricane Center forecast advisories, from NHC's GIS archive"
CONE_TOL = 0.02             # degrees: how far the thinned cone outline may stray from NHC's, about 2 km
SEEN_HOURS = 48             # a storm off the roster this recently is still looked for in the archive
MISS_HOURS = 24             # an advisory the archive has not posted in this long is not asked for again
HEALTH_KEY = "archive/_meta/health_advisories.json"   # this lane's own failure streak (see archive.update_health)
# three half-hourly passes in a row: an hour from the first failed pass to the third
FAIL_STREAK_ALARM = 3
# the time zones NHC writes its advisory times in, hours from UTC
ZONES = {"UTC": 0, "GMT": 0, "CVT": -1, "ADT": -3, "AST": -4, "EDT": -4, "EST": -5, "CDT": -5, "CST": -6,
         "MDT": -6, "MST": -7, "PDT": -7, "PST": -8, "HDT": -9, "HST": -10}
MONTHS = {m: i + 1 for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"))}


# ---- the package's files

def dbf_rows(data: bytes) -> list:
    """The records of a dBASE table, each a dict of stripped strings."""
    n, hlen, rlen = struct.unpack("<IHH", data[4:12])
    fields, pos = [], 32
    while data[pos] != 0x0D:
        fields.append((data[pos:pos + 11].split(b"\0")[0].decode("ascii"), data[pos + 16]))
        pos += 32
    rows = []
    for r in range(n):
        off = hlen + r * rlen + 1                # past the record's deletion flag
        row = {}
        for name, width in fields:
            row[name] = data[off:off + width].decode("latin-1").strip()
            off += width
        rows.append(row)
    return rows


def shp_shapes(data: bytes) -> list:
    """The shapes of a shapefile, in order: a point as (x, y), a polyline or a
    polygon as its parts, each a list of [x, y]; any other shape as None."""
    pos, out = 100, []
    while pos + 8 <= len(data):
        clen = struct.unpack(">i", data[pos + 4:pos + 8])[0] * 2
        body = data[pos + 8:pos + 8 + clen]
        kind = struct.unpack("<i", body[:4])[0] if len(body) >= 4 else 0
        if kind == 1:
            out.append(struct.unpack("<dd", body[4:20]))
        elif kind in (3, 5):
            nparts, npts = struct.unpack("<ii", body[36:44])
            starts = list(struct.unpack("<%di" % nparts, body[44:44 + 4 * nparts])) + [npts]
            xy = struct.unpack("<%dd" % (2 * npts), body[44 + 4 * nparts:44 + 4 * nparts + 16 * npts])
            pts = [[xy[2 * i], xy[2 * i + 1]] for i in range(npts)]
            out.append([pts[starts[i]:starts[i + 1]] for i in range(nparts)])
        else:
            out.append(None)
        pos += 8 + clen
    return out


def issued_time(advdate: str) -> Optional[dt.datetime]:
    """NHC's "1000 PM CDT Thu Oct 08 2026" as UTC; None when it does not read."""
    m = re.match(r"\s*(\d{1,2})(\d{2})\s+(AM|PM)\s+([A-Za-z]+)\s+[A-Za-z]+\s+([A-Za-z]{3})\s+(\d{1,2})\s+(\d{4})",
                 advdate or "")
    if not m or m.group(4) not in ZONES or m.group(5) not in MONTHS:
        return None
    hh = int(m.group(1)) % 12 + (12 if m.group(3) == "PM" else 0)
    local = dt.datetime(int(m.group(7)), MONTHS[m.group(5)], int(m.group(6)), hh, int(m.group(2)))
    return (local - dt.timedelta(hours=ZONES[m.group(4)])).replace(tzinfo=dt.timezone.utc)


def valid_time(stamp: str, near: dt.datetime) -> Optional[dt.datetime]:
    """A forecast point's "DD/HHMM" in UTC, in whichever month puts it nearest
    `near`: every point lies within five days of its advisory, so the month is
    never in doubt, even across a month's end."""
    m = re.match(r"\s*(\d{1,2})/(\d{2})(\d{2})\s*$", stamp or "")
    if not m:
        return None
    best = None
    for k in (-1, 0, 1):
        y, mo = near.year, near.month + k
        y, mo = (y - 1, 12) if mo < 1 else (y + 1, 1) if mo > 12 else (y, mo)
        try:
            t = dt.datetime(y, mo, int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=dt.timezone.utc)
        except ValueError:
            continue
        if best is None or abs(t - near) < abs(best - near):
            best = t
    return best


def thin(line: list, tol: float) -> list:
    """The points of a line that keep it within `tol` of the original
    (Douglas and Peucker), worked with a stack rather than recursion."""
    if len(line) < 3:
        return [list(p) for p in line]
    keep = [False] * len(line)
    keep[0] = keep[-1] = True
    stack = [(0, len(line) - 1)]
    while stack:
        a, b = stack.pop()
        (x0, y0), (x1, y1) = line[a], line[b]
        dx, dy = x1 - x0, y1 - y0
        span = (dx * dx + dy * dy) ** 0.5
        far, at = -1.0, None
        for i in range(a + 1, b):
            x, y = line[i]
            d = abs(dy * x - dx * y + x1 * y0 - y1 * x0) / span if span else ((x - x0) ** 2 + (y - y0) ** 2) ** 0.5
            if d > far:
                far, at = d, i
        if at is not None and far > tol:
            keep[at] = True
            stack += [(a, at), (at, b)]
    return [list(p) for p, k in zip(line, keep) if k]


def _minute(t: Optional[dt.datetime]) -> Optional[str]:
    return t.strftime("%Y-%m-%dT%H:%MZ") if t else None


def _num(v: str) -> Optional[float]:
    """A table figure as a number, None for NHC's 9999 (no value)."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x >= 9999:
        return None
    return int(x) if x == int(x) else x


def westward(lon: float, basin: Optional[str]) -> float:
    """A longitude as this lane writes it: west of Greenwich for an eastern or
    central Pacific storm, so a track across the date line runs on unbroken."""
    return lon - 360 if basin in ("EP", "CP") and lon > 0 else lon


def parse_package(blob: bytes) -> dict:
    """One full advisory's five-day package as an entry."""
    z = zipfile.ZipFile(io.BytesIO(blob))

    def member(tail: str) -> bytes:
        hit = [n for n in z.namelist() if n.lower().endswith(tail)]
        if not hit:
            raise ValueError("package has no *" + tail)
        return z.read(hit[0])
    rows, shapes = dbf_rows(member("_5day_pts.dbf")), shp_shapes(member("_5day_pts.shp"))
    if not rows or len(rows) != len(shapes):
        raise ValueError("the forecast points and their table disagree")
    head = rows[0]
    basin = head.get("BASIN") or None
    lon = lambda x: round(westward(x, basin), 2)
    issued = issued_time(head.get("ADVDATE", ""))
    if issued is None:
        raise ValueError("advisory time does not read: %r" % head.get("ADVDATE"))
    points = []
    for r, xy in zip(rows, shapes):
        if xy is None:
            continue
        points.append({"tau": int(float(r.get("TAU") or 0)), "t": _minute(valid_time(r.get("VALIDTIME", ""), issued)),
                       "lon": lon(xy[0]), "lat": round(xy[1], 2), "kt": _num(r.get("MAXWIND")),
                       "gust": _num(r.get("GUST")), "type": r.get("STORMTYPE") or None})
    points.sort(key=lambda p: p["tau"])
    lin = dbf_rows(member("_5day_lin.dbf"))
    # the track's table names the storm plainly ("Isaias", "Nine"); the points' table adds its type
    name = (lin[0].get("STORMNAME") if lin else "") or re.sub(r"^.*\s", "", head.get("STORMNAME", ""))
    cone = [[[lon(x), round(y, 2)] for x, y in thin([[westward(x, basin), y] for x, y in part], CONE_TOL)]
            for shp in shp_shapes(member("_5day_pgn.shp")) if shp for part in shp]
    track = [[[lon(x), round(y, 2)] for x, y in part]
             for shp in shp_shapes(member("_5day_lin.shp")) if shp for part in shp]
    return {"adv": "%03d" % int(float(head["ADVISNUM"])), "issued": _minute(issued),
            "t0": points[0]["t"] if points else None, "name": name.strip().title() if name.isupper() else name.strip(),
            "basin": basin, "points": points, "cone": cone, "track": track}


# ---- the lane

def storm_key(storm_id: str) -> str:
    return f"{PREFIX}{storm_id}.json"


def full_number(adv) -> Optional[int]:
    """The full advisory a roster's advisory number belongs to: "010" and "010A" are both 10."""
    m = re.match(r"\s*(\d+)", str(adv or ""))
    return int(m.group(1)) if m else None


def _load(store: Storage, key: str) -> Optional[dict]:
    raw = store.get(key)
    return json.loads(raw) if raw else None


def _hours_since(iso: Optional[str], now: dt.datetime) -> float:
    try:
        return (now - dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))).total_seconds() / 3600
    except ValueError:
        return float("inf")


def fill(doc: dict, numbers: list, now: dt.datetime, fetch: Callable,
         deadline: Optional[arch.Deadline] = None) -> int:
    """Copy into `doc` the advisories among `numbers` it does not hold, and
    return how many were added. One NHC has not posted is noted, asked for on
    later passes, and left once MISS_HOURS have passed since it was first missed."""
    have = {a["adv"] for a in doc["advisories"]}
    missing = doc.setdefault("missing", {})
    added = 0
    for num in numbers:
        adv = "%03d" % int(num)
        if adv in have or (adv in missing and _hours_since(missing[adv], now) > MISS_HOURS):
            continue
        if deadline is not None and deadline.over(15):
            break
        blob = fetch(doc["id"], adv)
        if not blob:
            missing.setdefault(adv, _iso(now))
            continue
        entry = parse_package(blob)
        if entry["adv"] != adv:
            raise ValueError("asked NHC for advisory %s and its package says %s" % (adv, entry["adv"]))
        doc["advisories"].append(entry)
        missing.pop(adv, None)
        have.add(adv)
        added += 1
    doc["advisories"].sort(key=lambda a: int(a["adv"]))
    return added


def names_of(doc: dict) -> list:
    """Every name the storm's advisories carried, in the order it carried them:
    a depression's number word, then the name it was given."""
    out = []
    for a in doc["advisories"]:
        if a.get("name") and a["name"] not in out:
            out.append(a["name"])
    return out


def _label_name(label: str) -> str:
    """The storm's name out of an NHC index label: "Hurricane ISAIAS" is Isaias."""
    return label.split()[-1].title() if label.split() else ""


def advisories_job(store: Storage, now: dt.datetime, fetch: Optional[Callable] = None,
                   listing: Optional[Callable] = None, year_index: Optional[Callable] = None,
                   deadline: Optional[arch.Deadline] = None) -> dict:
    fetch = fetch or gw.fetch_nhc_package
    listing = listing or gw.fetch_nhc_storm_advisories
    year_index = year_index or gw.fetch_nhc_year_storms
    idx = _load(store, INDEX) or {"schema": SCHEMA, "storms": {}}
    known = idx.setdefault("storms", {})
    roster = {s["id"]: s for s in ((_load(store, "snapshots/hurricane.json") or {}).get("storms") or []) if s.get("id")}
    # id -> the advisory numbers to hold, or None to ask the archive which there are
    todo: dict = {}
    for sid, s in roster.items():
        n = full_number(s.get("advisory"))
        if n:
            todo[sid] = list(range(1, n + 1))
    for sid, e in known.items():
        if sid not in roster and _hours_since(e.get("seen"), now) < SEEN_HOURS:
            todo.setdefault(sid, None)
    # the storms the Live Hurricane lane carries that are not held whole yet
    errors: list = []
    years: dict = {}
    for st in ((_load(store, "snapshots/reask.json") or {}).get("storms") or []):
        nm, yr = str(st.get("name") or "").strip(), st.get("year")
        if not nm or not yr:
            continue
        held = [sid for sid, e in known.items() if str(e.get("year")) == str(yr)
                and nm.lower() in [x.lower() for x in (e.get("names") or [])]]
        if held:
            if not known[held[0]].get("complete"):
                todo.setdefault(held[0], None)
            continue
        if yr not in years:
            try:
                years[yr] = year_index(int(yr))
            except Exception as e:  # noqa: BLE001 - the roster's storms do not wait on the index
                errors.append(f"index {yr}: {type(e).__name__}: {e}")
                years[yr] = {}
        hit = sorted(sid for sid, label in years[yr].items() if _label_name(label).lower() == nm.lower())
        if hit:
            todo.setdefault(hit[0], None)
    added, deferred = 0, False
    for sid, numbers in todo.items():
        if deadline is not None and deadline.over(15):
            deferred = True                      # the rest wait for the next pass
            break
        try:
            doc = _load(store, storm_key(sid)) or {"schema": SCHEMA, "id": sid, "basin": sid[:2].upper(),
                                                   "year": int(sid[4:]), "advisories": [], "missing": {}}
            listed = numbers is None
            if listed:
                numbers = [int(a) for a in listing(sid)]
            n = fill(doc, numbers, now, fetch, deadline)
            want = {"%03d" % int(x) for x in numbers}
            have = {a["adv"] for a in doc["advisories"]}
            gone = {a for a, first in (doc.get("missing") or {}).items() if _hours_since(first, now) > MISS_HOURS}
            names = names_of(doc)
            seen = _iso(now) if sid in roster else (known.get(sid) or {}).get("seen")
            if n or sid not in known:
                doc.update({"asof": _iso(now), "source": SOURCE, "names": names,
                            "name": names[-1] if names else doc.get("name")})
                store.put(storm_key(sid), json.dumps(doc, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
            adv = doc["advisories"]
            known[sid] = {"name": names[-1] if names else None, "names": names, "year": doc["year"],
                          "basin": doc["basin"], "n": len(adv), "first": adv[0]["t0"] if adv else None,
                          "last": adv[-1]["t0"] if adv else None, "seen": seen,
                          # held whole: everything the archive lists, or given up on, while the storm is off the roster
                          "complete": bool(listed and sid not in roster and want <= have | gone)}
            added += n
        except Exception as e:  # noqa: BLE001 - one storm's package does not stop the others
            errors.append(f"{sid}: {type(e).__name__}: {e}")
    idx.update({"schema": SCHEMA, "asof": _iso(now), "source": SOURCE})
    store.put(INDEX, json.dumps(idx, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
    return {"storms": len(todo), "added": added, "held": len(known), "deferred": deferred, "errors": errors}


def advisories_pass(cfg: dict, store: Storage) -> int:
    gw.set_user_agent(cfg.get("user_agent", ""))
    now = dt.datetime.now(dt.timezone.utc)
    t0 = time.time()
    deadline = arch.Deadline(arch.remaining_budget(cfg))
    # A storm whose package or listing NHC does not serve whole is reported and asked for again on the next pass, and
    # does not fail the pass (owner, 2026-11-13). NHC's archive cut its listing page for Pena (CP04) short at 8,116
    # bytes on 4 of 59 passes on the night of 12 November, the passes between read it, and each failure failed the
    # half-hourly chain, paged the owner and had Lambda run the whole chain twice more. The lane's failure streak raises
    # the alarm once NHC's reads have failed for FAIL_STREAK_ALARM passes in a row. The job's own failure, which no
    # read is to blame for, still fails the pass.
    failed = 0
    try:
        out = advisories_job(store, now, deadline=deadline)
        errors = len(out["errors"])
    except Exception as e:  # noqa: BLE001 - a missed pass leaves the files as they were
        out = {"error": f"{type(e).__name__}: {e}"}
        errors = failed = 1
    alarms: list = []
    if not failed:
        try:
            health = arch.update_health(store, {"nhc": {"ok": not errors, "error": (out["errors"] or [None])[0]}},
                                        now, key=HEALTH_KEY)
            out["failStreak"] = (health.get("nhc") or {}).get("fail_streak", 0)
            if out["failStreak"] >= FAIL_STREAK_ALARM:
                alarms = ["advisories"]
        except Exception as e:  # noqa: BLE001 - a streak that cannot be kept is the job's own failure
            out["healthError"] = f"{type(e).__name__}: {e}"
            failed = 1
    out.update({"kind": "advisories", "seconds": round(time.time() - t0, 1)})
    arch.LAST_STATUS = {"job": "advisories", "errors": errors, "alarms": alarms}
    print(json.dumps(out))
    return failed
