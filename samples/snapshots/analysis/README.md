# Fixtures for the analysis lane

The output of a real local run of `pipeline/analysis.py` against NOAA Open Data
(`python3 -m pipeline.run --job analysis` into a fresh scratch local root, six
passes of the default 480 s budget on 2026-09-27 from 21:27 to 22:11 UTC, no
errors), trimmed so the samples tree stays small. Nothing here is invented.
Every value is the job's own read of the RTMA and URMA files; the decoders were
checked against eccodes on the Mac (docs/analysis.md section 3).

What was kept. `index.json` with its `days` list cut to the newest six local
days, 2026-09-22 to 2026-09-27 (the run backfilled twenty, which made the tree
15 MB); every `days/` and `loc/` file of those six days (2026-09-27 is the live
day, and 2026-09-25 and 2026-09-26 are complete for both products at all fifty
places with the URMA precipitation resolved); and, per product and variable,
the newest four frames the run had made (RTMA 18 to 21 UTC and URMA 12 to
15 UTC on 2026-09-27), with `grid/index.json` rewritten to list only those. A
frame is about a quarter of a megabyte, which is why the rest were left behind.

Two things in the data are NOAA's, not the job's. `rtma2p5.t19z` of 2026-09-23
is not on the bucket, so every place's RTMA 2026-09-23 is 23 of 24 hours and
closed incomplete. The URMA precipitation files for 13 to 15 UTC on 2026-09-27
had the western River Forecast Center region absent from their bitmap through
the whole run, so the eleven west-coast places carry no precipitation for those
hours yet (`precip.hours` short of `hours` in the 2026-09-27 files) while the
job keeps refetching them.

The hourly precipitation in the `loc/` files is the stored ten-thousandth of an
inch (owner's decision 2026-09-27), so the hour table adds up to the day's exact
total.

`scripts/verify.py` reads the day and the hours it drives from these index
files rather than from dates written into the script, so a later re-run of the
job can replace this tree without touching the checks, as long as some day has
a complete New York URMA entry and some selectable day lists two URMA
temperature frames.
