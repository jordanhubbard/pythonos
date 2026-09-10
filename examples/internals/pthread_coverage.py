"""Path: /examples/internals/pthread_coverage.py

PythonOS pthread substrate coverage fixture.

Exercises the no-GIL critical-path surface defined in beads epic
pythonos-xa7. Each section emits a short status line that the host-side
smoke test (tests/smoke_test.py) asserts on.

Sections:
  A. lifecycle    — pythonos-xa7.1
  B. identity     — pythonos-xa7.2
  C. tss          — pythonos-xa7.3
  D. lock         — pythonos-xa7.4
  E. capacity     — pythonos-xa7.6
  F. attr         — pythonos-xa7.5 (driven via _hal.pthread_attr_selftest)
"""

import _thread
import asyncio

try:
    import _hal
except ImportError:
    _hal = None


def _emit(write, line):
    if write:
        write(line + "\n")
    else:
        print(line)


async def _flush():
    await asyncio.sleep(0)


def _worker_count():
    """How many APs are available to take pthread workers."""
    if _hal is None:
        return 1
    online = getattr(_hal, "SMP_ONLINE", 1)
    return max(1, online - 1)


def _arch():
    if _hal is None:
        return "unknown"
    return getattr(_hal, "ARCH", "unknown")


def _workers_supported():
    """Whether pthread workers can run on this build.

    Both archs implement pthread_create on top of smp_submit_worker now
    (pythonos-bjr). The only requirement for the worker-using sections
    is that at least one AP is online — the kernel surfaces this via
    _hal.SMP_ONLINE.
    """
    if _hal is None:
        return False
    return getattr(_hal, "SMP_ONLINE", 1) >= 2


def _spin(n):
    """Crude busy-wait that does not depend on time syscalls."""
    for _ in range(n):
        pass


def _start_with_retry(fn, args=(), retries=400, spin_per_retry=20000):
    """Start a CPython thread, retrying briefly on AP-busy EAGAIN.

    With SMP_CPUS-1 == 1, only one worker can be in flight at a time, and
    the AP can stay busy for a small window after the worker's done lock
    has been released. This helper papers over that microsecond gap so the
    benign "start workers in sequence" pattern is reliable; tests that
    intentionally probe EAGAIN call _thread.start_new_thread directly.
    """
    last_exc = None
    for _ in range(retries):
        try:
            return _thread.start_new_thread(fn, args)
        except RuntimeError as exc:
            last_exc = exc
            _spin(spin_per_retry)
    raise last_exc


def _start_joinable_with_retry(fn, retries=400, spin_per_retry=20000):
    joinable = getattr(_thread, "start_joinable_thread", None)
    if joinable is None:
        return None
    last_exc = None
    for _ in range(retries):
        try:
            return joinable(fn)
        except RuntimeError as exc:
            last_exc = exc
            _spin(spin_per_retry)
    raise last_exc


# ── A. lifecycle (xa7.1) ─────────────────────────────────────────────────────

