"""The daily pass ran out of its 512 MB every morning from 27 September 2026.

Two jobs held whole what they only needed to read through. The world crop
yields decoded the USDA bulk csv, 52 MB unpacked, into one string and copied it
into a StringIO, about 260 MB of live objects. The scorecard merged every
station's observations over the whole record once per station, 163 MB by
October, and kept every quote pass it parsed until the pass ended, 206 MB, and
both grew with each day the record kept. Its search for each day's cycles also
walked the station's whole archive, so its time grew with the square of the
record's length. Each test pins the shape that replaced one of these against
the answer the old one gave. No network.
"""
import datetime as dt
import gzip
import io
import json
import os
import random
import sys
import tempfile
import tracemalloc
import unittest
import weakref
import zipfile
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import exchange as ex, scorecard, series, snapshots as sn, storage   # noqa: E402

U = dt.timezone.utc
NY = ZoneInfo("America/New_York")
HNL = ZoneInfo("Pacific/Honolulu")
LA = ZoneInfo("America/Los_Angeles")


class CropYieldsReadThrough(unittest.TestCase):
    HEADER = "Commodity_Description,Country_Name,Market_Year,Attribute_Description,Value\r\n"

    @staticmethod
    def _zip(text: str) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("psd_grains_pulses.csv", text)
        return buf.getvalue()

    def test_the_csv_is_never_held_whole(self):
        # most of the real file is commodities and attributes the yields never read
        decoy = "".join(f"Barley,C{i},{1960 + i % 60},Ending Stocks,{i}.0\r\n" for i in range(60000))
        text = self.HEADER + "Corn,A,2024,Area Harvested,100\r\nCorn,A,2024,Production,600\r\n" + decoy
        raw = self._zip(text)
        tracemalloc.start()
        try:
            out = series.crop_yields(fetch=lambda url: raw)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(out["crop-corn"]["points"], [["2025", 6.0]])
        # the whole-file read peaked near five times the csv's size
        self.assertLess(peak, len(text) / 2)

    def test_a_byte_order_mark_is_not_part_of_the_first_column(self):
        text = "﻿" + self.HEADER + "Corn,A,2024,Area Harvested,100\r\nCorn,A,2024,Production,600\r\n"
        out = series.crop_yields(fetch=lambda url: self._zip(text))
        self.assertEqual(out["crop-corn"]["points"], [["2025", 6.0]])


class CycleWindow(unittest.TestCase):
    """Each scored day searches its own stretch of the archive and finds the
    cycle the whole archive would have given it."""

    def test_the_window_holds_every_cycle_within_the_lead(self):
        for kind, fmt in (("lamp", "%Y%m%dT%H30Z"), ("daily", "%Y%m%dT%H%M%SZ"), ("nbs", "%Y%m%dT%H00Z")):
            t = dt.datetime(2026, 8, 10, 0, 7, 41, tzinfo=U)
            keys = []
            while t < dt.datetime(2026, 8, 16, tzinfo=U):
                keys.append(f"archive/KLAX/{kind}_{t.strftime(fmt)}.txt.gz")
                t += dt.timedelta(minutes=97)
            stamps = scorecard.cycle_stamps(keys)
            self.assertIsNotNone(stamps)
            # cutoffs on the stamps themselves, and on the stamps a lead of
            # exactly twenty-four hours away, are the edges that matter
            cuts = [sn._stamp_time(sn._stamp_of(k)) + dt.timedelta(hours=h) for k in keys[40:60] for h in (0, 24)]
            cuts += [dt.datetime(2026, 8, 12, tzinfo=U) + dt.timedelta(minutes=53 * i) for i in range(60)]
            for cut_t in cuts:
                cut = cut_t.strftime("%Y%m%dT%H%M%SZ")
                got = scorecard.cycle_window(keys, stamps, cut)
                want = [k for k in keys if sn._norm_stamp(sn._stamp_of(k)) <= cut
                        and (cut_t - sn._stamp_time(sn._stamp_of(k))).total_seconds() <= scorecard.MAX_LEAD_H * 3600]
                self.assertTrue(set(want) <= set(got), (kind, cut))
                self.assertTrue(all(sn._norm_stamp(sn._stamp_of(k)) <= cut for k in got), (kind, cut))
                self.assertEqual(got, [k for k in keys if k in set(got)], (kind, cut))
                self.assertLess(len(got), 20)

    def test_stamps_out_of_order_hand_over_the_whole_list(self):
        keys = ["archive/KLAX/nbs_20260812T1200Z.txt.gz", "archive/KLAX/nbs_20260811T1200Z.txt.gz"]
        self.assertIsNone(scorecard.cycle_stamps(keys))
        self.assertEqual(scorecard.cycle_window(keys, None, "20260812T180000Z"), keys)

    def test_pick_levels_chooses_the_same_cycle_from_the_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = storage.LocalStorage(tmp)
            keys = []
            t = dt.datetime(2026, 8, 10, 0, tzinfo=U)
            while t < dt.datetime(2026, 8, 22, tzinfo=U):
                # every record carries three days, each value naming its cycle;
                # the 18Z records carry no high, so the high and the low are
                # often found in different cycles
                days = {(t.date() + dt.timedelta(days=k)).isoformat():
                        {"high": None if t.hour == 18 else float(t.day * 100 + t.hour), "low": float(t.hour)}
                        for k in range(3)}
                key = f"archive/KLAX/mavx_{t.strftime('%Y%m%dT%H00Z')}.json.gz"
                st.put(key, gzip.compress(json.dumps({"source": "IEM", "days": days}).encode()), "application/gzip")
                keys.append(key)
                t += dt.timedelta(hours=6)
            stamps = scorecard.cycle_stamps(keys)
            c = {"station": "KLAX", "unit": "F"}
            day = dt.date(2026, 8, 11)
            while day < dt.date(2026, 8, 23):
                for hour in (0, 5, 6, 11, 17, 18, 23):
                    cut = (dt.datetime.combine(day, dt.time(hour), tzinfo=U) - dt.timedelta(hours=7)).strftime("%Y%m%dT%H%M%SZ")
                    whole = sn.pick_levels(st, "mavx", keys, cut, c, LA, day.isoformat(), max_lead_h=scorecard.MAX_LEAD_H)
                    window = sn.pick_levels(st, "mavx", scorecard.cycle_window(keys, stamps, cut), cut, c, LA,
                                            day.isoformat(), max_lead_h=scorecard.MAX_LEAD_H)
                    self.assertEqual(whole, window, (day, hour))
                day += dt.timedelta(days=1)


