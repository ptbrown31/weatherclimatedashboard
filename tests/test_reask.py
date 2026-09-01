"""Tests for the vendor lane's stated highest-gust calculation.

The formula is printed beside every display of the number, so these hold the
code to the formula: independence across locations, uniform within threshold
bins, the interim settlement folded in as a floor, normalised to one.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import reask  # noqa: E402


class Pwin(unittest.TestCase):
    """The stated highest-gust calculation."""

    THR = [60, 70, 80, 90]

    def test_a_dominant_location_takes_nearly_everything(self):
        out = reask.pwin(self.THR, {"A": [99, 95, 80, 40], "B": [5, 1, 0, 0]})
        self.assertGreater(out["A"], 95)
        self.assertAlmostEqual(sum(out.values()), 100, delta=0.3)

    def test_identical_ladders_split_evenly(self):
        lad = [60, 30, 10, 2]
        out = reask.pwin(self.THR, {"A": lad, "B": lad, "C": lad})
        for v in out.values():
            self.assertAlmostEqual(v, 100 / 3, delta=1.0)

    def test_a_settled_gust_floors_the_lifetime_ladder(self):
        # B's forward ladder is weak, but its interim settlement says 80+ mph
        # is certain already; lifetime folds the settled figure in and B leads
        fwd = {"A": [70, 40, 10, 1], "B": [20, 5, 0, 0]}
        out = reask.pwin(self.THR, fwd, {"B": [100, 100, 100, 20]})
        self.assertGreater(out["B"], out["A"])

    def test_zero_ladders_are_not_candidates(self):
        out = reask.pwin(self.THR, {"A": [50, 20, 5, 0], "Z": [0, 0, 0, 0]})
        self.assertNotIn("Z", out)
        self.assertAlmostEqual(out["A"], 100, delta=0.1)

    def test_a_livecyc_step_carries_its_figure_and_an_interim_does_not(self):
        lad = {"thresholds": self.THR, "sites": {"A": {"name": "Alpha", "p": [50, 20, 5, 0]}}}
        st = reask._step(lad, "livecyc", "2026090106", "2026-09-01T06:00Z", "2026-09-01T10:00Z")
        self.assertIn("pwin", st)
        self.assertAlmostEqual(st["pwin"]["A"], 100, delta=0.1)
        st2 = reask._step(lad, "interim", "INT", "x", "y")
        self.assertNotIn("pwin", st2)

    def test_restating_brings_every_step_to_the_current_method(self):
        import json as j

        class Store:
            def __init__(self, doc):
                self.d = {"snapshots/storm/Dolly_2026.json": j.dumps(doc).encode()}
                self.puts = 0
            def get(self, k): return self.d.get(k)
            def put(self, k, body, *a): self.d[k] = body; self.puts += 1

        doc = {"thresholds": self.THR, "steps": [
            # written before the calculation existed
            {"kind": "livecyc", "id": "a", "sites": {"LC": [50, 15, 1, 0], "PA": [20, 5, 0, 0]}},
            # written under a method that was withdrawn
            {"kind": "livecyc", "id": "b", "sites": {"LC": [60, 20, 2, 0]},
             "pwin": {"LC": 99.0}, "pwinMethod": "old", "pwinPool": {"LHLX": {"LC": 50.0}}},
            # already current
            {"kind": "livecyc", "id": "c", "sites": {"LC": [40, 10, 0, 0]},
             "pwin": {"LC": 12.3}, "pwinMethod": reask.PWIN_METHOD},
            {"kind": "interim", "id": "INT", "sites": {}},
        ]}
        st = Store(doc)
        n = reask.restate_pwin(st, "Dolly", 2026, [], lambda **k: None)
        self.assertEqual(n, 2)                      # a and b, not c and not the interim
        out = j.loads(st.d["snapshots/storm/Dolly_2026.json"])
        a, b, c, i = out["steps"]
        self.assertEqual(a["pwinMethod"], reask.PWIN_METHOD)
        self.assertAlmostEqual(sum(a["pwin"].values()), 100, delta=0.3)
        self.assertNotIn("pwinPool", b)             # the withdrawn field is cleared
        self.assertNotEqual(b["pwin"]["LC"], 99.0)  # and its figure is recomputed
        self.assertEqual(c["pwin"]["LC"], 12.3)     # a current step is untouched
        self.assertNotIn("pwin", i)                 # an interim carries none
        reask.restate_pwin(st, "Dolly", 2026, [], lambda **k: None)
        self.assertEqual(st.puts, 1)                # idempotent: no second write

    def test_restating_uses_each_delivery_own_ladder(self):
        import json as j

        class Store:
            def __init__(self, doc):
                self.d = {"snapshots/storm/E_2026.json": j.dumps(doc).encode()}
            def get(self, k): return self.d.get(k)
            def put(self, k, body, *a): self.d[k] = body

        # two deliveries whose leader swaps: the restated series must swap too,
        # rather than painting the newest ladder across the history
        doc = {"thresholds": self.THR, "steps": [
            {"kind": "livecyc", "id": "a", "sites": {"X": [70, 30, 5, 0], "Y": [20, 4, 0, 0]}},
            {"kind": "livecyc", "id": "b", "sites": {"X": [20, 4, 0, 0], "Y": [70, 30, 5, 0]}}]}
        st = Store(doc)
        reask.restate_pwin(st, "E", 2026, [], lambda **k: None)
        a, b = j.loads(st.d["snapshots/storm/E_2026.json"])["steps"]
        self.assertGreater(a["pwin"]["X"], a["pwin"]["Y"])
        self.assertGreater(b["pwin"]["Y"], b["pwin"]["X"])
