"""
kernel.drivers.block.virtio_blk_pci — VirtIO block device (PCI transport).

x86_64 counterpart to the arm64 virtio-mmio driver in ``virtio_blk.py``.
The on-the-wire VirtIO block protocol is identical (same descriptor
ring layout, same 16-byte request header, same trailing status byte).
Only the transport differs: instead of a fixed MMIO register window
we walk the PCI capabilities list, find the *modern* VirtIO common
config / notify / device-config regions, and drive the device via
those.

Layout follows VirtIO 1.0+ spec section 4.1 (PCI bus binding):

  Common cfg cap (cfg_type=1) — common register block:
      0x00 device_feature_select  (u32)
      0x04 device_feature         (u32)
      0x08 driver_feature_select  (u32)
      0x0C driver_feature         (u32)
      0x10 msix_config            (u16)
      0x12 num_queues             (u16)
      0x14 device_status          (u8)
      0x15 config_generation      (u8)
      0x16 queue_select           (u16)
      0x18 queue_size             (u16)
      0x1A queue_msix_vector      (u16)
      0x1C queue_enable           (u16)
      0x1E queue_notify_off       (u16)
      0x20 queue_desc             (u64)
      0x28 queue_driver           (u64)
      0x30 queue_device           (u64)

  Notify cfg cap (cfg_type=2):
      MMIO region; the doorbell for queue Q lives at
      offset = queue_notify_off(Q) * notify_off_multiplier.

  Device cfg cap (cfg_type=4) — block-specific:
      0x00 capacity (u64, sectors of 512 bytes)
"""

import asyncio

import _hal
import kernel.log as log
from kernel.bus.pci import (
    PCIDevice,
    config_read32,
    config_read16,
    config_read8,
    bus as pci_bus,
)

# ── VirtIO constants ──────────────────────────────────────────────────────────

VIRTIO_VENDOR        = 0x1AF4
VIRTIO_BLK_DEV_MODERN = 0x1042   # VirtIO 1.0+ transitional / non-transitional
VIRTIO_BLK_DEV_LEGACY = 0x1001   # legacy PCI virtio-blk

VIRTIO_PCI_CAP_COMMON_CFG = 1
VIRTIO_PCI_CAP_NOTIFY_CFG = 2
VIRTIO_PCI_CAP_ISR_CFG    = 3
VIRTIO_PCI_CAP_DEVICE_CFG = 4

# Device status register bits
STATUS_ACK              = 0x01
STATUS_DRIVER           = 0x02
STATUS_DRIVER_OK        = 0x04
STATUS_FEATURES_OK      = 0x08
STATUS_FAILED           = 0x80

# VirtIO 1.0 feature
VIRTIO_F_VERSION_1 = 1 << 32

# Descriptor flags
VRING_DESC_F_NEXT  = 1
VRING_DESC_F_WRITE = 2

# Block request types
VIRTIO_BLK_T_IN  = 0
VIRTIO_BLK_T_OUT = 1

PAGE_SIZE  = 4096
QUEUE_SIZE = 16   # power-of-2; matches the MMIO driver

# PCI command register bits
PCI_CMD_IO_SPACE   = 0x01
PCI_CMD_MEM_SPACE  = 0x02
PCI_CMD_BUS_MASTER = 0x04


# ── PCI capability walking ────────────────────────────────────────────────────

def _bar_address(bus: int, dev: int, func: int, bar_index: int) -> tuple[int, bool]:
    """
    Resolve PCI BAR `bar_index` to (address, is_io).  Handles 64-bit
    memory BARs (which span two consecutive BAR slots).
    """
    off = 0x10 + bar_index * 4
    raw = config_read32(bus, dev, func, off)
    if raw & 0x1:
        # I/O port BAR
        return raw & 0xFFFFFFFC, True
    # Memory BAR; bits [2:1] = type (0=32-bit, 2=64-bit)
    is_64 = ((raw >> 1) & 0x3) == 2
    addr = raw & 0xFFFFFFF0
    if is_64:
        hi = config_read32(bus, dev, func, off + 4)
        addr |= (hi & 0xFFFFFFFF) << 32
    return addr, False


# ── VirtIO Modern PCI register window ─────────────────────────────────────────

