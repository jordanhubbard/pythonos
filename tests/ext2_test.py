"""
tests/ext2_test.py — Host-side tests for kernel.fs.ext2.

Round-trips file + directory operations against a copy of build/disk.img,
then runs `e2fsck -nf` (via Docker) on the modified image to confirm the
write path is correct.

Usage:
    python3 tests/ext2_test.py [path-to-pristine-image]

Defaults to build/disk.img in the repo root.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── Make `kernel.fs` importable on the host ─────────────────────────────────
# kernel/__init__.py imports _hal, which only exists in the guest.  Stub it.

def _install_hal_stub() -> None:
    if "_hal" in sys.modules:
        return
    stub = types.ModuleType("_hal")
    stub.ARCH = "host"
    stub.SMP_ONLINE = 1
    stub.SMP_CPUS = 1
    stub.SMP_WORKERS = 0
    stub.PY_GIL_DISABLED = 0
    sys.modules["_hal"] = stub


def _shim_kernel_init() -> None:
    """Replace kernel/__init__.py with a no-op so submodule imports work."""
    if "kernel" in sys.modules:
        return
    kernel_pkg = types.ModuleType("kernel")
    kernel_pkg.__path__ = [os.path.join(REPO, "kernel")]
    sys.modules["kernel"] = kernel_pkg
    # also stub kernel.log because vfs/tmpfs may import it transitively
    log_mod = types.ModuleType("kernel.log")
    log_mod.info = lambda *a, **k: None
    log_mod.warn = lambda *a, **k: None
    log_mod.error = lambda *a, **k: None
    log_mod.debug = lambda *a, **k: None
    sys.modules["kernel.log"] = log_mod


_install_hal_stub()
_shim_kernel_init()
sys.path.insert(0, REPO)

from kernel.fs.ext2 import Ext2FS, FileBlockDevice  # noqa: E402
from kernel.fs.vfs import VFS, OpenFlags, InodeType  # noqa: E402


# ── Test helpers ────────────────────────────────────────────────────────────

def _docker_e2fsck(image_path: str) -> tuple[int, str]:
    """Run `e2fsck -nf` on `image_path` via the pythonos-builder image.
    Returns (exit_code, combined_output). exit_code 0 means clean."""
    abspath = os.path.abspath(image_path)
    workdir = os.path.dirname(abspath)
    fname = os.path.basename(abspath)
    cmd = [
        "docker", "run", "--rm", "--platform", "linux/amd64",
        "-v", f"{workdir}:/work", "-w", "/work",
        "pythonos-builder",
        "e2fsck", "-nf", fname,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, (result.stdout or "") + (result.stderr or "")


# ── Tests ───────────────────────────────────────────────────────────────────

async def test_basic_roundtrip(image_path: str) -> None:
    dev = FileBlockDevice(image_path)
    try:
        fs = await Ext2FS.mount(dev)
        root = fs.root()

        names = await root.readdir()
        assert "home" in names, names
        assert "apps" in names, names
        assert "lost+found" in names, names
        print(f"  [OK] root readdir: {sorted(names)}")

        home = await root.lookup("home")
        st = await home.stat()
        assert st.inode_type == InodeType.DIR
        print(f"  [OK] /home stat: {st}")
    finally:
        dev.close()


async def test_create_write_read(image_path: str) -> None:
    payload = (b"hello pythonos ext2 driver\n" * 4096)   # ~104 KiB → spans many blocks + indirect
    dev = FileBlockDevice(image_path)
    try:
        fs = await Ext2FS.mount(dev)
        root = fs.root()
        home = await root.lookup("home")

        # Create file
        node = await home.create("test.txt", InodeType.FILE)
        n = await node.write(0, payload)
        assert n == len(payload)
        # And another small write at an offset to exercise partial-block writes
        await node.write(len(payload), b"TAIL")

        # Reopen via lookup and read back
        home2 = await fs.root().lookup("home")
        node2 = await home2.lookup("test.txt")
        st = await node2.stat()
        assert st.size == len(payload) + 4, st.size

        got = await node2.read(0, st.size)
        assert got == payload + b"TAIL", "read-back mismatch"
        print(f"  [OK] write+read {st.size} bytes round-trip")

        # Read with offset
        got2 = await node2.read(13, 17)
        assert got2 == payload[13:30]
        print(f"  [OK] partial read")
    finally:
        dev.close()


async def test_mkdir_nested(image_path: str) -> None:
    dev = FileBlockDevice(image_path)
    try:
        fs = await Ext2FS.mount(dev)
        home = await fs.root().lookup("home")
        sub = await home.create("sub", InodeType.DIR)
        nested = await sub.create("nested", InodeType.DIR)
        # readdir at each level
        home_names = await home.readdir()
        assert "sub" in home_names, home_names
        sub_names = await sub.readdir()
        assert set([".", "..", "nested"]).issubset(set(sub_names)), sub_names
        nested_names = await nested.readdir()
        assert set(nested_names) == {".", ".."}, nested_names
        print(f"  [OK] mkdir + readdir at three depths")
    finally:
        dev.close()


async def test_unlink_rmdir(image_path: str) -> None:
    dev = FileBlockDevice(image_path)
    try:
        fs = await Ext2FS.mount(dev)
        root = fs.root()
        home = await root.lookup("home")

        # /home/test.txt was created by test_create_write_read; unlink it.
        await home.unlink("test.txt")
        names = await home.readdir()
        assert "test.txt" not in names

        # rmdir nested first, then sub
        sub = await home.lookup("sub")
        await sub.unlink("nested")
        await home.unlink("sub")

        names = await home.readdir()
        assert "sub" not in names
        print(f"  [OK] unlink + rmdir")
    finally:
        dev.close()


async def test_truncate(image_path: str) -> None:
    dev = FileBlockDevice(image_path)
    try:
        fs = await Ext2FS.mount(dev)
        home = await fs.root().lookup("home")
        node = await home.create("trunc.txt", InodeType.FILE)
        await node.write(0, b"x" * 8192)
        st = await node.stat(); assert st.size == 8192
        await node.truncate(100)
        st = await node.stat(); assert st.size == 100
        got = await node.read(0, 200)
        assert got == b"x" * 100, len(got)
        # Extend via writes past end
        await node.write(200, b"Z")
        st = await node.stat(); assert st.size == 201, st.size
        got = await node.read(99, 102)
        # Bytes 99 = 'x', 100..199 hole = 0, 200 = 'Z'
        assert got == b"x" + b"\x00" * 100 + b"Z", got[:5]
        # Clean up
        await fs.root().lookup("home")
        await home.unlink("trunc.txt")
        print(f"  [OK] truncate + sparse extend")
    finally:
        dev.close()


async def test_persist_across_mounts(image_path: str) -> None:
    """Write a file in one mount session, verify after a fresh mount it
    reads back unchanged AND is still e2fsck-clean afterwards."""
    payload = b"persistence-check\n" * 200
    dev = FileBlockDevice(image_path)
    try:
        fs = await Ext2FS.mount(dev)
        home = await fs.root().lookup("home")
        node = await home.create("persist.bin", InodeType.FILE)
        await node.write(0, payload)
    finally:
        dev.close()
    # Fresh mount
    dev2 = FileBlockDevice(image_path)
    try:
        fs2 = await Ext2FS.mount(dev2)
        home2 = await fs2.root().lookup("home")
        node2 = await home2.lookup("persist.bin")
        st = await node2.stat()
        assert st.size == len(payload)
        got = await node2.read(0, st.size)
        assert got == payload, "persistence mismatch"
        # Leave it on disk — exercise the "files surviving" e2fsck path.
        print(f"  [OK] persisted across remount ({len(payload)} bytes)")
    finally:
        dev2.close()


async def test_via_vfs(image_path: str) -> None:
    """Confirm the driver speaks the VFS protocol by mounting it and doing
    file ops through the high-level VFS API."""
    dev = FileBlockDevice(image_path)
    try:
        fs = await Ext2FS.mount(dev)
        v = VFS()
        v.mount("/disk", fs)
        # readdir at root via VFS
        names = await v.readdir("/disk")
        assert "home" in names, names
        # Open + write + close
        fd = await v.open("/disk/home/via_vfs.txt", OpenFlags.RDWR | OpenFlags.CREAT)
        await v.write(fd, b"vfs payload")
        v.close(fd)
        # Reopen + read
        fd = await v.open("/disk/home/via_vfs.txt", OpenFlags.RDONLY)
        data = await v.read(fd, 1024)
        v.close(fd)
        assert data == b"vfs payload", data
        # mkdir + readdir
        await v.mkdir("/disk/home/vfsdir")
        ls = await v.readdir("/disk/home")
        assert "vfsdir" in ls
        # Cleanup
        await v.unlink("/disk/home/via_vfs.txt")
        await v.unlink("/disk/home/vfsdir")
        print(f"  [OK] VFS-level open/write/read/mkdir/unlink")
    finally:
        dev.close()


# ── Driver ──────────────────────────────────────────────────────────────────

async def _amain(pristine: str) -> None:
    with tempfile.TemporaryDirectory() as td:
        # Copy 1: raw round-trip + e2fsck
        copy1 = os.path.join(td, "disk.img")
        shutil.copyfile(pristine, copy1)

        print("[1] root listing")
        await test_basic_roundtrip(copy1)

        print("[2] create + write + read")
        await test_create_write_read(copy1)

        print("[3] mkdir nested")
        await test_mkdir_nested(copy1)

        print("[4] unlink + rmdir")
        await test_unlink_rmdir(copy1)

        print("[5] truncate")
        await test_truncate(copy1)

        print("[6] persist across mount sessions")
        await test_persist_across_mounts(copy1)

        print("[7] VFS integration")
        await test_via_vfs(copy1)

        print("[8] e2fsck -nf on modified image")
        rc, out = _docker_e2fsck(copy1)
        if rc != 0:
            print("--- e2fsck output ---")
            print(out)
            print("---------------------")
            raise SystemExit(f"e2fsck reported errors (rc={rc})")
        print("  [OK] e2fsck clean")
        # Show summary line
        for line in out.splitlines():
            if "blocks" in line or "non-contiguous" in line:
                print(f"     {line}")

    print("\nALL EXT2 TESTS PASSED")


def main() -> None:
    pristine = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "build", "disk.img")
    if not os.path.exists(pristine):
        raise SystemExit(f"image not found: {pristine}")
    asyncio.run(_amain(pristine))


if __name__ == "__main__":
    main()
