# Test fixtures

Small files the offline tests read. Every number below was produced with
eccodes 2.49.0 on 2026-09-27 so a reader can reproduce it; eccodes is never
imported by the pipeline or the tests, only by whoever checks the fixtures.

## rtma2p5_t10z_tmp_2m_subgrid.grb2 (43,484 bytes)

One GRIB2 message, simple packing (template 5.0), no bitmap. The 2 m
temperature of the RTMA analysis valid 2026-09-27 10:00 UTC, cut to a
192 by 164 subgrid of the wexp grid over the New York and Boston area by the
NOMADS grib filter on 2026-09-27
(`https://nomads.ncep.noaa.gov/cgi-bin/filter_rtma2p5.pl`, directory
`/rtma2p5.20260927`, file `rtma2p5.t10z.2dvaranl_ndfd.grb2_wexp`, variable
TMP at 2 m above ground, subregion). The box typed into the form was not
kept; the cut that came back is recorded in the message itself, first grid
point 40.069769 N, 284.429957 E, and the tests read it from section 3. The
same field on the full grid is message 3 of
`https://noaa-rtma-pds.s3.amazonaws.com/rtma2p5.20260927/rtma2p5.t10z.2dvaranl_ndfd.grb2_wexp`.

SHA-256 `291065f2cc6a6fceb0ec8af902a160fc19e1b42a6290c4d085e9ddbc1ca67a98`.

Section 5 as eccodes reads it has bitsPerValue 11, referenceValue
28100.998046875, binaryScaleFactor 0, decimalScaleFactor 2 and numberOfValues
31488. The reference value is a single precision float, which is why the
values below end in ...998046875 rather than a round hundredth of a kelvin.

Values in kelvin, `codes_get_values` indexed by k = j * 192 + i, and the
cell `codes_grib_find_nearest` picks for each airport.

| cell | i | j | k | value |
|---|---|---|---|---|
| first grid point | 0 | 0 | 0 | 286.03998046875 |
| KLGA (40.7792 N, 73.8800 W) | 53 | 41 | 7925 | 288.35998046875 |
| KJFK (40.6398 N, 73.7789 W) | 58 | 35 | 6778 | 288.31998046875003 |
| KBOS (42.3606 N, 71.0106 W) | 138 | 128 | 24714 | 287.73998046875 |
| last grid point | 191 | 163 | 31487 | 288.09998046875 |

Minimum 281.00998046875003, maximum 296.76998046875 over the 31,488 points.
The KLGA cell's centre is 40.787261792 N, 73.880520081 W, 0.898 km from the
airport.

## Complex packing

No complex-packed file is checked in. The smallest real one in hand, the
RTMA hourly precipitation `rtma2p5.2026092710.pcp.184.grb2`, is 510 KB, too
large for a fixture. `tests/test_grib2.py` instead carries a small GRIB2
writer and builds its own messages on a 7 by 5 grid, templates 5.0, 5.2 and
5.3 (spatial differencing of orders 1 and 2), with and without a bitmap and
with missing value management 1. The writer was checked on 2026-09-27 by
handing its messages to eccodes (`codes_new_from_message` then
`codes_get_values`): eccodes read every one as the intended packing type
(`grid_simple`, `grid_complex`, `grid_complex_spatial_differencing`) and
returned the intended values at every point, with the bitmap gaps and the
managed missing values reported as its missing value. The decoder was also
checked against eccodes on the real files that day, bit for bit, on the TMP,
WIND and GUST messages of the 10 UTC RTMA analysis, the RTMA precipitation
file above, and two URMA precipitation files; those numbers are in the
session notes rather than here because the files are not checked in.

## obs_kblm_20260927.json (24,052 bytes)

KBLM's rows in this site's observation archive, the UTC-day files
`archive/obs/20260927.json.gz` and `archive/obs/20260928.json.gz` as stored on
2026-09-28 (the second file as updated at 13:20:47Z), keyed `KBLM|{obsTime}`
under each UTC day. The rows are aviationweather.gov's METAR API output
(`format=json`) with only the fields the observation job reads kept: icaoId,
obsTime, metarType, rawOb, temp, dewp, wdir, wspd, wgst, cover and temp_source.

The 27 September file holds 58 rows and the 28 September file 37, the last at
12:56Z. KBLM's local day of 27 September (America/New_York, 04:00Z to 04:00Z)
holds 64 reports, 51 of them with no temperature group. The largest value
across the wind speed and gust columns is the 13:25Z special report,
`SPECI KBLM 271325Z AUTO 03029G39KT`, 39 kt or 45 mph. Weather Underground's
Daily Observations table for KBLM on 2026-09-27, read on 2026-09-28, shows the
same maximum, 45 mph at 9:25 AM, in a row with no temperature. The largest
value among the 13 reports that carry a temperature is the 14:52Z special
report's 37 kt gust, 43 mph.

SHA-256 `267849e595616217a48e0473b5250a77e4b529ad313d1d07012087a327834b3b`.
