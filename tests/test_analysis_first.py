"""The first-published rule in the analysis lane (the terms agreed on 17
November 2026; Patrick's choice of 18 November that this lane follow them): from local
day FIRST_FROM_DAY, the day's values and the hourly contracts of its hours
alike, where the two servers' copies of a file differ the copy published
first counts, by the servers' own Last-Modified times.

The cases are the engine's sixteen, each planted here, with a control wherever this lane's rule before decides the case
otherwise: the earliest Last-Modified and not the latest; NOMADS not
preferred regardless; publication and not which copy was found first; the
first version kept; a tie and the interval test, on both sides of it and for
the low; copies that differ elsewhere but agree at the place; a single copy;
a Last-Modified that cannot be placed; and the hourly contract, final at its
first copy found, undecided with an alarm where that copy was not the first
published. Then this lane's own: the dates it switches on, a day before them
keeping the rule it settled under, the engine's cap of twelve undecided hours,
a copy found after an hour was fixed, and the fields the page reads. No
network: copies are planted in a local store."""
import datetime as dt
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pipeline import analysis, storage   # noqa: E402

UTC = dt.timezone.utc
ALL = analysis.load_locations()
NY = next(l for l in ALL if l["id"] == "new-york-ny")
LA = next(l for l in ALL if l["id"] == "los-angeles-ca")
LOCS = [NY, LA]
DAY = "2026-11-20"                   # by the first-published rule
OLD_DAY = "2026-11-17"               # by the rule before


def iso(t):
    return analysis._iso(t)


def hours(day=DAY, tz=NY["tz"]):
    return analysis.local_day_hours(day, tz)


def at(t, minutes):
    return iso(t + dt.timedelta(minutes=minutes))


def copy(server, t, temp, seen, lm="default", other=280.0, wind=3.0, gust=5.0, sha=None):
    """A server's first copy of hour t's analysis: seen `seen` minutes after
    the hour, its Last-Modified `lm` minutes after it (a minute before it was
    seen when "default", none when None), New York's temperature `temp` in
    kelvin and Los Angeles's `other`."""
    values = {NY["id"]: {"temp": temp, "wind": wind, "gust": gust},
              LA["id"]: {"temp": other, "wind": wind, "gust": gust}}
    published = at(t, seen - 1) if lm == "default" else (None if lm is None else at(t, lm))
    shas = sha or {v: f"{server}-{temp}-{other}-{v}" for v in ("temp", "wind", "gust")}
    return {"server": server, "seen": at(t, seen), "published": published, "sha256": shas, "values": values,
            "decoded": ["temp", "wind", "gust"]}


def same(server, t, seen, lm="default", temp=281.0):
    """A copy with the same bytes as every other `same` copy of its hour."""
    return copy(server, t, temp, seen, lm, sha={v: f"same-{v}" for v in ("temp", "wind", "gust")})


