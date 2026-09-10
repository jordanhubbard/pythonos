"""Path: /examples/start_here/hello_kernel.py

First tour of the PythonOS kernel shell. Small helpers format output; ``main``
reads the virtual filesystem and scheduler so learners see that ordinary
Python objects expose live operating-system state.
"""

from kernel.fs.vfs import vfs
from kernel.scheduler import scheduler


def _line(write, text=""):
    if write:
        write(text + "\n")
    else:
        print(text)


def _join(items):
    return ", ".join(items)


async def main(argv=None, cwd="/", read_char=None, write=None):
    argv = argv or []
    name = argv[0] if argv else "PythonOS"

    _line(write, "Hello, " + name + "!")
    _line(write, "cwd: " + cwd)

    root_entries = await vfs.readdir("/")
    _line(write, "root entries: " + _join(root_entries))

    tasks = list(scheduler.ps())
    _line(write, "tasks: " + str(len(tasks)))
    for proc in tasks[:5]:
        pid = str(proc.pid).rjust(4)
        _line(write, "  " + pid + "  " + proc.name + "  " + proc.state.name)
