Storage and the VFS
===================

vfs_demo.py opens a file through PythonOS's virtual filesystem, writes bytes,
reads them back, inspects metadata, and lists a directory.

  run('/examples/storage/vfs_demo.py')
  sh('/examples/storage/vfs_demo.py /tmp/my-vfs-demo.txt')

Look for OpenFlags, try/finally around file descriptors, absolute-path
normalization, and the difference between bytes on disk and decoded text.

Next: /examples/concurrency/README.txt
