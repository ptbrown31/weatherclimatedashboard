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
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

from . import archive as arch
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
SLIM_DAYS = 14                  # days after its final that a storm's index entry keeps only its stamps
# the most cycles one pass files for one storm; the rest follow on later passes
MAX_NEW_CYCLES = 8
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


def parse_ladder_csv(text: str, keep_zero: bool = False) -> dict:
    """{"thresholds": [60, 70, ...], "sites": {ID: {"name", "lat", "lon", "covered", "p": [..percent..]}},
    "rows": n} from a LiveCyc or interim file.

    A blank cell is no figure, and a row of blanks is a location the file does
    not reach: the vendor's interim is computed on a domain around the
    landfall and marks the locations inside it covered, leaving the rest
    empty. A row of printed zeros is a figure, nothing above the lowest
    threshold. On a forecast cycle that figure carries nothing to draw and the
    row is dropped. On an interim, which folds what the storm has already done
    into the ladder, a covered zero is the vendor saying the location saw
    nothing, and keep_zero keeps it; a blank row is never kept, whatever the
    flag says. `rows` counts every row the file carried."""
    rd = csv.DictReader(io.StringIO(text))
    cols = rd.fieldnames or []
    thr = sorted((int(m.group(1)), c) for c in cols for m in [re.match(r"prob_(\d+)mph$", c)] if m)
    sites, n = {}, 0
    for row in rd:
        n += 1
        sid = (row.get("ID") or "").strip()
        if not sid:
            continue
        ps, blank = [], 0
        for _, col in thr:
            raw = (row.get(col) or "").strip()
            try:
                v = float(raw)
                if not (0 <= v < float("inf")):        # a NaN or a negative is no figure either
                    raise ValueError(raw)
                ps.append(round(v, 2))
            except ValueError:
                ps.append(0.0)
                blank += 1
        covered = (row.get("covered") or "").strip().lower() in ("true", "1", "yes")
        if not any(p > 0 for p in ps) and not (keep_zero and covered and thr and blank == 0):
            continue
        sites[sid] = {"name": (row.get("Display Location") or "").strip(),
                      "lat": _num(row.get("Latitude")), "lon": _num(row.get("Longitude")),
                      "covered": covered, "p": ps}
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


# ---- a storm whose exchange series is not the exchange's quotes
#
# Edouard (2026) was the exchange's first live-storm listing. No contract
# traded and the quotes were not maintained through the storm, so the owner
# ruled (2026-09-02) that its recorded prices are the per-delivery prices the
# exchange was given to quote, for every strike such a price was produced for.
# The ruling is a file under pipeline/overrides/, one per storm, named
# {storm}_{year}.json, and it is applied on every write of the ledger so that
# neither a re-delivered cycle nor a rebuild can put the exchange's quotes
# back. A storm without a file carries the exchange's quotes as read, and
# nothing here touches it.
OVERRIDES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "overrides")


def price_override(name: str, year) -> Optional[dict]:
    """The owner's ruling for one storm, or None when there is none."""
    path = os.path.join(OVERRIDES_DIR, "%s_%s.json" % (str(name or "").lower(), year))
    try:
        with open(path, encoding="utf-8") as f:
            ov = json.load(f)
    except (OSError, ValueError):
        return None
    return ov if isinstance(ov, dict) else None


