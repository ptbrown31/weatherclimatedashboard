"""
floater.py — a satellite loop for each active storm.

NOAA/NESDIS STAR points a GOES floater sector at every tropical cyclone NHC is
tracking and publishes each frame as a JPEG, every ten minutes, at several
sizes (cdn.star.nesdis.noaa.gov/FLOATER/data/<ID>/GEOCOLOR/). This job copies
the newest frames of each storm in NHC's roster into this site's bucket, so the
browser never asks a government server for anything, and writes one small
index the hurricane page plays them from (owner, 2026-10-10: a loop like the
ones storm pages carry, from the frames rather than STAR's ready-made animated
GIF, which comes only at 1000 pixels and runs to 13 or 14 MB):

  snapshots/floater/index.json            {asof, storms: {ID: {name, satellite, frames: [{key, t}]}}}
  snapshots/floater/<ID>/<stamp>.jpg      one frame, 500 pixels square, about 210 KB

GeoColor is true colour by day and infrared cloud tops over city lights by
night, the product most storm pages lead with. Eighteen frames at STAR's
ten-minute cadence is three hours, enough to see a storm turn and organise, and
about 4 MB for a reader who plays it. A frame that falls out of the loop is
deleted from the bucket, and a storm that leaves NHC's roster takes its frames
with it, so the prefix holds only what the page can show.

The same pass copies the Weather Prediction Center's surface analysis drawn
over GOES-East and -West imagery, its "Analysis/Satellite Composite (CONUS)",
for the loop under the landing map, where it replaced GOES-East's CONUS sector
(owner, 2026-10-20). WPC analyses every three hours and keeps one image per
analysis hour at a fixed address (www.wpc.ncep.noaa.gov/sfc/ussatsfcHH.gif, HH
00 to 21), each written over by the next day's analysis at that hour, so the
eight files are the last 24 hours of fronts and pressure systems:

  snapshots/wpcsfc/index.json               {asof, frames: [{t, key, w, h}]}
  snapshots/wpcsfc/<YYYYMMDDHH>-<w>x<h>.png one analysis on the landing map's projection, 960 wide

A file's analysis time is its hour on the day its Last-Modified places it,
which WPC writes about two hours after the analysis; a range read of sixteen
bytes asks for that time without fetching the image, and only an analysis not
already in the bucket is copied.

WPC draws the composite on a Mercator projection, which makes the northern
states taller and wider than the landing map's equal-area one, so each image is
carried onto the map's projection before it is stored (owner, 2026-10-20: the
states should have the shapes they have on the temperature map above it). The
frame is the map's 960 pixels wide at the scale that holds all of WPC's picture,
which draws the lower 48 at about two thirds of their size on the map, so what
lies past the borders stays in view (owner, the same day). Every pixel of the
frame is taken back through the projection to a latitude and longitude and given
the colour of WPC's pixel nearest that point, so no colour is mixed or invented.
The fan-shaped margins outside WPC's picture are transparent, and WPC's colour
key, its black title bands and its title block are left out.
"""
from __future__ import annotations
import datetime as dt
import json
import math
import re
import struct
import time
import zlib
from typing import Callable, Optional

from . import archive as arch
from . import basemap
from . import gov_weather as gw
from .snapshots import SNAP_CACHE, _iso
from .storage import Storage

SCHEMA = 1
PREFIX = "snapshots/floater/"
INDEX = PREFIX + "index.json"
BASE = "https://cdn.star.nesdis.noaa.gov/FLOATER/data/"
PRODUCT = "GEOCOLOR"
SIZE = "500x500"
FRAMES = 18                        # three hours at STAR's ten-minute cadence
SOURCE = "NOAA/NESDIS STAR GOES floater imagery, GeoColor"
FRAME_CACHE = "public, max-age=86400, immutable"   # a frame never changes under its stamp

