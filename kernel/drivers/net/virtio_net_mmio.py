"""
kernel.drivers.net.virtio_net_mmio — VirtIO-net over MMIO.

QEMU ``virt`` on arm64 exposes ``virtio-net-device`` as a virtio-mmio
device (DeviceID=1). This driver presents the same small surface as the
x86 PCI virtio-net driver:

    mac: bytes
    send(frame) / send_nowait(frame)
    recv() / recv_nowait()

The synchronous methods let the GUI bridge pump TCP while existing
``bridge.call(...)`` users block waiting for host responses.
"""

import asyncio
import _hal
import kernel.log as log


VIRTIO_MMIO_BASE   = 0x0a000000
VIRTIO_MMIO_STRIDE = 0x200
VIRTIO_MMIO_DEVS   = 32
VIRTIO_MAGIC       = 0x74726976
VIRTIO_DEV_NET     = 1

STATUS_ACK         = 1
STATUS_DRIVER      = 2
STATUS_FEATURES    = 8
STATUS_DRIVER_OK   = 4

VRING_DESC_F_WRITE = 2

VIRTIO_NET_F_MAC   = 1 << 5

PAGE_SIZE  = 4096
QUEUE_SIZE = 64
BUF_SIZE   = 2048
NET_HDR_SIZE = 10

RXQ = 0
TXQ = 1


def _r32(base: int, off: int) -> int:
    return _hal.mmio_read32(base + off)


def _w32(base: int, off: int, v: int) -> None:
    _hal.mmio_write32(base + off, v)


def _w8(addr: int, v: int) -> None:
    _hal.mmio_write8(addr, v)


def _alloc_aligned(size: int, align: int = PAGE_SIZE) -> int:
    raw = _hal.dma_alloc(size + align)
    return (raw + align - 1) & ~(align - 1)


def _copy_to_dma(addr: int, data) -> None:
    for i, b in enumerate(data):
        _w8(addr + i, b)


def _copy_from_dma(addr: int, n: int) -> bytes:
    return bytes(_hal.mmio_read8(addr + i) for i in range(n))


class _VirtQueue:
    def __init__(self, dev_base: int, q_idx: int, version: int) -> None:
        self.dev_base = dev_base
        self.q_idx = q_idx
        self.version = version
        self.next_desc = 0
        self.avail_idx = 0
        self.last_used = 0

        desc_sz   = QUEUE_SIZE * 16
        avail_sz  = 4 + QUEUE_SIZE * 2 + 2
        avail_off = desc_sz
        used_off  = (avail_off + avail_sz + PAGE_SIZE - 1) & ~(PAGE_SIZE - 1)
        used_sz   = 4 + QUEUE_SIZE * 8 + 2
        total     = used_off + used_sz

        self.desc_phys  = _alloc_aligned(total + PAGE_SIZE)
        self.avail_phys = self.desc_phys + avail_off
        self.used_phys  = self.desc_phys + used_off

        _w32(dev_base, 0x030, q_idx)
        max_n = _r32(dev_base, 0x034) or QUEUE_SIZE
        n = min(QUEUE_SIZE, max_n)
        if n < QUEUE_SIZE:
            raise RuntimeError("virtio-net queue too small")
        _w32(dev_base, 0x038, QUEUE_SIZE)

        if version == 1:
            _w32(dev_base, 0x03C, PAGE_SIZE)
            _w32(dev_base, 0x040, self.desc_phys >> 12)
        else:
            _w32(dev_base, 0x080, self.desc_phys & 0xFFFFFFFF)
            _w32(dev_base, 0x084, (self.desc_phys >> 32) & 0xFFFFFFFF)
            _w32(dev_base, 0x090, self.avail_phys & 0xFFFFFFFF)
            _w32(dev_base, 0x094, (self.avail_phys >> 32) & 0xFFFFFFFF)
            _w32(dev_base, 0x0A0, self.used_phys & 0xFFFFFFFF)
            _w32(dev_base, 0x0A4, (self.used_phys >> 32) & 0xFFFFFFFF)
            _w32(dev_base, 0x044, 1)

    def write_desc(self, idx: int, addr: int, length: int,
                   flags: int = 0, nxt: int = 0) -> None:
        d = self.desc_phys + idx * 16
        _hal.mmio_write32(d + 0,  addr & 0xFFFFFFFF)
        _hal.mmio_write32(d + 4,  (addr >> 32) & 0xFFFFFFFF)
        _hal.mmio_write32(d + 8,  length)
        _hal.mmio_write32(d + 12, (flags & 0xFFFF) | ((nxt & 0xFFFF) << 16))

    def alloc_desc(self) -> int:
        idx = self.next_desc
        self.next_desc = (self.next_desc + 1) % QUEUE_SIZE
        return idx

    def avail_push(self, head: int) -> None:
        slot = self.avail_idx % QUEUE_SIZE
        ring = self.avail_phys + 4 + slot * 2
        _w8(ring,     head & 0xFF)
        _w8(ring + 1, (head >> 8) & 0xFF)
        self.avail_idx = (self.avail_idx + 1) & 0xFFFF
        idx_addr = self.avail_phys + 2
        _w8(idx_addr,     self.avail_idx & 0xFF)
        _w8(idx_addr + 1, (self.avail_idx >> 8) & 0xFF)

    def used_idx(self) -> int:
        return (_hal.mmio_read32(self.used_phys + 0) >> 16) & 0xFFFF

    def has_used(self) -> bool:
        return self.used_idx() != self.last_used

    def used_pop(self) -> tuple[int, int]:
        slot = self.last_used % QUEUE_SIZE
        ring = self.used_phys + 4 + slot * 8
        desc_id = _hal.mmio_read32(ring) & 0xFFFFFFFF
        length = _hal.mmio_read32(ring + 4) & 0xFFFFFFFF
        self.last_used = (self.last_used + 1) & 0xFFFF
        return desc_id, length

    def notify(self) -> None:
        _w32(self.dev_base, 0x050, self.q_idx)


