"""kernel.net.chatbus — In-kernel chat bus shared across REPL sessions.

PythonOS already exposes a multi-session TCP REPL on port 5000. Each
``nc localhost 5555`` is its own kernel namespace + Shell. The chat
bus lets those sessions push lines to each other — exactly the BBS
trick an Apple ][ hobbyist would have built first thing if they had
two phone lines and a Python kernel.

Usage from any REPL session::

    chat.nick("alice")           # set your name (default: <port>)
    chat.send("hello world!")    # broadcast to all other sessions
    chat.who()                   # list connected nicks

Incoming messages arrive as line writes on the recipient's TCP
stream — no polling required, the message just appears in their
terminal output. ``chat`` is bound automatically into every Shell
namespace by :mod:`kernel.net.repl_server`.
"""

from typing import Callable


class ChatBus:
    def __init__(self) -> None:
        self._sessions: list[tuple[int, Callable[[str], None], list]] = []
        # Each entry: (handle, write_fn, [nick]). nick is mutable from
        # nick(), so we wrap it in a one-element list.
        self._next_handle = 0

    def register(self, write_fn: Callable[[str], None],
                  nick: str = "") -> int:
        self._next_handle += 1
        h = self._next_handle
        self._sessions.append((h, write_fn, [nick or f"user{h}"]))
        return h

    def unregister(self, handle: int) -> None:
        self._sessions = [s for s in self._sessions if s[0] != handle]

    def set_nick(self, handle: int, nick: str) -> None:
        for h, _w, holder in self._sessions:
            if h == handle:
                holder[0] = nick.strip() or f"user{h}"
                return

    def send(self, handle: int, msg: str) -> int:
        from_nick = "?"
        for h, _w, holder in self._sessions:
            if h == handle:
                from_nick = holder[0]
                break
        line = "\r\n\x1b[36m[" + from_nick + "]\x1b[0m " + str(msg) + "\r\n"
        count = 0
        for h, w, _holder in self._sessions:
            if h == handle:
                continue
            try:
                w(line)
                count += 1
            except Exception:
                pass
        return count

    def who(self) -> list[str]:
        return [holder[0] for _h, _w, holder in self._sessions]


bus = ChatBus()


class _SessionChat:
    """Per-session adapter — what the REPL user actually sees as
    `chat`. Bound to one handle so .send() / .nick() target the right
    session without the user having to track their own handle."""

    __slots__ = ("_handle",)

    def __init__(self, handle: int) -> None:
        self._handle = handle

    def send(self, msg: str) -> int:
        n = bus.send(self._handle, msg)
        return n

    def nick(self, name: str) -> None:
        bus.set_nick(self._handle, name)

    def who(self) -> list[str]:
        return bus.who()

    def __repr__(self) -> str:
        return f"<chat: {len(bus.who())} session(s) connected>"