WPC_URL = "https://www.wpc.ncep.noaa.gov/sfc/ussatsfc%s.gif"
WPC_HOURS = ("00", "03", "06", "09", "12", "15", "18", "21")
WPC_PREFIX = "snapshots/wpcsfc/"
WPC_INDEX = WPC_PREFIX + "index.json"
WPC_SOURCE = "NOAA/NWS Weather Prediction Center surface analysis over GOES-East and -West imagery"
WPC_MAX_LAG_H = 9                  # an image written later than this after its hour is not trusted
# WPC's Mercator, fitted to the state lines it draws: x = A + B * lon and
# y = C + D * M(lat) in its 900 by 675 image, M the Mercator ordinate in degrees.
# Over the lower 48 states' boundaries (the us-atlas outlines this site draws),
# 96 percent of the points fell within a pixel of WPC's own white lines in the
# 09Z analysis of 2026-10-20 and 97 percent in its 21Z one, mean 0.24 pixel; the
# two scales agree to 0.03 percent, as a Mercator's must.
WPC_SIZE = (900, 675)
WPC_A, WPC_B, WPC_C, WPC_D = 1680.954, 12.5526, 829.4734, -12.5557
WPC_KEY_X = 60                     # WPC's colour key runs down the image left of this
# The picture's first and last rows, with WPC's black title bands above and below,
# and its title block, which covers the picture left of the column from the row down.
# Measured on the 12Z and 15Z images of 2026-10-20, which agree.
WPC_ROWS = (31, 607)
WPC_TITLE = (338, 586)


def listing_url(sid: str) -> str:
    return "%s%s/%s/" % (BASE, sid, PRODUCT)


def parse_listing(html: str, sid: str) -> list:
    """The frames a floater's directory lists for one storm, oldest first:
    [(stamp, file name, satellite)]. STAR stamps a frame YYYYDDDHHMM (the day
    of the year, then the hour and minute in UTC)."""
    pat = re.compile(r'href="((\d{11})_(GOES\d\d)-ABI-FL-%s-%s-%s\.jpg)"' % (PRODUCT, re.escape(sid), SIZE))
    seen = {}
    for name, stamp, sat in pat.findall(html or ""):
        seen[stamp] = (stamp, name, sat)
    return [seen[k] for k in sorted(seen)]


def stamp_time(stamp: str) -> str:
    """'20262831000' -> '2026-10-10T10:00Z'."""
    t = dt.datetime.strptime(stamp[:4], "%Y") + dt.timedelta(days=int(stamp[4:7]) - 1,
                                                          hours=int(stamp[7:9]), minutes=int(stamp[9:11]))
    return t.strftime("%Y-%m-%dT%H:%MZ")


def active_storms(store: Storage) -> list:
    """NHC's roster as the hurricane job last wrote it: [(ID, name, basin)]."""
    raw = store.get("snapshots/hurricane.json")
    try:
        storms = (json.loads(raw) or {}).get("storms") or [] if raw else []
    except ValueError:
        storms = []
    out = []
    for s in storms:
        sid = str(s.get("id") or "").upper()
        if re.fullmatch(r"[A-Z]{2}\d{6}", sid):
            out.append((sid, s.get("name") or sid, s.get("basin")))
    return out


