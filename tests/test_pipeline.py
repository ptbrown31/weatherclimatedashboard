"""
Unit tests for the pipeline: parsers against fixtures cut from live bulletins
on 2026-08-21, the storage adapter, the observation merge, and config
overrides. Standard library unittest; no network.

    python3 -m unittest discover -s tests -v
"""
import datetime as dt
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import gov_weather as gw          # noqa: E402
from pipeline import storage, config, archive    # noqa: E402
from pipeline import hurricane                   # noqa: E402

NBH_BULLETIN = """\
 K00F   NBM V5.0 NBH GUIDANCE    8/21/2026  2200 UTC
 UTC  23 00 01 02 03 04 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19 20 21 22 23
 TMP  90 88 86 84 82 80 79 78 77 76 75 74 74 73 73 75 79 83 86 88 90 91 91 90 89
 KLAX   NBM V5.0 NBH GUIDANCE    8/21/2026  2200 UTC
 UTC  23 00 01 02 03 04 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19 20 21 22 23
 TSD   1  1  1  1  1  1  1  1  1  1  1  1  1  1  1  1  1  1  1  1  1  1  1  1  1
 TMP  78 77 76 74 73 71 71 71 70 70 69 69 69 69 69 70 73 75 78 80 79 79 79 79 78
 KLAXX  NBM V5.0 NBH GUIDANCE    8/21/2026  2200 UTC
 UTC  23 00 01
 TMP  50 50 50
"""

LAMP_BULLETIN = """\
 KLAX   GFS LAMP GUIDANCE   8/21/2026  2230 UTC
 UTC  23 00 01 02 03 04 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19 20 21 22 23
 TMP  79 78 76 74 73 72 71 71 71 71 71 71 70 70 69 70 72 74 77 79 80 79 79 79 78
 DPT  62 62 62 62 62 62 62 62 62 62 62 62 62 62 62 62 62 62 62 62 62 62 62 62 62
 KPHX   GFS LAMP GUIDANCE   8/21/2026  2230 UTC
 UTC  23 00 01 02 03 04 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19 20 21 22 23
 TMP 108107105103101 99 97 95 93 92 91 90 89 88 87 90 95 99102105107108109109108
"""

# the gust rows, as each family labels them: the blend prints GST with its
# spread GSD beside it, LAMP prints WGS and writes NG for no gust. Cut from the
# live 23 September 2026 cycles.
GUST_NBH = """\
 KBOS   NBM V5.0 NBH GUIDANCE    9/23/2026  1200 UTC
 UTC  13 14 15 16
 TMP  60 61 62 63
 WDR   5  5  5  5
 WSP  18 18 17 17
 GST  27 27 26 25
 GSD   4  4  4  3
"""

GUST_LAMP = """\
 KATL   GFS LAMP GUIDANCE   9/23/2026  2030 UTC
 UTC  21 22 23 00
 TMP  81 78 75 73
 WDR  09 09 08 08
 WSP  10 11 12 13
 WGS  NG NG 18 NG
"""

WINTER_BLOCK = """\
 KMSP   NBM V5.0 NBH GUIDANCE    1/15/2026  2200 UTC
 UTC  23 00 01 02 03 04 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19 20 21 22 23
 TMP  -8-10-12-13-14-15-16-17-18-18-19-19-20-20-19-17-14-11 -8 -6 -5 -5 -6 -8-10
"""

MAV_BULLETIN = """\
 KLAX   GFS MOS GUIDANCE    8/21/2026  1800 UTC
 DT /AUG  22                  /AUG  23                /AUG  24
 HR   00 03 06 09 12 15 18 21 00 03 06 09 12 15 18 21 00 03 06 12 18
 N/X              69          81          68          81       68
 TMP  77 72 71 71 70 72 78 79 76 72 72 70 70 72 78 78 76 72 71 69 80
 KSFO   GFS MOS GUIDANCE    8/21/2026  1800 UTC
 HR   00 03 06
 TMP  60 59 58
"""


