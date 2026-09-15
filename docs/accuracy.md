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
midpoint of the Yes bid and one dollar less the No bid when both are quoted.
With one side quoted it is the midpoint against the empty side at its limit, a
missing Yes bid counting as one cent and a missing No bid as a ninety-nine-cent
ask (from 2026-09-15; before, the one quoted side was read as the price, which
on a contract nobody bids Yes is the cost to buy Yes, not a midpoint). A strike
with no bid on either side is unquoted, and so is a two-sided book bidding one
cent against ninety-nine or a lone side sitting at that limit.

**Probabilities scored alike.** Wherever a probability is scored (CRPS, the
Brier score, the reliability diagrams), a contract the observations already
settled is scored at 100 cents for every system, the market included, and
every other probability is held to the exchange's range of 1 to 99 cents
(`PRICE_MIN`, `PRICE_MAX`), so no system is credited with a certainty a quote
cannot express. The probabilistic section draws every line on the city-day
hours at which the market and all four ensembles hold a value.
The ladder is made monotone in the strike by pooling adjacent violators. The
crossing `x` is the linear interpolation of the 0.5 level between the two
bracketing quoted strikes; a ladder that never crosses has no central value at
that snapshot and is counted as missing, and so does one whose bracketing
quoted strikes have two or more listed strikes with no price between them
(`MEDIAN_MAX_UNQUOTED_RUN`, from 2026-09-15). Late in the day a ladder often
quotes only its far strikes, 68 at 3 cents and 76 at 98 cents with nothing
between, and a straight line across that run would put the median at 72 when
the prices say only that it lies between 68 and 76. The whole-degree value is `ceil(x)` for
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
| HRRR | HRRR | alternative forecast system |

One HRRR is carried. The desk captures it directly with a real model cycle
but only out to 18 hours, which cannot be drawn across this page's whole lead
range, so the Open-Meteo rendering of the same model is the one kept and is
named simply HRRR.

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
          own: { id: {h: [...], mae: [...], n: [...]} },  // per-horizon own span, no interval
          crps: CRPS
        }
CRPS = { h: [36..0],
         cohorts: { own: { metar: CV, cli: CV }, fixed30: { metar: CV, cli: CV } },
         shared: { own: { start, end, days }, fixed30: {...} } }
CV = { systems: { FX|AIFS|GEFS|GEM|ICON: { crps: [per h], lo: [per h], hi: [per h], n: [per h] } },
       beats: [per h: {k: int, of: int, ids: [ensemble keys beaten]}] }
SERIES = { n: [per h], systems: { id: { mae: [per h], lo: [per h], hi: [per h],
                                       maeRaw: [per h], loRaw, hiRaw,          // forecast-only view
                                       maeCli: [per h], loCli, hiCli,          // climate-report frame
                                       lastLiveH: int|null,                    // where the raw line ends
                                       ageMedianH: [per h], changesPerHour: [per h] } },
           beats: [per h: {k: int, of: int, ids: [ids beaten]}],
           beatsRaw: [per h: ...],
           beatsCli: [per h: ...],
           binNote: [per h: {dates: [from, to], zones: {tz: count}} | null] }
```
In the climate-report frame each alternative forecast system is scored against
the National Weather Service report for the same date while the market keeps
the settle it pays on, so the market's `maeCli` equals its `mae` and Buckley
Field drops out of the frame.

Bins with `n < 30` carry `null` values. `h` above 30 is present only where the
cohort has members.

`crps` is the CRPS by lead that the probabilistic section draws. The market is
scored on its own standing ladder at each whole hour, and each ensemble at the
same instant on the same strikes, from a normal curve on its own centre and
spread over the hours left in the day, every probability scored alike (section
1), so the two are one measurement on one grid. The ladders are the ones the
market priced at that hour (restricted to the fixed sample in `fixed30`) at
which every ensemble also holds a reading, so every line is drawn on the same
ladders (`shared`). Ensembles are keyed by
their calibration key; the page maps GEM and ICON to the registry rows
`GEM_ENS` and `ICON_ENS` so they are never confused with the single runs. In
the climate-report frame an ensemble is held to the National Weather Service
report and the market keeps the settle, both on the city-days that hold a
report. There is no forecast-only CRPS.

`converge` is time to converge, drawn under the error curve:

```
converge: { h: [36..0],
            tol1: { own|fixed30: { metar|cli: { systems: { id: { share: [per h], median, never } } } } },
            tol2: { ... } }
