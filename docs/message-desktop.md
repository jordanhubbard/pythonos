# Message desktop prototype

This is a high-level GUI and multimedia canvas for language-oriented operating
systems. The host owns retained windows, controls, layout, focus, rendering,
and media playback. Applications send object/content updates and receive
semantic actions. Encoded assets are uploaded once; scene geometry, media
controls, and small state changes drive the host's SDL/OpenGL/FFmpeg backend.

The application protocol has no framebuffer upload or arbitrary SDL-call
escape hatch. Pixel generation, decoding, and presentation stay on the host.
This boundary is the project's differentiating feature: an OS can build its UI
without implementing rasterization, a compositor, or a media playback loop.

```
application (PythonOS or host Python; any language can implement the protocol)
    | desktop protocol 1: retained objects / encoded assets / semantic events
host desktop service (tools/desktop_service)
    | existing RemoteOS protocol 2: device operations / input / timed media
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
`x` to close. Drag the bottom-right grip to resize. Closing the example ends
the combined demonstration.

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
the backend and cleans up on close, failure, Ctrl-C, or SIGTERM. With no
`--demo`, the service stays up after applications disconnect.

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
features: `text`, `button`, `plot`, `atomic_commit`, `window.configure`,
`window.stacking`, `controls`, `inspect`, `sync`, and `audio.wav`. `scene` and
`video` are advertised only when supported by the connected backend. IDs are positive
integers echoed in responses. Clients issue one request at a time and await
its reply; input can be polled independently of content updates. Errors use
`{"v":1,"id":2,"ok":false,"error":"reason"}`. Invalid framing closes the
connection. Content validation errors leave the connection usable.

`commit` carries the next session revision (starting at 1) and an ordered
`commands` array. All commands validate against a private copy before any
content becomes visible. Failure changes neither content nor revision.
Successful replies acknowledge accepted state, not presentation completion.
The renderer may coalesce several commits into one frame. `sync` is a
presentation barrier: it processes queued device input and presents accepted
content before replying. `inspect` returns the caller's windows, stacking order,
geometry, focus, and node values/rectangles; it never exposes backend handles
or another session's objects.

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
| `create_view` | `view`, optional `title` and `properties` |
| `configure_view` | `view`, `properties`: any of `title`, `x`, `y`, `w`, `h`, `visible`, `state` |
| `focus_view` / `lower_view` | `view`; raise/focus or lower a visible window |
| `destroy_view` | `view` |
| `create_node` | `view`, `id`, `kind`, optional `text`, and kind-specific fields below |
| `set_text` | `view`, `id`, `text` |
| `set_enabled` | `view`, `id`, boolean `enabled` |
| `set_value` | `view`, `id`, kind-appropriate `value` |
| `append_data` | `view`, `id`, `values` |
| `set_scene` | `view`, `id`, `vertices` |
| `destroy_node` | `view`, `id` |

Window state is `normal`, `minimized`, or `maximized`. Maximizing saves normal
geometry; restoring recovers it. Restore before supplying explicit geometry.
Hidden/minimized windows retain their content and cannot receive input or be
focused. The display is 1000×720 with a 44-pixel desktop header. Windows must
fit it, with minimum size 320×180. The default remains 640×480.

| Node kind | Retained content and interaction |
| --- | --- |
| `text` | `text` label |
| `button` | `text`, `enabled` (default true); emits `activate` |
| `checkbox` | boolean `value` (default false), `text`, `enabled`; emits `change` |
| `entry` | string `value` (default empty), `enabled`; host appends printable input and handles Backspace |
| `slider` | numeric `value` 0..100 (default 0), `text`, `enabled`; pointer/left/right keys emit `change` |
| `plot` | `text`, `min`/`max` (defaults 0/100), bounded samples via `append_data` |
| `scene` | retained triangle `vertices`, depth-rendered on the host |
| `video` | `resource`: an owned, finished video asset; one node per video resource |

Tab/Shift-Tab traverse enabled controls in the focused window. Enter/Space
activate buttons and checkboxes. Changes to control values happen on the host
and produce semantic `change` events containing `view`, `target`, and `value`.
Dragging/resizing emits `configured` with final `x`, `y`, `w`, and `h`.

IDs are 1–64 characters. Text is up to 120 printable ASCII characters, matching
the backend bitmap font. Labels are truncated to the available width. The
service assigns a vertical layout that follows window width; commits exceeding
available height (including restored geometry) are rejected. Each view has at most one plot. Plot values
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

## Retained 3D, audio, and video

A `scene` node retains up to 256 triangles (768 vertices), each vertex in
`[x,y,z,w,r,g,b]` form: homogeneous clip coordinates and 0..1 colors. Coordinates
must be finite and within ±1e6. The host handles clipping, depth, rasterization,
and composition. It only rerenders geometry when the content or target size
changes; moving a window does not resend scene data from the client.

Media resources use separate lifecycle operations outside content commits:

| Operation | Parameters |
| --- | --- |
| `resource.create` | `resource` ID, `kind` (`audio` or `video`), declared `bytes` |
| `resource.append` | `resource`, exact next byte `offset`, base64 `data` |
| `resource.finish` | `resource`; validates/decodes the uploaded asset |
| `resource.release` | `resource`; rejected while referenced by a node |
| `media.control` | `resource`, `action`: `play`, `pause`, `stop`, or `seek`; seek adds `seconds` |
| `media.status` | `resource`; returns `kind`, `ready`, `playing`, `seconds`, `eof` |

No paths or URLs are accepted. Resources are immutable after finishing. Each
session may own four resources, at most 4 MiB each and 8 MiB total. Uploads use
chunks of at most 24576 decoded bytes, fitting the normal JSON frame limit.
Offsets are checked so retries or incomplete uploads cannot silently corrupt
an asset. Release unfinished or invalid uploads explicitly, or disconnect.

Audio assets are stereo, signed 16-bit PCM WAV at 8–192 kHz, up to ten seconds.
The service queues decoded audio to SDL and tracks actual queue consumption;
the application does not stream samples on each frame. Video assets use the
existing FFmpeg decoder and audio-clocked short-clip player, including embedded
audio. Its codec and short-clip limits are documented in
`services/remoteos-sdl/PROTOCOL.md`. There may be four decoded videos globally.

Create a `video` node referencing a finished asset, then send `media.control`
with `action: "play"`. Playback progresses without application polls or frame
messages, even when the window is hidden. Resizing flushes pending decoded
frames and resumes at the last sampled playback position. Pause freezes the
clock; stop resets to zero; EOF emits one `media_ended` event. Play at EOF
restarts the asset. Decoder errors emit `media_error` without closing other UI
sessions. Audio seek leaves playback paused; video seek preserves play/pause.

There is currently one exclusive audio-output owner, with no mixing. Competing
players get an explicit error. WAV pause/stop/EOF releases the output; a video
retains its device until `resource.release` or disconnect, including while
paused. Removing the last video node pauses it; release the asset to free its
decoder/device. This policy avoids one OS silently interrupting another's audio.

## Ownership and limits

Each TCP connection owns an independent namespace and all its views. No client
can address another client's resources. Disconnect destroys its views, focus,
queued actions, decoded assets, and host render targets. Reconnect creates a fresh session; replay desired state
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
make test-message-desktop             # integration plus standalone subprocess E2E suite
make test-chipset                     # existing host regression gate plus protocol tests
```

