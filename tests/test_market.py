"""The market layer's pure functions and the two gated lanes, no network:
symbol mapping, contract grouping, Yes/No complementing, the implied median,
the rolling quote history, the vendor lane's gates and parsers, and the NHC
wind-probability parser."""
import datetime as dt
import gzip
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import exchange as ex, market, reask, storage, gov_weather as gw   # noqa: E402

U = dt.timezone.utc
NOW = dt.datetime(2026, 8, 23, 10, 40, tzinfo=U)
SFO = {"station": "KSFO", "city": "San Francisco", "lat": 37.6196, "lon": -122.3656, "tz": "America/Los_Angeles", "unit": "F"}
SYD = {"station": "YSSY", "city": "Sydney", "lat": -33.9399, "lon": 151.1753, "tz": "Australia/Sydney", "unit": "C"}

MARKET = {"market_name": "San Francisco Daily Temperature High", "symbol": "UHSFO", "contracts": [
    {"conid": 1, "side": "Y", "expiration": "20260824", "strike": 72.0, "strike_label": "Above 72", "time_specifier": "2026.8.23"},
    {"conid": 2, "side": "N", "expiration": "20260824", "strike": 72.0, "strike_label": "Above 72", "time_specifier": "2026.8.23"},
    {"conid": 3, "side": "Y", "expiration": "20260824", "strike": 73.0, "strike_label": "Above 73", "time_specifier": "2026.8.23"},
    {"conid": 4, "side": "N", "expiration": "20260824", "strike": 73.0, "strike_label": "Above 73", "time_specifier": "2026.8.23"},
    {"conid": 5, "side": "Y", "expiration": "20260825", "strike": 70.0, "strike_label": "Above 70", "time_specifier": "2026.8.24"},
    {"conid": 6, "side": "Y", "expiration": "20270101", "strike": 1.0, "strike_label": "odd", "time_specifier": "2026.12"},
]}

TREE = {"categories": {
    "g1": {"name": "Environmental", "parent_id": None, "markets": []},
    "g2": {"name": "Major Weather Events", "parent_id": "g1", "markets": []},
    "g3": {"name": "Global", "parent_id": "g2", "markets": [{"symbol": "HLF", "name": "Hurricane Landfall", "conid": 10}]},
    "g4": {"name": "United States", "parent_id": "g2", "markets": [{"symbol": "HCAT4", "name": "Cat 4", "conid": 11}]},
    "g5": {"name": "Temperatures", "parent_id": "g1", "markets": []},
    "g6": {"name": "San Francisco", "parent_id": "g5", "markets": [{"symbol": "UHSFO", "name": "SF High", "conid": 20}]},
}}


class Symbols(unittest.TestCase):
    def test_us_and_international(self):
        self.assertEqual(ex.symbols_for(SFO), {"high": "UHSFO", "low": "ULSFO"})
        self.assertEqual(ex.symbols_for(SYD), {"high": "SHSSY"})
        self.assertEqual(ex.symbols_for({"station": "CYVR", "unit": "C"}), {"high": "SHYHC"})
        self.assertEqual(ex.symbols_for({"station": "LFPG", "unit": "C"}), {"high": "SHFPO"})

    def test_tree_walks(self):
        by = ex.markets_by_symbol(TREE)
        self.assertEqual(by["UHSFO"]["conid"], 20)
        self.assertEqual(by["HLF"]["category"], "Global")
        hur = ex.category_markets(TREE, "major weather events")
        self.assertEqual(sorted(m["symbol"] for m in hur), ["HCAT4", "HLF"])
        self.assertEqual(ex.category_markets(TREE, "nothing"), [])


