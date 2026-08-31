"""
iem.py — the one archive of past guidance bulletins that reaches back.

NOMADS serves the MOS and LAMP cycles that are current and drops them after
about two days, and no NOAA endpoint returns an earlier one, so a day this
site did not capture is a day gone. Iowa State University's Environmental
Mesonet keeps every cycle. What it returns is the National Weather Service's
own guidance, parsed out of the same bulletins; IEM is where the old ones are
kept, not another forecaster.

This is an exception to the US-government-only rule, taken deliberately and
narrowly, and CLAUDE.md records it. Two things keep it narrow. It is used only
for days before this site's own archive begins, never for a day the archive
could have covered itself, and every level it fills is written with its source
on it, so a value from here can always be told from one this site captured.

The levels are derived here rather than at read time, because what IEM returns
is already parsed and reconstructing a bulletin from it to re-parse would be a
round trip through a format neither side needs. The day mapping is the site's
own: pipeline/snapshots.py owns the convention that a MOS 00Z column is the
maximum of the day that ended there, and this calls into it rather than
restating it.
"""
from __future__ import annotations
import datetime as dt
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

API = "https://mesonet.agron.iastate.edu/api/1/mos.json"

# the site's kind -> IEM's model name. GFS is the MAV bulletin, LAV the LAMP one
MODEL = {"mav": "GFS", "lamp": "LAV"}
TIMEOUT = 30


def _get(url: str, ua: str) -> Optional[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": ua or "weather-tools-site"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, ValueError, OSError):
        return None


def rows(station: str, kind: str, runtime: dt.datetime, ua: str = "") -> list:
    """One cycle for one station, as IEM holds it. Empty when it has none."""
    q = urllib.parse.urlencode({"station": station, "model": MODEL[kind],
                                "runtime": runtime.strftime("%Y-%m-%dT%H:%MZ")})
    body = _get(API + "?" + q, ua)
    return (body or {}).get("data") or []


def _t(s: str) -> Optional[dt.datetime]:
    try:
        return dt.datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def extremes(data: list) -> dict:
    """A MOS cycle's N/X columns, in the shape snapshots._extremes_by_day reads.

    The column at 00Z is the maximum of the day that ended there and the one at
    12Z that morning's minimum, which is the bulletin's own convention and the
    same one the live parser applies.
    """
    out = []
    for r in data:
        v, t = r.get("n_x"), _t(r.get("ftime"))
        if v is None or t is None:
            continue
        out.append({"time": t, "kind": "max" if t.hour == 0 else "min", "temp_f": float(v)})
    return {"extremes": out}


def hourly(data: list) -> list:
    """A LAMP cycle's hourly temperatures, in the shape the site's own hourly
    rows take, so the day's extreme is taken over the same hours."""
    out = []
    for r in data:
        v, t = r.get("tmp"), _t(r.get("ftime"))
        if v is None or t is None:
            continue
        out.append({"t": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "tempF": float(v)})
    return out
