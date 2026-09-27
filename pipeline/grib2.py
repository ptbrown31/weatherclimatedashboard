"""
grib2.py — a GRIB2 reader in the standard library, for the analysis lane.

Pure parsing, no network. It reads the two things the lane needs from NCEP's
RTMA and URMA files: one message's metadata (grid, product, packing) and its
field, either whole or at a handful of cells. Everything else GRIB2 can carry
(other grid templates, other packings, ensembles, JPEG and PNG packing) is
refused with a ValueError rather than guessed at.

    sections(msg)       -> {section number: (offset, length)}
    grid_def(msg)       -> template 3.30, Lambert conformal on a sphere
    product_def(msg)    -> templates 4.0 and 4.8
    drs_def(msg)        -> templates 5.0, 5.2 and 5.3
    has_bitmap(msg)     -> section 6 indicator
    decode(msg)         -> the whole field, None where the bitmap says missing
    decode_cells(msg, ks)   -> a few cells, without decoding the field when
                               the packing allows it (5.0, no bitmap)
    random_access(msg)  -> whether decode_cells can do that for this message
    parse_idx(text)     -> the .idx sidecar as message byte ranges
    lcc_ij / lcc_latlon / nearest_cell   -> the Lambert projection, both ways
    WEXP, G184, same_grid   -> the two grids the lane accepts

Every multi-octet integer in GRIB2 is big-endian. Signed integers are not
two's complement: the most significant bit is a sign flag and the rest is the
magnitude (WMO FM 92 GRIB2 regulation 92.1.5). The binary scale E and the
decimal scale D of section 5 are the ones that matter here, both 16 bits; the
latitudes and longitudes of section 3 and the scaled level values of
section 4 follow the same rule at 32 bits.

A packed value X becomes Y = (R + X * 2**E) * 10**-D. The order of operations
is eccodes' (grib_accessor_class_data_g2simple_packing.c and
data_g22order_packing.c), so the floats this module returns agree with what
eccodes returns to the last bit; the alternative R/10**D + X*2**E/10**D
differs in the last place for some D.

Bytes and offsets in the comments below are zero-based within the section,
one less than the octet numbers in the WMO tables.
"""

from __future__ import annotations
import math
import struct
from typing import Optional

# ---------------------------------------------------------------------------
# 1. SECTION WALKING
# ---------------------------------------------------------------------------


def _u(b: bytes) -> int:
    return int.from_bytes(b, "big")


def _s(b: bytes) -> int:
    """A sign-magnitude integer of any width (regulation 92.1.5): the top bit
    of the first octet is the sign, the remaining bits are the magnitude."""
    raw = int.from_bytes(b, "big")
    top = 1 << (8 * len(b) - 1)
    return -(raw & (top - 1)) if raw & top else raw


def sections(msg: bytes) -> dict:
    """Walk one message and return {section number: (offset, length)}.

    Section 0 is the 16-octet indicator ('GRIB', discipline at octet 7,
    edition at 8, total length at 9 to 16); sections 1 to 7 each begin with a
    four-octet length and a one-octet number; section 8 is the four octets
    '7777'. Section 2 (local use) is optional. A message may in principle
    repeat sections 3 to 7 to carry several fields; NCEP's analysis files
    carry one field per message and a repeat is refused here so a second field
    can never be mistaken for the first.
    """
    if len(msg) < 16 or msg[:4] != b"GRIB":
        raise ValueError("not a GRIB message")
    if msg[7] != 2:
        raise ValueError(f"GRIB edition {msg[7]}, not 2")
    total = _u(msg[8:16])
    if total > len(msg):
        raise ValueError(f"message is {len(msg)} bytes, the indicator says {total}")
    if msg[total - 4:total] != b"7777":
        raise ValueError("message does not end in 7777")
    out = {0: (0, 16), 8: (total - 4, 4)}
    p = 16
    while p < total - 4:
        ln = _u(msg[p:p + 4])
        sec = msg[p + 4]
        if ln < 5 or p + ln > total - 4 or sec < 1 or sec > 7:
            raise ValueError(f"bad section header at offset {p}")
        if sec in out:
            raise ValueError(f"section {sec} repeats: more than one field in the message")
        out[sec] = (p, ln)
        p += ln
    for sec in (1, 3, 4, 5, 6, 7):
        if sec not in out:
            raise ValueError(f"section {sec} missing")
    return out


