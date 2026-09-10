"""
kernel.sound.mixer — Architecture-neutral PCM playback API.

Wraps the existing low-level audio drivers (HDA on x86, virtio-snd on
arm64 — follow-up) behind a single :class:`Mixer` so the sdl2.sdlmixer
shim and apps can target one surface. The native sample format is
signed 16-bit stereo at the rate the underlying device negotiated;
incoming samples in other formats are converted in pure Python.
"""

import _thread
import struct
import kernel.log as log


# ── Format conversion helpers ───────────────────────────────────────────────

def _to_int16_stereo(samples: bytes, channels: int,
                     fmt: str) -> bytes:
    """Convert ``samples`` to native int16 stereo bytes.

    fmt: 'int16' or 'float32'.
    channels: 1 (mono → duplicated) or 2 (passthrough).
    Sample rate conversion is NOT done here — caller must already match
    the device rate.
    """
    if fmt == "int16" and channels == 2:
        return bytes(samples)

    if fmt == "int16" and channels == 1:
        # Duplicate each 2-byte sample
        out = bytearray(len(samples) * 2)
        for i in range(0, len(samples) - 1, 2):
            out[i * 2]     = samples[i]
            out[i * 2 + 1] = samples[i + 1]
            out[i * 2 + 2] = samples[i]
            out[i * 2 + 3] = samples[i + 1]
        return bytes(out)

    if fmt == "float32":
        n_floats = len(samples) // 4
        floats = struct.unpack(f"<{n_floats}f", samples)
        out = bytearray()
        if channels == 1:
            for f in floats:
                v = max(-1.0, min(1.0, f))
                s = int(v * 32767)
                w = struct.pack("<h", s)
                out += w + w   # mono → stereo
        else:
            for f in floats:
                v = max(-1.0, min(1.0, f))
                s = int(v * 32767)
                out += struct.pack("<h", s)
        return bytes(out)

    raise ValueError(f"unsupported audio format: {fmt!r}, channels={channels}")


# ── Mixer ───────────────────────────────────────────────────────────────────

class PCMStream:
    """A period producer serviced by a dedicated PythonOS worker CPU.

    Rendering can block the asyncio thread in a remote display transaction.
    This worker waits without the GIL and keeps a few hardware periods queued,
    so video latency cannot turn directly into audio underruns.
    """

    def __init__(self, owner, producer, *, channels: int, rate: int,
                 fmt: str, period_us: int, prebuffer: int,
                 threaded: bool = True) -> None:
        self._owner = owner
        self._producer = producer
        self._channels = channels
        self._rate = rate
        self._fmt = fmt
        self._period_us = period_us
        self._prebuffer = prebuffer
        self._running = True
        self._pending = None
        self._produced_periods = 0
        self._accepted_periods = 0
        self._backpressure = 0
        self._produced_bytes = 0
        self._accepted_bytes = 0
        self._last_error = ""
        self._stopped_at = 0
        self._started, self._frequency = self._counter()
        self._threaded = threaded
        if threaded:
            _thread.start_new_thread(self._run, ())

    @staticmethod
    def _counter() -> tuple[int, int]:
        try:
            import _hal
            return int(_hal.perf_counter()), int(_hal.perf_frequency())
        except (ImportError, AttributeError):
            import time
            return int(time.monotonic() * 1_000_000), 1_000_000

    def _submit(self) -> bool:
        if self._pending is None:
            try:
                self._pending = self._producer()
            except Exception as exc:
                self._last_error = str(exc)
                self._running = False
                return False
            self._produced_periods += 1
            self._produced_bytes += len(self._pending)
        accepted = self._owner.play_pcm(
            self._pending, channels=self._channels,
            rate=self._rate, fmt=self._fmt, source="stream")
        if accepted:
            self._accepted_periods += 1
            self._accepted_bytes += accepted
            self._pending = None
            return True
        self._backpressure += 1
        return False

    def pump(self) -> bool:
        """Submit one period from an application's existing scheduled loop."""
        return self._running and self._submit()

    def _wait(self, microseconds: int) -> None:
        try:
            import _hal
            _hal.sleep_us(microseconds)
        except (ImportError, AttributeError):
            import time
            time.sleep(microseconds / 1_000_000)

    def _run(self) -> None:
        for _ in range(self._prebuffer):
            if not self._running or not self._submit():
                break
        while self._running:
            self._wait(self._period_us if self._pending is None else 2000)
            self._submit()

    def stop(self) -> None:
        if self._running:
            self._stopped_at, _frequency = self._counter()
        self._running = False

    def performance_snapshot(self, reset: bool = False) -> dict:
        """Report producer cadence and delivery shortfall without audio I/O."""
        now, frequency = self._counter()
        if self._stopped_at:
            now = self._stopped_at
        frequency = frequency or self._frequency
        elapsed_us = ((now - self._started) * 1_000_000 // frequency
                      if frequency else 0)
        expected = elapsed_us // self._period_us if self._period_us else 0
        values = {
            "running": self._running,
            "period_us": self._period_us,
            "prebuffer": self._prebuffer,
            "threaded": self._threaded,
            "elapsed_us": elapsed_us,
            "expected_periods": expected,
            "produced_periods": self._produced_periods,
            "accepted_periods": self._accepted_periods,
            "backpressure": self._backpressure,
            "pending": self._pending is not None,
            "produced_bytes": self._produced_bytes,
            "accepted_bytes": self._accepted_bytes,
            "delivery_shortfall": max(
                0, expected - self._accepted_periods + self._prebuffer),
            "last_error": self._last_error,
        }
        if reset:
            self._produced_periods = 0
            self._accepted_periods = 0
            self._backpressure = 0
            self._produced_bytes = 0
            self._accepted_bytes = 0
            self._started = now
        return values