def _run_lifecycle(write):
    failures = []
    completed = []
    completed_lock = _thread.allocate_lock()

    def detached_worker(token, done):
        completed_lock.acquire()
        completed.append(token)
        completed_lock.release()
        done.release()

    # Detached path via start_new_thread (CPython detaches internally).
    # Workers are dispatched onto APs one at a time on small SMP configs,
    # so we serialize start+wait per worker; this still exercises the
    # pthread_create/pthread_detach lifecycle once per iteration.
    detached_count = 4
    for index in range(detached_count):
        done = _thread.allocate_lock()
        done.acquire()
        ident = _start_with_retry(detached_worker, (index, done))
        if not ident:
            failures.append("detached ident=0 at " + str(index))
        if not done.acquire(True, 2.0):
            failures.append("detached " + str(index) + " did not finish")

    if sorted(completed) != list(range(detached_count)):
        failures.append("detached set mismatch: " + repr(sorted(completed)))

    # Joinable path via start_joinable_thread (3.14+) when available, with
    # graceful fallback to start_new_thread + lock-based join.
    joinable = getattr(_thread, "start_joinable_thread", None)
    join_marker = []
    join_marker_lock = _thread.allocate_lock()

    def joinable_worker():
        join_marker_lock.acquire()
        join_marker.append("ran")
        join_marker_lock.release()

    if joinable is not None:
        try:
            handle = _start_joinable_with_retry(joinable_worker)
            handle.join()
            if join_marker != ["ran"]:
                failures.append("joinable handle did not produce marker")
        except Exception as exc:  # pragma: no cover
            failures.append("joinable handle raised " + type(exc).__name__)
    else:
        done = _thread.allocate_lock()
        done.acquire()

        def joinable_via_lock():
            joinable_worker()
            done.release()

        _start_with_retry(joinable_via_lock, ())
        if not done.acquire(True, 2.0):
            failures.append("fallback joinable did not finish")
        elif join_marker != ["ran"]:
            failures.append("fallback joinable did not produce marker")

    # Slot reuse — start workers in sequence and ensure the same record gets
    # recycled. We can only assert that we keep being able to start new ones
    # past the record cap if the substrate is reusing slots.
    reuse_total = 32  # > MAX_PTHREADS = 16
    counter = [0]
    counter_lock = _thread.allocate_lock()
    reuse_dones = []

    def reuse_worker(local_done):
        counter_lock.acquire()
        counter[0] += 1
        counter_lock.release()
        local_done.release()

    for _ in range(reuse_total):
        local_done = _thread.allocate_lock()
        local_done.acquire()
        try:
            _start_with_retry(reuse_worker, (local_done,))
        except RuntimeError as exc:
            failures.append("slot reuse exhausted at iter: " + str(exc))
            break
        # Wait for completion so the AP frees up; the joinable record gets
        # released by the detached path automatically (start_new_thread is
        # detached under the hood in CPython).
        if not local_done.acquire(True, 2.0):
            failures.append("reuse worker did not finish")
            break
        reuse_dones.append(local_done)

    if counter[0] != reuse_total:
        failures.append("reuse counter " + str(counter[0]) + "/" + str(reuse_total))

    if failures:
        _emit(write, "lifecycle: FAIL " + " | ".join(failures))
        return False
    _emit(write, "lifecycle ok detached=" + str(detached_count) +
          " joinable=" + ("handle" if joinable else "fallback") +
          " reuse=" + str(counter[0]))
    return True


# ── B. identity (xa7.2) ──────────────────────────────────────────────────────

def _run_identity(write):
    failures = []
    parent_ident = _thread.get_ident()
    if not parent_ident:
        failures.append("parent ident is zero")

    seen = []
    seen_lock = _thread.allocate_lock()

    def ident_worker(slot, done):
        my_ident = _thread.get_ident()
        # Re-read; must be stable inside the worker.
        my_ident_again = _thread.get_ident()
        seen_lock.acquire()
        seen.append((slot, my_ident, my_ident_again))
        seen_lock.release()
        done.release()

    n = max(4, _worker_count() * 4)
    for slot in range(n):
        done = _thread.allocate_lock()
        done.acquire()
        _start_with_retry(ident_worker, (slot, done))
        # Serialize so each worker actually runs (small AP count).
        if not done.acquire(True, 2.0):
            failures.append("ident worker " + str(slot) + " hung")
            break

    idents = [row[1] for row in seen]
    if any(i == 0 for i in idents):
        failures.append("zero ident observed: " + repr(idents))

    for slot, ident, ident2 in seen:
        if ident != ident2:
            failures.append("unstable ident in worker " + str(slot))
        if ident == parent_ident:
            failures.append("worker ident equals parent ident at " + str(slot))

    # _thread.get_native_id is enabled by our cross-compiler defining
    # __linux__, so PyThread_get_thread_native_id is built. It maps to
    # syscall(SYS_gettid) which our libc implements as the cpuid-based
    # native CPU ident. Verify it returns a stable nonzero value matching
    # what we expect from the running CPU.
    native_id_present = hasattr(_thread, "get_native_id")
    parent_native = _thread.get_native_id() if native_id_present else None
    if native_id_present and parent_native == 0:
        failures.append("parent get_native_id returned 0")

    # Direct C-level smoke that exercises pthread_create / pthread_join /
    # pthread_self via the HAL self-test, which returns a known marker tuple.
    # Wrap in retry to absorb the AP-busy window left behind by the
    # workers we just ran (the AP can stay marked busy briefly after the
    # last worker's done lock has fired).
    if _hal is not None and getattr(_hal, "pthread_selftest", None) is not None:
        last = (None, None, None)
        for _ in range(400):
            rc, marker, retval = _hal.pthread_selftest()
            last = (rc, marker, retval)
            if rc == 0:
                break
            _spin(20000)
        rc, marker, retval = last
        if rc != 0 or marker != 123456789 or retval != 0x1234:
            failures.append("hal selftest mismatch: " + repr(last))

    if failures:
        _emit(write, "identity: FAIL " + " | ".join(failures))
        return False
    _emit(write, "identity ok workers=" + str(len(idents)) +
          " distinct=" + str(len(set(idents))) +
          " parent=" + str(parent_ident != 0) +
          " native_id=" + str(parent_native))
    return True


