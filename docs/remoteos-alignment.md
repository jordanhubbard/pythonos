# PythonOS, RubyOS, and RemoteOS-SDL alignment

PythonOS 0.4.2 and RubyOS 0.3.0 pin the same
[RemoteOS-SDL 0.2.0](https://github.com/jordanhubbard/RemoteOS-SDL) repository
at `services/remoteos-sdl`. The former language-branded C companion has been
removed; protocol and host-device behavior now have one owner.

| Concern | PythonOS | RubyOS | RemoteOS-SDL |
|---|---|---|---|
| Runtime | source-built CPython | source-built CRuby | no guest runtime |
| Scheduling | asyncio tasks | Fibers | host event loop only |
| Live programming | VFS Python modules and window-source reload | Module sandboxes and class patches | none |
| Transport | bare-metal TCP/UART | bare-metal TCP/VirtIO console | TCP/Unix stream endpoint |
| Presentation | Python compositor | Ruby compositor | SDL resources and input/audio devices |
| Metrics | guest RPC timing | guest RPC timing | wire volume and per-operation service time |

RemoteOS protocol v2 is deliberately breaking. Negotiation is mandatory; old
versions fail closed. Drawing is grouped into bounded, response-free batches.
PythonOS follows each batch with an ordered one-way presentation while its
independent input task supplies the next ordering barrier. RubyOS uses
`frame.commit` to present and collect input in one round trip. Both approaches
use the same service primitives but match their native schedulers.

The common layer stops at devices. PythonOS keeps asyncio, Python import/reload,
its widget hierarchy, applications, and teaching curriculum. RubyOS keeps
Fibers, Modules, class DSLs, object graphs, and Rack-shaped services. Convergence
below that line removes duplicated systems code without flattening either
language into a least-common-denominator API.

The shared service now provides depth-buffered 3D, FFmpeg decoding, audio-clocked
short-clip playback, and bounded Matroska export (MPEG-4 plus optional stereo PCM).
These are additive protocol-v2 capabilities. RubyOS exposes them through its
scene, resource and recording APIs; PythonOS's existing compositor and retro
graphics APIs remain unchanged. Both host builds now require FFmpeg development
libraries and OpenGL alongside SDL. Media limits and security policy live in
the pinned service's `PROTOCOL.md`, not in duplicated language-specific servers.
