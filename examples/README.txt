PythonOS learning examples
==========================

This tree is a curriculum, not a miscellaneous program dump. Start with the
short programs below, then follow the subject that interests you.

Suggested learning path
-----------------------

  1. run('/examples/start_here/hello_kernel.py')
  2. sh('/examples/start_here/primes.py 100')
  3. run('/examples/storage/vfs_demo.py')
  4. run('/examples/concurrency/async_tasks.py')
  5. run('/examples/concurrency/thread_demo.py')
  6. run('/examples/graphics/fb_test.py')
  7. run('/examples/audio/tone.py')

Tracks
------

  start_here/   First shell program and a small pure-Python algorithm
  storage/      Files, paths, metadata, and the virtual filesystem
  concurrency/  Cooperative asyncio tasks, queues, and CPU worker threads
  graphics/     Framebuffer drawing and the PySDL2-compatible API
  images/       Ready-to-view snake artwork in several visual styles
  audio/        PCM synthesis and audio-device output
  networking/   TCP clients, servers, and file transfer
  demos/        Interactive desktop demonstrations and teaching programs
  games/        Complete SDL-backed games; chipset constraints are separate
  internals/    Test fixtures for kernel contributors, not beginner lessons

Every subject directory contains its own README.txt with an ordered path and
the important concepts demonstrated there. Use the Files app or Editor's
File > Open command to browse and edit the source.

Every Python example begins with a teaching docstring containing its exact
/examples/... path, purpose, and a short source tour. This matters when the
source editor is open: editing is modal, so the file remains self-locating
after you leave the chooser or copy the lesson elsewhere. New examples should
also comment functional blocks and design decisions, not narrate every line.

Running examples
----------------

  run('/examples/path/program.py')

calls the program's async main() function. Use sh() when passing arguments:

  sh('/examples/start_here/primes.py 200')
  sh('/examples/storage/vfs_demo.py /tmp/my-note.txt')

Bundled application modules are loaded from readable Python source and
compiled on first import. The demos/ and games/ directories expose those same
application sources for study. Saving a source pane creates a writable
override under /apps; Reload starts a fresh instance with the edited code.

The internals/ track deliberately separates validation programs from lessons.
They remain readable and runnable for contributors, but are not presented as
part of the learning progression.