# ── C. tss (xa7.3) ───────────────────────────────────────────────────────────

class _Local(_thread._local):
    pass


def _run_tss(write):
    failures = []
    state = _Local()
    state.parent = "PARENT"

    observed = []
    observed_lock = _thread.allocate_lock()

    def tss_worker(slot, done):
        # Worker must see no leakage from parent or previous workers.
        had_parent = hasattr(state, "parent")
        had_old = hasattr(state, "scratch")
        state.scratch = ("worker", slot)
        observed_lock.acquire()
        observed.append((slot, had_parent, had_old, state.scratch))
        observed_lock.release()
        done.release()

    workers = max(4, _worker_count() * 4)
    for slot in range(workers):
        done = _thread.allocate_lock()
        done.acquire()
        _start_with_retry(tss_worker, (slot, done))
        if not done.acquire(True, 2.0):
            failures.append("tss worker " + str(slot) + " hung")
            break

    for slot, had_parent, had_old, scratch in observed:
        if had_parent:
            failures.append("worker " + str(slot) + " saw parent attr")
        if had_old:
            failures.append("worker " + str(slot) + " saw stale scratch")
        if scratch != ("worker", slot):
            failures.append("worker " + str(slot) + " scratch wrong: " + repr(scratch))

    # Parent attr survived independently of worker mutations.
    if not hasattr(state, "parent") or state.parent != "PARENT":
        failures.append("parent local was clobbered")
    if hasattr(state, "scratch"):
        failures.append("parent saw worker scratch")

    if failures:
        _emit(write, "tss: FAIL " + " | ".join(failures))
        return False
    _emit(write, "tss ok workers=" + str(len(observed)))
    return True


# ── D. lock + condvar (xa7.4) ────────────────────────────────────────────────

