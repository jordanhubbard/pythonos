"""
kernel.commands — Precompiled implementations for the seeded /bin commands.

The TmpFS still exposes small Python launcher scripts in /bin, but the shell
dispatches these functions directly while the runtime compiler is limited.
"""

from kernel.fs.vfs import vfs, OpenFlags
from kernel.net.ip import ip_str
from kernel.scheduler import scheduler


SCRIPTS = {
    "sysinfo.py": (
        "from kernel import commands\n"
        "await commands.sysinfo(argv, cwd, _write)\n"
    ),
    "netstat.py": (
        "from kernel import commands\n"
        "await commands.netstat(argv, cwd, _write)\n"
    ),
    "ls.py": (
        "from kernel import commands\n"
        "await commands.ls(argv, cwd, _write)\n"
    ),
    "ps.py": (
        "from kernel import commands\n"
        "await commands.ps(argv, cwd, _write)\n"
    ),
    "pwd.py": (
        "from kernel import commands\n"
        "await commands.pwd(argv, cwd, _write)\n"
    ),
    "cd.py": (
        "from kernel import commands\n"
        "cwd = await commands.cd(argv, cwd, _write)\n"
    ),
    "cat.py": (
        "from kernel import commands\n"
        "await commands.cat(argv, cwd, _write)\n"
    ),
    "cp.py": (
        "from kernel import commands\n"
        "await commands.cp(argv, cwd, _write)\n"
    ),
    "mv.py": (
        "from kernel import commands\n"
        "await commands.mv(argv, cwd, _write)\n"
    ),
    "ftp.py": (
        "from kernel import commands\n"
        "await commands.ftp(argv, cwd, _write)\n"
    ),
    "ed.py": (
        "from kernel import commands\n"
        "await commands.ed(argv, cwd, _write)\n"
    ),
    "pythonos_gui.py": (
        "from kernel import commands\n"
        "await commands.pythonos_gui(argv, cwd, _write)\n"
    ),
    "bridge_ping.py": (
        "from kernel import commands\n"
        "await commands.bridge_ping(argv, cwd, _write)\n"
    ),
}


def _line(write, text: str = "") -> None:
    write(text + "\n")


def _abspath(path: str, cwd: str) -> str:
    if path.startswith("/"):
        target = path
    else:
        target = cwd.rstrip("/") + "/" + path

    parts = []
    for seg in target.split("/"):
        if seg == "..":
            if parts:
                parts.pop()
        elif seg and seg != ".":
            parts.append(seg)
    return "/" + "/".join(parts)


async def sysinfo(argv: list[str], cwd: str, write) -> None:
    import _hal
    _line(write, "PythonOS")
    _line(write, "CPUs: " + str(getattr(_hal, "SMP_ONLINE", 1)) + "/" +
          str(getattr(_hal, "SMP_CPUS", 1)) + " online")
    _line(write, "SMP workers: " + str(getattr(_hal, "SMP_WORKERS", 1)) +
          " boot self-tests")
    _line(write, "GIL: " +
          ("disabled" if getattr(_hal, "PY_GIL_DISABLED", 0) else "enabled"))
    tasks = list(scheduler.ps())
    _line(write, "Scheduler: " + str(len(tasks)) + " tasks")
    _line(write, "cwd: " + cwd)


async def netstat(argv: list[str], cwd: str, write) -> None:
    from kernel.net import stack
    _line(write, "Interface   local_ip")
    _line(write, "lo          127.0.0.1")
    _line(write, "eth0        " + ip_str(stack.local_ip))


async def ls(argv: list[str], cwd: str, write) -> None:
    path = _abspath(argv[0], cwd) if argv else cwd
    entries = await vfs.readdir(path)
    _line(write, "  ".join(entries))


async def ps(argv: list[str], cwd: str, write) -> None:
    for p in scheduler.ps():
        pid = str(p.pid).rjust(4)
        name = p.name.ljust(22)
        _line(write, pid + "  " + name + "  " + p.state.name)


async def pwd(argv: list[str], cwd: str, write) -> None:
    _line(write, cwd)


async def cd(argv: list[str], cwd: str, write) -> str:
    target = _abspath(argv[0], cwd) if argv else "/"
    await vfs.readdir(target)
    return target


