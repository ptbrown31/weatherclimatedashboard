"""
market.py — the exchange quote job: top of book for every listed contract the
site shows, written as small snapshots the pages read, and an append-only
record of every pass.

What one pass does (every 10 minutes, its own schedule):

  1. the category tree (one request; new listings such as a storm's wind
     contracts appear in it, so it is fetched every pass, not cached);
  2. for each roster station, the daily high and low markets' contract
     lists, kept to the station's local today and tomorrow;
  3. the Yes-side quote of every contract on those days, plus the hurricane
     and climate groups, on a small thread pool;
  4. per station: snapshots/market/{SID}.json with the ladders, the market-
     implied medians and a rolling two-day history of each strike's quote,
     carried forward from the previous snapshot (no listing needed);
  5. snapshots/market/summary.json (implied medians for the map),
     snapshots/market/hurricane.json, snapshots/market/climate.json;
  6. archive/market/{YYYYMMDD}/{HHMMSS}.json.gz, every quote of the pass.

Prices are the exchange's own, in dollars per contract (0-1), not fee
adjusted; `mid` is what the pages call the implied probability. A pass
that cannot reach the exchange leaves every snapshot as it was, and the
failure counts toward the same streak alarm the weather sources use.
"""
from __future__ import annotations
import datetime as dt
import gzip
import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional

from . import archive as arch
from . import basemap
from . import exchange as ex
from . import gov_weather as gw
from .snapshots import SNAP_CACHE, _iso, day_markers
from .storage import Storage

SCHEMA = 1
SOURCE = "ForecastEx public market data"
HISTORY_HOURS = 48          # per-strike quote history carried in a station snapshot
QUOTE_WORKERS = 4           # measured 2026-08-23: no throttling at four; the internal capture uses ten
PREFIX = "snapshots/market/"


def _cents(v: Optional[float]) -> Optional[int]:
    return None if v is None else int(round(v * 100))


def _quote_rows(contracts: Dict[str, Dict[float, dict]], fetch: Callable, deadline: arch.Deadline) -> Dict[str, List[dict]]:
    """Quote every strike of every day in `contracts` (grouped by
    exchange.group_contracts). Returns {day: [row]} with rows in strike
    order; a row whose quote failed carries bid/ask None and an error."""
    jobs = []
    for day, strikes in contracts.items():
        for strike, slot in sorted(strikes.items()):
            jobs.append((day, strike, slot))

    def one(job):
        day, strike, slot = job
        row = {"strike": strike, "label": slot.get("label"), "expiration": slot.get("expiration"),
               "conid": slot.get("Y") or slot.get("N")}
        if deadline.over(5):
            row.update(ex.yes_quote(None, None)); row["error"] = "deadline"
            return day, row
        try:
            qy = fetch(slot["Y"]) if slot.get("Y") else None
            qn = None
            if not (qy and ("bid" in qy or "ask" in qy)) and slot.get("N"):
                qn = fetch(slot["N"])
            row.update(ex.yes_quote(qy, qn))
        except Exception as e:  # noqa: BLE001 - one bad quote must not sink the ladder
            row.update(ex.yes_quote(None, None)); row["error"] = f"{type(e).__name__}: {e}"
        row["mid"] = ex.mid(row)
        return day, row

    out: Dict[str, List[dict]] = {d: [] for d in contracts}
    with ThreadPoolExecutor(max_workers=QUOTE_WORKERS) as pool:
        for day, row in pool.map(one, jobs):
            out[day].append(row)
    for d in out:
        out[d].sort(key=lambda r: r["strike"])
    return out


