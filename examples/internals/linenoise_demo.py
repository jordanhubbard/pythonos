"""Internal coverage fixture for _hal's non-blocking linenoise editor.

The smoke test (tests/smoke_test.py) runs this and checks that the
edited line, computed entirely in-kernel from a scripted byte sequence
(no real tty), matches what linenoise should have produced for the
input "hello\\b world\\r" (i.e. 'h', 'e', 'l', 'l', 'o', backspace,
space, 'w', 'o', 'r', 'l', 'd', Enter).

The asyncio-aware wrapper kernel/linenoise.py is exercised via
linenoise_edit_byte_stream() below.
"""

import asyncio
import _hal
from kernel import linenoise as ln_async


def _write_capture(captured):
    def writer(text):
        captured.append(text)
    return writer


async def _feed_bytes(byte_seq):
    pos = [0]

    async def read_char():
        if pos[0] >= len(byte_seq):
            return ""
        b = byte_seq[pos[0]]
        pos[0] += 1
        return chr(b)

    captured = []
    line = await ln_async.linenoise_edit(":> ", read_char,
                                          _write_capture(captured))
    return line, captured


async def main(argv=None, cwd="/", read_char=None, write=None):
    del argv, cwd, read_char

    def emit(s):
        if write:
            write(s + "\n")
        else:
            print(s)

    emit("linenoise demo start")

    # Sequence: type "hello", backspace, " world", press Enter.
    byte_seq = [ord(c) for c in "hello"] + [0x7f] + [ord(c) for c in " world"] + [0x0d]
    try:
        line, captured = await _feed_bytes(byte_seq)
    except Exception as exc:
        emit("linenoise demo: EXC " + type(exc).__name__ + " " + str(exc))
        return

    if line == "hell world":
        emit("linenoise edit ok line=" + repr(line))
    else:
        emit("linenoise edit FAIL line=" + repr(line))

    # Empty-input EOF path: feeding only the Ctrl-C / cancel byte (0x03)
    # should make linenoise return None.
    try:
        line2, _ = await _feed_bytes([0x03])
    except Exception as exc:
        emit("linenoise eof: EXC " + type(exc).__name__ + " " + str(exc))
        line2 = "exception"
    if line2 is None:
        emit("linenoise eof ok")
    else:
        emit("linenoise eof FAIL line=" + repr(line2))

    # Verify history utilities still work after edit cycles.
    if _hal.linenoise_history_add("post-edit") == 1:
        emit("linenoise history ok")
    else:
        emit("linenoise history FAIL")

    emit("linenoise demo done")
