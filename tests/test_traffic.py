"""Counting page views out of CDN access logs. No network.

The sample lines below use CloudFront's real standard-log field order and its
real quirks: a '#Fields:' header the parser must read rather than assume, URL
encoding in the user-agent and referer columns, and '-' for absent values.
"""
import datetime as dt
import gzip
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import traffic, storage   # noqa: E402

FIELDS = ("date time x-edge-location sc-bytes c-ip cs-method cs(Host) cs-uri-stem sc-status "
          "cs(Referer) cs(User-Agent) cs-uri-query cs(Cookie) x-edge-result-type x-edge-request-id "
          "x-host-header cs-protocol cs-bytes time-taken x-forwarded-for ssl-protocol ssl-cipher "
          "x-edge-response-result-type cs-protocol-version fle-status fle-encrypted-fields c-port "
          "time-to-first-byte x-edge-detailed-result-type sc-content-type sc-content-len "
          "sc-range-start sc-range-end")
BROWSER = "Mozilla/5.0%20(Macintosh;%20Intel%20Mac%20OS%20X%2010_15_7)%20AppleWebKit/537.36"


def line(uri, ip="203.0.113.7", status="200", ref="-", agent=BROWSER, sc_bytes="4321"):
    cols = ["2026-08-23", "12:00:00", "IAD89-P2", sc_bytes, ip, "GET", "d3.cloudfront.net", uri, status,
            ref, agent, "-", "-", "Hit", "req-id", "weather.example.org", "https", "120", "0.001",
            "-", "TLSv1.3", "TLS_AES_128_GCM_SHA256", "Hit", "HTTP/2.0", "-", "-", "50000", "0.001",
            "Hit", "text/html", "4321", "-", "-"]
    return "\t".join(cols)


def logfile(*lines):
    body = "#Version: 1.0\n#Fields: " + FIELDS + "\n" + "\n".join(lines) + "\n"
    return gzip.compress(body.encode())


class ParsingTheLogFormat(unittest.TestCase):
    def test_fields_come_from_the_header_not_from_position(self):
        recs = traffic.parse_log(logfile(line("/index.html")))
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["cs-uri-stem"], "/index.html")
        self.assertEqual(recs[0]["c-ip"], "203.0.113.7")

    def test_a_file_with_no_header_yields_nothing_rather_than_guessing(self):
        body = gzip.compress((line("/index.html") + "\n").encode())
        self.assertEqual(traffic.parse_log(body), [])

    def test_a_row_with_the_wrong_column_count_is_skipped(self):
        body = "#Fields: " + FIELDS + "\n" + "too\tfew\tcolumns\n"
        self.assertEqual(traffic.parse_log(gzip.compress(body.encode())), [])

    def test_a_file_that_is_not_gzip_is_not_an_error(self):
        self.assertEqual(traffic.parse_log(b"not gzip at all"), [])


class CountingViews(unittest.TestCase):
    def test_a_page_view_is_the_document_not_its_dozen_parts(self):
        recs = traffic.parse_log(logfile(
            line("/index.html"), line("/css/site.css"), line("/js/common.js"),
            line("/js/map.js"), line("/data/snapshots/manifest.json")))
        got = traffic.summarise(recs, "2026-08-23")
        self.assertEqual(got["views"], 1)                      # not 5
        self.assertEqual(got["requests"], 5)

    def test_a_directory_request_counts_as_a_view(self):
        got = traffic.summarise(traffic.parse_log(logfile(line("/"), line("/embed/"))), "2026-08-23")
        self.assertEqual(got["views"], 2)

    def test_the_embed_is_counted_apart_from_the_site(self):
        got = traffic.summarise(traffic.parse_log(logfile(
            line("/embed/"), line("/embed/"), line("/hurricane.html"))), "2026-08-23")
        self.assertEqual(got["embedViews"], 2)
        self.assertEqual(got["siteViews"], 1)

    def test_visitors_are_distinct_addresses(self):
        got = traffic.summarise(traffic.parse_log(logfile(
            line("/index.html", ip="203.0.113.7"), line("/city.html", ip="203.0.113.7"),
            line("/index.html", ip="198.51.100.4"))), "2026-08-23")
        self.assertEqual(got["views"], 3)
        self.assertEqual(got["visitors"], 2)

    def test_bots_are_left_out_of_the_count_but_reported(self):
        got = traffic.summarise(traffic.parse_log(logfile(
            line("/index.html"),
            line("/index.html", agent="Mozilla/5.0%20(compatible;%20Googlebot/2.1)"),
            line("/index.html", agent="curl/8.4.0"))), "2026-08-23")
        self.assertEqual(got["views"], 1)
        self.assertEqual(got["botRequests"], 2)

    def test_errors_and_redirects_are_not_views(self):
        got = traffic.summarise(traffic.parse_log(logfile(
            line("/index.html", status="200"), line("/gone.html", status="404"),
            line("/index.html", status="301"))), "2026-08-23")
        self.assertEqual(got["views"], 2)                      # the 200 and the 301, not the 404

    def test_referrers_are_reduced_to_a_host(self):
        got = traffic.summarise(traffic.parse_log(logfile(
            line("/index.html", ref="https://www.interactivebrokers.com/campus/some/article?utm=x"),
            line("/index.html", ref="https://www.interactivebrokers.com/campus/other"),
            line("/index.html", ref="-"))), "2026-08-23")
        self.assertEqual(got["referrers"], [{"host": "www.interactivebrokers.com", "views": 2}])

    def test_pages_are_ranked_by_views(self):
        got = traffic.summarise(traffic.parse_log(logfile(
            line("/hurricane.html"), line("/hurricane.html"), line("/index.html"))), "2026-08-23")
        self.assertEqual(got["pages"][0], {"path": "/hurricane.html", "views": 2})


