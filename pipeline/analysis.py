"""
analysis.py — how a settlement framework built on NOAA's gridded analyses
would have resolved, at the sixty-seven settlement locations.

The contracts settle on station reports, and this site's other lanes read
those. This lane shows the alternative the owner asked to see (docs/analysis.md):
the daily and hourly values a contract would resolve on if it were written on
the Real-Time Mesoscale Analysis (RTMA), read from NOAA Open Data on AWS. No
contract settles on it, the page says so on every view, and nothing here
feeds any other lane.

Conventions, all from docs/analysis.md section 1 and fixed there:

    The value at a place is the analysis at one 2.5 km grid cell, never an
    interpolation, because a contract needs one number that two readers can
    recompute from the same file: the cell whose square contains the main
    city's City Hall, frozen in the settlement list the owner approved on
    2026-09-29 (config/analysis_locations.json).

    RTMA resolves every variable, precipitation included (owner's decision
    2026-09-29), and is the only analysis the lane reads: URMA, which does not
    resolve, was dropped from the lane and the page on 2026-09-30.

    The day is the local civil date at the place through its IANA zone. Every
    top-of-hour analysis whose local civil time falls on that date counts. That is
    24 analyses on an ordinary day, 23 on the spring-forward day (02:00 local
    does not occur) and 25 on the fall-back day (01:00 local occurs twice, and
    both analyses belong to the date). Precipitation is the one-hour
    accumulations that together cover the date from midnight to midnight: a
    day's row for the analysis at HH:00 carries the accumulation over the hour
    that starts then, which NOAA files under the hour it ends. A place-day is
    final at its cut-off, 12:45 AM local time on the day after (owner's ruling
    2026-10-14, the draft terms), on the files published on NOAA Open Data or
    NOMADS by then. A file's publication time is the first time it was on
    either server, its Last-Modified time there. A file published later is
    shown and never counted, the first published version of a file counts, an
    hour missing at the cut-off leaves the value on the hours available, and a
    place-day with no hour by the cut-off goes to event review. Until
    2026-10-14 a day was final once every hour's file was read or known not
    published.

    Every stored value is in the file's own unit and is converted exactly, in
    decimal arithmetic, and the aggregate (the highest hourly temperature,
    the mean wind, the summed accumulation) is rounded once, half up, away
    from zero (-20.5 F is -21, the way a published table shows it). The exact
    value shown beside the whole one is that aggregate cut toward zero to a
    thousandth (precipitation a ten-thousandth of an inch), so it never looks
    as if it rounds the other way. A value equal to
    the strike resolves Yes, at or above it for the high, gust, mean wind and
    precipitation and at or below it for the low (owner's decision
    2026-09-28). The exchange's current daily temperature and wind contracts
    keep the strict test, so on the boundary this framework and the board
    part company by design.

    Every file is read from NOAA Open Data and from NCEP's NOMADS server,
    whose copy counts as published too (owner's ruling 2026-11-03). Each
    server's first copy is kept on its own, with when this lane first saw it,
    the server's Last-Modified time and a sha256 of each message; a download
    with a message that fails the check is a copy only when the same bytes
    come again (owner's ruling 2026-11-04). A row takes NOMADS's copy when it
    was published by the day's cut-off, whatever NOAA Open Data's holds, then
    NOAA Open Data's, and per file: a place the copy that counts has no value
    for has none that hour. The choice is final once both copies are in, and
    otherwise at the first ten-minute pass after the day's cut-off, on the
    copies in hand (owner's ruling 2026-11-04). A copy read later, and a
    later replacement of a file, are recorded and never used, and a day shown
    final never changes. An hour's precipitation is a separate file and is
    read even when its analysis is missing, adding to the day's total and
    never to the count of analyses read.

    Each row of a place file also carries its values as the contracts show
    them (whole degrees, whole miles an hour, a hundredth of an inch, each
    rounded once from the exact value) and the day's values so far (the
    running high, low, peak gust, mean wind and precipitation total, rounded
    and exact), which `running` gets from day_summary itself over the hours
    up to that row. The page draws them and never adds or averages a value of
    its own, because rounded hours do not add up to the day (Boston on
    2026-09-28, 0.83 in from its rounded hours against the 0.84 it resolved
    on). Since 2026-11-04 (rowShape 3, the owner's approval of display v3)
    each row also carries its stored values as strings, its values converted
    before rounding and the day so far before rounding as exact strings, both
    files' publication times on each server, and the hourly temperature
    contract of its hour (Patrick's ruling of 4 November), whose own cut-off
    is 1 hour 30 minutes after the hour (his ruling of 5 November; three
    hours until then). It is final once NOMADS's copy published by then is
    in, otherwise at the first ten-minute pass after that cut-off on NOAA
    Open Data's copy published by then, and under review with neither.

Reads   noaa-rtma-pds and NOMADS, every file from both, through
        pipeline/gov_weather.py; the .idx sidecar of each analysis file and a
        range request per message (TMP 2 m, WIND 10 m, GUST 10 m, about 6 MB
        each), the one-message precipitation file whole, as soon as NOAA
        publishes it rather than with the next analysis, and a HEAD request
        for each first copy a day later; GRIB2 decoded by pipeline/grib2.py.
Writes  archive/analysis2/copies/rtma/<stamp>.<kind>.<server>.json.gz   each server's first copy of each file, write once
        archive/analysis2/copies/rtma/<stamp>.<kind>.<server>.later.json.gz   its later replacements, never used
        archive/analysis2/hours/rtma/<stamp>.json.gz      the hour's settled analysis, each place's copy and values, write once
        archive/analysis2/precip/rtma/<stamp>.json.gz     the hour's settled accumulations in millimetres, write once
        archive/analysis2/_meta/state.json                cursors, the open files, hours not published, stamps
        archive/analysis2/_meta/lease.json                the lease that keeps two passes from overlapping
        archive/_meta/health_analysis.json                the lane's own failure streaks
        snapshots/analysis2/grid/rtma/<var>/<stamp>.json  one map frame on the 320 x 200 lattice, with the
                                                          21 x 21 cell window round each place at full resolution
        snapshots/analysis2/grid/index.json               which frames exist, rewritten after every hour's frames
        snapshots/analysis2/days/YYYY-MM-DD.json          every place's day for that local date
        snapshots/analysis2/loc/<id>/YYYY-MM-DD.json      one place-day at the hourly scale, each hour with
                                                          its shown value and the day's values so far
        snapshots/analysis2/csv/YYYY-MM-DD-{raw,processed}.csv   the day as CSV
        snapshots/analysis2/index.json                    last, so a reader sees a consistent set

The lane runs two passes on its own schedules (owner's ruling 2026-11-03). The
ten-minute pass, at :08, :18 and so on, looks for every open file on both
servers, rebuilds the days those touch, once a day sweeps for files the walks
passed without, rewrites a few listed days that lack their CSV files or
predate the current layout, looks at each first copy once more a day after
its hour, and spends what budget remains walking backwards through the last
thirty days, newest first, so the page fills in from today back. The windowed pass runs
every two minutes inside :44 to :54 and :14 to :20, about when NOAA publishes
the analysis and an hour's precipitation, at the minutes the ten-minute pass
does not take, and does only light work. It reads the files newly due and
writes what they change, and when nothing is new it exits within seconds.
An hour's precipitation is read as soon as NOAA publishes it, without waiting
for the next analysis. Every pass ends before the next minute in a window
(pass_seconds), so the ten-minute pass at :48 and :18 does the windowed
pass's work first and little after it. A lease under archive/analysis2/ keeps
any two passes from overlapping on the state and the files. A pass takes it
with a conditional write before it reads the state, and one that finds it
held exits with the status "lease held", which is not a failure. A pass makes
at most forty calls to NOMADS, two seconds apart, well inside the about 120
requests a minute NOMADS allows a client. Nothing a NOAA object does
(missing, short, refused by the decoder, a 5xx after the retries) fails the
pass. Each copy's read is guarded, the failure is recorded, and the state and
the indexes are written in every case.
"""
from __future__ import annotations

import datetime as dt
import csv
import gzip
import hashlib
import io
import json
import math
import os
import time
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from . import archive as arch
from . import gov_weather as gw
from .storage import Lease, Storage

try:
    from . import grib2
except ImportError:  # the decoder is a separate module; without it the pass reports and writes nothing
    grib2 = None  # type: ignore

# Schema 2 (owner's decisions of 2026-09-29): the sixty-seven settlement
# locations, RTMA resolving, the archive in the files' own units with one
# rounding, and precipitation from midnight to midnight. Schema 1's files
# under snapshots/analysis/ and archive/analysis/ are left as they were and
# are no longer written.
SCHEMA = "analysis/2"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCATIONS_PATH = os.path.join(ROOT, "config", "analysis_locations.json")
LATTICE_PATH = os.path.join(ROOT, "config", "analysis_lattice.json")
# NOAA's own publication times of files the lane read before it kept each
# server's copy (2026-11-03), from the engine's read log and its NOMADS probe,
# written by scripts/build_analysis_file_times.py. Shown only, never counted
FILE_TIMES_PATH = os.path.join(ROOT, "config", "analysis_file_times.json")

PREFIX = "snapshots/analysis2/"
INDEX_KEY = PREFIX + "index.json"
DAY_KEY = PREFIX + "days/{day}.json"
LOC_KEY = PREFIX + "loc/{loc}/{day}.json"
GRID_INDEX_KEY = PREFIX + "grid/index.json"
FRAME_KEY = PREFIX + "grid/{product}/{var}/{stamp}.json"
# the day's values as CSV (owner's decision 2026-09-29): every hour and place
# in the files' own units, and the day's values as they resolve
CSV_RAW_KEY = PREFIX + "csv/{day}-raw.csv"
CSV_DAY_KEY = PREFIX + "csv/{day}-processed.csv"
HOUR_KEY = "archive/analysis2/hours/{product}/{stamp}.json.gz"
# the precipitation of a product-hour is its own key because it is a
# separate file that lands on its own time and is read even when the
# analysis never lands (RTMA 2026-09-23 19Z). Since 2026-11-03 it is written
# once, as the analysis hour is; one written before was filled in by later
# reads, a place still without a value taking the new read's
PRECIP_KEY = "archive/analysis2/precip/{product}/{stamp}.json.gz"
# each server's first copy of each file, on its own and written once (owner's
# ruling 2026-11-03), with when the lane first saw it, the server's
# Last-Modified, a sha256 of each message read and the values at the places.
# A later read of the same server's file that differs is a replacement,
# recorded beside it (LATER_KEY) and never used. Since then the archive hour
# and the precipitation key above are each the hour's settled file, written
# once when it has settled for every day it fills (settle_cuts); those
# written before keep the earlier shape
COPY_PREFIX = "archive/analysis2/copies/{product}/"
COPY_KEY = COPY_PREFIX + "{stamp}.{kind}.{server}.json.gz"
LATER_KEY = COPY_PREFIX + "{stamp}.{kind}.{server}.later.json.gz"
STATE_KEY = "archive/analysis2/_meta/state.json"
# the lease (LEASE_* below) lives under data/archive/, which the Lambda's role can write and the
# CDN never serves
LEASE_KEY = "archive/analysis2/_meta/lease.json"
HEALTH_KEY = "archive/_meta/health_analysis.json"   # this lane's own failure streaks (see archive.update_health)
# frames never change and a final place-day changes only to show a late hour or when LAYOUT
# does; everything else is live. A live file carries no stale-while-revalidate (2026-11-03).
# The settlement display fetches a live day's place file and day file again each minute with a
# stamp in the query string, and the CDN's snapshot policy keeps the query string out of its
# cache key, so with it the first fetch after the minute was answered from the stale copy
# while the edge refreshed behind it, up to max-age plus the 300 s, about six minutes old (a
# CloudFront hit of age 179 s on Boston's place file, seen on the live page). Without it a
# fetch is at most the minute old. stale-if-error stays, so an origin error still serves the
# last copy
CACHE_FINAL = "public, max-age=300, stale-while-revalidate=1800, stale-if-error=86400"
CACHE_LIVE = "public, max-age=60, stale-if-error=86400"

STATEMENT = "A proposed settlement framework. No contract settles on it."

# The product and where each file lives: <bucket>/<dir>.<YYYYMMDD>/<file>.
# The analysis file is one per hour with a .idx sidecar; the precipitation
# file is one message, on the smaller G184 grid. lagMinutes is the measured
# landing time of the analysis after the valid hour (docs/analysis.md,
# 2026-09-27), and is what the health check and the turn to NOMADS expect, not
# a promise NOAA makes; when the live walk looks is WINDOWS's. The lane is
# written over PRODUCTS so another analysis could be added; since 2026-09-30
# RTMA is the only one (the owner dropped URMA, which does not resolve).
NODD_DEFAULT = {"rtma": "https://noaa-rtma-pds.s3.amazonaws.com"}
# NCEP's own server keeps the last fourteen days of the analysis under the
# same directory and file names, and a file there counts as published
# (owner's decision 2026-09-29; the RTMA analysis of 2026-09-23 19Z never
# reached NOAA Open Data and NOMADS had it). Since the owner's ruling of
# 2026-11-03 the lane reads every file from both servers, and NOMADS's copy
# counts when the two differ, since an hour is final once NOMADS has it. The
# lane stops asking NOMADS a day short of its fourteen. NOMADS_AFTER_MINUTES
# and PCP_NOMADS_AFTER_MINUTES are when read_hour and read_precip, the readers
# of the archive's earlier shape, turn to it
NOMADS_DEFAULT = {"rtma": "https://nomads.ncep.noaa.gov/pub/data/nccf/com/rtma/prod"}
NOMADS_AFTER_MINUTES = 30
NOMADS_KEEP_HOURS = 13 * 24
# the two servers, in the order the CSV files name them, and the order a pass
# reads them in, NOMADS first, since it usually has a file a couple of minutes
# before NOAA Open Data and its copy is the one that counts
SERVERS = ("nodd", "nomads")
READ_ORDER = ("nomads", "nodd")
# NOMADS blocks a client that makes more than about 120 requests a minute. A
# pass makes at most NOMADS_CALLS_PER_PASS calls to it, at least
# NOMADS_MIN_INTERVAL seconds apart, and passes never overlap, so the lane
# makes at most about 30 calls a minute there, 60 requests if every call
# needed its second try (NomadsBudget)
NOMADS_CALLS_PER_PASS = 40
NOMADS_MIN_INTERVAL = 2.0
# the two files of an hour, the analysis (anl) and the precipitation file that
# ends at the hour (pcp)
KINDS = ("anl", "pcp")
# a settled file is asked for from the server that has not given its copy
# until this long after its hour, when every zone's cut-off has passed, so
# both servers' copies are kept and a NOMADS copy that lands after the cut-off
# is recorded. Then each first copy is looked at once more by its
# Last-Modified, so a file its server writes again is recorded as a later
# replacement; neither is ever used. RECHECK_HOURS_PER_PASS hours of them a
# ten-minute pass at most
RECHECK_AFTER_HOURS = 26
RECHECK_HOURS_PER_PASS = 4
# a file not on a server is asked for again every pass for this long after its
# hour, and once an hour after that, while the server keeps the hour
RETRY_EVERY_PASS_HOURS = 2
# the cut-off of the draft terms (owner's ruling 2026-10-14): a place-day is
# final at 12:45 AM local time on the day after it, on the files published by
# then, and a place-day with no hour by then goes to event review
CUTOFF_AFTER_DAY = dt.timedelta(minutes=45)
# read_precip, the earlier shape's reader, looks on NOMADS for a precipitation
# file NOAA Open Data does not have this long after its hour ends, so the
# day's last accumulation, usually on the bucket 17 minutes after midnight, is
# looked for there before the cut-off
PCP_NOMADS_AFTER_MINUTES = 27
# the product that resolves (owner's decision 2026-09-29): RTMA for every
# variable, precipitation included
SETTLE = "rtma"
PRODUCTS = {
    "rtma": {"bucket": "noaa-rtma-pds", "dir": "rtma2p5", "anl": "rtma2p5.t{hh}z.2dvaranl_ndfd.grb2_wexp",
             "pcp": "rtma2p5.{ymdh}.pcp.184.grb2", "pcpGrid": "g184", "lagMinutes": 47},
}
# the layout of the published files; when it changes, every listed day is
# rewritten, a few a pass, so no day keeps the old one (2026-09-30: URMA
# dropped from the days, the place files and the CSV files; 2026-10-14: the
# cut-off rule, the review state and the publication times in the CSV files;
# on 2026-11-03 each row of a place file gained its value as the contract
# shows it and the day's values so far, for the settlement display; then the
# raw CSV file gained each server's copy of each file, and on 2026-11-04 each
# row the pass at which its copies became final, then the exact strings, both
# files' times on each server and the hourly contract of rowShape 3; on
# 2026-11-05 the hourly contract's cut-off moved to 1 hour 30 minutes after
# the hour, so every listed day's hourly states are worked out again)
LAYOUT = "v3-2026-11-05"
# the shape of a place file's rows, stamped on the file as `rowShape` so the
# page can tell a file whose rows carry `show`, `run` and the publication
# times (2) and the exact strings, the files and the hourly contract (3) from
# one written before them, which it still draws, with no running figures
ROW_SHAPE = 3
# the hourly temperature contract's own cut-off (Patrick's ruling of 4
# November, the draft specification's section 6) is 1 hour 30 minutes after
# its hour, the same in every zone, and the daily 12:45 AM cut-off does not
# apply to it. It was three hours until his ruling of 5 November, which
# shortened it to the draft terms' Resolution Time, since on the engine's
# count over the record from September 2024 a file later than about 80
# minutes is nearly always part of a long outage that a longer wait would not
# have covered. Every hour is worked out under the cut-off in force when it is
# written, so a rewrite carries the change back. One analysis file serves the
# hourly contracts of its hour at every place
HOURLY_CUT_AFTER = dt.timedelta(hours=1, minutes=30)
# the three messages read from the analysis file, by the .idx sidecar's names
MESSAGES = (("temp", "TMP", "2 m above ground"), ("wind", "WIND", "10 m above ground"),
            ("gust", "GUST", "10 m above ground"))
# GRIB2 discipline, parameter category and number of each variable; a message
# that carries anything else is refused whatever the sidecar called it
PARAM = {"temp": (0, 0, 0), "wind": (0, 2, 1), "gust": (0, 2, 22), "precip": (0, 1, 8)}
HOURLY_VARS = ("temp", "wind", "gust", "precip")
ANALYSIS_VARS = ("temp", "wind", "gust")     # what the write-once archive hour holds
DAILY_VARS = ("high", "low", "gust", "wind", "precip")
UNITS = {"temp": "°F", "wind": "mph", "gust": "mph", "precip": "in"}
# what a frame's int is divided by: tenths of a degree and of a mile per
# hour, hundredths of an inch
SCALE = {"temp": 10, "wind": 10, "gust": 10, "precip": 100}
# the archive keeps each value in the file's own unit (kelvin, metres per
# second, millimetres) at the file's precision, and nothing is rounded before
# the day's aggregate: the analysis fields are packed to hundredths, so four
# decimals hold them exactly and drop the float noise of the decode, and the
# precipitation is kept to a millionth of a millimetre
RAW_DIGITS = {"temp": 4, "wind": 4, "gust": 4, "precip": 6}
# the whole value a strike is compared with (the aggregate rounded once, half
# up, away from zero), the exact aggregate shown beside it, and an hourly
# value in the place file. The shown values are CUT toward zero at their
# precision, never rounded, so a mean wind of 16.4996 shows as 16.499 beside
# its whole 16 and never as a 16.500 that looks as if it should be 17; a
# temperature is exact at a thousandth of a degree, since the file holds
# hundredths of a kelvin
DAY_UNIT = {"high": 1, "low": 1, "gust": 1, "wind": 1, "precip": 0.01}
EXACT_UNIT = {"high": 0.001, "low": 0.001, "gust": 0.001, "wind": 0.001, "precip": 0.0001}
HOUR_SHOW = {"temp": 0.001, "wind": 0.001, "gust": 0.001, "precip": 0.0001}
# an hourly value as the contract shows it, in the unit its daily variable is
# rounded to (whole degrees, whole miles an hour, a hundredth of an inch),
# rounded once, half up, away from zero, the way an hourly contract on that
# hour would resolve
HOUR_UNIT = {"temp": DAY_UNIT["high"], "wind": DAY_UNIT["wind"], "gust": DAY_UNIT["gust"], "precip": DAY_UNIT["precip"]}
MPH_PER_MS = 2.2369362921
MM_PER_INCH = 25.4
K0_DEC, MPH_DEC, MM_DEC = Decimal("273.15"), Decimal("2.2369362921"), Decimal("25.4")
# the exact strings of rowShape 3 (owner's approval of display v3, 2026-11-04)
# are computed in exact rational arithmetic, never a Decimal division. A
# converted hour is written to TEXT_PLACES (a thousandth of a degree, exact from
# hundredths of a kelvin; twelve places of a mile an hour, exact from hundredths
# of a metre per second times the ten places of 2.2369362921), and a value whose
# decimal does not end, which is every inch figure but a few, is cut toward zero
# at CUT_PLACES. A stored value finer than the file's precision is written exact
K0_FRAC, MPH_FRAC, MM_FRAC = Fraction("273.15"), Fraction("2.2369362921"), Fraction("25.4")
TEXT_PLACES = {"temp": 3, "wind": 12, "gust": 12, "precip": 10}
CUT_PLACES = 10

