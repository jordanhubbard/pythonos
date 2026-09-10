# pthread attr / condattr coverage

Tracks: beads `pythonos-xa7.5`.

This file documents which pthread attribute and condition-variable attribute
helpers CPython actually invokes against the PythonOS substrate, what
configuration macros gate each path, and which functions are present only as
source-compatibility no-ops. The intent is to make the "stub vs. live"
distinction explicit so future work doesn't accidentally promote a no-op
into a load-bearing API without re-validating it.

## Source of truth

The CPython call sites live in `deps/cpython-src/Python/thread_pthread.h`.
The configuration macros live in `deps/cpython/pyconfig.h` (x86_64) and
`deps/pyconfig_arm64.h` (arm64). Re-run the matrix below if any of those
files change.

## Macros currently set in pyconfig

These are the only thread-related feature macros our pyconfig defines:

```
#define _POSIX_THREADS 1
#define HAVE_PTHREAD_H 1
#define SIZEOF_PTHREAD_T 8
#define HAVE_CLOCK_GETTIME 1
#define HAVE_NANOSLEEP 1
```

Macros pertinent to pthread attr / condattr that are **not** defined:

```
_POSIX_THREAD_ATTR_STACKSIZE      → THREAD_STACK_SIZE path is dead
_POSIX_SEMAPHORES                 → USE_SEMAPHORES is dead
HAVE_SEM_TIMEDWAIT
HAVE_SEM_CLOCKWAIT
HAVE_PTHREAD_CONDATTR_SETCLOCK    → CONDATTR_MONOTONIC path is dead
PTHREAD_SYSTEM_SCHED_SUPPORTED    → pthread_attr_setscope path is dead
HAVE_PTHREAD_SIGMASK              → pthread_sigmask path is dead
HAVE_PTHREAD_GETNAME_NP
HAVE_PTHREAD_SETNAME_NP
PY_HAVE_THREAD_NATIVE_ID          → _thread.get_native_id() not built
```

The intersection of the active set with our pthread surface is what the
matrix below calls "live"; everything else is a stub.

## Attribute coverage matrix

| pthread call                        | Gated by                       | Status in our build | Test coverage                      |
|-------------------------------------|--------------------------------|---------------------|------------------------------------|
| `pthread_attr_init`                 | `THREAD_STACK_SIZE` ∪ `PTHREAD_SYSTEM_SCHED_SUPPORTED` | **stub (never reached by CPython)** — `pthread_create` is invoked with `NULL` attrs | `examples/internals/pthread_coverage.py: attr_self_test` — direct call only |
| `pthread_attr_destroy`              | same                           | **stub**            | same                               |
| `pthread_attr_setstacksize`         | `THREAD_STACK_SIZE` defined    | **stub**            | direct call asserts EINVAL on stack < 32 KiB |
| `pthread_attr_getstacksize`         | not used by CPython            | **stub** (round-trip works) | direct call round-trips a value |
| `pthread_attr_setdetachstate`       | not used by `do_start_joinable_thread` | **live for in-tree direct callers** (e.g. `pthread_create` honors `PTHREAD_CREATE_DETACHED`) | `examples/internals/pthread_coverage.py: lifecycle_detached` |
| `pthread_attr_setscope`             | `PTHREAD_SYSTEM_SCHED_SUPPORTED` | **not declared** — symbol absent. CPython would fail link if the macro were defined, which it isn't. | n/a |

Notes:

- CPython 3.13/3.14 on POSIX always passes attrs only when at least one of
  `THREAD_STACK_SIZE` or `PTHREAD_SYSTEM_SCHED_SUPPORTED` is defined; see
  `deps/cpython-src/Python/thread_pthread.h:289-294`. Our pyconfig defines
  neither, so CPython hits the `(pthread_attr_t *)NULL` arm.
- `pthread_attr_setdetachstate` is still load-bearing for **direct**
  in-tree callers that build an attr by hand. `examples/internals/pthread_coverage.py`
  exercises this path because the lifetime semantics it gates (detached
  worker reaps its own slot) is on the no-GIL critical path.
- `pthread_attr_setstacksize` rejects sizes below 32 KiB with `EINVAL`
  (`src/libc/pthread.c:321-327`). The substrate ignores the stored value —
  AP workers run on a fixed BSS stack — so success only means the value
  round-trips through the attr struct.
- `pthread_attr_getstacksize` returns 65536 when no value was set (the
  default planted by `pthread_attr_init`).

## Condvar attribute coverage matrix

| pthread call                        | Gated by                            | Status in our build  | Test coverage                |
|-------------------------------------|--------------------------------------|----------------------|------------------------------|
| `pthread_condattr_init`             | `CONDATTR_MONOTONIC`                | **stub (never reached by CPython)** — `init_condattr()` only runs the call when `CONDATTR_MONOTONIC` is defined, which requires `HAVE_PTHREAD_CONDATTR_SETCLOCK` | `examples/internals/pthread_coverage.py: attr_self_test` — direct call only |
| `pthread_condattr_destroy`          | same                                | **stub**             | same                         |
| `pthread_condattr_setclock`         | `CONDATTR_MONOTONIC`                | **stub** (silently accepts any clock; returns 0) | same                         |

`pthread_cond_init` always receives `NULL` from CPython in our build, so
the underlying clock for `pthread_cond_timedwait` is `CLOCK_REALTIME` (the
PIT-backed `_pit_ticks` counter via `clock_gettime`). The substrate's
condvar wait also includes a fallback spin counter to avoid hangs when the
PIT clock fails to advance during a tight wait
(`TIMEDWAIT_*_FALLBACK_SPINS` in `src/libc/pthread.c`).

## Failure-mode policy

The acceptance criterion for `pythonos-xa7.5` requires that "unsupported
attr features fail cleanly instead of silently claiming unsupported POSIX
semantics." The substrate enforces this as follows:

- Attr setters validate inputs and return `EINVAL` for bogus values
  (`pthread_attr_setdetachstate` on values that aren't
  `PTHREAD_CREATE_JOINABLE` or `PTHREAD_CREATE_DETACHED`;
  `pthread_attr_setstacksize` on sizes < 32 KiB; `pthread_attr_init` on
  NULL; `pthread_attr_getstacksize` on NULL out pointer).
- Condattr functions accept any input. They are no-ops because **no
  configuration macro currently routes CPython through them**, so a
  silent pass cannot hide a real bug. If a future pyconfig change defines
  `HAVE_PTHREAD_CONDATTR_SETCLOCK`, this section must be revisited and
  `pthread_condattr_setclock` must be promoted to a real implementation
  (or fail with `EINVAL` for clocks other than `CLOCK_REALTIME`).
- `pthread_attr_setscope` and other declined-features APIs are simply
  **absent from `src/libc/include/pthread.h`**. A caller that needs them
  will fail to compile, which is the strongest "fail cleanly" we can give.

## What gets tested where

- `examples/internals/pthread_coverage.py: attr_self_test` exercises the live
  failure modes (NULL inputs, undersized stack, bogus detach state) and
  confirms that the round-trip getters return the values we set. It also
  covers the no-op `pthread_condattr_*` happy path so that a future change
  cannot regress us into a hard failure for source-compatibility callers.
- `tests/smoke_test.py` checks that the example output contains the
  expected `attr ok` / `condattr ok` markers so CI gates regress on
  failure.

If the matrix above changes, both this file and `pthread_coverage.py`
must be updated in the same change; CI does not validate the matrix.