class Parsers(unittest.TestCase):
    def test_station_blocks_exact_token(self):
        blocks = gw.station_blocks(NBH_BULLETIN, ["KLAX", "K00F"], gw.BULLETIN_MARKER["nbh"])
        self.assertEqual(set(blocks), {"KLAX", "K00F"})
        self.assertTrue(blocks["KLAX"].startswith(" KLAX   NBM V5.0 NBH GUIDANCE"))
        self.assertNotIn("KLAXX", blocks["KLAX"])          # prefix is not a match
        self.assertEqual(blocks["KLAX"].count("\n"), 3)     # header + 3 rows

    def test_parse_hourly_block_nbh(self):
        block = gw.station_blocks(NBH_BULLETIN, ["KLAX"], gw.BULLETIN_MARKER["nbh"])["KLAX"]
        p = gw.parse_hourly_block(block)
        self.assertEqual(p["cycle"], dt.datetime(2026, 8, 21, 22, 0, tzinfo=dt.timezone.utc))
        self.assertEqual(len(p["rows"]), 25)
        self.assertEqual(p["rows"][0], {"time": dt.datetime(2026, 8, 21, 23, tzinfo=dt.timezone.utc), "temp_f": 78.0})
        self.assertEqual(p["rows"][1]["time"], dt.datetime(2026, 8, 22, 0, tzinfo=dt.timezone.utc))  # day rolled
        self.assertEqual(p["rows"][-1], {"time": dt.datetime(2026, 8, 22, 23, tzinfo=dt.timezone.utc), "temp_f": 78.0})
        self.assertEqual(max(r["temp_f"] for r in p["rows"]), 80.0)

    def test_parse_hourly_block_lamp_and_triple_digits(self):
        blocks = gw.station_blocks(LAMP_BULLETIN, ["KLAX", "KPHX"], gw.BULLETIN_MARKER["lamp"])
        lax = gw.parse_lamp_block(blocks["KLAX"])
        self.assertEqual(lax["cycle"].minute, 30)
        self.assertEqual([r["temp_f"] for r in lax["rows"]][:3], [79.0, 78.0, 76.0])
        phx = gw.parse_lamp_block(blocks["KPHX"])
        # three-digit cells touch their neighbours; slicing by position keeps them apart
        self.assertEqual([r["temp_f"] for r in phx["rows"]][:4], [108.0, 107.0, 105.0, 103.0])
        self.assertEqual(max(r["temp_f"] for r in phx["rows"]), 109.0)

    def test_parse_hourly_block_negative_cells(self):
        p = gw.parse_hourly_block(WINTER_BLOCK)
        temps = [r["temp_f"] for r in p["rows"]]
        self.assertEqual(temps[:5], [-8.0, -10.0, -12.0, -13.0, -14.0])
        self.assertEqual(min(temps), -20.0)
        self.assertEqual(len(temps), 25)

    def test_mav_blocks(self):
        blocks = gw.station_blocks(MAV_BULLETIN, ["KLAX", "KSFO", "KJFK"], gw.BULLETIN_MARKER["mav"])
        self.assertEqual(set(blocks), {"KLAX", "KSFO"})
        self.assertIn(" N/X", blocks["KLAX"])
        self.assertNotIn("KSFO", blocks["KLAX"])

    def test_gust_rows_by_family(self):
        """The blend's GST and its spread, and LAMP's WGS with NG for no gust."""
        blk = gw.station_blocks(GUST_NBH, ["KBOS"], "NBH GUIDANCE")["KBOS"]
        rows = gw.parse_wx_block(blk, "nbh")["rows"]
        self.assertEqual([r["wspd"] for r in rows], [18.0, 18.0, 17.0, 17.0])
        self.assertEqual([r["gust"] for r in rows], [27.0, 27.0, 26.0, 25.0])
        self.assertEqual([r["gust_sd"] for r in rows], [4.0, 4.0, 4.0, 3.0])
        blk = gw.station_blocks(GUST_LAMP, ["KATL"], "LAMP GUIDANCE")["KATL"]
        rows = gw.parse_wx_block(blk, "lamp")["rows"]
        self.assertEqual([r["gust"] for r in rows], [None, None, 18.0, None])
        self.assertIsNone(rows[0]["gust_sd"])

    def test_mav_has_no_gust_row(self):
        blk = gw.station_blocks(MAV_BULLETIN, ["KLAX"], "GFS MOS GUIDANCE")["KLAX"]
        rows = gw.parse_wx_block(blk, "mav")["rows"]
        self.assertTrue(rows)
        self.assertTrue(all(r["gust"] is None for r in rows))

    def test_body_temp_group_anchored(self):
        self.assertEqual(gw._body_temp_c("METAR KLAX 212153Z 25011KT 10SM FEW025 26/17 A2993 RMK AO2"), 26)
        self.assertEqual(gw._body_temp_c("SPECI KDEN 212228Z 36017G26KT 1/2SM TS M02/M05 A3019"), -2)
        self.assertIsNone(gw._body_temp_c("METAR KXXX 1/2SM M1/4SM A2993"))   # visibility fractions

    def test_t_group_detection(self):
        self.assertTrue(gw._T_GROUP.search("SPECI KDEN 212228Z ... RMK AO2 T03440039"))
        self.assertTrue(gw._T_GROUP.search("METAR KMSP 151253Z ... RMK AO2 T11221178 $"))
        self.assertFalse(gw._T_GROUP.search("METAR KMCO 212153Z 17005KT 10SM FEW050 33/22 A2999"))
        self.assertFalse(gw._T_GROUP.search("METAR KXXX RMK TSB26 T0"))

    def test_cli_summary_regex(self):
        m = gw._CLI_SUMMARY.search("...THE LOS ANGELES INTL AIRPORT CA CLIMATE SUMMARY FOR AUGUST 20 2026...")
        self.assertEqual(dt.datetime.strptime(m.group(1), "%B %d %Y").date(), dt.date(2026, 8, 20))


