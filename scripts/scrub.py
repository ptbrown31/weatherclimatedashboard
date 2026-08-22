"""
scrub.py — refuse to publish anything that must not leave the machine.

Scans every tracked text file (or any directory given) for strings that do
not belong in a public repository: credentials, private keys, local paths,
internal hostnames, employer identifiers. Exit status 1 on any hit, with
file and line. Run before every push; DEPLOY.md and the tests call it.

Two needle sets. The built-in set below is generic and safe to publish.
The second set is read from a file OUTSIDE the repo, ~/.weather-tools-site.scrub
(one needle per line, '#' comments), because a list of internal hostnames is
itself something that must not be committed.

    python3 scripts/scrub.py              # scan the git-tracked files
    python3 scripts/scrub.py some/dir     # scan a directory tree (e.g. an archive seed)
"""
from __future__ import annotations
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTERNAL = os.path.expanduser("~/.weather-tools-site.scrub")

BUILTIN = [
    "BEGIN PRIVATE KEY", "BEGIN RSA PRIVATE KEY", "BEGIN OPENSSH PRIVATE KEY",
    "AKIA",                      # AWS access key id prefix
    "aws_secret_access_key",
    "/Users/",                   # local Mac paths
    "/home/ec2-user", "/home/ubuntu",
    ".ts.net", "tailscale", "Tailscale",
    "@interactivebrokers.com", "@ibkr.com",
    "forecasttrader.interactivebrokers.com",   # the venue's endpoints stay out of this repo
]
TEXT_EXT = {".md", ".py", ".html", ".js", ".css", ".json", ".yaml", ".yml", ".toml", ".txt",
            ".service", ".timer", ".sh", ".env", ""}
SKIP_DIRS = {".git", "__pycache__", "node_modules", "data", "archive", "dist", "verify-out"}


def needles() -> list:
    out = list(BUILTIN)
    if os.path.isfile(EXTERNAL):
        for ln in open(EXTERNAL, encoding="utf-8"):
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                out.append(ln)
    return out


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


def scan(paths: list, needles_: list) -> list:
    hits = []
    self_path = os.path.abspath(__file__)
    for path in paths:
        if os.path.abspath(path) == self_path:
            continue                      # this file names the needles
        if os.path.splitext(path)[1] not in TEXT_EXT:
            continue
        try:
            text = open(path, encoding="utf-8", errors="strict").read()
        except (UnicodeDecodeError, OSError):
            continue
        for i, ln in enumerate(text.splitlines(), 1):
            for n in needles_:
                if n in ln:
                    hits.append(f"{os.path.relpath(path, ROOT)}:{i}: {n!r} in: {ln.strip()[:100]}")
    return hits


def main(argv) -> int:
    paths = walk(argv[0]) if argv else tracked_files()
    hits = scan(paths, needles())
    if hits:
        print("SCRUB FAILED - strings that must not be published:", file=sys.stderr)
        for h in hits[:60]:
            print(" ", h, file=sys.stderr)
        return 1
    print(f"scrub clean: {len(paths)} files, {len(needles())} needles")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
