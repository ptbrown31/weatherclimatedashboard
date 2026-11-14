"""
sst.py — the sea surface temperature anomaly behind the hurricane map.

NOAA Coral Reef Watch publishes a daily global 5 km sea surface temperature
anomaly (NESDIS STAR), the departure of the day's satellite SST from Coral
Reef Watch's own daily climatology for that pixel. Hurricanes draw their energy
from the upper ocean, and an anomaly map shows at a glance where the water in a
storm's path runs warmer than usual, which is why the hurricane map carries it.

It is read through NESDIS's CoastWatch ERDDAP as a CSV subset of each basin's
box at every fourth pixel (0.2 degrees, about 68,000 cells for the Atlantic),
which keeps the pass in the standard library and the snapshot small. The
product lands about two days after its date. Each pass first asks for the
dataset's newest date, a request of a few bytes, and fetches the fields only
when that date is newer than the one already published and the last check is
more than CHECK_HOURS old, so a half-hourly chain reads the large subsets
about once a day.

snapshots/sst.json carries one grid per basin, rows north to south and columns
west to east, each cell the anomaly in tenths of a degree Celsius as a signed
byte (base64), with -128 for land, ice and missing pixels. The page colours it;
nothing here smooths or fills it.
"""
from __future__ import annotations
import base64
import datetime as dt
import json
import math
import time
import urllib.parse
from typing import Callable, Optional

from . import archive as arch
from . import gov_weather as gw
from .snapshots import SNAP_CACHE, _iso
from .storage import Storage

SCHEMA = 1
KEY = "snapshots/sst.json"
ERDDAP = "https://coastwatch.noaa.gov/erddap/griddap/noaacrwsstanomalyDaily"
VAR = "sea_surface_temperature_anomaly"
SOURCE = "NOAA Coral Reef Watch daily 5 km SST anomaly (NESDIS STAR), via NOAA CoastWatch ERDDAP"
STRIDE = 4                        # every fourth 0.05-degree pixel: 0.2 degrees
CHECK_HOURS = 3.0                 # how often the newest date is asked for
MISSING = -128
# the boxes the hurricane page draws (site/js/hurricane.js BASINS), as
# (west, south, east, north)
BOXES = {"AL": (-101.0, 4.0, -40.0, 48.0), "EP": (-180.0, 0.0, -85.0, 40.0)}
HEALTH_KEY = "archive/_meta/health_sst.json"   # this lane's own failure streak (see archive.update_health)
# three half-hourly passes in a row: an hour from the first failed pass to the third
FAIL_STREAK_ALARM = 3


class ReadFailed(Exception):
    """ERDDAP gave the pass nothing to work with: the dataset's newest date did
    not come, or neither basin's subset did. The lane's streak counts it."""


def subset_url(box) -> str:
    """The CSV subset of the newest day over one box. The product's latitude
    runs north to south, so the latitude range is written from the north."""
    w, s, e, n = box
    q = "%s[(last)][(%s):%d:(%s)][(%s):%d:(%s)]" % (VAR, n, STRIDE, s, w, STRIDE, e)
    return ERDDAP + ".csv?" + urllib.parse.quote(q, safe="(),:-.")


def latest_url() -> str:
    return ERDDAP + ".csv?" + urllib.parse.quote("time[(last)]", safe="(),:-.")


