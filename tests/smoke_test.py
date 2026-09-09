#!/usr/bin/env python3
"""
Smoke test for PythonOS: boots the ISO under QEMU, waits for the TCP REPL
on port 5555 (host) → 5000 (guest), runs a handful of Python expressions,
and verifies expected output.

Usage:
    python3 tests/smoke_test.py [path/to/build/pythonos.iso]

Exit code: 0 = all tests passed, 1 = failure.
"""

import atexit
import os
import socket
import subprocess
import sys
import tempfile
import time

ISO = sys.argv[1] if len(sys.argv) > 1 else "build/pythonos.iso"
import platform

HOST_PORT = int(os.environ.get("PYTHONOS_HOST_PORT", "5555"))
FILE_HOST_PORT = int(os.environ.get("PYTHONOS_FILE_PORT", "17000"))
SMP_CPUS = os.environ.get("PYTHONOS_SMP_CPUS", "2")
FREE_THREADING = os.environ.get("PYTHONOS_FREE_THREADING", "1")
BOOT_TIMEOUT = 90      # seconds to wait for REPL to become reachable
RECV_TIMEOUT = 15.0    # per-response timeout


def _qemu_accel_for(target_arch: str) -> list:
    """Match the GNUmakefile policy: hardware acceleration (HVF/KVM) when
    the guest matches the host architecture, plain TCG with a generic CPU
    otherwise. arm64 HVF on Apple Silicon requires GICv3 (not yet
    implemented; tracked by pythonos-h7g), so arm64 stays on TCG even
    when running natively."""
    host_machine = platform.machine().lower()
    host_arch = "arm64" if host_machine in ("arm64", "aarch64") else "x86_64"
    host_os = platform.system()
    if host_arch != target_arch:
        return ["-cpu", "qemu64" if target_arch == "x86_64" else "cortex-a57"]
    if target_arch == "arm64":
        return ["-cpu", "cortex-a57"]
    mode = os.environ.get("PYTHONOS_QEMU_ACCEL", "auto").strip().lower() or "auto"
    if mode not in ("auto", "kvm", "tcg"):
        raise ValueError("PYTHONOS_QEMU_ACCEL must be auto, kvm, or tcg")
    if mode == "tcg":
        return ["-cpu", "qemu64"]

    kvm_ok = (host_os == "Linux"
              and os.path.exists("/dev/kvm")
              and os.access("/dev/kvm", os.R_OK | os.W_OK))
    if mode == "kvm" and (host_os != "Linux" or not kvm_ok):
        raise ValueError(
            "PYTHONOS_QEMU_ACCEL=kvm requested, but KVM is not usable")
    accel = None
    if host_os == "Darwin":
        accel = "hvf"
    elif host_os == "Linux" and kvm_ok:
        accel = "kvm"
    if accel:
        return ["-cpu", "host", "-accel", accel]
    return ["-cpu", "qemu64"]


DISK = os.environ.get("PYTHONOS_DISK", "build/disk.img")

QEMU_CMD = [
    "qemu-system-x86_64",
    "-machine", "q35",
    *_qemu_accel_for("x86_64"),
    "-m", "2G", "-smp", SMP_CPUS,
    "-netdev", f"user,id=net0,hostfwd=tcp::{HOST_PORT}-:5000,hostfwd=tcp::{FILE_HOST_PORT}-:7000",
    "-device", "virtio-net-pci,netdev=net0",
    "-device", "intel-hda", "-device", "hda-duplex",
    "-drive", f"if=none,file={DISK},format=raw,id=hd0",
    "-device", "virtio-blk-pci,drive=hd0",
    "-no-reboot", "-no-shutdown",
    "-cdrom", ISO, "-boot", "d",
    "-nographic",
    # serial output captured to a temp file for diagnostics
]

