"""The accuracy job on the local storage backend: a manifest publishes the
files it names and then the manifest; the same build a second time writes
nothing; a hash mismatch publishes nothing and is reported; a file without the
contract's stamps is skipped and reported while the rest go through; traces
the manifest no longer lists are pruned and nothing else is. The fixtures are
the record builder's own output from a six-city development run, checked in
under samples/snapshots/accuracy/, and every manifest here is built from those
bytes rather than trusted from a file, so a test says exactly which entry it
broke. No network."""
import datetime as dt
import hashlib
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pipeline import accuracy, archive, storage   # noqa: E402

FIXTURES = os.path.join(ROOT, "samples", "snapshots", "accuracy")
NOW = dt.datetime(2026, 9, 10, 17, 5, tzinfo=dt.timezone.utc)
BUILT = "2026-09-10T16:53:34Z"


def fixture(name: str) -> bytes:
    with open(os.path.join(FIXTURES, name), "rb") as fh:
        return fh.read()


def entry(name: str, raw: bytes, **override) -> dict:
    e = {"name": name, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    e.update(override)
    return e


def manifest(entries: list, built: str = BUILT) -> bytes:
    return json.dumps({"schema": "accuracy-figures/1", "built": built, "asof": "2026-09-09",
                       "files": entries}).encode()


class Recording(storage.LocalStorage):
    """The local backend, counting writes and deletes so a test can say that a
    pass changed nothing."""
    def __init__(self, root):
        super().__init__(root)
        self.puts, self.deletes = [], []

    def put(self, key, data, content_type="application/octet-stream", cache_control=None):
        self.puts.append((key, content_type, cache_control))
        super().put(key, data, content_type, cache_control)

    def delete(self, key):
        self.deletes.append(key)
        super().delete(key)


class Job(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = Recording(self.tmp.name)
        self.cfg = {}

    def ship(self, files: dict, built: str = BUILT, entries: list = None):
        """Put the builder's files and a manifest describing them under the
        archive prefix, the way push.sh leaves them."""
        for name, raw in files.items():
            self.st.put(accuracy.SRC_PREFIX + name, raw)
        ents = entries if entries is not None else [entry(n, r) for n, r in files.items()]
        self.st.put(accuracy.SRC_MANIFEST, manifest(ents, built))
        self.st.puts.clear()

    def run_pass(self) -> int:
        return accuracy.accuracy_pass(self.cfg, self.st, NOW)

    def published(self):
        raw = self.st.get(accuracy.DST_MANIFEST)
        return json.loads(raw) if raw else None

    # ------------------------------------------------------------ publishing
    def test_a_manifest_with_two_files_publishes_them_and_then_the_manifest(self):
        files = {"availability.json": fixture("availability.json"),
                 "calibration.json": fixture("calibration.json")}
        self.ship(files)
        self.assertEqual(self.run_pass(), 0)
        for name, raw in files.items():
            self.assertEqual(self.st.get(accuracy.DST_PREFIX + name), raw)   # the bytes, unchanged
        pub = self.published()
        self.assertEqual(pub["built"], BUILT)
        self.assertEqual(pub["asof"], "2026-09-09")
        self.assertEqual(sorted(pub["files"]), sorted(files))
        self.assertEqual(pub["skipped"], [])
        # the manifest is the last write, and every write carries the snapshot headers
        self.assertEqual(self.st.puts[-1][0], accuracy.DST_MANIFEST)
        for key, ctype, cache in self.st.puts:
            self.assertEqual(ctype, "application/json", key)
            self.assertEqual(cache, accuracy.SNAP_CACHE, key)
        self.assertEqual(archive.LAST_STATUS["job"], "accuracy")
        self.assertEqual(archive.LAST_STATUS["written"], 2)
        self.assertEqual(archive.LAST_STATUS["errors"], 0)

    def test_a_trace_file_is_published_on_its_own_stamps(self):
        files = {"trace/2026-09-09.json": fixture("trace/2026-09-09.json")}
        self.ship(files)
        self.assertEqual(self.run_pass(), 0)
        self.assertEqual(self.st.get(accuracy.DST_PREFIX + "trace/2026-09-09.json"), files["trace/2026-09-09.json"])
        self.assertEqual(self.published()["files"], ["trace/2026-09-09.json"])

    def test_no_manifest_means_nothing_to_do(self):
        self.assertEqual(self.run_pass(), 0)
        self.assertEqual(self.st.puts, [])
        self.assertIsNone(self.published())

    # ---------------------------------------------------------- idempotence
    def test_the_same_build_a_second_time_writes_nothing(self):
        files = {"availability.json": fixture("availability.json"),
                 "calibration.json": fixture("calibration.json")}
        self.ship(files)
        self.assertEqual(self.run_pass(), 0)
        self.st.puts.clear()
        self.assertEqual(self.run_pass(), 0)
        self.assertEqual(self.st.puts, [])
        self.assertEqual(self.st.deletes, [])

    def test_a_new_built_stamp_publishes_again(self):
        files = {"availability.json": fixture("availability.json")}
        self.ship(files)
        self.assertEqual(self.run_pass(), 0)
        self.ship(files, built="2026-09-11T10:50:00Z")
        self.assertEqual(self.run_pass(), 0)
        self.assertEqual(self.published()["built"], "2026-09-11T10:50:00Z")
        self.assertIn(accuracy.DST_PREFIX + "availability.json", [k for k, _c, _h in self.st.puts])

    # ------------------------------------------------------------ transport
    def test_a_hash_mismatch_publishes_nothing_and_reports_it(self):
        avail, cal = fixture("availability.json"), fixture("calibration.json")
        self.ship({"availability.json": avail, "calibration.json": cal},
                  entries=[entry("availability.json", avail),
                           entry("calibration.json", cal, sha256="0" * 64)])
        self.assertEqual(self.run_pass(), 1)
        self.assertEqual(self.st.puts, [])
        self.assertIsNone(self.published())
        failed = archive.LAST_STATUS["failed"]
        self.assertEqual([f["name"] for f in failed], ["calibration.json"])
        self.assertIn("sha256", failed[0]["reason"])

    def test_a_size_mismatch_is_a_transport_fault_too(self):
        avail = fixture("availability.json")
        self.ship({"availability.json": avail}, entries=[entry("availability.json", avail, size=len(avail) - 1)])
        self.assertEqual(self.run_pass(), 1)
        self.assertEqual(self.st.puts, [])
        self.assertIn("size", archive.LAST_STATUS["failed"][0]["reason"])

    def test_a_listed_file_that_is_not_there_fails_the_build(self):
        avail = fixture("availability.json")
        self.ship({"availability.json": avail},
                  entries=[entry("availability.json", avail), entry("grid.json", fixture("grid.json"))])
        self.assertEqual(self.run_pass(), 1)
        self.assertEqual(self.st.puts, [])
        self.assertEqual(archive.LAST_STATUS["failed"][0], {"name": "grid.json", "reason": "missing"})

    def test_a_name_that_leaves_the_prefix_is_refused(self):
        avail = fixture("availability.json")
        self.ship({"availability.json": avail},
                  entries=[entry("availability.json", avail), entry("../summary.json", avail)])
        self.assertEqual(self.run_pass(), 1)
        self.assertEqual(self.st.puts, [])

    # ------------------------------------------------------------- the meta
    def test_a_file_failing_the_meta_check_is_skipped_and_reported(self):
        good = fixture("availability.json")
        body = json.loads(fixture("calibration.json"))
        body["meta"]["conventions"] = "v2"          # built under conventions the page does not draw
        bad = json.dumps(body).encode()
        self.ship({"availability.json": good, "calibration.json": bad})
        self.assertEqual(self.run_pass(), 0)
        self.assertEqual(self.st.get(accuracy.DST_PREFIX + "availability.json"), good)
        self.assertIsNone(self.st.get(accuracy.DST_PREFIX + "calibration.json"))
        pub = self.published()
        self.assertEqual(pub["files"], ["availability.json"])
        self.assertEqual([s["name"] for s in pub["skipped"]], ["calibration.json"])
        self.assertIn("conventions", pub["skipped"][0]["reason"])
        self.assertEqual([s["name"] for s in archive.LAST_STATUS["skipped"]], ["calibration.json"])

    def test_the_stamps_the_contract_names(self):
        ok = {"meta": {"schema": "accuracy-figures/1", "asof": "2026-09-09", "conventions": "v1"}}
        self.assertIsNone(accuracy.check_meta("grid.json", ok))
        self.assertIn("schema", accuracy.check_meta("grid.json", {"meta": dict(ok["meta"], schema=1)}))
        self.assertIn("asof", accuracy.check_meta("grid.json", {"meta": {"schema": "accuracy-figures/1", "conventions": "v1"}}))
        self.assertIn("conventions", accuracy.check_meta("grid.json", {"meta": {"schema": "accuracy-figures/1", "asof": "2026-09-09"}}))
        self.assertEqual(accuracy.check_meta("grid.json", {"metric": {}}), "no meta")
        self.assertEqual(accuracy.check_meta("grid.json", [1, 2]), "not a JSON object")
        # a trace carries its date and build time and none of the figure stamps
        self.assertIsNone(accuracy.check_meta("trace/2026-09-09.json", {"meta": {"date": "2026-09-09", "built": BUILT}}))
        self.assertIn("date", accuracy.check_meta("trace/2026-09-09.json", {"meta": {"built": BUILT}}))
        self.assertIn("built", accuracy.check_meta("trace/2026-09-09.json", {"meta": {"date": "2026-09-09"}}))

    def test_the_fixtures_carry_the_stamps(self):
        for name in ("availability.json", "calibration.json", "dynamics.json", "grid.json", "lead-curve.json",
                     "map.json", "trace/2026-09-08.json", "trace/2026-09-09.json"):
            self.assertIsNone(accuracy.check_meta(name, json.loads(fixture(name))), name)

    def test_a_file_that_is_not_json_is_skipped_not_fatal(self):
        good, bad = fixture("availability.json"), b"{not json"
        self.ship({"availability.json": good, "grid.json": bad})
        self.assertEqual(self.run_pass(), 0)
        self.assertEqual(self.published()["files"], ["availability.json"])
        self.assertEqual(self.published()["skipped"][0]["reason"], "not valid JSON")

    # -------------------------------------------------------------- pruning
    def test_old_trace_files_are_pruned_and_nothing_else_is(self):
        old_curve = b'{"schema":2,"points":[]}'      # the retired exporter's file, at its own key
        self.st.put(accuracy.DST_PREFIX + "lead-curve.json", old_curve)
        self.st.put(accuracy.DST_PREFIX + "trace/2026-07-01.json", b'{"meta":{"date":"2026-07-01"}}')
        self.st.put(accuracy.DST_PREFIX + "trace/2026-07-02.json", b'{"meta":{"date":"2026-07-02"}}')
        new = fixture("trace/2026-09-08.json")
        self.ship({"trace/2026-09-08.json": new, "availability.json": fixture("availability.json")})
        self.assertEqual(self.run_pass(), 0)
        self.assertIsNone(self.st.get(accuracy.DST_PREFIX + "trace/2026-07-01.json"))
        self.assertIsNone(self.st.get(accuracy.DST_PREFIX + "trace/2026-07-02.json"))
        self.assertEqual(self.st.get(accuracy.DST_PREFIX + "trace/2026-09-08.json"), new)
        self.assertEqual(self.st.get(accuracy.DST_PREFIX + "lead-curve.json"), old_curve)
        self.assertEqual(sorted(self.st.deletes), [accuracy.DST_PREFIX + "trace/2026-07-01.json",
                                                   accuracy.DST_PREFIX + "trace/2026-07-02.json"])
        pub = self.published()
        self.assertEqual((pub["traceKept"], pub["tracePruned"]), (1, 2))

    def test_a_listed_trace_is_kept_even_when_this_build_skipped_it(self):
        # an earlier build published the trace; this build lists it again but
        # ships it without its stamps. Skipping the copy must not also delete
        # the copy already published, since the manifest still names it.
        self.st.put(accuracy.DST_PREFIX + "trace/2026-09-08.json", fixture("trace/2026-09-08.json"))
        self.ship({"trace/2026-09-08.json": b'{"meta":{}}', "availability.json": fixture("availability.json")})
        self.assertEqual(self.run_pass(), 0)
        self.assertIsNotNone(self.st.get(accuracy.DST_PREFIX + "trace/2026-09-08.json"))
        self.assertEqual(self.st.deletes, [])

    # ------------------------------------------------------------- deadline
    def test_out_of_time_leaves_the_published_set_alone(self):
        self.ship({"availability.json": fixture("availability.json")})
        self.cfg["_deadline_end"] = 0.0             # the chain's budget is already spent
        self.assertEqual(self.run_pass(), 0)
        self.assertEqual(self.st.puts, [])
        self.assertIsNone(self.published())


if __name__ == "__main__":
    unittest.main()