class Contracts(unittest.TestCase):
    def test_day_parsing(self):
        self.assertEqual(ex.day_of("2026.8.23"), "2026-08-23")
        self.assertIsNone(ex.day_of("2026.12"))
        self.assertIsNone(ex.day_of("2026.2.30"))

    def test_grouping_keeps_wanted_days_and_both_sides(self):
        g = ex.group_contracts(MARKET, {"2026-08-23"})
        self.assertEqual(list(g.keys()), ["2026-08-23"])
        self.assertEqual(g["2026-08-23"][72.0], {"label": "Above 72", "expiration": "20260824", "Y": 1, "N": 2})
        self.assertEqual(sorted(ex.group_contracts(MARKET).keys()), ["2026-08-23", "2026-08-24"])

    def test_yes_quote_prefers_yes_and_complements_no(self):
        self.assertEqual(ex.yes_quote({"bid": 0.52, "ask": 0.63, "bid_size": 100, "ask_size": 25}, {"bid": 0.9})["from"], "yes")
        q = ex.yes_quote({}, {"bid": 0.37, "bid_size": 25.0, "ask": 0.48, "ask_size": 100.0})
        self.assertEqual((q["bid"], q["ask"], q["bidSize"], q["askSize"], q["from"]), (0.52, 0.63, 100.0, 25.0, "no"))
        self.assertEqual(ex.yes_quote(None, None)["from"], None)
        self.assertIsNone(ex.mid(ex.yes_quote(None, {})))
        self.assertEqual(ex.mid({"bid": 0.52, "ask": 0.63}), 0.575)
        self.assertEqual(ex.mid({"bid": None, "ask": 0.05}), 0.05)

    def test_implied_median(self):
        high = [{"strike": 71, "mid": 0.82}, {"strike": 72, "mid": 0.75}, {"strike": 73, "mid": 0.575}, {"strike": 74, "mid": 0.24}, {"strike": 75, "mid": 0.25}]
        m = ex.implied_median(high, "high")
        self.assertAlmostEqual(m["value"], 73 + (0.575 - 0.5) / (0.575 - 0.24), places=2)
        low = [{"strike": 56, "mid": 0.2}, {"strike": 57, "mid": 0.45}, {"strike": 58, "mid": 0.7}]
        self.assertAlmostEqual(ex.implied_median(low, "low")["value"], 57.2, places=2)
        self.assertEqual(ex.implied_median([{"strike": 80, "mid": 0.9}, {"strike": 81, "mid": 0.8}], "high")["edge"], "above")
        self.assertEqual(ex.implied_median([{"strike": 80, "mid": 0.1}, {"strike": 81, "mid": 0.05}], "high")["edge"], "below")
        self.assertIsNone(ex.implied_median([{"strike": 80, "mid": None}], "high")["value"])


