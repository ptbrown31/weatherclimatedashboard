# The accuracy page: conventions and data contract

This document is the contract between the record builder, which runs on the
machine that holds the capture, and the site, which publishes what the builder
produces. Every number on the accuracy page is computed by the builder under the
conventions in section 1 and shipped in the files of section 3. The site's own
job (`pipeline/accuracy.py`) validates those files and copies them into
`snapshots/accuracy/`; it computes nothing of its own.

The builder is not in this repository because it names the capture system and
its host. Its code is kept beside this site's other private extractor.

## 1. Conventions, fixed once

**Roster.** The 24 United States stations the exchange lists daily temperature
contracts on. IANA zones come from `config/cities.json`. Colorado Springs was a
test station that the exchange never listed and appears nowhere. Honolulu and
the international stations are outside this page's scope.

**Clock.** One clock everywhere. For a city-day `i` with station-local date `D`,
`T_i` is the station-local midnight that ends `D`, in UTC through the zone.
Lead `h = (T_i - t) / 3600` in hours. A tool's timestamp `t` is the moment its
value was in the capture panel's hands (the capture time), which is an upper
bound on its availability; the market's timestamp is the ladder snapshot's own
time. Issuance times are recorded only for nine of the seventeen sources and
appear only as a sensitivity in the method note.

**Truth.** The market pays on the station's METAR settle: the day's extreme
over hourly and special reports, rounded half up to a whole degree Fahrenheit,
over the station-local clock day. Highs pay when `settle > strike` strictly;
lows when `settle < strike` strictly. The market is always scored against the
settle. Tools are scored against the settle by default and, through a toggle,
against the National Weather Service climate report for the same date, which is
a different definition of the day's extreme (a rounded five-minute mean over
the standard-time day) and runs about a degree warmer on highs. Denver's
climate report stands in for Buckley Field, so Buckley is excluded from the
climate-report frame.

**Market value.** For each ladder snapshot the Yes price per strike is the
midpoint of the Yes bid and one dollar less the No bid when both are quoted,
the single quoted side otherwise. A strike with no bid on either side is
unquoted. A two-sided book bidding one cent against ninety-nine is unquoted.
The ladder is made monotone in the strike by pooling adjacent violators. The
crossing `x` is the linear interpolation of the 0.5 level between the two
bracketing strikes; a ladder that never crosses has no central value at that
snapshot and is counted as missing. The whole-degree value is `ceil(x)` for
highs and `floor(x)` for lows, because settlement is strict.

**Standing value.** At any instant `t` a system's raw value is its last record
available at or before `t` for that target date, forward filled within the
target date and seeded by the earliest record for that date; nothing later is
ever read. The default value is what a reader held at that minute: for highs
`max(raw, bank_high(t))`, for lows `min(raw, bank_low(t))`, where the bank is
the running settle-rounded extreme of the station's reports up to that exact
instant. The bank is applied to the market's central value as well. The
forecast-only value is the raw value, undefined after the tool's last live
update for the target day. Carry-forward of earlier bulletins is not a page
rule.

**Cohorts.** `matched11` at a lead `h` holds the city-days where all eleven
panel tools have a value at `h` and the market has a crossing at `h`. `core5`
holds the National Weather Service, the Blend, the Aviation Forecast, the
European model and the American model with the market. `fixed30` holds the
city-days present in `matched11` at every hour from 30 to 0. Own-span figures
use each source's own start and are never colored against another source.

**Exclusions**, counted and printed, never silent: backfilled tool rows;
European-ensemble rows captured under five hours after their nominal run;
thin-book city-days (the ladder standing at the 30 h anchor and the one
standing at the 18 h anchor both have under half their listed strikes carrying
a Yes bid, or no crossing, which is what keeps a ladder still filling in the
minutes after listing from counting as thin); thin-book dates (over half the
listed cities thin on that metric, excluded for every system); capture-short dates (under 120 ladder snapshots); city-days with an
empty local hour in the report record; two quarantined low settles; days of
other than 24 hours.

**Uncertainty.** Bootstrap over target dates, 1,000 draws, seed 20260910, 95
percent percentile intervals. A drawn bin needs 30 city-days. Paired
differences (market minus tool) are resampled under the same draws.

