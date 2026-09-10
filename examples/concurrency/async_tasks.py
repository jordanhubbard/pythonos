"""Run two cooperative asyncio tasks through a bounded queue."""

import asyncio


def _line(write, text=""):
    if write:
        write(text + "\n")
    else:
        print(text)


async def _producer(queue, events):
    for item in (1, 2, 3, 4):
        await queue.put(item)
        events.append("put " + str(item))
        await asyncio.sleep(0)
    await queue.put(None)
    events.append("put stop")
    return 4


async def _consumer(queue, events):
    total = 0
    while True:
        item = await queue.get()
        if item is None:
            events.append("got stop")
            break
        total += item
        events.append("got " + str(item))
        await asyncio.sleep(0)
    return total


async def main(argv=None, cwd="/", read_char=None, write=None):
    queue = asyncio.Queue(maxsize=2)
    events = []
    sent, total = await asyncio.gather(
        _producer(queue, events),
        _consumer(queue, events),
    )

    _line(write, "async queue demo")
    _line(write, "events: " + ", ".join(events))
    _line(write, "producer sent: " + str(sent))
    _line(write, "consumer total: " + str(total))
