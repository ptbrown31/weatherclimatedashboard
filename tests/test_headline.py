"""The landing page's few numbers: ranking and rounding, the largest miss and
how a tie is broken, the widest disagreement about tomorrow with the market
counted as a source, which hurricane contract is chosen and what stands in for
it, and an empty store. No network: every input is a hand-written snapshot in a
temporary directory."""
import datetime as dt
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import headline, storage   # noqa: E402

NOW = dt.datetime(2026, 8, 23, 12, 0, tzinfo=dt.timezone.utc)


def day(date, obs_high, **srcs):
    """One scored day: the observed high plus {source: forecast high}. The
    error the scorecard stores is forecast minus observed."""
    row = {"date": date, "obsHigh": obs_high, "obsLow": obs_high - 20.0, "n": 24}
    for src, high in srcs.items():
        row[src] = {"high": high, "low": high - 20.0, "cycle": "20260822T0000Z", "lead": 6.0,
                    "errHigh": round(high - obs_high, 1), "errLow": 0.0}
    return row


def card(stations, asof="2026-08-23T06:00:00Z"):
    return {"schema": 2, "asof": asof, "stations": stations}


def station(city, days, unit="F"):
    return {"city": city, "tz": "America/New_York", "unit": unit, "daysScored": len(days),
            "summary": {}, "days": days}


def city_row(sid, city, tomorrow="2026-08-24", unit="F", **highs):
    row = {"station": sid, "city": city, "unit": unit,
           "markers": {"day": "2026-08-23", "tomorrow": tomorrow}}
    for src in ("nws", "nbm", "lamp", "mav"):
        row[src + "HighTomorrow"] = highs.get(src)
        row[src + "LowTomorrow"] = None
    return row


def market_row(sid, implied, tomorrow="2026-08-24"):
    return {"station": sid, "listed": True, "day": "2026-08-23", "tomorrow": tomorrow,
            "impliedHighTomorrow": implied, "impliedLowTomorrow": None}


def contract(spec, label, strike, expiry, bid, ask, mid):
    return {"spec": spec, "expiryLabel": expiry, "strike": strike, "label": label,
            "bid": bid, "ask": ask, "mid": mid}


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = storage.LocalStorage(self.tmp.name)

    def put(self, key, body):
        self.st.put(key, json.dumps(body).encode(), "application/json")

    def run_job(self, now=NOW):
        # at a stated moment, never the wall clock: which contract is this
        # month's depends on it, and these fixtures are written around NOW
        self.assertEqual(headline.headline_pass({}, self.st, now), 0)
        return json.loads(self.st.get(headline.SNAP_KEY))


