#!/usr/bin/env python3
"""
Boot PythonOS in supervised GUI mode.

Used by `make run-gui-x86_64` / `make run-gui-arm64` /
`make run-gui`. Foregrounds QEMU; Ctrl-C terminates both QEMU and
the host-side pythonos_bridge companion.

The default bridge transport is native guest TCP: PythonOS listens on a
guest TCP port, and the host pythonos_bridge process connects to it. For
debugging the older QEMU chardev path remains available with
PYTHONOS_BRIDGE_TRANSPORT=chardev.
"""

import os
import platform
import socket
import subprocess
import sys
import time
import json


def _macos() -> bool:
    return platform.system() == "Darwin"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _host_arch() -> str:
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    return machine


def _kvm_available() -> bool:
    return (platform.system() == "Linux"
            and os.path.exists("/dev/kvm")
            and os.access("/dev/kvm", os.R_OK | os.W_OK))


def _qemu_accel(arch: str) -> str:
    """Return kvm/tcg for this launch.

    ``PYTHONOS_QEMU_ACCEL`` accepts ``auto`` (default), ``kvm``, or ``tcg``.
    Auto enables KVM only where it is release-safe. arm64 KVM is currently
    opt-in because this kernel stalls before GIC init under KVM on native
    aarch64 hosts.
    """
    mode = os.environ.get("PYTHONOS_QEMU_ACCEL", "auto").strip().lower()
    if mode in ("off", "none"):
        mode = "tcg"
    if mode not in ("auto", "kvm", "tcg"):
        raise ValueError("PYTHONOS_QEMU_ACCEL must be auto, kvm, or tcg")
    if mode == "kvm" and (_host_arch() != arch or not _kvm_available()):
        raise RuntimeError(
            f"PYTHONOS_QEMU_ACCEL=kvm requested, but KVM is not usable "
            f"for {arch} on this host")
    if mode == "auto":
        if arch == "arm64":
            return "kvm" if _truthy(os.environ.get("PYTHONOS_ARM64_KVM")) \
                and _host_arch() == arch and _kvm_available() else "tcg"
        return "kvm" if _host_arch() == arch and _kvm_available() else "tcg"
    return mode


def _qemu_cpu(arch: str, accel: str, fallback: str) -> str:
    env_cpu = os.environ.get("PYTHONOS_QEMU_CPU")
    if env_cpu:
        return env_cpu
    return "host" if accel == "kvm" and _host_arch() == arch else fallback


def _bridge_bin() -> str:
    return os.environ.get(
        "PYTHONOS_BRIDGE_BIN",
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "pythonos_bridge", "pythonos_bridge"))


def _bridge_endpoint() -> tuple[str, int]:
    addr = os.environ.get("PYTHONOS_BRIDGE_ADDR")
    if addr:
        host, sep, port_s = addr.rpartition(":")
        if not sep:
            host, port_s = "127.0.0.1", addr
    else:
        host = os.environ.get("PYTHONOS_BRIDGE_HOST", "127.0.0.1")
        port_s = os.environ.get("PYTHONOS_BRIDGE_PORT", "17010")
    port = int(port_s)
    if not (0 < port < 65536):
        raise ValueError(f"invalid PYTHONOS_BRIDGE_PORT={port!r}")
    return host or "127.0.0.1", port


def _bridge_guest_port() -> int:
    port = int(os.environ.get("PYTHONOS_BRIDGE_GUEST_PORT", "5001"))
    if not (0 < port < 65536):
        raise ValueError(f"invalid PYTHONOS_BRIDGE_GUEST_PORT={port!r}")
    return port


def _bridge_transport() -> str:
    mode = (os.environ.get("PYTHONOS_BRIDGE_TRANSPORT")
            or os.environ.get("PYTHONOS_BRIDGE_MODE")
            or "native-tcp").strip().lower()
    aliases = {
        "tcp": "native-tcp",
        "native": "native-tcp",
        "guest-tcp": "native-tcp",
        "tcp-listener": "native-tcp",
        "serial": "chardev",
        "virtconsole": "chardev",
        "tcp-chardev": "chardev",
    }
    mode = aliases.get(mode, mode)
    if mode not in ("native-tcp", "chardev"):
        raise ValueError(
            "PYTHONOS_BRIDGE_TRANSPORT must be native-tcp or chardev")
    return mode