def fetch_desk_lhl(url: str, api_key: str = "", timeout: int = 15) -> Optional[bytes]:
    """The desk's pool-figure file, fetched from where the desk publishes it.

    Over https only and with redirects refused, the same handling the vendor
    lane gets, because this is another party's endpoint and a redirect is the
    cheap way to send a fetcher somewhere it did not mean to go. The desk
    serves the file rather than writing into this site's bucket: that keeps a
    credential off the desk box, and it keeps a file covering every storm the
    desk prices out of a bucket that CloudFront serves whole.
    """
    if not url:
        return None
    if not url.lower().startswith("https://"):
        raise ValueError("the desk feed URL must be https")
    headers = {"User-Agent": gw.USER_AGENT, "Accept": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    req = urllib.request.Request(url, headers=headers)
    with _OPENER.open(req, timeout=timeout) as r:
        return r.read()


def desk_pool_figures(raw: Optional[bytes]) -> dict:
    """The desk's pool figure per storm, keyed by (lowercased name, year).

    The site computes no pool figure of its own and never will. This is the
    desk's own calculation, delivered rather than derived, and the owner's
    decision of 24 September is that it stands in on the page while the
    exchange is not carrying a pool for that storm. The moment a pool lists,
    the page drops it for the exchange's price, which is the front end's
    business and not this job's.

    A missing or malformed file is an absence, not a failure: the page has a
    state for a storm with no figure, and it had nothing but that state until
    today.
    """
    try:
        doc = json.loads(raw or b"null")
    except Exception:  # noqa: BLE001 - an unreadable feed is an absence, not a failed pass
        return {}
    out = {}
    for s in ((doc or {}).get("storms") or []):
        nm, yr = str(s.get("name") or "").strip(), s.get("year")
        pw = s.get("pwin")
        if not nm or not isinstance(pw, dict) or not pw:
            continue
        # location id -> percent, dropping anything that is not a number, since
        # one bad row should not take the storm's whole figure with it
        vals = {str(k).upper(): float(v) for k, v in pw.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)}
        if not vals:
            continue
        # `note` is the desk's own sentence about what the figure is. It is
        # carried rather than written here because the site does not own the
        # model and should not be describing it from memory: the desk changes
        # the method, the desk changes the sentence, and the page prints
        # whatever it is handed.
        out[(nm.lower(), yr)] = {"pwin": vals, "cycle": s.get("cycle"), "method": s.get("method"),
                                 "note": s.get("note"), "asof": s.get("asof") or (doc or {}).get("asof")}
    return out


def apply_price_override(doc: dict, ov: Optional[dict]) -> bool:
    """Replace the recorded prices, and where the ruling carries one the pool
    figure, of every step the ruling names. Returns whether the document
    changed. Steps the ruling does not name keep what they recorded, so a
    delivery after the ruling carries the exchange's own quote for that
    delivery and the site's own calculation."""
    if not ov:
        return False
    want_by_id = ov.get("steps") or {}
    pwin_by_id = ov.get("pwin") or {}
    changed = False
    for st in doc.get("steps") or []:
        sid = str(st.get("id"))
        want = want_by_id.get(sid)
        if want is not None and st.get("prices") != want:
            st["prices"] = want
            changed = True
        pw = pwin_by_id.get(sid)
        if pw is not None and st.get("pwin") != pw:
            st["pwin"] = pw
            changed = True
    if doc.get("pricesFrom") != "override":
        doc["pricesFrom"] = "override"
        changed = True
    return changed


def latest_override_pwin(ov: Optional[dict]) -> Optional[dict]:
    """The ruling's pool figure at its newest delivery, for the index."""
    pw = (ov or {}).get("pwin") or {}
    for sid in sorted(pw, reverse=True):
        if pw[sid]:
            return pw[sid]
    return None