def _carry_history(prev: Optional[dict], days: dict, now: dt.datetime, keep_days: set) -> dict:
    """history[day][side][strike] = [[minuteOfEpoch, bidCents, askCents], ...],
    the previous snapshot's series plus this pass's sample, trimmed to the
    days still shown and the last HISTORY_HOURS."""
    hist = dict(((prev or {}).get("history") or {}))
    t = int(now.timestamp() // 60)
    cutoff = t - HISTORY_HOURS * 60
    for day, sides in days.items():
        for side, rows in sides.items():
            if side not in ("high", "low"):
                continue
            hs = hist.setdefault(day, {}).setdefault(side, {})
            for r in rows:
                if r.get("error") == "deadline":
                    continue
                key = str(r["strike"]).rstrip("0").rstrip(".") if "." in str(r["strike"]) else str(r["strike"])
                ser = hs.setdefault(key, [])
                if ser and ser[-1][0] == t:
                    continue
                ser.append([t, _cents(r.get("bid")), _cents(r.get("ask"))])
    out = {}
    for day, sides in hist.items():
        if day not in keep_days:
            continue
        kept = {}
        for side, series in sides.items():
            ks = {k: [s for s in v if s[0] >= cutoff] for k, v in series.items()}
            ks = {k: v for k, v in ks.items() if v}
            if ks:
                kept[side] = ks
        if kept:
            out[day] = kept
    return out


def _station_snapshot(c: dict, now: dt.datetime, markets: dict, days: dict, prev: Optional[dict], listed: dict) -> dict:
    mk = day_markers(c, now)
    keep = {mk["yesterday"], mk["day"], mk["tomorrow"]}
    implied = {}
    for day, sides in days.items():
        implied[day] = {side: ex.implied_median(rows, side) for side, rows in sides.items()}
    return {"schema": SCHEMA, "station": c["station"], "city": c.get("city"), "unit": c.get("unit"), "tz": c.get("tz"),
            "source": SOURCE, "asof": _iso(now), "written": _iso(now),
            "symbols": markets, "listed": listed, "markers": {"day": mk["day"], "tomorrow": mk["tomorrow"], "yesterday": mk["yesterday"]},
            "days": days, "implied": implied,
            "history": _carry_history(prev, days, now, keep)}


def _group_snapshot(name: str, now: dt.datetime, items: List[dict]) -> dict:
    return {"schema": SCHEMA, "group": name, "source": SOURCE, "asof": _iso(now), "written": _iso(now), "markets": items}


def quotes_job(cfg: dict, store: Storage, log: Callable, now: dt.datetime, deadline: Optional[arch.Deadline] = None) -> dict:
    deadline = deadline or arch.Deadline(arch.remaining_budget(cfg))
    xcfg = cfg.get("exchange") or {}
    ex.set_base_url(xcfg.get("base_url") or ex.BASE_URL)
    global QUOTE_WORKERS
    QUOTE_WORKERS = int(xcfg.get("quote_workers") or QUOTE_WORKERS)
    roster = basemap.load_roster()
    t0 = time.time()

    tree = ex.fetch_tree()                      # raises: the pass fails as a whole, snapshots untouched
    bysym = ex.markets_by_symbol(tree)
    log(kind="market", step="tree", markets=len(bysym))

    # ---- contract lists: one request per listed market, concurrently
    wanted = {}                                  # sid -> {side: symbol} for symbols the tree lists
    for c in roster:
        syms = ex.symbols_for(c)
        wanted[c["station"]] = {side: sym for side, sym in syms.items() if sym in bysym}
    hur_markets = ex.category_markets(tree, ex.HURRICANE_CATEGORY)
    clim_markets = [bysym[s] for s in ex.CLIMATE_SYMBOLS if s in bysym]
    to_list = [("station", sid, side, bysym[sym]) for sid, ss in wanted.items() for side, sym in ss.items()]
    to_list += [("hurricane", m["symbol"], None, m) for m in hur_markets]
    to_list += [("climate", m["symbol"], None, m) for m in clim_markets]

    def list_one(item):
        kind, key, side, m = item
        try:
            return kind, key, side, m, ex.fetch_market(m["conid"]), None
        except Exception as e:  # noqa: BLE001
            return kind, key, side, m, None, f"{type(e).__name__}: {e}"

    with ThreadPoolExecutor(max_workers=QUOTE_WORKERS) as pool:
        listed = list(pool.map(list_one, to_list))
    list_errors = [f"{k}:{key}:{err}" for k, key, _, _, _, err in listed if err]
    log(kind="market", step="contracts", markets=len(listed), errors=len(list_errors))

    # ---- the quotes
    quoted = 0
    failed = 0
    archive_rows: List[dict] = []
    by_station: Dict[str, dict] = {}
    for kind, key, side, m, mkt, err in listed:
        if kind != "station" or mkt is None:
            continue
        c = next(x for x in roster if x["station"] == key)
        mk = day_markers(c, now)
        grouped = ex.group_contracts(mkt, {mk["day"], mk["tomorrow"]})
        rows = _quote_rows(grouped, ex.fetch_quote, deadline)
        st = by_station.setdefault(key, {"markets": {}, "days": {}, "listed": {}})
        st["markets"][side] = {"symbol": m["symbol"], "name": mkt.get("market_name") or m.get("name"), "conid": m["conid"]}
        st["listed"][side] = sorted(grouped.keys())
        for day, rs in rows.items():
            st["days"].setdefault(day, {})[side] = rs
            for r in rs:
                quoted += r.get("error") is None
                failed += r.get("error") is not None
                archive_rows.append({"station": key, "day": day, "side": side, **{k: r.get(k) for k in ("strike", "conid", "bid", "ask", "bidSize", "askSize", "from")}})

    groups: Dict[str, List[dict]] = {"hurricane": [], "climate": []}
    for kind, key, side, m, mkt, err in listed:
        if kind == "station" or mkt is None:
            continue
        grouped_all = ex.group_contracts(mkt, None)
        # products with monthly or yearly specifiers are grouped by the raw specifier instead
        spec_groups: Dict[str, Dict[float, dict]] = {}
        for cn in mkt.get("contracts") or []:
            spec = str(cn.get("time_specifier") or "")
            try:
                strike = float(cn.get("strike"))
            except (TypeError, ValueError):
                continue
            slot = spec_groups.setdefault(spec, {}).setdefault(strike, {"label": cn.get("strike_label"), "expiration": cn.get("expiration"),
                                                                        "expiryLabel": cn.get("expiry_label")})
            sd = str(cn.get("side") or "").upper()
            if sd in ("Y", "N") and cn.get("conid"):
                slot[sd] = cn["conid"]
        rows = _quote_rows(spec_groups, ex.fetch_quote, deadline)
        contracts = []
        for spec, rs in rows.items():
            for r in rs:
                quoted += r.get("error") is None
                failed += r.get("error") is not None
                contracts.append({"spec": spec, "expiryLabel": (spec_groups[spec][r["strike"]]).get("expiryLabel"), **r})
                archive_rows.append({"market": m["symbol"], "spec": spec, **{k: r.get(k) for k in ("strike", "conid", "bid", "ask", "bidSize", "askSize", "from")}})
        groups[kind].append({"symbol": m["symbol"], "name": mkt.get("market_name") or m.get("name"), "conid": m["conid"],
                             "category": m.get("category"), "seriesKey": ex.CLIMATE_SYMBOLS.get(m["symbol"]),
                             "contracts": contracts})
        del grouped_all
    log(kind="market", step="quotes", quoted=quoted, failed=failed, seconds=round(time.time() - t0, 1),
        deadline=deadline.over(0))

    if quoted == 0:
        raise RuntimeError(f"no quote succeeded ({failed} failed, {len(list_errors)} contract lists failed)")

    # ---- write: per station, summary, groups, archive
    summary_rows = []
    for c in roster:
        sid = c["station"]
        st = by_station.get(sid)
        prev_raw = store.get(f"{PREFIX}{sid}.json")
        prev = json.loads(prev_raw) if prev_raw else None
        if st is None:
            # not listed today (or its contract lists failed): keep the previous
            # snapshot if there is one, else write an explicit unlisted one
            if prev and any(k for k in wanted.get(sid, {})):
                summary_rows.append({"station": sid, "listed": False, "symbols": wanted.get(sid, {}), "asof": prev.get("asof")})
                continue
            snap = {"schema": SCHEMA, "station": sid, "city": c.get("city"), "unit": c.get("unit"), "tz": c.get("tz"), "source": SOURCE,
                    "asof": _iso(now), "written": _iso(now), "symbols": {}, "listed": {}, "days": {}, "implied": {}, "history": {},
                    "markers": {k: day_markers(c, now)[k] for k in ("day", "tomorrow", "yesterday")}}
            store.put(f"{PREFIX}{sid}.json", json.dumps(snap, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
            summary_rows.append({"station": sid, "listed": False, "symbols": {}, "asof": snap["asof"]})
            continue
        snap = _station_snapshot(c, now, st["markets"], st["days"], prev, st["listed"])
        store.put(f"{PREFIX}{sid}.json", json.dumps(snap, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
        mk = snap["markers"]
        row = {"station": sid, "listed": True, "symbols": {s: m["symbol"] for s, m in st["markets"].items()}, "asof": snap["asof"],
               "day": mk["day"], "tomorrow": mk["tomorrow"]}
        for when, day in (("Today", mk["day"]), ("Tomorrow", mk["tomorrow"])):
            for side in ("high", "low"):
                im = (snap["implied"].get(day) or {}).get(side) or {}
                row[f"implied{side.capitalize()}{when}"] = im.get("value")
                row[f"implied{side.capitalize()}{when}Edge"] = im.get("edge")
                rs = (st["days"].get(day) or {}).get(side) or []
                row[f"quoted{side.capitalize()}{when}"] = sum(1 for r in rs if r.get("mid") is not None)
        summary_rows.append(row)

    summary = {"schema": SCHEMA, "source": SOURCE, "asof": _iso(now), "written": _iso(now), "quoted": quoted, "failed": failed,
               "listErrors": list_errors[:20], "cities": summary_rows}
    store.put(f"{PREFIX}summary.json", json.dumps(summary, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
    for name, items in groups.items():
        store.put(f"{PREFIX}{name}.json", json.dumps(_group_snapshot(name, now, items), separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
    stamp = now.strftime("%Y%m%d/%H%M%S")
    store.put(f"archive/market/{stamp}.json.gz",
              gzip.compress(json.dumps({"asof": _iso(now), "rows": archive_rows}, separators=(",", ":")).encode()), "application/gzip")
    arch.update_health(store, {"exchange": {"ok": True}}, now)
    log(kind="market", step="written", stations=len(by_station), groups={k: len(v) for k, v in groups.items()}, archiveRows=len(archive_rows))
    return {"quoted": quoted, "failed": failed, "stations": len(by_station)}


def quotes_pass(cfg: dict, store: Storage) -> int:
    """Entry point: one pass, status 0 unless nothing at all succeeded."""
    gw.set_user_agent(cfg.get("user_agent", ""))
    now = dt.datetime.now(dt.timezone.utc)
    t0 = time.time()
    entries: list = []

    def log(**kw):
        kw["t"] = _iso(dt.datetime.now(dt.timezone.utc))
        entries.append(kw)
        print(json.dumps(kw, default=str))

    errors = 0
    alarms: list = []
    try:
        quotes_job(cfg, store, log, now)
    except Exception as e:  # noqa: BLE001 - recorded, counted toward the streak
        errors = 1
        log(kind="market", error=f"{type(e).__name__}: {e}")
        health = arch.update_health(store, {"exchange": {"ok": False, "error": f"{type(e).__name__}: {e}"}}, now)
        if (health.get("exchange") or {}).get("fail_streak", 0) >= arch.FAIL_STREAK_ALARM:
            alarms = ["exchange"]
    arch.LAST_STATUS = {"job": "quotes", "errors": errors, "alarms": alarms, "seconds": round(time.time() - t0, 1), "entries": entries}
    print(f"quotes: {errors} errors, alarms {alarms or 'none'}, {round(time.time() - t0, 1)}s -> {store.describe()}")
    return errors
