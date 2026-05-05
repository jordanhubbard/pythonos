"""
kernel.bridge.tcp_transport — native TCP byte stream for pythonos_bridge.

PythonOS GUI bridge calls are currently synchronous. This transport accepts
a host-side ``pythonos_bridge --connect-tcp`` connection through the in-kernel
TCP stack, then pumps the NIC synchronously while bridge callers wait for
responses. That keeps the existing ``bridge.call(...)`` API intact while
removing the QEMU chardev/virtconsole dependency from native GUI mode.
"""

import asyncio

import kernel.log as log


DEFAULT_PORT = 5001

_conn = None


def is_active() -> bool:
    return _conn is not None


def attach(conn) -> None:
    global _conn
    _conn = conn
    log.info(f"bridge-tcp: host connected from port {conn.remote_port}")


def detach(conn=None) -> None:
    global _conn
    if conn is None or conn is _conn:
        _conn = None


def write_bytes(buf) -> None:
    if _conn is None:
        raise RuntimeError("bridge TCP transport is not connected")
    if not isinstance(buf, (bytes, bytearray)):
        buf = bytes(buf)
    if not buf:
        return
    if not _conn.send_nowait(bytes(buf)):
        from kernel.net import stack
        # A freshly accepted connection should already have the peer MAC
        # learned from the SYN, but give ARP a short chance to catch up.
        for _ in range(10000):
            stack.poll_once()
            if _conn.send_nowait(bytes(buf)):
                return
        raise RuntimeError("bridge TCP transport could not transmit")


def read_bytes(n: int, timeout_ms: int | None = None) -> bytes | None:
    if _conn is None:
        raise RuntimeError("bridge TCP transport is not connected")
    from kernel.net import stack
    from kernel.scheduler import scheduler
    deadline = None
    if timeout_ms is not None:
        deadline = scheduler.uptime_ms + max(0, int(timeout_ms))

    while len(_conn.rcv_buf) < n:
        stack.poll_once()
        if _conn.state.name in ("CLOSE_WAIT", "CLOSED") and not _conn.rcv_buf:
            return None
        if deadline is not None and scheduler.uptime_ms >= deadline:
            return None
    out = bytes(_conn.rcv_buf[:n])
    del _conn.rcv_buf[:n]
    return out


async def start_listener(port: int = DEFAULT_PORT,
                         app_name: str | None = None,
                         auto_desktop: bool = True) -> None:
    from kernel.net.tcp import tcp
    listener = await tcp.listen(port)
    log.info(f"bridge-tcp: listening on port {port}")
    while True:
        conn = await listener.accept()
        attach(conn)
        if auto_desktop:
            try:
                from kernel.bridge import py_desktop
                desktop = py_desktop(app_name or None)
            except Exception as e:
                log.warn(f"bridge-tcp: desktop start failed ({e})")
                desktop = None
            if desktop is None:
                log.warn("bridge-tcp: desktop requested but bridge is unavailable")
            else:
                log.info("bridge-tcp: desktop auto-started")
        # Keep this task alive until the connection closes. Bridge reads are
        # performed synchronously by bridge.call(); this loop only notices
        # disconnects between calls.
        while conn is _conn and conn.state.name not in ("CLOSE_WAIT", "CLOSED"):
            await asyncio.sleep(0.25)
        detach(conn)
        try:
            tcp.remove_connection(conn)
        except Exception:
            pass
        log.info("bridge-tcp: host disconnected")
