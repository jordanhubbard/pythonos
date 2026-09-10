Networking
==========

The shell's ftp command is the easiest file-transfer interface. These lower-
level examples show the TCP and VFS calls underneath it.

Receive a host file in PythonOS:

  sh('/examples/networking/recv_file.py 7000 /tmp/inbox.bin')
  host: nc localhost 17000 < local-file.txt

Send a PythonOS file to the host:

  host: nc -l 7001 > pythonos-example.txt
  sh('/examples/networking/send_file.py 10.0.2.2 7001 /examples/README.txt')

Notice that recv_file accepts a connection and writes collected bytes, while
send_file connects outward and streams an already-open VFS file.
