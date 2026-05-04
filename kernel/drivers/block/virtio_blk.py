"""
kernel.drivers.block.virtio_blk — VirtIO block device (MMIO transport).

QEMU virt arm64 exposes virtio-mmio devices at 0x0a000000, one per 0x200
bytes, up to 8 slots.  We scan for a block device (DeviceID = 2).

VirtIO MMIO v2 register map (offsets from device base):
  0x000  MagicValue    = 0x74726976 ('virt')
  0x004  Version       = 0x2
  0x008  DeviceID      (2 = block)
  0x010  DeviceFeatures / 0x014 DeviceFeaturesSel
  0x020  DriverFeatures / 0x024 DriverFeaturesSel
  0x030  QueueSel
  0x034  QueueNumMax
  0x038  QueueNum
  0x044  QueueReady
  0x050  QueueNotify
  0x060  InterruptStatus
  0x064  InterruptAck
  0x070  Status
  0x080  QueueDescLow / 0x084 QueueDescHigh
  0x090  QueueDriverLow / 0x094 QueueDriverHigh (available ring)
  0x0A0  QueueDeviceLow / 0x0A4 QueueDeviceHigh (used ring)
  0x100+ Config (block: u64 capacity in 512-byte sectors)
"""

import asyncio
import _hal
import kernel.log as log

VIRTIO_MMIO_BASE    = 0x0a000000
VIRTIO_MMIO_STRIDE  = 0x200
VIRTIO_MMIO_DEVS    = 32
VIRTIO_MAGIC        = 0x74726976   # 'virt' LE
VIRTIO_DEV_BLK      = 2

VRING_DESC_F_NEXT   = 1
VRING_DESC_F_WRITE  = 2

PAGE_SIZE   = 4096
QUEUE_SIZE  = 16   # must be power-of-2; 16 is enough for sequential I/O

STATUS_ACK       = 1
STATUS_DRIVER    = 2
STATUS_FEATURES  = 8
STATUS_DRIVER_OK = 4

VIRTIO_BLK_T_IN  = 0   # read sectors
VIRTIO_BLK_T_OUT = 1   # write sectors


def _r32(base: int, off: int) -> int:
    return _hal.mmio_read32(base + off)

def _w32(base: int, off: int, v: int) -> None:
    _hal.mmio_write32(base + off, v)

def _w8(addr: int, v: int) -> None:
    _hal.mmio_write8(addr, v)


