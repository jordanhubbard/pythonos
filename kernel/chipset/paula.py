"""Paula-style 4-channel sample mixer. Output is int16 stereo @ 48 kHz."""

from __future__ import annotations

import struct

OUTPUT_RATE = 48000
N_CHANNELS = 4


class Channel:
    def __init__(self) -> None:
        self.sample = b""
        self.rate = 22050
        self.volume = 64
        self.pan = 128
        self.loop = False
        self.loop_start = 0
        self.loop_end = 0
        self.playing = False
        self._pos = 0.0
        self._nframes = 0

    def play(self) -> None:
        self.playing = True
        self._pos = 0.0
        self._nframes = len(self.sample) // 2

    def stop(self) -> None:
        self.playing = False
        self._pos = 0.0


class Paula:
    def __init__(self) -> None:
        self.channel = [Channel() for _ in range(N_CHANNELS)]

    def mix(self, frames: int) -> bytes:
        left = [0] * frames
        right = [0] * frames
        for ch in self.channel:
            if not ch.playing or ch.volume <= 0 or ch._nframes <= 0:
                continue
            vol = max(0, min(64, ch.volume)) / 64.0
            pan = max(0, min(255, ch.pan))
            gain_l = vol * (255 - pan) / 255.0
            gain_r = vol * pan / 255.0
            step = (ch.rate / OUTPUT_RATE) if ch.rate > 0 else 0.0
            loop_end = ch.loop_end if ch.loop_end > 0 else ch._nframes
            loop_start = ch.loop_start
            smp = ch.sample
            pos = ch._pos
            nframes = ch._nframes
            for i in range(frames):
                idx = int(pos)
                if ch.loop:
                    span = loop_end - loop_start
                    if span <= 0:
                        break
                    while idx >= loop_end:
                        idx = loop_start + (idx - loop_end) % span
                        pos = float(idx) + (pos - int(pos))
                elif idx >= nframes:
                    ch.playing = False
                    break
                if idx < 0 or idx >= nframes:
                    break
                o = idx * 2
                if o + 1 >= len(smp):
                    break
                s = struct.unpack_from("<h", smp, o)[0]
                left[i] += int(s * gain_l)
                right[i] += int(s * gain_r)
                pos += step
            ch._pos = pos
        out = bytearray(frames * 4)
        for i in range(frames):
            l = max(-32768, min(32767, left[i]))
            r = max(-32768, min(32767, right[i]))
            struct.pack_into("<hh", out, i * 4, l, r)
        return bytes(out)
