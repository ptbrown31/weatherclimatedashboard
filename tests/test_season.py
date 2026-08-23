"""The season job: the HURDAT2 parse and its thresholds, the cumulative
climatology curves, the current-year ATCF pass, and the climatology cache.
No network -- every fetch is replaced.

The HURDAT2 rows below are shortened: the published file carries 21 fields per
row (wind radii out to the 64-kt quadrants), and the parser reads the first
seven of them. One row is written at full width to show that makes no
difference."""
import datetime as dt
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import season, storage, hurricane, gov_weather as gw    # noqa: E402

# Two seasons, used as the base period so every mean is a half.
#
#   ALPHA     34 kt on 06-10, hurricane on 06-15, major on 06-18
#   BETA      never reaches 34 kt: a depression, not part of the count
#   GAMMA     34 kt on 09-01, hurricane on 09-05, never major
#   DELTA     subtropical, 35 kt on 04-20: named before the curve opens
#   EPSILON   34 kt on 10-01, hurricane and major on the same row, 10-05
#   ZETA      34 kt on 12-10: named after the curve closes
#   ETA       35 kt, but as a low and then extratropical: not a cyclone
HURDAT = """\
AL012001,              ALPHA,      5,
20010609, 1200,  , TD, 25.0N,  55.0W,  30, 1008, -999, -999, -999, -999
20010610, 0000,  , TS, 25.5N,  56.0W,  35, 1005, -999, -999, -999, -999
20010615, 0000,  , HU, 27.0N,  60.0W,  70,  985,   60,   50,   40,   50
20010618, 0000,  , HU, 29.0N,  65.0W, 100,  950,   90,   80,   60,   70,   50,   40,   30,   40,   25,   20,   15,   20,    0,   30
20010620, 0000,  , EX, 33.0N,  70.0W,  55,  985, -999, -999, -999, -999
AL022001,               BETA,      2,
20010712, 0600,  , TD, 15.0N,  40.0W,  25, 1010, -999, -999, -999, -999
20010712, 1800,  , TD, 15.4N,  41.0W,  30, 1009, -999, -999, -999, -999
AL032001,              GAMMA,      3,
20010901, 0000,  , TS, 18.0N,  45.0W,  40, 1000, -999, -999, -999, -999
20010903, 0000,  , TS, 20.0N,  50.0W, -999, -999, -999, -999, -999, -999
20010905, 0000,  , HU, 22.0N,  55.0W,  70,  980,   60,   50,   40,   50
AL012002,              DELTA,      2,
20020420, 0000,  , SS, 30.0N,  60.0W,  35, 1004, -999, -999, -999, -999
20020422, 0000,  , SS, 32.0N,  58.0W,  40, 1002, -999, -999, -999, -999
AL022002,            EPSILON,      2,
20021001, 0000,  , TS, 16.0N,  50.0W,  45, 1000, -999, -999, -999, -999
20021005, 0000,  , HU, 20.0N,  58.0W,  96,  955,   80,   70,   60,   70
AL032002,               ZETA,      1,
20021210, 0000,  , TS, 24.0N,  48.0W,  50,  998, -999, -999, -999, -999
AL042002,                ETA,      2,
20020815, 0000,  , LO, 35.0N,  40.0W,  35, 1006, -999, -999, -999, -999
20020816, 0000,  , EX, 38.0N,  36.0W,  35, 1004, -999, -999, -999, -999
"""

# One ATCF best-track row. The b-deck repeats a synoptic time once per wind
# radius threshold, so the layout matters more than the values: field 2 is the
# time, 8 the wind, 10 the type, 27 the name.
BTK = "AL, {n}, {date},   , BEST,   0, 250N,  600W, {kt}, 1004, {ty},  34, NEQ, 0, 0, 0, 0, 1010, 80, 60, 40, 0, L, 0, , 0, 0, {name}, S, 0"


def btk(n, rows):
    return "\n".join(BTK.format(n="%02d" % n, date=d, kt="%3d" % kt, ty=ty, name=name) for d, kt, ty, name in rows)


def hurdat_with_filler(text=HURDAT, count=1100):
    """The fixture padded out to a plausible archive size, so it clears the
    job's guard against a truncated download. Every filler system peaks at
    30 kt, so none of them reaches any threshold."""
    parts = [text]
    for k in range(count):
        year = 1991 + (k % 30)
        parts.append("AL%02d%d,           UNNAMED,      1,\n%d0715, 0000,  , TD, 15.0N,  40.0W,  30, 1010, -999, -999\n"
                     % (50 + k % 40, year, year))
    return "".join(parts)