# ------------------------------------------------------------------ accuracy
class Accuracy(Store):
    def test_ranks_ascending_and_rounds_to_two_places(self):
        # KBOS: nws misses by 1 every day, nbm by 2, mav by 4 on one day only.
        # KLGA: nws misses by 2 every day, so pooled nws mae is 1.5.
        days_a = [day("2026-08-%02d" % d, 80.0, nws=81.0, nbm=82.0) for d in range(16, 23)]
        days_b = [day("2026-08-%02d" % d, 70.0, nws=72.0) for d in range(16, 23)]
        days_a[0]["mav"] = {"high": 84.0, "errHigh": 4.0}
        self.put("snapshots/scorecard.json", card({"KBOS": station("Boston", list(reversed(days_a))),
                                                   "KLGA": station("New York City", list(reversed(days_b)))}))
        got = self.run_job()["accuracy"]
        self.assertEqual(got["days"], 7)
        self.assertEqual(got["side"], "high")
        self.assertEqual(got["scoredDay"], "2026-08-22")
        self.assertEqual([r["src"] for r in got["rank"]], ["nws", "nbm", "mav"])
        self.assertEqual(got["rank"][0], {"src": "nws", "mae": 1.5, "n": 14})
        self.assertEqual(got["rank"][1], {"src": "nbm", "mae": 2.0, "n": 7})
        self.assertEqual(got["rank"][2], {"src": "mav", "mae": 4.0, "n": 1})

    def test_rounds_a_repeating_mean_to_two_places(self):
        days = [day("2026-08-20", 80.0, nws=81.0), day("2026-08-21", 80.0, nws=81.0),
                day("2026-08-22", 80.0, nws=82.0)]
        self.put("snapshots/scorecard.json", card({"KBOS": station("Boston", days)}))
        self.assertEqual(self.run_job()["accuracy"]["rank"][0]["mae"], 1.33)

    def test_only_the_last_seven_days_are_pooled(self):
        # ten days, the three oldest badly wrong: they must not move the mean
        days = [day("2026-08-%02d" % d, 80.0, nws=81.0) for d in range(13, 23)]
        for d in days[:3]:
            d["nws"]["errHigh"] = 20.0
        self.put("snapshots/scorecard.json", card({"KBOS": station("Boston", list(reversed(days)))}))
        got = self.run_job()["accuracy"]
        self.assertEqual(got["rank"][0], {"src": "nws", "mae": 1.0, "n": 7})
        self.assertEqual(got["days"], 7)

    def test_the_exchange_is_ranked_with_the_forecast_products(self):
        days = [day("2026-08-22", 80.0, nws=83.0)]
        days[0]["fx"] = {"high": 80.5, "errHigh": 0.5, "asof": "2026-08-22T03:50:00Z"}
        self.put("snapshots/scorecard.json", card({"KBOS": station("Boston", days)}))
        rank = self.run_job()["accuracy"]["rank"]
        self.assertEqual([r["src"] for r in rank], ["fx", "nws"])
        self.assertEqual(rank[0]["mae"], 0.5)

    def test_a_celsius_station_is_not_pooled_with_the_rest(self):
        f = station("Boston", [day("2026-08-22", 80.0, nws=81.0)])
        c = station("Frankfurt", [day("2026-08-22", 30.0, nws=38.0)], unit="C")
        self.put("snapshots/scorecard.json", card({"KBOS": f, "EDDF": c}))
        self.assertEqual(self.run_job()["accuracy"]["rank"], [{"src": "nws", "mae": 1.0, "n": 1}])

    def test_a_source_with_no_scored_day_is_left_out(self):
        days = [day("2026-08-22", 80.0, nws=81.0)]
        days[0]["lamp"] = {"high": None, "errHigh": None}
        self.put("snapshots/scorecard.json", card({"KBOS": station("Boston", days)}))
        self.assertEqual([r["src"] for r in self.run_job()["accuracy"]["rank"]], ["nws"])


# ------------------------------------------------------------ largest error
class LargestError(Store):
    def test_picks_the_biggest_miss_on_the_newest_day(self):
        boston = [day("2026-08-22", 85.0, nws=86.0, mav=91.0), day("2026-08-21", 70.0, nws=99.0)]
        lga = [day("2026-08-22", 90.0, nws=88.0), day("2026-08-21", 70.0, nws=60.0)]
        self.put("snapshots/scorecard.json", card({"KBOS": station("Boston", boston),
                                                   "KLGA": station("New York City", lga)}))
        got = self.run_job()["largestError"]
        self.assertEqual(got, {"station": "KBOS", "city": "Boston", "date": "2026-08-22", "side": "high",
                               "observed": 85.0, "forecast": 91.0, "source": "mav", "error": 6.0})

    def test_a_tie_goes_to_the_warm_miss(self):
        # KBOS is six degrees cold, KLGA six degrees warm, on the same day
        self.put("snapshots/scorecard.json", card({
            "KBOS": station("Boston", [day("2026-08-22", 85.0, nws=79.0)]),
            "KLGA": station("New York City", [day("2026-08-22", 85.0, nws=91.0)])}))
        got = self.run_job()["largestError"]
        self.assertEqual(got["station"], "KLGA")
        self.assertEqual(got["error"], 6.0)

    def test_an_exact_tie_goes_to_the_first_station_by_name(self):
        self.put("snapshots/scorecard.json", card({
            "KLGA": station("New York City", [day("2026-08-22", 85.0, nws=91.0)]),
            "KBOS": station("Boston", [day("2026-08-22", 85.0, nws=91.0)])}))
        self.assertEqual(self.run_job()["largestError"]["station"], "KBOS")

    def test_an_older_day_never_wins(self):
        days = [day("2026-08-22", 85.0, nws=86.0), day("2026-08-21", 85.0, nws=99.0)]
        self.put("snapshots/scorecard.json", card({"KBOS": station("Boston", days)}))
        got = self.run_job()["largestError"]
        self.assertEqual((got["date"], got["error"]), ("2026-08-22", 1.0))


