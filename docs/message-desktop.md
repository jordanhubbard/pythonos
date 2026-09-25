# Message desktop prototype

The host desktop service owns retained views, layout, composition, focus,
dragging, button feedback, and plotting. An application sends content changes
and receives semantic actions. It never draws pixels or imports SDL.

```
application (PythonOS or host Python; any language can implement the protocol)
    | desktop protocol 1: content transactions / semantic events
host desktop service (tools/desktop_service)
    | existing RemoteOS protocol 2: bounded draw batches / input
RemoteOS-SDL
    | SDL window and devices
```

This is an opt-in prototype alongside the existing PythonOS compositor. The
RemoteOS-SDL submodule is unchanged. The desktop service is a separate host
process, so the shared device service remains usable by existing clients.

## Run

Initialize the submodule and build the normal SDL backend, then launch both
the desktop and its example application:

```sh
git submodule update --init services/remoteos-sdl
make run-message-desktop
```

The example shows text, a Pause/Resume button, and a simulated live signal.
Click the button, or press Tab then Enter/Space. Drag the title bar; click its
`x` to close. Closing the example ends the combined demonstration.

To separate the application from the service:

```sh
make bridge
python3 tools/message_desktop.py
# In another terminal (repeat for independent client sessions):
python3 tools/message_desktop.py --client
```

The service listens on loopback port 17020 by default. `--host` and `--port`
select the endpoint. `--headless` uses the existing hidden SDL surface;
`--demo --frames 100` gives a finite automation run. The launcher supervises
the backend and cleans up on close, failure, or Ctrl-C.

For a PythonOS guest, start the host service, boot the normal network-enabled
guest, and run this from its Python prompt:

```text
sh('/examples/networking/message_desktop.py 10.0.2.2 17020')
```

The example uses the kernel's native TCP stack and the same protocol client
as the hosted demo. `10.0.2.2` is QEMU's user-network host address. This guest
path requires a newly built image that includes the example. The initial
validation covers the hosted client and real SDL service; guest boot testing
requires the Docker builder to be running.

## Desktop protocol 1

Each message is a four-byte big-endian unsigned length followed by UTF-8 JSON.
Maximum envelope size is 65536 bytes. There are no binary trailers. This is a
distinct protocol from RemoteOS v2; it has its own endpoint and version.

```json
{"v":1,"id":1,"op":"hello","params":{}}
{"v":1,"id":1,"ok":true,"result":{"protocol":1,"limits":{},"features":[]}}
```

`hello` must precede other requests. Its actual result advertises limits and
the features `text`, `button`, `plot`, and `atomic_commit`. IDs are positive
integers echoed in responses. Clients issue one request at a time and await
its reply; input can be polled independently of content updates. Errors use
`{"v":1,"id":2,"ok":false,"error":"reason"}`. Invalid framing closes the
connection. Content validation errors leave the connection usable.

`commit` carries the next session revision (starting at 1) and an ordered
`commands` array. All commands validate against a private copy before any
content becomes visible. Failure changes neither content nor revision.
Successful replies acknowledge accepted state, not presentation completion.
The renderer may coalesce several commits into one frame.

```json
{"v":1,"id":2,"op":"commit","params":{"revision":1,"commands":[
  {"op":"create_view","view":"system","title":"System"},
  {"op":"create_node","view":"system","id":"status","kind":"text","text":"Running"},
  {"op":"create_node","view":"system","id":"pause","kind":"button","text":"Pause"},
  {"op":"create_node","view":"system","id":"load","kind":"plot","text":"Load","min":0,"max":100},
  {"op":"append_data","view":"system","id":"load","values":[20,30,25]}
]}}
```

| Command | Fields beyond `op` |
| --- | --- |
| `create_view` | `view`, optional `title` (defaults to view ID) |
| `destroy_view` | `view` |
| `create_node` | `view`, `id`, `kind`, optional `text`; plots also accept `min`/`max` (defaults 0/100) |
| `set_text` | `view`, `id`, `text` |
| `append_data` | `view`, `id`, `values` |
| `destroy_node` | `view`, `id` |

IDs are 1–64 characters. Text is up to 120 printable ASCII characters, matching
the backend bitmap font. Labels are truncated to the available width. The
service assigns a vertical layout in a fixed 640×480 view; commits exceeding
available height are rejected. Each view has at most one plot. Plot values
are finite numbers within ±1e9; displayed values are clamped to the declared
range. The most recent 256 samples are retained.

`poll` with empty params drains queued events and returns the current revision:

```json
{"v":1,"id":3,"ok":true,"result":{"revision":1,"events":[
  {"event":"activate","view":"system","target":"pause","revision":1}
]}}
```

Closing a view immediately removes it and emits `closed` with `view` and the
current revision. Host interactions do not increment the content revision.
An event's revision records the content state when the event occurred.
Applications should handle close events before submitting more changes to
that view. Programmatic destruction does not emit a close event.

## Ownership and limits

Each TCP connection owns an independent namespace and all its views. No client
can address another client's resources. Disconnect destroys its views, focus,
and queued actions. Reconnect creates a fresh session; replay desired state
starting at revision 1. There is no resume token or exactly-once recovery after
a lost acknowledgement.

The service accepts at most eight connections, four views per session, 24
nodes per view (also limited by available layout space), 64 commands per
transaction, and 64 queued actions per session. Event overflow disconnects
the slow client rather than silently losing actions. Writes time out after
five seconds, and clients must send a complete request within 60 seconds.
Poll periodically even when content is unchanged. Rendering runs at up to
60 Hz independently of application requests and uses bounded RemoteOS batches.

The endpoint has no authentication or encryption. Loopback is the default;
use an authenticated tunnel for access outside a trusted host/network.

## Validation and scope

```sh
python3 tests/message_desktop_test.py  # atomic updates, framing, isolation, cleanup
make test-message-desktop             # real SDL rendering and injected input
make test-chipset                     # existing host regression gate plus protocol tests
```

The SDL test verifies the example's Pause action, rendering/capture, dragging
while an application is idle, and close delivery. It writes a BMP capture to
`/tmp/message-desktop.bmp` (override with `MESSAGE_DESKTOP_CAPTURE`).

This first slice intentionally uses a fixed display size, vertical layout,
ASCII text, and a single-series plot. It does not yet expose pixel surfaces,
images, clipboard, menus, accessibility semantics, or session restoration.
Those can extend the retained protocol without changing application transports.
