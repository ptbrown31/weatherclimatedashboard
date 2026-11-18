"""A day's wait, then URMA, in the analysis lane (Patrick's ruling of 17
November 2026, "Wait a day, then URMA"; Patrick's choice of 18 November that
this lane follow the engine): from WAIT_FROM, a temperature hour with no value
at a cut-off, a day's or an hourly contract's, waits a day for RTMA, then takes
URMA at the same grid cell, or else has no value.

The cases are the engine's (its W3 and the wait's end), each planted here in
this lane's own store and units, kelvin in the copies and whole degrees
Fahrenheit shown. The boundary the two outcomes share is the hour with nothing
in hand when its cut-off settles:
- OUR GAP: a copy on its server by the cut-off but found after the hour was
  fixed. The lane's record has a hole, so the copy cannot be known to be the
  first published, and the hour is undecided with an alarm.
- NCEP'S DELAY: a copy NOAA published after the cut-off. The hour waits, then
  takes it.
Each is shown against the reading that would give the other's answer, and the
rule before (WAIT_FROM moved out of reach). No network: copies are planted in a
local store."""
import datetime as dt
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pipeline import analysis, storage   # noqa: E402

UTC = dt.timezone.utc
NY = next(l for l in analysis.load_locations() if l["id"] == "new-york-ny")
DAY = "2026-11-20"                   # the wait's first day
EARLIER = "2026-11-19"               # the day before, by the first-published rule alone
H = 12                               # the planted hour, noon in New York
M = dt.timedelta(minutes=1)
HR = dt.timedelta(hours=1)


def iso(t):
    return analysis._iso(t)


def k(n):
    """Every other hour's temperature, a tenth of a kelvin warmer each hour (the
    maximum 282.3 K at 11 PM, 48.47 F)."""
    return round(280.0 + 0.1 * n, 4)


def half(kelvin):
    return analysis.half_up(analysis.exact("temp", kelvin), 1)


def rtma(server, t, temp, seen, published):
    return {"server": server, "kind": "anl", "valid": iso(t), "seen": iso(seen),
            "published": None if published is None else iso(published),
            "sha256": {v: f"{server}-{temp}-{v}" for v in ("temp", "wind", "gust")},
            "values": {NY["id"]: {"temp": temp, "wind": 3.0, "gust": 5.0}}, "decoded": ["temp", "wind", "gust"]}


def urma(server, t, temp, seen, published):
    return {"server": server, "kind": analysis.URMA, "product": analysis.URMA, "valid": iso(t), "seen": iso(seen),
            "published": iso(published), "sha256": {"temp": f"urma-{server}-{temp}"},
            "values": {NY["id"]: {"temp": temp}}, "decoded": ["temp"]}


def cut_of(day=DAY):
    return analysis.day_cutoff(day, NY["tz"])


def hour_of(n=H, day=DAY):
    return analysis.local_day_hours(day, NY["tz"])[n]


