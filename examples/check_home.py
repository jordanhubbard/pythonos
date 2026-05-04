# Round-trip a small write/read against /home to confirm the ext2 mount
# wired up by ef6.4 is actually live. Prints a fixed marker on success
# (matched by tests/smoke_test.py); anything else is a failure.

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
