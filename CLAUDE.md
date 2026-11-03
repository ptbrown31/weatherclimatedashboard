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
  Data on AWS (the NBM bulletins; RTMA on NOAA Open Data feeds the unlisted analysis-resolution
  page; the HRRR's surface files feed the city page's HRRR line, `pipeline/hrrr.py`; NDFD's CONUS daytime high and
  overnight low shade the landing map, `pipeline/ndfd.py`), NOMADS, NHC (its GIS archive of every advisory feeds the
  hurricane page's replay, `pipeline/advisories.py`), NCEI, NOAA GML, NOAA/NESDIS STAR, and the
  NWS Weather Prediction Center's analysis/satellite composite under the landing map (`pipeline/floater.py`). No
  Open-Meteo, no commercial vendors.
  Documented exceptions: USGS The National Map imagery for the station locators (a US government
  work, fetched server side and served from this site's own bucket), Natural Earth basemap outlines and, on the
  hurricane map, its roads and populated places (public domain; roads and places by the owner's decision of 2026-10-07,
  `scripts/build_map_features.py`) and the RAPID AMOC annual means
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
  mark rows scored from a backfilled level. The world GDP contract's page (`climate-gdp.html`, owner's
  decision 2026-11-02) is economic content outside this rule: the IMF's World Economic Outlook and its
  historical forecasts, the World Bank's WDI, and the Penn World Table through Bilal and Känzig's
  replication package, each named with its terms on the page and in NOTICE.
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
never fee adjusted. A book whose two sides both bid the minimum (one cent against one cent) is
empty, and its midpoint of fifty cents is not a price: outside the daily temperature ladders every
board shows no price for one and leaves its map region unshaded (`WXM.realMid`, owner's decision
2026-09-03). The daily temperature ladders are deliberately excluded and quote every book they are
sent; a book with bids on one side only is a real resting bid everywhere and keeps its price. Exchange language: there are no sellers, only bids to buy Yes or No, and a Yes bid and a No bid trade when they sum to $1. The pages never say the standing bids sum to a dollar (owner, 2026-10-16): a price box shows each standing bid and what it lets a reader buy now, the central value and the spread (`WXM.bookRows`);
the feed's "ask" on a Yes contract is one dollar less the No bid and the pages never say "ask", "sell"
or "offer". A price that names a listed contract opens it on IBKR's prediction-market app (`WXM.linkTo`). Every priced
chart or board says so in one line, "Click on any price to trade contracts through IBKR Prediction Markets"
(`WXM.tradeNote`, owner, 2026-10-22). The line sits under the chart or board, or once above a page of many priced
panels, and never where nothing opens a contract. The pages never compute a fair value, a model probability, a disagreement score, or a
figure of their own for the highest-wind pool (those are internal systems and stay out of this repo;
the pool's calculation is done once per cycle on the desk and reaches the exchange through the market
maker, so the page shows the exchange's price). An earlier exception for a site-computed pool figure
(2026-09-01) was withdrawn by the owner on 2026-09-02. The one documented exception (owner's decision
2026-09-02): a storm the owner has ruled on carries, as its exchange series and as its pool figure,
the desk's per-delivery output instead of the quotes the exchange showed. The ruling is a file under
`pipeline/overrides/` (Edouard 2026, the first listing: no contract traded and the quotes were not
maintained), applied on every write of the ledger and of the pool series so a rebuild cannot undo it;
`scripts/apply_overrides.py` applies it once to what is already stored. Storms without a file carry the
exchange's quotes as read and no figure, and the pages draw them with no note either way.

A depression's vendor files arrive under its number until NHC names it (Five became Edouard, Nine became
Isaias). The vendor lane files them under the named storm with the same ATCF number, read from NHC's roster
or the season's best tracks, so the page shows one storm and one series across the rename (owner's decision
2026-10-07; `renamed_storms` in `pipeline/reask.py`, kept in `reask.json` once made). The figures are the
vendor's as delivered, each step keeps the name it came under, and no ruling file is needed or written for it.

