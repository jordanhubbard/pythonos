#!/usr/bin/env python3
"""Machine-friendly remote console for a running PythonOS guest.

This deliberately speaks the public TCP REPL protocol, so it works with any
editor/agent that can run a command.  Each invocation prints only the guest's
reply (no telnet control sequences or interactive prompt) and returns a
non-zero status when the guest cannot be reached.

Examples:
  tools/pythonos_debug.py status
  tools/pythonos_debug.py eval "scheduler.tasks"
  tools/pythonos_debug.py launch pacmaze
  tools/pythonos_debug.py key right
  tools/pythonos_debug.py mouse move 240 180
  tools/pythonos_debug.py exec "import kernel.gui.input as i; print(i.pointer_position())"
"""

from __future__ import annotations

import argparse
import json
import os
import select
import socket
import subprocess
import sys
import time


PROMPT = b">>> "


class Debugger:
    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.setblocking(False)
        self.timeout = timeout
        self._read_until_prompt()  # banner

    def close(self) -> None:
        self.sock.close()

    def _read_until_prompt(self) -> str:
        data = bytearray()
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.sock], [], [],
                                        max(0, deadline - time.monotonic()))
            if not ready:
                break
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("PythonOS closed the TCP REPL connection")
            data.extend(chunk)
            if data.endswith(PROMPT):
                return data[:-len(PROMPT)].decode("utf-8", errors="replace")
        raise TimeoutError("timed out waiting for PythonOS REPL prompt")

    def execute(self, source: str) -> str:
        if "\n" in source:
            raise ValueError("use a single Python statement per debugger command")
        self.sock.sendall(source.encode("utf-8") + b"\n")
        return self._read_until_prompt()


def _status(dbg: Debugger) -> str:
    return dbg.execute("sysinfo")


