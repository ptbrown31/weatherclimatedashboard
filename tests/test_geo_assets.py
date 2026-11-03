"""The hurricane geography the page loads: every landfall region the exchange
names is drawn, and each is tagged for the view it belongs on."""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pipeline import basemap   # noqa: E402
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

    def test_the_hawaii_reference_locations_are_on_the_pacific_view(self):
        want = {"HN": "Honolulu", "HL": "Hilo", "KH": "Kahului", "KP": "Kapa'a"}
        have = {L["id"]: L for L in self.g["locations"]}
        self.assertEqual(len(have), len(self.g["locations"]))
        for lid, name in want.items():
            L = have[lid]
            self.assertEqual(L["name"], name)
            self.assertEqual(L["region"], "US Pacific")
            self.assertEqual(L["state"], "HI")
            self.assertTrue(EP_BOX[0] <= L["lon"] <= EP_BOX[2] and EP_BOX[1] <= L["lat"] <= EP_BOX[3], lid)
            self.assertFalse(AL_BOX[0] <= L["lon"] <= AL_BOX[2], lid)

    def test_every_reference_location_is_drawn_on_one_of_the_two_views(self):
        # a location outside both boxes has no dot anywhere, so the registry row is dead weight
        for L in self.g["locations"]:
            on = [b for b in (AL_BOX, EP_BOX) if b[0] <= L["lon"] <= b[2] and b[1] <= L["lat"] <= b[3]]
            self.assertTrue(on, L["id"])

    def test_atlantic_regions_stay_atlantic(self):
        for nm in ("Florida", "Texas", "Louisiana", "North Carolina", "Harris, Texas", "Miami-Dade, Florida"):
            self.assertIn(nm, self.regions)
            self.assertNotIn(nm, self.g["basins"])
            lon, lat = self.g["centroids"][nm]
            self.assertTrue(AL_BOX[0] <= lon <= AL_BOX[2] and AL_BOX[1] <= lat <= AL_BOX[3], (nm, lon, lat))

    def test_every_coastal_county_is_carried_and_no_inland_one(self):
        # the eight counties the exchange added for Isaias on 2026-10-07, which the
        # hand-kept list this replaced did not carry, and the coast either side of them
        for nm in ("Hancock, Mississippi", "Harrison, Mississippi", "Jackson, Mississippi", "Mobile, Alabama",
                   "Baldwin, Alabama", "Escambia, Florida", "Santa Rosa, Florida", "Okaloosa, Florida",
                   "Galveston, Texas", "Orleans, Louisiana", "Charleston, South Carolina", "Dare, North Carolina",
                   "Suffolk, New York", "Barnstable, Massachusetts", "Monroe, Florida"):
            self.assertIn(nm, self.g["counties"], nm)
        # no coastline: Orlando's county, Atlanta's, and Florida's Panhandle interior
        for nm in ("Orange, Florida", "Fulton, Georgia", "Leon, Florida", "Holmes, Florida"):
            self.assertNotIn(nm, self.g["counties"], nm)
        self.assertNotIn("Kalawao, Hawaii", self.g["counties"])
        # every county carries a centroid, and the label is "<county>, <state>"
        for nm in self.g["counties"]:
            self.assertIn(nm, self.g["centroids"])
            self.assertEqual(len(nm.split(", ")), 2, nm)


class GoesConus(unittest.TestCase):
    """The part of a GOES-East CONUS satellite frame the landing page shows under
    the map: inside the frame, clear of the caption STAR prints along its foot, and
    cut so the lower 48 fills it as it fills the map, which is what makes the
    country the map's size at the map's width."""
    def setUp(self):
        with open(os.path.join(ROOT, "site", "assets", "basemap.json")) as fh:
            self.g = json.load(fh)["goesConus"]
        topo, ring = basemap.decode_topo(os.path.join(ROOT, "geo", "states-10m.json"))
        self.rings = [r for g in topo["objects"]["states"]["geometries"]
                      if g["properties"].get("name") is not None and g["id"] not in basemap.SKIP
                      for r in basemap.geom_rings(g, ring)]
        with open(os.path.join(ROOT, "config", "field_grid.json")) as fh:
            tr = basemap.Transform.from_json(json.load(fh)["transform"])
        xs = [tr.project(lon, lat)[0] for r in self.rings for lon, lat in r]
        self.left, self.right = min(xs), max(xs)

    def test_the_point_under_the_satellite_is_the_grid_origin(self):
        x, y = basemap.goes_conus_px(-75.0, 0.0)
        self.assertAlmostEqual(x, 0.101332 / 0.139944 * 1250, places=6)
        self.assertAlmostEqual(y, 0.128212 / 0.083944 * 750, places=6)

    def test_the_crop_is_inside_the_frame_and_clear_of_the_caption(self):
        x, y, w, h = self.g["crop"]
        self.assertEqual(self.g["frame"], [1250, 750])
        self.assertTrue(x >= 0 and y >= 0 and x + w <= 1250 and y + h <= 700, self.g)

    def test_the_lower_48_fills_the_crop_as_it_fills_the_map(self):
        gx = [basemap.goes_conus_px(lon, lat)[0] for r in self.rings for lon, lat in r]
        on_map = (self.right - self.left) / basemap.W
        in_crop = (max(gx) - min(gx)) / self.g["crop"][2]
        self.assertAlmostEqual(in_crop, on_map, places=3)

    def test_the_shipped_crop_is_the_one_the_geometry_gives(self):
        # a change to the projection or the map is not live until build_assets is re-run
        self.assertEqual(basemap.goes_conus_crop(self.rings, self.left, self.right), self.g["crop"])


if __name__ == "__main__":
    unittest.main()
