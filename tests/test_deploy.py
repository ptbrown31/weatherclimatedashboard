"""Tests for what the site deploy can reach in the bucket.

`ops/aws/deploy.sh site` syncs the standalone build onto the bucket root with
--delete, so every key the build does not contain is removed unless an
exclusion on that sync protects it. Four prefixes live in the bucket without
being built here. data/ is the pipeline's, embed/ has a sync of its own,
papers/ holds the paper's old copy, and ndfd/ is the NDFD version of the site,
which its own script publishes. Losing an exclusion deletes that prefix at the
next deploy, so these tests read the script and fail if one goes. The owner's
decision of 3 November 2026 asks for exactly this on ndfd/.
"""
import os
import re
import shlex
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "ops", "aws", "deploy.sh")

# the prefixes the root sync must leave alone
PROTECTED = ("data/*", "embed/*", "papers/*", "ndfd/*")


def read_script():
    with open(SCRIPT) as fh:
        return fh.read()


def function_body(src, name):
    """The lines of one shell function, up to its closing brace at the start
    of a line."""
    m = re.search(r"^%s\(\) \{\n(.*?)^\}" % re.escape(name), src, re.S | re.M)
    if not m:
        raise AssertionError("deploy.sh has no function %s" % name)
    return m.group(1)


def calls(body, command):
    """Every call of one command in a function body, as its argument list."""
    return [shlex.split(line)[1:] for line in map(str.strip, body.splitlines())
            if line.startswith(command + " ")]


def excludes(args):
    return {args[i + 1] for i, a in enumerate(args[:-1]) if a == "--exclude"}


class RootSync(unittest.TestCase):
    def setUp(self):
        self.src = read_script()

    def test_the_root_sync_leaves_every_prefix_it_does_not_build(self):
        site = function_body(self.src, "phase_site")
        root = [a for a in calls(site, "upload_target") if a[1] == "s3://$bucket"]
        self.assertEqual(len(root), 1, "one upload to the bucket root")
        for prefix in PROTECTED:
            self.assertIn(prefix, excludes(root[0]), prefix)

    def test_the_exclusions_reach_the_sync_that_deletes(self):
        body = function_body(self.src, "upload_target")
        syncs = [line.strip() for line in body.splitlines() if line.strip().startswith("aws s3 sync ")]
        self.assertEqual(len(syncs), 1, syncs)
        self.assertIn("--delete", syncs[0])
        self.assertTrue(syncs[0].endswith('"$@"'), syncs[0])

    def test_every_other_upload_stays_below_its_own_prefix(self):
        site = function_body(self.src, "phase_site")
        for args in calls(site, "upload_target"):
            if args[1] != "s3://$bucket":
                self.assertTrue(args[1].startswith("s3://$bucket/"), args)
                self.assertNotIn("ndfd", args[1], args)

    def test_nothing_else_in_the_script_deletes(self):
        # one sync with --delete, the one inside upload_target, and no removal anywhere
        self.assertEqual(len(re.findall(r"aws s3 sync [^\n]*--delete", self.src)), 1)
        self.assertNotRegex(self.src, r"aws s3 (rm|rb)\b")
        self.assertNotRegex(self.src, r"s3api delete")


if __name__ == "__main__":
    unittest.main()
