# The accuracy page: conventions and data contract

This document is the contract between the record builder, which runs on the
machine that holds the capture, and the site, which publishes what the builder
produces. Every number on the accuracy page is computed by the builder under the
conventions in section 1 and shipped in the files of section 3. The site's own
job (`pipeline/accuracy.py`) validates those files and copies them into
`snapshots/accuracy/`; it computes nothing of its own.

The conventions are the working paper's, *Improvement of Daily Temperature
Forecasts from a Prediction Market* (Brown, 2026, EarthArXiv preprint,
doi 10.31223/X5522D), and every estimator the page draws is a port of the
paper's own analysis code, so a number on the page can be traced to the paper's.
The page goes beyond the paper in one place: the paper scores the daily maximum,
and the page carries the minimum too, scored with the same methods.

The builder is not in this repository because it names the capture system and
its host. Its code is kept beside this site's other private extractor.

## 1. Conventions, fixed once

**Roster.** The 24 United States stations the exchange lists daily temperature
contracts on. IANA zones come from `config/cities.json`. Colorado Springs was a
test station that the exchange never listed and appears nowhere. Honolulu and
the international stations are outside this page's scope.

**Two clocks.** For a city-day `i` with station-local date `D`, `T_i` is the
station-local midnight that ends `D`, in UTC through the zone. Absolute lead is
`h = (T_i - t) / 3600` in hours, binned hourly from 36 to 0. Relative lead counts
back from the last METAR report of the day that reached the settle value: an
instant `t` before that report enters bin `k = ceil((t_last - t) / 1 h)`, kept
for `k` from 30 to 1. An alternative's timestamp is the moment its value was
captured, or for a reconstructed run its publication time; the market's is the
ladder snapshot's own time.

**Series and their standards.** The market is always scored against the METAR
settle its contracts pay on, the day's extreme over hourly and special reports,
rounded half up to a whole degree Fahrenheit over the station-local clock day;
highs pay when `settle > strike` strictly, lows when `settle < strike`. Every
alternative is scored against the observation it is built to predict, and five
systems are scored twice, which gives 25 series across 20 systems
(section 2):

- The three station MOS products that issue a daily extreme of their own, GFS
  MOS, NAM MOS and the NBM station bulletin, are scored on that issued value, a
  daytime maximum for 07 to 19 local standard time (the X/N and TXN rows of the
  bulletin), against the National Weather Service climate report, and on their
  hourly rows, the extreme of the hours left in the day, against the settle.
- The National Weather Service forecast's daytime maximum is scored against the
  climate report. The issued-maximum series are scored only on the city-days
  whose final report times the extreme inside the window (for a low, 00 to 08
  local standard time, the part of the overnight window the report's calendar
  day covers). A final report without a time takes it from the latest earlier
  report, interim or final, that gives the same extreme.
- The forecast and the National Blend of Models are also scored against URMA,
  the Service's gridded analysis, at the 2.5 km grid box holding the station:
  the forecast's daytime maximum against the largest hourly analysis over 07 to
  19 local standard time (all 13 present), and the Blend's daily value against
  the largest over the station-local day (every hour present), each rounded half
  up. The URMA series score the maximum only.
- Every other alternative is scored against the settle.

