# Project rules

Read this first in every session.

## What this repo is

A public website that hosts live weather displays on its own domain, and is the source IBKR embeds
into the research tab of its prediction-market UI through an iframe. Two build targets from one
codebase: `standalone` (the full site) and `embed` (one chart, no chrome). A Python pipeline pulls
US government feeds on a schedule, keeps an append-only forecast archive, and writes small JSON
snapshots to object storage behind a CDN. The browser reads only those snapshots.

It shares code with an earlier reference package of the same displays, but has a different job:
this one has to stay up and stay fresh on its own.

## Hard constraints, decided, do not relitigate

- **US government data only** for weather content: api.weather.gov, aviationweather.gov, NOAA Open
  Data on AWS, NOMADS, NHC, NCEI, NOAA GML, NOAA/NESDIS STAR. No Open-Meteo, no commercial vendors.
  Documented exceptions: USGS The National Map imagery for the station locators (a US government
  work, fetched server side and served from this site's own bucket), Natural Earth basemap outlines (public domain) and the RAPID AMOC annual means
  (UK NERC, acknowledged in NOTICE), which the owner chose to keep; the exchange's public market-data
  endpoints for contract prices (owner's decision 2026-08-23, `pipeline/exchange.py`); the vendor
  lane for live-storm wind probabilities (`pipeline/reask.py`), which is off by default, needs a
  credential from the environment, and shows the vendor's numbers as published under its mark; and the
  seasonal hurricane forecast total in `season_forecast` (Colorado State University, owner's decision
  2026-08-23), which is attributed wherever it is drawn and falls back to the climatological pace when
  empty. The hurricane count panels draw both paces so the forecast is never the only line shown; and
  Iowa State University's IEM archive (`pipeline/iem.py`, owner's decision 2026-08-31), used ONLY to
  backfill GFS MOS and LAMP levels for days before this site's own bulletin archive begins — the
  content is the NWS's own guidance, IEM is where the old cycles are kept, every backfilled level
  names its source, the scorecard prefers this site's own capture wherever one exists, and the pages
  mark rows scored from a backfilled level.
- **Observations come from aviationweather.gov METAR, not api.weather.gov.** api.weather.gov also
  serves the five-minute ASOS stream, and a daily maximum over five-minute data lands at or above one
  over hourly METARs. The contracts settle on METARs. The reasoning is in `pipeline/gov_weather.py`.
- **`TEMP_SOURCE` and `INCLUDE_SPECI` stay top-level constants** in `pipeline/gov_weather.py`, never
  buried in parsing logic. Current values: `remarks`, `True`. Every observation snapshot stamps the
  convention it was decoded with.
- **The archive job runs before anything visible.** No public endpoint returns an earlier forecast.
  NOMADS keeps NBM text for about two days, so a gap longer than that is unrecoverable.
- **api.weather.gov needs a User-Agent** with a contact address (403 without). It comes from config.
  The browser never calls a government endpoint; all fetching is server side on a fixed cadence.
- **No credentials, internal hostnames, VPN config, or ForecastEx internal identifiers in the repo.**
  `scripts/scrub.py` runs before every push; its needle list for internal names lives outside the
  repo (`~/.weather-tools-site.scrub`), because the list itself would leak.
- **Deployment agnostic.** Bucket, endpoint, domain, cadence and User-Agent come from
  `config/site.json` or `WX_*` environment variables. `pipeline/handler.py` is the only
  vendor-specific file.
- **Everything runs on a clean checkout with no cloud account.** `samples/` plus local mode render
  both targets offline. Deploy is a separate, documented step (`DEPLOY.md`).

## Market layer

`site/js/market.js` is the only place market data enters the pages; `pipeline/exchange.py` is the
only place the exchange is called. `market_overlay` per target: `live` on the standalone site (the
quote job's snapshots: Yes bid, No bid and Yes price midpoint per listed strike, two-day quote history, implied medians,
hurricane and climate groups), `off` in the embed unless `?market=on`, `placeholder` for the
reference package's synthetic ladders. Prices are the exchange's, in cents, with their as-of time,
never fee adjusted. Exchange language: there are no sellers, only bids to buy Yes or No that sum to $1;
the feed's "ask" on a Yes contract is one dollar less the No bid and the pages never say "ask", "sell"
or "offer". The pages never compute a fair value, a model probability, a disagreement score, or a
figure of their own for the highest-wind pool (those are internal systems and stay out of this repo;
the pool's calculation is done once per cycle on the desk and reaches the exchange through the market
maker, so the page shows the exchange's price). An earlier exception for a site-computed pool figure
(2026-09-01) was withdrawn by the owner on 2026-09-02. The one documented exception (owner's decision
2026-09-02): a storm the owner has ruled on carries, as its exchange series and as its pool figure,
the desk's per-delivery output instead of the quotes the exchange showed. The ruling is a file under
`pipeline/overrides/` (Edouard 2026, the first listing: no contract traded and the quotes were not
maintained), applied on every write of the ledger and of the pool series so a rebuild cannot undo it;
`scripts/apply_overrides.py` applies it once to what is already stored. Storms without a file carry the
exchange's quotes as read and no figure, and the pages draw them with no note either way. When off, the
layout reserves no space for market elements.

## Decisions taken 2026-08-21

Hosting: AWS, as an isolated stack (S3, CloudFront, Lambda, EventBridge); the S3-API storage
adapter keeps other targets a config change. Domain: placeholder until chosen. Decode: remarks
tenths, SPECI counted. Overlay: on with placeholders (standalone). Disclosure text: in
`config/site.json`, verbatim. User-Agent contact: in config. Roster: the 38 contract stations.
Scope: city chart, national map, scorecard, hurricanes, NCEI normals, climate series with
placeholder markers. Archive seeded from the owner's existing archive. GitHub, public, MIT.
Forecast series: chart NWS + NBM + LAMP; scorecard NWS + NBM + GFS MOS MAV; all archived. Embed
parameters: `station`, `theme`, `market`.

## Layout

    config/          site.json (the config surface), cities.json and field_grid.json (generated by scripts/build_assets.py)
    pipeline/        gov_weather.py (every government call), exchange.py (every exchange call), archive.py,
                     snapshots.py, market.py (the quote job), reask.py (the vendor lane), hurricane.py,
                     scorecard.py, normals.py, climate.py, season.py (the count climatology),
                     energy.py (the EIA series behind the energy contracts, needs a key),
                     discussion.py (each forecast office's Area Forecast Discussion),
                     subhourly.py (the five minute stream, context only, never settlement),
                     locator.py (a metro-scale USGS map per station, fetched once),
                     traffic.py (page views from the CDN access logs),
                     basemap.py, storage.py, config.py,
                     run.py (command line), handler.py (the only vendor-specific file)
    site/            the static frontend; js/market.js is the market seam; js/storm.js is a live storm's
                     wind contracts, delivery by delivery; embed/ is the iframe target
    site/assets/     small projected geometry the pages load (generated)
    geo/             vendored inputs, not served: public-domain TopoJSON and the exchange's wind reference-location list
    samples/         snapshots for offline local mode (checked in)
    scripts/         build.py, serve_local.py, verify.py, build_assets.py, scrub.py, traffic.py (read the counts),
                     seed_archive.py, backfill_obs.py, make_samples.py, package_lambda.py
    ops/aws/         template.yaml (the stack); ops/cloudflare/ the serving alternative
    tests/           unittest, no network
    DEPLOY.md        the runbook for the steps that need the owner's accounts

Jobs: `python3 -m pipeline.run --job archive|forecast|obs|hurricane|quotes|reask|market|scorecard|normals|climate|season|discussion|subhourly|locator|traffic|half-hourly|daily|all`.
Verification: `python3 -m unittest discover -s tests` and `python3 scripts/verify.py` (Playwright).

## Working style

Python 3.9-compatible, standard library only; boto3 is imported only when the s3 backend is
configured. Hand-rolled twenty lines over a dependency. When a decision has a meteorological
reason, write the reason in a comment. Plain prose everywhere: no marketing language, in code
comments, docs or page text. Flag anything that needs the owner's decision instead of guessing.