# (expression_to_send, substring_expected_in_response)
TEST_CASES = [
    ("1 + 1\n",                         "2"),
    ("'hello' + ' world'\n",            "hello world"),
    ("type(scheduler).__name__\n",      "Scheduler"),
    ("vfs is not None\n",               "True"),
    ("len([x*x for x in range(5)])\n",  "5"),
    ("1 / 0\n",                         "ZeroDivisionError"),
    ("run('/bin/sysinfo.py')\n",        "PythonOS"),
    ("run('/bin/netstat.py')\n",        "Interface"),
    ("sh('ps')\n",                      "kshell"),
    ("sh('/bin/sysinfo.py')\n",          "PythonOS"),
    ("ls /bin\n",                       "ed.py"),
    ("cat /examples/README.txt\n",      "PythonOS examples"),
    ("help\n",                          "desktop('pacmaze')"),
    ("sh('help')\n",                    "Bundled examples:"),
    ("sh('desktop --list')\n",          "Demos: audio_tone"),
    ("sh('examples')\n",                "Frozen examples in /examples:"),
    ("desktop('help')\n",               "Games: defender, pacmaze, raiders, sprites"),
    ("examples()\n",                    "Frozen examples in /examples:"),
    ("halt\n",                          "PythonOS has no guest halt command"),
    ("ftp\n",                           "usage: ftp get DST"),
    ("ftp get /tmp/repl-port.txt 5000\n", "ftp: port already in use: 5000"),
    ("ls /examples\n",                  "hello_kernel.py"),
    ("vi\n",                            "NameError"),
    ("__import__('_hal').PY_GIL_DISABLED\n", "1" if FREE_THREADING == "1" else "0"),
    # linenoise wrappers (no-tty path: blocking call returns None
    # immediately because isatty(STDIN_FILENO)=0 in our libc).
    ("__import__('_hal').linenoise_history_set_max_len(50)\n", "1"),
    ("__import__('_hal').linenoise_history_add('test entry')\n", "1"),
    ("__import__('_hal').linenoise(':no-tty: ') is None\n", "True"),
    # virtio-blk-pci (ef6.3): driver bound, num_sectors matches the 64 MiB
    # ext2 disk image (64*1024*1024/512 = 131072 sectors). num_sectors comes
    # from device config space, so this proves enumeration → bind → init all
    # succeeded end-to-end.
    ("virtio_blk.blk.num_sectors\n", "131072"),
    # ef6.4: /home is wired up at boot (ext2 mount on arm64; tmpfs fallback
    # on x86 until the PCI virtio-blk read_sector hang is fixed).
    # /examples/check_home.py writes to /home/smoke.txt, reads it back, prints
    # a marker iff the round-trip matches. Proves the boot wiring end-to-end.
    ("run('/examples/check_home.py')\n", "EF64_HOME_OK"),
    # Dynamic compile() of compound statements + VFS-backed import.
    # Regression guard for pythonos-0ta (libc strncmp returned wrong value
    # when prefix matched but n was exhausted — broke all keyword lookups
    # in the PEG parser, manifesting as 'cannot delete function call' for
    # def/class/for/if/import). Together these prove the parser, the VFS
    # importer, and tmpfs sync-read are all wired up.
    ('compile("def f(): return 7", "<t>", "exec") is not None\n', "True"),
    ("__import__('_vfs_test').square(9)\n", "81"),
]

if SMP_CPUS.isdigit():
    TEST_CASES.append((
        "(__import__('_hal').SMP_ONLINE, __import__('_hal').SMP_CPUS)\n",
        f"({SMP_CPUS}, {SMP_CPUS})",
    ))
    TEST_CASES.append((
        "(__import__('_hal').SMP_WORKERS, __import__('_hal').SMP_ONLINE)\n",
        f"({SMP_CPUS}, {SMP_CPUS})",
    ))
    if int(SMP_CPUS) > 1:
        TEST_CASES.append((
            "__import__('_hal').pthread_selftest()\n",
            "(0, 123456789, 4660)",
        ))
        # smp_run_selftest dispatches a no-op runner to each AP and joins.
        # With SMP_CPUS=2 (1 AP), executed=1 and total=2.
        TEST_CASES.append((
            "__import__('_hal').smp_run_selftest()\n",
            f"({int(SMP_CPUS)-1}, {SMP_CPUS})",
        ))


def wait_for_port(port: int, timeout: float, proc: subprocess.Popen) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False  # QEMU exited early
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def recv_until_prompt(sock: socket.socket, prompt: bytes = b">>> ") -> str:
    buf = b""
    sock.settimeout(RECV_TIMEOUT)
    deadline = time.monotonic() + RECV_TIMEOUT
    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk
        if prompt in buf:
            break
    return buf.decode("utf-8", errors="replace")


