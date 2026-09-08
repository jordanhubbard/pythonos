#!/usr/bin/env python3
"""Boot the kernel and probe compile() behavior over the TCP REPL.

Single purpose: reproduce (or refute) the claim that
``compile("def f(): pass\\n", "<t>", "exec")`` fails inside the kernel.
"""
import os
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ISO = os.path.join(ROOT, "build", "pythonos.iso")
HOST_PORT = 5557


def wait_port(port, timeout, proc):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def send_recv(sock, expr, settle=1.5):
    sock.sendall(expr.encode())
    sock.settimeout(settle)
    buf = b""
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(8192)
        except socket.timeout:
            if buf:
                break
            continue
        if not chunk:
            break
        buf += chunk
        if buf.endswith(b">>> "):
            break
    return buf.decode("utf-8", errors="replace")


def main():
    serial = tempfile.NamedTemporaryFile(delete=False, suffix=".log")
    serial.close()
    DISK = os.path.join(ROOT, "build", "disk.img")
    cmd = [
        "qemu-system-x86_64",
        "-machine", "q35",
        "-cpu", "Skylake-Client", "-accel", "tcg",
        "-m", "2G", "-smp", "2",
        "-netdev", f"user,id=net0,hostfwd=tcp::{HOST_PORT}-:5000",
        "-device", "virtio-net-pci,netdev=net0",
        "-device", "intel-hda", "-device", "hda-duplex",
        "-drive", f"if=none,file={DISK},format=raw,id=hd0",
        "-device", "virtio-blk-pci,drive=hd0",
        "-no-reboot", "-no-shutdown",
        "-cdrom", ISO, "-boot", "d",
        "-nographic",
        "-serial", f"file:{serial.name}",
    ]
    print("[probe] qemu cmd:", " ".join(cmd), file=sys.stderr)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        if not wait_port(HOST_PORT, 90, proc):
            sys.stderr.write(f"REPL never came up\n")
            try:
                with open(serial.name) as f:
                    sys.stderr.write(f.read()[-6000:])
            except Exception as e:
                sys.stderr.write(f"(serial read failed: {e})\n")
            err = proc.stderr.read().decode("utf-8", errors="replace")
            sys.stderr.write("--- qemu stderr ---\n" + err[-2000:])
            return 1
        s = socket.create_connection(("127.0.0.1", HOST_PORT), timeout=5)
        # Drain banner
        banner = send_recv(s, "")
        print(f"banner: {banner!r}\n")
        probes = [
            "1+1\n",
            # Multi-line def at REPL — should now accumulate via codeop
            "def square(n):\n",
            "    return n * n\n",
            "\n",
            "square(7)\n",
            "square(9) + square(10)\n",
        ]
        for p in probes:
            print(f"\n--- send: {p.rstrip()}")
            out = send_recv(s, p)
            print(f"--- recv ({len(out)} bytes):")
            print(out)
        s.close()
        return 0
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        os.unlink(serial.name)


if __name__ == "__main__":
    sys.exit(main())
