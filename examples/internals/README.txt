Contributor internals
=====================

These are executable validation fixtures, not introductory lessons:

  _vfs_test.py          VFS import fixture used by smoke tests
  check_home.py         ext2 /home write-read-delete round trip
  linenoise_demo.py     scripted native line-editor coverage
  pthread_coverage.py   detailed pthread/TLS/lock/attribute coverage

They are kept readable because PythonOS should explain and test itself. Normal
learners can ignore this directory until they want to work on the kernel.
