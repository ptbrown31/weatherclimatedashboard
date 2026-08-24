"""Who read the site, counted from the CDN's own access logs.

CloudFront writes one gzipped, tab-separated file per batch of requests. This
job reads the files for a finished UTC day, counts what a person actually did,
and writes a small summary. It is deliberately not an analytics product: no
cookies, no identifiers, no third-party service, and nothing that reaches the
browser. Everything here comes from logs the CDN was already writing, which is
why the site needs no consent banner.

Two counting decisions carry the whole file, and both are the honest reading
rather than the flattering one:

  A page view is an HTML request, not a request. A cold view of one page pulls
  about a dozen objects (the document, the stylesheet, five or six scripts, the
  JSON snapshots, the map geometry), so counting requests would overstate
  readership by roughly an order of magnitude. Only the document counts.

  A visitor is a distinct client address. That undercounts an office behind one
  address and overcounts a phone moving between towers, so the number is a
  trend line and not a headcount. The summary says so in its own text rather
  than leaving a reader to assume precision that is not there.

The summary is written under the archive prefix, which the bucket policy denies
to CloudFront, so traffic figures are readable from the account and never from
the public site.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import time
from collections import Counter
from typing import Dict, List, Optional
from urllib.parse import unquote_plus, urlsplit

from . import archive as arch
from .storage import Storage

SCHEMA = 1
DAY_KEY = "archive/_meta/traffic/{day}.json"
SUMMARY_KEY = "archive/_meta/traffic/summary.json"
SUMMARY_DAYS = 90
MAX_FILES = 400          # a day's logs at this traffic; a runaway is capped, not silently truncated

# A document request is what a person asked for; everything else is the page
# fetching its own parts. Directory requests reach the origin already rewritten
# to index.html by the CloudFront function, so both spellings appear.
DOC_SUFFIXES = (".html", "/")

# Substrings, lower-cased, matched against the user agent. Deliberately short
# and explicit: a list nobody can read is a list nobody can correct.
BOT_MARKS = ("bot", "crawler", "spider", "slurp", "bingpreview", "headlesschrome",
             "python-requests", "curl/", "wget/", "go-http-client", "java/",
             "okhttp", "libwww", "scrapy", "monitoring", "uptime", "pingdom",
             "statuscake", "semrush", "ahrefs", "dataprovider", "facebookexternalhit")


def _is_bot(agent: str) -> bool:
    a = agent.lower()
    return any(m in a for m in BOT_MARKS)


def _is_doc(uri: str) -> bool:
    return uri.endswith(DOC_SUFFIXES) or uri == "/"


def parse_log(raw: bytes) -> List[dict]:
    """One CloudFront log file to a list of request records.

    The field order is read from the file's own '#Fields:' header rather than
    assumed, because the position of a column is not a promise CloudFront makes.
    A file whose header is missing yields nothing instead of silently mapping
    the wrong column onto the wrong meaning.
    """
    try:
        text = gzip.decompress(raw).decode("utf-8", "replace")
    except Exception:
        return []
    fields: List[str] = []
    out: List[dict] = []
    for line in text.splitlines():
        if line.startswith("#Fields:"):
            fields = line.split(":", 1)[1].split()
            continue
        if line.startswith("#") or not line.strip():
            continue
        if not fields:
            continue
        parts = line.split("\t")
        if len(parts) != len(fields):
            continue
        out.append(dict(zip(fields, parts)))
    return out


def _host(referer: str) -> str:
    if not referer or referer == "-":
        return ""
    try:
        return (urlsplit(referer).hostname or "").lower()
    except ValueError:
        return ""


def summarise(records: List[dict], day: str) -> dict:
    """Records for one day to the numbers worth keeping."""
    views = 0
    ips = set()
    pages: Counter = Counter()
    refs: Counter = Counter()
    embed = 0
    standalone = 0
    bots = 0
    requests = 0
    bytes_out = 0

    for r in records:
        requests += 1
        try:
            bytes_out += int(r.get("sc-bytes") or 0)
        except ValueError:
            pass
        status = (r.get("sc-status") or "")
        agent = unquote_plus(r.get("cs(User-Agent)") or "")
        if _is_bot(agent):
            bots += 1
            continue
        if not status.startswith(("2", "3")):
            continue
        uri = r.get("cs-uri-stem") or ""
        if not _is_doc(uri):
            continue
        views += 1
        ip = r.get("c-ip") or ""
        if ip:
            ips.add(ip)
        pages[uri] += 1
        if uri.startswith("/embed"):
            embed += 1
        else:
            standalone += 1
        host = _host(unquote_plus(r.get("cs(Referer)") or ""))
        if host:
            refs[host] += 1

    return {
        "day": day,
        "views": views,
        "visitors": len(ips),
        "requests": requests,
        "botRequests": bots,
        "megabytes": round(bytes_out / 1_048_576, 1),
        "embedViews": embed,
        "siteViews": standalone,
        "pages": [{"path": p, "views": n} for p, n in pages.most_common(20)],
        "referrers": [{"host": h, "views": n} for h, n in refs.most_common(15)],
    }


def _log_store(cfg: dict) -> Optional[Storage]:
    """Storage pointed at the log bucket, which is not the site bucket.

    Returns None when no log bucket is configured, which is the state of a
    clean checkout and of any deployment that has not turned logging on. That
    is not an error; the job simply has nothing to read.
    """
    tcfg = cfg.get("traffic") or {}
    bucket = (tcfg.get("log_bucket") or "").strip()
    if not bucket:
        return None
    st = cfg.get("storage", {})
    if st.get("backend") == "local":
        from .storage import LocalStorage
        return LocalStorage(bucket)
    from .storage import S3Storage
    return S3Storage(bucket, tcfg.get("log_prefix", "").strip("/"), st.get("endpoint", ""), st.get("region", ""))


def traffic_pass(cfg: dict, store: Storage) -> int:
    t0 = time.time()
    now = dt.datetime.now(dt.timezone.utc)
    tcfg = cfg.get("traffic") or {}
    logs = _log_store(cfg)
    if logs is None:
        print(json.dumps({"kind": "traffic", "written": False, "reason": "no log bucket configured",
                          "seconds": round(time.time() - t0, 1)}))
        arch.LAST_STATUS = {"job": "traffic", "errors": 0, "alarms": []}
        return 0

    # Yesterday, in UTC, because a day still running would publish a figure that
    # grows every time the job is run and would never match itself.
    day = (now.date() - dt.timedelta(days=1)).isoformat()
    errors: List[str] = []
    records: List[dict] = []
    files = 0
    try:
        # CloudFront names each file "<distribution>.YYYY-MM-DD-HH.<hash>.gz", so
        # the day is selectable without opening anything.
        keys = [k for k in logs.list("") if ("." + day + "-") in k and k.endswith(".gz")]
        for key in keys[:MAX_FILES]:
            raw = logs.get(key)
            if raw:
                records.extend(parse_log(raw))
                files += 1
        if len(keys) > MAX_FILES:
            errors.append(f"traffic: {len(keys) - MAX_FILES} log files past the {MAX_FILES} cap were not read")
    except Exception as e:  # noqa: BLE001
        errors.append(f"traffic: {type(e).__name__}: {e}")

    if not files and not records:
        # No logs for that day is normal the morning after logging is switched
        # on, and after a day the CDN served nothing. Neither is a failure.
        print(json.dumps({"kind": "traffic", "day": day, "written": False, "files": 0,
                          "errors": errors, "seconds": round(time.time() - t0, 1)}))
        arch.LAST_STATUS = {"job": "traffic", "errors": len(errors), "alarms": []}
        return 1 if errors else 0

    day_doc = summarise(records, day)
    day_doc.update({"schema": SCHEMA, "files": files, "written": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "note": "A view is an HTML document request, not a request; a cold page view pulls about a "
                            "dozen objects. A visitor is a distinct client address, which undercounts shared "
                            "networks and overcounts phones changing towers."})
    store.put(DAY_KEY.format(day=day), json.dumps(day_doc, separators=(",", ":")).encode(), "application/json")

    # the rolling summary, rebuilt from the day files so a re-run repairs it
    days: List[dict] = []
    try:
        for key in sorted(store.list("archive/_meta/traffic/"))[-SUMMARY_DAYS - 1:]:
            if key.endswith("summary.json"):
                continue
            raw = store.get(key)
            if not raw:
                continue
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            days.append({k: d.get(k) for k in ("day", "views", "visitors", "siteViews", "embedViews", "requests")})
    except Exception as e:  # noqa: BLE001
        errors.append(f"traffic summary: {type(e).__name__}: {e}")
    days = sorted(days, key=lambda d: d.get("day") or "")[-SUMMARY_DAYS:]
    totals = {k: sum(int(d.get(k) or 0) for d in days) for k in ("views", "siteViews", "embedViews", "requests")}
    summary = {"schema": SCHEMA, "written": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "days": days,
               "totals": totals, "latest": day_doc, "note": day_doc["note"]}
    store.put(SUMMARY_KEY, json.dumps(summary, separators=(",", ":")).encode(), "application/json")

    arch.LAST_STATUS = {"job": "traffic", "errors": len(errors), "alarms": []}
    print(json.dumps({"kind": "traffic", "day": day, "files": files, "views": day_doc["views"],
                      "visitors": day_doc["visitors"], "site": day_doc["siteViews"], "embed": day_doc["embedViews"],
                      "bots": day_doc["botRequests"], "errors": errors, "seconds": round(time.time() - t0, 1)}))
    return 1 if errors else 0
