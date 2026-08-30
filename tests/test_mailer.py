"""Tests for the SMTP sender and the alarm throttle. No network, no secrets."""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import handler, mailer  # noqa: E402

ENV = {"WX_SMTP_HOST": "smtp.example.com", "WX_SMTP_PORT": "587",
       "WX_SMTP_USER": "someone@example.com", "WX_SMTP_PASSWORD": "app-password",
       "WX_MAIL_TO": "", "WX_MAIL_FROM": ""}


class Settings(unittest.TestCase):
    def test_unconfigured_without_credentials(self):
        with mock.patch.dict(os.environ, {k: "" for k in ENV}, clear=False):
            self.assertFalse(mailer.configured())

    def test_recipient_and_sender_default_to_the_account(self):
        with mock.patch.dict(os.environ, ENV, clear=False):
            s = mailer.settings()
            self.assertEqual(s["to"], ["someone@example.com"])
            self.assertEqual(s["from"], "someone@example.com")
            self.assertTrue(mailer.configured(s))

    def test_recipients_split_on_commas(self):
        env = dict(ENV, WX_MAIL_TO="a@x.com, b@y.com ,c@z.com")
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(mailer.settings()["to"], ["a@x.com", "b@y.com", "c@z.com"])

    def test_send_refuses_when_unconfigured(self):
        with mock.patch.dict(os.environ, {k: "" for k in ENV}, clear=False):
            with self.assertRaises(RuntimeError):
                mailer.send("s", "b")


class Send(unittest.TestCase):
    def _sent(self, env):
        """Run send() against a fake SMTP and return the message it built."""
        captured = {}

        class FakeSMTP:
            def __init__(self, host, port, timeout=None, context=None):
                captured["host"], captured["port"] = host, port

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def ehlo(self):
                captured["ehlo"] = captured.get("ehlo", 0) + 1

            def starttls(self, context=None):
                captured["starttls"] = True

            def login(self, u, p):
                captured["login"] = (u, p)

            def send_message(self, msg):
                captured["msg"] = msg

        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(mailer.smtplib, "SMTP", FakeSMTP), \
             mock.patch.object(mailer.smtplib, "SMTP_SSL", FakeSMTP):
            mailer.send("Site visits", "line one\nline two\n")
        return captured

    def test_starttls_on_587_and_headers_are_set(self):
        c = self._sent(ENV)
        self.assertEqual((c["host"], c["port"]), ("smtp.example.com", 587))
        self.assertTrue(c["starttls"])
        self.assertEqual(c["login"], ("someone@example.com", "app-password"))
        m = c["msg"]
        self.assertEqual(m["Subject"], "Site visits")
        self.assertEqual(m["To"], "someone@example.com")
        self.assertIn("line two", m.get_content())
        self.assertTrue(m["Message-ID"])          # a message without one invites filtering

    def test_implicit_tls_on_465_does_not_starttls(self):
        c = self._sent(dict(ENV, WX_SMTP_PORT="465"))
        self.assertEqual(c["port"], 465)
        self.assertNotIn("starttls", c)


class AlarmThrottle(unittest.TestCase):
    class Store:
        def __init__(self):
            self.d = {}

        def get(self, k):
            return self.d.get(k)

        def put(self, k, body, *a):
            self.d[k] = body

    class Arch:
        FAIL_STREAK_ALARM = 3

    def test_repeat_alarms_are_held_back_but_a_new_source_gets_through(self):
        store, sent = self.Store(), []
        with mock.patch.dict(os.environ, ENV, clear=False), \
             mock.patch.object(handler, "_deliver", lambda s, b, k: sent.append(s) or True):
            handler._send_alarm(store, "obs", ["metar"], self.Arch)
            handler._send_alarm(store, "obs", ["metar"], self.Arch)   # inside the gap
            handler._send_alarm(store, "obs", ["metar", "nbm"], self.Arch)  # a different set
        self.assertEqual(len(sent), 2, sent)
        self.assertIn("metar", sent[0])
        self.assertIn("nbm", sent[1])

    def test_nothing_is_sent_without_credentials_or_alarms(self):
        store, sent = self.Store(), []
        with mock.patch.dict(os.environ, {k: "" for k in ENV}, clear=False), \
             mock.patch.object(handler, "_deliver", lambda s, b, k: sent.append(s) or True):
            handler._send_alarm(store, "obs", ["metar"], self.Arch)
        with mock.patch.dict(os.environ, ENV, clear=False), \
             mock.patch.object(handler, "_deliver", lambda s, b, k: sent.append(s) or True):
            handler._send_alarm(store, "obs", [], self.Arch)
        self.assertEqual(sent, [])

    def test_a_send_failure_does_not_record_the_gap(self):
        store = self.Store()

        def boom(*a):
            raise RuntimeError("smtp down")

        with mock.patch.dict(os.environ, ENV, clear=False), \
             mock.patch.object(handler, "_deliver", boom):
            handler._send_alarm(store, "obs", ["metar"], self.Arch)
        self.assertNotIn(handler.ALARM_STATE_KEY, store.d)   # so the next pass retries


class Delivery(unittest.TestCase):
    def test_smtp_is_preferred_and_sns_is_the_fallback(self):
        with mock.patch.dict(os.environ, ENV, clear=False), \
             mock.patch.object(mailer, "send", lambda *a, **k: None):
            self.assertTrue(handler._deliver("s", "b", "report"))
        # no credentials and no topic: nothing to do, and it says so
        with mock.patch.dict(os.environ, dict({k: "" for k in ENV}, WX_REPORT_TOPIC_ARN=""), clear=False):
            self.assertFalse(handler._deliver("s", "b", "report"))


if __name__ == "__main__":
    unittest.main()
