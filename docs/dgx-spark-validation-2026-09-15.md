# DGX Spark release-media validation

Host: `sparky`, Linux `aarch64`, kernel `6.17.0-1032-nvidia`, Docker 29.2.1.
These are local source builds and packaging checks, not published releases.
Existing source-built runtime caches were reused where current; RemoteOS-SDL
was force-recompiled. No system Ruby package was used: the bundled runtime
reports Ruby 4.0.6 with PRISM, built for `aarch64-linux`.

## Native ARM64 results

| Project | Command | Result |
| --- | --- | --- |
| PythonOS | `make package` | Release gate passed: chipset tests, SDL bridge, 37 serial checks, 8 GUI checks; archive checksum verified |
| RubyOS | `make release-linux` | Full parity gate passed, including ARM64 and x86_64 boot, desktop, storage, network, input, audio, SMP and debugger tests; archive checksum verified |
| RemoteOS-SDL | `make -B package` | Native recompilation and protocol-v2 tests passed; archive checksum verified |

RubyOS's archive was extracted separately and its bundled Ruby successfully
ran the hosted kernel. RemoteOS-SDL's extracted executable passed protocol
tests. The native SDL binaries dynamically link host libraries; these archives
are not dependency-free installers. Headless GUI tests do not validate physical
display/input or audible sound, and none exercises DGX GPU acceleration.

Artifacts are in each repository's `dist/` directory:

- PythonOS: `pythonos-v0.4.0-2-gaac0414-dirty-linux-arm64.tar.gz`
- PythonOS cross-built: `pythonos-v0.4.0-2-gaac0414-dirty-linux-x86_64.tar.gz`
- RubyOS: `rubyos-0.2.0-linux-arm64.tar.gz`
- RemoteOS-SDL: `remoteos-sdl-0.1.0-linux-aarch64.tar.gz`

Each has a neighboring `.sha256` file, checked from its `dist/` directory.
The PythonOS dirty suffix explicitly identifies an uncommitted working-tree
build. Its package contains boot media, documentation and build provenance;
it does not copy the developer's persistent disk.

## Additional x86_64 cross-build

The first PythonOS x86_64 serial gate reported 40 passed and 19 failed.
Failures showed the preceding command's output instead of the current one:
for example, `sh('examples')` received the response to `sh('desktop --list')`.
A TCP chunk ending at a literal prompt inside help text caused the test reader
to stop early. The harness now sends a separate completion marker and requires
that full output line plus the final prompt. Regression tests cover fragmented
prompts/markers, echoed markers and missing completion. The first raw log was
overwritten by the rerun; this summary records the observed failure evidence.

The first rerun passed all 59 serial checks, but the GUI gate exposed a second
timing issue: its 2.5-second command deadline expired partway through an echoed
completion command under cross-architecture TCG (25 passed, 1 failed). The GUI
reader now uses short completion commands, exact marker-line matching, a
15-second minimum command deadline, and fails closed on incomplete responses.
Compositor startup uses the existing 60-second first-redraw budget, since
guest PNG decoding can block the completion response under TCG. With these
changes, the serial gate passed 59/59 and the GUI gate passed 26/26.
Six host-only regression tests cover the serial and GUI framing behavior.
The separate desktop test passed 5/5 and audio test passed 6/6, after which
the x86_64 media was packaged and its checksum verified. Audio produced only
a valid WAV header, which the existing test accepts; this does not establish
non-silent PCM output. The full set of release-gate components passed across
the serial and final GUI reruns, without bypassing a failed component.

## Evidence and CI boundary

Local logs are `/tmp/pythonos-sparky-package.log`,
`/tmp/pythonos-sparky-x86-package.log` (GUI failure),
`/tmp/pythonos-sparky-x86-package-final.log`, `/tmp/pythonos-chipset-final.log`,
`/tmp/pythonos-sparky-x86-gui-final.log`,
`/tmp/rubyos-sparky-release.log`, and `/tmp/remoteos-sparky-release.log`.

The proposed PythonOS CI matrix adds macOS Intel alongside both Linux
architectures. Workflow lint and local regression tests pass; a hosted run of
the changed workflow is still required after publication. The manual macOS
ARM64 audit is separate evidence, not a substitute for that run. WSL2 remains
unverified. Tracking: `task_e98334d392db49af933c4daffbfb7d6c` (local media) and
`task_5f19d52fb9cc43e3be17e0c22a074458` (CI).
