"""kernel.chipset — software Agnus/Denise/Paula. No _hal imports."""

from __future__ import annotations

from kernel.chipset.blitter import cookie, copy, fill
from kernel.chipset.copper import Move, Wait
from kernel.chipset.paula import OUTPUT_RATE, Paula
from kernel.chipset.playfield import MODE_DIRECT, MODE_INDEXED, Playfield
from kernel.chipset.raster import raster_view
from kernel.chipset.sprite import Sprite
from kernel.chipset.view import View


class _Blitter:
    fill = staticmethod(fill)
    copy = staticmethod(copy)
    cookie = staticmethod(cookie)


blitter = _Blitter()
paula = Paula()

DEFAULT_DEST_W = 1024
DEFAULT_DEST_H = 768
TICK_HZ = 30


class Chipset:
    def __init__(self) -> None:
        self.active_view: View | None = None
        self.dest_w = DEFAULT_DEST_W
        self.dest_h = DEFAULT_DEST_H
        self._dest = bytearray(self.dest_w * self.dest_h * 4)
        self._present = None
        self._mixer = None
        self._running = False
        self._task = None
        self._vblank = None
        self.workbench: View | None = None
        self.tick_hz = TICK_HZ
        self.on_event = None
        self.exit_requested = False
        self._bridge_native_frames = False
        self._native_dest = bytearray()

    @property
    def is_running(self) -> bool:
        return self._running

    def set_dest(self, width: int, height: int) -> None:
        self.dest_w = width
        self.dest_h = height
        self._dest = bytearray(width * height * 4)

    def set_present(self, callback) -> None:
        self._present = callback

    def set_mixer(self, mixer) -> None:
        self._mixer = mixer

    def load_view(self, view: View | None) -> None:
        if view is None:
            raise ValueError("load_view requires a View")
        self.active_view = view
        self.exit_requested = False

    def request_exit(self) -> None:
        """Request that the active full-screen View return to Workbench."""
        self.exit_requested = True

    def tick(self) -> bytes:
        view = self.active_view
        native = (self._bridge_native_frames and view is not None
                  and int(view.scale) > 1)
        if native:
            width, height = view.width, view.height
            needed = width * height * 4
            if len(self._native_dest) != needed:
                self._native_dest = bytearray(needed)
            dest = self._native_dest
            raster_view(view, dest, width, height, scale_override=1)
            present_scale = int(view.scale)
        else:
            width, height = self.dest_w, self.dest_h
            dest = self._dest
            raster_view(view, dest, width, height)
            present_scale = 1
        frames = max(1, OUTPUT_RATE // self.tick_hz)
        pcm = paula.mix(frames)
        mixer = self._mixer
        if mixer is not None:
            try:
                mixer.play_pcm(pcm, channels=2, fmt="int16")
            except Exception:
                pass
        cb = self._present
        if cb is not None:
            cb(bytes(dest), width, height, present_scale)
        if self._vblank is not None:
            try:
                self._vblank.set()
                self._vblank.clear()
            except Exception:
                pass
        return bytes(dest)

    async def vblank(self):
        import asyncio
        if self._vblank is None:
            self._vblank = asyncio.Event()
        self._vblank.clear()
        await self._vblank.wait()

    def start(self, loop=None) -> None:
        if self._running:
            return
        import asyncio
        self._running = True
        if self._vblank is None:
            self._vblank = asyncio.Event()
        loop = loop or asyncio.get_event_loop()
        self._task = loop.create_task(self._run())

    def stop(self) -> None:
        self._running = False
        task = self._task
        if task is not None:
            task.cancel()
            self._task = None

    async def _run(self):
        import asyncio
        period = 1.0 / self.tick_hz
        while self._running:
            self.tick()
            await asyncio.sleep(period)

    def ensure_workbench(self, width: int = 1024, height: int = 768) -> View:
        if self.workbench is None:
            self.workbench = View(width, height, mode=MODE_DIRECT, scale=1)
        return self.workbench


chipset = Chipset()


def start_for_gui() -> None:
    """Wire dest size, mixer, present callback, Workbench View, clock."""
    from kernel.display.framebuffer import fb
    # Bridge GUI deliberately has no guest framebuffer: frames are uploaded
    # to the host SDL window by _present_frame.  It still needs the exact same
    # chipset clock and Workbench backing view as native-fb mode.
    width = fb.width if fb is not None else DEFAULT_DEST_W
    height = fb.height if fb is not None else DEFAULT_DEST_H
    chipset.set_dest(width, height)
    try:
        from kernel.sound.mixer import mixer
        chipset.set_mixer(mixer)
    except Exception:
        pass
    chipset.set_present(_present_frame)
    bridge_open = False
    try:
        from kernel.bridge import bridge as _br
        bridge_open = _br.opened
        chipset._bridge_native_frames = "frame.rle32" in _br.features
    except Exception:
        chipset._bridge_native_frames = False
    wb = chipset.ensure_workbench(width, height)
    if chipset.active_view is None:
        chipset.load_view(wb)
    # In bridge mode the compositor owns Workbench presentation until an app
    # explicitly activates a LoadView.  Starting the raster clock here would
    # replace the freshly opened desktop with an empty Workbench frame.
    if fb is not None and not bridge_open:
        chipset.start()


def _present_frame(buf: bytes, width: int, height: int,
                   scale: int = 1) -> None:
    from kernel.gui.compositor import compositor as _comp
    from kernel.display.framebuffer import fb
    if getattr(_comp, "_bridge_present", False) and _comp._bridge_fb_handle:
        try:
            from kernel.bridge import bridge as _br
            if scale > 1 and "frame.rle32" in _br.features:
                import _hal
                encoded = _hal.rle_encode32(buf)
                encoding = "rle32" if len(encoded) < len(buf) else "raw"
                payload = encoded if encoding == "rle32" else buf
                send = (_br.notify if "oneway" in _br.features else _br.call)
                send("surface.upload_scaled", {
                    "handle": _comp._bridge_fb_handle,
                    "src_w": width,
                    "src_h": height,
                    "scale": scale,
                    "encoding": encoding,
                    "present": True,
                }, payload=payload)
                return
            _br.call("surface.upload",
                     {"handle": _comp._bridge_fb_handle},
                     payload=buf)
            _br.call("display.present", {})
            return
        except Exception:
            pass
    if fb is not None:
        fb.present(buf)


__all__ = [
    "MODE_DIRECT",
    "MODE_INDEXED",
    "Move",
    "Wait",
    "View",
    "Playfield",
    "Sprite",
    "blitter",
    "paula",
    "chipset",
    "Chipset",
    "start_for_gui",
]