class Store(unittest.TestCase):
    """A day of New York's hours planted as the lane keeps and settles them:
    every hour Open Data's copy, published :49 and found :50, the planted hour
    as each case gives it, and each hour's cut-offs settled at the first pass
    after them, on the copies in hand then."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = storage.LocalStorage(self.tmp.name)

    def put(self, t, c, kind="anl"):
        analysis.write_copy(self.st, "rtma", kind, c["server"], t, c, {})

    def plant(self, day=DAY, hour=H, skip=()):
        for n, h in enumerate(analysis.local_day_hours(day, NY["tz"])):
            if n != hour and n not in skip:
                self.put(h, rtma("nodd", h, k(n), h + 50 * M, h + 49 * M))
            slots = {}
            analysis.settle_cuts(self.st, "rtma", "anl", h, slots, [NY], cut_of(day) + 5 * M, {}, True)

    def build(self, now, day=DAY):
        day_doc, loc_docs = analysis.build_day(self.st, day, [NY], {}, now, {}, times={})
        return day_doc["locations"][NY["id"]]["rtma"], loc_docs[NY["id"]]["hours"]

    def row(self, now, n=H, day=DAY):
        return self.build(now, day)[1][n]["rtma"]


# ------------------------------------------------------------------ the boundary, its two outcomes
class Boundary(Store):

    def case_a(self):
        """OUR GAP: Open Data's copy was on its server at :49, by the cut-off, but
        found only 30 minutes after the cut-off, the hour fixed with nothing."""
        self.plant()
        h = hour_of()
        self.put(h, rtma("nodd", h, 290.0, cut_of() + 30 * M, h + 49 * M))
        r = self.row(cut_of() + 2 * HR)
        return (r.get("hourly", {}).get("copyState") if r.get("undecided") is None else "undecided",
                r.get("temp"), bool(r.get("alarm")))

    def case_b(self):
        """NCEP'S DELAY: NOAA published the hour three hours after the cut-off,
        found a minute later."""
        self.plant()
        before = self.row(cut_of() + 2 * HR)
        h, pub = hour_of(), cut_of() + 3 * HR
        self.put(h, rtma("nodd", h, 290.0, pub + M, pub))
        after = self.row(cut_of() + 4 * HR)
        # the copy the wait took is no late copy, though it came after the cut-off
        self.assertNotIn("lateCopy", after)
        return (before["wait"]["state"], after["wait"]["state"], after["show"]["temp"])

    def test_our_gap_is_undecided_with_an_alarm(self):
        self.assertEqual(self.case_a(), ("undecided", None, True))

    def test_ncep_delay_waits_then_takes_the_copy(self):
        self.assertEqual(self.case_b(), ("waiting", "primary", half(290.0)))

    def test_read_as_the_rule_before_the_delay_is_never_waited_for(self):
        with mock.patch.object(analysis, "WAIT_FROM", "9999-12-31"):
            self.plant()
            h, pub = hour_of(), cut_of() + 3 * HR
            self.put(h, rtma("nodd", h, 290.0, pub + M, pub))
            r = self.row(cut_of() + 4 * HR)
        # the copy is recorded beside the row and never used, as before, and the hour has no value
        self.assertNotIn("wait", r)
        self.assertNotIn("temp", r)
        self.assertEqual(r["anlFile"]["nodd"]["seen"], iso(cut_of() + 3 * HR + M))
        self.assertEqual((r["hourly"]["copyState"], r["hourly"]["state"]), ("missing", "review"))
        self.assertNotIn("wait", r["hourly"])

    def test_read_as_a_delay_the_gap_s_copy_would_count(self):
        # the planted fault: the wait taking any copy by its end, the freeze's alarm off
        with mock.patch.object(analysis, "LATE_FIRST", False), \
                mock.patch.object(analysis, "FIX_AFTER", dt.timedelta(days=9)):
            self.plant()
            h = hour_of()
            self.put(h, rtma("nodd", h, 290.0, cut_of() + 30 * M, h + 49 * M))
            r = self.row(cut_of() + 2 * HR)
        self.assertNotIn("undecided", r)
        self.assertFalse(r.get("alarm"))


# ------------------------------------------------------------------ the wait, URMA and missing
class Daily(Store):

    def test_a_copy_in_hand_with_no_temperature_here_never_stands_in_the_wait_s_way(self):
        # Open Data's copy, published :49 by the cut-off, gives New York no temperature; NOMADS's, published three
        # hours after the cut-off, does. Only a copy published after the cut-off is the wait's, so NOMADS's counts
        h, pub = hour_of(), cut_of() + 3 * HR
        self.put(h, rtma("nodd", h, None, h + 50 * M, h + 49 * M))
        self.plant()                                      # the hour settles with Open Data's copy in hand
        self.put(h, rtma("nomads", h, 290.0, pub + M, pub))
        r = self.row(cut_of() + 4 * HR)
        self.assertEqual((r["wait"]["state"], r["wait"]["src"], r["show"]["temp"]), ("primary", "nomads", half(290.0)))

    def test_a_copy_published_just_after_the_cut_off_and_found_before_the_fix_is_the_wait_s_for_its_temperature(self):
        # published two minutes after the cut-off and found two minutes later, before the pass that fixes the hour:
        # the engine's fix holds it and its choice, made at the cut-off, does not, so the wait takes it. Its
        # temperature counts and its wind and gust, which do not wait, do not
        h, pub = hour_of(), cut_of() + 2 * M
        self.put(h, rtma("nodd", h, 295.0, pub + 2 * M, pub))
        self.plant()                                      # the hour settles at cut-off + 5 min with the copy in hand
        s, rows = self.build(cut_of() + 2 * HR)
        r = rows[H]["rtma"]
        self.assertEqual((r["wait"]["state"], r["show"]["temp"], r.get("wind"), r.get("late")), ("primary", half(295.0), None, None))
        self.assertEqual((s["final"], s["high"]["value"], s["hours"]), (True, half(295.0), 24))
        self.assertEqual(s["wind"]["value"], 7)                          # 3 m/s at the other 23 hours

    def test_a_copy_found_one_pass_after_its_publication_during_the_wait_is_no_gap(self):
        # the engine's planted case: the ten-minute pass, started 40 seconds late, finds the copy 10 min 40 s
        # after its server had it, which is no gap in the lane's record
        self.plant()
        h, pub = hour_of(), cut_of() + 3 * HR
        self.put(h, rtma("nodd", h, 290.0, pub + 10 * M + dt.timedelta(seconds=40), pub))
        r = self.row(cut_of() + 4 * HR)
        self.assertEqual((r["wait"]["state"], r["show"]["temp"]), ("primary", half(290.0)))
        self.assertNotIn("alarm", r)

    def test_a_waiting_hour_holds_the_day_waiting(self):
        self.plant()
        s, rows = self.build(cut_of() + 2 * HR)
        r = rows[H]["rtma"]
        self.assertEqual((s["waiting"], s["final"], s["review"]), (["high", "low"], False, False))
        self.assertEqual(s["waitUntil"], iso(cut_of() + analysis.WAIT))
        self.assertEqual(r["wait"], {"until": iso(cut_of() + analysis.WAIT), "state": "waiting", "product": None})
        self.assertNotIn("temp", r)
        # the wind, gust and precipitation do not wait: the hour is simply missing for them
        self.assertEqual(s["wind"]["value"], 7)

    def test_no_rtma_in_the_wait_takes_urma_at_its_end(self):
        self.plant()
        h = hour_of()
        self.put(h, urma("nodd", h, 285.0, h + 7 * HR + M, h + 7 * HR), analysis.URMA)
        self.assertEqual(self.row(cut_of() + 23 * HR)["wait"]["state"], "waiting")      # URMA only at the end
        s, rows = self.build(cut_of() + 25 * HR)
        r = rows[H]["rtma"]
        self.assertEqual((r["wait"]["state"], r["wait"]["product"], r["wait"]["src"]), ("urma", "urma", "nodd"))
        self.assertEqual(r["show"]["temp"], half(285.0))
        self.assertEqual((s["final"], s["high"]["value"]), (True, 53))                  # 53.33 F
        self.assertNotIn("waiting", s)

    def test_neither_rtma_nor_urma_is_missing_after_the_wait(self):
        self.plant()
        s, rows = self.build(cut_of() + 25 * HR)
        r = rows[H]["rtma"]
        self.assertEqual(r["wait"]["state"], "missing")
        self.assertEqual((s["final"], s["high"]["value"]), (True, 48))                  # the other hours' maximum
        self.assertEqual(s["hours"], 23)

    def test_a_copy_seen_long_after_its_server_had_it_inside_the_wait_is_a_gap(self):
        self.plant()
        h, pub = hour_of(), cut_of() + 3 * HR
        self.put(h, rtma("nodd", h, 290.0, pub + 2 * HR, pub))
        r = self.row(cut_of() + 6 * HR)
        self.assertEqual(r["wait"]["state"], "undecided")
        self.assertIn("gap in this lane's record", r["alarm"])
        self.assertEqual([c["show"] for c in r["undecided"]["temp"]], [None, half(290.0)])

    def test_a_daily_hour_whose_urma_came_after_the_wait_stays_missing_and_the_day_holds(self):
        self.plant()
        end, h = cut_of() + analysis.WAIT, hour_of()
        before, _ = self.build(end + HR)
        self.put(h, urma("nodd", h, 290.0, end + 2 * HR + M, end + 2 * HR), analysis.URMA)
        s, rows = self.build(end + 3 * HR)
        r = rows[H]["rtma"]
        self.assertEqual((before["final"], before["high"]["value"]), (True, 48))
        self.assertEqual((s["final"], s["high"]["value"], r["wait"]["state"]), (True, 48, "missing"))
        self.assertNotIn("temp", r)

    def test_a_daily_day_waits_until_the_fix_and_settles_once(self):
        self.plant()
        end, h = cut_of() + analysis.WAIT, hour_of()
        self.put(h, urma("nodd", h, 285.0, h + 7 * HR + M, h + 7 * HR), analysis.URMA)
        at_end, rows_end = self.build(end + M)
        fixed, rows_fixed = self.build(end + analysis.FIX_AFTER + M)
        self.assertEqual((at_end["final"], rows_end[H]["rtma"]["wait"]["state"]), (False, "waiting"))
        self.assertEqual((fixed["final"], fixed["high"]["value"], rows_fixed[H]["rtma"]["wait"]["state"]),
                         (True, 53, "urma"))
        self.assertEqual(rows_end[H]["rtma"]["wait"]["until"], iso(end))

    def test_wind_and_the_day_before_do_not_wait(self):
        self.plant(day=EARLIER)
        s, rows = self.build(cut_of(EARLIER) + 2 * HR, day=EARLIER)
        self.assertNotIn("waiting", s)
        self.assertEqual(s["final"], True)
        self.assertNotIn("wait", rows[H]["rtma"] or {})

    def test_a_day_long_outage_waits_rather_than_going_to_review(self):
        self.plant(skip=set(range(24)))
        s, _ = self.build(cut_of() + 2 * HR)
        self.assertEqual((s["final"], s["review"], s["waiting"]), (False, False, ["high", "low"]))

    def test_the_clocks_back_day_waits_each_one_am_apart(self):
        day = "2026-11-01"
        with mock.patch.object(analysis, "WAIT_FROM", day), mock.patch.object(analysis, "FIRST_FROM_DAY", day):
            hours = analysis.local_day_hours(day, NY["tz"])
            ones = [n for n, h in enumerate(hours) if h.astimezone(analysis.ZoneInfo(NY["tz"])).hour == 1]
            self.assertEqual(len(ones), 2)                                 # 1 AM EDT and 1 AM EST
            self.plant(day=day, hour=-1, skip=set(ones))
            for i, n in enumerate(ones):
                h = hours[n]
                self.put(h, urma("nodd", h, 270.0 + 0.01 * i, h + 7 * HR + M, h + 7 * HR), analysis.URMA)
            s, rows = self.build(cut_of(day) + 25 * HR, day=day)
        self.assertEqual([rows[n]["rtma"]["wait"]["state"] for n in ones], ["urma", "urma"])
        self.assertEqual([rows[n]["rtma"]["x"]["temp"] for n in ones],
                         [analysis.x_text("temp", 270.0), analysis.x_text("temp", 270.01)])
        self.assertEqual(s["low"]["value"], half(270.0))


# ------------------------------------------------------------------ the hourly contracts
class Hourly(Store):

    def hourly(self, now, n=H):
        return self.row(now, n)["hourly"]

    def test_an_hourly_contract_waits_then_takes_rtma(self):
        self.plant()
        t = hour_of()
        w = self.hourly(t + 2 * HR)
        self.assertEqual((w["state"], w["copyState"], w["at"], w["wait"]["state"]),
                         ("provisional", "waiting", None, "waiting"))
        pub = t + 3 * HR
        self.put(t, rtma("nodd", t, 290.0, pub + M, pub))
        got = self.hourly(t + 4 * HR)
        self.assertEqual((got["state"], got["copyState"], got["at"], got["value"], got["src"]),
                         ("final", "primary", iso(pub), half(290.0), "nodd"))
        self.assertFalse(got["late"])

    def test_an_hourly_contract_takes_urma_once_the_wait_s_end_is_fixed(self):
        self.plant()
        t = hour_of()
        cut = t + analysis.HOURLY_CUT_AFTER
        self.put(t, urma("nodd", t, 285.0, t + 7 * HR + M, t + 7 * HR), analysis.URMA)
        got = self.hourly(cut + analysis.WAIT + M)
        self.assertEqual((got["state"], got["copyState"]), ("provisional", "waiting"))
        self.assertTrue(got["why"].startswith("the wait ended at "))
        got = self.hourly(cut + analysis.WAIT + analysis.FIX_AFTER + M)
        self.assertEqual((got["state"], got["copyState"], got["value"], got["wait"]["product"]),
                         ("final", "urma", half(285.0), "urma"))

    def test_an_hourly_contract_with_neither_is_missing_and_under_review(self):
        self.plant()
        t = hour_of()
        cut = t + analysis.HOURLY_CUT_AFTER
        got = self.hourly(cut + analysis.WAIT + analysis.FIX_AFTER + M)
        self.assertEqual((got["state"], got["copyState"], got["at"]), ("review", "missing", iso(cut + analysis.WAIT)))
        self.assertEqual(got["why"], "no RTMA value within a day of the cut-off, and no URMA value")

    def test_an_hourly_contract_waits_into_the_next_contract_date(self):
        self.plant(hour=23)
        t = hour_of(23)
        got = self.hourly(t + 3 * HR, 23)
        self.assertEqual(got["copyState"], "waiting")
        self.assertEqual(got["wait"]["until"], iso(t + analysis.HOURLY_CUT_AFTER + analysis.WAIT))
        self.assertTrue(got["wait"]["until"].startswith("2026-11-22"))

    def test_an_hourly_whose_urma_came_after_the_wait_stays_in_review(self):
        self.plant()
        t = hour_of()
        end = t + analysis.HOURLY_CUT_AFTER + analysis.WAIT
        self.put(t, urma("nodd", t, 290.0, end + 2 * HR + M, end + 2 * HR), analysis.URMA)
        got = self.hourly(end + 3 * HR)
        self.assertEqual((got["state"], got["copyState"], got["value"]), ("review", "missing", None))
        self.assertEqual(got["why"], "no RTMA value within a day of the cut-off, and URMA's copy was published "
                                     "after the wait's end (nodd)")

    def test_rtma_published_in_the_wait_but_found_after_its_end_is_taken_and_never_urma_first(self):
        self.plant()
        t = hour_of()
        end = t + analysis.HOURLY_CUT_AFTER + analysis.WAIT
        self.put(t, urma("nodd", t, 285.0, t + 7 * HR + M, t + 7 * HR), analysis.URMA)
        first = self.hourly(end + M)                                  # RTMA's copy not yet found
        self.put(t, rtma("nodd", t, 290.0, end + 2 * M, end - M))
        then = self.hourly(end + 3 * M)
        self.assertEqual((first["state"], first["copyState"], first["value"]), ("provisional", "waiting", None))
        self.assertEqual((then["state"], then["copyState"], then["value"]), ("final", "primary", half(290.0)))

    def test_rtma_published_a_second_before_the_wait_s_end_and_found_at_the_next_pass_never_follows_urma(self):
        # the engine's planted case: with URMA in hand, RTMA published a second before the wait's end is found at
        # the next pass, 10 min 30 s after the end. The hour waits through that pass and takes RTMA, so no row is
        # shown final on URMA and then moved
        self.plant()
        t = hour_of()
        end = t + analysis.HOURLY_CUT_AFTER + analysis.WAIT
        self.put(t, urma("nodd", t, 285.0, t + 7 * HR + M, t + 7 * HR), analysis.URMA)
        lm, seen = end - dt.timedelta(seconds=1), end + 10 * M + dt.timedelta(seconds=30)
        before = self.hourly(end + 10 * M)
        self.put(t, rtma("nodd", t, 290.0, seen, lm))
        after = self.hourly(seen + M)
        self.assertEqual((before["state"], before["copyState"]), ("provisional", "waiting"))
        self.assertEqual((after["state"], after["copyState"], after["value"]), ("final", "primary", half(290.0)))
        self.assertNotIn("alarm", after)

    def test_a_urma_copy_published_by_the_end_but_found_after_the_fix_is_never_used_silently(self):
        self.plant()
        t = hour_of()
        end = t + analysis.HOURLY_CUT_AFTER + analysis.WAIT
        self.put(t, urma("nodd", t, 285.0, end + 20 * M, end - 30 * M), analysis.URMA)
        got = self.hourly(end + 30 * M)
        self.assertEqual(got["copyState"], "undecided")
        self.assertIn("gap in this lane's record", got["alarm"])
        self.assertEqual([(c["src"], c["value"]) for c in got["candidates"]], [(None, None), ("nodd", half(285.0))])
        self.assertEqual(got["state"], "review")


# ------------------------------------------------------------------ the reader
class Reader(unittest.TestCase):

    def test_an_hour_is_asked_for_every_pass_only_while_the_wait_may_need_it(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        st = storage.LocalStorage(tmp.name)
        t = hour_of()
        self.assertTrue(analysis.wait_hour(st, "rtma", t, [NY], {}))
        analysis.write_copy(st, "rtma", "anl", "nodd", t, rtma("nodd", t, 285.0, t + 50 * M, t + 49 * M), {})
        self.assertFalse(analysis.wait_hour(st, "rtma", t, [NY], {}))
        old = hour_of(day=EARLIER)
        self.assertFalse(analysis.wait_hour(st, "rtma", old, [NY], {}))
        # every pass while the wait may need the hour, and past LATE_END once an hour again
        now = t + 3 * HR + 10 * M
        self.assertTrue(analysis._ask("nodd", {"tried": iso(now - 10 * M)}, t, now, False, (), True))
        self.assertFalse(analysis._ask("nodd", {"tried": iso(now - 10 * M)}, t, now, False, (), False))
        late = t + analysis.LATE_END + 10 * M
        self.assertFalse(analysis._ask("nodd", {"tried": iso(late - 10 * M)}, t, late, False, (), True))

    def test_the_wait_s_last_end_falls_inside_late_end(self):
        # a 25-hour day's first hour: its day's cut-off is 25 h 45 min after it, its wait's end 24 h later
        day = "2026-11-01"
        t = analysis.local_day_hours(day, NY["tz"])[0]
        fix = analysis.day_cutoff(day, NY["tz"]) + analysis.WAIT + analysis.FIX_AFTER
        self.assertLess(fix, t + analysis.LATE_END)


if __name__ == "__main__":
    unittest.main()
