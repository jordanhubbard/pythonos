#!/usr/bin/env python3
"""Process-supervision regression tests for tools/run_gui.py."""

import importlib.util
import pathlib
import subprocess
import sys
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "run_gui", ROOT / "tools" / "run_gui.py")
run_gui = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(run_gui)


def _sleeper() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])


class RunGuiSupervisionTests(unittest.TestCase):
    def test_guest_shutdown_exits_bridge_for_supervisor(self):
        compositor_source = (
            ROOT / "kernel" / "gui" / "compositor.py").read_text()

        self.assertIn('_bridge.call("shutdown", {})', compositor_source)

    def test_bridge_exit_stops_qemu_and_returns_bridge_status(self):
        bridge = subprocess.Popen([sys.executable, "-c", "pass"])
        started = time.monotonic()

        rc = run_gui._launch_qemu(
            [sys.executable, "-c", "import time; time.sleep(30)"], bridge)

        self.assertEqual(rc, 0)
        self.assertLess(time.monotonic() - started, 3)

    def test_qemu_exit_stops_bridge_and_returns_qemu_status(self):
        bridge = _sleeper()
        try:
            rc = run_gui._launch_qemu(
                [sys.executable, "-c", "raise SystemExit(7)"], bridge)

            self.assertEqual(rc, 7)
            self.assertIsNotNone(bridge.poll())
        finally:
            run_gui._stop_proc(bridge)


if __name__ == "__main__":
    unittest.main(verbosity=2)
