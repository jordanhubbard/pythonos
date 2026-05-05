"""
kernel.drivers.net.virtio_net — VirtIO network device driver.

VirtIO-net is the paravirtualized NIC used by QEMU, VirtualBox, and most
hypervisors. It exposes receive (RX) and transmit (TX) virtqueues backed
by descriptor rings in shared memory.

Spec: virtio-v1.2 section 5.1 (Network Device)

Register layout (MMIO or PCI I/O BAR0):
  0x00  DeviceFeatures
  0x04  DriverFeatures
  0x08  QueueAddr (page-aligned, >> PAGE_SHIFT)
  0x0C  QueueSize
  0x0E  QueueSelect
  0x10  QueueNotify
  0x12  DeviceStatus
  0x13  ISRStatus
  0x14  ConfigSpace (MAC, status, max_virtqueue_pairs)
"""


import asyncio
import struct
from dataclasses import dataclass, field

from kernel.bus.pci import PCIDevice, PCIDriver, config_read32
from kernel.hal.io import mmio_read32, mmio_write32, mmio_read8
import kernel.log as log

# ── VirtIO constants ──────────────────────────────────────────────────────────

VIRTIO_VENDOR   = 0x1AF4
VIRTIO_NET_DEV  = 0x1000   # legacy; modern devices use 0x1041

# Feature bits
VIRTIO_F_VERSION_1     = 1 << 32
VIRTIO_NET_F_MAC       = 1 << 5
VIRTIO_NET_F_STATUS    = 1 << 16
VIRTIO_NET_F_CTRL_VQ   = 1 << 17

# Device status register bits
VIRTIO_STATUS_ACK       = 0x01
VIRTIO_STATUS_DRIVER    = 0x02
VIRTIO_STATUS_DRIVER_OK = 0x04
VIRTIO_STATUS_FEATURES  = 0x08
VIRTIO_STATUS_FAILED    = 0x80

# Descriptor flags
VRING_DESC_F_NEXT     = 1
VRING_DESC_F_WRITE    = 2   # device writes into this buffer

PAGE_SIZE  = 4096
QUEUE_SIZE = 256   # must be power of 2; virtio legacy limit = 256


# ── VirtIO PCI register accessors ─────────────────────────────────────────────

class VirtIORegs:
    """Wraps PCI BAR0 I/O port register access."""

    def __init__(self, iobase: int) -> None:
        self._base = iobase

    def _r8 (self, off: int) -> int:
        from kernel.hal.io import inb;  return inb(self._base + off)
    def _r16(self, off: int) -> int:
        from kernel.hal.io import inw;  return inw(self._base + off)
    def _r32(self, off: int) -> int:
        from kernel.hal.io import inl;  return inl(self._base + off)
    def _w8 (self, off: int, v: int) -> None:
        from kernel.hal.io import outb; outb(self._base + off, v)
    def _w16(self, off: int, v: int) -> None:
        from kernel.hal.io import outw; outw(self._base + off, v)
    def _w32(self, off: int, v: int) -> None:
        from kernel.hal.io import outl; outl(self._base + off, v)

    @property
    def device_features(self) -> int: return self._r32(0x00)
    @property
    def queue_size(self) -> int:      return self._r16(0x0C)
    @property
    def isr_status(self) -> int:      return self._r8(0x13)

    def set_driver_features(self, f: int) -> None: self._w32(0x04, f)
    def set_queue_select(self, q: int)    -> None: self._w16(0x0E, q)
    def set_queue_addr(self, pfn: int)    -> None: self._w32(0x08, pfn)
    def set_queue_notify(self, q: int)    -> None: self._w16(0x10, q)
    def set_status(self, s: int)          -> None: self._w8(0x12, s)
    def get_status(self) -> int:                   return self._r8(0x12)

    def read_mac(self) -> bytes:
        return bytes(self._r8(0x14 + i) for i in range(6))


# ── Virtqueue ─────────────────────────────────────────────────────────────────

@dataclass
class VirtqDesc:
    """One entry in the descriptor table (16 bytes)."""
    addr:  int   # physical address of buffer
    length: int
    flags: int
    next:  int   # index of next descriptor (if VRING_DESC_F_NEXT)

    def pack(self) -> bytes:
        return struct.pack("<QIHH", self.addr, self.length, self.flags, self.next)


