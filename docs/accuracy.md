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
value was captured, which is an upper bound on its availability; the
ForecastEx prediction market's timestamp is the ladder snapshot's own time.

**Truth.** The ForecastEx prediction market pays on the station's METAR settle: the day's extreme
over hourly and special reports, rounded half up to a whole degree Fahrenheit,
over the station-local clock day. Highs pay when `settle > strike` strictly;
lows when `settle < strike` strictly. The ForecastEx prediction market is always scored against the
settle. By default each alternative forecast system is scored against the observation its daily
value is built to predict, the own-target frame (owner's decision 2026-09-21). For the
alternatives that issue values at fixed hours that is the settle, since the daily value is the
extreme of those hours over the calendar day, which predicts the extreme of the hourly reports.
Four are the exception, each issuing a daily extreme of its own, a daytime maximum for 07 to 19
local standard time and an overnight minimum for 19 to 08: the National Weather Service forecast
and the station guidance of the GFS, the NAM and the National Blend of Models (`C.OWN_MAX_SYSTEMS`),
the last three read from the bulletin's own X/N and TXN rows rather than the maximum of its
temperature row. They are scored against the climate report on the city-days whose report puts the
extreme inside that window (for a low, 00 to 08, the part of the window the report's calendar day
covers; `C.NWS_WINDOW_LST`), and not on the other city-days, where they were not predicting the
day's extreme. Two toggles remain. The METAR
settle frame scores every alternative against the settle, that forecast included. The
climate-report frame scores every alternative against the National Weather Service climate report
for the same date, which is a different definition of the day's extreme (a rounded five-minute
mean over the standard-time day) and runs about a degree warmer on highs. Denver's climate report
stands in for Buckley Field, so Buckley is excluded from the climate-report frame and, for those
four, from the own-target frame.

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
archived run already published at the instant, which is the one-day run for an
hour within a day of it and the two-day run beyond. A run counts as published at
its initialization plus the delay Open-Meteo's own availability metadata reports
for that model (`om_backfill.PUBLISH_DELAY_H`), from 1.6 hours for the HRRR
values the American Model's series returns within a day to 9.55 hours for the
Japanese Model, so a reading is never credited with a run a reader could not
yet have had. Until 2026-09-16 archived runs were credited at initialization,
which let a reading use a run up to about ten hours before it was out and
lowered those systems' 30-hour errors by up to 0.12 degrees. That makes the reconstruction a
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
large enough to matter: the European model's highs, where
the reconstruction is about half a degree worse in the middle of the day, and
the American model's lows, where it is nine tenths worse at 18 hours. The
measurement is rebuilt with the record by `om_measure.py` beside the builder,
and the table was measured while archived runs were still credited at
initialization.

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

**Exclusions.** A city-day is scored unless it cannot be built (owner's
decision 2026-09-25): it is dropped only when it has no settlement value, which
includes a settle set aside after review, or when the market has no median at
any lead, that is when the ladder never crossed 50 cents inside its listed
strikes. The rules that used to exclude a city-day are still computed and
reported, never applied: a thin book at both anchors, a thin date, too few
price snapshots, an empty local hour in the report record, and a day of other
than 24 hours at the daylight saving changes. Two notes are printed beside
them, also never silent: backfilled forecast rows, and European-ensemble rows
captured under five hours after their nominal run.

**Capped captures.** The forecast feed returns at most 5,000 rows per request,
so a capture that reached the limit came back cut at a valid time and stored a
maximum over part of the day. Where the cut explains the stored value exactly,
the reading is replaced by the full-day value rebuilt from the rows the feed
held at that capture's instant (`ACC_WETHR_REPL`); anything that differs for
another reason is left as captured.

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

The three ensembles with no single-run row of their own are `GEFS` (American
Ensemble), `GEM_ENS` (Canadian Ensemble) and `ICON_ENS` (German Ensemble);
the calibration file keys them `GEFS`, `GEM` and `ICON`.

Every file uses these ids. Every system beside the ForecastEx prediction
market is an alternative forecast system. The page groups them into families
only through `config/forecast_systems.json`, which orders the systems table
and the scorecard grid alike.

AIFS is ECMWF's AI forecasting system, captured directly as the hourly
ensemble mean at eight runs a day, so its daily
extreme is taken here as the maximum and minimum of the captured hours over
the station-local day, from each capture that covers the whole of it.

## 3. Files

All files sit under `snapshots/accuracy/`. Every file carries

```
meta: { schema, asof, built, window: {from, to}, roster: [ids], conventions: "v1",
        cohorts: {own: {from, cities, n_high, n_low}, fixed30: {...}},
        exclusions: [{reason, dates, count}],
        systems: {id: {name, start, kind,
                       scored: {start, end, days},
                       byMetric: {high: {start, end, days}, low: {...}}}} }
```

