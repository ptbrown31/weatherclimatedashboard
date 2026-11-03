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

## hrrr_t13z_f06_tmp_2m_subgrid_c3.grb2 (1,218 bytes) and hrrr_t13z_f06_tmp_2m_subgrid_c3o2.grb2 (1,321 bytes)

The HRRR 2 m temperature of the 13Z run of 2026-10-16 at forecast hour 6, cut to a 33 by 35 subgrid over Los
Angeles by the NOMADS grib filter on 2026-10-16 (`https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl`,
directory `/hrrr.20261016/conus`, file `hrrr.t13z.wrfsfcf06.grib2`, variable TMP at 2 m above ground, subregion
33.6 to 34.4 N, 118.8 to 117.9 W). The filter returned the cut in simple packing, and eccodes 2.49.0 repacked it
with `packingType = grid_complex_spatial_differencing`, once with the spatial differencing order eccodes chose (1)
and once with `orderOfSpatialDifferencing = 2` set before the values were packed again, which is how NCEP packs the
HRRR's surface fields. The two files therefore test pipeline/grib2.py against messages packed by another encoder.
First grid point 33.467365 N, 241.267677 E, reference value 287.5699157714844, binary scale factor -4, decimal
scale factor 0, 9 bits per value, 1,155 values, resolution and component flags with bit 5 set (winds relative to
the grid, as in every HRRR message).

SHA-256 `0da52ca7f5549f44a0df7c144f4ba1b7f2e5f065e6fa0d5aff40111df033ecf3` (order 1) and
`318ecb9d9fd97fb38a5dc70500a93b48bea51f4824d9b27a9329ece553661283` (order 2).

Values in kelvin, `codes_get_values` indexed by k = j * 33 + i, the same in both files:

| k | value |
|---|---|
| 0 | 295.8199157714844 |
| 1 | 295.6949157714844 |
| 2 | 295.6324157714844 |
| 17 | 298.8199157714844 |
| 500 | 297.1324157714844 |
| 577 | 308.6949157714844 |
| 1154 | 298.5699157714844 |

Minimum 287.5699157714844, maximum 309.3199157714844.

## ndfd_maxt_conus_cut_scan80.grb2 (677 bytes) and ndfd_maxt_conus_cut_scan80_full.grb2 (1,209 bytes)

Two 40 by 30 cuts of NDFD's CONUS daytime maximum temperature for the 12-hour period ending 2026-10-21 00Z, from
the issuance with reference time 2026-10-20 11Z (`https://noaa-ndfd-pds.s3.amazonaws.com/opnl/AR.conus/VP.001-003/ds.maxt.bin`,
the first of its three messages). NDFD writes grid 184 with every second row running east to west (scanning mode
80) and packs it with spatial differencing of order 2 and managed missing values. eccodes 2.49.0 cloned the message,
set the cut's size and first grid point, and packed the cut's values again in that row order; the offshore cut keeps
`missingValueManagementUsed = 1` (316 of its 1,200 points are missing), the inland one has no missing values and
was packed with none. Offshore: cell (21, 894) of the full grid, first grid point 40.067909 N, 234.032035 E.
Inland, across Los Angeles: cell (207, 556), first grid point 33.509829 N, 241.014633 E. Values in kelvin, read by
eccodes with its odd rows turned round so k = j * 40 + i; tests/test_grib2.py checks the sums and the odd-row
probes that differ from their mirror image.

SHA-256 `a12dfc6c844458f9c24c10e8e63b6e8c83e854c4ac35c4a7486b135b838c85f9` (offshore) and `a540f744fda4406b648cda6599bb992ca70043f6f3cef1d1118beb7a3c7f9e2b` (inland).

## lzw_61x37.gif (2,439 bytes) and lzw_61x37.pixels (2,257 bytes)

A 61 by 37 palette image written by PIL 11.3.0's GIF encoder, whose LZW codes grow past nine bits as its table
fills, so tests/test_floater.py checks pipeline/floater.py's decoder against another encoder. The pixels file is
the image's palette indices as PIL reads them back, one byte each, rows from the top.

## nhc_al092026_5day_010.zip (31,945 bytes)

NHC's five-day package for Hurricane Isaias, advisory 10 (1000 PM CDT Thu Oct 08 2026, synoptic time 00Z 9 October),
as its GIS archive serves it at https://www.nhc.noaa.gov/gis/forecast/archive/al092026_5day_010.zip: the forecast
points, track and cone as shapefiles with their dBASE tables, and the watch and warning lines. A US government work.
tests/test_advisories.py reads it through pipeline/advisories.py, and edits its tables' advisory number and storm name
to stand in for other advisories.

SHA-256 `380c6a4138226e4697c5245b6aa14d2c48180acc1ee8a3593564efd524cd3674`.
