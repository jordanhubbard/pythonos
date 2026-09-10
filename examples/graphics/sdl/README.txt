SDL-compatible graphics
=======================

Run these in order:

  run('/examples/graphics/sdl/sdl_hello.py')
  run('/examples/graphics/sdl/sdl_renderer.py')
  run('/examples/graphics/sdl/sdl_text.py')
  run('/examples/graphics/sdl/sdl_image.py')
  run('/examples/graphics/sdl/sdl_jpeg.py')

The API intentionally resembles PySDL2 so programs can teach familiar SDL
ideas without exposing the lower-level desktop bridge protocol. The decoder
examples embed tiny image fixtures so they are deterministic and need no
external files.
