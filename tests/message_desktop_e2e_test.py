#!/usr/bin/env python3
"""Black-box standalone desktop tests. Only subprocesses, public TCP, and BMPs.

Requires the built RemoteOS-SDL service, ffmpeg, and Ruby. No imports from the
desktop implementation, no embedded Service, and no fake renderer are used.
"""

import base64
import io
import json
import math
import os
from pathlib import Path
import selectors
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import wave

ROOT = Path(__file__).resolve().parent.parent


def line_with_timeout(pipe, timeout=10):
    with selectors.DefaultSelector() as selector:
        selector.register(pipe, selectors.EVENT_READ)
        if not selector.select(timeout):
            raise AssertionError("process did not report readiness")
        return pipe.readline()


class Peer:
    def __init__(self, port, name="python-e2e"):
        self.socket = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.serial = self.revision = 0
        self.ops = []
        self.features = self.call("hello", client=name)["features"]

    def exact(self, count):
        data = b""
        while len(data) < count:
            part = self.socket.recv(count - len(data))
            if not part:
                raise EOFError("desktop closed connection")
            data += part
        return data

    def call(self, op, error=False, **params):
        self.serial += 1
        self.ops.append(op)
        data = json.dumps(dict(v=1, id=self.serial, op=op, params=params), allow_nan=False).encode()
        self.socket.sendall(struct.pack("!I", len(data)) + data)
        size, = struct.unpack("!I", self.exact(4))
        assert 0 < size <= 65536, size
        reply = json.loads(self.exact(size))
        assert reply["v"] == 1 and reply["id"] == self.serial, reply
        assert reply["ok"] is not error, reply
        return reply.get("error") if error else reply["result"]

    def commit(self, *commands, error=False):
        result = self.call("commit", revision=self.revision + 1, commands=list(commands), error=error)
        if not error:
            self.revision = result["revision"]
        return result

    def inspect(self):
        return self.call("inspect")

    def upload(self, rid, kind, data):
        self.call("resource.create", resource=rid, kind=kind, bytes=len(data))
        for offset in range(0, len(data), 24576):
            self.call("resource.append", resource=rid, offset=offset,
                      data=base64.b64encode(data[offset:offset + 24576]).decode())
        return self.call("resource.finish", resource=rid)

    def close(self):
        self.socket.close()


class Standalone:
    def __init__(self, directory, automation=True):
        self.directory = Path(directory)
        self.log = open(self.directory / "service.log", "wb")
        command = [sys.executable, "-u", str(ROOT / "tools/message_desktop.py"),
                   "--headless", "--port", "0"]
        if automation:
            command += ["--automation-dir", str(self.directory)]
        self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=self.log,
                                        start_new_session=True, env=dict(os.environ,
                                        SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy",
                                        REMOTEOS_3D_BACKEND="software"))
        try:
            line = line_with_timeout(self.process.stdout).decode().strip()
            assert line.startswith("Message desktop listening on 127.0.0.1:"), line
            self.port = int(line.rsplit(":", 1)[1])
        except BaseException:
            self.stop()
            raise

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
        try:
            code = self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=5)
            raise AssertionError("desktop or its backend failed to shut down")
        finally:
            self.process.stdout.close()
            self.log.close()
        assert code == 0, (code, (self.directory / "service.log").read_text())
        try:
            os.killpg(self.process.pid, 0)
        except ProcessLookupError:
            return
        os.killpg(self.process.pid, signal.SIGKILL)
        raise AssertionError("desktop left an orphaned backend")


def create(vid="app", **properties):
    return dict(op="create_view", view=vid, title=vid, properties=properties)


def node(nid, kind, view="app", **properties):
    return dict(op="create_node", view=view, id=nid, kind=kind, **properties)