Reask's LiveCyc probabilities are presented as **the fair-value reference** for the Live Hurricane contracts
(owner's decision 2026-10-09): the location cards open with LiveCyc forward and the exchange's price behind it,
the legend and the boxes name LiveCyc that way, and no column of the exchange's price less LiveCyc is shown,
because with no trading the gap is the market maker's quoting and reads as a disagreement. The site computes
nothing for this; it labels the vendor's published figure.

The Live Hurricane wind contracts resolve on whole miles an hour (owner's decision 2026-10-12): every page prints a
Metryc final gust rounded half up to a whole mile an hour and marks a strike settled against that figure
(`WXC.settleMph`), so 69.6 mph reads 70 and clears a 70 mph strike. The exchange's published terms report the
settlement value to one decimal place. The peak wind location pool still picks its winner on the vendor's full values,
which is how its terms break a tie.

Each pool's price series is kept under `snapshots/lhl/` and indexed by symbol, with the exchange's name, in
`snapshots/lhl/index.json` (`_lhl_index` in `pipeline/market.py`). The exchange drops a pool from its listing once the
storm settles, so a settled storm's page finds its pool there by the storm's name and draws the series and how the pool
resolved, each candidate's final gust and the side that pays (owner's decision 2026-10-13). A settled storm shows no card
about a pool not being carried.

Each storm's map steps through it (owner's decisions of 2026-10-20). The storm's section gets its map as soon as the
exchange lists a Live Hurricane contract for it, before the vendor's first file. One timeline moves the map and every
location's card: one step per full NHC advisory, then the Metryc interim and the final. At each advisory the map draws
that advisory's cone and forecast track, the storm's position and its path so far, and at each location the **median of
the newest LiveCyc ladder** at or before the advisory's synoptic time, carried at most twelve hours. The median is read
the way the accuracy paper reads a temperature ladder: pooled to fall with the threshold, read where it crosses fifty,
printed in whole miles an hour, and "under 60" below the lowest threshold. The interim step shows the interim's median,
and the final shows the gusts recorded. This median is a figure the site computes from the vendor's published ladder,
by the owner's decision, and the one exception to "the site computes nothing" for LiveCyc. The view is fixed for the
storm. It holds the storm's places and the cones of the advisories that carried LiveCyc figures, its contract period,
or of every advisory before the vendor's first file. Cones outside that run off the edge, so a long track such as
Nolo's does not shrink the places. The map zooms and pans as the basin map does. Its places, labels and lines keep
their size on screen, and the zoom holds while the timeline steps. The advisories come from NHC's GIS archive
(`pipeline/advisories.py`, the `advisories` job after `hurricane` in the half-hourly chain; NHC data). Every storm
the lane has carried stays on the page, grouped by season. Two weeks after its final, its index entry in `reask.json`
keeps only its stamps (`reask.slim_entry`), and the page draws it from its ledger. A storm's contracts are matched by
the exchange's product name, never by the symbol's letters.

A second documented exception, the owner's decisions of 2026-09-24 and 2026-09-25: where the exchange
is **not quoting a contract at all**, the desk's own figure for it may stand in. The site still computes
nothing. The desk publishes a file, this site fetches it over https with redirects refused
(`pipeline/desk.py`, and `fetch_desk_lhl` in `pipeline/reask.py` for the pool), and every lane is off
until its URL is set in the environment (`WX_DESK_LHL_URL`, `WX_DESK_MG_URL`). Three rules make this
safe and none of them is optional. The desk's rows never share a field with the exchange's: the market
job writes them under `anticipated`, never under `days`, and only for a station-day with no exchange
price, so a stand-in and a price are never both present and nothing downstream has to choose. They are
drawn so they cannot be read as a price: hatched, labelled as a percentage rather than in cents, and
carrying no link to a book that does not exist. And the feed's own `method` label is parsed but never
stored or rendered, because these snapshots are public and that string names an internal system. A
missing or malformed feed is an absence, never a failed pass. When off, the layout reserves no space
for market elements.

