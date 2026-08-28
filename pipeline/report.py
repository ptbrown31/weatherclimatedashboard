"""The daily traffic report, as plain text.

The traffic job already counts a finished day from the CDN's own access logs
and keeps a running series. This composes that series into something readable
in a mail client and hands it to the caller. It does not send anything. Mail
delivery is specific to where the site is deployed, so it lives in handler.py
with the rest of that; this file only decides what the message says.

The report is plain text on purpose. A mail client shows it in a fixed-width
font, which is what makes the bar chart line up, and there is no rendering,
no images and nothing to load. It reads the same in every client.

Counting follows traffic.py, and the same two caveats travel with the numbers.
A page view is a request for a document rather than for one of the dozen
objects a page pulls. A visitor is a distinct client address, which undercounts
an office behind one address and overcounts a phone moving between towers, so
it is a trend and not a headcount.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import List, Optional

from . import archive as arch
from . import traffic
from .storage import Storage

SCHEMA = 1
KEY = "archive/_meta/report/traffic-latest.json"
BAR_WIDTH = 34            # columns the longest bar fills, inside a narrow mail window


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def bars(days: List[dict], width: int = BAR_WIDTH) -> List[str]:
    """One line per day: the date, the count, and a bar scaled to the busiest.

    Scaled to the series rather than to a fixed ceiling, so the shape of a
    quiet week is as readable as a busy one. The number is printed as well as
    drawn, because a bar alone cannot be read off precisely and the exact
    figure is the point of the report.
    """
    if not days:
        return []
    top = max((d.get("views") or 0) for d in days) or 1
    out = []
    for d in days:
        v = d.get("views") or 0
        n = int(round((v / top) * width))
        out.append("  {day}  {views:>6}  {bar}".format(
            day=d.get("day", "?"), views=v, bar="#" * n))
    return out


def trend(days: List[dict]) -> Optional[str]:
    """The last seven days against the seven before them, when both exist.

    A single day says almost nothing on a site this size, where one link can
    treble a Tuesday. A week against the week before is the shortest window
    that survives that.
    """
    if len(days) < 14:
        return None
    recent = [d.get("views") or 0 for d in days[-7:]]
    prior = [d.get("views") or 0 for d in days[-14:-7]]
    a, b = sum(recent), sum(prior)
    if b == 0:
        return "{a} views over the last seven days, against none in the seven before.".format(a=a)
    pct = (a - b) / b * 100
    word = "up" if pct >= 0 else "down"
    return ("{a} views over the last seven days against {b} in the seven before, "
            "{word} {pct:.0f} per cent.").format(a=a, b=b, word=word, pct=abs(pct))


def compose(summary: dict, now: dt.datetime) -> dict:
    """{subject, body} for the report, or a body saying why there is nothing."""
    days = list(summary.get("days") or [])
    latest = summary.get("latest") or {}
    day = latest.get("day") or (days[-1].get("day") if days else None)

    if not days:
        return {"subject": "Weather tools site: no traffic counted yet",
                "body": "The traffic job has not counted a finished day yet. Nothing to report.\n"}

    views = latest.get("views") or 0
    visitors = latest.get("visitors")
    lines = []
    lines.append("Daily visits to weather.weatherclimatehumansystems.org")
    lines.append("")
    lines.append("{day}: {views} page views from {vis} distinct addresses.".format(
        day=day, views=views, vis=visitors if visitors is not None else "an unknown number of"))
    t = trend(days)
    if t:
        lines.append(t)
    lines.append("")
    lines.append("  date          views")
    lines.extend(bars(days[-30:]))
    lines.append("")

    pages = latest.get("pages") or []
    if pages:
        lines.append("Most read on {day}:".format(day=day))
        for p in pages[:8]:
            lines.append("  {v:>5}  {path}".format(v=p.get("views") or 0, path=p.get("path") or "?"))
        lines.append("")

    tot = summary.get("totals") or {}
    if tot:
        lines.append("Since counting began: {v} page views across {d} day{s}.".format(
            v=tot.get("views") or 0, d=len(days), s="" if len(days) == 1 else "s"))
    if latest.get("botRequests"):
        lines.append("{n} requests on {day} came from declared crawlers and are not counted above.".format(
            n=latest["botRequests"], day=day))
    lines.append("")
    lines.append("A page view is a request for a document, not for the dozen objects a page pulls.")
    lines.append("A visitor is a distinct client address, which undercounts an office behind one")
    lines.append("address and overcounts a phone moving between towers, so it is a trend and not")
    lines.append("a headcount. Counted from the CDN's own access logs; no cookies, no identifiers,")
    lines.append("no third-party analytics.")
    lines.append("")
    lines.append("Counted {at}.".format(at=_iso(now)))

    return {"subject": "Site visits {day}: {views} views".format(day=day, views=views),
            "body": "\n".join(lines) + "\n"}


def report_pass(cfg: dict, store: Storage) -> int:
    """Compose the report and leave it for the caller to send.

    The message is written to the archive as well, so a report that failed to
    send is still recoverable and the last one sent can be read back.
    """
    now = dt.datetime.now(dt.timezone.utc)
    raw = store.get(traffic.SUMMARY_KEY)
    summary = json.loads(raw) if raw else {}
    msg = compose(summary, now)
    doc = {"schema": SCHEMA, "written": _iso(now), "subject": msg["subject"], "body": msg["body"]}
    store.put(KEY, json.dumps(doc, separators=(",", ":")).encode(), "application/json")
    # handler.py sends this where the site is deployed; nothing here knows how
    arch.LAST_REPORT = msg
    arch.LAST_STATUS = {"job": "report", "errors": 0, "alarms": []}
    print(json.dumps({"kind": "report", "subject": msg["subject"],
                      "days": len(summary.get("days") or []), "bytes": len(msg["body"])}))
    return 0
