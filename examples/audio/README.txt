Audio
=====

tone.py synthesizes signed 16-bit stereo PCM and writes it to the Intel HDA
device when one is available:

  run('/examples/audio/tone.py')

Study _square_tone() first: frequency becomes a half-period in sample frames,
then each sample is encoded little-endian into left and right channels.

The desktop Audio Tone demo and games demonstrate the virtual Paula channels.
