"""Connect to a TCP server and send a TmpFS file."""

from kernel.fs.vfs import vfs
from kernel.net.tcp import tcp


def _line(write, text=""):
    if write:
        write(text + "\n")
    else:
        print(text)


def _abspath(path, cwd):
    if path.startswith("/"):
        return path
    return cwd.rstrip("/") + "/" + path


async def main(argv=None, cwd="/", read_char=None, write=None):
    argv = argv or []
    host = argv[0] if len(argv) > 0 else "10.0.2.2"
    port = int(argv[1]) if len(argv) > 1 else 7001
    path = _abspath(argv[2], cwd) if len(argv) > 2 else "/examples/README.txt"

    _line(write, "Connecting to " + host + ":" + str(port))
    _line(write, "Host example: nc -l 7001 > pythonos-example.txt")
    total = 0
    conn = None
    fd = None
    try:
        conn = await tcp.connect(host, port)
        fd = await vfs.open(path)
        while True:
            chunk = await vfs.read(fd, 1024)
            if not chunk:
                break
            await conn.send(chunk)
            total += len(chunk)
    finally:
        if fd is not None:
            vfs.close(fd)
        if conn is not None:
            conn.close()
            tcp.remove_connection(conn)
    _line(write, "sent " + str(total) + " bytes from " + path)
