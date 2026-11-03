"""The satellite loop lane: STAR's floater listing becomes a set of frames in
this site's bucket and an index the page plays, a later pass copies only the
new frames and deletes the ones that fall out of the loop, and a storm that
leaves NHC's roster takes its frames with it. No network."""
import datetime as dt
import json
import math
import os
import struct
import sys
import tempfile
import unittest
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pipeline import basemap, floater, storage   # noqa: E402

U = dt.timezone.utc
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


def listing(sid, stamps, sat="GOES19"):
    rows = []
    for st in stamps:
        for size in ("250x250", "500x500", "1000x1000"):
            rows.append('<a href="%s_%s-ABI-FL-GEOCOLOR-%s-%s.jpg">x</a>' % (st, sat, sid, size))
    rows.append('<a href="%s-GEOCOLOR-1000x1000.gif">loop</a>' % sid)
    return "<html><body>" + "\n".join(rows) + "</body></html>"


def stamps(n, start="20262830000"):
    """n consecutive ten-minute stamps from start (YYYYDDDHHMM)."""
    t = dt.datetime.strptime(start[:4], "%Y") + dt.timedelta(days=int(start[4:7]) - 1, hours=int(start[7:9]),
                                                            minutes=int(start[9:11]))
    out = []
    for k in range(n):
        u = t + dt.timedelta(minutes=10 * k)
        out.append("%04d%03d%02d%02d" % (u.year, u.timetuple().tm_yday, u.hour, u.minute))
    return out


class Listing(unittest.TestCase):
    def test_the_500_pixel_geocolor_frames_of_the_storm_oldest_first(self):
        html = listing("AL092026", ["20262831000", "20262830950"]) + listing("EP202026", ["20262831000"], "GOES18")
        got = floater.parse_listing(html, "AL092026")
        self.assertEqual([g[0] for g in got], ["20262830950", "20262831000"])
        self.assertEqual(got[-1][1], "20262831000_GOES19-ABI-FL-GEOCOLOR-AL092026-500x500.jpg")

    def test_a_stamp_is_the_day_of_the_year_and_the_time_in_utc(self):
        self.assertEqual(floater.stamp_time("20262831000"), "2026-10-10T10:00Z")
        self.assertEqual(floater.stamp_time("20260010005"), "2026-01-01T00:05Z")


