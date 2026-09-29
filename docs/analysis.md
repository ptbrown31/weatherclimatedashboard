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
included, and URMA is shown for comparison**; the sixty-seven settlement locations approved
that day replace the fifty population centres; every value is converted exactly and the
day's aggregate rounded once; the day's precipitation runs from midnight to midnight; a file
NOAA Open Data lacks is read from NCEP's NOMADS server. Schema 1's files under
`snapshots/analysis/` and `archive/analysis/` are left as they were and are no longer
written; the page reads schema 2 only. The owner's draft terms for contracts on this
framework are the RTMA Daily Weather Contract Terms of 2026-09-29.

Everything here is computed by the site's own pipeline in the standard library. Nothing
reaches the page except the JSON files of section 4. Sections 1, 3, 4 and 5 describe what
is built, checked against the code on 2026-09-27; where the first draft of this contract
and the code disagreed, the text was corrected to the code except for the defects the
review found, which the code was corrected for.

## 1. Conventions, fixed once

**Sources.** The Real-Time Mesoscale Analysis (RTMA) and the Unrestricted Mesoscale
Analysis (URMA), NCEP's hourly 2.5 km analyses of the CONUS NDFD grid, read from NOAA
Open Data on AWS (`noaa-rtma-pds`, `noaa-urma-pds`, no credentials, HTTP/1.1 range
requests honoured). URMA is the same analysis run again about seven hours later with the
late-arriving observations and is NOAA's analysis of record for verification. Measured over the
thirty days to 2026-09-27 from the objects' own write times (S3 LastModified on NOAA Open Data;
NOMADS directory times for the fifteen days NOMADS keeps), as lag after the valid hour, median
(90th percentile): the RTMA analysis 48 min (53) on Open Data and 46 min (47) on NOMADS; the
URMA analysis 6 h 56 min (7 h 00) on Open Data and 6 h 54 min (6 h 55) on NOMADS; the RTMA
precipitation 17 min (21) after the end of its hour; RTMA-RU 17 min (21). The Open Data copy
trails NOMADS by a median of under two minutes and occasionally by hours (up to 2 h 37 min on
the evening of 2026-09-23, when NOMADS was on time). A live poll of the bucket on 2026-09-28
found each object listed within one 20-second poll of its LastModified. The URMA precipitation
does not land at a fixed lag: a file is first written about an hour after its hour ends (the
10Z file of 2026-09-28 at 10:59:28Z, by the run near hh:58 that also rewrote twenty-five of the
trailing hours), rewritten by those hourly runs through the following day as the RFCs report,
then rerun at 1, 2, 3, 5, 7 and 8 days (25, 49, 73, 121, 169 and 193 hours) after its hour,
after which it no longer changes. The western River
Forecast Center region is often absent from the bitmap until the 25-hour rerun (on 2026-09-28 at
10Z the 13Z through 23Z files of the 27th still lacked it, while the 10Z through 12Z files had
it), and the rerun files carry the whole grid. The hourly analysis files are never
rewritten, so the lane treats the two kinds of file differently (section 5). Over the thirty
days to 2026-09-27 one file of the 1,440 analysis hours and none of the precipitation hours was
missing from NOAA Open Data: the RTMA analysis of 2026-09-23 19Z, which NOMADS had (published
there at 19:46Z) and which never reached the bucket. NCEP's NOMADS server keeps the last
fourteen days of both products under the same directory and file names
(`https://nomads.ncep.noaa.gov/pub/data/nccf/com/{rtma,urma}/prod/`) and answers byte ranges
with 206; a file not on NOAA Open Data three hours after its hour is read from there, while
NOMADS still keeps it (section 5), and every archive document records its `source`.

**Products and where each variable comes from.**