The world GDP contract's page is the one page that computes a probability (owner's decisions of
2026-11-02). It carries Patrick Brown's article and re-runs the contract's model in the browser
(`site/js/elnino-model.js`, a port of the published run that the reproduction repository's tests check
path by path), so the chance of Yes under each of two views, no temperature effect and Bilal and
Känzig's (2026) estimate, follows the reader's controls. The two are never blended into one number,
never called a price or a fair value, and are marked as not prices wherever they are drawn. Until the
exchange lists the contract, its ladder draws the strike `model.json` proposes (one since 2026-11-03, the
article's 1.1% a year) hatched, with the listing date and no link. Once `gdp_contract.product` in `config/site.json` names the listed product, the ladder draws the
exchange's prices through the market layer (`WXM.productBook`, the catalogue's terms and quotes), each
opening the contract on IBKR, with the two views' marks kept on each bar; the prices belong to the
listed contract's period, so they are drawn only at its end year. The model's files are the El Niño
refresh's, written monthly to `snapshots/elnino/` from outside this repo (DEPLOY.md section 12b); the
pipeline never writes them, and `samples/snapshots/elnino/` is a frozen copy for local mode. The article
is the owner's own copy and the house prose rules do not touch it; the page's own notes follow them.

## Decisions taken 2026-08-21

Hosting: AWS, as an isolated stack (S3, CloudFront, Lambda, EventBridge); the S3-API storage
adapter keeps other targets a config change. Domain: placeholder until chosen. Decode: remarks
tenths, SPECI counted. Overlay: on with placeholders (standalone). Disclosure text: in
`config/site.json`, verbatim. User-Agent contact: in config. Roster: the 38 contract stations.
Scope: city chart, national map, scorecard, hurricanes, NCEI normals, climate series with
placeholder markers. Archive seeded from the owner's existing archive. GitHub, public, MIT.
Forecast series: chart NWS + NBM + LAMP; scorecard NWS + NBM + GFS MOS MAV; all archived. Owner's
decision 2026-10-16: the city chart and its additional variables panels show each family's station rows
(the NBM's hourly bulletin carried on by its 3-hourly one, GFS MOS's 3-hourly rows, LAMP), the NWS forecast
as the one exception for its familiarity, and the HRRR at the grid point nearest each station (newest run
carried on by the newest 48-hour run; every run kept); the NBM and GFS MOS levels there are the highest and
lowest of those rows, never the TXN or N/X line, while the scorecards keep scoring both forms. Later the same day:
each model's newest run is drawn solid in its color and every run it superseded dotted, on the chart and the panels;
the level labels are pinned to the run each model issued before local midnight (a run issued at midnight belongs to
the day; the first run of the day where none is on file), its highest and lowest row inside the day for every model,
the NWS included, each level line spanning exactly the hours its rows cover, so the day's observations read against a
forecast that holds still; and the crosshair lists runs by model, the newest first. The snapshot's as-issued trace
and its issued levels (pick_levels, which the maps and pickers read) both use that strict cutoff, so "issued" means
one run across the daily views; the scorecards keep their own anchor at six the evening before. The
chart carries no legend (each source is named on its level labels and in the crosshair, which also reads the five
minute band), and the day-ahead view draws only the day-ahead board's strikes and prices, none before it lists. The
day-ahead view shades its nights as today's does, with the sun carried forward one day. "Overlay yesterday" is the
calendar's yesterday on every view: moved a day on to today, two days on to tomorrow, and on both days placed twice
(owner, 2026-10-20). The landing map (owner, 2026-10-20): its shading
is NDFD's own daytime high or overnight low in the NWS's color scale (today's from the issuance before midnight
Eastern, tomorrow's the newest), each dot carries the ForecastEx value inside it with the city's name beside it and
a legend under the map, its hover lists every model's number as the station's city chart draws it, and WPC's
analysis loop replaced the GOES-East CONUS loop. Every page that shows the market's number for a day's high or low
shows the accuracy paper's whole-degree central value (`exchange.central_value`: one-sided books against the limits,
monotone pooling, no value across two unpriced strikes, ceil for highs and floor for lows), the scorecard's
ForecastEx row included (owner, 2026-10-20). WPC's analysis loop is carried from its Mercator onto the landing map's
projection in the pipeline, at the scale that holds all of WPC's picture, so its states keep the map's shapes and what
lies past the borders stays in view (owner, 2026-10-20). Embed
parameters: `station`, `theme`, `market`.

