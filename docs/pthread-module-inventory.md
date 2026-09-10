# CPython modules and pthread/PyThread dependency inventory

Tracks: beads `pythonos-xa7.7`.

This file inventories every CPython module compiled into the PythonOS image
(per `deps/Modules.Setup.local`) and classifies how each one interacts with
the pthread / PyThread surface. The goal is to know which modules sit on the
no-GIL critical path and which are inert.

The classification has three levels:

- **Critical**: the module calls `PyThread_*`, `pthread_*`, or
  `Py_tss_*` directly, or it is the immediate consumer of those calls
  (e.g. `_thread`). Any regression in the substrate is visible here.
- **Indirect**: the module does not call thread APIs itself, but uses
  CPython runtime services (allocator, GIL, type cache) that internally
  rely on `PyThread_type_lock`. Correctness is still gated on the substrate.
- **Inert**: pure-data / pure-compute module with no thread interaction
  beyond the universal indirect dependency on the runtime allocator.

The `Modules.Setup.local` listing as of this beads epic is the source of
truth; rerun the audit (`grep -rn "PyThread_\|pthread_" deps/cpython-src/Modules/<name>*`)
when changing the module list.

## Critical-path modules

| Module      | Source                                | Notes |
|-------------|----------------------------------------|-------|
| `_thread`   | `Modules/_threadmodule.c`              | Direct consumer of `PyThread_start_joinable_thread`, `PyThread_join_thread`, `PyThread_detach_thread`, `PyThread_get_thread_ident_ex`, `PyThread_allocate_lock` (mutex+condvar emulation), and `Py_tss_*`. **This module is the entire reason the substrate exists.** Covered by `examples/concurrency/thread_demo.py` and `examples/internals/pthread_coverage.py`. |
| `_asyncio`  | `Modules/_asynciomodule.c`             | Allocates per-loop `PyMutex` (lightweight CPython mutex, *not* `PyThread_type_lock`) and reads `PyThread_get_thread_ident_ex` to enforce the loop's owning-thread invariant. Critical because `asyncio.run_coroutine_threadsafe` from a `_thread` worker must see a stable ident. |
| `_hal`      | `src/hal/hal.c`                        | PythonOS-local module. Calls `pthread_create` / `pthread_join` directly via `pthread_selftest()` for boot-time validation, and exports `SMP_*` constants. Already exercised by `tests/smoke_test.py` (`__import__('_hal').pthread_selftest()`). |

## Indirect-dependency modules

These modules do not call pthread APIs themselves but rely on the CPython
runtime, which uses `PyThread_type_lock` for its critical sections (interned
strings, type cache, frozen module state, etc.). They are listed for
completeness so a future no-GIL regression can be triaged faster.

| Module           | Source                              |
|------------------|--------------------------------------|
| `_collections`   | `Modules/_collectionsmodule.c`       |
| `_functools`     | `Modules/_functoolsmodule.c`         |
| `_abc`           | `Modules/_abc.c`                     |
| `_weakref`       | `Modules/_weakref.c`                 |
| `_operator`      | `Modules/_operator.c`                |
| `_stat`          | `Modules/_stat.c`                    |
| `_signal`        | `Modules/signalmodule.c`             |
| `_io`            | `Modules/_io/*.c`                    |
| `itertools`      | `Modules/itertoolsmodule.c`          |
| `time`           | `Modules/timemodule.c`               |
| `math`           | `Modules/mathmodule.c`               |
| `_struct`        | `Modules/_struct.c`                  |
| `binascii`       | `Modules/binascii.c`                 |
| `_csv`           | `Modules/_csv.c`                     |
| `_json`          | `Modules/_json.c`                    |
| `_random`        | `Modules/_randommodule.c`            |
| `_bisect`        | `Modules/_bisectmodule.c`            |
| `_heapq`         | `Modules/_heapqmodule.c`             |
| `_datetime`      | `Modules/_datetimemodule.c`          |
| `_pickle`        | `Modules/_pickle.c`                  |

## Inert-with-respect-to-pthread modules

The codec table modules below are read-only data with conversion routines.
They have no thread state; once loaded they are invariant under all
substrate behavior.

| Module           | Source                                  |
|------------------|------------------------------------------|
| `_codecs`        | `Modules/_codecsmodule.c`                |
| `_codecs_cn`     | `Modules/cjkcodecs/_codecs_cn.c`         |
| `_codecs_hk`     | `Modules/cjkcodecs/_codecs_hk.c`         |
| `_codecs_iso2022`| `Modules/cjkcodecs/_codecs_iso2022.c`    |
| `_codecs_jp`     | `Modules/cjkcodecs/_codecs_jp.c`         |
| `_codecs_kr`     | `Modules/cjkcodecs/_codecs_kr.c`         |
| `_codecs_tw`     | `Modules/cjkcodecs/_codecs_tw.c`         |
| `_multibytecodec`| `Modules/cjkcodecs/multibytecodec.c`     |
| `unicodedata`    | `Modules/unicodedata.c`                  |

## Excluded modules (not built)

The modules below are listed in `deps/Modules.Setup.local` under
`*disabled*` or `EXCLUDE` and are **not** part of the image. Most of them
would, if built, depend on POSIX features outside of our pthread substrate
(sockets, fcntl, signal handlers with `pthread_kill`, etc.). They are
recorded here so the audit is complete:

- `_blake2`, `_hashlib`, `_ssl` — depend on OpenSSL / HACL.
- `_md5`, `_sha1`, `_sha2`, `_sha3`, `_hmac` — depend on HACL.
- `_decimal` — depends on libmpdec hidden-visibility symbols.
- `_posixsubprocess`, `select`, `socket`, `_socket`, `fcntl`, `termios`,
  `readline`, `grp`, `pwd`, `resource`, `syslog`, `nis` — POSIX-only,
  not relevant to pthread.
- `_ctypes`, `_tkinter` — not relevant to pthread.

If any of the disabled modules are later enabled, this inventory must be
re-run for that module before the build is merged.

## Follow-ups

No critical-path module currently depends on any pthread feature listed in
`docs/pthread-non-goals.md`. If that changes — for example, if `socket` is
re-enabled and brings `pthread_kill` along the GIL signal path — file a
beads issue under the active pthread epic before merging the module change.
