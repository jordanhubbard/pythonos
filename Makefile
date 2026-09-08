# BSD make stub — delegates everything to GNU make.
#
# GNU make auto-discovers GNUmakefile ahead of Makefile, so this file is
# only read by BSD make (and any make that does not understand GNUmakefile).
# Prefer Homebrew gmake on macOS; fall back to the platform `make`.
GMAKE ?= $(if $(shell command -v gmake 2>/dev/null),gmake,make)

# Targets the user is expected to invoke. Each is forwarded as
# `gmake <target>` so `make TARGET_ARCH=arm64 build` still works.
TOP_GOALS := all build build-gui run run-gui run-fb start stop restart \
             test test-gui test-chipset clean cleanall \
             docker-build help disk-image \
             bridge bridge-clean test-bridge \
             release release-major release-minor release-patch validate-release \
             x86_64 run-x86_64 stop-x86_64 test-x86_64 run-gui-x86_64 \
                 test-gui-x86_64 run-fb-x86_64 \
             arm64 run-arm64 stop-arm64 test-arm64 test-arm64-gicv3 \
                 run-gui-arm64 test-gui-arm64 run-fb-arm64

.PHONY: $(TOP_GOALS)
$(TOP_GOALS):
	@$(GMAKE) $@

# Catch-all for anything not enumerated above (e.g. internal `_iso`).
.DEFAULT:
	@$(GMAKE) $@
