"""Async wrapper around _hal's non-blocking linenoise editing.

linenoise's blocking C API is not friendly to PythonOS's asyncio-based
shell I/O — it expects a synchronous read(0) loop. The non-blocking
surface (linenoiseEditStart/Feed/Stop, exposed via _hal as
linenoise_edit_start / linenoise_edit_feed_byte / linenoise_edit_stop)
hands control back to us after each byte, which is exactly what an
async read_char loop wants.

Use linenoise_edit() from a coroutine that has read_char and write
callables (the same pair the shell already wires through). Returns
the completed line as a string, or None if the user cancelled
(Ctrl-C / EOF).
"""

import _hal


async def linenoise_edit(prompt, read_char, write, complete=None):
    """Drive a non-blocking linenoise edit from a coroutine.

    `prompt` is the prompt to display.
    `read_char` is an awaitable returning one character (or one byte).
    `write` is a callable that accepts a str and emits it to the
    transport (terminal, TCP socket, ...). `complete`, when supplied,
    is a synchronous callable accepting the current line and returning
    full-line completion candidates. The wrapper installs a bytes-
    oriented bridge so linenoise's VT100 escapes flow through.
    """
    def _write_bytes(buf):
        # _hal hands us bytes. Raw sinks accept bytes; older sinks may
        # still expect str. Do not let callback exceptions escape into
        # the C hook, because that hook deliberately falls back to serial
        # when Python reports an unconsumed write.
        try:
            write(buf)
            return
        except Exception:
            pass
        try:
            write(buf.decode("utf-8", errors="replace"))
        except Exception:
            pass

    slot = _hal.linenoise_edit_start(prompt, _write_bytes, complete)
    try:
        while True:
            ch = await read_char()
            if isinstance(ch, str):
                if not ch:
                    raise EOFError
                b = ord(ch[0])
            elif isinstance(ch, (bytes, bytearray)):
                if not ch:
                    raise EOFError
                b = ch[0]
            elif isinstance(ch, int):
                b = ch
            else:
                raise TypeError("read_char must return str/bytes/int")
            # Treat LF as CR so callers feeding a stream that uses '\n'
            # for Enter (telnet-style line discipline, asyncio TCP test
            # clients, host smoke runners) drive linenoise correctly.
            # Real terminals always send '\r' for Enter, which is
            # already linenoise's KEY_ENTER.
            if b == 0x0a:
                b = 0x0d
            try:
                line = _hal.linenoise_edit_feed_byte(slot, b)
            except EOFError:
                return None
            if line is not None:
                return line
    finally:
        _hal.linenoise_edit_stop(slot)
