# The analysis resolution page: conventions and data contract

This document is the contract for the `analysis` lane: the job (`pipeline/analysis.py`,
reading GRIB2 through `pipeline/grib2.py`), the files it writes under `snapshots/analysis2/`,
and the page (`site/analysis-resolution.html`, `site/js/analysis.js`) that draws them. The
lane shows how a proposed settlement framework would have resolved: daily and hourly
weather variables at the settlement locations, taken from NOAA's gridded hourly analyses
instead of station reports. No contract settles on it. The page says so on every view, is
not in the site's navigation, and carries `<meta name="robots" content="noindex">`.

Owner's decisions. 2026-09-27: unlisted; local civil day; 30 days backfilled from NOAA Open
Data then live; hourly map frames; the daylight-time days keep every analysis of the civil
date; a negative half rounds away from zero. 2026-09-28: a value equal to the strike resolves
Yes. 2026-09-29 (schema 2, this document): **RTMA resolves every variable, precipitation
included**; the sixty-seven settlement locations approved that day replace the fifty
population centres; every value is converted exactly and the day's aggregate rounded once;
the day's precipitation runs from midnight to midnight; a file NOAA Open Data lacks is read
from NCEP's NOMADS server. 2026-10-14 (owner's ruling, the draft terms): **a place-day is
final at its cut-off, 12:45 AM local time on the day after**, on the files published on NOAA
Open Data or NOMADS by then (a file's Last-Modified time on the server it was read from); a file
published later never counts, the first published version of a file counts, and a place-day with
no hour by the cut-off goes to event review. It replaced the rule of 2026-10-13 (a file on
neither server six hours after its hour was not published, and a day was final once every file
was in or not published), which had replaced half an hour after the product usually lands. 2026-11-03 (owner's ruling):
**the job checks every two minutes around the expected arrivals** (section 5), and reads each
hour's precipitation file as soon as NOAA publishes it rather than with the next analysis. The
same ruling has the job read NOMADS for every hour, since an hour is final once NOMADS has it;
that part is not built yet, and until it is the job reads NOMADS only for a file NOAA Open Data
lacks (section 1). 2026-09-30: **URMA is dropped from the lane and the page**, since
it does not resolve. The lane read it for comparison until then; it reads RTMA only now, the
published days, place files and CSV files were rewritten without it (`LAYOUT`, section 5),
and its archive and frames under `archive/analysis2/{hours,precip}/urma/` and
`snapshots/analysis2/grid/urma/` are left where they are, no longer read or listed. Schema
1's files under `snapshots/analysis/` and `archive/analysis/` are left as they were and are
no longer written; the page reads schema 2 only. The owner's draft terms for contracts on
this framework are the RTMA Daily Weather Contract Terms of 2026-09-29.

Everything here is computed by the site's own pipeline in the standard library. Nothing
reaches the page except the JSON files of section 4. Sections 1, 3, 4 and 5 describe what
is built, checked against the code on 2026-09-27; where the first draft of this contract
and the code disagreed, the text was corrected to the code except for the defects the
review found, which the code was corrected for.

## 1. Conventions, fixed once

**Sources.** The Real-Time Mesoscale Analysis (RTMA), NCEP's hourly 2.5 km analysis of the
CONUS NDFD grid, read from NOAA Open Data on AWS (`noaa-rtma-pds`, no credentials, HTTP/1.1
range requests honoured). Measured over the thirty days to 2026-09-27 from the objects' own
write times (S3 LastModified on NOAA Open Data; NOMADS directory times for the fifteen days
NOMADS keeps), as lag after the valid hour, median (90th percentile): the analysis 48 min (53)
on Open Data and 46 min (47) on NOMADS; the precipitation 17 min (21) after the end of its
hour. The Open Data copy trails NOMADS by a median of under two minutes and occasionally by
hours (up to 2 h 37 min on the evening of 2026-09-23, when NOMADS was on time). NOAA's own
publication times over the two weeks to 2026-11-03 put the analysis 46 to 52 minutes after its
hour (median 49) and an hour's precipitation 16 to 18 minutes after the hour ends; the windowed
passes of section 5 are set on them. A live poll of
the bucket on 2026-09-28 found each object listed within one 20-second poll of its
LastModified. NOAA writes each RTMA file once; neither the analysis nor the precipitation file
is rewritten. Over the thirty days to 2026-09-27 one file of the 720 analysis hours and none of
the precipitation hours was missing from NOAA Open Data: the analysis of 2026-09-23 19Z, which
NOMADS had (published there at 19:46Z) and which never reached the bucket. NCEP's NOMADS
server lists fourteen day directories under the same directory and file names
(`https://nomads.ncep.noaa.gov/pub/data/nccf/com/rtma/prod/`) and answers byte ranges with 206; on
2026-10-13 it held the analysis files for about the last ten days (the oldest valid 2026-10-03 20Z)
and the precipitation files for all fourteen. A file on NOMADS counts as published (owner's decision 2026-09-29): a file NOAA Open
Data does not have half an hour after the product usually lands (`lagMinutes` plus
`NOMADS_AFTER_MINUTES = 30`) is read from there while NOMADS still keeps it (section 5), a file on
neither `NOT_PUBLISHED_AFTER_HOURS = 6` hours after its valid time (a precipitation file, after its
hour ends) is listed in the state as not published, and every archive document records its
`source` and its `published` time, the Last-Modified header of the server it was read from (from
2026-10-14; earlier documents carry none). A precipitation file NOAA Open Data lacks is looked for
on NOMADS from `PCP_NOMADS_AFTER_MINUTES = 27` after its hour ends, so the day's last
accumulation, usually on the bucket 17 minutes after midnight, is looked for before the cut-off.
The draft terms (owner's decision 2026-10-14) say the NOMADS copy counts when the two servers'
copies of a file differ, and that values count as published, without adjustment. The lane reads
NOAA Open Data first and NOMADS only for a file NOAA Open Data lacks, so it never compares the
two copies; no difference between them has been seen.

**Where each variable comes from.**

| variable | RTMA file and message |
|---|---|
| temperature | `rtma2p5.tHHz.2dvaranl_ndfd.grb2_wexp`, message TMP 2 m above ground, K |
| wind (sustained) | same file, WIND 10 m, m/s |
| gust | same file, GUST 10 m, m/s |
| precipitation | `rtma2p5.YYYYMMDDHH.pcp.184.grb2`, APCP 0 to 1 hour, kg/m² (= mm), on grid G184 |

Keys are `<bucket>/rtma2p5.<YYYYMMDD>/<file>`. Every analysis file has a `.idx` sidecar
listing message byte offsets; the job
reads the sidecar and range-fetches only the three messages it needs (about 6 MB each). The
precipitation file is one message and is fetched whole (about 0.5 to 0.7 MB). The
precipitation hour `HH` covers `HH-1` to `HH` UTC.

**Grids.** The wexp grid: Lambert conformal conic on a sphere of radius 6,371,200 m,
tangent cone at 25 N (Latin1 = Latin2 = LaD = 25), LoV 265 E, first grid point 19.228976 N,
233.723448 E, Dx = Dy = 2539.703 m, Ni = 2345, Nj = 1597, scanning mode 64 (i then j, both
increasing), index `k = j * Ni + i`. Grid G184 (the precipitation only): the same
projection, first grid point 20.191999 N, 238.445999 E, Ni = 2145, Nj = 1377. It is the wexp
grid without its western expansion: the wexp cell `(i, j)` is the G184 cell `(i - 200, j)`,
the same point on the ground to within 8.3 m over the sixty-seven locations (the two first
points are published to a millionth of a degree). The job
verifies section 3 of every message it decodes against these definitions and refuses a
message on any other grid.

**Locations.** The sixty-seven settlement locations the owner approved on 2026-09-29: the
fifty most populous metropolitan areas of the lower 48 states and the District of Columbia in
the Census Bureau's Vintage 2024 estimates, then for each lower-48 state with no main city
among them its most populous metro whose main city lies in the state (New Jersey and Delaware
have none, by ruling). Each settles at one wexp cell: the cell whose 2.5 km square contains the
centre of the main city's City Hall building footprint (its `position`, OpenStreetMap), frozen
as approved. Rulings of 2026-09-29: Hampton Roads settles at Norfolk, South Carolina at
Charleston, West Virginia at Huntington, Louisville at the building the city names City Hall,
and Miami downtown at its administration building, whose own square counts as water, so the
nearest land cell settles (1.72 km from the position). The list's water rule was applied on
URMA's significant wave height analysis of 2026-09-27 12Z, which RTMA's files do not carry
(checked 2026-09-30); the approved cells are frozen, so the rule is not applied again. The list is vendored in
`geo/settlement_locations.csv` (columns id, name, state, metro, metro_rank, basis, position,
lat, lon, i, j, tz, note), with the approved cell given, never recomputed;
`scripts/build_analysis_grid.py` derives `config/analysis_locations.json` from it and refuses to
write a cell that is not the one containing the position unless the list records why (Miami).
Time zones are from the National Weather Service points service. Location ids are slugs of
name and state (`new-york-ny`, `st-louis-mo`, `portland-or`, `portland-me`).

**Day.** The local civil date at the location through its IANA zone: every top-of-hour
analysis whose local civil time falls on that date. That is 24 analyses on an ordinary day,
23 on the spring-forward day (02:00 local does not occur, and no analysis is counted twice
for it) and 25 on the fall-back day (01:00 local occurs twice, and both analyses belong to
the date). *Complete* means every one of them was read (23, 24 or 25), and the mean wind is
over the hours read. The day's **precipitation** is the one-hour accumulations that together
cover the date from midnight to midnight: the accumulations NOAA files under the hours after
each analysis, the same 23, 24 or 25, so the day's row for the analysis at HH:00 carries the
accumulation over the hour that starts then (owner's decision 2026-09-29; schema 1 summed the
accumulations filed under 00 to 23 local, which ran from 23:00 the day before). A local day
spans two UTC day directories. Arizona keeps standard time and has 24 hours every day.

**Daily variables, all from the day's hourly values.**

| variable | rule | shown as | resolves Yes when |
|---|---|---|---|
| high | max of the hourly temperatures | whole °F, half up | value ≥ strike |
| low | min of the hourly temperatures | whole °F, half up | value ≤ strike |
| gust | max of the hourly gusts | whole mph, half up | value ≥ strike |
| wind | mean of the hourly sustained winds | whole mph, half up | value ≥ strike |
| precip | sum of the hourly accumulations | inches to 0.01, half up | value ≥ strike |

Conversions: K to °F as (K − 273.15) × 9/5 + 32; m/s to mph by 2.2369362921; mm to inches by
1/25.4; all in decimal arithmetic on the stored values, which are the files' own numbers
(hundredths of a kelvin and of a metre per second), so a value the file holds exactly is
converted exactly: 250.65 K is −8.5 °F, which binary floating point makes −8.49999999999995.
Nothing is rounded before the day's aggregate (owner's decision 2026-09-29; schema 1 rounded
each hour to a tenth first, which moved the whole value on about 5% of days: New York's high
of 2026-09-26 was 290.09 K, 62.492 °F, published as 63). The day's precipitation total is the
stored millimetres added up and converted once (from 2026-11-03). Each hour's inches are a
division by 25.4 that decimal arithmetic stops at its 28th digit, and a sum of those can sit a
hair under a total the millimetres hold exactly. 15.875 mm is 0.625 in and resolves 0.63, and
three hours of 0.015625, 2.546875 and 13.3125 mm summed in inches came to 0.6249999...9 and
resolved 0.62; 2.54 mm is 0.1000 in, and three hours of it showed an exact figure of 0.0999.
Temperature, wind and gust convert exactly hour by hour, so their aggregates needed no change. Half up means half away from zero:
-20.5 °F is -21, -0.5 is -1, the way the published tables read (owner's decision 2026-09-27,
pinned by tests on -20.5 and -0.5). It is applied once, on the aggregate. The exact value
beside the rounded one is that aggregate CUT toward zero to a thousandth (precipitation to a
ten-thousandth of an inch), never rounded, so a mean wind of 16.4996 shows as 16.499 beside its
16 and never as a 16.500 that looks as if it rounds up; a temperature is exact at a thousandth,
since the file holds hundredths of a kelvin. A value equal
to the strike resolves Yes, at or above it for the high, gust, mean wind and precipitation and
at or below it for the low, so the boundary case is Yes whichever way the contract runs
(owner's decision 2026-09-28; until then the page resolved it No). The Yes and No are drawn from the published values rather than
stored, so the rule holds on every day the page shows, the backfill included. The exchange's
current daily temperature and MG wind contracts keep the strict test (greater than, less than,
equal resolving No) and are unchanged, so on the boundary this framework deliberately parts
from the board. Mean wind and precipitation have no live contract and take the same shape by
the owner's decision.

**Hourly values.** Each row of a day is the analysis at the top of that hour and the
precipitation over the hour that starts then. The archive keeps the files' own numbers
(kelvin, metres per second, millimetres); the place files show them converted and cut to a
thousandth (precipitation a ten-thousandth of an inch). A place's hourly precipitation value,
once read and stored, stands, and a later file only fills the hours and places that had no
value yet, which on RTMA, whose files are never rewritten, is only a place a partial read missed.
There is no analogue of the station report conventions (last report in the hour, specials,
tenths group); the page says this in its method note.

**Final and provisional.** RTMA resolves (owner's decision 2026-09-29;
`index.resolvesOn`). A place-day is *final* at its cut-off, 12:45 AM local time on the day after
(`day_cutoff`, the day's end plus 45 minutes; clocks never change in that span), on the hours
whose files were published by then (owner's ruling 2026-10-14, the draft terms): an analysis
counts when its file's `published` time is at or before the cut-off, and an accumulation when
its file's is and the file holds a value at the place. A document read before 2026-10-14
carries no `published` time and counts. Until the cut-off the day is *provisional* and its
values run over the hours read. At the cut-off `finalAt` and `resolvedAt` are the cut-off
itself, and a day short of hours says so (`hours < of`, "final, 23 of 24 hours" on the page). A
file published after the cut-off is shown in the place file, marked `late` (or `pcpLate`),
dimmed on the page, and never counted; one published before the cut-off and read after it
counts, so the summary comes out the same whenever it is rebuilt and needs no memory of an
earlier one. The first published version of a file counts: the analysis hour is written once,
and a precipitation read of the same server's file with another Last-Modified time adds nothing
and is recorded as `replaced`. A place with no analysis that counts at the cut-off has no value
and is marked for event review (`review`), and one with no accumulation that counts has
`precip.review`; a place with nothing read at all gets `review_entry` when other places of the
day have hours, and a day with nothing read anywhere is not written. There is no closed state
any more: `closed` is always false, and the page still reads it in files written before
2026-10-14. The state lists hours on neither server six hours after they were due (`missing`,
`pmissing`) for the lane's own bookkeeping; they no longer decide anything about a day. The
valid times of the analyses are the `at`, `t`, `valid` and `asof` stamps, and `written` is the
pass that wrote a file.

**Map lattice.** For the map, each hourly field is sampled onto a regular lattice in the
site's CONUS screen space (the 960 × 600 Albers viewBox of `pipeline/basemap.py`): pitch 3 px,
320 columns × 200 rows, lattice point `(c, r)` at screen `(1.5 + 3c, 1.5 + 3r)`. The screen
point is taken back through the inverse Albers to longitude and latitude and forward through
the Lambert projection to the nearest grid cell; `config/analysis_lattice.json` holds that
cell index per lattice point for the wexp grid and for G184, or -1 off the grid. This is a
subsample for display, about 14 km between points nationally; the page says so. Sampled
values are ints: temperature in tenths of °F, wind and gust in tenths of mph, precipitation in
hundredths of an inch, rounded half up (the product is rounded to six places first so a
decimal half that binary floating point holds a hair under still rounds up); `null` where the
lattice point is off the grid or the cell is missing in the bitmap.

**Cell geometry and windows (owner's request 2026-09-27, evening).** A reader must be able to
zoom to the very grid cell a place resolves on. For each location `scripts/build_analysis_grid.py`
records, for the wexp grid and for G184, the resolving cell's centre in screen coordinates
(`cell.wexpPx`, `cell.g184Px`, `[x, y]` in the 960 × 600 viewBox), its outline
(`cell.wexpBox`, `cell.g184Box`: the four corners at `i ± 0.5, j ± 0.5` taken through
`grib2.lcc_latlon` and the basemap transform, in order round the cell), and a local basis
(`cell.wexpBasis`, `cell.g184Basis`: `{"di": [dx, dy], "dj": [dx, dy]}`, the screen vectors of one
cell step in `i` and in `j` at that place, from the projected centres of the neighbouring cells),
so that the cell `(i + a, j + b)` is centred at `centre + a·di + b·dj` and drawn as the
parallelogram `± di/2 ± dj/2`. Over a window of ten cells either way the error of that linear
frame against the exact projection is about 0.01 viewBox units at worst (measured 0.0092 at the
outer corner of a corner cell, Seattle on G184), which is far below a screen pixel only up to
about 20× zoom and about one screen pixel at the 96× maximum; the build script refuses to write a
frame past 0.02. `px` and `py` (the position, the City Hall) are written to three decimals so
the dot the page draws at them sits inside the outlined resolving cell (to a tenth such a dot
crossed the outline), everywhere except Miami, whose position is outside its cell by ruling. Around each place every frame
also carries a **window**: the true 2.5 km values of the 21 × 21 cells centred on the resolving
cell (`WINDOW_HALF = 10`, about 52 km across) on the grid the variable uses, so the
zoomed map shows the analysis at its own resolution rather than the 14 km lattice.

## 2. Naming

- `analysis` is the lane and the job name in `pipeline/run.py`; its snapshots are under
  `snapshots/analysis2/` and its archive under `archive/analysis2/` (schema 2).
- The product is `rtma`, the key of every per-product field (the lane is written over a
  table of products and has had one since 2026-09-30). Variables are `high`, `low`, `gust`, `wind`, `precip`
  (daily) and `temp`, `wind`, `gust`, `precip` (hourly frames and hourly series).
- Hours are UTC ISO strings `YYYY-MM-DDTHH:00:00Z`; frame and archive file names use `YYYYMMDDTHHZ`.
- Days are local dates `YYYY-MM-DD` of the location; the days index is keyed by that date,
  which is a different UTC span per zone.

## 3. GRIB2 in the standard library (`pipeline/grib2.py`)

Pure parsing, no network. It decodes:

- section walking of one message (`GRIB` indicator, sections 1 to 8), with the grid
  definition (template 3.30: Ni, Nj, first point, LoV, Latin1, Latin2, LaD, Dx, Dy, scanning
  mode, earth shape), the product definition (parameter category and number, level type and
  value, and for template 4.8 the accumulation window), the data representation (template
  number, reference value R as IEEE float, binary scale E and decimal scale D as
  sign-magnitude 16-bit ints, bits per value) and the bitmap indicator;
- simple packing (template 5.0): the whole field, and random access to a list of cell
  indices without decoding the field (`decode_cells`; `random_access(msg)` says when that
  applies, which is 5.0 with no bitmap). A constant field (bits per value 0) decodes to R
  itself, eccodes' convention, on both paths;
- complex packing (template 5.2) and complex packing with spatial differencing (5.3), with
  a bitmap or with missing-value management 1 or 2 (a zero-width group whose reference is
  the primary or, under management 2, the secondary missing value is missing), returning
  `None` for missing cells. The RTMA precipitation files are 5.2 with a bitmap; 5.3 (order 1,
  management 1, no bitmap) was the live path for URMA's rerun precipitation files while the
  lane read them, and stays tested;
- the sidecar `.idx` format (`n:offset:date:VAR:level:step:` lines) into message byte
  ranges (`start`, `end` inclusive). The last message's `end` is unknown to the sidecar and
  is `None`; the job asks for a 16 MiB range from its start, and `gov_weather.fetch_range`
  accepts the shorter body S3 answers with a 206 whose `Content-Range` shows the request ran
  past the end of the object (any other short body is an error);
- the Lambert forward projection to fractional `(i, j)` for a latitude and longitude on a
  given grid definition, and the inverse (cell to latitude and longitude), on the sphere.

Memory: a field is 3.7 million points and the lane runs in a 512 MB Lambda. `decode` never
holds the packed integers and the floats of a whole field together (simple packing is read
and converted a slice at a time; complex packing converts its ints in place, in slices, and
undoes the spatial differencing in place), and the job samples a simple-packed field cell by
cell for the map frame (64,000 lattice cells plus the places) rather than decoding it
whole. Measured on 2026-09-27 on real files: one product-hour with its four frames peaks at
184 to 236 MB of resident memory under python 3.9.6 and 3.13.5, in 1 to 2 s; a whole
100 s pass of the local job (21 hours backfilled with frames, the days rebuilt) at 314 MB.

The decoders were checked against eccodes on the Mac: the whole-field decode of TMP, WIND,
GUST and both precipitation packings with zero difference at every point, an eccodes
re-encode of real fields as 5.2 and 5.3 (orders 1 and 2, with and without a bitmap, with
managed missing values) bit for bit, and `decode_cells` equal to the whole decode at every
sampled cell. Tests (`tests/test_grib2.py`) run offline on small checked-in fixtures under
`tests/fixtures/` and on messages the test file writes itself.

## 4. Files

All files carry `schema: "analysis/2"`, `asof` (the newest analysis valid time they
contain, or for `index.json` the newest hour actually read) and `written` (the pass time).
Cache-Control: frames and location-day files that are final with resolved precipitation
`public, max-age=300, stale-while-revalidate=1800, stale-if-error=86400`; everything
else `public, max-age=60, stale-if-error=86400` (`CACHE_LIVE`). The live files carried
`stale-while-revalidate=300` until 2026-11-03 and no longer do. The settlement display fetches
a live day's place file and day file again each minute with a stamp in the query string, and
the CDN's snapshot policy keeps the query string out of its cache key, so with it the first
fetch after the minute was answered from the stale copy while the edge refreshed behind it, up
to about six minutes old (a CloudFront hit of age 179 s on Boston's place file, seen on the live
page). Without it a fetch is at most the minute old, and `stale-if-error` still serves the last
copy when the origin errs. The same header goes on the other live files, `index.json`, the grid
index and the CSV files. The stack's cache policy is unchanged.

**`snapshots/analysis2/index.json`**

```
{ "schema": "analysis/2", "asof": ..., "written": ..., "resolvesOn": "rtma",
  "statement": "A proposed settlement framework. No contract settles on it.",
  "conventions": { "day": "...", "high": "...", ... , "cell": "...", "lattice": "...", "units": "..." },
  "sources": { "rtma": {"bucket": "noaa-rtma-pds", "lagMinutes": 47, "latest": "2026-09-27T17:00:00Z"} },
  "locations": [ {"id": "new-york-ny", "name": "New York", "state": "NY",
                  "metro": "New York-Newark-Jersey City, NY-NJ", "metroRank": 1, "basis": "top50",
                  "position": "New York City Hall, City Hall, New York, NY 10007 (...)",
                  "lat": 40.7128, "lon": -74.006, "tz": "America/New_York", "note": "",
                  "px": 852.0, "py": 208.6, "cell": {"wexp": [2010, 857, 2011675], "g184": [1810, 857, 1840075],
                  "centre": [40.7141, -74.0129], "distanceKm": 0.63, ...geometry...}} , ... 67 ],
  "days": ["2026-08-30", ..., "2026-09-29"],
  "dayStatus": { "2026-09-27": {"places": 67, "final": 67, "resolved": 67, "closed": 0, "review": 0}, ... },
  "lastResolvedDay": "2026-09-27",
  "lattice": {"pitch": 3, "cols": 320, "rows": 200, "viewBox": "0 0 960 600"},
  "variables": { "high": {"unit": "°F", "hourly": "temp", "rule": "..."}, ... },
  "backfill": {"pointsDays": 30, "frameDays": 7, "done": false, "cursor": {...}} }
```

`sources.rtma.latest` and `asof` are the newest hour actually read
(never an hour the walk passed without reading), so an outage shows as an ageing `latest`.
`days` lists the newest 60 day files; the day and place files themselves are kept
indefinitely (the archive can rebuild any of them).

**`snapshots/analysis2/days/YYYY-MM-DD.json`** — one entry per location for that local date.

```
{ "schema": "analysis/2", "day": "2026-09-26", "asof": ..., "written": ...,
  "locations": { "new-york-ny": {
      "rtma": { "hours": 24, "of": 24, "complete": true, "closed": false, "final": true,
                "finalAt": "2026-09-27T04:48:12Z",
                "high": {"value": 64, "exact": 64.148, "at": "2026-09-26T18:00:00Z"},
                "low":  {"value": 58, "exact": 57.902, "at": "..."},
                "gust": {"value": 46, "exact": 45.703, "at": "..."},
                "wind": {"value": 21, "exact": 20.833},
                "precip": {"value": 0.49, "exact": 0.4917, "hours": 24, "resolved": true,
                           "resolvedAt": "2026-09-27T04:28:45Z"} } } } }
```

`of` is the day's hour count in that zone (23, 24 or 25). `precip.hours` is the number of
hours with a precipitation value. An hour whose analysis file never
landed still adds its precipitation, a separate file: it counts in `precip.hours` and never in
`hours`, so it cannot make a day complete (RTMA 2026-09-23 19Z never reached NOAA Open Data,
and its precipitation file did). A day file written before 2026-09-30 carried a `urma`
entry beside `rtma` and a `revised` field in each precipitation total; the rewrite of that day
drops both. A partial day has `complete: false` and `hours < of`; a place in event review has
`review: true`, `hours: 0` and no values, and its precipitation `review: true`. A day file
written before 2026-10-14 can carry a closed incomplete day (`closed: true`, `final: false`). A
place with no hours yet is absent until its cut-off. `high`, `low`, `gust`, `wind` or
`precip` is `null` when no hour read has a value for it.

**`snapshots/analysis2/loc/<id>/YYYY-MM-DD.json`** — the location-day at the hourly scale.

```
{ "schema": "analysis/2", "id": "new-york-ny", "day": "2026-09-26", "tz": "America/New_York",
  "rowShape": 2, "asof": ..., "written": ...,
  "hours": [ {"local": "00", "t": "2026-09-26T04:00:00Z",
              "rtma": {"temp": 63.14, "wind": 18.223, "gust": 33.506, "precip": 0.0197,
                       "pub": "2026-09-26T04:48:31Z", "pcpPub": "2026-09-26T05:17:02Z",
                       "src": "nodd", "pcpSrc": "nodd",
                       "show": {"temp": 63, "wind": 18, "gust": 34, "precip": 0.02},
                       "run": {"hours": 1,
                               "high": {"value": 63, "exact": 63.14, "at": "2026-09-26T04:00:00Z"},
                               "low": {...}, "gust": {...}, "wind": {"value": 18, "exact": 18.223},
                               "precip": {"value": 0.02, "exact": 0.0197, "hours": 1}}}}, ... ],
  "summary": { ...the same object as this location's entry in days/YYYY-MM-DD.json... } }
```

`hours` has one row per analysis of the local date (23, 24 or 25 rows); on the fall-back day
the second 01:00 is labelled `01*`, and the UTC stamp `t` sits beside every label. A row's
`temp`, `wind` and `gust` are the analysis at `t` and its `precip` the accumulation over the
hour that starts at `t`, which NOAA files under `t` plus an hour, all converted from the
archive's own numbers and cut to a thousandth (precipitation a ten-thousandth of an inch). An
hour with nothing read is `rtma: null`, and an hour read whose precipitation file had no value
yet has `precip: null`.

From 2026-11-03 (`rowShape: 2`, the settlement display) every row with a value carries three
more things beside its exact values, all made by the lane so the page adds, averages and rounds
nothing, since rounded hours do not add up to the day (Boston on 2026-09-28 is 0.83 in from its
rounded hours against the 0.84 it resolved on).

- `show` is each hourly value as the contract shows it, rounded once, half away from zero,
  from its exact value, to the units of `HOUR_UNIT`: whole °F, whole mph, a hundredth of an
  inch. 280.65 K is 45.5 °F and shows 46, 250.65 K is −8.5 °F and shows −9, and 15.875 mm is
  0.625 in and shows 0.63.
- `run` is the day's values so far, `running(product_hours, tz, day, now, t)`, which is
  `day_summary` itself over the day's hours up to and including the row, so every running figure
  follows the rules that resolve the day (a file published after the cut-off never counts, a
  missing hour leaves the values on the hours there are, a precipitation-only hour adds rain and
  no analysis). `hours` is the analyses counted so far; `high`, `low` and `gust` carry `value`,
  `exact` and `at`, `wind` its `value` and `exact`, and `precip` its `value`, `exact` and
  `hours`, or `null` while nothing counts. The running values of the day's last counted hour are
  the day's summary by construction, and tests check it for every hour of every place-day built
  from the fixtures and from the samples. The flags that turn on the clock (final, review,
  resolved) stay in the summary.
- `pub`, `pcpPub`, `src` and `pcpSrc` say when and where each file was published (its
  Last-Modified time and `nodd` or `nomads`), the same figures as the raw CSV, present only
  when known, so a file read before 2026-10-14 has no publication times.

A file without `rowShape` was written before these fields. The page still draws it, as the file
stands, with no running figures and no replay; the lane reads nothing from place files, and the
`LAYOUT` change of section 5 rewrites the listed days in the new shape a few a pass.

**`snapshots/analysis2/csv/YYYY-MM-DD-raw.csv` and `-processed.csv`** — the day as CSV (owner's
decision 2026-09-29), rewritten with the day file. The raw file has one row per place and hour
in the files' own units: `date, location, local_hour, valid_utc, temp_k, wind_ms, gust_ms,
precip_mm, precip_file_utc, analysis_source, precip_source, analysis_published, precip_published`
(the files' Last-Modified times, empty for a file read before 2026-10-14), the precipitation being the hour that
starts at `valid_utc` and filed under `precip_file_utc`, nothing converted or rounded. The
processed file has one row per place: `date, location, name, state, final, final_at, hours, of,
high_f, high_exact, low_f, low_exact, gust_mph, gust_exact, wind_mph, wind_exact, precip_in,
precip_exact, precip_hours, precip_resolved, precip_resolved_at, review, precip_review`. Both carried a `product` column
until 2026-09-30. The page links both for the selected day.

**`snapshots/analysis2/grid/index.json`** — which frames exist.

```
{ "schema": "analysis/2", "asof": ..., "written": ...,
  "frames": { "rtma": { "temp": { "2026-09-27": ["00", "01", ...] }, "wind": {...}, "gust": {...}, "precip": {...} } } }
```

Frame days here are UTC days. The index is rewritten after every hour's frames (it is
2 KB), so a frame on the bucket is always listed, and again at the end of every pass. Frames
are kept for 30 days and pruned once a day. A product the lane no longer reads leaves the
index on the next pass and its frames are never pruned or deleted by the lane.

**`snapshots/analysis2/grid/<product>/<var>/YYYYMMDDTHHZ.json`** — one lattice frame.

```
{ "schema": "analysis/2", "product": "rtma", "var": "temp", "valid": "2026-09-27T16:00:00Z",
  "unit": "°F", "scale": 10, "cols": 320, "rows": 200, "pitch": 3,
  "values": [ 612, 615, null, ... 64000 ints, row-major from the top-left lattice point ] }
```

Each frame also carries `windows`, keyed by location id:
`{"grid": "wexp", "half": 10, "values": [441 ints]}`, row-major with `dj` from −10 to +10 and
`di` from −10 to +10 inside each row, on the same `scale`, `null` where the cell is off the
grid or missing in the bitmap. For precipitation the window grid is `g184` and every other
frame's is `wexp`. A window is 1 to 2 KB (three-digit tenths for temperature, fewer digits for
the rest); fifty such windows added about 50 to 90 KB to a frame on real files (47 KB for
precipitation and 91 KB for temperature), at a cost of about 0.1 s per product-hour, and the
sixty-seven add about a third more.

The row of an hour whose analysis file never landed has `temp`, `wind` and `gust` null and
its `precip` set.

`scale` is what the int is divided by (10 for temperature, wind and gust; 100 for
precipitation in inches). A precipitation frame is rewritten when a refetch of the hour's
file adds coverage within the frame window.

**Archive.** `archive/analysis2/hours/<product>/YYYYMMDDTHHZ.json.gz` holds the locations'
hourly temperature (kelvin), wind and gust (metres per second) for that product-hour, the
files' own numbers kept to four decimals, and `source` (`nodd` or `nomads`) (write once,
`put_if_absent`). `archive/analysis2/precip/<product>/YYYYMMDDTHHZ.json.gz` holds the
locations' accumulation for the hour ending then, in millimetres to a millionth, and may be
rewritten: `read` (a file was decoded), `complete` (every place has a value), `missing` (the
places whose cell was absent from the bitmap), `source` and `values`; a rewrite only ever adds
values, never changes one. Together they let any day file be rebuilt from the archive without
re-reading NOAA. `archive/analysis2/_meta/state.json` holds the cursors: per product `missing` and `pmissing` (the
hours whose analysis or precipitation file is known not published, pruned past 33 days), `newest` (the
newest hour read), `scanned` (how far the live walk has looked), `since` (when the lane first
looked), `gaps` (hours passed without a read, retried for 48 hours), `precipPending`
(archived hours whose precipitation file is still being refetched), `reads` and `lastRead`
(when a pass last read an hour); per product
the backfill `cursor`, `oldest`, `done` and queued catch-up spans; `layout`, the `LAYOUT` the
listed days were last all written in, and `relayout`, the listed days still to be rewritten
after it changed; the day statuses; the prune stamp; `precipSwept`, the time of the last
precipitation sweep (step 4); and `healthAt`, when a ten-minute pass last advanced the failure
streak (section 5). `archive/analysis2/_meta/lease.json` is the lease of section 5, the token
of the pass that holds it with the pass's kind (`baseline` or `window`), when it was taken,
when it expires and when it was released; it is under `data/archive/`, which the Lambda's role
can write and the CDN never serves. A state written before 2026-09-30 loses its `urma` lanes and its
re-read and revision records when it is loaded, and has no `layout`, which queues every listed
day for rewriting.
A precipitation key without an archive hour is the precipitation of an hour whose analysis
file never landed. `archive/_meta/health_analysis.json` is the lane's own failure
streak file, written through `archive.update_health` under the market lane's convention so
this lane's streaks never collide with another lane's. Pages never read `archive/`.

## 5. The job

The job has two passes (owner's ruling 2026-11-03). `analysis_pass(cfg, store) -> int` is the
ten-minute pass, registered as `JOBS["analysis"]` and run on its own EventBridge schedule
`cron(8/10 * * * ? *)` (between the observation and quote lanes). `window_pass(cfg, store) ->
int` is the windowed pass, registered as `JOBS["analysis-window"]` and run by a second schedule,
`cron(14,16,20,44,46,50,52,54 * * * ? *)`, which is every two minutes inside :44 to :54 and :14
to :20 (`WINDOWS`), about when NOAA publishes the analysis and an hour's precipitation (section
1), at the minutes the ten-minute pass does not take. :48 and :18 are the ten-minute pass's own,
and there it does the windowed pass's work first. A test reads both schedules from
`ops/aws/template.yaml` and checks that no minute starts two passes. Minutes past the hour are
the same in UTC and in every zone the lane covers.

Each pass runs under `arch.Deadline(pass_seconds(now, budget))`, which is `PASS_CAP_SECONDS =
330` (or the invocation's budget when that is less), never later than `WINDOW_YIELD_SECONDS = 10`
before the next minute in a window, and never under `PASS_FLOOR_SECONDS = 75`. The ten-minute
pass outside the windows keeps its 330 s. At :48 and :18 it has until ten seconds before the
windowed pass two minutes on, room for the windowed work and a little backfill, so the sweep and
the refresh, which want 165 s, wait for the next ten-minute pass outside the windows. With
`FETCH_TRIES = 2` and `FETCH_TIMEOUT = 20` (about 212 s for the worst hour) a pass ends inside
the ten minutes. Each pass keeps `RESERVE_SECONDS = 45` back from the lanes for the rebuild and
the indexes, and sets `arch.LAST_STATUS` with its `mode` (`baseline` or `window`). Every NOAA
call is `fetch_range` or `fetch_bytes` in `pipeline/gov_weather.py` with the site's User-Agent,
two tries of twenty seconds each, so one hour's five fetches cannot hold a pass for more than a
few minutes and the 900 s Lambda timeout is never what stops it.

**The lease.** No two passes ever overlap. Before it reads the state, a pass takes the lease,
`archive/analysis2/_meta/lease.json` (section 4), with conditional writes on the store
(`storage.Lease`). It creates the lease with `If-None-Match: *`, takes over one that has expired
or been released with `If-Match` on the ETag it read, and releases its own by the same rule, so
of two passes racing for it exactly one holds it. On S3 the headers are added to PutObject
through the boto3 client's `before-call` event hook rather than as parameters, since the Lambda
runtime's botocore is not pinned and one older than the parameters would refuse them; S3
enforces them on the server. The local backend does the same with an exclusive create (a hard
link of the written file) and an atomic rename, under a guard file held for the few milliseconds
of each conditional write. A pass that finds the lease held writes nothing, prints the status
`"lease": "held"` with the holder's kind and times, and returns 0, so it is not a failure and
raises no alarm. The lease lasts past the longest its holder can run, the pass's budget (Lambda's
remaining time less the 30 s `handler.py` holds back) plus `LEASE_SPARE_SECONDS = 90`, or 900 s
plus that for a run with no budget, and the holder never renews it. A pass killed before it lets
go (out of memory, say) holds the lane until the lease expires, on Lambda up to about sixteen
minutes after the pass began (a budget of about 870 s, plus the 90 s).
A pass releases the lease after its last write. A release that finds the lease taken over is
recorded as a failure, since it would mean a pass outran its lease.

**The windowed pass** does only light work. It reads the newest precipitation files (step 1),
then the analysis hours after the walk's cursor and the gap of the hour now due (step 2),
rebuilds the days they touch (step 3), and writes the state when it moved and `index.json` when
a file came in. It leaves the pending precipitation, the older gaps, the days past their cut-off,
the sweep, the refresh, the backfill, the prune and the health file to the ten-minute pass. When
nothing is new it writes nothing but the lease and exits within seconds, its requests being the
lease, the state, the grid index, the newest hours' precipitation keys and a fetch or two of NOAA
files not yet published.

1. **Precipitation.** First the newest files, on both passes (`precip_lane`). An hour's
   precipitation file is read as soon as NOAA publishes it, without waiting for the next
   analysis, which it was read with until 2026-11-03, about half an hour after it landed. A
   file is looked for from 14 minutes after its hour ends (`LOOK_MINUTES`, its window's
   opening), through the `PCP_LIVE_HOURS = 3` newest files now due, oldest first. A file already
   stored is left alone, and one NOAA Open Data lacks 27 minutes after its hour ends is looked
   for on NOMADS as before. A read writes the precipitation key, its frame and `grid/index.json`.
   What counts is unchanged, and the day takes the file by its publication time as before; the
   day's last accumulation, on the bucket about 17 minutes after local midnight, is now in the
   day's total before the 12:45 AM cut-off. Then, on the ten-minute pass only, refetch the
   precipitation file of every archived hour in `precipPending`, an hour whose file was absent,
   refused, or short of a place's cell at the last read. A read that adds values rewrites the
   precipitation key and (within the frame window) the precipitation frame; an hour whose file
   is complete leaves the list, without a fetch when the newest files' read has stored it
   whole since; an hour past `precip_due`, six hours after its hour ends, is dropped
   with whatever coverage it has, a place still without a value being not published. The lane never waits on a precipitation file before
   archiving an analysis hour.
2. **Live hours.** For each product, the hours to try are the `gaps` first, then the hours
   after the walk's cursor (the later of `newest` and `scanned`) up to the hour now due
   (`due_latest`). An analysis is looked for from 44 minutes after its hour, its window's
   opening, two minutes before NOAA's earliest publication; at the ten-minute pass's own
   minutes that is the hour the product's 47-minute lag gives, as before. The windowed pass
   retries only the gap of the hour now due and leaves the older gaps to the ten-minute pass. A
   first pass starts at the present. A cursor more than 24 hours behind jumps to the present and
   queues the skipped span for the backfill (once, and no older than the backfill reaches). For
   each hour, up to three reads per product per pass: fetch the `.idx`, range-fetch TMP, WIND
   and GUST, sample each field at the places' cells and onto the lattice, then fetch the
   precipitation file, unless step 1 has stored it whole already, and do the same. Write the archive hour (once), the precipitation key, the four
   frames and `grid/index.json`; queue the hour in `precipPending` when its precipitation
   is short and its wait has not run out. A file NOAA Open Data does not have is looked for
   on NOMADS from `settled_after` (the product's lag plus `NOMADS_AFTER_MINUTES = 30`) until
   `NOMADS_KEEP_HOURS` (thirteen days, a day short of what NOMADS keeps), and the archive
   document records `source: "nomads"`; a file on neither six hours after its hour
   (`absence_final`) goes on the state's `missing` (or `pmissing`) list. The days an hour belongs
   to resolve at their cut-off whatever the lane has found by then, and `days_past_cutoff` rebuilds
   a recent place-day whose cut-off has passed while its summary is still provisional or absent. A `.idx` that is on neither (403 or 404) is an
   *absence*: the hour becomes a gap, `scanned` moves past it, and the walk continues. The
   absent hour's precipitation file is read all the same, on every retry of the gap until
   every place has a value, and stored without an archive hour. After one refused
   precipitation file of an absent hour, the lane reads no more absent hours' precipitation
   that pass; the gap's retries or the sweep of step 4 read it later. A
   read that fails (a short body, a message the decoder refuses, a 5xx after the retries)
   is an *error* recorded against the product in `errors`; the hour becomes a gap,
   `scanned` moves past it, and the product's walk stops for this pass so an outage costs
   at most one failing read per product per pass. An hour already archived (a crash between
   the archive write and the state write, or the backfill) counts as read without a fetch,
   unless it is within the frame window and its frames are not in the grid index, in which
   case it is read again for its frames. `newest` only ever moves on a read. The backfill's
   cursor is seeded by the first hour the walk looks at, read or not (on 2026-09-27 a fresh
   lane first looked while the newest hours were half an hour late, and a cursor
   seeded only by a read would have left the backfill idle until one landed).
3. **Days.** Rebuild every `days/` and `loc/` file that a new hour or a filled
   precipitation key touches (a UTC hour lands in one local day per zone), and on the
   ten-minute pass any day past its cut-off, from the archive hours, applying section 1's
   rules. A row reads the files of any hour that has begun (its analysis) or ended (its
   accumulation). Until 2026-11-03 it read only the hours the product's lag said had landed,
   which hid an hour's precipitation until the next analysis was due. The state is saved.
4. **Sweep.** Once a day (`PRECIP_SWEEP_HOURS = 24`), over the hours both walks have passed
   (from the backfill's `oldest`, at most 30 days back, to 48 hours before the live cursor,
   outside queued catch-up spans): read the analysis of every hour that has no archive hour
   while NOMADS may still keep it, and the precipitation of every hour that has no
   precipitation key, and rebuild the days they touch. A refused file ends that product's
   sweep until the next day's; a sweep cut short by the deadline is not stamped and is due
   again next pass.
5. **Refresh.** Rewrite, newest first and `REFRESH_DAYS_PER_PASS = 6` a pass, the listed days
   that lack either CSV file and the days still in `relayout`. When `LAYOUT` changes (it was
   set to `rtma-only-2026-09-30` when URMA was dropped, `cutoff-2026-10-14` with the cut-off
   rule, and `running-2026-11-03` when the place files gained `show`, `run` and the publication
   times) the first pass queues every listed day,
   so the listed days (sixty at most) are rewritten within about two hours; a day with no archive hours
   writes nothing and leaves the queue. The running values cost about 0.3 s of computing a day of
   sixty-seven places on a laptop (0.41 s against 0.10 s without them), inside the same deadline
   checks as every other rebuild.
6. **Backfill.** With the remaining budget, walk back, newest first: a queued catch-up span first, then from the cursor back to 30 days ago;
   frames for the last seven days, point reads only before that. The cursor advances only
   after a product-hour is written or known absent or unreadable (an unreadable hour is
   skipped and recorded, like a missing one). An absent hour's precipitation is read and
   stored as in step 2, with the same limit of one refused file per product per pass. The
   state is saved every twenty hours read.
7. **Prune**, once a day: frames older than 30 days and their index entries, and the state's
   lists of hours not published older than 33 days.
8. **Always**, in a `finally`. On the ten-minute pass `grid/index.json`, the health file, the
   state and `index.json` last, so a reader sees a consistent set whatever happened above (the
   health file before the state, which keeps when it was written). On the windowed pass the
   state when it moved and `index.json` when a file came in; every frame it wrote was followed
   by its grid index. Steps 4 to 7 are the ten-minute pass's.

Nothing a NOAA object does fails the pass. Every fetch and decode of an hour is guarded
inside `read_hour` and `read_precip`; every lane call is guarded in the pass; the pass-level
`except` is the last resort and still reaches the `finally`. A pass returns 1 only when a
read failed and nothing at all was read, a precipitation file included. The health file keeps a failure streak per product
through `archive.update_health`: a pass with a read of that product resets it; a pass counts
against it when a read of that product failed or when the product is *overdue*, which is the
newest hour read (or, before any read, the hour the lane first looked for) being
`OVERDUE_HOURS = 2` hours or more behind what should have landed; any other empty pass is
the normal wait between landings and counts neither way. Six such passes in a row
(`FAIL_STREAK_ALARM`) raise the lane's alarm the same way the other lanes do, so an outage on
NOAA's side is an alarm after about three hours and a corrupt file among readable hours is
not. Only the ten-minute pass advances the streak, so it still counts passes at the cadence
the alarm was set for. The windowed passes now read most hours between two ten-minute passes
and never touch it, so an hour any pass read since the last ten-minute pass (`lastRead` after
`healthAt` in the state) is a read there, and a read that fails in a windowed pass counts once
the ten-minute pass meets the same file with nothing read since. The health file keeps URMA's last streak from before 2026-09-30; a product the lane no
longer reads raises no alarm. `LAST_STATUS` carries the pass's `mode` and `lease` (`released`,
`held`, or `unknown` when the store could not be read), the counts and the lists (reads,
precipitation reads, absences, waiting precipitation, errors), and `newest` per product.

Write budget per pass (measured with fifty places, when the lane read two products): a pass
where one hour of each product landed wrote about 70 objects; an empty pass five; a
precipitation refetch that adds coverage one key, one frame, one day file and one place file
per place. That was about 110k PUTs a month with both products, and the 30-day backfill a
one-time 15k (about a third more with sixty-seven places); RTMA alone is a little over half.
From 2026-11-03, counted from the code rather than measured, an hour's precipitation is read
and its days rebuilt on their own, one more rebuild an hour (a day file, two CSV files and
sixty-seven place files, twice that across a local midnight), and each of the fourteen passes an
hour writes the lease about three times (a create the store refuses, a takeover, a release) and
reads it once, about 30k lease writes and 10k reads a month. A windowed pass with nothing new
writes only the lease. Schema 2's first 30 days were filled on 2026-09-29 by a local run of
the lane's own `read_hour`, `write_hour` and `rebuild_days` with the reads in parallel, then
uploaded, so the live lane started with its backfill done.

## 6. The page

`analysis-resolution.html`, unlisted (`noindex`, not in `config/contracts.json`, not in
`REF`), built from the same skeleton as `wind-markets.html`: title, `p.sub` carrying the
statement, a `.bar` with the controls and `#pageStatus`, the location panel, `p#anaPick` (a line
asking for a place, shown while none is picked), the map card, the method prose, `p.cap#foot`.
Scripts: common, data, `js/analysis-settle.js` (the settlement display, `WXSettle`), then
`js/analysis.js`; `WXAnalysis.init()`. The panel sits above the map since the settlement display
went in (owner's approval of 2026-11-03), so a picked place opens its day first.

**Controls.** The top bar holds a day button, a place button, **Zoom to the cell** and the day's
CSV links. They replaced the five variable buttons and the day and place selects on 2026-11-03, at
the owner's request, since the settlement display shows every variable at once; the variable is
switched on the map (below). The day button (`#anaDayBtn`) shows the day, labelled with the
weekday and with its state when it is not final, and opens a month calendar (`#anaCal`, a
non-modal dialog holding a `role="grid"` table). Only the days in `index.days` can be picked, and
every other day is greyed, `aria-disabled` and out of the tab order. Each listed day carries a mark
for its state from `index.dayStatus`, by the rule the day select used: final once every place has
resolved (a solid dot), provisional (a ring), event review when its unresolved places are all in
event review (a diamond), or closed on a file written before 2026-10-14 (a square). An index without
the field gives no mark. The month arrows, and Page Up and Page Down, step only among the months
that hold listed days. The arrow keys move between listed days, a week at a time up and down, Home
and End go to the first and last listed day of the month on view, Enter picks, and Escape closes the
calendar and returns the focus to the button, as a pick does. A click outside it or the focus
moving out of it closes it too. The place button (`#anaLocBtn`) shows the open place's name and
opens a small map of the sixty-seven places (`#anaPlaces`), drawn on the page's own projection and
`basemap.json` outlines, with a box to type a name. A click on a dot, or within 12 screen pixels of
one, opens that place. The box is a combobox over an alphabetical listbox of the places: typing
narrows the list and dims the other dots (a name that starts with the text first, then a word in it
that does, then any name or state that holds it), the arrows move through the matches, Enter opens
the highlighted match, which is the first until the arrows move on, and Escape closes the popover
and returns the focus to the button. The small map is hidden from assistive technology, since the
box reaches every place. Both popovers open under their buttons and move left as far as they must to
stay inside the page, so a 390 px phone never scrolls sideways, and only one is open at a time. The
main map's dots still open a place, and Zoom to the cell sits beside the place button. An hour
stepper sits under the map (◀ ▶ Play, the valid hour in UTC and in the selected location's zone when
one is picked). State lives in the URL: `?var=high&day=2026-09-26&hour=16&loc=new-york-ny`
(an address from before 2026-09-30 with `product=` opens on RTMA and loses the parameter).
With no `day` in the address the page opens on `index.lastResolvedDay` (owner's request
2026-09-28), the newest local date on which every one of the sixty-seven places has its RTMA
day final and its precipitation total resolved (`dayStatus.resolved == dayStatus.places`); an
index without the field opens on the newest day. The job keeps each day's status in its state
as it rebuilds the day and fills a status it lacks from the day's file. An RTMA day resolves
within about an hour of its local end, so the page typically opens on yesterday.

**Map.** The 960 × 600 SVG with `assets/basemap.json` state paths, the hour's frame drawn
onto a 320 × 200 canvas with the variable's fixed ramp and placed as an `<image>` filling the
viewBox with `image-rendering: pixelated`, clipped to the state outlines; the sixty-seven
location dots sized and coloured by that day's value of the selected variable, solid once it
has resolved, dashed at fill-opacity .55 while provisional, hollow when the day has no value
yet; hover
reads the cell under the pointer from the frame and the dot's daily value; click opens the
location panel. Zoom and pan as in `varmap.js`. Ramps are pinned: temperature −20 to 110 °F
on the site's nine-stop ramp, gust 0 to 60 mph, wind 0 to 30 mph, precipitation 0 to 2 in
with zero transparent. A legend under the map states the ramp and the subsample. The map's own
bar carries the variable switch beside the zoom buttons (`#anaVar`, since 2026-11-03), a radio group
of High, Low, Gust, Wind and Precip with one tab stop, whose arrow keys, Home and End move the
choice. It sets the variable, which is what the map shades, the hypothetical ladder under the
settlement display, the highlighted card and `var=`. A click on a card in the settlement display sets
it as well, so the switch, the cards and `var=` stay in step.

**Cell mode.** Once the zoom makes a resolving cell at least 12 screen pixels wide, the map
changes what it draws: the lattice raster is hidden (a 3 px lattice block would be a flat
patch far larger than a cell, and the caption says the field is hidden at this zoom), every
place in view draws its 21 × 21 window as parallelograms from its basis, coloured on the same
ramp, and its resolving cell with a dark border (`--ink`, 2 px, `vector-effect:
non-scaling-stroke`); the position (the City Hall) is a small dot; the place's value leaves the dot and
floats in a label outside the cell at a fixed screen offset (up and to the right, flipped when it
would leave the map), on a `--panel` background, with a leader line from the label to the centre
of the cell. The label, the cell and the window cells all open the panel on click and answer
hover with the cell's value and its `i, j`. A pan takes the pointer only once it has moved
more than 4 screen pixels, so a plain click while zoomed reaches whatever is under it (taking
pointer capture on pointerdown sent every click to the svg and nothing opened while zoomed; the
wind and hourly maps share the fix in `site/js/varmap.js`). A window cell whose value is zero or
`null` is drawn clear (no fill, no stroke) so hover still answers, with the value for a dry cell,
"off the grid" when the cell index leaves the grid and "missing in this hour" for a bitmap gap,
and the precipitation caption says dry cells are left clear. Keyboard focus on a mark outlines
the resolving cell in the accent at a fixed 1.5 px and leaves the position dot alone. A place
whose index entry has no cell geometry keeps its plain dot and value in cell mode, with Zoom to
the cell disabled. Below that zoom the dots draw as before, with the value text in `--ink` (it
was `--muted`, faint on the dark theme). The zoom range is raised so
the cell reaches about 40 px (`MAXZ = 96`), and a **Zoom to the cell** button in the panel and
beside the place button sets the view so the picked cell is about 30 px wide and centred; the
URL carries the view when zoomed (`?z=<factor>&cx=<x>&cy=<y>` in viewBox units) so a zoomed cell
can be linked.

**Location panel** (`#locPanel`, shown on pick or `?loc=`, above the map): the name, state,
the day and zone, Zoom to the cell, previous and next day buttons and Close; the statement; the
settlement display below; the hourly chart for the selected variable on a hypothetical ladder,
a 960-wide `svg.ts` in a `.card` in the style of `WXK.plot`: local hours on the x axis, midnight
and the day end marked, the RTMA hourly series solid in `--ana-rtma` (for precipitation the
lane's running total at each hour, exact, so the last counted hour sits on the day's total, and
no line on a file without `rowShape`), the day's value as the file stands as a horizontal line,
and a ladder column of hypothetical rungs at whole units around the value (precip at 0.01,
0.05, 0.10, 0.25, 0.50, 1.00, 2.00): Yes in `--yes`, No in `--no`, hatched with `#wxHatch`,
labelled "Yes" and "No", never cents, never linked; the cell note (cell centre, distance from
the position and its building, the grid cell on both grids, the ruling when present). Two tokens
are added to all three theme blocks of `site.css`: `--ana-rtma` and, for the rain bars,
`--ana-rain-soft`.

**Settlement display** (`#anaSettle`, `js/analysis-settle.js`; the prototype the owner approved
on 2026-11-03, built without its design notes and its sample picker, on the page's own place and
day selection). It draws only the lane's figures and computes none. The page never adds,
averages or rounds a value, and its only sums are of times and pixels. In order:

- the day's state, one pill and one line: Provisional with the analyses in so far and the
  cut-off, Final with the hours it resolved on, Event review when no hour was published by the
  cut-off, and Closed on a file written before 2026-10-14;
- the clock: the time the day is shown as of (local and UTC), the replay slider, Play the day
  (a minute every 30 ms) and Latest, the Show exact values toggle, and a note when arrival times
  are estimates;
- two countdowns, to the next analysis and to the next hour's precipitation, each in one of
  four states: counting down, due now, late, and missed once the cut-off has passed. They run
  to NOAA's own publication times over the two weeks to 2026-11-03, the analysis a median 49
  minutes after its hour and usually by 52, the precipitation a median 17 minutes after the hour
  ends and usually by 18 (`LAG` in the script). Late means the file is not in yet past the time
  it is usually out by, and its note gives that time and says the file counts if published by
  the cut-off. Until 2026-11-03 the lane read an hour's precipitation file with the next
  analysis, so that countdown read late for about half an hour each hour and its note said so;
  the lane now reads each file within about two minutes of its publication inside the arrival
  windows, the precipitation as soon as it lands (section 5), and the note says nothing of the
  next analysis;
- five value cards, High, Low, Peak gust, Mean wind and Precipitation, each the lane's running
  value at the last counted hour in (`run`), with the hour of the extreme or the hours counted,
  "so far" until the day is final, and the exact figure under the toggle; a place in event
  review shows no value and says no hour was published by the 12:45 AM cut-off. The card of the
  variable the map shows is highlighted, and a click on a card sets that variable;
- three charts on one time axis, local midnight to midnight: temperature with the High and Low
  marked, sustained wind and gust with the running mean as a dashed line and the peak gust
  marked, and hourly precipitation bars with the running total as a step line. Each shades the
  hours still to come, draws a now line at the clock and a dashed line at the next hour due, and
  answers hover with the hour's values, its exact values under the toggle and when its file was
  published, and where;
- every hour in a table, the shown or exact values, the running precipitation total and the
  analysis's publication time (≈ where estimated), with a footnote that the rounded hours can
  add up to a different figure than the total, which is the column the contract resolves on.

The replay runs from the day's local midnight to the clock on a live day, and to a quarter hour
past the cut-off on a day past it. An hour comes in at its file's publication time (`pub`,
`pcpPub`), or before 14 October, when none was kept, at the usual lag, marked ≈. A counted hour
enters the day's values once every counted hour before it has arrived, since its running values
cover them. A file published after the cut-off is shown dimmed once the cut-off has passed and
never counted. While the day is live and the tab is visible, the display follows the clock,
and each minute from :44 to :54 and from :14 to :20 past the hour, about when the analysis and
the precipitation land, it fetches the place file and the day file again with `?b=` and a
stamp that changes once a minute (`WXD.get`'s `bust`), so the day fills in without a reload.
It does the same each minute while a countdown reads late, for up to half an hour past the
file's usual time (`CHASE`), so a file the lane reads late, on its ten-minute pass outside the
windows, reaches the page a minute or two after the read and late ends when the file is in;
past that half hour the windows fetch the day twice an hour. The site's CloudFront snapshot
policy keeps query strings out of its cache key (`ops/aws/template.yaml`), so the stamp
reaches past the browser's cache but not the CDN's, and a refetch can still be up to the CDN's
60-second lifetime old. Since 2026-11-03 it is no older than that, because the live files
carry no `stale-while-revalidate` (section 4); with it the first fetch after the minute could
be about six minutes old.

A link such as `analysis-resolution.html?var=precip&day=2026-09-28&loc=boston-ma` opens that
place-day and, since it names a variable and a place, scrolls to that variable's chart
(temperature for the high and low, wind for the gust and mean wind). A file without `rowShape`
is drawn as it stands: the summary's values, the hours as stored, no running total, no replay,
and a line saying so.

**Method note.** The conventions block of `index.json` rendered as a definition list, the
sources with their measured lags, the subsample statement, and the statement that station
report conventions have no analogue here.

## 7. Verification

- `tests/test_grib2.py`: section walking, simple and complex decoding against fixture
  values, random-access equals whole-field, `.idx` parsing, Lambert forward and inverse round
  trip, refusal of a message on another grid.
- `tests/test_analysis.py`: with a recording `LocalStorage` and a monkeypatched fetch and
  decoder, a product-hour is written to the archive, its precipitation key and the frames
  with the grid index; day rules (half-up rounding away from zero, the at-least strike test,
  partial days, the local day spanning two UTC directories, the 23- and 25-hour daylight-time days,
  Arizona unchanged); `index.json` written last; a missing object is an absence; a raising
  fetch or decode is the hour's failure, the pass still writes the state, the grid index and
  the index, and a persisting one reaches the alarm streak; a product the lane no longer
  reads raises no alarm; the alarm fires after the outage passes and `asof` stays on the hour
  read; frames are indexed the pass they are written; a partial bitmap is retried and filled;
  days written before URMA was dropped are rewritten a few a pass and the grid index drops
  URMA's frames without deleting them; the catch-up span is queued once and clamped; the backfill starts behind
  a late newest hour; the place file carries the precipitation in the row of the hour it
  starts; the day's precipitation runs midnight to midnight; a value the file holds exactly is
  converted exactly and the aggregate rounded once; a complete day is final at its cut-off,
  12:45 AM local time the next day, and not before, stamped with the cut-off; a short day is
  final there on the hours available and never closed; a file published after the cut-off never
  counts and is shown as late, one published before it and read after it counts, and the day's
  last accumulation landing after the cut-off leaves the total; a place with nothing published by
  the cut-off goes to event review, through a pass, on a day other places have hours for, and a
  day with nothing read anywhere is not written; a replaced precipitation file keeps its first
  version; each read carries its file's Last-Modified time, which `fetch_bytes` and `fetch_range`
  report; a file missing from NOAA Open Data is read from NOMADS half an hour after it is due (a
  precipitation file 27 minutes after its hour ends) and not past thirteen days, and the sweep
  reads a missing analysis from there; the day is written as raw and processed CSV; an
  hour whose analysis never lands still adds its precipitation, in the live lane, in the
  backfill and through the daily sweep, without counting toward the day's analyses, and a
  refused file ends a product's sweep for the day;
  `fetch_range` accepts the clamped 206 for a last message.
- `tests/test_analysis.py`, for the settlement display: the three half ties (280.65 K shows 46,
  250.65 K shows −9, 15.875 mm shows 0.63) by the rule and through the place file; a split
  precipitation total is summed in millimetres and converted once; the running values are the
  day's own resolution of the hours so far, a missing hour and a late file included; for every
  hour of every place-day built from the fixtures (three places, a missing analysis with its
  rain, a file late for one zone and on time for another, the fall-back day) and of every sample
  place-day, the running values equal a fresh resolution of the raw CSV's hours up to it, the
  shown values are the hour's exact values rounded once, and the last counted hour carries the
  day's summary; place files without the new fields stay readable and are rewritten newest
  first, a few a pass, inside the deadline; and Boston's 2026-09-28 resolves on 0.84 while its
  rounded hours add to 0.83.
- `tests/test_analysis.py`, for the windowed passes (2026-11-03): an hour's precipitation is read
  as soon as it lands, before its analysis is due, and is in the place file and the day at once,
  and the analysis read later does not fetch it again; New York's last accumulation of a day is
  in before the 12:45 AM cut-off and the day resolves on it by the same rules; a windowed pass
  with nothing new writes nothing and fetches only the files newly due; the analysis is
  looked for from :44; a windowed pass leaves the older gaps, the pending precipitation, the
  backfill, the sweep, the refresh and the prune to the ten-minute pass, which does them; a pass
  that finds the lease held writes and fetches nothing and is not a failure, a pass holds the
  lease while it reads the state and for its budget plus 90 s, and one left by a pass that died
  is taken over once it expires; a windowed pass's read counts for the next ten-minute pass's
  streak and its failed read does not; a precipitation read is a read for the exit code; the
  live files carry no `stale-while-revalidate` and keep `stale-if-error`, while a final place-day
  keeps `CACHE_FINAL`; every pass ends ten seconds before the next minute in a window; and the
  two schedules in the template take each minute in a window once, the ten-minute pass keeping
  :48 and :18.
- `tests/test_lease.py`: the conditional writes and the lease on the local backend, on a stand-in
  for boto3's client that answers the conditional headers as S3 does, and, where botocore is
  installed, on a real boto3 client whose requests are answered in the process: create, held,
  an expired lease taken over and its old holder's release refused, release, two passes taking
  over one expired lease with one winning, a create whose answer was lost, an unreadable lease,
  the headers on each request with no hook outliving its call, a 409 counted as a lost race,
  and the local guard (never listed, a live one makes a write give up, a dead one is broken).
- `scripts/verify.py`: the page in the `pages` sweep; checks that the map's variable switch changes
  the legend and the frame, the page offers no product choice and never says URMA (an old
  `product=urma` address opens on RTMA), the day calendar and hour stepper change the caption,
  a dot click opens the panel with the hour rows, five cards and one hourly line, the
  hypothetical rungs are hatched and carry no `data-contract-url`, the status line says
  provisional with the count when `rtma` is incomplete, the chart rule says resolved on a final
  day and running on a provisional one, the exact figures are cut to a thousandth, `?loc=`
  opens the panel on load, and the 503 degradation shows "No data", and the day's CSV links
  fetch the raw and processed files. For the settlement display, with the clock fixed where time
  matters: the five cards carry the summary's values on a final day; the three charts share one
  axis and one now line, with the hours to come shaded; the countdowns pass through counting
  down, due now, late and missed; the hour table has a row per hour, its running total ends on
  the day's and the toggle shows exact figures; the replay moves the cards to the lane's running
  values for that minute and Latest brings the final day back; Play the day runs; `var=` opens
  its chart; a refetch inside an arrival window carries `?b=` and fills the day in; a late
  accumulation's note gives NOAA's usual time and the cut-off and nothing of the next analysis;
  outside the windows a late countdown keeps the place file fetched each minute and the day
  fills in, and past half an hour late nothing is fetched; and a place
  file without the new fields still draws, with no running figures and no replay.
  Fixtures under `samples/snapshots/analysis2/` come from a real local run of the job, rebuilt
  on 2026-11-03 by the lane's own code (their README says how).
- Cell mode: at the national extent no cell outline is drawn; after Zoom to the cell on a
  fixture place the resolving cell's outline exists with the `--ink` stroke, the label carries the
  dot's value and a leader line, the raster image is hidden and the caption says so, the window
  draws 441 cells for the picked place, hover on a window cell reports its `i, j` and value, and
  the URL carries `z`, `cx`, `cy`; a fixture without `windows` still draws the outline and the
  label with no window cells; while zoomed a click on a window cell and on the label opens the
  panel and a drag ending over a window cell opens nothing; a dry precipitation window draws its
  cells clear and hover on one reads 0.00 in; a place stripped of its cell keys keeps its plain
  dot; on the wind map a dot clicked while zoomed still scrolls to its section.
- `scripts/scrub.py` before push; the page's `<meta name="description">` under 200 characters.