def absorb_ledger(store: Storage, name: str, year, into: str, log: Callable) -> dict:
    """Fold one storm's ledger into another's and retire the first.

    The depression's deliveries go in under the storm's name, keeping their
    own ids, so the merged ledger reads as one series from the first file;
    a delivery already present under the storm's name wins. A site keeps the
    earliest delivery it appeared on. The retired ledger is copied under the
    archive before it is removed, so nothing is lost, and the ruling for the
    absorbing storm is applied to the result."""
    src_key = STORM_KEY.format(name=name, year=year)
    dst_key = STORM_KEY.format(name=into, year=year)
    raw = store.get(src_key)
    if not raw:
        return {"absorbed": 0, "reason": "no ledger"}
    src = json.loads(raw)
    dst = json.loads(store.get(dst_key) or b"null") or {
        "schema": SCHEMA, "name": into, "year": year, "attribution": ATTRIBUTION,
        "thresholds": src.get("thresholds") or [], "steps": [], "sites": {}, "final": None}
    have = {st.get("id") for st in dst.get("steps") or []}
    # each folded delivery keeps the name it came under
    added = [dict(st, **{"as": st.get("as") or name}) for st in src.get("steps") or [] if st.get("id") not in have]
    steps = (dst.get("steps") or []) + added
    steps.sort(key=lambda x: (x.get("kind") == "final", x.get("kind") == "interim", x.get("id") or ""))
    dst["steps"] = steps
    dst["thresholds"] = dst.get("thresholds") or src.get("thresholds") or []
    dst.setdefault("sites", {})
    for sid, meta in (src.get("sites") or {}).items():
        cur = dst["sites"].get(sid)
        if not cur:
            dst["sites"][sid] = dict(meta)
        elif (meta.get("firstStep") or "") and (meta.get("firstStep") or "") < (cur.get("firstStep") or "~"):
            cur["firstStep"] = meta["firstStep"]
    if dst.get("final") is None and src.get("final") is not None:
        dst["final"] = src["final"]
    dst["absorbed"] = sorted(set(dst.get("absorbed") or []) | {name})
    apply_price_override(dst, price_override(into, year))
    store.put(dst_key, json.dumps(dst, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
    store.put("archive/storm/%s_%s.absorbed-into-%s.json" % (name, year, into), raw, "application/json")
    store.delete(src_key)
    log(kind="reask", step="absorb", storm=into, absorbed=name, steps=len(added))
    return {"absorbed": len(added), "steps": len(steps)}


def absorbed_storms() -> dict:
    """{absorbed storm key: absorbing storm key} across every ruling.

    A depression is named by its number and the vendor's files simply start
    arriving under the new name, so the two are one storm. A ruling that
    lists the number-word under "absorbs" has that storm's deliveries merged
    into its own ledger (scripts/apply_overrides.py) and keeps the vendor
    lane from carrying the old name as a storm of its own again."""
    out = {}
    try:
        names = sorted(os.listdir(OVERRIDES_DIR))
    except OSError:
        return out
    for fn in names:
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(OVERRIDES_DIR, fn), encoding="utf-8") as f:
                ov = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(ov, dict):
            continue
        for nm in ov.get("absorbs") or []:
            out["%s_%s" % (nm, ov.get("year"))] = "%s_%s" % (ov.get("storm"), ov.get("year"))
    return out


# A depression is named by its number, and on naming the vendor's files start
# arriving under the storm's name: Five became Edouard, Nine became Isaias. The
# two are one storm, so the number's deliveries are folded into the named
# storm's ledger and any cycle the vendor still files under the number goes
# there too, which keeps one series across the rename (owner's decision
# 2026-10-07). The join is the ATCF number the word spells, read from NHC's
# roster or this season's best tracks, so it needs no ruling; once made it is
# kept in the index and outlives the storm's time on the roster. Atlantic only,
# as the best tracks this site reads are. The figures are the vendor's as
# delivered, and each step keeps the name it was delivered under.
NUMWORDS = ("One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
            "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen",
            "Nineteen", "Twenty", "Twenty-One", "Twenty-Two", "Twenty-Three", "Twenty-Four", "Twenty-Five",
            "Twenty-Six", "Twenty-Seven", "Twenty-Eight", "Twenty-Nine", "Thirty")
# a number-word storm whose newest cycle is older than this is not joined to a
# storm named since: it is some other depression's record
RENAME_FRESH_DAYS = 14


def number_of(name) -> Optional[int]:
    """The depression number a vendor stem spells, 'Nine' -> 9, or None."""
    low = str(name or "").strip().lower()
    for i, w in enumerate(NUMWORDS, start=1):
        if low == w.lower():
            return i
    return None


def named_by_number(store: Storage, year) -> dict:
    """{9: 'Isaias'}: this year's Atlantic storms that carry a name, by ATCF
    number, from NHC's roster and, once a storm has left it, the season's best
    tracks (both in snapshots/hurricane.json)."""
    raw = store.get("snapshots/hurricane.json")
    try:
        h = json.loads(raw) if raw else {}
    except ValueError:
        h = {}
    rows = list(h.get("storms") or []) + list(((h.get("season") or {}).get("events") or {}).get("named") or [])
    out = {}
    for r in rows:
        m = re.match(r"^al(\d{2})(\d{4})$", str(r.get("id") or "").strip(), re.I)
        nm = str(r.get("name") or "").strip()
        if not m or int(m.group(2)) != int(year) or not nm or nm.startswith("#") or number_of(nm) is not None:
            continue
        out.setdefault(int(m.group(1)), nm)
    return out


def renamed_storms(prev: dict, vendor: dict, by_num: dict, year, now: dt.datetime) -> dict:
    """{'Nine_2026': 'Isaias_2026'}: the renames already made, kept, and any new
    one for a number-word storm whose number NHC has since named. vendor maps a
    storm the vendor or the index carries to its newest cycle time."""
    out = dict(prev.get("renamed") or {})
    for name, ft in vendor.items():
        key = "%s_%s" % (name, year)
        n = number_of(name)
        if key in out or n is None or n not in by_num:
            continue
        try:
            t = dt.datetime.fromisoformat(str(ft).replace("Z", "+00:00")) if ft else None
        except ValueError:
            t = None
        nw = now if now.tzinfo else now.replace(tzinfo=dt.timezone.utc)
        if t is not None and (nw - (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc))).days > RENAME_FRESH_DAYS:
            continue
        out[key] = "%s_%s" % (by_num[n], year)
    return out


