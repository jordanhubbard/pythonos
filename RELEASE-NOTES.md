# PythonOS v0.4.3: The Host Picks the Screen Size Now

## Someone else decides how big the desktop is

A bare-metal guest has no environment, no display enumeration, and no way to
ask what the host is plugged into. So it asks for a number chosen at compile
time and lives with it, however large the monitor actually is. That was always
the wrong side of the wire to decide on.

PythonOS pins RemoteOS-SDL 0.3.0, where `REMOTEOS_SDL_SIZE=WxH` lets whoever
launches the service choose, and `display.open` reports back the framebuffer it
actually created. The compositor needed no changes to benefit: it already
adopted the reported size into `_bridge_w`/`_bridge_h` rather than assuming it
got what it asked for, and those feed layout throughout. A guest that was
already doing the right thing gets a new capability for free, which is a
pleasant change from the usual arrangement.

The bump also carries a latent fix on the window-surface fallback — the path
PythonOS takes whenever renderer creation fails. `SDL_GetWindowSurface` returns
a surface at the drawable size, so on a high-density display the guest was told
a size smaller than the buffer it had been handed, and would have painted into
one corner of it.

The high-DPI window creation in 0.3.0 is a measured no-op on 1:1 displays and
**untested** on scaled ones. Treat it as available, not proven.

## Closing the window actually closes everything

`make run-gui` supervises two processes. Closing the desktop window asked the
bridge to close its display and left both of them running, which is not what
anyone means by closing a window. The compositor now asks the bridge to exit,
so its supervisor can stop QEMU too. `tests/run_gui_test.py` covers the three
ways that session can end: the bridge exiting, QEMU exiting, and the guest
shutting down.

## A build that stops depending on luck

`make build` failed with `pull access denied for pythonos-builder` after the
image was pruned. The `.docker-image` stamp tracked the Dockerfile's mtime and
not whether the image still existed, so make skipped the build and went
straight to a `docker run` against nothing. The stamp is now dropped when the
image is gone.

The macOS Intel CI job had been failing for longer, and for two reasons that
looked like one. `setup_cpython.sh` drove libpython with `-j$(nproc)`, which
inside a 6 GB colima VM meant three concurrent compilers on CPython's heaviest
generated files; the cross compiler segfaulted in a different translation unit
each run. `PYTHONOS_BUILD_JOBS` now caps that, set to 1 on that runner alone.
With the build finishing, the job reached the GUI smoke and timed out there
instead — that runner boots the guest under TCG inside a QEMU-backed VM, so
`PYTHONOS_GUI_COMMAND_TIMEOUT` is now 240 there.

Both timeout readers also stopped trusting their environment. A matrix key set
for one entry arrives at the others as an empty string rather than absent, and
bare `float("")` raises; empty, non-numeric and non-positive values now fall
back to the documented default instead of crashing the test or, worse, becoming
zero.

## What changes for builders

Nothing, unless you want a bigger desktop — then set `REMOTEOS_SDL_SIZE` when
launching the display service. Linux x86_64 and ARM64 behaviour is unchanged,
and the Python compositor, asyncio behaviour, retro games and chipset teaching
model are all untouched.

[PythonOS v0.4.3](https://github.com/jordanhubbard/pythonos/releases/tag/v0.4.3).