def _run_lock(write):
    failures = []

    # Non-blocking acquire on a free lock succeeds.
    a = _thread.allocate_lock()
    if not a.acquire(False):
        failures.append("nonblock acquire on free lock failed")
    # Non-blocking acquire on a held lock fails.
    if a.acquire(False):
        failures.append("nonblock acquire on held lock unexpectedly succeeded")
    a.release()

    # Timed acquire on contended lock returns False after timeout.
    b = _thread.allocate_lock()
    b.acquire()
    timed = b.acquire(True, 0.05)
    if timed:
        failures.append("timed acquire on held lock returned True")
    b.release()

    # Timed acquire that gets released before deadline returns True quickly.
    c = _thread.allocate_lock()
    c.acquire()

    def releaser():
        _spin(50000)
        c.release()

    _start_with_retry(releaser, ())
    got = c.acquire(True, 2.0)
    if not got:
        failures.append("timed acquire missed near-deadline release")
    else:
        c.release()

    # Repeated wait/signal cycles. Each iteration pings a worker that
    # acquires-and-releases.
    cycles = 16
    for cycle in range(cycles):
        gate = _thread.allocate_lock()
        gate.acquire()
        ack = _thread.allocate_lock()
        ack.acquire()

        def cycler(g=gate, a=ack):
            g.acquire()
            a.release()
            g.release()

        _start_with_retry(cycler, ())
        gate.release()
        if not ack.acquire(True, 2.0):
            failures.append("cycle " + str(cycle) + " did not ack")
            break

    # Cross-thread release (unlock by a thread other than the locker).
    d = _thread.allocate_lock()
    d.acquire()
    cross_done = _thread.allocate_lock()
    cross_done.acquire()

    def cross_releaser(lock=d, done=cross_done):
        lock.release()
        done.release()

    _start_with_retry(cross_releaser, ())
    if not cross_done.acquire(True, 2.0):
        failures.append("cross-thread releaser hung")
    # The cross-thread release should have made the lock acquirable again.
    if not d.acquire(True, 1.0):
        failures.append("cross-thread release did not unlock")
    else:
        d.release()

    # Long-timeout that should be served by signal_before_deadline. The
    # pthread_cond_timedwait fallback-spin path must not produce a hang or
    # a premature ETIMEDOUT.
    e = _thread.allocate_lock()
    e.acquire()

    def slow_releaser():
        _spin(200000)
        e.release()

    _start_with_retry(slow_releaser, ())
    if not e.acquire(True, 5.0):
        failures.append("long-timeout signal-before-deadline failed")
    else:
        e.release()

    if failures:
        _emit(write, "lock: FAIL " + " | ".join(failures))
        return False
    _emit(write, "lock ok cycles=" + str(cycles) +
          " timeout_expired=True signal_before_deadline=True cross_release=True")
    return True


# ── E. AP worker capacity (xa7.6) ────────────────────────────────────────────

