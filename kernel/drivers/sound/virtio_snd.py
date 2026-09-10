"""
kernel.drivers.sound.virtio_snd — VirtIO Sound (DeviceID=25) over MMIO.

Exposes a write_pcm(bytes) -> int contract matching the existing HDA
backend so :class:`kernel.sound.mixer.Mixer` can attach uniformly. The
driver supports a single output stream (id 0) at 48 kHz, 2-channel,
S16 LE PCM — the same format the Mixer normalises to.

Protocol surface implemented:
    * Control queue: VIRTIO_SND_R_PCM_SET_PARAMS, _PREPARE, _START
    * TX queue: virtio_snd_pcm_xfer { stream_id } + PCM bytes + status

Spec reference: virtio v1.2 §5.14 (Sound Device).

Limitations:
    * No event-queue handler (we don't react to async device events)
    * No RX (input) path
    * One fixed 48 kHz stereo stream; a five-period DMA pool supports
      continuous playback without allocating in the hot path.
"""

import _hal
import kernel.log as log


VIRTIO_MMIO_BASE   = 0x0a000000
VIRTIO_MMIO_STRIDE = 0x200
VIRTIO_MMIO_DEVS   = 32
VIRTIO_MAGIC       = 0x74726976
VIRTIO_DEV_SOUND   = 25

STATUS_ACK         = 1
STATUS_DRIVER      = 2
STATUS_FEATURES    = 8
STATUS_DRIVER_OK   = 4

VRING_DESC_F_NEXT  = 1
VRING_DESC_F_WRITE = 2

PAGE_SIZE  = 4096
QUEUE_SIZE = 16
TX_PERIOD_BYTES = 48000                # 250 ms of 48 kHz stereo int16
TX_PERIODS = QUEUE_SIZE // 3           # three descriptors per transfer

# Control / event command codes
VIRTIO_SND_R_PCM_INFO        = 0x0100
VIRTIO_SND_R_PCM_SET_PARAMS  = 0x0101
VIRTIO_SND_R_PCM_PREPARE     = 0x0102
VIRTIO_SND_R_PCM_RELEASE     = 0x0103
VIRTIO_SND_R_PCM_START       = 0x0104
VIRTIO_SND_R_PCM_STOP        = 0x0105
VIRTIO_SND_S_OK              = 0x8000

# PCM format / rate enums (virtio v1.2 §5.14.6.6.4)
VIRTIO_SND_PCM_FMT_S16    = 5
# rates: 5512=0, 8000=1, 11025=2, 16000=3, 22050=4, 32000=5, 44100=6, 48000=7
VIRTIO_SND_PCM_RATE_48000 = 7

# Queue indices
CONTROLQ = 0
EVENTQ   = 1
TXQ      = 2
RXQ      = 3

# Mixer-facing constants (must match Mixer.native_*)
RATE     = 48000
CHANNELS = 2
BIT_DEPTH = 16


def _r32(base, off): return _hal.mmio_read32(base + off)
def _w32(base, off, v): _hal.mmio_write32(base + off, v)
def _w8 (addr, v): _hal.mmio_write8(addr, v)


def _alloc_aligned(size: int, align: int = PAGE_SIZE) -> int:
    raw = _hal.dma_alloc(size + align)
    return (raw + align - 1) & ~(align - 1)