def _obs_record(st, stations, first_utc: dt.date, n_days: int, sparse=None):
    """Hourly METARs plus one SPECI a day for each station, filed by the UTC day
    of their own time as the archive files them. `sparse` is (station, start,
    end) with all but every eighth report dropped between the two instants."""
    rng = random.Random(11)
    for i in range(n_days):
        day = first_utc + dt.timedelta(days=i)
        rows = {}
        for sid in stations:
            for h in range(24):
                t = dt.datetime.combine(day, dt.time(h, 51), tzinfo=U)
                if sparse and sid == sparse[0] and sparse[1] <= t < sparse[2] and h % 8:
                    continue
                ts = int(t.timestamp())
                rows[f"{sid}|{ts}"] = {"icaoId": sid, "obsTime": ts, "temp": round(rng.uniform(15, 33), 1),
                                       "metarType": "METAR", "temp_source": "tgroup", "rawOb": f"METAR {sid}"}
            ts = int(dt.datetime.combine(day, dt.time(15, 12), tzinfo=U).timestamp())
            rows[f"{sid}|{ts}"] = {"icaoId": sid, "obsTime": ts, "temp": 34.0, "metarType": "SPECI",
                                   "temp_source": "body", "rawOb": f"SPECI {sid}"}
        body = {"utc_day": day.strftime("%Y%m%d"), "rows": rows}
        st.put(f"archive/obs/{day.strftime('%Y%m%d')}.json.gz", gzip.compress(json.dumps(body).encode()),
               "application/gzip")


def _old_observed_days(st, sid, tz, unit, first, last) -> dict:
    """The per-station reading the scorecard used until October 2026: the whole
    record merged, then this station's rows."""
    days, d = [], first - dt.timedelta(days=1)
    while d <= last + dt.timedelta(days=1):
        days.append(d.strftime("%Y%m%d"))
        d += dt.timedelta(days=1)
    record = sn.load_obs_record(st, days)
    rows = sn.decode_rows([ob for k, ob in record.items() if k.startswith(sid + "|")], tz)
    out, d = {}, first
    while d <= last:
        e = sn.day_extremes(rows, tz, d.isoformat(), unit)
        if e and e["n"] >= 12:
            out[d.isoformat()] = {"high": e["high"]["v"], "low": e["low"]["v"], "n": e["n"]}
        d += dt.timedelta(days=1)
    return out


