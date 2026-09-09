#!/usr/bin/env python3
"""Host-side CI/release-gate tests. No QEMU, no Docker.

The x86_64 ISO must be built, smoked, uploaded, and attached to GitHub
releases — not only the arm64 ELF.

Run: python3 tests/ci_gate_test.py
"""

from __future__ import annotations

import os
import re
import stat
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_failed = 0
_passed = 0


def check(name: str, cond, detail: str = "") -> None:
    global _failed, _passed
    ok = bool(cond)
    if ok:
        _passed += 1
        print(f"  PASS  {name}" + (f" ({detail})" if detail else ""))
    else:
        _failed += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def _read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def main() -> int:
    print("ci_gate_test")

    ci = _read(".github/workflows/ci.yml")
    validate = _read("scripts/validate-release.sh")
    release = _read("scripts/release.sh")
    makefile = _read("GNUmakefile")

    check("CI runs on ubuntu-24.04 (x86_64)",
          "runner: ubuntu-24.04" in ci
          and "ubuntu-24.04-arm" in ci)
    check("CI matrix includes x86_64 and arm64 jobs",
          re.search(r"name:\s*x86_64", ci) is not None
          and re.search(r"name:\s*arm64", ci) is not None)
    check("CI uploads pythonos-x86_64 artifact",
          "artifact: pythonos-x86_64" in ci
          and "artifact_path: build/pythonos.iso" in ci)
    check("CI uploads pythonos-arm64 artifact",
          "artifact: pythonos-arm64" in ci
          and "artifact_path: build-arm64/pythonos-arm64.elf" in ci)
    check("CI fails if the image is missing",
          "if-no-files-found: error" in ci)
    check("CI has an all-arches aggregator",
          "needs: validate" in ci and "all-arches" in ci)
    check("CI runs validate-release.sh",
          "./scripts/validate-release.sh" in ci)
    check("CI installs qemu-system-x86",
          "qemu_pkg: qemu-system-x86" in ci)

    check("validate-release.sh is executable",
          os.stat(os.path.join(ROOT, "scripts", "validate-release.sh")).st_mode
          & stat.S_IXUSR)
    check("validate-release.sh smokes x86_64",
          "make test-x86_64" in validate
          and "make test-gui-x86_64" in validate)
    check("validate-release.sh still smokes arm64",
          "make test-arm64" in validate
          and "make test-gui-arm64" in validate)
    check("validate-release.sh dispatches on host arch",
          "PYTHONOS_VALIDATE_ARCH" in validate
          and 'ARCH="${PYTHONOS_VALIDATE_ARCH:-$(host_arch)}"' in validate)
    check("validate-release.sh runs the CI-gate tests first",
          "python3 tests/ci_gate_test.py" in validate)

    check("release.sh downloads pythonos-x86_64 from CI",
          "pythonos-x86_64" in release
          and "gh run download" in release)
    check("release.sh waits for the CI workflow",
          "--workflow CI" in release)
    check("release.sh downloads pythonos-arm64 from CI",
          "--name pythonos-arm64" in release)
    check("release.sh requires the x86 ISO",
          "did not upload pythonos.iso" in release)
    check("release.sh requires the arm64 ELF",
          "did not upload pythonos-arm64.elf" in release)
    check("release.sh attaches both images",
          '"$RELEASE_ISO#pythonos.iso"' in release
          and '"$RELEASE_ELF#pythonos-arm64.elf"' in release)

    check("test-x86_64 waits for the disk image",
          "test-x86_64: test-chipset $(ISO_OUT) $(DISK_IMG)" in makefile)
    check("makefile KVM requires /dev/kvm is writable",
          "test -r /dev/kvm -a -w /dev/kvm" in makefile)
    smoke = _read("tests/smoke_test.py")
    check("serial smoke skips KVM without write access",
          'os.access("/dev/kvm"' in smoke)
    check("CI forces TCG so KVM permission cannot abort QEMU",
          "PYTHONOS_QEMU_ACCEL: tcg" in ci)

    if _failed:
        print(f"\n{_failed} failed, {_passed} passed")
        return 1
    print(f"\n{_passed} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
