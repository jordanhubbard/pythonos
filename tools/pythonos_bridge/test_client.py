#!/usr/bin/env python3
"""
Smoke test for the pythonos_bridge JSON-RPC loop.

Spawns `pythonos_bridge --listen <socket>`, connects, walks the
length-prefixed JSON protocol through hello / ping / shutdown,
and asserts on the responses.

This is the host-only proof of life. It does NOT involve QEMU; the
guest-side transport (pythonos-xaz) lands in a later slice.
"""

import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
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


def main():
    repo_root = os.path.dirname(os.path.abspath(__file__))
    binary = os.path.join(repo_root, "pythonos_bridge")
    if not os.path.isfile(binary):
        print(f"missing binary: {binary}", file=sys.stderr)
        return 1

    sock_path = os.path.join(tempfile.gettempdir(), "pythonos-bridge-test.sock")
    if os.path.exists(sock_path):
        os.unlink(sock_path)

    proc = subprocess.Popen([binary, "--listen", sock_path])
    try:
        # Wait for the server to bind.
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if os.path.exists(sock_path):
                break
            time.sleep(0.05)
        if not os.path.exists(sock_path):
            raise RuntimeError("bridge never created its listen socket")

        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(sock_path)
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

        # shutdown
        _send(s, 4, "shutdown", {})
        r = _recv(s)
        check("shutdown.ok",         r.get("ok") is True)
        s.close()

        proc.wait(timeout=3.0)
        check("server exit code 0", proc.returncode == 0,
              detail=f"rc={proc.returncode}")

        print(f"\n[bridge-smoke] {passes} passed, {fails} failed")
        return 0 if fails == 0 else 1
    finally:
        if proc.poll() is None:
            proc.terminate()
            try: proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired: proc.kill()
        if os.path.exists(sock_path):
            try: os.unlink(sock_path)
            except OSError: pass


if __name__ == "__main__":
    sys.exit(main())