def _carry_entry(storms: dict, old: dict, new_key: str, new_name: str) -> None:
    """Move a renamed storm's index entry onto its successor's, keeping the
    newest cycle of the two and noting the name each file came under."""
    def relabel(part):
        if not part:
            return part
        part = dict(part)
        part.setdefault("vendorName", old.get("name"))
        return part
    cur = storms.get(new_key)
    if cur is None:
        storms[new_key] = {**old, "name": new_name, "livecyc": relabel(old.get("livecyc")),
                           "interim": relabel(old.get("interim")), "final": relabel(old.get("final"))}
        return
    a, b = (cur.get("livecyc") or {}), (old.get("livecyc") or {})
    if (b.get("forecastTime") or "") > (a.get("forecastTime") or ""):
        cur["livecyc"] = relabel(b)
    for kind in ("interim", "final"):
        if not cur.get(kind) and old.get(kind):
            cur[kind] = relabel(old.get(kind))


def _prune_interim(step: dict, known) -> None:
    """Keep an interim's zero rows only for locations the ledger already has.

    The interim covers a whole domain, so a storm that touched four locations
    arrives with a hundred and sixty covered zeros. A zero is a figure for a
    location this storm's cards draw, where it closes the ladder the
    forecasts opened; for every other location it is a row about a place the
    page never mentions, and it would enter the ledger as a site with nothing
    to show. Rows above zero are always kept."""
    if step.get("kind") != "interim":
        return
    keep = {sid for sid, ps in (step.get("sites") or {}).items()
            if sid in known or any((p or 0) > 0 for p in (ps or []))}
    for field in ("sites", "siteMeta", "prices"):
        step[field] = {k: v for k, v in (step.get(field) or {}).items() if k in keep}