class Virtqueue:
    """
    Legacy virtqueue layout in a single physically-contiguous page-aligned region.

    Memory layout (for QUEUE_SIZE N):
      Descriptor table:  N * 16 bytes
      Available ring:    (N + 3) * 2 bytes    (flags, idx, ring[N], used_event)
      [padding to next PAGE_SIZE boundary]
      Used ring:         (N + 3) * 4 + 4 bytes
    """

    def __init__(self, n: int, phys_base: int) -> None:
        self.n         = n
        self.phys      = phys_base
        self._next_desc = 0
        self._avail_idx = 0
        self._last_used = 0
        # Maps descriptor index → (physical_address, capacity) for RX buffers
        self._desc_bufs: dict[int, tuple[int, int]] = {}

        # Compute sub-region offsets within the allocated memory
        desc_size  = n * 16
        avail_size = (n + 3) * 2
        avail_off  = desc_size
        used_off   = (avail_off + avail_size + PAGE_SIZE - 1) & ~(PAGE_SIZE - 1)
        self._avail_phys = phys_base + avail_off
        self._used_phys  = phys_base + used_off

    def alloc_desc(self) -> int:
        idx = self._next_desc
        self._next_desc = (self._next_desc + 1) % self.n
        return idx

    def write_desc(self, idx: int, desc: VirtqDesc) -> None:
        packed = desc.pack()
        base = self.phys + idx * 16
        for i, b in enumerate(packed):
            mmio_write32(base + i, b) if i % 4 == 0 else None
        # Write as 4-byte chunks via mmio
        import struct as _s
        words = _s.unpack("<4I", packed)
        for i, w in enumerate(words):
            mmio_write32(base + i * 4, w)

    def avail_push(self, desc_idx: int) -> None:
        """Add descriptor to the available ring and advance the index."""
        ring_entry_addr = self._avail_phys + 4 + (self._avail_idx % self.n) * 2
        # Write entry as 16-bit
        from kernel.hal.io import outw
        # Can't outw to MMIO — write via mmio_write32 (lower 16 bits)
        mmio_write32(ring_entry_addr, desc_idx & 0xFFFF)
        self._avail_idx += 1
        # Write updated idx field (offset 2 in avail ring)
        mmio_write32(self._avail_phys + 2, self._avail_idx & 0xFFFF)

    def used_has_entries(self) -> bool:
        used_idx = mmio_read32(self._used_phys + 2) & 0xFFFF
        return used_idx != self._last_used

    def used_pop(self) -> tuple[int, int]:
        """Return (desc_idx, length) of next used entry."""
        ring_base = self._used_phys + 4 + (self._last_used % self.n) * 8
        desc_idx  = mmio_read32(ring_base)
        length    = mmio_read32(ring_base + 4)
        self._last_used += 1
        return desc_idx, length

    @property
    def pfn(self) -> int:
        return self.phys >> 12   # page frame number for VirtIO register


# ── VirtIO-net packet header ──────────────────────────────────────────────────

VIRTIO_NET_HDR_SIZE = 10   # legacy header: flags,gso_type,hdr_len,gso_size,csum_start,csum_offset

def make_net_header() -> bytes:
    # flags=0, gso_type=0, hdr_len=0, gso_size=0, csum_start=0, csum_offset=0
    return bytes(VIRTIO_NET_HDR_SIZE)


# ── Driver ────────────────────────────────────────────────────────────────────

