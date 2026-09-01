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
