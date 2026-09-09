#!/usr/bin/env python3
"""
Smoke test for the pythonos_bridge JSON-RPC loop.

Spawns `pythonos_bridge --listen-tcp 127.0.0.1:<port>`, connects, walks the
length-prefixed JSON protocol, then verifies `--connect-tcp` by having the
bridge connect back to a Python-owned listener.

This is the host-only proof of life. It does NOT involve QEMU; QEMU/guest
coverage lives in the run-gui and GUI smoke targets.
"""

import json
import os
import socket
import struct
import subprocess
import sys
import time


def _send(sock, frame_id, op, params=None):
    payload = json.dumps({
        "v": 1, "id": frame_id, "op": op, "params": params or {},
    }).encode("utf-8")
    sock.sendall(struct.pack(">I", len(payload)) + payload)


def _recv(sock):
    hdr = b""
    while len(hdr) < 4:
        chunk = sock.recv(4 - len(hdr))
        if not chunk:
            raise EOFError("peer closed during length read")
        hdr += chunk
    (length,) = struct.unpack(">I", hdr)
    payload = b""
    while len(payload) < length:
        chunk = sock.recv(length - len(payload))
        if not chunk:
            raise EOFError("peer closed during payload read")
        payload += chunk
    return json.loads(payload.decode("utf-8"))


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def main():
    repo_root = os.path.dirname(os.path.abspath(__file__))
    binary = os.path.join(repo_root, "pythonos_bridge")
    if not os.path.isfile(binary):
        print(f"missing binary: {binary}", file=sys.stderr)
        return 1

    port = _free_port()
    endpoint = f"127.0.0.1:{port}"

    proc = subprocess.Popen([binary, "--listen-tcp", endpoint])
    try:
        # Wait for the server to bind.
        deadline = time.time() + 3.0
        s = None
        while time.time() < deadline:
            try:
                s = socket.create_connection(("127.0.0.1", port), timeout=0.2)
                break
            except OSError:
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"bridge exited early with rc={proc.returncode}")
            time.sleep(0.05)
        if s is None:
            raise RuntimeError("bridge never opened its TCP listener")
        s.settimeout(3.0)

        passes, fails = 0, 0
        def check(name, ok, detail=""):
            nonlocal passes, fails
            tag = "PASS" if ok else "FAIL"
            print(f"[{tag}] {name}{(' — ' + detail) if detail else ''}")
            if ok: passes += 1
            else:  fails  += 1
            return ok

        # hello
        _send(s, 1, "hello", {"protocol": 1, "privileged": False})
        r = _recv(s)
        check("hello.id == 1",      r.get("id") == 1)
        check("hello.ok",            r.get("ok") is True)
        check("hello protocol == 1", r.get("result", {}).get("protocol") == 1)
        check("hello agent set",
              isinstance(r.get("result", {}).get("agent"), str)
              and r["result"]["agent"] == "pythonos_bridge")

        # ping with tag
        _send(s, 2, "ping", {"tag": "abc"})
        r = _recv(s)
        check("ping.id == 2",       r.get("id") == 2)
        check("ping.ok",             r.get("ok") is True)
        check("ping echo tag",       r.get("result", {}).get("tag") == "abc")
        check("ping.pong == ok",     r.get("result", {}).get("pong") == "ok")

        # Performance introspection is part of the agent-debug contract:
        # it separates co-process service time from guest-observed RTT.
        _send(s, 4, "debug.metrics", {"reset": False})
        r = _recv(s)
        metrics = r.get("result", {})
        check("debug.metrics.ok", r.get("ok") is True)
        check("debug.metrics frequency", isinstance(metrics.get("frequency_hz"), int)
              and metrics["frequency_hz"] > 0)
        check("debug.metrics records ping", metrics.get("ops", {}).get("ping", {})
              .get("count", 0) >= 1)

        # unknown op error
        _send(s, 3, "this_op_does_not_exist", {})
        r = _recv(s)
        check("unknown.id == 3",     r.get("id") == 3)
        check("unknown.ok == false", r.get("ok") is False)
        check("unknown.error.code",  r.get("error", {}).get("code") == 3)

        # sdl.call → SDL_GetTicks: tiny zero-arg SDL function. Proves the
        # mirror-SDL dispatcher works (registry lookup + arg unpack + return).
        _send(s, 100, "sdl.call", {"name": "SDL_GetTicks", "args": []})
        r = _recv(s)
        check("sdl.call.ok",                 r.get("ok") is True)
        check("sdl.call SDL_GetTicks rc int",
              isinstance(r.get("result", {}).get("rc"), int))

        # sdl.call → unknown SDL function: should error cleanly.
        _send(s, 101, "sdl.call",
              {"name": "SDL_FunctionThatDoesNotExist", "args": []})
        r = _recv(s)
        check("sdl.call unknown == error",   r.get("ok") is False)
        check("sdl.call unknown.error.code", r.get("error", {}).get("code") == 5)

        # SDL_ttf round-trip: init, find a system font, open it, render
        # a string, measure it, close it. Validates the font handle kind
        # in the unified handle table and the TTF_RenderUTF8_Blended path.
        _send(s, 110, "sdl.call", {"name": "TTF_Init", "args": []})
        r = _recv(s)
        check("TTF_Init.ok",          r.get("ok") is True)
        check("TTF_Init rc == 0",     r.get("result", {}).get("rc") == 0)

        _send(s, 111, "sdl.call",
              {"name": "pyo.default_font_path", "args": []})
        r = _recv(s)
        check("pyo.default_font_path.ok", r.get("ok") is True)
        font_path = r.get("result", {}).get("path", "")
        check("pyo.default_font_path is str",
              isinstance(font_path, str) and len(font_path) > 0)

        _send(s, 112, "sdl.call",
              {"name": "TTF_OpenFont", "args": [font_path, 14]})
        r = _recv(s)
        check("TTF_OpenFont.ok",      r.get("ok") is True)
        font_handle = int(r.get("result", {}).get("handle", 0))
        check("TTF_OpenFont handle != 0", font_handle != 0)

        _send(s, 113, "sdl.call",
              {"name": "TTF_SizeUTF8", "args": [font_handle, "PythonOS"]})
        r = _recv(s)
        check("TTF_SizeUTF8.ok",      r.get("ok") is True)
        size_w = r.get("result", {}).get("w", 0)
        check("TTF_SizeUTF8 width > 0", isinstance(size_w, int) and size_w > 0)

        _send(s, 114, "sdl.call",
              {"name": "TTF_RenderUTF8_Blended",
               "args": [font_handle, "Hello", 0xFFFFFFFF]})
        r = _recv(s)
        check("TTF_RenderUTF8_Blended.ok", r.get("ok") is True)
        text_surf = int(r.get("result", {}).get("handle", 0))
        check("TTF_RenderUTF8_Blended handle != 0", text_surf != 0)

        # Free the rendered surface via the existing surface.destroy op.
        _send(s, 115, "surface.destroy", {"handle": text_surf})
        r = _recv(s)
        check("surface.destroy of TTF surface", r.get("ok") is True)

        _send(s, 116, "sdl.call",
              {"name": "TTF_CloseFont", "args": [font_handle]})
        r = _recv(s)
        check("TTF_CloseFont.ok", r.get("ok") is True)

        # Now that the font handle is closed, rendering with it should
        # fail with "invalid font handle" (code 7).
        _send(s, 117, "sdl.call",
              {"name": "TTF_RenderUTF8_Blended",
               "args": [font_handle, "after-close", 0xFFFFFFFF]})
        r = _recv(s)
        check("render after close == err",
              r.get("ok") is False
              and r.get("error", {}).get("code") == 7)

        _send(s, 118, "sdl.call", {"name": "TTF_Quit", "args": []})
        r = _recv(s)
        check("TTF_Quit.ok", r.get("ok") is True)

        # shutdown
        _send(s, 4, "shutdown", {})
        r = _recv(s)
        check("shutdown.ok",         r.get("ok") is True)
        s.close()

        proc.wait(timeout=3.0)
        check("server exit code 0", proc.returncode == 0,
              detail=f"rc={proc.returncode}")

        # Native guest TCP mode uses the opposite connection direction:
        # PythonOS listens, and the host bridge connects to it. Emulate the
        # guest listener here and run a compact protocol pass over the
        # accepted socket.
        cport = _free_port()
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", cport))
        srv.listen(1)
        srv.settimeout(3.0)
        proc2 = subprocess.Popen([
            binary, "--connect-tcp", f"127.0.0.1:{cport}",
            "--connect-timeout-ms", "3000",
        ])
        s2 = None
        try:
            s2, _ = srv.accept()
            s2.settimeout(3.0)
            _send(s2, 200, "hello", {"protocol": 1})
            r = _recv(s2)
            check("connect-tcp hello.ok", r.get("ok") is True)
            check("connect-tcp hello agent",
                  r.get("result", {}).get("agent") == "pythonos_bridge")

            _send(s2, 201, "ping", {"tag": "connect"})
            r = _recv(s2)
            check("connect-tcp ping tag",
                  r.get("result", {}).get("tag") == "connect")

            _send(s2, 202, "shutdown", {})
            r = _recv(s2)
            check("connect-tcp shutdown.ok", r.get("ok") is True)
            s2.close()
            proc2.wait(timeout=3.0)
            check("connect-tcp exit code 0", proc2.returncode == 0,
                  detail=f"rc={proc2.returncode}")
        finally:
            if s2 is not None:
                try: s2.close()
                except OSError: pass
            srv.close()
            if proc2.poll() is None:
                proc2.terminate()
                try: proc2.wait(timeout=2.0)
                except subprocess.TimeoutExpired: proc2.kill()

        print(f"\n[bridge-smoke] {passes} passed, {fails} failed")
        return 0 if fails == 0 else 1
    finally:
        if proc.poll() is None:
            proc.terminate()
            try: proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired: proc.kill()


if __name__ == "__main__":
    sys.exit(main())
