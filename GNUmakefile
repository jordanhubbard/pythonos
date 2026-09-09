# PythonOS build system — GNU make required (gmake on macOS: brew install make)
#
# All compilation happens inside Docker (cross-toolchain + python3.14 guaranteed).
# QEMU runs on the host to boot the resulting ISO/ELF.
#
# Top-level targets default to the *host* architecture so local dev cycles
# don't unnecessarily emulate a non-native CPU. Override with
# `make TARGET_ARCH=arm64` or `make TARGET_ARCH=x86_64` to cross-build, or
# call the explicit per-arch targets directly (`make x86_64`, `make arm64`,
# `make run-x86_64`, `make run-arm64`, `make test-x86_64`, `make test-arm64`).
#
# First-time setup (takes ~10 min to cross-compile libpython):
#   make
#
# Subsequent builds (fast — libpython is cached):
#   make          # build for the host architecture
#   make run      # build if needed, then boot in QEMU (serial console)
#   make test     # build, boot, run smoke test
#   make stop     # kill running QEMU instance
#   make clean    # remove build artifacts (keeps libpython cache)
#   make cleanall # remove everything including libpython
#
# This file is named GNUmakefile so GNU make picks it up automatically
# (ahead of Makefile). The Makefile at the repo root is a BSD-make stub.

.DEFAULT_GOAL := all

# Disable built-in suffix rules (cc -c foo.c, etc.) so only our recipes run.
.SUFFIXES:
MAKEFLAGS += --no-builtin-rules

# Delete the target if a recipe fails halfway through writing it.
.DELETE_ON_ERROR:

# Recursively expand $2 under directory $1 (GNU make). Used for Python
# sources so a new file in any subdirectory invalidates the ISO/ELF.
rwildcard = $(strip $(foreach d,$(wildcard $1*),$(call rwildcard,$d/,$2)) $(wildcard $1$2))

# ── Host architecture and acceleration detection ─────────────────────────────
# `uname -m` reports the running kernel's CPU arch. Apple Silicon Macs report
# "arm64", Linux on ARM reports "aarch64", x86_64 hosts report "x86_64". Map
# both ARM forms to our internal "arm64" label.
HOST_UNAME_M := $(shell uname -m)
ifeq ($(HOST_UNAME_M),arm64)
HOST_ARCH := arm64
else ifeq ($(HOST_UNAME_M),aarch64)
HOST_ARCH := arm64
else
HOST_ARCH := x86_64
endif

HOST_OS := $(shell uname -s)
ifeq ($(HOST_OS),Darwin)
HOST_ACCEL := hvf
# Homebrew QEMU on macOS is typically built with cocoa (not SDL); audio uses
# CoreAudio. Override with QEMU_DISPLAY=sdl QEMU_AUDIODEV=sdl on builds that
# include SDL support.
QEMU_DISPLAY  ?= cocoa
QEMU_AUDIODEV ?= coreaudio
else ifeq ($(HOST_OS),Linux)
# GitHub-hosted runners often lack /dev/kvm; do not pass -accel kvm then.
ifneq ($(wildcard /dev/kvm),)
HOST_ACCEL := kvm
else
HOST_ACCEL :=
endif
QEMU_DISPLAY  ?= sdl
QEMU_AUDIODEV ?= sdl
else
HOST_ACCEL :=
QEMU_DISPLAY  ?= sdl
QEMU_AUDIODEV ?= sdl
endif

# TARGET_ARCH: which arch the dispatch targets (all/run/test/stop/restart)
# build and run. Defaults to the host architecture so `make` does the
# obvious thing on every machine. Set it explicitly to cross-build.
TARGET_ARCH ?= $(HOST_ARCH)

# Acceleration: when guest matches host and an accelerator is available, use
# it (HVF on macOS, KVM on Linux). Otherwise fall back to TCG. Each per-arch
# QEMU flag set picks up the right flag below.
QEMU_X86_ACCEL :=
QEMU_X86_CPU   := -cpu qemu64
ifeq ($(HOST_ARCH),x86_64)
ifneq ($(HOST_ACCEL),)
QEMU_X86_ACCEL := -accel $(HOST_ACCEL)
QEMU_X86_CPU   := -cpu host
endif
endif

# arm64 acceleration. Linux/KVM currently stalls before GIC init on this
# kernel, so it is opt-in via ARM64_KVM=1 until the EL2/GIC path is fixed.
# Native HVF on Apple Silicon is still gated on a QEMU upstream bug — Apple
# Silicon's hypervisor framework doesn't set the ISV (Instruction
# Syndrome Valid) bit on stage-2 data aborts for GIC MMIO and QEMU's
# hvf.c asserts on that. When the upstream lands, set ARM64_HVF=1 and
# HVF will Just Work — the GICv3 driver is already in place.
ARM64_KVM ?= 0
ARM64_HVF ?= 0
QEMU_ARM64_ACCEL :=
QEMU_ARM64_CPU   := -cpu cortex-a57
ifeq ($(HOST_ARCH),arm64)
ifneq ($(HOST_ACCEL),)
ifeq ($(HOST_ACCEL),kvm)
ifeq ($(ARM64_KVM),1)
QEMU_ARM64_ACCEL := -accel $(HOST_ACCEL)
QEMU_ARM64_CPU   := -cpu host
endif
else
ifeq ($(ARM64_HVF),1)
QEMU_ARM64_ACCEL := -accel $(HOST_ACCEL)
QEMU_ARM64_CPU   := -cpu host
endif
endif
endif
endif