def floater_job(cfg: dict, store: Storage, now: dt.datetime, fetch: Optional[Callable[[str], bytes]] = None,
                deadline: Optional[arch.Deadline] = None) -> dict:
    fetch = fetch or (lambda url: gw._fetch(url, timeout=60))
    raw = store.get(INDEX)
    try:
        prev = json.loads(raw) if raw else {}
    except ValueError:
        prev = {}
    errors: list = []
    storms_out = {}
    copied = 0
    for sid, name, basin in active_storms(store):
        if deadline is not None and deadline.over(30):
            errors.append(f"{sid}: not enough time left in the pass")
            if sid in (prev.get("storms") or {}):
                storms_out[sid] = prev["storms"][sid]
            continue
        try:
            frames = parse_listing(fetch(listing_url(sid)).decode("utf-8", "replace"), sid)[-FRAMES:]
            if not frames:
                raise RuntimeError("no %s %s frames listed" % (PRODUCT, SIZE))
            have = set(store.list(PREFIX + sid + "/"))
            keep = []
            for stamp, fname, sat in frames:
                key = "%s%s/%s.jpg" % (PREFIX, sid, stamp)
                if key not in have:
                    body = fetch(listing_url(sid) + fname)
                    if not body or body[:2] != b"\xff\xd8":
                        raise RuntimeError("%s is not a JPEG" % fname)
                    store.put(key, body, "image/jpeg", FRAME_CACHE)
                    copied += 1
                keep.append({"key": key[len("snapshots/"):], "t": stamp_time(stamp), "sat": sat})
            for key in have - {"snapshots/" + f["key"] for f in keep}:
                store.delete(key)
            storms_out[sid] = {"name": name, "basin": basin, "satellite": keep[-1]["sat"].replace("GOES", "GOES-"),
                               "product": "GeoColor", "frames": [{"key": f["key"], "t": f["t"]} for f in keep]}
        except Exception as e:  # noqa: BLE001 - one storm's floater does not hold up another's
            errors.append(f"{sid}: {type(e).__name__}: {e}")
            if sid in (prev.get("storms") or {}):
                storms_out[sid] = prev["storms"][sid]           # the last loop stands
    # a storm that has left NHC's roster takes its frames with it
    for key in store.list(PREFIX):
        rest = key[len(PREFIX):]
        if "/" in rest and rest.split("/", 1)[0] not in storms_out:
            store.delete(key)
    snap = {"schema": SCHEMA, "asof": _iso(now), "source": SOURCE, "product": "GeoColor", "size": SIZE,
            "storms": storms_out}
    store.put(INDEX, json.dumps(snap, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
    return {"written": True, "storms": len(storms_out), "copied": copied, "errors": errors}


def analysis_time(hour: str, last_modified: Optional[str]) -> Optional[dt.datetime]:
    """The analysis an image is: the latest time at its hour no later than when
    the file was written, None when that is further back than WPC_MAX_LAG_H
    (a file not written over yesterday, which is not the last 24 hours)."""
    if not last_modified:
        return None
    lm = dt.datetime.fromisoformat(last_modified.replace("Z", "+00:00"))
    t = lm.replace(hour=int(hour), minute=0, second=0, microsecond=0)
    if t > lm:
        t -= dt.timedelta(days=1)
    return t if lm - t <= dt.timedelta(hours=WPC_MAX_LAG_H) else None


def gif_size(head: bytes) -> Optional[tuple]:
    """(width, height) from a GIF's header, None for anything that is not a GIF."""
    if len(head) < 10 or head[:4] != b"GIF8":
        return None
    return int.from_bytes(head[6:8], "little"), int.from_bytes(head[8:10], "little")


def gif_pixels(data: bytes) -> tuple:
    """(width, height, palette, pixels) of a GIF's first image: palette a list
    of (r, g, b), pixels one palette index per byte, rows from the top."""
    if gif_size(data) is None:
        raise ValueError("not a GIF")
    w, h = struct.unpack("<HH", data[6:10])
    flags, bg = data[10], data[11]
    pos, palette = 13, []
    if flags & 0x80:
        n = 2 << (flags & 7)
        palette = [tuple(data[pos + 3 * i:pos + 3 * i + 3]) for i in range(n)]
        pos += 3 * n
    while pos < len(data):
        block = data[pos]
        if block == 0x21:                           # an extension: skip its sub-blocks
            pos += 2
            while data[pos]:
                pos += data[pos] + 1
            pos += 1
        elif block == 0x2C:                         # the image
            left, top, iw, ih, f = struct.unpack("<HHHHB", data[pos + 1:pos + 10])
            pos += 10
            if f & 0x80:
                n = 2 << (f & 7)
                palette = [tuple(data[pos + 3 * i:pos + 3 * i + 3]) for i in range(n)]
                pos += 3 * n
            min_size = data[pos]
            pos += 1
            chunks = []
            while data[pos]:
                chunks.append(data[pos + 1:pos + 1 + data[pos]])
                pos += data[pos] + 1
            idx = _lzw(b"".join(chunks), min_size, iw * ih)
            if f & 0x40:                            # interlaced: rows come in four passes
                order = [r for start, step in ((0, 8), (4, 8), (2, 4), (1, 2)) for r in range(start, ih, step)]
                rows = {r: idx[k * iw:(k + 1) * iw] for k, r in enumerate(order)}
                idx = b"".join(rows[r] for r in range(ih))
            out = bytearray([bg]) * (w * h)
            for r in range(ih):
                out[(top + r) * w + left:(top + r) * w + left + iw] = idx[r * iw:(r + 1) * iw]
            return w, h, palette, bytes(out)
        elif block == 0x3B:
            break
        else:
            raise ValueError("unexpected GIF block 0x%02x" % block)
    raise ValueError("GIF with no image")


def _lzw(data: bytes, min_size: int, npix: int) -> bytes:
    """GIF's variable-width LZW: codes grow a bit each time the table fills its
    width, up to twelve, and a clear code starts the table again."""
    clear, end = 1 << min_size, (1 << min_size) + 1
    size, table = min_size + 1, [bytes([i]) for i in range(clear)] + [b"", b""]
    out, prev, buf, nb = bytearray(), None, 0, 0
    for byte in data:
        buf |= byte << nb
        nb += 8
        while nb >= size:
            code = buf & ((1 << size) - 1)
            buf >>= size
            nb -= size
            if code == clear:
                size, table, prev = min_size + 1, table[:clear + 2], None
                continue
            if code == end:
                return bytes(out[:npix])
            if prev is None:
                entry = table[code]
            elif code < len(table):
                entry = table[code]
                table.append(prev + entry[:1])
            else:
                entry = prev + prev[:1]
                table.append(entry)
            out += entry
            prev = entry
            if len(table) == 1 << size and size < 12:
                size += 1
    return bytes(out[:npix])


def png_indexed(w: int, h: int, palette: list, pixels: bytes, transparent: Optional[int] = None) -> bytes:
    """An 8-bit palette PNG, rows unfiltered, with one palette entry fully
    transparent when `transparent` names it."""
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + pixels[r * w:(r + 1) * w] for r in range(h))
    trns = b"" if transparent is None else chunk(b"tRNS", b"\xff" * transparent + b"\x00")
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 3, 0, 0, 0))
            + chunk(b"PLTE", b"".join(bytes(c) for c in palette)) + trns + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def wpc_lonlat(x: float, y: float) -> tuple:
    """A point of WPC's image as (lon, lat), the fit above run backwards."""
    m = math.radians((y - WPC_C) / WPC_D)
    return (x - WPC_A) / WPC_B, math.degrees(2 * math.atan(math.exp(m)) - math.pi / 2)