def run() -> int:
    if not os.path.exists(ISO):
        print(f"[FAIL] ISO not found: {ISO}")
        print("       Run 'make' first to build the kernel image.")
        return 1

    serial_log = tempfile.NamedTemporaryFile(
        mode="w", suffix=".log", prefix="pythonos-serial-",
        delete=False
    )
    serial_log.close()
    atexit.register(lambda: os.unlink(serial_log.name) if os.path.exists(serial_log.name) else None)

    cmd = QEMU_CMD + ["-serial", f"file:{serial_log.name}"]

    print(f"[smoke] Starting QEMU with {ISO} ...")
    print(f"[smoke] Serial log: {serial_log.name}")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    def _cleanup():
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    try:
        print(f"[smoke] Waiting up to {BOOT_TIMEOUT}s for TCP REPL on port {HOST_PORT} ...")
        if not wait_for_port(HOST_PORT, BOOT_TIMEOUT, proc):
            rc = proc.poll()
            print(f"[FAIL] TCP REPL never became reachable on port {HOST_PORT}")
            if rc is not None:
                print(f"       QEMU exited early with code {rc}")
                stderr = proc.stderr.read().decode("utf-8", errors="replace")
                if stderr.strip():
                    print(f"       QEMU stderr: {stderr.strip()}")
            _print_serial(serial_log.name)
            return 1

        sock = None
        banner = ""
        for attempt in range(1, 4):
            try:
                s = socket.create_connection(("127.0.0.1", HOST_PORT), timeout=5)
            except OSError as e:
                print(f"[smoke] connect attempt {attempt}: {e}; retrying...")
                time.sleep(1.0)
                continue
            try:
                b = recv_until_prompt(s)
                if ">>>" in b:
                    sock = s
                    banner = b
                    break
                s.close()
            except Exception:
                try:
                    s.close()
                except Exception:
                    pass
            print(f"[smoke] attempt {attempt}: no prompt yet, retrying in 1s...")
            time.sleep(1.0)

        if sock is None:
            print(f"[FAIL] No shell prompt after 3 connection attempts")
            _print_serial(serial_log.name)
            return 1

        print(f"[smoke] Connected — shell prompt received.")
        try:
            passed = 0
            failed = 0
            for label, expected in [
                ("banner lists cat", "Commands: ls ps pwd cd cat"),
                ("banner advertises desktop()", "Desktop: desktop()"),
                ("banner advertises examples()", "Examples: examples()"),
                ("banner lists sh()", "Helpers: sh()"),
            ]:
                if expected in banner:
                    print(f"[PASS] {label:45s} → found {expected!r}")
                    passed += 1
                else:
                    print(f"[FAIL] {label:45s} → expected {expected!r}")
                    print(f"       got: {banner!r}")
                    failed += 1

            for expr, expected in TEST_CASES:
                sock.sendall(expr.encode())
                response = recv_until_prompt(sock)
                if expected in response:
                    print(f"[PASS] {expr.strip()!r:45s} → found {expected!r}")
                    passed += 1
                else:
                    print(f"[FAIL] {expr.strip()!r:45s} → expected {expected!r}")
                    print(f"       got: {response!r}")
                    failed += 1

            if run_file_copy_test(sock):
                passed += 1
            else:
                failed += 1

            if run_multiline_def_test(sock):
                passed += 1
            else:
                failed += 1

            if run_ed_editor_test(sock):
                passed += 1
            else:
                failed += 1

            example_passed, example_failed = run_example_tests(sock)
            passed += example_passed
            failed += example_failed

            if serial_contains(serial_log.name, "kernel thread self-test OK"):
                print("[PASS] boot kernel-thread self-test             -> serial marker found")
                passed += 1
            else:
                print("[FAIL] boot kernel-thread self-test             -> missing serial marker")
                failed += 1

            expected_smp = f"SMP online {SMP_CPUS}/{SMP_CPUS}"
            if SMP_CPUS.isdigit() and serial_contains(serial_log.name, expected_smp):
                print("[PASS] boot SMP AP startup                     -> serial marker found")
                passed += 1
            elif not SMP_CPUS.isdigit() and serial_contains(serial_log.name, "SMP online "):
                print("[PASS] boot SMP AP startup                     -> serial marker found")
                passed += 1
            else:
                print("[FAIL] boot SMP AP startup                     -> missing serial marker")
                failed += 1

            expected_workers = f"SMP workers {SMP_CPUS}/{SMP_CPUS} completed"
            if SMP_CPUS.isdigit() and serial_contains(serial_log.name, expected_workers):
                print("[PASS] boot SMP worker dispatch                -> serial marker found")
                passed += 1
            elif not SMP_CPUS.isdigit() and serial_contains(serial_log.name, "SMP workers "):
                print("[PASS] boot SMP worker dispatch                -> serial marker found")
                passed += 1
            else:
                print("[FAIL] boot SMP worker dispatch                -> missing serial marker")
                failed += 1

            print(f"\n[smoke] {passed} passed, {failed} failed")
            if failed:
                _print_serial(serial_log.name)
            return 0 if failed == 0 else 1

        finally:
            sock.close()
    finally:
        _cleanup()