class ObservedByStation(unittest.TestCase):
    ROSTER = [{"station": "KLGA", "tz": "America/New_York", "unit": "F"},
              {"station": "PHNL", "tz": "Pacific/Honolulu", "unit": "F"}]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = storage.LocalStorage(self.tmp.name)
        # KLGA loses most of its reports through its local 22nd, so that day
        # falls under the twelve-report floor; EGLL is not on the roster
        sparse = ("KLGA", dt.datetime(2026, 8, 22, 4, tzinfo=U), dt.datetime(2026, 8, 23, 4, tzinfo=U))
        _obs_record(self.st, ["KLGA", "PHNL", "EGLL"], dt.date(2026, 8, 18), 9, sparse)
        self.first = dt.date(2026, 8, 18)
        self.lasts = {"KLGA": dt.date(2026, 8, 25), "PHNL": dt.date(2026, 8, 24)}

    def tearDown(self):
        self.tmp.cleanup()

    def test_matches_merging_the_whole_record_per_station(self):
        got = scorecard.observed_by_station(self.st, self.ROSTER, self.first, self.lasts)
        self.assertEqual(set(got), {"KLGA", "PHNL"})
        self.assertEqual(got["KLGA"], _old_observed_days(self.st, "KLGA", NY, "F", self.first, self.lasts["KLGA"]))
        self.assertEqual(got["PHNL"], _old_observed_days(self.st, "PHNL", HNL, "F", self.first, self.lasts["PHNL"]))
        self.assertNotIn("2026-08-22", got["KLGA"])
        self.assertIn("2026-08-21", got["KLGA"])
        self.assertNotIn("2026-08-25", got["PHNL"])

    def test_each_file_is_read_once_and_few_are_held(self):
        reads = []
        real_get = self.st.get
        self.st.get = lambda k: (reads.append(k), real_get(k))[1]

        class Held(dict):
            """A decoded file that can be watched with a weak reference."""

        live, most = [0], [0]
        real = scorecard._decoded_obs_file

        def watched(store, day, zones):
            d = Held(real(store, day, zones))
            live[0] += 1
            most[0] = max(most[0], live[0])
            weakref.finalize(d, lambda: live.__setitem__(0, live[0] - 1))
            return d

        scorecard._decoded_obs_file = watched
        try:
            scorecard.observed_by_station(self.st, self.ROSTER, self.first, self.lasts)
        finally:
            scorecard._decoded_obs_file = real
        obs_reads = [k for k in reads if k.startswith("archive/obs/")]
        self.assertEqual(len(obs_reads), len(set(obs_reads)))
        self.assertLessEqual(most[0], 3)


def _old_levels(body: dict, sid: str, diso: str) -> dict:
    """The exchange's values read straight from a whole pass, ladder by ladder,
    by the accuracy paper's method, which the cached medians must equal."""
    out = {}
    for side in ("high", "low"):
        rows = [{"strike": r.get("strike"), "bid": r.get("bid"), "ask": r.get("ask")} for r in body.get("rows") or []
                if r.get("station") == sid and r.get("day") == diso and r.get("side") == side
                and r.get("strike") is not None]
        m = ex.central_value(rows, side) if rows else None
        if m and m.get("value") is not None:
            out[side] = m["value"]
    return out


class QuotePassCache(unittest.TestCase):
    def test_the_medians_match_reading_the_rows(self):
        rng = random.Random(5)
        rows = []
        for sid in ("KLGA", "KBOS", "KMIA"):
            for diso in ("2026-08-21", "2026-08-22", "2026-08-23"):
                for side in ("high", "low", "wind"):
                    for k in range(70, 92, 2):
                        rows.append({"station": sid, "day": diso, "side": side, "strike": rng.choice([float(k)] * 5 + [None]),
                                     "bid": rng.choice([None, round(rng.random(), 2)]),
                                     "ask": rng.choice([None, round(rng.random(), 2)])})
        body = {"asof": "2026-08-21T21:50:00Z", "rows": rows}
        p = scorecard.pass_medians(body)
        self.assertEqual(p["asof"], body["asof"])
        for sid in ("KLGA", "KBOS", "KMIA", "KSEA"):
            for diso in ("2026-08-21", "2026-08-22", "2026-08-23"):
                new = {side: p["medians"][(sid, diso, side)] for side in ("high", "low") if (sid, diso, side) in p["medians"]}
                self.assertEqual(new, _old_levels(body, sid, diso), (sid, diso))

    def test_a_pass_is_kept_as_its_medians(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = storage.LocalStorage(tmp)
            rows = [{"station": "KLGA", "day": "2026-08-22", "side": "high", "strike": 80.0, "bid": 0.69, "ask": 0.71},
                    {"station": "KLGA", "day": "2026-08-22", "side": "high", "strike": 84.0, "bid": 0.29, "ask": 0.31}]
            rows += [{"station": f"K{i:03d}", "day": "2026-08-22", "side": "high", "strike": float(k),
                      "bid": 0.9 - k / 100, "ask": 0.92 - k / 100, "conid": 700000 + i * 100 + k}
                     for i in range(200) for k in range(40, 90, 2)]
            key = "archive/market/20260821/215000.json.gz"
            st.put(key, gzip.compress(json.dumps({"asof": "2026-08-21T21:50:00Z", "rows": rows}).encode()),
                   "application/gzip")
            cache = {"list": {}, "body": {}}
            city = {"station": "KLGA", "city": "New York City", "tz": "America/New_York", "unit": "F"}
            got = scorecard.market_levels(st, city, NY, dt.date(2026, 8, 22), cache)
            self.assertAlmostEqual(got["high"], 82.0, places=2)
            held = cache["body"][key]
            self.assertNotIn("rows", held)
            self.assertEqual(len(held["medians"]), 201)


if __name__ == "__main__":
    unittest.main()
