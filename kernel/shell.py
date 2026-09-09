"""
kernel.shell — PythonOS interactive shell.

This is not a userspace program running on the OS — it IS the OS talking
to itself. The shell runs as a kernel task, with full access to kernel
internals. exec() of shell input runs in the kernel's own namespace.

Two I/O backends:
  - Console (framebuffer): default when a display is present
  - Serial (COM1):         always mirrored; primary when no display

Command dispatch
----------------
Bare words that are not Python names are looked up as /bin/<word>.py and
executed automatically, so the user can type:

    sysinfo
    ls /tmp
    cd /bin
    ps

Scripts receive two extra namespace variables:
  argv  — list of string arguments (may be empty)
  cwd   — current working directory string

Top-level `await` is supported in scripts via PyCF_ALLOW_TOP_LEVEL_AWAIT,
so scripts can call async VFS operations directly:

    entries = await vfs.readdir(path)

The `sh()` built-in provides the same dispatch programmatically:

    sh('ls /tmp')
    sh('cp /bin/sysinfo.py /tmp/copy.py')
"""


import asyncio
import traceback
from typing import Callable, Awaitable


_PYCF_ALLOW_TOP_LEVEL_AWAIT = 0x2000


class Shell:
    PROMPT      = ">>> "
    CONT_PROMPT = "... "

    def __init__(self,
                 read_char: Callable[[], Awaitable[str]],
                 write: Callable[[str], None],
                 read_byte=None,
                 write_raw=None,
                 can_exit: bool = False) -> None:
        self._read       = read_char
        self._write      = write
        self._read_byte  = read_byte
        self._write_raw  = write_raw
        self._can_exit   = can_exit
        self._exit_requested = False
        self._block      = ""         # accumulated multi-line block
        self._cwd        = "/"        # current working directory
        self._ns         = self._build_namespace()
        self._completion_entries = []
        # Linenoise editing is available iff both raw streams were
        # supplied. The TCP REPL (kernel/net/repl_server.py) and the
        # serial bring-up in kernel/__init__.py wire both; consumers
        # that only have a char-at-a-time loop (early boot diagnostics,
        # tests) fall back to the simple line buffer.
        self._linenoise_ready = False
        if read_byte is not None and write_raw is not None:
            try:
                import _hal
                _hal.linenoise_history_set_max_len(64)
                self._linenoise_ready = True
            except Exception:
                self._linenoise_ready = False

    def _build_namespace(self) -> dict:
        import kernel
        import kernel.log as log
        import kernel.net as net
        import kernel.sound as sound
        from kernel.bus.pci import bus as pci
        from kernel.fs.vfs import vfs, OpenFlags
        from kernel.scheduler import scheduler
        import kernel.display as display
        from kernel.drivers.block import virtio_blk
        from kernel.bridge import py_desktop

        ns = {
            "__name__":  "pythonos_shell",
            "kernel":    kernel,
            "log":       log,
            "pci":       pci,
            "vfs":       vfs,
            "OpenFlags": OpenFlags,
            "net":       net,
            "sound":     sound,
            "scheduler": scheduler,
            "display":   display,
            "py_desktop": py_desktop,
            "desktop":   lambda app=None: self._desktop(app),
            "examples":  lambda: self._examples(),
            "virtio_blk": virtio_blk,
            "help":      lambda: self._help(),
            "exit":      lambda: self._request_exit(),
            "quit":      lambda: self._request_exit(),
            "halt":      lambda: self._halt_help(),
            "clear":     lambda: self._clear(),
            "sh":        lambda cmd=None: self._sh(cmd),
            "run":       lambda path: self._run(path),
            "print":     lambda *args, sep=" ", end="\n":
                             self._write(sep.join(str(a) for a in args) + end),
            "cwd":       "/",
        }
        return ns

    async def _read_line(self, prompt: str):
        """Prompt the user for one line of input.

        With raw byte/write callables available, defers to linenoise for
        full line editing (cursor movement, history recall, Ctrl-K/U/W,
        etc.). Otherwise falls back to the simple char-buffered loop the
        shell shipped with originally.
        Returns the line as a str, or None if the user signalled EOF
        (Ctrl-C / Ctrl-D / closed connection).
        """
        if self._linenoise_ready:
            try:
                from kernel.linenoise import linenoise_edit
                self._completion_entries = await self._read_completion_entries()
                line = await linenoise_edit(prompt, self._read_byte,
                                             self._write_raw,
                                             self._complete_filename)
                # linenoise emits its own \r\n on Enter; for fallback
                # parity we still want a newline after the line is in
                # if the user came in via fallback path.
                return line
            except Exception:
                # Any runtime failure (e.g. _hal busy) drops back to
                # the buffered loop so the shell never wedges.
                pass
        return await self._read_line_fallback(prompt)

    async def _read_line_fallback(self, prompt: str):
        self._write(prompt)
        buf = ""
        while True:
            ch = await self._read()
            if ch == '\n':
                self._write('\n')
                return buf
            if ch == '\t':
                matches = await self._completion_matches(buf)
                if matches:
                    completed = matches[0]
                    if len(matches) > 1:
                        completed = self._common_prefix(matches)
                    if len(completed) > len(buf):
                        suffix = completed[len(buf):]
                        buf = completed
                        self._write(suffix)
                continue
            if ch == '\b' or ord(ch) == 127:
                if buf:
                    buf = buf[:-1]
                    self._write('\b \b')
                continue
            buf += ch
            self._write(ch)

    HISTORY_FILE = "/home/.repl_history"

    def _history_add(self, line: str) -> None:
        if not self._linenoise_ready:
            return
        if not line.strip():
            return
        try:
            import _hal
            _hal.linenoise_history_add(line)
        except Exception:
            pass
        # Persistent append happens inline in run() right after this
        # method via _history_persist — keeping it awaited rather than
        # ensure_future'd avoids racing the ext2 driver, which isn't
        # safe under overlapping in-flight operations.

    async def _history_persist(self, line: str) -> None:
        if not self._linenoise_ready or not line.strip():
            return
        try:
            from kernel.fs.vfs import vfs, OpenFlags
            fd = await vfs.open(self.HISTORY_FILE,
                                OpenFlags.WRONLY | OpenFlags.CREAT
                                | OpenFlags.APPEND)
            try:
                await vfs.write(fd, (line + "\n").encode("utf-8"))
            finally:
                vfs.close(fd)
        except Exception:
            pass

    async def _history_load(self) -> None:
        if not self._linenoise_ready:
            return
        try:
            from kernel.fs.vfs import vfs
            try:
                fd = await vfs.open(self.HISTORY_FILE)
            except FileNotFoundError:
                return
            chunks = []
            try:
                while True:
                    chunk = await vfs.read(fd, 4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
            finally:
                vfs.close(fd)
            text = b"".join(chunks).decode("utf-8", errors="replace")
            import _hal
            for entry in text.splitlines():
                if entry.strip():
                    _hal.linenoise_history_add(entry)
        except Exception:
            pass

    async def _read_completion_entries(self) -> list[str]:
        from kernel.fs.vfs import vfs, InodeType

        try:
            names = await vfs.readdir(self._cwd)
        except Exception:
            return []

        entries = []
        for name in names:
            if name in ('.', '..'):
                continue
            completion = name
            try:
                st = await vfs.stat(self._join_path(self._cwd, name))
                if st.inode_type == InodeType.DIR:
                    completion += "/"
            except Exception:
                pass
            entries.append(completion)
        entries.sort()
        return entries

    async def _completion_matches(self, line: str) -> list[str]:
        # Python attribute completion takes precedence when the token
        # under the cursor contains a '.'  — `vfs.<TAB>`, `os.path.<TAB>`,
        # etc. — since it's the more specific signal. We fall through to
        # filename completion otherwise so bare tokens still work.
        py = self._complete_python(line)
        if py:
            return py
        self._completion_entries = await self._read_completion_entries()
        return self._complete_filename(line)

    def _complete_filename(self, line: str) -> list[str]:
        # Used as the linenoise completion callback — runs synchronously
        # against the pre-populated entries. Try Python attribute
        # completion first so `vfs.<TAB>` works; fall back to filename
        # completion when the token has no dot.
        py = self._complete_python(line)
        if py:
            return py
        return self._filename_completion_candidates(
            line, self._completion_entries)

    def _complete_python(self, line: str) -> list[str]:
        delimiters = " \t\r\n'\"`({[=,:;+-*/%&|^~<>!"
        start = len(line)
        while start > 0 and line[start - 1] not in delimiters:
            start -= 1
        token = line[start:]
        if "." not in token:
            return []
        head_dotted, _, partial = token.rpartition(".")
        # Walk a chain of attribute accesses on a name in our namespace.
        parts = head_dotted.split(".")
        if not parts or not parts[0].isidentifier():
            return []
        try:
            obj = self._ns[parts[0]]
        except KeyError:
            return []
        for part in parts[1:]:
            if not part.isidentifier():
                return []
            try:
                obj = getattr(obj, part)
            except Exception:
                return []
        try:
            attrs = dir(obj)
        except Exception:
            return []
        head = line[:start] + head_dotted + "."
        results = []
        for a in attrs:
            if a.startswith("_") and not partial.startswith("_"):
                continue
            if a.startswith(partial):
                results.append(head + a)
        results.sort()
        return results

    @staticmethod
    def _filename_completion_candidates(line: str,
                                        entries: list[str]) -> list[str]:
        delimiters = " \t\r\n'\"`({[=,:;"
        start = len(line)
        while start > 0 and line[start - 1] not in delimiters:
            start -= 1

        token = line[start:]
        leading = ""
        prefix = token
        if token.startswith("./"):
            leading = "./"
            prefix = token[2:]
        if "/" in prefix:
            return []

        head = line[:start]
        return [
            head + leading + entry
            for entry in entries
            if entry.startswith(prefix)
        ]

    @staticmethod
    def _common_prefix(items: list[str]) -> str:
        if not items:
            return ""
        prefix = items[0]
        for item in items[1:]:
            i = 0
            limit = min(len(prefix), len(item))
            while i < limit and prefix[i] == item[i]:
                i += 1
            prefix = prefix[:i]
            if not prefix:
                break
        return prefix

    async def run(self) -> None:
        self._write("\nPythonOS kernel shell\n")
        self._write("Python " + __import__('sys').version + "\n")
        self._write("Type help or help() for commands, demos, and examples.\n")
        self._write("Commands: ls ps pwd cd cat cp mv ftp ed sysinfo netstat\n")
        self._write("Desktop: desktop()  desktop('pacmaze')  desktop('help')\n")
        self._write("Examples: examples()  run('/examples/hello_kernel.py')\n")
        self._write("Helpers: sh()  sh('cmd args')  run('/path')  clear()\n\n")

        await self._history_load()

        while True:
            prompt = self.CONT_PROMPT if self._block else self.PROMPT
            line = await self._read_line(prompt)
            if line is None:
                # EOF / closed: exit the REPL loop cleanly.
                if self._can_exit:
                    return
                self._write("\nThe native kernel console stays active. "
                            "Press Ctrl-A X in the QEMU terminal to stop the VM.\n")
                continue
            self._history_add(line)
            await self._history_persist(line)
            await self._process_line(line)
            if self._exit_requested:
                return

    async def _process_line(self, line: str) -> None:
        if not line.strip() and not self._block:
            return

        self._block += line + "\n"

        # Check if we need more input (open block)
        if self._is_incomplete(self._block):
            return

        src = self._block
        self._block = ""

        # Shell command dispatch: bare word(s) not in Python namespace → /bin/<name>.py
        if await self._try_shell_dispatch(src):
            return

        src = self._fixup_source(src)

        try:
            # Try as expression first (so we can print the value).
            # PyCF_ALLOW_TOP_LEVEL_AWAIT makes `await foo()` work directly at
            # the prompt; the result is a coroutine which we await below.
            try:
                result = eval(
                    compile(src.strip(), "<shell>", "eval",
                            flags=_PYCF_ALLOW_TOP_LEVEL_AWAIT),
                    self._ns,
                )
                if asyncio.iscoroutine(result):
                    result = await result
                if result is not None:
                    self._write(repr(result) + "\n")
            except SyntaxError:
                # Not an expression — exec as statement(s). Same flag so
                # multi-line input with `fd = await vfs.open(...)` works.
                code = compile(src, "<shell>", "exec",
                               flags=_PYCF_ALLOW_TOP_LEVEL_AWAIT)
                result = eval(code, self._ns)
                if asyncio.iscoroutine(result):
                    await result
        except SystemExit:
            self._request_exit()
        except Exception:
            self._write(traceback.format_exc())

    # ── Shell command dispatch ────────────────────────────────────────────────

    async def _try_shell_dispatch(self, src: str) -> bool:
        """Dispatch a bare command name to /bin/<name>.py.  Returns True if handled."""
        line = src.strip()
        # Only dispatch single-line input that looks like a shell invocation
        if not line or '\n' in line:
            return False
        parts = line.split()
        name = parts[0]
        # Must be a plain identifier (no dots, parens, operators…)
        if not name.isidentifier():
            return False
        if name == "help" and len(parts) == 1:
            self._help()
            return True
        if name in ("desktop", "examples"):
            return await self._run_script("/bin/" + name + ".py", parts[1:])
        if name in ("exit", "quit") and len(parts) == 1:
            self._request_exit()
            return True
        if name == "halt" and len(parts) == 1:
            self._halt_help()
            return True
        # Skip Python keywords
        try:
            import keyword
            if keyword.iskeyword(name):
                return False
        except ImportError:
            pass
        # Don't shadow names already live in the Python namespace
        if name in self._ns:
            return False
        return await self._run_script("/bin/" + name + ".py", parts[1:])

    async def _run_script(self, path: str, args: list) -> bool:
        """Load and exec a /bin script.  Returns False if the file is not found."""
        from kernel.fs.vfs import vfs
        try:
            fd = await vfs.open(path)
        except Exception:
            return False

        chunks = []
        while True:
            chunk = await vfs.read(fd, 4096)
            if not chunk:
                break
            chunks.append(chunk)
        vfs.close(fd)

        src = b"".join(chunks).decode("utf-8")

        if await self._try_precompiled_script(path, args, src):
            return True

        src = self._fixup_source(src)

        # Give scripts their own local view; argv and cwd are script-visible
        local_ns = dict(self._ns)
        local_ns['argv'] = args
        local_ns['cwd']  = self._cwd
        local_ns['_write'] = self._write

        try:
            # PyCF_ALLOW_TOP_LEVEL_AWAIT lets scripts use `await` at module level
            code  = compile(src, path, "exec", flags=_PYCF_ALLOW_TOP_LEVEL_AWAIT)
            coro  = eval(code, local_ns)
            if asyncio.iscoroutine(coro):
                await coro
        except Exception:
            self._write(traceback.format_exc())

        # Propagate cwd changes made by the script (e.g. cd.py sets cwd = target)
        self._update_cwd(local_ns.get('cwd'))

        return True

    async def _try_precompiled_script(self, path: str, args: list, src: str) -> bool:
        """Run known seeded scripts from frozen bytecode when available."""
        if path.startswith("/bin/") and path.endswith(".py") and "/" not in path[len("/bin/"):]:
            try:
                import kernel.commands as commands
                name = path[len("/bin/"):]
                if commands.SCRIPTS.get(name) != src:
                    return False
                func = getattr(commands, name[:-3], None)
                if func is None:
                    return False
                if name == "ed.py":
                    result = func(args, self._cwd, self._write, self._read)
                else:
                    result = func(args, self._cwd, self._write)
                if asyncio.iscoroutine(result):
                    result = await result
                self._update_cwd(result)
                return True
            except Exception:
                self._write(traceback.format_exc())
                return True

        if path.startswith("/examples/") and path.endswith(".py"):
            try:
                from kernel.frozen_sources import SOURCES
                if SOURCES.get(path) != src:
                    return False
                mod_name = "examples." + path[len("/examples/"):-3].replace("/", ".")
                import sys
                sys.modules.pop(mod_name, None)
                mod = __import__(mod_name, fromlist=["main"])
                main = getattr(mod, "main", None)
                if main is None:
                    self._write("run: " + path + ": no main() in frozen example\n")
                    return True
                result = main(
                    argv=args,
                    cwd=self._cwd,
                    read_char=self._read,
                    write=self._write,
                )
                if asyncio.iscoroutine(result):
                    result = await result
                self._update_cwd(result)
                return True
            except Exception:
                self._write(traceback.format_exc())
                return True

        return False

    def _update_cwd(self, new_cwd) -> None:
        if isinstance(new_cwd, str) and new_cwd != self._cwd:
            self._cwd = new_cwd
            self._ns['cwd'] = new_cwd

    async def _sh(self, cmd=None) -> None:
        """sh() → interactive sub-shell.  sh('cmd args') → dispatch to /bin/."""
        if cmd is None:
            await self._sh_repl()
            return
        parts = cmd.strip().split()
        if not parts:
            return
        await self._run_sh_parts(parts)

    async def _sh_repl(self) -> None:
        """Interactive sub-shell: $ prompt, command dispatch, 'exit' to return.

        Uses the same line-edit path as the top-level Python REPL so
        users get arrow-key cursor movement, backspace, and Ctrl-A/E,
        plus a separate history ring (we keep using the global
        linenoise history, so commands typed in the sub-shell are
        recallable in the parent REPL).
        """
        SH = "$ "
        self._write("PythonOS shell: help | examples | desktop [APP] | exit\n")
        while True:
            line = await self._read_line(SH)
            if line is None:
                return
            line = line.strip()
            if line == 'exit':
                return
            if line:
                self._history_add(line)
                parts = line.split()
                await self._run_sh_parts(parts)

    async def _run_sh_parts(self, parts: list[str]) -> None:
        name = parts[0]
        args = parts[1:]

        if name in ("help", "?") and not args:
            self._help()
            return

        path = self._sh_script_path(name)
        if path is not None:
            if not await self._run_script(path, args):
                self._write("sh: " + name + ": not found\n")
            return

        if not await self._run_script("/bin/" + name + ".py", args):
            self._write("sh: " + name + ": command not found\n")

    def _sh_script_path(self, name: str) -> str | None:
        if not name.endswith(".py"):
            return None
        if name.startswith("/"):
            target = name
        else:
            target = self._join_path(self._cwd, name)

        return self._normalize_path(target)

    @staticmethod
    def _join_path(base: str, path: str) -> str:
        if path.startswith("/"):
            return path
        base = base.rstrip("/")
        if not base:
            return "/" + path
        return base + "/" + path

    @staticmethod
    def _normalize_path(path: str) -> str:
        parts = []
        for seg in path.split("/"):
            if seg == "..":
                if parts:
                    parts.pop()
            elif seg and seg != ".":
                parts.append(seg)
        return "/" + "/".join(parts)

    async def _run(self, path: str) -> None:
        """run('/full/path/to/script.py') — execute any VFS file by absolute path."""
        if not await self._run_script(path, []):
            self._write("run: " + path + ": not found\n")

    async def _desktop(self, app_name=None) -> None:
        """desktop([app]) — open the GUI through the active display backend."""
        from kernel import commands
        args = [] if app_name is None else [str(app_name)]
        await commands.desktop(args, self._cwd, self._write)

    async def _examples(self) -> None:
        """examples() — list programs frozen into the /examples directory."""
        from kernel import commands
        await commands.examples([], self._cwd, self._write)

    def _request_exit(self) -> None:
        if self._can_exit:
            self._exit_requested = True
            return
        self._write(
            "The native kernel console stays active. "
            "Press Ctrl-A X in the QEMU terminal to stop the VM.\n"
        )

    def _halt_help(self) -> None:
        self._write(
            "PythonOS has no guest halt command. "
            "Press Ctrl-A X in the QEMU terminal (or stop QEMU from the host).\n"
        )

    # ── Source fixups for frozen Python 3.14 ─────────────────────────────────

    @staticmethod
    def _fixup_source(src: str) -> str:
        """Rewrite 'is [not] None/True/False' → '==/!= None/True/False'.

        The frozen Python 3.14 kernel fails to compile these forms; the
        equality equivalents work correctly for singleton constants.
        """
        for kw in ('None', 'True', 'False'):
            src = src.replace('is not ' + kw, '!= ' + kw)
            src = src.replace('is ' + kw, '== ' + kw)
        return src

    @staticmethod
    def _is_incomplete(src: str) -> bool:
        try:
            import codeop
            result = codeop.compile_command(src, "<shell>", "exec")
            return result is None   # None = need more input
        except SyntaxError:
            return False
        except Exception as _e:
            # codeop unavailable (e.g. _py_warnings not frozen yet);
            # surface in the kernel log so silent breakage doesn't hide
            # regressions like the multi-line-def-at-REPL one.
            try:
                import kernel.log as _log
                _log.warn(f"_is_incomplete: codeop failed: {_e!r}")
            except Exception:
                pass
            return False

    # ── Built-in shell commands ───────────────────────────────────────────────

    def _help(self) -> None:
        self._write(
            "\nCommands (type bare name or sh('cmd args')):\n"
            "  ls [path]      — list directory\n"
            "  ps             — kernel task list\n"
            "  pwd            — print working directory\n"
            "  cd [path]      — change directory\n"
            "  cat FILE [...] — print file contents\n"
            "  cp SRC DST     — copy file\n"
            "  mv SRC DST     — move / rename file\n"
            "  ftp get/put    — copy files over TCP\n"
            "  ed [path]      — ed-style line editor\n"
            "  sysinfo        — system overview\n"
            "  netstat        — network status\n"
            "  desktop [APP]  — open the GUI desktop; optionally launch APP\n"
            "  desktop --list — list bundled desktop apps, demos, and games\n"
            "  examples       — list readable programs frozen into /examples\n"
            "  clear()        — clear framebuffer console\n"
            "  run('/path')   — run script by absolute path\n"
            "  sh()           — enter shell sub-REPL\n"
            "  sh('cmd args') — same, with shell-style argument splitting\n"
            "  foo.py         — in sh(), run a Python file from cwd\n"
            "  /path/file.py  — in sh(), run a Python file directly\n"
            "\nDesktop from Python:\n"
            "  desktop()            — open the desktop\n"
            "  desktop('pacmaze')   — open it and launch a bundled game\n"
            "  desktop('help')      — list all apps, demos, and games\n"
            "\nBundled examples:\n"
            "  examples()                         — list /examples\n"
            "  run('/examples/hello_kernel.py')   — run one\n"
            "  cat /examples/README.txt           — usage and descriptions\n"
            "\nLeaving the shell:\n"
            "  exit() / quit() — close a TCP or desktop terminal session\n"
            "                    (the native kernel console stays active)\n"
            "  halt / halt()   — show how to stop the VM from the host\n"
            "\nLive kernel objects:\n"
            "  pci        — PCI bus: list(pci), pci.find_by_class(0x0200)\n"
            "  scheduler  — task scheduler: scheduler.ps()\n"
            "  vfs        — filesystem: await vfs.readdir('/')\n"
            "  display    — framebuffer / console\n"
            "  net        — network: net.local_ip\n"
            "  sound      — HDA audio: sound.hda.generate_tone(freq, ms)\n"
            "  cwd        — current working directory (string)\n\n"
        )

    def _clear(self) -> None:
        from kernel.display.console import console
        if console:
            console.clear()
