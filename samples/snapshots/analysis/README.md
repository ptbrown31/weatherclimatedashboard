# Fixtures for the analysis lane

The output of a real local run of `pipeline/analysis.py` against NOAA Open Data
(`python3 -m pipeline.run --job analysis` into a fresh scratch local root, three
passes of the 330 s cap on 2026-09-28 from 00:23 to 00:38 UTC, 288, 288 and
286 s, no errors), trimmed so the samples tree stays small. Nothing here is
invented. Every value is the job's own read of the RTMA and URMA files; the
decoders were checked against eccodes on the Mac (docs/analysis.md section 3).

What was kept. `index.json` with its `days` list cut to the newest six local
days the run had reached, 2026-09-22 to 2026-09-27 (the backfill had walked
to 2026-09-22 12 UTC when the third pass ended, so 2026-09-22 holds a few
hours per product and 2026-09-23 has RTMA at 23 of 24 hours); every `days/`
and `loc/` file of those six days (2026-09-27 is the live day, and 2026-09-24
to 2026-09-26 are complete for both products at all fifty places with the URMA
precipitation resolved); and, per product and variable, the newest four frames
the run had made (RTMA 20 to 23 UTC and URMA 14 to 17 UTC on 2026-09-27), with
`grid/index.json` rewritten to list only those. A frame is a quarter to a third
of a megabyte, which is why the rest were left behind.

Every frame here carries `windows`, the job's real output of docs/analysis.md
section 4: for each of the fifty places the 441 true 2.5 km values of the
21 by 21 cells round its resolving cell, on the frame's scale, `null` where the
cell is missing from the file's bitmap. The `cell.wexpPx`, `wexpBox`,
`wexpBasis`, `g184Px`, `g184Box` and `g184Basis` keys of every location in
`index.json` are what `scripts/build_analysis_grid.py` wrote into
`config/analysis_locations.json`, copied by the job as it copies the other
location keys. Measured on these files, the windows add about 91 KB to a
temperature frame (347 KB against 256 KB without), 69 KB to wind, 76 KB to
gust and 47 KB to precipitation. The hand-patched previews that stood in for
both before the job wrote them are gone.

One patch since that run. `scripts/build_analysis_grid.py` now writes `px` and
`py` to three decimals (to a tenth the Census dot the page draws at them
crossed the outlined resolving cell at Los Angeles, Austin and Tulsa), and the
fifty locations in `index.json` were patched by hand on 2026-09-27 from the
regenerated `config/analysis_locations.json` so the fixture matches what the
next live pass writes. Only `px` and `py` changed; every `cell` key was already
equal. The job was not re-run and no frame, day or loc file was touched.

Three things in the data are NOAA's, not the job's. `rtma2p5.t19z` of
2026-09-23 is not on the bucket, so every place's RTMA 2026-09-23 is 23 of 24
hours and closed incomplete. The URMA precipitation files for 14 to 17 UTC on
2026-09-27 had the western River Forecast Center region absent from their
bitmap through the run, so fourteen western places carry `null` window cells
(4,966 of the 22,050 per frame) and no precipitation for those hours yet
(`precip.hours` short of `hours` in the 2026-09-27 files) while the job keeps
refetching them. And `urma2p5.t17z` of 2026-09-27 was the newest URMA file on
the bucket at the run, about seven hours behind the clock, which is the
product's usual lag.

The hourly precipitation in the `loc/` files is the stored ten-thousandth of an
inch (owner's decision 2026-09-27), so the hour table adds up to the day's exact
total.

`scripts/verify.py` reads the day and the hours it drives from these index
files rather than from dates written into the script, and finds the frame it
drives cell mode on by looking for `windows`, so a later re-run of the job can
replace this tree without touching the checks, as long as some day has a
complete New York URMA entry, some selectable day lists two URMA temperature
frames, and one of those frames carries windows.