class Job(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = storage.LocalStorage(self.tmp.name)
        self.lists = {}
        self.calls = []
        self.broken = set()
        self.roster(["al092026"])

    def roster(self, ids):
        self.st.put("snapshots/hurricane.json", json.dumps({"storms": [
            {"id": i, "name": {"al092026": "Isaias", "ep202026": "Simon"}.get(i, i), "basin": i[:2].upper()} for i in ids]}).encode())

    def fetch(self, url):
        self.calls.append(url)
        for sid, html in self.lists.items():
            if sid in self.broken and sid in url:
                raise OSError("STAR timed out")
            if url == floater.listing_url(sid):
                return html.encode()
            if url.startswith(floater.listing_url(sid)) and url.endswith(".jpg"):
                return JPEG
        raise AssertionError(url)

    def run_at(self, minute=0):
        return floater.floater_job({}, self.st, dt.datetime(2026, 10, 10, 10, minute, tzinfo=U), fetch=self.fetch)

    def index(self):
        return json.loads(self.st.get(floater.INDEX))

    def frames(self, sid):
        return [k.rsplit("/", 1)[1] for k in self.st.list(floater.PREFIX + sid + "/")]

    def test_a_first_pass_copies_the_newest_three_hours_and_indexes_them(self):
        s = stamps(24)
        self.lists["AL092026"] = listing("AL092026", s)
        out = self.run_at()
        self.assertEqual((out["storms"], out["copied"], out["errors"]), (1, 18, []))
        self.assertEqual(self.frames("AL092026"), [x + ".jpg" for x in s[-18:]])
        idx = self.index()["storms"]["AL092026"]
        self.assertEqual((idx["name"], idx["satellite"], idx["product"]), ("Isaias", "GOES-19", "GeoColor"))
        self.assertEqual(idx["frames"][-1], {"key": "floater/AL092026/%s.jpg" % s[-1], "t": floater.stamp_time(s[-1])})
        self.assertEqual(len(idx["frames"]), 18)

    def test_a_later_pass_copies_only_new_frames_and_drops_the_oldest(self):
        s = stamps(21)
        self.lists["AL092026"] = listing("AL092026", s[:18])
        self.run_at()
        self.lists["AL092026"] = listing("AL092026", s)
        n0 = len(self.calls)
        out = self.run_at(30)
        self.assertEqual(out["copied"], 3)
        self.assertEqual(len(self.calls) - n0, 1 + 3)                  # the listing and three frames
        self.assertEqual(self.frames("AL092026"), [x + ".jpg" for x in s[-18:]])

    def test_a_storm_that_leaves_the_roster_takes_its_frames_with_it(self):
        self.roster(["al092026", "ep202026"])
        self.lists["AL092026"] = listing("AL092026", stamps(5))
        self.lists["EP202026"] = listing("EP202026", stamps(5), "GOES18")
        self.run_at()
        self.assertEqual(self.index()["storms"]["EP202026"]["satellite"], "GOES-18")
        self.roster(["al092026"])
        self.run_at(30)
        self.assertEqual(sorted(self.index()["storms"]), ["AL092026"])
        self.assertEqual(self.frames("EP202026"), [])

    def test_a_storm_star_cannot_serve_keeps_its_last_loop(self):
        self.roster(["al092026", "ep202026"])
        self.lists["AL092026"] = listing("AL092026", stamps(5))
        self.lists["EP202026"] = listing("EP202026", stamps(5), "GOES18")
        self.run_at()
        before = self.index()["storms"]["EP202026"]
        self.broken = {"EP202026"}
        self.lists["AL092026"] = listing("AL092026", stamps(6))
        out = self.run_at(30)
        self.assertTrue(any(e.startswith("EP202026:") for e in out["errors"]))
        self.assertEqual(self.index()["storms"]["EP202026"], before)
        self.assertEqual(len(self.frames("EP202026")), 5)               # still there to be played
        self.assertEqual(len(self.index()["storms"]["AL092026"]["frames"]), 6)

    def test_a_frame_that_is_not_a_jpeg_is_refused(self):
        self.lists["AL092026"] = listing("AL092026", stamps(2))
        good = self.fetch
        self.fetch = lambda url: b"<html>error</html>" if url.endswith(".jpg") else good(url)
        out = self.run_at()
        self.assertTrue(out["errors"])
        self.assertNotIn("AL092026", self.index()["storms"])


FIXTURES = os.path.join(ROOT, "tests", "fixtures")
PALETTE = [((i * 37) % 256, (i * 91) % 256, (i * 53) % 256) for i in range(255)] + [(0, 0, 0)]


def gif_bytes(w, h, pixels, palette=PALETTE):
    """A GIF whose LZW stream never grows past nine bits: a clear code every
    254 values keeps the table short, so the test needs no LZW encoder."""
    codes = [256]
    for n, p in enumerate(pixels, 1):
        codes.append(p)
        if n % 254 == 0:
            codes.append(256)
    codes.append(257)
    buf = nb = 0
    raw = bytearray()
    for c in codes:
        buf |= c << nb
        nb += 9
        while nb >= 8:
            raw.append(buf & 255)
            buf >>= 8
            nb -= 8
    if nb:
        raw.append(buf & 255)
    blocks = b"".join(bytes([len(raw[i:i + 255])]) + bytes(raw[i:i + 255]) for i in range(0, len(raw), 255)) + b"\x00"
    return (b"GIF87a" + struct.pack("<HHBBB", w, h, 0xF7, 0, 0) + b"".join(bytes(c) for c in palette)
            + b"\x2c" + struct.pack("<HHHHB", 0, 0, w, h, 0) + b"\x08" + blocks + b"\x3b")


def merc_px(lon, lat):
    """Where WPC's Mercator puts a point in its 900 by 675 image."""
    return (round(floater.WPC_A + floater.WPC_B * lon),
            round(floater.WPC_C + floater.WPC_D * math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))))


DENVER = (-104.67, 39.86)


def wpc_gif():
    """A 900 by 675 analysis: index 7 everywhere, 200 in a 5 by 5 block where WPC's Mercator puts Denver."""
    px = bytearray([7]) * (900 * 675)
    x, y = merc_px(*DENVER)
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            px[(y + dy) * 900 + x + dx] = 200
    return gif_bytes(900, 675, bytes(px))