def _desktop_mode() -> str:
    mode = os.environ.get("PYTHONOS_DESKTOP_MODE", "interactive").strip().lower()
    if mode not in ("interactive", "headless"):
        raise ValueError("PYTHONOS_DESKTOP_MODE must be interactive or headless")
    return mode


def _qemu_connect_host(listen_host: str) -> str:
    return os.environ.get(
        "PYTHONOS_BRIDGE_CONNECT_HOST",
        "127.0.0.1" if listen_host in ("", "*", "0.0.0.0") else listen_host)


def _bridge_chardev_arg(endpoint: tuple[str, int] | None) -> list:
    """Return the QEMU chardev that reaches the host bridge TCP endpoint."""
    if not endpoint:
        return []
    host, port = endpoint
    return ["-chardev", f"socket,id=br,host={host},port={port},reconnect=2"]


def _bridge_uart_args(endpoint: tuple[str, int] | None) -> list:
    if not endpoint:
        return []
    return [
        *_bridge_chardev_arg(endpoint),
        "-serial", "chardev:br",
    ]


def _bridge_virtconsole_args(endpoint: tuple[str, int] | None) -> list:
    if not endpoint:
        return []
    return [
        *_bridge_chardev_arg(endpoint),
        "-device", "virtio-serial-device",
        "-device", "virtconsole,chardev=br",
    ]


def _hostfwd(host: str, host_port: int, guest_port: int) -> str:
    hostaddr = "" if host in ("", "*", "0.0.0.0") else host
    return f"hostfwd=tcp:{hostaddr}:{host_port}-:{guest_port}"


def _bridge_fw_cfg_args(mode: str, app_name: str | None = None,
                        guest_port: int | None = None) -> list:
    gui_mode = "bridge-tcp" if mode == "native-tcp" else "bridge"
    args = ["-fw_cfg", f"name=opt/pythonos/gui,string={gui_mode}"]
    if app_name:
        args += ["-fw_cfg", f"name=opt/pythonos/gui-app,string={app_name}"]
    if guest_port:
        args += ["-fw_cfg",
                 f"name=opt/pythonos/gui-bridge-port,string={guest_port}"]
    return args


def _disk_path() -> str:
    return (os.environ.get("PYTHONOS_DISK")
            or os.environ.get("PYTHONOS_ARM64_DISK")
            or "build/disk.img")


def _qemu_cmd_x86_64(iso: str, repl_port: int, display: str, audiodev: str,
                      bridge_endpoint: tuple[str, int] | None = None,
                      gui_app: str | None = None,
                      bridge_transport: str = "native-tcp",
                      bridge_listen_host: str = "127.0.0.1",
                      bridge_guest_port: int = 5001) -> list:
    # When the bridge is on, the host SDL window IS the desktop —
    # QEMU's own framebuffer console window is redundant noise.
    qdisp = "none" if bridge_endpoint else display
    disk = _disk_path()
    netdev = f"user,id=net0,hostfwd=tcp::{repl_port}-:5000"
    if bridge_endpoint and bridge_transport == "native-tcp":
        netdev += "," + _hostfwd(bridge_listen_host,
                                 bridge_endpoint[1],
                                 bridge_guest_port)
    accel = _qemu_accel("x86_64")
    cmd = [
        "qemu-system-x86_64",
        "-machine", "q35",
        "-accel", accel,
        "-cpu", _qemu_cpu("x86_64", accel, "qemu64"),
        "-m", "2G",
        "-smp", "2",
        "-netdev", netdev,
        "-device", "virtio-net-pci,netdev=net0",
        "-device", "intel-hda",
        "-device", "hda-duplex",
        "-drive", f"if=none,file={disk},format=raw,id=hd0",
        "-device", "virtio-blk-pci,drive=hd0",
        "-no-reboot", "-no-shutdown",
        "-cdrom", iso,
        "-boot", "d",
        "-display", qdisp,
        "-serial", "stdio",
    ]
    if not bridge_endpoint:
        cmd += ["-vga", "std"]    # only needed for the QEMU-native fb path
    else:
        cmd += _bridge_fw_cfg_args(bridge_transport, gui_app,
                                   bridge_guest_port)
    if bridge_endpoint and bridge_transport == "chardev":
        cmd += _bridge_uart_args(bridge_endpoint)
    return cmd