**Language.** Yes bid, No bid, Yes price. Never ask, offer, sell, fair value,
or a model probability of the site's own.

## 2. Naming

| id | Reader name | Source |
|---|---|---|
| FX | ForecastEx | the ladder capture, every 10 minutes |
| NDFD | National Weather Service | NWS point forecast, captured hourly |
| NBM | National Blend of Models | captured hourly |
| LAMP | Aviation Forecast | LAMP, captured hourly |
| ECMWF | European Model | ECMWF HRES, captured hourly |
| GFS | American Model | GFS raw, captured hourly |
| MOSMIX | German Statistical Model | four captures a day |
| ICON | German Model | four captures a day |
| GEM | Canadian Model | four captures a day |
| UKMO | UK Model | four captures a day |
| MF | French Model | four captures a day |
| JMA | Japanese Model | four captures a day |
| ECMWF_IFS | European Ensemble Mean | extra, own span |
| GFS_MOS | GFS MOS | extra, own span |
| NAM_MOS | NAM MOS | extra, own span |
| NBS_MOS | Blend MOS | extra, own span |
| HRRR | HRRR | extra, own span, no value above 18 h |
| HRRR_OM | HRRR (Open-Meteo) | extra, own span |

The eleven panel tools are NDFD through JMA. Every export uses these ids.

## 3. Files

All files sit under `snapshots/accuracy/`. Every file carries

```
meta: { schema, asof, built, window: {from, to}, roster: [ids], conventions: "v1",
        cohorts: {matched11: {from, cities, n_high, n_low}, core5: {...}},
        exclusions: [{reason, dates, count}], systems: {id: {name, start, lag_p50_h, kind}} }
```

`schema` is the string `accuracy-figures/1`. `asof` is the newest resolved
target date; `built` the build time. Numbers are rounded to three decimals;
counts are integers; a missing value is `null`.

### lead-curve.json

```
{ meta, metric: {high: BLOCK, low: BLOCK} }
BLOCK = { h: [36..0],
          cohorts: { matched11: SERIES, core5: SERIES, fixed30: SERIES },
          own: { id: {h: [...], mae: [...], n: [...]} }  // per-horizon own span, no interval
        }
SERIES = { n: [per h], systems: { id: { mae: [per h], lo: [per h], hi: [per h],
                                       maeRaw: [per h], loRaw, hiRaw,          // forecast-only view
                                       lastLiveH: int|null,                    // where the raw line ends
                                       ageMedianH: [per h], changesPerHour: [per h] } },
           beats: [per h: {k: int, of: int, ids: [ids beaten]}],
           beatsRaw: [per h: ...],
           binNote: [per h: {dates: [from, to], zones: {tz: count}} | null] }
```
Bins with `n < 30` carry `null` values. `h` above 30 is present only where the
cohort has members.

### dynamics.json

```
{ meta,
  converge: { metric: { tol1: { h: [36..0], systems: { id: { share: [...], median: number|null, never: number } } },
                        tol2: { ... } } },
  rate: { byLead: { metric: { h: [36..0], systems: { id: [changes per hour] } } },
          byLocalHour: { metric: { hour: [0..23], fx: [...], fxLo, fxHi, tools: { id: [...] } } },
          perCityDay: { metric: { fx: {median, q1, q3}, tools: { id: median } } } },
  event: { metric: { k: [0,10,...,120], systems: { id: { delta: [...], lo, hi } }, events: int } },
  traceIndex: { dates: [YYYY-MM-DD], default: {date, city}, byDate: { date: { city: { maeH12: number, changes: int } } } } }
```

### trace/{date}.json

One file per target date, the last 60 kept.

```
{ meta: {date, built}, cities: { id: {
    tz, dayStart, dayEnd, settleHigh, settleLow, listing,
    obs: [[t, tempF, isSpeci]],                 // hourly plus special reports, settle-rounded temp too
    bank: { high: [[t, v]], low: [[t, v]] },
    market: { high: [[t, ceil, x, q10, q90, nStrikes, twoSided]], low: [...] },
    strikes: { high: [[t, strike, p, state]] } , // state: 2 two-sided, 1 bid only, -1 ask only, 0 empty, 9 is 1/99
    tools: { id: { high: [[t, raw]], low: [[t, raw]], lastLive: t } } } } }
```
Times are ISO UTC strings; sizes are kept under 1 MB gzipped per date.

