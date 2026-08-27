"""The forecaster's own reasoning, per station.

Every National Weather Service office publishes an Area Forecast Discussion: a
few hundred words, written by the forecaster on shift, saying what the models
are doing, where they disagree, and what the office decided. It is the one place
the reasoning behind a forecast is written down in plain language, and on a page
that shows a market disagreeing with a forecast it is the obvious next question.

Offices, not stations. A discussion covers a forecast area, so the thirty-odd US
stations on this site share far fewer offices, and each one is fetched once. The
station-to-office mapping is already resolved and cached by the archive job, so
this lane reads that rather than asking api.weather.gov again.

The text is the office's, published by a US government agency and in the public
domain. It is carried whole, never summarised or edited, and every page that
shows it names the office, the issuance time, and links to the office's own page
so a reader can check it against the source.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from typing import Callable, Dict, Optional

from . import archive as arch
from . import gov_weather as gw
from .storage import Storage

SCHEMA = 1
KEY = "snapshots/discussion/{wfo}.json"
INDEX_KEY = "snapshots/discussion/index.json"
# offices reissue a discussion a few times a day and amend in between, so this is
# read often enough to catch an amendment without asking on every pass
CACHE = "public, max-age=900, stale-while-revalidate=7200, stale-if-error=86400"
# the office's own product page, which is where a reader should end up
OFFICE_URL = ("https://forecast.weather.gov/product.php?site={wfo}&issuedby={wfo}"
              "&product=AFD&format=CI&version=1&glossary=0")


def split_product(text: str) -> dict:
    """Office, issuance line, and the discussion itself.

    A product arrives with its teleprinter routing on the front — a sequence
    number, a WMO header, the product identifier — which is addressing, not
    forecasting, and is not shown on the office's own page either. It is dropped
    from the body and the whole text is still carried beside it, so nothing about
    the discussion is edited: what is removed is the envelope.

    The office's long name and the issuance line are the two lines after the
    product title, and they are lifted out so a page can say whose reasoning this
    is and when it was written without the reader parsing it.
    """
    lines = (text or "").replace("\r", "").split("\n")
    i = 0
    while i < len(lines) and "Area Forecast Discussion" not in lines[i]:
        i += 1
        if i > 12:                       # not the shape expected; carry it whole
            return {"office": None, "issued": None, "body": (text or "").strip()}
    office = issued = None
    for j in range(i + 1, min(i + 5, len(lines))):
        ln = lines[j].strip()
        if not ln:
            continue
        if office is None and ln.lower().startswith("national weather service"):
            office = ln[len("national weather service"):].strip() or None
        elif issued is None and any(ch.isdigit() for ch in ln):
            issued = ln
    return {"office": office, "issued": issued, "body": "\n".join(lines[i:]).strip()}


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def discussion_pass(cfg: dict, store: Storage, fetch: Optional[Callable] = None) -> int:
    t0 = time.time()
    now = dt.datetime.now(dt.timezone.utc)
    gw.set_user_agent(cfg.get("user_agent", ""))
    deadline = arch.Deadline(arch.remaining_budget(cfg))

    raw = store.get(arch.GRIDS_KEY)
    grids = json.loads(raw) if raw else {}
    # station -> office, from the mapping the archive already resolved
    by_station = {sid: (g or {}).get("wfo") for sid, g in grids.items()
                  if isinstance(g, dict) and g.get("wfo")}
    offices = sorted({w for w in by_station.values() if w})
    if not offices:
        print(json.dumps({"kind": "discussion", "written": 0,
                          "reason": "no station-to-office mapping yet; the archive job resolves it"}))
        return 0

    written, errors = 0, []
    for wfo in offices:
        if deadline.over(20):
            errors.append("deadline")
            break
        try:
            text = (fetch or gw.fetch_forecast_discussion)({"office_url": "https://api.weather.gov/offices/" + wfo})
        except Exception as e:  # noqa: BLE001
            errors.append(f"{wfo}: {type(e).__name__}")
            continue
        text = (text or "").strip()
        if not text:
            errors.append(f"{wfo}: empty")
            continue
        prev_raw = store.get(KEY.format(wfo=wfo))
        prev = json.loads(prev_raw) if prev_raw else None
        # an unchanged discussion keeps its first-seen time, so the page can say
        # how old the forecaster's reasoning is rather than how recently it was read
        seen = (prev or {}).get("seen") if prev and prev.get("text") == text else _iso(now)
        parts = split_product(text)
        doc = {"schema": SCHEMA, "wfo": wfo, "asof": _iso(now), "seen": seen or _iso(now),
               "url": OFFICE_URL.format(wfo=wfo), "text": text,
               "body": parts["body"], "office": parts["office"], "issued": parts["issued"],
               "source": "National Weather Service"
                         + (", " + parts["office"] if parts["office"] else ", " + wfo + " forecast office"),
               "note": "Written by the forecaster on shift. Carried whole and unedited; "
                       "a US government work, in the public domain."}
        store.put(KEY.format(wfo=wfo), json.dumps(doc, separators=(",", ":")).encode(),
                  "application/json", CACHE)
        written += 1

    idx = {"schema": SCHEMA, "asof": _iso(now), "stations": by_station,
           "offices": offices, "written": written}
    store.put(INDEX_KEY, json.dumps(idx, separators=(",", ":")).encode(), "application/json", CACHE)
    arch.LAST_STATUS = {"job": "discussion", "errors": len(errors),
                        "alarms": ["discussion: no office answered"] if (offices and not written) else []}
    print(json.dumps({"kind": "discussion", "offices": len(offices), "written": written,
                      "errors": errors[:5], "seconds": round(time.time() - t0, 1)}))
    return 1 if (offices and not written) else 0