# ------------------------------------------------------------ widest spread
class WidestSpread(Store):
    def test_picks_the_station_whose_sources_disagree_most(self):
        self.put("snapshots/summary.json", {"schema": 2, "asof": "2026-08-23T11:00:00Z", "cities": [
            city_row("KBOS", "Boston", nws=80.0, nbm=87.0, mav=83.0),
            city_row("KLGA", "New York City", nws=90.0, nbm=91.0)]})
        got = self.run_job()["widestSpread"]
        self.assertEqual(got, {"station": "KBOS", "city": "Boston", "day": "2026-08-24", "side": "high",
                               "spread": 7.0, "low": 80.0, "high": 87.0, "sources": ["nws", "nbm", "mav"]})

    def test_the_market_implied_counts_as_a_source(self):
        self.put("snapshots/summary.json", {"schema": 2, "asof": "2026-08-23T11:00:00Z", "cities": [
            city_row("KBOS", "Boston", nws=80.0, nbm=81.0),
            city_row("KLGA", "New York City", nws=90.0, nbm=91.0)]})
        self.put("snapshots/market/summary.json", {"schema": 1, "asof": "2026-08-23T10:45:00Z",
                                                   "cities": [market_row("KBOS", 88.5)]})
        got = self.run_job()["widestSpread"]
        self.assertEqual(got["station"], "KBOS")
        self.assertEqual(got["sources"], ["nws", "nbm", "fx"])
        self.assertEqual((got["low"], got["high"], got["spread"]), (80.0, 88.5, 8.5))

    def test_a_market_row_for_another_day_is_not_mixed_in(self):
        # a quote pass written before the station's local midnight names the
        # day before as its tomorrow, and must not join this comparison
        self.put("snapshots/summary.json", {"schema": 2, "asof": "2026-08-23T11:00:00Z", "cities": [
            city_row("KBOS", "Boston", nws=80.0, nbm=81.0)]})
        self.put("snapshots/market/summary.json", {"schema": 1, "asof": "2026-08-23T10:45:00Z",
                                                   "cities": [market_row("KBOS", 95.0, tomorrow="2026-08-23")]})
        got = self.run_job()["widestSpread"]
        self.assertEqual(got["sources"], ["nws", "nbm"])
        self.assertEqual(got["spread"], 1.0)

    def test_one_source_is_not_a_spread(self):
        self.put("snapshots/summary.json", {"schema": 2, "asof": "2026-08-23T11:00:00Z", "cities": [
            city_row("KBOS", "Boston", nws=80.0),
            city_row("KLGA", "New York City", nws=90.0, nbm=92.0)]})
        got = self.run_job()["widestSpread"]
        self.assertEqual(got["station"], "KLGA")

    def test_a_tie_goes_to_the_first_station_by_name(self):
        self.put("snapshots/summary.json", {"schema": 2, "asof": "2026-08-23T11:00:00Z", "cities": [
            city_row("KLGA", "New York City", nws=90.0, nbm=95.0),
            city_row("KBOS", "Boston", nws=80.0, nbm=85.0)]})
        self.assertEqual(self.run_job()["widestSpread"]["station"], "KBOS")

    def test_a_celsius_station_is_skipped(self):
        self.put("snapshots/summary.json", {"schema": 2, "asof": "2026-08-23T11:00:00Z", "cities": [
            city_row("EDDF", "Frankfurt", unit="C", nws=20.0, nbm=32.0),
            city_row("KBOS", "Boston", nws=80.0, nbm=83.0)]})
        self.assertEqual(self.run_job()["widestSpread"]["station"], "KBOS")


