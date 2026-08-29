"""
gov_weather.py — the US government weather feeds behind the site, in one file.

Single file on purpose. Read it top to bottom and you have seen every
government call the site makes. Standard library only.

Sources, all US government, all public domain, no key and no subscription.

  aviationweather.gov   METAR observations          -> the observed line
  api.weather.gov       gridpoint hourly forecast   -> the NWS forecast line
  api.weather.gov       day/night forecast          -> the NWS official high and low
  NOAA Open Data (S3)   NBM text guidance (NBH/NBS) -> second forecast, hourly and daily
  NOMADS                LAMP text guidance          -> same-day hourly trace, 25 h
  NOMADS                GFS MOS text (MAV)          -> independent daily max/min
  api.weather.gov       CLI text product            -> official daily climate report
  NHC / NOAA GIS        storms, cones, tracks       -> the hurricane page
  NHC HURDAT2           Atlantic best-track archive -> the season pace figure

Run it standalone for a quick look at three stations:
    python3 -m pipeline.gov_weather KLAX KPHX
"""

from __future__ import annotations
import json, re, sys, time, urllib.request, urllib.parse, urllib.error, datetime as dt
from typing import Any, Optional

# ---------------------------------------------------------------------------
# 1. REQUIRED HEADER
#
# api.weather.gov returns 403 without a User-Agent that names the application
# and carries a contact address (reproduced 2026-08-21). There is no API key
# today; NWS says a key will replace this. aviationweather.gov asks for a
# custom User-Agent too, "to prevent automated filtering inadvertently blocking
# valid traffic". The string comes from config/site.json; the pipeline calls
# set_user_agent() once at start. The placeholder below is never sent in
# production.
# ---------------------------------------------------------------------------
USER_AGENT = "weather-tools-site/0.1 (set user_agent in config/site.json)"


def set_user_agent(ua: str) -> None:
    global USER_AGENT
    if ua:
        USER_AGENT = ua


# ---------------------------------------------------------------------------
# 2. TWO DECODE CONVENTIONS THAT ARE NOT SETTLED YET
#
# A METAR carries the temperature twice.
#
#   body      "34/18"        whole degrees Celsius
#   remarks   "T03440178"    tenths, so 34.4C and 17.8C
#
# 34.0C -> 93F.  34.4C -> 94F.  Across the range of values the two paths give a
# different Fahrenheit integer a little under half the time, so this is routine
# rather than an edge case. Which one is correct depends on what the ForecastEx
# settlement feed uses, and that is an open question. Same for whether special
# observations (SPECI) count toward a daily maximum.
#
# Both are constants rather than buried logic so flipping either is one line.
#
# What the feed itself hands back: aviationweather.gov's decoded `temp` field
# is the tenths value whenever the observation carries a T-group in its
# remarks, and the whole-degree body value otherwise. US ASOS stations carry
# the T-group on essentially every routine report, so TEMP_SOURCE="remarks"
# means "tenths when available", not "tenths always" -- a station or SPECI
# without the T-group quietly falls back to whole degrees. "body" parses the
# body group out of the raw METAR text and is whole degrees always.
# ---------------------------------------------------------------------------
TEMP_SOURCE = "remarks"   # "remarks" (tenths) or "body" (whole degrees)
INCLUDE_SPECI = True      # count SPECI reports toward the daily maximum