class History(unittest.TestCase):
    def test_carry_trim_and_dedupe(self):
        t = int(NOW.timestamp() // 60)
        old = {"history": {"2026-08-23": {"high": {"72": [[t - 60 * 50, 10, 20], [t - 30, 50, 60]]}},
                           "2026-08-20": {"high": {"72": [[t - 30, 1, 2]]}}}}
        days = {"2026-08-23": {"high": [{"strike": 72.0, "bid": 0.52, "ask": 0.63}, {"strike": 73.0, "bid": None, "ask": 0.1}]}}
        h = market._carry_history(old, days, NOW, {"2026-08-22", "2026-08-23", "2026-08-24"})
        self.assertNotIn("2026-08-20", h)                          # day no longer shown
        s72 = h["2026-08-23"]["high"]["72"]
        self.assertEqual(s72, [[t - 30, 50, 60], [t, 52, 63]])     # 50-hour-old sample trimmed, new appended
        self.assertEqual(h["2026-08-23"]["high"]["73"], [[t, None, 10]])
        again = market._carry_history({"history": h}, days, NOW, {"2026-08-23"})
        self.assertEqual(len(again["2026-08-23"]["high"]["72"]), 2)  # same minute not appended twice


PWS = """
LOCATION       KT

BERMUDA        34   X   X( X)   X( X)   X( X)   1( 1)   6( 7)   5(12)
BERMUDA        50   X   X( X)   X( X)   X( X)   X( X)   2( 2)   2( 4)
BERMUDA        64   X   X( X)   X( X)   X( X)   X( X)   1( 1)   1( 2)
CHARLESTON SC  34   X   X( X)   X( X)   X( X)   X( X)   X( X)   X( X)
MIAMI FL       34   2   5( 7)  10(17)  12(29)   3(32)   X(32)   X(32)
"""

LADDER_CSV = """ID,Region Group,Subregion,Country / Territory,Admin / State,Display Location,Latitude,Longitude,n_land_pixels,covered,grid_center_lat,grid_center_lon,prob_60mph,prob_70mph,prob_80mph
BR,US Gulf/Atlantic,Texas Gulf,United States,TX,Brownsville,25.9017,-97.4975,297,True,25.9,-97.49,40.5,20.25,5
CC,US Gulf/Atlantic,Texas Gulf,United States,TX,Corpus Christi,27.8006,-97.3964,150,True,27.79,-97.39,0,0,0
"""
FINAL_CSV = """ID,Display Location,PeakGust_mph
BR,Brownsville,88.4
CC,Corpus Christi,
"""


class Parsers(unittest.TestCase):
    def test_wind_probabilities(self):
        rows = gw.parse_wind_probabilities("<html><pre>" + PWS + "</pre></html>")
        self.assertEqual([r["location"] for r in rows], ["BERMUDA", "MIAMI FL"])   # 64-kt odds sort first; all-X rows dropped
        self.assertEqual(rows[0]["p34"], 12)
        self.assertEqual(rows[1]["p34"], 32)

    def test_ladder_and_final(self):
        lad = reask.parse_ladder_csv(LADDER_CSV)
        self.assertEqual(lad["thresholds"], [60, 70, 80])
        self.assertEqual(lad["rows"], 2)
        self.assertEqual(list(lad["sites"]), ["BR"])                # the all-zero site is dropped
        self.assertEqual(lad["sites"]["BR"]["p"], [40.5, 20.25, 5.0])
        self.assertTrue(lad["sites"]["BR"]["covered"])
        self.assertEqual(reask.parse_final_csv(FINAL_CSV), {"BR": {"name": "Brownsville", "peakGustMph": 88.4}})


class VendorLane(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = storage.LocalStorage(self.tmp.name)
        self.logs = []

    def log(self, **kw):
        self.logs.append(kw)

    def test_gates(self):
        out = reask.reask_job({"sources": {"reask": False}, "reask": {"api_key": "x"}}, self.st, self.log, NOW)
        self.assertEqual(out, {"enabled": False, "attempted": False})
        snap = json.loads(self.st.get(reask.KEY))
        self.assertFalse(snap["enabled"])
        self.assertEqual(snap["reason"], "lane off in config")
        out = reask.reask_job({"sources": {"reask": True}, "reask": {"api_key": ""}}, self.st, self.log, NOW)
        self.assertFalse(out["attempted"])
        self.assertEqual(json.loads(self.st.get(reask.KEY))["reason"], "no credential configured")

    def test_live_storm_flow(self):
        calls = []

        def fetch(path, params=None):
            calls.append((path, params))
            if path == "/livecyc/tcwind/list":
                return json.dumps({"storms": [
                    {"storm_name": "Milton", "storm_year": 2026, "last_modified": "2026-08-23T09:00:00Z",
                     "forecasts": [{"forecast_datetime": "2026-08-23T00:00:00Z", "last_modified": "2026-08-23T03:00:00Z"},
                                   {"forecast_datetime": "2026-08-23T06:00:00Z", "last_modified": "2026-08-23T09:00:00Z"}]},
                    {"storm_name": "Old", "storm_year": 2024, "forecasts": [{"forecast_datetime": "2024-10-07T00:00:00Z"}]}]}).encode()
            if path == "/livecyc/tcwind/probabilities":
                return LADDER_CSV.encode()
            if path == "/metryc/interim/tcwind/list":
                return json.dumps({"storms": [{"storm_name": "Milton", "storm_year": 2026, "last_modified": "2026-08-23T08:00:00Z"}]}).encode()
            if path == "/metryc/interim/tcwind/probabilities":
                return LADDER_CSV.encode()
            if path == "/metryc/historical/tcwind/list":
                return json.dumps({"storms": []}).encode()
            raise AssertionError(path)

        cfg = {"sources": {"reask": True}, "reask": {"api_key": "k", "base_url": "https://vendor.invalid/v1"}}
        out = reask.reask_job(cfg, self.st, self.log, NOW, fetch=fetch)
        self.assertTrue(out["ok"])
        self.assertEqual(out["storms"], 1)
        self.assertEqual(out["fetched"], 2)
        snap = json.loads(self.st.get(reask.KEY))
        s = snap["storms"][0]
        self.assertEqual(s["livecyc"]["forecastTime"], "2026-08-23T06:00:00Z")      # the latest cycle
        self.assertEqual(s["livecyc"]["sites"]["BR"]["p"][0], 40.5)
        self.assertEqual(s["interim"]["lastModified"], "2026-08-23T08:00:00Z")
        self.assertEqual(snap["asof"], "2026-08-23T06:00:00Z")
        self.assertEqual(snap["attribution"], "Powered by Reask")
        self.assertTrue(self.st.exists("archive/reask/Milton_2026/livecyc_2026082306.csv.gz"))
        self.assertEqual(gzip.decompress(self.st.get("archive/reask/Milton_2026/livecyc_2026082306.csv.gz")).decode(), LADDER_CSV)
        # the 2024 backtest storm never appears, and the probability endpoints got the documented params
        self.assertNotIn("Old", json.dumps(snap))
        self.assertIn(("/livecyc/tcwind/probabilities", {"storm_name": "Milton", "storm_year": 2026, "forecast_start_time": "2026-08-23T06:00:00Z", "format": "csv"}), calls)
        # a second poll with nothing new fetches no files
        n = len(calls)
        out2 = reask.reask_job(cfg, self.st, self.log, NOW + dt.timedelta(minutes=10), fetch=fetch)
        self.assertEqual(out2["fetched"], 0)
        self.assertEqual([c[0] for c in calls[n:]], ["/livecyc/tcwind/list", "/metryc/interim/tcwind/list", "/metryc/historical/tcwind/list"])

    def test_list_failure_keeps_previous(self):
        def fetch(path, params=None):
            raise OSError("down")
        cfg = {"sources": {"reask": True}, "reask": {"api_key": "k", "base_url": "https://vendor.invalid/v1"}}
        self.st.put(reask.KEY, json.dumps({"asof": "2026-08-23T00:00:00Z", "storms": [{"name": "Milton", "year": 2026, "livecyc": {"forecastTime": "2026-08-23T00:00:00Z"}}],
                                           "polled": "2026-08-23T00:10:00Z"}).encode())
        out = reask.reask_job(cfg, self.st, self.log, NOW, fetch=fetch)
        self.assertFalse(out["ok"])
        snap = json.loads(self.st.get(reask.KEY))
        self.assertEqual(snap["asof"], "2026-08-23T00:00:00Z")
        self.assertEqual(snap["polled"], "2026-08-23T00:10:00Z")
        self.assertEqual(len(snap["storms"]), 1)


class QuoteJob(unittest.TestCase):
    def test_quote_rows_with_failures_and_deadline(self):
        from pipeline import archive as arch
        grouped = ex.group_contracts(MARKET, {"2026-08-23"})

        def fetch(conid):
            if conid == 1:
                return {"bid": 0.7, "ask": 0.8}
            if conid == 3:
                return {}
            if conid == 4:
                return {"bid": 0.3, "ask": 0.5}
            raise OSError("no")
        rows = market._quote_rows(grouped, fetch, arch.Deadline(None))["2026-08-23"]
        self.assertEqual([r["strike"] for r in rows], [72.0, 73.0])
        self.assertEqual((rows[0]["bid"], rows[0]["ask"], rows[0]["from"], rows[0]["mid"]), (0.7, 0.8, "yes", 0.75))
        self.assertEqual((rows[1]["bid"], rows[1]["ask"], rows[1]["from"]), (0.5, 0.7, "no"))   # Yes empty, No complemented
        over = arch.Deadline(1e-6)
        rows = market._quote_rows(grouped, fetch, over)["2026-08-23"]
        self.assertTrue(all(r.get("error") == "deadline" for r in rows))


if __name__ == "__main__":
    unittest.main()


class Gates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = storage.LocalStorage(self.tmp.name)
        self.logs = []

    def log(self, **kw):
        self.logs.append(kw)

    def test_exchange_switch_off_skips(self):
        out = market.quotes_job({"sources": {"exchange": False}}, self.st, self.log, NOW)
        self.assertTrue(out["skipped"])
        self.assertIsNone(self.st.get("snapshots/market/summary.json"))

    def test_vendor_lane_needs_base_url_and_https(self):
        out = reask.reask_job({"sources": {"reask": True}, "reask": {"api_key": "k", "base_url": ""}}, self.st, self.log, NOW)
        self.assertFalse(out["attempted"])
        self.assertEqual(json.loads(self.st.get(reask.KEY))["reason"], "no base URL configured")
        with self.assertRaises(ValueError):
            reask._get("http://example.invalid", "/livecyc/tcwind/list", "k")

    def test_lane_health_files_are_separate(self):
        from pipeline import archive as arch
        arch.update_health(self.st, {"nws": {"ok": False, "error": "x"}}, NOW)
        arch.update_health(self.st, {"exchange": {"ok": False, "error": "y"}}, NOW, key=arch.MARKET_HEALTH_KEY)
        weather = json.loads(self.st.get(arch.HEALTH_KEY))
        mkt = json.loads(self.st.get(arch.MARKET_HEALTH_KEY))
        self.assertIn("nws", weather); self.assertNotIn("exchange", weather)
        self.assertIn("exchange", mkt); self.assertNotIn("nws", mkt)
        for _ in range(arch.FAIL_STREAK_ALARM - 1):
            arch.update_health(self.st, {"exchange": {"ok": False, "error": "y"}}, NOW, key=arch.MARKET_HEALTH_KEY)
        self.assertEqual(arch.alarms_in(json.loads(self.st.get(arch.MARKET_HEALTH_KEY))), ["exchange"])
        self.assertEqual(arch.alarms_in(json.loads(self.st.get(arch.HEALTH_KEY))), [])

    def test_partial_ladder_keeps_previous_snapshot(self):
        from pipeline import archive as arch
        prev = {"asof": "2026-08-23T10:00:00Z", "days": {"2026-08-23": {"high": [{"strike": 72.0, "mid": 0.5}]}}, "history": {}}
        self.st.put("snapshots/market/KSFO.json", json.dumps(prev).encode())
        calls = {"n": 0}

        def fake_tree():
            return {"categories": {"g": {"name": "San Francisco", "parent_id": None, "markets": [{"symbol": "UHSFO", "conid": 1}]}}}

        def fake_market(conid):
            return MARKET

        import time as _time
        dl = arch.Deadline(None)

        def fake_quote(conid):
            calls["n"] += 1
            dl.end = _time.time() - 1                   # the budget runs out after the first quote
            return {"bid": 0.4, "ask": 0.6}
        orig = (ex.fetch_tree, ex.fetch_market, ex.fetch_quote, market.QUOTE_WORKERS)
        ex.fetch_tree, ex.fetch_market, ex.fetch_quote, market.QUOTE_WORKERS = fake_tree, fake_market, fake_quote, 1
        try:
            out = market.quotes_job({"sources": {"exchange": True}, "exchange": {"quote_workers": 1}}, self.st, self.log,
                                    dt.datetime(2026, 8, 23, 18, 0, tzinfo=U), deadline=dl)
        finally:
            ex.fetch_tree, ex.fetch_market, ex.fetch_quote, market.QUOTE_WORKERS = orig
        self.assertEqual(calls["n"], 1)
        self.assertEqual(out["quoted"], 1)
        snap = json.loads(self.st.get("snapshots/market/KSFO.json"))
        self.assertEqual(snap["asof"], "2026-08-23T10:00:00Z")       # the previous snapshot stands
        summary = json.loads(self.st.get("snapshots/market/summary.json"))
        self.assertIn("KSFO", summary["partialKept"])
        row = next(r for r in summary["cities"] if r["station"] == "KSFO")
        self.assertTrue(row["partial"]); self.assertEqual(row["day"], "2026-08-23")
        # a deadline-cut group keeps its previous snapshot; with no previous snapshot a cut station writes nothing
        self.st.put("snapshots/market/hurricane.json", json.dumps({"asof": "2026-08-23T10:00:00Z", "markets": [{"symbol": "HLF"}]}).encode())

        def fake_tree2():
            return {"categories": {"g": {"name": "Major Weather Events", "parent_id": None, "markets": [{"symbol": "HLF", "conid": 2}]},
                                   "h": {"name": "Sydney", "parent_id": None, "markets": [{"symbol": "SHSSY", "conid": 3}]}}}
        dl2 = arch.Deadline(None)

        def fake_quote2(conid):
            dl2.end = _time.time() - 1
            return {"bid": 0.1, "ask": 0.2}
        syd = {"market_name": "Sydney Daily Temperature High", "symbol": "SHSSY", "contracts": [
            {"conid": 31, "side": "Y", "expiration": "20260824", "strike": 20.0, "strike_label": "Above 20", "time_specifier": "2026.8.24"},
            {"conid": 32, "side": "Y", "expiration": "20260824", "strike": 21.0, "strike_label": "Above 21", "time_specifier": "2026.8.24"}]}

        def fake_market2(conid):
            return syd if conid == 3 else MARKET
        ex.fetch_tree, ex.fetch_market, ex.fetch_quote, market.QUOTE_WORKERS = fake_tree2, fake_market2, fake_quote2, 1
        try:
            market.quotes_job({"sources": {"exchange": True}, "exchange": {"quote_workers": 1}}, self.st, self.log,
                              dt.datetime(2026, 8, 23, 18, 0, tzinfo=U), deadline=dl2)
        finally:
            ex.fetch_tree, ex.fetch_market, ex.fetch_quote, market.QUOTE_WORKERS = orig
        hur = json.loads(self.st.get("snapshots/market/hurricane.json"))
        self.assertEqual(hur["asof"], "2026-08-23T10:00:00Z")
        # the first run wrote YSSY as unlisted (its symbol was not in that tree); the cut second run left it untouched
        ys = json.loads(self.st.get("snapshots/market/YSSY.json"))
        self.assertEqual((ys["days"], ys["symbols"]), ({}, {}))
        row = next(r for r in json.loads(self.st.get("snapshots/market/summary.json"))["cities"] if r["station"] == "YSSY")
        self.assertTrue(row["partial"]); self.assertEqual(row["asof"], "2026-08-23T18:00:00Z")   # the unlisted snapshot it kept


class StormWindContracts(unittest.TestCase):
    """The two products that appear only while a storm is live: a gust ladder per
    reference location, and a pool contract whose strikes are place names."""

    TREE = {"categories": {
        "g1": {"name": "Environmental", "parent_id": None, "markets": []},
        "g2": {"name": "Major Weather Events", "parent_id": "g1", "markets": [{"symbol": "HLF", "name": "Hurricane Landfall", "conid": 10}]},
        # the exchange lists a live storm's markets somewhere this job cannot predict
        "g9": {"name": "Live Storm", "parent_id": None, "markets": [
            {"symbol": "LERBR", "name": "Erin — Brownsville peak gust", "conid": 21},
            {"symbol": "LERTA", "name": "Erin — Tampa peak gust", "conid": 22},
            {"symbol": "LHLERG", "name": "Erin — highest wind, Gulf Coast", "conid": 23},
            {"symbol": "LFIBR", "name": "Fiona — Brownsville peak gust", "conid": 24},
            {"symbol": "LOFUS", "name": "US Total Layoffs", "conid": 25},
        ]},
    }}

    def test_matches_by_storm_code_not_by_pattern(self):
        got = ex.storm_wind_markets(self.TREE, ["Erin"])
        self.assertEqual(sorted(m["symbol"] for m in got), ["LERBR", "LERTA", "LHLERG"])
        # a symbol that merely starts with L is not swept in, and another storm is not either
        self.assertNotIn("LOFUS", [m["symbol"] for m in got])
        self.assertNotIn("LFIBR", [m["symbol"] for m in got])

    def test_several_storms_at_once(self):
        got = {m["symbol"]: m for m in ex.storm_wind_markets(self.TREE, ["Erin", "Fiona"])}
        self.assertEqual(sorted(got), ["LERBR", "LERTA", "LFIBR", "LHLERG"])
        self.assertEqual(got["LERBR"]["product"], "L")
        self.assertEqual(got["LERBR"]["location"], "BR")
        self.assertEqual(got["LHLERG"]["product"], "LHL")
        self.assertEqual(ex.storm_wind_markets(self.TREE, []), [])

    def test_storm_code_is_the_first_two_letters(self):
        self.assertEqual(ex.storm_code("Erin"), "ER")
        self.assertEqual(ex.storm_code("van der Meer"), "VA")
        self.assertEqual(ex.storm_code(""), "")

    def test_a_pool_contract_keeps_its_named_strikes(self):
        from pipeline import archive as arch
        pool = {"market_name": "Erin — highest wind, Gulf Coast", "symbol": "LHLERG", "contracts": [
            {"conid": 1, "side": "Y", "strike": "Tampa", "strike_label": "Tampa", "time_specifier": "2026.9", "expiry_label": "September 2026"},
            {"conid": 2, "side": "N", "strike": "Tampa", "strike_label": "Tampa", "time_specifier": "2026.9"},
            {"conid": 3, "side": "Y", "strike": None, "strike_label": "Brownsville", "time_specifier": "2026.9"},
        ]}
        tmp = tempfile.TemporaryDirectory()
        st = storage.LocalStorage(tmp.name)

        def fake_tree():
            return {"categories": {"g": {"name": "Live Storm", "parent_id": None, "markets": [{"symbol": "LHLERG", "conid": 23}]}}}
        st.put("snapshots/hurricane.json", json.dumps({"storms": [{"name": "Erin"}]}).encode())
        orig = (ex.fetch_tree, ex.fetch_market, ex.fetch_quote)
        ex.fetch_tree, ex.fetch_market, ex.fetch_quote = fake_tree, (lambda c: pool), (lambda c: {"bid": 0.2, "ask": 0.3})
        try:
            market.quotes_job({"sources": {"exchange": True}, "exchange": {"quote_workers": 1}}, st, lambda **k: None,
                              dt.datetime(2026, 9, 3, 12, 0, tzinfo=U), deadline=arch.Deadline(None))
        finally:
            ex.fetch_tree, ex.fetch_market, ex.fetch_quote = orig
        grp = json.loads(st.get("snapshots/market/hurricane.json"))
        m = next(x for x in grp["markets"] if x["symbol"] == "LHLERG")
        strikes = sorted(c["strike"] for c in m["contracts"])
        self.assertEqual(strikes, ["Brownsville", "Tampa"])          # named strikes survive
        self.assertTrue(all(c["numeric"] is False for c in m["contracts"]))
        tampa = next(c for c in m["contracts"] if c["strike"] == "Tampa")
        self.assertEqual((tampa["bid"], tampa["ask"]), (0.2, 0.3))   # and the Yes side is quoted
        tmp.cleanup()


class StormLedger(unittest.TestCase):
    """The per-storm delivery ledger: append-only, forward-only, capped, and
    written per storm so several can run at once."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = storage.LocalStorage(self.tmp.name)
        self.logs = []
        self.st.put("snapshots/hurricane.json", json.dumps({"storms": [{"name": "Erin", "intensityKt": 90}]}).encode())

    def log(self, **kw):
        self.logs.append(kw)

    def ladder(self, ps):
        return {"thresholds": [70, 80, 90], "rows": len(ps),
                "sites": {k: {"name": k.lower(), "lat": 1.0, "lon": 2.0, "p": v} for k, v in ps.items()}}

    def test_appends_and_keeps_delivery_order(self):
        for i, ps in enumerate([{"BR": [10, 2, 0]}, {"BR": [30, 9, 1]}, {"BR": [55, 20, 4], "TA": [8, 1, 0]}]):
            step = reask._step(self.ladder(ps), "livecyc", "202609010%d" % i, "t", "2026-09-01T00:00:00Z")
            reask.append_step(self.st, "Erin", 2026, step, self.log)
        doc = json.loads(self.st.get("snapshots/storm/Erin_2026.json"))
        self.assertEqual([x["id"] for x in doc["steps"]], ["2026090100", "2026090101", "2026090102"])
        self.assertEqual(doc["thresholds"], [70, 80, 90])
        self.assertEqual(doc["steps"][2]["sites"]["BR"], [55, 20, 4])
        # a site that appears late keeps the delivery it first appeared at
        self.assertEqual(doc["sites"]["TA"]["firstStep"], "2026090102")
        self.assertEqual(doc["sites"]["BR"]["firstStep"], "2026090100")

    def test_a_reissued_delivery_replaces_rather_than_duplicates(self):
        for ps in ([{"BR": [10, 2, 0]}], [{"BR": [44, 9, 1]}]):
            reask.append_step(self.st, "Erin", 2026, reask._step(self.ladder(ps[0]), "livecyc", "2026090100", "t", "ts"), self.log)
        doc = json.loads(self.st.get("snapshots/storm/Erin_2026.json"))
        self.assertEqual(len(doc["steps"]), 1)
        self.assertEqual(doc["steps"][0]["sites"]["BR"], [44, 9, 1])

    def test_interim_and_final_sort_last_and_final_carries_the_gusts(self):
        reask.append_step(self.st, "Erin", 2026, reask._step(self.ladder({"BR": [10, 2, 0]}), "livecyc", "2026090100", "t", "ts"), self.log)
        reask.append_step(self.st, "Erin", 2026, reask._step(self.ladder({"BR": [70, 30, 5]}), "interim", "INT", "t", "ts"), self.log)
        reask.append_step(self.st, "Erin", 2026, {"id": "FINAL", "kind": "final", "at": "t", "ts": "ts", "sites": {},
                                                 "siteMeta": {}, "thresholds": None, "final": {"BR": 88.4}}, self.log)
        doc = json.loads(self.st.get("snapshots/storm/Erin_2026.json"))
        self.assertEqual([x["kind"] for x in doc["steps"]], ["livecyc", "interim", "final"])
        self.assertEqual(doc["final"], {"BR": 88.4})
        self.assertEqual(doc["thresholds"], [70, 80, 90])          # the final carries none and must not clear them

    def test_cycles_are_capped_but_interim_and_final_are_kept(self):
        reask.append_step(self.st, "Erin", 2026, reask._step(self.ladder({"BR": [1, 0, 0]}), "interim", "INT", "t", "ts"), self.log)
        for i in range(reask.MAX_STEPS + 6):
            reask.append_step(self.st, "Erin", 2026, reask._step(self.ladder({"BR": [i, 0, 0]}), "livecyc", "%010d" % i, "t", "ts"), self.log)
        doc = json.loads(self.st.get("snapshots/storm/Erin_2026.json"))
        cyc = [x for x in doc["steps"] if x["kind"] == "livecyc"]
        self.assertEqual(len(cyc), reask.MAX_STEPS)
        self.assertEqual(cyc[-1]["id"], "%010d" % (reask.MAX_STEPS + 5))   # the newest cycles survive
        self.assertEqual(len([x for x in doc["steps"] if x["kind"] == "interim"]), 1)

    def test_two_storms_keep_separate_ledgers(self):
        reask.append_step(self.st, "Erin", 2026, reask._step(self.ladder({"BR": [10, 0, 0]}), "livecyc", "A", "t", "ts"), self.log)
        reask.append_step(self.st, "Fiona", 2026, reask._step(self.ladder({"TA": [20, 0, 0]}), "livecyc", "A", "t", "ts"), self.log)
        e = json.loads(self.st.get("snapshots/storm/Erin_2026.json"))
        f = json.loads(self.st.get("snapshots/storm/Fiona_2026.json"))
        self.assertEqual(list(e["sites"]), ["BR"])
        self.assertEqual(list(f["sites"]), ["TA"])
        self.assertEqual(f["steps"][0]["kind"], "livecyc")
