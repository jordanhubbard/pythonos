"""Chunked file transfer between the PythonOS VFS and its desktop host."""

import asyncio

from kernel.bridge import bridge
from kernel.fs.vfs import OpenFlags, vfs


CHUNK_SIZE = 32 * 1024


def safe_name(name: str) -> str:
    """Reduce an untrusted host label to one VFS basename."""
    value = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    value = "".join(ch for ch in value if ch >= " " and ch != "/")
    if value in ("", ".", ".."):
        return "dropped-file"
    return value[:255]


def join_path(directory: str, name: str) -> str:
    return (directory.rstrip("/") + "/" + name) if directory != "/" else "/" + name


async def _unused_path(directory: str, name: str) -> str:
    path = join_path(directory, name)
    try:
        await vfs.stat(path)
    except FileNotFoundError:
        return path
    for suffix in range(1, 10000):
        candidate = path + "." + str(suffix)
        try:
            await vfs.stat(candidate)
        except FileNotFoundError:
            return candidate
    raise OSError("could not choose a unique destination name")


async def import_host_file(token: int, name: str, size: int,
                           directory: str = "/home") -> tuple[str, int]:
    """Pull one opaque SDL drop token into the VFS in bounded chunks."""
    if token <= 0:
        raise ValueError("invalid host file token")
    path = await _unused_path(directory, safe_name(name))
    fd = await vfs.open(path, OpenFlags.WRONLY | OpenFlags.CREAT | OpenFlags.TRUNC)
    offset = 0
    complete = False
    try:
        while True:
            result = bridge.call("host.file.read", {
                "token": token, "offset": offset, "length": CHUNK_SIZE,
            })
            chunk = bytes.fromhex(result.get("data", ""))
            written = 0
            while written < len(chunk):
                count = await vfs.write(fd, chunk[written:])
                if count <= 0:
                    raise OSError("short VFS write during host import")
                written += count
            offset += len(chunk)
            if result.get("eof"):
                break
            if not chunk:
                raise OSError("host import stopped before EOF")
            await asyncio.sleep(0)
        if size >= 0 and offset != size:
            raise OSError("host file changed during transfer")
        complete = True
    finally:
        vfs.close(fd)
        if not complete:
            try:
                await vfs.unlink(path)
            except OSError:
                pass
    return path, offset


async def export_guest_file(path: str) -> tuple[str, int]:
    """Stream one VFS file into the desktop host's configured export dir."""
    name = safe_name(path)
    started = bridge.call("host.export.begin", {"name": name})
    token = int(started.get("token", 0))
    if token <= 0:
        raise OSError("desktop did not create an export")
    fd = await vfs.open(path, OpenFlags.RDONLY)
    total = 0
    complete = False
    try:
        while True:
            chunk = await vfs.read(fd, CHUNK_SIZE)
            if not chunk:
                break
            bridge.call("host.export.chunk", {"token": token}, payload=chunk)
            total += len(chunk)
            await asyncio.sleep(0)
        finished = bridge.call("host.export.finish", {"token": token})
        complete = True
    finally:
        vfs.close(fd)
        if not complete:
            try:
                bridge.call("host.export.abort", {"token": token})
            except Exception:
                pass
    return str(finished.get("path", started.get("path", name))), total
