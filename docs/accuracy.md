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
Lead `h = (T_i - t) / 3600` in hours. An alternative forecast system's timestamp `t` is the moment its
value was in the capture panel's hands (the capture time), which is an upper
bound on its availability; the ForecastEx prediction market's timestamp is the ladder snapshot's own
time. Issuance times are recorded only for nine of the seventeen sources and
appear only as a sensitivity in the method note.

**Truth.** The ForecastEx prediction market pays on the station's METAR settle: the day's extreme
over hourly and special reports, rounded half up to a whole degree Fahrenheit,
over the station-local clock day. Highs pay when `settle > strike` strictly;
lows when `settle < strike` strictly. The ForecastEx prediction market is always scored against the
settle. Alternative forecast systems are scored against the settle by default and, through a toggle,
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

**Market sources.** The ladder is reconstructed from two records. The first is
the desk's own quote capture, which holds both sides of the book at every
snapshot and begins in mid-May. The second is the exchange's published archive,
a tape of every trade and a daily closing file, which begins the day the
temperature board opened and carries the record back more than three months
further. A traded price is taken as both sides of that strike at that instant;
each strike's last traded price is then carried forward to every hour of the
day, so a ladder is assembled across strikes that traded at different times.
The closing file is used only for the strike set that was listed and for
settlement, never for a price, because its untraded strikes carry a stale mark
whose crossing sits close to a degree away from the quoted one. Every row
records which lane it came from in a `src` column, and the two lanes were
compared over their full overlap: the crossings agree to a median of about a
tenth of a degree.

**Forecast sources.** Each source's own capture is its record wherever the
capture reaches. For the eight sources Open-Meteo carries, the months before
their capture began are reconstructed from Open-Meteo's archive, cut at the
first target date the capture itself holds, so the archive never restates a
day the capture already has. Every reading records which lane it came from.

The archive is indexed by valid hour, not by run: for each hour it carries
that hour's value in the run of one day earlier and of two days earlier. So
the reconstruction reads, at an instant and for a target day, the extreme over
the hours of that day still to come, each hour taken from the freshest
archived run that existed at the instant, which is the one-day run for an hour
within a day of it and the two-day run beyond. That makes the reconstruction a
remaining-window source, scored the way the Aviation Forecast and the Blend
already are, with the observation bank supplying what already happened. It
also makes it stale against a live capture by up to a run cycle, because the
archive has no finer step than a day.

That handicap is measured rather than assumed. Over the months where both
records exist, the same city-days and the same leads, the reconstruction's
mean absolute error less the capture's, in degrees:

| system | highs 30 h | 18 h | 12 h | 6 h | lows 30 h | 18 h | 12 h | 6 h |
|---|---|---|---|---|---|---|---|---|
| European AI Ensemble Mean | −0.21 | −0.16 | −0.13 | +0.06 | +0.05 | +0.02 | +0.02 | +0.02 |
| European Model | +0.38 | +0.47 | +0.55 | +0.05 | +0.06 | +0.08 | +0.02 | +0.06 |
| American Model | −0.06 | +0.02 | +0.24 | +0.04 | +0.49 | +0.91 | +0.26 | +0.33 |
| German Model | −0.09 | −0.03 | +0.04 | +0.02 | +0.02 | +0.16 | +0.02 | +0.03 |
| Canadian Model | −0.10 | +0.06 | +0.13 | +0.02 | +0.18 | +0.32 | +0.04 | +0.06 |
| UK Model | +0.03 | +0.05 | +0.18 | +0.10 | −0.12 | +0.03 | −0.00 | +0.00 |
| French Model | −0.08 | +0.08 | +0.24 | +0.06 | −0.08 | +0.09 | +0.01 | +0.01 |
| Japanese Model | −0.49 | −0.18 | −0.06 | +0.01 | −0.11 | +0.04 | +0.01 | +0.02 |

A positive number is a handicap against the reconstructed source. Most cells
are a tenth of a degree or less against errors of about two degrees, and the
median of the two values themselves is identical to a tenth. Two cells are
large enough to matter and the page says so: the European model's highs, where
the reconstruction is about half a degree worse in the middle of the day, and
the American model's lows, where it is nine tenths worse at 18 hours. The
measurement is rebuilt with the record by `om_measure.py` beside the builder.

The four statistical guidance sources have a second archive, Iowa State's,
which keeps every bulletin as it was issued with its cycle time and its
forecast hours. That lane needs no allowance for staleness: the bulletin
standing at an instant is the last one issued before it, exactly as in life.
What the archive does not carry is the moment a bulletin reached a reader, so
a reconstructed bulletin is timed to its cycle plus the median lag the capture
itself measures for that source, 1.3 hours for the Aviation Forecast, 5.3 for
GFS MOS, 3.3 for NAM MOS and 2.2 for Blend MOS. Measured the same way on the
months both records cover, that lane's readings are identical to the capture's
to a tenth of a degree at the median, and its worst mean absolute error
difference at any lead is two tenths. `mos_measure.py` rebuilds it.