| variable | RTMA | URMA |
|---|---|---|
| temperature | `rtma2p5.tHHz.2dvaranl_ndfd.grb2_wexp`, message TMP 2 m above ground, K | `urma2p5.tHHz.2dvaranl_ndfd.grb2_wexp`, same message |
| wind (sustained) | same file, WIND 10 m, m/s | same |
| gust | same file, GUST 10 m, m/s | same |
| precipitation | `rtma2p5.YYYYMMDDHH.pcp.184.grb2`, APCP 0 to 1 hour, kg/m² (= mm), on grid G184 | `urma2p5.YYYYMMDDHH.pcp_01h.wexp.grb2`, APCP 0 to 1 hour, on the wexp grid |

Keys are `<bucket>/<product>.<YYYYMMDD>/<file>` where `<product>` is `rtma2p5` or
`urma2p5`. Every analysis file has a `.idx` sidecar listing message byte offsets; the job
reads the sidecar and range-fetches only the three messages it needs (about 6 MB each). The
precipitation file is one message and is fetched whole (about 0.5 to 0.7 MB). The
precipitation hour `HH` covers `HH-1` to `HH` UTC.

**Grids.** The wexp grid: Lambert conformal conic on a sphere of radius 6,371,200 m,
tangent cone at 25 N (Latin1 = Latin2 = LaD = 25), LoV 265 E, first grid point 19.228976 N,
233.723448 E, Dx = Dy = 2539.703 m, Ni = 2345, Nj = 1597, scanning mode 64 (i then j, both
increasing), index `k = j * Ni + i`. Grid G184 (RTMA precipitation only): the same
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
and Miami downtown at its administration building, whose own square is water to the analysis,
so the nearest land cell settles (1.72 km from the position). The list is vendored in
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
of 2026-09-26 was 290.09 K, 62.492 °F, published as 63). Half up means half away from zero:
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
value yet: RTMA precipitation files are never rewritten, and URMA's are, for up to eight days.
There is no analogue of the station report conventions (last report in the hour, specials,
tenths group); the page says this in its method note.

