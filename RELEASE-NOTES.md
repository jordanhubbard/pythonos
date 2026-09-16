# PythonOS v0.4.2: One Media Service, Two Very Opinionated Siblings

## The family has acquired a studio

PythonOS now pins RemoteOS-SDL 0.2.0, the same service used by RubyOS 0.3.0.
The shared host layer gains depth-buffered 3D, FFmpeg video decoding,
audio-clocked clip playback and bounded audiovisual export. We have boldly
concluded that duplicating a codec stack for each programming language is
not, in fact, a compelling systems architecture.

This is a service-alignment release for PythonOS. Its Python compositor,
asyncio behavior, retro games and chipset teaching model are unchanged.
RubyOS's new scene/recording classes belong to RubyOS; PythonOS does not
suddenly acquire a Ruby API because a release note was feeling ambitious.

## What changes for builders

Install the FFmpeg development libraries, including swresample, and OpenGL
alongside SDL on Linux/WSL2; Homebrew users add ffmpeg. The README and automated
Linux/macOS setup now reflect these dependencies. Both guests pin the canonical
service instead of carrying separate implementations.

The new media operations are advertised additions to protocol v2. Existing
desktop drawing, input, audio and telemetry retain their contract. All the new
host operations remain language-neutral and available through the protocol.

## The boundaries are still real

RemoteOS-SDL exports short Matroska movies with MPEG-4 video and optional
48 kHz stereo PCM. Encoded clips cap at 16 MiB; export and audio predecode cap
at 60 seconds. OpenGL rendering includes readback and a software fallback.
This is not a claim that PythonOS now ships a nonlinear editor or a native
Windows desktop. Windows continues to mean WSL2.

The service endpoint still requires loopback or an authenticated tunnel.
Acquiring a video encoder does not magically acquire authentication.

## Release gates and artifacts

Publication requires the normal local gate and Linux x86_64, Linux ARM64,
and macOS Intel CI. The release includes the CI-produced x86_64 ISO and ARM64
ELF. Existing serial, GUI, bridge and chipset checks remain mandatory.

## Executive summary

PythonOS keeps its Python personality and its games. RubyOS develops its own
creative language. Both use one tested host media service. Revolutionary
synergy, achieved by deleting the part where we maintain everything twice.

[PythonOS v0.4.2](https://github.com/jordanhubbard/pythonos/releases/tag/v0.4.2)