class ObservationMerge(unittest.TestCase):
    def test_merge_keeps_corrections(self):
        base = {"rows": {}}
        first = [{"icaoId": "KDEN", "obsTime": 1787351280, "rawOb": "SPECI KDEN 212228Z 36017G26KT 34/04 T03440039",
                  "receiptTime": "2026-08-21T22:32:21.973Z", "temp": 34.4}]
        merged, added, updated = archive.merge_obs_rows(base, first)
        self.assertEqual((added, updated), (1, 0))
        again, added, updated = archive.merge_obs_rows(merged, first)
        self.assertEqual((added, updated), (0, 0))            # identical row: no change
        cor = [{"icaoId": "KDEN", "obsTime": 1787351280, "rawOb": "SPECI KDEN 212228Z COR 36017G26KT 34/04 T03440039",
                "receiptTime": "2026-08-21T22:38:14.759Z", "temp": 34.4}]
        merged, added, updated = archive.merge_obs_rows(again, cor)
        self.assertEqual((added, updated), (0, 1))
        row = merged["rows"]["KDEN|1787351280"]
        self.assertIn(" COR ", row["rawOb"])
        self.assertEqual(len(row["superseded"]), 1)
        self.assertEqual(row["superseded"][0]["receiptTime"], "2026-08-21T22:32:21.973Z")
        self.assertNotIn("superseded", row["superseded"][0])

    def test_rows_without_key_are_ignored(self):
        merged, added, updated = archive.merge_obs_rows({"rows": {}}, [{"icaoId": "KLAX"}, {"obsTime": 5}])
        self.assertEqual((added, updated, merged["rows"]), (0, 0, {}))


class LocalStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = storage.LocalStorage(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_put_get_exists_list(self):
        self.assertIsNone(self.st.get("archive/KLAX/x.gz"))
        self.st.put("archive/KLAX/hourly_20260821T000000Z.json.gz", b"abc")
        self.st.put("archive/KLAX/daily_20260821T000000Z.json.gz", b"def")
        self.assertEqual(self.st.get("archive/KLAX/hourly_20260821T000000Z.json.gz"), b"abc")
        self.assertTrue(self.st.exists("archive/KLAX/daily_20260821T000000Z.json.gz"))
        self.assertEqual(self.st.list("archive/KLAX/hourly_"), ["archive/KLAX/hourly_20260821T000000Z.json.gz"])
        self.assertEqual(len(self.st.list("archive/KLAX")), 2)
        self.assertEqual(self.st.list("nothing/here"), [])

    def test_put_if_absent_is_write_once(self):
        self.assertTrue(self.st.put_if_absent("a/b", b"1"))
        self.assertFalse(self.st.put_if_absent("a/b", b"2"))
        self.assertEqual(self.st.get("a/b"), b"1")

    def test_no_escape_from_root(self):
        with self.assertRaises(ValueError):
            self.st.put("../outside", b"x")

    def test_from_config(self):
        st = storage.from_config({"storage": {"backend": "local", "root": self.tmp.name}})
        self.assertEqual(st.describe(), f"local:{os.path.abspath(self.tmp.name)}")
        with self.assertRaises(ValueError):
            storage.from_config({"storage": {"backend": "ftp"}})


class ConfigTests(unittest.TestCase):
    def test_env_overrides_and_relative_root(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"user_agent": "x/1 (a@b)", "storage": {"backend": "local", "root": "./data"}}, fh)
            path = fh.name
        try:
            os.environ["WX_USER_AGENT"] = "override/2 (c@d)"
            cfg = config.load(path)
            self.assertEqual(cfg["user_agent"], "override/2 (c@d)")
            self.assertTrue(os.path.isabs(cfg["storage"]["root"]))
            self.assertTrue(cfg["storage"]["root"].endswith("data"))
        finally:
            del os.environ["WX_USER_AGENT"]
            os.unlink(path)

    def test_site_json_loads_and_has_required_keys(self):
        cfg = config.load()
        for k in ("user_agent", "storage", "cadence_minutes", "sources", "market_overlay", "disclosure"):
            self.assertIn(k, cfg)
        self.assertNotIn("set user_agent", cfg["user_agent"])


class CycleStamp(unittest.TestCase):
    def test_update_time_wins_over_generated(self):
        self.assertEqual(archive.cycle_stamp({"updateTime": "2026-08-21T19:52:18+00:00",
                                              "generatedAt": "2026-08-21T22:20:08+00:00"}),
                         "20260821T195218Z")
        self.assertEqual(archive.cycle_stamp({"updateTime": "2026-08-21T12:52:18-07:00"}), "20260821T195218Z")
        self.assertIsNone(archive.cycle_stamp({"generatedAt": "2026-08-21T22:20:08+00:00"}))



class GeometryFreshness(unittest.TestCase):
    """Whether the NHC cone package on hand still belongs to the advisory the
    roster is on. Two apart is a service that has stopped issuing; one apart is
    one that is catching up, and its cone is still the current forecast."""

    def test_a_package_two_or_more_advisories_back_is_stale(self):
        # Edouard: the roster reached 12 while the package stayed at its landfall
        self.assertTrue(hurricane.geometry_is_stale("6A", "012"))
        self.assertTrue(hurricane.geometry_is_stale("27", "029"))

    def test_one_advisory_back_is_the_normal_publishing_lag(self):
        # Lowell: a running category three, its package minutes behind the advisory
        self.assertFalse(hurricane.geometry_is_stale("28", "029"))

    def test_in_step_is_not_stale_however_the_number_is_written(self):
        self.assertFalse(hurricane.geometry_is_stale("29", "029"))
        self.assertFalse(hurricane.geometry_is_stale("10", "010"))
        self.assertFalse(hurricane.geometry_is_stale("6A", "6A"))

    def test_an_intermediate_advisory_counts_as_its_own_number(self):
        # 12A follows 12, so a package at 12A is level with a roster on 12
        self.assertFalse(hurricane.geometry_is_stale("12A", "012"))
        self.assertFalse(hurricane.geometry_is_stale("12", "012A"))

    def test_nothing_to_compare_is_never_called_stale(self):
        for a, b in ((None, "012"), ("12", None), ("", "012"), ("A", "012"), (None, None)):
            self.assertFalse(hurricane.geometry_is_stale(a, b), (a, b))

if __name__ == "__main__":
    unittest.main()