**Observation sources.** The observation record is the desk's own METAR
ingest, which begins 2026-03-09, plus Iowa State's ASOS archive before that,
fetched once into a directory the builder reads beside the database. The ingest
wins wherever both hold a report, so the archive only fills gaps and never
restates a day the desk already holds. Temperatures are converted from the
report's Celsius field, never from the archive's whole-degree Fahrenheit
column, which would round away the half degree the settlement rule turns on,
and routine and special reports are fetched separately because the archive
distinguishes them by request rather than by column. Over the 600 United States
city-days from 2026-02-11 to 2026-03-07 the archive alone reproduces the
published settle exactly, on highs and on lows.

**Standing value.** At any instant `t` a system's raw value is its last record
available at or before `t` for that target date, forward filled within the
target date and seeded by the earliest record for that date; nothing later is
ever read. The default value is what a reader held at that minute: for highs
`max(raw, bank_high(t))`, for lows `min(raw, bank_low(t))`, where the bank is
the running settle-rounded extreme of the station's reports up to that exact
instant. The bank is applied to the ForecastEx prediction market's central value as well. The
forecast-only value is the raw value, undefined after the system's last live
update for the target day. Carry-forward of earlier bulletins is not a page
rule.

**Which days.** Two bases, both about days and neither a grouping of systems.
`own` scores each system on the city-days its own record covers and the
ForecastEx prediction market priced, which is the default everywhere. `fixed30`
restricts those days to the ones the ForecastEx prediction market priced at
every hour from 30 down to 0, so a curve drawn across those hours is drawn on
one set of days and its slope cannot be an artefact of the sample changing
underneath it. Every comparison between two systems, the skill score and the
percent improvement alike, is computed on the days the pair share, so a
system's own span never has to match another's.

**Exclusions**, counted and printed, never silent: backfilled forecast rows;
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
differences (ForecastEx prediction market minus a system) are resampled under the same draws.

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
| AIFS | European AI Ensemble Mean | alternative forecast system |
| ECMWF_IFS | European Ensemble Mean | alternative forecast system |
| GFS_MOS | GFS MOS | alternative forecast system |
| NAM_MOS | NAM MOS | alternative forecast system |
| NBS_MOS | Blend MOS | alternative forecast system |
| HRRR | HRRR | alternative forecast system, no value above 18 h |
| HRRR_OM | HRRR (Open-Meteo) | alternative forecast system |

Every export uses these ids. There are no sub-groups: every system beside the exchange's own market is an alternative forecast system and is presented the same way as the others.

AIFS is ECMWF's AI forecasting system, captured directly as the hourly
ensemble mean at eight runs a day rather than through the panel, so its daily
extreme is taken here as the maximum and minimum of the captured hours over
the station-local day, from each capture that covers the whole of it.

## 3. Files

All files sit under `snapshots/accuracy/`. Every file carries

```
meta: { schema, asof, built, window: {from, to}, roster: [ids], conventions: "v1",
        cohorts: {fixed30: {from, cities, n_high, n_low}},
        exclusions: [{reason, dates, count}],
        systems: {id: {name, start, lag_p50_h, kind,
                       scored: {start, end, days},
                       byMetric: {high: {start, end, days}, low: {...}}}} }
```

`start` is the first target date on which at least twenty stations hold a raw
value at any grid hour, so it is when the source entered the capture. `scored`
is narrower: the first and last target date the system was actually scored on
and how many such dates there are, after exclusions. `byMetric` splits that by
high and low, which the ForecastEx prediction market needs, since its highs were priced from the day
the temperature board opened and its lows were too thinly quoted to score for
some months after. The page prints the scored span, not the raw one.

`schema` is the string `accuracy-figures/1`. `asof` is the newest resolved
target date; `built` the build time. Numbers are rounded to three decimals;
counts are integers; a missing value is `null`.

### lead-curve.json

```
{ meta, metric: {high: BLOCK, low: BLOCK} }
BLOCK = { h: [36..0],
          cohorts: { own: SERIES, fixed30: SERIES },
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
A city with fewer than 30 matched city-days for an alternative forecast system carries `null` values.

### grid.json

```
{ meta,
  h: [30, 18, 12, 6],
  cohorts: { own: ROWS, fixed30: ROWS },
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
lead curve; the city map; the scorecard grid; calibration and Brier; how the
market moves between forecast cycles. Then the coverage strip, then the
conventions. Highs default, lows a tab on every figure. No commentary. The old
lead curve stays published until this page is live.

**Spans are printed everywhere.** The systems do not share a record. The
coverage strip draws one bar per system over the days it was scored on, with
its own metric tabs. Every figure's method note carries a span line saying
which days that view used, every legend entry carries the date its system's
record starts, and the scorecard grid carries a `Record since` column in every
cohort, beside the cohort's own first day. A sample begins where the
last of its members begins, and where the ForecastEx prediction market's own record runs back behind
that, the note says so rather than letting the cohort's start stand in for the
market's.