class _VirtQueue:
    """Minimal split virtqueue helper — descriptor table + avail + used.

    Each TX/control transfer chains 2-3 descriptors so we keep enough
    slack with QUEUE_SIZE = 16."""

    def __init__(self, base: int) -> None:
        self.base = base
        desc_sz   = QUEUE_SIZE * 16
        avail_sz  = 4 + QUEUE_SIZE * 2 + 2
        avail_off = desc_sz
        used_off  = (avail_off + avail_sz + PAGE_SIZE - 1) & ~(PAGE_SIZE - 1)
        used_sz   = 4 + QUEUE_SIZE * 8 + 2
        total     = used_off + used_sz
        self.size = total

        self.desc_phys  = _alloc_aligned(total + PAGE_SIZE)
        self.avail_phys = self.desc_phys + avail_off
        self.used_phys  = self.desc_phys + used_off
        self.next_desc  = 0
        self.avail_idx  = 0
        self.last_used  = 0

    def write_desc(self, idx, addr, length, flags, nxt) -> None:
        d = self.desc_phys + idx * 16
        _hal.mmio_write32(d + 0,  addr & 0xFFFFFFFF)
        _hal.mmio_write32(d + 4,  (addr >> 32) & 0xFFFFFFFF)
        _hal.mmio_write32(d + 8,  length)
        _hal.mmio_write32(d + 12, (flags & 0xFFFF) | ((nxt & 0xFFFF) << 16))

    def avail_push(self, head) -> None:
        slot = self.avail_idx % QUEUE_SIZE
        ring = self.avail_phys + 4 + slot * 2
        _w8(ring,     head & 0xFF)
        _w8(ring + 1, (head >> 8) & 0xFF)
        self.avail_idx = (self.avail_idx + 1) & 0xFFFF
        idx_addr = self.avail_phys + 2
        _w8(idx_addr,     self.avail_idx & 0xFF)
        _w8(idx_addr + 1, (self.avail_idx >> 8) & 0xFF)

    def used_idx(self) -> int:
        return _hal.mmio_read32(self.used_phys + 0) >> 16


def _put_le32(buf_phys: int, off: int, v: int) -> None:
    _hal.mmio_write8(buf_phys + off,     v        & 0xFF)
    _hal.mmio_write8(buf_phys + off + 1, (v >> 8)  & 0xFF)
    _hal.mmio_write8(buf_phys + off + 2, (v >> 16) & 0xFF)
    _hal.mmio_write8(buf_phys + off + 3, (v >> 24) & 0xFF)


def _get_le32(buf_phys: int, off: int) -> int:
    return ((_hal.mmio_read8(buf_phys + off + 0))      |
            (_hal.mmio_read8(buf_phys + off + 1) <<  8) |
            (_hal.mmio_read8(buf_phys + off + 2) << 16) |
            (_hal.mmio_read8(buf_phys + off + 3) << 24))