class ParseHurdat(unittest.TestCase):
    def setUp(self):
        self.by_id = {s["id"]: s for s in season.parse_hurdat(HURDAT)}

    def test_every_system_is_read(self):
        self.assertEqual(len(self.by_id), 7)
        self.assertEqual(self.by_id["AL012001"]["name"], "Alpha")
        self.assertEqual(self.by_id["AL012001"]["year"], 2001)

    def test_thresholds_and_formation_dates(self):
        a = self.by_id["AL012001"]
        self.assertEqual((a["named"], a["hurricanes"], a["majors"]),
                         ("2001-06-10", "2001-06-15", "2001-06-18"))

    def test_below_34_kt_is_not_named(self):
        b = self.by_id["AL022001"]
        self.assertEqual((b["named"], b["hurricanes"], b["majors"]), (None, None, None))

    def test_a_low_at_35_kt_is_not_named(self):
        # 35 kt on a low and then an extratropical row: strong enough, wrong type
        self.assertIsNone(self.by_id["AL042002"]["named"])

    def test_hurricane_without_major(self):
        g = self.by_id["AL032001"]
        self.assertEqual((g["named"], g["hurricanes"], g["majors"]),
                         ("2001-09-01", "2001-09-05", None))

    def test_missing_wind_is_ignored(self):
        # GAMMA's 09-03 row carries -999; the first date stays the 09-01 row
        self.assertEqual(self.by_id["AL032001"]["named"], "2001-09-01")

    def test_96_kt_is_a_major_on_the_same_row_as_the_hurricane(self):
        e = self.by_id["AL022002"]
        self.assertEqual((e["hurricanes"], e["majors"]), ("2002-10-05", "2002-10-05"))

    def test_off_season_formation_dates_are_kept(self):
        self.assertEqual(self.by_id["AL012002"]["named"], "2002-04-20")     # April
        self.assertEqual(self.by_id["AL032002"]["named"], "2002-12-10")     # December

    def test_a_wrong_row_count_does_not_swallow_the_next_system(self):
        broken = HURDAT.replace("AL012001,              ALPHA,      5,",
                                "AL012001,              ALPHA,     40,")
        ids = [s["id"] for s in season.parse_hurdat(broken)]
        self.assertEqual(ids, [s["id"] for s in season.parse_hurdat(HURDAT)])


class SeasonDays(unittest.TestCase):
    def test_window_is_may_through_november(self):
        days = season._season_days()
        self.assertEqual((len(days), days[0], days[-1]), (214, "05-01", "11-30"))
        self.assertEqual(days[40], "06-10")            # 31 days of May, then ten of June


class Climatology(unittest.TestCase):
    def setUp(self):
        self.clim = season.climatology(season.parse_hurdat(HURDAT), "hurdat2-1851-2002-010103.txt",
                                       "2026-08-23", first=2001, last=2002)
        self.days = season._season_days()

    def test_header(self):
        self.assertEqual(self.clim["source"], "NHC HURDAT2 (hurdat2-1851-2002-010103.txt)")
        self.assertEqual((self.clim["period"], self.clim["years"], self.clim["start"]), ("2001-2002", 2, "05-01"))

    def test_totals_are_full_calendar_years(self):
        # five named (December's included), three hurricanes, two majors, over two years
        self.assertEqual(self.clim["totals"], {"named": 2.5, "hurricanes": 1.5, "majors": 1.0})

    def test_curves_have_one_value_per_day(self):
        for group in season.GROUPS:
            self.assertEqual(len(self.clim[group]), 214)

    def test_curve_opens_above_zero_when_a_system_formed_before_may(self):
        # DELTA on 04-20 is already counted when the window opens: one storm, two years
        self.assertEqual(self.clim["named"][0], 0.5)
        self.assertEqual(self.clim["hurricanes"][0], 0.0)

    def test_curve_steps_on_the_day_of_formation(self):
        i = self.days.index("06-10")
        self.assertEqual(self.clim["named"][i - 1], 0.5)
        self.assertEqual(self.clim["named"][i], 1.0)
        self.assertEqual(self.clim["named"][self.days.index("09-01")], 1.5)
        self.assertEqual(self.clim["named"][self.days.index("10-01")], 2.0)

    def test_curve_ends_below_the_total_when_a_system_formed_in_december(self):
        # ZETA on 12-10 is past the right-hand end of the window but still in the total
        self.assertEqual(self.clim["named"][-1], 2.0)
        self.assertEqual(self.clim["totals"]["named"], 2.5)
        # nothing after November for the other two, so those curves do finish at the total
        self.assertEqual(self.clim["hurricanes"][-1], self.clim["totals"]["hurricanes"])
        self.assertEqual(self.clim["majors"][-1], self.clim["totals"]["majors"])

    def test_monthly_majors_split(self):
        months = self.clim["monthlyMajors"]
        self.assertEqual(sorted(months), ["05", "06", "07", "08", "09", "10", "11"])
        self.assertEqual(months["06"], 0.5)                 # ALPHA
        self.assertEqual(months["10"], 0.5)                 # EPSILON
        self.assertEqual(months["08"], 0.0)                 # every month of the window, zero or not
        self.assertAlmostEqual(sum(months.values()), self.clim["totals"]["majors"], places=3)

    def test_a_major_outside_the_window_still_counts(self):
        off = HURDAT.replace("20021005, 0000,  , HU, 20.0N,  58.0W,  96,",
                             "20021225, 0000,  , HU, 20.0N,  58.0W,  96,")
        months = season.climatology(season.parse_hurdat(off), "f.txt", "2026-08-23",
                                    first=2001, last=2002)["monthlyMajors"]
        self.assertEqual(months["12"], 0.5)
        self.assertAlmostEqual(sum(months.values()), 1.0, places=3)