# ---------------------------------------------------------------- hurricane
def hurricane_group(markets):
    return {"schema": 1, "group": "hurricane", "asof": "2026-08-23T10:45:00Z", "markets": markets}


MHCMA = {"symbol": "MHCMA", "name": "Atlantic Major Hurricanes", "contracts": [
    contract("2026.8", "Above 0", 0.0, "August 2026", 0.29, 0.31, 0.30),
    contract("2026.8", "Above 1", 1.0, "August 2026", 0.02, 0.08, 0.05),
    contract("2026.9", "Above 0", 0.0, "September 2026", 0.36, 0.42, 0.39)]}

HCAB = {"symbol": "HCAB", "name": "Hurricane Count Atlantic Basin", "contracts": [
    contract("2026.12", "At Least 1", 1.0, "2026", None, None, None),
    contract("2026.12", "At Least 3", 3.0, "2026", 0.65, 0.85, 0.75),
    contract("2026.12", "At Least 5", 5.0, "2026", 0.38, 0.68, 0.53)]}


class Hurricane(Store):
    def test_picks_this_months_contract_at_a_strike_of_zero(self):
        self.put("snapshots/market/hurricane.json", hurricane_group([MHCMA, HCAB]))
        self.assertEqual(self.run_job()["hurricane"],
                         {"symbol": "MHCMA", "label": "Above 0", "expiryLabel": "August 2026",
                          "yes": 0.30, "bid": 0.29, "ask": 0.31})

    def test_falls_back_to_the_lowest_priced_season_strike(self):
        # no August contract listed: the season count stands in, and its
        # lowest strike has no price so the next one up is taken
        september = {"symbol": "MHCMA", "contracts": [c for c in MHCMA["contracts"] if c["spec"] == "2026.9"]}
        self.put("snapshots/market/hurricane.json", hurricane_group([september, HCAB]))
        self.assertEqual(self.run_job()["hurricane"],
                         {"symbol": "HCAB", "label": "At Least 3", "expiryLabel": "2026",
                          "yes": 0.75, "bid": 0.65, "ask": 0.85})

    def test_an_unpriced_month_contract_falls_back_too(self):
        unpriced = {"symbol": "MHCMA", "contracts": [contract("2026.8", "Above 0", 0.0, "August 2026", None, None, None)]}
        self.put("snapshots/market/hurricane.json", hurricane_group([unpriced, HCAB]))
        self.assertEqual(self.run_job()["hurricane"]["symbol"], "HCAB")

    def test_the_key_is_left_out_when_neither_market_is_listed(self):
        self.put("snapshots/market/hurricane.json", hurricane_group([{"symbol": "SWTUS", "contracts": []}]))
        self.assertNotIn("hurricane", self.run_job())

    def test_a_one_sided_book_keeps_the_side_it_has(self):
        one_sided = {"symbol": "MHCMA", "contracts": [contract("2026.8", "Above 0", 0.0, "August 2026", None, 0.31, 0.31)]}
        self.put("snapshots/market/hurricane.json", hurricane_group([one_sided]))
        self.assertEqual(self.run_job()["hurricane"], {"symbol": "MHCMA", "label": "Above 0",
                                                       "expiryLabel": "August 2026",
                                                       "yes": 0.31, "bid": None, "ask": 0.31})

    def test_the_month_comes_from_the_clock(self):
        group = hurricane_group([MHCMA])
        got = headline.hurricane(group, dt.datetime(2026, 9, 15, tzinfo=dt.timezone.utc))
        self.assertEqual(got["expiryLabel"], "September 2026")
        self.assertEqual(got["yes"], 0.39)

    def test_a_malformed_spec_is_not_matched(self):
        odd = {"symbol": "MHCMA", "contracts": [contract("august", "Above 0", 0.0, "August 2026", 0.1, 0.2, 0.15)]}
        self.assertIsNone(headline.hurricane(hurricane_group([odd]), NOW))


