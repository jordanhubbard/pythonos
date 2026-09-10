Virtual Amiga chipset lessons
=============================

Run these in order. Each program isolates one idea before the complete demos
and games combine them. Every lesson is full-screen; Esc always returns to the
desktop and the control guide is drawn on screen.

  01_playfield.py       Indexed pixels, palettes, View, and smooth scrolling
  02_copper.py          Scanline WAIT/MOVE display lists and palette changes
  03_blitter.py         Clipped rectangle fills and block copies
  04_sprites.py         Transparent hardware sprites independent of playfields
  05_dual_playfields.py Two scrolling layers with a transparent key color
  06_paula.py           Four PCM voices, volume, looping, rate, and stereo pan
  07_display_window.py  DIWSTART/DIWSTOP raster display-window control

Afterward, study /examples/demos/sprites.py and the games/ sources to see the
same pieces used together. Press F2 while a registered demo or game is focused
to edit and reload its source.
