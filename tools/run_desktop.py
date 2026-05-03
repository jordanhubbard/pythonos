#!/usr/bin/env python3
"""
Boot PythonOS in GUI mode and auto-launch pythonos_gui so a desktop
window is visible immediately rather than after the user types the
command at the REPL.

Used by `make run-desktop-x86_64` / `make run-desktop-arm64` /
`make run-desktop`. Foregrounds QEMU; Ctrl-C terminates both QEMU and
this launcher.

x86_64: command is sent over the TCP REPL forwarded by the user-mode
        net stack (host port 5560 → guest port 5000).
arm64:  no virtio-net driver yet, so there is no TCP REPL — drive the
        PL011 serial console (QEMU `-serial stdio`) instead by piping
        the command through QEMU's stdin once the shell prompt prints.
"""

import os
import platform
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time


def _macos() -> bool:
    return platform.system() == "Darwin"


def _bridge_chardev_args(socket_path: str | None) -> list:
    """If a bridge socket is provided, return the QEMU `-chardev` + `-serial`
    flags that wire a second UART (COM2 on x86, PL011 #1 at 0x09040000 on
    arm64) to that unix socket. The bridge listens on the socket; QEMU
    connects as a client. `reconnect=2` survives bridge restarts."""
    if not socket_path:
        return []
    return [
        # QEMU 9+ uses reconnect-ms; older releases used reconnect=N (seconds).
        "-chardev", f"socket,id=br,path={socket_path},reconnect-ms=2000",
        "-serial", "chardev:br",
    ]


def _qemu_cmd_x86_64(iso: str, repl_port: int, display: str, audiodev: str,
                      bridge_socket: str | None = None) -> list:
    return [
        "qemu-system-x86_64",
        "-machine", "q35",
        "-cpu", "qemu64",
        "-m", "2G",
        "-smp", "2",
        "-netdev", f"user,id=net0,hostfwd=tcp::{repl_port}-:5000",
        "-device", "virtio-net-pci,netdev=net0",
        "-device", "intel-hda",
        "-device", "hda-duplex",
        "-no-reboot", "-no-shutdown",
        "-cdrom", iso,
        "-boot", "d",
        "-display", display,
        "-vga", "std",
        "-serial", "stdio",
    ] + _bridge_chardev_args(bridge_socket)


def _qemu_cmd_arm64(elf: str, repl_port: int, display: str, audiodev: str,
                     bridge_socket: str | None = None) -> list:
    disk = os.environ.get("PYTHONOS_ARM64_DISK", "disk-arm64.img")
    return [
        "qemu-system-aarch64",
        "-machine", "virt",
        "-cpu", "cortex-a57",
        "-m", "2G",
        "-smp", "2",
        "-no-reboot", "-no-shutdown",
        "-display", display,
        "-device", "ramfb",
        "-serial", "stdio",
        "-device", "virtio-keyboard-device",
        "-device", "virtio-tablet-device",
        "-audiodev", f"{audiodev},id=a",
        "-device", "virtio-sound-device,audiodev=a",
        "-netdev", f"user,id=net1,hostfwd=tcp::{repl_port}-:5000",
        "-device", "virtio-net-device,netdev=net1",
        "-drive", f"if=none,file={disk},format=raw,id=hd0",
        "-device", "virtio-blk-device,drive=hd0",
        "-kernel", elf,
    ] + _bridge_chardev_args(bridge_socket)


def _spawn_bridge(socket_path: str) -> subprocess.Popen:
    """Spawn pythonos_bridge --listen <socket> and wait for the listen
    socket to appear. Returns the running subprocess; caller is
    responsible for terminating it on exit."""
    bridge_bin = os.environ.get(
        "PYTHONOS_BRIDGE_BIN",
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "pythonos_bridge", "pythonos_bridge"))
    if not os.path.isfile(bridge_bin):
        raise RuntimeError(f"pythonos_bridge binary not found at {bridge_bin} "
                           "(run `make bridge`)")
    if os.path.exists(socket_path):
        try: os.unlink(socket_path)
        except OSError: pass
    print(f"[run-desktop] spawning {bridge_bin} on {socket_path}",
          file=sys.stderr)
    proc = subprocess.Popen([bridge_bin, "--listen", socket_path])
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if os.path.exists(socket_path):
            return proc
        if proc.poll() is not None:
            raise RuntimeError(
                f"pythonos_bridge exited early with rc={proc.returncode}")
        time.sleep(0.05)
    proc.terminate()
    raise RuntimeError("pythonos_bridge never created its listen socket")


def _launch_via_tcp(cmd: list, port: int, boot_cmd: str) -> int:
    """x86_64 path: connect to the forwarded TCP REPL and send the command."""
    proc = subprocess.Popen(cmd)
    deadline = time.time() + 30.0
    s = None
    while time.time() < deadline and s is None:
        try:
            s = socket.create_connection(("localhost", port), timeout=2)
        except OSError:
            time.sleep(0.5)
    if s is None:
        print("run-desktop: kernel REPL never came up", file=sys.stderr)
        proc.terminate()
        return 2

    s.settimeout(4)
    # Ping the shell with bare newlines until we see the prompt — the
    # banner can finish printing several hundred ms after the socket is
    # accepted, and a command sent before that point is silently dropped.
    for _ in range(30):
        time.sleep(0.5)
        try:
            s.sendall(b"\n")
            d = s.recv(4096)
            if b">>>" in d:
                break
        except (TimeoutError, BlockingIOError, OSError):
            continue

    try:
        s.sendall((boot_cmd + "\n").encode())
        print(f"[run-desktop] sent: {boot_cmd}", file=sys.stderr)
    finally:
        s.close()

    return _wait_proc(proc)


