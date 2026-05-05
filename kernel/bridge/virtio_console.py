"""
kernel.bridge.virtio_console - synchronous byte stream over VirtIO console.

QEMU arm64 ``virt`` exposes only one PL011 UART, already used by the
kernel shell. In bridge mode run_gui adds a ``virtio-serial-device`` with
one ``virtconsole`` port; this driver binds the resulting VirtIO-MMIO
console device (DeviceID 3) and exposes the same blocking read/write
surface that kernel.bridge.uart used for the old second-UART path.
"""

import _hal
import kernel.log as log


VIRTIO_MMIO_BASE   = 0x0a000000
VIRTIO_MMIO_STRIDE = 0x200
VIRTIO_MMIO_DEVS   = 32
VIRTIO_MAGIC       = 0x74726976
VIRTIO_DEV_CONSOLE = 3

STATUS_ACK         = 1
STATUS_DRIVER      = 2
STATUS_FEATURES    = 8
STATUS_DRIVER_OK   = 4

VRING_DESC_F_WRITE = 2

PAGE_SIZE  = 4096
QUEUE_SIZE = 16
BUF_SIZE   = 4096

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

        _w32(dev_base, 0x030, q_idx)                 # QueueSel
        max_n = _r32(dev_base, 0x034) or QUEUE_SIZE  # QueueNumMax
        n = min(QUEUE_SIZE, max_n)
        if n < QUEUE_SIZE:
            raise RuntimeError("virtio-console queue too small")
        _w32(dev_base, 0x038, QUEUE_SIZE)            # QueueNum

        if version == 1:
            _w32(dev_base, 0x03C, PAGE_SIZE)         # QueueAlign
            _w32(dev_base, 0x040, self.desc_phys >> 12)
        else:
            _w32(dev_base, 0x080, self.desc_phys & 0xFFFFFFFF)
            _w32(dev_base, 0x084, (self.desc_phys >> 32) & 0xFFFFFFFF)
            _w32(dev_base, 0x090, self.avail_phys & 0xFFFFFFFF)
            _w32(dev_base, 0x094, (self.avail_phys >> 32) & 0xFFFFFFFF)
            _w32(dev_base, 0x0A0, self.used_phys & 0xFFFFFFFF)
            _w32(dev_base, 0x0A4, (self.used_phys >> 32) & 0xFFFFFFFF)
            _w32(dev_base, 0x044, 1)                 # QueueReady

    def write_desc(self, idx: int, addr: int, length: int,
                   flags: int = 0, nxt: int = 0) -> None:
        d = self.desc_phys + idx * 16
        _hal.mmio_write32(d + 0,  addr & 0xFFFFFFFF)
        _hal.mmio_write32(d + 4,  (addr >> 32) & 0xFFFFFFFF)
        _hal.mmio_write32(d + 8,  length)
        _hal.mmio_write32(d + 12, (flags & 0xFFFF) | ((nxt & 0xFFFF) << 16))

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

    def used_pop(self) -> tuple[int, int]:
        slot = self.last_used % QUEUE_SIZE
        ring = self.used_phys + 4 + slot * 8
        desc_id = _hal.mmio_read32(ring) & 0xFFFFFFFF
        length = _hal.mmio_read32(ring + 4) & 0xFFFFFFFF
        self.last_used = (self.last_used + 1) & 0xFFFF
        return desc_id, length

    def notify(self) -> None:
        _w32(self.dev_base, 0x050, self.q_idx)


class VirtioConsole:
    def __init__(self, base: int) -> None:
        self._base = base
        self._version = 0
        self._rxq: _VirtQueue | None = None
        self._txq: _VirtQueue | None = None
        self._rx_bufs: list[int] = []
        self._rx_pending = bytearray()

    def probe(self) -> bool:
        if _r32(self._base, 0x000) != VIRTIO_MAGIC:
            return False
        version = _r32(self._base, 0x004)
        if version not in (1, 2):
            return False
        if _r32(self._base, 0x008) != VIRTIO_DEV_CONSOLE:
            return False
        self._version = version

        _w32(self._base, 0x070, 0)
        _w32(self._base, 0x070, STATUS_ACK)
        _w32(self._base, 0x070, STATUS_ACK | STATUS_DRIVER)

        if version == 1:
            _w32(self._base, 0x028, PAGE_SIZE)
            _w32(self._base, 0x020, 0)
        else:
            _w32(self._base, 0x024, 0); _w32(self._base, 0x020, 0)
            _w32(self._base, 0x024, 1); _w32(self._base, 0x020, 0)

        _w32(self._base, 0x070, STATUS_ACK | STATUS_DRIVER | STATUS_FEATURES)

        self._rxq = _VirtQueue(self._base, RXQ, version)
        self._txq = _VirtQueue(self._base, TXQ, version)
        self._prime_rx()

        _w32(self._base, 0x070,
             STATUS_ACK | STATUS_DRIVER | STATUS_FEATURES | STATUS_DRIVER_OK)
        self._rxq.notify()
        log.info(f"virtio-console: ready at {self._base:#x}")
        return True

    def _prime_rx(self) -> None:
        for i in range(QUEUE_SIZE):
            buf = _hal.dma_alloc(BUF_SIZE)
            self._rx_bufs.append(buf)
            self._rxq.write_desc(i, buf, BUF_SIZE, VRING_DESC_F_WRITE, 0)
            self._rxq.avail_push(i)

    def _poll_rx_once(self, timeout_ms: int | None = None) -> bool:
        from kernel.scheduler import scheduler
        deadline = scheduler.uptime_ms + max(0, int(timeout_ms or 0))
        while self._rxq.used_idx() == self._rxq.last_used:
            if timeout_ms is not None and scheduler.uptime_ms >= deadline:
                return False
        desc_id, length = self._rxq.used_pop()
        if 0 <= desc_id < len(self._rx_bufs) and length:
            self._rx_pending.extend(_copy_from_dma(self._rx_bufs[desc_id], length))
            self._rxq.avail_push(desc_id)
            self._rxq.notify()
        return True

    def read_bytes(self, n: int, timeout_ms: int | None = None) -> bytes | None:
        while len(self._rx_pending) < n:
            if not self._poll_rx_once(timeout_ms):
                return None
        out = bytes(self._rx_pending[:n])
        del self._rx_pending[:n]
        return out

    def write_bytes(self, data) -> None:
        if not data:
            return
        if not isinstance(data, (bytes, bytearray)):
            data = bytes(data)
        idx = self._txq.next_desc
        self._txq.next_desc = (self._txq.next_desc + 1) % QUEUE_SIZE
        buf = _hal.dma_alloc(len(data))
        _copy_to_dma(buf, data)
        self._txq.write_desc(idx, buf, len(data), 0, 0)
        self._txq.avail_push(idx)
        self._txq.notify()
        target = (self._txq.last_used + 1) & 0xFFFF
        while self._txq.used_idx() != target:
            pass
        self._txq.used_pop()


def find_virtio_console() -> VirtioConsole | None:
    for i in range(VIRTIO_MMIO_DEVS):
        base = VIRTIO_MMIO_BASE + i * VIRTIO_MMIO_STRIDE
        dev = VirtioConsole(base)
        if dev.probe():
            return dev
    return None


_console: VirtioConsole | None = None


def console() -> VirtioConsole:
    global _console
    if _console is None:
        _console = find_virtio_console()
    if _console is None:
        raise RuntimeError("virtio-console bridge device not found")
    return _console
