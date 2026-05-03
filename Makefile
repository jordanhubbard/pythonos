# BSD make stub — delegates everything to GNU make.
#
# This file only matters when `make` reads it instead of GNUMakefile.
# GNU make's auto-discovery prefers `GNUmakefile` (lowercase 'm');
# we keep `GNUMakefile` (capital M) for legacy reasons, so this stub
# explicitly forwards every common top-level goal to it.
#
# Prefer gmake (Homebrew GNU make) if present; fall back to plain make
# (which on macOS is GNU make 3.81 — old but adequate).
GMAKE ?= $(if $(shell which gmake 2>/dev/null),gmake,make)

# Targets that the user is expected to invoke directly. Every one is
# forwarded as `gmake -f GNUMakefile <target>` so options like
# `make TARGET_ARCH=arm64 build` work as expected.
TOP_GOALS := all build build-gui run run-gui run-fb start stop restart \
             test test-gui clean cleanall \
             docker-build help \
             bridge bridge-clean test-bridge \
             x86_64 run-x86_64 stop-x86_64 test-x86_64 run-gui-x86_64 \
                 test-gui-x86_64 run-desktop-x86_64 \
             arm64 run-arm64 stop-arm64 test-arm64 run-gui-arm64 \
                 test-gui-arm64 run-desktop-arm64 \
             run-gui run-desktop

.PHONY: $(TOP_GOALS)
$(TOP_GOALS):
	@$(GMAKE) -f GNUMakefile $@

# Catch-all for anything not enumerated above (e.g. internal `_freeze`).
.DEFAULT:
	@$(GMAKE) -f GNUMakefile $@
