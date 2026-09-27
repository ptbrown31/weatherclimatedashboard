"""Tests for the GRIB2 reader behind the analysis lane.

Two sources of truth. A real message from NOMADS (a 192 by 164 cut of the
RTMA 2 m temperature, simple packing) with values recorded from eccodes in
tests/fixtures/README.md, and messages this file writes itself for the
packings the real fixture does not cover (complex packing with and without
spatial differencing, bitmaps, managed missing values); the writer was
checked against eccodes once and the README says how. No network, and
eccodes is never imported here.
"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import grib2  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
SUBGRID = os.path.join(FIXTURES, "rtma2p5_t10z_tmp_2m_subgrid.grb2")

# The values eccodes reads at named cells of the fixture (README).
SUBGRID_CELLS = {
    "first": (0, 286.03998046875),
    "KLGA": (7925, 288.35998046875),
    "KJFK": (6778, 288.31998046875003),
    "KBOS": (24714, 287.73998046875),
    "last": (31487, 288.09998046875),
}

# The sidecar of rtma2p5.20260927/rtma2p5.t10z.2dvaranl_ndfd.grb2_wexp on
# noaa-rtma-pds, as published; the file is 84,264,163 bytes.
RTMA_IDX = """\
1:0:d=2026092710:HGT:surface:anl:
2:7490118:d=2026092710:PRES:surface:anl:
3:14980236:d=2026092710:TMP:2 m above ground:anl:
4:21065993:d=2026092710:DPT:2 m above ground:anl:
5:27151750:d=2026092710:UGRD:10 m above ground:anl:
6:33237507:d=2026092710:VGRD:10 m above ground:anl:
7:38855143:d=2026092710:SPFH:2 m above ground:anl:
8:45877141:d=2026092710:WDIR:10 m above ground:anl:
9:51494777:d=2026092710:WIND:10 m above ground:anl:
10:57112413:d=2026092710:GUST:10 m above ground:anl:
11:62730049:d=2026092710:VIS:surface:anl:
12:69283926:d=2026092710:CEIL:cloud ceiling:anl:
13:77710286:d=2026092710:TCDC:entire atmosphere (considered as a single layer):anl:
"""
RTMA_LENGTH = 84264163


# ---------------------------------------------------------------------------
# A small GRIB2 writer, enough to build one field on a Lambert grid in the
# three packings the reader supports. It follows the templates octet by
# octet, so a reader can check any message it writes against the WMO tables.
# ---------------------------------------------------------------------------

class _BitWriter:
    def __init__(self):
        self.acc = 0
        self.n = 0

    def write(self, value, bits):
        if bits == 0:
            return
        if value < 0 or value >= (1 << bits):
            raise ValueError(f"{value} does not fit in {bits} bits")
        self.acc = (self.acc << bits) | value
        self.n += bits

    def align(self):
        pad = (-self.n) % 8
        self.acc <<= pad
        self.n += pad

    def prepend(self, raw):
        """Whole octets ahead of everything written so far."""
        self.acc = (int.from_bytes(raw, "big") << self.n) | self.acc
        self.n += 8 * len(raw)

    def bytes(self):
        self.align()
        return self.acc.to_bytes(self.n // 8, "big") if self.n else b""


def _section(number, body):
    return (len(body) + 5).to_bytes(4, "big") + bytes([number]) + body


def _sm(value, octets):
    """A sign-magnitude integer of the given width (regulation 92.1.5)."""
    top = 1 << (8 * octets - 1)
    if value < 0:
        return (top | -value).to_bytes(octets, "big")
    return value.to_bytes(octets, "big")


def _bits_for(value):
    return max(1, value.bit_length())


TINY_GRID = {"lat1": 40.0, "lon1": 285.0, "lov": 265.0, "latin1": 25.0, "latin2": 25.0,
             "lad": 25.0, "dx": 2539.703, "dy": 2539.703, "radius": 6371200.0}


def grib2_message(ints, ni, nj, template=0, order=0, group_len=4, R=0.0, E=0, D=0,
                  product="analysis", missing_mgmt=0, missing_at=()):
    """One GRIB2 message on a ni by nj Lambert grid. `ints` are the packed
    integers X per grid point in scanning order, None for a point the bitmap
    leaves out; a reader should return (R + X * 2**E) * 10**-D at each
    point and None at the gaps. With missing_mgmt=1 (template 5.2 only) the
    points listed in missing_at are written as the primary missing value
    inside their groups instead of through a bitmap."""
    npts = ni * nj
    assert len(ints) == npts
    missing_at = set(missing_at)
    present = [v for v in ints if v is not None]
    n = len(present)
    # Section 1: NCEP (7), reference time 2026-09-27 09:00 UTC, analysis.
    s1 = _section(1, bytes([0, 7, 0, 4, 1, 1, 1]) + (2026).to_bytes(2, "big")
                  + bytes([9, 27, 9, 0, 0, 0, 1]))
    # Section 3, template 3.30: sphere of the given radius (shape 1), then
    # Ni, Nj, first point, resolution flags, LaD, LoV, Dx, Dy, projection
    # centre, scanning mode 64, Latin1, Latin2, southern pole.
    g = TINY_GRID
    b = bytearray()
    b += bytes([0]) + npts.to_bytes(4, "big") + bytes([0, 0]) + (30).to_bytes(2, "big")
    b += bytes([1, 0]) + int(g["radius"]).to_bytes(4, "big") + bytes(5) + bytes(5)
    b += ni.to_bytes(4, "big") + nj.to_bytes(4, "big")
    b += _sm(round(g["lat1"] * 1e6), 4) + _sm(round(g["lon1"] * 1e6), 4) + bytes([8])
    b += _sm(round(g["lad"] * 1e6), 4) + _sm(round(g["lov"] * 1e6), 4)
    b += round(g["dx"] * 1e3).to_bytes(4, "big") + round(g["dy"] * 1e3).to_bytes(4, "big")
    b += bytes([0, 64]) + _sm(round(g["latin1"] * 1e6), 4) + _sm(round(g["latin2"] * 1e6), 4)
    b += _sm(-90000000, 4) + bytes(4)
    s3 = _section(3, bytes(b))
    # Section 4: template 4.0, TMP at 2 m above ground, or template 4.8, APCP
    # at the surface accumulated over the hour to 2026-09-27 10:00 UTC.
    if product == "analysis":
        b = bytes(2) + (0).to_bytes(2, "big") + bytes([0, 0, 0, 0, 109, 0, 0, 0, 1]) + bytes(4)
        b += bytes([103, 0]) + (2).to_bytes(4, "big") + bytes([255, 0]) + bytes(4)
    else:
        b = bytes(2) + (8).to_bytes(2, "big") + bytes([1, 8, 2, 0, 109, 0, 0, 0, 1]) + bytes(4)
        b += bytes([1, 0]) + bytes(4) + bytes([255, 0]) + bytes(4)
        b += (2026).to_bytes(2, "big") + bytes([9, 27, 10, 0, 0, 1]) + bytes(4)
        b += bytes([1, 2, 1]) + (1).to_bytes(4, "big") + bytes([255]) + bytes(4)
    s4 = _section(4, b)
    # Sections 5 and 7.
    head = (n.to_bytes(4, "big") + template.to_bytes(2, "big") + struct.pack(">f", R)
            + _sm(E, 2) + _sm(D, 2))
    bw = _BitWriter()
    if template == 0:
        bits = _bits_for(max(present))
        s5 = _section(5, head + bytes([bits, 0]))
        for v in present:
            bw.write(v, bits)
    else:
        vals = list(present)
        extra = b""
        if template == 3:
            # Regulation 92.9.3.7: keep the first `order` values aside,
            # difference the rest, subtract the minimum difference.
            firsts = vals[:order]
            work = list(vals)
            for j in range(len(work) - 1, order - 1, -1):
                if order == 1:
                    work[j] = work[j] - work[j - 1]
                else:
                    work[j] = work[j] - 2 * work[j - 1] + work[j - 2]
            for t in range(order):
                work[t] = 0
            minsd = min(work)
            vals = [w - minsd for w in work]
            octets = max(1, (max(abs(minsd), max(firsts)).bit_length() + 8) // 8)
            extra = b"".join(f.to_bytes(octets, "big") for f in firsts) + _sm(minsd, octets)
        groups = [vals[k:k + group_len] for k in range(0, len(vals), group_len)]
        flags = [k in missing_at for k in range(len(vals))]
        refs, widths, lens, packed = [], [], [], []
        for gi, grp in enumerate(groups):
            fl = flags[gi * group_len:(gi + 1) * group_len]
            real = [v for v, m in zip(grp, fl) if not m]
            ref = min(real) if real else 0
            span = (max(real) - ref) if real else 0
            width = _bits_for(span) if span else 0
            if missing_mgmt and any(fl):
                # All ones at the group width is the missing mark, so the
                # width must leave that pattern free.
                width = max(width, 1)
                while (1 << width) - 1 <= span:
                    width += 1
            refs.append(ref)
            widths.append(width)
            lens.append(len(grp))
            packed.append([((1 << width) - 1) if m else v - ref for v, m in zip(grp, fl)])
        bits = _bits_for(max(refs))
        if missing_mgmt:
            while (1 << bits) - 1 <= max(refs):
                bits += 1
        bits_w = _bits_for(max(widths))
        ref_l = min(lens)
        bits_l = _bits_for(max(lens) - ref_l) if max(lens) > ref_l else 0
        b = head + bytes([bits, 0, 1, missing_mgmt]) + bytes(8) + len(groups).to_bytes(4, "big")
        b += (bytes([0, bits_w]) + ref_l.to_bytes(4, "big") + bytes([1])
              + lens[-1].to_bytes(4, "big") + bytes([bits_l]))
        if template == 3:
            b += bytes([order, len(extra) // (order + 1)])
        s5 = _section(5, b)
        for v in refs:
            bw.write(v, bits)
        bw.align()
        for w in widths:
            bw.write(w, bits_w)
        bw.align()
        for L in lens:
            bw.write(L - ref_l, bits_l)
        bw.align()
        for grp, w in zip(packed, widths):
            for v in grp:
                bw.write(v, w)
        if template == 3:
            bw.prepend(extra)
    s7 = _section(7, bw.bytes())
    if any(v is None for v in ints):
        bm = _BitWriter()
        for v in ints:
            bm.write(0 if v is None else 1, 1)
        s6 = _section(6, bytes([0]) + bm.bytes())
    else:
        s6 = _section(6, bytes([255]))
    body = s1 + s3 + s4 + s5 + s6 + s7 + b"7777"
    s0 = b"GRIB" + bytes([0, 0, 0, 2]) + (16 + len(body)).to_bytes(8, "big")
    return s0 + body


def _expected(ints, R, E, D):
    return [None if x is None else (x * 2.0 ** E + R) * (1.0 / 10.0 ** D) for x in ints]


# A fixed field for the written messages: 7 by 5, values a reader can check
# by hand, with four points left out by the bitmap in the *_holes variants.
NI, NJ = 7, 5
INTS = [1783, 3951, 604, 2287, 40, 3190, 1502, 777, 2960, 12, 1234, 2000, 3999, 5, 88,
        2345, 1111, 999, 3500, 3501, 3502, 0, 4000, 2222, 1900, 250, 251, 249, 3000, 100,
        4095, 4094, 1, 2, 3]
HOLES = (3, 4, 10, 30)
INTS_HOLES = [None if k in HOLES else v for k, v in enumerate(INTS)]


class SectionWalking(unittest.TestCase):
    def setUp(self):
        with open(SUBGRID, "rb") as fh:
            self.msg = fh.read()

    def test_the_fixture_walks_into_its_sections(self):
        secs = grib2.sections(self.msg)
        self.assertEqual(sorted(secs), [0, 1, 3, 4, 5, 6, 7, 8])
        self.assertEqual(secs[0], (0, 16))
        self.assertEqual(secs[8], (len(self.msg) - 4, 4))
        # each section starts where the previous one ends
        offset = 16
        for n in (1, 3, 4, 5, 6, 7):
            self.assertEqual(secs[n][0], offset)
            offset += secs[n][1]
        self.assertEqual(offset, len(self.msg) - 4)
        self.assertEqual(secs[7][1], 43301)

    def test_a_damaged_message_is_refused(self):
        with self.assertRaises(ValueError):
            grib2.sections(b"NOPE" + self.msg[4:])
        with self.assertRaises(ValueError):
            grib2.sections(self.msg[:-4] + b"7776")
        with self.assertRaises(ValueError):
            grib2.sections(self.msg[:-100])          # shorter than the indicator says
        edition1 = self.msg[:7] + b"\x01" + self.msg[8:]
        with self.assertRaises(ValueError):
            grib2.sections(edition1)

    def test_a_second_field_in_one_message_is_refused(self):
        # Sections 3 to 7 repeated after the first field: not something
        # NCEP writes, and not something the reader should silently pick one of.
        secs = grib2.sections(self.msg)
        s3 = secs[3][0]
        s8 = secs[8][0]
        doubled = self.msg[:s8] + self.msg[s3:s8] + b"7777"
        doubled = doubled[:8] + len(doubled).to_bytes(8, "big") + doubled[16:]
        with self.assertRaises(ValueError):
            grib2.sections(doubled)


class Headers(unittest.TestCase):
    def setUp(self):
        with open(SUBGRID, "rb") as fh:
            self.msg = fh.read()
        self.tiny = grib2_message(INTS, NI, NJ, template=2, R=0.0, E=-6, D=0, product="apcp")

    def test_grid_def_of_the_fixture(self):
        g = grib2.grid_def(self.msg)
        self.assertEqual((g["Ni"], g["Nj"], g["npts"]), (192, 164, 31488))
        self.assertEqual((g["lat1"], g["lon1"]), (40.069769, 284.429957))
        self.assertEqual((g["lov"], g["latin1"], g["latin2"], g["lad"]), (265.0, 25.0, 25.0, 25.0))
        self.assertEqual((g["dx"], g["dy"], g["scan"], g["radius"]), (2539.703, 2539.703, 64, 6371200.0))

    def test_grid_def_of_a_written_message(self):
        g = grib2.grid_def(self.tiny)
        self.assertEqual((g["Ni"], g["Nj"], g["npts"], g["scan"]), (NI, NJ, NI * NJ, 64))
        for key, want in TINY_GRID.items():
            self.assertAlmostEqual(g[key], want, places=6, msg=key)

    def test_product_def_template_4_0(self):
        p = grib2.product_def(self.msg)
        self.assertEqual(p["template"], 0)
        self.assertEqual((p["discipline"], p["category"], p["number"]), (0, 0, 0))   # TMP
        self.assertEqual((p["level_type"], p["level_value"]), (103, 2.0))           # 2 m above ground
        self.assertEqual(p["forecast_hours"], 0.0)
        self.assertNotIn("acc_hours", p)
        self.assertEqual(grib2.ref_time(self.msg), (2026, 9, 27, 10, 0, 0))

    def test_product_def_template_4_8(self):
        p = grib2.product_def(self.tiny)
        self.assertEqual(p["template"], 8)
        self.assertEqual((p["discipline"], p["category"], p["number"]), (0, 1, 8))   # APCP
        self.assertEqual((p["level_type"], p["level_value"]), (1, 0.0))
        self.assertEqual(p["stat"], 1)                                              # accumulation
        self.assertEqual(p["acc_hours"], 1.0)
        self.assertEqual(p["end"], (2026, 9, 27, 10, 0, 0))
        self.assertEqual(grib2.ref_time(self.tiny), (2026, 9, 27, 9, 0, 0))

    def test_drs_def_simple_packing(self):
        d = grib2.drs_def(self.msg)
        self.assertEqual(d["template"], 0)
        self.assertEqual(d["n"], 31488)
        self.assertEqual(d["R"], 28100.998046875)
        self.assertEqual((d["E"], d["D"], d["bits"]), (0, 2, 11))
        self.assertFalse(grib2.has_bitmap(self.msg))

    def test_drs_def_complex_packing(self):
        d = grib2.drs_def(self.tiny)
        self.assertEqual(d["template"], 2)
        self.assertEqual((d["n"], d["R"], d["E"], d["D"]), (35, 0.0, -6, 0))
        self.assertEqual((d["split"], d["missing"], d["ng"]), (1, 0, 9))
        self.assertEqual((d["ref_l"], d["inc_l"], d["last_l"]), (3, 1, 3))
        self.assertFalse(grib2.has_bitmap(self.tiny))
        holes = grib2_message(INTS_HOLES, NI, NJ, template=3, order=2, R=26243.0, E=0, D=2)
        d = grib2.drs_def(holes)
        self.assertEqual((d["template"], d["order"], d["n"]), (3, 2, 31))
        self.assertTrue(grib2.has_bitmap(holes))

    def test_negative_scales_are_sign_magnitude(self):
        # 0x8006 is -6, not -32762: the top bit is a sign flag, the rest the
        # magnitude. This is the E of the real RTMA precipitation files.
        msg = grib2_message(INTS, NI, NJ, template=0, R=0.0, E=-6, D=-1)
        secs = grib2.sections(msg)
        s5 = msg[secs[5][0]:secs[5][0] + secs[5][1]]
        self.assertEqual(s5[15:17], b"\x80\x06")
        self.assertEqual(s5[17:19], b"\x80\x01")
        d = grib2.drs_def(msg)
        self.assertEqual((d["E"], d["D"]), (-6, -1))
        self.assertEqual(grib2.decode(msg)[0], (INTS[0] * 2.0 ** -6 + 0.0) * 10.0)


class Decoding(unittest.TestCase):
    def setUp(self):
        with open(SUBGRID, "rb") as fh:
            self.msg = fh.read()

    def test_the_fixture_decodes_to_the_recorded_values(self):
        field = grib2.decode(self.msg)
        self.assertEqual(len(field), 31488)
        for name, (k, want) in SUBGRID_CELLS.items():
            self.assertEqual(field[k], want, name)
        self.assertEqual(min(field), 281.00998046875003)
        self.assertEqual(max(field), 296.76998046875)
        self.assertNotIn(None, field)

    def test_random_access_equals_the_whole_field(self):
        field = grib2.decode(self.msg)
        ks = [k for k, _ in SUBGRID_CELLS.values()] + [1, 191, 192, 12345]
        self.assertEqual(grib2.decode_cells(self.msg, ks), [field[k] for k in ks])
        with self.assertRaises(IndexError):
            grib2.decode_cells(self.msg, [31488])

    def test_simple_packing_written_here(self):
        for ints in (INTS, INTS_HOLES):
            msg = grib2_message(ints, NI, NJ, template=0, R=26243.0, E=0, D=2)
            want = _expected(ints, 26243.0, 0, 2)
            self.assertEqual(grib2.decode(msg), want)
            ks = [0, 3, 4, 5, 34]
            self.assertEqual(grib2.decode_cells(msg, ks), [want[k] for k in ks])

    def test_complex_packing(self):
        for ints in (INTS, INTS_HOLES):
            for R, E, D in ((0.0, -6, 0), (0.0, 2, 4), (26243.0, 0, 2)):
                msg = grib2_message(ints, NI, NJ, template=2, R=R, E=E, D=D, product="apcp")
                want = _expected(ints, R, E, D)
                self.assertEqual(grib2.decode(msg), want, (R, E, D))
                ks = [0, 3, 10, 30, 34]
                self.assertEqual(grib2.decode_cells(msg, ks), [want[k] for k in ks])

    def test_complex_packing_with_spatial_differencing(self):
        for order in (1, 2):
            for ints in (INTS, INTS_HOLES):
                for group_len in (4, 7, 35):
                    msg = grib2_message(ints, NI, NJ, template=3, order=order, group_len=group_len,
                                        R=0.0, E=-6, D=0, product="apcp")
                    want = _expected(ints, 0.0, -6, 0)
                    self.assertEqual(grib2.decode(msg), want, (order, group_len))
                    self.assertEqual(grib2.decode_cells(msg, [0, 1, 2, 34]), [want[k] for k in (0, 1, 2, 34)])

    def test_managed_missing_values_come_back_as_none(self):
        msg = grib2_message(INTS, NI, NJ, template=2, R=0.0, E=-6, D=0, product="apcp",
                            missing_mgmt=1, missing_at=(2, 9, 20))
        want = _expected([None if k in (2, 9, 20) else v for k, v in enumerate(INTS)], 0.0, -6, 0)
        self.assertEqual(grib2.decode(msg), want)

    def test_a_constant_group_and_a_bitmap_gap_together(self):
        ints = [7] * 20 + [None] * 5 + [7, 9, 7, 9, 7, 9, 7, 9, 7, 9]
        msg = grib2_message(ints, NI, NJ, template=2, group_len=10, R=1.5, E=0, D=1, product="apcp")
        self.assertEqual(grib2.decode(msg), _expected(ints, 1.5, 0, 1))

    def test_a_constant_field_decodes_to_the_reference_value(self):
        # bitsPerValue 0: no packed values, every point is R. eccodes (and its
        # encoder, which writes D for such a field) returns R itself, not
        # R * 10**-D; a constant 288.25 field with D 2 reads back as 288.25.
        msg = grib2_message(INTS, NI, NJ, template=0, R=0.0, E=0, D=0)
        p = grib2.sections(msg)[5][0]
        const = msg[:p + 11] + struct.pack(">f", 288.25) + _sm(0, 2) + _sm(2, 2) + bytes([0]) + msg[p + 20:]
        d = grib2.drs_def(const)
        self.assertEqual((d["bits"], d["R"], d["D"]), (0, 288.25, 2))
        self.assertEqual(grib2.decode(const), [288.25] * (NI * NJ))
        self.assertEqual(grib2.decode_cells(const, [0, 17, 34]), [288.25] * 3)
        self.assertTrue(grib2.random_access(const))

    def test_missing_value_management_2_marks_the_secondary_value_in_a_constant_group(self):
        # a zero-width group whose reference is all ones less one (4094 at 12
        # bits) is missing under management 2 and a value under management 1
        ints = [4094] * 4 + list(range(31))
        msg = grib2_message(ints, NI, NJ, template=2, group_len=4, R=0.0, E=0, D=0, product="apcp", missing_mgmt=1)
        d = grib2.drs_def(msg)
        self.assertEqual((d["bits"], d["missing"]), (12, 1))
        self.assertEqual(grib2.decode(msg)[:5], [4094.0] * 4 + [0.0])
        p = grib2.sections(msg)[5][0]
        mvm2 = msg[:p + 22] + bytes([2]) + msg[p + 23:]
        self.assertEqual(grib2.drs_def(mvm2)["missing"], 2)
        self.assertEqual(grib2.decode(mvm2)[:5], [None] * 4 + [0.0])
        # in the two-bit groups both patterns (3 and 2) are missing too under
        # management 2, which is how the whole message reads
        self.assertEqual(grib2.decode(mvm2)[5:9], [1.0, None, None, 4.0])

    def test_decoding_in_slices_matches_decoding_whole(self):
        # the simple-packed field is read a slice at a time and the complex
        # one converted in place a slice at a time; a small slice exercises
        # the boundaries the real 65,536-value slice never meets in a fixture
        keep = grib2._CHUNK
        try:
            grib2._CHUNK = 1000
            field = grib2.decode(self.msg)
            for name, (k, want) in SUBGRID_CELLS.items():
                self.assertEqual(field[k], want, name)
            grib2._CHUNK = 8
            for template, order in ((2, 0), (3, 1), (3, 2)):
                msg = grib2_message(INTS_HOLES, NI, NJ, template=template, order=order, R=0.0, E=-6, D=0, product="apcp")
                self.assertEqual(grib2.decode(msg), _expected(INTS_HOLES, 0.0, -6, 0), (template, order))
        finally:
            grib2._CHUNK = keep

    def test_unsupported_packing_is_refused(self):
        msg = grib2_message(INTS, NI, NJ, template=0, R=0.0, E=0, D=0)
        secs = grib2.sections(msg)
        p = secs[5][0]
        jpeg = msg[:p + 9] + (40).to_bytes(2, "big") + msg[p + 11:]
        with self.assertRaises(ValueError):
            grib2.drs_def(jpeg)
        with self.assertRaises(ValueError):
            grib2.decode(jpeg)


class Sidecar(unittest.TestCase):
    def test_the_rtma_inventory_becomes_byte_ranges(self):
        rows = grib2.parse_idx(RTMA_IDX, total_length=RTMA_LENGTH)
        self.assertEqual(len(rows), 13)
        self.assertEqual([r["n"] for r in rows], list(range(1, 14)))
        by_var = {r["var"]: r for r in rows}
        tmp = by_var["TMP"]
        self.assertEqual((tmp["start"], tmp["end"]), (14980236, 21065992))
        self.assertEqual((tmp["level"], tmp["step"], tmp["date"]), ("2 m above ground", "anl", "2026092710"))
        self.assertEqual((by_var["WIND"]["start"], by_var["WIND"]["end"]), (51494777, 57112412))
        self.assertEqual((by_var["GUST"]["start"], by_var["GUST"]["end"]), (57112413, 62730048))
        self.assertEqual(by_var["TCDC"]["end"], RTMA_LENGTH - 1)
        self.assertEqual(by_var["TCDC"]["level"], "entire atmosphere (considered as a single layer)")
        # the TMP range is the 6,085,757 bytes of the message alone
        self.assertEqual(tmp["end"] - tmp["start"] + 1, 6085757)

    def test_without_the_file_length_the_last_end_is_open(self):
        rows = grib2.parse_idx(RTMA_IDX)
        self.assertEqual(rows[-1]["end"], None)
        self.assertEqual(rows[-2]["end"], 77710285)

    def test_a_one_message_precipitation_sidecar(self):
        rows = grib2.parse_idx("1:0:d=2026092709:APCP:surface:0-1 hour acc fcst:\n", total_length=656016)
        self.assertEqual(rows, [{"n": 1, "start": 0, "end": 656015, "date": "2026092709",
                                 "var": "APCP", "level": "surface", "step": "0-1 hour acc fcst"}])
        self.assertEqual(grib2.parse_idx(""), [])
        with self.assertRaises(ValueError):
            grib2.parse_idx("1:0:junk\n")


class Lambert(unittest.TestCase):
    def test_forward_then_inverse_round_trips(self):
        for grid in (grib2.WEXP, grib2.G184):
            for lat in range(20, 55, 3):
                for lon in range(-125, -65, 4):
                    fi, fj = grib2.lcc_ij(grid, lat, lon)
                    la, lo = grib2.lcc_latlon(grid, fi, fj)
                    self.assertAlmostEqual(la, lat, delta=1e-6)
                    self.assertAlmostEqual(lo, lon, delta=1e-6)

    def test_the_first_grid_point_is_the_origin(self):
        for grid in (grib2.WEXP, grib2.G184):
            fi, fj = grib2.lcc_ij(grid, grid["lat1"], grid["lon1"])
            self.assertAlmostEqual(fi, 0.0, delta=1e-9)
            self.assertAlmostEqual(fj, 0.0, delta=1e-9)
            la, lo = grib2.lcc_latlon(grid, 0, 0)
            self.assertAlmostEqual(la, grid["lat1"], delta=1e-6)
            self.assertAlmostEqual(lo, grid["lon1"] - 360.0, delta=1e-6)

    def test_longitude_on_either_convention(self):
        a = grib2.lcc_ij(grib2.WEXP, 40.7792, -73.88)
        b = grib2.lcc_ij(grib2.WEXP, 40.7792, 286.12)
        self.assertAlmostEqual(a[0], b[0], delta=1e-9)
        self.assertAlmostEqual(a[1], b[1], delta=1e-9)

    def test_klga_lands_on_the_probe_cell(self):
        # The probe of 2026-09-27: fi 2014.075, fj 860.640, so i 2014, j 861,
        # which eccodes' find_nearest also picks, 0.90 km from the airport.
        fi, fj = grib2.lcc_ij(grib2.WEXP, 40.7792, -73.8800)
        self.assertAlmostEqual(fi, 2014.075, delta=1e-3)
        self.assertAlmostEqual(fj, 860.640, delta=1e-3)
        self.assertEqual(grib2.nearest_cell(grib2.WEXP, 40.7792, -73.8800), (2014, 861, 2021059))
        la, lo = grib2.lcc_latlon(grib2.WEXP, 2014, 861)
        self.assertAlmostEqual(la, 40.7873, delta=5e-5)
        self.assertAlmostEqual(lo, -73.8805, delta=5e-5)
        self.assertEqual(grib2.nearest_cell(grib2.G184, 40.7792, -73.8800), (1814, 861, 1848659))

    def test_nearest_cell_on_the_fixture_grid_agrees_with_eccodes(self):
        with open(SUBGRID, "rb") as fh:
            grid = grib2.grid_def(fh.read())
        self.assertEqual(grib2.nearest_cell(grid, 40.7792, -73.8800), (53, 41, 7925))     # KLGA
        self.assertEqual(grib2.nearest_cell(grid, 40.6398, -73.7789), (58, 35, 6778))     # KJFK
        self.assertEqual(grib2.nearest_cell(grid, 42.3606, -71.0106), (138, 128, 24714))  # KBOS

    def test_rounding_is_half_up_and_outside_is_none(self):
        # Points are put through the inverse and back, which lands within
        # 1e-13 of the fraction asked for, so the half is approached from
        # either side rather than hit exactly.
        grid = dict(grib2.WEXP)
        la, lo = grib2.lcc_latlon(grid, 100.5 + 1e-6, 200.5 + 1e-6)
        self.assertEqual(grib2.nearest_cell(grid, la, lo)[:2], (101, 201))
        la, lo = grib2.lcc_latlon(grid, 100.5 - 1e-6, 200.5 - 1e-6)
        self.assertEqual(grib2.nearest_cell(grid, la, lo)[:2], (100, 200))
        self.assertIsNone(grib2.nearest_cell(grid, 10.0, -95.0))       # south of the grid
        self.assertIsNone(grib2.nearest_cell(grid, 60.0, -95.0))       # north of it
        la, lo = grib2.lcc_latlon(grid, -0.51, 500)
        self.assertIsNone(grib2.nearest_cell(grid, la, lo))
        la, lo = grib2.lcc_latlon(grid, 2344.49, 500)
        self.assertEqual(grib2.nearest_cell(grid, la, lo)[0], 2344)
        la, lo = grib2.lcc_latlon(grid, 2344.51, 500)
        self.assertIsNone(grib2.nearest_cell(grid, la, lo))
        la, lo = grib2.lcc_latlon(grid, 500, 1596.51)
        self.assertIsNone(grib2.nearest_cell(grid, la, lo))


class Grids(unittest.TestCase):
    def test_same_grid_tolerances(self):
        self.assertTrue(grib2.same_grid(grib2.WEXP, dict(grib2.WEXP)))
        self.assertFalse(grib2.same_grid(grib2.WEXP, grib2.G184))
        near = dict(grib2.WEXP, lat1=19.228977, dx=2539.709)
        self.assertTrue(grib2.same_grid(grib2.WEXP, near))
        self.assertFalse(grib2.same_grid(grib2.WEXP, dict(grib2.WEXP, lov=265.001)))
        self.assertFalse(grib2.same_grid(grib2.WEXP, dict(grib2.WEXP, dx=2539.72)))
        self.assertFalse(grib2.same_grid(grib2.WEXP, dict(grib2.WEXP, scan=0)))
        self.assertFalse(grib2.same_grid(grib2.WEXP, dict(grib2.WEXP, Ni=2344)))

    def test_the_constants_match_the_contract(self):
        self.assertEqual((grib2.WEXP["Ni"], grib2.WEXP["Nj"], grib2.WEXP["npts"]), (2345, 1597, 3744965))
        self.assertEqual((grib2.G184["Ni"], grib2.G184["Nj"], grib2.G184["npts"]), (2145, 1377, 2953665))
        self.assertEqual(sorted(grib2.WEXP), sorted(grib2.G184))
        with open(SUBGRID, "rb") as fh:
            self.assertEqual(sorted(grib2.grid_def(fh.read())), sorted(grib2.WEXP))

    def test_a_message_on_another_grid_is_refused(self):
        # The fixture is a cut of the wexp grid: same projection, different
        # extent. A job that took it for the full grid would read cell
        # 2021059 off the end of the field, so the check has to fail.
        with open(SUBGRID, "rb") as fh:
            grid = grib2.grid_def(fh.read())
        self.assertFalse(grib2.same_grid(grid, grib2.WEXP))
        self.assertFalse(grib2.same_grid(grid, grib2.G184))
        tiny = grib2.grid_def(grib2_message(INTS, NI, NJ))
        self.assertFalse(grib2.same_grid(tiny, grib2.WEXP))

    def test_other_grid_templates_are_refused(self):
        msg = grib2_message(INTS, NI, NJ)
        p = grib2.sections(msg)[3][0]
        latlon = msg[:p + 12] + (0).to_bytes(2, "big") + msg[p + 14:]
        with self.assertRaises(ValueError):
            grib2.grid_def(latlon)
        ellipsoid = msg[:p + 14] + bytes([2]) + msg[p + 15:]
        with self.assertRaises(ValueError):
            grib2.grid_def(ellipsoid)


if __name__ == "__main__":
    unittest.main()