def _load_session(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _qmp(path: str, command: str) -> dict:
    """Run a QMP command after its required capabilities handshake."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(4)
    s.connect(path)
    def recv() -> dict:
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                raise ConnectionError("QMP closed the control socket")
            buf += chunk
        return json.loads(buf.split(b"\n", 1)[0])
    recv()  # greeting
    s.sendall(b'{"execute":"qmp_capabilities"}\n')
    recv()
    s.sendall(json.dumps({"execute": command}).encode() + b"\n")
    try:
        return recv()
    finally:
        s.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int,
                        help="host-forwarded PythonOS REPL port (auto from debug manifest, else 5555)")
    parser.add_argument("--timeout", type=float, default=8.0)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    p_session = commands.add_parser("session", help="print native debug endpoints")
    p_session.add_argument("--session", default="build/pythonos-debug.json")
    p_serial = commands.add_parser("serial", help="print captured guest serial output")
    p_serial.add_argument("--session", default="build/pythonos-debug.json")
    p_serial.add_argument("--lines", type=int, default=120)
    p_qmp = commands.add_parser("qmp", help="control the paused/running QEMU VM")
    p_qmp.add_argument("action", choices=("status", "stop", "cont", "reset"))
    p_qmp.add_argument("--session", default="build/pythonos-debug.json")
    p_native = commands.add_parser("native", help="inspect native PythonOS/QEMU state")
    p_native.add_argument("--session", default="build/pythonos-debug.json")
    p_native.add_argument("commands", nargs=argparse.REMAINDER,
                          help="adapter commands, e.g. -- 'bt' 'info registers'")
    p_eval = commands.add_parser("eval")
    p_eval.add_argument("expression")
    p_exec = commands.add_parser("exec")
    p_exec.add_argument("statement")
    p_launch = commands.add_parser("launch")
    p_launch.add_argument("app")
    p_key = commands.add_parser("key", help="post a key event to the guest GUI")
    p_key.add_argument("key", help="esc, space, tab, left, right, up, down, or one ASCII character")
    p_key.add_argument("--up", action="store_true", help="post key-up instead of key-down")
    p_mouse = commands.add_parser("mouse", help="post a desktop-relative mouse event")
    p_mouse.add_argument("action", choices=("move", "down", "up"))
    p_mouse.add_argument("x", type=int)
    p_mouse.add_argument("y", type=int)
    p_mouse.add_argument("--button", type=int, default=1)
    p_perf = commands.add_parser("perf", help="fetch guest RTT and host bridge service metrics")
    p_perf.add_argument("--reset", action="store_true")
    p_capture = commands.add_parser("capture", help="save the host SDL desktop as a BMP")
    p_capture.add_argument("path", nargs="?", default="build/pythonos-debug.bmp")
    p_desktop = commands.add_parser("desktop", help="inspect or attach to the host desktop co-process")
    p_desktop.add_argument("action", choices=("status", "metrics", "native"))
    p_desktop.add_argument("commands", nargs=argparse.REMAINDER)
    p_exercise = commands.add_parser("exercise", help="launch and drive an app without human input")
    p_exercise.add_argument("app", help="registry app/demo/game name")
    p_exercise.add_argument("--seconds", type=float, default=1.0,
                            help="time to let the workload run before capture")
    args = parser.parse_args()

    # Native-control commands deliberately work even if the guest TCP stack
    # never came up — that is the point of this debugger plane.
    if args.command in ("session", "serial", "qmp", "native") or \
            (args.command == "desktop" and args.action != "metrics"):
        try:
            session = _load_session(getattr(args, "session", "build/pythonos-debug.json"))
            if args.command == "session":
                print(json.dumps(session, indent=2, sort_keys=True))
                return 0
            if args.command == "serial":
                with open(session["serial_log"], encoding="utf-8", errors="replace") as f:
                    print("".join(f.readlines()[-args.lines:]), end="")
                return 0
            if args.command == "qmp":
                op = {"status": "query-status", "stop": "stop",
                      "cont": "cont", "reset": "system_reset"}[args.action]
                print(json.dumps(_qmp(session["qmp"], op), indent=2, sort_keys=True))
                return 0
            if args.command == "desktop":
                desktop = session.get("desktop_co_process", {})
                if args.action == "status":
                    out = dict(desktop)
                    log_path = desktop.get("log")
                    if log_path and os.path.exists(log_path):
                        with open(log_path, encoding="utf-8", errors="replace") as f:
                            out["recent_log"] = f.readlines()[-80:]
                    print(json.dumps(out, indent=2, sort_keys=True))
                    return 0
                pid = desktop.get("pid")
                if not pid:
                    raise ValueError("desktop co-process PID unavailable")
                adapter = os.environ.get("PYTHONOS_DESKTOP_DEBUGGER",
                                         "lldb" if sys.platform == "darwin" else "gdb")
                if os.path.basename(adapter).startswith("lldb"):
                    cmd = [adapter, "-p", str(pid)]
                    for statement in args.commands:
                        if statement != "--": cmd += ["-o", statement]
                else:
                    cmd = [adapter, "-p", str(pid)]
                    for statement in args.commands:
                        if statement != "--": cmd += ["-ex", statement]
                return subprocess.call(cmd)
            # The default adapter uses the GDB remote protocol because QEMU
            # implements it, but the agent-facing command is `native` and a
            # different adapter may be selected without changing the session.
            adapter = os.environ.get("PYTHONOS_NATIVE_DEBUGGER",
                                     os.environ.get("PYTHONOS_GDB", "gdb"))
            cmd = [adapter, "-q", session["symbols"], "-ex", "set pagination off",
                   "-ex", "target remote " + session["native_remote"]]
            for statement in args.commands:
                if statement == "--":
                    continue
                cmd += ["-ex", statement]
            return subprocess.call(cmd)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            print(f"pythonos-debug: {exc}", file=sys.stderr)
            return 2

    try:
        port = args.port
        if port is None and os.path.exists("build/pythonos-debug.json"):
            port = int(_load_session("build/pythonos-debug.json")["repl"]["port"])
        dbg = Debugger(args.host, port or 5555, args.timeout)
        try:
            if args.command == "status":
                reply = _status(dbg)
            elif args.command == "eval":
                reply = dbg.execute(args.expression)
            elif args.command == "exec":
                reply = dbg.execute(args.statement)
            elif args.command == "launch":
                # repr prevents a registry name from becoming guest code.
                reply = dbg.execute("desktop(" + repr(args.app) + ")")
            elif args.command == "perf":
                reply = dbg.execute(
                    "from kernel.bridge import bridge; print(bridge.performance_snapshot(reset=%s))"
                    % bool(args.reset))
            elif args.command == "desktop":
                reply = dbg.execute(
                    "from kernel.bridge import bridge; print(bridge.call('debug.metrics', {'reset': False}))")
            elif args.command == "capture":
                reply = dbg.execute(
                    "from kernel.bridge import bridge; print(bridge.call('debug.capture', {'path': %r}))"
                    % os.path.abspath(args.path))
            elif args.command == "exercise":
                # A deterministic smoke workload suitable for an agent loop:
                # open, exercise both pointer and keyboard routing, sample
                # metrics, then ESC back to a clean desktop.
                dbg.execute("from kernel.bridge import bridge; bridge.performance_snapshot(reset=True)")
                dbg.execute("desktop(" + repr(args.app) + ")")
                time.sleep(max(0.1, args.seconds))
                # Paint's body starts at (140,142); these desktop-relative
                # strokes also validate the compositor local-coordinate path.
                for kind, x, y, code in (("MOUSE_MOVE", 180, 180, 0),
                                         ("MOUSE_DOWN", 180, 180, 1),
                                         ("MOUSE_MOVE", 260, 220, 0),
                                         ("MOUSE_UP", 260, 220, 1)):
                    dbg.execute(
                        "import kernel.gui.input as i; i.queue.post(i.Event(kind=i.%s, x=%d, y=%d, code=%d))"
                        % (kind, x, y, code))
                for key in ("KEY_RIGHT", "KEY_SPACE", "KEY_ESC"):
                    dbg.execute(
                        "import kernel.gui.input as i; i.queue.post(i.Event(kind=i.EVENT_KEY_DOWN, code=i.%s))"
                        % key)
                time.sleep(0.25)
                reply = dbg.execute(
                    "from kernel.bridge import bridge; print(bridge.performance_snapshot())")
            elif args.command == "key":
                aliases = {"esc": "KEY_ESC", "space": "KEY_SPACE", "tab": "KEY_TAB",
                           "left": "KEY_LEFT", "right": "KEY_RIGHT",
                           "up": "KEY_UP", "down": "KEY_DOWN"}
                key_name = aliases.get(args.key.lower())
                if key_name is None:
                    if len(args.key) != 1:
                        raise ValueError("key must be a named key or one ASCII character")
                    key_expr = "ord(" + repr(args.key) + ")"
                else:
                    key_expr = "i." + key_name
                kind = "EVENT_KEY_UP" if args.up else "EVENT_KEY_DOWN"
                reply = dbg.execute(
                    "import kernel.gui.input as i; i.queue.post(i.Event(kind=i.%s, code=%s))"
                    % (kind, key_expr))
            else:
                kinds = {"move": "MOUSE_MOVE", "down": "MOUSE_DOWN", "up": "MOUSE_UP"}
                extra = "" if args.action == "move" else ", code=" + str(args.button)
                reply = dbg.execute(
                    "import kernel.gui.input as i; i.queue.post(i.Event(kind=i.%s, x=%d, y=%d%s))"
                    % (kinds[args.action], args.x, args.y, extra))
            sys.stdout.write(reply)
            return 0
        finally:
            dbg.close()
    except (OSError, ConnectionError, TimeoutError, ValueError) as exc:
        print(f"pythonos-debug: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
