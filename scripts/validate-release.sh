#!/usr/bin/env bash
# Release validation gate for PythonOS.
#
# This is intentionally the same shape as the nanolang release gate: one
# script owns the local source/build/test checks, and release automation calls
# this script before it waits for CI, tags, and publishes.

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

info "checking Python syntax"
run python3 -m py_compile \
    tools/run_gui.py \
    tests/smoke_test_arm64.py \
    tests/gui_smoke_test_arm64.py \
    kernel/gui/compositor.py \
    apps/_textwin.py \
    kernel/linenoise.py

info "checking whitespace"
run git diff --check

info "building GUI artifacts"
run make build-gui

info "running bridge smoke"
run make test-bridge

info "running arm64 serial smoke"
run make test-arm64

info "running arm64 GUI smoke"
run make test-gui-arm64

info "release validation passed"
