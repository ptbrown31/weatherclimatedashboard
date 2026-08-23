"""
scrub.py — refuse to publish anything that must not leave the machine.

Scans every tracked file (or any directory given) for strings that do not
belong in a public repository: credentials, private keys, local paths,
internal names. Exit status 1 on any hit, with file and line. Run before
every push; DEPLOY.md lists it as the last step before `git push`.

Two needle sets. The built-in set below is generic: key headers, credential
field names, local home paths. The second set lives OUTSIDE the repo, in
~/.weather-tools-site.scrub (one needle per line, '#' comments), and holds
the specific internal names, because a list of them is itself something that
must not be committed. The scrub refuses to pass when that file is missing,
unless --no-external is given deliberately.

Every file is scanned regardless of extension; only files that look binary
(a NUL byte in the first 8 KB) are skipped, and gzip members in directory
mode are decompressed and scanned too. Skipped files are listed.

    python3 scripts/scrub.py                 # scan the git-tracked and untracked files
    python3 scripts/scrub.py some/dir        # scan a directory tree (e.g. an archive seed)
    python3 scripts/scrub.py --no-external   # only when the external list is knowingly absent
"""
from __future__ import annotations
import gzip
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTERNAL = os.path.expanduser("~/.weather-tools-site.scrub")

BUILTIN = [
    "BEGIN PRIVATE KEY", "BEGIN RSA PRIVATE KEY", "BEGIN OPENSSH PRIVATE KEY", "BEGIN EC PRIVATE KEY",
    "AKIA",                      # AWS access key id prefix
    "aws_secret_access_key", "AWS_SECRET_ACCESS_KEY",
    "/Users/",                   # local Mac paths
    "/home/ec2-user", "/home/ubuntu",
]
SKIP_DIRS = {".git", "__pycache__", "node_modules", "data", "dist", "verify-out"}


def needles(require_external: bool = True) -> tuple:
    ext = []
    if os.path.isfile(EXTERNAL):
        for ln in open(EXTERNAL, encoding="utf-8"):
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                ext.append(ln)
    elif require_external:
        raise FileNotFoundError(EXTERNAL)
    return list(BUILTIN), ext


def tracked_files() -> list:
    try:
        res = subprocess.run(["git", "-C", ROOT, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, check=True)
        return [os.path.join(ROOT, p) for p in res.stdout.decode().split("\0") if p]
    except Exception:
        return walk(ROOT)


def walk(base: str) -> list:
    out = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        out.extend(os.path.join(dirpath, f) for f in filenames)
    return out


def _text_of(path: str):
    """The file's text, decompressing .gz; None for binary files."""
    with open(path, "rb") as fh:
        head = fh.read(8192)
        if path.endswith(".gz"):
            try:
                data = gzip.decompress(head + fh.read())
            except Exception:
                return None
            if b"\0" in data[:8192]:
                return None
            return data.decode("utf-8", "replace")
        if b"\0" in head:
            return None
        return (head + fh.read()).decode("utf-8", "replace")


def scan(paths: list, all_needles: list) -> tuple:
    hits, skipped = [], []
    self_path = os.path.abspath(__file__)
    for path in paths:
        if os.path.abspath(path) == self_path:
            continue                      # this file names the built-in needles
        try:
            text = _text_of(path)
        except OSError:
            skipped.append(path)
            continue
        if text is None:
            skipped.append(path)
            continue
        for i, ln in enumerate(text.splitlines(), 1):
            for n in all_needles:
                if n in ln:
                    hits.append(f"{os.path.relpath(path, ROOT)}:{i}: {n!r} in: {ln.strip()[:100]}")
    return hits, skipped


def config_slots() -> list:
    """The vendor lane's credential and host belong to the environment, never
    to config/site.json; a value pasted there for a local run must not be
    committed. Returns hits in the same format as scan()."""
    path = os.path.join(ROOT, "config", "site.json")
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return []
    hits = []
    for field in ("api_key", "base_url"):
        if ((cfg.get("reask") or {}).get(field) or "").strip():
            hits.append(f"config/site.json: reask.{field} is filled in; it must stay empty (set WX_REASK_* in the environment)")
    return hits


def main(argv) -> int:
    args = [a for a in argv if not a.startswith("--")]
    require_external = "--no-external" not in argv
    try:
        builtin, ext = needles(require_external)
    except FileNotFoundError:
        print(f"SCRUB REFUSED: the external needle list {EXTERNAL} is missing. "
              f"Create it (one internal name per line) or pass --no-external deliberately.", file=sys.stderr)
        return 2
    paths = walk(args[0]) if args else tracked_files()
    hits, skipped = scan(paths, builtin + ext)
    hits += config_slots()
    for s in skipped:
        print(f"  skipped (binary): {os.path.relpath(s, ROOT)}")
    if hits:
        print("SCRUB FAILED - strings that must not be published:", file=sys.stderr)
        for h in hits[:60]:
            print(" ", h, file=sys.stderr)
        return 1
    print(f"scrub clean: {len(paths)} files scanned, {len(skipped)} binary skipped, "
          f"{len(builtin)} built-in + {len(ext)} external needles"
          + ("" if ext or not require_external else " (external list empty)"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