## Layout

    config/          site.json (the config surface), cities.json and field_grid.json (generated by scripts/build_assets.py),
                     analysis_locations.json and analysis_lattice.json (generated by scripts/build_analysis_grid.py from geo/settlement_locations.csv),
                     category_aliases.json (the exchange's research button opens /<category id>.html for a product's
                     category; scripts/build.py writes one page per alias that sends the reader on to the page the owner
                     maps it to, e.g. hurricane.html?basin=AL&loc=PC for one wind-contract location)
    pipeline/        gov_weather.py (every government call), exchange.py (every exchange call), archive.py,
                     snapshots.py, market.py (the quote job), reask.py (the vendor lane), hurricane.py,
                     scorecard.py, normals.py, climate.py, season.py (the count climatology),
                     energy.py (the EIA series behind the energy contracts, needs a key),
                     discussion.py (each forecast office's Area Forecast Discussion),
                     subhourly.py (the five minute stream, context only, never settlement),
                     sst.py (NOAA Coral Reef Watch's daily SST anomaly behind the hurricane map, NESDIS CoastWatch ERDDAP),
                     floater.py (satellite loops: NOAA/NESDIS STAR's GOES floater frames of each active storm, played under
                     the hurricane map, and WPC's surface analysis over satellite imagery every three hours, played under
                     the landing map, copied into the bucket every half hour),
                     ndfd.py (NDFD's daytime high and overnight low sampled at the landing map's 4-pixel cells, each new
                     issuance archived; today's views from the issuance before midnight Eastern, on its own schedule),
                     locator.py (a metro-scale USGS map per station, fetched once),
                     traffic.py (page views from the CDN access logs),
                     basemap.py, storage.py, config.py,
                     analysis.py (the gridded-analysis resolution lane: RTMA resolving at the
                     sixty-seven settlement locations, on its own schedule; unlisted page, no contract settles on it),
                     grib2.py (a GRIB2 reader in the standard library, used by analysis.py and hrrr.py),
                     hrrr.py (the HRRR at each station's grid point, run by run, on its own schedule),
                     advisories.py (every full NHC advisory of each storm, from NHC's GIS archive, behind the
                     hurricane page's replay; Pacific longitudes written west of Greenwich across the date line),
                     run.py (command line), handler.py (the only vendor-specific file)
    site/            the static frontend; js/market.js is the market seam; js/storm.js is a live storm's
                     wind contracts, delivery by delivery; js/climate-gdp.js and js/elnino-model.js are the
                     world GDP contract's article and its model; embed/ is the iframe target
    site/assets/     small projected geometry the pages load (generated)
    site/vendor/     third-party code shipped as it is (KaTeX; see NOTICE)
    geo/             vendored inputs, not served: public-domain TopoJSON, the exchange's wind reference-location list and the
                     owner's settlement-location list for the analysis lane (settlement_locations.csv)
    samples/         snapshots for offline local mode (checked in)
    scripts/         build.py, serve_local.py, verify.py, build_assets.py, build_analysis_grid.py, scrub.py, traffic.py (read the counts),
                     build_relief.py (hurricane map hill shading from NOAA NCEI ETOPO 2022), build_map_features.py (its roads and cities),
                     seed_archive.py, backfill_obs.py, make_samples.py, package_lambda.py
    docs/            the per-lane contracts (accuracy.md, analysis.md)
    ops/aws/         template.yaml (the stack); ops/cloudflare/ the serving alternative
    tests/           unittest, no network
    DEPLOY.md        the runbook for the steps that need the owner's accounts

Jobs: `python3 -m pipeline.run --job archive|forecast|obs|hurricane|advisories|quotes|reask|market|scorecard|normals|climate|season|discussion|subhourly|locator|traffic|catalogue|catquotes|series|severe|headline|report|accuracy|analysis|sst|floater|hrrr|ndfd|half-hourly|daily|all`.
Verification: `python3 -m unittest discover -s tests` and `python3 scripts/verify.py` (Playwright).

## Working style

Python 3.9-compatible, standard library only; boto3 is imported only when the s3 backend is
configured. Hand-rolled twenty lines over a dependency. When a decision has a meteorological
reason, write the reason in a comment. Plain prose everywhere: no marketing language, in code
comments, docs or page text. Flag anything that needs the owner's decision instead of guessing.
