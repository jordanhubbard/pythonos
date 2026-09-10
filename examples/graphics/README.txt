Graphics
========

Start with chipset/ for seven focused lessons covering the virtual Amiga
playfields, Copper, Blitter, sprites, Paula audio, and raster display window.

Start with fb_test.py for direct framebuffer rectangles and bitmap text:

  run('/examples/graphics/fb_test.py')

Then follow the SDL progression in /examples/graphics/sdl/:

  1. sdl_hello.py     window, surface, fill, present, and event polling
  2. sdl_renderer.py  renderer clear and rectangle drawing
  3. sdl_text.py      text surfaces, textures, and render copies
  4. sdl_image.py     in-memory PNG decoding and pixel inspection
  5. sdl_jpeg.py      baseline JPEG decoding

For longer interactive programs, continue with /examples/demos/. For complete
chipset-driven programs, inspect /examples/games/.
