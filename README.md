# Weather tools site

Live displays of US government weather data for the stations that daily temperature contracts
settle on: observed temperature against the forecasts that were standing before the day began, a
national map, a forecast scorecard, and hurricane tracking. A Python pipeline pulls the feeds on a
schedule, keeps an append-only archive of every forecast cycle, and writes small JSON snapshots.
The site is static and reads only those snapshots. Two build targets come from one codebase: the
full `standalone` site and a single-chart `embed` for an iframe.

Everything runs on a clean checkout with no cloud account. Deployment is a separate, documented
step (`DEPLOY.md`).

## Run it locally

    python3 -m unittest discover -s tests          # no network
    python3 -m pipeline.run --job archive          # one archive pass into ./data (hits the .gov feeds)
    python3 scripts/scrub.py                       # refuse to publish strings that must stay private

Python 3.9 or later, standard library only. `boto3` is needed only when the `s3` storage backend
is configured.

## What the archive job stores

`pipeline/archive.py` records, through `pipeline/storage.py`, one object per forecast issuance per
station and one file per UTC day of observations:

| Key | Source | Why |
| --- | --- | --- |
| `archive/{STATION}/hourly_{cycle}.json.gz` | api.weather.gov hourly forecast, raw body | the forecast as it stood, keyed by its own `updateTime` |
| `archive/{STATION}/daily_{cycle}.json.gz` | api.weather.gov day/night forecast | the official NWS high and low |
| `archive/{STATION}/nbh_{cycle}.txt.gz` | NBM hourly bulletin block (NOAA Open Data) | second hourly series, 25 h |
| `archive/{STATION}/nbs_{cycle}.txt.gz` | NBM short-range block | the blend's daily max and min (`TXN`) |
| `archive/{STATION}/lamp_{cycle}.txt.gz` | LAMP hourly block (NOMADS, the :30 run) | same-day trace, 25 h |
| `archive/{STATION}/mav_{cycle}.txt.gz` | GFS MOS MAV block (NOMADS) | independent daily max and min (`N/X`) |
| `archive/obs/{YYYYMMDD}.json.gz` | aviationweather.gov METAR rows, raw | observations keyed by `obsTime`; corrections kept |
| `archive/_meta/grids.json`, `_done/`, `_runs/` | | grid cache, cycle markers, one record per pass |

Nothing is written twice. A pass that finds every cycle already stored does nothing, which also
makes a retried scheduled run harmless. The observation day files are the one exception: they are
re-read and upserted each pass so that a corrected report (`COR`) replaces the original under the
same `obsTime` while the file keeps what it superseded.

Why the archive comes first: no public endpoint returns an earlier forecast, NOMADS keeps NBM text
for about two days, and data from days when the job was not running cannot be recovered.

## Configuration

`config/site.json`, overridden by environment variables (and by `pipeline.run` flags, which set
the same variables):

| Setting | Env | Meaning |
| --- | --- | --- |
| `storage.backend` | `WX_STORAGE_BACKEND` | `local` (a directory) or `s3` (any S3-compatible bucket) |
| `storage.root` | `WX_STORAGE_ROOT` | local backend: the data directory |
| `storage.bucket`, `.endpoint`, `.prefix`, `.region` | `WX_STORAGE_BUCKET` … | s3 backend; set `endpoint` for Cloudflare R2, leave empty for Amazon S3 |
| `user_agent` | `WX_USER_AGENT` | sent to every government endpoint; api.weather.gov returns 403 without one |
| `domain` | `WX_DOMAIN` | the site's domain |
| `cadence_minutes.*` | | how often each job runs: obs 10, forecast 30, archive 30, hurricane 30 |
| `sources.*` | | switch NBM, LAMP, MAV or the observation record off |
| `market_overlay` | | `placeholder` or `off`, per build target |
| `disclosure` | | the affiliation text shown in the footer and about page |

Two decode conventions are top-level constants in `pipeline/gov_weather.py`, never buried in
parsing logic: `TEMP_SOURCE` (`remarks` tenths or `body` whole degrees) and `INCLUDE_SPECI`.
Every observation file stamps the convention it was decoded with.

## Seeding the archive

An archive written by the earlier reference archiver can be imported once:

    python3 scripts/seed_archive.py /path/to/archive [--backend s3 --bucket ...]

Files are scanned with the scrub needles before they are written; nothing is overwritten.

## Layout

    config/        site.json, cities.json
    pipeline/      gov_weather.py (every government call), archive.py, storage.py, config.py,
                   run.py (command line), handler.py (the only vendor-specific file: AWS Lambda)
    scripts/       scrub.py, seed_archive.py
    site/          the static frontend (later tasks)
    samples/       snapshots for offline local mode (later tasks)
    tests/         unittest, no network

## Data sources and licensing

All weather data is US government and in the public domain; see `NOTICE`. api.weather.gov
requires a User-Agent naming the application with a contact address, publishes an appropriate-use
policy, and blocks addresses that affect its service, which is why all fetching is server side on
a fixed cadence and the browser reads only from this site's own storage. Anything smoothed,
interpolated or computed here is labelled as derived, not as a National Weather Service product.
