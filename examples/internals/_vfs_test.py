"""Path: /examples/internals/_vfs_test.py

Tiny contributor fixture for VFS importing. A constant, function, and module
docstring give the smoke test three simple properties to validate without any
external dependency.
"""

GREETING = "hello from /examples/internals/_vfs_test"


def square(n):
    return n * n


def doc():
    return __doc__
