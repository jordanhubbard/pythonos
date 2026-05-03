"""kernel.gui.image.jpeg — Pure-Python baseline JPEG decoder.

Covers the baseline DCT (SOF0) profile with 8-bit precision in
sequential scan order — that's the format ~99% of everyday JPEGs land
in. Supports 1-component (grayscale) and 3-component (Y/Cb/Cr) images
with 4:4:4, 4:2:2, and 4:2:0 chroma subsampling. Output goes into an
:class:`SDL_Surface` as XRGB8888.

NOT supported (raise NotImplementedError on first sight):
    * Progressive (SOF2) and arithmetic-coded JPEGs
    * 12-bit precision
    * 4-component CMYK or other unusual layouts

The decoder is tuned for correctness rather than speed — there's a
straight 8×8 IDCT and per-pixel YCbCr→RGB. A real-world image viewer
would FFI to stb_image for a 50× speedup, but the kernel's libz-less
build makes this the right pragmatic choice for v0.
"""

from kernel.gui.sdl2.surface import SDL_Surface


# ── Zigzag order (RFC ITU-T T.81 §F.1.1.5) ─────────────────────────────────

_ZIGZAG = [
     0,  1,  8, 16,  9,  2,  3, 10,
    17, 24, 32, 25, 18, 11,  4,  5,
    12, 19, 26, 33, 40, 48, 41, 34,
    27, 20, 13,  6,  7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36,
    29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46,
    53, 60, 61, 54, 47, 55, 62, 63,
]


# ── IDCT (straightforward 8×8) ──────────────────────────────────────────────
# Float-domain math is fine for correctness; the inner loop runs once per
# block so total cost stays manageable for v0.

import math
_PI = math.pi
_SQRT_HALF = 1.0 / math.sqrt(2)

_COS = [
    [math.cos((2 * x + 1) * u * _PI / 16.0) for x in range(8)]
    for u in range(8)
]


def _idct_block(block: list) -> list:
    """8×8 IDCT — input/output are flat 64-element lists in row-major order."""
    out = [0.0] * 64
    for y in range(8):
        for x in range(8):
            s = 0.0
            for u in range(8):
                cu = _SQRT_HALF if u == 0 else 1.0
                cy = _COS[u][x]
                for v in range(8):
                    cv = _SQRT_HALF if v == 0 else 1.0
                    cx = _COS[v][y]
                    s += cu * cv * block[v * 8 + u] * cy * cx
            out[y * 8 + x] = s * 0.25
    return out


# ── Bit reader (MSB-first, with 0xFF00 stuffing) ───────────────────────────

class _BitReader:
    __slots__ = ("data", "pos", "bit_buf", "bit_count")

    def __init__(self, data: bytes, pos: int) -> None:
        self.data = data
        self.pos = pos
        self.bit_buf = 0
        self.bit_count = 0

    def _fill_byte(self) -> int:
        if self.pos >= len(self.data):
            return 0
        b = self.data[self.pos]
        self.pos += 1
        if b == 0xFF:
            # 0xFF00 stuffing — skip the trailing 0x00.
            if self.pos < len(self.data) and self.data[self.pos] == 0x00:
                self.pos += 1
            else:
                # 0xFF followed by non-zero is a marker — back up.
                self.pos -= 1
                return 0
        return b

    def read_bits(self, n: int) -> int:
        while self.bit_count < n:
            self.bit_buf = (self.bit_buf << 8) | self._fill_byte()
            self.bit_count += 8
        self.bit_count -= n
        v = (self.bit_buf >> self.bit_count) & ((1 << n) - 1)
        self.bit_buf &= (1 << self.bit_count) - 1
        return v

    def receive_extend(self, n: int) -> int:
        if n == 0:
            return 0
        v = self.read_bits(n)
        if v < (1 << (n - 1)):
            return v - (1 << n) + 1
        return v


# ── JPEG canonical Huffman ──────────────────────────────────────────────────

class _JpegHuff:
    """Decode-only canonical Huffman table for JPEG.

    Stored as a list of (code, length) → symbol entries indexed by
    cumulative code value per-length, exactly the way RFC ITU-T T.81
    builds them in §C.2.
    """

    __slots__ = ("codes",)

    def __init__(self, counts: list, syms: list) -> None:
        self.codes: dict[tuple[int, int], int] = {}
        code = 0
        idx = 0
        for length in range(1, 17):
            for _ in range(counts[length - 1]):
                self.codes[(length, code)] = syms[idx]
                idx += 1
                code += 1
            code <<= 1

    def decode(self, reader: _BitReader) -> int:
        code = 0
        for length in range(1, 17):
            code = (code << 1) | reader.read_bits(1)
            sym = self.codes.get((length, code))
            if sym != None:
                return sym
        raise ValueError("jpeg: invalid huffman code")


# ── Marker scanning ─────────────────────────────────────────────────────────

# Marker symbolic names we care about (stand-alone pairs after 0xFF).
_M_SOI  = 0xD8
_M_EOI  = 0xD9
_M_SOS  = 0xDA
_M_DQT  = 0xDB
_M_DHT  = 0xC4
_M_DRI  = 0xDD
_M_SOF0 = 0xC0
_M_SOF1 = 0xC1


