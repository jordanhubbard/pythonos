"""
kernel — PythonOS kernel package.

Entry point: kernel.boot(mmap, fb_info) is called by the C HAL after
early hardware init. From here, Python owns the machine.
"""


import asyncio

import _hal
import kernel.log as log
from kernel.hal.io import set_interrupt_router
from kernel.interrupts.router import router
from kernel.interrupts import handlers  # noqa: F401 — registers default handlers
from kernel.memory.pmm import PhysicalMemoryManager
from kernel.memory.vmm import VirtualMemoryManager
from kernel.bus.pci import bus as pci_bus, PCIClass
from kernel.scheduler import scheduler
from kernel.fs.vfs import vfs
from kernel.fs.tmpfs import TmpFS

_ARCH = getattr(_hal, 'ARCH', 'x86_64')


def _fwcfg_text(name: str) -> str:
    """Read a small text value passed with QEMU ``-fw_cfg name=...``."""
    try:
        from kernel.drivers.display import fwcfg
        if fwcfg.signature() != b"QEMU":
            return ""
        data = fwcfg.read_file(name)
    except Exception:
        return ""
    if not data:
        return ""
    return data.decode("utf-8", errors="replace").strip("\x00 \t\r\n")


async def _auto_start_bridge_desktop(app_name: str | None = None) -> None:
    await asyncio.sleep(0.1)
    try:
        from kernel.bridge import py_desktop
        desktop = py_desktop(app_name or None)
    except Exception as e:
        log.warn(f"kernel: bridge desktop auto-start failed ({e})")
        return
    if desktop is None:
        log.warn("kernel: bridge desktop auto-start requested but bridge is unavailable")
    else:
        log.info("kernel: bridge desktop auto-started")


def boot(mmap: list[tuple[int, int]],
         fb_info: dict | None = None) -> None:
    """
    Called once by the C bootstrap. Never returns.

    mmap:    list of (base_address, length) tuples for usable RAM.
    fb_info: dict with framebuffer parameters, or None.
    """
    log.info("kernel.boot: starting")

    # ── Interrupt routing ──────────────────────────────────────────────────
    set_interrupt_router(router._dispatch)
    log.info("kernel.boot: interrupt router connected")

    # ── Memory ────────────────────────────────────────────────────────────
    pmm = PhysicalMemoryManager(mmap)
    log.info(f"kernel.boot: PMM ready — {pmm.free_pages} pages free "
             f"({pmm.free_pages * 4096 // 1024 // 1024} MiB)")

    vmm = VirtualMemoryManager(pmm)
    # Make vmm accessible to page-fault handler
    import kernel.memory.vmm as _vmm_mod
    _vmm_mod.vmm = vmm
    log.info("kernel.boot: VMM ready")

    # ── PCI enumeration ────────────────────────────────────────────────────
    # On arm64 QEMU virt we use VirtIO-MMIO for all devices; skip PCI scan.
    if _ARCH == 'x86_64':
        log.info("kernel.boot: enumerating PCI bus...")
        pci_bus.enumerate()
        log.info(f"kernel.boot: {len(pci_bus)} PCI devices found")
        for dev in pci_bus:
            log.info(f"  {dev}")
    else:
        log.info("kernel.boot: skipping PCI enumeration (arm64 VirtIO-MMIO)")

    # ── Filesystem ─────────────────────────────────────────────────────────
    root_fs = TmpFS()
    import kernel.commands as commands
    try:
        from kernel.frozen_sources import SOURCES as _seed_sources
    except Exception:
        _seed_sources = {}
    _examples = {}
    for _path, _source in _seed_sources.items():
        if not _path.startswith("/examples/"):
            continue
        _rel = _path[len("/examples/"):]
        if "/" not in _rel:
            _examples[_rel] = _source

    root_fs.seed({
        "dev": {},
        "tmp": {},
        "proc": {},
        "sys": {},
        "bin": commands.SCRIPTS,
        "examples": _examples,
    })
    vfs.mount("/", root_fs)
    log.info("kernel.boot: tmpfs mounted at /")

    # VFS-backed import path: lets the REPL `import` files written to
    # /examples or /home (anything on a tmpfs mount). Disk-backed mounts
    # need a future async-import bridge; for now the legacy
    # `run('/path/to/file.py')` flow handles those.
    try:
        import kernel.vfs_import as _vfs_import
        _vfs_import.install()
    except Exception as e:
        log.info(f"kernel.boot: vfs_import install failed: {e}")

    # ── Event loop ─────────────────────────────────────────────────────────
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    router.set_event_loop(loop)
    scheduler.attach_loop(loop)   # also registers loop with _hal for threadsafe dispatch
    log.info("kernel.boot: event loop ready")

    log.info("kernel.boot: entering main loop")
    loop.run_until_complete(_kernel_main(pmm, vmm, fb_info))