```

`share[i]` is the share of a system's city-days whose held value was within
the tolerance (1 or 2 degrees) of the truth at every hour from `h[i]` to the end
of the day at which it held a value. An hour with no value, a gap in the
market's book or a crossing outside its ladder, is no evidence either way and
does not break the run; the days are the ones the system held a value on at
any lead. `median` is the lead at which half the days had converged,
interpolated between whole hours, and `never` the share that had not by the
end of the day. It is on the held value only: a raw record stops at a
forecast's last update, after which every later hour would pass for want of a
value. The frames and day bases are the lead curve's. The standalone
`dynamics.json` (changes per hour, the event strip and the traced city-day)
and the per-date `trace/` files were retired on 2026-09-15; the site's job
prunes the published traces once a manifest stops listing them.

### calibration.json

```
{ meta,
  bins: { edges: [0,0.1,...,1.0] },
  metric: { high: { cohorts: { own: COHORT, fixed30: COHORT } }, low: {...} } }
COHORT = { price: { mid: PRICEBLOCK, twoSided: PRICEBLOCK }, ensembles: { id: { name, span } },
           shared: { start, end, days } }     // the days every system holds a value on
PRICEBLOCK = { leadBins: [[36,24],[24,12],[12,6]],
               reliability: [ per lead bin: CELL ],
               ensembles: { id: { metar: [ per lead bin: CELL ], cli: [...] } },
               brier: BRIER }
CELL = { n, nCityDays, x: [10], y: [10], count: [10], lo: [10], hi: [10], hist: [10] }
BRIER = { h: [36..0],
          strikes: { all: BF, nearMoney: BF } }        // BF = { metar: BSET, cli: BSET }
BSET = { systems: { FX|AIFS|GEFS|GEM|ICON: { rms: [per h], lo, hi, brier: [per h], n: [contracts], days: [city-days] } },
         beats: [per h: {k, of, ids}] }
```

A diagram cell is the decile curve and its sample; the Brier score, reliability
and resolution numbers it carried until 2026-09-15 are gone, since nothing drew
them. The last lead bin (6 to 0 hours) was dropped on
2026-09-15, since by then most contracts are settled or priced at a cent.

`brier` is the Brier score by whole hour of lead, the mean over contracts of
(p - y)^2, and `rms` its square root in probability units, which the page draws
in cents: the typical gap between a contract's price and what it paid. Summed
over every one-degree strike of a ladder the Brier score is CRPS, so it is
averaged per contract here and reads in probability rather than degrees. The contracts are the market's own under the price rule at hours when all four
ensembles hold a reading, and every system is scored on exactly those contract
rows (city, day, hour and strike), so every line is drawn on the same contracts
and the same days (`shared`). Every probability is scored alike (section 1). `all` is every
quoted strike; `nearMoney` is the middle listed strike of the day's ladder and
the strike on either side (`NEAR_MONEY_OFFSET`, the desk's own convention),
fixed by the listing before any lead is scored. In `cli` the market keeps the
settle and both are restricted to the city-days holding a report. `lo` and
`hi` are the square roots of the date-bootstrap interval of the
contract-weighted mean; `beats` is the paired difference of per-city-day means
at each hour. A lead under 30 city-days carries nulls. The reliability and
resolution series by lead that stood here from 2026-09-15 were replaced by
this block the same day.

`own` scores every eligible city-day and `fixed30` the city-days the market
priced at every hour from 30 to 0, the lead curve's two day bases. The Yes-bid
price rule was dropped from the file on 2026-09-15.

Shapes the builder settled where the text above was loose: grid cells carry `nLow` beside `n`;
availability exclusion rows carry `metric`; map cells are nested tool id then
city id; `meta.cohorts` includes `fixed30`.

Four of the alternative forecast systems publish the spread of their ensemble
members as well as a centre, so they are scored on the same contracts. Their
diagram cells sit under each price rule (`price.<rule>.ensembles`), on that
rule's contracts, and `cohorts.<c>.ensembles.<id>` keeps the name and the span
of days the system's probabilities could be scored on, for the systems table.

The climate-report frame recomputes the contract's own outcome against the
National Weather Service report for the same date, so a station whose report
stands in for another drops out of it. The market keeps the settle its
contracts pay on in either frame, since that is what they pay on.

The reading is taken over the hours of the day still to come, from the
standing capture (the builder's `ens_standing` frame, from 2026-09-15). At a
standing instant the centre is the extreme of the capture's hourly ensemble
means over the station-local hours from that instant to the end of the day,
with the member spread at the hour that extreme falls on, and the day's
extreme is the larger (for a low, the smaller) of that and the observed extreme
so far. So a strike the observations already cleared is paid, any other pays
only if the hours left reach it (a normal curve on that centre and spread), and
at the end of the day, with no hours left, nothing more can. A high pays when
the unrounded extreme reaches half a degree past the strike and a low when it
falls half a degree under, which is how settlement rounds. Nothing is fitted
and no bias is removed. Read over the whole day instead, a 4 pm peak kept its
spread at 11 pm and gave a real chance to strikes the day could no longer
reach. The scorecard's ensemble rows take the same centre, held at the observed
extreme.

Two limits belong with these lines and are stated on the page: a system
publishes a spread for each hour rather than for the extreme of several hours,
so reading the spread at the hour of the extreme as the extreme's is this
page's approximation and not the system's; and the level bias each carries in
the error figures passes straight into its probability here, which shows up as
reliability.

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
  h: [30, 18, 12],
  cohorts: { own: ROWS, fixed30: ROWS },
  frames: ["metar", "cli"] }
ROWS = [ { id, start, frame: { metar: CELLS, cli: CELLS } } ]
CELLS = { h: { "30": { maeHigh, maeLow, meHigh, meLow, hr1High, n, nLow, ssHigh, ssLo, ssHi,
                       crps, crpsLow } ... } }   // crps keys only on rows with a distribution
```
`ss` is the percent difference from the National Weather Service row on the
same cells; a `null` interval means the paired interval covered zero. The 6 h
column and the newsletter ranking were removed on 2026-09-14.