async def cat(argv: list[str], cwd: str, write) -> None:
    if not argv:
        _line(write, "usage: cat FILE [...]")
        return

    for arg in argv:
        path = _abspath(arg, cwd)
        fd = None
        try:
            fd = await vfs.open(path)
            while True:
                chunk = await vfs.read(fd, 1024)
                if not chunk:
                    break
                write(chunk.decode("utf-8", errors="replace"))
        except FileNotFoundError:
            _line(write, "cat: " + path + ": not found")
        except IsADirectoryError:
            _line(write, "cat: " + path + ": is a directory")
        except Exception as e:
            _line(write, "cat: " + path + ": " + str(e))
        finally:
            if fd is not None:
                vfs.close(fd)


async def _read_all(path: str) -> bytes:
    fd = await vfs.open(path)
    data = b""
    while True:
        chunk = await vfs.read(fd, 4096)
        if not chunk:
            break
        data = data + chunk
    vfs.close(fd)
    return data


async def _write_all(path: str, data: bytes) -> None:
    fd = await vfs.open(path, OpenFlags.WRONLY | OpenFlags.CREAT | OpenFlags.TRUNC)
    await vfs.write(fd, data)
    vfs.close(fd)


async def cp(argv: list[str], cwd: str, write) -> None:
    if len(argv) < 2:
        _line(write, "usage: cp SRC DST")
        return
    src = _abspath(argv[0], cwd)
    dst = _abspath(argv[1], cwd)
    await _write_all(dst, await _read_all(src))


async def mv(argv: list[str], cwd: str, write) -> None:
    if len(argv) < 2:
        _line(write, "usage: mv SRC DST")
        return
    src = _abspath(argv[0], cwd)
    dst = _abspath(argv[1], cwd)
    if src == dst:
        return
    await _write_all(dst, await _read_all(src))
    await vfs.unlink(src)


def _ftp_usage(write) -> None:
    _line(write, "usage: ftp get DST [PORT]")
    _line(write, "       ftp put SRC [HOST] [PORT]")
    _line(write, "       ftp recv DST [PORT]")
    _line(write, "       ftp send SRC [HOST] [PORT]")
    _line(write, "get/recv: listen for one TCP stream and save it to DST")
    _line(write, "put/send: connect to HOST:PORT and send SRC")
    _line(write, "defaults: PORT=7000 for get, HOST=10.0.2.2 PORT=7001 for put")


def _parse_port(value: str, write):
    try:
        port = int(value)
    except ValueError:
        _line(write, "ftp: invalid port: " + value)
        return None
    if port < 1 or port > 65535:
        _line(write, "ftp: port out of range: " + value)
        return None
    return port


async def _ftp_get(path: str, port: int, write) -> None:
    from kernel.net import stack
    from kernel.net.tcp import tcp

    fd = None
    conn = None
    listener = None
    listener = await tcp.listen(port)
    total = 0
    saved = False

    _line(write, "ftp: listening on " + ip_str(stack.local_ip) + ":" + str(port))
    _line(write, "ftp: waiting for one incoming file stream")

    try:
        conn = await listener.accept()
        listener.close()
        listener = None

        fd = await vfs.open(path, OpenFlags.WRONLY | OpenFlags.CREAT | OpenFlags.TRUNC)
        while True:
            chunk = await conn.recv(1024)
            if not chunk:
                break
            await vfs.write(fd, chunk)
            total += len(chunk)
        saved = True
    finally:
        if fd is not None:
            vfs.close(fd)
        if conn is not None:
            conn.close()
            tcp.remove_connection(conn)
        if listener is not None:
            listener.close()

    if saved:
        _line(write, "ftp: saved " + str(total) + " bytes to " + path)


async def _ftp_put(path: str, host: str, port: int, write) -> None:
    from kernel.net.tcp import tcp

    fd = await vfs.open(path)
    conn = None
    _line(write, "ftp: connecting to " + host + ":" + str(port))
    total = 0
    try:
        conn = await tcp.connect(host, port)
        while True:
            chunk = await vfs.read(fd, 1024)
            if not chunk:
                break
            await conn.send(chunk)
            total += len(chunk)
    finally:
        vfs.close(fd)
        if conn is not None:
            conn.close()
            tcp.remove_connection(conn)

    _line(write, "ftp: sent " + str(total) + " bytes from " + path)