def png_pixels(png):
    """(width, height, pixels) of the 8-bit palette PNGs png_indexed writes."""
    pos, idat, w, h = 8, b"", 0, 0
    while pos < len(png):
        n, kind = struct.unpack(">I4s", png[pos:pos + 8])
        body = png[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            w, h = struct.unpack(">II", body[:8])
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    raw = zlib.decompress(idat)
    return w, h, b"".join(raw[r * (w + 1) + 1:(r + 1) * (w + 1)] for r in range(h))


def png_alpha(png):
    """{palette index: alpha} from a PNG's tRNS chunk; entries it does not list are opaque."""
    pos = 8
    while pos < len(png):
        n, kind = struct.unpack(">I4s", png[pos:pos + 8])
        if kind == b"tRNS":
            return dict(enumerate(png[pos + 8:pos + 8 + n]))
        pos += 12 + n
    return {}


_GIF = wpc_gif()


def gif(w=900, h=675):
    return _GIF if (w, h) == (900, 675) else gif_bytes(w, h, bytes(w * h))


class WpcOnMap(unittest.TestCase):
    """WPC's Mercator image carried onto the landing map's projection, at the scale
    that holds all of WPC's picture (owner, 2026-10-20)."""
    def test_the_decoder_reads_a_gif_another_encoder_packed(self):
        with open(os.path.join(FIXTURES, "lzw_61x37.gif"), "rb") as fh:
            w, h, palette, px = floater.gif_pixels(fh.read())
        with open(os.path.join(FIXTURES, "lzw_61x37.pixels"), "rb") as fh:
            self.assertEqual((w, h, px), (61, 37, fh.read()))
        self.assertEqual(len(palette), 256)

    def test_a_place_on_wpc_lands_where_the_frame_draws_it(self):
        png = floater.wpc_on_map(_GIF)
        w, h, px = png_pixels(png)
        tr, fw, fh = floater.wpc_frame()
        self.assertEqual((w, h), (fw, fh))
        sx, sy = tr.project(*DENVER)
        self.assertEqual(px[int(sy) * w + int(sx)], 200)
        # Kansas City is plain analysis and opaque; the frame's top left corner lies outside
        # WPC's picture and is transparent
        kx, ky = tr.project(-94.71, 39.30)
        alpha = png_alpha(png)
        self.assertEqual(px[int(ky) * w + int(kx)], 7)
        self.assertEqual((alpha.get(7, 255), alpha.get(px[0], 255)), (255, 0))

    def test_the_frame_holds_all_of_wpcs_picture_across_the_maps_width(self):
        tr, fw, fh = floater.wpc_frame()
        self.assertEqual((fw, fh), (960, 583))
        # the lower 48 at about two thirds of their size on the landing map
        land = basemap.Transform.from_json(basemap.load_field_grid()["transform"])
        self.assertAlmostEqual(tr.S / land.S, 0.668, places=3)
        x0, x1 = floater.WPC_KEY_X, floater.WPC_SIZE[0] - 1
        y0, y1 = floater.WPC_ROWS
        for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1), ((x0 + x1) // 2, y0), ((x0 + x1) // 2, y1)):
            sx, sy = tr.project(*floater.wpc_lonlat(x, y))
            self.assertTrue(0 <= sx <= fw and 0 <= sy <= fh, (x, y, sx, sy))

    def test_the_colour_key_the_title_bands_and_the_title_block_are_left_out(self):
        px = bytearray([7]) * (900 * 675)
        for y in range(675):
            for x in range(900):
                if not floater.in_picture(x, y):
                    px[y * 900 + x] = 99
        self.assertTrue(floater.in_picture(400, 300))
        self.assertFalse(floater.in_picture(30, 300) or floater.in_picture(400, 10)
                         or floater.in_picture(400, 640) or floater.in_picture(200, 600))
        w, h, out = png_pixels(floater.wpc_on_map(gif_bytes(900, 675, bytes(px))))
        self.assertNotIn(99, set(out))

    def test_an_image_of_another_size_is_refused(self):
        with self.assertRaises(ValueError):
            floater.wpc_on_map(gif_bytes(40, 30, bytes(1200)))


class WpcLoop(unittest.TestCase):
    """The surface analysis loop under the landing map (owner, 2026-10-20)."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = storage.LocalStorage(self.tmp.name)
        # when WPC last wrote each hour's file: today's 00Z to 09Z, yesterday's 12Z to 21Z
        self.written = {"00": "2026-10-20T02:10:04Z", "03": "2026-10-20T04:44:06Z", "06": "2026-10-20T08:07:04Z",
                        "09": "2026-10-20T10:40:05Z", "12": "2026-10-19T14:16:05Z", "15": "2026-10-19T16:45:23Z",
                        "18": "2026-10-19T20:06:09Z", "21": "2026-10-19T23:10:04Z"}
        self.bad = set()
        self.gets = []

    def meta_fetch(self, url, meta):
        hh = url[-6:-4]
        meta["lastModified"] = self.written[hh]
        return gif()[:16]

    def fetch(self, url):
        self.gets.append(url)
        return b"<html>busy</html>" if url[-6:-4] in self.bad else gif()

    def run_job(self):
        return floater.wpc_job(self.st, dt.datetime(2026, 10, 20, 11, 0, tzinfo=U), fetch=self.fetch,
                               meta_fetch=self.meta_fetch)

    def index(self):
        return json.loads(self.st.get(floater.WPC_INDEX))

    def test_an_image_is_the_analysis_at_its_hour_before_it_was_written(self):
        self.assertEqual(floater.analysis_time("21", "2026-10-19T23:10:04Z"), dt.datetime(2026, 10, 19, 21, tzinfo=U))
        self.assertEqual(floater.analysis_time("00", "2026-10-20T02:10:04Z"), dt.datetime(2026, 10, 20, 0, tzinfo=U))
        # written a whole day ago and not since: not the last 24 hours
        self.assertIsNone(floater.analysis_time("12", "2026-10-19T02:16:05Z"))
        self.assertEqual(floater.gif_size(gif()), (900, 675))
        self.assertIsNone(floater.gif_size(b"\xff\xd8\xff\xe0"))

    def test_the_eight_analyses_oldest_first(self):
        out = self.run_job()
        self.assertEqual((out["frames"], out["copied"]), (8, 8))
        idx = self.index()
        self.assertEqual([f["t"] for f in idx["frames"]],
                         ["2026-10-19T12:00Z", "2026-10-19T15:00Z", "2026-10-19T18:00Z", "2026-10-19T21:00Z",
                          "2026-10-20T00:00Z", "2026-10-20T03:00Z", "2026-10-20T06:00Z", "2026-10-20T09:00Z"])
        self.assertEqual(idx["frames"][-1], {"t": "2026-10-20T09:00Z", "key": "wpcsfc/2026102009-960x583.png",
                                             "w": 960, "h": 583})

    def test_a_later_pass_copies_only_the_new_analysis_and_drops_the_one_it_replaced(self):
        self.run_job()
        self.written["12"] = "2026-10-20T14:20:00Z"
        n0 = len(self.gets)
        out = self.run_job()
        self.assertEqual((out["copied"], len(self.gets) - n0), (1, 1))
        keys = sorted(k.rsplit("/", 1)[1] for k in self.st.list(floater.WPC_PREFIX) if not k.endswith("index.json"))
        self.assertNotIn("2026101912-960x583.png", keys)
        self.assertEqual((len(keys), keys[-1]), (8, "2026102012-960x583.png"))

    def test_analyses_stored_in_an_earlier_frame_are_drawn_again(self):
        # the frames of 2026-10-20 morning, on the landing map's 960 by 600 frame
        for hh in ("2026101921", "2026102000", "2026102003"):
            self.st.put(floater.WPC_PREFIX + hh + ".png", b"old", "image/png")
        out = self.run_job()
        self.assertEqual(out["copied"], 8)
        keys = sorted(k.rsplit("/", 1)[1] for k in self.st.list(floater.WPC_PREFIX) if not k.endswith("index.json"))
        self.assertEqual(len(keys), 8)
        self.assertTrue(all(k.endswith("-960x583.png") for k in keys), keys)

    def test_an_image_that_is_not_a_gif_leaves_the_last_loop(self):
        self.run_job()
        before = self.index()
        self.written["12"] = "2026-10-20T14:20:00Z"
        self.bad = {"12"}
        with self.assertRaises(RuntimeError):
            self.run_job()
        self.assertEqual(self.index(), before)


if __name__ == "__main__":
    unittest.main()
