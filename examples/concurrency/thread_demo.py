"""Exercise CPython threads on AP-backed pthread workers."""

import _thread
import asyncio

try:
    import _hal
except ImportError:
    _hal = None


def _line(write, text=""):
    if write:
        write(text + "\n")
    else:
        print(text)


async def _flush():
    await asyncio.sleep(0)


async def main(argv=None, cwd="/", read_char=None, write=None):
    del argv, cwd, read_char

    _line(write, "thread demo")
    await _flush()

    values = []
    value_lock = _thread.allocate_lock()
    worker_count = 1
    if _hal is not None:
        worker_count = max(1, min(3, getattr(_hal, "SMP_ONLINE", 2) - 1))

    idents = []
    worker_done = []

    def worker(index, done):
        value_lock.acquire()
        values.append(index)
        value_lock.release()
        done.release()

    for index in range(worker_count):
        done = _thread.allocate_lock()
        done.acquire()
        idents.append(_thread.start_new_thread(worker, (index, done)))
        worker_done.append(done.acquire(True, 1.0))
        _line(write, "worker " + str(index) + " done: " + str(worker_done[-1]))
        await _flush()

    values.sort()

    timeout_lock = _thread.allocate_lock()
    timeout_lock.acquire()
    timeout_expired = not timeout_lock.acquire(True, 0.05)
    timeout_lock.release()
    _line(write, "timeout phase: " + str(timeout_expired))
    await _flush()

    delayed_lock = _thread.allocate_lock()
    delayed_lock.acquire()

    def delayed_release():
        for _ in range(20000):
            pass
        delayed_lock.release()

    delayed_ident = _thread.start_new_thread(delayed_release, ())
    delayed_acquire = delayed_lock.acquire(True, 1.0)
    if delayed_acquire:
        delayed_lock.release()
    _line(write, "delayed phase: " + str(delayed_acquire))
    await _flush()

    _line(write, "worker ident: " + str(all(ident != 0 for ident in idents)))
    _line(write, "worker done: " + str(all(worker_done)))
    _line(write, "delayed ident: " + str(delayed_ident != 0))
    _line(write, "workers: " + str(worker_count))
    _line(write, "values: " + repr(values))
    _line(write, "timeout expired: " + str(timeout_expired))
    _line(write, "delayed acquire: " + str(delayed_acquire))