class DesktopE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for tool in ("ffmpeg", "ruby"):
            if not shutil.which(tool):
                raise AssertionError(tool + " is required for the full standalone contract suite")
        if not (ROOT / "services/remoteos-sdl/remoteos-sdl").exists():
            raise AssertionError("run make bridge first")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="desktop-e2e-")
        self.addCleanup(self.temp.cleanup)
        artifacts = os.environ.get("MESSAGE_DESKTOP_E2E_ARTIFACTS")
        if artifacts:
            destination = Path(artifacts) / (self._testMethodName + "-" + Path(self.temp.name).name)
            self.addCleanup(lambda: shutil.copytree(self.temp.name, destination, dirs_exist_ok=True))
        self.service = Standalone(self.temp.name)
        self.addCleanup(self.service.stop)
        self.control = self.peer("automation")

    def peer(self, name="python-e2e"):
        peer = Peer(self.service.port, name)
        self.addCleanup(peer.close)
        return peer

    def wait(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            self.assertIsNone(self.service.process.poll(), "desktop exited unexpectedly")
            time.sleep(0.02)
        self.fail("condition did not become true before timeout")

    def input(self, **fields):
        self.control.call("debug.input", **fields)

    def click(self, x, y):
        self.input(kind=4, button=1, x=x, y=y)
        self.input(kind=5, button=1, x=x, y=y)
        self.control.call("sync")

    def capture(self):
        name = self.control.call("debug.capture")["file"]
        data = (Path(self.temp.name) / name).read_bytes()
        self.assertEqual(data[:2], b"BM")
        offset, = struct.unpack_from("<I", data, 10)
        width, height = struct.unpack_from("<ii", data, 18)
        bits, = struct.unpack_from("<H", data, 28)
        self.assertEqual((width, abs(height)), (1000, 720))
        self.assertIn(bits, (24, 32))
        stride = ((width * bits + 31) // 32) * 4

        def pixel(x, y):
            row = height - 1 - y if height > 0 else y
            start = offset + row * stride + x * (bits // 8)
            blue, green, red = data[start:start + 3]
            return red, green, blue
        return data, pixel

    def test_window_lifecycle_geometry_stacking_visibility_and_host_drag_resize(self):
        a, b = self.peer(), self.peer()
        a.commit(create(), node("label", "text", text="Window A"))
        b.commit(create(), node("button", "button", text="Window B"))
        self.assertFalse(a.inspect()["views"]["app"]["focused"])
        self.assertTrue(b.inspect()["views"]["app"]["focused"])
        a.commit(dict(op="configure_view", view="app", properties=dict(title="Renamed", x=20, y=60, w=700, h=520)),
                 dict(op="focus_view", view="app"))
        v = a.inspect()["views"]["app"]
        self.assertEqual((v["title"], v["x"], v["y"], v["w"], v["h"]), ("Renamed", 20, 60, 700, 520))
        self.assertEqual(v["nodes"]["label"]["rect"][2], 660)
        self.assertEqual(self.capture()[1](22, 62), (0x36, 0x51, 0x6D))
        before = a.inspect()
        a.commit(dict(op="set_text", view="app", id="label", text="Must roll back"),
                 dict(op="configure_view", view="app", properties=dict(w=2000)), error=True)
        self.assertEqual(a.inspect(), before)
        a.commit(dict(op="lower_view", view="app"))
        self.assertTrue(b.inspect()["views"]["app"]["focused"])
        a.commit(dict(op="configure_view", view="app", properties=dict(state="maximized")),
                 dict(op="focus_view", view="app"))
        v = a.inspect()["views"]["app"]
        self.assertEqual([v[k] for k in ("x", "y", "w", "h")], [0, 44, 1000, 676])
        a.commit(dict(op="configure_view", view="app", properties=dict(state="normal")))
        self.assertEqual(a.inspect()["views"]["app"]["w"], 700)
        a.commit(dict(op="configure_view", view="app", properties=dict(state="minimized")))
        self.assertTrue(b.inspect()["views"]["app"]["focused"])
        self.assertEqual(self.capture()[1](22, 62), (0x11, 0x19, 0x23))
        a.commit(dict(op="configure_view", view="app", properties=dict(state="normal", visible=False)))
        a.commit(dict(op="focus_view", view="app"), error=True)
        a.commit(dict(op="configure_view", view="app", properties=dict(visible=True)),
                 dict(op="focus_view", view="app"))
        # No commands from A during interactive drag/resize; host updates its geometry.
        self.input(kind=4, button=1, x=60, y=75)
        self.input(kind=3, x=100, y=105)
        self.input(kind=5, button=1, x=100, y=105)
        self.control.call("sync")
        v = a.inspect()["views"]["app"]
        self.assertEqual((v["x"], v["y"]), (60, 90))
        self.input(kind=4, button=1, x=758, y=608)
        self.input(kind=3, x=799, y=639)
        self.input(kind=5, button=1, x=799, y=639)
        self.control.call("sync")
        v = a.inspect()["views"]["app"]
        self.assertEqual((v["w"], v["h"]), (740, 550))
        self.assertEqual(len([e for e in a.call("poll")["events"] if e["event"] == "configured"]), 2)
        self.click(v["x"] + v["w"] - 20, v["y"] + 15)
        self.assertEqual(a.inspect()["views"], {})
        self.assertEqual(a.call("poll")["events"], [dict(event="closed", view="app", revision=a.revision)])
        self.assertEqual(a.call("poll")["events"], [])
        b.commit(dict(op="destroy_view", view="app"))
        self.assertEqual(b.call("poll")["events"], [])

    def test_controls_are_semantic_and_operate_while_application_is_idle(self):
        app = self.peer()
        app.commit(create(), node("go", "button", text="Go"), node("check", "checkbox", text="Enabled"),
                   node("name", "entry"), node("level", "slider", text="Level"),
                   node("disabled", "button", text="Disabled", enabled=False))
        nodes = app.inspect()["views"]["app"]["nodes"]
        before = self.capture()[0]
        def click_node(nid, fraction=0.5):
            x, y, w, h = nodes[nid]["rect"]
            self.click(x + int(w * fraction), y + h // 2)
        click_node("go")
        click_node("check")
        click_node("name")
        for char in "Ruby + Python":
            self.input(kind=1, code=ord(char), text=char)
        self.input(kind=1, code=8)
        click_node("level")
        self.input(kind=1, code=0x4000004F)
        click_node("disabled")
        self.control.call("sync")
        state = app.inspect()["views"]["app"]["nodes"]
        self.assertTrue(state["check"]["value"])
        self.assertEqual(state["name"]["value"], "Ruby + Pytho")
        self.assertEqual(state["level"]["value"], 51)
        self.assertNotEqual(self.capture()[0], before)
        events = app.call("poll")["events"]
        self.assertEqual([e["target"] for e in events if e["event"] == "activate"], ["go"])
        self.assertTrue(any(e["event"] == "change" and e["target"] == "name" and e["value"] == "Ruby + Pytho" for e in events))
        app.commit(dict(op="set_enabled", view="app", id="disabled", enabled=True))
        click_node("disabled")
        self.assertEqual(app.call("poll")["events"][0]["target"], "disabled")
        self.input(kind=1, code=9, mod=1)  # Shift-Tab moves to the previous enabled control.
        self.control.call("sync")
        self.assertTrue(app.inspect()["views"]["app"]["nodes"]["level"]["focused"])
        # Press inside/release outside must not invoke the button.
        x, y, _, _ = nodes["go"]["rect"]
        self.input(kind=4, button=1, x=x + 10, y=y + 10)
        self.input(kind=5, button=1, x=999, y=719)
        self.control.call("sync")
        self.assertEqual(app.call("poll")["events"], [])

    def test_retained_scene_uses_depth_rendering_and_no_pixel_uploads(self):
        app = self.peer()
        self.assertIn("scene", app.features)
        def triangle(z, color):
            return [[-.8, -.8, z, 1, *color], [.8, -.8, z, 1, *color], [0, .8, z, 1, *color]]
        near, far = triangle(-.5, [0, 1, 0]), triangle(.5, [1, 0, 0])
        app.commit(create(), node("scene", "scene", vertices=near + far))
        x, y, w, h = app.inspect()["views"]["app"]["nodes"]["scene"]["rect"]
        self.assertEqual(self.capture()[1](x + w // 2, y + h // 2), (0, 255, 0))
        stats = self.control.call("debug.stats")["backend"]["ops"]
        initial = stats["scene3d.render"]["count"]
        app.call("sync")
        app.call("sync")
        self.assertEqual(self.control.call("debug.stats")["backend"]["ops"]["scene3d.render"]["count"], initial)
        app.commit(dict(op="set_scene", view="app", id="scene", vertices=far + near))
        self.assertEqual(self.capture()[1](x + w // 2, y + h // 2), (0, 255, 0))
        app.commit(dict(op="set_scene", view="app", id="scene", vertices=far))
        self.assertEqual(self.capture()[1](x + w // 2, y + h // 2), (255, 0, 0))
        app.commit(dict(op="configure_view", view="app", properties=dict(w=760)))
        app.call("sync")
        self.assertEqual(self.control.call("debug.stats")["surfaces"], 1)
        self.assertNotIn("surface.upload", self.control.call("debug.stats")["backend"]["ops"])
        app.close()
        self.wait(lambda: self.control.call("debug.stats")["surfaces"] == 0)

    def test_video_asset_decodes_plays_seeks_and_finishes_without_client_frames(self):
        app = self.peer()
        clip = Path(self.temp.name) / "clip.mkv"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=64x48:r=10:d=1",
                        "-f", "lavfi", "-i", "color=blue:s=64x48:r=10:d=1",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
                        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a",
                        "-c:v", "mpeg4", "-c:a", "pcm_s16le", str(clip)], check=True, timeout=15)
        app.upload("movie", "video", clip.read_bytes())
        app.commit(create(), node("movie", "video", resource="movie"))
        x, y, w, h = app.inspect()["views"]["app"]["nodes"]["movie"]["rect"]
        app.call("media.control", resource="movie", action="play")
        app.commit(dict(op="configure_view", view="app", properties=dict(w=760)))
        app.call("sync")
        w = app.inspect()["views"]["app"]["nodes"]["movie"]["rect"][2]
        requests = len(app.ops)
        time.sleep(.25)  # No application messages; host drives decoding/audio clock/presentation.
        self.assertEqual(len(app.ops), requests)
        pixel = self.capture()[1](x + w // 2, y + h // 2)
        self.assertGreater(pixel[0], 230)
        self.assertLess(pixel[2], 20)
        self.assertGreater(app.call("media.status", resource="movie")["seconds"], .1)
        app.call("media.control", resource="movie", action="pause")
        app.call("sync")
        paused = app.call("media.status", resource="movie")["seconds"]
        time.sleep(.15)
        self.assertEqual(app.call("media.status", resource="movie")["seconds"], paused)
        app.call("media.control", resource="movie", action="seek", seconds=1.3)
        pixel = self.capture()[1](x + w // 2, y + h // 2)
        self.assertGreater(pixel[2], 230)
        self.assertLess(pixel[0], 20)
        app.call("resource.release", resource="movie", error=True)
        app.call("media.control", resource="movie", action="play")
        self.wait(lambda: app.call("media.status", resource="movie")["eof"])
        self.assertEqual([e["resource"] for e in app.call("poll")["events"] if e["event"] == "media_ended"], ["movie"])
        self.assertEqual(app.ops.count("resource.finish"), 1)
        self.assertEqual(self.control.call("debug.stats")["backend"]["ops"]["video.open"]["count"], 1)
        app.commit(dict(op="destroy_view", view="app"))
        app.call("resource.release", resource="movie")
        app.call("sync")
        stats = self.control.call("debug.stats")
        self.assertEqual((stats["resources"], stats["surfaces"]), (0, 0))

    def test_audio_upload_once_pause_resume_ownership_and_disconnect_cleanup(self):
        app, other = self.peer(), self.peer()
        stream = io.BytesIO()
        with wave.open(stream, "wb") as wav:
            wav.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
            wav.writeframes(b"".join(struct.pack("<hh", *([int(2000 * math.sin(i * 2 * math.pi * 440 / 48000))] * 2)) for i in range(48000)))
        app.upload("sound", "audio", stream.getvalue())
        other.call("media.control", resource="sound", action="play", error=True)
        other.upload("sound", "audio", stream.getvalue())
        app.call("media.control", resource="sound", action="play")
        other.call("media.control", resource="sound", action="play", error=True)
        time.sleep(.15)
        self.assertGreater(app.call("media.status", resource="sound")["seconds"], .05)
        paused = app.call("media.control", resource="sound", action="pause")["seconds"]
        time.sleep(.1)
        self.assertEqual(app.call("media.status", resource="sound")["seconds"], paused)
        app.call("media.control", resource="sound", action="play")
        self.wait(lambda: app.call("media.status", resource="sound")["seconds"] > paused + .05)
        app.call("media.control", resource="sound", action="stop")
        self.assertEqual(app.call("media.status", resource="sound")["seconds"], 0)
        other.call("media.control", resource="sound", action="play")
        other.close()
        self.wait(lambda: self.control.call("debug.stats")["resources"] == 1)
        app.call("media.control", resource="sound", action="play")
        self.wait(lambda: app.call("media.status", resource="sound")["eof"])
        self.assertEqual([e["resource"] for e in app.call("poll")["events"] if e["event"] == "media_ended"], ["sound"])
        self.assertEqual(app.call("media.control", resource="sound", action="play")["seconds"], 0)
        app.call("media.control", resource="sound", action="stop")
        app.call("resource.release", resource="sound")
        self.assertEqual(self.control.call("debug.stats")["resources"], 0)

    def test_independent_ruby_and_python_clients_share_desktop_not_namespaces(self):
        app = self.peer()
        app.commit(create("shared"), node("action", "button", view="shared", text="Python action"))
        ruby = subprocess.Popen(["ruby", str(ROOT / "tests/fixtures/message_desktop_client.rb"), str(self.service.port)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            result = json.loads(line_with_timeout(ruby.stdout))
            self.assertEqual(result["state"]["views"]["shared"]["title"], "Ruby application")
            self.assertFalse(app.inspect()["views"]["shared"]["focused"])
            x, y, w, h = result["state"]["views"]["shared"]["nodes"]["action"]["rect"]
            self.click(x + w // 2, y + h // 2)
            self.assertEqual(app.call("poll")["events"], [])
            ruby.stdin.write(b"poll\n")
            ruby.stdin.flush()
            events = json.loads(line_with_timeout(ruby.stdout))["events"]
            self.assertEqual(events, [dict(event="activate", view="shared", target="action", revision=1)])
            self.assertEqual(ruby.wait(timeout=5), 0, ruby.stderr.read().decode())
            self.wait(lambda: app.inspect()["views"]["shared"]["focused"])
        finally:
            if ruby.poll() is None:
                ruby.kill()
                ruby.wait(timeout=5)
            for pipe in (ruby.stdin, ruby.stdout, ruby.stderr):
                pipe.close()

    def test_malformed_stream_upload_limits_disconnect_and_fresh_reconnect(self):
        app = self.peer()
        app.commit(create())
        app.call("surface.upload", error=True)
        app.call("sdl.call", name="SDL_RenderPresent", args=[], error=True)
        app.call("resource.create", resource="partial", kind="video", bytes=10)
        app.call("resource.append", resource="partial", offset=1, data="eA==", error=True)
        app.call("resource.append", resource="partial", offset=0, data="not base64!", error=True)
        app.call("resource.finish", resource="partial", error=True)
        app.call("resource.append", resource="partial", offset=0, data=base64.b64encode(b"not video!").decode())
        app.call("resource.finish", resource="partial", error=True)
        app.call("resource.create", resource="huge", kind="audio", bytes=8 * 1024 * 1024, error=True)
        app.call("debug.capture", path="/tmp/unsafe.bmp", error=True)
        bad = self.peer()
        bad.socket.sendall(struct.pack("!I", 65537))
        self.assertEqual(bad.socket.recv(1), b"")
        app.close()
        self.wait(lambda: self.control.call("debug.stats")["resources"] == 0)
        new = self.peer()
        self.assertEqual(new.inspect()["revision"], 0)
        new.commit(create())
        self.assertEqual(len(new.inspect()["views"]), 1)
        # The example client runs as an independent process as well.
        subprocess.run([sys.executable, str(ROOT / "tools/message_desktop.py"), "--client",
                        "--port", str(self.service.port), "--frames", "4"], check=True, timeout=10)
        self.wait(lambda: self.control.call("debug.stats")["views"] == 1)

    def test_slow_event_consumer_is_disconnected_without_affecting_other_sessions(self):
        app = self.peer()
        app.commit(create(), node("go", "button", text="Go"))
        x, y, _, _ = app.inspect()["views"]["app"]["nodes"]["go"]["rect"]
        for _ in range(65):
            self.click(x + 10, y + 10)
        self.assertEqual(app.socket.recv(1), b"")
        self.wait(lambda: self.control.call("debug.stats")["views"] == 0)
        replacement = self.peer()
        replacement.commit(create())
        replacement.call("sync")

    def test_host_quit_closes_standalone_service_clients_and_backend(self):
        app = self.peer()
        app.commit(create())
        self.input(kind=6)
        self.assertEqual(app.socket.recv(1), b"")
        self.assertEqual(self.service.process.wait(timeout=5), 0)
        # The common cleanup also checks that the entire process group is gone.

    def test_automation_is_disabled_by_default(self):
        with tempfile.TemporaryDirectory(prefix="desktop-no-automation-") as directory:
            standalone = Standalone(directory, automation=False)
            peer = None
            try:
                peer = Peer(standalone.port)
                self.assertEqual(peer.call("debug.input", kind=6, error=True), "automation disabled")
                self.assertEqual(peer.call("debug.capture", error=True), "automation disabled")
                peer.commit(create())
                peer.call("sync")
            finally:
                if peer:
                    peer.close()
                standalone.stop()


if __name__ == "__main__":
    unittest.main()