# Internal arch label used by the x86_64 build paths below; do not change.
ARCH         := x86_64
BUILD        := build
BUILD_ARM64  := build-arm64
ISO_DIR      := $(BUILD)/iso
ISO_OUT      := $(BUILD)/pythonos.iso
ARM64_ELF    := $(BUILD_ARM64)/pythonos-arm64.elf
KERNEL_ELF   := $(BUILD)/pythonos.elf
GRUB_CFG     := src/boot/grub.cfg
DOCKER_IMG   := pythonos-builder
LIBPYTHON    := deps/cpython/libpython3.14.a
LIBPYTHON_ARM64 := deps-arm64/cpython/libpython3.14.a

ifeq ($(HOST_ARCH),arm64)
HOST_DOCKER_ARCH := arm64
else ifeq ($(HOST_ARCH),x86_64)
HOST_DOCKER_ARCH := amd64
else
HOST_DOCKER_ARCH := $(HOST_UNAME_M)
endif
DOCKER_PLATFORM ?= linux/$(HOST_DOCKER_ARCH)
DOCKER_USER ?= $(shell id -u):$(shell id -g)

# Persistent storage for /home and /apps (epic pythonos-ef6). One ext2 image
# shared between x86_64 and arm64 — / stays tmpfs. Built inside the Docker
# container via tools/build_disk.sh; see tools/Dockerfile for e2fsprogs.
DISK_IMG     := $(BUILD)/disk.img
DISK_SIZE_MB ?= 64
ARM64_DISK   := $(DISK_IMG)
REPL_HOST_PORT ?= $(if $(PYTHONOS_HOST_PORT),$(PYTHONOS_HOST_PORT),5555)
FILE_HOST_PORT ?= $(if $(PYTHONOS_FILE_PORT),$(PYTHONOS_FILE_PORT),17000)
ARM64_REPL_HOST_PORT ?= 5556
ARM64_FILE_HOST_PORT ?= 17002
SMP_CPUS ?= $(if $(PYTHONOS_SMP_CPUS),$(PYTHONOS_SMP_CPUS),2)
PYTHONOS_FREE_THREADING ?= 1

# Shared x86 QEMU machine bits. Serial vs GUI only differs in the display
# and serial flags at the end. `-vga std` activates QEMU's bochs-VBE
# adapter; GRUB negotiates the framebuffer through multiboot2 (see
# src/boot/boot.asm) and the kernel picks it up via parse_mb2_framebuffer().
QEMU_X86_BASE := -machine q35 $(QEMU_X86_CPU) $(QEMU_X86_ACCEL) -m 2G -smp $(SMP_CPUS) \
              -netdev user,id=net0,hostfwd=tcp::$(REPL_HOST_PORT)-:5000,hostfwd=tcp::$(FILE_HOST_PORT)-:7000 -device virtio-net-pci,netdev=net0 \
              -device intel-hda -device hda-duplex \
              -no-reboot -no-shutdown \
              -drive if=none,file=$(DISK_IMG),format=raw,id=hd0 \
              -device virtio-blk-pci,drive=hd0 \
              -cdrom $(ISO_OUT) -boot d
QEMU_FLAGS     := $(QEMU_X86_BASE) -nographic -serial mon:stdio
QEMU_GUI_FLAGS := $(QEMU_X86_BASE) -display $(QEMU_DISPLAY) -vga std -serial stdio

# Host-side docker invocations. CURDIR is make's working directory; PWD can
# be inherited from the environment and point somewhere else.
# Do not pass $(MAKE) into the container — that is the host binary.
DOCKER_RUN = docker run --rm --platform $(DOCKER_PLATFORM) --user $(DOCKER_USER) \
             -v $(CURDIR):/work -w /work

# ── User-facing targets ───────────────────────────────────────────────────────

.PHONY: all build build-gui run run-gui start stop restart test clean cleanall \
        docker-build help disk-image \
        bridge bridge-clean test-bridge \
        _freeze _iso _iso_arm64 \
        x86_64 run-x86_64 stop-x86_64 test-x86_64 run-gui-x86_64 test-gui-x86_64 run-fb-x86_64 \
        arm64 run-arm64 stop-arm64 test-arm64 test-arm64-gicv3 run-gui-arm64 run-fb-arm64 \
        run-fb test-gui test-chipset


# ── Help ─────────────────────────────────────────────────────────────────────
# Show the user-facing top-level targets and the env vars that tweak them.
# Per-arch explicit forms (run-x86_64, run-arm64, etc.) are described in
# the explicit per-arch sections further down — the defaults dispatch to
# the host arch automatically.

