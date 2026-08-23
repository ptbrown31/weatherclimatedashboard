"""The exchange's implied median on a scored day: picking the last quote pass
before local midnight out of the quote archive, and reading a ladder out of it.
No network."""
import datetime as dt
import gzip
import json
import os
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import scorecard, storage   # noqa: E402

U = dt.timezone.utc
NY = ZoneInfo("America/New_York")
CITY = {"station": "KLGA", "city": "New York City", "tz": "America/New_York", "unit": "F"}


def rows_for(day, high_strikes, low_strikes):
    """A pass body: Yes prices falling through 50c across the high ladder and
    rising across the low ladder, plus a decoy station and a decoy day."""
    out = []
    for k, mid in high_strikes:
        out.append({"station": "KLGA", "day": day, "side": "high", "strike": k, "bid": mid - 0.01, "ask": mid + 0.01})
    for k, mid in low_strikes:
        out.append({"station": "KLGA", "day": day, "side": "low", "strike": k, "bid": mid - 0.01, "ask": mid + 0.01})
    out.append({"station": "KLAX", "day": day, "side": "high", "strike": 70.0, "bid": 0.4, "ask": 0.6})
    out.append({"station": "KLGA", "day": "2099-01-01", "side": "high", "strike": 5.0, "bid": 0.4, "ask": 0.6})
    return out


def put_pass(st, stamp, body):
    st.put(f"archive/market/{stamp[:8]}/{stamp[8:]}.json.gz",
           gzip.compress(json.dumps(body).encode()), "application/gzip")


class MarketOnAScoredDay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = storage.LocalStorage(self.tmp.name)
        self.cache = {"list": {}, "body": {}}
        # 2026-08-22 local midnight in New York is 2026-08-22T04:00Z
        self.day = dt.date(2026, 8, 22)

    def test_picks_the_last_pass_before_local_midnight(self):
        early = {"asof": "2026-08-22T03:43:09Z", "rows": rows_for("2026-08-22", [(80.0, 0.9), (84.0, 0.1)], [])}
        late = {"asof": "2026-08-22T03:53:09Z", "rows": rows_for("2026-08-22", [(80.0, 0.7), (84.0, 0.3)], [])}
        after = {"asof": "2026-08-22T04:03:09Z", "rows": rows_for("2026-08-22", [(80.0, 0.1), (84.0, 0.0)], [])}
        put_pass(self.st, "20260822034309", early)
        put_pass(self.st, "20260822035309", late)
        put_pass(self.st, "20260822040309", after)
        got = scorecard.market_levels(self.st, CITY, NY, self.day, self.cache)
        self.assertEqual(got["asof"], "2026-08-22T03:53:09Z")      # the last one before midnight, not after it
        self.assertAlmostEqual(got["high"], 82.0, places=2)        # 0.7 -> 0.3 crosses 50c midway between 80 and 84

    def test_reaches_back_a_utc_day_when_midnight_falls_early(self):
        body = {"asof": "2026-08-21T23:57:00Z", "rows": rows_for("2026-08-22", [(70.0, 0.8), (72.0, 0.2)], [])}
        put_pass(self.st, "20260821235700", body)
        got = scorecard.market_levels(self.st, CITY, NY, self.day, self.cache)
        self.assertEqual(got["asof"], "2026-08-21T23:57:00Z")
        self.assertAlmostEqual(got["high"], 71.0, places=2)

    def test_low_ladder_rises_through_the_crossing(self):
        # P(low < K) rises with K, so the median is where it passes 50c going up
        body = {"asof": "2026-08-22T03:50:00Z", "rows": rows_for("2026-08-22", [], [(66.0, 0.25), (68.0, 0.75)])}
        put_pass(self.st, "20260822035000", body)
        got = scorecard.market_levels(self.st, CITY, NY, self.day, self.cache)
        self.assertAlmostEqual(got["low"], 67.0, places=2)
        self.assertNotIn("high", got)

    def test_other_stations_and_other_days_are_not_mixed_in(self):
        body = {"asof": "2026-08-22T03:50:00Z", "rows": rows_for("2026-08-22", [(80.0, 0.9), (84.0, 0.1)], [])}
        put_pass(self.st, "20260822035000", body)
        other = dict(CITY, station="KBOS")
        self.assertIsNone(scorecard.market_levels(self.st, other, NY, self.day, self.cache))
        self.assertIsNone(scorecard.market_levels(self.st, CITY, NY, dt.date(2026, 8, 20), self.cache))

    def test_no_archive_is_not_an_error(self):
        self.assertIsNone(scorecard.market_levels(self.st, CITY, NY, self.day, self.cache))

    def test_a_corrupt_pass_is_skipped_not_raised(self):
        self.st.put("archive/market/20260822/035000.json.gz", b"not gzip", "application/gzip")
        self.assertIsNone(scorecard.market_levels(self.st, CITY, NY, self.day, self.cache))

    def test_listings_and_bodies_are_fetched_once_per_pass(self):
        body = {"asof": "2026-08-22T03:50:00Z", "rows": rows_for("2026-08-22", [(80.0, 0.9), (84.0, 0.1)], [])}
        put_pass(self.st, "20260822035000", body)
        gets = {"n": 0}
        real = self.st.get
        self.st.get = lambda k: (gets.__setitem__("n", gets["n"] + 1), real(k))[1]
        for sid in ("KLGA", "KLGA", "KLGA"):
            scorecard.market_levels(self.st, dict(CITY, station=sid), NY, self.day, self.cache)
        self.assertEqual(gets["n"], 1)                              # every station in a zone shares one pass


if __name__ == "__main__":
    unittest.main()