def run_file_copy_test(sock: socket.socket) -> bool:
    payload = b"hello from host via ftp\n"
    target = "/tmp/ftp-in.txt"

    if not run_ftp_get_once(sock, target, payload):
        return False
    if not run_ftp_get_once(sock, "/tmp/ftp-in-2.txt", b"second ftp get\n"):
        return False
    if not run_ftp_put_once(sock, target, payload):
        return False

    print("[PASS] 'ftp get/put file copy'                   → round-trip bytes matched")
    return True


def run_multiline_def_test(sock: socket.socket) -> bool:
    """Define a function across multiple input lines, then call it.

    Regression guard: codeop must be importable for the shell to detect
    incomplete blocks. Previously this silently fell back to "compile
    every line as complete", which broke def/class at the prompt because
    `_py_warnings` wasn't frozen and codeop's import chain failed.

    We send the whole block plus the call as one stream and read until
    we see the call's marker — that way we don't have to second-guess
    when each `>>> ` lands relative to `... ` continuation prompts.
    """
    payload = (
        b"def _multiline_smoke(x):\n"
        b"    return x + x + x\n"
        b"\n"
        b"_multiline_smoke(14)\n"
    )
    sock.sendall(payload)
    buf = b""
    sock.settimeout(RECV_TIMEOUT)
    deadline = time.monotonic() + RECV_TIMEOUT
    marker = b"42"
    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk
        # Need both the marker and a trailing prompt so the next test
        # starts cleanly.
        if marker in buf and buf.rstrip().endswith(b">>>"):
            break
    response = buf.decode("utf-8", errors="replace")
    if marker.decode() not in response:
        print("[FAIL] 'multi-line def at REPL' did not return 42")
        print(f"       got: {response!r}")
        return False
    print("[PASS] 'multi-line def at REPL'                 → returned 42")
    return True


def run_ed_editor_test(sock: socket.socket) -> bool:
    target = "/tmp/ed-test.txt"
    script = (
        "ed -s " + target + "\n"
        "a\n"
        "alpha\n"
        "beta\n"
        ".\n"
        ",p\n"
        "1,2n\n"
        "w\n"
        "Q\n"
    )

    sock.sendall(script.encode())
    response = recv_until_prompt(sock)
    if "alpha" not in response or "beta" not in response or "2\tbeta" not in response:
        print("[FAIL] 'ed append/print/write' did not show expected buffer output")
        print(f"       got: {response!r}")
        return False

    sock.sendall(("cat " + target + "\n").encode())
    response = recv_until_prompt(sock)
    if "alpha" not in response or "beta" not in response:
        print("[FAIL] 'ed append/print/write' did not save expected file content")
        print(f"       got: {response!r}")
        return False

    print("[PASS] 'ed append/print/write'                  → file content matched")
    return True


def run_example_tests(sock: socket.socket) -> tuple[int, int]:
    passed = 0
    failed = 0
    runners = [
        run_hello_kernel_example,
        run_vfs_demo_example,
        run_async_tasks_example,
    ]
    if SMP_CPUS.isdigit() and int(SMP_CPUS) > 1:
        runners.append(run_thread_demo_example)
        runners.append(run_pthread_coverage_example)
    runners.extend([
        run_primes_example,
        run_recv_file_example,
        run_send_file_example,
        run_tone_example,
        run_linenoise_demo_example,
    ])

    for runner in runners:
        if runner(sock):
            passed += 1
        else:
            failed += 1
    return passed, failed


