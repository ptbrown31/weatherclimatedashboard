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
        builtin, ext = scrub.needles(require_external=False)
        hits, skipped = scrub.scan(scrub.tracked_files(), builtin + ext)
        self.assertEqual(hits, [], "forbidden strings in tracked files:\n" + "\n".join(hits[:20]))

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