def in_picture(x: int, y: int) -> bool:
    """Whether a pixel of WPC's image is picture: not the colour key, the black
    bands or the title block."""
    return (WPC_KEY_X <= x < WPC_SIZE[0] and WPC_ROWS[0] <= y <= WPC_ROWS[1]
            and not (x < WPC_TITLE[0] and y >= WPC_TITLE[1]))


_WPC_FRAME: Optional[tuple] = None


def wpc_frame() -> tuple:
    """(transform, width, height) of the loop's frame: the landing map's
    projection at the scale that puts all of WPC's picture across the map's
    width. The picture's outline, its outer pixel edges, is projected point by
    point, and the frame is the box around it."""
    global _WPC_FRAME
    if _WPC_FRAME is None:
        x0, x1 = WPC_KEY_X - 0.5, WPC_SIZE[0] - 0.5
        y0, y1 = WPC_ROWS[0] - 0.5, WPC_ROWS[1] + 0.5
        n = 1000
        edge = [(x0 + (x1 - x0) * k / n, y) for k in range(n + 1) for y in (y0, y1)]
        edge += [(x, y0 + (y1 - y0) * k / n) for k in range(n + 1) for x in (x0, x1)]
        pts = [basemap.albers(*wpc_lonlat(x, y)) for x, y in edge]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        w = int(basemap.W)
        s = w / (max(xs) - min(xs))
        _WPC_FRAME = (basemap.Transform(s, -min(xs) * s, max(ys) * s), w, int(math.ceil((max(ys) - min(ys)) * s)))
    return _WPC_FRAME


_WPC_TABLE: Optional[list] = None


def wpc_table() -> list:
    """For every pixel of the loop's frame, rows from the top, the index of WPC's
    pixel nearest the same latitude and longitude, or None where that is not
    WPC's picture."""
    global _WPC_TABLE
    if _WPC_TABLE is None:
        tr, fw, fh = wpc_frame()
        out = []
        for j in range(fh):
            for i in range(fw):
                lon, lat = tr.unscreen(i + 0.5, j + 0.5)
                x = round(WPC_A + WPC_B * lon)
                y = round(WPC_C + WPC_D * math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))))
                out.append(y * WPC_SIZE[0] + x if in_picture(x, y) else None)
        _WPC_TABLE = out
    return _WPC_TABLE


