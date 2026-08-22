"""
serve_local.py — a static server for the built targets, with a failure switch.

    python3 scripts/serve_local.py                       # dist/standalone on http://127.0.0.1:8088/
    python3 scripts/serve_local.py --target embed        # dist/embed
    python3 scripts/serve_local.py --fail-fetch          # every /data/ request answers 503, to
                                                         # exercise the page's degradation paths
    python3 scripts/serve_local.py --live                # serve data/snapshots in place of the bundled copy

Standard library only. Not for production: the deploy target is a CDN.
"""
from __future__ import annotations
import argparse
import functools
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Handler(SimpleHTTPRequestHandler):
    fail_fetch = False
    live_root = None

    def do_GET(self):
        if self.path.startswith("/data/") and self.fail_fetch:
            self.send_response(503)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"simulated data outage\n")
            return
        super().do_GET()

    def translate_path(self, path):
        if self.live_root and path.startswith("/data/snapshots/"):
            rel = path[len("/data/snapshots/"):].split("?", 1)[0]
            return os.path.join(self.live_root, rel)
        return super().translate_path(path)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        if "--quiet" not in sys.argv:
            super().log_message(fmt, *args)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", choices=["standalone", "embed"], default="standalone")
    ap.add_argument("--port", type=int, default=8088)
    ap.add_argument("--fail-fetch", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    directory = os.path.join(ROOT, "dist", args.target)
    if not os.path.isdir(directory):
        print(f"{directory} does not exist; run scripts/build.py first", file=sys.stderr)
        return 1
    Handler.fail_fetch = args.fail_fetch
    Handler.live_root = os.path.join(ROOT, "data", "snapshots") if args.live else None
    handler = functools.partial(Handler, directory=directory)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"serving {args.target} at http://127.0.0.1:{args.port}/"
          + (" (data requests fail)" if args.fail_fetch else "") + (" (live data)" if args.live else ""))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
