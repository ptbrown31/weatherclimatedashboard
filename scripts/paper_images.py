"""
paper_images.py — the two pages at the top of the accuracy page.

    python3 scripts/paper_images.py --brief BRIEF.pdf --paper PAPER.pdf

The research brief is served from the site, so its PDF is copied into
site/assets/ and its page rendered there at two widths, one for the row and
one for the enlarged view. The working paper is not served from the site,
which links its DOI instead, so only an image of its first page is written.
Either argument can be given alone, to replace one of the two.

Needs pdftoppm (poppler), which writes the JPEGs itself, so the script stays
on the standard library.
"""
from __future__ import annotations
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "site", "assets")

BRIEF_PDF = "Brown_2026_prediction_market_brief.pdf"
# file name -> width in pixels; the row shows a page about 460 CSS pixels
# wide, so 1000 is sharp on a 2x screen, and the dialog shows it up to 900
BRIEF_IMAGES = {"research-brief.jpg": 1000, "research-brief-large.jpg": 1800}
PAPER_IMAGES = {"paper-first-page.jpg": 1000}
QUALITY = 82


def first_page(pdf: str, width: int, out: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        stem = os.path.join(tmp, "page")
        subprocess.run(["pdftoppm", "-f", "1", "-l", "1", "-singlefile", "-jpeg",
                        "-jpegopt", "quality=%d,optimize=y" % QUALITY,
                        "-scale-to-x", str(width), "-scale-to-y", "-1", pdf, stem], check=True)
        shutil.move(stem + ".jpg", out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--brief", help="the research brief, one page")
    ap.add_argument("--paper", help="the working paper as the archive serves it")
    a = ap.parse_args(argv)
    if not (a.brief or a.paper):
        ap.error("give --brief, --paper or both")
    if shutil.which("pdftoppm") is None:
        sys.exit("pdftoppm is needed: brew install poppler")
    for pdf in (a.brief, a.paper):
        if pdf:
            with open(pdf, "rb") as fh:
                if fh.read(5) != b"%PDF-":
                    sys.exit(f"{pdf} does not start like a PDF")
    os.makedirs(ASSETS, exist_ok=True)
    if a.brief:
        shutil.copyfile(a.brief, os.path.join(ASSETS, BRIEF_PDF))
        print(f"{BRIEF_PDF}  {os.path.getsize(a.brief):,} bytes")
    for pdf, images in ((a.brief, BRIEF_IMAGES), (a.paper, PAPER_IMAGES)):
        if not pdf:
            continue
        for name, width in images.items():
            out = os.path.join(ASSETS, name)
            first_page(pdf, width, out)
            print(f"{name}  {width} px wide  {os.path.getsize(out):,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