class Choice(unittest.TestCase):
    """Cases 1 to 3 and 7: which copy counts where they differ."""

    def setUp(self):
        self.t = hours()[12]
        self.cut = iso(analysis.day_cutoff(DAY, NY["tz"]))

    def pick(self, nodd, nomads):
        return analysis.choose_first({"nodd": nodd, "nomads": nomads}, self.cut)

    def test_1_the_earliest_last_modified_is_used_not_the_latest(self):
        t = self.t
        ch = self.pick(copy("nodd", t, 290.0, 47, 46), copy("nomads", t, 288.0, 49, 48))
        self.assertEqual((ch["src"], ch["differ"], ch["candidates"]), ("nodd", True, None))
        self.assertEqual(analysis.choose({"nodd": copy("nodd", t, 290.0, 47, 46),
                                          "nomads": copy("nomads", t, 288.0, 49, 48)}, self.cut), "nomads")
        ch = self.pick(copy("nodd", t, 290.0, 49, 48), copy("nomads", t, 288.0, 47, 46))   # and the mirror
        self.assertEqual(ch["src"], "nomads")

    def test_2_nomads_is_not_preferred_regardless(self):
        t = self.t
        for nodd_lm, nomads_lm, want in ((46, 48, "nodd"), (48, 46, "nomads")):
            ch = self.pick(copy("nodd", t, 290.0, nodd_lm + 1, nodd_lm), copy("nomads", t, 288.0, nomads_lm + 1, nomads_lm))
            self.assertEqual(ch["src"], want)

    def test_3_the_pick_is_by_publication_not_by_which_copy_was_found_first(self):
        t = self.t
        # NOMADS's seen first (:49, published :48); Open Data's seen later (:52) but published earlier (:46)
        self.assertEqual(self.pick(copy("nodd", t, 290.0, 52, 46), copy("nomads", t, 288.0, 49, 48))["src"], "nodd")
        self.assertEqual(self.pick(copy("nodd", t, 290.0, 49, 48), copy("nomads", t, 288.0, 52, 46))["src"], "nomads")

    def test_5_copies_that_agree_take_the_first_held_and_a_tie_is_undecided(self):
        t = self.t
        ch = self.pick(same("nodd", t, 50, 49), same("nomads", t, 48, 47))
        self.assertEqual((ch["src"], ch["differ"]), ("nomads", False))
        ch = self.pick(copy("nodd", t, 290.0, 50, 49), copy("nomads", t, 288.0, 51, 49))
        self.assertIsNone(ch["src"])
        self.assertEqual(ch["candidates"], ["nodd", "nomads"])
        self.assertIn("both copies carry the Last-Modified", ch["why"])

    def test_5_copies_that_differ_elsewhere_but_agree_here_decide_the_hour(self):
        t = self.t
        ch = self.pick(copy("nodd", t, 285.0, 50, 49, other=270.0), copy("nomads", t, 285.0, 51, 49, other=275.0))
        self.assertEqual(ch["candidates"], ["nodd", "nomads"])
        src, value, cands = analysis.first_at_place(ch, {"nodd": copy("nodd", t, 285.0, 50, 49, other=270.0),
                                                         "nomads": copy("nomads", t, 285.0, 51, 49, other=275.0)},
                                                    "anl", NY["id"])
        self.assertEqual((src, value["temp"], cands), ("nodd", 285.0, None))

    def test_7_a_missing_last_modified_is_placed_at_its_finding_and_never_raises(self):
        t = self.t
        # none on record, seen :47, before NOMADS's published :49: Open Data's was first
        self.assertEqual(self.pick(copy("nodd", t, 290.0, 47, None), copy("nomads", t, 288.0, 50, 49))["src"], "nodd")
        # none on record, seen :52, after NOMADS's :49: the order cannot be decided
        ch = self.pick(copy("nodd", t, 290.0, 52, None), copy("nomads", t, 288.0, 50, 49))
        self.assertIsNone(ch["src"])
        self.assertIn("no Last-Modified on record", ch["why"])
        ch = self.pick(copy("nodd", t, 290.0, 47, None), copy("nomads", t, 288.0, 50, None))
        self.assertIn("neither copy can be placed", ch["why"])
        # equal copies need no order
        ch = self.pick(same("nodd", t, 47, None), same("nomads", t, 50, None))
        self.assertEqual((ch["src"], ch["differ"]), ("nodd", False))
        # one that cannot be read is placed the same way, and never raises
        bad = dict(copy("nodd", t, 290.0, 47), published="not a time")
        self.assertEqual(analysis.lm_time(bad), (None, "its Last-Modified 'not a time' cannot be read"))
        self.assertEqual(self.pick(bad, copy("nomads", t, 288.0, 50, 49))["src"], "nodd")

    def test_times_are_compared_in_one_form_and_one_without_a_zone_cannot_be_read(self):
        t = self.t
        a = dict(copy("nodd", t, 290.0, 50), published="2026-11-20T17:49:00+00:00")
        b = dict(copy("nomads", t, 288.0, 51), published="2026-11-20T17:49:00Z")
        ch = self.pick(a, b)                                              # one time in two forms: a tie
        self.assertEqual(ch["candidates"], ["nodd", "nomads"])
        self.assertEqual(analysis.lm_time(a), ("2026-11-20T17:49:00Z", None))
        naive = dict(copy("nodd", t, 290.0, 47), published="2026-11-20T17:46:00")
        self.assertEqual(analysis.lm_time(naive), (None, "its Last-Modified '2026-11-20T17:46:00' names no zone"))
        self.assertEqual(self.pick(naive, copy("nomads", t, 288.0, 50, 49))["src"], "nodd")   # placed at its seeing

    def test_6_a_single_copy_is_the_one_held_and_one_published_after_the_cut_off_is_later(self):
        t = self.t
        ch = self.pick(copy("nodd", t, 290.0, 50), None)
        self.assertEqual((ch["src"], ch["later"]), ("nodd", []))
        late = copy("nomads", t, 288.0, 26 * 60, 26 * 60)
        ch = analysis.choose_first({"nodd": copy("nodd", t, 290.0, 50), "nomads": late}, self.cut)
        self.assertEqual((ch["src"], ch["later"]), ("nodd", ["nomads"]))
        self.assertEqual(analysis.choose_first({}, self.cut)["why"], "on neither server by the cut-off")

    def test_a_copy_seen_before_its_last_modified_was_on_its_server_from_when_it_was_seen(self):
        # a server whose clock runs ahead: its copy, seen five minutes before the cut-off, carries a
        # Last-Modified two minutes after it, and was on the server by the cut-off all the same
        t, cut = self.t, analysis._parse_iso(self.cut)
        ahead = dict(copy("nomads", t, 288.0, 50), seen=iso(cut - dt.timedelta(minutes=5)),
                     published=iso(cut + dt.timedelta(minutes=2)))
        self.assertEqual(analysis.on_time(ahead), iso(cut - dt.timedelta(minutes=5)))
        ch = analysis.choose_first({"nodd": None, "nomads": ahead}, self.cut)
        self.assertEqual((ch["src"], ch["later"]), ("nomads", []))