def _net_header() -> bytes:
    return bytes(NET_HDR_SIZE)


class VirtioMmioNet:
    def __init__(self, base: int) -> None:
        self._base = base
        self._version = 0
        self._rxq: _VirtQueue | None = None
        self._txq: _VirtQueue | None = None
        self._rx_bufs: dict[int, int] = {}
        self._mac = bytes([0x02, 0x50, 0x59, 0x4f, 0x00, 0x01])

    def probe(self) -> bool:
        if _r32(self._base, 0x000) != VIRTIO_MAGIC:
            return False
        version = _r32(self._base, 0x004)
        if version not in (1, 2):
            return False
        if _r32(self._base, 0x008) != VIRTIO_DEV_NET:
            return False
        self._version = version

        _w32(self._base, 0x070, 0)
        _w32(self._base, 0x070, STATUS_ACK)
        _w32(self._base, 0x070, STATUS_ACK | STATUS_DRIVER)

        features0 = 0
        if version == 1:
            _w32(self._base, 0x028, PAGE_SIZE)
            features0 = _r32(self._base, 0x010)
            _w32(self._base, 0x020, features0 & VIRTIO_NET_F_MAC)
        else:
            _w32(self._base, 0x014, 0)
            features0 = _r32(self._base, 0x010)
            _w32(self._base, 0x024, 0)
            _w32(self._base, 0x020, features0 & VIRTIO_NET_F_MAC)
            _w32(self._base, 0x024, 1)
            _w32(self._base, 0x020, 0)

        _w32(self._base, 0x070, STATUS_ACK | STATUS_DRIVER | STATUS_FEATURES)

        if features0 & VIRTIO_NET_F_MAC:
            mac = bytes(_hal.mmio_read8(self._base + 0x100 + i)
                        for i in range(6))
            if any(mac):
                self._mac = mac

        self._rxq = _VirtQueue(self._base, RXQ, version)
        self._txq = _VirtQueue(self._base, TXQ, version)
        self._prime_rx()

        _w32(self._base, 0x070,
             STATUS_ACK | STATUS_DRIVER | STATUS_FEATURES | STATUS_DRIVER_OK)
        self._rxq.notify()
        log.info(f"virtio-net-mmio: ready at {self._base:#x}, MAC "
                 f"{':'.join(f'{b:02x}' for b in self._mac)}")
        return True

    def _prime_rx(self) -> None:
        for i in range(QUEUE_SIZE):
            buf = _hal.dma_alloc(BUF_SIZE)
            self._rx_bufs[i] = buf
            self._rxq.write_desc(i, buf, BUF_SIZE, VRING_DESC_F_WRITE, 0)
            self._rxq.avail_push(i)

    def send_nowait(self, frame: bytes) -> None:
        if not self._txq:
            return
        payload = _net_header() + bytes(frame)
        buf = _hal.dma_alloc(len(payload))
        _copy_to_dma(buf, payload)
        idx = self._txq.alloc_desc()
        self._txq.write_desc(idx, buf, len(payload), 0, 0)
        self._txq.avail_push(idx)
        self._txq.notify()

    async def send(self, frame: bytes) -> None:
        self.send_nowait(frame)

    def recv_nowait(self) -> bytes | None:
        if not self._rxq or not self._rxq.has_used():
            return None
        desc_id, length = self._rxq.used_pop()
        buf = self._rx_bufs.get(desc_id)
        if buf is None:
            return None
        actual = min(length, BUF_SIZE)
        data = _copy_from_dma(buf, actual)
        self._rxq.avail_push(desc_id)
        self._rxq.notify()
        if len(data) <= NET_HDR_SIZE:
            return None
        return data[NET_HDR_SIZE:]

    async def recv(self) -> bytes:
        while True:
            frame = self.recv_nowait()
            if frame is not None:
                return frame
            await asyncio.sleep(0)

    @property
    def mac(self) -> bytes:
        return self._mac


def find_virtio_net_mmio() -> VirtioMmioNet | None:
    for i in range(VIRTIO_MMIO_DEVS):
        base = VIRTIO_MMIO_BASE + i * VIRTIO_MMIO_STRIDE
        dev = VirtioMmioNet(base)
        if dev.probe():
            return dev
    return None
