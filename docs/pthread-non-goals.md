# PythonOS pthread non-goals

Tracks: beads `pythonos-xa7.8`.

The PythonOS pthread substrate (`src/libc/pthread.c`) exists to satisfy the
no-GIL CPython critical path on bare metal. It is **not** a general-purpose
POSIX threading implementation. The list below records what is explicitly
**out of scope** so future work does not expand the surface without a concrete
in-tree dependency.

## Out of scope

The following pthread features are intentionally not implemented, and code
that would require them must either be excluded from the build or raise a
follow-up beads issue with a concrete justification before any work begins.

### Scheduling and priority

- `pthread_attr_setschedpolicy`, `pthread_attr_setschedparam`,
  `pthread_attr_setinheritsched` — no-op or unimplemented.
- `pthread_setschedparam`, `pthread_setschedprio`,
  `pthread_getschedparam` — unimplemented.
- `pthread_attr_setscope` (`PTHREAD_SCOPE_PROCESS` /
  `PTHREAD_SCOPE_SYSTEM`) — unimplemented. CPython only calls this when
  `PTHREAD_SYSTEM_SCHED_SUPPORTED` is defined, which is **not** defined in
  `deps/cpython/pyconfig.h`.
- Real-time priorities, fair-share scheduling, CPU affinity APIs
  (`pthread_setaffinity_np`, `sched_setaffinity`) — unimplemented.

### Signals and cancellation

- `pthread_kill`, `pthread_sigmask` — unimplemented. CPython probes
  `HAVE_PTHREAD_SIGMASK`; we do not define it, so the GIL build avoids these
  paths and the no-GIL build does not require them.
- `pthread_cancel`, `pthread_setcancelstate`, `pthread_setcanceltype`,
  `pthread_testcancel` — present as no-op stubs only. Cancellation is
  inherently incompatible with bare-metal interrupt-driven code; we do not
  honor cancel points, and `pthread_cancel` always returns 0 without
  cancelling.

### Robust mutexes and advanced locks

- Robust mutexes (`PTHREAD_MUTEX_ROBUST`, `pthread_mutex_consistent`) —
  unimplemented. We do not detect or recover from "owner died holding the
  lock" because no separate process can die.
- Recursive and error-checking mutex types are accepted by
  `pthread_mutexattr_settype` for source compatibility, but the underlying
  mutex is always normal. Recursive lock acquisition by the same thread will
  spin forever. The CPython `PyThread_type_lock` mutex+condvar emulation
  never recurses, so this is acceptable on the critical path.
- Priority-inheritance / priority-protection
  (`pthread_mutexattr_setprotocol`) — unimplemented.

### rwlocks and barriers

- Reader/writer locks (`pthread_rwlock_*`) are present but implemented as a
  single binary spin lock — readers and writers are mutually exclusive. They
  exist only to satisfy header references; no in-tree caller relies on
  reader concurrency. If a future caller needs real shared/exclusive
  semantics, file a new beads issue rather than promoting the stub.
- Barriers (`pthread_barrier_*`) — not declared and not implemented.
- Spinlocks (`pthread_spin_*`) — not declared and not implemented; in-tree
  code uses our `spinlock_t` from `src/libc/include/spinlock.h` directly.

### Per-thread CPU clocks and timing

- `pthread_getcpuclockid`, per-thread CPU time clocks
  (`CLOCK_THREAD_CPUTIME_ID`) — unimplemented.
- `pthread_condattr_setclock(CLOCK_MONOTONIC)` is accepted as a no-op for
  source compatibility, but `HAVE_PTHREAD_CONDATTR_SETCLOCK` is **not**
  defined in `pyconfig.h`, so CPython never selects a non-default clock for
  condvars and our condvar wait uses `clock_gettime(CLOCK_REALTIME, …)`
  via the PIT-backed clock.

### Arbitrary native blocking

- Arbitrary blocking system calls inside a pthread worker are not supported.
  Workers run on AP CPUs with no kernel-side blocking primitives; only the
  spin-based mutex / condvar / lock-acquire-with-timeout paths are correct.
  Code that calls into kernel I/O paths (VFS, network) from a CPython thread
  must rely on the asyncio event loop, not pthread blocking semantics.
- Forking (`pthread_atfork`) — unimplemented. PythonOS does not fork.

### Process-shared synchronization

- `PTHREAD_PROCESS_SHARED` mutexes / condvars / rwlocks — unimplemented.
  PythonOS has a single address space.

### Thread names, keys beyond the cap

- `pthread_setname_np` / `pthread_getname_np` — unimplemented. CPython only
  uses these when `HAVE_PTHREAD_GETNAME_NP` / `HAVE_PTHREAD_SETNAME_NP` is
  defined, which we do not define.
- `pthread_key_create` is bounded by `MAX_KEYS = 64`. `PTHREAD_KEYS_MAX`
  in our `pthread.h` advertises 128 for source compatibility but the
  substrate caps at 64; exceeding the cap returns `ENOMEM`. Raising the cap
  is a code change, not a runtime configuration.

## Native thread ID (`_thread.get_native_id`)

`PY_HAVE_THREAD_NATIVE_ID` is defined whenever the compiler advertises one
of `__APPLE__`, `__linux__`, `__FreeBSD__`, `__OpenBSD__`, `_AIX`,
`__NetBSD__`, or `__DragonFly__`. The `x86_64-elf-gcc` cross-compiler in
our build environment advertises `__linux__`, so:

- `_thread.get_native_id()` **is** exposed by the build.
- CPython routes through the `__linux__` arm of
  `PyThread_get_thread_native_id`, which calls `syscall(SYS_gettid)`. Our
  libc shim (`src/libc/include/sys/syscall.h` + `src/libc/syscalls.c`)
  implements that path by returning the CPU/thread ident from `cpuid` —
  the same value used by `pthread_self()`.

This is a deliberate piece of the supported surface: callers that want a
stable, nonzero per-worker identity get one. What is **not** in scope is
expanding `syscall()` to other Linux syscall numbers — `SYS_gettid` is the
only number our shim recognizes; everything else returns 1.

## In scope (for context)

For comparison, the supported critical-path surface is documented in
`docs/pthread-attr-coverage.md` and exercised by
`examples/internals/pthread_coverage.py`. In short:

- `pthread_create` / `pthread_join` / `pthread_detach` / `pthread_self` /
  `pthread_equal` / `pthread_exit` (worker entry only).
- `pthread_mutex_*` (normal type only) and `pthread_cond_*` with timed
  wait against `CLOCK_REALTIME`.
- `pthread_key_create` / `pthread_key_delete` /
  `pthread_setspecific` / `pthread_getspecific`, partitioned by native
  thread slot.
- `pthread_once`.

## Re-opening scope

Adding any of the out-of-scope features requires:

1. A new beads issue under epic `pythonos-xa7` (or its successor) naming the
   in-tree caller that depends on the feature.
2. An updated entry in this file moving the feature out of "Out of scope".
3. Test coverage in `examples/internals/pthread_coverage.py` (or a sibling) before
   merge, plus the corresponding assertions in `tests/smoke_test.py`.