async def _run_ftp_get(path: str, port: int, write) -> None:
    try:
        await _ftp_get(path, port, write)
    except OSError as e:
        _line(write, "ftp: " + str(e))


async def _run_ftp_put(path: str, host: str, port: int, write) -> None:
    try:
        await _ftp_put(path, host, port, write)
    except OSError as e:
        _line(write, "ftp: " + str(e))


async def ftp(argv: list[str], cwd: str, write) -> None:
    if not argv or argv[0] in ("help", "-h", "--help"):
        _ftp_usage(write)
        return

    op = argv[0]
    if op in ("get", "recv"):
        if len(argv) < 2 or len(argv) > 3:
            _ftp_usage(write)
            return
        path = _abspath(argv[1], cwd)
        port = 7000
        if len(argv) == 3:
            parsed = _parse_port(argv[2], write)
            if parsed is None:
                return
            port = parsed
        await _run_ftp_get(path, port, write)
        return

    if op in ("put", "send"):
        if len(argv) < 2 or len(argv) > 4:
            _ftp_usage(write)
            return
        path = _abspath(argv[1], cwd)
        host = argv[2] if len(argv) >= 3 else "10.0.2.2"
        port = 7001
        if len(argv) == 4:
            parsed = _parse_port(argv[3], write)
            if parsed is None:
                return
            port = parsed
        await _run_ftp_put(path, host, port, write)
        return

    _line(write, "ftp: unknown operation: " + op)
    _ftp_usage(write)


async def ed(argv: list[str], cwd: str, write, read_char=None) -> None:
    from kernel.ed import run as _ed_run

    await _ed_run(argv, cwd, write, read_char)


async def pythonos_gui(argv: list[str], cwd: str, write) -> None:
    """Start the GUI desktop. Refuses to run without a framebuffer +
    GUI input bound. Lists registered apps; ``pythonos_gui <name>``
    launches one directly. With no argument, launches the first app
    (or a banner if there are none)."""
    from kernel.display.framebuffer import fb
    if not fb:
        _line(write, "pythonos_gui: no framebuffer (boot in GUI mode: make run-gui)")
        return

    from kernel.gui import input as _gui_input
    if not _gui_input.queue:
        _line(write, "pythonos_gui: no GUI input bound")
        return

    # Importing apps triggers each app module's registry.register() call.
    import apps                             # noqa: F401
    import apps.demos                        # noqa: F401
    import apps.terminal                     # noqa: F401
    import apps.editor                       # noqa: F401
    import apps.image_viewer                 # noqa: F401
    import apps.files                        # noqa: F401
    from apps import registry
    from kernel.gui.compositor import compositor

    apps_list = registry.list_apps()
    if not apps_list:
        _line(write, "pythonos_gui: no apps registered")
        return

    requested = argv[0] if argv else None
    if requested:
        info = registry.get(requested)
        if info is None:
            _line(write, "pythonos_gui: unknown app: " + requested)
            _line(write, "available: " + ", ".join(a.name for a in apps_list))
            return
        target = info
    else:
        target = apps_list[0]

    _line(write, f"pythonos_gui: starting compositor + {target.name}")
    compositor.start()
    try:
        await target.entry()
    except Exception as e:
        _line(write, f"pythonos_gui: {target.name} crashed: {e}")
    _line(write, "pythonos_gui: app exited; back at REPL")


async def bridge_ping(argv: list[str], cwd: str, write) -> None:
    """Round-trip a hello + ping through the host pythonos_bridge
    companion. bridge.call is synchronous now."""
    from kernel import bridge as br
    try:
        r = br.bridge.hello()
        _line(write, f"hello: agent={r.get('agent')} "
                     f"protocol={r.get('protocol')} sdl={r.get('sdl_ver')}")
    except br.BridgeError as e:
        _line(write, f"bridge_ping: hello failed: {e}")
        return
    tag = argv[0] if argv else "from-pythonos"
    try:
        r = br.bridge.call("ping", {"tag": tag})
        _line(write, f"ping:  pong={r.get('pong')} tag={r.get('tag')}")
    except br.BridgeError as e:
        _line(write, f"bridge_ping: ping failed: {e}")
