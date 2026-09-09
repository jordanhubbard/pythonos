#!/usr/bin/env bash
# Release validation gate for PythonOS.
#
# Builds and smokes the architecture of *this* machine (or PYTHONOS_VALIDATE_ARCH).
# GitHub Actions runs the script once per arch (ubuntu-24.04 and ubuntu-24.04-arm)
# so both images are tested before `make release` publishes them.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

info() {
    printf '[validate] %s\n' "$*"
}

run() {
    info "$*"
    "$@"
}

host_arch() {
    local u
    u="$(uname -m)"
    case "$u" in
        arm64|aarch64) printf 'arm64\n' ;;
        *)             printf 'x86_64\n' ;;
    esac
}

ARCH="${PYTHONOS_VALIDATE_ARCH:-$(host_arch)}"
case "$ARCH" in
    arm64|x86_64) ;;
    *)
        printf '[validate] ERROR: PYTHONOS_VALIDATE_ARCH must be arm64 or x86_64 (got %s)\n' "$ARCH" >&2
        exit 1
        ;;
esac

info "validating $ARCH"

info "checking Python syntax"
run python3 -m py_compile \
    tools/run_gui.py \
    tests/smoke_test.py \
    tests/smoke_test_arm64.py \
    tests/gui_smoke_test.py \
    tests/gui_smoke_test_arm64.py \
    tests/ci_gate_test.py \
    kernel/gui/compositor.py \
    apps/_textwin.py \
    kernel/linenoise.py

info "checking CI/release gate"
run python3 tests/ci_gate_test.py

info "checking whitespace"
run git diff --check

info "building GUI artifacts for $ARCH"
run make TARGET_ARCH="$ARCH" build-gui

info "running bridge smoke"
run make test-bridge

if [ "$ARCH" = arm64 ]; then
    info "running arm64 serial smoke"
    run make test-arm64
    info "running arm64 GUI smoke"
    run make test-gui-arm64
else
    info "running x86_64 serial smoke"
    run make test-x86_64
    info "running x86_64 GUI smoke"
    run make test-gui-x86_64
fi

info "release validation passed ($ARCH)"
