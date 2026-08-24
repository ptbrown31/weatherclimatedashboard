"""
reask.py — the vendor lane for live-storm wind probabilities, off by default.

Reask (reask.earth) publishes, for the exchange's hurricane wind contracts,
the probability that the peak 3-second gust at each of 163 reference
locations exceeds each of a ladder of thresholds (60 to 200 mph), four
cycles a day while a storm is active ("LiveCyc"), then an interim and a
final settlement file ("Metryc"). It is the one non-government weather
source on this site, and it is gated twice:

  - `sources.reask` in config/site.json must be true, and
  - the API base URL and key must be present (WX_REASK_BASE_URL and
    WX_REASK_API_KEY in the environment; neither is in the repository).
    Without all three, the pass writes a status snapshot that says the lane
    is off and touches nothing else.

Terms, from the vendor's agreement with the exchange: every figure that shows
this data carries the "Powered by Reask" mark, and a site that displays it
pulls with its own credential. The pages show the vendor's numbers as
published; nothing here widens, holds or combines them.

Requests are GET with the key in a header, over https only, and redirects
are refused so the key is never re-sent to another host. A listing is
{"storms": [{"storm_name", "storm_year", "forecasts": [{"forecast_datetime",
"last_modified"}], "last_modified"}]}; a probability file is CSV with one row
per reference location (ID, Display Location, Latitude, Longitude, covered,
prob_<N>mph in percent); the final file carries PeakGust_mph instead.

Every file fetched is archived once (archive/reask/{storm}_{year}/...) and
the snapshot carries the latest cycle per storm. The lane was built against
the documented shapes without a live storm to test on; the parser is unit
tested on a synthetic file, and the first real delivery should be checked
against the archive by hand.
"""
from __future__ import annotations
import csv
import datetime as dt
import gzip
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

from . import archive as arch
from . import exchange as ex
from . import gov_weather as gw
from .snapshots import SNAP_CACHE, _iso
from .storage import Storage

SCHEMA = 2
KEY = "snapshots/reask.json"
STORM_KEY = "snapshots/storm/{name}_{year}.json"
ATTRIBUTION = "Powered by Reask"

# One storm's delivery ledger is written to its own file, because it grows with
# every delivery and only a reader who opens that storm needs it; the index in
# reask.json stays small enough for the hurricane page to load every pass.
# Caps follow the internal desk's: the most recent LiveCyc cycles, and the sites
# that have actually signalled, so a long-lived storm cannot grow without bound.
MAX_STEPS = 48
MAX_SITES = 60


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the key to whatever host answers; refuse it."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"redirect refused (to {newurl.split('?')[0]})", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect())


def _get(base: str, path: str, api_key: str, params: Optional[dict] = None, timeout: int = 30) -> bytes:
    if not base.lower().startswith("https://"):
        raise ValueError("the vendor base URL must be https")
    url = base.rstrip("/") + path + (("?" + urllib.parse.urlencode(params)) if params else "")
    req = urllib.request.Request(url, headers={"x-api-key": api_key, "User-Agent": gw.USER_AGENT, "Accept": "*/*"})
    with _OPENER.open(req, timeout=timeout) as r:
        return r.read()


def parse_ladder_csv(text: str) -> dict:
    """{"thresholds": [60, 70, ...], "sites": {ID: {"name", "lat", "lon", "p": [..percent..]}},
    "rows": n} from a LiveCyc or interim file. Sites whose every probability
    is zero are dropped (they carry nothing to draw); `rows` keeps the count."""
    rd = csv.DictReader(io.StringIO(text))
    cols = rd.fieldnames or []
    thr = sorted((int(m.group(1)), c) for c in cols for m in [re.match(r"prob_(\d+)mph$", c)] if m)
    sites, n = {}, 0
    for row in rd:
        n += 1
        sid = (row.get("ID") or "").strip()
        if not sid:
            continue
        ps = []
        for _, col in thr:
            try:
                ps.append(round(float(row.get(col) or 0), 2))
            except ValueError:
                ps.append(0.0)
        if not any(p > 0 for p in ps):
            continue
        sites[sid] = {"name": (row.get("Display Location") or "").strip(),
                      "lat": _num(row.get("Latitude")), "lon": _num(row.get("Longitude")),
                      "covered": (row.get("covered") or "").strip().lower() in ("true", "1", "yes"), "p": ps}
    return {"thresholds": [t for t, _ in thr], "sites": sites, "rows": n}


