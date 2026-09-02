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
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

from . import archive as arch
from . import basemap
from . import exchange as ex
from . import gov_weather as gw
from .snapshots import SNAP_CACHE, _iso, day_markers
from .storage import Storage

SCHEMA = 1
SOURCE = "ForecastEx public market data"
HISTORY_HOURS = 48          # per-strike quote history carried in a station snapshot
QUOTE_WORKERS = 4           # measured 2026-08-23: no throttling at four concurrent requests
PREFIX = "snapshots/market/"


def _cents(v: Optional[float]) -> Optional[int]:
    return None if v is None else int(round(v * 100))


def _cut_short(row: dict) -> bool:
    """Whether this row is missing because the pass stopped, not because the
    contract had no book. Both reasons — the deadline, and the breaker giving
    up on an unreachable exchange — mean the ladder is incomplete and the
    previous snapshot should stand."""
    err = str((row or {}).get("error") or "")
    return err == "deadline" or err.startswith("BreakerOpen")


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
               "conid": slot.get("Y") or slot.get("N"), "conidYes": slot.get("Y"), "conidNo": slot.get("N")}
        if deadline.over(5):
            row.update(ex.yes_quote(None, None)); row["error"] = "deadline"; row["mid"] = None
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
        # numbers first in numeric order, then named strikes alphabetically
        out[d].sort(key=lambda r: (0, r["strike"], "") if isinstance(r["strike"], (int, float)) else (1, 0, str(r["strike"])))
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
                if r.get("error"):                 # a failed request is not a 'no book' sample
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


LHL_MAX_POINTS = 3000          # a storm's pool lives days; this holds weeks at the quote cadence
LHL_MAX_AGE_DAYS = 60          # and a pool symbol comes round again with its storm's name, years later

# One pool's opening quotes ruled out of the public display (the owner's call,
# 2026-09-01): Edouard's market opened on a pair-normalised 95/5 book that had
# nothing to do with the field and was corrected within hours. Points at or
# before the cutoff are dropped on every write, so a rebuild cannot put them
# back.
LHL_DROP_BEFORE = {("LHLED", 2026): "2026-09-01T17:30:00Z"}

# A pool whose series is not the exchange's quotes at all: the same ruling
# that governs a storm's ledger (pipeline/reask.py, OVERRIDES_DIR) can carry
# a "pools" block, one point per delivery, and that block is the series,
# written in place of the quotes on every pass so nothing can overwrite it.
# A pool symbol is the storm's first two letters and carries no year, and
# the name list comes round again every six years, so a ruling reaches a
# pool only in the year the ruling names.
OVERRIDES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "overrides")


def lhl_override(symbol: str, year) -> Optional[list]:
    """The points a ruling substitutes for one pool's series in one year, or None."""
    try:
        names = sorted(os.listdir(OVERRIDES_DIR))
    except OSError:
        return None
    for fn in names:
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(OVERRIDES_DIR, fn), encoding="utf-8") as f:
                ov = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(ov, dict) or str(ov.get("year")) != str(year):
            continue
        pts = (ov.get("pools") or {}).get(symbol)
        if isinstance(pts, list):
            return pts
    return None