# ── Decoder state ───────────────────────────────────────────────────────────

class _Decoder:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0
        self.width = 0
        self.height = 0
        self.qtables: dict[int, list] = {}
        self.htables: dict[tuple[int, int], _JpegHuff] = {}   # (class, id) → table
        self.components: list = []   # per-component dict
        self.scan_components: list = []  # per-scan dict (id, td, ta)
        self.restart_interval = 0
        self.h_max = 1
        self.v_max = 1

    def _u16_be(self) -> int:
        v = (self.data[self.pos] << 8) | self.data[self.pos + 1]
        self.pos += 2
        return v

    def _expect_marker(self) -> int:
        while self.pos < len(self.data) and self.data[self.pos] == 0xFF:
            self.pos += 1
        if self.pos >= len(self.data):
            raise ValueError("jpeg: unexpected EOF scanning markers")
        m = self.data[self.pos]
        self.pos += 1
        return m

    def _read_segment(self) -> bytes:
        length = self._u16_be()
        body = self.data[self.pos: self.pos + length - 2]
        self.pos += length - 2
        return body

    # ── SOF0 ────────────────────────────────────────────────────────────

    def _parse_sof0(self, body: bytes) -> None:
        precision = body[0]
        if precision != 8:
            raise NotImplementedError(f"jpeg: precision {precision} not supported")
        self.height = (body[1] << 8) | body[2]
        self.width  = (body[3] << 8) | body[4]
        n = body[5]
        if n not in (1, 3):
            raise NotImplementedError(f"jpeg: {n} components not supported")
        comps: list = []
        for i in range(n):
            cid = body[6 + i * 3]
            samp = body[7 + i * 3]
            qid = body[8 + i * 3]
            h = (samp >> 4) & 0xF
            v = samp & 0xF
            comps.append({"id": cid, "h": h, "v": v, "qid": qid,
                          "dc": 0,
                          "td": 0, "ta": 0,
                          "data": None})
            if h > self.h_max: self.h_max = h
            if v > self.v_max: self.v_max = v
        self.components = comps

    # ── DQT ─────────────────────────────────────────────────────────────

    def _parse_dqt(self, body: bytes) -> None:
        i = 0
        while i < len(body):
            pq_tq = body[i]; i += 1
            pq = pq_tq >> 4
            tq = pq_tq & 0xF
            if pq != 0:
                raise NotImplementedError("jpeg: 16-bit Q-table not supported")
            self.qtables[tq] = list(body[i: i + 64])
            i += 64

    # ── DHT ─────────────────────────────────────────────────────────────

    def _parse_dht(self, body: bytes) -> None:
        i = 0
        while i < len(body):
            tc_th = body[i]; i += 1
            tc = tc_th >> 4
            th = tc_th & 0xF
            counts = list(body[i: i + 16]); i += 16
            n_syms = sum(counts)
            syms = list(body[i: i + n_syms]); i += n_syms
            self.htables[(tc, th)] = _JpegHuff(counts, syms)

    # ── DRI ─────────────────────────────────────────────────────────────

    def _parse_dri(self, body: bytes) -> None:
        self.restart_interval = (body[0] << 8) | body[1]

    # ── SOS + scan ──────────────────────────────────────────────────────

    def _parse_sos(self, body: bytes) -> int:
        n = body[0]
        scan: list = []
        for i in range(n):
            cid = body[1 + i * 2]
            tdta = body[2 + i * 2]
            td = (tdta >> 4) & 0xF
            ta = tdta & 0xF
            for c in self.components:
                if c["id"] == cid:
                    c["td"] = td; c["ta"] = ta
                    scan.append(c)
                    break
        # Skip 3 byte tail (Ss, Se, AhAl)
        self.scan_components = scan
        return n

    def _decode_block(self, reader: _BitReader, comp) -> list:
        dc_table = self.htables[(0, comp["td"])]
        ac_table = self.htables[(1, comp["ta"])]
        qt = self.qtables[comp["qid"]]
        zz = [0] * 64

        # DC
        s = dc_table.decode(reader)
        diff = reader.receive_extend(s)
        comp["dc"] = comp["dc"] + diff
        zz[0] = comp["dc"]

        # AC
        i = 1
        while i < 64:
            rs = ac_table.decode(reader)
            if rs == 0:
                break    # EOB
            run = rs >> 4
            cat = rs & 0xF
            if cat == 0 and run == 15:
                i += 16  # ZRL — 16 zeros
                continue
            i += run
            if i >= 64:
                break
            zz[i] = reader.receive_extend(cat)
            i += 1

        # Dequantize + un-zigzag
        block = [0.0] * 64
        for k in range(64):
            block[_ZIGZAG[k]] = zz[k] * qt[k]
        return _idct_block(block)

    def _decode_scan(self) -> None:
        reader = _BitReader(self.data, self.pos)

        h_max, v_max = self.h_max, self.v_max
        mcu_w = h_max * 8
        mcu_h = v_max * 8
        n_mcu_x = (self.width  + mcu_w - 1) // mcu_w
        n_mcu_y = (self.height + mcu_h - 1) // mcu_h

        # Allocate component sample planes at full resolution post-upsample.
        for c in self.components:
            c["data"] = [0] * (n_mcu_x * mcu_w * n_mcu_y * mcu_h)

        plane_w = n_mcu_x * mcu_w

        for my in range(n_mcu_y):
            for mx in range(n_mcu_x):
                for c in self.scan_components:
                    h, v = c["h"], c["v"]
                    sx_step = h_max // h
                    sy_step = v_max // v
                    for vy in range(v):
                        for hx in range(h):
                            samples = self._decode_block(reader, c)
                            base_x = mx * mcu_w + hx * 8 * sx_step
                            base_y = my * mcu_h + vy * 8 * sy_step
                            # Place samples into the plane, upsampling
                            # by replication for sx_step / sy_step > 1.
                            for sy in range(8):
                                for sx in range(8):
                                    val = int(samples[sy * 8 + sx] + 128)
                                    if val < 0:   val = 0
                                    elif val > 255: val = 255
                                    for dy in range(sy_step):
                                        py = base_y + sy * sy_step + dy
                                        if py >= n_mcu_y * mcu_h: continue
                                        row = py * plane_w
                                        for dx in range(sx_step):
                                            px = base_x + sx * sx_step + dx
                                            if px >= plane_w: continue
                                            c["data"][row + px] = val

        self.pos = reader.pos

    # ── Top-level decode ────────────────────────────────────────────────

    def decode(self) -> SDL_Surface:
        if len(self.data) < 4 or self.data[0] != 0xFF or self.data[1] != _M_SOI:
            raise ValueError("jpeg: missing SOI marker")
        self.pos = 2

        while self.pos < len(self.data):
            m = self._expect_marker()
            if m == _M_EOI:
                break
            if 0xD0 <= m <= 0xD7:
                # Restart marker — would consume in-scan; we shouldn't
                # see one outside a scan. Skip.
                continue
            if m == _M_SOF1:
                raise NotImplementedError("jpeg: extended sequential not supported")
            if m == _M_SOF0:
                self._parse_sof0(self._read_segment())
            elif m == _M_DQT:
                self._parse_dqt(self._read_segment())
            elif m == _M_DHT:
                self._parse_dht(self._read_segment())
            elif m == _M_DRI:
                self._parse_dri(self._read_segment())
            elif m == _M_SOS:
                # SOS body parses then transitions to entropy-coded data.
                length = self._u16_be()
                body = self.data[self.pos: self.pos + length - 2]
                self.pos += length - 2
                self._parse_sos(body)
                self._decode_scan()
                # After scan, expect EOI (or further scans we don't support).
                continue
            elif 0xC0 <= m <= 0xCF:
                raise NotImplementedError(
                    f"jpeg: unsupported SOF marker 0xFF{m:02X}")
            else:
                # APPn / COM / unknown — skip the segment.
                length = self._u16_be()
                self.pos += length - 2

        return self._compose()

    def _compose(self) -> SDL_Surface:
        s = SDL_Surface(self.width, self.height, host_backed=False)
        plane_w = self.h_max * 8 * ((self.width + self.h_max * 8 - 1) // (self.h_max * 8))
        if len(self.components) == 1:
            y = self.components[0]["data"]
            for py in range(self.height):
                for px in range(self.width):
                    v = y[py * plane_w + px]
                    o = (py * self.width + px) * 4
                    s.pixels[o]     = v
                    s.pixels[o + 1] = v
                    s.pixels[o + 2] = v
                    s.pixels[o + 3] = 0xFF
        else:
            yp = self.components[0]["data"]
            cb = self.components[1]["data"]
            cr = self.components[2]["data"]
            for py in range(self.height):
                row = py * plane_w
                for px in range(self.width):
                    yv = yp[row + px]
                    cbv = cb[row + px] - 128
                    crv = cr[row + px] - 128
                    r = yv + (45913 * crv) // 32768               # 1.402
                    g = yv - (11277 * cbv + 23401 * crv) // 32768  # 0.344136 / 0.714136
                    b = yv + (58065 * cbv) // 32768               # 1.772
                    if r < 0:   r = 0
                    elif r > 255: r = 255
                    if g < 0:   g = 0
                    elif g > 255: g = 255
                    if b < 0:   b = 0
                    elif b > 255: b = 255
                    o = (py * self.width + px) * 4
                    s.pixels[o]     = b
                    s.pixels[o + 1] = g
                    s.pixels[o + 2] = r
                    s.pixels[o + 3] = 0xFF
        return s


# ── Public API ──────────────────────────────────────────────────────────────

def decode_jpeg(data: bytes) -> SDL_Surface:
    return _Decoder(data).decode()


def load_jpeg(path: str) -> SDL_Surface:
    with open(path, "rb") as f:
        return decode_jpeg(f.read())