class TheJob(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.site = storage.LocalStorage(os.path.join(self.tmp.name, "site"))
        self.logs = os.path.join(self.tmp.name, "logs")
        os.makedirs(self.logs)
        self.day = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)).isoformat()

    def cfg(self, bucket=None):
        return {"storage": {"backend": "local"},
                "traffic": {"log_bucket": self.logs if bucket is None else bucket, "log_prefix": ""}}

    def write_log(self, *lines, day=None):
        name = f"E123.{day or self.day}-12.abcdef.gz"
        with open(os.path.join(self.logs, name), "wb") as f:
            f.write(logfile(*lines))

    def test_no_log_bucket_configured_is_not_a_failure(self):
        rc = traffic.traffic_pass({"storage": {"backend": "local"}, "traffic": {"log_bucket": ""}}, self.site)
        self.assertEqual(rc, 0)
        self.assertIsNone(self.site.get(traffic.SUMMARY_KEY))

    def test_a_day_with_no_logs_is_not_a_failure(self):
        self.assertEqual(traffic.traffic_pass(self.cfg(), self.site), 0)

    def test_it_reads_yesterday_and_ignores_other_days(self):
        self.write_log(line("/index.html"), line("/city.html"))
        self.write_log(line("/hurricane.html"), day="2020-01-01")
        self.assertEqual(traffic.traffic_pass(self.cfg(), self.site), 0)
        doc = json.loads(self.site.get(traffic.DAY_KEY.format(day=self.day)))
        self.assertEqual(doc["views"], 2)
        self.assertEqual(doc["day"], self.day)

    def test_the_summary_carries_the_day_and_a_total(self):
        self.write_log(line("/index.html"), line("/embed/"))
        traffic.traffic_pass(self.cfg(), self.site)
        s = json.loads(self.site.get(traffic.SUMMARY_KEY))
        self.assertEqual(s["totals"]["views"], 2)
        self.assertEqual(s["latest"]["embedViews"], 1)
        self.assertEqual([d["day"] for d in s["days"]], [self.day])

    def test_the_summary_says_what_a_view_and_a_visitor_mean(self):
        self.write_log(line("/index.html"))
        traffic.traffic_pass(self.cfg(), self.site)
        note = json.loads(self.site.get(traffic.SUMMARY_KEY))["note"]
        self.assertIn("HTML document request", note)
        self.assertIn("undercounts shared", note)

    def test_rerunning_a_day_replaces_it_rather_than_doubling_it(self):
        self.write_log(line("/index.html"))
        traffic.traffic_pass(self.cfg(), self.site)
        traffic.traffic_pass(self.cfg(), self.site)
        s = json.loads(self.site.get(traffic.SUMMARY_KEY))
        self.assertEqual(s["totals"]["views"], 1)
        self.assertEqual(len(s["days"]), 1)

    def test_the_summary_is_written_where_the_cdn_cannot_serve_it(self):
        self.write_log(line("/index.html"))
        traffic.traffic_pass(self.cfg(), self.site)
        # the bucket policy denies data/archive/* to CloudFront, so traffic
        # figures stay readable from the account and not from the public site
        self.assertTrue(traffic.SUMMARY_KEY.startswith("archive/"))
        self.assertTrue(traffic.DAY_KEY.startswith("archive/"))


if __name__ == "__main__":
    unittest.main()