### calibration.json

```
{ meta,
  bins: { edges: [0,0.1,...,1.0] },
  metric: { high: { price: { mid: PRICEBLOCK, twoSided: PRICEBLOCK, yesBid: PRICEBLOCK } }, low: {...} } }
PRICEBLOCK = { leadBins: [[36,24],[24,12],[12,6],[6,0]],
               reliability: [ per lead bin: { n, nCityDays, brier, brierTrunc, retained,
                                              x: [10], y: [10], count: [10], lo: [10], hi: [10],
                                              hist: [10] } ],
               byLead: { h: [36,30,24,18,12,6,3,1], brier, lo, hi, brierBinned, rel, res, unc, brierTrunc, retained, bss, base, n },
               // brier is the raw score; brierBinned = rel - res + unc on the ten price bins;
               // brierTrunc keeps prices strictly inside (0.02, 0.98), retained is their share
               sharpness: { h: [...], width: [...], maeMedian: [...], n: [...] },
               movedPerHour: { h: [...], contracts: [...] } }
```

Shapes the builder settled where the text above was loose: trace `obs` rows
are `[t, tempF, tempRounded, isSpeci]`; grid cells carry `nLow` beside `n`;
availability exclusion rows carry `metric`; map cells are nested tool id then
city id; `meta.cohorts` includes `fixed30`.

### map.json

```
{ meta, cities: [ { id, name, px, py, tz } ],
  windows: { morning: "06 to 12 local", eve: "18:00 local the day before", newsletter: "5 PM to 5 AM ET" },
  metric: { high: { window: { morning: CELLS, eve: CELLS, newsletter: CELLS } }, low: {...} } }
CELLS = { frame: { metar: { toolId: { cityId: { fx: {mae, n}, tool: {mae, n}, pi, lo, hi, matched: n, fxChanges, toolChanges } | null } },
                   cli:   { ... } }, median: { frame: { toolId: {pi, lo, hi, colored: int} } } }
```
A city with fewer than 30 matched city-days for a tool carries `null` values.

### grid.json

```
{ meta,
  h: [30, 18, 12, 6],
  cohorts: { matched11: ROWS, core5: ROWS, own: ROWS },
  frames: ["metar", "cli"],
  newsletter: { window: {days, from, to}, rows: [ {id, mae, n, rank} ] } }
ROWS = [ { id, start, frame: { metar: CELLS, cli: CELLS } } ]
CELLS = { h: { "30": { maeHigh, maeLow, meHigh, hr1High, crps (FX only), n, ssHigh, ssLo, ssHi } ... } }
```
`ss` is the percent difference from the National Weather Service row on the
same cells; a `null` interval means the paired interval covered zero.

### availability.json

```
{ meta, metric: { high: { h: [36..0], systems: { id: [n per h] } } },
  exclusions: [ {reason, date, count} ], starts: { id: {perHorizon, fixedAnchor} } }
```

## 4. Transport

The builder writes the files above to a private prefix under the archive,
`data/archive/accuracy/latest/`, with a `manifest.json` of the form
`{schema, built, asof, files: [{name, size, sha256}]}` listing every file by
its path under `latest/`. The site's `accuracy` job, in the half-hourly chain, reads
the manifest, and when its `built` differs from the one last published, copies
each file to `snapshots/accuracy/` after checking that it parses and carries
`meta.schema`, `meta.asof` and `meta.conventions`. The pages read only
`snapshots/accuracy/`.

## 5. The page

Five figures, each with a method note carrying the estimator and the sampling
rule, and a status strip naming the window and the build time. In order: the
lead curve; how the market moves between forecast cycles; calibration and
Brier; the city map; the scorecard grid. Highs default, lows a tab on every
figure. No commentary. The old lead curve stays published until this page is
live.