def _section(msg: bytes, secs: dict, n: int) -> bytes:
    p, ln = secs[n]
    return msg[p:p + ln]


# ---------------------------------------------------------------------------
# 2. THE HEADERS: SECTIONS 1, 3, 4, 5, 6
# ---------------------------------------------------------------------------

# Code table 3.2, shape of the earth. Only spheres: the Lambert maths below is
# spherical, and every NCEP 2.5 km grid is on the 6,371,200 m sphere (shape 1
# with the radius written into the section).
_SPHERES = {0: 6367470.0, 6: 6371229.0, 8: 6371200.0}


def ref_time(msg: bytes) -> "tuple":
    """The reference time of section 1 as (year, month, day, hour, minute,
    second). For an analysis it is the valid time; for an accumulation
    (template 4.8) it is the start of the window."""
    b = _section(msg, sections(msg), 1)
    return (_u(b[12:14]), b[14], b[15], b[16], b[17], b[18])


def grid_def(msg: bytes) -> dict:
    """Section 3, grid definition template 3.30 (Lambert conformal).

    Keys: Ni, Nj (points along a row and rows), lat1, lon1 (first grid point,
    degrees, longitude 0 to 360 as written), lov (the orientation longitude),
    latin1, latin2 (the standard parallels), lad (the latitude at which Dx and
    Dy are true), dx, dy (metres), scan (scanning mode flags), radius (metres)
    and npts (the number of data points, which the bitmap and the decoders
    need). Any other template or a non-spherical earth is refused.
    """
    b = _section(msg, sections(msg), 3)
    template = _u(b[12:14])
    if template != 30:
        raise ValueError(f"grid template 3.{template}, not 3.30 (Lambert conformal)")
    shape = b[14]
    if shape == 1:
        # Sphere with the radius given by the producer: scale factor at 15,
        # scaled value at 16 to 19.
        radius = _u(b[16:20]) / 10.0 ** b[15]
    elif shape in _SPHERES:
        radius = _SPHERES[shape]
    else:
        raise ValueError(f"shape of the earth {shape} is not a sphere")
    return {
        "Ni": _u(b[30:34]), "Nj": _u(b[34:38]),
        "lat1": _s(b[38:42]) / 1e6, "lon1": _s(b[42:46]) / 1e6,
        "lad": _s(b[47:51]) / 1e6, "lov": _s(b[51:55]) / 1e6,
        "dx": _u(b[55:59]) / 1e3, "dy": _u(b[59:63]) / 1e3,
        "scan": b[64],
        "latin1": _s(b[65:69]) / 1e6, "latin2": _s(b[69:73]) / 1e6,
        "radius": radius, "npts": _u(b[6:10]),
    }


# Code table 4.4, indicator of unit of time range, as hours.
_TIME_UNIT_HOURS = {0: 1 / 60, 1: 1.0, 2: 24.0, 10: 3.0, 11: 6.0, 12: 12.0, 13: 1 / 3600}


def product_def(msg: bytes) -> dict:
    """Section 4, product definition templates 4.0 (analysis or forecast at a
    point in time) and 4.8 (statistically processed over a window).

    Keys: discipline (from the indicator), category, number (parameter
    category and number within the discipline), level_type, level_value (the
    first fixed surface, scaled value applied), template, forecast_hours, and
    for 4.8 also stat (code table 4.10; 1 is accumulation) and acc_hours (the
    length of the window). The lane's messages are discipline 0, category 0
    number 0 (TMP), category 2 numbers 1 and 22 (WIND, GUST) at level type 103
    (height above ground) and category 1 number 8 (APCP) accumulated over one
    hour.
    """
    secs = sections(msg)
    b = _section(msg, secs, 4)
    template = _u(b[7:9])
    if template not in (0, 8):
        raise ValueError(f"product template 4.{template}, not 4.0 or 4.8")
    unit = b[17]
    out = {
        "discipline": msg[6], "category": b[9], "number": b[10], "template": template,
        "level_type": b[22], "level_value": _s(b[24:28]) / 10.0 ** _s(b[23:24]),
        "forecast_hours": _u(b[18:22]) * _TIME_UNIT_HOURS.get(unit, float("nan")),
    }
    if template == 8:
        # Octets 35 to 41 (zero-based 34 to 40) are the end of the overall
        # window; 42 the number of time ranges, then each range: statistical
        # process, type of time increment, unit and length of the range, unit
        # and length of the increment. One range is all NCEP writes here.
        n_ranges = b[41]
        if n_ranges != 1:
            raise ValueError(f"{n_ranges} time ranges in template 4.8, expected 1")
        out["stat"] = b[46]
        out["acc_hours"] = _u(b[49:53]) * _TIME_UNIT_HOURS.get(b[48], float("nan"))
        out["end"] = (_u(b[34:36]), b[36], b[37], b[38], b[39], b[40])
    return out


