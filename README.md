# Weather tools site

Live displays of US government weather data for the stations that daily temperature contracts
settle on: observed temperature against the forecasts that were standing before the day began, a
national map, a forecast scorecard, and hurricane tracking. A Python pipeline pulls the feeds on a
schedule, keeps an append-only archive of every forecast cycle, and writes small JSON snapshots.
The site is static and reads only those snapshots. Two build targets come from one codebase: the
full `standalone` site and a single-chart `embed` for an iframe.

Everything runs on a clean checkout with no cloud account. Deployment is a separate, documented
step (`DEPLOY.md`, added with the deployment templates).

## Run it locally

    python3 -m unittest discover -s tests          # no network
    python3 scripts/build_assets.py                # project the map geometry once (outputs are committed)
    python3 -m pipeline.run --job all              # every job once (archive, forecast, obs, hurricane, quotes, reask, daily) into ./data
    python3 scripts/scrub.py                       # refuse to publish strings that must stay private

Python 3.9 or later, standard library only. `boto3` is needed only when the `s3` storage backend
is configured. The scrub reads a second needle list from `~/.weather-tools-site.scrub`, outside
the repo, and refuses to pass without it unless told `--no-external`.

Jobs and their cadence: `archive` (every 30 min, first), `forecast` (after it, from the archive),
`obs` (every 10 min: the observation record, per-station observation snapshots, `summary.json`,
`manifest.json`), `hurricane` (every 30 min), `quotes` and `reask` (every 10 min, together as
`market`). `half-hourly` runs archive, forecast and hurricane
in one invocation; `all` runs everything once.

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
| `archive/_meta/grids.json`, `health.json`, `_done/`, `_runs/` | | grid cache, per-source failure streaks, cycle markers, one record per pass plus `latest.json` |

Nothing is written twice. A pass that finds every cycle already stored does nothing, which also
makes a retried scheduled run harmless. The observation day files are the one exception: they are
re-read and upserted each pass so that a corrected report (`COR`) replaces the original under the
same `obsTime` while the file keeps what it superseded.

Why the archive comes first: no public endpoint returns an earlier forecast, NOMADS keeps NBM text
for about two days, and data from days when the job was not running cannot be recovered. Bulletins
are caught up, not sampled: every cycle in the lookback window without a completion marker is
fetched, a few per pass, and a marker is written only when the bulletin covered the stations it
should. One limit by design: the NWS forecast is served with an hour's CDN cache and the job
honours it, so two issuances less than an hour apart can reach the archive as one.

## Snapshots

The browser reads only these (all JSON, short `Cache-Control`). Every file carries two clocks:
`asof`, the time the data is good to (for observations, the last successful pull), and `written`,
when the file was built, so a feed that has stopped shows as an ageing as-of rather than a fresh
file. Every level carries its provenance: which cycle supplied it, whether that cycle was issued
before the day began, and whether it is an official product value or an extreme over hourly rows.

| Key | Cadence | Contents |
| --- | --- | --- |
| `snapshots/obs/{STATION}.json` | 10 min | last 72 h of reports, today's and yesterday's extremes (with report type and decode source), the latest raw report, `recordEnd`, `fetchOk` |
| `snapshots/summary.json` | 10 min | every station: position, observed extremes so far (with `obsDay`), standing and as-issued forecast levels with flags (`nwsOfficialHighToday`, `{src}IssuedPreDay`, `{src}HighTodayFrom`), day markers, `alarms` |
| `snapshots/manifest.json` | 10 min | as-of per data type (read from the files that hold the data), cadences, archive depth, alarms, unhealed observation gaps |
| `snapshots/forecast/{STATION}.json` | 30 min | standing NWS (hourly + official day/night), NBM, LAMP, MAV; per source the as-issued pre-day trace and the level for the day picked per extreme (`levelCycleHigh`, `levelCycleLow`, `levelPreDay`); yesterday's as-issued |
| `snapshots/field.json` | 30 min | the map shading: inverse-distance interpolation of tomorrow's NWS highs and lows (derived), with the date it is for per station |
| `snapshots/hurricane.json` | 30 min | active storms with points, track, cone, past track (re-fetched only when the advisory changed); outlook regions; season counts (once a day) |
| `snapshots/scorecard.json` | daily | per station and source: n, MAE, bias, share within 1° and 2°; the last 14 scored days |
| `snapshots/normals/{STATION}.json` | weekly | NCEI 2006-2020 daily normals, with the GHCN station id and its distance from the airport |
| `snapshots/climate.json` | daily | NCEI, GML, STAR series and the RAPID annual means |
| `snapshots/market/{STATION}.json` | 10 min | the station's listed strikes for today and tomorrow with Yes bid/ask/mid and sizes, the implied medians, and each strike's quote history (two days, 10-minute samples) |
| `snapshots/market/summary.json` | 10 min | per station: listed or not, the implied medians for today and tomorrow |
| `snapshots/market/hurricane.json`, `climate.json` | 10 min | every contract of the exchange's hurricane category and of the climate products, with quotes |
| `snapshots/reask.json` | 10 min | the vendor lane: its state when off; per storm the latest LiveCyc ladder and the interim and final settlement files when on |
| `archive/market/{YYYYMMDD}/{HHMMSS}.json.gz` | 10 min | every quote of the pass, append-only (expires after 400 days on AWS) |

