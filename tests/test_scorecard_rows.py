"""The scorecard keeps each station-day's row and scores again only what can have changed.

Scoring the whole record every morning reached 798 s of the daily invocation's
900 s by 13 November 2026 and ran the first try into Lambda's limit every
morning. Each test pins one rule of the kept rows against the answer a full
pass of the same record gives: the snapshot a pass writes from kept rows is the
one a pass that scores everything again writes. The forecasts, the market and
the observations are stand-ins, so what is tested is which days a pass scores
and what it writes. No network.
"""
import datetime as _dt
import gzip
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import scorecard, snapshots as sn, storage   # noqa: E402

U = _dt.timezone.utc
ROSTER = [{"station": "KAAA", "city": "Alpha", "tz": "America/New_York", "unit": "F"},
          {"station": "KBBB", "city": "Beta", "tz": "Pacific/Honolulu", "unit": "F"},
          {"station": "CXXX", "city": "Gamma", "tz": "America/Toronto", "unit": "C"}]


def days(first, last):
    d, out = first, []
    while d <= last:
        out.append(d.isoformat())
        d += _dt.timedelta(days=1)
    return out


class Rows(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = storage.LocalStorage(self.tmp.name)
        self.st.put("archive/obs/20261020.json.gz", gzip.compress(b"{}"))
        self.obs = {sid: {d: {"high": 60 + i % 9, "low": 40 + i % 7, "n": 24}
                          for i, d in enumerate(days(_dt.date(2026, 10, 20), _dt.date(2026, 11, 12)))}
                    for sid in ("KAAA", "KBBB")}
        self.shift = {}                      # (station, day) -> degrees a stand-in forecast moves
        self.calls, self.listed = [], []
        self.limit = None                    # station-days a pass may score before its time is up
        self.roster = list(ROSTER)
        self.now = _dt.datetime(2026, 11, 13, 11, 30, tzinfo=U)
        for target, fake in ((scorecard, "observed_by_station"), (scorecard, "forecast_for_day"),
                             (scorecard, "market_levels"), (sn, "list_recent")):
            p = mock.patch.object(target, fake, getattr(self, "_" + fake))
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(scorecard.basemap, "load_roster", lambda: self.roster)
        p.start()
        self.addCleanup(p.stop)
        outer = self

        class Frozen(_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return outer.now.astimezone(tz) if tz else outer.now.replace(tzinfo=None)

        shim = types.SimpleNamespace(**{k: getattr(_dt, k) for k in dir(_dt) if not k.startswith("__")})
        shim.datetime = Frozen
        p = mock.patch.object(scorecard, "dt", shim)
        p.start()
        self.addCleanup(p.stop)

        class Deadline:
            def __init__(self, seconds):
                pass

            def over(self, need=0.0):
                return outer.limit is not None and len(outer.calls) >= outer.limit * len(scorecard.SOURCES)

        p = mock.patch.object(scorecard.arch, "Deadline", Deadline)
        p.start()
        self.addCleanup(p.stop)

    # ---- stand-ins
    def _observed_by_station(self, store, roster, first, lasts):
        return {c["station"]: {d: dict(o) for d, o in self.obs.get(c["station"], {}).items()
                               if d <= lasts[c["station"]].isoformat()} for c in roster}

    def _forecast_for_day(self, store, by_kind, source, c, tz, day, stamps=None):
        self.calls.append((c["station"], day.isoformat(), source))
        if source == "lamp" and day.day % 5 == 0:
            return None
        k = sum(map(ord, c["station"] + source)) % 4 + self.shift.get((c["station"], day.isoformat()), 0)
        return {"high": 61.0 + k + day.day % 3, "low": 41.0 + k, "cycle": day.strftime("%Y%m%dT0000Z"),
                "cycleHigh": None, "cycleLow": None, "lead": 6.0}

    def _market_levels(self, store, c, tz, day, cache):
        if day.day % 4 == 0:
            return None
        return {"high": 62.0 + day.day % 2, "low": 42.0, "asof": day.isoformat() + "T22:00:00Z"}

    def _list_recent(self, store, sid, kind, now, hours=48):
        self.listed.append((sid, kind, hours))
        return []

    # ---- helpers
    def run_pass(self, cfg=None):
        self.calls, self.listed = [], []
        self.assertEqual(scorecard.scorecard_pass(cfg or {}, self.st), 0)
        return set((s, d) for s, d, _ in self.calls)

    def snapshot(self):
        raw = self.st.get("snapshots/scorecard.json")
        doc = json.loads(raw) if raw else None
        if doc:
            doc.pop("asof")
        return doc

    def full(self):
        """The snapshot a pass with no kept rows writes for the same record and the same moment."""
        keep = self.st.get(scorecard.ROWS_KEY)
        self.st.delete(scorecard.ROWS_KEY)
        self.run_pass()
        snap = self.snapshot()
        if keep is not None:
            self.st.put(scorecard.ROWS_KEY, keep)
        return snap

    def kept(self):
        return json.loads(gzip.decompress(self.st.get(scorecard.ROWS_KEY)))["stations"]

    def next_day(self):
        self.now += _dt.timedelta(days=1)
        nd = max(self.obs["KAAA"])
        new = (_dt.date.fromisoformat(nd) + _dt.timedelta(days=1)).isoformat()
        for sid in self.obs:
            self.obs[sid][new] = {"high": 70, "low": 50, "n": 23}
        return new

    # ---- the rules
    def test_a_first_pass_scores_every_day_and_keeps_every_row(self):
        scored = self.run_pass()
        self.assertEqual(len(scored), 2 * 24)
        kept = self.kept()
        self.assertEqual(sorted(kept), ["KAAA", "KBBB"])
        self.assertEqual(sorted(kept["KAAA"]), sorted(self.obs["KAAA"]))
        self.assertTrue(all(e["v"] == scorecard.SCORE_VERSION for e in kept["KAAA"].values()))
        self.assertEqual(self.snapshot()["stations"]["KAAA"]["daysScored"], 24)

    def test_a_morning_scores_the_new_day_and_the_week_before_it_and_writes_the_full_pass_snapshot(self):
        self.run_pass()
        new = self.next_day()
        scored = self.run_pass()
        last = _dt.date.fromisoformat(new)
        want = {(sid, d) for sid in ("KAAA", "KBBB")
                for d in days(last - _dt.timedelta(days=scorecard.RESCORE_DAYS - 1), last)}
        self.assertEqual(scored, want)
        incremental = self.snapshot()
        self.assertEqual(incremental, self.full())

    def test_the_listing_reaches_back_only_to_the_oldest_day_scored(self):
        self.run_pass()
        full_hours = {h for _, _, h in self.listed}
        self.next_day()
        self.run_pass()
        hours = {h for _, _, h in self.listed}
        self.assertEqual(len(hours), 1)
        self.assertLess(max(hours), min(full_hours))
        self.assertLess(max(hours), (scorecard.RESCORE_DAYS + 3) * 24)

    def test_a_day_whose_observation_changed_is_scored_again(self):
        self.run_pass()
        self.obs["KAAA"]["2026-10-25"]["high"] += 2
        scored = self.run_pass()
        self.assertIn(("KAAA", "2026-10-25"), scored)
        self.assertNotIn(("KBBB", "2026-10-25"), scored)
        self.assertEqual(self.kept()["KAAA"]["2026-10-25"]["row"]["obsHigh"], self.obs["KAAA"]["2026-10-25"]["high"])
        self.assertEqual(self.snapshot(), self.full())

    def test_a_kept_row_is_not_scored_again_when_nothing_about_it_changed(self):
        self.run_pass()
        self.shift[("KAAA", "2026-10-25")] = 3          # a forecast that moved after the fact, unseen by the rule
        scored = self.run_pass()
        self.assertNotIn(("KAAA", "2026-10-25"), scored)
        self.assertNotEqual(self.snapshot(), self.full())

    def test_rows_kept_under_another_version_are_all_scored_again(self):
        self.run_pass()
        self.shift[("KAAA", "2026-10-25")] = 3
        with mock.patch.object(scorecard, "SCORE_VERSION", scorecard.SCORE_VERSION + 1):
            scored = self.run_pass()
            self.assertEqual(len(scored), 2 * 24)
            self.assertEqual(self.snapshot(), self.full())

    def test_a_pass_out_of_time_keeps_what_it_scored_and_the_last_complete_snapshot(self):
        self.run_pass()
        before = self.st.get("snapshots/scorecard.json")
        self.shift[("KAAA", "2026-10-25")] = 3          # what the new version scores differently
        with mock.patch.object(scorecard, "SCORE_VERSION", scorecard.SCORE_VERSION + 1):
            self.limit = 10
            scored = self.run_pass({"_deadline_end": 1.0})
            self.assertEqual(len(scored), 10)
            self.assertEqual(self.st.get("snapshots/scorecard.json"), before)
            current = [d for sid, v in self.kept().items() for d, e in v.items() if e["v"] == scorecard.SCORE_VERSION]
            self.assertEqual(len(current), 10)
            self.limit = None
            scored = self.run_pass()
            # the 38 rows still under the old version, the newest week among them
            self.assertEqual(len(scored), 2 * 24 - 10)
            self.assertNotEqual(self.st.get("snapshots/scorecard.json"), before)
            self.assertEqual(self.snapshot(), self.full())

    def test_a_first_pass_out_of_time_writes_no_snapshot_and_the_next_one_finishes(self):
        self.limit = 30
        self.run_pass({"_deadline_end": 1.0})
        self.assertIsNone(self.st.get("snapshots/scorecard.json"))
        self.limit = None
        scored = self.run_pass()
        # the first pass scored Alpha's 24 days and Beta's first 6: now Beta's other 18 and Alpha's newest week
        self.assertEqual(len(scored), 18 + scorecard.RESCORE_DAYS)
        self.assertEqual(self.snapshot(), self.full())

    def test_a_day_the_record_no_longer_scores_is_dropped(self):
        self.run_pass()
        del self.obs["KBBB"]["2026-10-21"]
        self.run_pass()
        self.assertNotIn("2026-10-21", self.kept()["KBBB"])
        self.assertEqual(self.snapshot()["stations"]["KBBB"]["daysScored"], 23)
        self.assertEqual(self.snapshot(), self.full())

    def test_a_station_off_the_roster_is_dropped_from_the_rows(self):
        self.run_pass()
        self.roster = [c for c in ROSTER if c["station"] != "KBBB"]
        self.run_pass()
        self.assertEqual(sorted(self.kept()), ["KAAA"])
        self.assertNotIn("KBBB", self.snapshot()["stations"])

    def test_rows_of_another_schema_score_the_record_again(self):
        self.run_pass()
        doc = json.loads(gzip.decompress(self.st.get(scorecard.ROWS_KEY)))
        doc["schema"] = scorecard.ROWS_SCHEMA + 1
        self.st.put(scorecard.ROWS_KEY, gzip.compress(json.dumps(doc).encode()))
        self.assertEqual(len(self.run_pass()), 2 * 24)

    def test_unreadable_rows_score_the_record_again(self):
        self.run_pass()
        self.st.put(scorecard.ROWS_KEY, b"not gzip")
        self.assertEqual(len(self.run_pass()), 2 * 24)
        self.assertEqual(self.snapshot(), self.full())


if __name__ == "__main__":
    unittest.main()