class _CommonCfg:
    """MMIO accessors for the VirtIO modern common-config register block."""

    def __init__(self, base: int) -> None:
        self._b = base

    # 32-bit fields (native width)
    def device_feature_select(self, v: int) -> None:
        _hal.mmio_write32(self._b + 0x00, v)
    def device_feature(self) -> int:
        return _hal.mmio_read32(self._b + 0x04)
    def driver_feature_select(self, v: int) -> None:
        _hal.mmio_write32(self._b + 0x08, v)
    def driver_feature(self, v: int) -> None:
        _hal.mmio_write32(self._b + 0x0C, v)

    # 8-bit fields
    def status(self) -> int:
        return _hal.mmio_read8(self._b + 0x14)
    def set_status(self, v: int) -> None:
        _hal.mmio_write8(self._b + 0x14, v)

    # 16-bit fields. We have no native mmio_{read,write}16 in HAL, so we
    # do RMW on the containing 32-bit word. QEMU's modern virtio MMIO
    # region accepts aligned 32-bit accesses for narrower fields.
    def _r16_at(self, off: int) -> int:
        word_off = off & ~0x3
        shift    = (off & 0x2) * 8
        return (_hal.mmio_read32(self._b + word_off) >> shift) & 0xFFFF

    def _w16_at(self, off: int, v: int) -> None:
        word_off = off & ~0x3
        shift    = (off & 0x2) * 8
        cur = _hal.mmio_read32(self._b + word_off)
        cur = cur & ~(0xFFFF << shift)
        cur |= (v & 0xFFFF) << shift
        _hal.mmio_write32(self._b + word_off, cur)

    def num_queues(self) -> int:                     return self._r16_at(0x12)
    def queue_select(self, v: int) -> None:          self._w16_at(0x16, v)
    def queue_size(self) -> int:                     return self._r16_at(0x18)
    def set_queue_size(self, v: int) -> None:        self._w16_at(0x18, v)
    def set_queue_msix_vector(self, v: int) -> None: self._w16_at(0x1A, v)
    def queue_enable(self) -> int:                   return self._r16_at(0x1C)
    def set_queue_enable(self, v: int) -> None:      self._w16_at(0x1C, v)
    def queue_notify_off(self) -> int:               return self._r16_at(0x1E)

    # 64-bit fields (split into two aligned 32-bit writes — low, high).
    def set_queue_desc(self, addr: int) -> None:
        _hal.mmio_write32(self._b + 0x20, addr & 0xFFFFFFFF)
        _hal.mmio_write32(self._b + 0x24, (addr >> 32) & 0xFFFFFFFF)
    def set_queue_driver(self, addr: int) -> None:
        _hal.mmio_write32(self._b + 0x28, addr & 0xFFFFFFFF)
        _hal.mmio_write32(self._b + 0x2C, (addr >> 32) & 0xFFFFFFFF)
    def set_queue_device(self, addr: int) -> None:
        _hal.mmio_write32(self._b + 0x30, addr & 0xFFFFFFFF)
        _hal.mmio_write32(self._b + 0x34, (addr >> 32) & 0xFFFFFFFF)


# ── Driver ────────────────────────────────────────────────────────────────────