LATTICE_PITCH = 3
LATTICE_COLS, LATTICE_ROWS = 320, 200
# each frame also carries, per place, the field on the WINDOW_HALF cells
# either side of the resolving cell (21 x 21, about 52 km across at 2.5 km),
# so the zoomed map shows the analysis at its own resolution where the 14 km
# lattice would be a flat patch. Ten is where three things meet: the window
# still fills the map at the zoom where a cell is 30 px across (that is a
# 630 px square), the page's linear placement of the window cells from one
# basis is still about 0.01 viewBox units out at the corners, under a
# hundredth of a screen pixel at the national extent and about one pixel at
# the 96x zoom the page allows (scripts/build_analysis_grid.py measures it,
# worst 0.0092, and refuses past 0.02), and 441 ints are about 1 KB
# a place, 50 KB a frame, a third of what the lattice already costs
WINDOW_HALF = 10
BACKFILL_POINT_DAYS = 30        # location values this far back, by random-access cell reads
BACKFILL_FRAME_DAYS = 7         # map frames this far back
FRAME_KEEP_DAYS = 30            # frames older than this are pruned
INDEX_DAYS = 60                 # index.json lists this many newest days; the day files themselves are kept
CLOSE_AFTER_HOURS = 48          # a day still incomplete this long after its local end is closed
LIVE_HOURS_PER_PASS = 3         # the hours a catch-up jump opens at the present (walk_candidates)
# how often the pass looks, over the backfill's reach, for hours whose
# analysis never landed and whose precipitation was never read
PRECIP_SWEEP_HOURS = 24
# listed days still without their two CSV files (every day written before
# the files existed, or a write that failed), or written in an older LAYOUT,
# are rebuilt this many a pass
REFRESH_DAYS_PER_PASS = 6
# a product whose newest hour READ is this much or more behind what should
# have landed counts as a failed pass toward the lane's alarm; short of it,
# an empty pass is the normal wait between landings
OVERDUE_HOURS = 2
# a live cursor further behind than this stops walking hour by hour: it jumps
# to the present and the span it skipped is queued for the backfill
LIVE_CATCHUP_HOURS = 24
PASS_CAP_SECONDS = 330          # the most a pass spends; the deadline is looked at before each copy,
                                # and with FETCH_TRIES x FETCH_TIMEOUT bounding a copy near 170 s it
                                # ends inside the ten minutes, and the lease, not this cap, is what
                                # keeps two passes from overlapping
RESERVE_SECONDS = 45            # kept back from the lanes for the day rebuild, prune and index
# the windowed passes (owner's ruling 2026-11-03) run every two minutes inside these minutes past
# the hour, about when NOAA publishes each file. Over the two weeks to 2026-11-03 NOAA's own
# publication times put the analysis 46 to 52 minutes after its hour (median 49) and an hour's
# precipitation 16 to 18 minutes after the hour ends, so each window opens two minutes before
# the earliest and closes two minutes after the latest. Minutes past the hour are the same in
# UTC and in every zone the lane covers. The ten-minute pass takes :48 and :18 itself
# (ops/aws/template.yaml)
WINDOWS = {"anl": (44, 54), "pcp": (14, 20)}
WINDOW_STEP = 2
WINDOW_MINUTES = tuple(sorted(m for a, b in WINDOWS.values() for m in range(a, b + 1, WINDOW_STEP)))
# a file is looked for from its window's opening, the analysis of hour H from H:44 and the
# precipitation of the hour that ends at H from H:14
LOOK_MINUTES = {kind: w[0] for kind, w in WINDOWS.items()}
# every pass ends this long before the next minute in a window, so the pass then finds the
# lease free, and has at least PASS_FLOOR_SECONDS whatever the clock says
WINDOW_YIELD_SECONDS = 10
PASS_FLOOR_SECONDS = RESERVE_SECONDS + 30
# the newest precipitation files a windowed pass asks for, the one now due and the two before
# it (_newly_due)
PCP_LIVE_HOURS = 3
# the lease lasts past the longest its holder can run, which is the pass's budget plus the 30 s
# handler.py holds back from Lambda's timeout, plus a minute for clocks. A local run has no
# budget, and Lambda's 900 s timeout stands in. A pass never renews it, so one killed before it
# lets go (out of memory) holds the lane until it expires
LEASE_SPARE_SECONDS = 90
LEASE_DEFAULT_SECONDS = 900
MAX_MESSAGE_BYTES = 16 * 1024 * 1024   # a range for the last message of a file, whose end the sidecar cannot give
STATE_EVERY = 20                # backfill hours between state writes, bounding what a timeout loses
# every NOAA call from this lane: two tries of twenty seconds, so one copy's
# four fetches at most (an analysis's sidecar and three messages) cannot hold
# the pass for more than a few minutes and the 900 s Lambda timeout is never
# the thing that stops it
FETCH_TRIES = 2
FETCH_TIMEOUT = 20

CONVENTIONS = {
    "day": "The local civil date at the place through its IANA zone. Temperature, wind and gust use every "
           "top-of-hour analysis whose local civil time falls on that date, 24 on an ordinary day, 23 on the "
           "spring-forward day (02:00 does not occur) and 25 on the fall-back day (both 01:00 analyses count). "
           "Precipitation uses the one-hour accumulations that together cover the date from midnight to "
           "midnight, the same count. Complete means every analysis of the day counts; the mean wind is over the "
           "hours that count. An hour whose analysis file never landed still adds its precipitation, which NOAA publishes "
           "as a separate file.",
    "high": "The highest hourly temperature of the day, whole degrees Fahrenheit rounded half up; "
            "Yes when the value is at or above the strike.",
    "low": "The lowest hourly temperature of the day, whole degrees Fahrenheit rounded half up; "
           "Yes when the value is at or below the strike.",
    "gust": "The highest hourly gust of the day, whole miles per hour rounded half up; "
            "Yes when the value is at or above the strike.",
    "wind": "The mean of the day's hourly sustained winds, whole miles per hour rounded half up; "
            "Yes when the value is at or above the strike.",
    "precip": "The sum of the day's hourly accumulations, inches to a hundredth rounded half up; "
              "Yes when the value is at or above the strike.",
    "rounding": "Every hourly value is converted exactly, in decimal arithmetic, and the day's aggregate is "
                "rounded once, half up, away from zero (-20.5 is -21). The exact value beside the whole one is "
                "that aggregate cut, not rounded, to a thousandth (precipitation to a ten-thousandth of an inch), "
                "so it never looks as if it rounds the other way.",
    "strike": "A value equal to the strike resolves Yes, at or above it for the high, gust, mean wind and "
              "precipitation and at or below it for the low (decided 28 September 2026). The exchange's "
              "current daily temperature and wind contracts resolve a value equal to the strike No.",
    "provisional": "RTMA resolves, precipitation included (decided 29 September 2026). A place-day is final at "
                   "its cut-off, 12:45 AM local time on the day after (decided 14 October 2026), on the files "
                   "published on NOAA Open Data or NOMADS by then, and provisional until then. Each hour's copy is "
                   "fixed once both servers' copies are in, or at the first ten-minute pass after the cut-off on "
                   "the copies read by then, and the day turns final once every hour's copy is fixed, so a final "
                   "day never changes (decided 4 November 2026). A file published after the cut-off is shown and "
                   "never counted, a copy read after its hour was fixed is recorded and never used, and an hour "
                   "missing at the cut-off leaves the value on the hours available.",
    "review": "A place-day with no hour published by its cut-off has no value and is marked for event review, "
              "as the draft terms provide.",
    "missing": "Every file is read from NOAA Open Data and from NCEP's NOMADS server, and a file on either counts "
               "as published, from the first time it was on either, its Last-Modified time there. A file missing "
               "from one server counts from the other. Where the two copies differ, NOMADS's counts when it was "
               "published by the cut-off, and otherwise NOAA Open Data's. A place the copy that counts has no "
               "value for has no value that hour (decided 4 November 2026). A download whose message fails the "
               "check counts only once a second download returns the same bytes.",
    "hourly": "Each row is the analysis at the top of the hour and the precipitation over the hour that starts "
              "then, which NOAA files under the hour it ends. Station report conventions (the last report in "
              "the hour, specials, the tenths group) have no analogue here.",
    "hourlyContract": "Each hour's temperature is also the hourly temperature contract's value, whole degrees "
                      "Fahrenheit rounded once, half up, away from zero (decided 4 November 2026). Its cut-off is "
                      "1 hour 30 minutes after the hour in every zone (decided 5 November 2026; three hours until "
                      "then), and the daily 12:45 AM cut-off does not apply to it. It is final once NOMADS's copy of "
                      "the analysis is in, if published by that cut-off; "
                      "otherwise at the first ten-minute pass after the cut-off, on NOAA Open Data's copy if "
                      "published by then; with neither, there is no value and the hour is marked for review. A copy "
                      "read after that pass is recorded and never used. An hour read before the lane kept each "
                      "server's copy is final at the cut-off on the copy read, or at NOMADS's publication time where "
                      "one by the cut-off is on record, and in review where NOAA Open Data published after the "
                      "cut-off.",
    "cell": "The settlement grid cell approved on 2026-09-29: the 2.5 km cell whose square contains the main "
            "city's City Hall (in Miami the nearest land cell to the city's administration building, whose own "
            "cell counts as water). The value is the analysis at the cell, never an average over the "
            "square. RTMA files its precipitation on a grid 200 columns narrower, and the same point there is "
            "200 columns to the left in the same row.",
    "lattice": "The map samples each hourly field onto a 320 by 200 lattice at 3 screen pixels, about 14 km "
               "between points nationally. It is a subsample for display; the place values come from the "
               "full grid.",
    "window": "Each hourly frame also carries, for every place, the field on the 21 by 21 cells centred on its "
              "resolving cell, ten cells either way on the 2.5 km grid, about 52 km across. The zoomed map draws "
              "those cells at their own resolution in place of the lattice and outlines the resolving cell; the "
              "place value is that cell's own.",
    "resolvedDay": "A day is fully resolved when every one of the sixty-seven places has its RTMA day final and "
                   "its precipitation total resolved. The settlement display opens on the newest such day, and "
                   "its calendar marks each day final, provisional, under review or closed.",
    "units": "Kelvin to Fahrenheit as (K - 273.15) x 9/5 + 32, metres per second to miles per hour by "
             "2.2369362921, millimetres to inches by 1/25.4, all in decimal arithmetic.",
}
VARIABLES = {
    "high": {"unit": "°F", "hourly": "temp", "rule": CONVENTIONS["high"]},
    "low": {"unit": "°F", "hourly": "temp", "rule": CONVENTIONS["low"]},
    "gust": {"unit": "mph", "hourly": "gust", "rule": CONVENTIONS["gust"]},
    "wind": {"unit": "mph", "hourly": "wind", "rule": CONVENTIONS["wind"]},
    "precip": {"unit": "in", "hourly": "precip", "rule": CONVENTIONS["precip"]},
}


