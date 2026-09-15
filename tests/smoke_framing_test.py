#!/usr/bin/env python3
"""Regression tests for TCP fragmentation of REPL prompt examples."""
import unittest

from smoke_test import recv_until_prompt
import gui_smoke_test


class FragmentedSocket:
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    def settimeout(self, timeout):
        pass

    def recv(self, size):
        return next(self.chunks, b"")

    def sendall(self, data):
        self.sent = data


class FramingTest(unittest.TestCase):
    def test_prompt_example_and_echo_are_not_completion(self):
        chunks = [
            b"Help example:\r\n>>> ",
            b"sh('examples')\r\n>>> ",
            b"print('__done__')\r\n",
            b"__do", b"ne__\r\n>>", b"> ",
        ]
        sock = FragmentedSocket(chunks)
        self.assertEqual(recv_until_prompt(sock, completion_marker=b"__done__"),
                         b"".join(chunks).decode())

    def test_initial_prompt_without_marker(self):
        self.assertEqual(recv_until_prompt(FragmentedSocket([b"hello\n>", b">> "])),
                         "hello\n>>> ")

    def test_missing_marker_fails_closed(self):
        with self.assertRaises(RuntimeError):
            recv_until_prompt(FragmentedSocket([b"expected output\n>>> "]),
                              completion_marker=b"__done__")

    def test_echo_of_marker_is_not_completion(self):
        with self.assertRaises(RuntimeError):
            recv_until_prompt(FragmentedSocket([b"print('__done__')\r\n>>> "]),
                              completion_marker=b"__done__")

    def test_gui_marker_fragmentation(self):
        gui_smoke_test._SEND_SEQUENCE = 0
        chunks = [b"help\r\n>>> ", b"print('__PYTHONOS_DONE_1__')\r\n",
                  b"__PYTHONOS_", b"DONE_1__\r\n>>> "]
        sock = FragmentedSocket(chunks)
        self.assertEqual(gui_smoke_test._send(sock, "help"), b"".join(chunks).decode())
        self.assertEqual(sock.sent, b"help\nprint('__PYTHONOS_DONE_1__')\n")

    def test_gui_incomplete_response_fails_closed(self):
        with self.assertRaises(RuntimeError):
            gui_smoke_test._send(FragmentedSocket([b"True\r\n>>> "]), "True")


if __name__ == "__main__":
    unittest.main()