def _lhl_series(store: Storage, now: dt.datetime, items: List[dict]) -> None:
    """The highest-wind pools' prices, kept through time.

    A pool contract's Yes price is the market's own probability that its
    location takes the pool, and how that moved through a storm is worth as
    much as where it stands. The quote archive holds every pass, but a page
    cannot read a season of gzipped passes, so each pool gets a small series
    of its own.

    One point per pass, not per hour. An hourly sample missed the largest
    move Edouard's pool made all day, a flip from 95 to 23 cents that fell
    between two samples, which is exactly the event the series exists to
    show. Points are keyed by the contract's LABEL, the place name, because
    the strike is an index and a chart legend reading "2.0" names nothing;
    the page also matches the stated calculation to these lines by name.
    """
    for m in items:
        if not str(m.get("symbol", "")).startswith("LHL"):
            continue
        key = f"snapshots/lhl/{m['symbol']}.json"
        ruled = lhl_override(m["symbol"], now.year)
        if ruled is not None:
            store.put(key, json.dumps({"schema": 1, "symbol": m["symbol"], "name": m.get("name"),
                                       "asof": _iso(now), "override": True, "points": ruled},
                                      separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
            continue
        try:
            doc = json.loads(store.get(key) or b"null") or {}
        except (ValueError, TypeError):
            doc = {}
        pts = doc.get("points") or []
        # Both sides of the book, not the midpoint. The exchange's own screen
        # shows the two bids to buy, so a page carrying one derived number
        # cannot be checked against it; a reader comparing 54 against a screen
        # reading 51 has no way to see that both are the same book.
        prices = {}
        for c in m.get("contracts") or []:
            nm = c.get("label") or (None if c.get("strike") is None else str(c["strike"]))
            if not nm:
                continue
            b, a = c.get("bid"), c.get("ask")
            if b is None and a is None:
                continue
            prices[str(nm)] = [None if b is None else round(float(b) * 100, 1),
                               None if a is None else round(float(a) * 100, 1)]
        if not prices:
            continue
        stamp = _iso(now)
        # points from another season under the same symbol are another storm's
        floor = _iso(now - dt.timedelta(days=LHL_MAX_AGE_DAYS))
        pts = [q for q in pts if q.get("t") != stamp and (q.get("t") or "") >= floor]
        pts.append({"t": stamp, "p": prices})
        pts.sort(key=lambda q: q["t"])
        cut = LHL_DROP_BEFORE.get((m["symbol"], now.year))
        if cut:
            pts = [q for q in pts if q.get("t", "") > cut]
        if len(pts) > LHL_MAX_POINTS:
            pts = pts[-LHL_MAX_POINTS:]
        store.put(key, json.dumps({"schema": 1, "symbol": m["symbol"], "name": m.get("name"),
                                   "asof": stamp, "points": pts},
                                  separators=(",", ":")).encode(), "application/json", SNAP_CACHE)


def quotes_job(cfg: dict, store: Storage, log: Callable, now: dt.datetime, deadline: Optional[arch.Deadline] = None) -> dict:
    if not (cfg.get("sources") or {}).get("exchange", True):
        log(kind="market", skipped="sources.exchange is off")
        return {"skipped": True, "quoted": 0, "failed": 0, "stations": 0}
    deadline = deadline or arch.Deadline(arch.remaining_budget(cfg))
    # one breaker for this pass: when the exchange stops answering the pass
    # stops asking, rather than spending its whole budget timing out
    breaker = ex.Breaker()
    fetch = breaker.guard(ex.fetch_quote)
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
    unmatched = {}                               # sid -> the symbols derived but absent from the tree
    for c in roster:
        syms = ex.symbols_for(c)
        wanted[c["station"]] = {side: sym for side, sym in syms.items() if sym in bysym}
        if not wanted[c["station"]]:
            unmatched[c["station"]] = sorted(syms.values())
    if unmatched:
        # a station whose derived symbol is not in the tree is either not listed or listed under a
        # code this mapping does not know (exchange.CODE_OVERRIDES); the summary names them so the
        # difference can be checked against the tree by hand
        log(kind="market", step="unmatched", stations=unmatched)
    hur_markets = ex.category_markets(tree, ex.HURRICANE_CATEGORY)
    # a live storm's own wind markets are listed under a category this job cannot
    # know in advance, so they are matched by storm code off the NHC roster and
    # the vendor lane's storms, and added to the hurricane group either way
    names = set()
    for key in ("snapshots/hurricane.json", "snapshots/reask.json"):
        raw = store.get(key)
        if not raw:
            continue
        try:
            for st_ in (json.loads(raw).get("storms") or []):
                if st_.get("name"):
                    names.add(st_["name"])
        except ValueError:
            pass
    have = {m["symbol"] for m in hur_markets}
    wind = [m for m in ex.storm_wind_markets(tree, sorted(names)) if m["symbol"] not in have]
    if wind:
        log(kind="market", step="storm-wind", storms=sorted(names), markets=[m["symbol"] for m in wind])
    hur_markets = hur_markets + wind
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
        rows = _quote_rows(grouped, fetch, deadline)
        st = by_station.setdefault(key, {"markets": {}, "days": {}, "listed": {}, "partial": False})
        st["markets"][side] = {"symbol": m["symbol"], "name": mkt.get("market_name") or m.get("name"),
                               "conid": m["conid"], "productConid": m.get("productConid")}
        st["listed"][side] = sorted(grouped.keys())
        for day, rs in rows.items():
            st["days"].setdefault(day, {})[side] = rs
            for r in rs:
                quoted += r.get("error") is None
                failed += r.get("error") is not None
                if _cut_short(r):
                    st["partial"] = True
                archive_rows.append({"station": key, "day": day, "side": side, **{k: r.get(k) for k in ("strike", "conid", "bid", "ask", "bidSize", "askSize", "from")}})

    groups: Dict[str, List[dict]] = {"hurricane": [], "climate": []}
    for kind, key, side, m, mkt, err in listed:
        if kind == "station" or mkt is None:
            continue
        # products with monthly or yearly specifiers are grouped by the raw specifier
        # instead of a weather day. A strike is not always a number: the exchange's
        # "which location records the highest wind" contracts carry a place name as
        # the strike, so the key is the number when there is one and the strike's
        # own label otherwise, and the row reports which it was.
        spec_groups: Dict[str, Dict[Any, dict]] = {}
        for cn in mkt.get("contracts") or []:
            spec = str(cn.get("time_specifier") or "")
            key_, num = ex.strike_key(cn)
            if key_ is None:
                continue
            slot = spec_groups.setdefault(spec, {}).setdefault(key_, {"label": cn.get("strike_label"), "expiration": cn.get("expiration"),
                                                                      "expiryLabel": cn.get("expiry_label"), "numeric": num})
            sd = str(cn.get("side") or "").upper()
            if sd in ("Y", "N") and cn.get("conid"):
                slot[sd] = cn["conid"]
        rows = _quote_rows(spec_groups, fetch, deadline)
        contracts = []
        for spec, rs in rows.items():
            for r in rs:
                quoted += r.get("error") is None
                failed += r.get("error") is not None
                meta = spec_groups[spec][r["strike"]]
                contracts.append({"spec": spec, "expiryLabel": meta.get("expiryLabel"), "numeric": meta.get("numeric"), **r})
                archive_rows.append({"market": m["symbol"], "spec": spec, **{k: r.get(k) for k in ("strike", "conid", "bid", "ask", "bidSize", "askSize", "from")}})
        groups[kind].append({"symbol": m["symbol"], "name": mkt.get("market_name") or m.get("name"), "conid": m["conid"],
                             "productConid": m.get("productConid"),
                             "category": m.get("category"), "seriesKey": ex.CLIMATE_SYMBOLS.get(m["symbol"]),
                             "contracts": contracts})
    log(kind="market", step="quotes", quoted=quoted, failed=failed, seconds=round(time.time() - t0, 1),
        deadline=deadline.over(0))

    if quoted == 0:
        raise RuntimeError(f"no quote succeeded ({failed} failed, {len(list_errors)} contract lists failed)")

    # ---- write: per station, summary, groups, archive
    summary_rows = []
    partial_kept: List[str] = []
    for c in roster:
        sid = c["station"]
        st = by_station.get(sid)
        prev_raw = store.get(f"{PREFIX}{sid}.json")
        prev = json.loads(prev_raw) if prev_raw else None
        if st is not None and st["partial"]:
            # the deadline cut this ladder short: a partial ladder would carry a fresh as-of time and a
            # median computed from the strikes that happened to come first, so the previous snapshot
            # stands (or, with none, nothing is written) and the summary row says so
            partial_kept.append(sid)
            mk = day_markers(c, now)
            summary_rows.append({"station": sid, "listed": True, "symbols": {s: m["symbol"] for s, m in st["markets"].items()},
                                 "asof": prev.get("asof") if prev else None, "partial": True,
                                 "day": mk["day"], "tomorrow": mk["tomorrow"]})
            continue
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

    for name, items in groups.items():
        cut = any(_cut_short(c) for m in items for c in m.get("contracts") or [])
        if cut and store.get(f"{PREFIX}{name}.json"):
            partial_kept.append(name)
            log(kind="market", step="group", group=name, kept="previous snapshot: the deadline cut this group's quotes short")
            continue
        store.put(f"{PREFIX}{name}.json", json.dumps(_group_snapshot(name, now, items), separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
        if name == "hurricane":
            _lhl_series(store, now, items)
    summary = {"schema": SCHEMA, "source": SOURCE, "asof": _iso(now), "written": _iso(now), "quoted": quoted, "failed": failed,
               "listErrors": list_errors[:20], "unmatched": unmatched, "partialKept": partial_kept,
               "deadline": bool(partial_kept), "cities": summary_rows}
    store.put(f"{PREFIX}summary.json", json.dumps(summary, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
    stamp = now.strftime("%Y%m%d/%H%M%S")
    store.put(f"archive/market/{stamp}.json.gz",
              gzip.compress(json.dumps({"asof": _iso(now), "rows": archive_rows}, separators=(",", ":")).encode()), "application/gzip")
    """The pass reached the exchange, and separately: did it finish?

    A pass the deadline cuts short keeps the previous ladders rather than
    writing a half-read one, which is the right call — but every snapshot it
    leaves behind still carries a plausible as-of time, so nothing downstream
    can tell that the prices stopped moving. Reported as ok, that state was
    invisible: the streak never advanced and no alarm could fire.

    Completeness is therefore its own source. It stays distinguishable from
    the exchange being unreachable, and it escalates on the same rule as any
    other source — FAIL_STREAK_ALARM passes in a row, an hour at this job's
    ten-minute cadence, so one slow pass is noise and an hour of them is not.
    """
    health = arch.update_health(store, {
        "exchange": {"ok": True},
        "quotes-complete": {
            "ok": not partial_kept,
            "error": (("deadline cut " + str(len(partial_kept)) + " ladder"
                       + ("" if len(partial_kept) == 1 else "s") + " short: "
                       + ", ".join(partial_kept[:8])) if partial_kept else None),
        },
    }, now, key=arch.MARKET_HEALTH_KEY)
    if breaker.opened:
        log(kind="market", step="breaker", opened=True, failures=breaker.failures,
            note="the exchange stopped answering; the pass was abandoned rather than timing out on every call")
    log(kind="market", step="written", stations=len(by_station), partialKept=partial_kept,
        groups={k: len(v) for k, v in groups.items()}, archiveRows=len(archive_rows))
    return {"quoted": quoted, "failed": failed, "stations": len(by_station), "health": health}


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
        res = quotes_job(cfg, store, log, now)
        # a pass can succeed and still be behind: alarms are read from the
        # streaks, not from whether this one raised
        alarms = arch.alarms_in(res.get("health"))
    except Exception as e:  # noqa: BLE001 - recorded, counted toward the streak
        errors = 1
        log(kind="market", error=f"{type(e).__name__}: {e}")
        health = arch.update_health(store, {"exchange": {"ok": False, "error": f"{type(e).__name__}: {e}"}}, now, key=arch.MARKET_HEALTH_KEY)
        if (health.get("exchange") or {}).get("fail_streak", 0) >= arch.FAIL_STREAK_ALARM:
            alarms = ["exchange"]
    arch.LAST_STATUS = {"job": "quotes", "errors": errors, "alarms": alarms, "seconds": round(time.time() - t0, 1), "entries": entries}
    print(f"quotes: {errors} errors, alarms {alarms or 'none'}, {round(time.time() - t0, 1)}s -> {store.describe()}")
    return errors
