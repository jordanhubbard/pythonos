"""Generate a tiny PCM tone buffer for the HDA audio driver when available."""

import kernel.sound as sound


def _line(write, text=""):
    if write:
        write(text + "\n")
    else:
        print(text)


def _square_tone(freq=440, ms=2):
    frames = sound.SAMPLE_RATE * ms // 1000
    half_period = max(1, sound.SAMPLE_RATE // (freq * 2))
    out = bytearray(frames * sound.CHANNELS * 2)
    for i in range(frames):
        sample = 12000 if (i // half_period) % 2 == 0 else -12000
        lo = sample & 0xFF
        hi = (sample >> 8) & 0xFF
        off = i * 4
        out[off] = lo
        out[off + 1] = hi
        out[off + 2] = lo
        out[off + 3] = hi
    return bytes(out)


async def main(argv=None, cwd="/", read_char=None, write=None):
    hda = sound.hda
    if hda is None:
        _line(write, "No HDA device is available on this machine.")
        return

    pcm = _square_tone(freq=440, ms=500)
    _line(write, "Generated PythonOS tone buffer for Intel HDA.")
    _line(write, str(len(pcm)) + " PCM bytes ready")
    consumed = hda.write_pcm(pcm)
    _line(write, "wrote " + str(consumed) + " bytes to HDA")
    _line(write, "done")
