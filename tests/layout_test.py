#!/usr/bin/env python3
"""Host-side repository layout and Makefile convention tests. No QEMU.

Generated images belong under build/ and build-arm64/. Linker scripts and
the GRUB menu live with the C sources in src/. The GNU make file is
GNUmakefile.

Run: python3 tests/layout_test.py
"""

from __future__ import annotations

import os
import re
import subprocess
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


def _git_tracked(rel: str) -> bool:
    out = subprocess.check_output(
        ["git", "-C", ROOT, "ls-files", "--", rel],
        text=True,
    )
    return rel in out.splitlines()


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
    check("GNU make file is tracked as GNUmakefile",
          os.path.isfile(os.path.join(ROOT, "GNUmakefile")))
    check("no GNUMakefile sibling (case-sensitive check via git)",
          _git_tracked("GNUmakefile") and not _git_tracked("GNUMakefile"))

    makefile = _read("GNUmakefile")
    iso_out = re.search(r"^ISO_OUT\s*:=\s*(\S+)", makefile, re.M)
    arm64_elf = re.search(r"^ARM64_ELF\s*:=\s*(\S+)", makefile, re.M)
    iso_dir = re.search(r"^ISO_DIR\s*:=\s*(\S+)", makefile, re.M)
    disk_img = re.search(r"^DISK_IMG\s*:=\s*(\S+)", makefile, re.M)
    build_dir = re.search(r"^BUILD\s*:=\s*(\S+)", makefile, re.M)

    check("BUILD is build",
          build_dir is not None and build_dir.group(1) == "build",
          build_dir.group(1) if build_dir else "missing")
    check("ISO_OUT is under BUILD",
          iso_out is not None and "BUILD" in iso_out.group(1),
          iso_out.group(1) if iso_out else "missing")
    check("ARM64_ELF is under BUILD_ARM64",
          arm64_elf is not None and "BUILD_ARM64" in arm64_elf.group(1),
          arm64_elf.group(1) if arm64_elf else "missing")
    check("ISO_DIR is under BUILD",
          iso_dir is not None and "BUILD" in iso_dir.group(1),
          iso_dir.group(1) if iso_dir else "missing")
    check("DISK_IMG is under BUILD",
          disk_img is not None and "BUILD" in disk_img.group(1),
          disk_img.group(1) if disk_img else "missing")
    check("x86 link uses src/linker.ld",
          "-T src/linker.ld" in makefile)
    check("arm64 link uses src/linker_arm64.ld",
          "-T src/linker_arm64.ld" in makefile)
    check("GRUB config is src/boot/grub.cfg",
          "GRUB_CFG     := src/boot/grub.cfg" in makefile
          or "GRUB_CFG := src/boot/grub.cfg" in makefile)

    check("enables .DELETE_ON_ERROR",
          re.search(r"^\.DELETE_ON_ERROR:", makefile, re.M) is not None)
    check("clears built-in suffix rules",
          re.search(r"^\.SUFFIXES:\s*$", makefile, re.M) is not None)
    check("docker volume uses CURDIR not PWD",
          "-v $(CURDIR):/work" in makefile and "-v $(PWD):/work" not in makefile)
    check(".docker-image depends on tools/Dockerfile",
          re.search(r"^\.docker-image:\s+tools/Dockerfile\s*$", makefile, re.M)
          is not None)
    check(".docker-image does not depend on FORCE",
          re.search(r"^\.docker-image:.*FORCE", makefile, re.M) is None)
    check("frozen_kernel.c is a real target",
          "$(BUILD)/frozen_kernel.c:" in makefile)
    freezer = _read("tools/freeze_kernel.py")
    check("freezer fails instead of silently omitting invalid modules",
          "ERROR: cannot freeze" in freezer
          and "raise SystemExit(1) from e" in freezer
          and "WARNING: skipping" not in freezer)
    check("freezer embeds application source for live teaching panes",
          'src_dir.name not in ("examples", "apps")' in freezer
          and '"/src/apps/"' in freezer)
    compositor = _read("kernel/gui/compositor.py")
    desktop = _read("kernel/gui/desktop.py")
    editor = _read("apps/editor/edwin.py")
    check("focused app source has shortcut and menubar entry",
          "open_focused_source" in compositor
          and "KEY_F2" in compositor
          and "View Source (F2)" in desktop)
    check("source pane supports save cancel and runtime reload",
          'MenuItem("Save (Ctrl-S)"' in editor
          and 'MenuItem("Cancel Changes"' in editor
          and 'MenuItem("Reload Running App"' in editor
          and 'compile(runtime_text, overlay, "exec"' in editor)
    ui = _read("kernel/gui/ui.py")
    check("desktop exposes a Python view hierarchy over SDL surfaces",
          "class UIElement" in ui and "class View(UIElement)" in ui
          and "class Container(View)" in ui and "class Panel(Container)" in ui
          and "class Button(Label)" in ui and "class TextView(View)" in ui
          and "class ListView(TextView)" in ui)
    check("editor terminal and files share high-level view classes",
          "class EditorView(TextView)" in editor
          and "class TextWin(TextView)" in _read("apps/_textwin.py")
          and "class _Browser(ListView)" in _read("apps/files/browser.py"))
    run_gui = _read("tools/run_gui.py")
    check("interactive x86 GUI binds HDA output to the host audiodev",
          '"-audiodev", f"{audiodev},id=a"' in run_gui
          and '"hda-output,audiodev=a"' in run_gui)
    check("C compiles emit -MMD dependencies",
          "DEPFLAGS = -MMD" in makefile)
    for driver_path in ("kernel/drivers/net/virtio_net.py",
                        "kernel/drivers/net/virtio_net_mmio.py"):
        driver = _read(driver_path)
        send_body = driver.split("def send_nowait", 1)[1].split("async def send", 1)[0]
        check(f"{driver_path} reuses bounded TX DMA buffers",
              "_tx_free" in driver and "_reclaim_tx" in send_body
              and "dma_alloc" not in send_body)
    pci_net = _read("kernel/drivers/net/virtio_net.py")
    avail_body = pci_net.split("def avail_push", 1)[1].split(
        "def used_has_entries", 1)[0]
    check("PCI virtqueue writes packed avail fields without neighbor corruption",
          "mmio_write8" in avail_body and "mmio_write32" not in avail_body)

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
