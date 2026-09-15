# PythonOS v0.4.0: One Display Service, Zero Duplicate C Piles

PythonOS 0.4.0 moves its SDL remote desktop to the independently versioned
[RemoteOS-SDL](https://github.com/jordanhubbard/RemoteOS-SDL) service.  PythonOS
and RubyOS now share one high-performance, OS-neutral device boundary while
remaining language-native systems above it.

## Highlights

- Replaces the in-tree `pythonos_bridge` implementation with RemoteOS-SDL v2.
- Makes a deliberate protocol break: clients negotiate protocol v2 and no
  compatibility facade is retained.
- Batches response-free drawing work with `render.batch`, uses one-way display
  presentation where possible, and keeps input polling independent.
- Adds service telemetry for requests, frames, wire traffic, batches, dropped
  events, audio, per-operation timing, and slow requests.
- Keeps PythonOS's Python-first compositor, applications, async services, and
  source-editing environment above the shared service boundary.
- Documents the coordinated architecture and the design differences between
  PythonOS and RubyOS.

## Release artifacts

The release contains the x86_64 bootable ISO and the ARM64 ELF image.  Build
and test automation exercises the shared RemoteOS-SDL service as a pinned git
submodule.

## Security note

RemoteOS-SDL v2 is intentionally a trusted local or tunneled service protocol;
it does not provide encryption or authentication itself.  Use SSH or another
authenticated transport when crossing a trust boundary.

For implementation details, see
[`docs/remoteos-alignment.md`](docs/remoteos-alignment.md) and
[`docs/gui.md`](docs/gui.md).