**Final, provisional, comparison.** RTMA resolves (owner's decision 2026-09-29;
`index.resolvesOn`). An RTMA day's high, low, gust and wind are *final* when every hourly
analysis file of the day has been read (`complete`), and its precipitation is *resolved* when
every hourly precipitation file of the day has been read (`precip.hours` equal to the day's
hour count), stamped `resolvedAt` by the first pass that found them all. NOAA never revises an
RTMA file, so neither changes afterwards and RTMA carries no revision. URMA is read, stored
and shown for **comparison** and is never final (`final` is always false for it). Because its
precipitation files are rewritten for up to eight days, its comparison total keeps the schema 1
rules: resolved at the first rebuild with every hour, the last eight local days re-read once a
day (a place still without a value takes the re-read's), and a re-read total that differs by
0.01 inch or more, or in the rounded hundredth (owner's decision 2026-09-27), carried beside it
as `revised`. A partial day carries the running value over the hours read and is labelled with
the count. A day still short of its hours 48 hours after its local end is *closed incomplete*:
its value stands on the hours read, it is never marked final, and the count is shown. The valid
times of the analyses are the `at`, `t`, `valid` and `asof` stamps; `resolvedAt` and
`revised.at` are the times of the pass that resolved or revised, and `written` is the pass
that wrote the file.

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
cell (`WINDOW_HALF = 10`, about 52 km across) on the grid that product and variable use, so the
zoomed map shows the analysis at its own resolution rather than the 14 km lattice.

## 2. Naming

- `analysis` is the lane and the job name in `pipeline/run.py`; its snapshots are under
  `snapshots/analysis2/` and its archive under `archive/analysis2/` (schema 2).
- Products are `rtma` and `urma`. Variables are `high`, `low`, `gust`, `wind`, `precip`
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
  `None` for missing cells. Both are live paths: the same-day precipitation files are 5.2
  with a bitmap, and the URMA precipitation files the RFCs rerun a day or more later are 5.3
  (order 1, management 1, no bitmap);
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
else `public, max-age=60, stale-while-revalidate=300, stale-if-error=86400`.

**`snapshots/analysis2/index.json`**

```
{ "schema": "analysis/2", "asof": ..., "written": ..., "resolvesOn": "rtma",
  "statement": "A proposed settlement framework. No contract settles on it.",
  "conventions": { "day": "...", "high": "...", ... , "cell": "...", "lattice": "...", "units": "..." },
  "sources": { "rtma": {"bucket": "noaa-rtma-pds", "lagMinutes": 47, "latest": "2026-09-27T17:00:00Z"},
               "urma": {"bucket": "noaa-urma-pds", "lagMinutes": 414, "latest": "2026-09-27T10:00:00Z"} },
  "locations": [ {"id": "new-york-ny", "name": "New York", "state": "NY",
                  "metro": "New York-Newark-Jersey City, NY-NJ", "metroRank": 1, "basis": "top50",
                  "position": "New York City Hall, City Hall, New York, NY 10007 (...)",
                  "lat": 40.7128, "lon": -74.006, "tz": "America/New_York", "note": "",
                  "px": 852.0, "py": 208.6, "cell": {"wexp": [2010, 857, 2011675], "g184": [1810, 857, 1840075],
                  "centre": [40.7141, -74.0129], "distanceKm": 0.63, ...geometry...}} , ... 67 ],
  "days": ["2026-08-30", ..., "2026-09-29"],
  "dayStatus": { "2026-09-27": {"places": 67, "final": 67, "resolved": 67, "closed": 0}, ... },
  "lastResolvedDay": "2026-09-27",
  "lattice": {"pitch": 3, "cols": 320, "rows": 200, "viewBox": "0 0 960 600"},
  "variables": { "high": {"unit": "°F", "hourly": "temp", "rule": "..."}, ... },
  "backfill": {"pointsDays": 30, "frameDays": 7, "done": false, "cursor": {...}} }
```

`sources.<product>.latest` and `asof` are the newest hour actually read for the product
(never an hour the walk passed without reading), so an outage shows as an ageing `latest`.
`days` lists the newest 60 day files; the day and place files themselves are kept
indefinitely (the archive can rebuild any of them).

**`snapshots/analysis2/days/YYYY-MM-DD.json`** — one entry per location for that local date.

```
{ "schema": "analysis/2", "day": "2026-09-26", "asof": ..., "written": ...,
  "locations": { "new-york-ny": {
      "rtma": { "hours": 24, "of": 24, "complete": true, "closed": false, "final": true,
                "high": {"value": 64, "exact": 64.148, "at": "2026-09-26T18:00:00Z"},
                "low":  {"value": 58, "exact": 57.902, "at": "..."},
                "gust": {"value": 46, "exact": 45.703, "at": "..."},
                "wind": {"value": 21, "exact": 20.833},
                "precip": {"value": 0.49, "exact": 0.4917, "hours": 24, "resolved": true,
                           "resolvedAt": "2026-09-27T04:28:45Z", "revised": null} },
      "urma": { "hours": 24, "of": 24, "complete": true, "closed": false, "final": false,
                "high": {...}, "low": {...}, "gust": {...}, "wind": {...},
                "precip": {"value": 0.53, "exact": 0.5284, "hours": 24, "resolved": true,
                           "resolvedAt": "2026-09-27T11:08:31Z",
                           "revised": null } } } } }
```

`of` is the day's hour count in that zone (23, 24 or 25). `precip.hours` is the number of
hours with a precipitation value and is on both products. An hour whose analysis file never
landed still adds its precipitation, a separate file: it counts in `precip.hours` and never in
`hours`, so it cannot make a day complete (RTMA 2026-09-23 19Z never reached NOAA Open Data,
and its precipitation file did). `final` is only ever true for the resolving product (RTMA).
`resolved` and `resolvedAt` are on both; `revised` is always null for RTMA, whose files NOAA
never rewrites. For URMA `revised`, when set, is `{"value": 0.61, "exact": 0.6104, "at":
"2026-09-29T13:08:12Z"}`, the `at` being the pass that recorded it. A partial product-day
has `complete: false` and `hours < of`; a closed incomplete day has `closed: true` and
`final: false`. A product-day with no hours yet is absent. `high`, `low`, `gust`, `wind` or
`precip` is `null` when no hour read has a value for it.

**`snapshots/analysis2/loc/<id>/YYYY-MM-DD.json`** — the location-day at the hourly scale.

```
{ "schema": "analysis/2", "id": "new-york-ny", "day": "2026-09-26", "tz": "America/New_York",
  "asof": ..., "written": ...,
  "hours": [ {"local": "00", "t": "2026-09-26T04:00:00Z",
              "rtma": {"temp": 63.14, "wind": 18.223, "gust": 33.506, "precip": 0.0197},
              "urma": {"temp": 63.032, "wind": 18.401, "gust": 33.103, "precip": 0.0315}}, ... ],
  "summary": { ...the same object as this location's entry in days/YYYY-MM-DD.json... } }
```

`hours` has one row per analysis of the local date (23, 24 or 25 rows); on the fall-back day
the second 01:00 is labelled `01*`, and the UTC stamp `t` sits beside every label. A row's
`temp`, `wind` and `gust` are the analysis at `t` and its `precip` the accumulation over the
hour that starts at `t`, which NOAA files under `t` plus an hour, all converted from the
archive's own numbers and cut to a thousandth (precipitation a ten-thousandth of an inch). The
page's running sum of the rows is pinned to the summary's exact total once every hourly file is
read. A product missing at an hour is `null`, and a product read at an hour whose
precipitation file had no value yet has `precip: null`.

**`snapshots/analysis2/grid/index.json`** — which frames exist.

```
{ "schema": "analysis/2", "asof": ..., "written": ...,
  "frames": { "rtma": { "temp": { "2026-09-27": ["00", "01", ...] }, "wind": {...}, "gust": {...}, "precip": {...} },
              "urma": { ... } } }
```

Frame days here are UTC days. The index is rewritten after every hour's frames (it is
2 KB), so a frame on the bucket is always listed, and again at the end of every pass. Frames
are kept for 30 days and pruned once a day.

**`snapshots/analysis2/grid/<product>/<var>/YYYYMMDDTHHZ.json`** — one lattice frame.

```
{ "schema": "analysis/2", "product": "rtma", "var": "temp", "valid": "2026-09-27T16:00:00Z",
  "unit": "°F", "scale": 10, "cols": 320, "rows": 200, "pitch": 3,
  "values": [ 612, 615, null, ... 64000 ints, row-major from the top-left lattice point ] }
```

Each frame also carries `windows`, keyed by location id:
`{"grid": "wexp", "half": 10, "values": [441 ints]}`, row-major with `dj` from −10 to +10 and
`di` from −10 to +10 inside each row, on the same `scale`, `null` where the cell is off the
grid or missing in the bitmap. For RTMA precipitation the window grid is `g184` and every other
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
re-reading NOAA. `archive/analysis2/_meta/state.json` holds the cursors: per product `newest` (the
newest hour read), `scanned` (how far the live walk has looked), `since` (when the lane first
looked), `gaps` (hours passed without a read, retried for 48 hours), `precipPending`
(archived hours whose precipitation file is still being refetched) and `reads`; per product
the backfill `cursor`, `oldest`, `done` and queued catch-up spans; the precipitation re-read
stamps and revision records (both pruned past nine days; a revision lives on in its day
file); the prune stamp; and `precipSwept`, the time of the last precipitation sweep (step 4b).
A precipitation key without an archive hour is the precipitation of an hour whose analysis
file never landed. `archive/_meta/health_analysis.json` is the lane's own failure
streak file, written through `archive.update_health` under the market lane's convention so
this lane's streaks never collide with another lane's. Pages never read `archive/`.

## 5. The job

`analysis_pass(cfg, store) -> int`, registered as `JOBS["analysis"]` and run on its own
EventBridge schedule `cron(8/10 * * * ? *)` (between the observation and quote lanes). Each
pass runs under `arch.Deadline(min(arch.remaining_budget(cfg), PASS_CAP_SECONDS))` with
`PASS_CAP_SECONDS = 330`, which with `FETCH_TRIES = 2` and `FETCH_TIMEOUT = 20` (about 212 s
for the worst hour) keeps a pass inside the ten-minute cadence, so two passes never overlap on
the state file, keeps
`RESERVE_SECONDS = 45` back from the lanes for the rebuild and the indexes, and sets
`arch.LAST_STATUS`. Every NOAA call is `fetch_range` or `fetch_bytes` in
`pipeline/gov_weather.py` with the site's User-Agent, two tries of twenty seconds each, so
one hour's five fetches cannot hold a pass for more than a few minutes and the 900 s Lambda
timeout is never what stops it.

1. **Pending precipitation.** For each product, refetch the precipitation file of every
   archived hour in `precipPending`: an hour whose file was absent, refused, or short of a
   place's cell at the last read. A read that adds values rewrites the precipitation key
   and (within the frame window) the precipitation frame; an hour whose file is complete
   leaves the list; an hour past `PRECIP_WAIT_HOURS = 3` after the product's lag is dropped
   with whatever coverage it has (the URMA re-read of step 4 fills it later). The lane
   never waits on a precipitation file before archiving an analysis hour.
2. **Live hours.** For each product, the hours to try are the `gaps` first, then the hours
   after the walk's cursor (the later of `newest` and `scanned`) up to what should have
   landed (`now` less the product's lag, floored to the hour). A first pass starts at the
   present. A cursor more than 24 hours behind jumps to the present and queues the skipped
   span for the backfill (once, and no older than the backfill reaches). For each hour, up
   to three reads per product per pass: fetch the `.idx`, range-fetch TMP, WIND and GUST,
   sample each field at the places' cells and onto the lattice, then fetch the precipitation
   file and do the same. Write the archive hour (once), the precipitation key, the four
   frames and `grid/index.json`; queue the hour in `precipPending` when its precipitation
   is short and its wait has not run out. A file NOAA Open Data does not have is looked for
   on NOMADS once it is `NOMADS_AFTER_HOURS = 3` hours past its hour and until
   `NOMADS_KEEP_HOURS` (thirteen days, a day short of what NOMADS keeps), and the archive
   document records `source: "nomads"`. A `.idx` that is on neither (403 or 404) is an
   *absence*: the hour becomes a gap, `scanned` moves past it, and the walk continues. The
   absent hour's precipitation file is read all the same, on every retry of the gap until
   every place has a value, and stored without an archive hour. After one refused
   precipitation file of an absent hour, the lane reads no more absent hours' precipitation
   that pass; the gap's retries or the sweep of step 4b read it later. A
   read that fails (a short body, a message the decoder refuses, a 5xx after the retries)
   is an *error* recorded against the product in `errors`; the hour becomes a gap,
   `scanned` moves past it, and the product's walk stops for this pass so an outage costs
   at most one failing read per product per pass. An hour already archived (a crash between
   the archive write and the state write, or the backfill) counts as read without a fetch,
   unless it is within the frame window and its frames are not in the grid index, in which
   case it is read again for its frames. `newest` only ever moves on a read. The backfill's
   cursor is seeded by the first hour the walk looks at, read or not (on 2026-09-27 a fresh
   lane first looked while both products' newest hours were half an hour late, and a cursor
   seeded only by a read would have left the backfill idle until one landed).
3. **Days.** Rebuild every `days/` and `loc/` file that a new hour or a filled
   precipitation key touches (a UTC hour lands in one local day per zone), and any day past
   its close time, from the archive hours, applying section 1's rules. The state is saved.
4. **Precipitation re-reads.** Once a day per local day for the last eight days, the oldest
   day due first: re-read every URMA precipitation hour of any day that is complete at some
   place (resolved or not), fill the precipitation keys where a place had no value, and for
   each resolved place compare the re-read total with the resolved one; record a revision
   when it differs by 0.01 inch or more or in the rounded hundredth, withdraw one that came
   back within that. Rebuild the days touched. A re-read cut short by the deadline is not
   stamped and is due again next pass.
4b. **Sweep.** Once a day (`PRECIP_SWEEP_HOURS = 24`), over the hours both walks have passed
   (from the backfill's `oldest`, at most 30 days back, to 48 hours before the live cursor,
   outside queued catch-up spans): read the analysis of every hour that has no archive hour
   while NOMADS may still keep it, and the precipitation of every hour that has no
   precipitation key, and rebuild the days they touch. A refused file ends that product's
   sweep until the next day's; a sweep cut short by the deadline is not stamped and is due
   again next pass.
5. **Backfill.** With the remaining budget, walk both products backwards, alternating,
   newest first: a queued catch-up span first, then from the cursor back to 30 days ago;
   frames for the last seven days, point reads only before that. The cursor advances only
   after a product-hour is written or known absent or unreadable (an unreadable hour is
   skipped and recorded, like a missing one). An absent hour's precipitation is read and
   stored as in step 2, with the same limit of one refused file per product per pass. The
   state is saved every twenty hours read.
6. **Prune**, once a day: frames older than 30 days and their index entries, and re-read
   and revision records older than nine days.
7. **Always**, in a `finally`: `grid/index.json`, the state, the health file and
   `index.json` last, so a reader sees a consistent set whatever happened above.

Nothing a NOAA object does fails the pass. Every fetch and decode of an hour is guarded
inside `read_hour` and `read_precip`; every lane call is guarded in the pass; the pass-level
`except` is the last resort and still reaches the `finally`. A pass returns 1 only when a
read failed and nothing at all was read. The health file keeps a failure streak per product
through `archive.update_health`: a pass with a read of that product resets it; a pass counts
against it when a read of that product failed or when the product is *overdue*, which is the
newest hour read (or, before any read, the hour the lane first looked for) being
`OVERDUE_HOURS = 2` hours or more behind what should have landed; any other empty pass is
the normal wait between landings and counts neither way. Six such passes in a row
(`FAIL_STREAK_ALARM`) raise the lane's alarm the same way the other lanes do, so an outage on
NOAA's side is an alarm after about three hours and a corrupt file among readable hours is
not. `LAST_STATUS` carries the counts and the lists (reads, absences, waiting precipitation,
errors), and `newest` per product.

Write budget per pass (measured with fifty places): a pass where one hour of each product lands writes
about 70 objects; an empty pass five; a precipitation refetch that adds coverage one key,
one frame, one day file and one place file per place. With RTMA and URMA landing in different passes
that is about 110k PUTs a month, and the 30-day backfill a one-time 15k (about a third more
with sixty-seven places). Schema 2's first 30 days were filled on 2026-09-29 by a local run of
the lane's own `read_hour`, `write_hour` and `rebuild_days` with the reads in parallel, then
uploaded, so the live lane started with its backfill done.

## 6. The page

`analysis-resolution.html`, unlisted (`noindex`, not in `config/contracts.json`, not in
`REF`), built from the same skeleton as `wind-markets.html`: title, `p.sub` carrying the
statement, a `.bar` with the controls and `#pageStatus`, the map card, the location panel,
the method prose, `p.cap#foot`. Scripts: common, data, then `js/analysis.js`; `WXAnalysis.init()`.

**Controls.** Variable buttons High · Low · Peak gust · Mean wind · Precipitation; product
buttons RTMA · URMA, RTMA first and the default, since it resolves; a day `<select>` over `index.days` (newest first, labelled with the
weekday, and a day short of every place resolving marked `provisional`, or `closed` when its
unresolved places have all stopped waiting); an hour stepper under the map (◀ ▶ Play, the valid hour in UTC and in the selected
location's zone when one is picked). State lives in the URL: `?var=high&product=rtma&day=2026-09-26&hour=16&loc=new-york-ny`.
With no `day` in the address the page opens on `index.lastResolvedDay` (owner's request
2026-09-28), the newest local date on which every one of the sixty-seven places has its RTMA
day final and its precipitation total resolved (`dayStatus.resolved == dayStatus.places`); an
index without the field opens on the newest day. The job keeps each day's status in its state
as it rebuilds the day and fills a status it lacks from the day's file. An RTMA day resolves
within about an hour of its local end, so the page typically opens on yesterday.

**Map.** The 960 × 600 SVG with `assets/basemap.json` state paths, the hour's frame drawn
onto a 320 × 200 canvas with the variable's fixed ramp and placed as an `<image>` filling the
viewBox with `image-rendering: pixelated`, clipped to the state outlines; the sixty-seven
location dots sized and coloured by that day's value of the selected variable and product,
solid once RTMA has resolved it, dashed at fill-opacity .55 while provisional and on the URMA
comparison, hollow when the day has no value yet; hover
reads the cell under the pointer from the frame and the dot's daily value; click opens the
location panel. Zoom and pan as in `varmap.js`. Ramps are pinned: temperature −20 to 110 °F
on the site's nine-stop ramp, gust 0 to 60 mph, wind 0 to 30 mph, precipitation 0 to 2 in
with zero transparent. A legend under the map states the ramp and the subsample.

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
beside the place select sets the view so the picked cell is about 30 px wide and centred; the
URL carries the view when zoomed (`?z=<factor>&cx=<x>&cy=<y>` in viewBox units) so a zoomed cell
can be linked.

**Location panel** (`#locPanel`, shown on pick or `?loc=`): the name, state, the day and
zone, previous and next day buttons; a resolution row of five cards (rounded value, exact
value cut to a thousandth, status pill final / provisional / closed on RTMA and comparison on
URMA, with n of the day's hours while incomplete, the time of the extreme, and the other
product's value); the hourly chart for the selected variable, a 960-wide `svg.ts` in a `.card` in
the style of `WXK.plot`: local hours on the x axis, midnight and the day end marked, URMA as
a dashed line in `--ana-urma` underneath and RTMA solid in `--ana-rtma` on top, the value as a horizontal
line, and a ladder column of hypothetical rungs at whole units around the value (precip at
0.01, 0.05, 0.10, 0.25, 0.50, 1.00, 2.00): Yes in `--yes`, No in `--no`, hatched with
`#wxHatch`, labelled "Yes" and "No", never cents, never linked; an hour table with one row
per analysis of the day (local hour, UTC, both products, all four hourly variables, the
precipitation over the hour that starts at the stamp); the cell note (cell centre, distance
from the position and its building, the grid cell on both grids, the ruling when present). Two
tokens are added to all three theme blocks of `site.css`: `--ana-rtma` and `--ana-urma`.

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
  partial and closed days, precipitation revision by exact total and by rounded hundredth,
  the local day spanning two UTC directories, the 23- and 25-hour daylight-time days,
  Arizona unchanged); `index.json` written last; a missing object is an absence; a raising
  fetch or decode on one product still writes the other product's hour, the day file, the
  state and the index and reaches the alarm streak when it persists; the alarm fires after
  the outage passes and `asof` stays on the hour read; frames are indexed the pass they are
  written; a partial bitmap is retried and filled; a re-read fills a place the first read
  had no cell for; the catch-up span is queued once and clamped; the backfill starts behind
  a late newest hour; the place file carries the precipitation in the row of the hour it
  starts; the day's precipitation runs midnight to midnight; a value the file holds exactly is
  converted exactly and the aggregate rounded once; an RTMA day is final and URMA only
  compares; a file missing from NOAA Open Data is read from NOMADS after three hours and not
  past thirteen days, and the sweep reads a missing analysis from there; an
  hour whose analysis never lands still adds its precipitation, in the live lane, in the
  backfill and through the daily sweep, without counting toward the day's analyses, and a
  refused file ends a product's sweep for the day;
  `fetch_range` accepts the clamped 206 for a last message.
- `scripts/verify.py`: the page in the `pages` sweep; checks that the variable and product
  buttons change the legend and the frame, the day select and hour stepper change the caption,
  a dot click opens the panel with the hour rows and five cards, the hypothetical rungs are
  hatched and carry no `data-contract-url`, the provisional pill appears when `rtma` is
  incomplete, the chart rule says resolved on a final RTMA day and value on the URMA
  comparison, the exact figures are cut to a thousandth, `?loc=` opens the panel on load, and
  the 503 degradation shows "No data". Fixtures under `samples/snapshots/analysis2/` come from
  a real local run of the job.
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
