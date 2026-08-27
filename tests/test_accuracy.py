"""The two derivations behind the accuracy curve.

Both change what the curve says, and both are easy to get wrong in a way that
looks reasonable, so both are pinned here rather than trusted to a comment.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import export_accuracy as ea   # noqa: E402


class SettlementDegree(unittest.TestCase):
    """A contract pays when the recorded high is STRICTLY above the strike.

    A market certain of 92 bids the 91 strike to a dollar and the 92 strike to
    nothing, so the crossing sits at 91.5 — the midpoint of a step, not a
    temperature. The degree above is the settle; the nearest degree is a coin
    flip that measured 55% against 98% on real ladders.
    """

    def test_a_half_resolves_upward_not_to_the_nearest(self):
        self.assertEqual(ea.settle_degree(91.5), 92.0)
        self.assertEqual(ea.settle_degree(92.5), 93.0)

    def test_a_crossing_inside_a_degree_names_the_degree_above(self):
        self.assertEqual(ea.settle_degree(91.2), 92.0)
        self.assertEqual(ea.settle_degree(91.9), 92.0)

    def test_a_crossing_on_a_whole_degree_stays_there(self):
        self.assertEqual(ea.settle_degree(91.0), 91.0)

    def test_no_crossing_is_not_a_number(self):
        self.assertIsNone(ea.settle_degree(None))


class CarriedForecast(unittest.TestCase):
    """The bulletin's window expires; the forecast for the day does not.

    Late in the day the remaining window holds only the night, so read literally
    the Service forecast 70 for a day it had called 88. That is the window, not a
    forecast anyone made, and scoring it that way grew the Service's error to
    eighteen degrees an hour before midnight.
    """

    def test_a_dropped_window_keeps_the_day_it_forecast(self):
        recs = [
            {"city": "KATL", "day": "2026-08-20", "lead": 30, "nws": 88.0, "obs": 88.0},
            {"city": "KATL", "day": "2026-08-20", "lead": 12, "nws": 88.0, "obs": 88.0},
            {"city": "KATL", "day": "2026-08-20", "lead": 3, "nws": 70.0, "obs": 88.0},
        ]
        ea.carry_forward(recs)
        self.assertEqual([r["nwsDay"] for r in sorted(recs, key=lambda r: -r["lead"])],
                         [88.0, 88.0, 88.0])

    def test_a_rising_forecast_still_rises(self):
        """Carrying forward must not freeze a forecast that genuinely climbs."""
        recs = [
            {"city": "KDEN", "day": "2026-08-20", "lead": 30, "nws": 84.0, "obs": 90.0},
            {"city": "KDEN", "day": "2026-08-20", "lead": 20, "nws": 89.0, "obs": 90.0},
            {"city": "KDEN", "day": "2026-08-20", "lead": 6, "nws": 90.0, "obs": 90.0},
        ]
        ea.carry_forward(recs)
        self.assertEqual([r["nwsDay"] for r in sorted(recs, key=lambda r: -r["lead"])],
                         [84.0, 89.0, 90.0])

    def test_cities_and_days_do_not_leak_into_each_other(self):
        recs = [
            {"city": "KATL", "day": "2026-08-20", "lead": 10, "nws": 99.0, "obs": 99.0},
            {"city": "KATL", "day": "2026-08-21", "lead": 30, "nws": 70.0, "obs": 70.0},
            {"city": "KDEN", "day": "2026-08-20", "lead": 30, "nws": 60.0, "obs": 60.0},
        ]
        ea.carry_forward(recs)
        got = {(r["city"], r["day"]): r["nwsDay"] for r in recs}
        self.assertEqual(got[("KATL", "2026-08-21")], 70.0)
        self.assertEqual(got[("KDEN", "2026-08-20")], 60.0)


class Curve(unittest.TestCase):
    def test_a_thin_bin_is_dropped_rather_than_drawn(self):
        recs = [{"city": "K%02d" % i, "day": "2026-08-20", "lead": 5,
                 "nws": 80.0, "fx": 80.5, "obs": 81.0} for i in range(5)]
        self.assertEqual(ea.curve(recs)["points"], [])

    def test_a_full_bin_reports_both_errors_and_its_count(self):
        recs = [{"city": "K%02d" % i, "day": "2026-08-20", "lead": 5,
                 "nws": 79.0, "fx": 80.5, "obs": 81.0} for i in range(40)]
        p = ea.curve(recs)["points"][0]
        self.assertEqual(p["cityDays"], 40)
        self.assertAlmostEqual(p["nws"], 2.0)     # 79 vs 81
        self.assertAlmostEqual(p["fx"], 0.0)      # 80.5 -> 81
        self.assertAlmostEqual(p["improvement"], 100.0)