# ------------------------------------------------------------------ small helpers
def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_iso(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _stamp(t: dt.datetime) -> str:
    """The file-name form of an hour, YYYYMMDDTHHZ."""
    return t.astimezone(dt.timezone.utc).strftime("%Y%m%dT%HZ")


def _from_stamp(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y%m%dT%HZ").replace(tzinfo=dt.timezone.utc)


def _floor_hour(t: dt.datetime) -> dt.datetime:
    return t.astimezone(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)


def _half_up_int(x: float) -> int:
    # snapshots._half_up, copied rather than imported: it is a private name of
    # another lane, and the rule has to read here where the daily values are
    # made. Round half away from zero (-20.5 is -21), which is what a
    # published table shows and is not what round() does.
    return int(math.floor(x + 0.5)) if x >= 0 else -int(math.floor(-x + 0.5))


def _dec(x) -> Decimal:
    """A value as a Decimal: a Decimal as it is, a float through its shortest
    repr (to nine decimals, which drops the noise of a sum like 70.549999999
    and nothing a file holds)."""
    return x if isinstance(x, Decimal) else Decimal(repr(round(float(x), 9)))


def half_up(x, unit: float = 1):
    """x rounded half up, away from zero, to a multiple of unit (1, 0.01,
    0.0001), in decimal arithmetic: 70.55 in binary is a hair under 70.55 and
    would round the wrong way in floats. Whole units return an int."""
    if x is None:
        return None
    q = _dec(x).quantize(Decimal(repr(unit)) if unit != 1 else Decimal(1), rounding=ROUND_HALF_UP)
    return int(q) if unit == 1 else float(q)


def cut(x, unit: float):
    """x cut toward zero to a multiple of unit, for a value shown beside a
    rounded one, so it never looks as if it rounds the other way."""
    if x is None:
        return None
    return float(_dec(x).quantize(Decimal(repr(unit)), rounding=ROUND_DOWN))


def exact(var: str, raw) -> Optional[Decimal]:
    """A stored value in the contract's unit, exactly: kelvin to degrees
    Fahrenheit, metres per second to miles per hour, millimetres to inches,
    in decimal arithmetic on the stored value, so a value the file holds
    exactly is converted exactly. 250.65 K is -8.5 F, which binary floating
    point makes -8.49999999999995, a hair short of the half: rounded as it
    stands that is -8 where the exact value is -9."""
    if raw is None:
        return None
    d = _dec(raw)
    if var == "temp":
        return (d - K0_DEC) * 9 / 5 + 32
    if var in ("wind", "gust"):
        return d * MPH_DEC
    return d / MM_DEC


def _frac(raw) -> Fraction:
    """A stored value as an exact fraction of the decimal the lane reads it as (_dec)."""
    return Fraction(_dec(raw))


def exact_frac(var: str, raw) -> Optional[Fraction]:
    """exact(), in rational arithmetic, so a quotient that does not end in
    decimal (millimetres to inches) is held exactly and cut once when written."""
    if raw is None:
        return None
    v = _frac(raw)
    if var == "temp":
        return (v - K0_FRAC) * 9 / 5 + 32
    if var in ("wind", "gust"):
        return v * MPH_FRAC
    return v / MM_FRAC


def _ends(fr: Fraction) -> bool:
    """Whether a fraction's decimal ends, which it does when its denominator has no prime but 2 and 5."""
    d = fr.denominator
    for p in (2, 5):
        while d % p == 0:
            d //= p
    return d == 1


def _digits(fr: Fraction, places: int) -> str:
    """A fraction cut toward zero to `places` decimals, written with exactly that many."""
    n = abs(fr.numerator) * 10 ** places // fr.denominator
    s = str(n).rjust(places + 1, "0")
    return ("-" if fr < 0 and n else "") + (s[:-places] + "." + s[-places:] if places else s)


def cut_text(fr: Optional[Fraction], places: int = CUT_PLACES) -> Optional[str]:
    """A value cut toward zero at `places`, as the mean wind and every
    inch figure are written whether or not their decimal ends."""
    return None if fr is None else _digits(fr, places)


def fixed_text(fr: Optional[Fraction], places: int) -> Optional[str]:
    """A value written exactly where its decimal ends, with at least `places`
    decimals and more only for a stored value finer than the file's, and cut
    toward zero at CUT_PLACES where it does not end."""
    if fr is None:
        return None
    if not _ends(fr):
        return _digits(fr, CUT_PLACES)
    need = places
    while (fr * 10 ** need).denominator != 1:
        need += 1
    return _digits(fr, need)


def raw_text(var: str, raw) -> Optional[str]:
    """A stored value as the file holds it, in its own unit: kelvin and
    metres per second to two decimals, the hundredths they are packed to,
    and millimetres with no trailing zeros, "0" for none."""
    if raw is None:
        return None
    d = _dec(raw)
    if var == "precip":
        return "0" if d == 0 else format(d.normalize(), "f")
    if d.as_tuple().exponent >= -2:
        return format(d.quantize(Decimal("0.01")), "f")
    return format(d.normalize(), "f")


def x_text(var: str, raw) -> Optional[str]:
    """A stored value converted to the contract's unit before rounding, as
    an exact string (TEXT_PLACES), the hover's and the CSV files' figure."""
    return fixed_text(exact_frac(var, raw), TEXT_PLACES[var])


def k_to_f(k: float) -> float:
    return (k - 273.15) * 9.0 / 5.0 + 32.0


def ms_to_mph(v: float) -> float:
    return v * MPH_PER_MS


def mm_to_inch(v: float) -> float:
    return v / MM_PER_INCH


CONVERT: Dict[str, Callable[[float], float]] = {"temp": k_to_f, "wind": ms_to_mph, "gust": ms_to_mph, "precip": mm_to_inch}


def _read_json(store: Storage, key: str) -> Optional[dict]:
    raw = store.get(key)
    if not raw:
        return None
    try:
        body = json.loads(raw)
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _read_gz(store: Storage, key: str) -> Optional[dict]:
    raw = store.get(key)
    if not raw:
        return None
    try:
        body = json.loads(gzip.decompress(raw))
    except (OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def _dump(doc) -> bytes:
    return json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _gz(doc) -> bytes:
    return gzip.compress(_dump(doc), compresslevel=6)


def _fail(status: dict, text: str) -> None:
    """Record one failure the pass continues past. Texts start with the
    product's name where one is to blame, which is how health_results counts
    them toward that product's streak. A failure no product is to blame for
    (the lease, the state, a write) is the lane's own, and only that kind can
    fail the pass (analysis_pass)."""
    status["errors"].append(text)
    status["failed"] += 1
    if not text.startswith(tuple(p + " " for p in PRODUCTS)):
        status["laneFailed"] = status.get("laneFailed", 0) + 1


def load_locations(path: str = LOCATIONS_PATH) -> list:
    with open(path) as fh:
        return json.load(fh)


def load_lattice(path: str = LATTICE_PATH) -> dict:
    with open(path) as fh:
        return json.load(fh)


# ------------------------------------------------------------------ the local day
def day_end(day_iso: str, tz: str) -> dt.datetime:
    """The UTC instant the local day ends (the next day's 00:00 local)."""
    zone = ZoneInfo(tz)
    y, m, d = (int(p) for p in day_iso.split("-"))
    nxt = dt.date(y, m, d) + dt.timedelta(days=1)
    return dt.datetime(nxt.year, nxt.month, nxt.day, 0, 0, tzinfo=zone, fold=0).astimezone(dt.timezone.utc)


def local_day_hours(day_iso: str, tz: str) -> List[dt.datetime]:
    """The distinct UTC instants of every top-of-hour analysis whose local
    civil time falls on that date, in order.

    Built with ZoneInfo and never a fixed offset, so daylight time is right
    on every day of the year. The day runs from 00:00 local to the next
    day's 00:00 local, so it is 24 instants on an ordinary day, 23 on the
    spring-forward day (02:00 local does not occur, and no instant is
    counted twice for it) and 25 on the fall-back day (01:00 local occurs
    twice and both analyses fall on the date). The owner's decision of
    2026-09-27: the day is the civil date, and every analysis on it counts
    once.
    """
    zone = ZoneInfo(tz)
    y, m, d = (int(p) for p in day_iso.split("-"))
    t = dt.datetime(y, m, d, 0, 0, tzinfo=zone, fold=0).astimezone(dt.timezone.utc)
    end = day_end(day_iso, tz)
    out = []
    while t < end:
        out.append(t)
        t += dt.timedelta(hours=1)
    return out


def hour_label(t: dt.datetime, tz: str) -> str:
    """The local hour of an instant as the two-digit label the hour table
    shows; the second 01:00 of the fall-back night is '01*' so the two rows
    that share a label can be told apart (the UTC stamp sits beside it)."""
    lt = t.astimezone(ZoneInfo(tz))
    return f"{lt:%H}" + ("*" if lt.fold else "")


def local_day_of(t: dt.datetime, tz: str) -> str:
    """The local date a UTC instant falls on in a zone: exactly one."""
    return t.astimezone(ZoneInfo(tz)).date().isoformat()


def review_entry(day_iso: str, tz: str) -> dict:
    """The product-day of a place with no hour read by its cut-off: no value,
    and marked for event review, the precipitation too."""
    return {"hours": 0, "of": len(local_day_hours(day_iso, tz)), "complete": False, "high": None, "low": None,
            "gust": None, "wind": None, "final": False, "finalAt": None, "review": True, "closed": False,
            "precip": {"value": None, "exact": None, "hours": 0, "resolved": False, "resolvedAt": None,
                       "review": True}}


def day_cutoff(day_iso: str, tz: str) -> dt.datetime:
    """A place-day's cut-off: 12:45 AM local time on the day after (owner's
    ruling 2026-10-14, the draft terms). Clocks do not change between midnight
    and 12:45 AM in the lane's zones, so it is the day's end plus 45 minutes."""
    return day_end(day_iso, tz) + CUTOFF_AFTER_DAY


def _in_time(published: Optional[str], cutoff_iso: str) -> bool:
    """Whether a file published at `published` counts for a day with this
    cut-off (both UTC ISO strings, which compare in time order). A document
    read before 2026-10-14 carries no publication time and counts: its day
    resolved under the earlier rule."""
    return published is None or published <= cutoff_iso


def _counted(product_hours: dict, tz: str, day_iso: str) -> Tuple[list, str, list, list, list]:
    """A place-day's hours as day_summary counts them: the day's hours, its
    cut-off, the hours read, and of those the analyses that count (published
    by the cut-off) and the accumulations that count (a value, published by
    the cut-off), each a list of (hour, record) in time order. Whether an hour
    counts is its own file's matter, so the hours that count up to any hour
    are these up to it, which `running` and `running_texts` both rely on."""
    hours = local_day_hours(day_iso, tz)
    cut_iso = _iso(day_cutoff(day_iso, tz))
    read = []
    for h in hours:
        rec = product_hours.get(_iso(h))
        if rec is not None:
            read.append((h, rec))
    anl_rows = [(h, rec) for h, rec in read if rec.get("anl", True) and _in_time(rec.get("pub"), cut_iso)]
    pcp_rows = [(h, rec) for h, rec in read
                if rec.get("precip") is not None and _in_time(rec.get("pcpPub"), cut_iso)]
    return hours, cut_iso, read, anl_rows, pcp_rows


def day_summary(product_hours: dict, tz: str, day_iso: str, now: dt.datetime, prior: Optional[dict] = None,
                missing: Optional[set] = None, pmissing: Optional[set] = None, pending: bool = False) -> Optional[dict]:
    """One product-day at a place, from its exact hourly values.

    product_hours maps the hour's UTC ISO string to {temp, wind, gust, precip}
    exact values (None where the product had no value at that hour), with
    `pub` and `pcpPub`, the publication times of the analysis and the
    precipitation file, and `mm`, the stored millimetres the precipitation
    was converted from. An entry with `anl` False is an hour whose analysis
    file never landed: it carries only its precipitation. The result is the
    contract's product-day shape, or None when no hour of the day has been
    read (build_day gives such a place its review entry at the cut-off).

    The day's precipitation total is the stored millimetres added up and
    converted once. Each hour's inches are a division by 25.4 that Decimal
    stops at its 28th digit, so a sum of them can sit a hair under a total
    the millimetres hold exactly. 15.875 mm is 0.625 in, which resolves 0.63,
    and summed hour by hour it came to 0.6249999...9 and resolved 0.62 on
    about one split in seven. A caller that gives inches without `mm` (the
    tests' own hours) has its inches added as given.

    The day is final at its cut-off (owner's ruling 2026-10-14) on the hours
    whose files were published by then: an analysis counts when its file was,
    an accumulation when its file was and holds a value at the place. A file
    published later never counts, so the summary comes out the same whenever
    it is rebuilt. Before the cut-off the values run over the hours read. A
    place-day with no analysis that counts at the cut-off has no value and
    goes to event review (`review`), and one with no accumulation that counts
    has `precip.review`. Since 2026-11-04 the day waits past its cut-off
    while a file of its rows may still change for it (`pending`, build_day's
    _pending), at most until the first ten-minute pass after the cut-off for
    the files the live lane holds, and longer only while a catch-up span or
    the backfill has yet to reach the day, so a day shown final never changes
    (owner's ruling). `prior`, `missing` and `pmissing` are accepted from
    callers that still pass them and are no longer used."""
    hours, cut_iso, read, anl_rows, pcp_rows = _counted(product_hours, tz, day_iso)
    at_cutoff = now >= day_cutoff(day_iso, tz) and not pending
    if not read:
        return None
    n = len(anl_rows)
    expected = len(hours)
    complete = n == expected

    def series(var):
        return [(h, _dec(rec[var])) for h, rec in anl_rows if rec.get(var) is not None]

    def extreme(var, daily, pick):
        s = series(var)
        if not s:
            return None
        # the first hour at which the extreme stands, which is what a
        # reader checking the hour table expects to find
        if pick is max:
            at, v = max(s, key=lambda p: (p[1], -p[0].timestamp()))
        else:
            at, v = min(s, key=lambda p: (p[1], p[0].timestamp()))
        return {"value": half_up(v, DAY_UNIT[daily]), "exact": cut(v, EXACT_UNIT[daily]), "at": _iso(at)}

    winds = series("wind")
    precip = [(h, _dec(rec["precip"])) for h, rec in pcp_rows]
    out: dict = {"hours": n, "of": expected, "complete": complete}
    out["high"] = extreme("temp", "high", max)
    out["low"] = extreme("temp", "low", min)
    out["gust"] = extreme("gust", "gust", max)
    # the aggregate is exact (decimal arithmetic on unrounded values) and is
    # rounded once, here, for the whole value (owner's decision 2026-09-29)
    if winds:
        mean = sum(v for _, v in winds) / len(winds)
        out["wind"] = {"value": half_up(mean, DAY_UNIT["wind"]), "exact": cut(mean, EXACT_UNIT["wind"])}
    else:
        out["wind"] = None
    final = at_cutoff and n > 0
    out["final"] = final
    out["finalAt"] = cut_iso if final else None
    out["review"] = at_cutoff and n == 0
    # the closed state ended with the cut-off rule: every day is final or in review at its cut-off
    out["closed"] = False
    if precip:
        # one conversion of the summed millimetres (see the docstring), so the
        # total is exact wherever the millimetres put it on a half hundredth
        # or a ten-thousandth of an inch, and it is rounded once below
        if all(rec.get("mm") is not None for _, rec in pcp_rows):
            total = exact("precip", sum(_dec(rec["mm"]) for _, rec in pcp_rows))
        else:
            total = sum(v for _, v in precip)
        out["precip"] = {"value": half_up(total, DAY_UNIT["precip"]), "exact": cut(total, EXACT_UNIT["precip"]),
                         "hours": len(precip), "resolved": at_cutoff, "resolvedAt": cut_iso if at_cutoff else None}
    elif at_cutoff:
        out["precip"] = {"value": None, "exact": None, "hours": 0, "resolved": False, "resolvedAt": None,
                         "review": True}
    else:
        out["precip"] = None
    return out


def running(product_hours: dict, tz: str, day_iso: str, now: dt.datetime, through: dt.datetime) -> Optional[dict]:
    """The day's values so far, from day_summary itself over the hours up to
    and including `through`, so every running figure is resolved by the code that
    resolves the day, with its rules on what counts (a file published after
    the cut-off never does, a missing hour leaves the values on the hours
    there are), and the running values of the day's last counted hour are the
    day's own by construction. The page shows these and adds nothing up.

    `hours` is the analyses counted so far; `high`, `low` and `gust` carry
    their value, exact figure and hour, `wind` its value and exact figure,
    and `precip` its value, exact figure and the accumulations counted, or
    None where nothing counts yet. The flags that turn on the clock (final,
    review, resolved) are left to the day's summary."""
    upto = _iso(through)
    s = day_summary({iso: rec for iso, rec in product_hours.items() if iso <= upto}, tz, day_iso, now)
    if s is None:
        return None
    p = s.get("precip") or {}
    return {"hours": s["hours"], "high": s["high"], "low": s["low"], "gust": s["gust"], "wind": s["wind"],
            "precip": None if p.get("value") is None else {"value": p["value"], "exact": p["exact"], "hours": p["hours"]}}


def running_texts(product_hours: dict, tz: str, day_iso: str) -> Dict[str, dict]:
    """Each read hour's `runX` (rowShape 3), keyed by its UTC stamp, which is
    the day's values so far before rounding, as exact strings, over the hours `running`
    counts (_counted, day_summary's own rule), in one pass in time order. The
    high and low are exact to a thousandth and the peak gust to twelve places;
    the mean wind and the precipitation total are cut toward zero at ten, the
    total converted once from the summed millimetres as day_summary does. A
    value is None while nothing counts for it, as in `run`."""
    _, _, read, anl_rows, pcp_rows = _counted(product_hours, tz, day_iso)
    anl = {_iso(h): rec for h, rec in anl_rows}
    pcp = {_iso(h): rec for h, rec in pcp_rows}
    hi = lo = peak = None
    wind, winds = Fraction(0), 0
    mm, inch, all_mm, wet = Fraction(0), Fraction(0), True, 0
    out = {}
    for h, _ in read:
        k = _iso(h)
        a = anl.get(k)
        if a is not None:
            if a.get("temp") is not None:
                v = _frac(a["temp"])
                hi = v if hi is None or v > hi else hi
                lo = v if lo is None or v < lo else lo
            if a.get("gust") is not None:
                v = _frac(a["gust"])
                peak = v if peak is None or v > peak else peak
            if a.get("wind") is not None:
                wind += _frac(a["wind"])
                winds += 1
        p = pcp.get(k)
        if p is not None:
            wet += 1
            inch += _frac(p["precip"])
            if p.get("mm") is None:
                all_mm = False
            else:
                mm += _frac(p["mm"])
        total = None
        if wet:
            total = fixed_text(mm / MM_FRAC, CUT_PLACES) if all_mm else cut_text(inch)
        out[k] = {"high": fixed_text(hi, TEXT_PLACES["temp"]), "low": fixed_text(lo, TEXT_PLACES["temp"]),
                  "gust": fixed_text(peak, TEXT_PLACES["gust"]),
                  "wind": cut_text(wind / winds) if winds else None, "precip": total}
    return out


def resolves(var: str, value, strike) -> Optional[bool]:
    """Whether a hypothetical contract on a daily variable resolves Yes at a
    strike: the low resolves on a value at or below the strike, everything
    else on a value at or above it, so the boundary case is Yes whichever way
    the contract runs (owner's decision 2026-09-28; it was strict before, as
    the exchange's current contracts still are). The rule the page draws its
    ladders with; kept here so a test can pin it and a reader can find it in
    one place. None while the day has no value."""
    if value is None:
        return None
    return value <= strike if var == "low" else value >= strike


def _frame_ints(sampled, scale: int, convert: Optional[Callable]) -> list:
    """Lattice values (None off the grid or missing) as ints, value times
    scale rounded half up. The product is rounded to six places first so a
    value that is x.5 in decimal but a hair under in binary (68.05 * 10 is
    680.4999...) rounds the way half_up would."""
    out = []
    for v in sampled:
        if v is None:
            out.append(None)
            continue
        if convert is not None:
            v = convert(v)
        out.append(_half_up_int(round(v * scale, 6)))
    return out


def frame_from_field(values, lattice_indices: list, scale: int, convert: Optional[Callable] = None) -> list:
    """The lattice sample of a decoded field as ints (value times scale,
    half up), None where the lattice point is off the grid (-1) or the cell
    is missing in the bitmap. Only the sampled cells are converted, not the
    3.7 million of the field."""
    return _frame_ints([None if k < 0 else values[k] for k in lattice_indices], scale, convert)


def window_indices(cell, grid: dict, half: int = WINDOW_HALF) -> list:
    """The grid indices of the window round a resolving cell (i, j): row-major
    with the j offset outer from -half to +half and the i offset inner, so
    entry (b + half) * (2 half + 1) + (a + half) is cell (i + a, j + b) at
    k = (j + b) * Ni + (i + a). A cell off the grid is -1 (a place near the
    grid edge; the page draws nothing there), never clipped to the edge,
    because a clipped index would be a real cell in the wrong place."""
    i, j = cell[0], cell[1]
    ni, nj = grid["Ni"], grid["Nj"]
    out = []
    for b in range(-half, half + 1):
        jj = j + b
        for a in range(-half, half + 1):
            ii = i + a
            out.append(jj * ni + ii if 0 <= ii < ni and 0 <= jj < nj else -1)
    return out


def window_from_field(values, window_ks: list, scale: int, convert: Optional[Callable] = None) -> list:
    """The window of a decoded field as ints on the frame's scale: the same
    rule as the lattice, None off the grid or missing in the bitmap."""
    return frame_from_field(values, window_ks, scale, convert)


def window_doc(grid_name: str, vals: list) -> dict:
    """One place's window as the frame carries it (docs/analysis.md section 4)."""
    return {"grid": grid_name, "half": WINDOW_HALF, "values": vals}


def own_doc(var: str, values: Optional[dict]) -> dict:
    """Each place's value of `var` at its own cell as its hourly row in the
    place file gives it, from the stored values the frame was drawn from: an
    analysis's {var: raw} or a precipitation file's millimetres, by place.
    `value` is the contract's, rounded once, and `exact` is cut beside it. The
    window's centre holds the same cell rounded to a tenth, and a tenth ending
    in .5 can sit either side of the contract's half (76.4996 F is 76.5 there
    and 76 here), so a page showing an hour as the contract takes it reads
    this (owner, 2026-11-12; docs/analysis.md section 4)."""
    out = {}
    for lid, v in (values or {}).items():
        x = exact(var, v.get(var) if isinstance(v, dict) else (v if var == "precip" else None))
        if x is not None:
            out[lid] = {"value": half_up(x, HOUR_UNIT[var]), "exact": cut(x, HOUR_SHOW[var])}
    return out


# ------------------------------------------------------------------ NOAA objects
def _bucket(product: str) -> str:
    # gov_weather owns the bucket constants; the default is the same string,
    # so this module imports whether or not that file has them yet
    return getattr(gw, "NODD_" + product.upper(), NODD_DEFAULT[product])


def _base(product: str, nomads: bool = False) -> str:
    if nomads:
        return getattr(gw, "NOMADS_" + product.upper(), NOMADS_DEFAULT[product])
    return _bucket(product)


def anl_url(product: str, t: dt.datetime, nomads: bool = False) -> str:
    p = PRODUCTS[product]
    t = t.astimezone(dt.timezone.utc)
    return f"{_base(product, nomads)}/{p['dir']}.{t:%Y%m%d}/{p['anl'].format(hh=f'{t:%H}')}"


def pcp_url(product: str, t: dt.datetime, nomads: bool = False) -> str:
    """The precipitation file whose hour HH covers HH-1 to HH UTC, so the
    accumulation for the hour ending at t carries t's own stamp."""
    p = PRODUCTS[product]
    t = t.astimezone(dt.timezone.utc)
    return f"{_base(product, nomads)}/{p['dir']}.{t:%Y%m%d}/{p['pcp'].format(ymdh=f'{t:%Y%m%d%H}')}"


def file_name(product: str, kind: str, t: dt.datetime) -> str:
    """A file's name as NCEP names it, under its day directory, the same on
    both servers: hour t's analysis, or the precipitation file of the hour
    that ends at t (anl_url and pcp_url end with it)."""
    p = PRODUCTS[product]
    t = t.astimezone(dt.timezone.utc)
    name = p["anl"].format(hh=f"{t:%H}") if kind == "anl" else p["pcp"].format(ymdh=f"{t:%Y%m%d%H}")
    return f"{p['dir']}.{t:%Y%m%d}/{name}"


def settled_after(product: str, t: dt.datetime) -> dt.datetime:
    """Half an hour after hour t's file usually lands: from then a file NOAA
    Open Data does not have is looked for on NOMADS."""
    return t + dt.timedelta(minutes=PRODUCTS[product]["lagMinutes"] + NOMADS_AFTER_MINUTES)


def nomads_ok(product: str, t: dt.datetime, now: dt.datetime) -> bool:
    """Whether a file for hour t not on NOAA Open Data is looked for on
    NOMADS: from settled_after, and while NOMADS still keeps it."""
    return settled_after(product, t) <= now <= t + dt.timedelta(hours=NOMADS_KEEP_HOURS)


def nomads_ok_pcp(product: str, t: dt.datetime, now: dt.datetime) -> bool:
    """Whether a precipitation file for the hour ending at t that NOAA Open
    Data does not have is looked for on NOMADS: from PCP_NOMADS_AFTER_MINUTES
    after the hour ends, so the day's last accumulation is looked for before
    the cut-off, and while NOMADS still keeps it."""
    return t + dt.timedelta(minutes=PCP_NOMADS_AFTER_MINUTES) <= now <= t + dt.timedelta(hours=NOMADS_KEEP_HOURS)


def check_message(msg: bytes, var: str, grid: dict, t: "Optional[dt.datetime]" = None) -> Optional[str]:
    """Why a message is not the one wanted, or None. Section 3 must be the
    grid the cells were computed on, since a cell index on any other grid is
    a different place; the parameter must be the variable the sidecar named;
    and when the hour is given the message must be valid then (the analysis
    reference time, or the end of the precipitation window), so a misfiled
    object is refused rather than read as another hour."""
    try:
        g = grib2.grid_def(msg)
    except Exception as e:  # noqa: BLE001
        return f"grid definition unreadable ({type(e).__name__})"
    if not grib2.same_grid(g, grid):
        return "message is on another grid"
    try:
        pd = grib2.product_def(msg)
    except Exception as e:  # noqa: BLE001
        return f"product definition unreadable ({type(e).__name__})"
    want = PARAM[var]
    got = (pd.get("discipline"), pd.get("category"), pd.get("number"))
    if got != want:
        return f"parameter {got} is not {var} {want}"
    if var == "precip" and pd.get("acc_hours") not in (None, 1):
        return f"accumulation is {pd.get('acc_hours')} h, not 1"
    if t is not None:
        got = None
        if var == "precip":
            got = pd.get("end")
        else:
            rt = getattr(grib2, "ref_time", None)
            if rt is not None:
                try:
                    got = rt(msg)
                except Exception as e:  # noqa: BLE001
                    return f"reference time unreadable ({type(e).__name__})"
        if got is not None and tuple(got[:4]) != (t.year, t.month, t.day, t.hour):
            return f"message is valid {tuple(got[:4])}, not {(t.year, t.month, t.day, t.hour)}"
    return None


def _grid(name: str) -> dict:
    return grib2.WEXP if name == "wexp" else grib2.G184


def _sample(msg: bytes, var: str, grid_name: str, ks: list, lattice: Optional[dict],
            windows: Optional[list] = None) -> Tuple[list, Optional[list], Optional[list]]:
    """The values at the location cells ks and, when a lattice is given, the
    map frame and (when `windows`, one index list per place from
    window_indices, is given) each place's window on the frame's scale. A
    simple-packed message without a bitmap (every analysis field) is read
    cell by cell: the 64,000 lattice cells, the fifty places and the 22,050
    window cells, never the 3.7 million floats of the field, which is what
    keeps an hour with frames inside the Lambda's memory. Anything else (the
    complex-packed precipitation) is decoded whole and sampled."""
    if lattice is None:
        return grib2.decode_cells(msg, ks), None, None
    lat = lattice[grid_name]
    windows = windows or []
    if grib2.random_access(msg):
        want = [k for k in lat if k >= 0]
        wks = [k for w in windows for k in w if k >= 0]
        vals = grib2.decode_cells(msg, want + ks + wks)
        it = iter(vals)
        sampled = [next(it) if k >= 0 else None for k in lat]
        cells = vals[len(want):len(want) + len(ks)]
        it = iter(vals[len(want) + len(ks):])
        wins = [_frame_ints([next(it) if k >= 0 else None for k in w], SCALE[var], CONVERT[var]) for w in windows]
        return cells, _frame_ints(sampled, SCALE[var], CONVERT[var]), wins
    field = grib2.decode(msg)
    frame = frame_from_field(field, lat, SCALE[var], CONVERT[var])
    wins = [window_from_field(field, w, SCALE[var], CONVERT[var]) for w in windows]
    cells = [field[k] for k in ks]
    del field
    return cells, frame, wins


# The readers and writers of the archive's earlier shape, in which one file
# was read from one server (NOAA Open Data, else NOMADS once the file was
# overdue there) and stored as the archive hour and a precipitation key that
# later reads filled in. Every hour archived before 2026-11-03 has that shape, and build_day and
# file_view still read it; the passes now keep each server's copy instead
# (read_copy, write_copy, settle_cuts) and write this shape no more. The tests make
# hours of that shape with these.
class HourResult:
    """What reading one product-hour came to: `status` is ok, absent (the
    analysis file is not on the bucket, and the hour's precipitation, read
    all the same, may still be in `precip`) or error; `doc` is the write-once
    archive hour, `precip` the precipitation document (None when its file
    was absent or refused, with `precip_error` saying why), `frames` the
    lattice samples when they were asked for, and `windows` beside them the
    per-place windows by variable ({var: {place id: window_doc}})."""
    def __init__(self, status: str, doc: Optional[dict] = None, frames: Optional[dict] = None,
                 error: Optional[str] = None, precip: Optional[dict] = None, precip_error: Optional[str] = None,
                 windows: Optional[dict] = None, precip_status: Optional[str] = None):
        self.status, self.doc, self.frames, self.error = status, doc, frames, error
        self.precip, self.precip_error, self.windows = precip, precip_error, windows
        # ok, absent or error for the precipitation file read with the hour;
        # None when it was not read
        self.precip_status = precip_status


def read_precip(product: str, t: dt.datetime, locs: list, lattice: Optional[dict], now: dt.datetime) -> HourResult:
    """Fetch and decode one product-hour's precipitation file: `ok` with the
    precipitation document (and the frame when a lattice is given), `absent`
    when the file is not on the bucket, `error` with the reason otherwise.
    Nothing raises out of here: a short body, a refused packing or a 5xx
    after the retries is an error the caller records."""
    p = PRODUCTS[product]
    # the window is on the grid the file is on: G184 for the RTMA
    # precipitation, wexp for everything else, the same choice as the cell
    grid_name = p["pcpGrid"]
    ks = [loc["cell"][grid_name][2] for loc in locs]
    source = "nodd"
    meta: dict = {}
    try:
        raw = gw.fetch_bytes(pcp_url(product, t), tries=FETCH_TRIES, timeout=FETCH_TIMEOUT, meta=meta)
        if raw is None and nomads_ok_pcp(product, t, now):
            meta = {}
            raw = gw.fetch_bytes(pcp_url(product, t, nomads=True), tries=FETCH_TRIES, timeout=FETCH_TIMEOUT,
                                 meta=meta)
            source = "nomads"
        if raw is None:
            return HourResult("absent")
        why = check_message(raw, "precip", _grid(grid_name), t)
        if why:
            return HourResult("error", error=why)
        wins = None if lattice is None else [window_indices(loc["cell"][grid_name], _grid(grid_name)) for loc in locs]
        cells, frame, wvals = _sample(raw, "precip", grid_name, ks, lattice, wins)
    except Exception as e:  # noqa: BLE001
        return HourResult("error", error=f"{type(e).__name__}: {e}")
    # millimetres, as the file holds them; nothing is converted or rounded
    # until the day's total (owner's decision 2026-09-29)
    values = {loc["id"]: (None if v is None else round(float(v), RAW_DIGITS["precip"])) for loc, v in zip(locs, cells)}
    doc = precip_doc(product, t, values, now, read=True)
    doc["source"] = source
    # when the server says the file was written: the cut-off is judged on it
    doc["published"] = meta.get("lastModified")
    if lattice is None:
        return HourResult("ok", doc=doc)
    return HourResult("ok", doc=doc, frames={"precip": frame},
                      windows={"precip": {loc["id"]: window_doc(grid_name, w) for loc, w in zip(locs, wvals)}})


def precip_doc(product: str, t: dt.datetime, values: dict, now: dt.datetime, read: bool) -> dict:
    """The precipitation document of a product-hour: `read` when a file was
    decoded, `complete` when every place has a value, `missing` the places
    that do not (their cells were absent from the bitmap), `values` in
    millimetres, and `source` (nodd or nomads) where the file came from."""
    missing = sorted(lid for lid, v in values.items() if v is None)
    return {"schema": SCHEMA, "product": product, "valid": _iso(t), "written": _iso(now),
            "read": read, "complete": not missing, "missing": missing, "values": values}


def read_hour(product: str, t: dt.datetime, locs: list, lattice: dict, frames: bool, now: dt.datetime,
              precip_if_absent: bool = True, precip: bool = True) -> HourResult:
    """Fetch and decode one product-hour: the three analysis messages, then
    the precipitation file. With `frames` every field is sampled onto the
    lattice as well as at the fifty cells. Nothing raises out of here: any
    failure in a fetch or a decode is an `error` result the lane records
    against the product and moves past.

    An hour whose analysis file is not on the bucket is `absent`, and its
    precipitation, a separate file, is read all the same unless
    `precip_if_absent` is False (the store already holds all of it). The
    RTMA analysis of 2026-09-23 19Z never reached NOAA Open Data while its
    precipitation file did, and until 2026-09-29 the lane dropped that
    hour's precipitation with the missing analysis. With `precip` False the
    precipitation file is not read at all, whether or not the analysis is
    there, because the store holds all of it already; the result's
    precip_status is then None."""
    ks_wexp = [loc["cell"]["wexp"][2] for loc in locs]
    values: Dict[str, dict] = {loc["id"]: {} for loc in locs}
    out_frames: dict = {}
    out_windows: dict = {}
    # the analysis fields are all on wexp, so one set of window indices
    # serves the three messages
    wins = [window_indices(loc["cell"]["wexp"], _grid("wexp")) for loc in locs] if frames else None
    source = "nodd"
    meta: dict = {}
    try:
        url = anl_url(product, t)
        idx_raw = gw.fetch_bytes(url + ".idx", tries=FETCH_TRIES, timeout=FETCH_TIMEOUT)
        if idx_raw is None and nomads_ok(product, t, now):
            url = anl_url(product, t, nomads=True)
            idx_raw = gw.fetch_bytes(url + ".idx", tries=FETCH_TRIES, timeout=FETCH_TIMEOUT)
            source = "nomads"
        if idx_raw is None:
            if not (precip and precip_if_absent):
                return HourResult("absent")
            return _absent_with_precip(read_precip(product, t, locs, lattice if frames else None, now))
        entries = grib2.parse_idx(idx_raw.decode("ascii", "replace"))
        for var, name, level in MESSAGES:
            hit = [e for e in entries if e.get("var") == name and e.get("level") == level]
            if not hit:
                return HourResult("error", error=f"no {name} message in the sidecar")
            e = hit[0]
            end = e.get("end")
            if end is None:
                # the last message of the file: the sidecar cannot give its
                # end, and fetch_range accepts the clamped 206 S3 answers
                end = e["start"] + MAX_MESSAGE_BYTES
            msg = gw.fetch_range(url, e["start"], end, tries=FETCH_TRIES, timeout=FETCH_TIMEOUT, meta=meta)
            if msg is None:
                return HourResult("error", error=f"{name} message missing")
            why = check_message(msg, var, _grid("wexp"), t)
            if why:
                return HourResult("error", error=f"{name}: {why}")
            cells, frame, wvals = _sample(msg, var, "wexp", ks_wexp, lattice if frames else None, wins)
            del msg
            if frames:
                out_frames[var] = frame
                out_windows[var] = {loc["id"]: window_doc("wexp", w) for loc, w in zip(locs, wvals)}
            # kelvin and metres per second, as the file holds them
            for loc, v in zip(locs, cells):
                values[loc["id"]][var] = None if v is None else round(float(v), RAW_DIGITS[var])
    except Exception as e:  # noqa: BLE001
        return HourResult("error", error=f"{type(e).__name__}: {e}")
    doc = {"schema": SCHEMA, "product": product, "valid": _iso(t), "written": _iso(now), "source": source,
           "published": meta.get("lastModified"), "values": values}
    if not precip:
        return HourResult("ok", doc=doc, frames=out_frames if frames else None,
                          windows=out_windows if frames else None)
    prec = read_precip(product, t, locs, lattice if frames else None, now)
    if frames:
        out_frames["precip"] = (prec.frames or {}).get("precip") if prec.status == "ok" else None
        out_windows["precip"] = (prec.windows or {}).get("precip") if prec.status == "ok" else None
    return HourResult("ok", doc=doc, frames=out_frames if frames else None,
                      precip=prec.doc if prec.status == "ok" else None,
                      precip_error=prec.error if prec.status == "error" else None,
                      windows=out_windows if frames else None, precip_status=prec.status)


def _absent_with_precip(prec: HourResult) -> HourResult:
    """An absent analysis hour carrying what its precipitation read came to:
    the document, with its frame and windows when they were sampled, when the
    file was read; the reason when it was refused; nothing when the file is
    absent too."""
    if prec.status == "ok":
        frames = {"precip": prec.frames.get("precip")} if prec.frames else None
        windows = {"precip": prec.windows.get("precip")} if prec.windows else None
        return HourResult("absent", precip=prec.doc, frames=frames, windows=windows, precip_status="ok")
    return HourResult("absent", precip_error=prec.error if prec.status == "error" else None, precip_status=prec.status)


# ------------------------------------------------------------------ each server's copy (2026-11-03)
class Skipped(Exception):
    """A NOMADS call the pass's budget has no room for. The file waits for the
    next pass, and nothing about this one is recorded."""


class NomadsBudget:
    """The NOMADS calls a pass may still make (NOMADS_CALLS_PER_PASS), each at
    least NOMADS_MIN_INTERVAL seconds after the one before it."""
    def __init__(self, calls: Optional[int] = None):
        self.left = NOMADS_CALLS_PER_PASS if calls is None else calls
        self.used = 0
        self._last = 0.0

    def take(self) -> bool:
        if self.left <= 0:
            return False
        wait = self._last + NOMADS_MIN_INTERVAL - time.time()
        if wait > 0:
            time.sleep(wait)
        self.left -= 1
        self.used += 1
        self._last = time.time()
        return True


def _call(server: str, budget: Optional[NomadsBudget], fn: Callable, *args, **kwargs):
    """One government call for one server's file, a NOMADS one only within the
    pass's budget (a caller outside a pass gives none, and no limit)."""
    if server == "nomads" and budget is not None and not budget.take():
        raise Skipped()
    return fn(*args, **kwargs)


def file_url(product: str, kind: str, server: str, t: dt.datetime) -> str:
    """Where a server keeps a file, hour t's analysis or the precipitation
    file of the hour that ends at t."""
    return (anl_url if kind == "anl" else pcp_url)(product, t, nomads=server == "nomads")


class CopyResult:
    """What reading one server's copy of one file came to. `status` is ok,
    absent, error, suspect (a message failed the check and these bytes have
    not been seen before, so the transfer may have broken) or skipped (the
    pass's NOMADS budget is spent), `doc` the copy, `frames` and `windows` the
    map samples of the messages it decoded with a lattice, `bad` the failing
    messages' hashes and reasons, and `error` why it was refused."""
    def __init__(self, status: str, doc: Optional[dict] = None, frames: Optional[dict] = None,
                 windows: Optional[dict] = None, error: Optional[str] = None, bad: Optional[dict] = None):
        self.status, self.doc, self.frames, self.windows, self.error = status, doc, frames, windows, error
        self.bad = bad


def read_copy(product: str, kind: str, server: str, t: dt.datetime, locs: list, lattice: Optional[dict],
              now: dt.datetime, other: Optional[dict] = None, budget: Optional[NomadsBudget] = None,
              decode: bool = True, prior_bad: Optional[dict] = None) -> CopyResult:
    """One server's copy of one file (owner's ruling 2026-11-03), hour t's
    analysis (kind anl, its TMP, WIND and GUST messages by range from the
    .idx sidecar) or the precipitation file of the hour that ends at t (kind
    pcp, one message, whole). Each message is hashed (sha256) and checked, for
    its grid, its parameter and its valid time, a clean decode and a value at
    one place at least. A message whose hash equals the same message in
    `other`, the other server's first copy, is the same bytes, so its values
    are taken from there. Any other is decoded at the places, which is how two
    copies whose hashes differ are compared by value, and onto the lattice and
    the windows when one is given. A download with a message that fails the
    check is not a copy (owner's ruling 2026-11-04), since a broken transfer
    looks just like that: it is `suspect`, and it becomes the server's copy
    only when a later download fails with the same bytes (`prior_bad`, from
    the one before), which is NOAA's own file, kept with those messages
    `refused`. With `decode` False nothing is decoded, for a later read kept
    only as its hashes. The copy records when this pass saw it (`seen`) and
    the server's Last-Modified (`published`). Nothing raises out of here."""
    grid_name = "wexp" if kind == "anl" else PRODUCTS[product]["pcpGrid"]
    ks = [loc["cell"][grid_name][2] for loc in locs]
    wins = None if lattice is None else [window_indices(loc["cell"][grid_name], _grid(grid_name)) for loc in locs]
    url = file_url(product, kind, server, t)
    meta: dict = {}
    sha: Dict[str, str] = {}
    decoded: List[str] = []
    bad: Dict[str, dict] = {}
    values: Dict[str, dict] = {loc["id"]: {} for loc in locs}
    frames: dict = {}
    windows: dict = {}
    try:
        if kind == "anl":
            idx = _call(server, budget, gw.fetch_bytes, url + ".idx", tries=FETCH_TRIES, timeout=FETCH_TIMEOUT)
            if idx is None:
                return CopyResult("absent")
            entries = grib2.parse_idx(idx.decode("ascii", "replace"))
            wanted = []
            for var, name, level in MESSAGES:
                hit = [e for e in entries if e.get("var") == name and e.get("level") == level]
                if not hit:
                    return CopyResult("error", error=f"no {name} message in the sidecar")
                wanted.append((var, name, hit[0]))
        else:
            wanted = [("precip", "APCP", None)]
        for var, name, e in wanted:
            if e is None:
                msg = _call(server, budget, gw.fetch_bytes, url, tries=FETCH_TRIES, timeout=FETCH_TIMEOUT, meta=meta)
                if msg is None:
                    return CopyResult("absent")
            else:
                # the last message of a file has no end in the sidecar, and
                # fetch_range accepts the clamped 206 S3 answers for it
                end = e["end"] if e.get("end") is not None else e["start"] + MAX_MESSAGE_BYTES
                msg = _call(server, budget, gw.fetch_range, url, e["start"], end, tries=FETCH_TRIES,
                            timeout=FETCH_TIMEOUT, meta=meta)
                if msg is None:
                    return CopyResult("error", error=f"{name} message missing")
            sha[var] = hashlib.sha256(msg).hexdigest()
            for lid in values:
                values[lid][var] = None
            why = check_message(msg, var, _grid(grid_name), t)
            if why is None and other is not None and (other.get("sha256") or {}).get(var) == sha[var]:
                # the same bytes as the other server's copy, so the same values
                for lid in values:
                    values[lid][var] = _value_at(other, kind, lid, var)
            elif why is None and decode:
                try:
                    cells, frame, wvals = _sample(msg, var, grid_name, ks, lattice, wins)
                except Exception as ex:  # noqa: BLE001 - a message that will not decode fails the check
                    why = f"undecodable ({type(ex).__name__}: {ex})"
                else:
                    if locs and all(v is None for v in cells):
                        why = "no value at any place"
                    else:
                        decoded.append(var)
                        for loc, v in zip(locs, cells):
                            values[loc["id"]][var] = None if v is None else round(float(v), RAW_DIGITS[var])
                        if lattice is not None:
                            frames[var] = frame
                            windows[var] = {loc["id"]: window_doc(grid_name, w) for loc, w in zip(locs, wvals)}
            if why is not None:
                bad[var] = {"sha256": sha[var], "why": f"{name}: {why}"}
            del msg
    except Skipped:
        return CopyResult("skipped")
    except Exception as e:  # noqa: BLE001
        return CopyResult("error", error=f"{type(e).__name__}: {e}")
    if bad and not all((prior_bad or {}).get(var, {}).get("sha256") == b["sha256"] for var, b in bad.items()):
        return CopyResult("suspect", bad=bad, error="; ".join(b["why"] for b in bad.values()))
    doc = {"schema": SCHEMA, "product": product, "kind": kind, "server": server, "valid": _iso(t),
           "seen": _iso(now), "published": meta.get("lastModified"), "sha256": sha, "decoded": decoded,
           "written": _iso(now)}
    if bad:
        # NOAA's own file, fetched twice with the same bytes: kept, the
        # messages that fail the check without values
        doc["refused"] = {var: b["why"] for var, b in bad.items()}
    if decode:
        if kind == "pcp":
            # millimetres, as the file holds them
            doc["values"] = {lid: v.get("precip") for lid, v in values.items()}
            doc["missing"] = sorted(lid for lid, v in doc["values"].items() if v is None)
        else:
            # kelvin and metres per second, as the file holds them
            doc["values"] = values
    return CopyResult("ok", doc=doc, frames=frames or None, windows=windows or None)


def _value_at(c: Optional[dict], kind: str, lid: str, var: Optional[str] = None):
    """A copy's value at a place, an analysis's temperature, wind and gust (or
    the one `var`) or a precipitation file's millimetres."""
    v = ((c or {}).get("values") or {}).get(lid)
    if kind == "pcp" or var is None:
        return v
    return (v or {}).get(var)


def copy_key(product: str, kind: str, server: str, t: dt.datetime) -> str:
    return COPY_KEY.format(product=product, stamp=_stamp(t), kind=kind, server=server)


def later_key(product: str, kind: str, server: str, t: dt.datetime) -> str:
    return LATER_KEY.format(product=product, stamp=_stamp(t), kind=kind, server=server)


def get_copy(store: Storage, product: str, kind: str, server: str, t: dt.datetime, cache: dict) -> Optional[dict]:
    """A server's first copy of a file, or None."""
    ck = ("copy", product, kind, server, _iso(t))
    if ck not in cache:
        cache[ck] = _read_gz(store, copy_key(product, kind, server, t))
    return cache[ck]


def copies_of(store: Storage, product: str, kind: str, t: dt.datetime, cache: dict) -> dict:
    """Both servers' first copies of a file, None for a server with none."""
    return {s: get_copy(store, product, kind, s, t, cache) for s in SERVERS}


def get_later(store: Storage, product: str, kind: str, server: str, t: dt.datetime, cache: dict) -> Optional[dict]:
    """The later replacements a server's file has been seen with, or None."""
    ck = ("later", product, kind, server, _iso(t))
    if ck not in cache:
        cache[ck] = _read_gz(store, later_key(product, kind, server, t))
    return cache[ck]


def prime_copies(store: Storage, product: str, t0: dt.datetime, t1: dt.datetime, cache: dict) -> None:
    """One listing of the copies of the hours t0 to t1, so the day rebuild asks
    the store only for copies and replacements that exist."""
    prefix = COPY_PREFIX.format(product=product)
    keys = set(store.list(prefix, start_after=prefix + _stamp(t0 - dt.timedelta(hours=1))))
    t = t0
    while t <= t1:
        for kind in KINDS:
            for server in SERVERS:
                for ck, key in ((("copy", product, kind, server, _iso(t)), copy_key(product, kind, server, t)),
                                (("later", product, kind, server, _iso(t)), later_key(product, kind, server, t))):
                    if ck not in cache and key not in keys:
                        cache[ck] = None
        t += dt.timedelta(hours=1)


def write_copy(store: Storage, product: str, kind: str, server: str, t: dt.datetime, doc: dict, cache: dict) -> bool:
    """Keep a server's first copy of a file, once (owner's ruling 2026-11-03):
    a copy already kept stands, and a later read that differs is recorded by
    note_later and never used. Returns True when this one was kept."""
    wrote = store.put_if_absent(copy_key(product, kind, server, t), _gz(doc), "application/gzip")
    if wrote:
        cache[("copy", product, kind, server, _iso(t))] = doc
    return wrote


def note_later(store: Storage, product: str, kind: str, server: str, t: dt.datetime, later: dict,
               cache: dict) -> bool:
    """Record a later read of a server's file that is not its first copy
    (other bytes, or another Last-Modified where the first copy had one)
    beside the first copy, with when the lane saw it, its Last-Modified and
    its hashes. It is never used (owner's ruling 2026-11-03). Returns True
    when the record gained it."""
    first = get_copy(store, product, kind, server, t, cache)
    if first is None:
        return False
    if first.get("sha256") == later.get("sha256") and first.get("published") in (None, later.get("published")):
        return False
    entry = {"seen": later.get("seen"), "published": later.get("published"), "sha256": later.get("sha256")}
    doc = get_later(store, product, kind, server, t, cache) or {
        "schema": SCHEMA, "product": product, "kind": kind, "server": server, "valid": _iso(t), "later": []}
    if any(e.get("published") == entry["published"] and e.get("sha256") == entry["sha256"] for e in doc["later"]):
        return False
    doc = dict(doc, later=doc["later"] + [entry])
    store.put(later_key(product, kind, server, t), _gz(doc), "application/gzip")
    cache[("later", product, kind, server, _iso(t))] = doc
    return True


# ------------------------------------------------------------------ which copy counts (2026-11-03)
def _pub(c: Optional[dict]) -> Optional[str]:
    """When a copy's file was on its server, its Last-Modified or, failing
    that, when this lane first saw it there."""
    return None if not c else (c.get("published") or c.get("seen"))


def first_published(copies: dict) -> Optional[str]:
    """The first time a file was on either server, which the cut-off is judged
    on, since the draft filings count a file that was on NOAA Open Data or
    NOMADS by then."""
    ts = [p for p in (_pub(c) for c in copies.values()) if p]
    return min(ts) if ts else None


def first_server(copies: dict) -> Optional[str]:
    """The server a file was on first, whose time first_published gives; NOAA
    Open Data on a tie."""
    have = [(p, i, server) for i, server in enumerate(SERVERS) for p in [_pub(copies.get(server))] if p]
    return min(have)[2] if have else None


def hash_match(copies: dict) -> Optional[bool]:
    """Whether the two servers' copies are the same bytes, message by message;
    None while either is missing."""
    if any(copies.get(s) is None for s in SERVERS):
        return None
    return copies["nodd"].get("sha256") == copies["nomads"].get("sha256")


def value_match(copies: dict, kind: str, lid: str) -> Optional[bool]:
    """Whether the two servers' copies give a place the same values; None while
    either is missing."""
    if any(copies.get(s) is None for s in SERVERS):
        return None
    return _value_at(copies["nodd"], kind, lid) == _value_at(copies["nomads"], kind, lid)


def row_day(kind: str, t: dt.datetime, tz: str) -> str:
    """The local day of the row a file fills, which is an analysis's own
    hour's, and for a precipitation file the day of the hour before its
    stamp."""
    return local_day_of(t if kind == "anl" else t - dt.timedelta(hours=1), tz)


def choose(copies: dict, cut_iso: str) -> Optional[str]:
    """Which server's copy a row uses on a day with this cut-off, from the
    copies given (owner's ruling 2026-11-03): NOMADS's when it was published
    by the cut-off, whatever NOAA Open Data's holds, else NOAA Open Data's
    when it was, else neither counts and the row shows NOMADS's, or NOAA Open
    Data's when NOMADS has none, as published late. None for no copy."""
    nod, nom = copies.get("nodd"), copies.get("nomads")
    if nom is not None and _pub(nom) <= cut_iso:
        return "nomads"
    if nod is not None and _pub(nod) <= cut_iso:
        return "nodd"
    if nom is not None:
        return "nomads"
    return "nodd" if nod is not None else None


def hourly_cut(t: dt.datetime) -> dt.datetime:
    """The hourly contracts' cut-off for hour t's analysis, at every place."""
    return t + HOURLY_CUT_AFTER


def place_cuts(kind: str, t: dt.datetime, locs: list) -> Dict[str, str]:
    """The cut-off of the day each place's row of a file falls on, by place."""
    by_tz: Dict[str, str] = {}
    out = {}
    for loc in locs:
        tz = loc["tz"]
        if tz not in by_tz:
            by_tz[tz] = _iso(day_cutoff(row_day(kind, t, tz), tz))
        out[loc["id"]] = by_tz[tz]
    return out


def _cut_entry(copies: dict, held: list, cut_iso: str, at: str) -> dict:
    """How a file settled for the days of one cut-off: when (`at`, the pass),
    on which copies (`with`, the servers whose first copies were in hand),
    the copy chosen (`src`), and the first time the file was on either of
    those servers and which one (`published`, `pubSrc`)."""
    sub = {s: copies.get(s) for s in held}
    return {"at": at, "with": list(held), "src": choose(sub, cut_iso), "published": first_published(sub),
            "pubSrc": first_server(sub)}


def settled_doc(product: str, kind: str, t: dt.datetime, copies: dict, cutoffs: dict, picks: dict,
                now: dt.datetime, hourly: Optional[dict] = None) -> dict:
    """A file's settled record, with how it settled for each cut-off of its
    days (`cutoffs`) and, for an analysis, for the hourly cut (`hourly`), the
    copy each place settled on for its day and its value, and each server's
    first copy's times and hashes as they stood."""
    values = {lid: (_value_at(copies.get(server), kind, lid) if server else None) for lid, server in picks.items()}
    used = sorted({s for s in picks.values() if s})
    doc = {"schema": SCHEMA, "product": product, "kind": kind, "valid": _iso(t), "settled": _iso(now),
           "written": _iso(now), "published": first_published(copies), "cutoffs": cutoffs,
           # the one copy every place settled on; the picks name it per place when they differ
           "source": used[0] if len(used) == 1 else None, "picks": picks, "values": values,
           "servers": {s: {k: c.get(k) for k in ("seen", "published", "sha256")} for s, c in copies.items() if c},
           "hashMatch": hash_match(copies)}
    if kind == "pcp":
        doc["missing"] = sorted(lid for lid, v in values.items() if v is None)
        doc.update({"read": True, "complete": not doc["missing"]})
    if hourly is not None:
        doc["hourly"] = hourly
    return doc


def settle_cuts(store: Storage, product: str, kind: str, t: dt.datetime, slots: dict, locs: list,
                now: dt.datetime, cache: dict, freeze: bool) -> bool:
    """Settle what of a file can be settled (owner's rulings 2026-11-03 and
    2026-11-04). Every cut-off of its days settles at the pass at which both
    servers' first copies are in, since no later read can count; and on a
    ten-minute pass (`freeze`) each cut-off that has passed settles on the
    copies in hand, NOMADS's or not. A cut-off settled stays as it settled,
    in `slots["cutoffs"]`, and a copy read later never changes it. A file
    carried over from before 2026-11-03 (`slots["migrated"]`) settles on no
    copy for every cut-off that passed before then, since its days were
    final already. An analysis file has one more cut, the hourly contracts'
    (hourly_cut, Patrick's ruling of 4 November), kept apart from the daily
    ones in `slots["hourly"]` in the same shape and settled by the same rules:
    at the pass at which both copies are in, or on a ten-minute pass once it
    has passed, on the copies in hand, and on no copy for a carried-over file
    whose hourly cut passed before then. Once every cut-off and, for an
    analysis, the hourly cut have settled, the file's settled record is
    written, once, the archive hour or the precipitation key, even with no
    copy at all. While the hourly cut was three hours after the hour (until
    2026-11-05), at 10 and 11 PM in a zone it came later than the day's, and
    where no zone's cut-off was later still (never with the sixty-seven
    places) the record waited for it; at 1 hour 30 minutes it never comes
    after the day's own cut-off. The daily cut-offs settle as they did.
    Returns True when a cut-off settled."""
    copies = copies_of(store, product, kind, t, cache)
    held = [s for s in SERVERS if copies.get(s) is not None]
    by_place = place_cuts(kind, t, locs)
    done = dict(slots.get("cutoffs") or {})
    migrated = slots.get("migrated")
    changed = False
    for cut_iso in sorted(set(by_place.values())):
        if cut_iso in done:
            continue
        if migrated and cut_iso <= migrated:
            done[cut_iso] = _cut_entry(copies, [], cut_iso, migrated)
        elif len(held) == len(SERVERS) or (freeze and _iso(now) >= cut_iso):
            done[cut_iso] = _cut_entry(copies, held, cut_iso, _iso(now))
        else:
            continue
        changed = True
    if changed:
        slots["cutoffs"] = done
    if kind == "anl" and "hourly" not in slots:
        hc = _iso(hourly_cut(t))
        if migrated and hc <= migrated:
            slots["hourly"] = _cut_entry(copies, [], hc, migrated)
        elif len(held) == len(SERVERS) or (freeze and _iso(now) >= hc):
            slots["hourly"] = _cut_entry(copies, held, hc, _iso(now))
        changed = changed or "hourly" in slots
    if by_place and all(c in done for c in by_place.values()) and (kind != "anl" or "hourly" in slots):
        picks = {lid: done[c]["src"] for lid, c in by_place.items()}
        doc = settled_doc(product, kind, t, {s: copies.get(s) for s in SERVERS}, dict(done), picks, now,
                          hourly=slots.get("hourly"))
        store.put_if_absent(hour_key(product, t) if kind == "anl" else precip_key(product, t), _gz(doc),
                            "application/gzip")
        cache[("anl" if kind == "anl" else "precip", product, _iso(t))] = doc
    return changed


def _late(src: Optional[str], held: list, copies: dict) -> bool:
    """Whether a row's file has a copy that came too late to count (owner's
    rulings 2026-11-03 and 2026-11-04, a record only): a copy read after the
    row settled, from either server, or a NOMADS copy in hand that differs
    from the NOAA Open Data copy the row keeps, having been published after
    the cut-off."""
    if any(copies.get(s) is not None and s not in held for s in SERVERS):
        return True
    nod, nom = copies.get("nodd"), copies.get("nomads")
    return bool(src == "nodd" and "nomads" in held and nod is not None and nom is not None
                and nom.get("sha256") != nod.get("sha256"))


def file_view(store: Storage, product: str, kind: str, t: dt.datetime, lid: str, cut_iso: str, now: dt.datetime,
              slots: Optional[dict], cache: dict) -> Optional[dict]:
    """What one file gives one row, which is the value the row uses (an
    analysis's temperature, wind and gust, or a precipitation file's
    millimetres), whose copy it is (`src`), when the file was first on either
    server (`pub`) and which server that was (`pubSrc`), when the row's copy
    became final (`settled`, the pass, None until then), whether a copy came
    too late to count (`late`, a record only), and, for the raw CSV, both
    servers' copies and their later replacements. A settled row reads only
    the copies it settled on. A file in the archive's earlier shape gives its
    one copy. None when nothing of the file was read."""
    rec = get_anl(store, product, t, cache) if kind == "anl" else get_precip(store, product, t, cache)
    if rec is not None and "settled" not in rec:
        # read before 2026-11-03 from one server, with no copies beside it
        return {"value": (rec.get("values") or {}).get(lid), "src": rec.get("source"), "pub": rec.get("published"),
                "pubSrc": rec.get("source"), "settled": None, "late": None, "copies": None, "later": None}
    copies = copies_of(store, product, kind, t, cache)
    if rec is None and not any(copies.values()):
        return None
    entry = ((rec if rec is not None else slots) or {}).get("cutoffs", {}).get(cut_iso)
    if entry is None and rec is not None:
        # a cut-off the record does not name (a zone added since): the copies it was written with
        entry = _cut_entry(copies, sorted(rec.get("servers") or {}), cut_iso, rec.get("settled"))
    if entry is not None:
        src, held = entry.get("src"), entry.get("with") or []
        if rec is not None and (rec.get("picks") or {}).get(lid, src) == src:
            value = (rec.get("values") or {}).get(lid)
        else:
            value = _value_at(copies.get(src), kind, lid) if src else None
        pub, pub_src, settled = entry.get("published"), entry.get("pubSrc"), entry.get("at")
    else:
        src, held = choose(copies, cut_iso), [s for s in SERVERS if copies.get(s) is not None]
        value = _value_at(copies.get(src), kind, lid) if src else None
        pub, pub_src, settled = first_published(copies), first_server(copies), None
    return {"value": value, "src": src, "pub": pub, "pubSrc": pub_src, "settled": settled,
            "late": _late(src, held, copies), "copies": copies,
            "later": {s: get_later(store, product, kind, s, t, cache) for s in SERVERS}}


# ------------------------------------------------------------------ the files and the hourly contract (2026-11-04)
_FILE_TIMES: Dict[str, dict] = {}


def load_file_times(path: Optional[str] = None) -> dict:
    """NOAA's publication times of the files read before the lane kept each
    server's copy, {kind: {file_utc: {server: {lm, seen}}}} with a
    precipitation file under the hour it ends (FILE_TIMES_PATH), read once a
    process. It is shown only, and no day's figure, count, late flag or state
    reads it."""
    path = path or FILE_TIMES_PATH
    if path not in _FILE_TIMES:
        with open(path) as fh:
            _FILE_TIMES[path] = json.load(fh)
    return _FILE_TIMES[path]


def table_times(kind: str, t: dt.datetime, src: Optional[str], pub: Optional[str], times: dict) -> dict:
    """Each server's times for a file read before the lane kept each server's
    copy: the Last-Modified (`lm`) the record kept for the server it was read
    from, or else the table's, and when it was first found there (`seen`)
    only where the table has it. None for a server with neither, since an
    estimated time is never shown."""
    row = ((times or {}).get(kind) or {}).get(_iso(t)) or {}
    out = {}
    for s in SERVERS:
        tab = row.get(s) or {}
        lm = (pub if s == src else None) or tab.get("lm")
        out[s] = {"lm": lm, "seen": tab.get("seen")} if lm or tab.get("seen") else None
    return out


def file_doc(product: str, kind: str, t: dt.datetime, view: Optional[dict], counts: bool, cut_iso: str,
             times: dict) -> dict:
    """A row's file as rowShape 3 carries it (`anlFile`, `pcpFile`): its name
    as NCEP names it, each server's Last-Modified (`lm`) and when the lane
    first found its copy there (`seen`), None for a server with no record,
    whose copy the row uses (`src`), whether the hour counts for the day
    (`counts`, false when the row's value is marked late or it has none), and
    whether the first publication shown is after the day's cut-off
    (`afterCut`). A file read before 2026-10-14 can be both, since the day
    counted every file read until then. A file read before the lane kept each
    server's copy takes its times from table_times, and a file nothing of
    which was read has none."""
    copies = (view or {}).get("copies")
    if view is None:
        servers = {s: None for s in SERVERS}
    elif copies is not None:
        servers = {s: ({"lm": copies[s].get("published"), "seen": copies[s].get("seen")} if copies.get(s) else None)
                   for s in SERVERS}
    else:
        servers = table_times(kind, t, view.get("src"), view.get("pub"), times)
    lms = [v["lm"] for v in servers.values() if v and v.get("lm")]
    return {"name": file_name(product, kind, t), "nodd": servers["nodd"], "nomads": servers["nomads"],
            "src": (view or {}).get("src"), "counts": counts, "afterCut": bool(lms) and min(lms) > cut_iso}


def hourly_file(store: Storage, product: str, t: dt.datetime, slots: Optional[dict], cache: dict,
                now: dt.datetime, times: dict) -> dict:
    """The hourly temperature contract of hour t's analysis file, which every
    place shares (Patrick's ruling of 4 November): its cut (`cut`), its state
    (final, provisional or review), when it became final or went to review
    (`at`), on what (`basis`), the copy it reads (`src`) and that copy's
    values (`values`), whether a copy was read after its cut settled
    (`late`), and whether anything of the file is known (`known`: a copy, a
    record or a settled cut).

    It is final at NOMADS's copy's Last-Modified (basis nomads) once that copy
    is in, published by the cut and read before the cut settled. Otherwise,
    once the cut has settled (settle_cuts), it is final at the cut itself on
    the copy choose() takes, published by the cut (basis cutoff), as a day's
    finalAt is its cut-off, or under review at the cut with no copy published
    by then; until then it is provisional on the copy in hand. A copy read
    after the cut settled is recorded, flagged and never used. A record
    settled before the hourly cut existed (from 2026-11-03 to 2026-11-04) is
    read as settling it with the record, on the copies it was written with,
    as file_view reads a cut-off a record does not name.

    A file read before the lane kept each server's copy has no NOMADS copy on
    record (basis norecord): final at the cut on the copy read, or at NOMADS's
    Last-Modified where the table or the record has one by the cut (basis
    nomads, the copy read still the one used, as the engine's probe found the
    two servers' copies the same in every file it compared), and under review
    at the cut where NOAA Open Data's Last-Modified is after it. The table is
    shown times only, so it never moves a day's figures; here it can make an
    hour's state."""
    hc = _iso(hourly_cut(t))
    out = {"cut": hc, "state": "provisional", "at": None, "basis": None, "src": None, "late": False,
           "values": {}, "known": False}
    rec = get_anl(store, product, t, cache)
    if rec is not None and "settled" not in rec:
        src = rec.get("source")
        lm = {s: (v or {}).get("lm") for s, v in table_times("anl", t, src, rec.get("published"), times).items()}
        if lm["nomads"] and lm["nomads"] <= hc:
            out.update(state="final", at=lm["nomads"], basis="nomads", src=src)
        elif _iso(now) < hc:
            out.update(src=src)
        elif lm["nodd"] and lm["nodd"] > hc:
            out.update(state="review", at=hc, basis="norecord")
        else:
            out.update(state="final", at=hc, basis="norecord", src=src)
        out.update(values=(rec.get("values") or {}) if out["src"] else {}, known=True)
        return out
    copies = copies_of(store, product, "anl", t, cache)
    entry = (rec or slots or {}).get("hourly")
    if rec is not None and entry is None:
        entry = _cut_entry(copies, sorted(rec.get("servers") or {}), hc, rec.get("settled"))
    if entry is None and not any(copies.values()):
        return out
    held = (entry.get("with") or []) if entry is not None else [s for s in SERVERS if copies.get(s) is not None]
    sub = {s: copies.get(s) for s in held}
    nom = sub.get("nomads")
    if nom is not None and _pub(nom) <= hc:
        out.update(state="final", at=_pub(nom), basis="nomads", src="nomads")
    elif entry is not None:
        src = choose(sub, hc)
        if src is not None and _pub(sub[src]) <= hc:
            out.update(state="final", at=hc, basis="cutoff", src=src)
        else:
            out.update(state="review", at=hc, basis="cutoff")
    else:
        src = choose(sub, hc)
        out["src"] = src if src is not None and _pub(sub[src]) <= hc else None
    out["late"] = entry is not None and any(copies.get(s) is not None and s not in held for s in SERVERS)
    out.update(values=((copies.get(out["src"]) or {}).get("values") or {}) if out["src"] else {}, known=True)
    return out


def hourly_row(hv: dict, lid: str) -> dict:
    """One place's hourly contract from its file's (hourly_file): the value,
    whole degrees Fahrenheit rounded once, half up, away from zero, and its
    exact string, from the place's temperature in the copy the contract reads.
    A copy that counts with no temperature at the place leaves the place no
    value, which is a review at the cut, the choice being per file (owner's
    ruling 2026-11-04)."""
    k = ((hv.get("values") or {}).get(lid) or {}).get("temp")
    out = {key: hv[key] for key in ("cut", "state", "at", "basis", "src")}
    if out["state"] == "final" and k is None:
        out.update(state="review", at=hv["cut"])
    out["value"] = half_up(exact("temp", k), 1) if k is not None else None
    out["x"] = x_text("temp", k)
    out["late"] = hv["late"]
    return out


# ------------------------------------------------------------------ writing
def hour_key(product: str, t: dt.datetime) -> str:
    return HOUR_KEY.format(product=product, stamp=_stamp(t))


def precip_key(product: str, t: dt.datetime) -> str:
    return PRECIP_KEY.format(product=product, stamp=_stamp(t))


def get_precip(store: Storage, product: str, t: dt.datetime, cache: dict) -> Optional[dict]:
    ck = ("precip", product, _iso(t))
    if ck not in cache:
        cache[ck] = _read_gz(store, precip_key(product, t))
    return cache[ck]


def get_anl(store: Storage, product: str, t: dt.datetime, cache: dict) -> Optional[dict]:
    """The archived analysis hour as written, without its precipitation."""
    ck = ("anl", product, _iso(t))
    if ck not in cache:
        cache[ck] = _read_gz(store, hour_key(product, t))
    return cache[ck]


def merge_precip(old: Optional[dict], new: dict) -> dict:
    """The stored precipitation document brought up to date by a new read.
    A place's value, once stored, stands: the resolved daily total is the
    sum of the stored hourlies and has to stay reproducible from the hour
    table, so a read never rewrites a stored value. Only places still
    without a value take the new read's."""
    if old is None:
        return new
    # the first published version of a file counts (owner's ruling
    # 2026-10-14): a read of the same server's file written at another time
    # is a replacement, and adds nothing beyond the note that it was seen
    if (old.get("published") and new.get("published") and old.get("source") == new.get("source")
            and new["published"] != old["published"]):
        out = dict(old)
        out["replaced"] = new["published"]
        return out
    values = dict(old.get("values") or {})
    for lid, v in (new.get("values") or {}).items():
        if values.get(lid) is None and v is not None:
            values[lid] = v
    missing = sorted(lid for lid, v in values.items() if v is None)
    out = dict(old)
    out.update({"written": new.get("written", old.get("written")), "read": bool(old.get("read")) or bool(new.get("read")),
                "complete": not missing, "missing": missing, "values": values})
    return out


def write_precip(store: Storage, product: str, t: dt.datetime, pdoc: dict, cache: dict) -> bool:
    """Store a product-hour's precipitation document, merged over what is
    already stored (this key may be rewritten). Returns True when the stored
    document changed, which is when the days it touches need rebuilding."""
    old = get_precip(store, product, t, cache)
    merged = merge_precip(old, pdoc)
    changed = (old is None or merged.get("values") != old.get("values") or merged.get("read") != old.get("read")
               or merged.get("replaced") != old.get("replaced"))
    if changed:
        store.put(precip_key(product, t), _gz(merged), "application/gzip")
    cache[("precip", product, _iso(t))] = merged
    return changed


def write_hour(store: Storage, product: str, t: dt.datetime, res: HourResult, grid_index: dict,
               cache: dict, now: dt.datetime) -> None:
    """One archive hour (write once), its precipitation (merged), and, when
    they were made, its frames with the grid index rewritten behind them so
    a frame on the bucket is never one the page cannot find."""
    store.put_if_absent(hour_key(product, t), _gz(res.doc), "application/gzip")
    cache[("anl", product, _iso(t))] = res.doc
    if res.precip is not None:
        write_precip(store, product, t, res.precip, cache)
    if res.frames:
        write_frames(store, product, t, res.frames, grid_index, now, res.windows,
                     values=(res.doc or {}).get("values"), pcp=(res.precip or {}).get("values"))
        write_grid_index(store, grid_index, now)


def write_precip_only(store: Storage, product: str, t: dt.datetime, res: HourResult, grid_index: dict,
                      cache: dict, now: dt.datetime) -> bool:
    """The precipitation of an hour whose analysis file is missing: stored
    (merged) with its frame when one was sampled, and no archive hour, so the
    analysis stays a gap the live lane retries. Returns True when the stored
    precipitation changed, which is when the days it touches need rebuilding."""
    if res.precip is None:
        return False
    changed = write_precip(store, product, t, res.precip, cache)
    if changed and res.frames and res.frames.get("precip") is not None:
        write_frames(store, product, t, res.frames, grid_index, now, res.windows, pcp=res.precip.get("values"))
        write_grid_index(store, grid_index, now)
    return changed


def write_frames(store: Storage, product: str, t: dt.datetime, frames: dict, grid_index: dict, now: dt.datetime,
                 windows: Optional[dict] = None, values: Optional[dict] = None, pcp: Optional[dict] = None) -> None:
    """One frame file per variable sampled, the place windows of that
    variable inside it under `windows` when they were sampled (a frame
    without them is the older shape and the page accepts it), and under
    `own` each place's value as the contract takes it, from the stored
    values the frame was drawn from: `values` an analysis's, `pcp` a
    precipitation file's (own_doc)."""
    stamp = _stamp(t)
    day, hh = f"{t:%Y-%m-%d}", f"{t:%H}"
    for var in HOURLY_VARS:
        vals = frames.get(var)
        if vals is None:
            continue
        doc = {"schema": SCHEMA, "product": product, "var": var, "valid": _iso(t), "asof": _iso(t),
               "written": _iso(now), "unit": UNITS[var], "scale": SCALE[var],
               "cols": LATTICE_COLS, "rows": LATTICE_ROWS, "pitch": LATTICE_PITCH, "values": vals}
        wins = (windows or {}).get(var)
        if wins is not None:
            doc["windows"] = wins
        own = own_doc(var, pcp if var == "precip" else values)
        if own:
            doc["own"] = own
        store.put(FRAME_KEY.format(product=product, var=var, stamp=stamp), _dump(doc), "application/json", CACHE_FINAL)
        hours = grid_index.setdefault(product, {}).setdefault(var, {}).setdefault(day, [])
        if hh not in hours:
            hours.append(hh)
            hours.sort()


def touched_by(t: dt.datetime, locs: list) -> set:
    """The (location, local day) pairs one UTC hour belongs to: the day its
    analysis falls on, and the day of the hour before it, whose row carries
    the precipitation that ends at t (they differ only at local midnight)."""
    days_by_tz: dict = {}
    out = set()
    for loc in locs:
        tz = loc["tz"]
        if tz not in days_by_tz:
            days_by_tz[tz] = {local_day_of(t, tz), local_day_of(t - dt.timedelta(hours=1), tz)}
        for d in days_by_tz[tz]:
            out.add((loc["id"], d))
    return out


def _will_look(state: dict, product: str, t: dt.datetime, now: dt.datetime) -> bool:
    """Whether the lane will still look for a file of hour t it holds nothing
    of and is not looking for: the live walk has not opened its stamp, it lies
    in a queued catch-up span, or the backfill has yet to reach it."""
    ps = ((state or {}).get("products") or {}).get(product) or {}
    bf = ((state or {}).get("backfill") or {}).get(product) or {}
    cursor = _scan_cursor(ps)
    if cursor is not None and t > cursor:
        return True
    for a, b in bf.get("queue") or []:
        if _parse_iso(a) <= t <= _parse_iso(b):
            return True
    oldest = _floor_hour(now) - dt.timedelta(days=BACKFILL_POINT_DAYS)
    return bool(not bf.get("done") and bf.get("cursor") and oldest <= t < _parse_iso(bf["cursor"]))


def _pending(view: Optional[dict], slots: Optional[dict], state: dict, product: str, t: dt.datetime,
             now: dt.datetime, cut_iso: str) -> bool:
    """Whether a row's file may still change for the days of this cut-off,
    which it may until it has settled for that cut-off, while the lane holds
    it open or will still look for it (owner's ruling 2026-11-04: a day shown
    final never changes)."""
    if view is not None and (view.get("settled") or view.get("copies") is None):
        return False
    if slots is not None:
        return cut_iso not in (slots.get("cutoffs") or {})
    return _will_look(state, product, t, now)


def _has_copy(view: Optional[dict]) -> bool:
    return bool(view and view.get("copies") and any(view["copies"].values()))


def _hourly(memo: dict, store: Storage, product: str, t: dt.datetime, slots: Optional[dict], cache: dict,
            now: dt.datetime, times: dict) -> dict:
    """hourly_file once per file in a build, since every place shares it."""
    key = (product, _iso(t))
    if key not in memo:
        memo[key] = hourly_file(store, product, t, slots, cache, now, times)
    return memo[key]


def build_day(store: Storage, day_iso: str, locs: list, cache: dict, now: dt.datetime, state: dict,
              only: Optional[set] = None, raw: Optional[list] = None, times: Optional[dict] = None) -> Tuple[dict, dict]:
    """The days/ document for one local date and the loc/ documents of the
    places in `only` (all of them when None), from the archive hours. With
    `raw`, the day's rows in the files' own units are appended to it for the
    raw CSV. A place-day turns final, or goes to event review, at its cut-off
    once no file of its rows may still change for it (_pending). Each row of a
    place file with a value carries rowShape 3's fields beside `show` and
    `run`, and a row with none whose hourly contract has a state carries that
    state and its files; `times` is the table of load_file_times, which they
    alone read."""
    if times is None:
        times = load_file_times()
    day_doc: dict = {"schema": SCHEMA, "day": day_iso, "asof": None, "written": _iso(now), "locations": {}}
    loc_docs: dict = {}
    asof = None
    waiting: Dict[str, bool] = {}
    rows_of: Dict[str, list] = {}
    hourly: dict = {}
    # one listing of the copies the day's hours have, in every zone
    span = [h for tz in sorted({loc["tz"] for loc in locs}) for h in local_day_hours(day_iso, tz)]
    if span:
        for product in PRODUCTS:
            prime_copies(store, product, min(span), max(span) + dt.timedelta(hours=1), cache)
    for loc in locs:
        lid, tz = loc["id"], loc["tz"]
        hours = local_day_hours(day_iso, tz)
        entry: dict = {}
        rows = [{"local": hour_label(h, tz), "t": _iso(h)} for h in hours]
        rows_of[lid] = rows
        cut_iso = _iso(day_cutoff(day_iso, tz))
        wanted = only is None or lid in only
        valued: set = set()
        for product in PRODUCTS:
            product_hours = {}
            views: dict = {}
            pending = False
            opened = (((state or {}).get("products") or {}).get(product) or {}).get("open") or {}
            # no file for an hour that has not begun (an analysis) or ended (an
            # accumulation) is looked for. This was expected_latest, which hid
            # an hour's precipitation until the next analysis was due, about
            # half an hour after the lane had read it
            latest = _floor_hour(now)
            for h, row in zip(hours, rows):
                # each row is the analysis at the top of the hour and the
                # precipitation over the hour that starts then, which NOAA
                # files under the hour it ends, so the day's rows cover it
                # from midnight to midnight (owner's decision 2026-09-29).
                # An hour whose analysis never landed still carries its
                # precipitation, a separate file (RTMA 2026-09-23 19Z).
                end = h + dt.timedelta(hours=1)
                sa, sp = (opened.get(_iso(h)) or {}).get("anl"), (opened.get(_iso(end)) or {}).get("pcp")
                fa = file_view(store, product, "anl", h, lid, cut_iso, now, sa, cache) if h <= latest else None
                fp = file_view(store, product, "pcp", end, lid, cut_iso, now, sp, cache) if end <= latest else None
                pending = (pending or _pending(fa, sa, state, product, h, now, cut_iso)
                           or _pending(fp, sp, state, product, end, now, cut_iso))
                va = (fa or {}).get("value")
                vp = (fp or {}).get("value")
                row[product] = None
                if va is None and vp is None:
                    # a copy with no value the row uses, read too late, say: the
                    # raw CSV still records it
                    if raw is not None and product == SETTLE and (_has_copy(fa) or _has_copy(fp)):
                        raw.append([day_iso, lid, hour_label(h, tz), _iso(h), None, None, None, None, _iso(end),
                                    None, fp["src"] if fp is not None else None, None,
                                    fp["pub"] if fp is not None else None] + raw_copy_columns(fa, fp, lid))
                    # an hour with no value for the day whose hourly contract
                    # has a state (a file on neither server by the hourly cut,
                    # or one too late for the day and in time for the hourly
                    # contract) still carries it and its files, and nothing else
                    if wanted and h <= latest:
                        hv = _hourly(hourly, store, product, h, sa, cache, now, times)
                        if hv["known"]:
                            row[product] = {"hourly": hourly_row(hv, lid),
                                            "anlFile": file_doc(product, "anl", h, fa, False, cut_iso, times),
                                            "pcpFile": (file_doc(product, "pcp", end, fp, False, cut_iso, times)
                                                        if end <= latest else None)}
                    continue
                rec = {var: exact(var, (va or {}).get(var)) for var in ANALYSIS_VARS}
                rec["precip"] = exact("precip", vp)
                # the stored millimetres, which the day's total is summed from
                rec["mm"] = vp
                rec["anl"] = va is not None
                # the file was read and had no value at the place: read, not pending
                rec["pcpRead"] = fp is not None
                # when each file was first on either server, which the day's
                # cut-off is judged on (a file read before 2026-11-03 carries
                # its one server's time)
                rec["pub"] = fa["pub"] if va is not None else None
                rec["pcpPub"] = fp["pub"] if fp is not None else None
                product_hours[_iso(h)] = rec
                views[_iso(h)] = (fa, fp, va, vp, sa)
                # the hourly values in the contract's units, cut to a
                # thousandth (precipitation a ten-thousandth of an inch)
                row[product] = {var: cut(rec[var], HOUR_SHOW[var]) for var in HOURLY_VARS}
                # a file published after the day's cut-off is shown and never counted
                if va is not None and not _in_time(rec["pub"], cut_iso):
                    row[product]["late"] = True
                if vp is not None and not _in_time(rec["pcpPub"], cut_iso):
                    row[product]["pcpLate"] = True
                # when each file was first on either server and which server
                # that was, for the page's arrival times (none on a file read
                # before 2026-10-14), and whose copy the row uses
                for key, val in (("pub", rec["pub"]), ("pcpPub", rec["pcpPub"] if vp is not None else None),
                                 ("src", fa["src"] if va is not None else None),
                                 ("pcpSrc", fp["src"] if vp is not None else None),
                                 ("pubSrc", fa["pubSrc"] if va is not None else None),
                                 ("pcpPubSrc", fp["pubSrc"] if vp is not None else None),
                                 # when each file's copy for the row became final, the pass
                                 ("settled", (fa or {}).get("settled")), ("pcpSettled", (fp or {}).get("settled"))):
                    if val:
                        row[product][key] = val
                # a copy read after the row settled, or a NOMADS copy that
                # differs from the NOAA Open Data copy the row keeps, having
                # been published after the cut-off, is a record only and
                # changes no value, count or state (owner's rulings 2026-11-03
                # and 2026-11-04)
                if (fa or {}).get("late"):
                    row[product]["lateCopy"] = True
                if (fp or {}).get("late"):
                    row[product]["pcpLateCopy"] = True
                if raw is not None and product == SETTLE:
                    raw.append([day_iso, lid, hour_label(h, tz), _iso(h),
                                (va or {}).get("temp"), (va or {}).get("wind"), (va or {}).get("gust"),
                                vp, _iso(end), fa["src"] if va is not None else None,
                                fp["src"] if fp is not None else None, rec["pub"], rec["pcpPub"]]
                               + raw_copy_columns(fa, fp, lid))
                if asof is None or h > asof:
                    asof = h
            s = day_summary(product_hours, tz, day_iso, now, pending=pending)
            if s is not None:
                entry[product] = s
            if product == SETTLE:
                waiting[lid] = pending
            # each row with a value carries it as the contract shows it, and
            # the day's values over the hours up to it, which the settlement
            # display steps through; only the place files carry them
            if wanted:
                texts = running_texts(product_hours, tz, day_iso)
                for h, row in zip(hours, rows):
                    rec = product_hours.get(_iso(h))
                    if rec is None or row[product] is None:
                        continue
                    cell = row[product]
                    cell["show"] = {var: half_up(rec[var], HOUR_UNIT[var]) for var in HOURLY_VARS}
                    cell["run"] = running(product_hours, tz, day_iso, now, h)
                    # rowShape 3 (owner's approval of display v3, 2026-11-04):
                    # the day so far and the hour before rounding as exact
                    # strings, the stored values as the file holds them, both
                    # files with their times on each server, and the hour's
                    # hourly contract, so the page computes nothing
                    fa, fp, va, vp, sa = views[_iso(h)]
                    end = h + dt.timedelta(hours=1)
                    stored = {var: (va or {}).get(var) for var in ANALYSIS_VARS}
                    stored["precip"] = vp
                    cell["runX"] = texts.get(_iso(h))
                    cell["raw"] = {var: raw_text(var, v) for var, v in stored.items()}
                    cell["x"] = {var: x_text(var, v) for var, v in stored.items()}
                    cell["anlFile"] = file_doc(product, "anl", h, fa, va is not None and _in_time(rec["pub"], cut_iso),
                                               cut_iso, times)
                    cell["pcpFile"] = (file_doc(product, "pcp", end, fp,
                                                vp is not None and _in_time(rec["pcpPub"], cut_iso), cut_iso, times)
                                       if end <= latest else None)
                    cell["hourly"] = hourly_row(_hourly(hourly, store, product, h, sa, cache, now, times), lid)
            valued |= set(product_hours)
        if not entry:
            continue
        day_doc["locations"][lid] = entry
        if wanted:
            loc_asof = max((r["t"] for r in rows if r["t"] in valued), default=None)
            loc_docs[lid] = {"schema": SCHEMA, "id": lid, "day": day_iso, "tz": tz, "rowShape": ROW_SHAPE,
                             "asof": loc_asof, "written": _iso(now), "hours": rows, "summary": entry}
    # a place with no hour read by its cut-off goes to event review, on a day
    # the lane has hours for at other places; a day with nothing read anywhere
    # is not written at all
    if day_doc["locations"]:
        for loc in locs:
            lid, tz = loc["id"], loc["tz"]
            if lid in day_doc["locations"] or now < day_cutoff(day_iso, tz) or waiting.get(lid):
                continue
            entry = {product: review_entry(day_iso, tz) for product in PRODUCTS}
            day_doc["locations"][lid] = entry
            if only is None or lid in only:
                # the rows the loop above made, with no value at any hour, and
                # the hourly contract where an hour's has a state
                loc_docs[lid] = {"schema": SCHEMA, "id": lid, "day": day_iso, "tz": tz, "rowShape": ROW_SHAPE,
                                 "asof": None, "written": _iso(now), "hours": rows_of[lid], "summary": entry}
    day_doc["asof"] = _iso(asof) if asof else None
    return day_doc, loc_docs


def day_status(day_doc: dict, locs: list) -> dict:
    """How far one local date has resolved, counted over every place: `final`
    places have every analysis hour read or known not published, `resolved`
    places are final and have their precipitation total resolved too,
    `closed` places stopped waiting short of a full day. A day is fully
    resolved when `resolved` equals `places`; the page opens on the newest
    such day. A place with no hours yet counts toward `places` and nothing
    else, so a day is never called resolved on the places that happen to
    have landed."""
    entries = (day_doc or {}).get("locations") or {}
    out = {"places": len(locs), "final": 0, "resolved": 0, "closed": 0, "review": 0}
    for loc in locs:
        u = (entries.get(loc["id"]) or {}).get(SETTLE) or {}
        if u.get("closed"):
            out["closed"] += 1
        if u.get("review"):
            out["review"] += 1
        if u.get("final"):
            out["final"] += 1
            if (u.get("precip") or {}).get("resolved"):
                out["resolved"] += 1
    return out


def index_days(store: Storage) -> list:
    """The newest INDEX_DAYS local dates that have a days/ file."""
    days = sorted({k[len(PREFIX + "days/"):-len(".json")] for k in store.list(PREFIX + "days/") if k.endswith(".json")})
    return days[-INDEX_DAYS:]


def refresh_day_status(store: Storage, state: dict, locs: list, days: list) -> int:
    """Fill the status of any listed day the state does not carry yet (the
    first pass after this field was added, or a day file written by an older
    pass) from its days/ file, and drop statuses of days no longer listed.
    A day's status is otherwise kept current by rebuild_days, which writes it
    from the document it has just built. Returns the number of files read."""
    ds = state.setdefault("dayStatus", {})
    read = 0
    for day_iso in days:
        if day_iso not in ds:
            ds[day_iso] = day_status(_read_json(store, DAY_KEY.format(day=day_iso)) or {}, locs)
            read += 1
    for day_iso in list(ds):
        if day_iso not in days:
            del ds[day_iso]
    return read


def last_resolved_day(state: dict, days: list) -> Optional[str]:
    """The newest listed day on which every place has resolved."""
    ds = state.get("dayStatus") or {}
    for day_iso in reversed(days):
        st = ds.get(day_iso)
        if st and st.get("places") and st.get("resolved") == st.get("places"):
            return day_iso
    return None


RAW_HEADER = ["date", "location", "local_hour", "valid_utc", "temp_k", "wind_ms", "gust_ms",
              "precip_mm", "precip_file_utc", "analysis_source", "precip_source", "analysis_published",
              "precip_published"]
# each server's copy of each file, after the columns above (owner's ruling
# 2026-11-03), which keep their order and their meaning. temp_k, wind_ms,
# gust_ms and precip_mm are the values the row settles on, NOMADS's copy when
# the two differ and it was published by the cut-off; analysis_source and
# precip_source name that copy's server; and analysis_published and
# precip_published are the first time the file was on either server, of the
# copies the row settled on. Then, for each file, the pass at which the copy
# the row uses became final (owner's ruling 2026-11-04, empty until then); each
# server's first copy, when this lane first saw it, the server's
# Last-Modified, its values at the place and the sha256 of each message;
# whether the two copies are the same bytes and give the place the same
# values; whether a copy came too late to count (a record that changes
# nothing); and how many replacements each server's file was later seen with,
# and the newest of them. All empty for a file read before 2026-11-03, or not
# read
RAW_COPY_HEADER = (
    ["analysis_settled", "precip_settled"]
    + [f"analysis_{s}_{f}" for s in SERVERS
       for f in ("first_seen", "last_modified", "temp_k", "wind_ms", "gust_ms", "temp_sha256", "wind_sha256",
                 "gust_sha256")]
    + ["analysis_hash_match", "analysis_value_match", "analysis_late_copy"]
    + [f"analysis_{s}_{f}" for s in SERVERS
       for f in ("replacements", "replacement_seen", "replacement_last_modified", "replacement_temp_sha256",
                 "replacement_wind_sha256", "replacement_gust_sha256")]
    + [f"precip_{s}_{f}" for s in SERVERS for f in ("first_seen", "last_modified", "mm", "sha256")]
    + ["precip_hash_match", "precip_value_match", "precip_late_copy"]
    + [f"precip_{s}_{f}" for s in SERVERS
       for f in ("replacements", "replacement_seen", "replacement_last_modified", "replacement_sha256")])
RAW_HEADER = RAW_HEADER + RAW_COPY_HEADER


def raw_copy_columns(fa: Optional[dict], fp: Optional[dict], lid: str) -> list:
    """A raw CSV row's columns for each server's copy, in RAW_COPY_HEADER's
    order, from the row's two file views (file_view)."""
    out = [(fa or {}).get("settled"), (fp or {}).get("settled")]
    for kind, view, fields in (("anl", fa, ANALYSIS_VARS), ("pcp", fp, ("precip",))):
        copies = (view or {}).get("copies") or {}
        later = (view or {}).get("later") or {}
        for s in SERVERS:
            c = copies.get(s)
            v = _value_at(c, kind, lid) if c else None
            vals = [(v or {}).get(var) for var in fields] if kind == "anl" else [v]
            out += ([c.get("seen") if c else None, c.get("published") if c else None] + vals
                    + [((c or {}).get("sha256") or {}).get(var) for var in fields])
        out += [hash_match(copies) if copies else None, value_match(copies, kind, lid) if copies else None,
                view.get("late") if copies else None]
        for s in SERVERS:
            entries = (later.get(s) or {}).get("later") or []
            e = entries[-1] if entries else {}
            out += ([len(entries) if copies else None, e.get("seen"), e.get("published")]
                    + [(e.get("sha256") or {}).get(var) for var in fields])
    return out


DAY_HEADER = ["date", "location", "name", "state", "final", "final_at", "hours", "of",
              "high_f", "high_exact", "low_f", "low_exact", "gust_mph", "gust_exact", "wind_mph", "wind_exact",
              "precip_in", "precip_exact", "precip_hours", "precip_resolved", "precip_resolved_at", "review",
              "precip_review"]


def _csv(rows: list, header: list) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow(["" if v is None else ("true" if v is True else "false" if v is False else v) for v in r])
    return buf.getvalue().encode("utf-8")


def raw_csv(rows: list) -> bytes:
    """Every hour and place of a day in the files' own units: temperature in
    kelvin, wind and gust in metres per second as the analysis file holds
    them, and the precipitation of the hour that starts at the row's time in
    millimetres, with the valid time of the file it comes from, whose copy
    each value is (nodd or nomads), and after them each server's copy
    (RAW_COPY_HEADER). Nothing converted or rounded."""
    return _csv(rows, RAW_HEADER)


def processed_csv(day_iso: str, day_doc: dict, locs: list) -> bytes:
    """The day's values per place as they resolve: the whole value, the
    exact aggregate cut to its precision, the hour counts and the status."""
    rows = []
    for loc in locs:
        e = ((day_doc.get("locations") or {}).get(loc["id"]) or {}).get(SETTLE)
        if not e:
            continue
        g = lambda k, f: (e.get(k) or {}).get(f)
        p = e.get("precip") or {}
        rows.append([day_iso, loc["id"], loc["name"], loc["state"], e.get("final"), e.get("finalAt"),
                     e.get("hours"), e.get("of"), g("high", "value"), g("high", "exact"), g("low", "value"),
                     g("low", "exact"), g("gust", "value"), g("gust", "exact"), g("wind", "value"),
                     g("wind", "exact"), p.get("value"), p.get("exact"), p.get("hours"), p.get("resolved"),
                     p.get("resolvedAt"), bool(e.get("review")), bool(p.get("review"))])
    return _csv(rows, DAY_HEADER)


def refresh_days(store: Storage, state: dict, locs: list, cache: dict, now: dt.datetime, deadline: arch.Deadline,
                 status: dict) -> int:
    """Rebuild, newest first and REFRESH_DAYS_PER_PASS at most, the listed
    days that lack either CSV file or were written in an older LAYOUT, so
    every day the page offers links to files that exist and no day keeps a
    layout the page has left behind. The first pass after LAYOUT changes
    queues every listed day. Returns the number of files written."""
    days = index_days(store)
    if state.get("layout") != LAYOUT:
        state["layout"] = LAYOUT
        state["relayout"] = list(days)
    stale = set(state.get("relayout") or []) & set(days)
    prefix = PREFIX + "csv/"
    have = {k[len(prefix):] for k in store.list(prefix)}
    todo = [d for d in reversed(days)
            if d in stale or f"{d}-raw.csv" not in have or f"{d}-processed.csv" not in have]
    written = done = 0
    for day_iso in todo[:REFRESH_DAYS_PER_PASS]:
        if deadline.over(RESERVE_SECONDS + 60):
            break
        written += rebuild_days(store, {(loc["id"], day_iso) for loc in locs}, locs, cache, now, state)
        stale.discard(day_iso)
        done += 1
    if stale:
        state["relayout"] = sorted(stale)
    else:
        state.pop("relayout", None)
    status["refreshed"] = {"days": done, "left": max(0, len(todo) - done)}
    return written


def empty_loc_doc(lid: str, tz: str, day_iso: str, now: dt.datetime) -> dict:
    """The loc/ document of a place with no hour yet on a listed day: no
    rows and no summary, which the page reads as a day with no hour in yet."""
    return {"schema": SCHEMA, "id": lid, "day": day_iso, "tz": tz, "rowShape": ROW_SHAPE, "asof": None,
            "written": _iso(now), "hours": [], "summary": {}}


def rebuild_days(store: Storage, touched: set, locs: list, cache: dict, now: dt.datetime, state: dict) -> int:
    """Rewrite every days/ file a touched (location, day) pair names, the
    loc/ files of the touched pairs and the day's two CSV files. When a day
    is first listed, each place with no hour of it yet whose cut-off is
    ahead gets a loc/ file with no rows (empty_loc_doc), so its page says no
    hour is in yet instead of failing to read the file: between Eastern and
    Pacific midnight a western place has no hour of the new day (owner's
    approval 2026-11-04). Its first hour replaces the file. Returns the
    number of files written."""
    by_day: Dict[str, set] = {}
    for lid, day in touched:
        by_day.setdefault(day, set()).add(lid)
    written = 0
    for day_iso in sorted(by_day):
        raw: list = []
        day_doc, loc_docs = build_day(store, day_iso, locs, cache, now, state, only=by_day[day_iso], raw=raw)
        if not day_doc["locations"]:
            continue
        if day_iso not in (state.get("dayStatus") or {}):
            # the day is not listed yet: this write lists it
            for loc in locs:
                if (loc["id"] not in day_doc["locations"] and loc["id"] not in loc_docs
                        and now < day_cutoff(day_iso, loc["tz"])):
                    loc_docs[loc["id"]] = empty_loc_doc(loc["id"], loc["tz"], day_iso, now)
        store.put(DAY_KEY.format(day=day_iso), _dump(day_doc), "application/json", CACHE_LIVE)
        store.put(CSV_RAW_KEY.format(day=day_iso), raw_csv(raw), "text/csv; charset=utf-8", CACHE_LIVE)
        store.put(CSV_DAY_KEY.format(day=day_iso), processed_csv(day_iso, day_doc, locs), "text/csv; charset=utf-8",
                  CACHE_LIVE)
        state.setdefault("dayStatus", {})[day_iso] = day_status(day_doc, locs)
        written += 3
        for lid, doc in loc_docs.items():
            u = (doc["summary"].get(SETTLE) or {})
            final = bool(u.get("final")) and bool((u.get("precip") or {}).get("resolved"))
            store.put(LOC_KEY.format(loc=lid, day=day_iso), _dump(doc), "application/json",
                      CACHE_FINAL if final else CACHE_LIVE)
            written += 1
    return written


def days_past_cutoff(store: Storage, locs: list, now: dt.datetime) -> set:
    """(location, day) pairs of recent days whose cut-off has passed while
    their published summary is still provisional, or absent, so a day nobody
    adds an hour to still turns final, or goes to event review, at its
    cut-off."""
    out = set()
    days = set()
    for tz in sorted({loc["tz"] for loc in locs}):
        for back in range(0, 3):
            days.add(local_day_of(now - dt.timedelta(days=back), tz))
    for day_iso in sorted(days):
        entries = (_read_json(store, DAY_KEY.format(day=day_iso)) or {}).get("locations") or {}
        for loc in locs:
            if now < day_cutoff(day_iso, loc["tz"]):
                continue
            s = (entries.get(loc["id"]) or {}).get(SETTLE)
            if s is None or not (s.get("final") or s.get("review")):
                out.add((loc["id"], day_iso))
    return out


# ------------------------------------------------------------------ state
def _product_state() -> dict:
    # newest: the newest hour whose analysis was READ, from either server
    # (what asof and the alarm go by); scanned: how far the live walk has
    # opened stamps; since: when the lane first looked, so a product that has
    # never landed can still become overdue; open: the stamps whose files
    # have not settled, or wait for a copy still to come, each file with when
    # each server was last asked and the download that last failed the check
    # ({server: {tried, bad}}), how it settled for each cut-off (cutoffs) and,
    # carried over from before 2026-11-03, since when (migrated) (work_file);
    # missing / pmissing: hours whose analysis / precipitation file settled
    # on no copy; lastRead: when a pass last read an hour, which the
    # next ten-minute pass's health check counts (health_results);
    # rechecked: the newest hour whose copies were looked at again
    # (recheck_lane)
    return {"newest": None, "scanned": None, "since": None, "open": {}, "reads": 0,
            "missing": [], "pmissing": [], "lastRead": None, "rechecked": None}


def _mark(ps: dict, key: str, t: dt.datetime) -> None:
    iso = _iso(t)
    lst = ps.setdefault(key, [])
    if iso not in lst:
        lst.append(iso)


def new_state() -> dict:
    # healthAt: when a ten-minute pass last advanced the failure streak
    return {"schema": SCHEMA, "layout": LAYOUT, "products": {p: _product_state() for p in PRODUCTS},
            "backfill": {p: {"cursor": None, "oldest": None, "done": False, "queue": []} for p in PRODUCTS},
            "dayStatus": {}, "pruned": None, "precipSwept": None, "healthAt": None}


def load_state(store: Storage, now: Optional[dt.datetime] = None) -> dict:
    s = _read_json(store, STATE_KEY)
    if not s:
        return new_state()
    base = new_state()
    # a state written before LAYOUT existed was written in an older layout
    s.setdefault("layout", None)
    for k, v in base.items():
        if k not in s:
            s[k] = v
    # what a product no longer read (URMA, dropped 2026-09-30) or its
    # re-reads left behind
    for k in ("rereads", "revisions"):
        s.pop(k, None)
    for k in ("products", "backfill"):
        for p in list(s[k]):
            if p not in PRODUCTS:
                del s[k][p]
    for p in PRODUCTS:
        ps = s["products"].setdefault(p, {})
        for k, v in _product_state().items():
            ps.setdefault(k, v)
        s["backfill"].setdefault(p, base["backfill"][p])
        # in a state written before 2026-11-03, an hour the walk passed
        # without reading joins the open list with both its files, and an
        # archived hour whose precipitation was still being fetched with that
        # file. A file the archive holds stands (work_file), so a precipitation
        # file stored short of a place is not fetched again, and one carried
        # over settles on no copy for each day already past its cut-off, which
        # was final without it (settle_cuts)
        since = _iso(now) if now is not None else s.get("updated")
        for g in ps.pop("gaps", None) or []:
            ps["open"].setdefault(g, {k: ({"migrated": since} if since else {}) for k in KINDS})
        for g in ps.pop("precipPending", None) or []:
            ps["open"].setdefault(g, {}).setdefault("pcp", {"migrated": since} if since else {})
    return s


def save_state(store: Storage, state: dict, now: dt.datetime) -> None:
    state["updated"] = _iso(now)
    store.put(STATE_KEY, json.dumps(state, indent=1).encode(), "application/json")


def prune_state(state: dict, now: dt.datetime) -> None:
    """Drop the hours known not published that are past the reach of any
    rebuild, and the open stamps past the reach of either server."""
    oldest = _iso(_floor_hour(now) - dt.timedelta(days=BACKFILL_POINT_DAYS + 3))
    for ps in (state.get("products") or {}).values():
        for k in ("missing", "pmissing"):
            ps[k] = [iso for iso in ps.get(k) or [] if iso >= oldest]
        reach = _iso(_floor_hour(now) - dt.timedelta(days=BACKFILL_POINT_DAYS))
        ps["open"] = {iso: e for iso, e in (ps.get("open") or {}).items() if iso >= reach}


# ------------------------------------------------------------------ the lanes of a pass
def expected_latest(product: str, now: dt.datetime) -> dt.datetime:
    """The newest hour whose file should have landed by now."""
    return _floor_hour(now - dt.timedelta(minutes=PRODUCTS[product]["lagMinutes"]))


def due_latest(product: str, now: dt.datetime) -> dt.datetime:
    """The newest hour whose analysis the live walk looks for. An analysis
    is looked for from its window's opening, LOOK_MINUTES after the hour, two
    minutes before NOAA's earliest publication in the two weeks to
    2026-11-03. At the ten-minute pass's own minutes this is the hour
    expected_latest gives."""
    return _floor_hour(now - dt.timedelta(minutes=LOOK_MINUTES["anl"]))


def next_window_minute(now: dt.datetime) -> dt.datetime:
    """The next minute in a window after now, when the next windowed pass
    runs or the ten-minute pass at :48 or :18 does the windowed pass's work."""
    t = now.astimezone(dt.timezone.utc).replace(second=0, microsecond=0)
    for _ in range(60):
        t += dt.timedelta(minutes=1)
        if t.minute in WINDOW_MINUTES:
            break
    return t


def pass_seconds(now: dt.datetime, budget: Optional[float]) -> float:
    """How long a pass may run. That is PASS_CAP_SECONDS, or the invocation's
    budget when that is less, never closer than WINDOW_YIELD_SECONDS to the
    next minute in a window and never under PASS_FLOOR_SECONDS. So no pass
    keeps the next windowed one waiting on the lease, and the ten-minute pass
    at :48 and :18 does the windowed pass's work and little more."""
    cap = min(budget, PASS_CAP_SECONDS) if budget else PASS_CAP_SECONDS
    room = (next_window_minute(now) - now).total_seconds() - WINDOW_YIELD_SECONDS
    return min(cap, max(PASS_FLOOR_SECONDS, room))


def _scan_cursor(ps: dict) -> Optional[dt.datetime]:
    """How far the live walk has looked: the later of the newest hour read
    and the newest hour scanned."""
    stamps = [ps.get("newest"), ps.get("scanned")]
    ts = [_parse_iso(s) for s in stamps if s]
    return max(ts) if ts else None


def _advance(ps: dict, key: str, t: dt.datetime) -> None:
    if not ps.get(key) or t > _parse_iso(ps[key]):
        ps[key] = _iso(t)


def walk_candidates(product: str, state: dict, now: dt.datetime) -> List[dt.datetime]:
    """The stamps the live walk opens this pass, oldest first, which are every
    hour after the walk's cursor (the later of `newest` and `scanned`) whose
    precipitation file is due, LOOK_MINUTES after its hour ends; the analysis
    stamped with the same hour is due half an hour later. A first pass starts
    at the present; the backfill walks back. A cursor further behind than
    LIVE_CATCHUP_HOURS jumps to the present and queues the span it skipped for
    the backfill, once, and no older than the backfill reaches."""
    ps = state["products"][product]
    latest = _floor_hour(now - dt.timedelta(minutes=LOOK_MINUTES["pcp"]))
    cursor = _scan_cursor(ps)
    if cursor is None:
        start = latest
    elif latest - cursor > dt.timedelta(hours=LIVE_CATCHUP_HOURS):
        start = latest - dt.timedelta(hours=LIVE_HOURS_PER_PASS - 1)
        a = max(cursor + dt.timedelta(hours=1), _floor_hour(now) - dt.timedelta(days=BACKFILL_POINT_DAYS))
        b = start - dt.timedelta(hours=1)
        span = [_iso(a), _iso(b)]
        queue = state["backfill"][product].setdefault("queue", [])
        if b >= a and span not in queue:
            queue.append(span)
        ps["scanned"] = _iso(b)
    else:
        start = cursor + dt.timedelta(hours=1)
    out: List[dt.datetime] = []
    t = start
    while t <= latest:
        out.append(t)
        t += dt.timedelta(hours=1)
    return out


def _due(kind: str, t: dt.datetime, now: dt.datetime) -> bool:
    """Whether a file can be on a server yet, which it can from LOOK_MINUTES
    after its stamp, an analysis's hour and the end of a precipitation file's
    hour."""
    return now >= t + dt.timedelta(minutes=LOOK_MINUTES[kind])


def _newly_due(product: str, kind: str, t: dt.datetime, now: dt.datetime) -> bool:
    """Whether a file is among those newly due, all a windowed pass asks for:
    the analysis of the hour now due, and the PCP_LIVE_HOURS newest
    precipitation files."""
    if kind == "anl":
        return t >= due_latest(product, now)
    newest = _floor_hour(now - dt.timedelta(minutes=LOOK_MINUTES["pcp"]))
    return t >= newest - dt.timedelta(hours=PCP_LIVE_HOURS - 1)


def _reach(server: str) -> dt.timedelta:
    """How far back the lane looks on a server for an hour, a day short of the
    fourteen days NOMADS keeps and the backfill's thirty days on NOAA Open
    Data."""
    return dt.timedelta(hours=NOMADS_KEEP_HOURS if server == "nomads" else BACKFILL_POINT_DAYS * 24)


def _cutoffs(kind: str, t: dt.datetime, locs: list) -> List[dt.datetime]:
    """The cut-offs of the days a file fills, one per zone, earliest first."""
    return sorted({day_cutoff(row_day(kind, t, tz), tz) for tz in {loc["tz"] for loc in locs}})


def _ask(server: str, slot: Optional[dict], t: dt.datetime, now: dt.datetime, force: bool,
         cuts: Sequence[dt.datetime] = ()) -> bool:
    """Whether to look on a server for a file it has not given. It is every
    pass for RETRY_EVERY_PASS_HOURS after the hour, then once an hour, at the
    first pass after each cut-off in `cuts`, so the ten-minute pass that
    settles the days of that cut-off looks once more first, and at the next
    pass after a download that failed the check, while the lane reaches the
    hour there."""
    if now > t + _reach(server):
        return False
    tried = (slot or {}).get("tried")
    if force or not tried or (slot or {}).get("bad") or now - t <= dt.timedelta(hours=RETRY_EVERY_PASS_HOURS):
        return True
    if any(tried < _iso(c) <= _iso(now) for c in cuts):
        return True
    return now - _parse_iso(tried) >= dt.timedelta(hours=1)


def _record(store: Storage, product: str, kind: str, t: dt.datetime, cache: dict) -> Optional[dict]:
    return (get_anl if kind == "anl" else get_precip)(store, product, t, cache)


def _settle(store: Storage, product: str, kind: str, t: dt.datetime, slots: dict, locs: list, now: dt.datetime,
            cache: dict, state: dict, freeze: bool) -> set:
    """settle_cuts, with the hour listed as without the file once it has
    settled on no copy. Returns the (location, day) pairs it changed."""
    touched: set = set()
    if settle_cuts(store, product, kind, t, slots, locs, now, cache, freeze):
        touched |= touched_by(t, locs)
    if _record(store, product, kind, t, cache) is not None and not any(copies_of(store, product, kind, t, cache).values()):
        _mark(state["products"][product], "missing" if kind == "anl" else "pmissing", t)
    return touched


def settle_file(store: Storage, product: str, kind: str, t: dt.datetime, entry: dict, locs: list, now: dt.datetime,
                cache: dict, state: dict, freeze: bool) -> set:
    """Settle an open file as far as it can be (settle_cuts), and take it off
    `entry` once its settled record is written and both servers' copies are
    kept, or RECHECK_AFTER_HOURS after its hour, when every zone's cut-off has
    passed; until then a copy still to come is looked for, kept and flagged
    as late, and never used. Returns the (location, day) pairs it changed."""
    touched: set = set()
    slots = entry.get(kind)
    if slots is None:
        return touched
    rec = _record(store, product, kind, t, cache)
    if rec is None:
        touched |= _settle(store, product, kind, t, slots, locs, now, cache, state, freeze)
        rec = _record(store, product, kind, t, cache)
    if rec is not None and (all(get_copy(store, product, kind, s, t, cache) is not None for s in SERVERS)
                            or now >= t + dt.timedelta(hours=RECHECK_AFTER_HOURS)):
        entry.pop(kind, None)
    return touched


def work_file(store: Storage, product: str, kind: str, t: dt.datetime, entry: dict, locs: list, lattice: dict,
              cache: dict, grid_index: dict, now: dt.datetime, status: dict, budget: NomadsBudget, blocked: set,
              state: dict, force: bool = False, lane: str = "live", deadline: Optional[arch.Deadline] = None,
              freeze: bool = True, settle_first: bool = False, settle: bool = True) -> set:
    """Work one open file of one stamp. Each server that has not given its
    copy is asked for it (NOMADS first), what comes is kept (write_copy) with
    its frames, and the file settles as far as it can (settle_file), which a
    windowed pass does only once both copies are in (`freeze` False). With
    `settle_first` the file settles on what is in hand before any server is
    asked, so what comes is recorded and never used (the daily sweep). A file
    the archive holds from before 2026-11-03 stands, with no copies beside
    it. A server that refuses a file is asked for no more files of that kind
    this pass, as (kind, server) in `blocked`, so an outage costs one failing
    read of each; a download that fails the check is not a copy until a later
    one fails with the same bytes (read_copy); a NOMADS call past the budget
    leaves NOMADS for the next pass, as the server's name in `blocked`. Each
    slot of `entry[kind]` keeps, per server, when it was last asked (`tried`)
    and the last download that failed the check (`bad`), and under
    `cutoffs` how the file settled for each cut-off. The pass's `deadline` is
    looked at before each copy, so a pass overruns it by one copy at most.
    Returns the (location, day) pairs whose files changed."""
    touched: set = set()
    ps = state["products"][product]
    iso = _iso(t)
    slots = entry.setdefault(kind, {})
    rec = _record(store, product, kind, t, cache)
    if rec is not None and "settled" not in rec:
        entry.pop(kind, None)
        return touched
    if not _due(kind, t, now):
        return touched
    if rec is None and (settle_first or slots.get("migrated")):
        # the sweep's file settles on what is in hand before it is read, and a
        # file carried over from before 2026-11-03 for the days already final
        touched |= _settle(store, product, kind, t, slots, locs, now, cache, state, settle_first)
        rec = _record(store, product, kind, t, cache)
    # nothing read once a cut-off has settled changes the map
    settled_any = rec is not None or bool(slots.get("cutoffs"))
    frame_cutoff = _floor_hour(now) - dt.timedelta(days=BACKFILL_FRAME_DAYS)
    cuts = _cutoffs(kind, t, locs)
    for server in READ_ORDER:
        if get_copy(store, product, kind, server, t, cache) is not None:
            slots.pop(server, None)
            continue
        if (server in blocked or (kind, server) in blocked
                or not _ask(server, slots.get(server), t, now, force, cuts)):
            continue
        if deadline is not None and deadline.over(RESERVE_SECONDS):
            break
        other = get_copy(store, product, kind, "nodd" if server == "nomads" else "nomads", t, cache)
        lat = lattice if t >= frame_cutoff and not settled_any and (other is None or server == "nomads") else None
        res = read_copy(product, kind, server, t, locs, lat, now, other=other, budget=budget,
                        prior_bad=(slots.get(server) or {}).get("bad"))
        # when a server was asked is kept only once it can matter, from the
        # hour on which it is asked once an hour (_ask), so a pass that finds
        # nothing new changes nothing
        if now - t >= dt.timedelta(hours=RETRY_EVERY_PASS_HOURS):
            slots.setdefault(server, {})["tried"] = _iso(now)
        if res.status == "skipped":
            blocked.add(server)
            continue
        if res.status == "absent":
            status["absent"].append(f"{product} {iso} {kind} {server}")
            continue
        if res.status in ("error", "suspect"):
            _fail(status, f"{product} {iso} {kind} {server}: {res.error}")
            if res.status == "suspect":
                # not a copy: the next pass downloads it again, and the same
                # bytes failing again make it the server's copy
                slots.setdefault(server, {})["bad"] = res.bad
            blocked.add((kind, server))
            continue
        write_copy(store, product, kind, server, t, res.doc, cache)
        slots.pop(server, None)
        # the map follows the copy that counts, the first copy kept and then
        # NOMADS's where its bytes differ (read_copy samples only those), while
        # no cut-off has settled and when it was published by the first one
        if res.frames and (other is None or (cuts and _pub(res.doc) <= _iso(cuts[0]))):
            stored = (res.doc or {}).get("values")
            write_frames(store, product, t, res.frames, grid_index, now, res.windows,
                         values=None if kind == "pcp" else stored, pcp=stored if kind == "pcp" else None)
            write_grid_index(store, grid_index, now)
        touched |= touched_by(t, locs)
        if other is not None:
            status["copies"].append(f"{product} {iso} {kind} {server}")
        elif kind == "pcp":
            status["pcpRead"].append(f"{product} {iso}")
        elif lane == "live":
            status["read"].append(f"{product} {iso}")
            _advance(ps, "newest", t)
            ps["reads"] = ps.get("reads", 0) + 1
            ps["lastRead"] = _iso(now)
        else:
            status["backfilled"] += 1
            status["backfilledBy"][product] += 1
    if settle:
        touched |= settle_file(store, product, kind, t, entry, locs, now, cache, state, freeze)
    return touched


def live_lane(store: Storage, product: str, state: dict, locs: list, lattice: dict, cache: dict,
              grid_index: dict, now: dt.datetime, deadline: arch.Deadline, status: dict, budget: NomadsBudget,
              light: bool = False) -> set:
    """The live hours (owner's rulings 2026-11-03 and 2026-11-04). The walk
    opens each stamp as its precipitation file becomes due, and each open
    file is asked for from each server that has not given its copy, newest
    stamp first, until the file settles and leaves the open list (work_file).
    On a ten-minute pass every open file is worked, and once the deadline is
    near it is settled without a read, so each settles at the first
    ten-minute pass after its cut-off on the copies in hand. A windowed pass
    (`light`) asks only for the files newly due and settles a file only once
    both copies are in. Returns the (location, day) pairs whose files
    changed."""
    ps = state["products"][product]
    if not ps.get("since"):
        ps["since"] = _iso(now)
    opened = ps.setdefault("open", {})
    for t in walk_candidates(product, state, now):
        opened.setdefault(_iso(t), {k: {} for k in KINDS})
        _advance(ps, "scanned", t)
        _seed_backfill(state, product, _iso(t))
    touched: set = set()
    blocked: set = set()
    for iso in sorted(opened, reverse=True):
        t, entry = _parse_iso(iso), opened[iso]
        for kind in KINDS:
            if kind in entry and (not light or _newly_due(product, kind, t, now)):
                touched |= work_file(store, product, kind, t, entry, locs, lattice, cache, grid_index, now,
                                     status, budget, blocked, state, deadline=deadline, freeze=not light)
        if not entry:
            del opened[iso]
    status["waiting"] += [f"{product} {iso} {' '.join(k for k in KINDS if k in opened[iso])}" for iso in sorted(opened)]
    return touched


def _seed_backfill(state: dict, product: str, iso: str) -> None:
    """The backfill starts just behind the first hour the live walk looked
    at, whether or not that hour was there. On 2026-09-27 both analyses'
    newest hours were late by half an hour when a fresh lane first looked,
    and a cursor seeded only by a read left the backfill idle until one
    landed; the absent hour itself stays a gap of the live lane."""
    bf = state["backfill"][product]
    if bf.get("cursor") is None:
        bf["cursor"] = iso


def precip_sweep_due(state: dict, now: dt.datetime) -> bool:
    """Whether the once-a-day sweep of precipitation for hours whose analysis
    never landed is due."""
    last = state.get("precipSwept")
    return not last or now - _parse_iso(last) >= dt.timedelta(hours=PRECIP_SWEEP_HOURS)


def _stamps(store: Storage, key_fmt: str, product: str, start: dt.datetime) -> set:
    """The hours with an archive key of this kind from `start` on."""
    tail = ".json.gz"
    prefix = key_fmt.format(product=product, stamp="")[:-len(tail)]
    keys = store.list(prefix, start_after=prefix + _stamp(start - dt.timedelta(hours=1)) + tail)
    return {k[len(prefix):-len(tail)] for k in keys if k.startswith(prefix) and k.endswith(tail)}


def _held(store: Storage, product: str, kind: str, start: dt.datetime) -> set:
    """The stamps from `start` on whose file of this kind the archive holds,
    settled, read before 2026-11-03, or as a copy from either server."""
    have = _stamps(store, HOUR_KEY if kind == "anl" else PRECIP_KEY, product, start)
    prefix = COPY_PREFIX.format(product=product)
    for k in store.list(prefix, start_after=prefix + _stamp(start - dt.timedelta(hours=1))):
        parts = k[len(prefix):].split(".")
        if len(parts) >= 2 and parts[1] == kind:
            have.add(parts[0])
    return have


def precip_sweep(store: Storage, state: dict, locs: list, lattice: dict, cache: dict, grid_index: dict,
                 now: dt.datetime, deadline: arch.Deadline, status: dict, budget: NomadsBudget) -> set:
    """Once a day, over every hour the live walk and the backfill have both
    passed and the open list no longer holds, ask both servers again for a
    file the archive has nothing of, neither settled nor as a copy; NOMADS is
    asked while it keeps the hour and within the pass's budget. The file
    settles on no copy first, since its days were final without it, so what
    comes is recorded and flagged as late and never shown or counted (owner's
    ruling 2026-11-04). Hours in a queued catch-up span are left to the
    backfill. A refused file ends that product's sweep until the next day's,
    and a sweep cut short by the deadline or the budget is not stamped and is
    due again next pass. Returns the (location, day) pairs whose files
    changed."""
    touched: set = set()
    window_start = _floor_hour(now) - dt.timedelta(days=BACKFILL_POINT_DAYS)
    tried = filled = 0
    for product in PRODUCTS:
        ps = state["products"][product]
        cursor = _scan_cursor(ps)
        bf = state["backfill"][product]
        if cursor is None or not bf.get("oldest"):
            continue
        start = max(window_start, _parse_iso(bf["oldest"]))
        end = cursor - dt.timedelta(hours=CLOSE_AFTER_HOURS)
        if end < start:
            continue
        queued = set()
        for a, b in bf.get("queue") or []:
            t = _parse_iso(a)
            while t <= _parse_iso(b):
                queued.add(t)
                t += dt.timedelta(hours=1)
        have = {kind: _held(store, product, kind, start) for kind in KINDS}
        opened = ps.setdefault("open", {})
        blocked: set = set()
        t = start
        while t <= end:
            iso_, stamp = _iso(t), _stamp(t)
            want = [kind for kind in KINDS if stamp not in have[kind]]
            if want and t not in queued and iso_ not in opened:
                if deadline.over(RESERVE_SECONDS + 30) or budget.left <= 0:
                    status["swept"] = {"tried": tried, "filled": filled, "done": False}
                    return touched
                tried += 1
                entry = {kind: {} for kind in want}
                failed = status["failed"]
                for kind in want:
                    touched |= work_file(store, product, kind, t, entry, locs, lattice, cache, grid_index, now,
                                         status, budget, blocked, state, force=True, lane="sweep",
                                         deadline=deadline, settle_first=True)
                    if any(copies_of(store, product, kind, t, cache).values()):
                        filled += 1
                if entry:
                    opened[iso_] = entry
                if status["failed"] > failed:
                    break
            t += dt.timedelta(hours=1)
    state["precipSwept"] = _iso(now)
    status["swept"] = {"tried": tried, "filled": filled, "done": True}
    return touched


def backfill_next(product: str, state: dict, now: dt.datetime) -> Optional[dt.datetime]:
    """The next hour the backfill reads for a product, or None when it is
    done: a queued span first (newest hour first), then the walk back from
    the cursor to BACKFILL_POINT_DAYS ago. Nothing older than that is read
    whatever a span says."""
    bf = state["backfill"][product]
    oldest = _floor_hour(now) - dt.timedelta(days=BACKFILL_POINT_DAYS)
    queue = bf.get("queue") or []
    for span in list(queue):
        a, b = _parse_iso(span[0]), _parse_iso(span[1])
        if b < oldest or b < a:
            queue.remove(span)
            continue
        if a < oldest:
            span[0] = _iso(oldest)
        return b
    bf["queue"] = queue
    if bf.get("cursor") is None:
        return None
    t = _parse_iso(bf["cursor"]) - dt.timedelta(hours=1)
    if t < oldest:
        return None
    return t


def backfill_advance(product: str, state: dict, t: dt.datetime) -> None:
    """Move the cursor past t, only ever after t is fully written or known absent."""
    bf = state["backfill"][product]
    iso = _iso(t)
    queue = bf.get("queue") or []
    for span in list(queue):
        if span[1] == iso:
            nb = t - dt.timedelta(hours=1)
            if nb < _parse_iso(span[0]):
                queue.remove(span)
            else:
                span[1] = _iso(nb)
            bf["queue"] = queue
            return
    bf["cursor"] = iso
    bf["oldest"] = iso


def backfill_lane(store: Storage, state: dict, locs: list, lattice: dict, cache: dict, grid_index: dict,
                  now: dt.datetime, deadline: arch.Deadline, status: dict, budget: NomadsBudget) -> set:
    """With the remaining budget, walk the products backwards, newest first,
    alternating when there is more than one. Each hour's two files are looked
    for on both servers (NOMADS while it keeps the hour, within the pass's
    budget) and kept, and each file then settles for every cut-off that has
    passed, on the copies in hand, since this is the first ten-minute pass
    that has worked it (owner's ruling 2026-11-04); the days it fills were
    never final without it (build_day). A file with a day still before its
    cut-off, or whose other copy is still to come within a day of its hour,
    joins the open list. Once the pass's NOMADS budget is spent, or the
    deadline comes within an hour, the walk stops at the hour it is on,
    unpassed and unsettled, and carries on next pass. Hours within
    BACKFILL_FRAME_DAYS get frames; older ones are point reads only."""
    touched: set = set()
    since_save = 0
    # a refused file or a spent budget holds for the rest of the pass, as in the live lane
    blocked = {p: set() for p in PRODUCTS}
    stopped: set = set()
    while not deadline.over(RESERVE_SECONDS):
        progressed = False
        for product in PRODUCTS:
            if deadline.over(RESERVE_SECONDS):
                break
            if product in stopped:
                continue
            t = backfill_next(product, state, now)
            if t is None:
                state["backfill"][product]["done"] = True
                continue
            progressed = True
            iso = _iso(t)
            reach = now <= t + _reach("nomads")
            if reach and "nomads" in blocked[product]:
                # NOMADS's budget for the pass is spent: the hour waits for the next pass
                stopped.add(product)
                continue
            entry = {kind: {} for kind in KINDS}
            try:
                for kind in KINDS:
                    touched |= work_file(store, product, kind, t, entry, locs, lattice, cache, grid_index, now, status,
                                         budget, blocked[product], state, force=True, lane="backfill",
                                         deadline=deadline, settle=False)
                if entry and ((reach and "nomads" in blocked[product]) or deadline.over(RESERVE_SECONDS)):
                    # NOMADS's budget ran out, or the deadline came, within the
                    # hour: it waits for the next pass, unsettled
                    stopped.add(product)
                    continue
                for kind in list(entry):
                    touched |= settle_file(store, product, kind, t, entry, locs, now, cache, state, True)
            except Exception as e:  # noqa: BLE001
                _fail(status, f"{product} {iso}: {type(e).__name__}: {e}")
            if entry:
                state["products"][product].setdefault("open", {}).setdefault(iso, {}).update(entry)
            backfill_advance(product, state, t)
            since_save += 1
            if since_save >= STATE_EVERY:
                save_state(store, state, now)
                since_save = 0
        if not progressed:
            break
    return touched


def recheck_lane(store: Storage, product: str, state: dict, locs: list, cache: dict, now: dt.datetime,
                 deadline: arch.Deadline, status: dict, budget: NomadsBudget) -> set:
    """Each first copy looked at once more RECHECK_AFTER_HOURS after its hour,
    when every zone's cut-off has passed, by its server's Last-Modified from a
    HEAD request (owner's ruling 2026-11-03). A file its server has written
    again since is fetched and hashed and recorded as a later replacement
    (note_later), and never used. RECHECK_HOURS_PER_PASS hours a pass at most,
    NOMADS within the pass's budget; an hour is passed only once all its
    copies have been looked at. Returns the (location, day) pairs whose raw
    CSV rows gained a replacement."""
    ps = state["products"][product]
    upto = _floor_hour(now - dt.timedelta(hours=RECHECK_AFTER_HOURS))
    t = _parse_iso(ps["rechecked"]) + dt.timedelta(hours=1) if ps.get("rechecked") else upto
    t = max(t, _floor_hour(now) - dt.timedelta(days=BACKFILL_POINT_DAYS))
    touched: set = set()
    found = done = 0
    while t <= upto and done < RECHECK_HOURS_PER_PASS and not deadline.over(RESERVE_SECONDS):
        try:
            for kind in KINDS:
                for server in SERVERS:
                    first = get_copy(store, product, kind, server, t, cache)
                    if first is None or now > t + _reach(server):
                        continue
                    head = _call(server, budget, gw.fetch_head, file_url(product, kind, server, t),
                                 tries=FETCH_TRIES, timeout=FETCH_TIMEOUT)
                    if not head or not head.get("lastModified") or head["lastModified"] == first.get("published"):
                        continue
                    res = read_copy(product, kind, server, t, [], None, now, budget=budget, decode=False)
                    if res.status == "skipped":
                        raise Skipped()
                    if res.status in ("error", "suspect"):
                        # a download that fails the check is no replacement
                        _fail(status, f"{product} {_iso(t)} {kind} {server} recheck: {res.error}")
                    elif res.status == "ok" and note_later(store, product, kind, server, t, res.doc, cache):
                        found += 1
                        touched |= touched_by(t, locs)
        except Skipped:
            break
        except Exception as e:  # noqa: BLE001
            _fail(status, f"{product} {_iso(t)} recheck: {type(e).__name__}: {e}")
            break
        ps["rechecked"] = _iso(t)
        t += dt.timedelta(hours=1)
        done += 1
    status["rechecked"] = {"hours": done, "replaced": found}
    return touched


def prune_frames(store: Storage, grid_index: dict, now: dt.datetime) -> int:
    """Remove frames older than FRAME_KEEP_DAYS and their index entries."""
    cutoff = f"{now - dt.timedelta(days=FRAME_KEEP_DAYS):%Y%m%dT%HZ}"
    removed = 0
    for product in PRODUCTS:
        for var in HOURLY_VARS:
            prefix = PREFIX + f"grid/{product}/{var}/"
            for key in store.list(prefix):
                stamp = key[len(prefix):-len(".json")]
                if len(stamp) == len(cutoff) and stamp < cutoff:
                    store.delete(key)
                    removed += 1
            days = grid_index.get(product, {}).get(var, {})
            for day in list(days):
                if day.replace("-", "") < cutoff[:8]:
                    del days[day]
    return removed


def write_index(store: Storage, state: dict, locs: list, grid_index: dict, now: dt.datetime,
                days: Optional[list] = None) -> None:
    if days is None:
        days = index_days(store)
    ds = state.get("dayStatus") or {}
    newest = [_parse_iso(state["products"][p]["newest"]) for p in PRODUCTS if state["products"][p].get("newest")]
    asof = _iso(max(newest)) if newest else None
    sources = {p: {"bucket": PRODUCTS[p]["bucket"], "lagMinutes": PRODUCTS[p]["lagMinutes"],
                   "latest": state["products"][p].get("newest")} for p in PRODUCTS}
    bf = state["backfill"]
    index = {"schema": SCHEMA, "asof": asof, "written": _iso(now), "statement": STATEMENT, "resolvesOn": SETTLE,
             "conventions": CONVENTIONS, "sources": sources, "locations": locs, "days": days,
             "dayStatus": {d: ds[d] for d in days if d in ds},
             "lastResolvedDay": last_resolved_day(state, days),
             "lattice": {"pitch": LATTICE_PITCH, "cols": LATTICE_COLS, "rows": LATTICE_ROWS, "viewBox": "0 0 960 600"},
             "variables": VARIABLES,
             "backfill": {"pointsDays": BACKFILL_POINT_DAYS, "frameDays": BACKFILL_FRAME_DAYS,
                          "done": all(bf[p].get("done") for p in PRODUCTS),
                          "cursor": {p: bf[p].get("cursor") for p in PRODUCTS}}}
    store.put(INDEX_KEY, _dump(index), "application/json", CACHE_LIVE)


def _grid_asof(grid_index: dict) -> Optional[str]:
    """The newest hour that has a frame, from the index itself."""
    newest = None
    for vars_ in grid_index.values():
        for days in vars_.values():
            for day, hhs in days.items():
                for hh in hhs:
                    stamp = f"{day}T{hh}:00:00Z"
                    if newest is None or stamp > newest:
                        newest = stamp
    return newest


def write_grid_index(store: Storage, grid_index: dict, now: dt.datetime, asof: Optional[str] = None) -> None:
    doc = {"schema": SCHEMA, "asof": asof or _grid_asof(grid_index), "written": _iso(now), "frames": grid_index}
    store.put(GRID_INDEX_KEY, _dump(doc), "application/json", CACHE_LIVE)


def health_results(state: dict, status: dict, now: dt.datetime) -> dict:
    """Per product, what this pass says to the lane's failure streak. A read
    is a success. An empty pass is only a failure when a file failed to read
    or the newest hour READ is overdue, because between landings there is
    nothing to read and that is not a fault. A product nothing has ever
    been read for is overdue once the lane has looked for it that long.

    Only the ten-minute pass judges, so the streak counts passes at the
    cadence the alarm was set for. The windowed passes now read most hours
    between two of them and never touch the streak, so an hour any pass read
    since the last judgement (`lastRead` after `healthAt`) is a read here."""
    out = {}
    for p in PRODUCTS:
        ps = state["products"][p]
        ok = any(s.startswith(p + " ") for s in status["read"]) or status["backfilledBy"].get(p, 0) > 0
        last, since = ps.get("lastRead"), state.get("healthAt")
        ok = ok or bool(last and (not since or last > since))
        failed = any(s.startswith(p + " ") for s in status["errors"])
        # a failure outside the product lanes (the rebuild, the prune, a
        # refused snapshot write) is the lane's failure as well
        failed = failed or any(not s.startswith(tuple(q + " " for q in PRODUCTS)) for s in status["errors"])
        newest, since = ps.get("newest"), ps.get("since")
        overdue = False
        if newest:
            overdue = expected_latest(p, now) - _parse_iso(newest) >= dt.timedelta(hours=OVERDUE_HOURS)
        elif since:
            overdue = expected_latest(p, now) - expected_latest(p, _parse_iso(since)) >= dt.timedelta(hours=OVERDUE_HOURS)
        out[p] = {"ok": ok, "attempted": bool(ok or failed or overdue),
                  "error": None if ok else ("read failed" if failed else
                                            ("no hour since %s" % (newest or since) if overdue else None))}
    return out


def _health(store: Storage, state: dict, status: dict, now: dt.datetime) -> list:
    """Advance the lane's failure streaks and name the alarms they raise."""
    try:
        health = arch.update_health(store, health_results(state, status, now), now, key=HEALTH_KEY)
        state["healthAt"] = _iso(now)
        # a product the lane no longer reads keeps its last streak in the
        # file and raises nothing
        return ["analysis: no %s hour readable for %d passes" % (p, health[p]["fail_streak"])
                for p in arch.alarms_in(health) if p in PRODUCTS]
    except Exception as e:  # noqa: BLE001
        status["errors"].append(f"health: {type(e).__name__}: {e}")
        return []


# ------------------------------------------------------------------ the pass
def analysis_pass(cfg: dict, store: Storage, now: Optional[dt.datetime] = None, window: bool = False) -> int:
    """Entry point of both passes, the ten-minute pass and, with `window`,
    the windowed pass (window_pass). Returns 1 only when the lane itself
    failed (a failure no product is to blame for) and nothing was read, so
    the scheduler flags an outage. A failed read alone returns 0: it is in
    the status line, and the failure streak raises the lane's alarm once
    reads keep failing. A missing object is an absence, reported and never a
    failure. A pass that finds the lease held
    writes nothing and returns 0 with the status "lease held", which is not
    a failure and raises no alarm. Each lane is guarded on its own so one
    lane's trouble never costs the day rebuild, the state or the indexes."""
    t0 = time.time()
    now = now or dt.datetime.now(dt.timezone.utc)
    mode = "window" if window else "baseline"
    gw.set_user_agent(cfg.get("user_agent", ""))
    budget = arch.remaining_budget(cfg)
    status: dict = {"kind": "analysis", "mode": mode, "read": [], "pcpRead": [], "copies": [], "absent": [], "waiting": [],
                    "errors": [], "failed": 0, "laneFailed": 0, "backfilled": 0, "backfilledBy": {p: 0 for p in PRODUCTS}, "days": 0,
                    "pruned": 0}
    if grib2 is None:
        status["errors"].append("pipeline.grib2 is not available; nothing read")
        arch.LAST_STATUS = {"job": "analysis", "mode": mode, "errors": 1, "alarms": ["analysis: grib2 decoder missing"]}
        print(json.dumps(status))
        return 1
    # the lease is taken before the state is read, so no two passes ever work
    # on the state and the files at once
    lease = Lease(store, LEASE_KEY)
    try:
        got = lease.acquire(now, (budget or LEASE_DEFAULT_SECONDS) + LEASE_SPARE_SECONDS, mode)
    except Exception as e:  # noqa: BLE001 - the store cannot say who holds the lane, so nothing is written
        status["errors"].append(f"lease: {type(e).__name__}: {e}")
        return _close(status, t0, mode, 1, lease="unknown")
    if not got:
        held = lease.held or {}
        status["holder"] = {k: held.get(k) for k in ("mode", "acquired", "expires")}
        return _close(status, t0, mode, 0, lease="held")
    state, alarms = None, []
    try:
        state, alarms = _run(store, now, window, budget, status)
    except Exception as e:  # noqa: BLE001 - the last resort; _run guards its own steps
        _fail(status, f"pass: {type(e).__name__}: {e}")
    finally:
        try:
            if not lease.release(now + dt.timedelta(seconds=time.time() - t0)):
                _fail(status, "lease: taken over before this pass released it")
        except Exception as e:  # noqa: BLE001 - a lease not released expires on its own
            status["errors"].append(f"lease release: {type(e).__name__}: {e}")
    reads = len(status["read"]) + len(status["pcpRead"]) + status["backfilled"]
    # A failed read alone does not fail the pass (owner, 2026-11-07). NOMADS puts up a file's .idx before the file is
    # whole, so a read in that moment fails and a pass a minute or two later takes it, and a server's 503 clears as
    # fast; failing the pass for it paged the owner several times a day for hours the lane read minutes later. Each
    # failure stays in the status line, and the failure streak raises the lane's alarm once reads keep failing (an
    # hour of ten-minute passes, inside the hourly contracts' 90-minute cut-off). The lane's own failure still fails
    # a pass that read nothing, as any failure did before.
    errors = 1 if (status["laneFailed"] and reads == 0) else 0
    return _close(status, t0, mode, errors, alarms=alarms, state=state, lease="released")


def window_pass(cfg: dict, store: Storage, now: Optional[dt.datetime] = None) -> int:
    """The windowed pass, the job analysis-window (ops/aws/template.yaml),
    which reads the files newly due, writes what they change and does nothing
    else."""
    return analysis_pass(cfg, store, now, window=True)


def _run(store: Storage, now: dt.datetime, window: bool, budget: Optional[float], status: dict) -> Tuple[dict, list]:
    """A pass's work under the lease, the ten-minute pass's steps or the
    windowed pass's few. Returns the state and the alarms raised."""
    deadline = arch.Deadline(pass_seconds(now, budget))
    nomads = NomadsBudget()                         # this pass's NOMADS calls, shared by every lane
    alarms: list = []
    locs = load_locations()
    lattice = load_lattice()
    state = load_state(store, now)
    loaded = json.dumps(state, sort_keys=True)      # a windowed pass writes the state only when it moved
    cache: dict = {}
    grid_index = (_read_json(store, GRID_INDEX_KEY) or {}).get("frames") or {}
    # the frames of a product the lane no longer reads (URMA, dropped
    # 2026-09-30) leave the index; the files stay where they are
    for p in list(grid_index):
        if p not in PRODUCTS:
            del grid_index[p]
    touched: set = set()
    try:
        # 1. the live hours, each file from both servers, then 2. the days
        # they touch (and on the ten-minute pass any day due to close)
        for product in PRODUCTS:
            try:
                touched |= live_lane(store, product, state, locs, lattice, cache, grid_index, now, deadline, status,
                                     nomads, light=window)
            except Exception as e:  # noqa: BLE001
                _fail(status, f"{product} live lane: {type(e).__name__}: {e}")
        if not window:
            try:
                touched |= days_past_cutoff(store, locs, now)
            except Exception as e:  # noqa: BLE001
                _fail(status, f"days to close: {type(e).__name__}: {e}")
        status["days"] += rebuild_days(store, touched, locs, cache, now, state)
        # the rest is the ten-minute pass's, and the windowed pass has done its work
        if not window:
            save_state(store, state, now)
            # 3. once a day, the files of hours both walks passed without them
            if not deadline.over(RESERVE_SECONDS + 120) and precip_sweep_due(state, now):
                try:
                    changed = precip_sweep(store, state, locs, lattice, cache, grid_index, now, deadline, status,
                                           nomads)
                    status["days"] += rebuild_days(store, changed, locs, cache, now, state)
                    save_state(store, state, now)
                except Exception as e:  # noqa: BLE001
                    _fail(status, f"precipitation sweep: {type(e).__name__}: {e}")
            # 3b. listed days without their CSV files or in an older layout, a few a pass
            if not deadline.over(RESERVE_SECONDS + 120):
                try:
                    status["days"] += refresh_days(store, state, locs, cache, now, deadline, status)
                    save_state(store, state, now)
                except Exception as e:  # noqa: BLE001
                    _fail(status, f"refresh days: {type(e).__name__}: {e}")
            # 3c. each first copy looked at once more after every cut-off,
            # then the days whose raw CSV a replacement adds to
            for product in PRODUCTS:
                try:
                    changed = recheck_lane(store, product, state, locs, cache, now, deadline, status, nomads)
                    status["days"] += rebuild_days(store, changed, locs, cache, now, state)
                except Exception as e:  # noqa: BLE001
                    _fail(status, f"{product} recheck: {type(e).__name__}: {e}")
            # 4. backfill with what remains, then the days it touched
            try:
                bt = backfill_lane(store, state, locs, lattice, cache, grid_index, now, deadline, status, nomads)
                status["days"] += rebuild_days(store, bt, locs, cache, now, state)
            except Exception as e:  # noqa: BLE001
                _fail(status, f"backfill: {type(e).__name__}: {e}")
            # 5. prune once a day
            pruned_at = state.get("pruned")
            if not pruned_at or now - _parse_iso(pruned_at) >= dt.timedelta(hours=24):
                try:
                    status["pruned"] = prune_frames(store, grid_index, now)
                    prune_state(state, now)
                    state["pruned"] = _iso(now)
                except Exception as e:  # noqa: BLE001
                    _fail(status, f"prune: {type(e).__name__}: {e}")
    except Exception as e:  # noqa: BLE001 - the last resort; the lanes guard their own hours
        _fail(status, f"pass: {type(e).__name__}: {e}")
    finally:
        status["nomadsCalls"] = nomads.used
        try:
            if window:
                # the windowed pass writes what changed and nothing else, the
                # state when it moved and the index, last, when a file came in.
                # Every frame written was followed by its grid index, and the
                # health streak is the ten-minute pass's (health_results)
                if json.dumps(state, sort_keys=True) != loaded:
                    save_state(store, state, now)
                if touched or status["read"] or status["pcpRead"]:
                    write_index(store, state, locs, grid_index, now)
            else:
                # the grid index, the health file, the state and the index are
                # written whatever happened above, the index last so a reader
                # sees a consistent set and every frame on the bucket is listed;
                # the health file before the state, which keeps when it was judged
                days = index_days(store)
                try:
                    refresh_day_status(store, state, locs, days)
                except Exception as e:  # noqa: BLE001 - a status is a convenience; the index goes out without it
                    _fail(status, f"day status: {type(e).__name__}: {e}")
                write_grid_index(store, grid_index, now)
                alarms = _health(store, state, status, now)
                save_state(store, state, now)
                write_index(store, state, locs, grid_index, now, days=days)
        except Exception as e:  # noqa: BLE001
            _fail(status, f"index: {type(e).__name__}: {e}")
    return state, alarms


def _close(status: dict, t0: float, mode: str, errors: int, alarms: Optional[list] = None,
           state: Optional[dict] = None, lease: str = "released") -> int:
    """Print the pass's status line, leave its summary in arch.LAST_STATUS
    and return its exit code."""
    status["lease"] = lease
    status["seconds"] = round(time.time() - t0, 1)
    # the lists are for the log; the counts are what the status carries
    for k in ("read", "pcpRead", "copies", "absent", "waiting", "errors"):
        status[k + "Count"] = len(status[k])
        status[k] = status[k][:12]
    arch.LAST_STATUS = {"job": "analysis", "mode": mode, "lease": lease, "errors": errors, "alarms": alarms or [],
                        "seconds": status["seconds"], "read": status["readCount"], "pcpRead": status["pcpReadCount"],
                        "backfilled": status["backfilled"], "days": status["days"], "absent": status["absentCount"],
                        "failed": status["failed"], "copies": status["copiesCount"],
                        "nomadsCalls": status.get("nomadsCalls", 0),
                        "newest": {p: state["products"][p].get("newest") for p in PRODUCTS} if state else {}}
    print(json.dumps(status, ensure_ascii=False))
    return errors
