#!/usr/bin/env python3
"""
Unit tests for kernel.fs.vfs mount-table behavior (bd issue pythonos-ef6.1).

Runs on the host without QEMU. Exercises the parts of VFS that ef6.1 added:
  * longest-prefix-match path resolution
  * path stripping at mount boundaries
  * readdir crossing mount boundaries
  * unmount flushes the FS and raises on missing mounts
  * existing single-FS-at-/ behavior is preserved

Stub kernel imports that don't load on host so vfs.py and tmpfs.py can be
imported in isolation.
"""

import asyncio
import os
import sys
import types
import unittest

# Make the repo root importable.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# vfs.py imports nothing kernel-only, but tmpfs.py imports it via
# `from kernel.fs.vfs import ...`. Ensure the package itself does not
# pull in heavy boot wiring. kernel/fs/__init__.py is empty, but
# `kernel/__init__.py` is huge — guard against accidental import by
# pre-seeding a stub package if it hasn't been loaded yet.
if "kernel" not in sys.modules:
    pkg = types.ModuleType("kernel")
    pkg.__path__ = [os.path.join(ROOT, "kernel")]
    sys.modules["kernel"] = pkg
if "kernel.fs" not in sys.modules:
    fs_pkg = types.ModuleType("kernel.fs")
    fs_pkg.__path__ = [os.path.join(ROOT, "kernel", "fs")]
    sys.modules["kernel.fs"] = fs_pkg

from kernel.fs.vfs import VFS, OpenFlags, InodeType  # noqa: E402
from kernel.fs.tmpfs import TmpFS  # noqa: E402