def _qemu_cmd_arm64(elf: str, repl_port: int, display: str, audiodev: str,
                     bridge_endpoint: tuple[str, int] | None = None,
                     gui_app: str | None = None,
                     bridge_transport: str = "native-tcp",
                     bridge_listen_host: str = "127.0.0.1",
                     bridge_guest_port: int = 5001) -> list:
    disk = _disk_path()
    qdisp = "none" if bridge_endpoint else display
    # Keep the agent TCP REPL available in GUI mode too.  Bridge traffic has
    # its own forwarding below; without this arm64 GUI sessions had no
    # high-level debugger endpoint at all.
    netdev = f"user,id=net1,hostfwd=tcp::{repl_port}-:5000"
    if bridge_endpoint and bridge_transport == "native-tcp":
        netdev += "," + _hostfwd(bridge_listen_host,
                                 bridge_endpoint[1],
                                 bridge_guest_port)
    accel = _qemu_accel("arm64")
    cmd = [
        "qemu-system-aarch64",
        "-machine", "virt",
        "-accel", accel,
        "-cpu", _qemu_cpu("arm64", accel, "cortex-a57"),
        "-m", "2G",
        "-smp", "2",
        "-no-reboot", "-no-shutdown",
        "-display", qdisp,
        "-serial", "stdio",
        "-audiodev", f"{audiodev},id=a",
        "-device", "virtio-sound-device,audiodev=a",
        "-netdev", netdev,
        "-device", "virtio-net-device,netdev=net1",
        "-drive", f"if=none,file={disk},format=raw,id=hd0",
        "-device", "virtio-blk-device,drive=hd0",
        "-kernel", elf,
    ]
    if not bridge_endpoint:
        # ramfb + virtio-input only matter when we're using QEMU's native
        # display path (the bridge handles input on its own SDL window).
        cmd += [
            "-device", "ramfb",
            "-device", "virtio-keyboard-device",
            "-device", "virtio-tablet-device",
        ]
    else:
        cmd += _bridge_fw_cfg_args(bridge_transport, gui_app,
                                   bridge_guest_port)
    if bridge_endpoint and bridge_transport == "chardev":
        cmd += _bridge_virtconsole_args(bridge_endpoint)
    return cmd


def _wait_tcp_listener(host: str, port: int, proc: subprocess.Popen,
                       timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"pythonos_bridge exited early with rc={proc.returncode}")
        try:
            s = socket.create_connection((host, port), timeout=0.2)
            s.close()
            return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"pythonos_bridge did not listen on {host}:{port}")


def _spawn_bridge_listen(listen_host: str, port: int,
                         log_path: str | None = None,
                         desktop_mode: str = "interactive") -> subprocess.Popen:
    """Spawn pythonos_bridge --listen-tcp and wait for the listener."""
    bridge_bin = _bridge_bin()
    if not os.path.isfile(bridge_bin):
        raise RuntimeError(f"pythonos_bridge binary not found at {bridge_bin} "
                           "(run `make bridge`)")
    endpoint = f"{listen_host}:{port}"
    print(f"[run-gui] spawning {bridge_bin} on tcp {endpoint}",
          file=sys.stderr)
    stream = open(log_path, "ab", buffering=0) if log_path else None
    env = dict(os.environ, PYTHONOS_DESKTOP_MODE=desktop_mode)
    proc = subprocess.Popen([bridge_bin, "--listen-tcp", endpoint], env=env,
                            stdout=stream, stderr=subprocess.STDOUT)
    try:
        _wait_tcp_listener(_qemu_connect_host(listen_host), port, proc)
    except Exception:
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise
    return proc


