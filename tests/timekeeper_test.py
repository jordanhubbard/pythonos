#!/usr/bin/env python3
"""Host tests for the session clock layered over monotonic uptime."""

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
kernel = types.ModuleType("kernel")
kernel.__path__ = [os.path.join(ROOT, "kernel")]
sys.modules["kernel"] = kernel

from kernel import timekeeper


def main() -> int:
    now = [100]
    timekeeper._uptime_seconds = lambda: now[0]
    timekeeper.clear()
    assert timekeeper.format_hms() == "00:01:40"
    timekeeper.set_hms(23, 59, 58)
    now[0] += 3
    assert timekeeper.format_hms() == "00:00:01"
    try:
        timekeeper.set_hms(24, 0)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid hour accepted")
    timekeeper.clear()
    assert not timekeeper.is_set()
    print("timekeeper_test\n  PASS  session time, rollover, validation, clear")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