def _fetch(url: str, tries: int = 3, timeout: int = 30, accept: str = "*/*") -> bytes:
    """GET with the required header and a polite back-off. NWS says a
    rate-limit error clears within about five seconds, so the waits grow."""
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            # A client error other than 429 will not change on retry.
            if 400 <= e.code < 500 and e.code != 429:
                raise
            if attempt == tries - 1:
                raise
            time.sleep(2.5 * (attempt + 1))
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(2.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def _get(url: str, tries: int = 3) -> Any:
    """JSON GET. An empty body (aviationweather.gov answers 204 with no body
    for a valid request that matches nothing) returns None, not an error."""
    body = _fetch(url, tries, accept="application/geo+json,application/json")
    if not body.strip():
        return None
    return json.loads(body)


def _get_text(url: str, tries: int = 3, timeout: int = 120) -> str:
    return _fetch(url, tries, timeout=timeout).decode("ascii", "replace")


def _head(url: str) -> bool:
    """True if the object exists. S3 answers 403 for a missing key on a
    public bucket, so 403 and 404 both mean 'not there yet'."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        urllib.request.urlopen(req, timeout=30)
        return True
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return False
        raise


def c_to_f(c: float) -> float:
    return c * 9 / 5 + 32


# ===========================================================================
# OBSERVATIONS — aviationweather.gov
# ===========================================================================
def fetch_subhourly(station: str, hours: int = 30) -> list[dict]:
    """
    The five minute ASOS stream for one station, from api.weather.gov.

    THIS IS NOT THE SETTLEMENT RECORD and must never be used as one. The
    contracts resolve on hourly METARs and this site reads those from
    aviationweather.gov; see fetch_observations above for why. A daily maximum
    taken over five minute data lands at or above one taken over the hourly
    reports and effectively never below, so swapping the two would move the
    number a contract settles on.

    What it is good for is showing what happened BETWEEN the hourly reports. An
    hourly record is twenty-four readings of a day that had nearly three hundred,
    and a brief afternoon peak that fell between two reports is invisible in it
    while being exactly the kind of thing a reader wonders about. Carried
    separately, drawn separately, and labelled as not the settlement record.

    Rows are {t, tempF, qc}: the observation time, the temperature in Fahrenheit
    from the feed's Celsius, and the feed's own quality flag, which is passed
    through rather than interpreted here.
    """
    start = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    d = _get(f"https://api.weather.gov/stations/{station}/observations"
             f"?start={urllib.parse.quote(start)}&limit=500")
    out = []
    for f in (d.get("features") or []):
        p = f.get("properties") or {}
        t = p.get("timestamp")
        v = ((p.get("temperature") or {}).get("value"))
        if not t or v is None:
            continue
        out.append({"t": str(t).replace("+00:00", "Z"),
                    "tempF": round(c_to_f(float(v)), 1),
                    "qc": (p.get("temperature") or {}).get("qualityControl")})
    out.sort(key=lambda r: r["t"])
    return out


def fetch_observations(stations: list[str], hours: int = 36) -> dict[str, list[dict]]:
    """
    METAR observations for one or more ICAO stations.

    Why this and not api.weather.gov/stations/{id}/observations. The two feeds
    do not disagree about any observation they both carry. api.weather.gov
    additionally serves the five minute ASOS stream, and a daily maximum taken
    over five minute data lands at or above one taken over hourly METARs and
    effectively never below. Our contracts settle on METAR observations, so the
    hourly feed is the matching one. It takes many stations per request and
    holds about two weeks of history against roughly four days.

    One measured limit: the endpoint caps a response at 400 observations
    TOTAL, silently, dropping the OLDEST rows (documented: "Most endpoints
    return a maximum of 400 entries"; reproduced 2026-08-21 with five stations
    at 72 hours, which came back as exactly 400 rows spanning 70.6 hours). So
    requests are batched by station, sized for two reports an hour, and a
    batch that comes back with 400 rows is reported as truncated.

    Three more facts about this feed that shape the code (all checked live):
    - `obsTime` is the observation's epoch time. `reportTime` on a routine
      METAR is the NEXT nominal top of the hour, so the 23:53Z report carries
      a reportTime of 00:00Z the following day. Key everything on obsTime.
    - A corrected report (COR) replaces the original in place under the same
      obsTime with a new receiptTime. Readers must upsert, never append.
    - `temp` is the remarks T-group in tenths when the report carries one and
      the whole-degree body value when it does not. Reports without a T-group
      do occur at contract stations (KCLT, KMCO on 2026-08-21), so every row
      is stamped with which it was: temp_source = "tgroup" | "body".
    - History is available for 30 days; older dates are refused (HTTP 400).
    """
    out: dict[str, list[dict]] = {}
    for ob in fetch_observations_raw(stations, hours):
        if ob.get("temp") is None:
            continue
        if not INCLUDE_SPECI and ob.get("metarType") == "SPECI":
            continue
        temp_c = ob["temp"] if TEMP_SOURCE == "remarks" else _body_temp_c(ob.get("rawOb", ""))
        if temp_c is None:
            continue
        out.setdefault(ob["icaoId"], []).append({
            "time": dt.datetime.fromtimestamp(ob["obsTime"], dt.timezone.utc),
            "temp_c": temp_c,
            "temp_f": round(c_to_f(temp_c), 1),
            "type": ob.get("metarType"),
            "temp_source": ob.get("temp_source"),
            "raw": ob.get("rawOb", ""),
        })
    for rows in out.values():
        rows.sort(key=lambda r: r["time"])
    return out


def fetch_latest_metars(stations: list[str]) -> dict[str, dict]:
    """The newest observation per station, decoded for display.

    The neighbour overlay on the city maps wants one current reading per
    nearby field, not a history. The endpoint has no latest-only switch (it
    rejects unknown parameters), so this asks for a ninety-minute window,
    which always holds the last routine METAR, batched under the 400-row cap,
    and keeps the newest row per station. Temperature follows the same decode
    the site settles with: the remarks tenths where the report carries them.
    """
    out: dict[str, dict] = {}
    for ob in fetch_observations_raw(stations, hours=2):
        sid = ob.get("icaoId")
        if not sid or ob.get("temp") is None:
            continue
        cur = out.get(sid)
        if cur and cur["obsTime"] >= ob["obsTime"]:
            continue
        temp_c = ob["temp"] if TEMP_SOURCE == "remarks" else _body_temp_c(ob.get("rawOb", ""))
        if temp_c is None:
            continue
        out[sid] = {
            "obsTime": ob["obsTime"],
            "tempF": round(c_to_f(temp_c), 1),
            "dewF": round(c_to_f(ob["dewp"]), 1) if ob.get("dewp") is not None else None,
            "wdir": ob.get("wdir"), "wspd": ob.get("wspd"), "wgst": ob.get("wgst"),
            "type": ob.get("metarType"), "src": ob.get("temp_source"),
        }
    return out


AWC_ROW_CAP = 400
_T_GROUP = re.compile(r"(?:^|\s)T[01]\d{3}[01]\d{3}(?:\s|$)")


def fetch_observations_raw(stations: list[str], hours: int = 36,
                           on_truncated=None, end: Optional[dt.datetime] = None) -> list[dict]:
    """
    The raw decoded rows from aviationweather.gov, every field as served, plus
    one stamp per row: temp_source = "tgroup" if the remarks carry a T-group
    (so `temp` is tenths) else "body" (so `temp` is whole degrees C). The
    archive stores these rows verbatim, because the archive cannot be
    regenerated and the fields we use may change.

    Batches are sized so stations x hours x 2 reports/hour stays under the
    400-row cap. `on_truncated(batch_ids)` is called for any batch that comes
    back with exactly 400 rows.
    """
    # three reports an hour of headroom (hourly METAR plus SPECI bursts), and
    # strictly under the cap: at 6 h that is 8 stations, at 24 h five, at 72 h one
    per_batch = max(1, min(8, (AWC_ROW_CAP - 1) // max(1, hours * 3)))
    rows: list = []
    for i in range(0, len(stations), per_batch):
        ids = stations[i:i + per_batch]
        params = {"ids": ",".join(ids), "format": "json", "hours": hours}
        if end is not None:
            # `date` is the END of the window and `hours` counts back from it;
            # anything older than 30 days is refused with HTTP 400.
            params["date"] = end.astimezone(dt.timezone.utc).strftime("%Y%m%d_%H%M")
        url = "https://aviationweather.gov/api/data/metar?" + urllib.parse.urlencode(params)
        batch = _get(url) or []
        if len(batch) >= AWC_ROW_CAP and on_truncated:
            on_truncated(ids)
        rows.extend(batch)
    for ob in rows:
        ob["temp_source"] = "tgroup" if _T_GROUP.search(ob.get("rawOb", "") or "") else "body"
    return rows


# Anchored to the whole token. The temperature group is the only slash token
# shaped M?dd/M?dd -- visibility fractions ("1/2SM", "M1/4SM") share the slash
# but not the shape, and an unanchored match read them as temperatures on
# every low-visibility observation, which is exactly the weather where a
# contested settlement is most likely.
_TEMP_GROUP = re.compile(r"^(M?\d{2})/(M?\d{2})?$")


def _body_temp_c(raw: str) -> float | None:
    """Whole-degree temperature from the METAR body group, e.g. '34/18', 'M02/M05'."""
    for tok in raw.split():
        m = _TEMP_GROUP.match(tok)
        if m:
            lhs = m.group(1)
            v = int(lhs.lstrip("M"))
            return -v if lhs.startswith("M") else v
    return None


# ===========================================================================
# FORECAST — api.weather.gov
# ===========================================================================
def resolve_grid(lat: float, lon: float) -> dict:
    """
    Map a coordinate to its NWS forecast grid. Two requests are required by
    design, and the mapping almost never changes, so cache this per city.
    The docs do ask that it be re-resolved periodically, because the grid and
    even the office for a coordinate can change; the archive job does so
    daily. Coordinates beyond four decimals are answered with a 301 to the
    rounded point, so round first and never pay the redirect.
    """
    p = _get(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}")["properties"]
    return {
        "wfo": p["gridId"], "x": p["gridX"], "y": p["gridY"],
        "timezone": p["timeZone"],
        "hourly_url": p["forecastHourly"],
        "daily_url": p["forecast"],          # the day/night product: official high and low
        "grid_url": p["forecastGridData"],
        "office_url": p["forecastOffice"],   # for the Area Forecast Discussion
    }


def fetch_daily_forecast(grid: dict) -> list[dict]:
    """
    The day/night forecast carries the OFFICIAL NWS high and low. The hourly
    product starts at the current hour, so late in the day its remaining-hours
    maximum understates the official high; these periods do not. A daytime
    period's temperature is the high for its local day; a night period's is
    the low for the local day the period ENDS on (a calendar-day low usually
    happens in the morning, inside the period that began the prior evening).
    """
    periods = _get(grid["daily_url"])["properties"]["periods"]
    rows = []
    for p in periods:
        f = period_temp_f(p)
        if f is None:
            continue
        rows.append({
            "start": dt.datetime.fromisoformat(p["startTime"]),
            "end": dt.datetime.fromisoformat(p["endTime"]),
            "is_day": bool(p.get("isDaytime")),
            "temp_f": f,
        })
    return rows


def period_temp_f(p: dict) -> Optional[float]:
    """
    A forecast period's temperature in Fahrenheit, or None when the period
    carries null (api.weather.gov does serve "temperature": null for some
    periods; four archived day/night files from 2026-08-20 have one). Two
    shapes: today a scalar with a sibling temperatureUnit; under the
    forecast_temperature_qv feature flag, which NWS says will become the
    default, an object {"unitCode": "wmoUnit:degC", "value": 25} with NO
    temperatureUnit key, and the value is Celsius even when units=us.
    """
    t = p.get("temperature")
    if t is None:
        return None
    if isinstance(t, dict):
        v = t.get("value")
        if v is None:
            return None
        code = str(t.get("unitCode", "")).lower()
        if code.endswith("degc"):
            return round(c_to_f(float(v)), 1)
        if code.endswith("degf"):
            return round(float(v), 1)
        raise ValueError(f"unknown temperature unitCode {t.get('unitCode')!r}")
    unit = p.get("temperatureUnit")
    if unit == "F":
        return round(float(t), 1)
    if unit == "C":
        return round(c_to_f(float(t)), 1)
    raise ValueError(f"period has no temperatureUnit: {p!r}"[:200])


def fetch_hourly_forecast(grid: dict) -> list[dict]:
    """
    NWS hourly forecast, which is the official National Weather Service forecast
    and is NDFD derived.

    IMPORTANT. This returns the forecast as it stands right now, running forward
    from the current hour. It does not return what the forecast said earlier
    today. The city chart compares observed against forecast across the elapsed
    part of the day, which needs the forecast as issued before the day began, so
    that history has to come from an archive you keep. See archive_forecast below.
    Start writing that archive before building the display, because the data
    cannot be reconstructed after the fact.
    """
    periods = _get(grid["hourly_url"])["properties"]["periods"]
    rows = []
    for p in periods:
        # The response is served with s-maxage=3600 and its first period is
        # the hour current at generation time, so a copy fetched late in the
        # hour can start with a period that has already ended. Readers that
        # want "now" must drop periods whose endTime has passed rather than
        # trust period[0]. Both temperature shapes are handled by
        # period_temp_f; a null temperature skips the period.
        f = period_temp_f(p)
        if f is None:
            continue
        rows.append({"time": dt.datetime.fromisoformat(p["startTime"]), "temp_f": f})
    return rows


def fetch_forecast_discussion(grid: dict) -> str:
    """
    Area Forecast Discussion, the forecaster's plain-language reasoning. Useful
    for flagging the anomalous situations where models diverge most, which is
    where the market tends to disagree with the guidance.
    """
    office = grid["office_url"].rstrip("/").split("/")[-1]
    idx = _get(f"https://api.weather.gov/products/types/AFD/locations/{office}")
    items = idx.get("@graph") or []
    return _get(items[0]["@id"])["productText"] if items else ""


_TCD_ID = re.compile(r"\b([AECP][LP]\d{6})\b")


def fetch_storm_discussions(limit: int = 12) -> dict:
    """The forecaster's reasoning on each active cyclone, keyed by ATCF id.

    The Tropical Cyclone Discussion is the hurricane equivalent of the Area
    Forecast Discussion a city page carries: a few hundred words from the
    specialist on shift saying what the models are doing and what the centre
    decided. The product names the storm it belongs to on its own second line
    (AL042026), which is the id the storm snapshot already uses.

    Products are listed newest first, so the first one seen for a storm is its
    current discussion and later issuances for the same storm are skipped.
    """
    idx = _get("https://api.weather.gov/products/types/TCD")
    out = {}
    for item in (idx.get("@graph") or [])[:limit]:
        pid = item.get("id")
        if not pid:
            continue
        try:
            prod = _get("https://api.weather.gov/products/" + pid)
        except Exception:                       # noqa: BLE001 - one product must not sink the rest
            continue
        text = (prod.get("productText") or "").strip()
        m = _TCD_ID.search(text)
        if not m:
            continue
        sid = m.group(1).lower()
        if sid in out:
            continue                            # a newer issuance is already held
        out[sid] = {"id": pid, "storm": sid, "issued": prod.get("issuanceTime"),
                    "office": prod.get("issuingOffice"), "text": text}
    return out


_CLI_SUMMARY = re.compile(r"CLIMATE SUMMARY FOR (\w+ \d{1,2} \d{4})")


def fetch_cli_report(climate_id: str, for_date: Optional[dt.date] = None) -> Optional[dict]:
    """
    CLI, the official daily climate report: the climate program's record of the
    day's maximum and minimum. Useful as a public cross-check when a display
    number is questioned, but it is not the settlement source and its max can
    differ from a METAR-derived one (it reads the full ASOS record).

    Two facts about the product listing, checked 2026-08-21. The products are
    filed under the CLIMATE STATION id (LAX), not the forecast office (LOX):
    /locations/LOX answers 200 with an empty list. And a station gets several
    issuances per climate day (an evening preliminary, then one or two final
    ones after local midnight), so the right one is chosen by the date in the
    "CLIMATE SUMMARY FOR <date>" header, newest issuance winning, not by
    taking the first item.

    The text holds two MAXIMUM/MINIMUM pairs, the daily one under
    "TEMPERATURE (F)" and a month-to-date one further down, so the parse is
    anchored on the daily block.
    """
    idx = _get(f"https://api.weather.gov/products/types/CLI/locations/{climate_id}")
    for item in (idx.get("@graph") or [])[:12]:
        text = _get(item["@id"])["productText"]
        m = _CLI_SUMMARY.search(text)
        if not m:
            continue
        try:
            summary_date = dt.datetime.strptime(m.group(1), "%B %d %Y").date()
        except ValueError:
            continue
        if for_date and summary_date != for_date:
            continue
        block = text.split("TEMPERATURE (F)", 1)[-1]
        hi = re.search(r"^\s*MAXIMUM\s+(-?\d+|MM)", block, re.M)
        lo = re.search(r"^\s*MINIMUM\s+(-?\d+|MM)", block, re.M)
        val = lambda mm: None if (mm is None or mm.group(1) == "MM") else int(mm.group(1))
        return {"climate_id": climate_id, "issued": item.get("issuanceTime"),
                "summary_date": summary_date.isoformat(),
                "max_f": val(hi), "min_f": val(lo), "text": text}
    return None


# ===========================================================================
# MORE FORECAST SERIES — station text bulletins, no GRIB2 decoding
#
# Beyond the NWS gridpoint forecast, three US government station forecasts
# are published as fixed-width ASCII bulletins over HTTPS, keyed by ICAO id.
# All were checked live on 2026-08-21.
#
#   NBM  National Blend of Models, NOAA's calibrated multi-model blend. NBH is
#        hourly guidance out 25 hours; NBS is 3-hourly out 71 hours and carries
#        the TXN row, the 18-hour maximum and minimum, which is the blend's own
#        daily high and low. Hourly cycles, 28-30 MB per file, ~9,600 stations
#        (US plus the Canadian stations on our roster). Published on NOMADS and
#        mirrored byte-for-byte (MD5 checked) on the NOAA Open Data bucket,
#        which lags NOMADS by a few minutes but has no rate limit and supports
#        Range requests. NOMADS keeps NBM text for about two days, so a gap
#        longer than that cannot be backfilled. Forecasters start from NBM when
#        editing the grids that become the NWS forecast, so NBM and NWS are
#        related, not independent.
#
#   LAMP MDL's observation-updated hourly guidance, 25 hours out. The CGI
#        page and api.weather.gov's LAV type are dead ends (403 / empty), but
#        the bulletins themselves are on NOMADS: a file every 15 minutes, of
#        which the :30 run each hour (~4 MB) carries the temperature row; the
#        other three carry only ceiling and visibility. US stations only.
#
#   MAV  GFS MOS, a single-model statistical product with no forecaster
#        editing, so the cleanest independent comparison for the scorecard.
#        N/X row (daily max and min) and 3-hourly temperature to 60 h then
#        6-hourly to 72 h. Four cycles a day posting ~4h15m after cycle time,
#        ~3 MB, ten days retained on NOMADS.
#
# Two parsing rules matter for all of them. Values are fixed-width
# three-character columns: split on whitespace and a winter "-12" runs into
# its neighbour, so slice by position. And station ids are matched as the
# exact first token of the header line: the NBM bulletins also carry
# digit-bearing FAA ids (K00F, K00M ...) that a prefix match would confuse.
# ===========================================================================
NBM_TEXT_URL = ("https://noaa-nbm-grib2-pds.s3.amazonaws.com/"
                "blend.{date}/{hh}/text/blend_{kind}tx.t{hh}z")       # kind: nbh | nbs
LAMP_TEXT_URL = ("https://nomads.ncep.noaa.gov/pub/data/nccf/com/lmp/prod/"
                 "lmp.{date}/lmp.t{hh}30z.lavtxt.ascii")
MAV_TEXT_URL = ("https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs_mos/prod/"
                "gfs_mos.{date}/mdl_gfsmav.t{hh}z")

BULLETIN_MARKER = {"nbh": " NBH GUIDANCE", "nbs": " NBS GUIDANCE",
                   "lamp": " LAMP GUIDANCE", "mav": " MOS GUIDANCE"}


def bulletin_cycles(source: str, lookback_hours: int = 36, now: Optional[dt.datetime] = None) -> list:
    """
    Every cycle of one bulletin family that could exist in the lookback
    window, newest first, as (cycle datetime, url). Nothing is requested
    here; the archive job checks its own markers first and HEADs only the
    cycles it does not hold, so a scheduler gap is caught up from the bucket
    instead of silently skipped. NBM keeps about two days on NOMADS, LAMP and
    MAV about ten, hence the default window.
    """
    now = (now or dt.datetime.now(dt.timezone.utc)).replace(minute=0, second=0, microsecond=0)
    out = []
    if source in ("nbh", "nbs"):
        for k in range(lookback_hours + 1):
            t = now - dt.timedelta(hours=k)
            out.append((t, NBM_TEXT_URL.format(date=t.strftime("%Y%m%d"), hh=t.strftime("%H"), kind=source)))
    elif source == "lamp":
        for k in range(lookback_hours + 1):
            t = now - dt.timedelta(hours=k)
            out.append((t.replace(minute=30), LAMP_TEXT_URL.format(date=t.strftime("%Y%m%d"), hh=t.strftime("%H"))))
    elif source == "mav":
        t = now.replace(hour=(now.hour // 6) * 6)
        for _ in range(lookback_hours // 6 + 1):
            out.append((t, MAV_TEXT_URL.format(date=t.strftime("%Y%m%d"), hh=t.strftime("%H"))))
            t -= dt.timedelta(hours=6)
    else:
        raise ValueError(f"unknown bulletin source {source!r}")
    return out


def latest_nbm_cycle(lookback_hours: int = 4, kind: str = "nbh") -> tuple[dt.datetime, str]:
    """Most recent NBM cycle already on the bucket (first HEAD that answers)."""
    for t, url in bulletin_cycles(kind, lookback_hours):
        if _head(url):
            return t, url
    raise RuntimeError(f"no {kind.upper()} cycle on the bucket in the last {lookback_hours}h")


def latest_lamp_cycle(lookback_hours: int = 4) -> tuple[dt.datetime, str]:
    """Most recent :30 LAMP run on NOMADS (the run with the temperature row).
    Files post at about HH:36, not on the exact quarter hour."""
    for t, url in bulletin_cycles("lamp", lookback_hours):
        if _head(url):
            return t, url
    raise RuntimeError(f"no LAMP :30 run on NOMADS in the last {lookback_hours}h")


def latest_mav_cycle(lookback_cycles: int = 6) -> tuple[dt.datetime, str]:
    """Most recent GFS MOS MAV cycle (00/06/12/18Z) already posted."""
    for t, url in bulletin_cycles("mav", lookback_cycles * 6):
        if _head(url):
            return t, url
    raise RuntimeError(f"no MAV cycle on NOMADS in the last {lookback_cycles} cycles")


def fetch_bulletin_text(url: str) -> str:
    """A whole bulletin as text (NBM files are about 28-30 MB). Prefer
    fetch_bulletin_blocks, which streams and keeps memory near one chunk."""
    return _get_text(url, timeout=180)


def fetch_bulletin_blocks(url: str, stations: list, marker: str, timeout: int = 180,
                          tries: int = 2) -> tuple:
    """
    Stream a bulletin and keep only the wanted stations' blocks, verbatim.
    A 28 MB NBM file read whole and split into lines peaks near 125 MB of
    memory; streamed a megabyte at a time it stays near the chunk size,
    which is what lets the job run in a small function. The urlopen timeout
    is an idle timeout, so a stalled transfer fails within `timeout` seconds
    rather than running on.
        -> (blocks: {station: text}, bytes_read, total_block_count)
    """
    wanted = set(stations)
    last_err: Optional[Exception] = None
    for attempt in range(tries):
        out: dict = {}
        current, lines, nblocks, total = None, [], 0, 0
        buf = b""

        def take(line: str):
            nonlocal current, lines, nblocks
            if marker in line:
                if current in wanted:
                    out[current] = "\n".join(lines)
                current, lines = line.split()[0], [line]
                nblocks += 1
            elif current is not None:
                lines.append(line)

        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    total += len(chunk)
                    buf += chunk
                    parts = buf.split(b"\n")
                    buf = parts.pop()
                    for raw in parts:
                        take(raw.decode("ascii", "replace").rstrip("\r"))
            if buf:
                take(buf.decode("ascii", "replace").rstrip("\r"))
            if current in wanted:
                out[current] = "\n".join(lines)
            return out, total, nblocks
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500 and e.code != 429:
                raise
            last_err = e
        except Exception as e:
            last_err = e
        time.sleep(2.5 * (attempt + 1))
    raise RuntimeError(f"bulletin fetch failed after {tries} tries: {last_err}")


def station_blocks(text: str, stations: list[str], marker: str) -> dict[str, str]:
    """
    Each wanted station's bulletin block, verbatim. Kept separate from the
    parsers so the archive stores exactly what NOAA published. A block runs
    from one header line containing `marker` (" NBH GUIDANCE", " LAMP
    GUIDANCE" ...) to the next; the station id is the header's first token,
    matched exactly.
    """
    wanted = set(stations)
    out: dict[str, str] = {}
    current, lines = None, []
    for line in text.splitlines():
        if marker in line:
            if current in wanted:
                out[current] = "\n".join(lines)
            current, lines = line.split()[0], [line]
        elif current is not None:
            lines.append(line)
    if current in wanted:
        out[current] = "\n".join(lines)
    return out


def nbh_station_blocks(text: str, stations: list[str]) -> dict[str, str]:
    return station_blocks(text, stations, BULLETIN_MARKER["nbh"])


# Kept for readers of the NBM docs: "fetch the whole NBH bulletin".
fetch_nbm_text = fetch_bulletin_text


def parse_hourly_block(block: str) -> dict:
    """
    One hourly station block (NBH or LAMP; both use a UTC row and a TMP row of
    25 three-character cells) -> {"cycle": datetime, "rows": [{"time",
    "temp_f"}]}. The valid hours are the UTC row; they advance one day each
    time the hour value wraps past midnight. Temperatures are Fahrenheit
    integers as published.
    """
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{2})(\d{2}) UTC", block)
    if not m:
        raise ValueError("bulletin block has no cycle header")
    mo, da, yr, hh, mi = map(int, m.groups())
    cycle = dt.datetime(yr, mo, da, hh, mi, tzinfo=dt.timezone.utc)

    def row(label: str) -> list:
        # " UTC  20 21 ..." -> 4-char label, then right-aligned 3-char cells
        # starting at column 5. Sliced by position, not split on whitespace,
        # because a winter "-12" fills its cell and touches its neighbour.
        # Missing cells ("NG" or blank) become None IN PLACE: dropping them
        # per-row would misalign every hour after the gap when the two rows
        # are zipped together. A block without the row is an error, not an
        # empty forecast: the LAMP :00/:15/:45 files carry no TMP row.
        for line in block.splitlines():
            if line[:4].strip() == label:
                cells = [line[5 + 3 * i: 8 + 3 * i].strip() for i in range(25)]
                return [int(c) if c not in ("", "NG") else None for c in cells]
        raise ValueError(f"bulletin block has no {label} row")

    hours, temps = row("UTC"), row("TMP")
    rows, day, prev = [], cycle.date(), cycle.hour
    for h, t in zip(hours, temps):
        if h is None:
            continue
        if h < prev:                       # wrapped past 23Z into the next day
            day += dt.timedelta(days=1)
        prev = h
        if t is None:                      # a gap hour: skip the pair, keep alignment
            continue
        rows.append({"time": dt.datetime(day.year, day.month, day.day, h,
                                         tzinfo=dt.timezone.utc),
                     "temp_f": float(t)})
    return {"cycle": cycle, "rows": rows}


parse_nbh_block = parse_hourly_block
parse_lamp_block = parse_hourly_block


def bulletin_cycle(block: str) -> dt.datetime:
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{2})(\d{2}) UTC", block)
    if not m:
        raise ValueError("bulletin block has no cycle header")
    mo, da, yr, hh, mi = map(int, m.groups())
    return dt.datetime(yr, mo, da, hh, mi, tzinfo=dt.timezone.utc)


def parse_columns(block: str, labels: tuple, ncols: int) -> dict:
    """
    The named rows of a bulletin block as lists of `ncols` integers (None for
    blank or 'NG' cells). Every bulletin family (NBH, NBS, LAMP, MAV) lays its
    rows out the same way: a label in the first four characters, then
    right-aligned three-character cells from column 5. Slicing by position is
    what keeps '-12-13' and '108107' apart.
    """
    out: dict = {}
    for line in block.splitlines():
        lab = line[:4].strip()
        if lab in labels:
            cells = [line[5 + 3 * i: 8 + 3 * i].strip() for i in range(ncols)]
            out[lab] = [int(c) if c.lstrip("-").isdigit() else None for c in cells]
    return out


def column_times(cycle: dt.datetime, hours: list) -> list:
    """UTC datetimes for a row of valid hours. The hour values advance a day
    each time they wrap past midnight; None cells stay None in place."""
    out, day, prev = [], cycle.date(), cycle.hour
    for h in hours:
        if h is None:
            out.append(None)
            continue
        if h < prev:
            day += dt.timedelta(days=1)
        prev = h
        out.append(dt.datetime(day.year, day.month, day.day, h, tzinfo=dt.timezone.utc))
    return out


def _hour_row_width(block: str, label: str) -> int:
    for line in block.splitlines():
        if line[:4].strip() == label:
            return max(0, (len(line.rstrip()) - 5 + 2) // 3)
    return 0


def parse_daily_extremes(block: str, hour_label: str, value_label: str) -> dict:
    """
    The daily max/min row of an NBS (`TXN`) or MAV (`N/X`) block, plus the
    3-hourly temperature row. Convention, checked against the 2026-08-21
    bulletins by character alignment: the value under a 00Z column is the
    MAXIMUM of the day that ends there (the daytime max of the previous local
    day in every US zone), and the value under a 12Z column is the MINIMUM of
    the night ending that morning. Converting each column time to the
    station's local zone therefore lands each extreme on its own local day.

        -> {"cycle", "rows": [{"time", "temp_f"}],
            "extremes": [{"time", "kind": "max"|"min", "temp_f"}]}
    """
    cycle = bulletin_cycle(block)
    n = _hour_row_width(block, hour_label)
    if not n:
        raise ValueError(f"bulletin block has no {hour_label} row")
    # GFS MOS names the extremes row by which extreme comes first in the
    # cycle's columns: 'N/X' when the 12Z minimum leads (12Z/18Z cycles),
    # 'X/N' when the 00Z maximum leads (00Z/06Z cycles). Both are accepted.
    labels = (value_label, "X/N", "N/X") if value_label in ("N/X", "X/N") else (value_label,)
    cols = parse_columns(block, (hour_label, "TMP") + labels, n)
    hours = cols.get(hour_label, [])
    times = column_times(cycle, hours)
    if "TMP" not in cols:
        raise ValueError("bulletin block has no TMP row")
    rows = [{"time": t, "temp_f": float(v)} for t, v in zip(times, cols.get("TMP", []))
            if t is not None and v is not None]
    values = next((cols[lab] for lab in labels if lab in cols), None)
    if values is None:
        raise ValueError(f"bulletin block has no {'/'.join(labels)} row")
    extremes = []
    for t, v in zip(times, values):
        if t is None or v is None:
            continue
        if t.hour == 0:
            extremes.append({"time": t, "kind": "max", "temp_f": float(v)})
        elif t.hour == 12:
            extremes.append({"time": t, "kind": "min", "temp_f": float(v)})
    return {"cycle": cycle, "rows": rows, "extremes": extremes}


def parse_nbs_block(block: str) -> dict:
    return parse_daily_extremes(block, "UTC", "TXN")


def parse_mav_block(block: str) -> dict:
    return parse_daily_extremes(block, "HR", "N/X")


def fetch_nbm_hourly(stations: list[str]) -> dict[str, dict]:
    """Latest NBH cycle, parsed, for the given stations. One bulletin
    download serves every station, so call this once per pass, not per city."""
    cycle, url = latest_nbm_cycle()
    blocks = station_blocks(fetch_bulletin_text(url), stations, BULLETIN_MARKER["nbh"])
    return {sid: parse_hourly_block(b) for sid, b in blocks.items()}


# ===========================================================================
# HURRICANES — National Hurricane Center
#
# Two feeds cover the hurricane display. CurrentStorms.json is the roster of
# active cyclones with advisory metadata and links. The geometry — forecast
# track, the cone of uncertainty, past track, and the development-outlook
# regions with formation odds — comes from NOAA's public ArcGIS REST service
# as GeoJSON, so there is no shapefile or KMZ decoding. Layers are addressed
# by NAME ("EP3 Forecast Cone"), resolved through the service's layer index
# at call time, because NHC has renumbered layer ids before.
#
# Cyclone numbers 1-49 are real storms; 80-89 are internal NHC test entries
# that occasionally appear in feeds and must be skipped.
# ===========================================================================
NHC_CURRENT = "https://www.nhc.noaa.gov/CurrentStorms.json"
NHC_MAP = ("https://mapservices.weather.noaa.gov/tropical/rest/services/"
           "tropical/NHC_tropical_weather/MapServer")


def fetch_current_storms() -> list[dict]:
    """Active tropical cyclones, NHC's own roster. Each entry carries the
    basin bin ('EP3'), classification, intensity, and advisory links."""
    out = []
    for s in (_get(NHC_CURRENT) or {}).get("activeStorms", []):
        try:
            num = int(s["id"][2:4])
        except (KeyError, ValueError):
            continue
        if not 1 <= num <= 49:                 # 80-89 are test storms
            continue
        out.append(s)
    return out


_NHC_LAYERS: dict | None = None


def reset_nhc_layers() -> None:
    """Forget the layer index. A warm process (a Lambda container) would
    otherwise keep a renumbered index for its whole life; the hurricane job
    calls this once per pass, one small request."""
    global _NHC_LAYERS
    _NHC_LAYERS = None


def fetch_nhc_layer(name: str) -> list[dict]:
    """GeoJSON features of one named layer, e.g. 'AT1 Forecast Cone' or
    'Seven-Day: Potential Development Region'. Empty when the layer does not
    exist or holds nothing, which is the normal quiet-basin case."""
    global _NHC_LAYERS
    if _NHC_LAYERS is None:
        _NHC_LAYERS = {l["name"]: l["id"] for l in _get(f"{NHC_MAP}?f=json")["layers"]}
    lid = _NHC_LAYERS.get(name)
    if lid is None:
        return []
    q = urllib.parse.urlencode({"where": "1=1", "outFields": "*", "f": "geojson"})
    return (_get(f"{NHC_MAP}/{lid}/query?{q}") or {}).get("features", [])


# ===========================================================================
# LOCAL DAY BUCKETING
# ===========================================================================
def local_day(when: dt.datetime, tz_offset_hours: float) -> dt.date:
    """
    A daily high contract resolves on a local calendar day while observations
    arrive in UTC. Getting this wrong shifts a whole day's maximum, which is the
    single most likely source of a display disagreeing with settlement, so route
    every day boundary through one function.

    tz_offset_hours is a simplification for the demo. Production should use the
    IANA zone from resolve_grid()["timezone"] so daylight saving is handled.
    """
    return (when + dt.timedelta(hours=tz_offset_hours)).date()


def daily_max(observations: list[dict], tz_offset_hours: float) -> dict[dt.date, float]:
    out: dict[dt.date, float] = {}
    for o in observations:
        d = local_day(o["time"], tz_offset_hours)
        out[d] = max(out.get(d, -999.0), o["temp_f"])
    return out


def daily_min(observations: list[dict], tz_offset_hours: float) -> dict[dt.date, float]:
    """Half the listed contracts are on the daily LOW, bucketed the same way."""
    out: dict[dt.date, float] = {}
    for o in observations:
        d = local_day(o["time"], tz_offset_hours)
        out[d] = min(out.get(d, 999.0), o["temp_f"])
    return out


# ===========================================================================
# ARCHIVE
#
# The append-only record of every forecast cycle lives in pipeline/archive.py.
# Two reasons it is not optional.
#
# 1. The chart needs the forecast that was standing before the day began, and
#    no public endpoint will hand that back to you later.
# 2. The scorecard scores forecasts against what actually happened, which
#    requires knowing what each source said at the time.
# ===========================================================================


# ===========================================================================
# POLLING CADENCE
#
# NWS publishes an appropriate-use policy, monitors load, and may block IP
# addresses that affect its service delivery. Match the refresh rate of the
# data rather than polling faster, and pull server side into your own store so
# clients read from you and not from a government endpoint. aviationweather.gov
# documents 100 requests a minute and blocks for abuse.
#
#   METAR observations      hourly at :51-:56, plus SPECIs off-cycle
#   Gridpoint forecast      about hourly, underlying cycle nearer six hours
#   NBM text                hourly, landing ~35-40 min after the hour
#   LAMP text               the :30 run each hour, landing ~HH:36
#   GFS MOS MAV             00/06/12/18Z, landing ~4h15m after cycle time
# ===========================================================================
POLL_SECONDS = {"observations": 600, "forecast": 1800, "nbm": 1800, "lamp": 1800, "mav": 1800}


if __name__ == "__main__":
    REGISTRY = {  # demo subset; the full contract roster lives in cities.py
        "KLAX": ("Los Angeles", 33.9381, -118.3889, -7),
        "KPHX": ("Phoenix",     33.4278, -112.0035, -7),
        "KPHL": ("Philadelphia", 39.8733, -75.2268, -4),
    }
    stations = [s for s in sys.argv[1:] if s in REGISTRY] or list(REGISTRY)

    obs = fetch_observations(stations)
    for s in stations:
        name, lat, lon, tz = REGISTRY[s]
        grid = resolve_grid(lat, lon)
        fc = fetch_hourly_forecast(grid)
        highs = daily_max(obs.get(s, []), tz)
        lows = daily_min(obs.get(s, []), tz)
        today = local_day(dt.datetime.now(dt.timezone.utc), tz)

        print(f"\n{name} ({s})  grid {grid['wfo']}/{grid['x']},{grid['y']}  {grid['timezone']}")
        print(f"  observations   {len(obs.get(s, []))} reports, decode source '{TEMP_SOURCE}'")
        print(f"  high so far    {highs.get(today, float('nan')):.1f} F  (local day {today})")
        print(f"  low so far     {lows.get(today, float('nan')):.1f} F")
        fc_today = [r["temp_f"] for r in fc if local_day(r["time"], tz) == today]
        if fc_today:
            print(f"  NWS forecast   {max(fc_today):.1f} F remaining today, {len(fc)} hours ahead")
        cli = fetch_cli_report(s[1:])     # CLI products are filed by climate id: KLAX -> LAX
        if cli:
            print(f"  CLI report     max {cli['max_f']} / min {cli['min_f']}  for {cli['summary_date']}")
        if obs.get(s):
            print(f"  latest METAR   {obs[s][-1]['raw'][:88]}")


# ===========================================================================
# NHC wind speed probabilities (PWSAT)
# ===========================================================================
_PWS_ROW = re.compile(r"^(.{1,16}?)\s+(34|50|64)\s+(.*)$")
_PWS_CUM = re.compile(r"(?:\d+|X)\(\s*(\d+|X)\)")


def parse_wind_probabilities(text: str) -> list[dict]:
    """The PWSAT product (NHC's tropical cyclone wind speed probabilities):
    for each location and threshold (34, 50, 64 kt) the text lists the
    probability of onset in each period and, in parentheses, the cumulative
    probability through it. The last cumulative value is the five-day
    probability. 'X' means under 1 percent. Returns [{location, p34, p50,
    p64}] in percent, highest 64-kt probability first, rows with nothing
    above zero dropped."""
    if "<pre" in text.lower():
        m = re.search(r"<pre[^>]*>(.*?)</pre>", text, re.S | re.I)
        if m:
            import html as _html
            text = _html.unescape(m.group(1))
    by_loc: dict = {}
    for line in text.splitlines():
        mm = _PWS_ROW.match(line.strip())
        if not mm:
            continue
        loc, kt, rest = mm.group(1).strip(), mm.group(2), mm.group(3)
        cums = _PWS_CUM.findall(rest)
        if not cums:
            continue
        final = cums[-1]
        d = by_loc.setdefault(loc, {"location": loc, "p34": 0, "p50": 0, "p64": 0})
        d[f"p{kt}"] = 0 if final == "X" else int(final)
    rows = [d for d in by_loc.values() if max(d["p34"], d["p50"], d["p64"]) > 0]
    rows.sort(key=lambda d: (-d["p64"], -d["p50"], -d["p34"]))
    return rows


def fetch_wind_probabilities(url: str) -> list[dict]:
    """The PWSAT text behind a roster entry's windSpeedProbabilities link.
    One attempt with a short timeout: this runs last in the half-hourly
    chain and must not be able to eat the chain's remaining budget."""
    return parse_wind_probabilities(_fetch(url, tries=1, timeout=20).decode("ascii", "replace"))


# ===========================================================================
# NHC best-track archive (HURDAT2)
# ===========================================================================
# HURDAT2 is the Atlantic best-track database: every system back to 1851,
# post-season reanalysed and quality controlled. That reanalysis is what makes
# it the right basis for a climatological pace, and it is also why it only
# covers completed seasons -- a year is not added until the following spring.
# The season to date therefore comes from the live ATCF best tracks instead
# (pipeline/hurricane.py), and the two are never mixed for the same year.
#
# The file name carries the last season included and the date the file was
# cut, e.g. hurdat2-1851-2025-02272026.txt, and changes about once a year, so
# it is discovered from the directory listing rather than written down here.
# The same directory holds the eastern North Pacific database
# (hurdat2-nepac-1949-...) and a couple of older Atlantic layouts
# (hurdat2-atl-...); the pattern below matches only the current Atlantic file.
HURDAT_DIR = "https://www.nhc.noaa.gov/data/hurdat/"
_HURDAT_ATLANTIC = re.compile(r"hurdat2-1851-(\d{4})-(\d+)\.txt")


def latest_hurdat_url() -> str:
    """The newest Atlantic HURDAT2 file NHC is publishing, as a full URL.
    The listing names each file twice (href and link text), so collect a set;
    the season year is fixed width and leads the cut date, so a plain sort
    puts the newest season last."""
    names = sorted({m.group(0) for m in _HURDAT_ATLANTIC.finditer(_get_text(HURDAT_DIR, timeout=60))})
    if not names:
        raise RuntimeError(f"no Atlantic HURDAT2 file found in {HURDAT_DIR}")
    return HURDAT_DIR + names[-1]


def fetch_hurdat(url: str) -> str:
    """The best-track archive itself, about 7 MB of ASCII. Pulled roughly once
    a year in practice: what it feeds is cached against the file name."""
    return _get_text(url, timeout=180)
