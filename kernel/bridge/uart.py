"""
kernel.bridge.uart — byte-stream transport for pythonos_bridge.

PythonOS already uses one UART for the kernel REPL (PL011 #0 on arm64,
COM1 on x86). On x86 the bridge runs on COM2. QEMU arm64 ``virt`` only
exposes one PL011, so arm64 bridge mode uses a VirtIO console byte stream:

  arm64 (QEMU virt):  virtio-console-device over MMIO
  x86_64 (q35):       16550 COM2 at I/O port 0x2F8

QEMU x86 is launched with:
  -chardev socket,id=br,host=127.0.0.1,port=17010,reconnect=2
  -serial chardev:br
QEMU arm64 wires the same chardev through virtio-serial/virtconsole.
"""

import _hal


_ARCH = getattr(_hal, "ARCH", "x86_64")


# ── x86_64 16550 COM2 ──────────────────────────────────────────────────────

_COM2_BASE = 0x2F8
_COM2_DATA = _COM2_BASE                # RBR/THR
_COM2_LSR  = _COM2_BASE + 5            # line status: bit 0 = DR, bit 5 = THRE

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

def _tcp_transport():
    try:
        from kernel.bridge import tcp_transport
        if tcp_transport.is_active():
            return tcp_transport
    except Exception:
        pass
    return None


if _ARCH == "arm64":
    def write_bytes(buf) -> None:
        tcp = _tcp_transport()
        if tcp is not None:
            return tcp.write_bytes(buf)
        from kernel.bridge import virtio_console
        virtio_console.console().write_bytes(buf)

    def read_bytes(n: int) -> bytes:
        tcp = _tcp_transport()
        if tcp is not None:
            return tcp.read_bytes(n)
        from kernel.bridge import virtio_console
        return virtio_console.console().read_bytes(n)

    def read_bytes_timeout(n: int, timeout_ms: int = 2000) -> bytes | None:
        tcp = _tcp_transport()
        if tcp is not None:
            return tcp.read_bytes(n, timeout_ms=timeout_ms)
        from kernel.bridge import virtio_console
        return virtio_console.console().read_bytes(n, timeout_ms=timeout_ms)
else:
    def write_bytes(buf) -> None:
        """Bulk write through COM2 in C."""
        tcp = _tcp_transport()
        if tcp is not None:
            return tcp.write_bytes(buf)
        _hal.uart16550_write_buf(_COM2_BASE, buf)

    def read_bytes(n: int) -> bytes:
        tcp = _tcp_transport()
        if tcp is not None:
            return tcp.read_bytes(n)
        return _hal.uart16550_read_buf(_COM2_BASE, n)

    def read_bytes_timeout(n: int, timeout_ms: int = 2000) -> bytes | None:
        """Read up to ``n`` bytes without waiting forever.

        The fast C bulk reader intentionally blocks. This bytewise path is only
        used for the initial bridge probe, where an absent host peer should
        report unavailable instead of wedging the kernel.
        """
        tcp = _tcp_transport()
        if tcp is not None:
            return tcp.read_bytes(n, timeout_ms=timeout_ms)
        from kernel.scheduler import scheduler
        deadline = scheduler.uptime_ms + max(0, int(timeout_ms))
        out = bytearray()
        while len(out) < n:
            b = _com2_try_read_byte()
            if b >= 0:
                out.append(b)
                continue
            if scheduler.uptime_ms >= deadline:
                return None
        return bytes(out)