def _spawn_bridge_connect(host: str, port: int,
                          log_path: str | None = None,
                          desktop_mode: str = "interactive") -> subprocess.Popen:
    bridge_bin = _bridge_bin()
    if not os.path.isfile(bridge_bin):
        raise RuntimeError(f"pythonos_bridge binary not found at {bridge_bin} "
                           "(run `make bridge`)")
    endpoint = f"{host}:{port}"
    timeout_ms = os.environ.get("PYTHONOS_BRIDGE_CONNECT_TIMEOUT_MS", "120000")
    print(f"[run-gui] spawning {bridge_bin} connecting to tcp {endpoint}",
          file=sys.stderr)
    stream = open(log_path, "ab", buffering=0) if log_path else None
    env = dict(os.environ, PYTHONOS_DESKTOP_MODE=desktop_mode)
    return subprocess.Popen([
        bridge_bin,
        "--connect-tcp", endpoint,
        "--connect-timeout-ms", timeout_ms,
    ], env=env, stdout=stream, stderr=subprocess.STDOUT)


def _launch_qemu(cmd: list) -> int:
    return _wait_proc(subprocess.Popen(cmd))


def _debug_session(image: str, arch: str, repl_port: int) -> dict | None:
    """Create agent-debug endpoints and a durable session manifest.

    This is opt-in because serial is redirected to a file in debug mode.
    QMP is a local Unix socket and the native remote endpoint binds only to
    loopback; no privileged debugger port is exposed to the network.
    """
    if not _truthy(os.environ.get("PYTHONOS_DEBUG")):
        return None
    build = os.path.abspath(os.environ.get("PYTHONOS_DEBUG_DIR", "build"))
    os.makedirs(build, exist_ok=True)
    prefix = os.path.join(build, "pythonos-debug")
    native_port = int(os.environ.get(
        "PYTHONOS_DEBUG_NATIVE_PORT",
        os.environ.get("PYTHONOS_DEBUG_GDB_PORT", "12345")))
    if not 0 < native_port < 65536:
        raise ValueError("PYTHONOS_DEBUG_NATIVE_PORT must be a valid TCP port")
    paths = {"native_remote": "127.0.0.1:" + str(native_port), "qmp": prefix + ".qmp.sock",
             "serial_log": prefix + ".serial.log", "manifest": prefix + ".json"}
    # These are fixed, generated debug artifacts under the chosen debug dir.
    for path in (paths["qmp"], paths["serial_log"]):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
    symbols = image if arch == "arm64" else os.path.join("build", "pythonos.elf")
    session = {
        "version": 1, "arch": arch, "repl": {"host": "127.0.0.1", "port": repl_port},
        "native_remote": paths["native_remote"], "qmp": paths["qmp"], "serial_log": paths["serial_log"],
        "symbols": os.path.abspath(symbols), "image": os.path.abspath(image),
        "desktop_mode": _desktop_mode(),
        "desktop_co_process": {"pid": None, "log": prefix + ".desktop.log"},
    }
    session["manifest"] = paths["manifest"]
    _write_debug_manifest(session)
    return session


def _write_debug_manifest(session: dict) -> None:
    manifest = session["manifest"]
    payload = dict(session)
    payload.pop("manifest", None)
    temp = manifest + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(temp, manifest)