class VirtioMmioBlk:
    """Driver for a single VirtIO-MMIO block device."""

    def __init__(self, base: int) -> None:
        self._base       = base
        self._version    = 0
        self._num_sectors = 0
        self._desc_phys  = 0
        self._avail_phys = 0
        self._used_phys  = 0
        self._next_desc  = 0
        self._avail_idx  = 0
        self._last_used  = 0

    # ── Register helpers ──────────────────────────────────────────────────────

    def _status(self, s: int) -> None:   _w32(self._base, 0x070, s)
    def _get_status(self) -> int:        return _r32(self._base, 0x070)

    # ── Probe / initialise ────────────────────────────────────────────────────

    def probe(self) -> bool:
        magic   = _r32(self._base, 0x000)
        if magic != VIRTIO_MAGIC:
            return False
        version = _r32(self._base, 0x004)
        dev_id  = _r32(self._base, 0x008)
        if dev_id != 0:
            log.info(f"virtio-mmio @{self._base:#x}: ver={version} devid={dev_id}")
        if version not in (1, 2):
            return False
        if dev_id != VIRTIO_DEV_BLK:
            return False

        self._version = version

        # Reset
        self._status(0)
        self._status(STATUS_ACK)
        self._status(STATUS_ACK | STATUS_DRIVER)

        if version == 1:
            # Legacy: set GuestPageSize; features are a single 32-bit word
            _w32(self._base, 0x028, PAGE_SIZE)   # GuestPageSize = 4096
            _w32(self._base, 0x020, 0)           # GuestFeatures = 0
        else:
            # Modern: feature selection pages
            _w32(self._base, 0x024, 0)   # DriverFeaturesSel = page 0
            _w32(self._base, 0x020, 0)   # DriverFeatures = 0
            _w32(self._base, 0x024, 1)   # DriverFeaturesSel = page 1
            _w32(self._base, 0x020, 0)

        self._status(STATUS_ACK | STATUS_DRIVER | STATUS_FEATURES)

        # Read disk capacity from config space
        # v1 legacy config starts at 0x100; v2 also at 0x100
        cap_lo = _r32(self._base, 0x100)
        cap_hi = _r32(self._base, 0x104)
        self._num_sectors = cap_lo | (cap_hi << 32)

        # Set up virtqueue 0 — layout differs between v1 (legacy) and v2
        _w32(self._base, 0x030, 0)             # QueueSel = 0
        _w32(self._base, 0x038, QUEUE_SIZE)    # QueueNum

        # Allocate queue memory: descriptor table + available ring + used ring
        desc_sz   = QUEUE_SIZE * 16
        avail_sz  = 4 + QUEUE_SIZE * 2 + 2
        avail_off = desc_sz
        used_off  = (avail_off + avail_sz + PAGE_SIZE - 1) & ~(PAGE_SIZE - 1)
        used_sz   = 4 + QUEUE_SIZE * 8 + 2
        total     = used_off + used_sz

        raw = _hal.dma_alloc(total + PAGE_SIZE)
        base_phys = (raw + PAGE_SIZE - 1) & ~(PAGE_SIZE - 1)
        self._desc_phys  = base_phys
        self._avail_phys = base_phys + avail_off
        self._used_phys  = base_phys + used_off

        if self._version == 1:
            # Legacy: single QueuePFN register (page frame number)
            _w32(self._base, 0x03C, PAGE_SIZE)          # QueueAlign
            _w32(self._base, 0x040, base_phys >> 12)    # QueuePFN
        else:
            # Modern: separate descriptor / driver / device addresses
            _w32(self._base, 0x080, base_phys           & 0xFFFFFFFF)
            _w32(self._base, 0x084, (base_phys >> 32)   & 0xFFFFFFFF)
            _w32(self._base, 0x090, self._avail_phys     & 0xFFFFFFFF)
            _w32(self._base, 0x094, (self._avail_phys >> 32) & 0xFFFFFFFF)
            _w32(self._base, 0x0A0, self._used_phys      & 0xFFFFFFFF)
            _w32(self._base, 0x0A4, (self._used_phys >> 32) & 0xFFFFFFFF)
            _w32(self._base, 0x044, 1)                  # QueueReady = 1

        self._status(STATUS_ACK | STATUS_DRIVER | STATUS_FEATURES | STATUS_DRIVER_OK)

        log.info(f"virtio-blk: ready at {self._base:#x}, {self._num_sectors} sectors "
                 f"({self._num_sectors * 512 // 1024 // 1024} MiB)")
        return True

    # ── Descriptor table helpers ──────────────────────────────────────────────

    def _write_desc(self, idx: int, addr: int, length: int,
                    flags: int, nxt: int) -> None:
        # Direct DMA-buffer writes — `_w32(base, off, v)` is for register I/O.
        base = self._desc_phys + idx * 16
        _hal.mmio_write32(base + 0,  addr & 0xFFFFFFFF)
        _hal.mmio_write32(base + 4,  (addr >> 32) & 0xFFFFFFFF)
        _hal.mmio_write32(base + 8,  length)
        # flags (LE16) at byte 12, next (LE16) at byte 14 → one LE32 word
        _hal.mmio_write32(base + 12, (flags & 0xFFFF) | ((nxt & 0xFFFF) << 16))

    def _avail_push(self, head: int) -> None:
        slot = self._avail_idx % QUEUE_SIZE
        ring_addr = self._avail_phys + 4 + slot * 2
        _w8(ring_addr,     head & 0xFF)
        _w8(ring_addr + 1, (head >> 8) & 0xFF)
        self._avail_idx += 1
        idx_addr = self._avail_phys + 2
        _w8(idx_addr,     self._avail_idx & 0xFF)
        _w8(idx_addr + 1, (self._avail_idx >> 8) & 0xFF)

    def _used_idx(self) -> int:
        # used ring header: u16 flags @ +0, u16 idx @ +2. Read the aligned
        # u32 at +0 and extract the high half (matches the PCI driver).
        return (_hal.mmio_read32(self._used_phys) >> 16) & 0xFFFF

    def _used_pop(self) -> tuple[int, int]:
        ring = self._used_phys + 4 + (self._last_used % QUEUE_SIZE) * 8
        desc_id = _hal.mmio_read32(ring)
        length  = _hal.mmio_read32(ring + 4)
        self._last_used += 1
        return desc_id, length

    # ── I/O ───────────────────────────────────────────────────────────────────

    async def read_sector(self, lba: int) -> bytes:
        """Read 512 bytes at logical block address lba."""
        # Header: type(u32) + reserved(u32) + sector(u64). Use direct MMIO
        # writes — the 3-arg `_w32(base, off, v)` helper is for register
        # I/O, not DMA-buffer fills.
        hdr = _hal.dma_alloc(16)
        _hal.mmio_write32(hdr + 0,  VIRTIO_BLK_T_IN)
        _hal.mmio_write32(hdr + 4,  0)
        _hal.mmio_write32(hdr + 8,  lba & 0xFFFFFFFF)
        _hal.mmio_write32(hdr + 12, (lba >> 32) & 0xFFFFFFFF)

        data_phys = _hal.dma_alloc(512)
        stat_phys = _hal.dma_alloc(4)

        d0 = self._next_desc; self._next_desc = (self._next_desc + 1) % QUEUE_SIZE
        d1 = self._next_desc; self._next_desc = (self._next_desc + 1) % QUEUE_SIZE
        d2 = self._next_desc; self._next_desc = (self._next_desc + 1) % QUEUE_SIZE

        self._write_desc(d0, hdr,       16,  VRING_DESC_F_NEXT, d1)
        self._write_desc(d1, data_phys, 512, VRING_DESC_F_WRITE | VRING_DESC_F_NEXT, d2)
        self._write_desc(d2, stat_phys, 1,   VRING_DESC_F_WRITE, 0)

        self._avail_push(d0)
        _w32(self._base, 0x050, 0)   # QueueNotify = 0 (kick)

        # Poll for completion, yielding to the event loop between checks
        target = (self._last_used + 1) & 0xFFFF
        while self._used_idx() != target:
            await asyncio.sleep(0.001)
        self._used_pop()

        # Copy 512 bytes from DMA buffer
        result = bytearray(512)
        for i in range(0, 512, 4):
            w = _hal.mmio_read32(data_phys + i)
            result[i]   =  w        & 0xFF
            result[i+1] = (w >>  8) & 0xFF
            result[i+2] = (w >> 16) & 0xFF
            result[i+3] = (w >> 24) & 0xFF
        return bytes(result)

    async def write_sector(self, lba: int, data: bytes) -> None:
        """Write 512 bytes at logical block address lba."""
        if len(data) != 512:
            raise ValueError(f"write_sector: data must be 512 bytes, got {len(data)}")

        hdr = _hal.dma_alloc(16)
        _hal.mmio_write32(hdr + 0,  VIRTIO_BLK_T_OUT)
        _hal.mmio_write32(hdr + 4,  0)
        _hal.mmio_write32(hdr + 8,  lba & 0xFFFFFFFF)
        _hal.mmio_write32(hdr + 12, (lba >> 32) & 0xFFFFFFFF)

        data_phys = _hal.dma_alloc(512)
        for i in range(0, 512, 4):
            w = (data[i]
                 | (data[i+1] << 8)
                 | (data[i+2] << 16)
                 | (data[i+3] << 24))
            _hal.mmio_write32(data_phys + i, w)

        stat_phys = _hal.dma_alloc(4)

        d0 = self._next_desc; self._next_desc = (self._next_desc + 1) % QUEUE_SIZE
        d1 = self._next_desc; self._next_desc = (self._next_desc + 1) % QUEUE_SIZE
        d2 = self._next_desc; self._next_desc = (self._next_desc + 1) % QUEUE_SIZE

        # Data descriptor is device-read (no VRING_DESC_F_WRITE on the data
        # buffer for T_OUT). Status descriptor is device-write as always.
        self._write_desc(d0, hdr,       16,  VRING_DESC_F_NEXT, d1)
        self._write_desc(d1, data_phys, 512, VRING_DESC_F_NEXT, d2)
        self._write_desc(d2, stat_phys, 1,   VRING_DESC_F_WRITE, 0)

        self._avail_push(d0)
        _w32(self._base, 0x050, 0)

        target = (self._last_used + 1) & 0xFFFF
        while self._used_idx() != target:
            await asyncio.sleep(0.001)
        self._used_pop()

    @property
    def num_sectors(self) -> int:
        return self._num_sectors


def _find_virtio_mmio_blk() -> 'VirtioMmioBlk | None':
    """Scan virtio-mmio slots and return the first block device found."""
    for i in range(VIRTIO_MMIO_DEVS):
        base = VIRTIO_MMIO_BASE + i * VIRTIO_MMIO_STRIDE
        dev = VirtioMmioBlk(base)
        if dev.probe():
            return dev
    return None


def find_virtio_blk():
    """
    Locate a VirtIO block device using the transport appropriate to this
    architecture.

    arm64:   virtio-mmio scan (legacy and modern).
    x86_64:  virtio-blk-pci via the PCI bus.

    The returned driver exposes a uniform surface (``num_sectors`` int and
    ``read_sector(lba)`` / ``write_sector(lba, data)`` coroutines), so
    higher layers (kernel.fs.ext2, /home + /apps boot wiring) don't care
    which transport is in use.
    """
    arch = getattr(_hal, 'ARCH', 'x86_64')
    if arch == 'arm64':
        return _find_virtio_mmio_blk()
    # Lazy import keeps arm64 boots free of any PCI machinery.
    from kernel.drivers.block import virtio_blk_pci
    return virtio_blk_pci.find_virtio_blk_pci()


blk = None  # populated by kernel.boot once a device is bound