class AtcfSeason(unittest.TestCase):
    """The season in progress, from the live best tracks."""
    def setUp(self):
        self.files = {
            "bal012026.dat": btk(1, [("2026061512", 25, "DB", "INVEST"), ("2026061712", 35, "TS", "ONE"),
                                     ("2026061718", 40, "TS", "ARTHUR"), ("2026061800", 35, "TS", "ARTHUR")]),
            "bal022026.dat": btk(2, [("2026072100", 35, "TS", "BERTHA"), ("2026072512", 65, "HU", "BERTHA"),
                                     ("2026072600", 100, "HU", "BERTHA")]),
            "bal032026.dat": btk(3, [("2026081012", 35, "LO", "SIX"), ("2026081018", 30, "TD", "SIX")]),
            # 90 and above are invest areas, 80-89 test entries: neither is a system
            "bal952026.dat": btk(95, [("2026082000", 45, "TS", "INVEST")]),
            "bal832026.dat": btk(83, [("2026082000", 90, "HU", "TEST")]),
        }
        self.orig = gw._get_text
        gw._get_text = self._text

    def tearDown(self):
        gw._get_text = self.orig

    def _text(self, url, timeout=60):
        if url.endswith("/btk/"):
            return "".join('<a href="%s">%s</a>\n' % (n, n) for n in sorted(self.files))
        return self.files[url.rsplit("/", 1)[-1]]

    def test_storms_dates_and_names(self):
        storms = season.atcf_season(2026)
        self.assertEqual([s["id"] for s in storms], ["AL012026", "AL022026"])
        a, b = storms
        # the name comes from the latest cyclone row: the first one still says ONE
        self.assertEqual((a["name"], a["named"], a["hurricanes"]), ("Arthur", "2026-06-17", None))
        self.assertEqual((b["name"], b["named"], b["hurricanes"], b["majors"]),
                         ("Bertha", "2026-07-21", "2026-07-25", "2026-07-26"))

    def test_a_low_at_35_kt_is_not_a_named_storm(self):
        self.assertNotIn("AL032026", [s["id"] for s in season.atcf_season(2026)])

    def test_agrees_with_the_hurricane_job(self):
        """The two jobs read the same files and both numbers appear on the
        site, so the thresholds have to give the same answer."""
        mine = season.season_lists(season.atcf_season(2026))
        theirs = hurricane.season_counts(2026)
        self.assertEqual(len(mine["named"]), theirs["named"])
        self.assertEqual(len(mine["hurricanes"]), theirs["hurricanes"])
        self.assertEqual(len(mine["majors"]), theirs["majors"])
        self.assertEqual([r["name"] for r in mine["named"]], theirs["names"])

    def test_lists_are_in_date_order(self):
        lists = season.season_lists(season.atcf_season(2026))
        self.assertEqual([r["date"] for r in lists["named"]], ["2026-06-17", "2026-07-21"])
        self.assertEqual(lists["majors"], [{"date": "2026-07-26", "name": "Bertha", "id": "AL022026"}])


class ClimatologyCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = storage.LocalStorage(self.tmp.name)
        self.today = dt.date(2026, 8, 23)
        self.store.put(season.CLIM_KEY, json.dumps(
            {"file": "hurdat2-1851-2025-02272026.txt", "computed": "2026-08-20",
             "climatology": {"totals": {"named": 14.4}}}).encode())

    def test_same_file_is_reused(self):
        clim = season.load_climatology(self.store, "hurdat2-1851-2025-02272026.txt", self.today)
        self.assertEqual(clim, {"totals": {"named": 14.4}})

    def test_a_new_file_forces_a_recompute(self):
        self.assertIsNone(season.load_climatology(self.store, "hurdat2-1851-2026-03012027.txt", self.today))

    def test_a_stale_cache_is_reread(self):
        self.assertIsNone(season.load_climatology(self.store, "hurdat2-1851-2025-02272026.txt",
                                                  self.today + dt.timedelta(days=40)))

    def test_missing_or_damaged_cache(self):
        empty = storage.LocalStorage(tempfile.mkdtemp(dir=self.tmp.name))
        self.assertIsNone(season.load_climatology(empty, "any.txt", self.today))
        self.store.put(season.CLIM_KEY, b"{not json")
        self.assertIsNone(season.load_climatology(self.store, "hurdat2-1851-2025-02272026.txt", self.today))