class VirtioPciBlk:
    """VirtIO block device over modern PCI transport."""

    # Shape attributes (used by pci_bus.register_driver matching).
    VENDOR = VIRTIO_VENDOR
    DEVICE = VIRTIO_BLK_DEV_MODERN

    def __init__(self) -> None:
        self._common: _CommonCfg | None = None
        self._notify_base = 0
        self._notify_mult = 0
        self._dev_cfg     = 0   # device-specific cfg base (capacity etc.)
        self._num_sectors = 0
        self._desc_phys   = 0
        self._avail_phys  = 0
        self._used_phys   = 0
        self._next_desc   = 0
        self._avail_idx   = 0
        self._last_used   = 0
        self._queue_size  = QUEUE_SIZE
        self._queue_notify_off = 0

    # ── Capability walking ────────────────────────────────────────────────

    def _walk_caps(self, dev: PCIDevice) -> dict:
        """Return {cfg_type: (mmio_base, offset, length, notify_mult)}."""
        b, d, f = dev.addr.bus, dev.addr.device, dev.addr.function
        # Status register at 0x06; bit 4 → capabilities list present
        status = config_read16(b, d, f, 0x06)
        if not (status & 0x10):
            return {}
        cap_ptr = config_read8(b, d, f, 0x34) & 0xFC

        caps: dict[int, tuple[int, int, int, int]] = {}
        bar_addr_cache: dict[int, int] = {}

        guard = 32
        while cap_ptr != 0 and guard > 0:
            guard -= 1
            cap_id   = config_read8(b, d, f, cap_ptr + 0)
            cap_next = config_read8(b, d, f, cap_ptr + 1) & 0xFC
            if cap_id == 0x09:  # vendor-specific (VirtIO PCI cap)
                # cap_len at +2; cfg_type at +3, bar at +4
                cfg_type = config_read8(b, d, f, cap_ptr + 3)
                bar_idx  = config_read8(b, d, f, cap_ptr + 4) & 0x07
                offset   = config_read32(b, d, f, cap_ptr + 8)
                length   = config_read32(b, d, f, cap_ptr + 12)
                notify_mult = 0
                if cfg_type == VIRTIO_PCI_CAP_NOTIFY_CFG:
                    notify_mult = config_read32(b, d, f, cap_ptr + 16)
                if bar_idx not in bar_addr_cache:
                    addr, is_io = _bar_address(b, d, f, bar_idx)
                    bar_addr_cache[bar_idx] = 0 if is_io else addr
                base = bar_addr_cache[bar_idx]
                if base:
                    caps[cfg_type] = (base + offset, offset, length, notify_mult)
            cap_ptr = cap_next
        return caps

    def _enable_pci_master(self, dev: PCIDevice) -> None:
        """Set Memory-Space + Bus-Master in the PCI command register."""
        b, d, f = dev.addr.bus, dev.addr.device, dev.addr.function
        cmd = config_read16(b, d, f, 0x04)
        new = cmd | PCI_CMD_MEM_SPACE | PCI_CMD_BUS_MASTER
        if new == cmd:
            return
        # No public config_write32 yet; do a 32-bit RMW via CF8/CFC directly.
        from kernel.hal.io import outl
        full = config_read32(b, d, f, 0x04)
        full = (full & ~0xFFFF) | (new & 0xFFFF)
        addr = (1 << 31) | (b << 16) | (d << 11) | (f << 8) | (0x04 & 0xFC)
        outl(0xCF8, addr)
        outl(0xCFC, full)

    # ── Probe ─────────────────────────────────────────────────────────────

    def probe(self, dev: PCIDevice) -> bool:
        if dev.vendor_id != VIRTIO_VENDOR:
            return False
        if dev.device_id not in (VIRTIO_BLK_DEV_MODERN, VIRTIO_BLK_DEV_LEGACY):
            return False

        self._enable_pci_master(dev)

        caps = self._walk_caps(dev)
        if VIRTIO_PCI_CAP_COMMON_CFG not in caps:
            log.info(f"virtio-blk-pci: no common cfg cap at {dev.addr}")
            return False
        if VIRTIO_PCI_CAP_NOTIFY_CFG not in caps:
            log.info(f"virtio-blk-pci: no notify cfg cap at {dev.addr}")
            return False
        if VIRTIO_PCI_CAP_DEVICE_CFG not in caps:
            log.info(f"virtio-blk-pci: no device cfg cap at {dev.addr}")
            return False

        common_base, _, _, _           = caps[VIRTIO_PCI_CAP_COMMON_CFG]
        notify_base, _, _, notify_mult = caps[VIRTIO_PCI_CAP_NOTIFY_CFG]
        dev_cfg_base, _, _, _          = caps[VIRTIO_PCI_CAP_DEVICE_CFG]

        self._common      = _CommonCfg(common_base)
        self._notify_base = notify_base
        self._notify_mult = notify_mult
        self._dev_cfg     = dev_cfg_base

        c = self._common

        # ── Reset and acknowledge ─────────────────────────────────────────
        c.set_status(0)
        # Spin briefly until reset is observed.
        for _ in range(8):
            if c.status() == 0:
                break
        c.set_status(STATUS_ACK)
        c.set_status(STATUS_ACK | STATUS_DRIVER)

        # ── Feature negotiation: we only accept VIRTIO_F_VERSION_1 ───────
        # device_feature_select=1 selects bits 32..63
        c.device_feature_select(1)
        feat_hi = c.device_feature()
        c.device_feature_select(0)
        _ = c.device_feature()   # drain low word

        # VIRTIO_F_VERSION_1 = bit 32 (i.e. bit 0 of the high word)
        accept_hi = 0x1 if (feat_hi & 0x1) else 0
        c.driver_feature_select(0)
        c.driver_feature(0)
        c.driver_feature_select(1)
        c.driver_feature(accept_hi)

        c.set_status(STATUS_ACK | STATUS_DRIVER | STATUS_FEATURES_OK)
        if not (c.status() & STATUS_FEATURES_OK):
            log.info("virtio-blk-pci: device rejected FEATURES_OK")
            return False

        # ── Capacity ──────────────────────────────────────────────────────
        cap_lo = _hal.mmio_read32(self._dev_cfg + 0)
        cap_hi = _hal.mmio_read32(self._dev_cfg + 4)
        self._num_sectors = cap_lo | (cap_hi << 32)

        # ── Virtqueue 0 setup ─────────────────────────────────────────────
        c.queue_select(0)
        max_q = c.queue_size()
        if max_q == 0:
            log.info("virtio-blk-pci: queue 0 absent")
            return False
        n = min(max_q, QUEUE_SIZE)
        # Round to power of 2
        p = 1
        while p * 2 <= n:
            p *= 2
        self._queue_size = p
        c.set_queue_size(p)
        c.set_queue_msix_vector(0xFFFF)   # NO_VECTOR — we poll
        self._queue_notify_off = c.queue_notify_off()

        # Allocate descriptor table + available ring + used ring.
        desc_sz   = self._queue_size * 16
        avail_sz  = 4 + self._queue_size * 2 + 2
        avail_off = desc_sz
        used_off  = (avail_off + avail_sz + PAGE_SIZE - 1) & ~(PAGE_SIZE - 1)
        used_sz   = 4 + self._queue_size * 8 + 2
        total     = used_off + used_sz

        raw = _hal.dma_alloc(total + PAGE_SIZE)
        base_phys = (raw + PAGE_SIZE - 1) & ~(PAGE_SIZE - 1)
        self._desc_phys  = base_phys
        self._avail_phys = base_phys + avail_off
        self._used_phys  = base_phys + used_off

        c.set_queue_desc(self._desc_phys)
        c.set_queue_driver(self._avail_phys)
        c.set_queue_device(self._used_phys)
        c.set_queue_enable(1)

        c.set_status(STATUS_ACK | STATUS_DRIVER | STATUS_FEATURES_OK | STATUS_DRIVER_OK)

        log.info(
            f"virtio-blk: ready at PCI {dev.addr}, {self._num_sectors} sectors "
            f"({self._num_sectors * 512 // 1024 // 1024} MiB)"
        )
        return True

    def remove(self, dev: PCIDevice) -> None:
        if self._common:
            self._common.set_status(0)

    # ── Descriptor ring helpers ───────────────────────────────────────────

    def _write_desc(self, idx: int, addr: int, length: int,
                    flags: int, nxt: int) -> None:
        base = self._desc_phys + idx * 16
        _hal.mmio_write32(base + 0,  addr & 0xFFFFFFFF)
        _hal.mmio_write32(base + 4,  (addr >> 32) & 0xFFFFFFFF)
        _hal.mmio_write32(base + 8,  length)
        _hal.mmio_write32(base + 12, (flags & 0xFFFF) | ((nxt & 0xFFFF) << 16))

    def _avail_push(self, head: int) -> None:
        slot = self._avail_idx % self._queue_size
        ring_addr = self._avail_phys + 4 + slot * 2
        _hal.mmio_write8(ring_addr,     head & 0xFF)
        _hal.mmio_write8(ring_addr + 1, (head >> 8) & 0xFF)
        self._avail_idx = (self._avail_idx + 1) & 0xFFFF
        idx_addr = self._avail_phys + 2
        _hal.mmio_write8(idx_addr,     self._avail_idx & 0xFF)
        _hal.mmio_write8(idx_addr + 1, (self._avail_idx >> 8) & 0xFF)

    def _kick(self) -> None:
        """Notify the device that the available ring has new entries."""
        notify_addr = self._notify_base + self._queue_notify_off * self._notify_mult
        # The doorbell value is the queue index (we have one queue → 0).
        _hal.mmio_write32(notify_addr, 0)

    def _used_idx(self) -> int:
        # used ring header: u16 flags @ +0, u16 idx @ +2.
        # Read the aligned u32 at +0 and extract the high half.
        return (_hal.mmio_read32(self._used_phys) >> 16) & 0xFFFF

    def _used_pop(self) -> tuple[int, int]:
        ring = self._used_phys + 4 + (self._last_used % self._queue_size) * 8
        desc_id = _hal.mmio_read32(ring)
        length  = _hal.mmio_read32(ring + 4)
        self._last_used = (self._last_used + 1) & 0xFFFF
        return desc_id, length

    # ── Block I/O ─────────────────────────────────────────────────────────

    async def _submit(self, op: int, lba: int, data: bytes | None) -> tuple[int, int]:
        """
        Submit a block request. Returns (status_phys, data_phys) so callers
        can read back the buffer (for reads).
        """
        if self._common is None:
            raise RuntimeError("virtio-blk-pci not initialised")

        hdr_phys = _hal.dma_alloc(16)
        _hal.mmio_write32(hdr_phys + 0, op)
        _hal.mmio_write32(hdr_phys + 4, 0)
        _hal.mmio_write32(hdr_phys + 8,  lba & 0xFFFFFFFF)
        _hal.mmio_write32(hdr_phys + 12, (lba >> 32) & 0xFFFFFFFF)

        data_phys = _hal.dma_alloc(512)
        if op == VIRTIO_BLK_T_OUT and data is not None:
            payload = bytes(data) + bytes(max(0, 512 - len(data)))
            payload = payload[:512]
            for i in range(0, 512, 4):
                w = (payload[i]
                     | (payload[i+1] << 8)
                     | (payload[i+2] << 16)
                     | (payload[i+3] << 24))
                _hal.mmio_write32(data_phys + i, w)

        stat_phys = _hal.dma_alloc(4)
        # Pre-stamp status so we can tell if the device wrote a response.
        _hal.mmio_write8(stat_phys, 0xFF)

        d0 = self._next_desc; self._next_desc = (self._next_desc + 1) % self._queue_size
        d1 = self._next_desc; self._next_desc = (self._next_desc + 1) % self._queue_size
        d2 = self._next_desc; self._next_desc = (self._next_desc + 1) % self._queue_size

        # Header: device-readable
        self._write_desc(d0, hdr_phys, 16, VRING_DESC_F_NEXT, d1)
        # Data: device-writable on read, device-readable on write
        data_flags = VRING_DESC_F_NEXT
        if op == VIRTIO_BLK_T_IN:
            data_flags |= VRING_DESC_F_WRITE
        self._write_desc(d1, data_phys, 512, data_flags, d2)
        # Status: device-writable
        self._write_desc(d2, stat_phys, 1, VRING_DESC_F_WRITE, 0)

        self._avail_push(d0)
        self._kick()

        target = (self._last_used + 1) & 0xFFFF
        spins = 0
        while self._used_idx() != target:
            await asyncio.sleep(0.001)
            spins += 1
            if spins > 5000:   # ~5s sanity cap
                raise TimeoutError(
                    f"virtio-blk-pci: I/O timed out (op={op} lba={lba})")
        self._used_pop()

        return stat_phys, data_phys

    async def read_sector(self, lba: int) -> bytes:
        """Read 512 bytes at logical block address `lba`."""
        _stat, data_phys = await self._submit(VIRTIO_BLK_T_IN, lba, None)
        result = bytearray(512)
        for i in range(0, 512, 4):
            w = _hal.mmio_read32(data_phys + i)
            result[i]   =  w        & 0xFF
            result[i+1] = (w >>  8) & 0xFF
            result[i+2] = (w >> 16) & 0xFF
            result[i+3] = (w >> 24) & 0xFF
        return bytes(result)

    async def write_sector(self, lba: int, data: bytes) -> None:
        """Write 512 bytes at logical block address `lba`."""
        if len(data) > 512:
            raise ValueError("write_sector: data must be <=512 bytes")
        await self._submit(VIRTIO_BLK_T_OUT, lba, data)

    @property
    def num_sectors(self) -> int:
        return self._num_sectors


