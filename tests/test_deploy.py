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
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
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


class CodeGuards(unittest.TestCase):
    def test_the_code_phase_runs_the_zone_guards_before_it_packages(self):
        # The NDFD pages refuse a place whose analysis index record has no zone the browser knows, and a pass
        # writes its index whatever its lanes did, so the code phase runs the two zone tests, needs both to have
        # run, and only then packages and uploads. A guard that goes, or moves after the upload, fails here, and
        # so does a rename of either test that the phase does not follow
        body = function_body(read_script(), "phase_code")
        names = ("test_every_place_ships_a_canonical_zone",
                 "test_a_product_hour_writes_the_archive_the_frames_and_the_grid_index")
        at = [body.find("-k " + n) for n in names] + [body.find("grep -q '^Ran 2 tests'")]
        pkg, up = body.find("scripts/package_lambda.py"), body.find("aws lambda update-function-code")
        self.assertTrue(min(at) >= 0, "phase_code runs both zone tests and checks that both ran")
        self.assertTrue(max(at) < pkg < up, "the zone guards come before the packaging and the upload")
        with open(os.path.join(ROOT, "tests", "test_analysis.py")) as fh:
            src = fh.read()
        for n in names:
            self.assertIn("def " + n + "(", src, n)


class CodeGuardsRun(unittest.TestCase):
    """The code phase itself, run on a copy of the repository with a stand-in aws that records its calls, reaches
    nothing and fails. With a place's zone broken the phase refuses before any aws call, and with the zones whole it
    goes on to the stack, where the stand-in stops it. No deploy.env is copied or read."""

    def run_code_phase(self, break_zone):
        with tempfile.TemporaryDirectory() as tmp:
            copy = os.path.join(tmp, "repo")
            for d in ("pipeline", "tests", "config", "geo", "ops"):
                shutil.copytree(os.path.join(ROOT, d), os.path.join(copy, d),
                                ignore=shutil.ignore_patterns("__pycache__", "deploy.env"))
            if break_zone:
                p = os.path.join(copy, "config", "analysis_locations.json")
                with open(p) as fh:
                    locs = json.load(fh)
                locs[0].pop("tz")
                with open(p, "w") as fh:
                    json.dump(locs, fh)
            stand_in = os.path.join(tmp, "bin")
            os.makedirs(stand_in)
            log = os.path.join(tmp, "aws-calls")
            with open(os.path.join(stand_in, "aws"), "w") as fh:
                fh.write('#!/bin/sh\necho "$@" >> "%s"\nexit 1\n' % log)
            os.chmod(os.path.join(stand_in, "aws"), 0o755)
            env = dict(os.environ, PATH=stand_in + os.pathsep + os.environ.get("PATH", ""),
                       DEPLOY_ENV=os.path.join(tmp, "no.env"), PYTHONDONTWRITEBYTECODE="1")
            r = subprocess.run(["bash", os.path.join(copy, "ops", "aws", "deploy.sh"), "code"],
                               capture_output=True, text=True, env=env, timeout=600)
            calls = ""
            if os.path.exists(log):
                with open(log) as fh:
                    calls = fh.read()
            return r, calls

    def test_a_broken_zone_stops_the_code_phase_before_any_aws_call(self):
        r, calls = self.run_code_phase(True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("a zone guard failed, so nothing was packaged or uploaded", r.stderr)
        self.assertEqual(calls, "")

    def test_whole_zones_let_the_code_phase_go_on_to_the_stack(self):
        r, calls = self.run_code_phase(False)
        self.assertNotEqual(r.returncode, 0)                    # the stand-in aws fails the stack's lookup
        self.assertIn("cloudformation describe-stacks", calls)
        self.assertNotIn("zone guard", r.stderr)


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
