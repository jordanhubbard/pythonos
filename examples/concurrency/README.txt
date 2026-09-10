Concurrency
===========

1. async_tasks.py
   Two cooperative coroutines communicate through a bounded asyncio.Queue.

     run('/examples/concurrency/async_tasks.py')

2. thread_demo.py
   CPython worker threads run on secondary processors and synchronize through
   locks, including timeout behavior.

     run('/examples/concurrency/thread_demo.py')

Compare cooperative scheduling in the first program with parallel workers in
the second. Detailed pthread validation lives under internals/.

Next: /examples/graphics/README.txt