def drs_def(msg: bytes) -> dict:
    """Section 5, data representation templates 5.0 (simple packing), 5.2
    (complex packing) and 5.3 (complex packing with spatial differencing).

    Keys: template, n (number of packed values), R (reference value, IEEE
    single), E (binary scale), D (decimal scale), bits (per packed value).
    E and D are sign-magnitude 16-bit integers. For 5.2 and 5.3 also the group
    parameters: split, missing, ng, ref_w, bits_w, ref_l, inc_l, last_l,
    bits_l; for 5.3 also order and extra (octets per extra descriptor).
    """
    b = _section(msg, sections(msg), 5)
    template = _u(b[9:11])
    if template not in (0, 2, 3):
        raise ValueError(f"data representation template 5.{template} is not supported")
    out = {
        "template": template, "n": _u(b[5:9]),
        "R": struct.unpack(">f", b[11:15])[0],
        "E": _s(b[15:17]), "D": _s(b[17:19]), "bits": b[19],
    }
    if template in (2, 3):
        out.update({
            "split": b[21], "missing": b[22],
            "ng": _u(b[31:35]), "ref_w": b[35], "bits_w": b[36],
            "ref_l": _u(b[37:41]), "inc_l": b[41], "last_l": _u(b[42:46]), "bits_l": b[46],
        })
    if template == 3:
        out["order"] = b[47]
        out["extra"] = b[48]
    return out


def has_bitmap(msg: bytes) -> bool:
    """Section 6. Indicator 0 means a bitmap follows in this section, 255
    means none. 1 to 254 are predefined bitmaps the centre keeps elsewhere;
    NCEP never writes them and they are refused."""
    b = _section(msg, sections(msg), 6)
    ind = b[5]
    if ind == 0:
        return True
    if ind == 255:
        return False
    raise ValueError(f"bitmap indicator {ind} refers to a predefined bitmap")


# ---------------------------------------------------------------------------
# 3. THE FIELD: SECTION 7
#
# The live RTMA and URMA files use 5.0 for the analysis fields and 5.2 with a
# bitmap for the same-day precipitation. The URMA precipitation files the
# River Forecast Centers rerun a day or more later come back as 5.3 (spatial
# differencing, managed missing values, no bitmap), so both complex packings
# are live paths, not fixtures.
#
# Memory: a field is 3.7 million points. The decoders below never hold the
# packed integers and the floats of a whole field at the same time (the ints
# are converted in place, in chunks), because the lane runs in a 512 MB
# Lambda beside its own frames and the lattice.
# ---------------------------------------------------------------------------

# values converted per slice, so at most this many packed ints and their
# floats are alive together
_CHUNK = 1 << 16