Buckley Field has no climate report of its own (Denver's stands in), so the
series scored against the report have no value there.

**Market value.** For each ladder snapshot the Yes price per strike is the
midpoint of the Yes bid and one dollar less the No bid when both are quoted.
With one side quoted it is the midpoint against the empty side at its limit, a
missing Yes bid counting as one cent and a missing No bid as a ninety-nine-cent
ask. A strike with no bid on either side is unquoted, and so is a two-sided book
bidding one cent against ninety-nine or a lone side sitting at that limit. The
ladder is made monotone in the strike by pooling adjacent violators. The
crossing `x` is the linear interpolation of the 0.5 level between the two
bracketing quoted strikes; a ladder that never crosses has no central value at
that snapshot, and neither does one whose bracketing quoted strikes have two or
more listed strikes with no price between them. The whole-degree central value
is `ceil(x)` for highs and `floor(x)` for lows, because settlement is strict.
The market's value at an instant is its last ladder at or before it, if under an
hour old, and it is used as quoted, never held at the observed extreme.

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
values the NOAA GFS's series returns within a day to 9.55 hours for the
JMA GSM, so a reading is never credited with a run a reader could not
yet have had. Until 2026-09-16 archived runs were credited at initialization,
which let a reading use a run up to about ten hours before it was out and
lowered those systems' 30-hour errors by up to 0.12 degrees. That makes the reconstruction a
remaining-window source, scored the way LAMP and the National Blend of Models
already are, with the observation bank supplying what already happened. It
also makes it stale against a live capture by up to a run cycle, because the
archive has no finer step than a day.

That handicap is measured rather than assumed. Over the months where both
records exist, the same city-days and the same leads, the reconstruction's
mean absolute error less the capture's, in degrees:

| system | highs 30 h | 18 h | 12 h | 6 h | lows 30 h | 18 h | 12 h | 6 h |
|---|---|---|---|---|---|---|---|---|
| ECMWF AIFS | −0.21 | −0.16 | −0.13 | +0.06 | +0.05 | +0.02 | +0.02 | +0.02 |
| ECMWF IFS | +0.38 | +0.47 | +0.55 | +0.05 | +0.06 | +0.08 | +0.02 | +0.06 |
| NOAA GFS | −0.06 | +0.02 | +0.24 | +0.04 | +0.49 | +0.91 | +0.26 | +0.33 |
| DWD ICON | −0.09 | −0.03 | +0.04 | +0.02 | +0.02 | +0.16 | +0.02 | +0.03 |
| ECCC GEM | −0.10 | +0.06 | +0.13 | +0.02 | +0.18 | +0.32 | +0.04 | +0.06 |
| Met Office UM | +0.03 | +0.05 | +0.18 | +0.10 | −0.12 | +0.03 | −0.00 | +0.00 |
| Météo-France ARPEGE | −0.08 | +0.08 | +0.24 | +0.06 | −0.08 | +0.09 | +0.01 | +0.01 |
| JMA GSM | −0.49 | −0.18 | −0.06 | +0.01 | −0.11 | +0.04 | +0.01 | +0.02 |

A positive number is a handicap against the reconstructed source. Most cells
are a tenth of a degree or less against errors of about two degrees, and the
median of the two values themselves is identical to a tenth. Two cells are
large enough to matter: the ECMWF IFS highs, where
the reconstruction is about half a degree worse in the middle of the day, and
the NOAA GFS lows, where it is nine tenths worse at 18 hours. The
measurement is rebuilt with the record by `om_measure.py` beside the builder,
and the table was measured while archived runs were still credited at
initialization.

The four statistical guidance sources have a second archive, Iowa State's,
which keeps every bulletin as it was issued with its cycle time and its
forecast hours. That lane needs no allowance for staleness: the bulletin
standing at an instant is the last one issued before it, exactly as in life.
What the archive does not carry is the moment a bulletin reached a reader, so
a reconstructed bulletin is timed to its cycle plus the median lag the capture
itself measures for that source, 1.3 hours for LAMP, 5.3 for
GFS MOS, 3.3 for NAM MOS and 2.2 for the NBM station bulletin. Measured the same way on the
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

**Standing value.** At any instant `t` an alternative's raw value is its last
record available at or before `t` for that target date; nothing later is ever
read. The value scored is what a reader held at that minute, for highs
`max(raw, bank_high(t))` and for lows `min(raw, bank_low(t))`, where the bank is
the running settle-rounded extreme of the station's reports up to that exact
instant, so an alternative is never charged for a forecast the observations had
already falsified. The market is not held.

**Bias correction.** The fifteen grid-box series, the raw models, the three
ensemble medians, the AI model, the National Blend of Models scored against the
settle and the National Weather Service forecast, carry a trailing station bias
correction (the paper's T14 with T16's set). For each series, station, metric
and lead, the mean of its own scored error over the trailing 30 target dates is
subtracted from the held value, and the result is held at the observed extreme
again. Only dates whose day had ended, and whose standard had been published, at
the forecast instant count (`d-30` to `d-k`, `k = max(1, ceil((h + lag)/24))`,
with `lag` 10 hours for the series scored against the climate report or URMA and
0 for the settle, a day further back where the 23-hour spring day would let
`d-k` end after the instant, and a climate report first published later than
the rule leaves the windows of the instants before it), and at least ten such
dates are needed. A row with no offset yet, the warm-up, is not scored for that
series. The relative clock uses each instant's absolute-lead offset. The market,
the station MOS series and the two URMA series are left as issued. Every figure
carries the values as issued beside the corrected ones.

**Ensembles.** Four systems publish ensemble members: the ECMWF AIFS, NOAA
GEFS, ECCC GEPS and DWD ICON-EPS. Each one's distribution for the day's extreme
is built from every remaining hour's ensemble mean and member spread, each hour a
normal distribution, with the hourly errors taken as perfectly correlated: a high
exceeds strike `k` with probability `1 - Phi(min_h (k + 0.5 - m_h) / s_h)`, the
Fréchet bound, and a low is the mirror. The median of that distribution, the
highest remaining hourly mean held at the observed extreme, is each ensemble's
central value (`GEFS`, `GEM_ENS`, `ICON_ENS`; the AIFS row is its ensemble-mean
series). An instant with no hour left has no value.

**EMOS.** The ensembles' distributions are calibrated by ensemble model output
statistics (the paper's T14, refit in T17). For each ensemble, station, metric
and lead window (36 to 25, 24 to 13 and 12 to 1 hours), a normal whose mean is
`a + b mu` and whose variance is `c + d sd^2` is fitted by minimising the CRPS of
the forecast held at the observed extreme against the settle, on the instants of
the trailing 30 days that had ended with reports still to come, at least ten
days and twenty rows; `mu` and `sd` are the highest remaining hourly mean and its
spread. The fit is applied to every remaining hour. A fit with `b` outside 0.2
to 2 or `|a|` over 50 is set aside, the latest fit carries forward, and before
the first fit the raw ensemble stands. A low is fitted on the negated centre,
outcome and running minimum, the mirror of the maximum.

**Probabilities.** CRPS is summed over every whole degree across the market's
strikes, the market's ladder closed at its end strikes, highs `F(k) = 1 - P(k)`
and lows `F(k - 1) = P(k)`. The market's prices are used as quoted, held to the
exchange's range of 1 to 99 cents; an ensemble's chance for a contract the
observations already settled is 100 cents, and any other is held to the same
range. The CRPS figures draw every line on the contracts and city-day hours the
market and all four ensembles share. The reliability diagrams take each strike's
last price in its hour on the city-days all four ensembles cover, in two windows,
lead in (12, 24] before the end of the day and in (6, 18] before the extreme,
pooled in ten price bins. Each system's summary is its Brier score, the mean
squared difference between each contract's price and its outcome, taken contract
by contract, and the slope of a logistic regression of outcomes on the log-odds
of the price. The file also carries the paper's summary, the count-weighted mean
absolute difference between price and payout share over the ten bins (`mad`).
The page prints the Brier score in its place (owner, 2026-11-13), because the
mean difference counts calibration alone and the Brier score also counts how
cleanly the prices separate the contracts that paid from those that did not.

**Which days.** Two bases. `all` scores each series on the city-days its own
record covers and the market priced, which is the default everywhere, and every
comparison between the market and a series is computed on the days the two
share. `balanced` restricts the days to those on which every series holds a
corrected value at every lead from 1 to 30 hours before the end of the day (the
paper's balanced sample; no hour is left for the ensembles at 0).

**Exclusions.** A city-day is scored unless it cannot be built: it is dropped
when it has no settlement value, when the market has no central value at any
lead, or when its recorded settle carries an ingest error. The last are the
Buckley Field days of the paper's `settle_exclusions_t16.csv`, kept beside the
builder (`settle_exclusions.csv`), where the day's top report was stored rounded
to a half degree and rounded again or a valid report was dropped by a quality
guard; they leave both metrics. An instant whose ladder never crosses one half
is unpaired for every series, since a comparison needs the market's value. The
retired rules (a thin book, a thin date, too few price snapshots, an hour with
no report, a day of other than 24 hours) are still computed and reported, never
applied.

**Capped captures.** The forecast feed returns at most 5,000 rows per request,
so a capture that reached the limit came back cut at a valid time and stored a
maximum over part of the day. Where the cut explains the stored value exactly,
the reading is replaced by the full-day value rebuilt from the rows the feed
held at that capture's instant (`ACC_WETHR_REPL`); anything that differs for
another reason is left as captured.

**Uncertainty.** Every interval is a 95 percent percentile interval over 1,000
resamples of seven-day blocks of target dates, drawn circularly (seed 20260911),
which keeps a week of weather together. A drawn bin needs 30 city-days. The
market is better than a series at a lead when their paired difference in error
is below zero at the 5 percent level after Holm's adjustment across the series
compared there (on the map, across the series at that station), the p-value from
the normal approximation to the bootstrap distribution of the mean difference. A
system counts as beaten only when every series of it is beaten, so the counts
are over the 20 systems.

**Language.** Yes bid, No bid, Yes price. Never ask, offer, sell, fair value,
or a model probability of the site's own.

## 2. Naming

Every file uses these ids, and `meta.series` carries each one's name, its
qualifier (the daily value it is scored on, whether it is corrected, and its
standard), its system, its group and its scored span. The names are the paper's
Table 3; the page writes "issued max" out as "issued maximum", and names the AI
model's series an ensemble mean, the day's largest hourly ensemble mean, where
the paper's Table 3 calls it a median.

| id | Series | Group | Standard | Offset |
|---|---|---|---|---|
| FX | ForecastEx prediction market | | METARs | as quoted |
| LAMP | LAMP | Station MOS | METARs | as issued |
| NBS_MOS | NBM station MOS, issued maximum | Station MOS | NWS CLI | as issued |
| NBS_MOS_ROW | NBM station MOS, hourly rows | Station MOS | METARs | as issued |
| GFS_MOS | GFS MOS, issued maximum | Station MOS | NWS CLI | as issued |
| GFS_MOS_ROW | GFS MOS, hourly rows | Station MOS | METARs | as issued |
| NAM_MOS | NAM MOS, issued maximum | Station MOS | NWS CLI | as issued |
| NAM_MOS_ROW | NAM MOS, hourly rows | Station MOS | METARs | as issued |
| MOSMIX | DWD MOSMIX | Station MOS | METARs | as issued |
| NBM | National Blend of Models, METAR | Blend | METARs | corrected |
| NBM_URMA | National Blend of Models, URMA | Blend | URMA | as issued |
| NDFD | National Weather Service, climate report | Human-edited | NWS CLI | corrected |
| NDFD_URMA | National Weather Service, URMA | Human-edited | URMA | as issued |
| AIFS | ECMWF AIFS ens. mean | Artificial intelligence | METARs | corrected |
| HRRR | NOAA HRRR | Raw numerical weather prediction | METARs | corrected |
| ECMWF | ECMWF IFS | Raw numerical weather prediction | METARs | corrected |
| ECMWF_IFS | ECMWF IFS ens. mean | Raw numerical weather prediction | METARs | corrected |
| GFS | NOAA GFS | Raw numerical weather prediction | METARs | corrected |
| GEFS | NOAA GEFS median | Raw numerical weather prediction | METARs | corrected |
| ICON | DWD ICON | Raw numerical weather prediction | METARs | corrected |
| ICON_ENS | DWD ICON-EPS median | Raw numerical weather prediction | METARs | corrected |
| GEM | ECCC GEM | Raw numerical weather prediction | METARs | corrected |
| GEM_ENS | ECCC GEPS median | Raw numerical weather prediction | METARs | corrected |
| UKMO | Met Office UM | Raw numerical weather prediction | METARs | corrected |
| MF | Météo-France ARPEGE | Raw numerical weather prediction | METARs | corrected |
| JMA | JMA GSM | Raw numerical weather prediction | METARs | corrected |

One HRRR is carried, the Open-Meteo rendering, since the desk's direct capture
reaches only 18 hours. AIFS is ECMWF's AI forecasting system, captured as the
hourly ensemble mean. The probabilistic files key the four ensembles `AIFS`,
`GEFS`, `GEM` and `ICON`. `config/forecast_systems.json` lists every series in
the paper's groups for the systems table at the foot of the page.

## 3. Files

All files sit under `snapshots/accuracy/`. Every file carries

```
meta: { schema: "accuracy-figures/2", asof, built, window: {from, to}, roster: [ids], conventions: "v2",
        series: [{id, name, qual, system, group, standard, corrected, second, kind,
                  scored: {start, end, days}, byMetric: {high: {start, end, days}, low: {...}}}],
        groups: [{title, series: [ids]}], twenty: [system ids],
        cohorts: {all: {from, cities, n_high, n_low}, balanced: {n_high, n_low, from_high, from_low}},
        waterfall: {high: {listed, no_settlement, no_market_median, settle_ingest_error, scored, ...}, low: {...}},
        exclusions: [{reason, dates, count}], settleExcluded: [{city, date}],
        inference: {draws, blockDays, seed, holmAlpha, minDays},
        correction: {windowDays, minDates, publishLagH},
        leads: {six: {abs: [30, 18, 12], rel: [18, 12, 6]}, relMax, partialH},
        notes: {urma: {hours, first, last}, emos: {fits, implausible, fitted_share}} }
```

`series` is in the paper's order, the market first. `scored` is the span a
series is actually scored over, its warm-up left out, whole and per metric.
`asof` is the newest resolved target date; `built` the build time. Numbers are
rounded to four decimals, so a value printed to two rounds once, as in the
paper's tables, and the Brier score, printed to three, to five; counts are
integers; a missing value is `null`.

A **series block** is `{mae, lo, hi, n}` per bin, and for an alternative also
`{pi, piLo, piHi, better, worse}`: its mean absolute error (or CRPS) with the 95
percent interval and its own count, and the market's percent improvement over
it on the city-days both hold, `100 (1 - sum e_FX / sum e_s)`, with its interval
and the Holm verdicts. A **beats** entry per bin is `{k, of, notBeaten}`, the
systems compared, those the market is better than, and the rest.

### lead-curve.json

```
{ meta, metric: { high|low: { cohorts: { all|balanced: { clocks: { abs|rel: {
    bins: [36..0] | [30..1], share: [per bin],
    corrected: { series: {id: series block}, beats: [per bin], lowest: [per bin] },
    issued: { ... }                                   (cohort all only) } } } } } } }
```

`share` is the right-hand axis, on the absolute clock the share of eligible
city-days whose extreme had already been observed (strictly after the last
report at the settle) and on the relative clock the share still holding a market
price that far ahead. `lowest` marks the bins where the market's error is below
every system's best series. `corrected` scores the grid-box series with the
correction, `issued` scores every series without it.

### calibration.json

```
{ meta, ensembles: {AIFS|GEFS|GEM|ICON: {name, series}},
  metric: { high|low: {
    crps: { span: {start, end, days}, clocks: { abs|rel: { bins,
            calibrated: {series: {FX|AIFS|GEFS|GEM|ICON: series block}, beats},
            issued: {...} } } },
    reliability: { abs|rel: { window: [lo, hi],
            calibrated: {FX|AIFS|...: {summary: {n, brier, mad, slope, slopeLo, slopeHi}, bins: [[price, share, count]]}},
            issued: {AIFS|...: {...}} } } } } }
```

`calibrated` is EMOS, `issued` the raw ensembles; the market is the same in both.

### map.json

```
{ meta, cities: [{id, name, px, py, tz}], leads: [{key, clock, lead}],
  cellFields: ["pi", "lo", "hi", "n", "marketMae", "seriesMae", "sig"],
  metric: { high|low: { abs_30|abs_18|rel_18|rel_12: { id: {
      cells: {station: [pi, lo, hi, n, marketMae, seriesMae, sig]},
      median: {pi, lo, hi, cities} } } } } }
```

The four leads are the paper's Fig 8. A cell is scored on the city-days the
market and the series both hold a value at that lead, bias-corrected; `sig` is 1
where the market is better after Holm across the series at that station, -1
where the series is, 0 otherwise. A station under 30 matched city-days keeps
its count with no value, and a station with none has no cell. The median is over
the stations with a value, and its interval is the spread of the per-draw
medians.

### grid.json

```
{ meta, six: [{clock, lead}],
  metric: { high|low: { cohorts: { all|balanced: {
      series: {id: {mae: [6], lo: [6], hi: [6], pi: [6], better: [6], n: [6], me18}},
      groups: [{title, series: [ids by 18 h error], mean: [6], meanSeries: [6]}],
      issued: {...},                                    (cohort all only)
      crps: {series: {FX|AIFS|GEFS|GEM|ICON: {...}}, order: [ids by 18 h]}  (cohort all only) } } } } }
```

The paper's Tables 3 and 4 at the six leads. A group's `mean` is over its
systems, each on its first series, the paper's group rows; `meanSeries` counts
every series. The groups are in the order of their 18 h mean.

## 4. Transport

The builder writes the files above to a private prefix under the archive,
`data/archive/accuracy/latest/`, with a `manifest.json` of the form
`{schema, built, asof, files: [{name, size, sha256}]}` listing every file by its
path under `latest/`. The site's `accuracy` job, in the half-hourly chain, reads
the manifest, and when its `built` differs from the one last published, copies
each file to `snapshots/accuracy/` after checking that it parses and carries
`meta.schema` equal to `accuracy-figures/2`, `meta.asof` and `meta.conventions`
equal to `v2`. A file of an earlier schema is skipped and reported, so the page
never reads a shape it does not draw. A build none of whose files passes is not
published, so the files in place and their record stay and the pass reports a
failure. A record that wrote no file never counts as the build being published,
so a build that arrives before the code that reads its schema is published once
that code is deployed. The pages read only `snapshots/accuracy/`. The standalone
`dynamics.json` and the per-date `trace/` files were retired on 2026-09-15; a
`trace/` file a manifest still lists is checked on its own stamps (`meta.date`
and `meta.built`), and the job prunes the published traces once a manifest stops
listing them.

**Keeping it running.** The daily service on the machine that holds the capture
does six things in order: fetch the exchange's published archive, extend the
Iowa State MOS archive for the record's stations (a chunk is fetched again until
it was written after its last day was complete), extend the URMA archive by the
hours published since the last run (`urma.py`; URMA lands about seven hours
after its hour, so the newest day of the two URMA series is completed by the
next day's run), build the record, push the figure files here, and back up
everything irreplaceable to `data/archive/accuracy/backup/` on the same bucket,
which is not served. Nothing in that chain involves a laptop. The site's own
account copies the files into place every half hour and, past three days without
a new build, mails a health alarm.

## 5. The page

Under the title the page opens on the research brief and the paper's first
page. Then, each section with a method note carrying the estimator, the sampling
rule and the conventions it depends on behind a "Show details of calculation"
button, and a status strip naming the window and the build time:

- **Deterministic skill.** The paper's Fig 6: the mean absolute error of the
  market and the 25 series by lead on either clock, coloured by the paper's
  groups (station MOS green with LAMP heavier, the Blend teal, the National
  Weather Service orange, the AI model purple, the raw models tan), a system's
  second series dashed, the corrected series drawn faint as issued behind
  their corrected lines, the right-hand axis of each clock, and strips counting
  the market's city-days and the systems it beats (`lead-curve.json`).
- **Probabilistic skill.** The paper's Fig 9: the CRPS of the market and the
  four EMOS-calibrated ensembles by lead, the raw ensembles faint, then the
  reliability diagrams of the two windows with each system's Brier score and slope
  (`calibration.json`).
- **City map.** The paper's Fig 8: the market's percent improvement over a
  chosen series at each station at the four single leads, bold where Holm holds,
  with the median over stations (`map.json`).
- **Scorecard.** The paper's Tables 3 and 4 (`grid.json`), then the source
  tables.

Highs default, lows a tab on every figure. No commentary. Every chart against
lead is drawn by one helper (`WXAcc.leadChart`). The convergence chart and the
Brier chart left the page with the paper's methods on 4 October 2026. The Brier
score came back on 13 November 2026 as the reliability table's summary, in place
of the mean gap (owner's decision).

**The source tables.** The foot of the page lists every series, the market
first and then the paper's five groups. Each row says whether the system is
deterministic, an ensemble mean, or probabilistic; gives the provider's grid
spacing, time step and update frequency; and links to its documentation. The
record column is filled at load from each series' scored span. A second table
lists the other data sources behind this page.
