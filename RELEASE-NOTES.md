# PythonOS v0.3.4: Somehow, We Made an Operating System More Fun Than Your Operating System

Since v0.3.3—released approximately five minutes ago in geological time—PythonOS
has evolved from “an astonishing bare-metal Python experiment” into “an
astonishing bare-metal Python experiment that now has better educational tooling
than several products with enterprise pricing.”

## The desktop stopped communicating by carrier pigeon

The compositor now routes graphics through a generic SDL command protocol with
batching, one-way operations, TCP tuning, bounded queues, and performance
instrumentation.

Result: dramatically less waiting while individual pixels obtain planning
permission from the kernel.

PythonOS still supports running the kernel on machine X and the display on
machine Y. Because apparently reinventing X11 was insufficiently ambitious
unless we also made it understandable.

## Native ARM64: what a concept

PythonOS now builds, boots, tests, and ships natively for ARM64.

This replaces the innovative former performance strategy of running an x86
computer inside an ARM computer and then wondering why Pac-Man moved like
continental drift.

The release includes both:

- `pythonos-arm64.elf`
- `pythonos.iso` for x86_64

Both passed architecture-specific CI gates, because “works on my machine” is
especially unconvincing when your machine is imaginary.

## Defender was promoted from cave painting to video game

Defender received a nearly complete reconstruction:

- Higher-resolution graphics
- Circular scrolling world
- Terrain and radar
- Humans who can be abducted, rescued, dropped, caught, and returned
- Landers, mutants, bombers, mines, pods, swarmers, and baiters
- Waves, lives, scoring, smart bombs, hyperspace, thrust, and reverse
- Continuous music, engine sound, and effects
- Demo mode for automated testing
- Visible controls
- Proper input ownership

The old game’s audio played mainly while moving, which was less “arcade sound
system” and more “loose wire in a 1973 station wagon.”

## Audio now comes with an incident response team

The audio path gained end-to-end profiling across:

- PCM production
- Mixer dispatch
- Device acceptance
- DMA submission and reclamation
- Queue occupancy
- Backpressure
- Dropped sources
- Delivery shortfall

These statistics appear in Top and through `pythonos_debug.py audio`.

Defender now feeds bounded 250 ms periods from its scheduled game loop into a
five-buffer virtio-sound queue. Stale Paula output can no longer barge into an
unrelated game and remix it into experimental glitch jazz.

## The Amiga chipset found its proper calling

The virtual blitter, copper, sprites, playfields, Fat Agnus-inspired machinery,
and Paula audio remain available—but are now clearly positioned as an optional,
inspectable teaching and demoscene environment.

Ordinary applications use the normal SDL-backed desktop path.

This ends the previous architectural policy of asking every application author:

> Would you like the convenient interface, or would you prefer to role-play as
> a 1987 assembly programmer?

Seven progressive lessons now teach the chipset features individually.

## Source code is part of the user interface

Every running window can expose its source through **View Window’s Source** or
F2.

The common editor supports:

- Read and edit
- Save and cancel
- Runtime reload
- Arrow-key navigation
- Basic Emacs travel keys
- `Ctrl-V`, `Meta-V`, word movement, sentence movement, paragraph movement,
  buffer movement, and recentering

Applications are source-first and dynamically compiled by the VFS importer.
Edited applications can be reloaded without rebuilding the entire kernel.

In other words, PythonOS has become what happens when Smalltalk, Logo,
Processing, and an operating-systems course are left unsupervised near SDL.

## File dialogs now contain files

The Editor and Image Viewer use a shared graphical file chooser derived from
the same view as the Files application.

It supports:

- Mouse selection
- Double-click navigation
- Directory traversal
- Open and Save workflows
- Shared scrolling behavior

A staggering triumph over the former file-dialog design, whose core interaction
model was “already know the exact path.”

## Scrolling has been discovered

Windows now support:

- Trackpad and mouse-wheel scrolling
- Middle-button drag scrolling
- Shared scrolling behavior for text and list views

Context menus clamp themselves to the screen and scroll when oversized.

You can now open a menu near the bottom of the desktop without half its contents
escaping into a lower-dimensional universe.

## A real teaching-oriented examples library

The examples directory is now organized as a curriculum:

- Start here
- Storage and VFS
- Concurrency
- Graphics
- Virtual chipset
- SDL
- Audio
- Networking
- Games and demos
- Kernel internals

Every Python example identifies its exact VFS path and explains its purpose and
functional structure.

There are also several original snake-themed images ready for the Image Viewer,
satisfying the previously neglected requirement that an operating system named
Python must contain sufficient snakes.

## Desktop quality-of-life, an emerging research area

Also added:

- Configurable global desktop keybindings
- A docked Keybindings application
- Top-style task, system, network, bridge, and audio monitoring
- A clock with discoverable session-time controls
- Drag-and-drop file transfer between host and guest
- Clearer remote-display instructions
- Correct Python version and architecture reporting
- Reliable Escape handling from full-screen applications

Look, it exits now.

## And yes, Space Invaders

PythonOS now includes a full Space Invaders-style game with graphics, audio,
demo mode, scoring, formations, shields, and escalating gameplay.

Because the obvious next step after booting CPython directly on bare metal is to
defend Earth from aliens using an event loop.

## Executive summary

PythonOS v0.3.4 is:

- Faster
- Native on ARM64
- Remotely displayable
- Dynamically editable
- Actually navigable
- Properly instrumentable
- Extensively instrumented
- Much more playable
- Dramatically more useful for teaching
- Still, against all available advice, an operating system whose kernel is
  Python

It is available now as
[PythonOS v0.3.4](https://github.com/jordanhubbard/pythonos/releases/tag/v0.3.4).