The original SDL integration test verifies the example's Pause action,
rendering/capture, dragging while an application is idle, and close delivery. It writes a BMP capture to
`/tmp/message-desktop.bmp` (override with `MESSAGE_DESKTOP_CAPTURE`).

The standalone suite requires Ruby and the ffmpeg CLI in addition to the normal
SDL backend dependencies. It launches the actual executable on an ephemeral
TCP port for each scenario, with dummy SDL display/audio and real decoding and
software depth rendering. It imports no service implementation or fake renderer.
Independent Python and Ruby clients share the service concurrently.

The suite checks geometry, stacking, visibility, maximize/restore, drag/resize,
close/destroy, semantic controls, depth-order pixels, stable retained scene
render counts, video color/seek/EOF, audio queue consumption and ownership,
resource cleanup, malformed requests, event overflow, and process shutdown.
Every service shutdown verifies that its backend process has also exited.
Tests of media playback let the application go idle while the host advances it.

For logs and BMP evidence, preserve artifacts with:

```sh
MESSAGE_DESKTOP_E2E_ARTIFACTS=build/message-desktop-e2e make test-message-desktop
```

`--headless --automation-dir <directory>` enables `debug.input`, `debug.capture`,
and `debug.stats` only on a loopback-bound service. Input is injected through
the real RemoteOS event path. Captures get service-generated filenames in that
directory; clients cannot specify filesystem paths. `debug.stats` includes
device operation counts and resource counts. These operations are disabled
by default and are verification facilities, not the application rendering API.

This proves the standalone protocol with hosted Python and Ruby, not booted
PythonOS/RubyOS guests or physical speaker/GPU behavior. The prototype still
has a fixed display size, vertical layout, ASCII text, end-only entry editing,
and a single-series plot. Full menus, scrollable containers, rich text/IME,
images, clipboard, accessibility, general scene graphs/materials, recording,
audio mixing, and session restoration remain future work. It is not yet a
drop-in implementation of every control used by either OS.
