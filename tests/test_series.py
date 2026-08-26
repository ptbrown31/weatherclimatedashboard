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


def _psd_zip(rows):
    """A PSD bulk download: a zip holding one csv of country rows."""
    import csv as _csv
    import io
    import zipfile
    # a crop name carries a comma ("Rice, Milled"), so this has to be quoted the
    # way the real download is
    sio = io.StringIO()
    w = _csv.writer(sio)
    w.writerow(["Commodity_Description", "Country_Name", "Market_Year",
                "Attribute_Description", "Value"])
    for r in rows:
        w.writerow(list(r))
    body = sio.getvalue()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("psd_grains_pulses.csv", body)
    return buf.getvalue()


class CropYieldBasis(unittest.TestCase):
    """Rice is reported milled against paddy area, so the obvious ratio is wrong.

    The department publishes Rough Production beside the milled figure, and its
    own Yield attribute is that over area harvested. Dividing the milled figure
    instead gives about two thirds of the published yield, which put the whole
    series a third below the strikes listed against it. Corn carries no rough
    figure and must be unaffected.
    """

    def test_rice_uses_the_rough_basis_and_corn_is_unchanged(self):
        rows = [
            ("Rice, Milled", "A", 2024, "Area Harvested", 100.0),
            ("Rice, Milled", "A", 2024, "Production", 300.0),
            ("Rice, Milled", "A", 2024, "Rough Production", 450.0),
            ("Corn", "A", 2024, "Area Harvested", 100.0),
            ("Corn", "A", 2024, "Production", 600.0),
        ]
        out = series.crop_yields(fetch=lambda url: _psd_zip(rows))
        rice = dict(out["crop-rice"]["points"])
        corn = dict(out["crop-corn"]["points"])
        # rough 450 over area 100, not milled 300 over 100
        self.assertEqual(rice["2025"], 4.5)
        self.assertEqual(corn["2025"], 6.0)

    def test_the_series_is_keyed_by_the_contract_year_not_the_database_year(self):
        """The terms put the reference year at the second year of the marketing
        year while the database lists the first, so a 2024 market year is the
        2025 contract. Confirmed against the market: corn's 2026 contract prices
        a cliff between 6.16 and 6.21, which is the database's 2025 value."""
        rows = [("Corn", "A", 2024, "Area Harvested", 100.0),
                ("Corn", "A", 2024, "Production", 600.0)]
        out = series.crop_yields(fetch=lambda url: _psd_zip(rows))
        self.assertEqual([p[0] for p in out["crop-corn"]["points"]], ["2025"])


class EnergyLane(unittest.TestCase):
    """The energy series need a key, and several of the readings are ambiguous.

    Without a key the lane writes nothing and the rest of the daily pass carries
    on: that is a state to report, not a failure. The mappings themselves were
    checked against the strikes the exchange lists, and the two that are easiest
    to get wrong are pinned here.
    """

    def test_no_key_is_an_empty_result_not_an_error(self):
        from pipeline import energy
        import os
        old = os.environ.pop("WX_EIA_API_KEY", None)
        try:
            self.assertEqual(energy.energy_series({}), {})
            self.assertEqual(energy.api_key({}), "")
        finally:
            if old is not None:
                os.environ["WX_EIA_API_KEY"] = old

    def test_gas_is_dry_production_and_oil_is_per_day(self):
        """Marketed gas production runs about a tenth above dry and falls outside
        every listed ladder; the month's oil total is thirty times the daily
        rate the strikes are set on."""
        from pipeline import energy
        self.assertEqual(energy.SPECS["NGP"][2]["params"]["facets[series][]"], "N9070US2")
        self.assertEqual(energy.SPECS["OP"][2]["params"]["facets[series][]"], "MCRFPUS2")

    def test_a_month_lands_at_its_middle_on_the_year_axis(self):
        """Monthly and annual series share one axis, so a month has to be placed
        inside its year rather than at its start."""
        from pipeline import energy
        self.assertEqual(energy._x("2025"), 2025.0)
        self.assertAlmostEqual(energy._x("2026-01"), 2026 + 0.5 / 12, places=3)
        self.assertAlmostEqual(energy._x("2026-12"), 2026 + 11.5 / 12, places=3)
        self.assertTrue(2026.0 < energy._x("2026-08-24") < 2027.0)
        self.assertIsNone(energy._x("not a period"))

    def test_every_mapped_product_has_its_own_series_key(self):
        from pipeline import energy
        keys = list(energy.product_keys().values())
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(keys), len(energy.SPECS) + len(energy.SHARES))
