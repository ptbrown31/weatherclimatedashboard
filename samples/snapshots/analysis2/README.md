# Fixtures for the analysis lane, schema 2

The output of a real local run of the lane's own code against NOAA Open Data and NOMADS on
2026-09-29 (a fresh local root; `read_hour`, `write_hour`, `write_precip_only` and
`rebuild_days` from `pipeline/analysis.py`, with the reads in a thread pool, for the thirty
days to 20:09 UTC, 1,434 product-hours, no absent hours and no errors), trimmed so the samples
tree stays small. Nothing here is invented. Every value is the job's own read of the RTMA and
URMA files at the sixty-seven settlement locations of `config/analysis_locations.json`.

What was kept. `index.json` with its `days` list cut to the newest six local days, 2026-09-24
to 2026-09-29, and its `dayStatus` cut to match; every `days/` and `loc/` file of those six
days (2026-09-29 is the live day, and 2026-09-24 to 2026-09-28 are final with their
precipitation resolved at all sixty-seven places, so `lastResolvedDay` is 2026-09-28); and, per
product and variable, the newest four frames the run had made (RTMA 16 to 19 UTC and URMA 10
to 13 UTC on 2026-09-29), with `grid/index.json` rewritten to list only those. Every frame
carries `windows` for all sixty-seven places.

One thing in the data is NOAA's, not the job's. `rtma2p5.t19z` of 2026-09-23 is not on NOAA
Open Data; the run read it from NOMADS (`source: "nomads"` in its archive document), so every
place's RTMA 2026-09-23 is 24 of 24 hours. That day is outside the six kept here.

The values follow schema 2 (docs/analysis.md): RTMA resolves and URMA is shown for
comparison, the archive keeps kelvin, metres per second and millimetres, the day's aggregate is
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
