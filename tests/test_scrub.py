"""The scrub as a test: every tracked file is scanned with the built-in
needles (and the external list when present), so a credential, private key
or local path cannot reach a commit that passes the suite. The external list
is optional here because a clean clone elsewhere will not have it; the
pre-push step in DEPLOY.md requires it."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import scrub    # noqa: E402


class ScrubTrackedFiles(unittest.TestCase):
    def test_no_forbidden_strings(self):
        builtin, ext, allowed = scrub.needles(require_external=False)
        hits, skipped = scrub.scan(scrub.tracked_files(), builtin + ext, allowed)
        self.assertEqual(hits, [], "forbidden strings in tracked files:\n" + "\n".join(hits[:20]))

    def test_an_exception_covers_only_the_string_it_names(self):
        """A published string is allowed; the needle it excepts still bites.

        The risk in an exception mechanism is that it widens, so this holds it
        to the line: allowing one address at a domain must leave every other
        address at that domain caught.
        """
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "page.html")
            with open(f, "w") as fh:
                fh.write("mail ok@example.com here\nmail secret@example.com here\n")
            hits, _ = scrub.scan([f], ["@example.com"], ["ok@example.com"])
            self.assertEqual(len(hits), 1, hits)
            self.assertIn("secret@example.com", hits[0])

    def test_a_bang_line_is_an_exception_not_a_needle(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".scrub", delete=False) as fh:
            fh.write("# comment\nreal-needle\n!published-string\n")
            path = fh.name
        try:
            old, scrub.EXTERNAL = scrub.EXTERNAL, path
            builtin, ext, allowed = scrub.needles()
        finally:
            scrub.EXTERNAL = old
            os.unlink(path)
        self.assertEqual(ext, ["real-needle"])
        self.assertEqual(allowed, ["published-string"])

    def test_vendor_slots_empty(self):
        self.assertEqual(scrub.config_slots(), [])

    def test_scan_catches_a_key_header(self):
        import tempfile
        # the needle is assembled at runtime so this source file does not carry it
        needle = "BEGIN " + "PRIVATE " + "KEY"
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "deploy.pem")
            open(p, "w").write("-----" + needle + "-----\nabc\n")
            hits, _ = scrub.scan([p], scrub.BUILTIN)
            self.assertEqual(len(hits), 1)
            b = os.path.join(tmp, "blob.bin")
            open(b, "wb").write(b"\0\1\2" + needle.encode())
            hits, skipped = scrub.scan([b], scrub.BUILTIN)
            self.assertEqual((hits, len(skipped)), ([], 1))


if __name__ == "__main__":
    unittest.main()


class PortNeedlesAreHostAware(unittest.TestCase):
    """A bare-port needle is also what a JSON number looks like after its key.
    It must still catch a real internal host and must not fire on contract data
    — a strike of 10000 is a strike.

    The needles are assembled from parts rather than written out, because this
    file is itself scanned and a literal one here would be a hit."""

    P1 = ":" + "10000"
    P2 = ":" + "8443"

    def test_a_real_host_and_port_is_caught(self):
        for line in ("https://box.example.net" + self.P1 + "/", "box.example.net" + self.P1,
                     "host-1" + self.P2 + "/path", "10.0.0.4" + self.P1):
            self.assertTrue(scrub._real(self.P1, line) or scrub._real(self.P2, line), line)

    def test_a_json_number_is_not_a_port(self):
        for line in ('{"strike"' + self.P1 + ',"label":"Above 10000"}', '{"a":1,"b"' + self.P1 + '}'):
            self.assertFalse(scrub._real(self.P1, line), line)

    def test_every_other_needle_still_matches_as_written(self):
        # any needle that is not a bare port is returned as a hit unconditionally
        for n in ("some-internal-name", "-----" + "BEGIN " + "PRIVATE KEY" + "-----", "aws_" + "secret_key"):
            self.assertTrue(scrub._real(n, "prefix " + n + " suffix"), n)
        self.assertTrue(scrub._real(":x99", "not:x99"))          # not a port shape, so matched plainly

    def test_the_catalogue_samples_are_clean(self):
        # the data that first tripped this: a contract with a 10000 strike
        import glob
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        files = glob.glob(os.path.join(root, "samples", "snapshots", "catalogue", "product", "*.json"))
        self.assertTrue(files, "no catalogue samples to check")
        hits, _ = scrub.scan(files, [self.P1, self.P2])
        self.assertEqual(hits, [])