def run_simple_example(sock: socket.socket, expr: str, expected_markers: tuple[str, ...]) -> bool:
    sock.sendall(expr.encode())
    response = recv_until_prompt(sock)
    missing = [marker for marker in expected_markers if marker not in response]
    if not missing and ">>>" in response:
        print(f"[PASS] {expr.strip()!r:45s} -> found example markers")
        return True

    print(f"[FAIL] {expr.strip()!r:45s} -> missing {missing!r}")
    print(f"       got: {response!r}")
    return False


def run_hello_kernel_example(sock: socket.socket) -> bool:
    return run_simple_example(
        sock,
        "run('/examples/hello_kernel.py')\n",
        ("Hello, PythonOS!", "root entries:", "tasks:"),
    )


def run_vfs_demo_example(sock: socket.socket) -> bool:
    return run_simple_example(
        sock,
        "run('/examples/vfs_demo.py')\n",
        ("VFS demo wrote", "read back:", "PythonOS VFS demo"),
    )


def run_async_tasks_example(sock: socket.socket) -> bool:
    return run_simple_example(
        sock,
        "run('/examples/async_tasks.py')\n",
        ("async queue demo", "producer sent: 4", "consumer total: 10"),
    )


def run_primes_example(sock: socket.socket) -> bool:
    return run_simple_example(
        sock,
        "sh('/examples/primes.py 30')\n",
        ("Prime numbers up to 30", "found 10 primes"),
    )


def run_tone_example(sock: socket.socket) -> bool:
    expr = "run('/examples/tone.py')\n"
    sock.sendall(expr.encode())
    response = recv_until_prompt(sock)
    expected = (
        "Generated PythonOS tone buffer",
        "No HDA device is available",
    )
    for marker in expected:
        if marker in response and ">>>" in response:
            print(f"[PASS] {expr.strip()!r:45s} → found {marker!r}")
            return True

    print(f"[FAIL] {expr.strip()!r:45s} → expected a completed tone example status")
    print(f"       got: {response!r}")
    return False


def run_thread_demo_example(sock: socket.socket) -> bool:
    return run_simple_example(
        sock,
        "run('/examples/thread_demo.py')\n",
        (
            "thread demo",
            "worker ident: True",
            "worker done: True",
            "delayed ident: True",
            "workers: " + str(max(1, min(3, int(SMP_CPUS) - 1))),
            "values: " + repr(list(range(max(1, min(3, int(SMP_CPUS) - 1))))),
            "timeout expired: True",
            "delayed acquire: True",
        ),
    )


def run_linenoise_demo_example(sock: socket.socket) -> bool:
    # Closes pythonos-e6f: drives _hal's non-blocking linenoise edit
    # surface from kernel/linenoise.py with a synthetic byte stream.
    return run_simple_example(
        sock,
        "run('/examples/linenoise_demo.py')\n",
        (
            "linenoise demo start",
            "linenoise edit ok line='hell world'",
            "linenoise eof ok",
            "linenoise history ok",
            "linenoise demo done",
        ),
    )


def run_pthread_coverage_example(sock: socket.socket) -> bool:
    # Covers beads pythonos-xa7.{1,2,3,4,5,6}: lifecycle, identity, TSS,
    # lock+condvar, AP capacity, and attr surface.
    return run_simple_example(
        sock,
        "run('/examples/pthread_coverage.py')\n",
        (
            "pthread coverage start",
            "lifecycle ok",
            "identity ok",
            "tss ok",
            "lock ok",
            "capacity ok",
            "attr ok",
            "pthread coverage done passed=6/6",
        ),
    )


def run_recv_file_example(sock: socket.socket) -> bool:
    payload = b"hello from recv_file example\n"
    target = "/tmp/example-recv.txt"
    expr = "sh('/examples/recv_file.py 7000 " + target + "')\n"

    sock.sendall(expr.encode())
    response = recv_until_prompt(sock, prompt=b"Saving to ")
    if "Receiving one file" not in response:
        print(f"[FAIL] {expr.strip()!r:45s} → recv_file did not start listening")
        print(f"       got: {response!r}")
        return False

    try:
        with socket.create_connection(("127.0.0.1", FILE_HOST_PORT), timeout=5) as data_sock:
            data_sock.sendall(payload)
    except OSError as e:
        print(f"[FAIL] {expr.strip()!r:45s} → host could not connect: {e}")
        return False

    response += recv_until_prompt(sock)
    expected = "saved " + str(len(payload)) + " bytes"
    if expected in response:
        print(f"[PASS] {expr.strip()!r:45s} → found {expected!r}")
        return True

    print(f"[FAIL] {expr.strip()!r:45s} → expected {expected!r}")
    print(f"       got: {response!r}")
    return False


