"""
package_lambda.py — zip the pipeline for AWS Lambda.

The zip holds pipeline/, config/ (site.json, cities.json, field_grid.json,
contracts.json)
and geo/rapid_amoc_annual.json, laid out as in the repo so the code finds
its config the same way it does locally. boto3 is in the Lambda runtime.
The only optional extra is the `tzdata` package: the IANA zone database
the pipeline's day bucketing needs. Amazon Linux 2023 runtimes carry the
system zone database; `--with-tzdata` vendors the pure-Python package
anyway (about 600 KB) so a missing system database cannot break the job.

    python3 scripts/package_lambda.py [--with-tzdata]   -> dist/lambda.zip
"""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INCLUDE = ["pipeline", "config/site.json", "config/cities.json", "config/field_grid.json",
           "config/contracts.json", "config/cat4_climatology.json", "config/severe_climo.json", "config/nearby_stations.json",
           "geo/rapid_amoc_annual.json"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--with-tzdata", action="store_true")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist", "lambda.zip"))
    args = ap.parse_args(argv)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    n = 0
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as z:
        for item in INCLUDE:
            src = os.path.join(ROOT, item)
            if os.path.isdir(src):
                for dirpath, dirnames, filenames in os.walk(src):
                    dirnames[:] = [d for d in dirnames if d != "__pycache__"]
                    for fn in filenames:
                        if fn.endswith(".pyc"):
                            continue
                        p = os.path.join(dirpath, fn)
                        z.write(p, os.path.relpath(p, ROOT))
                        n += 1
            else:
                z.write(src, item)
                n += 1
        if args.with_tzdata:
            with tempfile.TemporaryDirectory() as tmp:
                subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--target", tmp, "tzdata"], check=True)
                for dirpath, dirnames, filenames in os.walk(tmp):
                    dirnames[:] = [d for d in dirnames if d != "__pycache__"]
                    for fn in filenames:
                        p = os.path.join(dirpath, fn)
                        z.write(p, os.path.relpath(p, tmp))
                        n += 1
    size = os.path.getsize(args.out) / 1e6
    print(f"lambda package: {n} files, {size:.1f} MB -> {os.path.relpath(args.out, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
