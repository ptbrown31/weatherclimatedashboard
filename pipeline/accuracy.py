"""
accuracy.py — publish the record builder's figures.

Every number on the accuracy page is computed by the record builder, which
runs beside the capture and is not in this repository (docs/accuracy.md says
why). The builder writes its files to a private prefix under the archive with
a manifest listing each one's size and hash, and this job is the only path
from that prefix to the pages. It copies; it computes nothing.

Copying rather than recomputing is the point. The page draws a comparison of
forecast tools against the market under conventions that were fixed once
(the settle-rounded truth, the strict-inequality whole degree, the standing
value with the bank applied). Deriving any of those a second time here would
be a second place for them to drift. The site therefore checks that a file
is what the builder shipped and carries the contract's stamps, and writes
the bytes it read. It never modifies a number.

A build is published whole or not at all. The builder uploads its files
before the manifest, so a manifest whose hashes do not match its files means
the reader is looking at a mixed state (a push that failed part way, or the
next push in progress), and the right response is to leave the published set
as it stands and try again next pass. A file that arrives intact but does
not carry the contract's stamps is a different case, a builder change the
site has not caught up with, and costs that one file, reported, rather than
the build.

Trace files are one per target date and the builder keeps the last sixty,
so the site keeps the same sixty: a trace the manifest no longer lists is
removed from snapshots/accuracy/trace/. Nothing outside trace/ is ever
deleted here, so a file written under the prefix by anything else (the
retired exporter's lead curve, until the builder's own replaces it) is left
where it is.

Reads  archive/accuracy/latest/manifest.json and the files it names.
Writes snapshots/accuracy/<file> and, last, snapshots/accuracy/manifest.json,
so a reader that sees a build's stamp finds every file it names.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import time
from typing import Optional

from . import archive as arch
from .snapshots import SNAP_CACHE, _iso
from .storage import Storage

SCHEMA = 1                                  # the published manifest's own shape
SRC_PREFIX = "archive/accuracy/latest/"
SRC_MANIFEST = SRC_PREFIX + "manifest.json"
DST_PREFIX = "snapshots/accuracy/"
DST_MANIFEST = DST_PREFIX + "manifest.json"
TRACE_DIR = "trace/"
# the stamps every figure file must carry, from docs/accuracy.md section 3.
# A trace file is one target date's raw record and carries meta.date and
# meta.built instead.
FILE_SCHEMA = "accuracy-figures/1"
CONVENTIONS = "v1"
# a pass that cannot finish validating within this many seconds of the chain's
# end leaves the published set alone; the next half-hour tries again
NEED_SECONDS = 30.0


# ------------------------------------------------------------------ reading
def _read_json(store: Storage, key: str) -> Optional[dict]:
    """One JSON object, or None when the key is absent, empty, unparsable or
    not an object."""
    raw = store.get(key)
    if not raw:
        return None
    try:
        body = json.loads(raw)
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def safe_name(name) -> bool:
    """A manifest entry names a file by its path under the prefix. Anything
    that could step outside it, or that is not a JSON file, is refused before
    a key is built from it."""
    if not isinstance(name, str) or not name.endswith(".json") or name.startswith("/"):
        return False
    return all(p not in ("", ".", "..") for p in name.split("/"))


def is_trace(name: str) -> bool:
    return name.startswith(TRACE_DIR)


def check_meta(name: str, body) -> Optional[str]:
    """Why a parsed file fails the contract, or None when it passes.

    The stamps are the contract's, not the site's: a figure file carries the
    schema, the newest resolved target date and the conventions tag, so a
    page can refuse a file built under conventions it does not draw. A trace
    file carries its target date and the build time instead."""
    if not isinstance(body, dict):
        return "not a JSON object"
    meta = body.get("meta")
    if not isinstance(meta, dict):
        return "no meta"

    def text(field: str) -> bool:
        return isinstance(meta.get(field), str) and bool(meta.get(field))

    if is_trace(name):
        if not text("date"):
            return "meta.date missing"
        if not text("built"):
            return "meta.built missing"
        return None
    if meta.get("schema") != FILE_SCHEMA:
        return f"meta.schema is {meta.get('schema')!r}, wanted {FILE_SCHEMA!r}"
    if not text("asof"):
        return "meta.asof missing"
    if meta.get("conventions") != CONVENTIONS:
        return f"meta.conventions is {meta.get('conventions')!r}, wanted {CONVENTIONS!r}"
    return None


def check_file(raw: Optional[bytes], entry: dict) -> Optional[str]:
    """Why the bytes read are not the file the manifest describes, or None.
    A size or hash mismatch is a transport fault and fails the whole build."""
    if raw is None:
        return "missing"
    size = entry.get("size")
    if isinstance(size, int) and not isinstance(size, bool) and size != len(raw):
        return f"size {len(raw)}, manifest says {size}"
    want = entry.get("sha256")
    if not isinstance(want, str) or not want:
        return "manifest entry has no sha256"
    if hashlib.sha256(raw).hexdigest() != want.lower():
        return "sha256 mismatch"
    return None


# ------------------------------------------------------------------ the pass
def validate(store: Storage, manifest: dict, deadline: arch.Deadline) -> dict:
    """Read every file the manifest names and sort it into one of three
    outcomes: `ok` (name, bytes) pairs ready to write, `skipped` files that
    arrived intact but fail the contract's stamps, and `failed` files that are
    not what the manifest says they are. `listed` is every well-formed name
    the manifest carries, which is what the trace pruning keeps. A deadline
    stop is recorded as `stopped` so the caller writes nothing."""
    out: dict = {"ok": [], "skipped": [], "failed": [], "listed": [], "stopped": False}
    entries = manifest.get("files")
    if not isinstance(entries, list):
        out["failed"].append({"name": "manifest.json", "reason": "no files list"})
        return out
    for entry in entries:
        if deadline.over(NEED_SECONDS):
            out["stopped"] = True
            return out
        name = entry.get("name") if isinstance(entry, dict) else None
        if not safe_name(name):
            out["failed"].append({"name": str(name), "reason": "unsafe or non-JSON name"})
            continue
        out["listed"].append(name)
        raw = store.get(SRC_PREFIX + name)
        why = check_file(raw, entry)
        if why:
            out["failed"].append({"name": name, "reason": why})
            continue
        try:
            body = json.loads(raw)
        except ValueError:
            out["skipped"].append({"name": name, "reason": "not valid JSON"})
            continue
        why = check_meta(name, body)
        if why:
            out["skipped"].append({"name": name, "reason": why})
            continue
        out["ok"].append((name, raw))
    return out


def prune_traces(store: Storage, keep: set) -> int:
    """Remove trace files the manifest no longer lists. Only keys under the
    trace directory are candidates, so nothing else under the prefix can be
    removed by this job."""
    removed = 0
    for key in store.list(DST_PREFIX + TRACE_DIR):
        name = key[len(DST_PREFIX):]
        if is_trace(name) and name not in keep:
            store.delete(key)
            removed += 1
    return removed


def _run(store: Storage, now: dt.datetime, deadline: arch.Deadline, status: dict) -> int:
    src = _read_json(store, SRC_MANIFEST)
    if src is None:
        status["note"] = "no manifest at " + SRC_MANIFEST
        return 0
    built = src.get("built")
    if not isinstance(built, str) or not built:
        status["failed"].append({"name": "manifest.json", "reason": "no built stamp"})
        return 1
    status["built"] = built

    # the same build is already published; nothing to read and nothing to write
    published = _read_json(store, DST_MANIFEST) or {}
    if published.get("built") == built:
        status["note"] = "already published"
        return 0

    v = validate(store, src, deadline)
    status["skipped"] = v["skipped"]
    status["failed"] = v["failed"]
    if v["stopped"]:
        status["note"] = "deadline; nothing written"
        return 0
    if v["failed"]:
        status["note"] = "build not published"
        return 1

    written: list = []
    for name, raw in v["ok"]:
        # the bytes as the builder shipped them, under the snapshot cache header
        store.put(DST_PREFIX + name, raw, "application/json", SNAP_CACHE)
        written.append(name)
    keep = {name for name in v["listed"] if is_trace(name)}
    pruned = prune_traces(store, keep)

    out = {"schema": SCHEMA, "built": built, "asof": src.get("asof"), "written": _iso(now),
           "files": written, "skipped": v["skipped"], "traceKept": len(keep), "tracePruned": pruned}
    # last, so a reader that sees this build's stamp finds every file it names
    store.put(DST_MANIFEST, json.dumps(out, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
    status["written"] = len(written)
    status["pruned"] = pruned
    return 0


def accuracy_pass(cfg: dict, store: Storage, now: Optional[dt.datetime] = None) -> int:
    """Entry point. Returns the number of failures: 0 when the published set
    is current, whether or not this pass wrote anything, and 1 when a build
    could not be published. No network of its own; every read and write goes
    through the storage adapter."""
    now = now or dt.datetime.now(dt.timezone.utc)
    t0 = time.time()
    deadline = arch.Deadline(arch.remaining_budget(cfg))
    status: dict = {"kind": "accuracy", "written": 0, "skipped": [], "failed": []}
    errors = 0
    try:
        errors = _run(store, now, deadline, status)
    except Exception as e:  # noqa: BLE001 - recorded like any other job's failure
        errors = 1
        status["error"] = f"{type(e).__name__}: {e}"
    status["errors"] = errors
    status["seconds"] = round(time.time() - t0, 1)
    arch.LAST_STATUS = {"job": "accuracy", "errors": errors, "alarms": [], "seconds": status["seconds"],
                        "written": status["written"], "built": status.get("built"),
                        "failed": status["failed"], "skipped": status["skipped"]}
    print(json.dumps(status))
    return errors
