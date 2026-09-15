# PythonOS v0.4.1: The Mac Is Invited to CI This Time

An operating system written in Python deserves an installation story that
requires fewer archaeological qualifications. This patch release turns the
fresh-checkout audit into repeatable checks, ships a local packaging command,
and makes macOS a required participant in the release gate. Revolutionary:
testing the platform we said we supported.

## Three build hosts, two bootable images

GitHub CI now runs the documented build and full release gate on Linux x86_64,
Linux ARM64, and macOS Intel. The Mac job uses Colima's Docker builder plus
native Homebrew QEMU and SDL. Every matrix cell contributes to the required
`all-arches` result. Published media remain `pythonos.iso` and
`pythonos-arm64.elf`: guest architecture is not the same thing as host OS,
despite how much easier the marketing spreadsheet would be if it were.

Apple Silicon was separately built and boot-tested on puck. WSL2/WSLg is still
unverified. A green Linux badge does not confer honorary Windows citizenship.

## Packages without publishing by accident

`make package` validates the selected architecture and produces local boot
media, documentation, provenance and a checksum. Existing development disks
are excluded; your private files are not release-note Easter eggs. This is
boot media, not a dependency-free desktop installer.

Fresh-clone instructions now initialize the shared SDL submodule and list SDL
dependencies. Both OSes pin RemoteOS-SDL 0.1.1, whose standalone packages now
include Linux ARM64 and whose checksums work beside the downloaded archive.

## Tests that wait for the answer

Cross-building on DGX Spark exposed fragmented REPL output being mistaken for
a completed command. Serial and GUI tests now use explicit completion lines,
reject incomplete responses, and budget compositor startup for emulated CPUs.
Six regression tests cover framing. The local x86_64 checks passed 59 serial,
26 GUI, 5 desktop and 6 audio assertions; ARM64 passed 37 serial and 8 GUI
checks. The audio check accepted a header-only WAV, not proof of audible PCM.
No invented latency victory lap is included.

## Executive summary

Required macOS CI, honest platform coverage, local release media, and tests
that read the whole response. Python still owns the kernel; the shared SDL
service still owns host devices. Protocol v2 still requires a trusted network
or authenticated tunnel.

[PythonOS v0.4.1](https://github.com/jordanhubbard/pythonos/releases/tag/v0.4.1)