def run(coro):
    """Run an async coroutine to completion in a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class FlushRecordingTmpFS(TmpFS):
    """TmpFS subclass that records flush() invocations for unmount tests."""

    def __init__(self) -> None:
        super().__init__()
        self.flush_calls = 0

    async def flush(self) -> None:
        self.flush_calls += 1


class MountTableTests(unittest.TestCase):
    # ── single-FS / backward-compat ───────────────────────────────────────────

    def test_root_only_single_fs_works_as_before(self):
        async def go():
            vfs = VFS()
            root = TmpFS()
            root.seed({"tmp": {"hello.txt": b"hi"}})
            vfs.mount("/", root)

            self.assertEqual(
                set(await vfs.readdir("/tmp")),
                {".", "..", "hello.txt"},
            )
            fd = await vfs.open("/tmp/hello.txt")
            try:
                self.assertEqual(await vfs.read(fd, 64), b"hi")
            finally:
                vfs.close(fd)
        run(go())

    # ── longest-prefix match ──────────────────────────────────────────────────

    def test_longest_prefix_match_picks_most_specific_mount(self):
        async def go():
            vfs = VFS()
            root = TmpFS();  root.seed({"tmp": {"x": b"root-x"}})
            home = TmpFS();  home.seed({"foo": b"home-foo"})
            apps = TmpFS();  apps.seed({"bar": b"apps-bar"})
            # Intentionally mount in a non-sorted order to exercise the
            # internal sort.
            vfs.mount("/", root)
            vfs.mount("/apps", apps)
            vfs.mount("/home", home)

            # /home/foo must hit the home FS, not root.
            fd = await vfs.open("/home/foo")
            self.assertEqual(await vfs.read(fd, 64), b"home-foo")
            vfs.close(fd)

            # /apps/bar must hit the apps FS.
            fd = await vfs.open("/apps/bar")
            self.assertEqual(await vfs.read(fd, 64), b"apps-bar")
            vfs.close(fd)

            # /tmp/x stays in the root FS.
            fd = await vfs.open("/tmp/x")
            self.assertEqual(await vfs.read(fd, 64), b"root-x")
            vfs.close(fd)
        run(go())

    def test_path_is_stripped_to_mount_relative(self):
        """The mounted FS sees /foo, not /home/foo."""
        async def go():
            vfs = VFS()
            root = TmpFS()
            home = TmpFS()
            home.seed({"alice": {"profile": b"A"}})
            vfs.mount("/", root)
            vfs.mount("/home", home)

            # If path translation were broken, the home FS would try to
            # look up "home" as a child of its root and 404.
            fd = await vfs.open("/home/alice/profile")
            self.assertEqual(await vfs.read(fd, 64), b"A")
            vfs.close(fd)

            # Stat at the mount point itself returns the FS's root inode,
            # which is a directory.
            st = await vfs.stat("/home")
            self.assertEqual(st.inode_type, InodeType.DIR)
        run(go())

    def test_writes_in_mounted_fs_dont_leak_into_root(self):
        async def go():
            vfs = VFS()
            root = TmpFS()
            home = TmpFS()
            vfs.mount("/", root)
            vfs.mount("/home", home)

            fd = await vfs.open("/home/note", OpenFlags.WRONLY | OpenFlags.CREAT)
            await vfs.write(fd, b"hello")
            vfs.close(fd)

            # File exists in the home FS.
            self.assertIn("note", await vfs.readdir("/home"))
            # readdir('/') sees 'home' as a mount-point entry.
            self.assertIn("home", await vfs.readdir("/"))
            # The root FS itself never received the write — it has no
            # 'home' directory at all.
            self.assertNotIn("home", root.root()._children)
        run(go())

    # ── readdir crossing mount boundaries ─────────────────────────────────────

    def test_readdir_root_lists_mount_points(self):
        async def go():
            vfs = VFS()
            root = TmpFS();  root.seed({"tmp": {}, "dev": {}})
            home = TmpFS()
            apps = TmpFS()
            vfs.mount("/", root)
            vfs.mount("/home", home)
            vfs.mount("/apps", apps)

            entries = set(await vfs.readdir("/"))
            self.assertIn("tmp", entries)
            self.assertIn("dev", entries)
            self.assertIn("home", entries)
            self.assertIn("apps", entries)
        run(go())

    def test_readdir_only_lists_immediate_child_mounts(self):
        """A mount at /a/b/c must NOT show up in readdir('/')."""
        async def go():
            vfs = VFS()
            root = TmpFS();  root.seed({"a": {"b": {}}})
            deep = TmpFS()
            vfs.mount("/", root)
            vfs.mount("/a/b/c", deep)

            entries = await vfs.readdir("/")
            self.assertIn("a", entries)
            self.assertNotIn("c", entries)

            # But readdir('/a/b') should surface 'c'.
            self.assertIn("c", await vfs.readdir("/a/b"))
        run(go())

    def test_readdir_does_not_duplicate_when_mountpoint_also_exists_in_parent(self):
        """If the parent FS happens to have a 'home' dir AND we mount over
        it, readdir('/') should list 'home' exactly once."""
        async def go():
            vfs = VFS()
            root = TmpFS();  root.seed({"home": {}})  # tmpfs already has /home
            home = TmpFS()
            vfs.mount("/", root)
            vfs.mount("/home", home)

            entries = await vfs.readdir("/")
            self.assertEqual(entries.count("home"), 1)
        run(go())

    # ── unmount ───────────────────────────────────────────────────────────────

    def test_unmount_calls_flush_and_removes_mount(self):
        async def go():
            vfs = VFS()
            root = TmpFS()
            home = FlushRecordingTmpFS()
            vfs.mount("/", root)
            vfs.mount("/home", home)

            await vfs.unmount("/home")
            self.assertEqual(home.flush_calls, 1)

            # After unmount, /home routes back to the root FS, which has
            # no 'home' entry — so opening a file under /home should fail.
            with self.assertRaises(FileNotFoundError):
                await vfs.open("/home/anything")
        run(go())

    def test_unmount_tolerates_fs_without_flush(self):
        async def go():
            vfs = VFS()
            root = TmpFS()
            home = TmpFS()  # no flush() method
            vfs.mount("/", root)
            vfs.mount("/home", home)
            await vfs.unmount("/home")  # must not raise
        run(go())

    def test_unmount_unknown_mount_raises(self):
        async def go():
            vfs = VFS()
            vfs.mount("/", TmpFS())
            with self.assertRaises(KeyError):
                await vfs.unmount("/nope")
        run(go())

    def test_remount_replaces_previous_fs(self):
        async def go():
            vfs = VFS()
            root = TmpFS()
            a = TmpFS();  a.seed({"marker": b"A"})
            b = TmpFS();  b.seed({"marker": b"B"})
            vfs.mount("/", root)
            vfs.mount("/data", a)
            vfs.mount("/data", b)   # replace

            fd = await vfs.open("/data/marker")
            self.assertEqual(await vfs.read(fd, 16), b"B")
            vfs.close(fd)
        run(go())

    # ── mount path normalization ──────────────────────────────────────────────

    def test_mount_path_with_trailing_slash_is_normalized(self):
        async def go():
            vfs = VFS()
            root = TmpFS()
            home = TmpFS();  home.seed({"f": b"h"})
            vfs.mount("/", root)
            vfs.mount("/home/", home)   # trailing slash

            fd = await vfs.open("/home/f")
            self.assertEqual(await vfs.read(fd, 4), b"h")
            vfs.close(fd)
            await vfs.unmount("/home")  # without trailing slash also works
        run(go())


if __name__ == "__main__":
    unittest.main(verbosity=2)