class _Bits:
    """A bit reader over section 7's payload, most significant bit first."""
    __slots__ = ("data", "pos")

    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    def read(self, n: int) -> int:
        if n == 0:
            return 0
        byte = self.pos >> 3
        off = self.pos & 7
        nb = (off + n + 7) >> 3
        chunk = int.from_bytes(self.data[byte:byte + nb], "big")
        self.pos += n
        return (chunk >> (nb * 8 - off - n)) & ((1 << n) - 1)

    def read_many(self, n: int, count: int) -> list:
        """count values of n bits each. Reads whole bytes in runs that hold a
        whole number of values, which is what makes a 3.7 million point field
        decode in a couple of seconds rather than a minute."""
        if n == 0:
            return [0] * count
        out = []
        pos = self.pos
        data = self.data
        mask = (1 << n) - 1
        # Values per run: the smallest run of whole bytes that holds a whole
        # number of values, once the start is byte aligned.
        per = 8 // math.gcd(n, 8)
        run = per * n // 8
        # Lead-in: values until the read position is byte aligned.
        while count and pos & 7:
            byte = pos >> 3
            off = pos & 7
            nb = (off + n + 7) >> 3
            chunk = int.from_bytes(data[byte:byte + nb], "big")
            out.append((chunk >> (nb * 8 - off - n)) & mask)
            pos += n
            count -= 1
        shifts = [run * 8 - n * (v + 1) for v in range(per)]
        byte = pos >> 3
        nruns = count // per
        for _ in range(nruns):
            chunk = int.from_bytes(data[byte:byte + run], "big")
            out.extend([(chunk >> s) & mask for s in shifts])
            byte += run
        pos += nruns * per * n
        count -= nruns * per
        # Tail: the values that do not fill a run.
        for _ in range(count):
            byte = pos >> 3
            off = pos & 7
            nb = (off + n + 7) >> 3
            chunk = int.from_bytes(data[byte:byte + nb], "big")
            out.append((chunk >> (nb * 8 - off - n)) & mask)
            pos += n
        self.pos = pos
        return out

    def align(self) -> None:
        self.pos = (self.pos + 7) & ~7


def _bitmap_bits(msg: bytes, secs: dict, npts: int) -> Optional[list]:
    """The bitmap as a list of 0/1 per grid point, or None when there is
    none. Section 6 bit i set means point i is present in the packed data."""
    if not has_bitmap(msg):
        return None
    b = _section(msg, secs, 6)
    bits = _Bits(b, 6 * 8).read_many(1, npts)
    return bits


def _expand(vals: list, bitmap: Optional[list], npts: int) -> list:
    """Place packed values on the grid, None where the bitmap has a 0."""
    if bitmap is None:
        if len(vals) != npts:
            raise ValueError(f"{len(vals)} values for {npts} grid points")
        return vals
    present = sum(bitmap)
    if len(vals) != present:
        raise ValueError(f"{len(vals)} values for {present} bitmap points")
    out = [None] * npts
    it = iter(vals)
    for k in range(npts):
        if bitmap[k]:
            out[k] = next(it)
    return out


def _scales(drs: dict) -> "tuple":
    """2**E and 10**-D formed the way eccodes forms them (grib_power), so
    the products below round the same way."""
    bs = 2.0 ** drs["E"]
    d = drs["D"]
    if d >= 0:
        ds = 1.0 / (10.0 ** d)
    else:
        ds = 10.0 ** (-d)
    return bs, ds


def decode(msg: bytes) -> list:
    """The whole field, one float per grid point in section 3 order (k = j *
    Ni + i for scanning mode 64), None where the bitmap marks a point missing
    or the packing's own missing-value management does."""
    secs = sections(msg)
    grid = grid_def(msg)
    drs = drs_def(msg)
    npts = grid["npts"]
    if grid["scan"] != 64:
        # Scanning mode 64: +i, +j, i varies fastest. Any other layout would
        # change what k means, so it is refused rather than reordered.
        raise ValueError(f"scanning mode {grid['scan']}, not 64")
    p, ln = secs[7]
    data = msg[p + 5:p + ln]
    bitmap = _bitmap_bits(msg, secs, npts)
    bs, ds = _scales(drs)
    R = drs["R"]
    n = drs["n"]
    if drs["template"] == 0:
        if drs["bits"] == 0:
            # A constant field carries no packed values; eccodes (and its
            # encoder, which writes D for such a field) returns R itself,
            # not R * 10**-D, and this follows it.
            vals = [R] * n
        else:
            # read and convert a slice at a time: the ints of one slice and
            # the floats of the whole field are the most alive together
            br = _Bits(data)
            vals = []
            left = n
            bits = drs["bits"]
            while left:
                c = _CHUNK if left > _CHUNK else left
                vals.extend([(x * bs + R) * ds for x in br.read_many(bits, c)])
                left -= c
    else:
        vals = _unpack_complex(data, drs)
        # converted in place, slice by slice, so the int list shrinks as the
        # float list grows instead of both standing whole
        for i in range(0, n, _CHUNK):
            vals[i:i + _CHUNK] = [None if x is None else (x * bs + R) * ds for x in vals[i:i + _CHUNK]]
    return _expand(vals, bitmap, npts)