class UnregisteredWeather(unittest.TestCase):
    """The site's own listing alarm. The registry is hand-kept, so the exchange
    can open a weather contract this site has no row for, and the catalogue pass
    is the only place that sees both lists at once."""

    def setUp(self):
        from pipeline import catalogue
        self.fn = catalogue.unregistered_weather

    def test_it_names_a_weather_product_the_registry_lacks(self):
        listed = {"MGACK": {"name": "Nantucket Max Wind Speed"},
                  "UHATL": {"name": "Atlanta Daily Temperature High"}}
        self.assertEqual(self.fn(listed, {"UHATL"}), ["MGACK"])

    def test_the_ticker_shape_is_not_the_test(self):
        # both of these match a weather family's pattern and neither is weather:
        # DASHO matches [USD][HLA][A-Z]{3} and SHNOR matches the SH series
        listed = {"DASHO": {"name": "DoorDash Orders"},
                  "SHNOR": {"name": "Hormuz Return to Normal"}}
        self.assertEqual(self.fn(listed, set()), [])

    def test_nothing_to_say_when_the_registry_covers_the_board(self):
        listed = {"UHATL": {"name": "Atlanta Daily Temperature High"},
                  "HRULGA": {"name": "New York City Hourly Temperature"}}
        self.assertEqual(self.fn(listed, {"UHATL", "HRULGA"}), [])

    def test_every_family_the_site_draws_is_covered(self):
        listed = {"UHXXX": {"name": "Nowhere Daily Temperature High"},
                  "HRUXXX": {"name": "Nowhere Hourly Temperature"},
                  "MGXXX": {"name": "Nowhere Max Wind Speed"},
                  "AWXXX": {"name": "Nowhere Average Wind Speed"}}
        self.assertEqual(self.fn(listed, set()), ["AWXXX", "HRUXXX", "MGXXX", "UHXXX"])


class DeskPoolFigures(unittest.TestCase):
    """The desk's pool figure, delivered rather than derived. The site computes
    none of its own; this reads what the desk publishes and hands it to the
    page, which shows it only while the exchange has no book."""

    def fn(self, body):
        from pipeline import reask
        return reask.desk_pool_figures(body)

    def test_the_feed_is_https_only_and_off_without_a_url(self):
        from pipeline import reask
        self.assertIsNone(reask.fetch_desk_lhl(""))
        with self.assertRaises(ValueError):
            reask.fetch_desk_lhl("http://example.invalid/lhl.json")

    def test_a_storm_is_keyed_by_name_and_year(self):
        body = json.dumps({"schema": 1, "asof": "2026-09-24T10:15:37Z", "storms": [
            {"name": "Nolo", "year": 2026, "cycle": "2026092400", "method": "argmax",
             "pwin": {"HL": 45.5, "KH": 27.7}}]}).encode()
        got = self.fn(body)
        self.assertEqual(sorted(got), [("nolo", 2026)])
        self.assertEqual(got[("nolo", 2026)]["pwin"], {"HL": 45.5, "KH": 27.7})
        self.assertEqual(got[("nolo", 2026)]["cycle"], "2026092400")
        self.assertEqual(got[("nolo", 2026)]["asof"], "2026-09-24T10:15:37Z")

    def test_location_ids_are_upper_cased_to_match_the_registry(self):
        body = json.dumps({"storms": [{"name": "Nolo", "year": 2026, "pwin": {"hl": 45.5}}]}).encode()
        self.assertEqual(self.fn(body)[("nolo", 2026)]["pwin"], {"HL": 45.5})

    def test_one_bad_row_does_not_take_the_storm_with_it(self):
        body = json.dumps({"storms": [{"name": "Nolo", "year": 2026,
                                       "pwin": {"HL": 45.5, "KH": "n/a", "HN": True}}]}).encode()
        self.assertEqual(self.fn(body)[("nolo", 2026)]["pwin"], {"HL": 45.5})

    def test_a_storm_with_nothing_usable_is_absent(self):
        body = json.dumps({"storms": [{"name": "Nolo", "year": 2026, "pwin": {}},
                                      {"name": "", "year": 2026, "pwin": {"HL": 1.0}},
                                      {"name": "Fay", "year": 2026, "pwin": {"BR": "x"}}]}).encode()
        self.assertEqual(self.fn(body), {})

    def test_the_desks_own_sentence_is_carried_not_written_here(self):
        body = json.dumps({"storms": [{"name": "Nolo", "year": 2026, "pwin": {"HL": 52.6},
                                       "method": "argmax, distance-weighted sub-60",
                                       "note": "below 60 mph the vendor publishes nothing"}]}).encode()
        got = self.fn(body)[("nolo", 2026)]
        self.assertEqual(got["note"], "below 60 mph the vendor publishes nothing")
        self.assertEqual(got["method"], "argmax, distance-weighted sub-60")

    def test_no_file_and_a_broken_file_are_both_an_absence(self):
        self.assertEqual(self.fn(None), {})
        self.assertEqual(self.fn(b"{not json"), {})