def parse_final_csv(text: str) -> dict:
    """{ID: {"name", "peakGustMph"}} from the historical (final) file."""
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        sid = (row.get("ID") or "").strip()
        v = _num(row.get("PeakGust_mph"))
        if sid and v is not None:
            out[sid] = {"name": (row.get("Display Location") or "").strip(), "peakGustMph": v}
    return out


def _num(v) -> Optional[float]:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def append_step(store: Storage, name: str, year: int, step: dict, log: Callable) -> dict:
    """Append one delivery to a storm's ledger and return its summary.

    The ledger is append-only and keyed by the delivery's own identity (the
    forecast time for a cycle, the file's last-modified stamp for an interim or
    final), so re-reading a file the vendor re-issued replaces that delivery
    rather than adding a duplicate, and a delivery that arrives late still lands
    in its own place. Nothing here looks forward: a step records only what that
    delivery said."""
    key = STORM_KEY.format(name=name, year=year)
    raw = store.get(key)
    doc = json.loads(raw) if raw else {"schema": SCHEMA, "name": name, "year": year, "attribution": ATTRIBUTION,
                                       "thresholds": [], "steps": [], "sites": {}, "final": None}
    doc["thresholds"] = step.get("thresholds") or doc.get("thresholds") or []
    steps = [x for x in doc.get("steps") or [] if x.get("id") != step["id"]]
    steps.append({k: v for k, v in step.items() if k != "thresholds"})
    steps.sort(key=lambda x: (x.get("kind") == "final", x.get("kind") == "interim", x.get("id") or ""))
    # keep the most recent cycles, always keeping any interim and the final
    cycles = [x for x in steps if x.get("kind") == "livecyc"]
    if len(cycles) > MAX_STEPS:
        drop = {id(x) for x in cycles[:-MAX_STEPS]}
        steps = [x for x in steps if id(x) not in drop]
    doc["steps"] = steps
    # a site enters the ledger the first time it carries a non-zero probability,
    # and keeps its place afterwards, so cards do not appear and vanish
    for sid, meta in (step.get("siteMeta") or {}).items():
        doc["sites"].setdefault(sid, {**meta, "firstStep": step["id"]})
    if len(doc["sites"]) > MAX_SITES:
        peak = {sid: max((max(x.get("sites", {}).get(sid) or [0]) for x in steps), default=0) for sid in doc["sites"]}
        for sid in sorted(peak, key=lambda k: -peak[k])[MAX_SITES:]:
            doc["sites"].pop(sid, None)
    if step.get("kind") == "final":
        doc["final"] = step.get("final")
    doc["written"] = step.get("ts")
    store.put(key, json.dumps(doc, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
    log(kind="reask", step=step["id"], storm=name, sites=len(step.get("sites") or {}), steps=len(steps), bytes=len(json.dumps(doc)))
    return {"steps": len(steps), "sites": len(doc["sites"]), "latest": step["id"], "final": doc.get("final") is not None}


def l_prices(store: Storage, storm: str, sids) -> dict:
    """{site: {threshold: cents}} — the exchange's Yes price for this storm's
    per-location gust contracts at this moment, recorded with the delivery it
    arrived alongside. Without it a reader scrubbing back through the deliveries
    would see an old ladder against today's price, which is not a comparison."""
    raw = store.get("snapshots/market/hurricane.json")
    if not raw:
        return {}
    try:
        doc = json.loads(raw)
    except ValueError:
        return {}
    code = ex.storm_code(storm)
    if not code:
        return {}
    pre, out = "L" + code, {}
    for m in doc.get("markets") or []:
        sym = str(m.get("symbol") or "")
        if not sym.startswith(pre) or len(sym) != len(pre) + 2:
            continue
        site = sym[len(pre):]
        if sids and site not in sids:
            continue
        row = {}
        for c in m.get("contracts") or []:
            try:
                t = int(float(c.get("strike")))
            except (TypeError, ValueError):
                continue
            if c.get("mid") is not None:
                row[str(t)] = round(float(c["mid"]) * 100, 1)
        if row:
            out[site] = row
    return out


def _step(lad: dict, kind: str, sid: str, at, ts: str, prices: Optional[dict] = None) -> dict:
    """One delivery as the ledger stores it: the probability ladder per site, as
    percentages in the file's own threshold order. Deliberately nothing else.
    The advisory's sustained wind is a one-minute mean and these contracts settle
    on a peak three-second gust; carrying the two together invites a comparison
    between different measurements, so the ledger does not."""
    sites = {k: v.get("p") or [] for k, v in (lad.get("sites") or {}).items()}
    meta = {k: {"name": v.get("name"), "lat": v.get("lat"), "lon": v.get("lon")} for k, v in (lad.get("sites") or {}).items()}
    return {"id": sid, "kind": kind, "at": at, "ts": ts, "prices": prices or {},
            "sites": sites, "siteMeta": meta, "thresholds": lad.get("thresholds")}


def _stamp(s: str) -> str:
    """'2026-09-01T06:00:00Z' -> '2026090106' for archive names."""
    digits = re.sub(r"\D", "", s or "")
    return digits[:10] if len(digits) >= 10 else digits


def reask_job(cfg: dict, store: Storage, log: Callable, now: dt.datetime, fetch: Optional[Callable] = None) -> dict:
    rcfg = cfg.get("reask") or {}
    enabled = bool((cfg.get("sources") or {}).get("reask"))
    api_key = (rcfg.get("api_key") or "").strip()
    base = (rcfg.get("base_url") or "").strip()
    prev_raw = store.get(KEY)
    prev = json.loads(prev_raw) if prev_raw else {}
    if not enabled or not api_key or not base:
        reason = "lane off in config" if not enabled else ("no credential configured" if not api_key else "no base URL configured")
        snap = {"schema": SCHEMA, "enabled": False, "reason": reason, "written": _iso(now), "asof": None,
                "attribution": ATTRIBUTION, "storms": []}
        store.put(KEY, json.dumps(snap, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
        log(kind="reask", enabled=False, reason=reason)
        return {"enabled": False, "attempted": False}

    fetch = fetch or (lambda path, params=None: _get(base, path, api_key, params))
    year = now.year
    errors = []
    storms = {s["name"] + "_" + str(s["year"]): s for s in prev.get("storms") or []}
    fetched = 0

    def current(listing: dict) -> list:
        return [s for s in (listing or {}).get("storms") or [] if str(s.get("storm_year")) == str(year)]

    # ---- LiveCyc: the latest forecast cycle per current-year storm
    try:
        live = json.loads(fetch("/livecyc/tcwind/list").decode())
    except Exception as e:  # noqa: BLE001
        errors.append(f"livecyc list: {type(e).__name__}: {e}")
        live = None
    for s in current(live) if live else []:
        name, fcs = s.get("storm_name"), s.get("forecasts") or []
        if not name or not fcs:
            continue
        latest = max(fcs, key=lambda f: f.get("forecast_datetime") or "")
        ft = latest.get("forecast_datetime")
        key = f"{name}_{year}"
        entry = storms.setdefault(key, {"name": name, "year": year, "livecyc": None, "interim": None, "final": None})
        if (entry.get("livecyc") or {}).get("forecastTime") == ft and (entry.get("livecyc") or {}).get("lastModified") == latest.get("last_modified"):
            continue
        akey = f"archive/reask/{name}_{year}/livecyc_{_stamp(ft)}.csv.gz"
        try:
            body = fetch("/livecyc/tcwind/probabilities", {"storm_name": name, "storm_year": year, "forecast_start_time": ft, "format": "csv"})
            store.put(akey, gzip.compress(body), "application/gzip")        # a re-delivered cycle replaces its file
            lad = parse_ladder_csv(body.decode("utf-8", "replace"))
            entry["livecyc"] = {"forecastTime": ft, "lastModified": latest.get("last_modified"), "fetched": _iso(now),
                                "cycles": len(fcs), **lad}
            entry["ledger"] = append_step(store, name, year,
                                          _step(lad, "livecyc", _stamp(ft) or ft, ft, _iso(now),
                                                l_prices(store, name, set((lad.get("sites") or {}).keys()))), log)
            fetched += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"livecyc {name} {ft}: {type(e).__name__}: {e}")

    # ---- Metryc interim and final, refetched when the listing says they changed
    for kind, lpath, ppath, parse in (("interim", "/metryc/interim/tcwind/list", "/metryc/interim/tcwind/probabilities", parse_ladder_csv),
                                      ("final", "/metryc/historical/tcwind/list", "/metryc/historical/tcwind/peak_gust", parse_final_csv)):
        try:
            listing = json.loads(fetch(lpath).decode())
        except Exception as e:  # noqa: BLE001
            errors.append(f"{kind} list: {type(e).__name__}: {e}")
            continue
        for s in current(listing):
            name = s.get("storm_name")
            if not name:
                continue
            key = f"{name}_{year}"
            entry = storms.setdefault(key, {"name": name, "year": year, "livecyc": None, "interim": None, "final": None})
            lm = s.get("last_modified")
            if (entry.get(kind) or {}).get("lastModified") == lm and entry.get(kind):
                continue
            akey = f"archive/reask/{name}_{year}/{kind}_{_stamp(lm) or 'latest'}.csv.gz"
            try:
                body = fetch(ppath, {"storm_name": name, "storm_year": year, "format": "csv"})
                store.put(akey, gzip.compress(body), "application/gzip")
                parsed = parse(body.decode("utf-8", "replace"))
                entry[kind] = {"lastModified": lm, "fetched": _iso(now), **(parsed if kind == "interim" else {"sites": parsed})}
                if kind == "interim":
                    entry["ledger"] = append_step(store, name, year,
                                                  _step(parsed, "interim", "INT", lm, _iso(now),
                                                        l_prices(store, name, set((parsed.get("sites") or {}).keys()))), log)
                else:
                    entry["ledger"] = append_step(store, name, year,
                                                  {"id": "FINAL", "kind": "final", "at": lm, "ts": _iso(now),
                                                   "sites": {}, "siteMeta": {}, "thresholds": None,
                                                   "final": {k: v.get("peakGustMph") for k, v in parsed.items()}}, log)
                fetched += 1
            except Exception as e:  # noqa: BLE001
                errors.append(f"{kind} {name}: {type(e).__name__}: {e}")

    # storms with a final older than two weeks leave the snapshot
    keep = []
    for s in storms.values():
        f = s.get("final") or {}
        if f.get("fetched"):
            try:
                age = (now - dt.datetime.fromisoformat(f["fetched"].replace("Z", "+00:00"))).days
            except ValueError:
                age = 0
            if age > 14:
                continue
        keep.append(s)
    asof = max([(s.get("livecyc") or {}).get("forecastTime") or "" for s in keep] + [prev.get("asof") or ""]) or None
    ok = live is not None
    snap = {"schema": SCHEMA, "enabled": True, "attribution": ATTRIBUTION, "asof": asof, "written": _iso(now),
            "polled": _iso(now) if ok else prev.get("polled"), "year": year, "storms": keep, "fetched": fetched, "errors": errors,
            "ledgerKey": STORM_KEY}
    store.put(KEY, json.dumps(snap, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
    log(kind="reask", enabled=True, storms=len(keep), fetched=fetched, errors=errors)
    return {"enabled": True, "attempted": True, "ok": ok, "storms": len(keep), "fetched": fetched, "errors": errors}


def reask_pass(cfg: dict, store: Storage) -> int:
    gw.set_user_agent(cfg.get("user_agent", ""))
    now = dt.datetime.now(dt.timezone.utc)
    t0 = time.time()
    entries: list = []

    def log(**kw):
        kw["t"] = _iso(dt.datetime.now(dt.timezone.utc))
        entries.append(kw)
        print(json.dumps(kw, default=str))

    errors, alarms = 0, []
    try:
        out = reask_job(cfg, store, log, now)
        if out.get("attempted"):
            # a failed poll counts toward the streak alarm; it is not an invocation error, because the
            # quote job in the same invocation has already done its work
            health = arch.update_health(store, {"reask": {"ok": bool(out.get("ok")), "error": "; ".join(out.get("errors") or [])[:300] or None}}, now,
                                        key=arch.MARKET_HEALTH_KEY)
            if (health.get("reask") or {}).get("fail_streak", 0) >= arch.FAIL_STREAK_ALARM:
                alarms = ["reask"]
    except Exception as e:  # noqa: BLE001
        errors = 1
        log(kind="reask", error=f"{type(e).__name__}: {e}")
    arch.LAST_STATUS = {"job": "reask", "errors": errors, "alarms": alarms, "seconds": round(time.time() - t0, 1), "entries": entries}
    print(f"reask: {errors} errors, alarms {alarms or 'none'}, {round(time.time() - t0, 1)}s -> {store.describe()}")
    return errors