def _add_debug_qemu_args(cmd: list, session: dict) -> None:
    """Replace interactive serial with capture and add native control planes."""
    serial_at = cmd.index("-serial") + 1
    cmd[serial_at] = "file:" + session["serial_log"]
    # QEMU names this option -gdb; its wire protocol is merely the transport
    # used by the PythonOS native-debug adapter, not our public API.
    cmd += ["-gdb", "tcp:" + session["native_remote"] + ",server=on,wait=off",
            "-qmp", "unix:" + session["qmp"] + ",server=on,wait=off"]
    if _truthy(os.environ.get("PYTHONOS_DEBUG_PAUSE")):
        cmd.append("-S")


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
    image = sys.argv[1] if len(sys.argv) > 1 else "build/pythonos.iso"
    arch = os.environ.get("PYTHONOS_GUI_ARCH")
    if arch is None:
        arch = "arm64" if image.endswith(".elf") else "x86_64"

    default_port = "5560" if arch == "x86_64" else "5561"
    port = int(os.environ.get("PYTHONOS_GUI_PORT", default_port))
    gui_app = os.environ.get("PYTHONOS_GUI_APP", "").strip() or None
    desktop_mode = _desktop_mode()

    display  = os.environ.get("QEMU_DISPLAY",  "cocoa" if _macos() else "sdl")
    audiodev = os.environ.get("QEMU_AUDIODEV", "coreaudio" if _macos() else "sdl")

    if not os.path.exists(image):
        print(f"run-gui: {image} not found; run `make` first", file=sys.stderr)
        return 1
    debug_session = _debug_session(image, arch, port)

    # Bridge mode is the normal run-gui path. Set PYTHONOS_BRIDGE=0
    # (or the old PYTHONOS_BRIDGE_SOCKET= compatibility knob to empty)
    # to fall back to QEMU's native framebuffer path.
    bridge_disabled = (
        _truthy(os.environ.get("PYTHONOS_BRIDGE_DISABLE"))
        or os.environ.get("PYTHONOS_BRIDGE") == "0"
        or os.environ.get("PYTHONOS_BRIDGE_SOCKET") == ""
    )
    if bridge_disabled:
        bridge_endpoint = None
        listen_host = ""
        listen_port = 0
        bridge_transport = "off"
        guest_bridge_port = 0
    else:
        bridge_transport = _bridge_transport()
        guest_bridge_port = _bridge_guest_port()
        listen_host, listen_port = _bridge_endpoint()
        bridge_endpoint = (_qemu_connect_host(listen_host), listen_port)

    bridge_proc = None
    if bridge_endpoint and not _truthy(os.environ.get("PYTHONOS_BRIDGE_EXTERNAL")):
        try:
            if bridge_transport == "native-tcp":
                bridge_proc = _spawn_bridge_connect(bridge_endpoint[0],
                                                    bridge_endpoint[1],
                                                    debug_session["desktop_co_process"]["log"]
                                                    if debug_session else None,
                                                    desktop_mode)
            else:
                bridge_proc = _spawn_bridge_listen(
                    listen_host, listen_port,
                    debug_session["desktop_co_process"]["log"]
                    if debug_session else None, desktop_mode)
            if debug_session:
                debug_session["desktop_co_process"]["pid"] = bridge_proc.pid
                _write_debug_manifest(debug_session)
        except RuntimeError as e:
            print(f"[run-gui] bridge unavailable: {e}", file=sys.stderr)
            return 2

    print(f"[run-gui] booting {image} (arch={arch}) with -display {display}"
          + f", accel={_qemu_accel(arch)}"
          + (f", bridge={bridge_transport}://{bridge_endpoint[0]}:{bridge_endpoint[1]}"
             if bridge_endpoint else ", bridge=off")
          + (f", app={gui_app}" if gui_app else "")
          + f", desktop={desktop_mode}"
          + (f"; guest listens on :{guest_bridge_port}"
             if bridge_endpoint and bridge_transport == "native-tcp" else "")
          + ("; guest auto-starts desktop via fw_cfg"
             if bridge_endpoint else "; legacy framebuffer path"),
          file=sys.stderr)
    if debug_session:
        print("[run-gui] debug manifest: " + debug_session["manifest"],
              file=sys.stderr)

    try:
        if arch == "arm64":
            cmd = _qemu_cmd_arm64(image, port, display, audiodev,
                                  bridge_endpoint, gui_app,
                                  bridge_transport, listen_host,
                                  guest_bridge_port)
            if debug_session:
                _add_debug_qemu_args(cmd, debug_session)
            return _launch_qemu(cmd)
        cmd = _qemu_cmd_x86_64(image, port, display, audiodev,
                               bridge_endpoint, gui_app,
                               bridge_transport, listen_host,
                               guest_bridge_port)
        if debug_session:
            _add_debug_qemu_args(cmd, debug_session)
        return _launch_qemu(cmd)
    finally:
        if bridge_proc is not None and bridge_proc.poll() is None:
            bridge_proc.terminate()
            try: bridge_proc.wait(timeout=2)
            except subprocess.TimeoutExpired: bridge_proc.kill()


if __name__ == "__main__":
    sys.exit(main())