The rows are every system in order, then the ensembles with no single-run row
of their own, under their registry ids `GEFS`, `GEM_ENS` and `ICON_ENS`. An
ensemble row scores its centre held at the running observed extreme, on the
city-days the market priced. `crps` and `crpsLow` are present on the market's
row, on the European AI row (the same product as its ensemble), and on the
three ensemble rows; they are the mean CRPS over the cell's city-days that the
distribution covers, `null` under 30 of them. The Canadian and German single
runs never carry their ensemble's CRPS.

The page draws the grid with the mean absolute error and then the mean error
under each lead, each on the high and the low. It does not order the rows by
score: it groups and orders them from `config/forecast_systems.json`, the same
registry the forecast systems table at the foot of the page is rendered from
at build time, so a change to the grouping or the order there moves both.

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

**Keeping it running.** The daily service on the machine that holds the
capture does four things in order: fetch the exchange's published archive,
build the record, push the figure files here, and back up everything
irreplaceable to `data/archive/accuracy/backup/` on the same bucket, which is
not served. Nothing in that chain involves a laptop. The site's own account
copies the files into place every half hour and, past three days without a new
build, mails a health alarm: a stale archive and a healthy pass look identical
from this side, so nothing else would notice.

## 5. The page

Two kinds of chart against lead, a city map, the scorecard grid and
reliability diagrams, each section with a method note carrying the estimator,
the sampling rule and the conventions it depends on (the settle, the clock,
the market's price and median, the exclusions, the bootstrap) behind a "Show
details of calculation" button, and a status strip naming the window and the
build time. In order:

- **Deterministic skill.** One value per system, the ForecastEx prediction
  market's being the median of its ladder: mean absolute error by lead, and
  under it on the same axis time to converge (`lead-curve.json`).
- **Probabilistic skill.** Every system that publishes a distribution, the
  market's ladder and the four ensembles: CRPS by lead (`lead-curve.json`
  `crps`) and the Brier score by lead (`calibration.json` `brier`), each as
  wide as the error curve, then the reliability diagrams for the three lead
  bins to six hours out.
- **City map**, **scorecard grid** and the source tables. The coverage strip
  and the conventions list were removed on 2026-09-15: the systems table
  carries every record's span, and each method note carries the conventions
  its section uses.

Highs default, lows a tab on every figure. No commentary. Every chart against
lead is drawn by one helper (`WXAcc.leadChart`), so the axis, the hatching of
the partial bins above 30 hours, the market's band and the step drawing of a
forecast look the same on all four.

**The source tables.** The foot of the page lists every forecast system,
the ForecastEx prediction market first and then the alternative systems in four
families: raw numerical weather prediction models, numerical weather prediction
with model output statistics, human forecasting systems, and AI systems. Each
row says whether the system is deterministic, an ensemble mean, or
probabilistic, and which section it appears in; gives the provider's grid
spacing, time step and update frequency; and links to its documentation. For
the systems whose run times this record holds, the update frequency is the one
measured from those run times. The record column is filled at load from each
system's scored span in the files' meta, and for an ensemble from the days its
probabilities were scored on. A second table lists the other data sources
behind the site, which the FAQ used to carry.

**Spans are printed everywhere.** The systems do not share a record. The
systems table dates every record. Every figure's method note carries a span line saying
which days that view used, every legend entry carries the date its system's
record starts, and the scorecard grid carries a `Record since` column in every
cohort, beside the cohort's own first day. A sample begins where the
last of its members begins, and where the ForecastEx prediction market's own record runs back behind
that, the note says so rather than letting the cohort's start stand in for the
market's.
