# Fixtures for the analysis lane, schema 2

The output of a real local run of the lane's own code against NOAA Open Data and NOMADS on
2026-09-29 (a fresh local root; `read_hour`, `write_hour`, `write_precip_only` and
`rebuild_days` from `pipeline/analysis.py`, with the reads in a thread pool, for the thirty
days to 20:09 UTC, 1,434 product-hours, no absent hours and no errors), trimmed so the samples
tree stays small. Nothing here is invented. Every value is the job's own read of the RTMA
files at the sixty-seven settlement locations of `config/analysis_locations.json`.

What was kept. `index.json` with its `days` list cut to the newest six local days, 2026-09-24
to 2026-09-29, and its `dayStatus` cut to match; every `days/` and `loc/` file of those six
days (2026-09-29 is the live day, and 2026-09-24 to 2026-09-28 are final with their
precipitation resolved at all sixty-seven places, so `lastResolvedDay` is 2026-09-28); and, per
variable, the newest four frames the run had made (16 to 19 UTC on 2026-09-29), with
`grid/index.json` rewritten to list only those. Every frame carries `windows` for all
sixty-seven places.

One thing in the data is NOAA's, not the job's. `rtma2p5.t19z` of 2026-09-23 is not on NOAA
Open Data; the run read it from NOMADS (`source: "nomads"` in its archive document), so every
place's RTMA 2026-09-23 is 24 of 24 hours. That day is outside the six kept here.

The values follow schema 2 (docs/analysis.md): RTMA resolves, the archive keeps kelvin,
metres per second and millimetres, the day's aggregate is
rounded once with the exact figure cut to a thousandth (precipitation a ten-thousandth of an
inch), and each row's precipitation is the accumulation over the hour that starts at its stamp.

`scripts/verify.py` reads the day and the hours it drives from these index files rather than
from dates written into the script, and finds the frames it drives cell mode on by looking for
`windows`, so a later re-run of the job can replace this tree without touching the checks, as
long as some day has a complete New York RTMA entry, some selectable day lists two RTMA
temperature frames, and those frames carry windows.

Later on 2026-09-29 the owner set the missing-hour rule (a day resolves on the hours available once
every other file is known not published) and asked for the day's values as CSV. The kept days were
rebuilt from the run's own archive with that code, which added `finalAt` to each RTMA entry (null on
these days, which were final before the field existed) and wrote `csv/<day>-raw.csv` and
`csv/<day>-processed.csv` for each; no value changed.

On 2026-09-30 the owner dropped URMA from the lane and the page. The run's own archive was
rewritten with that code (`load_state`, then `refresh_days` over every listed day, then the
indexes, at the run's time of 2026-09-29 20:20 UTC): the day and place files lost their `urma`
entries and rows and each precipitation total its `revised` field, the CSV files lost their
`product` column, the index lost `sources.urma`, and the four URMA frames per variable left the
tree with their `grid/index.json` entries. Every RTMA value of the 2,077 place-days of the run is
unchanged.

On 2026-11-03 the place files gained each hour's value as the contract shows it, the day's values so
far and the files' publication times, for the settlement display. The run's archive is not in this
repository, so it was rebuilt from what is: the six days' raw CSV files carry every place and hour in
the archive's own stored numbers (kelvin, metres per second and millimetres, with their sources), and
those were written back as archive hours and precipitation keys in a scratch local store. The lane's own
`refresh_days` then rewrote the six listed days there, as a pass does after a `LAYOUT` change, and
`write_index` wrote the index, both at the run's time of 2026-09-29 20:20 UTC, and the rewritten `days/`,
`loc/` and `csv/` files and `index.json` replaced these; `grid/` is unchanged. A second rebuild from the
rewritten CSV files changes no file. No value changed: every high, low, gust, wind and precipitation
figure, exact figure, hour and count of the 402 place-days is the same. The days did take on the layout
of 2026-10-14, which they had never been rewritten in: a final day's `finalAt` and its precipitation's
`resolvedAt` are now its cut-off, every entry carries `review`, the raw CSV files carry the two
publication-time columns (empty, since these files were read before 2026-10-14), and the index carries
the current conventions and `review` counts. The files predate the publication times, so the page
replays these days at the usual lags, marked ≈, and its live features run on them only under a fixed
clock, which is how `scripts/verify.py` drives them (2026-09-29 is the live day, its hours read to 19
UTC).

On 2026-11-04 the place files moved to `rowShape` 3 for version 3 of the settlement display (the owner's
approval of display v3): each row with a value gained its stored values as strings (`raw`), its values
converted before rounding and the day so far before rounding as exact strings (`x`, `runX`), both files
with each server's times (`anlFile`, `pcpFile`) and the hourly temperature contract of its hour
(`hourly`). The tree was rebuilt the way it was on 2026-11-03. The six days' raw CSV files were written
back as archive hours and precipitation keys of the earlier shape in a scratch local store, and the
lane's own `rebuild_days` rebuilt the six days there at the run's time of 2026-09-29 20:20 UTC, with the
table `config/analysis_file_times.json`. The day files and the processed CSV files are byte for byte as
they were, and the raw CSV files keep every column and value they had. Every place file is the same once
the new fields are taken away, apart from two the lane has written on every row since 2026-11-03, which
these files predated: `pubSrc` and `pcpPubSrc`, each the one server its file was read from (`nodd`).
The raw CSV files gained the lane's columns for each server's copy in the same way, empty for files read
from one server. The new fields were checked against the prototype's own integer arithmetic, from the raw
CSV files and the engine's table, at all 9,046 rows, and a second rebuild from the rewritten CSV files
changes no file. `index.json` changed only in its conventions, where `hourlyContract` is new and `provisional`
and `missing` take the lane's current text.

The files' times are NOAA Open Data's own Last-Modified times from the engine's table, for every file of
the six days; `seen` is null throughout, since the engine kept no first sighting before 14 October, and
`pub` and `pcpPub` stay absent, as the lane counted these files without them. No file here was published
after a cut-off of its day. Every hour's hourly contract is final at its cut, three hours after the
hour, on the copy read (basis `norecord`, since no NOMADS copy is on record for these files), except the
hours of 18 and 19 UTC on 2026-09-29, whose cut came after the run's time and which are provisional. The
precipitation file of 2026-09-29's 19 UTC row was not read by the run and has no times.