async def _kernel_main(
    pmm: PhysicalMemoryManager,
    vmm: VirtualMemoryManager,
    fb_info: dict | None,
) -> None:
    log.info("kernel: async main started")

    # ── Framebuffer + console ──────────────────────────────────────────────
    import sys as _sys
    import kernel.display as display
    import kernel.display.framebuffer
    import kernel.display.console
    _fb_mod      = _sys.modules['kernel.display.framebuffer']
    _console_mod = _sys.modules['kernel.display.console']

    # On arm64 the firmware doesn't negotiate a framebuffer; if QEMU was
    # started with `-device ramfb` we set one up ourselves via fw_cfg.
    if fb_info is None and _ARCH == 'arm64':
        try:
            from kernel.drivers.display import ramfb
            fb_info = ramfb.setup()
        except Exception as e:
            log.info(f"kernel: ramfb setup failed: {e}")
            fb_info = None

    if fb_info:
        fb = display.Framebuffer(fb_info)
        _fb_mod.fb = fb
        console = display.Console(fb)
        _console_mod.console = console
        console.writeln("PythonOS")
        console.writeln(f"RAM: {pmm.free_pages * 4096 // 1024 // 1024} MiB free")
        log.info("kernel: framebuffer console ready")
        # Bring the GUI input substrate up alongside the framebuffer.
        # x86: PS/2 keyboard (IRQ1) + PS/2 mouse (IRQ12).
        # arm64: virtio-input keyboard via virtio-mmio (mouse follow-up).
        try:
            from kernel.gui import input as _gui_input
            _gui_input.init()
            if _ARCH == 'x86_64':
                _gui_input.install_ps2_bridge()
                _gui_input.install_ps2_mouse_bridge(fb.width, fb.height)
                log.info("kernel: GUI input ready (PS/2 kbd+mouse)")
            else:
                from kernel.drivers.input import virtio_input
                n = virtio_input.install_virtio_input_bridge(
                    scheduler, screen_w=fb.width, screen_h=fb.height)
                if n:
                    log.info(f"kernel: GUI input ready (virtio-input x{n})")
                else:
                    log.info("kernel: no virtio-input device found")
        except Exception as e:
            log.info(f"kernel: GUI input setup failed: {e}")
    else:
        console = None
        log.info("kernel: no framebuffer — serial only")

    def _write(text: str) -> None:
        if console:
            console.write(text)
        else:
            log._serial_raw(text)

    def _write_raw(buf) -> None:
        # Raw byte/string sink for linenoise — bypasses any \n→\r\n
        # translation; on serial we go straight to the same path as
        # _write but without the console double-write.
        if isinstance(buf, (bytes, bytearray)):
            text = buf.decode('utf-8', errors='replace')
        else:
            text = buf
        log._serial_raw(text)

    # ── Keyboard / serial input ────────────────────────────────────────────
    if _ARCH == 'x86_64':
        from kernel.drivers.input import com1
        keyboard = com1
        log.info("kernel: COM1 serial input ready")
    else:
        from kernel.drivers.input import pl011
        keyboard = pl011
        log.info("kernel: PL011 serial input ready")

    # ── Register PCI drivers before binding ───────────────────────────────
    from kernel.drivers.net.virtio_net import VirtIONetDriver, VIRTIO_VENDOR, VIRTIO_NET_DEV
    pci_bus.register_driver(VirtIONetDriver, vendor=VIRTIO_VENDOR, device=VIRTIO_NET_DEV)
    if _ARCH == 'x86_64':
        from kernel.sound.hda import HDADriver, HDA_VENDOR_INTEL, HDA_DEVICE_ICH6
        pci_bus.register_driver(HDADriver, vendor=HDA_VENDOR_INTEL, device=HDA_DEVICE_ICH6)

    # ── PCI driver binding ─────────────────────────────────────────────────
    pci_bus.bind_drivers()

    # ── Network ────────────────────────────────────────────────────────────
    nic = next((dev.driver for dev in pci_bus
                if isinstance(dev.driver, VirtIONetDriver)), None)
    if nic:
        from kernel.net.stack import net_init
        scheduler.spawn(net_init(nic, "10.0.2.15", "10.0.2.2"), name="net-init")
        log.info("kernel: network stack starting")
        from kernel.net import repl_server
        scheduler.spawn(repl_server.start(), name="repl-server")
        log.info("kernel: TCP REPL server starting (nc localhost 5555)")
        _write("TCP REPL ready — connect: nc localhost 5555\n")

    # ── Sound ─────────────────────────────────────────────────────────────
    # x86: Intel HDA via PCI (requires PCI scan to have run already).
    # arm64: virtio-snd via virtio-mmio.
    if _ARCH == 'x86_64':
        import kernel.sound.hda
        _hda_mod = _sys.modules['kernel.sound.hda']
        hda_dev = next((dev.driver for dev in pci_bus
                        if isinstance(getattr(dev, 'driver', None), _hda_mod.HDADriver)), None)
        if hda_dev:
            _hda_mod.hda = hda_dev
            _sys.modules['kernel.sound'].hda = hda_dev
            from kernel.sound.mixer import mixer
            mixer.attach(hda_dev)
            log.info("kernel: HDA sound ready")
    else:
        from kernel.drivers.sound.virtio_snd import find_virtio_snd
        snd = find_virtio_snd()
        if snd:
            from kernel.sound.mixer import mixer
            mixer.attach(snd)
            log.info("kernel: virtio-snd ready")
        else:
            log.info("kernel: no audio device found (arm64)")

    # ── VirtIO block device (arm64 → MMIO, x86 → PCI; transport dispatch
    # lives in kernel.drivers.block.virtio_blk.find_virtio_blk).
    from kernel.drivers.block import virtio_blk
    _blk = virtio_blk.find_virtio_blk()
    if _blk:
        virtio_blk.blk = _blk
        log.info(f"kernel: virtio-blk ready, {_blk.num_sectors} sectors")
    else:
        log.info("kernel: no virtio-blk device found")

    # ── /home and /apps: persistent ext2 mounts (fall back to tmpfs) ──────
    # Disk layout: a single ext2 FS with /home and /apps as top-level dirs.
    # We pre-resolve each subdirectory and wrap it in NodeFS so the VFS sees
    # them as separate mount points. The whole block is wrapped in a single
    # try/except — a failure here must NEVER prevent kshell from spawning.
    try:
        from kernel.fs.vfs import NodeFS
        _ext2 = None
        # x86 PCI virtio-blk read_sector is currently broken (descriptor
        # ring / poll loop hang — tracked separately). Until that's fixed
        # we only attempt the ext2 mount on arm64 (MMIO transport, known
        # working). x86 takes the tmpfs fallback below — no persistence,
        # but boot completes cleanly.
        if virtio_blk.blk is not None and _ARCH == 'arm64':
            try:
                from kernel.fs.ext2 import Ext2FS
                log.info("kernel: mounting ext2 from virtio-blk...")
                _ext2 = await Ext2FS.mount(virtio_blk.blk)
                _root = _ext2.root()
                _home = await _root.lookup('home')
                _apps = await _root.lookup('apps')
                vfs.mount('/home', NodeFS(_home))
                vfs.mount('/apps', NodeFS(_apps))
                log.info("kernel: /home and /apps mounted from virtio-blk (ext2)")
            except Exception as _e:
                log.info(f"kernel: ext2 mount failed ({_e!r}) — falling back to tmpfs")
                _ext2 = None
        if _ext2 is None:
            # Tmpfs fallback: ensure /home and /apps exist as ordinary dirs
            # in the root tmpfs so consumers can write to them (non-durably).
            for _path in ('/home', '/apps'):
                try:
                    await vfs.stat(_path)
                except FileNotFoundError:
                    await vfs.mkdir(_path)
            log.info("kernel: /home and /apps available as tmpfs (non-persistent)")
    except Exception as _e:
        log.info(f"kernel: /home /apps wiring crashed: {_e!r} — continuing without")

    # ── Shell ──────────────────────────────────────────────────────────────
    from kernel.shell import Shell

    shell = Shell(read_char=keyboard.read_char, write=_write,
                  read_byte=getattr(keyboard, 'read_byte', None),
                  write_raw=_write_raw)
    scheduler.spawn(shell.run(), name="kshell")
    log.info("kernel: shell spawned — system ready")

    gui_mode = _fwcfg_text("opt/pythonos/gui")
    if gui_mode == "bridge":
        app_name = _fwcfg_text("opt/pythonos/gui-app")
        scheduler.spawn(_auto_start_bridge_desktop(app_name or None),
                        name="bridge-desktop")
        log.info("kernel: bridge desktop auto-start requested")
    elif gui_mode:
        log.warn(f"kernel: unknown GUI boot mode {gui_mode!r}")

    # Main loop: keep the event loop alive; subsystems run as tasks
    while True:
        await asyncio.sleep(0.1)