help:
	@echo "PythonOS — top-level make targets"
	@echo ""
	@echo "Two flavors, two pairs of targets:"
	@echo ""
	@echo "  Minimal (text REPL, no GUI):"
	@echo "    make build              Build the kernel ISO/ELF only"
	@echo "    make run                Boot in QEMU; serial REPL on stdio"
	@echo ""
	@echo "  Full GUI (kernel + pythonos_bridge host companion):"
	@echo "    make build-gui          Build kernel AND pythonos_bridge"
	@echo "    make run-gui            Boot kernel + spawn bridge + open SDL"
	@echo "                              desktop with full app dock"
	@echo ""
	@echo "Build flags:"
	@echo "  make TARGET_ARCH=x86_64   Cross-build for x86_64"
	@echo "  make TARGET_ARCH=arm64    Cross-build for arm64"
	@echo "  make docker-build         Rebuild the Docker cross-toolchain image"
	@echo "  make disk-image           Build build/disk.img (ext2, /home + /apps)"
	@echo "  make clean                Remove build artifacts (keeps libpython cache)"
	@echo "  make cleanall             Remove everything including libpython"
	@echo ""
	@echo "Lifecycle:"
	@echo "  make stop                 Kill the running QEMU instance"
	@echo "  make restart              stop + run"
	@echo ""
	@echo "Legacy GUI (QEMU framebuffer console window — pre-bridge path):"
	@echo "  make run-fb               Open QEMU's own SDL/cocoa window;"
	@echo "                            kernel pushes pixels to ramfb directly"
	@echo ""
	@echo "Test:"
	@echo "  make test-chipset         Host-side chipset, arcade, dock, layout, and CI-gate tests (no QEMU)"
	@echo "  make test                 Boot in QEMU, run TCP-REPL smoke tests"
	@echo "                            (x86: 41 tests, arm64: 28 tests)"
	@echo "  make test-gui             Run headless GUI smoke tests"
	@echo "                            (x86: 23+5+6 tests; arm64: 8 tests)"
	@echo "  make validate-release     Host-arch build + smoke (CI runs each arch)"
	@echo ""
	@echo "Per-arch explicit forms (run-x86_64, run-arm64, run-gui-x86_64, etc.)"
	@echo "exist for every dispatched target above — useful when both archs are"
	@echo "built side-by-side."
	@echo ""
	@echo "Environment variables:"
	@echo "  TARGET_ARCH={x86_64,arm64}     which arch the dispatch targets build/run"
	@echo "                                 (default: host = $(HOST_ARCH))"
	@echo "  DOCKER_PLATFORM=linux/<arch>   builder image platform"
	@echo "                                 (default: host = $(DOCKER_PLATFORM))"
	@echo "  DOCKER_USER=<uid:gid>          user for Docker volume writes"
	@echo "                                 (default: host = $(DOCKER_USER))"
	@echo "  SMP_CPUS=<n>                   number of x86_64 vCPUs (default: 2)"
	@echo "  ARM64_SMP_CPUS=<n>             number of arm64 vCPUs (default: 2)"
	@echo "  PYTHONOS_FREE_THREADING={0,1}  build CPython with --disable-gil (x86 default 1)"
	@echo "  PYTHONOS_GUI_APP=<name>        which app run-gui auto-launches"
	@echo "                                 (bouncing_ball | terminal | editor | files |"
	@echo "                                  image_viewer | audio_tone | sprites | toaster)"
	@echo "  PYTHONOS_BRIDGE_HOST=<addr>    bridge listen address (default: 127.0.0.1)"
	@echo "  PYTHONOS_BRIDGE_PORT=<port>    bridge TCP port (default: 17010)"
	@echo "  PYTHONOS_BRIDGE_GUEST_PORT=<p> native guest bridge port (default: 5001)"
	@echo "  PYTHONOS_BRIDGE_TRANSPORT=<m>  native-tcp (default) or chardev"
	@echo "  PYTHONOS_BRIDGE_EXTERNAL=1     use an already-running remote bridge"
	@echo "  PYTHONOS_GOLDEN_REFRESH=1      regenerate test screendump goldens"
	@echo ""
	@echo "TCP REPL access (host → guest forwarded ports):"
	@echo "  nc localhost 5555              x86_64 default + GUI"
	@echo "  nc localhost 5556              arm64"
	@echo ""
	@echo "More:"
	@echo "  README.md             quickstart, shell + Python REPL surface"
	@echo "  docs/gui.md           GUI subsystem (compositor, apps, sdl2, decoders)"
	@echo "  mac task list --project pythonos --state=open"

# Top-level dispatch. These pick x86_64 or arm64 based on TARGET_ARCH (which
# defaults to the host arch). Explicit per-arch targets are listed below.
#
#   build / run        — minimal text-REPL kernel.
#   build-gui / run-gui — kernel + pythonos_bridge + host SDL desktop.
#   run-fb              — legacy: QEMU's own framebuffer window (no bridge).
ifeq ($(TARGET_ARCH),arm64)
all:         arm64
run:         run-arm64
stop:        stop-arm64
test:        test-arm64
run-fb:      run-fb-arm64
run-gui:     run-gui-arm64
else
all:         x86_64
run:         run-x86_64
stop:        stop-x86_64
test:        test-x86_64
run-fb:      run-fb-x86_64
run-gui:     run-gui-x86_64
endif

build:     all
build-gui: all bridge
start:     run
restart:   stop start

# ── Persistent disk image (ext2, /home + /apps) ──────────────────────────────
# Built inside the build container — see tools/build_disk.sh and
# tools/Dockerfile (e2fsprogs). The image is consumed by the QEMU run
# targets on both arches via virtio-blk. `make clean` removes build/.

disk-image: $(DISK_IMG)

$(DISK_IMG): tools/build_disk.sh .docker-image
	@mkdir -p $(BUILD)
	$(DOCKER_RUN) $(DOCKER_IMG) bash tools/build_disk.sh $(DISK_IMG) $(DISK_SIZE_MB)

