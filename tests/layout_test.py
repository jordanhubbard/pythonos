#!/usr/bin/env python3
"""Host-side repository layout tests. No QEMU.

Generated images belong under build/ and build-arm64/. Linker scripts and
the GRUB menu live with the C sources in src/.

Run: python3 tests/layout_test.py
"""

from __future__ import annotations

import os
import re
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
    print("layout_test")

    check("src/linker.ld exists",
          os.path.isfile(os.path.join(ROOT, "src", "linker.ld")))
    check("src/linker_arm64.ld exists",
          os.path.isfile(os.path.join(ROOT, "src", "linker_arm64.ld")))
    check("src/boot/grub.cfg exists",
          os.path.isfile(os.path.join(ROOT, "src", "boot", "grub.cfg")))
    check("linker scripts are not at the repo root",
          not os.path.exists(os.path.join(ROOT, "linker.ld"))
          and not os.path.exists(os.path.join(ROOT, "linker_arm64.ld")))
    check("iso/ is not a source tree",
          not os.path.isdir(os.path.join(ROOT, "iso")))

    makefile = _read("GNUMakefile")
    iso_out = re.search(r"^ISO_OUT\s*:=\s*(\S+)", makefile, re.M)
    arm64_elf = re.search(r"^ARM64_ELF\s*:=\s*(\S+)", makefile, re.M)
    iso_dir = re.search(r"^ISO_DIR\s*:=\s*(\S+)", makefile, re.M)
    disk_img = re.search(r"^DISK_IMG\s*:=\s*(\S+)", makefile, re.M)

    check("ISO_OUT is under build/",
          iso_out is not None and iso_out.group(1).startswith("build/"),
          iso_out.group(1) if iso_out else "missing")
    check("ARM64_ELF is under build-arm64/",
          arm64_elf is not None and arm64_elf.group(1).startswith("build-arm64/"),
          arm64_elf.group(1) if arm64_elf else "missing")
    check("ISO_DIR is under build/",
          iso_dir is not None and iso_dir.group(1).startswith("build/"),
          iso_dir.group(1) if iso_dir else "missing")
    check("DISK_IMG is under build/",
          disk_img is not None and disk_img.group(1).startswith("build/"),
          disk_img.group(1) if disk_img else "missing")
    check("x86 link uses src/linker.ld",
          "-T src/linker.ld" in makefile)
    check("arm64 link uses src/linker_arm64.ld",
          "-T src/linker_arm64.ld" in makefile)
    check("GRUB config is src/boot/grub.cfg",
          "GRUB_CFG := src/boot/grub.cfg" in makefile)

    gitignore = _read(".gitignore")
    for name in ("build/", "build-arm64/", "pythonos.iso",
                 "pythonos-arm64.elf", "disk-arm64.img", "iso/"):
        check(f".gitignore lists {name}", name in gitignore.splitlines()
              or name in gitignore)

    if _failed:
        print(f"\n{_failed} failed, {_passed} passed")
        return 1
    print(f"\n{_passed} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