class VirtioMmioSound:
    """Single virtio-snd device with stream 0 set up for output."""

    def __init__(self, base: int) -> None:
        self._base = base
        self._version = 0
        self._cq: _VirtQueue | None = None
        self._txq: _VirtQueue | None = None
        # Reusable header / status buffers for control commands
        self._ctrl_req  = 0
        self._ctrl_resp = 0
        # A bounded pool prevents a continuous stream from consuming fresh
        # DMA memory forever. Longer periods tolerate normal-GIL service jitter
        # caused by synchronous remote-display transactions.
        self._tx_headers: list[int] = []
        self._tx_statuses: list[int] = []
        self._tx_buffers: list[int] = []
        self._tx_free: list[int] = []
        self._tx_inflight: dict[int, int] = {}
        self._bytes_consumed = 0
        self._tx_submitted = 0
        self._tx_reclaimed = 0
        self._tx_backpressure = 0
        self._tx_high_water = 0

    # ── Probe + queue setup ─────────────────────────────────────────────

    def probe(self) -> bool:
        if _r32(self._base, 0x000) != VIRTIO_MAGIC:
            return False
        version = _r32(self._base, 0x004)
        if version not in (1, 2):
            return False
        if _r32(self._base, 0x008) != VIRTIO_DEV_SOUND:
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

        # Set up controlq (0) and txq (2). Eventq + rxq are not used.
        self._cq  = self._setup_queue(CONTROLQ)
        self._txq = self._setup_queue(TXQ)

        # Pre-allocate small reusable buffers.
        self._ctrl_req  = _hal.dma_alloc(64)
        self._ctrl_resp = _hal.dma_alloc(64)
        self._tx_headers = [_hal.dma_alloc(4) for _ in range(TX_PERIODS)]
        self._tx_statuses = [_hal.dma_alloc(8) for _ in range(TX_PERIODS)]
        self._tx_buffers = [
            _hal.dma_alloc(TX_PERIOD_BYTES) for _ in range(TX_PERIODS)]
        self._tx_free = list(range(TX_PERIODS))

        _w32(self._base, 0x070,
             STATUS_ACK | STATUS_DRIVER | STATUS_FEATURES | STATUS_DRIVER_OK)

        if not self._configure_stream():
            log.info("virtio-snd: stream configuration failed")
            return False

        log.info(f"virtio-snd: ready at {self._base:#x} (stream 0, "
                 f"{RATE} Hz / {CHANNELS}ch / S16)")
        return True

    def _setup_queue(self, q_idx: int) -> _VirtQueue:
        _w32(self._base, 0x030, q_idx)
        _w32(self._base, 0x038, QUEUE_SIZE)
        q = _VirtQueue(self._base)
        if self._version == 1:
            _w32(self._base, 0x03C, PAGE_SIZE)
            _w32(self._base, 0x040, q.desc_phys >> 12)
        else:
            _w32(self._base, 0x080, q.desc_phys             & 0xFFFFFFFF)
            _w32(self._base, 0x084, (q.desc_phys >> 32)     & 0xFFFFFFFF)
            _w32(self._base, 0x090, q.avail_phys            & 0xFFFFFFFF)
            _w32(self._base, 0x094, (q.avail_phys >> 32)    & 0xFFFFFFFF)
            _w32(self._base, 0x0A0, q.used_phys             & 0xFFFFFFFF)
            _w32(self._base, 0x0A4, (q.used_phys >> 32)     & 0xFFFFFFFF)
            _w32(self._base, 0x044, 1)
        return q

    # ── Control plane ───────────────────────────────────────────────────

    def _ctrl_xfer(self, req_len: int, resp_len: int = 4) -> int:
        """Submit a control transfer; return status code (or -1 on timeout)."""
        cq = self._cq
        d0 = cq.next_desc; cq.next_desc = (cq.next_desc + 1) % QUEUE_SIZE
        d1 = cq.next_desc; cq.next_desc = (cq.next_desc + 1) % QUEUE_SIZE
        cq.write_desc(d0, self._ctrl_req,  req_len,  VRING_DESC_F_NEXT, d1)
        cq.write_desc(d1, self._ctrl_resp, resp_len, VRING_DESC_F_WRITE, 0)
        cq.avail_push(d0)
        _w32(self._base, 0x050, CONTROLQ)
        target = (cq.last_used + 1) & 0xFFFF
        for _ in range(1_000_000):
            if cq.used_idx() == target:
                cq.last_used = (cq.last_used + 1) & 0xFFFF
                return _get_le32(self._ctrl_resp, 0)
        return -1

    def _configure_stream(self) -> bool:
        # PCM_SET_PARAMS request body — virtio_snd_pcm_set_params:
        # struct virtio_snd_pcm_hdr { u32 code; u32 stream_id; }
        # u32 buffer_bytes; u32 period_bytes; u32 features;
        # u8  channels; u8  format; u8  rate; u8  padding;
        # Advertise the same capacity and period size that write_pcm actually
        # submits. A mismatch here makes QEMU consume with the wrong cadence.
        buf_bytes = TX_PERIOD_BYTES * TX_PERIODS
        period_bytes = TX_PERIOD_BYTES
        for off in range(28):
            _hal.mmio_write8(self._ctrl_req + off, 0)
        _put_le32(self._ctrl_req,  0, VIRTIO_SND_R_PCM_SET_PARAMS)
        _put_le32(self._ctrl_req,  4, 0)                              # stream_id
        _put_le32(self._ctrl_req,  8, buf_bytes)
        _put_le32(self._ctrl_req, 12, period_bytes)
        _put_le32(self._ctrl_req, 16, 0)                              # features
        _hal.mmio_write8(self._ctrl_req + 20, CHANNELS)
        _hal.mmio_write8(self._ctrl_req + 21, VIRTIO_SND_PCM_FMT_S16)
        _hal.mmio_write8(self._ctrl_req + 22, VIRTIO_SND_PCM_RATE_48000)
        _hal.mmio_write8(self._ctrl_req + 23, 0)
        st = self._ctrl_xfer(28, 4)
        if st != VIRTIO_SND_S_OK:
            log.info(f"virtio-snd: SET_PARAMS status={st:#x}")
            return False

        # PCM_PREPARE — just a virtio_snd_pcm_hdr.
        _put_le32(self._ctrl_req, 0, VIRTIO_SND_R_PCM_PREPARE)
        _put_le32(self._ctrl_req, 4, 0)
        st = self._ctrl_xfer(8, 4)
        if st != VIRTIO_SND_S_OK:
            log.info(f"virtio-snd: PREPARE status={st:#x}")
            return False

        # PCM_START
        _put_le32(self._ctrl_req, 0, VIRTIO_SND_R_PCM_START)
        _put_le32(self._ctrl_req, 4, 0)
        st = self._ctrl_xfer(8, 4)
        if st != VIRTIO_SND_S_OK:
            log.info(f"virtio-snd: START status={st:#x}")
            return False
        return True

    # ── Mixer-facing API ────────────────────────────────────────────────

    def write_pcm(self, pcm: bytes) -> int:
        """Submit ``pcm`` to the TX queue. Returns bytes consumed (or 0
        on backend not-ready)."""
        if not self._txq:
            return 0
        # Reclaim exactly the descriptor heads returned through the used ring;
        # this remains correct even if a transport completes out of order.
        txq = self._txq
        used = txq.used_idx()
        while txq.last_used != used:
            elem = txq.used_phys + 4 + (txq.last_used % QUEUE_SIZE) * 8
            head = _hal.mmio_read32(elem)
            slot = self._tx_inflight.pop(head, None)
            if slot is not None:
                self._tx_free.append(slot)
                self._tx_reclaimed += 1
            txq.last_used = (txq.last_used + 1) & 0xFFFF

        n = min(len(pcm), TX_PERIOD_BYTES)
        if n == 0:
            return 0
        if not self._tx_free:
            self._tx_backpressure += 1
            return 0

        slot = self._tx_free.pop()
        tx_buf = self._tx_buffers[slot]
        for i, b in enumerate(pcm):
            if i >= n:
                break
            _hal.mmio_write8(tx_buf + i, b)

        # Header: virtio_snd_pcm_xfer { u32 stream_id; }
        tx_hdr = self._tx_headers[slot]
        tx_status = self._tx_statuses[slot]
        _put_le32(tx_hdr, 0, 0)

        # Each pool slot owns a permanent three-descriptor chain.
        d0, d1, d2 = slot * 3, slot * 3 + 1, slot * 3 + 2

        txq.write_desc(d0, tx_hdr,          4, VRING_DESC_F_NEXT, d1)
        txq.write_desc(d1, tx_buf,          n, VRING_DESC_F_NEXT, d2)
        txq.write_desc(d2, tx_status,        8, VRING_DESC_F_WRITE, 0)
        self._tx_inflight[d0] = slot
        self._tx_submitted += 1
        self._tx_high_water = max(self._tx_high_water,
                                  len(self._tx_inflight))
        txq.avail_push(d0)
        _w32(self._base, 0x050, TXQ)

        self._bytes_consumed += n
        return n

    def audio_metrics(self, reset: bool = False) -> dict:
        """Return bounded-queue diagnostics used by Top and the debugger."""
        values = {
            "period_bytes": TX_PERIOD_BYTES,
            "periods": TX_PERIODS,
            "submitted": self._tx_submitted,
            "reclaimed": self._tx_reclaimed,
            "backpressure": self._tx_backpressure,
            "inflight": len(self._tx_inflight),
            "free": len(self._tx_free),
            "high_water": self._tx_high_water,
            "bytes": self._bytes_consumed,
        }
        if reset:
            self._tx_submitted = 0
            self._tx_reclaimed = 0
            self._tx_backpressure = 0
            self._tx_high_water = len(self._tx_inflight)
        return values


# ── Public discovery / install ─────────────────────────────────────────

def find_virtio_snd() -> "VirtioMmioSound | None":
    for i in range(VIRTIO_MMIO_DEVS):
        base = VIRTIO_MMIO_BASE + i * VIRTIO_MMIO_STRIDE
        if _r32(base, 0x000) != VIRTIO_MAGIC:
            continue
        if _r32(base, 0x008) != VIRTIO_DEV_SOUND:
            continue
        dev = VirtioMmioSound(base)
        if dev.probe():
            return dev
    return None