def _unpack_complex(data: bytes, drs: dict) -> list:
    """Templates 5.2 and 5.3 (regulation 92.9.x; NCEP g2c comunpack.c). The
    field is split into groups; each group has a reference, a width and a
    length, packed as three arrays ahead of the data, each byte aligned.
    Returns the integers X per packed value, None for a missing one."""
    n = drs["n"]
    ng = drs["ng"]
    nbits = drs["bits"]
    if drs["split"] != 1:
        raise ValueError(f"group splitting method {drs['split']} is not the general one")
    if drs["missing"] not in (0, 1, 2):
        raise ValueError(f"missing value management {drs['missing']}")
    br = _Bits(data)
    # 5.3 puts the spatial differencing descriptors first: the first one or
    # two original values and the minimum of the differences, each in
    # `extra` octets, the minimum sign-magnitude.
    order = 0
    first = []
    minsd = 0
    if drs["template"] == 3:
        order = drs["order"]
        w = drs["extra"]
        if order not in (1, 2) or w == 0:
            raise ValueError(f"spatial differencing order {order} with {w} extra octets")
        for _ in range(order):
            first.append(br.read(8 * w))
        raw = br.read(8 * w)
        top = 1 << (8 * w - 1)
        minsd = -(raw & (top - 1)) if raw & top else raw
    refs = br.read_many(nbits, ng)
    br.align()
    widths = [drs["ref_w"] + v for v in br.read_many(drs["bits_w"], ng)]
    br.align()
    inc = drs["inc_l"]
    ref_l = drs["ref_l"]
    lens = [ref_l + inc * v for v in br.read_many(drs["bits_l"], ng)]
    br.align()
    if ng:
        lens[-1] = drs["last_l"]
    if sum(lens) != n:
        raise ValueError(f"group lengths sum to {sum(lens)}, section 5 says {n} values")
    missing = drs["missing"]
    out = []
    for g in range(ng):
        w = widths[g]
        ref = refs[g]
        L = lens[g]
        if w == 0:
            # A constant group. With missing-value management on, a reference
            # of all ones (at the reference width) means the whole group is
            # the primary missing value, and with management 2 all ones less
            # one is the secondary one (regulation 92.9.2.4; eccodes reads
            # both as missing).
            if missing and nbits and (ref == (1 << nbits) - 1 or (missing == 2 and ref == (1 << nbits) - 2)):
                out.extend([None] * L)
            else:
                out.extend([ref] * L)
            continue
        xs = br.read_many(w, L)
        if missing:
            m1 = (1 << w) - 1
            m2 = m1 - 1 if missing == 2 else -1
            out.extend([None if x == m1 or x == m2 else ref + x for x in xs])
        else:
            out.extend([ref + x for x in xs])
    if order == 0:
        return out
    # Undo the spatial differencing (regulation 92.9.3.7 to 92.9.3.9). The
    # packed values are differences less their minimum; the first `order`
    # values present are replaced by the descriptors. Missing values are
    # skipped and the recurrence runs over the values present, as g2c does.
    # Done in place, carrying the last one or two undone values, so no second
    # list of 3.7 million ints is built beside `out`.
    cnt = 0
    p1 = p2 = 0
    for k in range(n):
        x = out[k]
        if x is None:
            continue
        if cnt < order:
            x = first[cnt]
        elif order == 1:
            x = x + minsd + p1
        else:
            x = x + minsd + 2 * p1 - p2
        out[k] = x
        p2 = p1
        p1 = x
        cnt += 1
    return out


def random_access(msg: bytes) -> bool:
    """True when decode_cells reads cells in place without decoding the
    field: simple packing (5.0) and no bitmap. The lane uses it to decide
    whether a map frame can be sampled cell by cell (64,000 lattice cells
    plus the fifty places) instead of through the whole 3.7 million points."""
    return drs_def(msg)["template"] == 0 and not has_bitmap(msg)


