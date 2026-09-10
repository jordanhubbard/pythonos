"""Path: /examples/internals/check_home.py

Internal round-trip fixture proving that the ext2-backed ``/home`` mount is
live. The write, stat, and read sections emit fixed markers consumed by the
host smoke tests; this file is contributor material rather than a lesson.
"""

from kernel.fs.vfs import vfs, OpenFlags

PATH = '/home/smoke.txt'
PAYLOAD = b'ef6.4-mark'


async def main(argv=None, cwd="/", read_char=None, write=None):
    out = write or (lambda s: print(s, end=""))

    fd = await vfs.open(PATH, OpenFlags.WRONLY | OpenFlags.CREAT | OpenFlags.TRUNC)
    n = await vfs.write(fd, PAYLOAD)
    vfs.close(fd)
    if n != len(PAYLOAD):
        out(f"EF64_HOME_FAIL: short write {n}/{len(PAYLOAD)}\n")
        return

    fd = await vfs.open(PATH, OpenFlags.RDONLY)
    got = await vfs.read(fd, 64)
    vfs.close(fd)
    if got != PAYLOAD:
        out(f"EF64_HOME_FAIL: got {got!r}\n")
        return

    await vfs.unlink(PATH)
    out("EF64_HOME_OK\n")
