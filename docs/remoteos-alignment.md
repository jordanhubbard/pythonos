# PythonOS, RubyOS, and RemoteOS-SDL alignment

PythonOS 0.4.x and RubyOS 0.2.x pin the same
[RemoteOS-SDL 0.1.x](https://github.com/jordanhubbard/RemoteOS-SDL) repository
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
