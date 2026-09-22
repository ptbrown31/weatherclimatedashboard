"""The hurricane geography the page loads: every landfall region the exchange
names is drawn, and each is tagged for the view it belongs on."""
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EP_BOX = (-180.0, 0.0, -85.0, 40.0)
AL_BOX = (-101.0, 4.0, -40.0, 48.0)


class HurricaneGeo(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "site", "assets", "hurricane-geo.json")) as fh:
            self.g = json.load(fh)
        self.regions = {**self.g["states"], **self.g["counties"]}

    def test_hawaii_state_and_counties_are_drawn_on_the_pacific_view(self):
        for nm in ("Hawaii", "Honolulu, Hawaii", "Hawaii, Hawaii", "Kauai, Hawaii", "Maui, Hawaii"):
            self.assertIn(nm, self.regions)
            self.assertEqual(self.g["basins"].get(nm), ["EP"], nm)
            lon, lat = self.g["centroids"][nm]
            self.assertTrue(EP_BOX[0] <= lon <= EP_BOX[2] and EP_BOX[1] <= lat <= EP_BOX[3], (nm, lon, lat))
            self.assertFalse(AL_BOX[0] <= lon <= AL_BOX[2], (nm, lon))

    def test_the_pacific_view_carries_hawaii_and_the_regions_facing_both_oceans(self):
        want = sorted([n for n in self.regions if n == "Hawaii" or n.endswith(", Hawaii")] + ["Mexico", "Honduras"])
        self.assertEqual(sorted(self.g["basins"]), want)
        for nm in ("Mexico", "Honduras"):
            self.assertEqual(self.g["basins"][nm], ["AL", "EP"], nm)
            self.assertIn(nm, self.g["countries"])

    def test_the_mexico_outline_reaches_the_pacific_coast(self):
        # the landfall contract admits a Pacific landfall, so Baja and the Sonora coast
        # have to be in the outline the map draws
        lons = [p[0] for ring in self.g["countries"]["Mexico"] for p in ring]
        self.assertLess(min(lons), -114.0, min(lons))

    def test_atlantic_regions_stay_atlantic(self):
        for nm in ("Florida", "Texas", "Louisiana", "North Carolina", "Harris, Texas", "Miami-Dade, Florida"):
            self.assertIn(nm, self.regions)
            self.assertNotIn(nm, self.g["basins"])
            lon, lat = self.g["centroids"][nm]
            self.assertTrue(AL_BOX[0] <= lon <= AL_BOX[2] and AL_BOX[1] <= lat <= AL_BOX[3], (nm, lon, lat))


if __name__ == "__main__":
    unittest.main()
