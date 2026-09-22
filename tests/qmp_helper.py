"""
Helpers for driving QEMU through its HMP monitor over a Unix socket.

Used by the GUI tests to:
    * capture a screen image (`screendump path`)
    * inject a keystroke (`sendkey k`)
    * inject mouse motion / button (`mouse_move`, `mouse_button`)
    * sample specific pixels from the captured PPM

The transport is QEMU's classic HMP (text protocol). Connect, read the
banner, then send commands terminated by '\\n'; each response ends with
the next prompt, "(qemu) ". We strip the prompt before returning.

PPM (P6) parsing here is intentionally minimal — the format is a tiny
ASCII header followed by raw RGB bytes.
"""

import os
import socket
import time


def env_seconds(name: str, default: float) -> float:
    """Read a timeout from the environment, falling back on anything unusable.

    A workflow that sets one of these from a matrix key defined for only some
    entries passes an empty string to the rest rather than leaving the variable
    out, and bare float("") raises. Empty, non-numeric and non-positive values
    all mean "not configured" here, so a typo cannot turn a timeout into a
    crash -- or into zero.
    """
    try:
        value = float(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


class QemuMonitor:
    """Connection to a QEMU HMP monitor exposed at a Unix socket.

    Pass `connect_timeout` long enough to outlast guest boot — the
    monitor is alive immediately, but launching QEMU and binding the
    socket can take a moment.
    """

    def __init__(self, sock_path: str, connect_timeout: float = 10.0) -> None:
        self._sock_path = sock_path
        self._sock = self._open(sock_path, connect_timeout)
        self._read_until_prompt()

    @staticmethod
    def _open(sock_path: str, timeout: float) -> socket.socket:
        deadline = time.time() + timeout
        last_exc: Exception | None = None
        while time.time() < deadline:
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(2.0)
                s.connect(sock_path)
                return s
            except (FileNotFoundError, ConnectionRefusedError, OSError) as e:
                last_exc = e
                time.sleep(0.2)
        raise TimeoutError(
            f"qmp_helper: monitor socket {sock_path!r} never opened: {last_exc}")

    # ── Low-level I/O ──────────────────────────────────────────────────────

    def _read_until_prompt(self, timeout: float = 5.0) -> str:
        self._sock.settimeout(timeout)
        buf = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                chunk = self._sock.recv(4096)
            except (TimeoutError, BlockingIOError):
                continue
            if not chunk:
                break
            buf += chunk
            if buf.endswith(b"(qemu) "):
                break
        return buf.decode("utf-8", errors="replace")

    def command(self, cmd: str, settle: float = 0.05) -> str:
        """Run an HMP command, return everything between the echo and the
        next prompt (with the prompt stripped)."""
        self._sock.sendall((cmd + "\n").encode("utf-8"))
        time.sleep(settle)
        out = self._read_until_prompt()
        if out.startswith(cmd):
            out = out[len(cmd):]
        if out.endswith("(qemu) "):
            out = out[: -len("(qemu) ")]
        return out.lstrip("\r\n").rstrip()

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

    # ── High-level conveniences ────────────────────────────────────────────

    def screendump(self, path: str, settle: float = 0.2) -> None:
        """Run ``screendump path`` and wait for the file to materialize."""
        if os.path.exists(path):
            os.remove(path)
        self.command(f"screendump {path}", settle=settle)
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if os.path.exists(path) and os.path.getsize(path) > 0:
                return
            time.sleep(0.05)
        raise TimeoutError(f"qmp_helper: {path} did not appear")

    def sendkey(self, key: str, hold_ms: int | None = None) -> None:
        """``key`` is QEMU's keyname (a-z, ret, esc, tab, up, …)."""
        if hold_ms is None:
            self.command(f"sendkey {key}")
        else:
            self.command(f"sendkey {key} {hold_ms}")

    def mouse_move(self, dx: int, dy: int) -> None:
        self.command(f"mouse_move {dx} {dy}")

    def mouse_button(self, mask: int) -> None:
        """``mask`` is the QEMU button bitmap: 1=left, 2=middle, 4=right."""
        self.command(f"mouse_button {mask}")


# ── PPM (P6) parsing + pixel sampling ──────────────────────────────────────

def parse_ppm(path: str) -> tuple[int, int, bytes]:
    """Parse a binary P6 PPM file. Returns (width, height, rgb_bytes)."""
    with open(path, "rb") as f:
        data = f.read()
    if not data.startswith(b"P6"):
        raise ValueError(f"qmp_helper.parse_ppm: {path}: not P6")
    off = 2
    n = len(data)

    def read_token(o: int) -> tuple[bytes, int]:
        while o < n and data[o:o+1] in (b" ", b"\n", b"\r", b"\t"):
            o += 1
        if o < n and data[o:o+1] == b"#":
            while o < n and data[o:o+1] != b"\n":
                o += 1
            while o < n and data[o:o+1] in (b" ", b"\n", b"\r", b"\t"):
                o += 1
        start = o
        while o < n and data[o:o+1] not in (b" ", b"\n", b"\r", b"\t"):
            o += 1
        return data[start:o], o

    w_tok, off = read_token(off)
    h_tok, off = read_token(off)
    m_tok, off = read_token(off)
    if off < n and data[off:off+1] in (b" ", b"\n", b"\r", b"\t"):
        off += 1
    width, height, maxval = int(w_tok), int(h_tok), int(m_tok)
    if maxval != 255:
        raise ValueError(f"qmp_helper.parse_ppm: {path}: maxval={maxval} unsupported")
    body = data[off : off + width * height * 3]
    if len(body) < width * height * 3:
        raise ValueError(f"qmp_helper.parse_ppm: {path}: truncated")
    return width, height, body


def sample_pixel(width: int, rgb: bytes, x: int, y: int) -> tuple[int, int, int]:
    o = (y * width + x) * 3
    return rgb[o], rgb[o + 1], rgb[o + 2]


def color_close(rgb_at: tuple[int, int, int],
                expected: tuple[int, int, int],
                tolerance: int = 8) -> bool:
    r, g, b = rgb_at
    er, eg, eb = expected
    return (abs(r - er) <= tolerance
            and abs(g - eg) <= tolerance
            and abs(b - eb) <= tolerance)


# ── Tile-hash golden helpers ────────────────────────────────────────────────

import hashlib
import os


def tile_hashes(rgb: bytes, width: int, height: int, tile: int = 16) -> list[str]:
    """SHA-256 hashes of every ``tile``×``tile`` block, row-major.

    Each hash is a hex digest of just the raw RGB bytes belonging to that
    tile. Tiles at the right and bottom edges that don't divide evenly
    are clipped (their dimensions are smaller, but the hash is still
    well-defined).
    """
    out: list[str] = []
    bytes_per_row = width * 3
    for ty in range(0, height, tile):
        th = min(tile, height - ty)
        for tx in range(0, width, tile):
            tw = min(tile, width - tx)
            h = hashlib.sha256()
            for row in range(ty, ty + th):
                start = row * bytes_per_row + tx * 3
                h.update(rgb[start:start + tw * 3])
            out.append(h.hexdigest())
    return out


def compare_tilehashes(actual: list[str],
                       expected: list[str],
                       max_diffs: int = 1) -> tuple[bool, int]:
    """Return ``(ok, diff_count)`` allowing up to ``max_diffs`` mismatches.

    A small tolerance keeps minor sub-pixel jitter from breaking the
    test while still catching wholesale changes (cursor/window moved,
    color theme changed, etc.).
    """
    if len(actual) != len(expected):
        return False, abs(len(actual) - len(expected)) + max(len(actual), len(expected))
    diffs = sum(1 for a, b in zip(actual, expected) if a != b)
    return diffs <= max_diffs, diffs


def golden_check_or_refresh(actual: list[str], golden_path: str,
                             max_diffs: int = 1) -> tuple[bool, str]:
    """Compare ``actual`` against the file at ``golden_path``.

    With ``PYTHONOS_GOLDEN_REFRESH=1`` in the environment, write the
    actual list to ``golden_path`` and pass. Without it, fail if the
    file is missing.
    """
    if os.environ.get("PYTHONOS_GOLDEN_REFRESH", "") == "1":
        os.makedirs(os.path.dirname(golden_path), exist_ok=True)
        with open(golden_path, "w") as f:
            f.write("\n".join(actual) + "\n")
        return True, f"refreshed {len(actual)} tile hashes -> {golden_path}"

    if not os.path.exists(golden_path):
        return False, (f"golden missing: {golden_path}; "
                        f"run with PYTHONOS_GOLDEN_REFRESH=1 to create it")
    with open(golden_path) as f:
        expected = [line.strip() for line in f if line.strip()]
    ok, diffs = compare_tilehashes(actual, expected, max_diffs=max_diffs)
    return ok, (f"{diffs} of {len(actual)} tiles differ "
                 f"(max_diffs={max_diffs})")