def run_send_file_example(sock: socket.socket) -> bool:
    source = "/examples/README.txt"
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(10)
    put_port = listener.getsockname()[1]
    expr = "sh('/examples/send_file.py 10.0.2.2 " + str(put_port) + " " + source + "')\n"

    received = b""
    try:
        sock.sendall(expr.encode())
        conn, _ = listener.accept()
        conn.settimeout(10)
        try:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                received += chunk
        finally:
            conn.close()
    except OSError as e:
        print(f"[FAIL] {expr.strip()!r:45s} → host could not receive data: {e}")
        return False
    finally:
        listener.close()

    response = recv_until_prompt(sock)
    if b"PythonOS examples" not in received:
        print(f"[FAIL] {expr.strip()!r:45s} → received unexpected bytes")
        print(f"       got: {received!r}")
        return False
    if "sent " not in response or " bytes from " + source not in response:
        print(f"[FAIL] {expr.strip()!r:45s} → send_file did not report expected send")
        print(f"       got: {response!r}")
        return False

    print(f"[PASS] {expr.strip()!r:45s} → README bytes received")
    return True


def run_ftp_get_once(sock: socket.socket, target: str, payload: bytes) -> bool:
    sock.sendall(("ftp get " + target + "\n").encode())
    response = recv_until_prompt(
        sock,
        prompt=b"ftp: waiting for one incoming file stream",
    )
    if "ftp: waiting for one incoming file stream" not in response:
        print("[FAIL] 'ftp get' did not start listening")
        print(f"       got: {response!r}")
        return False

    try:
        with socket.create_connection(("127.0.0.1", FILE_HOST_PORT), timeout=5) as data_sock:
            data_sock.sendall(payload)
    except OSError as e:
        print(f"[FAIL] host could not connect to ftp get port {FILE_HOST_PORT}: {e}")
        return False

    response += recv_until_prompt(sock)
    expected = "ftp: saved " + str(len(payload)) + " bytes to " + target
    if expected not in response:
        print("[FAIL] 'ftp get' did not save expected bytes")
        print(f"       expected: {expected!r}")
        print(f"       got: {response!r}")
        return False

    return True


def run_ftp_put_once(sock: socket.socket, target: str, payload: bytes) -> bool:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(10)
    put_port = listener.getsockname()[1]

    received = b""
    try:
        sock.sendall(("ftp put " + target + " 10.0.2.2 " + str(put_port) + "\n").encode())
        conn, _ = listener.accept()
        conn.settimeout(10)
        try:
            while len(received) < len(payload):
                chunk = conn.recv(4096)
                if not chunk:
                    break
                received += chunk
        finally:
            conn.close()
    except OSError as e:
        print(f"[FAIL] host could not receive ftp put data: {e}")
        return False
    finally:
        listener.close()

    response = recv_until_prompt(sock)
    if received != payload:
        print("[FAIL] 'ftp put' returned different bytes")
        print(f"       expected: {payload!r}")
        print(f"       got: {received!r}")
        return False
    if "ftp: sent " + str(len(payload)) + " bytes from " + target not in response:
        print("[FAIL] 'ftp put' did not report expected send")
        print(f"       got: {response!r}")
        return False

    return True


def _print_serial(path: str) -> None:
    try:
        with open(path) as f:
            content = f.read()
        if content.strip():
            print(f"\n--- serial log ({path}) ---")
            print(content[-4000:] if len(content) > 4000 else content)
            print("--- end serial log ---")
        else:
            print("[smoke] (serial log is empty)")
    except OSError:
        pass


def serial_contains(path: str, needle: str) -> bool:
    try:
        with open(path) as f:
            return needle in f.read()
    except OSError:
        return False


if __name__ == "__main__":
    sys.exit(run())
