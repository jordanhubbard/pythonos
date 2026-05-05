"""
kernel.fs.vfs — Virtual Filesystem Switch.

All filesystem access goes through here. Concrete filesystems
(tmpfs, ext2, etc.) register as mounts at a path prefix.

The VFS presents a uniform Protocol:
  open / read / write / seek / close / stat
  mkdir / rmdir / readdir / unlink / rename

File descriptors are integers (like POSIX). The fd table lives here.
"""


import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import IntFlag, IntEnum
from pathlib import PurePosixPath
from typing import AsyncIterator, Protocol, runtime_checkable


# ── Constants ─────────────────────────────────────────────────────────────────

class OpenFlags(IntFlag):
    RDONLY  = 0
    WRONLY  = 1
    RDWR    = 2
    CREAT   = 0x040
    TRUNC   = 0x200
    APPEND  = 0x400
    NONBLOCK= 0x800

class SeekWhence(IntEnum):
    SET = 0   # from beginning
    CUR = 1   # from current position
    END = 2   # from end

class InodeType(IntEnum):
    FILE    = 0
    DIR     = 1
    SYMLINK = 2
    DEVICE  = 3
    PIPE    = 4


# ── Filesystem Protocol ───────────────────────────────────────────────────────

@dataclass(slots=True)
class Stat:
    inode_type: InodeType
    size:       int
    uid:        int = 0
    gid:        int = 0
    mode:       int = 0o644
    nlink:      int = 1


@runtime_checkable
class FSNode(Protocol):
    async def stat(self) -> Stat: ...
    async def read(self, offset: int, n: int) -> bytes: ...
    async def write(self, offset: int, data: bytes) -> int: ...
    async def truncate(self, size: int = 0) -> None: ...
    async def readdir(self) -> list[str]: ...
    async def lookup(self, name: str) -> "FSNode": ...
    async def create(self, name: str, inode_type: InodeType) -> "FSNode": ...
    async def unlink(self, name: str) -> None: ...


@runtime_checkable
class Filesystem(Protocol):
    def root(self) -> FSNode: ...
    # Optional: a filesystem MAY implement flush() to persist pending writes
    # before unmount. The VFS calls it best-effort and tolerates its absence.
    # def flush(self) -> Awaitable[None] | None: ...


class NodeFS:
    """Adapter that exposes a single FSNode as the root of a Filesystem.

    Used to graft a sub-directory of one filesystem in as a mount point.
    For example, an ext2 disk laid out with ``/home`` and ``/apps`` at its
    root can be mounted into the VFS at those same paths by pre-resolving
    each sub-node with ``await ext2.root().lookup('home')`` and wrapping the
    result in ``NodeFS`` before calling ``vfs.mount('/home', NodeFS(node))``.

    A single underlying filesystem can back multiple NodeFS mounts; the
    wrappers do not own the underlying device.
    """

    __slots__ = ("_node",)

    def __init__(self, node: FSNode) -> None:
        self._node = node

    def root(self) -> FSNode:
        return self._node


# ── File descriptor table ─────────────────────────────────────────────────────

@dataclass(slots=True)
class FileDescription:
    node:   FSNode
    flags:  OpenFlags
    offset: int = 0


