"""
kernel.vfs_import — meta-path importer that loads Python source from VFS.

CPython's import system is synchronous, but the VFS protocol is async
(every backend implements ``async def read``). For tmpfs this is just
ceremony — the data lives in in-memory dicts — so the tmpfs backend
exposes :meth:`read_sync` directly. This module wires that into a
``sys.meta_path`` finder so users can write a Python file to
``/home/user/foo.py`` (or any other tmpfs path) and then ``import foo``
from the kernel REPL.

Disk-backed filesystems (ext2 over virtio-blk) don't expose a
synchronous read path — files there must be loaded via the legacy
``run('/path/to/script.py')`` flow.

The importer is intentionally written without ``importlib.abc`` /
``importlib.machinery`` because the kernel's frozen Python doesn't
include them; we use the legacy ``find_module`` / ``load_module``
protocol that CPython still supports for ``sys.meta_path`` entries.
Top-level modules only in v0 — package nesting (``import a.b``) is a
follow-up that needs a per-package finder + path attribute.
"""

import sys
import types

from kernel.fs.vfs import vfs
import kernel.log as log


search_path: list[str] = []


# ── Loader ──────────────────────────────────────────────────────────────────

class _Spec:
    """Tiny duck-typed ModuleSpec — has just the attributes
    ``importlib._bootstrap._init_module_attrs`` reads. We can't
    ``from importlib.machinery import ModuleSpec`` because the
    importlib package isn't frozen in our stdlib build."""

    __slots__ = (
        "name", "loader", "origin", "submodule_search_locations",
        "has_location", "cached", "parent", "loader_state",
        "_set_fileattr", "_initializing",
    )

    def __init__(self, name, loader, origin, is_package) -> None:
        self.name = name
        self.loader = loader
        self.origin = origin
        self.has_location = True
        self.cached = None
        self.parent = name if is_package else ""
        self.loader_state = None
        self._set_fileattr = True
        self._initializing = False
        if is_package:
            self.submodule_search_locations = [origin.rsplit("/", 1)[0]]
        else:
            self.submodule_search_locations = None


class _VfsLoader:
    def __init__(self, vdir: str) -> None:
        self._vdir = vdir.rstrip("/")

    # PEP 451 callbacks
    def create_module(self, spec):
        return None   # use default ModuleType

    def exec_module(self, module) -> None:
        path = module.__spec__.origin
        data = vfs.read_sync(path)
        if data is None:
            raise ImportError(f"vfs_import: lost {path}")
        # The kernel shell's _fixup_source rewrites `is None` / `is True` /
        # `is False` to their `==` equivalents — the frozen Python
        # rejects the identity forms when compiling source dynamically.
        # Apply the same fixup here so VFS-imported modules get the same
        # treatment that /examples/run() applies.
        src = data.decode("utf-8")
        for kw in ("None", "True", "False"):
            src = src.replace("is not " + kw, "!= " + kw)
            src = src.replace("is " + kw, "== " + kw)
        # PYCF_ALLOW_TOP_LEVEL_AWAIT (0x2000) — the same flag the shell
        # uses when running /examples/*.py. Without it the kernel's
        # compile() rejects ordinary `def` blocks at module top level
        # with a confusing "cannot delete function call" diagnostic.
        code = compile(src, path, "exec", flags=0x2000)
        exec(code, module.__dict__)

    def get_filename(self, fullname: str) -> str:
        return fullname

    def get_data(self, path: str) -> bytes:
        data = vfs.read_sync(path)
        if data is None:
            raise FileNotFoundError(path)
        return data


# ── Finder ──────────────────────────────────────────────────────────────────

class _VfsFinder:
    """PEP 451 ``find_spec`` finder — no importlib dependency."""

    def find_spec(self, fullname: str, path=None, target=None):
        if "." in fullname:
            return None     # top-level only for v0
        for vdir in search_path:
            base = vdir.rstrip("/") + "/" + fullname
            init = base + "/__init__.py"
            flat = base + ".py"
            if vfs.read_sync(init) is not None:
                return _Spec(fullname, _VfsLoader(vdir),
                              origin=init, is_package=True)
            if vfs.read_sync(flat) is not None:
                return _Spec(fullname, _VfsLoader(vdir),
                              origin=flat, is_package=False)
        return None


_finder: "_VfsFinder | None" = None


def add_search_dir(path: str) -> None:
    """Append a VFS directory to the importer's search path. The dir
    must live on a sync-readable filesystem (tmpfs); disk-backed
    mounts are silently skipped."""
    p = "/" + path.strip("/")
    if p in search_path:
        return
    if not vfs.isdir_sync(p):
        log.info(f"vfs_import: {p} is not on a tmpfs mount; skipping")
        return
    search_path.append(p)
    if p not in sys.path:
        sys.path.append(p)


def install() -> None:
    """Install the meta-path finder and seed the default search dirs.
    Idempotent."""
    global _finder
    if _finder is not None:
        return
    _finder = _VfsFinder()
    sys.meta_path.append(_finder)
    add_search_dir("/examples")
    add_search_dir("/home")
    log.info(f"vfs_import: search path = {search_path}")
