"""The underlying series the monthly and weekly weather contracts settle on.

Two rules here decide whether a number is the right number, so both are pinned:
the drought feed answers with two areas for the same week and only the
contiguous states is the one the contract reads, and NOAA marks a missing month
with a large negative sentinel that must not be plotted as a value.
"""
import datetime as dt
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import series   # noqa: E402

USDM_CSV = (
    "MapDate,AreaOfInterest,None,D0,D1,D2,D3,D4,ValidStart,ValidEnd,StatisticFormatID\n"
    "20260818,CONUS,23.79,76.21,52.70,29.87,10.60,1.35,2026-08-18,2026-08-24,1\n"
    "20260818,Total,34.56,65.44,45.00,25.00,9.00,1.00,2026-08-18,2026-08-24,1\n"
    "20260811,CONUS,26.38,73.62,50.38,29.50,10.27,1.04,2026-08-11,2026-08-17,1\n"
    "20260811,Total,36.91,63.09,44.00,24.00,8.50,0.90,2026-08-11,2026-08-17,1\n"
)


class Drought(unittest.TestCase):
    def test_only_the_contiguous_states_are_read(self):
        d = series.drought(dt.datetime(2026, 8, 26, tzinfo=dt.timezone.utc), lambda u, timeout=0: USDM_CSV)
        self.assertEqual(d["area"], "CONUS")
        self.assertEqual(d["points"], [["20260811", 73.62], ["20260818", 76.21]])

    def test_one_value_per_week_not_two(self):
        d = series.drought(dt.datetime(2026, 8, 26, tzinfo=dt.timezone.utc), lambda u, timeout=0: USDM_CSV)
        dates = [p[0] for p in d["points"]]
        self.assertEqual(len(dates), len(set(dates)))

    def test_the_figure_is_everything_that_is_not_none(self):
        # the Monitor publishes the drought-free share; the contract reads the rest
        d = series.drought(dt.datetime(2026, 8, 26, tzinfo=dt.timezone.utc), lambda u, timeout=0: USDM_CSV)
        self.assertAlmostEqual(d["points"][-1][1], 100.0 - 23.79, places=2)

    def test_the_title_says_which_area(self):
        d = series.drought(dt.datetime(2026, 8, 26, tzinfo=dt.timezone.utc), lambda u, timeout=0: USDM_CSV)
        self.assertIn("contiguous", d["title"])


class CitySeries(unittest.TestCase):
    def body(self, data):
        return {"description": {"title": "Seattle, Washington Precipitation", "units": "Inches"}, "data": data}

    def test_months_come_back_in_order_with_their_values(self):
        d = series.caag("X", "pcp", 2026, lambda u: self.body(
            {"202602": {"value": 3.8}, "202601": {"value": 5.8}}))
        self.assertEqual(d["points"], [["202601", 5.8], ["202602", 3.8]])
        self.assertEqual(d["units"], "Inches")

    def test_a_missing_month_is_dropped_not_plotted(self):
        d = series.caag("X", "pcp", 2026, lambda u: self.body(
            {"202601": {"value": 5.8}, "202602": {"value": -99.99}, "202603": {"value": -999}}))
        self.assertEqual(d["points"], [["202601", 5.8]])

    def test_a_blank_or_unparseable_value_is_dropped(self):
        d = series.caag("X", "pcp", 2026, lambda u: self.body(
            {"202601": {"value": ""}, "202602": {"value": None}, "202603": {"value": "n/a"},
             "202604": {"value": 1.2}}))
        self.assertEqual(d["points"], [["202604", 1.2]])

    def test_the_title_noaa_returns_is_kept(self):
        # it names the place the number is for, which is not always the place
        # the product code suggests
        d = series.caag("X", "tavg", 2026, lambda u: self.body({"202601": {"value": 1}}))
        self.assertEqual(d["title"], "Seattle, Washington Precipitation")


if __name__ == "__main__":
    unittest.main()