class VFS:
    def __init__(self) -> None:
        self._mounts: list[tuple[str, Filesystem]] = []   # sorted longest-first
        self._fds:    dict[int, FileDescription]   = {}
        self._next_fd = 3   # 0/1/2 reserved for stdin/stdout/stderr

    # ── Mount / unmount ───────────────────────────────────────────────────────

    def mount(self, path: str, fs: Filesystem) -> None:
        """Attach `fs` at `path`. If something is already mounted there it
        is replaced (the previous FS is dropped without flushing — caller
        should unmount first if persistence matters)."""
        norm = path.rstrip("/") or "/"
        self._mounts = [(p, f) for p, f in self._mounts if p != norm]
        self._mounts.append((norm, fs))
        # Sort longest-prefix-first so _resolve() finds the most specific
        # mount before falling back to a shorter (e.g. root) mount.
        self._mounts.sort(key=lambda m: len(m[0]), reverse=True)

    async def unmount(self, path: str) -> None:
        """Detach the filesystem at `path`. Calls `flush()` on it best-effort
        so durable filesystems (ext2 over virtio-blk, etc.) can persist any
        pending writes before they go away. Raises KeyError if no FS is
        mounted at `path`."""
        norm = path.rstrip("/") or "/"
        for i, (p, fs) in enumerate(self._mounts):
            if p == norm:
                # Best-effort flush: support both sync and async flush(), and
                # tolerate filesystems that don't implement it at all.
                flush = getattr(fs, "flush", None)
                if flush is not None:
                    result = flush()
                    if asyncio.iscoroutine(result):
                        await result
                del self._mounts[i]
                return
        raise KeyError(f"No filesystem mounted at {path!r}")

    # ── Sync helpers (used by kernel.vfs_import) ───────────────────────────
    # Synchronous read/exists checks for any mount whose backend exposes
    # ``read_sync`` / ``isdir_sync``. tmpfs implements both; ext2/virtio
    # backends don't (they're disk-bound) and read_sync returns None for
    # them — callers must use the async API.

    def read_sync(self, path: str) -> "bytes | None":
        s = "/" + path.strip("/")
        for mount_path, fs in self._mounts:
            if mount_path == "/":
                rel = s
            elif s == mount_path:
                rel = "/"
            elif s.startswith(mount_path + "/"):
                rel = "/" + s[len(mount_path) + 1:]
            else:
                continue
            reader = getattr(fs, "read_sync", None)
            if reader is None:
                return None
            return reader(rel)
        return None

    def isdir_sync(self, path: str) -> bool:
        s = "/" + path.strip("/")
        for mount_path, fs in self._mounts:
            if mount_path == "/":
                rel = s
            elif s == mount_path:
                rel = "/"
            elif s.startswith(mount_path + "/"):
                rel = "/" + s[len(mount_path) + 1:]
            else:
                continue
            checker = getattr(fs, "isdir_sync", None)
            if checker is None:
                return False
            return checker(rel)
        return False

    # ── Path resolution ───────────────────────────────────────────────────────

    async def _resolve(self, path: str) -> FSNode:
        # Normalize to /foo/bar (no trailing slash except for root)
        s = "/" + path.strip("/")
        for mount_path, fs in self._mounts:
            mp = mount_path   # stored as rstrip("/") or "/"
            if mp == "/":
                rel_parts = [p for p in s[1:].split("/") if p]
            elif s == mp:
                rel_parts = []
            elif s.startswith(mp + "/"):
                rel_parts = [p for p in s[len(mp) + 1:].split("/") if p]
            else:
                continue
            node = fs.root()
            for part in rel_parts:
                node = await node.lookup(part)
            return node
        raise FileNotFoundError(f"No filesystem mounted for {path!r}")

    # ── fd operations ─────────────────────────────────────────────────────────

    async def open(self, path: str, flags: OpenFlags = OpenFlags.RDONLY) -> int:
        if flags & OpenFlags.CREAT:
            try:
                node = await self._resolve(path)
            except FileNotFoundError:
                s = "/" + path.strip("/")
                parts = [p for p in s.split("/") if p]
                if not parts:
                    raise IsADirectoryError("cannot create root")
                parent_s = "/" + "/".join(parts[:-1]) if len(parts) > 1 else "/"
                parent = await self._resolve(parent_s)
                node = await parent.create(parts[-1], InodeType.FILE)
        else:
            node = await self._resolve(path)
        if flags & OpenFlags.TRUNC:
            await node.truncate()
        fd = self._next_fd
        self._next_fd += 1
        self._fds[fd] = FileDescription(node=node, flags=flags)
        return fd

    async def read(self, fd: int, n: int) -> bytes:
        desc = self._fds[fd]
        data = await desc.node.read(desc.offset, n)
        desc.offset += len(data)
        return data

    async def write(self, fd: int, data: bytes) -> int:
        desc = self._fds[fd]
        if desc.flags & OpenFlags.APPEND:
            st = await desc.node.stat()
            desc.offset = st.size
        written = await desc.node.write(desc.offset, data)
        desc.offset += written
        return written

    def seek(self, fd: int, offset: int, whence: SeekWhence = SeekWhence.SET) -> int:
        desc = self._fds[fd]
        if whence == SeekWhence.SET:
            desc.offset = offset
        elif whence == SeekWhence.CUR:
            desc.offset += offset
        elif whence == SeekWhence.END:
            # Would need stat — skip for now
            pass
        return desc.offset

    def close(self, fd: int) -> None:
        self._fds.pop(fd, None)

    async def stat(self, path: str) -> Stat:
        node = await self._resolve(path)
        return await node.stat()

    async def mkdir(self, path: str) -> None:
        parent_path = str(PurePosixPath(path).parent)
        name        = PurePosixPath(path).name
        parent      = await self._resolve(parent_path)
        await parent.create(name, InodeType.DIR)

    async def readdir(self, path: str) -> list[str]:
        node = await self._resolve(path)
        entries = await node.readdir()
        # Cross mount boundaries: if any FS is mounted as an immediate child
        # of `path`, surface its mount-point name even though it lives in a
        # different filesystem. e.g. readdir('/') on the root tmpfs should
        # also list 'home' and 'apps' when they have ext2 mounts.
        norm = "/" + path.strip("/")
        prefix = "/" if norm == "/" else norm + "/"
        seen = set(entries)
        for mp, _fs in self._mounts:
            if mp == "/" or mp == norm:
                continue
            if not mp.startswith(prefix):
                continue
            child = mp[len(prefix):]
            if "/" in child:        # not an immediate child
                continue
            if child not in seen:
                entries.append(child)
                seen.add(child)
        return entries

    async def unlink(self, path: str) -> None:
        parent_path = str(PurePosixPath(path).parent)
        name        = PurePosixPath(path).name
        parent      = await self._resolve(parent_path)
        await parent.unlink(name)

    @asynccontextmanager
    async def opened(self, path: str, flags: OpenFlags = OpenFlags.RDONLY) -> AsyncIterator[int]:
        fd = await self.open(path, flags)
        try:
            yield fd
        finally:
            self.close(fd)


# Module-level VFS singleton
vfs = VFS()