class SeasonPass(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = storage.LocalStorage(self.tmp.name)
        self.cfg = {"user_agent": "test", "season_forecast": {"named": 17, "hurricanes": 9, "majors": 4,
                                                             "source": "NOAA CPC", "label": "2026 outlook"}}
        self.hurdat_calls = []
        self.files = {"bal012026.dat": btk(1, [("2026061712", 40, "TS", "ARTHUR")])}
        self.orig = (gw._get_text, gw.latest_hurdat_url, gw.fetch_hurdat)
        gw._get_text = lambda url, timeout=60: ('<a href="bal012026.dat">bal012026.dat</a>' if url.endswith("/btk/")
                                                else self.files[url.rsplit("/", 1)[-1]])
        gw.latest_hurdat_url = lambda: "https://www.nhc.noaa.gov/data/hurdat/hurdat2-1851-2025-02272026.txt"
        gw.fetch_hurdat = self._hurdat

    def tearDown(self):
        gw._get_text, gw.latest_hurdat_url, gw.fetch_hurdat = self.orig

    def _hurdat(self, url):
        self.hurdat_calls.append(url)
        return hurdat_with_filler()

    def snap(self):
        return json.loads(self.store.get(season.SNAP_KEY))

    def test_writes_the_snapshot(self):
        self.assertEqual(season.season_pass(self.cfg, self.store), 0)
        s = self.snap()
        self.assertEqual(s["schema"], 1)
        self.assertEqual(s["year"], dt.datetime.now(dt.timezone.utc).year)
        self.assertEqual(s["season"]["named"], [{"date": "2026-06-17", "name": "Arthur", "id": "AL012026"}])
        self.assertEqual(s["season"]["hurricanes"], [])
        self.assertEqual(s["climatology"]["source"], "NHC HURDAT2 (hurdat2-1851-2025-02272026.txt)")
        self.assertEqual(s["climatology"]["period"], "1991-2020")
        self.assertEqual(len(s["climatology"]["named"]), 214)

    def test_forecast_comes_straight_from_config(self):
        season.season_pass(self.cfg, self.store)
        self.assertEqual(self.snap()["forecast"], {"named": 17, "hurricanes": 9, "majors": 4,
                                                   "source": "NOAA CPC", "label": "2026 outlook"})

    def test_forecast_is_empty_when_config_has_none(self):
        season.season_pass({"user_agent": "test"}, self.store)
        self.assertEqual(self.snap()["forecast"], {"named": None, "hurricanes": None, "majors": None,
                                                   "source": "", "label": ""})

    def test_the_same_file_is_not_downloaded_twice(self):
        season.season_pass(self.cfg, self.store)
        first = self.snap()["climatology"]
        season.season_pass(self.cfg, self.store)
        self.assertEqual(len(self.hurdat_calls), 1)
        self.assertEqual(self.snap()["climatology"], first)

    def test_a_new_file_is_downloaded(self):
        season.season_pass(self.cfg, self.store)
        gw.latest_hurdat_url = lambda: "https://www.nhc.noaa.gov/data/hurdat/hurdat2-1851-2026-03012027.txt"
        season.season_pass(self.cfg, self.store)
        self.assertEqual(len(self.hurdat_calls), 2)
        self.assertEqual(self.snap()["climatology"]["source"], "NHC HURDAT2 (hurdat2-1851-2026-03012027.txt)")

    def test_a_truncated_download_is_refused(self):
        gw.fetch_hurdat = lambda url: HURDAT              # far too few systems to be the archive
        self.assertEqual(season.season_pass(self.cfg, self.store), 1)
        self.assertIsNone(self.store.get(season.SNAP_KEY))

    def test_a_failed_pass_keeps_the_previous_snapshot(self):
        season.season_pass(self.cfg, self.store)
        good = self.snap()
        boom = self._raise
        gw._get_text = boom
        gw.latest_hurdat_url = boom
        self.assertEqual(season.season_pass(self.cfg, self.store), 1)
        after = self.snap()
        self.assertEqual(after["season"], good["season"])
        self.assertEqual(after["climatology"], good["climatology"])
        self.assertEqual(after["asof"], good["asof"])       # not restamped: the season list is not fresh
        self.assertGreaterEqual(after["written"], good["written"])

    @staticmethod
    def _raise(*a, **k):
        raise RuntimeError("feed down")


if __name__ == "__main__":
    unittest.main()