def decode_cells(msg: bytes, ks: list) -> list:
    """The values at grid indices ks, in that order, None for a missing cell.

    Simple packing without a bitmap is random access: cell k is the `bits`
    bits starting at bit k * bits of section 7, so fifty cells cost fifty
    slices of a few bytes and no whole-field decode. Everything else falls
    back to decode(), because complex packing has no fixed position per
    value and a bitmap shifts every position after the first gap.
    """
    secs = sections(msg)
    drs = drs_def(msg)
    npts = grid_def(msg)["npts"]
    for k in ks:
        if not 0 <= k < npts:
            raise IndexError(f"cell {k} outside a grid of {npts} points")
    if drs["template"] != 0 or has_bitmap(msg):
        field = decode(msg)
        return [field[k] for k in ks]
    p, ln = secs[7]
    data = msg[p + 5:p + ln]
    nbits = drs["bits"]
    bs, ds = _scales(drs)
    R = drs["R"]
    if nbits == 0:
        # Every value equals the reference (see decode: eccodes returns R).
        return [R for _ in ks]
    mask = (1 << nbits) - 1
    out = []
    for k in ks:
        bit = k * nbits
        byte = bit >> 3
        off = bit & 7
        nb = (off + nbits + 7) >> 3
        chunk = int.from_bytes(data[byte:byte + nb], "big")
        x = (chunk >> (nb * 8 - off - nbits)) & mask
        out.append((x * bs + R) * ds)
    return out


# ---------------------------------------------------------------------------
# 4. THE .idx SIDECAR
# ---------------------------------------------------------------------------


def parse_idx(text: str, total_length: Optional[int] = None) -> list:
    """The wgrib2 inventory NCEP publishes beside each file, one line per
    message: `n:offset:d=YYYYMMDDHH:VAR:level:step:`. Returns one dict per
    message with n, start, end (inclusive), var, level, step and date (the
    ten digits after `d=`). A message ends where the next begins; the last one
    runs to the end of the file, so its end is total_length - 1 when the
    caller knows the length and None otherwise."""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(":")
        if len(parts) < 6:
            raise ValueError(f"bad .idx line: {line!r}")
        date = parts[2][2:] if parts[2].startswith("d=") else parts[2]
        rows.append({"n": int(parts[0]), "start": int(parts[1]), "end": None,
                     "date": date, "var": parts[3], "level": parts[4], "step": parts[5]})
    rows.sort(key=lambda r: r["start"])
    for a, b in zip(rows, rows[1:]):
        a["end"] = b["start"] - 1
    if rows and total_length is not None:
        rows[-1]["end"] = total_length - 1
    return rows


# ---------------------------------------------------------------------------
# 5. THE LAMBERT CONFORMAL PROJECTION, ON THE SPHERE
#
# Template 3.30 on a sphere is the conic of Snyder (1987, USGS Professional
# Paper 1395, section 15). NCEP's 2.5 km grids are a tangent cone: Latin1 =
# Latin2 = LaD = 25 N, so the spacing Dx = Dy is true along 25 N and the
# scale grows away from it. The maths below takes the general two-parallel
# form and treats LaD as documentation, which is right for a grid whose LaD
# is one of its standard parallels (both grids here) and would be wrong
# otherwise; same_grid() is what guarantees the lane only ever sees these two.
# ---------------------------------------------------------------------------


def _cone(grid: dict) -> "tuple":
    """(n, R*F) for the grid's cone: n is the cone constant, F the Snyder
    constant, so rho = R*F / tan(pi/4 + phi/2)**n."""
    p1 = math.radians(grid["latin1"])
    p2 = math.radians(grid["latin2"])
    if abs(p1 - p2) < 1e-9:
        n = math.sin(p1)
    else:
        n = (math.log(math.cos(p1) / math.cos(p2))
             / math.log(math.tan(math.pi / 4 + p2 / 2) / math.tan(math.pi / 4 + p1 / 2)))
    F = math.cos(p1) * math.tan(math.pi / 4 + p1 / 2) ** n / n
    return n, grid["radius"] * F


def _xy(grid: dict, lat: float, lon: float, n: float, rf: float) -> "tuple":
    """Projection metres of a point, with the origin on the central meridian
    at the pole: x east, y north."""
    dlon = (lon - grid["lov"] + 180.0) % 360.0 - 180.0
    theta = n * math.radians(dlon)
    rho = rf / math.tan(math.pi / 4 + math.radians(lat) / 2) ** n
    return rho * math.sin(theta), -rho * math.cos(theta)


