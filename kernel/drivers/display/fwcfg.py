"""
kernel.drivers.display.fwcfg — QEMU fw_cfg device.

fw_cfg is QEMU's mechanism for passing configuration blobs from the host
into the guest. On x86_64 the device is exposed through I/O ports:

    0x510       selector     (2-byte little-endian)
    0x511       data         (8-bit data port)

On the arm64 ``virt`` machine the device is mapped at:

    0x09020000  data         (8-byte data port)
    0x09020008  selector     (2-byte BE; only 16-bit writes accepted by QEMU)
    0x09020010  DMA control  (8-byte BE DMA control register)

The arm64 path talks over DMA: every operation builds a
FWCfgDmaAccess header in guest RAM and posts its address to the DMA
register. Each DMA op carries its own selector (via the SELECT bit and
the high 16 bits of the control word), so we never touch the 16-bit
selector register directly — which would otherwise require a primitive
not currently exposed by ``_hal``.

Spec: docs/specs/fw_cfg.txt in the QEMU source tree.
"""

import _hal
from kernel.hal.io import inb, outw, mmio_read8, mmio_write8, mmio_write32

_ARCH = getattr(_hal, "ARCH", "x86_64")

FW_CFG_DATA = 0x09020000
FW_CFG_DMA  = 0x09020010

FW_CFG_IO_SELECTOR = 0x510
FW_CFG_IO_DATA     = 0x511

FW_CFG_SIGNATURE = 0x0000
FW_CFG_ID        = 0x0001
FW_CFG_FILE_DIR  = 0x0019

FW_CFG_DMA_ERROR  = 0x01
FW_CFG_DMA_READ   = 0x02
FW_CFG_DMA_SKIP   = 0x04
FW_CFG_DMA_SELECT = 0x08
FW_CFG_DMA_WRITE  = 0x10

DIR_ENTRY_SIZE = 64   # uint32 size + uint16 sel + uint16 resv + char[56] name


def _be32(v: int) -> int:
    """Encode v so that a CPU-native (LE) 32-bit store places it BE in memory."""
    return int.from_bytes((v & 0xFFFFFFFF).to_bytes(4, "big"), "little")


def _read_be32(addr: int) -> int:
    return ((mmio_read8(addr)     << 24) |
            (mmio_read8(addr + 1) << 16) |
            (mmio_read8(addr + 2) <<  8) |
             mmio_read8(addr + 3))


def _dma_op(selector: int, length: int, buf_phys: int, op_flags: int) -> bool:
    """Issue one fw_cfg DMA op; returns True on success."""
    dma_phys = _hal.dma_alloc(16)
    control = (selector << 16) | FW_CFG_DMA_SELECT | op_flags
    mmio_write32(dma_phys + 0,  _be32(control))
    mmio_write32(dma_phys + 4,  _be32(length))
    mmio_write32(dma_phys + 8,  _be32((buf_phys >> 32) & 0xFFFFFFFF))
    mmio_write32(dma_phys + 12, _be32(buf_phys        & 0xFFFFFFFF))

    # Post the 64-bit BE address of the FWCfgDmaAccess struct. QEMU
    # triggers the transfer on the lower-half write, so post high first.
    mmio_write32(FW_CFG_DMA,     _be32((dma_phys >> 32) & 0xFFFFFFFF))
    mmio_write32(FW_CFG_DMA + 4, _be32(dma_phys        & 0xFFFFFFFF))

    for _ in range(1_000_000):
        ctl = _read_be32(dma_phys)
        pending = ctl & (FW_CFG_DMA_SELECT | FW_CFG_DMA_WRITE | FW_CFG_DMA_READ)
        if not pending:
            return (ctl & FW_CFG_DMA_ERROR) == 0
    return False


def _read_into_bytes(buf_phys: int, length: int) -> bytes:
    return bytes(mmio_read8(buf_phys + i) for i in range(length))


def _dma_read(selector: int, length: int) -> bytes | None:
    if length == 0:
        return b""
    buf_phys = _hal.dma_alloc(length)
    if not _dma_op(selector, length, buf_phys, FW_CFG_DMA_READ):
        return None
    return _read_into_bytes(buf_phys, length)


def _dma_write(selector: int, data: bytes) -> bool:
    n = len(data)
    if n == 0:
        return True
    buf_phys = _hal.dma_alloc(n)
    for i, b in enumerate(data):
        mmio_write8(buf_phys + i, b)
    return _dma_op(selector, n, buf_phys, FW_CFG_DMA_WRITE)


def _pio_read(selector: int, length: int) -> bytes | None:
    if length == 0:
        return b""
    outw(FW_CFG_IO_SELECTOR, selector & 0xFFFF)
    return bytes(inb(FW_CFG_IO_DATA) for _ in range(length))


def _read(selector: int, length: int) -> bytes | None:
    if _ARCH == "arm64":
        return _dma_read(selector, length)
    return _pio_read(selector, length)


# ── Public API ───────────────────────────────────────────────────────────────

def signature() -> bytes:
    """Reads the 4-byte signature item; returns b'QEMU' if fw_cfg is present."""
    sig = _read(FW_CFG_SIGNATURE, 4)
    return sig if sig != None else b""


def list_files() -> dict[str, tuple[int, int]]:
    """Enumerate fw_cfg directory; returns ``{name: (size, selector)}``.

    Each DMA-SELECT resets the item's read position to 0, so we issue
    two reads: first to get the entry count, second to slurp every
    entry from the start.
    """
    head = _read(FW_CFG_FILE_DIR, 4)
    if not head:
        return {}
    cnt = int.from_bytes(head, "big")
    if cnt == 0:
        return {}

    total = 4 + DIR_ENTRY_SIZE * cnt
    body = _read(FW_CFG_FILE_DIR, total)
    if not body or len(body) < total:
        return {}

    files: dict[str, tuple[int, int]] = {}
    for i in range(cnt):
        off  = 4 + i * DIR_ENTRY_SIZE
        size = int.from_bytes(body[off    : off + 4],  "big")
        sel  = int.from_bytes(body[off + 4: off + 6],  "big")
        name = body[off + 8: off + DIR_ENTRY_SIZE].split(b"\x00", 1)[0]
        files[name.decode("ascii", errors="replace")] = (size, sel)
    return files


def read_item(selector: int, length: int) -> bytes | None:
    """Read ``length`` bytes from a fw_cfg selector."""
    return _read(selector, length)


def read_file(name: str) -> bytes | None:
    """Read a named ``-fw_cfg name=...`` item, or ``None`` if absent."""
    entry = list_files().get(name)
    if entry == None:
        return None
    size, selector = entry
    return read_item(selector, size)


def write_item(selector: int, data: bytes) -> bool:
    """DMA-WRITE ``data`` into the fw_cfg item at ``selector``."""
    if _ARCH != "arm64":
        return False
    return _dma_write(selector, data)