class Mixer:
    """Single-channel PCM mixer over whichever audio backend is bound.

    The HDA backend hard-codes 48 kHz int16 stereo (see kernel.sound.hda
    constants); this matches the format Mixer normalizes to.
    """

    def __init__(self) -> None:
        self._backend = None
        self._rate    = 48000
        self._fmt     = "int16"
        self._channels = 2
        self._bytes_consumed = 0
        self._stream = None
        self._source_drops = {}

    def attach(self, backend) -> None:
        """Bind the underlying device. Called from kernel.boot when the
        relevant driver successfully probed."""
        self._backend = backend
        log.info(f"mixer: attached backend {type(backend).__name__}")

    @property
    def native_rate(self) -> int: return self._rate
    @property
    def native_channels(self) -> int: return self._channels
    @property
    def bytes_consumed(self) -> int: return self._bytes_consumed

    def play_pcm(self, samples: bytes, channels: int = 2,
                 rate: int | None = None, fmt: str = "int16",
                 source: str = "oneshot") -> int:
        """Push PCM samples to the backend. Returns bytes consumed.

        ``rate`` is informational for now — the backend runs at its own
        native rate. Caller is responsible for resampling.
        """
        if self._backend == None:
            return 0
        if (source == "chipset" and self._stream is not None
                and self._stream._running):
            self._source_drops[source] = self._source_drops.get(source, 0) + 1
            return 0
        if rate not in (None, self._rate):
            log.info(f"mixer: ignoring rate={rate}; native is {self._rate}")
        normalized = _to_int16_stereo(samples, channels, fmt)
        n = self._backend.write_pcm(normalized)
        self._bytes_consumed += n
        return n

    def queue(self, samples: bytes, channels: int = 2,
              rate: int | None = None, fmt: str = "int16") -> int:
        """Alias of :meth:`play_pcm` — the HDA backend is itself a queue
        so there is no separate path to a play command."""
        return self.play_pcm(samples, channels, rate, fmt)

    def start_stream(self, producer, *, channels: int = 2,
                     rate: int = 48000, fmt: str = "int16",
                     period_ms: int = 20, prebuffer: int = 4,
                     threaded: bool = True) -> PCMStream:
        """Run a PCM-period producer independently of the GUI event loop."""
        if self._stream is not None:
            self._stream.stop()
        self._stream = PCMStream(
            self, producer, channels=channels, rate=rate, fmt=fmt,
            period_us=max(1, period_ms) * 1000,
            prebuffer=max(1, prebuffer), threaded=threaded)
        return self._stream

    def stop(self) -> None:
        """Stop the active producer; the hardware stream remains configured."""
        if self._stream is not None:
            self._stream.stop()
            self._stream = None

    def performance_snapshot(self, reset: bool = False) -> dict:
        """Return mixer, producer, and hardware-queue audio diagnostics."""
        backend = self._backend
        backend_metrics = {}
        if backend is not None and hasattr(backend, "audio_metrics"):
            backend_metrics = backend.audio_metrics(reset=reset)
        return {
            "backend": type(backend).__name__ if backend is not None else None,
            "bytes_consumed": self._bytes_consumed,
            "source_drops": dict(self._source_drops),
            "stream": (self._stream.performance_snapshot(reset=reset)
                       if self._stream is not None else {}),
            "device": backend_metrics,
        }


# Module-level singleton
mixer = Mixer()