def lcc_ij(grid: dict, lat: float, lon: float) -> "tuple":
    """Fractional (i, j) of a latitude and longitude on the grid, with (0, 0)
    at the first grid point. Longitude may be given east positive on either
    convention (-74 or 286)."""
    n, rf = _cone(grid)
    x0, y0 = _xy(grid, grid["lat1"], grid["lon1"], n, rf)
    x, y = _xy(grid, lat, lon, n, rf)
    return (x - x0) / grid["dx"], (y - y0) / grid["dy"]


def lcc_latlon(grid: dict, i: float, j: float) -> "tuple":
    """The inverse: latitude and longitude (degrees, longitude in -180 to
    180) of fractional grid coordinates. lcc_latlon(lcc_ij(lat, lon))
    returns to 1e-6 degrees."""
    n, rf = _cone(grid)
    x0, y0 = _xy(grid, grid["lat1"], grid["lon1"], n, rf)
    x = x0 + i * grid["dx"]
    y = y0 + j * grid["dy"]
    # y = -rho cos(theta), x = rho sin(theta), so theta = atan2(x, -y) for a
    # northern cone (n > 0, rho > 0); a southern cone flips both signs.
    sgn = 1.0 if n >= 0 else -1.0
    rho = sgn * math.hypot(x, y)
    theta = math.atan2(sgn * x, -sgn * y)
    lat = math.degrees(2 * math.atan((rf / rho) ** (1 / n)) - math.pi / 2)
    lon = grid["lov"] + math.degrees(theta / n)
    lon = (lon + 180.0) % 360.0 - 180.0
    return lat, lon


def nearest_cell(grid: dict, lat: float, lon: float) -> Optional["tuple"]:
    """(i, j, k) of the grid cell nearest the point, k = j * Ni + i, with the
    fractional coordinates rounded half up; None when the point falls
    outside [0, Ni) x [0, Nj)."""
    fi, fj = lcc_ij(grid, lat, lon)
    i = math.floor(fi + 0.5)
    j = math.floor(fj + 0.5)
    if not (0 <= i < grid["Ni"] and 0 <= j < grid["Nj"]):
        return None
    return i, j, j * grid["Ni"] + i


# ---------------------------------------------------------------------------
# 6. THE TWO GRIDS THE LANE ACCEPTS
# ---------------------------------------------------------------------------

# The NDFD CONUS 2.5 km grid as RTMA and URMA write it ("wexp", the expanded
# western edge): every analysis field and the URMA precipitation.
WEXP = {
    "Ni": 2345, "Nj": 1597, "lat1": 19.228976, "lon1": 233.723448,
    "lov": 265.0, "latin1": 25.0, "latin2": 25.0, "lad": 25.0,
    "dx": 2539.703, "dy": 2539.703, "scan": 64, "radius": 6371200.0,
    "npts": 2345 * 1597,
}

# NCEP grid 184, the older CONUS 2.5 km cut of the same projection: the RTMA
# precipitation only.
G184 = {
    "Ni": 2145, "Nj": 1377, "lat1": 20.191999, "lon1": 238.445999,
    "lov": 265.0, "latin1": 25.0, "latin2": 25.0, "lad": 25.0,
    "dx": 2539.703, "dy": 2539.703, "scan": 64, "radius": 6371200.0,
    "npts": 2145 * 1377,
}

_GRID_DEGREES = ("lat1", "lon1", "lov", "latin1", "latin2", "lad")
_GRID_METRES = ("dx", "dy", "radius")


def same_grid(a: dict, b: dict) -> bool:
    """True when two grid definitions describe the same grid: the counts and
    scanning mode exactly, the angles to 1e-4 degrees (the section stores
    millionths, and NCEP has written the same point as ...976 and ...977
    across generations of the files), the lengths to 0.01 m."""
    for key in ("Ni", "Nj", "scan"):
        if a.get(key) != b.get(key):
            return False
    for key in _GRID_DEGREES:
        if abs(float(a.get(key, 0)) - float(b.get(key, 0))) > 1e-4:
            return False
    for key in _GRID_METRES:
        if abs(float(a.get(key, 0)) - float(b.get(key, 0))) > 0.01:
            return False
    return True