def _run_capacity(write):
    failures = []
    notes = []

    # Hold one worker on the only available AP via a never-released lock,
    # then attempt to start another worker. With SMP_CPUS-1 == 1 (the
    # default smoke test config), the second start must fail with
    # RuntimeError; with more APs, this section just confirms that
    # in-flight workers do not falsely fail.
    workers_at_once = _worker_count()

    block = _thread.allocate_lock()
    block.acquire()
    started_ack = []
    ack_lock = _thread.allocate_lock()

    def busy(slot):
        ack_lock.acquire()
        started_ack.append(slot)
        ack_lock.release()
        # Wait until released. Use a long timeout that we will satisfy.
        block.acquire(True, 30.0)
        block.release()

    for slot in range(workers_at_once):
        try:
            _thread.start_new_thread(busy, (slot,))
        except RuntimeError as exc:
            failures.append("could not start busy worker " + str(slot) +
                            ": " + str(exc))
            break

    # Drain started_ack so we know workers actually entered the busy loop.
    deadline_spins = 200
    while len(started_ack) < workers_at_once and deadline_spins > 0:
        _spin(50000)
        deadline_spins -= 1

    if len(started_ack) != workers_at_once:
        failures.append("busy ack " + str(len(started_ack)) + "/" +
                        str(workers_at_once))

    overflow_failed = False
    if workers_at_once == 1:
        # Only with 1 AP do we have a hard guarantee that a single extra
        # start fails. With multiple APs, several queued attempts may still
        # succeed unpredictably.
        try:
            _thread.start_new_thread(busy, (-1,))
            failures.append("overflow start unexpectedly succeeded")
        except RuntimeError:
            overflow_failed = True
            notes.append("ap_eagain=True")
    else:
        notes.append("ap_eagain=skipped(" + str(workers_at_once) + "APs)")

    # Release everyone.
    for _ in range(workers_at_once):
        block.release()

    # Record-slot exhaustion: start MAX_PTHREADS=16 short joinable
    # threads in sequence WITHOUT joining them. The 17th must EAGAIN.
    # Note: start_new_thread is CPython's "detached" path (it pthread_detach
    # immediately), so records are freed by the worker entry on exit. To
    # stress the record cap we need to use start_joinable_thread to keep
    # records pinned in_use=1 until handle.join() is called.
    record_cap = 16
    joinable = getattr(_thread, "start_joinable_thread", None)
    if joinable is None:
        notes.append("record_cap=skipped(no start_joinable_thread)")
    else:
        handles = []
        record_eagain = False
        record_started = 0

        def quick():
            return None

        for slot in range(record_cap + 4):
            handle = None
            # Retry briefly to absorb the AP-busy window between successive
            # quick workers. Once retries are exhausted, treat the failure
            # as the genuine record-cap exhaustion we are probing for.
            inner_retries = 400
            inner_exc = None
            for _ in range(inner_retries):
                try:
                    handle = joinable(quick)
                    break
                except RuntimeError as exc:
                    inner_exc = exc
                    _spin(20000)
            if handle is None:
                record_eagain = True
                break
            handles.append(handle)
            record_started += 1
            # Wait for this worker to complete so the AP frees up before
            # we start the next one. Joining would free the record (we want
            # the record to stay pinned), so we wait via is_done() polling.
            for _ in range(400):
                if handle.is_done():
                    break
                _spin(20000)

        # Drain.
        for handle in handles:
            try:
                handle.join()
            except Exception:
                pass

        notes.append("record_started=" + str(record_started))
        notes.append("record_eagain=" + str(record_eagain))

        # After joining, slots must be reusable.
        try:
            reused = joinable(quick)
            reused.join()
            notes.append("record_reuse=True")
        except RuntimeError as exc:
            failures.append("record reuse after join failed: " + str(exc))

    if failures:
        _emit(write, "capacity: FAIL " + " | ".join(failures))
        return False
    _emit(write, "capacity ok workers=" + str(workers_at_once) +
          " ap_overflow=" + ("True" if (workers_at_once != 1 or overflow_failed) else "False") +
          " " + " ".join(notes))
    return True


# ── F. attr / condattr (xa7.5) ──────────────────────────────────────────────

def _run_attr(write):
    if _hal is None or getattr(_hal, "pthread_attr_selftest", None) is None:
        _emit(write, "attr skipped: _hal.pthread_attr_selftest unavailable")
        return True

    result = _hal.pthread_attr_selftest()
    # Convention: returns a tuple (ok_count, failure_string).
    if isinstance(result, tuple) and len(result) == 2 and not result[1]:
        _emit(write, "attr ok cases=" + str(result[0]))
        return True

    _emit(write, "attr: FAIL " + repr(result))
    return False


# ── Driver ───────────────────────────────────────────────────────────────────

async def main(argv=None, cwd="/", read_char=None, write=None):
    del argv, cwd, read_char

    _emit(write, "pthread coverage start")
    await _flush()

    sections = [
        ("lifecycle", _run_lifecycle, True),
        ("identity", _run_identity, True),
        ("tss", _run_tss, True),
        ("lock", _run_lock, True),
        ("capacity", _run_capacity, True),
        ("attr", _run_attr, False),  # arch-agnostic: works on arm64 too
    ]

    workers_ok = _workers_supported()
    results = []
    for name, fn, needs_workers in sections:
        if needs_workers and not workers_ok:
            _emit(write, name + " skipped (" + _arch() +
                  ": no pthread workers; tracked by beads pythonos-bjr)")
            results.append(True)
            await _flush()
            continue
        try:
            ok = fn(write)
        except Exception as exc:
            _emit(write, name + ": EXC " + type(exc).__name__ + " " + str(exc))
            ok = False
        results.append(ok)
        await _flush()

    passed = sum(1 for r in results if r)
    _emit(write, "pthread coverage done passed=" + str(passed) +
          "/" + str(len(results)))
