# Coordinated release installation audit

Audited PythonOS v0.4.0, RubyOS v0.2.0, and RemoteOS-SDL v0.1.0 from
fresh GitHub clones on Linux ARM64 (`sparky`) and macOS ARM64 (`puck.local`).
The working checkouts and existing runtime installations were not used as
the build inputs. Host dependencies and Docker image caches already existed;
this is a fresh-checkout audit, not a clean-machine dependency installation.

## Results

| Check | Result |
| --- | --- |
| Linux PythonOS default source build | Pass |
| Linux PythonOS ARM64 serial gate | 37 passed, 0 failed |
| Linux Ruby source build, kernel and teaching examples | Pass; private CRuby 4.0.6 |
| Linux RubyOS fresh freestanding build and native TCP desktop | Pass |
| macOS Ruby source build and hosted build/test suite | Pass; private CRuby 4.0.6 |
| Linux and macOS RemoteOS-SDL source build/protocol tests | Pass |
| RubyOS Linux release checksum and relocated runtime | Pass |
| RubyOS Linux released native-TCP ELF with bundled SDL service | Pass |
| RubyOS macOS released runtime and bundled SDL protocol test | Pass |
| Standalone macOS RemoteOS-SDL release protocol test | Pass |
| Released PythonOS ELF with released SDL on Linux and macOS | Pass; 1024x768 captures inspected |
| macOS PythonOS Docker source build | Blocked by Docker Desktop Keychain access over SSH |
| Windows WSL2 | Not tested; no accessible WSL2 host |

The Linux ARM64 SDL binary was taken from the RubyOS release bundle. The
standalone SDL release publishes Linux x86_64 and macOS ARM64, so no standalone
Linux ARM64 asset was available. Linux x86_64 native-host installation was not
tested in this audit.

## Fixes from the audit

- Both OS guides now include recursive cloning and existing-checkout submodule
  initialization. Without that step, `make bridge` fails in an ordinary clone.
- Both build targets explain the recovery command when the submodule is absent.
- The guides list SDL dependencies and distinguish WSL2/WSLg requirements.
- PythonOS's default build description now gives the architecture-dependent
  image path.
- RemoteOS-SDL run examples use the source-build executable path and document
  the release archive layout, architecture availability, and shared libraries.
- RubyOS's hosted, VirtIO-console and debugger smokes now set
  `REMOTEOS_SDL_MODE`; the former language-specific variable was ignored.
- Newly packaged RemoteOS-SDL checksum files refer to the archive basename,
  allowing verification beside the downloaded archive. Published 0.1.0 files
  retain their original `dist/` prefix; the guide describes how to verify them.

Changed RubyOS hosted, native GUI, and debugger tests pass. PythonOS chipset
and bridge gates pass. The new SDL archive checksum verifies from its download
directory. Published tags and assets have not been rewritten.

## Evidence locations

Linux logs, clones, release downloads, and inspected captures are retained at
`/home/jkh/Src/release-audit.6Rc0Lw/`. macOS evidence is retained at
`/Users/jkh/Src/release-audit.BzPSER/` on puck. Desktop captures were taken in
headless SDL mode; this does not test physical mouse/keyboard interaction or
audible playback.

mac task `task_62fe904dad7f4a9bb420a89ee4023019` tracks this audit.
`task_cb3d67092e494d1f9ce9136790fc5076` tracks actual WSL2 verification.