# ── Discovery ─────────────────────────────────────────────────────────────────

def find_virtio_blk_pci() -> VirtioPciBlk | None:
    """Look up an enumerated virtio-blk-pci device on the PCI bus and probe it."""
    candidates: list[PCIDevice] = []
    candidates += pci_bus.find_by_id(VIRTIO_VENDOR, VIRTIO_BLK_DEV_MODERN)
    candidates += pci_bus.find_by_id(VIRTIO_VENDOR, VIRTIO_BLK_DEV_LEGACY)
    for dev in candidates:
        existing = getattr(dev, 'driver', None)
        if isinstance(existing, VirtioPciBlk):
            # Already bound by pci_bus.bind_drivers() — reuse it.
            return existing
        if existing is not None:
            continue
        drv = VirtioPciBlk()
        if drv.probe(dev):
            dev.driver = drv
            return drv
    return None


# Register so that a future `pci_bus.bind_drivers()` pass picks us up too.
# (Idempotent: register_driver only stores the most recent class per id.)
# This means just importing this module on x86 is enough to attach the
# driver during the boot-time PCI binding pass.
pci_bus.register_driver(
    VirtioPciBlk, vendor=VIRTIO_VENDOR, device=VIRTIO_BLK_DEV_MODERN
)
pci_bus.register_driver(
    VirtioPciBlk, vendor=VIRTIO_VENDOR, device=VIRTIO_BLK_DEV_LEGACY
)