def wpc_on_map(gif: bytes) -> bytes:
    """One WPC analysis carried onto the loop's frame, as a palette PNG with the
    margins outside WPC's picture transparent. They take a palette entry the
    picture does not use (WPC's images use about fifty of their 256), or black
    should every entry be in use."""
    w, h, palette, px = gif_pixels(gif)
    if (w, h) != WPC_SIZE:
        raise ValueError("WPC image is %d by %d, not the %d by %d this site's fit is for" % ((w, h) + WPC_SIZE))
    table = wpc_table()
    used = {px[k] for k in table if k is not None}
    clear = next((i for i in range(256) if i not in used), None)
    if clear is None:
        margin = palette.index((0, 0, 0)) if (0, 0, 0) in palette else 0
    else:
        margin = clear
        palette = palette + [(0, 0, 0)] * max(0, clear + 1 - len(palette))
    _, fw, fh = wpc_frame()
    out = bytes(margin if k is None else px[k] for k in table)
    return png_indexed(fw, fh, palette, out, transparent=clear)


def wpc_job(store: Storage, now: dt.datetime, fetch: Optional[Callable] = None,
            meta_fetch: Optional[Callable] = None, deadline: Optional[arch.Deadline] = None) -> dict:
    """Copy the WPC analysis images not yet in the bucket and index the newest
    eight, oldest first. Nothing is deleted until the new loop is in the bucket,
    so a pass that fails part way leaves the last loop as it was."""
    fetch = fetch or (lambda u: gw._fetch(u, timeout=60))
    meta_fetch = meta_fetch or (lambda u, meta: gw.fetch_range(u, 0, 15, meta=meta))
    have = set(store.list(WPC_PREFIX))
    found, copied = [], 0
    for hh in WPC_HOURS:
        meta: dict = {}
        head = meta_fetch(WPC_URL % hh, meta)
        t = analysis_time(hh, meta.get("lastModified"))
        size = gif_size(head or b"")
        if t is None or size is None:
            continue
        # the frame's size is in the key, so a change of frame draws every analysis again
        key = "%s%s-%dx%d.png" % ((WPC_PREFIX, t.strftime("%Y%m%d%H")) + wpc_frame()[1:])
        if key not in have:
            if deadline is not None and deadline.over(20):
                raise RuntimeError("not enough time left in the pass")
            body = fetch(WPC_URL % hh)
            if gif_size(body or b"") is None:
                raise RuntimeError("ussatsfc%s.gif is not a GIF" % hh)
            store.put(key, wpc_on_map(body), "image/png", FRAME_CACHE)
            copied += 1
        found.append({"t": t.strftime("%Y-%m-%dT%H:%MZ"), "key": key[len("snapshots/"):],
                      "w": wpc_frame()[1], "h": wpc_frame()[2]})
    if not found:
        raise RuntimeError("no WPC analysis image could be dated")
    keep = sorted(found, key=lambda f: f["t"])[-len(WPC_HOURS):]
    for key in have - {"snapshots/" + f["key"] for f in keep} - {WPC_INDEX}:
        store.delete(key)
    snap = {"schema": SCHEMA, "asof": _iso(now), "source": WPC_SOURCE,
            "product": "Analysis/Satellite Composite (CONUS)", "frames": keep}
    store.put(WPC_INDEX, json.dumps(snap, separators=(",", ":")).encode(), "application/json", SNAP_CACHE)
    return {"written": True, "frames": len(keep), "copied": copied}


def floater_pass(cfg: dict, store: Storage) -> int:
    gw.set_user_agent(cfg.get("user_agent", ""))
    now = dt.datetime.now(dt.timezone.utc)
    t0 = time.time()
    deadline = arch.Deadline(arch.remaining_budget(cfg))
    # A storm STAR has no frames for is reported and keeps its last loop, and does not fail the pass: a failed pass
    # fails the half-hourly chain, and Lambda then runs the whole chain again, the catalogue's quotes included, for a
    # storm whose frames will still be missing (owner, 2026-11-07). Only a part that fails as a whole fails the pass.
    failed = 0
    try:
        out = floater_job(cfg, store, now, deadline=deadline)
        errors = len(out.get("errors") or [])
    except Exception as e:  # noqa: BLE001 - a missed pass leaves the last loops standing
        out = {"written": False, "error": f"{type(e).__name__}: {e}"}
        errors = failed = 1
    try:
        out["wpc"] = wpc_job(store, now, deadline=deadline)
    except Exception as e:  # noqa: BLE001 - the storms' loops do not wait on the analysis loop
        out["wpc"] = {"written": False, "error": f"{type(e).__name__}: {e}"}
        errors += 1
        failed = 1
    out.update({"kind": "floater", "seconds": round(time.time() - t0, 1)})
    arch.LAST_STATUS = {"job": "floater", "errors": errors, "alarms": []}
    print(json.dumps(out))
    return failed
