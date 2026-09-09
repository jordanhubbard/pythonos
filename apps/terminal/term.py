"""apps.terminal.term — REPL inside a compositor window.

Reuses :class:`apps._textwin.TextWin` for the text-grid + input wiring
and feeds it to a fresh :class:`kernel.shell.Shell` instance.
"""

from kernel.gui.compositor import compositor, CompositorWindow
from kernel.shell import Shell
from apps import registry
from apps._textwin import TextWin


async def main(*args, **kwargs) -> None:
    win = CompositorWindow("Terminal", x=60, y=60, w=640, h=400)
    compositor.add_window(win)
    view = TextWin(win)
    win.set_event_handler(view.on_event)

    shell = Shell(read_char=view.read_char, write=view.write,
                   read_byte=view.read_byte, write_raw=view.write_raw,
                   can_exit=True)
    try:
        await shell.run()
    finally:
        win.close()


from apps._icons import terminal_icon

registry.register(
    name="terminal",
    description="Python REPL in a window",
    entry=main,
    icon_factory=terminal_icon,
)
