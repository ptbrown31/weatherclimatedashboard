"""The normals step's time budget, and gov_weather._fetch's whole-call bound (owner's decision 2026-11-19).

On 18 November 2026 NCEI answered fifteen stations' normals at about 48.6 s each, 729 s in all, and the daily chain
ran past Lambda's 900 s. The step now starts no station after BUDGET_SECONDS (or the chain's own remaining budget,
if smaller), no request runs past it, and a station it does not reach waits for the next morning untouched."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import gov_weather as gw, normals, storage   # noqa: E402

T0 = 1_800_000_000.0
ROSTER = [{"station": f"K{i:03d}", "unit": "F", "lat": 40.0 + i, "lon": -75.0} for i in range(6)]
DAYS = {f"01-{d:02d}": {"tmax": 40.0, "tmin": 25.0, "tmaxSd": 5.0, "tminSd": 5.0} for d in range(1, 32)}
DAYS.update({f"{m:02d}-{d:02d}": {"tmax": 60.0, "tmin": 40.0, "tmaxSd": 5.0, "tminSd": 5.0}
             for m in range(2, 13) for d in range(1, 29)})      # 31 + 308 = 339 days, over the 300 the step asks


class Clock:
    """A wall clock that moves only when the code under test sleeps or a fake fetch spends time."""
    def __init__(self):
        self.t = T0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, s)


class NormalsBudget(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = storage.LocalStorage(self.tmp.name)
        ghcn = {c["station"]: {"ghcn": "USW000" + c["station"][1:], "name": c["station"], "distanceKm": 1.0}
                for c in ROSTER}
        self.st.put(normals.GHCN_KEY, json.dumps(ghcn).encode(), "application/json")
        self.clock = Clock()
        self.calls = []
        for p in (mock.patch.object(normals.time, "time", self.clock.time),
                  mock.patch.object(normals.time, "sleep", self.clock.sleep),
                  mock.patch.object(normals.basemap, "load_roster", lambda: ROSTER),
                  mock.patch.object(normals.gw, "set_user_agent", lambda ua: None)):
            p.start()
            self.addCleanup(p.stop)

    def slow(self, per):
        """A fetch_normals that spends `per` seconds of the clock and records the bound it was given."""
        def fetch(ghcn, year, end=None):
            self.calls.append((ghcn, end))
            self.clock.t += per
            return dict(DAYS)
        return fetch

    def run_pass(self, cfg=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = normals.normals_pass(cfg or {}, self.st)
        lines = [json.loads(x) for x in out.getvalue().splitlines() if x.strip()]
        return rc, lines[-1]

    def written(self):
        return [c["station"] for c in ROSTER if self.st.get(f"snapshots/normals/{c['station']}.json")]

    def test_a_slow_ncei_stops_the_step_at_its_budget_and_defers_the_rest(self):
        with mock.patch.object(normals, "fetch_normals", self.slow(50)):
            rc, line = self.run_pass()
        self.assertEqual(rc, 0)
        self.assertEqual((line["written"], line["deferred"], line["errors"]), (3, 3, 0))
        self.assertEqual(line["deferredStations"], ["K003", "K004", "K005"])
        self.assertEqual(self.written(), ["K000", "K001", "K002"])
        self.assertLess(line["seconds"], normals.BUDGET_SECONDS + 50)

    def test_a_usual_ncei_writes_every_station_due(self):
        with mock.patch.object(normals, "fetch_normals", self.slow(1.5)):
            rc, line = self.run_pass()
        self.assertEqual((rc, line["written"], line["deferred"], line["errors"]), (0, 6, 0, 0))
        self.assertNotIn("deferredStations", line)

    def test_every_request_carries_the_steps_end(self):
        with mock.patch.object(normals, "fetch_normals", self.slow(1.5)):
            self.run_pass()
        self.assertEqual(len(self.calls), 6)
        self.assertEqual({end for _, end in self.calls}, {T0 + normals.BUDGET_SECONDS})

    def test_the_chains_own_budget_bounds_the_step_when_smaller(self):
        with mock.patch.object(normals, "fetch_normals", self.slow(25)):
            rc, line = self.run_pass({"_deadline_end": T0 + 60})
        self.assertEqual((line["written"], line["deferred"]), (2, 4))
        self.assertEqual({end for _, end in self.calls}, {T0 + 60})

    def test_a_fresh_station_is_skipped_not_deferred_and_asks_nothing(self):
        asof = normals.dt.datetime.now(normals.dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        for c in ROSTER[3:]:
            self.st.put(f"snapshots/normals/{c['station']}.json", json.dumps({"asof": asof, "days": DAYS}).encode(),
                        "application/json")
        with mock.patch.object(normals, "fetch_normals", self.slow(100)):
            rc, line = self.run_pass()
        self.assertEqual((line["written"], line["deferred"], line["skipped"]), (2, 1, 3))
        self.assertEqual([g for g, _ in self.calls], ["USW000000", "USW000001"])

    def test_an_unresolved_station_past_the_budget_is_deferred_without_a_search(self):
        ghcn = json.loads(self.st.get(normals.GHCN_KEY))
        del ghcn["K005"]
        self.st.put(normals.GHCN_KEY, json.dumps(ghcn).encode(), "application/json")
        searched = []
        with mock.patch.object(normals, "fetch_normals", self.slow(50)), \
                mock.patch.object(normals, "resolve_ghcn", lambda c, today, end=None: searched.append(c) or None):
            rc, line = self.run_pass()
        self.assertEqual(searched, [])
        self.assertIn("K005", line["deferredStations"])


class _Trickle:
    """A response that sends ten bytes every ten seconds of the clock, forever."""
    def __init__(self, clock):
        self.clock = clock

    def read1(self, n):
        self.clock.t += 10
        return b"x" * 10

    def read(self):
        self.clock.t += 1000
        return b"all of it, late"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FetchBound(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        for p in (mock.patch.object(gw.time, "time", self.clock.time), mock.patch.object(gw.time, "sleep", self.clock.sleep)):
            p.start()
            self.addCleanup(p.stop)

    def test_an_answer_still_arriving_at_the_end_is_a_timeout(self):
        with mock.patch.object(gw.urllib.request, "urlopen", lambda req, timeout: _Trickle(self.clock)):
            with self.assertRaises(TimeoutError):
                gw._fetch("https://example.invalid/x", tries=1, end=T0 + 30)
        self.assertLessEqual(self.clock.t, T0 + 40)

    def test_without_an_end_the_call_reads_as_it_always_did(self):
        seen = []

        def urlopen(req, timeout):
            seen.append(timeout)
            return _Trickle(self.clock)
        with mock.patch.object(gw.urllib.request, "urlopen", urlopen):
            self.assertEqual(gw._fetch("https://example.invalid/x", tries=1), b"all of it, late")
        self.assertEqual(seen, [30])

    def test_no_back_off_waits_past_the_end_and_no_try_starts_after_it(self):
        tries = []

        def urlopen(req, timeout):
            tries.append(self.clock.t)
            raise urllib.error.URLError("refused")
        with mock.patch.object(gw.urllib.request, "urlopen", urlopen):
            with self.assertRaises(TimeoutError):
                gw._fetch("https://example.invalid/x", tries=3, end=T0 + 1)
        self.assertEqual(tries, [T0])
        self.assertLessEqual(self.clock.t, T0 + 1)


if __name__ == "__main__":
    unittest.main()