# ── pythonos_bridge (host-side companion linking SDL2) ───────────────────────
# See tools/pythonos_bridge/main.c. The bridge is a host program — entirely
# separate from the kernel build. Slice 1 ships the protocol loop + a
# --selftest mode that opens an SDL2 window directly. Later slices add the
# display/audio/input ops that PythonOS apps will drive.

bridge:
	$(MAKE) -C tools/pythonos_bridge

bridge-clean:
	$(MAKE) -C tools/pythonos_bridge clean

test-bridge: bridge
	python3 tools/pythonos_bridge/test_client.py

# Explicit x86_64 targets (also reachable as the dispatch default on x86 hosts).
x86_64: $(ISO_OUT)

run-x86_64: $(ISO_OUT) $(DISK_IMG)
	qemu-system-x86_64 $(QEMU_FLAGS)

# Legacy framebuffer mode for x86_64: opens QEMU's own SDL window
# driven by the bochs-VBE adapter. Kernel writes pixels straight to
# ramfb. Pre-bridge path; kept for `make run-fb`.
run-fb-x86_64: $(ISO_OUT) $(DISK_IMG)
	qemu-system-x86_64 $(QEMU_GUI_FLAGS)

# Bridge desktop mode for x86_64: supervises pythonos_bridge as a sibling
# process. Default transport is native guest TCP; set
# PYTHONOS_BRIDGE_TRANSPORT=chardev to use the older COM2 path.
run-gui-x86_64: $(ISO_OUT) $(DISK_IMG) bridge
	QEMU_DISPLAY=$(QEMU_DISPLAY) QEMU_AUDIODEV=$(QEMU_AUDIODEV) \
	    PYTHONOS_DISK=$(DISK_IMG) \
	    python3 tools/run_gui.py $(ISO_OUT)

stop-x86_64:
	@pkill -f "[q]emu-system-x86_64.*$(ISO_OUT)" || echo "No x86_64 QEMU running."

test-chipset:
	python3 tests/chipset_test.py
	python3 tests/arcade_test.py
	python3 tests/dock_test.py
	python3 tests/layout_test.py
	python3 tests/ci_gate_test.py

test-x86_64: test-chipset $(ISO_OUT) $(DISK_IMG)
	PYTHONOS_HOST_PORT=$(REPL_HOST_PORT) PYTHONOS_FILE_PORT=$(FILE_HOST_PORT) PYTHONOS_SMP_CPUS=$(SMP_CPUS) PYTHONOS_FREE_THREADING=$(PYTHONOS_FREE_THREADING) python3 tests/smoke_test.py $(ISO_OUT)

# GUI smoke test — boots with -display none -vga std (headless GUI mode)
# and exercises the sdl2 shim, compositor import, and serial markers.
# `make test-gui` dispatches per host arch (x86: gui+desktop+audio; arm64: gui).
test-gui-x86_64: $(ISO_OUT)
	python3 tests/gui_smoke_test.py $(ISO_OUT)
	python3 tests/desktop_smoke_test.py $(ISO_OUT)
	python3 tests/audio_smoke_test.py $(ISO_OUT)

test-gui-arm64: $(ARM64_ELF) $(ARM64_DISK)
	python3 tests/gui_smoke_test_arm64.py $(ARM64_ELF)

ifeq ($(TARGET_ARCH),arm64)
test-gui: test-gui-arm64
else
test-gui: test-gui-x86_64
endif

# ── Release automation ──────────────────────────────────────────────────────
# Same shape as nanolang/scripts/release.sh: clean main + GitHub CLI auth +
# local validation gate + push + wait for CI + annotated tag + GitHub release.
# scripts/release.sh accepts `major`, `minor`, `patch`, or an explicit X.Y.Z;
# the Makefile wrappers are sugar so users can type `make release-minor`
# instead of `./scripts/release.sh minor`.
.PHONY: release release-major release-minor release-patch validate-release
release: release-patch

release-patch:
	@./scripts/release.sh patch

release-minor:
	@./scripts/release.sh minor

release-major:
	@./scripts/release.sh major

validate-release:
	@./scripts/validate-release.sh

clean:
	rm -rf build iso \
	       build-arm64 \
	       pythonos.iso pythonos-arm64.elf disk-arm64.img \
	       deps/cpython deps/cpython-src \
	       deps-arm64/cpython deps-arm64/cpython-src

cleanall: clean
	rm -rf .docker-image deps/Python-*.tar.xz deps-arm64/Python-*.tar.xz

# ── Docker image (rebuild when the Dockerfile is newer than the stamp) ───────
# Previously this stamp depended on a FORCE target, which marked every
# ISO/ELF out of date on every `make`. Depend on the Dockerfile instead.

.docker-image: tools/Dockerfile
	docker build --platform $(DOCKER_PLATFORM) --load -t $(DOCKER_IMG) -f tools/Dockerfile .
	printf '%s %s\n' "$(DOCKER_IMG)" "$(DOCKER_PLATFORM)" > $@

# Always rebuild the image (new platform, broken stamp, Dockerfile pulled in).
docker-build:
	$(MAKE) -B .docker-image

# ── CPython library (slow, cached — only rebuild if missing) ─────────────────

$(LIBPYTHON): tools/setup_cpython.sh .docker-image
	$(DOCKER_RUN) -e PYTHONOS_FREE_THREADING=$(PYTHONOS_FREE_THREADING) $(DOCKER_IMG) \
	  ./tools/setup_cpython.sh --build

