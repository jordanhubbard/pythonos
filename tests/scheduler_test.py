#!/usr/bin/env python3
"""Focused process-lifecycle tests for the cooperative scheduler."""

import asyncio
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
kernel = types.ModuleType("kernel")
kernel.__path__ = [os.path.join(ROOT, "kernel")]
sys.modules["kernel"] = kernel

from kernel.scheduler import ProcessState, Scheduler


_passed = 0
_failed = 0


def check(name, condition):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}")


async def _done():
    await asyncio.sleep(0)
    return 42


async def _drain_callbacks():
    # One turn starts the coroutine, one completes it, and one runs its
    # add_done_callback handlers. Keep an extra turn for loop variations.
    for _ in range(4):
        await asyncio.sleep(0)


async def _exercise():
    retained = Scheduler()
    pid = retained.spawn(_done(), name="retained")
    await _drain_callbacks()
    proc = retained.ps()[0]
    check("normal exit is inspectable as a zombie",
          proc.pid == pid and proc.state == ProcessState.ZOMBIE)
    check("reap returns the completed process", retained.reap(pid) is proc)
    check("reaped process leaves ps", retained.ps() == [])

    automatic = Scheduler()
    automatic.spawn(_done(), name="request", auto_reap=True)
    await _drain_callbacks()
    check("short-lived request is automatically reaped", automatic.ps() == [])

    cancelled = Scheduler()
    cancel_pid = cancelled.spawn(_done(), name="cancelled")
    cancelled.kill(cancel_pid)
    await _drain_callbacks()
    check("cancelled process becomes a zombie without callback failure",
          cancelled.ps()[0].state == ProcessState.ZOMBIE)


def main() -> int:
    print("scheduler_test")
    asyncio.run(_exercise())
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
