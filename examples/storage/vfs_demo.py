"""Path: /examples/storage/vfs_demo.py

Virtual-filesystem lesson: normalize a path, create and write a TmpFS file,
reopen it for reading, then inspect metadata. Each stage mirrors the familiar
open/write/read/stat lifecycle without relying on a host filesystem.
"""

from kernel.fs.vfs import vfs, OpenFlags


def _line(write, text=""):
    if write:
        write(text + "\n")
    else:
        print(text)


def _abspath(path, cwd):
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


async def _write_all(path, data):
    fd = None
    try:
        fd = await vfs.open(path, OpenFlags.WRONLY | OpenFlags.CREAT | OpenFlags.TRUNC)
        await vfs.write(fd, data)
    finally:
        if fd is not None:
            vfs.close(fd)


async def _read_all(path):
    fd = None
    chunks = []
    try:
        fd = await vfs.open(path)
        while True:
            chunk = await vfs.read(fd, 1024)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        if fd is not None:
            vfs.close(fd)
    return b"".join(chunks)


async def main(argv=None, cwd="/", read_char=None, write=None):
    argv = argv or []
    path = _abspath(argv[0], cwd) if argv else "/tmp/vfs-demo.txt"
    lines = [
        "PythonOS VFS demo",
        "created from /examples/storage/vfs_demo.py",
        "cwd=" + cwd,
    ]
    payload = ("\n".join(lines) + "\n").encode("utf-8")

    await _write_all(path, payload)
    stat = await vfs.stat(path)
    _line(write, "VFS demo wrote " + str(stat.size) + " bytes to " + path)

    data = (await _read_all(path)).decode("utf-8", errors="replace")
    _line(write, "read back:")
    for line in data.splitlines():
        _line(write, "  " + line)

    entries = await vfs.readdir("/tmp")
    _line(write, "/tmp entries: " + ", ".join(entries))
