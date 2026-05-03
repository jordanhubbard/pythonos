"""
kernel.bridge.uart — driver for the secondary UART that carries the
pythonos_bridge byte stream.

PythonOS already uses one UART for the kernel REPL (PL011 #0 on arm64,
COM1 on x86). The bridge runs on a *second* UART:

  arm64 (QEMU virt):  PL011 #1 at MMIO 0x09040000
  x86_64 (q35):       16550 COM2 at I/O port 0x2F8

QEMU is launched with:
  -chardev socket,id=br,path=/tmp/pythonos-bridge.sock,reconnect=2
  -serial chardev:br
which forwards raw bytes between the second UART and the host
pythonos_bridge process. There is no virtio device, no virtqueue, no
descriptor ring — just bytes.
"""

import asyncio
import _hal


_ARCH = getattr(_hal, "ARCH", "x86_64")


# ── arm64 PL011 #1 ─────────────────────────────────────────────────────────

_PL011_BASE = 0x09040000
_PL011_DR   = _PL011_BASE + 0x000     # data register (read = RX, write = TX)
_PL011_FR   = _PL011_BASE + 0x018     # flag register
_PL011_RXFE = 1 << 4                  # bit 4: RX FIFO empty
_PL011_TXFF = 1 << 5                  # bit 5: TX FIFO full


# ── x86_64 16550 COM2 ──────────────────────────────────────────────────────

_COM2_BASE = 0x2F8
_COM2_DATA = _COM2_BASE                # RBR/THR
_COM2_LSR  = _COM2_BASE + 5            # line status: bit 0 = DR, bit 5 = THRE


# ── arm64 path ─────────────────────────────────────────────────────────────

def _pl011_write_byte(b: int) -> None:
    while _hal.mmio_read32(_PL011_FR) & _PL011_TXFF:
        pass
    _hal.mmio_write32(_PL011_DR, b & 0xFF)


def _pl011_try_read_byte() -> int:
    """Return 0..255 if a byte is ready, else -1."""
    if _hal.mmio_read32(_PL011_FR) & _PL011_RXFE:
        return -1
    return _hal.mmio_read32(_PL011_DR) & 0xFF


# ── x86 path ───────────────────────────────────────────────────────────────

def _com2_write_byte(b: int) -> None:
    while (_hal.inb(_COM2_LSR) & 0x20) == 0:
        pass
    _hal.outb(_COM2_DATA, b & 0xFF)


def _com2_try_read_byte() -> int:
    if (_hal.inb(_COM2_LSR) & 0x01) == 0:
        return -1
    return _hal.inb(_COM2_DATA) & 0xFF


# ── Generic façade ─────────────────────────────────────────────────────────

if _ARCH == "arm64":
    _write_byte    = _pl011_write_byte
    _try_read_byte = _pl011_try_read_byte
else:
    _write_byte    = _com2_write_byte
    _try_read_byte = _com2_try_read_byte


def write_bytes(buf) -> None:
    """Blocking-ish bulk write. Each TX poll is fast; we don't yield
    inside writes since frames are small (<= 16 KiB worst case)."""
    for b in buf:
        _write_byte(b)


async def read_byte() -> int:
    """Yield until a byte is ready, then return it."""
    while True:
        b = _try_read_byte()
        if b >= 0:
            return b
        await asyncio.sleep(0)


async def read_bytes(n: int) -> bytes:
    """Read exactly n bytes from the bridge UART."""
    out = bytearray(n)
    for i in range(n):
        out[i] = await read_byte()
    return bytes(out)