class Decide(unittest.TestCase):
    """Case 5: the interval test, on day_summary's own hours."""

    def day(self, cands, temps=None, now=None, var="high"):
        """A New York day of 24 hours, each 280.0 K plus a tenth of a kelvin
        an hour, with the hours in `cands` undecided between two values."""
        hs = hours()
        ph = {}
        for n, h in enumerate(hs):
            k = (temps or {}).get(n, 280.0 + 0.1 * n)
            rec = {"temp": analysis.exact("temp", k), "wind": analysis.exact("wind", 3.0),
                   "gust": analysis.exact("gust", 5.0), "precip": None, "mm": None, "anl": True,
                   "pub": at(h, 46), "pcpPub": None}
            if n in cands:
                rec["temp"] = None
                rec["cands"] = {"temp": list(cands[n])}
            ph[iso(h)] = rec
        now = now or analysis.day_cutoff(DAY, NY["tz"]) + dt.timedelta(hours=1)
        return analysis.day_summary(ph, NY["tz"], DAY, now)

    def test_a_tie_that_cannot_reach_the_maximum_leaves_the_date_final(self):
        s = self.day({3: (280.01, 280.02)})
        self.assertEqual((s["high"]["value"], s["final"], s["split"]), (half(280.0 + 2.3), True, []))

    def test_a_tie_at_the_maximum_that_rounds_two_ways_goes_to_review(self):
        s = self.day({23: (282.3, 282.9)})                                # 48.47 F or 49.55 F
        self.assertIsNone(s["high"])
        self.assertEqual(s["split"], ["high"])

    def test_a_tie_at_the_maximum_that_rounds_alike_is_final_without_one_exact(self):
        s = self.day({23: (282.25, 282.3)})                               # 48.38 F or 48.47 F, both 48
        self.assertEqual((s["high"]["value"], s["high"]["exact"], s["inexact"]), (48, None, ["high"]))

    def test_the_interval_test_holds_for_the_low_and_inside_the_cut_off(self):
        s = self.day({0: (279.0, 279.9)})                                 # the minimum's hour: 42.53 F or 44.15 F
        self.assertIsNone(s["low"])
        self.assertIn("low", s["split"])
        s = self.day({5: (280.05, 280.2)})                                # under 280.0 nowhere: the minimum stays
        self.assertEqual(s["low"]["value"], 44)
        early = self.day({12: (282.3, 282.9)}, temps={n: 280.0 for n in range(24)},
                         now=hours()[20])                                 # inside the date: no value yet, not final
        self.assertIsNone(early["high"])
        self.assertFalse(early["final"])

    def test_a_candidate_with_no_value_drops_its_hour_from_that_combination(self):
        s = self.day({23: (None, 282.9)})                                 # without the hour 48, with it 50
        self.assertEqual(s["split"], ["high"])
        s = self.day({3: (None, 280.02)})                                 # far under the maximum either way
        self.assertEqual((s["high"]["value"], s["split"]), (half(280.0 + 2.3), []))

    def test_past_twelve_undecided_hours_a_value_goes_to_review_even_if_it_would_round_alike(self):
        s = self.day({n: (280.0, 280.001) for n in range(12)})
        self.assertIsNotNone(s["high"])
        s = self.day({n: (280.0, 280.001) for n in range(13)}, temps={n: 280.0 for n in range(24)})
        self.assertIn("high", s["split"])


def half(k):
    return analysis.half_up(analysis.exact("temp", k), 1)