# ------------------------------------------------------------- whole file
class WholeFile(Store):
    def test_an_empty_store_still_writes_a_valid_file(self):
        got = self.run_job()
        self.assertEqual(got["schema"], 1)
        self.assertEqual(got["asof"], got["written"])       # nothing contributed a clock of its own
        for key in ("accuracy", "largestError", "widestSpread", "hurricane"):
            self.assertNotIn(key, got)

    def test_unreadable_inputs_are_not_an_error(self):
        self.st.put("snapshots/scorecard.json", b"{not json", "application/json")
        self.st.put("snapshots/market/hurricane.json", b"[]", "application/json")
        got = self.run_job()
        self.assertEqual(sorted(got), ["asof", "schema", "written"])

    def test_the_asof_is_the_oldest_input_that_contributed(self):
        self.put("snapshots/scorecard.json", card({"KBOS": station("Boston", [day("2026-08-22", 80.0, nws=81.0)])},
                                                  asof="2026-08-23T06:00:00Z"))
        self.put("snapshots/summary.json", {"schema": 2, "asof": "2026-08-23T11:00:00Z", "cities": [
            city_row("KBOS", "Boston", nws=80.0, nbm=87.0)]})
        self.put("snapshots/market/hurricane.json", hurricane_group([MHCMA]))
        got = self.run_job()
        self.assertEqual(got["asof"], "2026-08-23T06:00:00Z")   # the scorecard, the oldest of the three

    def test_a_stale_input_that_contributed_nothing_does_not_age_the_file(self):
        # the hurricane group is old but lists nothing usable, so it must not
        # drag the as-of back behind the numbers the file actually carries
        self.put("snapshots/summary.json", {"schema": 2, "asof": "2026-08-23T11:00:00Z", "cities": [
            city_row("KBOS", "Boston", nws=80.0, nbm=87.0)]})
        self.put("snapshots/market/hurricane.json", {"schema": 1, "asof": "2026-01-01T00:00:00Z", "markets": []})
        self.assertEqual(self.run_job()["asof"], "2026-08-23T11:00:00Z")

    def test_the_file_stays_small(self):
        # the whole point of the file: the landing page reads this instead of
        # the scorecard, so a full set of sections has to stay near a kilobyte
        days = [day("2026-08-%02d" % d, 80.0, nws=81.0, nbm=82.0, mav=84.0, lamp=83.0) for d in range(16, 23)]
        self.put("snapshots/scorecard.json", card({"K%03d" % i: station("City %d" % i, days) for i in range(30)}))
        self.put("snapshots/summary.json", {"schema": 2, "asof": "2026-08-23T11:00:00Z",
                                            "cities": [city_row("K%03d" % i, "City %d" % i, nws=80.0, nbm=87.0)
                                                       for i in range(30)]})
        self.put("snapshots/market/hurricane.json", hurricane_group([MHCMA, HCAB]))
        self.run_job()
        self.assertLess(len(self.st.get(headline.SNAP_KEY)), 1500)

    def test_no_display_strings_or_formatting_reach_the_file(self):
        self.put("snapshots/scorecard.json", card({"KBOS": station("Boston", [day("2026-08-22", 85.0, nws=91.0)])}))
        self.put("snapshots/market/hurricane.json", hurricane_group([MHCMA]))
        got = self.run_job()
        body = self.st.get(headline.SNAP_KEY).decode()
        for mark in ("°", "$", "%"):
            self.assertNotIn(mark, body)
        self.assertIsInstance(got["largestError"]["error"], float)
        self.assertIsInstance(got["hurricane"]["yes"], float)


if __name__ == "__main__":
    unittest.main()
