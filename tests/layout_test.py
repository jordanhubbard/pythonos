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
    check("applications are source-first and compiled by the VFS importer",
          'if src_dir.name != "apps"' in freezer
          and 'add_search_dir("/lib")' in _read("kernel/vfs_import.py")
          and 'relative = fullname.replace(".", "/")'
              in _read("kernel/vfs_import.py")
          and '"lib": {"apps": _lib_apps}' in _read("kernel/__init__.py"))
    compositor = _read("kernel/gui/compositor.py")
    desktop = _read("kernel/gui/desktop.py")
    editor = _read("apps/editor/edwin.py")
    check("focused app source has shortcut and menubar entry",
          "open_focused_source" in compositor
          and "KEY_F2" in _read("kernel/gui/keybindings.py")
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
    filechooser = _read("kernel/gui/filechooser.py")
    check("editor terminal and files share high-level view classes",
          "class EditorView(TextView)" in editor
          and "class TextWin(TextView)" in _read("apps/_textwin.py")
          and "class FileChooserView(ListView)" in filechooser
          and "choose_file" in _read("apps/files/browser.py"))
    check("editor open and save-as use the shared graphical chooser",
          'choose_file(title="Open File"' in editor
          and 'choose_file(title="Save File"' in editor)
    check("image viewer uses the shared graphical chooser",
          'choose_file(title="Open Image"'
              in _read("apps/image_viewer/viewer.py")
          and "await _image.load_vfs(path)" in _read("apps/image_viewer/viewer.py"))
    check("shared editor implements basic Emacs travel keys",
          "def _word_forward" in editor
          and "def _sentence_backward" in editor
          and "def _paragraph_forward" in editor
          and "MOD_ALT | _gui_input.MOD_META" in editor
          and 'letter == "v"' in editor
          and 'letter == "l"' in editor
          and 'ev.text == "<"' in editor
          and 'ev.text == ">"' in editor)
    check("file chooser supports mouse selection and double-click activation",
          "MOUSE_DOWN" in filechooser and "_DOUBLE_CLICK_SECONDS" in filechooser
          and "activate_selected" in filechooser)
    check("frozen game and demo sources are browsable under examples",
          'f"/examples/{category}/{source_file.name}"' in freezer
          and '_node.setdefault(_part, {})' in _read("kernel/__init__.py"))
    check("examples form documented teaching tracks with recursive rebuilds",
          "start_here/" in _read("examples/README.txt")
          and "internals/" in _read("examples/README.txt")
          and "$(call rwildcard,examples/,*.py)" in makefile
          and not os.path.exists(os.path.join(ROOT, "examples", "primes.py")))
    check("image viewer opens a seeded binary teaching gallery",
          'path="/examples/images/"' in _read("apps/image_viewer/viewer.py")
          and "extensions=(" in _read("apps/image_viewer/viewer.py")
          and '".png"' in freezer
          and "SDL_Surface.from_image_bytes(encoded)" in _read("kernel/gui/image/__init__.py")
          and "green-tree-python.png" in _read("examples/images/README.txt"))
    bridge_input = _read("kernel/bridge/input.py")
    check("bridge keyboard events preserve modifiers for Emacs bindings",
          "_translate_modifiers" in bridge_input
          and 'ev.get("mod", 0)' in bridge_input)
    check("remote display has explicit kernel-server and display-client targets",
          "run-display-server:" in makefile
          and "connect-display:" in makefile
          and "PYTHONOS_DISPLAY_SERVER" in makefile)
    check("desktop file drops use bounded tokenized bridge transfers",
          "HOST_FILE_DROP" in _read("kernel/gui/input.py")
          and 'bridge.call("host.file.read"' in _read("kernel/gui/filetransfer.py")
          and 'bridge.call("host.export.chunk"' in _read("kernel/gui/filetransfer.py")
          and "FILE_CHUNK_MAX" in _read("tools/pythonos_bridge/main.c"))
    about = _read("apps/about/about.py")
    check("About derives Python version from version_info",
          "sys.version_info" in about and "sys.version.split()" not in about)
    clock = _read("apps/clock/clock.py")
    check("Clock exposes a discoverable validated session-time control",
          'MenuItem("Set Time… (S)"' in clock
          and "timekeeper.set_hms" in clock
          and os.path.isfile(os.path.join(ROOT, "kernel", "timekeeper.py")))
    check("desktop shortcuts use a configurable common registry",
          "action_for(ev)" in _read("kernel/gui/compositor.py")
          and "set_binding" in _read("apps/keybindings/keybindings.py")
          and "Esc always exits full-screen" in _read("apps/keybindings/keybindings.py"))
    check("Top shows tasks and bounded bridge performance samples",
          'name="top"' in _read("apps/sysmon/sysmon.py")
          and "performance_snapshot" in _read("apps/sysmon/sysmon.py")
          and "sample_number % 4" in _read("apps/sysmon/sysmon.py"))
    check("chipset has an ordered one-feature-at-a-time curriculum",
          os.path.isfile(os.path.join(ROOT, "examples", "graphics", "chipset",
                                      "07_display_window.py"))
          and "01_playfield.py" in _read("examples/graphics/chipset/README.txt")
          and "06_paula.py" in _read("examples/graphics/chipset/README.txt"))
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
