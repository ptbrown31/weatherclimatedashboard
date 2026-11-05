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
same ruling has the job **read every file from NOMADS as well as NOAA Open Data** and keep each
server's first copy, NOMADS's counting when the two differ (section 1). A file's publication time
is since then the first time it was on either server. 2026-11-04 (owner's ruling): **the choice of
copy for every hour of a day is final at the first ten-minute pass after the day's cut-off**, on
the copies read by then, or earlier once both servers' copies are in; a copy read later is
recorded and flagged and never used, and **a day shown final never changes**. The same ruling
keeps the choice per file (a place the copy that counts has no value for has none that hour), and
a download whose message fails the check is a server's copy only when a second download returns
the same bytes. Later on 2026-11-04 (owner's approval of display v3): **each row of a place file
carries its figures as exact strings, both files' publication times on each server and the hourly
temperature contract of its hour** (`rowShape` 3, section 4), so the settlement display computes
nothing, and **the hourly contract has its own cut-off**, three hours after its hour by Patrick's
ruling of 4 November and **1 hour 30 minutes after it by his ruling of 5 November**, which every
listed day was rewritten under (section 1). 2026-09-30: **URMA is dropped from the lane and the page**, since
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
and the precipitation files for all fourteen. A file on NOMADS counts as published (owner's
decision 2026-09-29). The draft terms (owner's decision 2026-10-14) say the NOMADS copy counts
when the two servers' copies of a file differ, and that values count as published, without
adjustment.

**Both servers (owner's ruling 2026-11-03).** The lane reads every analysis and precipitation
file from NOMADS and from NOAA Open Data, NOMADS first, from the moment the file is due, and
NOMADS while it keeps the hour (`NOMADS_KEEP_HOURS`, thirteen days, a day short of its
fourteen). Each server's first copy of a file is kept on its own and written once (section 4),
with when this lane first saw it, the server's Last-Modified time and a sha256 of each message
read. Where a message's hash equals the other server's, its values are that copy's; where the
hashes differ, both copies are decoded at the places, and the raw CSV keeps the hash comparison
apart from the value comparison. A later read of the same server's file with other bytes or
another Last-Modified time is a replacement, recorded beside the first copy and never used
(section 5). Each place's row takes one copy, by the day's cut-off:

- NOMADS's copy, when it was published by the cut-off, whatever NOAA Open Data's holds;
- otherwise NOAA Open Data's, when it was;
- otherwise neither counts, and the row shows NOMADS's copy, or NOAA Open Data's when NOMADS has
  none, as published late.

The choice is per file (owner's ruling 2026-11-04). If the copy that counts has no value at a
place, from a bitmap that leaves the place out or a message refused, that place has no value for
the hour, whatever the other copy holds. A file's publication time, which the cut-off is judged
on, is the first time it was on either server, its Last-Modified time there, or when this lane
first saw it where a server sent none, counting, once the row has settled, only the copies it
settled on.

**When the choice is final (owner's ruling 2026-11-04).** A file settles for the days of each
cut-off, and the pass at which it did is kept (`settled`). It settles at the pass at which both
servers' first copies are in, since no later read can count, which is usually minutes after NOAA
publishes it and long before any cut-off. Otherwise it settles at the first ten-minute pass after
the cut-off, on the copies read by then. If NOMADS could not be reached and no NOMADS copy is in
hand, that is NOAA Open Data's copy, when it was published by the cut-off; if neither server has
given one, the hour has no file. The lane looks for every open file once more at that pass before
it settles it. A copy read after its file settled, from either server and whatever its
Last-Modified, is recorded beside the others and flagged (`lateCopy` or `pcpLateCopy` in the place
file, `analysis_late_copy` or `precip_late_copy` in the raw CSV), as is a NOMADS copy in hand that
differs from the NOAA Open Data copy a row keeps, having been published after the cut-off. Neither
ever changes a value, a count, a day state or a map frame. A place-day turns final only once every
file of its rows has settled for its cut-off, so a day shown final never changes; at the cut-off
that is at once when every file has both copies, and otherwise at that first ten-minute pass. The
settled record of a file (the archive hour, or the precipitation key) is written once, when it has
settled for every cut-off of its days, with no copy in it when neither server gave one. Until
2026-11-04 a file settled at once on NOMADS's copy, and otherwise waited past the cut-off for each
server without a copy to say it had none, for up to thirteen days when NOMADS could not be reached.

**Broken transfers (owner's ruling 2026-11-04).** Each message is checked for its grid, its
parameter, its valid time, a clean decode and a value at one place at least. A download whose
message fails that check is not a server's copy, since a transfer that broke looks just like it,
and the server has no copy for the hour until the next pass downloads the file again within the
NOMADS budget. A download that comes whole is the copy. One that fails with the same bytes as the
download before is NOAA's own file, and becomes the server's copy with the failing messages
refused and without values; when it is the copy that counts, those values are missing for the
hour, as the file is. The rule is the same on both servers. Until 2026-11-03 the lane read NOAA
Open Data first and NOMADS only for a file NOAA Open Data lacked (half an hour after the product
usually lands, and a precipitation file 27 minutes after its hour ends), so it never compared the
two copies.

**The hourly contract (Patrick's ruling of 4 November).** Each hour's temperature is also the value
of an hourly temperature contract on that hour, whole °F rounded once, half up, away from zero.
Its cut-off is 1 hour 30 minutes after the hour (`HOURLY_CUT_AFTER`, Patrick's ruling of 5
November, the draft terms' Resolution Time; three hours from his ruling of 4 November until then),
the same in every zone, and the day's 12:45 AM cut-off does not apply to it. He shortened it on the
engine's count over the record since September 2024, by which a file later than about 80 minutes is
nearly always part of a long outage that a longer wait would not have covered. Each hour's state is
worked out from its copies and their times under the cut-off in force when its place file is
written, so the change came back over every listed day with `LAYOUT` (section 5): the hours before
NOMADS's times were kept turned final at 1 hour 30 minutes on NOAA Open Data's copy, and those whose
NOAA Open Data copy came between 1 hour 30 minutes and three hours after the hour went to review. One
analysis file serves the hourly contracts of its
hour at every place, and the file settles for the hourly cut apart from the days' cut-offs
(`settle_cuts`, under `hourly` beside `cutoffs`) by the same rules: at the pass at which both
servers' copies are in, or at the first ten-minute pass after the cut, on the copies in hand. The
contract is:

- final once NOMADS's copy is in, if it was published by the cut and read before the cut settled,
  at that copy's Last-Modified (basis `nomads`);
- otherwise, once the cut has settled, final at the cut itself on the copy `choose` takes with the
  cut as its cut-off, if that was published by the cut (basis `cutoff`), as a day's `finalAt` is
  its cut-off;
- under review at the cut, with no value, when the cut settled with no copy published by then;
- provisional until then, on the copy in hand.

A copy read after the hourly cut settled, whatever its Last-Modified, is recorded and flagged
(`late`) and never used. Since the two cut-offs differ, an hour can count for its day and not for its
hourly contract: a file published two hours after an afternoon hour counts only for the day, and New
York's 23:00, with its hourly cut at 12:30 AM and its day's cut-off at 12:45 AM, counts a file
published at 12:40 AM for the day alone. The reverse, which the three hours allowed at 10 and 11 PM,
no longer happens, since the hourly cut now always comes before the day's own cut-off. Where both
count the value is the same, unless NOMADS's copy first appears between the two cut-offs and differs
from NOAA Open Data's. A place the copy that counts has no temperature for is under review at the
cut, the choice being per file. The settled record waits for the hourly cut as well as the days'
cut-offs; with the sixty-seven places some zone's cut-off is always the later, at least 4 h 45 min
after the hour, so the record is written when it was before, and a test holds a day's figures,
states and CSV files byte-identical to the lane's before the change. A record settled before this
change has no `hourly` and is read as settling it with the record, on the copies it was written
with, as a cut-off a record does not name is read.

An hour read before the lane kept each server's copy has no NOMADS copy on record. It is final at
the cut on the copy read (basis `norecord`), or at NOMADS's Last-Modified where the engine's probe or
the record has one by the cut (basis `nomads`, the copy read still the one used; the probe found the
two servers' copies the same in every file it compared), and under review at the cut where NOAA Open
Data's Last-Modified is after it. Of the files from 2026-08-29 to 2026-10-14 only the analysis of
2026-09-23 23Z is in review, on NOAA Open Data at 02:06:41Z, and the analysis of 2026-09-23 19Z,
which never reached NOAA Open Data, is final at NOMADS's 19:46:54Z. The times come from
`config/analysis_file_times.json` (section 4), which no day's figure, count or state reads.

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
lat, lon, i, j, tz, note, and from 2026-11-04 code, ForecastEx's location code, which the
display's header and CSV files name), with the approved cell given, never recomputed;
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
thousandth (precipitation a ten-thousandth of an inch). Each server's first copy of a file
stands, and a file's settled record is written once, so a settled value never changes. A place
the copy that counts has no value for has no value that hour (owner's ruling 2026-11-04, per
file). Until 2026-11-03 a later read of a precipitation file filled a place a partial read had
missed.
There is no analogue of the station report conventions (last report in the hour, specials,
tenths group); the page says this in its method note.

**Final and provisional.** RTMA resolves (owner's decision 2026-09-29;
`index.resolvesOn`). A place-day is *final* at its cut-off, 12:45 AM local time on the day after
(`day_cutoff`, the day's end plus 45 minutes; clocks never change in that span), on the hours
whose files were published by then (owner's ruling 2026-10-14, the draft terms): an analysis
counts when its file's `published` time is at or before the cut-off, and an accumulation when
its file's is and the file holds a value at the place. A document read before 2026-10-14
carries no `published` time and counts. Since 2026-11-04 the day turns final at the cut-off only
once every file of its rows has settled for it (above), so while a file the lane holds open or
will still read (the live walk, a queued catch-up span, the backfill) may change, the day stays
provisional past its cut-off, for minutes when every file has a copy in hand, and a day shown
final never changes. Until then the day is *provisional* and its values run over the hours read.
At the cut-off `finalAt` and `resolvedAt` are the cut-off itself, and a day short of hours says
so (`hours < of`, "final, 23 of 24 hours" on the page). A file published after the cut-off and
read by the time its row settled is shown in the place file, marked `late` (or `pcpLate`),
dimmed on the page, and never counted; one published before the cut-off and read by then
counts, so the summary comes out the same whenever it is rebuilt and needs no memory of an
earlier one. The first published version of a file counts. Each server's first copy is kept and
the settled record is written once, and a later version is recorded and never used (until
2026-11-03 a precipitation read of the same server's file with another Last-Modified time was
recorded as `replaced`). A place with no analysis that counts at the cut-off has no value
and is marked for event review (`review`), and one with no accumulation that counts has
`precip.review`; a place with nothing read at all gets `review_entry` when other places of the
day have hours, and a day with nothing read anywhere is not written. There is no closed state
any more: `closed` is always false, and the page still reads it in files written before
2026-10-14. The state lists the files that settled on no copy (`missing`, `pmissing`) for the
lane's own bookkeeping; they decide nothing about a day. The
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
  "rowShape": 3, "asof": ..., "written": ...,
  "hours": [ {"local": "00", "t": "2026-09-26T04:00:00Z",
              "rtma": {"temp": 63.14, "wind": 18.223, "gust": 33.506, "precip": 0.0197,
                       "pub": "2026-09-26T04:48:31Z", "pcpPub": "2026-09-26T05:17:02Z",
                       "src": "nomads", "pcpSrc": "nomads", "pubSrc": "nodd", "pcpPubSrc": "nomads",
                       "show": {"temp": 63, "wind": 18, "gust": 34, "precip": 0.02},
                       "run": {"hours": 1,
                               "high": {"value": 63, "exact": 63.14, "at": "2026-09-26T04:00:00Z"},
                               "low": {...}, "gust": {...}, "wind": {"value": 18, "exact": 18.223},
                               "precip": {"value": 0.02, "exact": 0.0197, "hours": 1}},
                       "runX": {"high": "63.140", "low": "63.140", "gust": "33.506...", "wind": "18.223...",
                                "precip": "0.0197007874"},
                       "raw": {"temp": "290.45", "wind": "...", "gust": "...", "precip": "0.5004"},
                       "x": {"temp": "63.140", "wind": "18.223...", "gust": "33.506...", "precip": "0.0197007874"},
                       "anlFile": {"name": "rtma2p5.20260926/rtma2p5.t04z.2dvaranl_ndfd.grb2_wexp",
                                   "nodd": {"lm": "2026-09-26T04:48:31Z", "seen": "2026-09-26T04:50:12Z"},
                                   "nomads": {"lm": "2026-09-26T04:49:40Z", "seen": "2026-09-26T04:50:02Z"},
                                   "src": "nomads", "counts": true, "afterCut": false},
                       "pcpFile": {"name": "rtma2p5.20260926/rtma2p5.2026092605.pcp.184.grb2", ...},
                       "hourly": {"cut": "2026-09-26T07:00:00Z", "state": "final", "at": "2026-09-26T04:49:40Z",
                                  "basis": "nomads", "src": "nomads", "value": 63, "x": "63.140",
                                  "late": false}}}, ... ],
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
- `pub` and `pcpPub` are when each file was first on either server (its Last-Modified time
  there), which the cut-off is judged on, and `pubSrc` and `pcpPubSrc` which server that was
  (`nodd` or `nomads`, from 2026-11-03), which the display names beside the time. `src` and
  `pcpSrc` name the server whose copy the row's values are, NOMADS's when it counts (section 1).
  They are the raw CSV's figures, present only when known, so a file read before 2026-10-14 has
  no publication times and one read before 2026-11-03 names its one server in `src`.
- `settled` and `pcpSettled`, from 2026-11-04, are the pass at which the copy each file gives the
  row became final (section 1), absent until then; the raw CSV's `analysis_settled` and
  `precip_settled` are the same times.
- `lateCopy` and `pcpLateCopy`, from 2026-11-03, mark an hour with a copy that came too late to
  count, read after its row settled, or a NOMADS copy that differs from the NOAA Open Data copy
  the row keeps, having been published after the cut-off (section 1). They change no value,
  count or state. An hour whose files gave its row no value has no place-file row to carry them,
  and the raw CSV records it.

From 2026-11-04 (`rowShape: 3`, the owner's approval of display v3) every row with a value
carries six more, so the display shows every figure and time as the lane wrote it and computes
none. Every time is ISO UTC with Z, and a missing figure is `null`.

- `raw` is each value as stored, a string in the file's own unit: kelvin and metres per second to
  two decimals, millimetres with no trailing zeros and `"0"` for none, `null` for a variable the
  row has no value for.
- `x` is each value converted before rounding, an exact string (`TEXT_PLACES`): a thousandth of a
  degree, twelve places of a mile an hour (hundredths of a metre per second times the ten places of
  2.2369362921), and inches cut toward zero at ten places, since their decimal seldom ends (0.0123
  mm is written 0.0004842519, where rounding would give 0.0004842520). It is computed in rational
  arithmetic, never a Decimal division. A stored value finer than the file's precision would be
  written exactly, at more places, as would an inch figure whose decimal ends past ten places; the
  analysis is packed to hundredths and the archive keeps millimetres to six places, so none is.
- `runX` is the day so far after the row before rounding, over exactly the hours `run` counts
  (`running_texts`, on day_summary's own rule, `_counted`): the high and low to a thousandth and the
  peak gust to twelve places, exact, and the mean wind and precipitation total cut at ten places,
  the total converted once from the summed millimetres.
- `anlFile` and `pcpFile` are the row's analysis file and its precipitation file: `name` as NCEP
  names it under its day directory (`file_name`, the ending of `anl_url` and `pcp_url`); `nodd` and
  `nomads`, each server's `lm` (its Last-Modified) and `seen` (when the lane first found its copy
  there), `null` for a server with no record; `src`, whose copy the row uses; `counts`, whether the
  hour counts for the day, false when the row's value is marked late or it has none; and
  `afterCut`, whether the first publication shown, the earliest `lm`, is after the day's cut-off.
  `pcpFile` is `null` until the precipitation hour has ended. A file read before the lane kept each
  server's copy takes the Last-Modified its record kept for the server it was read from, or else
  the table's (`table_times`, below), with `seen` only where the table has it; a file nothing of
  which was read has no times. Such a file read before 2026-10-14 can have `counts` and `afterCut`
  both true, as it counted under the rule then. The precipitation of the hour ending
  2026-09-30T07Z, on NOAA Open Data at 08:01:44Z, after the 07:45Z cut-off of 29 September at Los
  Angeles and Phoenix, is the one such file, and it stays counted.
- `hourly` is the hour's hourly contract (section 1): `cut`; `state`, `final`, `provisional` or
  `review`; `at`, NOMADS's Last-Modified for basis `nomads` and the cut for `cutoff`, `norecord`
  and a review, `null` while provisional; `basis`, `nomads`, `cutoff` or `norecord`, `null` while
  provisional; `src`, the copy it reads, `null` in review; `value`, whole °F, and `x`, its exact
  string; and `late`, a copy read after its cut settled.

A row with no value for the day whose hourly contract has a state (a file on neither server by
the hourly cut, or, under the three hours, one too late for the day and in time for the hourly
contract) carries `hourly`,
`anlFile` and `pcpFile` and no value fields, and a row of which nothing is known stays `null`. A
place with no value yet for its day has no place file to carry them until its first value or its
cut-off. The day files, both CSV files and every field of before are unchanged.

A file without `rowShape` was written before these fields. The page still draws it, as the file
stands, with no running figures and no replay; the lane reads nothing from place files, and the
`LAYOUT` change of section 5 rewrites the listed days in the new shape a few a pass.

From 2026-11-04 (owner's approval) a place with no hour yet on a newly listed day gets a file with
no rows (`"hours": []`) and an empty `summary` when the day is listed, if its cut-off is still
ahead (`empty_loc_doc`). The page then says no hour is in yet, where it said the file could not be
read: between Eastern and Pacific midnight a western place has no hour of the new day. Its first
hour replaces the file, and the day file lists the place only from then.

**`snapshots/analysis2/csv/YYYY-MM-DD-raw.csv` and `-processed.csv`** — the day as CSV (owner's
decision 2026-09-29), rewritten with the day file. The raw file has one row per place and hour
in the files' own units: `date, location, local_hour, valid_utc, temp_k, wind_ms, gust_ms,
precip_mm, precip_file_utc, analysis_source, precip_source, analysis_published, precip_published`
(the files' Last-Modified times, empty for a file read before 2026-10-14), the precipitation being the hour that
starts at `valid_utc` and filed under `precip_file_utc`, nothing converted or rounded. Those
columns keep their order and their meaning from 2026-11-03. `temp_k`, `wind_ms`, `gust_ms` and
`precip_mm` are the values the row settles on, NOMADS's copy when the two differ and it counts;
`analysis_source` and `precip_source` name that copy's server; and `analysis_published` and
`precip_published` are the first time the file was on either server, of the copies the row
settled on once it has. After them come each server's copy of each file, in this order
(`RAW_COPY_HEADER`):

- `analysis_settled`, `precip_settled`, the pass (a UTC time) at which the copy the row uses
  became final, empty until then and for a file read before 2026-11-03. Until 2026-11-04 they
  were `true` or `false`; the names and places are unchanged. A copy whose `first_seen` is later
  than its row's settled time is never the one the row uses;
- for the analysis, for `nodd` and then `nomads`, `analysis_<server>_first_seen`,
  `_last_modified`, `_temp_k`, `_wind_ms`, `_gust_ms`, `_temp_sha256`, `_wind_sha256` and
  `_gust_sha256`;
- `analysis_hash_match`, `analysis_value_match` (the two copies' bytes, and their values at the
  place) and `analysis_late_copy` (section 1);
- for `nodd` and then `nomads`, `analysis_<server>_replacements` (how many later replacements
  were recorded) and the newest one's `_replacement_seen`, `_replacement_last_modified`,
  `_replacement_temp_sha256`, `_replacement_wind_sha256` and `_replacement_gust_sha256`;
- the same for the precipitation file, `precip_<server>_first_seen`, `_last_modified`, `_mm` and
  `_sha256`, then `precip_hash_match`, `precip_value_match` and `precip_late_copy`, then
  `precip_<server>_replacements`, `_replacement_seen`, `_replacement_last_modified` and
  `_replacement_sha256`.

A copy's columns are empty when that server's copy was never read, and the comparisons are
empty until both copies are in. A row is written when either file gives it a value, and from
2026-11-04 also when either file has a copy and gives it none, a copy read too late, say, with
the value columns empty. The processed file has one row per place: `date, location, name, state, final, final_at, hours, of,
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

**Archive.** `archive/analysis2/copies/<product>/YYYYMMDDTHHZ.<kind>.<server>.json.gz` is one
server's first copy of one file (owner's ruling 2026-11-03), `kind` being `anl` (the analysis
of that hour) or `pcp` (the precipitation file of the hour ending then) and `server` `nodd` or
`nomads`, written once (`put_if_absent`). It holds `seen` (when this lane first saw it),
`published` (the server's Last-Modified time), `sha256` (a hash of each message read, by
variable), `decoded` (the messages decoded, since one whose hash equals the other server's takes
that copy's values), `values` (kelvin and metres per second, or millimetres, the files' own
numbers), for a precipitation file `missing` (the places its bitmap leaves out), and `refused`
(the messages that failed the check in NOAA's own file, fetched twice with the same bytes, which
have hashes and no values; section 1). A later replacement of the same server's file is
appended to the copy's `.later.json.gz` beside it, with when the lane saw it, its Last-Modified
time and its hashes, and is never used.
`archive/analysis2/hours/<product>/YYYYMMDDTHHZ.json.gz` and
`archive/analysis2/precip/<product>/YYYYMMDDTHHZ.json.gz` are each the file's settled record,
written once when it has settled for every cut-off of its days, and an analysis for its hourly
cut too (section 1): `settled` (that pass), `cutoffs` (for each cut-off, the pass it settled at,
`at`, the servers whose copies were in hand, `with`, the copy chosen, `src`, and the first
publication of those copies and its server, `published` and `pubSrc`), for an analysis from
2026-11-04 `hourly` (the hourly cut's entry in the same shape), `picks` (the server each place's row uses, `null` for
none), `values` (that copy's values), `source` (the one server every place settled on, `null`
when they differ or none), `published`, `servers` (each server's copy's `seen`, `published` and
`sha256` at the time) and `hashMatch`, and for the precipitation `missing`, `read` and
`complete`. A file that settled on no copy has one too, every pick `null`. A record written before 2026-11-03 has the
earlier shape, one file read from one server with its `source` and `published` time, and
stands; the precipitation key of that shape was filled in by later reads, never changing a
value. Together they let any day file be rebuilt from the archive without re-reading NOAA.
`archive/analysis2/_meta/state.json` holds the cursors: per product `missing` and `pmissing`
(the hours whose analysis or precipitation file settled on no copy, pruned past 33 days),
`newest` (the newest hour whose analysis was read, from either server), `scanned` (how far the
live walk has opened stamps), `since` (when the lane first looked), `open` (the stamps whose
files have not settled, or have settled while a server's copy is still to come, each file with,
per server, when it was last looked at, `tried`, and the download that last failed the check,
`bad`, its hashes and reasons, and how the file has settled so far, `cutoffs` and, for an
analysis, `hourly`, and for a file carried over from before 2026-11-03 since when, `migrated`),
`rechecked` (the newest hour whose
copies were looked at again), `reads` and `lastRead` (when a pass last read an hour); per
product
the backfill `cursor`, `oldest`, `done` and queued catch-up spans; `layout`, the `LAYOUT` the
listed days were last all written in, and `relayout`, the listed days still to be rewritten
after it changed; the day statuses; the prune stamp; `precipSwept`, the time of the last
precipitation sweep (step 3); and `healthAt`, when a ten-minute pass last advanced the failure
streak (section 5). `archive/analysis2/_meta/lease.json` is the lease of section 5, the token
of the pass that holds it with the pass's kind (`baseline` or `window`), when it was taken,
when it expires and when it was released; it is under `data/archive/`, which the Lambda's role
can write and the CDN never serves. A state written before 2026-09-30 loses its `urma` lanes and its
re-read and revision records when it is loaded, and has no `layout`, which queues every listed
day for rewriting. A state written before 2026-11-03 carries `gaps` and `precipPending`, which
join `open` when it is loaded, a gap with both its files and a pending hour with its
precipitation file; a file the archive holds stands, so a precipitation file stored short of a
place is not fetched again, and a file carried over settles on no copy for each day already
past its cut-off, since that day was final without it.
A precipitation key without an archive hour is the precipitation of an hour whose analysis
file never landed. `archive/_meta/health_analysis.json` is the lane's own failure
streak file, written through `archive.update_health` under the market lane's convention so
this lane's streaks never collide with another lane's. Pages never read `archive/`.

**`config/analysis_file_times.json`** (from 2026-11-04) holds NOAA's own publication times of
the files the lane read before it kept each server's copy, which it kept no Last-Modified for
before 2026-10-14 and no NOMADS copy for at all, as `{kind: {file_utc: {server: {"lm", "seen"}}}}`
with a precipitation file under the hour it ends, one file to a line. The engine extracted them read-only,
NOAA Open Data's earliest Last-Modified from its read log for 1,127 analysis and 1,128
precipitation files from 2026-08-29 to 2026-10-14 (and NOMADS's for the analysis of 2026-09-23 19Z,
which never reached the bucket), with no first sighting, since its read times were its own and
some of them catch-up reads, and NOMADS's Last-Modified and first sighting from its timing probe
for the 25 analysis and 26 precipitation files from 10:00 UTC on 2026-11-03 to the copies' start
on 2026-11-04, the probe having begun at 15:13 UTC, with no first sighting for the five of each
already on NOMADS when it began.
`scripts/build_analysis_file_times.py` builds it from the engine's CSV (columns `kind`, `file_utc`,
`server`, `last_modified`, `first_seen`) and refuses a row that is not one file on one server at the
top of an hour; `scripts/package_lambda.py` ships it, and `load_file_times` reads it once a process.
Only `anlFile`, `pcpFile` and the hourly contract of an hour read before the copies read it (section
1); no day's figure, count, late flag or state does, so a time after a cut-off changes what is shown
and nothing else.

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
the refresh, which want 165 s, wait for the next ten-minute pass outside the windows. The
deadline is looked at before each server's copy of each file, and with `FETCH_TRIES = 2` and
`FETCH_TIMEOUT = 20` a copy takes about 170 s at worst (an analysis's sidecar and three messages,
each timing out once), so a pass ends inside the ten minutes. Until 2026-11-03 the unit was an
hour, about 212 s at worst. Each pass keeps `RESERVE_SECONDS = 45` back from the lanes for the
rebuild and the indexes, and sets `arch.LAST_STATUS` with its `mode` (`baseline` or `window`).
Every NOAA call is `fetch_range`, `fetch_bytes` or `fetch_head` in `pipeline/gov_weather.py`
with the site's User-Agent, two tries of twenty seconds each, so the 900 s Lambda timeout is
never what stops a pass.

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

**The windowed pass** does only light work. It opens the stamps now due and looks for the files
newly due, the analysis of the hour now due and the `PCP_LIVE_HOURS = 3` newest precipitation
files (step 1), settles a file only once both copies are in, rebuilds the days they touch (step
2), and writes the state when it moved and `index.json` when a file came in. It leaves the older
open files, the settling at the cut-offs, the days past their cut-off,
the sweep, the refresh, the recheck, the backfill, the prune and the health file to the
ten-minute pass. When nothing is new it writes nothing but the lease and exits within seconds,
its requests being the lease, the state, the grid index, the copies of the files it looks for
and a fetch or two from each server of files not yet published.

**NOMADS's limit.** NOMADS blocks a client that makes more than about 120 requests a minute. A
pass makes at most `NOMADS_CALLS_PER_PASS = 40` calls to it, shared by every lane of the pass
(`NomadsBudget`), each at least `NOMADS_MIN_INTERVAL = 2` seconds after the one before, and passes
never overlap, so the lane makes at most about 30 calls a minute there, 60 requests if every call
needed its second try. An analysis copy is four calls (the sidecar and three messages), a
precipitation copy one, a file not there yet one, and a recheck one more. A call the budget has
no room for is not made. The file waits for the next pass with nothing recorded about it, a copy
cut short that way is dropped whole, and the backfill stops at the hour it is on. A normal hour
takes five or six calls of the forty.

1. **Live files.** The walk opens each stamp as its precipitation file becomes due, 14 minutes
   after its hour ends (`LOOK_MINUTES`, its window's opening), every stamp after the walk's
   cursor (the later of `newest` and `scanned`), into the state's `open` list with both its
   files, the precipitation file that ends at the stamp and the analysis of that hour, which is
   due from 44 minutes after the hour, two minutes before NOAA's earliest publication. A first
   pass starts at the present, and a cursor more than 24 hours behind jumps to the present and
   queues the skipped span for the backfill (once, and no older than the backfill reaches). Then
   each open file is looked for on each server that has not given its copy, NOMADS first, newest
   stamp first (`work_file`). A server is asked every pass for two hours after the hour
   (`RETRY_EVERY_PASS_HOURS`), then once an hour, and again at the first ten-minute pass after
   each of the file's cut-offs, and at the next pass after a download that failed the check,
   while the lane reaches the hour there. A copy (`read_copy`) is the `.idx` sidecar and a range
   request for each of TMP, WIND and GUST, or the precipitation file whole, each message hashed
   and checked (section 1). A message whose hash equals the other server's copy takes that copy's
   values, and any other is decoded at the places and, within the frame window, onto the lattice.
   The copy is written once with its frames and `grid/index.json`. The map follows the copy that
   counts, the first copy kept and NOMADS's where its bytes differ, while no cut-off of the file
   has settled and when it was published by the first one. The file then settles as far as it
   can (`settle_cuts`, section 1), for every cut-off once both copies are in, and on the
   ten-minute pass for each cut-off that has passed, on the copies in hand, an analysis's hourly
   cut among them. Every open file is
   worked on the ten-minute pass, and once the deadline is near it is settled without a read, so
   none misses the first ten-minute pass after its cut-off. Its settled record is written once it
   has settled for every cut-off and its hourly cut, and it leaves the open list once both servers' copies are kept,
   or `RECHECK_AFTER_HOURS = 26` after its hour, when every zone's cut-off has passed; so the
   second copy of a file that settled on the first is kept too, and recorded as late when it came
   after. A file that settled on no copy leaves the hour without it and goes on the state's
   `missing` (or `pmissing`) list. When a server was asked is kept only once it can matter, so a
   pass that finds nothing new changes nothing. A `.idx` or a file not there (403 or 404) is an
   *absence*. A copy that fails (a short body, a 5xx after the retries) is an *error*, and a
   download whose message fails the check is *suspect* and no copy (section 1); both are recorded
   against the product in `errors`, and that server is asked for no more files of that kind this
   pass, so an outage costs one failing read of each. `newest`
   only ever moves on the first copy of an analysis. The backfill's cursor is seeded by the first
   stamp the walk opens, read or not (on 2026-09-27 a fresh lane first looked while the newest
   hours were half an hour late, and a cursor seeded only by a read would have left the backfill
   idle until one landed). An hour's precipitation is read as soon as NOAA publishes it, without
   waiting for the next analysis, which it was read with until 2026-11-03, about half an hour
   after it landed, so the day's last accumulation, on the bucket about 17 minutes after local
   midnight, is in the day's total before the 12:45 AM cut-off. The days an hour belongs to
   resolve once their files have settled for their cut-off, whatever the lane has found by then,
   and `days_past_cutoff` rebuilds a recent place-day whose cut-off has passed while its summary is
   still provisional or absent.
2. **Days.** Rebuild every `days/` and `loc/` file that a new copy or a settled file touches (a
   UTC hour lands in one local day per zone), and on the ten-minute pass any day past its
   cut-off, from the archive, applying section 1's rules. A row reads the files of any hour that
   has begun (its analysis) or ended (its accumulation). Until 2026-11-03 it read only the hours
   the product's lag said had landed, which hid an hour's precipitation until the next analysis
   was due. The state is saved.
3. **Sweep.** Once a day (`PRECIP_SWEEP_HOURS = 24`), over the hours both walks have passed
   (from the backfill's `oldest`, at most 30 days back, to 48 hours before the live cursor,
   outside queued catch-up spans and the open list): look on both servers for every file the
   archive has nothing of, neither a settled record nor a copy, NOMADS while it may still keep
   the hour and within the pass's budget, and rebuild the days they touch. The file settles on no
   copy first, since its days were final without it, so what the sweep finds is recorded and
   flagged as late and never shown or counted. A refused file ends that product's sweep until the
   next day's; a sweep cut short by the deadline or the budget is not stamped and is due again
   next pass.
4. **Refresh.** Rewrite, newest first and `REFRESH_DAYS_PER_PASS = 6` a pass, the listed days
   that lack either CSV file and the days still in `relayout`. When `LAYOUT` changes (it was
   set to `rtma-only-2026-09-30` when URMA was dropped, `cutoff-2026-10-14` with the cut-off
   rule, `running-2026-11-03` when the place files gained `show`, `run` and the publication
   times, `copies-2026-11-04` when the raw CSV gained each server's copy and the rows the pass
   their copies became final at, and `v3-2026-11-04` when the rows took `rowShape` 3) the first pass
   queues every listed day,
   so the listed days (sixty at most) are rewritten within about two hours; a day with no archive hours
   writes nothing and leaves the queue. The running values cost about 0.3 s of computing a day of
   sixty-seven places on a laptop (0.41 s against 0.10 s without them), inside the same deadline
   checks as every other rebuild, and `rowShape` 3 about as much again (1.14 s against 0.58 s for a
   sample day, measured 2026-11-04).
5. **Recheck.** Each first copy is looked at once more `RECHECK_AFTER_HOURS = 26` after its hour,
   when every zone's cut-off has passed, by its server's Last-Modified time from a HEAD request
   (`recheck_lane`, `gov_weather.fetch_head`), `RECHECK_HOURS_PER_PASS = 4` hours a pass at most,
   NOMADS within the pass's budget. A file its server has written again since is fetched and
   hashed, not decoded, and recorded as a later replacement (`note_later`), never used, and the
   days whose raw CSV it adds to are rebuilt; a download that fails the check is no replacement
   and is reported as an error. An hour is passed (`rechecked`) only once all its copies have been
   looked at.
6. **Backfill.** With the remaining budget, walk back, newest first: a queued catch-up span first,
   then from the cursor back to 30 days ago. Each hour's two files are looked for on both servers
   (NOMADS while it keeps the hour) and kept, and each file then settles for every cut-off that has
   passed, on the copies in hand, this being the first ten-minute pass to work it; frames for the
   last seven days, point reads only before that. The days it fills were never final without it,
   since a day waits while the backfill has yet to reach any of its hours (section 1). A file
   with a day still before its cut-off, or whose other copy is still to come within a day of its
   hour, joins the open list. Once the pass's NOMADS budget is spent, or the deadline comes, within
   an hour, the walk stops at the hour it is on, unpassed and unsettled, and carries on next pass.
   The state is saved every twenty hours.
7. **Prune**, once a day: frames older than 30 days and their index entries, the state's
   lists of hours not published older than 33 days, and open stamps older than 30 days.
8. **Always**, in a `finally`. On the ten-minute pass `grid/index.json`, the health file, the
   state and `index.json` last, so a reader sees a consistent set whatever happened above (the
   health file before the state, which keeps when it was written). On the windowed pass the
   state when it moved and `index.json` when a file came in; every frame it wrote was followed
   by its grid index. Steps 3 to 7 are the ten-minute pass's.

Nothing a NOAA object does fails the pass. Every fetch and decode of a copy is guarded
inside `read_copy`; every lane call is guarded in the pass; the pass-level
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
`held`, or `unknown` when the store could not be read), the counts (reads, precipitation reads,
second copies, absences, errors, the NOMADS calls made) and `newest` per product. The printed
status adds the lists, the files left open (`waiting`) and the recheck's and the sweep's counts.

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
writes only the lease. With both servers read (2026-11-03, counted the same way) each hour writes
four copies and two settled records where it wrote an archive hour and a precipitation key, and a
second copy that lands in a later pass than the first, which NOAA Open Data's usually does,
trailing NOMADS by a couple of minutes, rebuilds the days it touches again for the raw CSV, up to
two more rebuilds an hour, about 100k more PUTs a month at most. Schema 2's first 30 days were filled on 2026-09-29 by a local run of
the lane's own `read_hour`, `write_hour` and `rebuild_days` with the reads in parallel, then
uploaded, so the live lane started with its backfill done.

## 6. The page

`analysis-resolution.html`, unlisted (`noindex`, not in `config/contracts.json`, not in
`REF`), shows version 3 of the settlement display, the prototype the owner approved on
2026-11-04. It is built from the same skeleton as
`wind-markets.html`: title, `p.sub` carrying the statement, a `.bar` with the day's CSV links,
`#pageStatus` and the newest analysis hour, the calendar and the map (`#anaPickers`), the open
place's header (`#anaHead`), `p#anaPick` (a line asking for a place, shown while none is open),
the place-day (`#locPanel`), the method prose and `p.cap#foot`. Scripts: common, data,
`js/analysis-settle.js` (the place-day, `WXSettle`), then `js/analysis.js` (the rest,
`WXAnalysis.init()`). The page computes nothing. Every value, converted value, running figure and
time it shows or saves is one the lane wrote, and the page only compares times, for the replay and
the countdowns, and places pixels.

**Calendar and map.** Both are always on the page, side by side at the top on a wide screen (the
calendar 300 px wide) and stacked on a phone, never behind a button. They replaced the day and place
buttons and their popovers on 2026-11-04.

- The calendar (`#anaCal`, a `role="grid"` table) shows the month of the open date, filled in the
  accent. Only the days in `index.days` can be chosen, and every other day is greyed,
  `aria-disabled` and out of the tab order. Each listed day carries a mark for its state from
  `index.dayStatus`: final once every place has resolved (a solid dot), provisional (a ring), under
  review when its unresolved places are all under review (a diamond), or closed on a file written
  before 2026-10-14 (a square). Choosing a date opens it for the same place. The grid has one tab
  stop. The arrows move between listed days, a week at a time up and down, Page Up and Page Down
  and the month arrows step only among the months that hold listed days, Home and End go to the
  first and last listed day of the month on view, and Enter or Space opens the day with the focus
  kept on it.
- The map (`#anaPlaceMap`) is the page's only map, on its own projection and `basemap.json`
  outlines. Every place carries a label with its value on the open date for the chosen variable, a
  pill in one of nine colours on the variable's pinned range (high 20 to 100 °F, low 10 to 90 °F,
  gust 0 to 50 mph, wind 0 to 25 mph, precipitation 0 to 1.5 in with a dry day clear), solid once
  the place's day is final and dashed while provisional, and a hollow dot marks a place with no
  value. The open place is ringed and named. A click or a tap on a label, or within 12 screen pixels
  of a place, opens it on the same date, and the pointer reads its value. The labels keep about
  10.5 px on screen and are drawn again when the map's width changes. The colours are the tokens
  `--ana-r0` to `--ana-r8` and `--ana-b0` to `--ana-b8` in all three theme blocks of `site.css`,
  the approved design's ramps, with `--ana-ink-dk` for the label on the three deepest blues.
- The box under the map (`#anaPlaceQ`) finds a place by name for the keyboard, a screen reader and
  a phone, so the map is hidden from assistive technology. It is a combobox over a list of the
  matches that opens as the reader types, a name that starts with the text first, then a word in it
  that does, then any name, state or code that holds it, each match with its value on the map. The
  arrows move through the matches, Enter opens the highlighted one, which is the first until the
  arrows move on, and Escape or the focus leaving the box closes the list.
- The variable switch (`#anaVar`) above the map is a radio group of High, Low, Gust, Wind and Precip
  with one tab stop, whose arrows, Home and End move the choice. It sets what the map labels, the
  pressed card and `var=`, and a click on a value card sets it in turn.
- A legend under the map names the variable and the day, "so far" until the day is final, and shows
  the nine colours.

State lives in the URL, as `?var=high&day=2026-09-26&loc=new-york-ny`. An address from before
2026-11-04 may carry `hour=` and the map's view (`z`, `cx`, `cy`), which went with the frames, and
the page opens it and drops them; one from before 2026-09-30 with `product=` opens on RTMA. With no
`day` in the address the page opens on `index.lastResolvedDay` (owner's request 2026-09-28), the
newest local date on which every one of the sixty-seven places has its RTMA day final and its
precipitation total resolved (`dayStatus.resolved == dayStatus.places`); an index without the field
opens on the newest day. The job keeps each day's status in its state as it rebuilds the day and
fills a status it lacks from the day's file. An RTMA day resolves within about an hour of its local
end, so the page typically opens on yesterday. The page reads `index.json`, the day files and the
place files, and no longer the grid index or the frames, which the lane still writes.

**Header** (`#anaHead`, shown once a place is open). The place's name, with its code in brackets
when the index carries one (`code`, ForecastEx's three-character code), the date, previous and next
day buttons and Close; its analysis grid point and Grid 184 point (i, j), the centre of its grid
square and its zone, from the index's location list; the statement; and how far the centre is from
the position, with the position itself and any ruling.

**The place-day** (`#anaSettle`, `js/analysis-settle.js`), in the specification's order:

- the day's state, one pill and one line: Provisional with the hours in so far and the cut-off,
  Final with the hours it resolved on, Under review when no hour was published by the cut-off
  (event review until 2026-11-04), and Closed on a file written before 2026-10-14;
- two countdowns, to the next analysis and to the next hour's precipitation, each in one of four
  states: counting down, expected now, late, and missed once the cut-off has passed. They run to the
  file's first copy, NOMADS's, which from 10:00 UTC on 3 November to 08:00 UTC on 5 November came
  first in every file both servers had a time for, a median 46 minutes after the hour for an analysis
  and 16 minutes after the hour ends for a precipitation file. NOAA Open Data's medians over the two
  weeks to 2026-11-03 were 49 and 17 minutes, and the first copy is usually in by 52 and 18 (`LAG` in
  the script). Late means the file is not in yet past the time it is usually out by, and its note
  gives that time and says the file counts if published by the cut-off. These usual times are the
  page's only expected times, and only for files not in yet. The page says "expected", never "due"
  (owner, 2026-11-05);
- five value cards, High, Low, Peak gust, Mean wind and Precipitation, each the lane's running value
  at the last counted hour in (`run`), with the hour of the extreme or the hours counted and "so
  far" until the day is final. A place under review shows no value and says no hour was published by
  the 12:45 AM cut-off. Each card is a button, the card of the variable the map shows is pressed,
  and a click on one sets that variable;
- three charts on one time axis, local midnight to midnight: temperature with the High and Low
  marked, sustained wind and gust with the running mean as a dashed line and the peak gust marked,
  and hourly precipitation bars with the running total as a step line. Each shades the hours still
  to come and draws a now line at the clock and a dashed line at the next hour to come, labelled
  "next, expected" with its usual time, left of the line where the plot ends too soon for it, and
  under each is a CSV file for each value on it;
- every hour in a table: local and UTC time, each value as the contract shows it, the hour's hourly
  contract in a few words with its full wording on hover, the running precipitation total, and under
  one header, "Analysis published", a column for NOMADS and one for NOAA Open Data (owner,
  2026-11-05). Each gives that server's own Last-Modified time for the hour's analysis once the moment
  shown reaches it, marked ", after the cut-off" when it came after the day's cut-off; before then
  "expected" with the time that server usually publishes (46 minutes after the hour for NOMADS, 49
  for NOAA Open Data), while the file can still come; "not on record" where no time was kept for it,
  NOMADS before 10:00 UTC on 3 November or a server the record has no time for on a file that is in;
  and "not published" past the cut-off. A footnote says the rounded hours can add up to a different
  figure than the total, which is the column the contract resolves on, explains the two servers and
  the hourly contract, and gives the hourly cut-off as the day's file has it;
- the replay, last: the time the day is shown as of, local and UTC, a slider, Play the day (a minute
  every 30 ms) and Latest. It is held at the foot of the screen while the day is on it, so the
  charts it moves stay in view.

**Hover.** Pointing at an hour, or tapping it on a phone, opens a box for that hour. A tap or a
click pins it until a tap elsewhere or Escape, and the arrow keys step through the hours of a
focused chart. For each value on the chart it gives the value as stored (`raw`, in K, m/s or mm),
the converted value before rounding (`x`, as the lane writes it) and the rounded value (`show`); for
temperature the hourly contract's value and state; the day so far after that hour, rounded (`run`)
and before rounding (`runX`); and the file: its name, each server's Last-Modified time
("published") and the lane's first find ("found"), or "not on record" ("not on record before 10:00
UTC on 3 Nov" for NOMADS before the timing probe's earliest file), the copy used and whether the
hour counts. In a replay a server's time after the moment shown is not given: its copy is "expected
about" that server's usual time, or past the cut-off "not published by the cut-off", and a first find
after the moment is left out. A
file published after the cut-off and counted under the rule before 14 October 2026 (`counts` and
`afterCut` both true; one real case, the precipitation hour ending 2026-09-30T07:00Z for western
places on 29 September) says "published after the cut-off, counted under the rule before 14
October 2026", never not counted.

**Hourly contract.** Each hour's temperature has its own state, from the row's `hourly`, in the
owner's wording of 2026-11-04, put plainly the same evening at his request so that a reader who has
never heard of either server can follow it, with times on the place's clock to the second, and since
2026-11-05 saying when an hour not final yet will be. Its cut-off is worded from the file's own
`hourly.cut`, "1 hour 30 minutes after the hour", so a day the lane wrote under the three hours reads
"three hours after the hour" until it is rewritten. The every-hour table's cell gives a final hour's
time and whose file made it final, "final 10:46 AM (NOMADS)" for NOMADS's publication or "final 11:30
AM (Open Data)" for NOAA Open Data's copy at the cut, the hours read before both servers were, final
at NOMADS's publication with NOAA Open Data's value, reading "(NOMADS)" with the value's copy in the
hover. An hour not final yet reads "provisional, final expected 10:46 AM, cut-off 11:30 AM", NOMADS's
usual time while it is ahead and the cut-off, which decides the hour either way, then "provisional,
cut-off 11:30 AM", and an hour to come the same without "provisional"; an hour before NOMADS's times
were kept gives the cut-off alone. "At the latest" is said only of NOAA Open Data's copy, since with
neither copy by the cut-off the hour goes to review (owner's rulings of 2026-11-05). On a live
day an hour the lane has not written yet reads the same until its cut-off. The cell's title, the
hover and the temperature CSV give the sentence. The note under the table explains the two
servers: NOAA publishes every file on NOAA Open Data and on NOMADS, the page reads both, the copies
are normally identical, the NOMADS copy counts where they differ if it was published in time, and
NOMADS's publication times are on record only from 10:00 UTC on 3 November.

- Basis `nomads` reads "final at {at}, when NOMADS published the file. The value is NOMADS's copy.",
  `at` being NOMADS's Last-Modified time, or "The value is NOAA Open Data's copy." when that is the
  copy used, as for some hours of 3 and 4 November read before both servers were.
- Basis `cutoff` reads "final at {cut}, 1 hour 30 minutes after the hour, from NOAA Open Data's copy,
  because NOMADS had not published the file by then."
- Basis `norecord` reads "final at {cut}, 1 hour 30 minutes after the hour, from NOAA Open Data's
  copy. NOMADS's publication time for this hour is not on record."
- State `review` reads "no value: neither server had published the file by {cut}, 1 hour 30 minutes
  after the hour. Under review." With basis `norecord`, a file read before both servers were, it reads
  "no value: NOAA Open Data published the file after {cut}, 1 hour 30 minutes after the hour, and
  NOMADS's time is not on record. Under review." when NOAA Open Data has a copy on record, as for
  2026-09-23 at 23Z, and "no value: neither server is on record with the file by {cut}, 1 hour 30
  minutes after the hour. Under review." when it has none.
- State `provisional` reads "provisional. It becomes final when NOMADS publishes the file, expected
  about {NOMADS's usual time}, or at the latest at its cut-off, {cut}, 1 hour 30 minutes after the
  hour, from NOAA Open Data's copy. With neither copy by then, it goes to review.", without the
  expected time once NOMADS's usual time has passed, and for an hour before NOMADS's times were kept
  "provisional. NOMADS's publication time for this hour is not on record, so it becomes final at its
  cut-off, {cut}, 1 hour 30 minutes after the hour, from NOAA Open Data's copy. With no copy by then,
  it goes to review." An hour to come reads the same without "provisional."

In a replay an hour shows final only from `at` on and provisional before it, and an hour under
review shows provisional until its cut-off. A row with an hourly state and no daily value is no data
for the charts and the cards, and the table, the hover and the CSV show its state.

**CSV files.** The temperature chart has a temperature file under it, the wind chart a wind speed
and a wind gust file, and the precipitation chart a precipitation file, each for the open place and
date and named as the prototype names them (`2026-09-28 NYC temperature.csv`, with the place's id
where the index has no code). Each has a row for every row of the place file, the whole place-day
whatever the replay shows, in the prototype's columns: `location_code, location, contract_date,
time_zone, local_time` (with its UTC offset and zone), `valid_utc` (for precipitation the end of
the hour), `file, copy_used, nodd_first_found, nodd_last_modified, nomads_first_found,
nomads_last_modified, stored_value, stored_unit, converted_unrounded, unit, rounded` (for
precipitation `rounded_for_display`) and `counted`, then in the temperature file
`hourly_resolution_value_state` and `hourly_final_at`, and last the day so far before rounding and
rounded (`high_so_far_unrounded`, `high_so_far_rounded`, `low_so_far_unrounded` and
`low_so_far_rounded`, or `mean_so_far_`, `peak_so_far_` or `total_so_far_` with `unrounded` and
`rounded`). Every value and time is the place file's own string. The page assembles the file and
saves it through a Blob link. The day's two files at every place, raw and processed, stay in the
top bar.

**Publication times.** The page never estimates one. An hour comes in, for the replay and the table,
at its file's first publication on either server that is on record: the lane's (`pub`, `pcpPub`), or
an earlier Last-Modified time in its file record (`anlFile`, `pcpFile`), which the lane fills for old
days from NOAA's records. So an hour of 3 or 4 November read before both servers were, whose `pub`
is NOAA Open Data's, comes in at NOMADS's earlier publication, as its hourly contract turns final
then. A time not on record is said to be. The replay runs from the day's local
midnight to the clock on a live day. On a day past its cut-off it runs to a quarter hour past the
latest of the cut-off, the last hour's hourly cut-off (`hourly.cut`) and any file the old rule
counted late, so the 11 PM hour shows final. A day whose counted files have no time on record is
shown as it stands, with no replay and a line saying why. A counted hour enters the day's values once
every counted hour before it has arrived, since its running values cover them. A file published
after the cut-off is shown dimmed once the cut-off has passed and never counted.

**Live days.** While the day is live and the tab is visible, the display follows the clock, and each
minute from :44 to :54 and from :14 to :20 past the hour, about when the analysis and the
precipitation land, it fetches the place file and the day file again with `?b=` and a stamp that
changes once a minute (`WXD.get`'s `bust`), so the day fills in without a reload. It does the same
each minute while a countdown reads late, for up to half an hour past the file's usual time
(`CHASE`), and for as long while an hour the file holds provisional is past its hourly cut-off, so
the lane's next pass settles it on the page. The site's CloudFront snapshot policy keeps query
strings out of its cache key (`ops/aws/template.yaml`), so the stamp reaches past the browser's cache
but not the CDN's, and a refetch can still be up to the CDN's 60-second lifetime old. Since
2026-11-03 it is no older than that, because the live files carry no `stale-while-revalidate`
(section 4); with it the first fetch after the minute could be about six minutes old.

A link such as `analysis-resolution.html?var=precip&day=2026-09-28&loc=boston-ma` opens that
place-day and, since it names a variable and a place, scrolls to that variable's chart (temperature
for the high and low, wind for the gust and mean wind).

**Older files.** A place file in `rowShape` 2 has no stored or converted values, file records or
hourly contract. The hover leaves them out and says no file time is on record, the CSV leaves them
blank, and the day replays only if its rows carry `pub` times. A file without `rowShape` is drawn as
it stands, with the summary's values, the hours as stored, no running total, no replay and a line
saying so. The lane rewrote the listed days in `rowShape` 3 within about two hours of its deploy.

Version 3 removed the day and place buttons and their popovers, the map below the day with its
hourly frames, hour stepper, cell mode and Zoom to the cell, the hypothetical ladder, the Show exact
values toggle, and every estimated publication time with its ≈ mark.

**Method note.** The conventions block of `index.json` rendered as a definition list, the sources
with their measured lags, what the map shows, and the statement that station report conventions have
no analogue here.

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
  read; frames are indexed the pass they are written; days written before URMA was dropped are rewritten a few a pass and the grid index drops
  URMA's frames without deleting them; the catch-up span is queued once and clamped; the backfill starts behind
  a late newest hour; the place file carries the precipitation in the row of the hour it
  starts; the day's precipitation runs midnight to midnight; a value the file holds exactly is
  converted exactly and the aggregate rounded once; a complete day is final at its cut-off,
  12:45 AM local time the next day, and not before, stamped with the cut-off; a short day is
  final there on the hours available and never closed; a file published after the cut-off never
  counts and is shown as late, one published before it counts (the summary goes by publication
  time, and the copy a row has is fixed when its file settles), and the day's
  last accumulation landing after the cut-off leaves the total; a place with nothing published by
  the cut-off goes to event review, through a pass, on a day other places have hours for, and a
  day with nothing read anywhere is not written; each read carries its file's Last-Modified
  time, which `fetch_bytes` and `fetch_range` report, and `fetch_head` gives it without the body;
  the day is written as raw and processed CSV; an hour whose analysis never lands still adds its
  precipitation, in the live lane and in the backfill, without counting toward the day's
  analyses, and a refused file ends a product's sweep for the day;
  `fetch_range` accepts the clamped 206 for a last message. The readers and writers of the
  archive's earlier shape, which the tests still make hours with, keep their own tests (a
  replaced precipitation file keeps its first version, a file missing from NOAA Open Data is
  read from NOMADS half an hour after it is due and not past thirteen days).
- `tests/test_analysis.py`, for both servers (2026-11-03) and the freeze (2026-11-04): which copy
  counts, case by case (`choose`), the first publication on either server and its server, and
  settling cut-off by cut-off (nothing on one copy before a cut-off or on a windowed pass, a
  cut-off's days at the first ten-minute pass after it on the copy in hand, the rest at once when
  the second copy comes, a copy read after a cut-off settled late for its days, a carried-over
  file settling on no copy); both copies kept with their times and hashes, the second taking the
  first's values without decoding where the bytes match, the hour taking the first publication
  while its values are NOMADS's, and the settled time in the raw CSV and the place file; a file
  settling once both copies are in, and one NOAA Open Data never has settling on NOMADS's at the
  first ten-minute pass after its cut-offs and looked for until a day after its hour; a file on
  neither server settling on no copy for each day at that pass, with its record written with the
  last; the two copies differing, NOMADS's counting, and the hash and value differences kept
  apart in the raw CSV; a NOMADS copy that differs and lands after the cut-off recorded and
  flagged in the raw CSV and the place file, with the archive hour, the day, the map and every
  value unchanged; a NOMADS copy that differs before the cut-off redrawing the map; a day staying
  provisional past its cut-off until its files settle, a windowed pass leaving it so, then final
  and unchanged by a copy found later; a later replacement found by the recheck recorded beside
  the first copy, added to the raw CSV and never used; the NOMADS budget's cap and spacing, a call
  it has no room for neither made nor recorded, and the backfill stopping on it, unpassed, and
  carrying on; no copy started once the pass is inside its reserve; a download that fails the
  check no copy until it comes whole, the same failing bytes twice kept as NOAA's own file with
  the message refused and counting per file, and a message with no value at any place treated
  the same; the daily sweep recording what it finds and never using it; and a state from before
  2026-11-03 carrying its gaps and pending precipitation into the open list, a carried-over file
  settling on no copy for days already final.
- `tests/test_analysis.py`, for a newly listed day (2026-11-04): each place with no hour of it gets
  a file with no rows as the day is listed, its first hour replaces it, and a later rebuild of the
  listed day writes no more of them.
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
- `tests/test_analysis.py`, for `rowShape` 3 and the hourly contract (2026-11-04): the contract
  final at NOMADS's Last-Modified once its copy is in, before its cut settles; final at the cut
  from the first ten-minute pass on NOAA Open Data's copy alone, and provisional until then; under
  review with neither copy, the row with no value carrying the contract and its files alone; a
  NOMADS copy read after the hourly cut settled flagged and never used, though published before the
  cut, while the day takes it by its own rules; at New York's 23:00 the hourly cut at 12:30 AM coming
  before the day's 12:45 AM cut-off, and a file between them counting for New York's and Chicago's
  days and not the contract, and one after an afternoon hour's cut counting for the day and not the
  contract; the hourly cut settling apart from the days', on its own ten-minute pass, on no copy for
  a carried-over file, never on a windowed pass with one copy and never for a precipitation file, the
  record waiting for the day's cut-off at Los Angeles alone and for the later zones with three,
  and the file leaving the open list as before; the exact strings from the stored values, the three
  half ties and a precipitation figure whose cut differs from its rounding among them, finer stored
  values written exactly, and `runX` on the hours `run` counts, a late hour adding nothing; files read
  before the copies with and without the table's times (NOAA Open Data's shown, NOMADS's by the cut
  making the contract final there, NOAA Open Data's after the cut a review, the record's own
  Last-Modified standing over the table's), the day the same either way; a table time after the
  cut-off, counted under the earlier rule, changing only the times shown and `afterCut`; a full day
  of three zones run against `tests/fixtures/analysis_v2_day.json.gz`, which the lane wrote from the
  same scenario before the change, its day file, CSV files and place files byte-identical once the
  new fields are taken away, and its place files in `rowShape` 3; every `rowShape` 3 field of every
  fixture and sample place-day agreeing with the row's figures of before; the file names against
  the URLs; the table builder and the Lambda package; and a rebuilt location list keeping each
  place's ForecastEx code, none missing or shared.
- `tests/test_analysis.py`, for the windowed passes (2026-11-03): an hour's precipitation is read
  as soon as it lands, before its analysis is due, and is in the place file and the day at once,
  and the analysis read later does not fetch it again; New York's last accumulation of a day is
  in before the 12:45 AM cut-off and the day resolves on it by the same rules; a windowed pass
  with nothing new writes nothing and fetches only the files newly due; the analysis is
  looked for from :44; a windowed pass leaves the older open files, the backfill, the sweep, the
  refresh and the prune to the ten-minute pass, which does them; a pass
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
- `scripts/verify.py`: the page in the `pages` sweep, and, with every read that depends on drawing
  polled for first, that the page opens on the newest fully resolved day with the later days marked
  provisional, or on the newest day when the index lacks the field; the day's CSV links fetch the
  raw and processed files; the calendar and the map are on the page with no day or place button,
  popover, second map, ladder or exact box, side by side at 1280 px and stacked at 375 px with a
  16 px gutter, above the place's header and its day, with no sideways scroll; the map labels every
  place with the day file's value for the chosen variable in the scheme's own colours, and a day with
  no file draws every place hollow; the calendar marks each listed day's state, greys the rest, steps
  only through months with listed days, walks by keyboard and opens a date for the same place; a
  place chosen on the map or in the box opens on the same date, and the box's list narrows, steps
  and closes on Escape or when the focus leaves; the switch, a value card and a link's `var=` set the
  map's variable and the card; the header gives both grid points, the centre and the zone; the page
  offers no product choice, never says URMA (an old `product=urma` address opens on RTMA) and shows
  no price; a short day reads provisional with its count, a place under review says so on every card,
  an unreadable file is reported as unreadable, and the 503 degradation shows "No data". For the
  settlement display, on the samples' final New York day: the five cards carry the summary's values;
  the hour table gives each hour's publication time on each server under one header and its hourly
  contract, and ends its running total on the day's; each chart's hover gives the lane's stored,
  converted and rounded values, the day so far and both servers' times or "not on record"; each of the
  four CSV files has its header, 24 rows and every value and time the place file's own string; the
  replay ends a quarter hour past the later of the cut-off and the last hour's hourly cut-off with
  the 11 PM hour final and provisional before its own cut, moves the cards to the lane's running values
  for a minute at the files' own times, shows each hourly contract as it stood and when each hour to
  come will be final, and Latest and Play the day work; `var=` opens its chart; and a `rowShape` 2 file with no times and a file without `rowShape`
  still draw, with no replay. On a `rowShape` 3 file made by hand from the samples
  (`ana_row_shape3`): each basis's wording of the hourly contract, both servers' found and published
  times, a file counted under the rule before 14 October, an hour under review before and after its
  cut-off, and an hour NOAA Open Data published after its hourly cut-off with no NOMADS copy on
  record, in the table, the hover and the CSV; in a replay each server's column filling at its own
  time, and an hour read before both servers were coming in at NOMADS's time; and, through the
  script's own functions, an hour of 5 November with NOMADS's usual time ahead, past and final, and a
  file written under the three hours. With the clock fixed on the samples' live day:
  the three charts share one axis and one now line, with the hours to come shaded; the countdowns pass
  through counting down, expected now, late and missed; a refetch inside an arrival window carries `?b=`
  and fills the day in; a late accumulation's note gives NOAA's usual time and the cut-off and
  nothing of the next analysis; outside the windows a late countdown keeps the place file fetched
  each minute and the day fills in, and past half an hour late nothing is fetched. No ≈ or estimated
  time appears anywhere and the page raises no script error. Fixtures under
  `samples/snapshots/analysis2/` come from a real local run of the job, rebuilt in `rowShape` 3 on
  2026-11-04 and under the hourly cut-off of 2026-11-05 by the lane's own code (their README says
  how).
- `scripts/scrub.py` before push; the page's `<meta name="description">` under 200 characters.