def append_step(store: Storage, name: str, year: int, step: dict, log: Callable) -> dict:
    """Append one delivery to a storm's ledger and return its summary.

    The ledger is append-only and keyed by the delivery's own identity: the
    forecast time for a cycle, and one standing id each for the interim and
    the final, so a file the vendor re-issues replaces the one before it
    rather than adding a duplicate, and a cycle that arrives late still lands
    in its own place. Nothing here looks forward: a step records only what
    that delivery said. The summary names the sites the step kept, which is
    what the index needs to carry the same rows the ledger does."""
    key = STORM_KEY.format(name=name, year=year)
    raw = store.get(key)
    doc = json.loads(raw) if raw else {"schema": SCHEMA, "name": name, "year": year, "attribution": ATTRIBUTION,
                                       "thresholds": [], "steps": [], "sites": {}, "final": None}
    _prune_interim(step, set(doc.get("sites") or {}))
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
    apply_price_override(doc, price_override(name, year))
    store.put(key, json.dumps(doc, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
    log(kind="reask", step=step["id"], storm=name, sites=len(step.get("sites") or {}), steps=len(steps), bytes=len(json.dumps(doc)))
    return {"steps": len(steps), "sites": len(doc["sites"]), "latest": step["id"], "final": doc.get("final") is not None,
            "kept": sorted(step.get("sites") or {})}


def l_prices(store: Storage, storm: str, sids) -> dict:
    """{site: {threshold: cents}} — the exchange's Yes price for this storm's
    per-location gust contracts at this moment, recorded with the delivery it
    arrived alongside. Without it a reader scrubbing back through the deliveries
    would see an old ladder against today's price, which is not a comparison.

    A storm's contracts are found by the exchange's product name, which carries
    the storm's, as the pages find them: the symbol's two storm letters are not
    always the first two of its name (Nolo's went up as LNL), and matching on
    those recorded no price for Nolo at all. A book whose two sides both stand
    at the minimum is empty, and its fifty-cent midpoint is no price
    (WXM.realMid), so it is not recorded."""
    raw = store.get("snapshots/market/hurricane.json")
    if not raw:
        return {}
    try:
        doc = json.loads(raw)
    except ValueError:
        return {}
    name = str(storm or "").strip().lower()
    if not name:
        return {}
    out = {}
    for m in doc.get("markets") or []:
        sym = str(m.get("symbol") or "")
        if not re.fullmatch(r"L[A-Z]{4}", sym) or sym.startswith("LHL") or name not in str(m.get("name") or "").lower():
            continue
        site = sym[3:]
        if sids and site not in sids:
            continue
        row = {}
        for c in m.get("contracts") or []:
            try:
                t = int(float(c.get("strike")))
            except (TypeError, ValueError):
                continue
            bid, ask = c.get("bid"), c.get("ask")
            if bid is not None and ask is not None and bid <= 0.011 and ask >= 0.989:
                continue
            if c.get("mid") is not None:
                row[str(t)] = round(float(c["mid"]) * 100, 1)
        if row:
            out[site] = row
    return out


def newest_delivery(steps: list) -> Optional[dict]:
    """The delivery standing now, ordered as the page orders them: by arrival,
    with the cycles in cycle order among the places cycles hold, so a cycle filed
    late (a backfill) is not taken for the newest."""
    live = [s for s in steps or [] if s.get("kind") != "final"]
    if not live:
        return None
    out = [live[i] for i in sorted(range(len(live)), key=lambda i: (live[i].get("ts") or "~", i))]
    slots = [k for k, s in enumerate(out) if s.get("kind") == "livecyc"]
    for k, s in zip(slots, sorted((out[k] for k in slots), key=lambda s: s.get("id") or "")):
        out[k] = s
    return out[-1]


def mark_end_prices(store: Storage, name: str, year: int, now: dt.datetime, log: Callable) -> bool:
    """Keep the exchange's prices on the newest delivery as they stand now, each pass.

    The reading taken as a file lands comes before the market maker has moved to
    it, and the reading taken as the next file lands can come after the market
    maker has already moved to that one: Isaias's 06Z cycle of 9 October was
    priced nine minutes after the vendor published it, before this lane filed it.
    The last reading a delivery gets while it is the newest is the market's answer
    to it and to nothing later, so the page pairs each file with that
    (owner, 2026-10-09). Only the newest delivery is touched; once the next one is
    filed, the earlier one keeps the reading it had. A storm under the owner's
    ruling carries the ruling's figures, and a settled storm has no market to read."""
    if price_override(name, year):
        return False
    key = STORM_KEY.format(name=name, year=year)
    raw = store.get(key)
    if not raw:
        return False
    doc = json.loads(raw)
    if doc.get("final"):
        return False
    st = newest_delivery(doc.get("steps") or [])
    if not st:
        return False
    prices = l_prices(store, name, set(doc.get("sites") or {}))
    if not prices or prices == st.get("pricesEnd"):
        return False
    st["pricesEnd"], st["pricesEndTs"] = prices, _iso(now)
    store.put(key, json.dumps(doc, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
    log(kind="reask", storm=name, step=st.get("id"), pricesEnd=len(prices))
    return True


def _step(lad: dict, kind: str, sid: str, at, ts: str, prices: Optional[dict] = None) -> dict:
    """One delivery as the ledger stores it: the probability ladder per site, as
    percentages in the file's own threshold order, and the exchange's prices
    recorded with it. Deliberately nothing else. The advisory's sustained wind
    is a one-minute mean and these contracts settle on a peak three-second
    gust; carrying the two together invites a comparison between different
    measurements, so the ledger does not. No figure of the site's own rides
    on a step: the pool's calculation is the desk's, reaching the exchange
    through the market maker, and the page shows the exchange's price."""
    sites = {k: v.get("p") or [] for k, v in (lad.get("sites") or {}).items()}
    meta = {k: {"name": v.get("name"), "lat": v.get("lat"), "lon": v.get("lon")} for k, v in (lad.get("sites") or {}).items()}
    if kind == "interim":
        # whether the interim's domain reached the location, which is what
        # separates a zero it published from a location it never looked at
        for k, v in (lad.get("sites") or {}).items():
            meta[k]["covered"] = bool(v.get("covered"))
    return {"id": sid, "kind": kind, "at": at, "ts": ts, "prices": prices or {},
            "sites": sites, "siteMeta": meta, "thresholds": lad.get("thresholds")}


def _stamp(s: str) -> str:
    """'2026-09-01T06:00:00Z' -> '2026090106' for archive names."""
    digits = re.sub(r"\D", "", s or "")
    return digits[:10] if len(digits) >= 10 else digits


def slim_entry(entry: dict) -> dict:
    """A settled storm's index entry with its files' site tables left out: the
    stamps that say what was fetched and when stay, with a count of the sites
    each file carried, so the lane does not fetch a file again and the page can
    say how many locations it covered."""
    out = dict(entry)
    for kind in ("livecyc", "interim", "final"):
        f = out.get(kind)
        if isinstance(f, dict) and ("sites" in f or "rows" in f):
            out[kind] = {k: v for k, v in f.items() if k not in ("sites", "rows")}
            out[kind]["n"] = len(f.get("sites") or {})
    return out


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
    fetched = 0
    # storms this pass did not bring up to date with the vendor's listing: their
    # newest delivery may not be the vendor's newest, so its end price is not read
    behind = set()

    def current(listing: dict) -> list:
        return [s for s in (listing or {}).get("storms") or [] if str(s.get("storm_year")) == str(year)]

    try:
        live = json.loads(fetch("/livecyc/tcwind/list").decode())
    except Exception as e:  # noqa: BLE001
        errors.append(f"livecyc list: {type(e).__name__}: {e}")
        live = None

    # ---- one storm across a rename: the owner's rulings (absorbed_storms) and
    # every depression NHC has since named, its ledger folded into the named
    # storm's before anything is filed, so the series reads from its first file
    absorbed = absorbed_storms()
    seen = {s.get("name"): (s.get("livecyc") or {}).get("forecastTime") for s in prev.get("storms") or []}
    for s in current(live) if live else []:
        fcs = s.get("forecasts") or []
        if s.get("storm_name") and fcs:
            seen[s["storm_name"]] = max(f.get("forecast_datetime") or "" for f in fcs)
    auto = renamed_storms(prev, {k: v for k, v in seen.items() if k}, named_by_number(store, year), year, now)
    renamed = {**auto, **absorbed}
    for old_key, new_key in sorted(renamed.items()):
        old_name, new_name = old_key.rsplit("_", 1)[0], new_key.rsplit("_", 1)[0]
        if store.get(STORM_KEY.format(name=old_name, year=year)):
            try:
                absorb_ledger(store, old_name, year, new_name, log)
            except Exception as e:  # noqa: BLE001
                errors.append(f"fold {old_name} into {new_name}: {type(e).__name__}: {e}")
    storms = {}
    for s in prev.get("storms") or []:
        k = s["name"] + "_" + str(s["year"])
        if k not in renamed:
            storms[k] = s
    for s in prev.get("storms") or []:
        k = s["name"] + "_" + str(s["year"])
        if k in renamed:
            _carry_entry(storms, s, renamed[k], renamed[k].rsplit("_", 1)[0])

    def shown_as(name: str):
        """The index key and display name a vendor storm is filed under."""
        key = renamed.get(f"{name}_{year}", f"{name}_{year}")
        return key, key.rsplit("_", 1)[0]

    # ---- LiveCyc: every listed cycle the storm's ledger lacks, oldest first.
    # The vendor can publish two cycles at once: Isaias's first files were the
    # 00Z and 06Z cycles of 7 October, landing together, and a lane that took
    # only the newest left the 00Z out of the series. So each pass files every
    # cycle of the storm's newest MAX_STEPS that is not yet in its ledger (no
    # more than MAX_NEW_CYCLES of them, so a storm first seen late is caught up
    # over a few passes), and fetches the newest again when the vendor reissues
    # it. A cycle in the ledger under the depression's number is fetched again
    # under the storm's name, whose copy stands.
    for s in current(live) if live else []:
        name, fcs = s.get("storm_name"), s.get("forecasts") or []
        if not name or not fcs:
            continue
        ordered = sorted(fcs, key=lambda f: f.get("forecast_datetime") or "")[-MAX_STEPS:]
        latest = ordered[-1]
        key, shown = shown_as(name)
        under_number = name.lower() != shown.lower()
        entry = storms.setdefault(key, {"name": shown, "year": year, "livecyc": None, "interim": None, "final": None})
        raw_led = store.get(STORM_KEY.format(name=shown, year=year))
        held = {}
        for st in (json.loads(raw_led).get("steps") or []) if raw_led else []:
            held[st.get("id")] = str(st.get("as") or shown)
        cid = lambda f: _stamp(f.get("forecast_datetime")) or f.get("forecast_datetime")
        todo = [f for f in ordered
                if cid(f) not in held or (not under_number and held[cid(f)].lower() != shown.lower())]
        todo = todo[-MAX_NEW_CYCLES:]
        cur = entry.get("livecyc") or {}
        if (latest not in todo and cur.get("forecastTime") == latest.get("forecast_datetime")
                and str(cur.get("vendorName") or shown).lower() == name.lower()
                and cur.get("lastModified") != latest.get("last_modified")):
            todo.append(latest)                         # the vendor reissued its newest cycle
        got = set(held)
        for f in todo:
            ft = f.get("forecast_datetime")
            akey = f"archive/reask/{name}_{year}/livecyc_{_stamp(ft)}.csv.gz"   # filed under the name it came with
            try:
                body = fetch("/livecyc/tcwind/probabilities", {"storm_name": name, "storm_year": year, "forecast_start_time": ft, "format": "csv"})
                store.put(akey, gzip.compress(body), "application/gzip")        # a re-delivered cycle replaces its file
                lad = parse_ladder_csv(body.decode("utf-8", "replace"))
                step = _step(lad, "livecyc", cid(f), ft, _iso(now),
                             l_prices(store, shown, set((lad.get("sites") or {}).keys())))
                if under_number:
                    step["as"] = name
                entry["ledger"] = append_step(store, shown, year, step, log)
                cur = entry.get("livecyc") or {}
                if (ft or "") >= (cur.get("forecastTime") or ""):
                    # the index shows the newest cycle the storm has, whichever name it came under
                    entry["livecyc"] = {"forecastTime": ft, "lastModified": f.get("last_modified"), "fetched": _iso(now),
                                        "cycles": len(fcs), **lad}
                    if under_number:
                        entry["livecyc"]["vendorName"] = name
                fetched += 1
                got.add(cid(f))
            except Exception as e:  # noqa: BLE001
                errors.append(f"livecyc {name} {ft}: {type(e).__name__}: {e}")
                behind.add(shown)
        if cid(latest) not in got:
            behind.add(shown)

    # ---- Metryc interim and final, refetched when the listing says they changed
    for kind, lpath, ppath in (("interim", "/metryc/interim/tcwind/list", "/metryc/interim/tcwind/probabilities"),
                               ("final", "/metryc/historical/tcwind/list", "/metryc/historical/tcwind/peak_gust")):
        try:
            listing = json.loads(fetch(lpath).decode())
        except Exception as e:  # noqa: BLE001
            errors.append(f"{kind} list: {type(e).__name__}: {e}")
            continue
        for s in current(listing):
            name = s.get("storm_name")
            if not name:
                continue
            key, shown = shown_as(name)
            under_number = name.lower() != shown.lower()
            entry = storms.setdefault(key, {"name": shown, "year": year, "livecyc": None, "interim": None, "final": None})
            lm = s.get("last_modified")
            cur = entry.get(kind) or {}
            cur_from = str(cur.get("vendorName") or shown)
            if cur and under_number and cur_from.lower() != name.lower():
                continue        # the file under the storm's name stands over one under its number
            if cur and cur.get("lastModified") == lm and cur_from.lower() == name.lower():
                continue
            akey = f"archive/reask/{name}_{year}/{kind}_{_stamp(lm) or 'latest'}.csv.gz"
            try:
                body = fetch(ppath, {"storm_name": name, "storm_year": year, "format": "csv"})
                store.put(akey, gzip.compress(body), "application/gzip")
                text = body.decode("utf-8", "replace")
                came = {"vendorName": name} if under_number else {}
                if kind == "interim":
                    # the covered zeros are figures here; the ledger keeps the
                    # ones for its own sites and the index carries those same rows
                    parsed = parse_ladder_csv(text, keep_zero=True)
                    st = _step(parsed, "interim", "INT", lm, _iso(now),
                               l_prices(store, shown, set((parsed.get("sites") or {}).keys())))
                    if under_number:
                        st["as"] = name
                    entry["ledger"] = append_step(store, shown, year, st, log)
                    kept = set(entry["ledger"].get("kept") or [])
                    entry[kind] = {"lastModified": lm, "fetched": _iso(now), **parsed, **came,
                                   "sites": {k: v for k, v in (parsed.get("sites") or {}).items() if k in kept}}
                else:
                    parsed = parse_final_csv(text)
                    entry[kind] = {"lastModified": lm, "fetched": _iso(now), "sites": parsed, **came}
                    fin = {"id": "FINAL", "kind": "final", "at": lm, "ts": _iso(now), "sites": {}, "siteMeta": {},
                           "thresholds": None, "final": {k: v.get("peakGustMph") for k, v in parsed.items()}}
                    if under_number:
                        fin["as"] = name
                    entry["ledger"] = append_step(store, shown, year, fin, log)
                fetched += 1
            except Exception as e:  # noqa: BLE001
                errors.append(f"{kind} {name}: {type(e).__name__}: {e}")
                behind.add(shown)

    # Every storm stays: a settled storm can be stepped through on the hurricane
    # page for good (owner, 2026-10-20), where it used to leave the snapshot two
    # weeks after its final. From then on its entry keeps only its stamps; the page
    # draws a settled storm from its ledger, which holds every file's figures, so
    # the files' site tables here would only add about twenty kilobytes a storm.
    keep = []
    for s in storms.values():
        f = s.get("final") or {}
        if f.get("fetched"):
            try:
                age = (now - dt.datetime.fromisoformat(f["fetched"].replace("Z", "+00:00"))).days
            except ValueError:
                age = 0
            if age > SLIM_DAYS:
                s = slim_entry(s)
        keep.append(s)
    # the pool's figure is not the site's to compute: it is the desk's, and it
    # reaches the exchange through the market maker. A storm the owner has
    # ruled on carries the ruling's figure; a storm the desk is pricing carries
    # the desk's, which the page shows only while no pool is listed; every
    # other storm carries none
    dcfg = cfg.get("desk") or {}
    try:
        desk = desk_pool_figures(fetch_desk_lhl((dcfg.get("lhl_url") or "").strip(),
                                                (dcfg.get("api_key") or "").strip()))
    except Exception as e:  # noqa: BLE001 - the desk's endpoint is not this pass's business
        errors.append(f"desk lhl: {type(e).__name__}: {e}")
        desk = {}
    for s2 in keep:
        lc = s2.get("livecyc")
        if not lc:
            continue
        ruled_pw = latest_override_pwin(price_override(s2.get("name"), s2.get("year")))
        d = desk.get((str(s2.get("name") or "").lower(), s2.get("year")))
        if ruled_pw is not None:
            # the owner's ruling outranks the feed: it is the figure he has said
            # stands for that storm, and the feed is whatever the desk holds now
            lc["pwin"] = ruled_pw
            lc["pwinMethod"] = "override"
            lc.pop("pwinMeta", None)
        elif d:
            lc["pwin"] = d["pwin"]
            lc["pwinMethod"] = "desk"
            lc["pwinMeta"] = {k: d[k] for k in ("cycle", "method", "note", "asof") if d.get(k)}
        else:
            lc.pop("pwin", None)
            lc.pop("pwinMethod", None)
            lc.pop("pwinMeta", None)
    # the market's standing answer to each storm's newest delivery, read every pass
    # while the vendor's listing shows nothing newer than what is filed
    if live is not None:
        for s2 in keep:
            if s2.get("name") in behind or not s2.get("livecyc"):
                continue
            try:
                mark_end_prices(store, s2.get("name"), s2.get("year"), now, log)
            except Exception as e:  # noqa: BLE001 - a missed reading is not a failed pass
                errors.append(f"end prices {s2.get('name')}: {type(e).__name__}: {e}")
    asof = max([(s.get("livecyc") or {}).get("forecastTime") or "" for s in keep] + [prev.get("asof") or ""]) or None
    ok = live is not None
    snap = {"schema": SCHEMA, "enabled": True, "attribution": ATTRIBUTION, "asof": asof, "written": _iso(now),
            "polled": _iso(now) if ok else prev.get("polled"), "year": year, "storms": keep, "fetched": fetched, "errors": errors,
            "ledgerKey": STORM_KEY, "renamed": auto}
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
