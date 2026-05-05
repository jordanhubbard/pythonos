#!/usr/bin/env python3
"""Boot arm64 kernel and probe compile() over PL011 serial.

arm64 is built with FREE_THREADING=0 (default) — testing here
isolates whether the parser bug is free-threading-specific.
"""
import os
import platform
import select
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ELF  = os.path.join(ROOT, "pythonos-arm64.elf")
DISK = os.path.join(ROOT, "build", "disk.img")


def _accel():
    if platform.machine().lower() in ("arm64", "aarch64"):
        # HVF on Apple Silicon needs GICv3, stay TCG.
        return ["-cpu", "cortex-a57"]
    return ["-cpu", "cortex-a57"]


QEMU_CMD = [
    "qemu-system-aarch64",
    "-machine", "virt",
    *_accel(),
    "-m", "2G", "-smp", "2",
    "-no-reboot", "-no-shutdown",
    "-display", "none",
    "-monitor", "none",
    "-serial", "stdio",
    "-drive", f"if=none,file={DISK},format=raw,id=hd0",
    "-device", "virtio-blk-device,drive=hd0",
    "-kernel", ELF,
]


def read_until(proc, needle, timeout, accum, search_from=0):
    deadline = time.monotonic() + timeout
    fd = proc.stdout.fileno()
    if needle in "".join(accum)[search_from:]:
        return True
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        ready, _, _ = select.select([fd], [], [], 0.5)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            return False
        if not chunk:
            return False
        accum.append(chunk.decode("utf-8", errors="replace"))
        if needle in "".join(accum)[search_from:]:
            return True
    return False


def main():
    proc = subprocess.Popen(QEMU_CMD,
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL,
                            bufsize=0)
    try:
        stream = []
        if not read_until(proc, ">>> ", 90, stream):
            sys.stderr.write("never reached prompt\n")
            sys.stderr.write("".join(stream)[-3000:])
            return 1

        def send_and_wait(expr, timeout=15.0):
            mark = sum(len(s) for s in stream)
            proc.stdin.write(expr.encode())
            proc.stdin.flush()
            read_until(proc, ">>> ", timeout, stream, search_from=mark)
            return "".join(stream)[mark:]

        probes = [
            'compile("1+1", "<t>", "exec")\n',
            'compile("def f(): pass", "<t>", "exec")\n',
            'compile("def f(): pass\\n", "<t>", "exec")\n',
            'compile("class C: pass", "<t>", "exec")\n',
            'compile("def f():\\n    return 7\\n", "<t>", "exec")\n',
            'compile("import os", "<t>", "exec")\n',
            # Repeat the def case to detect non-determinism.
            'compile("def f(): pass\\n", "<t>", "exec")\n',
            'compile("def f(): pass\\n", "<t>", "exec")\n',
        ]
        for p in probes:
            print(f"\n>>> {p.rstrip()}")
            out = send_and_wait(p)
            print(out)
        return 0
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
