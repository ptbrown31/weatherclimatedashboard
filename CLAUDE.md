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
  Two documented exceptions, both cartography or settlement-basis, never weather: Natural Earth basemap
  outlines (public domain) and the RAPID AMOC annual means (UK NERC, acknowledged in NOTICE), which
  the owner chose to keep.
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

`site/js/market.js` is the only place market data enters. Its `source` is config: `placeholder`
on the standalone site at launch (labelled synthetic ladders, from the reference package), `off` in
the embed unless `?market=on`. No live ForecastEx feed exists here; wiring one is the owner's
decision after compliance input. When off, the layout reserves no space for market elements.

## Decisions taken 2026-08-21

Hosting: AWS, as an isolated stack (S3, CloudFront, Lambda, EventBridge); the S3-API storage
adapter keeps other targets a config change. Domain: placeholder until chosen. Decode: remarks
tenths, SPECI counted. Overlay: on with placeholders (standalone). Disclosure text: in
`config/site.json`, verbatim. User-Agent contact: in config. Roster: the 38 contract stations.
Scope: city chart, national map, scorecard, hurricanes, NCEI normals, climate series with
placeholder markers. Archive seeded from the owner's existing archive. GitHub, public, MIT.
Forecast series: chart NWS + NBM + LAMP; scorecard NWS + NBM + GFS MOS MAV; all archived. Embed
parameters: `station`, `theme`, `market`.

## Working style

Python 3.9-compatible, standard library only; boto3 is imported only when the s3 backend is
configured. Hand-rolled twenty lines over a dependency. When a decision has a meteorological
reason, write the reason in a comment. Plain prose everywhere: no marketing language, in code
comments, docs or page text. Flag anything that needs the owner's decision instead of guessing.