Jobs run inside one wall-clock budget: a scheduled invocation sets the budget, the archive step
reserves time for the steps after it, and every step records what it skipped rather than being
killed mid-write. A station whose forecast build fails keeps its previous snapshot, flagged
`staleSince`.

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
| `cadence_minutes.*` | | how often each job runs: obs 10, forecast 30, archive 30, hurricane 30, market 10 |
| `sources.*` | | switch NBM, LAMP, MAV, the observation record, the exchange quotes or the vendor lane off |
| `market_overlay` | | `live`, `placeholder` or `off` per build target; `source` is what `?market=on` means |
| `exchange.base_url`, `.quote_workers` | | the exchange's public market-data host and the quote thread pool (4) |
| `reask.base_url`, `.api_key` | `WX_REASK_API_KEY` | the vendor lane; the key comes only from the environment |
| `disclosure` | | the affiliation text shown in the footer and about page |

Two decode conventions are top-level constants in `pipeline/gov_weather.py`, never buried in
parsing logic: `TEMP_SOURCE` (`remarks` tenths or `body` whole degrees) and `INCLUDE_SPECI`.
Every observation file stamps the convention it was decoded with.

## Seeding the archive and backfilling observations

An archive written by the earlier reference archiver can be imported once:

    python3 scripts/seed_archive.py /path/to/archive [--backend s3 --bucket ...]

Files are scanned with the scrub needles before they are written; nothing is overwritten.

The observation record heals gaps of up to 72 hours itself. aviationweather.gov holds 30 days, so
a longer gap (the days before the site went live, or an outage) is filled once with

    python3 scripts/backfill_obs.py --from 2026-08-13

Run it soon after deploying: the 30-day window slides, and the scorecard can only score days that
have observations. `manifest.json` lists stations with an unhealed gap under `obsUnhealed`.

## The two build targets

`scripts/build.py` writes `dist/standalone/` (the full site: map, city chart, scorecard,
hurricanes, climate, about, with header, footer and the affiliation disclosure) and
`dist/embed/` (one page: the city chart, no chrome, for an iframe). Both read the same snapshot
files and the same JavaScript; only the generated `config.js` differs. The embed accepts
`?station=KLAX`, `theme=light|dark` and `market=on|off`.

The market layer lives in `site/js/market.js` behind one switch (`market_overlay` per target in
`config/site.json`, overridable by `?market=`). The standalone site ships `live`: the quote job
(`pipeline/market.py`, every 10 minutes) reads the exchange's three public market-data endpoints
(category tree, a market's contracts, a contract's top of book; no key) and writes per-station
snapshots with the Yes-side bid, ask and midpoint of every listed strike for the station's today
and tomorrow, a rolling two-day history of each strike's quote, the market-implied median (the
strike where the Yes price crosses 50¢), and the hurricane and climate product groups. Pages show
prices in cents with the time they were read; nothing is fee adjusted. `placeholder` keeps the
reference package's labelled synthetic ladders. The embed ships `off`; when off, the pages
reserve no space for market elements.

The one non-government weather source is the vendor lane for live-storm wind probabilities
(`pipeline/reask.py`), off by default and doubly gated (`sources.reask` and a credential in the
environment); the hurricane page says whether it is on.

`scripts/serve_local.py` serves a built target (`--fail-fetch` makes every data request answer
503, to see the degradation paths). `scripts/verify.py` drives Playwright's Chromium over both
targets in light and dark and checks rendering, zero script errors, the overlay-off layout and
both degradation paths; it needs `python3 -m pip install playwright`.

## Layout

    config/        site.json (the config surface), cities.json and field_grid.json (generated)
    pipeline/      gov_weather.py (every government call), archive.py, snapshots.py, hurricane.py,
                   scorecard.py, normals.py, climate.py, basemap.py, storage.py, config.py,
                   run.py (command line), handler.py (the only vendor-specific file: AWS Lambda)
    site/          the static frontend; site/assets/ holds the small projected geometry
    geo/           vendored inputs, not served: public-domain TopoJSON, and the exchange's wind
                   reference-location list (id, name, position; the vendor's registry, see NOTICE)
    samples/       snapshots for offline local mode (checked in)
    scripts/       build.py, serve_local.py, verify.py, build_assets.py, scrub.py,
                   seed_archive.py, backfill_obs.py, make_samples.py, package_lambda.py
    ops/aws/       the CloudFormation stack; ops/cloudflare/ the serving alternative
    tests/         unittest, no network
    DEPLOY.md      the runbook

## Data sources and licensing

All weather data is US government and in the public domain; see `NOTICE`. api.weather.gov
requires a User-Agent naming the application with a contact address, publishes an appropriate-use
policy, and blocks addresses that affect its service, which is why all fetching is server side on
a fixed cadence and the browser reads only from this site's own storage. Anything smoothed,
interpolated or computed here is labelled as derived, not as a National Weather Service product.