`start` is the first target date on which at least twenty stations hold a raw
value at any grid hour, so it is when the source entered the capture. `scored`
is narrower: the first and last target date the system was actually scored on
and how many such dates there are, after exclusions. `byMetric` splits that by
high and low, which the ForecastEx prediction market needs, since its highs
and lows do not share a span. The page prints the scored span, not the raw
one. `cohorts.own` counts the ForecastEx prediction market's own eligible
city-days and `cohorts.fixed30` the fixed sample; `exclusions` is one entry
per reason, `dates` its distinct dates and `count` what the builder counted
for it, summed over highs and lows.

`schema` is the string `accuracy-figures/1`. `asof` is the newest resolved
target date; `built` the build time. Numbers are rounded to three decimals;
counts are integers; a missing value is `null`.

### lead-curve.json

```
{ meta, metric: {high: BLOCK, low: BLOCK} }
BLOCK = { h: [36..0],
          cohorts: { own: SERIES, fixed30: SERIES },
          crps: CRPS,
          converge: CONVERGE,
          relative: RELATIVE
        }
CRPS = { h: [36..0],
         cohorts: { own: { metar: CV, cli: CV }, fixed30: { metar: CV, cli: CV } },
         shared: { own: { start, end, days }, fixed30: {...} } }
CV = { systems: { FX|AIFS|GEFS|GEM|ICON: { crps: [per h], lo: [per h], hi: [per h], n: [per h] } },
       beats: [per h: {k: int, of: int, ids: [ensemble keys beaten]}] }
SERIES = { n: [per h], systems: { id: { mae: [per h], lo: [per h], hi: [per h],
                                       maeRaw: [per h], loRaw, hiRaw,          // forecast-only view
                                       maeCli: [per h], loCli, hiCli,          // climate-report frame
                                       n: [per h], nRaw, nCli,                 // the system's own city-days per view
                                       maeTarget, loTarget, hiTarget, nTarget, // own-target frame, NDFD only
                                       maeRawTarget, loRawTarget, hiRawTarget, nRawTarget,
                                       lastLiveH: int|null } },                // where the raw line ends
           beats: [per h: {k: int, of: int, ids: [ids beaten]}],
           beatsRaw: [per h: ...],
           beatsCli: [per h: ...],
           beatsTarget: [per h: ...], beatsRawTarget: [per h: ...],     // own-target frame
           binNote: [per h: {dates: [from, to], zones: {tz: count}} | null],
           observed: [per h: share 0..1 | null] }                  // extreme already observed
RELATIVE = { anchor: "last", k: [1..K],
             cohorts: { own|fixed30: { systems: { id: { mae, lo, hi, n, maeRaw, loRaw, hiRaw, nRaw,   // per k
                                                        maeTarget ... nRawTarget } },   // NDFD only
                                       beats: [per k], beatsRaw: [per k], beatsTarget: [per k], beatsRawTarget: [per k],
                                       n: [per k], nDays: int,
                                       share: [per k], tieShare: [per k] } } }
```
In the climate-report frame each alternative forecast system is scored against
the National Weather Service report for the same date while the market keeps
the settle it pays on, so the market's `maeCli` equals its `mae` and Buckley
Field drops out of the frame.

In the own-target frame only the National Weather Service forecast (`NDFD`) is scored against
anything but the settle, so only its entry carries the `...Target` series; every other system's
own-target series is its METAR series, and a page reads a missing `maeTarget` as `mae` (and a
missing `maeRawTarget` as `maeRaw`). `beatsTarget` and `beatsRawTarget` are the market's paired
comparisons in that frame. The forecast-only view has an own-target version (`maeRawTarget`);
the climate-report frame has none, so its forecast-only view is the METAR one.

`SERIES.n` is the ForecastEx prediction market's count of city-days per bin,
which the page prints in its strip; each system's own `n`, `nRaw` and `nCli`
count the city-days behind its value in each view. A system's bin with fewer
than 30 carries `null` values. `h` above 30 is present only where the cohort
has members.

`observed[i]` is the share of the market's scored city-days at `h[i]` whose
extreme had already been observed, some METAR report at or before that
instant having reached the settle value. It is zero above 24 h, before the
target day begins, and the page draws it on a second axis under the error
curve.

