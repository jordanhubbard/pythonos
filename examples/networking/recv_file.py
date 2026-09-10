"""Receive one TCP connection and save its bytes into TmpFS."""

from kernel.fs.vfs import vfs, OpenFlags
from kernel.net import stack
from kernel.net.ip import ip_str
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
    port = int(argv[0]) if argv else 7000
    out_path = _abspath(argv[1], cwd) if len(argv) > 1 else "/tmp/inbox.bin"

    _line(write, "Receiving one file on " + ip_str(stack.local_ip) + ":" + str(port))
    _line(write, "Host example with make run: printf hello | nc localhost 17000")
    _line(write, "Saving to " + out_path)

    listener = await tcp.listen(port)
    conn = None
    chunks = []
    total = 0
    try:
        conn = await listener.accept()
        while True:
            chunk = await conn.recv(1024)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            _line(write, "received " + str(total) + " bytes")
    finally:
        if conn is not None:
            conn.close()
            tcp.remove_connection(conn)
        listener.close()

    fd = await vfs.open(out_path, OpenFlags.WRONLY | OpenFlags.CREAT | OpenFlags.TRUNC)
    await vfs.write(fd, b"".join(chunks))
    vfs.close(fd)
    _line(write, "saved " + str(total) + " bytes")