class VirtIONetDriver:
    VENDOR  = VIRTIO_VENDOR
    DEVICE  = VIRTIO_NET_DEV

    def __init__(self) -> None:
        self._rxq: Virtqueue | None = None
        self._txq: Virtqueue | None = None
        self._regs: VirtIORegs | None = None
        self._mac: bytes = bytes(6)

    def probe(self, dev: PCIDevice) -> bool:
        # BAR0 is an I/O port region
        iobase = dev.bars[0] & ~0x3   # mask I/O indicator bits
        self._regs = VirtIORegs(iobase)
        regs = self._regs

        # Negotiation sequence (virtio legacy)
        regs.set_status(0)                           # reset
        regs.set_status(VIRTIO_STATUS_ACK)
        regs.set_status(VIRTIO_STATUS_ACK | VIRTIO_STATUS_DRIVER)

        features = regs.device_features & (VIRTIO_NET_F_MAC | VIRTIO_NET_F_STATUS)
        regs.set_driver_features(features)
        regs.set_status(VIRTIO_STATUS_ACK | VIRTIO_STATUS_DRIVER | VIRTIO_STATUS_FEATURES)

        if features & VIRTIO_NET_F_MAC:
            self._mac = regs.read_mac()
            log.info(f"virtio-net: MAC {':'.join(f'{b:02x}' for b in self._mac)}")

        # Set up RX queue (index 0) and TX queue (index 1)
        self._rxq = self._setup_queue(regs, 0)
        self._txq = self._setup_queue(regs, 1)

        regs.set_status(
            VIRTIO_STATUS_ACK | VIRTIO_STATUS_DRIVER |
            VIRTIO_STATUS_FEATURES | VIRTIO_STATUS_DRIVER_OK
        )

        # Pre-populate RX ring with receive buffers
        self._fill_rx_ring()

        log.info(f"virtio-net: ready on BAR0={iobase:#x}")
        return True

    def _setup_queue(self, regs: VirtIORegs, idx: int) -> Virtqueue:
        regs.set_queue_select(idx)
        n = min(regs.queue_size, QUEUE_SIZE)
        import _hal
        # desc(4096) + avail(518) pad-to-page → used starts at 8192;
        # used ring = 6 + n*8 = 2054 bytes → total 10246 → need 3 pages
        phys = _hal.dma_alloc(3 * PAGE_SIZE)
        q = Virtqueue(n, phys)
        regs.set_queue_addr(q.pfn)
        return q

    def _fill_rx_ring(self) -> None:
        if not self._rxq:
            return
        import _hal
        first_phys = None
        for _ in range(self._rxq.n // 2):
            phys = _hal.dma_alloc(1526)   # max Ethernet frame + virtio header
            if first_phys is None:
                first_phys = phys
            idx = self._rxq.alloc_desc()
            self._rxq._desc_bufs[idx] = (phys, 1526)  # record for later read-back
            desc = VirtqDesc(
                addr=phys,
                length=1526,
                flags=VRING_DESC_F_WRITE,
                next=0,
            )
            self._rxq.write_desc(idx, desc)
            self._rxq.avail_push(idx)
        log.info(f"virtio-net: filled {self._rxq.n // 2} RX bufs, first_buf={first_phys:#x}, "
                 f"avail_idx={self._rxq._avail_idx}")
        if self._regs:
            self._regs.set_queue_notify(0)

    def send_nowait(self, frame: bytes) -> None:
        """Transmit an Ethernet frame without yielding.

        The async wrapper below exists for the network stack's normal
        cooperative path. The native GUI bridge also needs to pump TCP while
        synchronous bridge.call() users are blocked waiting for a response.
        """
        if not self._txq or not self._regs:
            log.info("tx: no txq/regs — drop")
            return
        import _hal
        payload = make_net_header() + frame
        phys = _hal.dma_alloc(len(payload))
        # Copy payload bytes into DMA buffer via MMIO writes
        for i in range(0, len(payload) - 3, 4):
            w = payload[i] | (payload[i+1] << 8) | (payload[i+2] << 16) | (payload[i+3] << 24)
            mmio_write32(phys + i, w)
        # Write remaining bytes
        rem = len(payload) % 4
        if rem:
            tail = 0
            for j in range(rem):
                tail |= payload[len(payload) - rem + j] << (j * 8)
            mmio_write32(phys + len(payload) - rem, tail)

        idx = self._txq.alloc_desc()
        desc = VirtqDesc(addr=phys, length=len(payload), flags=0, next=0)
        self._txq.write_desc(idx, desc)
        self._txq.avail_push(idx)
        self._regs.set_queue_notify(1)   # kick TX queue

    async def send(self, frame: bytes) -> None:
        """Transmit an Ethernet frame."""
        self.send_nowait(frame)

    def recv_nowait(self) -> bytes | None:
        """Return one Ethernet frame if RX has completed, else ``None``."""
        if self._rxq and self._rxq.used_has_entries():
            frame = self._rx_dequeue()
            if frame is not None and len(frame) > VIRTIO_NET_HDR_SIZE:
                return frame[VIRTIO_NET_HDR_SIZE:]
        return None

    async def recv(self) -> bytes:
        """Poll the VirtIO RX used ring for completed frames (no IRQ required)."""
        while True:
            frame = self.recv_nowait()
            if frame is not None:
                return frame
            await asyncio.sleep(0)

    def _rx_dequeue(self) -> bytes | None:
        """Pull one entry from the used RX ring, read frame from DMA, resubmit descriptor."""
        if not self._rxq:
            return None
        desc_idx, length = self._rxq.used_pop()
        buf = self._rxq._desc_bufs.get(desc_idx)
        if not buf:
            return None
        phys, cap = buf
        actual = min(length, cap)
        # Read frame from DMA buffer 4 bytes at a time (identity-mapped physical RAM)
        data = bytearray(actual)
        for i in range(0, actual, 4):
            word = mmio_read32(phys + i)
            chunk = min(4, actual - i)
            for j in range(chunk):
                data[i + j] = (word >> (j * 8)) & 0xFF
        # Resubmit the descriptor so the NIC can reuse the buffer
        self._rxq.avail_push(desc_idx)
        if self._regs:
            self._regs.set_queue_notify(0)
        return bytes(data)

    def handle_irq(self) -> None:
        """Legacy IRQ entry point — clears ISR; polling mode handles the data."""
        if self._regs:
            self._regs.isr_status   # read to clear

    @property
    def mac(self) -> bytes:
        return self._mac


# Module-level singleton
virtio_net: VirtIONetDriver | None = None
