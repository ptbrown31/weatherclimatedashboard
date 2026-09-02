"""Tests for the vendor lane's delivery steps.

The site computes no figure of its own from the vendor's ladders. The pool's
calculation is the desk's and reaches the exchange through the market maker,
so a step carries the ladder as published and the exchange's prices recorded
with it, and nothing more. These hold that line.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import reask  # noqa: E402


class Steps(unittest.TestCase):
    THR = [60, 70, 80, 90]

    def test_a_step_carries_the_ladder_and_the_prices_and_no_figure_of_its_own(self):
        lad = {"thresholds": self.THR, "sites": {"A": {"name": "Alpha", "lat": 1.0, "lon": 2.0, "p": [50, 20, 5, 0]}}}
        st = reask._step(lad, "livecyc", "2026090106", "2026-09-01T06:00Z", "2026-09-01T10:00Z", {"A": {"70": 12.0}})
        self.assertEqual(st["sites"], {"A": [50, 20, 5, 0]})
        self.assertEqual(st["siteMeta"], {"A": {"name": "Alpha", "lat": 1.0, "lon": 2.0}})
        self.assertEqual(st["prices"], {"A": {"70": 12.0}})
        self.assertEqual(st["thresholds"], self.THR)
        self.assertNotIn("pwin", st)
        self.assertNotIn("pwinMethod", st)

    def test_an_interim_step_says_whether_the_file_reached_each_location(self):
        lad = {"thresholds": self.THR, "sites": {"A": {"name": "Alpha", "covered": True, "p": [0, 0, 0, 0]}}}
        st = reask._step(lad, "interim", "INT", "lm", "ts")
        self.assertTrue(st["siteMeta"]["A"]["covered"])
        self.assertNotIn("pwin", st)


if __name__ == "__main__":
    unittest.main()