def parse_latest(text: str) -> Optional[str]:
    """'2026-10-05T12:00:00Z' -> '2026-10-05' from ERDDAP's two header lines and one value."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 3 or not lines[2][:10].count("-") == 2:
        return None
    return lines[2][:10]


def parse_grid(text: str) -> dict:
    """One basin's grid from ERDDAP's CSV (time, latitude, longitude, value,
    after two header lines), in the row order ERDDAP writes: north to south,
    and west to east within a row."""
    lats, lons, vals = [], [], []
    seen_lat, seen_lon = set(), set()
    day = None
    for i, ln in enumerate(text.splitlines()):
        if i < 2 or not ln.strip():
            continue
        parts = ln.split(",")
        if len(parts) < 4:
            continue
        day = day or parts[0][:10]
        la, lo = float(parts[1]), float(parts[2])
        if la not in seen_lat:
            seen_lat.add(la)
            lats.append(la)
        if lo not in seen_lon:
            seen_lon.add(lo)
            lons.append(lo)
        try:
            v = float(parts[3])
        except ValueError:
            v = float("nan")
        vals.append(v)
    nx, ny = len(lons), len(lats)
    if not nx or not ny or len(vals) != nx * ny:
        raise ValueError("grid is %d x %d but carries %d values" % (nx, ny, len(vals)))
    cells = bytearray(nx * ny)
    for k, v in enumerate(vals):
        if v != v or math.isinf(v):
            b = MISSING
        else:
            b = max(-127, min(127, int(round(v * 10))))
        cells[k] = b & 0xFF
    step = round(abs(lats[1] - lats[0]), 4) if ny > 1 else round(abs(lons[1] - lons[0]), 4)
    return {"day": day, "lat0": lats[0], "lon0": lons[0], "step": step, "nx": nx, "ny": ny,
            "data": base64.b64encode(bytes(cells)).decode("ascii")}


def decode(cells_b64: str) -> list:
    """The signed tenths a grid carries, None where missing (tests and checks)."""
    out = []
    for b in base64.b64decode(cells_b64):
        s = b - 256 if b > 127 else b
        out.append(None if s == MISSING else s / 10.0)
    return out


def sst_job(cfg: dict, store: Storage, now: dt.datetime, fetch: Optional[Callable[[str], str]] = None,
            deadline: Optional[arch.Deadline] = None) -> dict:
    fetch = fetch or (lambda url: gw._get_text(url, timeout=150))
    raw = store.get(KEY)
    prev = json.loads(raw) if raw else {}
    checked = prev.get("checked")
    if checked and prev.get("basins"):
        try:
            age_h = (now - dt.datetime.fromisoformat(checked.replace("Z", "+00:00"))).total_seconds() / 3600
        except ValueError:
            age_h = CHECK_HOURS
        if age_h < CHECK_HOURS:
            return {"written": False, "note": "checked %.1f h ago" % age_h, "asked": False}
    errors: list = []
    try:
        latest = parse_latest(fetch(latest_url()))
    except Exception as e:  # noqa: BLE001 - ERDDAP's answer, counted in the lane's streak
        raise ReadFailed(f"{type(e).__name__}: {e}") from None
    if not latest:
        raise ReadFailed("the dataset's newest date could not be read")
    basins = dict(prev.get("basins") or {})
    if latest == prev.get("asof") and all(b in basins for b in BOXES):
        snap = dict(prev, checked=_iso(now))
        store.put(KEY, json.dumps(snap, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
        return {"written": True, "fetched": 0, "asof": latest, "note": "no newer day"}
    fetched = 0
    for b, box in BOXES.items():
        if deadline is not None and deadline.over(60):
            errors.append(f"{b}: not enough time left in the pass")
            continue
        try:
            g = parse_grid(fetch(subset_url(box)))
            basins[b] = dict(g, box=list(box))
            fetched += 1
        except Exception as e:  # noqa: BLE001 - the other basin and the previous grid stand
            errors.append(f"{b}: {type(e).__name__}: {e}")
    if not fetched:
        raise ReadFailed("; ".join(errors) or "nothing fetched")
    days = sorted({g.get("day") for g in basins.values() if g.get("day")})
    snap = {"schema": SCHEMA, "asof": days[-1] if days else latest, "checked": _iso(now), "fetched": _iso(now),
            "source": SOURCE, "units": "degC", "scale": 0.1, "missing": MISSING, "basins": basins}
    store.put(KEY, json.dumps(snap, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
    return {"written": True, "fetched": fetched, "asof": snap["asof"], "errors": errors}


def sst_pass(cfg: dict, store: Storage) -> int:
    gw.set_user_agent(cfg.get("user_agent", ""))
    now = dt.datetime.now(dt.timezone.utc)
    t0 = time.time()
    deadline = arch.Deadline(arch.remaining_budget(cfg))
    # A pass that ERDDAP gives nothing is reported and asked again on the next pass, and does not fail the pass
    # (owner, 2026-11-14). A missed check leaves the last grid standing, and the product lands about two days after its date, so one failed
    # pass costs the map nothing. From 15 October to 14 November 2026 ERDDAP failed the lane on 79 of 1,440 passes,
    # in ten runs: eight of one or two passes, each cleared by the next pass or a retry, and two outages of 47 and 23
    # passes. Each failed pass failed the half-hourly chain and had Lambda run the whole chain twice more, and 8 of
    # the 38 pipeline alarm emails of those 30 days followed one. The lane's failure streak raises the alarm once
    # ERDDAP has failed FAIL_STREAK_ALARM passes in a row, which only the two outages did. A pass inside CHECK_HOURS
    # asks nothing and leaves the streak as it is. A basin that fails while the other is fetched still fails the
    # pass, because that basin is not asked for again until a newer day lands, and so does the job's own failure,
    # which no read is to blame for.
    failed = 0
    read_error = None
    own = False
    try:
        out = sst_job(cfg, store, now, deadline=deadline)
        errors = failed = len(out.get("errors") or [])
    except ReadFailed as e:
        out = {"written": False, "error": str(e)}
        errors = 1
        read_error = out["error"]
    except Exception as e:  # noqa: BLE001 - a missed day leaves the last grid standing
        out = {"written": False, "error": f"{type(e).__name__}: {e}"}
        errors = failed = 1
        own = True                              # the job's own failure leaves the streak as it is
    alarms: list = []
    if out.get("asked", True) and not own:
        try:
            health = arch.update_health(store, {"erddap": {"ok": read_error is None, "error": read_error}},
                                        now, key=HEALTH_KEY)
            out["failStreak"] = (health.get("erddap") or {}).get("fail_streak", 0)
            if out["failStreak"] >= FAIL_STREAK_ALARM:
                alarms = ["sst"]
        except Exception as e:  # noqa: BLE001 - a streak that cannot be kept is the job's own failure
            out["healthError"] = f"{type(e).__name__}: {e}"
            failed = 1
    out.update({"kind": "sst", "seconds": round(time.time() - t0, 1)})
    arch.LAST_STATUS = {"job": "sst", "errors": errors, "alarms": alarms}
    print(json.dumps(out))
    return failed