`relative` is the same error curve with lead counted back from the report that
set the day's extreme, using only the instants before that report, so every
value scored is a forecast made before the extreme happened. The extreme's time
is the last report of the day that reached the settle value
(`C.EXTREME_ANCHOR = "last"`; temperatures are whole degrees, so the value often
recurs, and the last report keeps the most hours). Bin `k` holds the instants
between `k - 1` and `k` hours before it. Each system keeps its own rows and its
held value (and the forecast-only value in `maeRaw`), in the METAR frame and, for the National
Weather Service forecast, in the own-target frame, which the page draws. `n` is
the market's count per bin and `nDays` its city-days in the cohort, `share` is
`n / nDays`, the share of its city-days holding a forecast that far ahead, and
`tieShare` the share of the bin's market instants at which an earlier report had
already reached the settle value. `k` runs to the last bin where any system has
30 city-days in the own cohort.

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
CONVERGE = { h: [36..0],
            tol1: { own|fixed30: { target|metar|cli: { systems: { id: { share: [per h], median, never } } } } },
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
map cells are nested tool id then city id.

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
standing capture (the builder's `ens_standing` and `ens_hours` frames). At a
standing instant the distribution of the day's extreme is built from every one
of the capture's station-local hours from that instant to the end of the day,
with the hourly errors taken as perfectly correlated (owner's decision
2026-09-25): the extreme is above a strike exactly when one of those hours is,
so a high pays with chance one less the normal probability at the smallest of
(strike + 0.5 - mean) / spread over those hours, and a low is the mirror. The
centre of that distribution is the extreme of the hourly means, the same value
the scorecard row carries. The day's extreme is the larger (for a low, the
smaller) of that and the observed extreme so far. So a strike the observations
already cleared is paid, any other pays only if the hours left reach it, and at
the end of the day, with no hours left, nothing more can. A high pays when
the unrounded extreme reaches half a degree past the strike and a low when it
falls half a degree under, which is how settlement rounds. Nothing is fitted
and no bias is removed. Read over the whole day instead, a 4 pm peak kept its
spread at 11 pm and gave a real chance to strikes the day could no longer
reach. The scorecard's ensemble rows take the same centre, held at the observed
extreme.

Two limits belong with these lines and are stated on the page: a system
publishes a spread for each hour rather than for the extreme of several hours,
and taking the hourly errors as perfectly correlated stands in for the members
it does not publish, which widens the distribution slightly; and the level bias
in each ensemble's centre, which the scorecard's mean error shows, passes
straight into its probability.

### map.json

```
{ meta, cities: [ { id, name, px, py, tz } ],
  windows: { morning: "6 AM to noon local", eve: "6 PM local the day before" },
  metric: { high: { window: { morning: CELLS, eve: CELLS } }, low: {...} } }
CELLS = { frame: { target: { sysId: { cityId: {...} } },        // own-target frame, the four that issue one
                   metar: { toolId: { cityId: { fx: {mae}, tool: {mae}, pi, lo, hi, matched } } },
                   cli:   { ... } },
          median: { target: { sysId: {...} }, metar: { toolId: {pi, lo, hi, cities, colored} }, cli: { ... } } }
```
The own-target frame holds the four systems that issue a daily extreme of their own, against the
climate report on the city-days it puts the maximum (minimum) inside their window; every other
tool's own-target cells are its METAR cells, and the page reads them from there.
A city-day's value in a window is the mean of the held value at each whole
hour of it (h 18 to 12 for the morning, h 30 for the evening before), and
counts only when every hour holds one. `pi` is 100 (1 - MAE_fx / MAE_tool)
on the matched city-days, with its interval from the ratio under the shared
draws. A city with fewer than 30 matched city-days carries `null` values. The
median runs over every city with a value (`cities`), its interval from the
per-draw median, and `colored` counts the cities whose own interval clears
zero. The newsletter window and the change counts were removed on 2026-09-15.

### grid.json

```
{ meta,
  h: [30, 18, 12],
  cohorts: { own: ROWS, fixed30: ROWS },
  frames: ["target", "metar", "cli"] }
ROWS = [ { id, start, frame: { target: CELLS, metar: CELLS, cli: CELLS } } ]
CELLS = { h: { "30": { maeHigh, maeLow, meHigh, meLow, n, nLow, ssHigh, ssLo, ssHi,
                       crps, crpsLow } ... } }   // crps keys only on rows with a distribution
```
`ssHigh` is the skill score on the high, 100 (1 - MAE_s / MAE_NWS) on the
city-days the row shares with the National Weather Service row; `ssLo` and
`ssHi` are `null` when its interval covered zero. In the own-target frame the National Weather
Service row, and every skill score against it, uses that forecast's own target. A distribution's
CRPS in that frame is its METAR-frame score, since its own target is the settle. The 6 h column and the
newsletter ranking were removed on 2026-09-14, and the hour-one column on
2026-09-15.

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

The standalone `availability.json` was retired on 2026-09-15; nothing drew it.

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
build time. The deterministic sections (error by lead, the map and the scorecard) open in the
own-target frame, with the METAR settle and climate-report frames one toggle away; the
probabilistic sections score the market and the ensembles against the settle, which is each one's
own target. In order:

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
probabilistic; gives the provider's grid spacing, time step and update
frequency; and links to its documentation. For
the systems whose run times this record holds, the update frequency is the one
measured from those run times. The record column is filled at load from each
system's scored span in the files' meta, and for an ensemble from the days its
probabilities were scored on. A second table lists the other data sources
behind this page.

**Spans are printed everywhere.** The systems do not share a record. The
systems table dates every record. Every figure's method note carries a span
line saying which days that view used, the deterministic legend carries the
date each system's record starts, and the scorecard grid carries a `Record
since` column in every cohort.