class Planted(unittest.TestCase):
    """Copies planted in a local store and settled as the lane settles them."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = storage.LocalStorage(self.tmp.name)

    def put(self, t, c):
        analysis.write_copy(self.st, "rtma", "anl", c["server"], t, dict(c, kind="anl", valid=iso(t)), {})

    def settle(self, t, now, freeze=True, slots=None):
        slots = {} if slots is None else slots
        analysis.settle_cuts(self.st, "rtma", "anl", t, slots, LOCS, now, {}, freeze)
        return slots

    def view(self, t, day, now, slots=None, lid=None):
        loc = NY if lid is None else next(l for l in LOCS if l["id"] == lid)
        cut = iso(analysis.day_cutoff(day, loc["tz"]))
        return analysis.file_view(self.st, "rtma", "anl", t, loc["id"], cut, now, slots, {},
                                  analysis.first_rule_day(day))

    def hourly(self, t, now, lid=NY["id"]):
        hv = analysis.hourly_file(self.st, "rtma", t, None, {}, now, {}, True)
        return analysis.hourly_row(hv, lid)


class Hourly(Planted):
    """The hourly contract (the engine's place_hours)."""

    def setUp(self):
        super().setUp()
        self.t = hours()[12]

    def test_an_hourly_is_final_at_its_first_copy_found(self):
        t = self.t
        self.put(t, same("nodd", t, 50, 49))
        self.put(t, same("nomads", t, 53, 52))
        row = self.hourly(t, t + dt.timedelta(minutes=55))
        self.assertEqual((row["state"], row["copyState"], row["src"], row["at"], row["basis"]),
                         ("final", "primary", "nodd", at(t, 49), "first"))

    def test_open_data_alone_is_final_at_its_own_time_before_the_cut(self):
        t = self.t
        self.put(t, same("nodd", t, 50, 49))
        row = self.hourly(t, t + dt.timedelta(minutes=60))
        self.assertEqual((row["state"], row["at"]), ("final", at(t, 49)))
        # the control: an hour before the switch waits for NOMADS or the cut
        old = hours(OLD_DAY)[12]
        self.put(old, same("nodd", old, 50, 49))
        hv = analysis.hourly_file(self.st, "rtma", old, None, {}, old + dt.timedelta(minutes=60), {})
        self.assertEqual(analysis.hourly_row(hv, NY["id"])["state"], "provisional")

    def test_an_hourly_whose_first_copy_found_was_not_the_first_published_carries_an_alarm(self):
        t = self.t
        self.put(t, copy("nodd", t, 281.2, 50, 49))                     # 46.49 F: 46
        self.put(t, copy("nomads", t, 281.0, 58, 47, other=270.0))      # 46.13 F: 46, published first, found later
        row = self.hourly(t, t + dt.timedelta(minutes=60))
        self.assertEqual((row["state"], row["copyState"], row["value"], row["src"]), ("final", "undecided", 46, "nodd"))
        self.assertIn("the first copy found is not the first published", row["why"])
        self.assertTrue(row["alarm"].startswith("the NOMADS copy, first seen at " + at(t, 58)))
        self.assertEqual([c["value"] for c in row["candidates"]], [46, 46])

    def test_an_hourly_undecided_between_values_that_round_apart_is_under_review(self):
        t = self.t
        self.put(t, copy("nodd", t, 281.2, 50, 49))                     # 46
        self.put(t, copy("nomads", t, 282.0, 58, 47, other=270.0))      # 47.93 F: 48
        row = self.hourly(t, t + dt.timedelta(minutes=60))
        self.assertEqual((row["state"], row["copyState"], row["value"], row["src"]), ("review", "undecided", None, None))
        self.assertTrue(row["alarm"])

    def test_an_hourly_tie_is_undecided_without_an_alarm(self):
        t = self.t
        self.put(t, copy("nodd", t, 281.2, 50, 49))
        self.put(t, copy("nomads", t, 282.0, 51, 49, other=270.0))
        row = self.hourly(t, t + dt.timedelta(minutes=60))
        self.assertEqual((row["copyState"], row["state"]), ("undecided", "review"))
        self.assertNotIn("alarm", row)

    def test_a_first_copy_without_a_value_is_missing_and_none_by_the_cut_is_missing(self):
        t = self.t
        c = same("nodd", t, 50, 49)
        c["values"][NY["id"]]["temp"] = None
        self.put(t, c)
        row = self.hourly(t, t + dt.timedelta(minutes=55))
        self.assertEqual((row["copyState"], row["state"], row["src"]), ("missing", "review", "nodd"))
        t2 = hours()[13]
        self.assertEqual(self.hourly(t2, t2 + dt.timedelta(minutes=60))["copyState"], "pending")
        self.assertEqual(self.hourly(t2, t2 + dt.timedelta(minutes=91))[("copyState")], "missing")
        self.assertEqual(self.hourly(t2, t2 + dt.timedelta(minutes=91))["state"], "review")

    def test_a_copy_published_after_the_cut_is_recorded_and_never_used(self):
        t = self.t
        self.put(t, same("nodd", t, 50, 49))
        self.put(t, copy("nomads", t, 290.0, 200, 199))
        row = self.hourly(t, t + dt.timedelta(minutes=240))
        self.assertEqual((row["state"], row["src"], row["late"]), ("final", "nodd", True))


class Days(Planted):
    """A day's rows by each rule, the switch and a copy found after an hour was fixed."""

    def test_the_rule_switches_on_its_day_in_every_zone_and_its_hour(self):
        for loc in ALL:
            before = iso(analysis.day_cutoff("2026-11-18", loc["tz"]))
            after = iso(analysis.day_cutoff(analysis.FIRST_FROM_DAY, loc["tz"]))
            self.assertLess(before, after)
            self.assertFalse(analysis.first_rule_day("2026-11-18"))
            self.assertTrue(analysis.first_rule_day(analysis.FIRST_FROM_DAY))
        # the cut-offs of one file's rows in two zones, each with its own day
        t = dt.datetime(2026, 11, 19, 6, tzinfo=UTC)                     # 1 AM in New York, 10 PM the day before in LA
        days = analysis.cut_days("anl", t, LOCS)
        self.assertEqual(sorted(days.values()), ["2026-11-18", "2026-11-19"])

    def test_one_file_s_hourly_contract_is_read_by_each_place_day_s_rule(self):
        # 06:00 UTC on 19 November is 1 AM on the 19th in New York and 10 PM on the 18th in Los Angeles
        t = dt.datetime(2026, 11, 19, 6, tzinfo=UTC)
        self.put(t, same("nodd", t, 47, 46))
        self.put(t, same("nomads", t, 49, 48))
        self.settle(t, t + dt.timedelta(minutes=55))
        now = t + dt.timedelta(hours=30)
        _, ny = analysis.build_day(self.st, "2026-11-19", [NY], {}, now, {}, times={})
        _, la = analysis.build_day(self.st, "2026-11-18", [LA], {}, now, {}, times={})
        ny_row = next(r for r in ny[NY["id"]]["hours"] if r["t"] == iso(t))["rtma"]["hourly"]
        la_row = next(r for r in la[LA["id"]]["hours"] if r["t"] == iso(t))["rtma"]["hourly"]
        self.assertEqual((ny_row["basis"], ny_row["src"], ny_row["at"]), ("first", "nodd", at(t, 46)))
        self.assertEqual((la_row["basis"], la_row["src"], la_row["at"]), ("nomads", "nomads", at(t, 48)))

    def test_a_day_by_the_new_rule_takes_the_copy_published_first_and_one_before_keeps_nomadss(self):
        for day, want in ((DAY, "nodd"), (OLD_DAY, "nomads")):
            t = hours(day)[12]
            self.put(t, copy("nodd", t, 290.0, 52, 46))
            self.put(t, copy("nomads", t, 288.0, 49, 48))
            slots = self.settle(t, t + dt.timedelta(minutes=55))
            cut = iso(analysis.day_cutoff(day, NY["tz"]))
            self.assertEqual(slots["cutoffs"][cut]["src"], want, day)
            self.assertEqual(slots["cutoffs"][cut].get("rule"), "first" if day == DAY else None)
            v = self.view(t, day, t + dt.timedelta(hours=20))
            self.assertEqual((v["src"], v["value"]["temp"]), (want, 290.0 if want == "nodd" else 288.0))

    def test_an_undecided_file_shows_both_values_where_they_differ_and_one_where_they_agree(self):
        t = hours()[12]
        self.put(t, copy("nodd", t, 290.0, 50, 49, other=270.0))
        self.put(t, copy("nomads", t, 288.0, 51, 49, other=270.0))
        slots = self.settle(t, t + dt.timedelta(minutes=55))
        v = self.view(t, DAY, t + dt.timedelta(hours=20))
        self.assertEqual((v["src"], v["value"]["temp"], v["cands"]), (None, None, {"temp": [290.0, 288.0]}))
        self.assertIn("both copies carry the Last-Modified", v["why"])
        la = self.view(t, DAY, t + dt.timedelta(hours=20), lid=LA["id"])
        self.assertEqual((la["src"], la["value"]["temp"], la["cands"]), ("nodd", 270.0, None))
        self.assertEqual(slots["cutoffs"][iso(analysis.day_cutoff(DAY, NY["tz"]))]["candidates"], ["nodd", "nomads"])

    def test_a_copy_found_after_the_hour_was_fixed_published_first_and_differing_is_undecided_with_an_alarm(self):
        t = hours()[12]
        cut = analysis.day_cutoff(DAY, NY["tz"])
        self.put(t, copy("nodd", t, 290.0, 50, 49))
        slots = self.settle(t, cut + dt.timedelta(minutes=3))              # fixed on Open Data's alone
        self.assertEqual(slots["cutoffs"][iso(cut)]["src"], "nodd")
        # NOMADS's, seen a day later, had been on its server at :47 and differs
        self.put(t, copy("nomads", t, 288.0, 26 * 60, 47))
        v = self.view(t, DAY, cut + dt.timedelta(hours=26), slots)
        self.assertEqual((v["src"], v["cands"]), (None, {"temp": [290.0, 288.0]}))
        self.assertTrue(v["alarm"].startswith("the NOMADS copy, first seen at"))
        self.assertTrue(v["late"])

    def test_a_copy_found_after_the_hour_was_fixed_that_agrees_or_came_after_the_cut_off_changes_nothing(self):
        t = hours()[12]
        cut = analysis.day_cutoff(DAY, NY["tz"])
        self.put(t, same("nodd", t, 50, 49))
        slots = self.settle(t, cut + dt.timedelta(minutes=3))
        self.put(t, same("nomads", t, 26 * 60, 47))                       # the same bytes
        v = self.view(t, DAY, cut + dt.timedelta(hours=26), slots)
        self.assertEqual((v["src"], v["cands"], v["alarm"]), ("nodd", None, None))
        t2 = hours()[13]
        self.put(t2, copy("nodd", t2, 290.0, 50, 49))
        slots2 = self.settle(t2, cut + dt.timedelta(minutes=3))
        self.put(t2, copy("nomads", t2, 288.0, 26 * 60, 26 * 60))         # published after the cut-off
        v = self.view(t2, DAY, cut + dt.timedelta(hours=26), slots2)
        self.assertEqual((v["src"], v["value"]["temp"], v["cands"], v["alarm"], v["late"]),
                         ("nodd", 290.0, None, None, True))


class Day(Planted):
    """A whole day through build_day, with an undecided hour."""

    def test_the_day_and_its_rows_carry_the_undecided_hour_and_the_interval_test(self):
        state = {}
        for n, h in enumerate(hours()):
            if n == 23:
                self.put(h, copy("nodd", h, 282.3, 50, 49))              # 48.47 F
                self.put(h, copy("nomads", h, 282.9, 51, 49))            # 49.55 F, a tie at the maximum
            else:
                self.put(h, same("nodd", h, 50, 49, temp=280.0 + 0.05 * n))
                self.put(h, same("nomads", h, 51, 50, temp=280.0 + 0.05 * n))
            self.settle(h, h + dt.timedelta(minutes=55))
        now = analysis.day_cutoff(DAY, NY["tz"]) + dt.timedelta(hours=2)
        day_doc, loc_docs = analysis.build_day(self.st, DAY, [NY], {}, now, state, times={})
        s = day_doc["locations"][NY["id"]]["rtma"]
        self.assertEqual((s["high"], s["split"], s["final"]), (None, ["high"], True))
        self.assertEqual(s["low"]["value"], half(280.0))
        row = loc_docs[NY["id"]]["hours"][23]["rtma"]
        self.assertEqual(row["undecided"], {"temp": [{"x": analysis.x_text("temp", 282.3), "show": 48},
                                                     {"x": analysis.x_text("temp", 282.9), "show": 50}]})
        self.assertIn("both copies carry the Last-Modified", row["why"])
        self.assertIsNone(row["temp"])
        self.assertEqual(row["hourly"]["copyState"], "undecided")
        self.assertEqual(row["hourly"]["state"], "review")
        self.assertIsNone(row["runX"]["high"])
        first = loc_docs[NY["id"]]["hours"][0]["rtma"]
        self.assertEqual((first["hourly"]["copyState"], first["src"]), ("primary", "nodd"))


if __name__ == "__main__":
    unittest.main()
