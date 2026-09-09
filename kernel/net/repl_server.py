"""
kernel.net.repl_server — Multi-session TCP kernel REPL server.

Listens on TCP port 5000.  Each connection spawns an independent asyncio task
running a full kernel Shell with access to all live kernel objects.  Multiple
sessions can exist simultaneously — this demonstrates that Python IS a
multitasking kernel, not just a single serial console.

Connect from the QEMU host after `make run` or `make run-arm64`:
    nc localhost 5555     (x86_64 — QEMU forwards host:5555 → guest:5000)
    nc localhost 5556     (arm64  — QEMU forwards host:5556 → guest:5000)

Each session has its own namespace snapshot but shares live kernel objects
(pci, vfs, scheduler, net, etc.).  Changes made in one session are visible
in all others.
"""

import asyncio
import kernel.log as log
from kernel.shell import Shell
from kernel.scheduler import scheduler

_PORT = 5000


async def start(port: int = _PORT) -> None:
    from kernel.net.tcp import tcp
    listener = await tcp.listen(port)
    log.info(f"repl: TCP REPL listening on port {port} — connect: nc localhost 5555")
    while True:
        conn = await listener.accept()
        log.info(f"repl: new session from remote port {conn.remote_port}")
        scheduler.spawn(_session(conn), name=f"repl:{conn.remote_port}")


async def _session(conn) -> None:
    rx_buf = bytearray()

    async def _refill() -> None:
        while not rx_buf:
            data = await conn.recv(64)
            if not data:
                raise EOFError("connection closed")
            rx_buf.extend(data)

    async def read_byte() -> int:
        """Raw byte stream; linenoise needs CR (\\r) untranslated."""
        await _refill()
        return rx_buf.pop(0)

    async def read_char() -> str:
        """Legacy char stream with CR→LF translation, used as a fallback
        when linenoise editing is not active."""
        b = await read_byte()
        ch = chr(b & 0x7F)
        return '\n' if ch == '\r' else ch

    def write(text: str) -> None:
        """\\n → \\r\\n translation for terminal compatibility."""
        out = text.replace('\n', '\r\n').encode('utf-8', errors='replace')
        asyncio.ensure_future(conn.send(out))

    def write_raw(buf) -> None:
        """Raw byte/string sink — used by linenoise, which emits its
        own VT100 escapes including \\r and \\n."""
        if isinstance(buf, str):
            buf = buf.encode('utf-8', errors='replace')
        asyncio.ensure_future(conn.send(bytes(buf)))

    # Hook this session into the in-kernel chat bus so any connected
    # nc client can broadcast to the others. The chat object is the
    # session-specific adapter — it knows its own handle so the
    # user doesn't have to.
    from kernel.net.chatbus import bus as _chat_bus, _SessionChat
    chat_handle = _chat_bus.register(write_raw, nick=f"port{conn.remote_port}")
    chat_obj = _SessionChat(chat_handle)

    try:
        shell = Shell(read_char=read_char, write=write,
                      read_byte=read_byte, write_raw=write_raw,
                      can_exit=True)
        shell._ns["chat"] = chat_obj
        await shell.run()
    except EOFError:
        pass
    except Exception:
        import traceback
        log.error(f"repl session: {traceback.format_exc()}")
    finally:
        _chat_bus.unregister(chat_handle)
        conn.close()
        from kernel.net.tcp import tcp
        tcp.remove_connection(conn)
        log.info(f"repl: session on port {conn.remote_port} closed")