# ── Source file sets ($(wildcard) is evaluated by Make, not a subshell) ──────

BOOT_SRC   := $(wildcard src/boot/*.c src/boot/*.h src/boot/*.asm src/boot/*.S src/boot/*.cfg src/boot/*.ld)
HAL_SRC    := $(wildcard src/hal/*.c  src/hal/*.h)
LIBC_SRC   := $(wildcard src/libc/*.c src/libc/include/*.h src/libc/include/sys/*.h)
LINENOISE_SRC := $(wildcard src/linenoise/*.c src/linenoise/*.h)
KERNEL_PY  := $(call rwildcard,kernel/,*.py) $(call rwildcard,apps/,*.py)
ASYNCIO_PY := $(wildcard asyncio/*.py)
STUBS_PY   := $(call rwildcard,tools/stdlib_stubs/,*.py)
EXAMPLES_SRC := $(wildcard examples/*.py examples/*.txt)

# Python sources shared by both architectures
KERNEL_DEPS := $(KERNEL_PY) $(ASYNCIO_PY) $(STUBS_PY) $(EXAMPLES_SRC) tools/freeze_kernel.py

# ── Kernel ISO (fast — skips libpython rebuild) ──────────────────────────────

$(ISO_OUT): $(LIBPYTHON) \
            $(BOOT_SRC) $(HAL_SRC) $(LIBC_SRC) $(LINENOISE_SRC) $(KERNEL_DEPS) \
            src/linker.ld src/boot/grub.cfg .docker-image
	$(DOCKER_RUN) $(DOCKER_IMG) make _iso
	@echo "ISO ready: $(ISO_OUT)"

# ── Internal targets — called from inside Docker, not directly by users ───────

TARGET  := $(ARCH)-elf
CC      := $(TARGET)-gcc
LD      := $(TARGET)-ld
AS      := nasm

CPYTHON    := deps/cpython
PYTHON_INC := $(CPYTHON)/Include
PYTHON_LIB := $(CPYTHON)/libpython3.14.a

COMMON_CFLAGS := -std=c11 -O2 -ffreestanding -fno-stack-protector -fno-pie \
                 -mno-red-zone -Wall -Wextra \
                 -I src/libc/include \
                 -I src/linenoise \
                 -I $(PYTHON_INC) -I $(CPYTHON)

BOOT_CFLAGS := $(COMMON_CFLAGS) -mno-sse -mno-sse2 -mno-mmx -mno-80387
KERN_CFLAGS := $(COMMON_CFLAGS)
ASFLAGS     := -f elf64
LIBGCC      := $(shell $(CC) -print-libgcc-file-name 2>/dev/null || echo "")
LDFLAGS     := -T src/linker.ld -nostdlib -z max-page-size=0x1000
# Generated next to each .o so header edits rebuild the right objects
# inside Docker. Recursive `=` so $(@) is the current target.
DEPFLAGS = -MMD -MP -MF $(@:.o=.d)

BOOT_ASM  := src/boot/boot.asm src/boot/isr_stubs.asm \
             src/boot/kthread_switch.asm src/boot/ap_trampoline_blob.asm
BOOT_C    := src/boot/gdt.c src/boot/idt.c src/boot/main.c \
             src/boot/pit.c src/boot/fb.c src/boot/kthread.c src/boot/smp.c \
             src/boot/tls.c
HAL_C     := src/hal/hal.c
LIBC_C    := src/libc/malloc.c  src/libc/string.c  src/libc/stdio.c \
             src/libc/time.c    src/libc/syscalls.c src/libc/math.c  \
             src/libc/pthread.c
LINENOISE_C := src/linenoise/linenoise.c

BOOT_OBJS := $(patsubst src/%.asm,$(BUILD)/%.asm.o,$(BOOT_ASM))
BOOT_OBJS += $(patsubst src/%.c,$(BUILD)/%.c.o,$(BOOT_C))
HAL_OBJS  := $(patsubst src/%.c,$(BUILD)/%.c.o,$(HAL_C))
LIBC_OBJS := $(patsubst src/%.c,$(BUILD)/%.c.o,$(LIBC_C))
LINENOISE_OBJS := $(patsubst src/%.c,$(BUILD)/%.c.o,$(LINENOISE_C))

AP_TRAMPOLINE_BIN := $(BUILD)/boot/ap_trampoline.bin

$(BUILD)/%.asm.o: src/%.asm
	@mkdir -p $(dir $@)
	$(AS) $(ASFLAGS) $< -o $@

$(AP_TRAMPOLINE_BIN): src/boot/ap_trampoline16.asm
	@mkdir -p $(dir $@)
	$(AS) -f bin $< -o $@

$(BUILD)/boot/ap_trampoline_blob.asm.o: src/boot/ap_trampoline_blob.asm $(AP_TRAMPOLINE_BIN)
	@mkdir -p $(dir $@)
	$(AS) $(ASFLAGS) $< -o $@

$(BUILD)/boot/%.c.o: src/boot/%.c
	@mkdir -p $(dir $@)
	$(CC) $(BOOT_CFLAGS) $(DEPFLAGS) -c $< -o $@

$(BUILD)/hal/%.c.o: src/hal/%.c
	@mkdir -p $(dir $@)
	$(CC) $(KERN_CFLAGS) $(DEPFLAGS) -c $< -o $@

$(BUILD)/libc/%.c.o: src/libc/%.c
	@mkdir -p $(dir $@)
	$(CC) $(KERN_CFLAGS) $(DEPFLAGS) -c $< -o $@

# linenoise warns -Wunused-result on the read/write return values it
# intentionally discards; suppress to keep the kernel build warning-free.
$(BUILD)/linenoise/%.c.o: src/linenoise/%.c
	@mkdir -p $(dir $@)
	$(CC) $(KERN_CFLAGS) $(DEPFLAGS) -Wno-unused-result -Wno-unused-but-set-variable -c $< -o $@

ENCODINGS_SRC := deps/cpython-src/Lib/encodings
CPYTHON_LIB   := deps/cpython-src/Lib
STDLIB_SHIM   := $(BUILD)/stdlib_shim

STDLIB_REAL_FILES := \
	enum.py typing.py operator.py types.py \
	reprlib.py keyword.py copy.py weakref.py _weakrefset.py contextlib.py \
	warnings.py _py_warnings.py copyreg.py struct.py codeop.py __future__.py

$(STDLIB_SHIM)/.stamp: $(CPYTHON_LIB)/enum.py $(CPYTHON_LIB)/struct.py $(CPYTHON_LIB)/codeop.py \
                        $(CPYTHON_LIB)/__future__.py \
                        $(CPYTHON_LIB)/_py_warnings.py $(CPYTHON_LIB)/warnings.py \
                        $(CPYTHON_LIB)/json/__init__.py \
                        tools/stdlib_stubs/inspect.py \
                        tools/stdlib_stubs/pathlib.py \
                        tools/stdlib_stubs/functools.py \
                        tools/stdlib_stubs/dataclasses.py \
                        tools/stdlib_stubs/os.py \
                        tools/stdlib_stubs/ctypes/__init__.py \
                        tools/stdlib_stubs/random.py \
                        tools/stdlib_stubs/traceback.py \
                        tools/stdlib_stubs/linecache.py \
                        tools/stdlib_stubs/sdl2.py
	@mkdir -p $(STDLIB_SHIM)/re $(STDLIB_SHIM)/collections $(STDLIB_SHIM)/ctypes \
	          $(STDLIB_SHIM)/json
	@$(foreach f,$(STDLIB_REAL_FILES),cp $(CPYTHON_LIB)/$(f) $(STDLIB_SHIM)/$(f);)
	@cp $(CPYTHON_LIB)/re/__init__.py    $(STDLIB_SHIM)/re/
	@cp $(CPYTHON_LIB)/re/_compiler.py  $(STDLIB_SHIM)/re/
	@cp $(CPYTHON_LIB)/re/_parser.py    $(STDLIB_SHIM)/re/
	@cp $(CPYTHON_LIB)/re/_constants.py $(STDLIB_SHIM)/re/
	@cp $(CPYTHON_LIB)/re/_casefix.py   $(STDLIB_SHIM)/re/
	@cp $(CPYTHON_LIB)/collections/__init__.py $(STDLIB_SHIM)/collections/
	@cp $(CPYTHON_LIB)/_collections_abc.py     $(STDLIB_SHIM)/collections/abc.py
	@cp $(CPYTHON_LIB)/json/__init__.py $(STDLIB_SHIM)/json/
	@cp $(CPYTHON_LIB)/json/decoder.py  $(STDLIB_SHIM)/json/
	@cp $(CPYTHON_LIB)/json/encoder.py  $(STDLIB_SHIM)/json/
	@cp $(CPYTHON_LIB)/json/scanner.py  $(STDLIB_SHIM)/json/
	@cp tools/stdlib_stubs/inspect.py     $(STDLIB_SHIM)/inspect.py
	@cp tools/stdlib_stubs/pathlib.py     $(STDLIB_SHIM)/pathlib.py
	@cp tools/stdlib_stubs/functools.py   $(STDLIB_SHIM)/functools.py
	@cp tools/stdlib_stubs/dataclasses.py $(STDLIB_SHIM)/dataclasses.py
	@cp tools/stdlib_stubs/os.py          $(STDLIB_SHIM)/os.py
	@cp tools/stdlib_stubs/ctypes/__init__.py $(STDLIB_SHIM)/ctypes/__init__.py
	@cp tools/stdlib_stubs/random.py      $(STDLIB_SHIM)/random.py
	@cp tools/stdlib_stubs/traceback.py   $(STDLIB_SHIM)/traceback.py
	@cp tools/stdlib_stubs/linecache.py   $(STDLIB_SHIM)/linecache.py
	@cp tools/stdlib_stubs/sdl2.py        $(STDLIB_SHIM)/sdl2.py
	@touch $@

$(BUILD)/frozen_kernel.c: $(KERNEL_DEPS) $(STDLIB_SHIM)/.stamp
	@mkdir -p $(BUILD)
	python3 tools/freeze_kernel.py kernel asyncio $(ENCODINGS_SRC) \
	    $(STDLIB_SHIM) examples apps $@

_freeze: $(BUILD)/frozen_kernel.c

$(BUILD)/frozen_kernel.o: $(BUILD)/frozen_kernel.c
	@mkdir -p $(BUILD)
	$(CC) $(KERN_CFLAGS) $(DEPFLAGS) -c $< -o $@

$(KERNEL_ELF): $(BOOT_OBJS) $(HAL_OBJS) $(LIBC_OBJS) $(LINENOISE_OBJS) \
               $(BUILD)/frozen_kernel.o $(PYTHON_LIB)
	@mkdir -p $(BUILD)
	$(LD) $(LDFLAGS) -o $@ $^ $(LIBGCC)

_iso: $(KERNEL_ELF) $(GRUB_CFG)
	@mkdir -p $(ISO_DIR)/boot/grub $(dir $(ISO_OUT))
	cp $(GRUB_CFG) $(ISO_DIR)/boot/grub/grub.cfg
	cp $(KERNEL_ELF) $(ISO_DIR)/boot/pythonos.elf
	grub-mkrescue -o $(ISO_OUT) $(ISO_DIR)

# ── arm64 build support ───────────────────────────────────────────────────────

ARM64_SMP_CPUS ?= 2
QEMU_ARM64_FLAGS := -machine virt $(QEMU_ARM64_CPU) $(QEMU_ARM64_ACCEL) -m 2G -smp $(ARM64_SMP_CPUS) \
                    -no-reboot -no-shutdown \
                    -nographic -serial mon:stdio \
                    -netdev user,id=net1,hostfwd=tcp::$(ARM64_REPL_HOST_PORT)-:5000,hostfwd=tcp::$(ARM64_FILE_HOST_PORT)-:7000 -device virtio-net-device,netdev=net1

# arm64 GUI mode: ramfb provides a flat XRGB8888 framebuffer that QEMU
# samples and renders through the host SDL surface. The guest configures
# ramfb via fw_cfg at boot — see kernel/drivers/display/ramfb.py.
QEMU_ARM64_GUI_FLAGS := -machine virt $(QEMU_ARM64_CPU) $(QEMU_ARM64_ACCEL) -m 2G -smp $(ARM64_SMP_CPUS) \
                    -no-reboot -no-shutdown \
                    -display $(QEMU_DISPLAY) -device ramfb -serial stdio \
                    -device virtio-keyboard-device -device virtio-tablet-device \
                    -audiodev $(QEMU_AUDIODEV),id=a -device virtio-sound-device,audiodev=a \
                    -netdev user,id=net1,hostfwd=tcp::$(ARM64_REPL_HOST_PORT)-:5000,hostfwd=tcp::$(ARM64_FILE_HOST_PORT)-:7000 -device virtio-net-device,netdev=net1

# arm64 attaches the same persistent ext2 image as x86 — see DISK_IMG/ARM64_DISK
# at the top of the file.

arm64: $(ARM64_ELF)

# run-arm64: attach the disk image when present
run-arm64: $(ARM64_ELF) $(ARM64_DISK)
	qemu-system-aarch64 $(QEMU_ARM64_FLAGS) \
	    -drive if=none,file=$(ARM64_DISK),format=raw,id=hd0 \
	    -device virtio-blk-device,drive=hd0 \
	    -kernel $(ARM64_ELF)

# Legacy framebuffer mode for arm64: QEMU's own SDL window backed by
# `-device ramfb`. Pre-bridge path; reachable via `make run-fb`.
run-fb-arm64: $(ARM64_ELF) $(ARM64_DISK)
	qemu-system-aarch64 $(QEMU_ARM64_GUI_FLAGS) \
	    -drive if=none,file=$(ARM64_DISK),format=raw,id=hd0 \
	    -device virtio-blk-device,drive=hd0 \
	    -kernel $(ARM64_ELF)

# Bridge desktop mode for arm64: supervises pythonos_bridge as a sibling
# process. Default transport is native guest TCP; set
# PYTHONOS_BRIDGE_TRANSPORT=chardev to use the older virtconsole path.
run-gui-arm64: $(ARM64_ELF) $(ARM64_DISK) bridge
	PYTHONOS_GUI_ARCH=arm64 PYTHONOS_ARM64_DISK=$(ARM64_DISK) \
	    PYTHONOS_DISK=$(ARM64_DISK) \
	    QEMU_DISPLAY=$(QEMU_DISPLAY) QEMU_AUDIODEV=$(QEMU_AUDIODEV) \
	    python3 tools/run_gui.py $(ARM64_ELF)

stop-arm64:
	@pkill -f "[q]emu-system-aarch64.*$(ARM64_ELF)" || echo "No arm64 QEMU running."

test-arm64: test-chipset $(ARM64_ELF) $(ARM64_DISK)
	PYTHONOS_ARM64_HOST_PORT=$(ARM64_REPL_HOST_PORT) PYTHONOS_ARM64_FILE_PORT=$(ARM64_FILE_HOST_PORT) PYTHONOS_ARM64_DISK=$(ARM64_DISK) python3 tests/smoke_test_arm64.py $(ARM64_ELF)

# Boot the same kernel under TCG with -cpu cortex-a76 + gic-version=3
# so the GICv3 driver path (src/boot/gic_arm64.c) is exercised end-to-
# end. The default test-arm64 hits the GICv2 path via cortex-a57.
test-arm64-gicv3: $(ARM64_ELF) $(ARM64_DISK)
	PYTHONOS_ARM64_DISK=$(ARM64_DISK) python3 tests/smoke_test_arm64_gicv3.py $(ARM64_ELF)

# arm64 libpython defaults to GIL-enabled because Py_GIL_DISABLED hangs
# Py_InitializeFromConfig on aarch64-elf in our build environment (tracked
# by pythonos-hyg). Override with PYTHONOS_ARM64_FREE_THREADING=1 if you
# want to chase the upstream bug.
PYTHONOS_ARM64_FREE_THREADING ?= 0
$(LIBPYTHON_ARM64): tools/setup_cpython.sh .docker-image
	$(DOCKER_RUN) -e PYTHONOS_FREE_THREADING=$(PYTHONOS_ARM64_FREE_THREADING) $(DOCKER_IMG) \
	  ./tools/setup_cpython.sh --arch=arm64 --build

$(ARM64_ELF): $(LIBPYTHON_ARM64) \
              $(BOOT_SRC) $(HAL_SRC) $(LIBC_SRC) $(LINENOISE_SRC) $(KERNEL_DEPS) \
              src/linker_arm64.ld .docker-image
	$(DOCKER_RUN) $(DOCKER_IMG) \
	  make CPYTHON_LIB=deps-arm64/cpython-src/Lib \
	       ENCODINGS_SRC=deps-arm64/cpython-src/Lib/encodings \
	       _iso_arm64
	@echo "ARM64 ELF ready: $(ARM64_ELF)"

# Internal: build arm64 ELF (called from inside Docker)
TARGET_ARM64 := aarch64-elf
CC_ARM64     := $(TARGET_ARM64)-gcc
LD_ARM64     := $(TARGET_ARM64)-ld

CFLAGS_ARM64 := -std=c11 -O2 -ffreestanding -fno-stack-protector -fno-pie \
                -Wall -Wextra -DARCH_ARM64 -march=armv8-a \
                -I src/libc/include \
                -I src/linenoise \
                -I deps-arm64/cpython/Include -I deps-arm64/cpython

LIBGCC_ARM64 := $(shell $(CC_ARM64) -print-libgcc-file-name 2>/dev/null || echo "")

BOOT_ARM64_S  := src/boot/boot_arm64.S
BOOT_ARM64_C  := src/boot/main_arm64.c src/boot/fb.c src/boot/gic_arm64.c \
                 src/boot/smp_arm64.c
HAL_ARM64_C   := src/hal/hal.c
LIBC_ARM64_C  := $(LIBC_C)

BOOT_ARM64_OBJS := $(BUILD_ARM64)/boot/boot_arm64.S.o \
                   $(patsubst src/%.c,$(BUILD_ARM64)/%.c.o,$(BOOT_ARM64_C))
HAL_ARM64_OBJS  := $(BUILD_ARM64)/hal/hal.c.o
LIBC_ARM64_OBJS := $(patsubst src/%.c,$(BUILD_ARM64)/%.c.o,$(LIBC_ARM64_C))
LINENOISE_ARM64_OBJS := $(patsubst src/%.c,$(BUILD_ARM64)/%.c.o,$(LINENOISE_C))

$(BUILD_ARM64)/boot/boot_arm64.S.o: src/boot/boot_arm64.S
	@mkdir -p $(dir $@)
	$(CC_ARM64) $(CFLAGS_ARM64) -c $< -o $@

$(BUILD_ARM64)/boot/%.c.o: src/boot/%.c
	@mkdir -p $(dir $@)
	$(CC_ARM64) $(CFLAGS_ARM64) $(DEPFLAGS) -c $< -o $@

$(BUILD_ARM64)/hal/%.c.o: src/hal/%.c
	@mkdir -p $(dir $@)
	$(CC_ARM64) $(CFLAGS_ARM64) $(DEPFLAGS) -c $< -o $@

$(BUILD_ARM64)/libc/%.c.o: src/libc/%.c
	@mkdir -p $(dir $@)
	$(CC_ARM64) $(CFLAGS_ARM64) $(DEPFLAGS) -c $< -o $@

$(BUILD_ARM64)/linenoise/%.c.o: src/linenoise/%.c
	@mkdir -p $(dir $@)
	$(CC_ARM64) $(CFLAGS_ARM64) $(DEPFLAGS) -Wno-unused-result -Wno-unused-but-set-variable -c $< -o $@

$(BUILD_ARM64)/frozen_kernel.o: $(BUILD)/frozen_kernel.c
	@mkdir -p $(BUILD_ARM64)
	$(CC_ARM64) $(CFLAGS_ARM64) $(DEPFLAGS) -c $< -o $@

_iso_arm64: $(BOOT_ARM64_OBJS) $(HAL_ARM64_OBJS) $(LIBC_ARM64_OBJS) \
            $(LINENOISE_ARM64_OBJS) \
            $(BUILD_ARM64)/frozen_kernel.o $(LIBPYTHON_ARM64)
	@mkdir -p $(BUILD_ARM64)
	$(LD_ARM64) -T src/linker_arm64.ld -nostdlib -o $(ARM64_ELF) $^ $(LIBGCC_ARM64)
	@echo "ARM64 ELF: $(ARM64_ELF)"

# Compiler-generated header dependencies (missing files are ignored).
-include $(BOOT_OBJS:.o=.d) $(HAL_OBJS:.o=.d) $(LIBC_OBJS:.o=.d) \
         $(LINENOISE_OBJS:.o=.d) $(BUILD)/frozen_kernel.d \
         $(BOOT_ARM64_OBJS:.o=.d) $(HAL_ARM64_OBJS:.o=.d) \
         $(LIBC_ARM64_OBJS:.o=.d) $(LINENOISE_ARM64_OBJS:.o=.d) \
         $(BUILD_ARM64)/frozen_kernel.d