def _launch_via_serial(cmd: list, boot_cmd: str) -> int:
    """arm64 path: pipe the command through QEMU's stdin (PL011 serial)."""
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )

    sent = threading.Event()
    out_fd = proc.stdout.fileno()
    in_pipe = proc.stdin

    def relay() -> None:
        buf = bytearray()
        deadline = time.time() + 60.0
        while proc.poll() is None:
            r, _, _ = select.select([out_fd], [], [], 0.5)
            if out_fd in r:
                chunk = os.read(out_fd, 4096)
                if not chunk:
                    break
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
                if not sent.is_set():
                    buf.extend(chunk)
                    if b">>>" in buf:
                        try:
                            in_pipe.write((boot_cmd + "\n").encode())
                            in_pipe.flush()
                            sent.set()
                            print(f"\n[run-desktop] sent: {boot_cmd}",
                                  file=sys.stderr)
                        except OSError as e:
                            print(f"[run-desktop] write failed: {e}", file=sys.stderr)
                            return
                        buf.clear()
            elif not sent.is_set() and time.time() > deadline:
                print("[run-desktop] timed out waiting for kernel prompt",
                      file=sys.stderr)
                return

    t = threading.Thread(target=relay, daemon=True)
    t.start()
    return _wait_proc(proc)


def _wait_proc(proc: subprocess.Popen) -> int:
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            proc.kill()
        return 130
    return proc.returncode or 0


def main() -> int:
    image = sys.argv[1] if len(sys.argv) > 1 else "pythonos.iso"
    arch = os.environ.get("PYTHONOS_DESKTOP_ARCH")
    if arch is None:
        arch = "arm64" if image.endswith(".elf") else "x86_64"

    default_port = "5560" if arch == "x86_64" else "5561"
    port = int(os.environ.get("PYTHONOS_DESKTOP_PORT", default_port))
    boot_app = os.environ.get("PYTHONOS_DESKTOP_APP", "bouncing_ball")
    # PYTHONOS_DESKTOP_BOOT_CMD overrides the line we inject at the kernel
    # prompt — useful for one-shots like `bridge_ping` that don't go
    # through the compositor at all.
    boot_cmd = os.environ.get("PYTHONOS_DESKTOP_BOOT_CMD",
                               f"pythonos_gui {boot_app}")

    display  = os.environ.get("QEMU_DISPLAY",  "cocoa" if _macos() else "sdl")
    audiodev = os.environ.get("QEMU_AUDIODEV", "coreaudio" if _macos() else "sdl")

    if not os.path.exists(image):
        print(f"run-desktop: {image} not found; run `make` first", file=sys.stderr)
        return 1

    # PYTHONOS_BRIDGE_SOCKET=<path> enables the host-side companion. Empty
    # / unset = run-desktop legacy in-kernel framebuffer path. Default ON
    # if the bridge binary exists, so the bridge gets exercised by default.
    bridge_socket_env = os.environ.get("PYTHONOS_BRIDGE_SOCKET")
    if bridge_socket_env is None:
        bridge_default = os.path.join(tempfile.gettempdir(),
                                       "pythonos-bridge.sock")
        bridge_bin_default = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "pythonos_bridge", "pythonos_bridge")
        bridge_socket = bridge_default if os.path.isfile(bridge_bin_default) else None
    elif bridge_socket_env == "":
        bridge_socket = None
    else:
        bridge_socket = bridge_socket_env

    bridge_proc = None
    if bridge_socket:
        try:
            bridge_proc = _spawn_bridge(bridge_socket)
        except RuntimeError as e:
            print(f"[run-desktop] bridge unavailable: {e}", file=sys.stderr)
            print("[run-desktop] continuing without bridge "
                  "(set PYTHONOS_BRIDGE_SOCKET= to silence)", file=sys.stderr)
            bridge_socket = None

    print(f"[run-desktop] booting {image} (arch={arch}) with -display {display}"
          + (f", bridge=on ({bridge_socket})" if bridge_socket else ", bridge=off")
          + f"; will inject {boot_cmd!r} once the shell prompt is ready",
          file=sys.stderr)

    try:
        if arch == "arm64":
            cmd = _qemu_cmd_arm64(image, port, display, audiodev, bridge_socket)
            return _launch_via_serial(cmd, boot_cmd)
        cmd = _qemu_cmd_x86_64(image, port, display, audiodev, bridge_socket)
        return _launch_via_tcp(cmd, port, boot_cmd)
    finally:
        if bridge_proc is not None and bridge_proc.poll() is None:
            bridge_proc.terminate()
            try: bridge_proc.wait(timeout=2)
            except subprocess.TimeoutExpired: bridge_proc.kill()


if __name__ == "__main__":
    sys.exit(main())
