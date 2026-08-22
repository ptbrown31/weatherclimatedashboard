"""
build_assets.py — project the map geometry once.

Writes site/assets/basemap.json, site/assets/hurricane-geo.json,
config/cities.json and config/field_grid.json from the vendored TopoJSON and
the station roster. Re-run only when the roster or the vendored geometry
changes; the scheduled jobs read the outputs and never touch TopoJSON.

    python3 scripts/build_assets.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import basemap          # noqa: E402
from pipeline.cities import CITIES    # noqa: E402

if __name__ == "__main__":
    print(basemap.build_assets(CITIES))
