#!/usr/bin/env python3
"""Launch the desktop endpoint and its supervised RemoteOS-SDL backend."""

import argparse
import asyncio
import contextlib
import importlib.util
import os
from pathlib import Path

from desktop_service.server import Service
from desktop_service.wire import RemoteOS

ROOT = Path(__file__).resolve().parent.parent


class Stream:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    async def recv(self, size):
        return await self.reader.read(size)

    async def send(self, data):
        self.writer.write(data)
        await self.writer.drain()


async def run_demo(host, port, frames=None):
    spec = importlib.util.spec_from_file_location("message_demo", ROOT / "examples/networking/message_desktop.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    reader, writer = await asyncio.open_connection(host, port)
    try:
        await module.demo(Stream(reader, writer), frames)
    finally:
        writer.close()
        await writer.wait_closed()


async def run(args):
    if args.client:
        await run_demo(args.host, args.port, args.frames)
        return
    accepted = asyncio.get_running_loop().create_future()

    def accept(reader, writer):
        if not accepted.done():
            accepted.set_result((reader, writer))
        else:
            writer.close()

    listener = await asyncio.start_server(accept, "127.0.0.1", 0)
    backend_port = listener.sockets[0].getsockname()[1]
    env = dict(os.environ)
    env["REMOTEOS_SDL_MODE"] = "headless" if args.headless else "interactive"
    process = None
    backend = service = endpoint = None
    tasks = []
    try:
        process = await asyncio.create_subprocess_exec(str(args.backend), "--connect-tcp",
                                                       f"127.0.0.1:{backend_port}", env=env)
        reader, writer = await asyncio.wait_for(accepted, 10)
        backend = RemoteOS(reader, writer)
        listener.close()
        service = Service(backend)
        # Open before advertising readiness to application clients.
        await service.renderer.open()
        endpoint = await asyncio.start_server(service.client, args.host, args.port)
        port = endpoint.sockets[0].getsockname()[1]
        print(f"Message desktop listening on {args.host}:{port}", flush=True)
        tasks.append(asyncio.create_task(service.run(open_display=False)))
        if args.demo:
            tasks.append(asyncio.create_task(run_demo("127.0.0.1" if args.host == "0.0.0.0" else args.host, port, args.frames)))
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        if endpoint:
            endpoint.close()
        if service:
            await service.close()
        if endpoint:
            await endpoint.wait_closed()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if backend:
            backend.writer.close()
            with contextlib.suppress(ConnectionError):
                await backend.writer.wait_closed()
        listener.close()
        await listener.wait_closed()
        if process and process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), 3)
            except TimeoutError:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 3)
                except TimeoutError:
                    process.kill()
                    await process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=17020)
    parser.add_argument("--backend", type=Path, default=ROOT / "services/remoteos-sdl/remoteos-sdl")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--demo", action="store_true", help="also launch the example application")
    parser.add_argument("--client", action="store_true", help="run only the example against an existing desktop")
    parser.add_argument("--frames", type=int, help="stop example after this many iterations")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
