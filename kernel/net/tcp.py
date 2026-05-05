"""
kernel.net.tcp — Minimal TCP stack.

Implements the state machine, connection management, and stream I/O.
Does not yet implement: congestion control, SACK, timestamps, window scaling.
Think of this as TCP Reno circa 1988 — correct, not fast.

Usage:
    conn = await tcp.connect("93.184.216.34", 80)
    await conn.send(b"GET / HTTP/1.0\r\n\r\n")
    data = await conn.recv(4096)
    conn.close()
"""


import asyncio
import struct
import random
from dataclasses import dataclass, field
from enum import IntEnum

from kernel.net.ip import IPv4Packet, inet_cksum, PROTO_TCP, ip_str, ip_from_str
from kernel.net.ethernet import EtherFrame, ETHERTYPE_IPv4
import kernel.log as log


class TCPState(IntEnum):
    CLOSED      = 0
    LISTEN      = 1
    SYN_SENT    = 2
    SYN_RCVD    = 3
    ESTABLISHED = 4
    FIN_WAIT_1  = 5
    FIN_WAIT_2  = 6
    CLOSE_WAIT  = 7
    CLOSING     = 8
    LAST_ACK    = 9
    TIME_WAIT   = 10

# TCP flag bits
F_FIN = 0x01
F_SYN = 0x02
F_RST = 0x04
F_PSH = 0x08
F_ACK = 0x10
F_URG = 0x20

# No PMTU/MSS negotiation yet. Keep payloads comfortably below Ethernet MTU
# so bridge pixel uploads do not create impossible IPv4 packets.
TCP_MAX_PAYLOAD = 1200
TRACE_SEGMENTS = False


@dataclass
class TCPSegment:
    src_port:  int
    dst_port:  int
    seq:       int
    ack:       int
    flags:     int
    window:    int
    payload:   bytes

    @classmethod
    def decode(cls, raw: bytes, src_ip: bytes, dst_ip: bytes) -> "TCPSegment | None":
        if len(raw) < 20:
            return None
        src_p, dst_p = struct.unpack(">HH", raw[0:4])
        seq, ack     = struct.unpack(">II", raw[4:12])
        off_flags    = struct.unpack(">H", raw[12:14])[0]
        flags        = off_flags & 0x1FF
        offset       = (off_flags >> 12) * 4
        window       = struct.unpack(">H", raw[14:16])[0]
        return cls(src_port=src_p, dst_port=dst_p, seq=seq, ack=ack,
                   flags=flags, window=window, payload=raw[offset:])

    def encode(self, src_ip: bytes, dst_ip: bytes) -> bytes:
        pseudo = src_ip + dst_ip + struct.pack(">BBI", 0, PROTO_TCP, 20 + len(self.payload))
        header = struct.pack(">HHIIHHHHH",
            self.src_port, self.dst_port,
            self.seq, self.ack,
            (5 << 12) | self.flags,  # data offset = 5 (20 bytes), flags
            self.window,
            0,        # checksum placeholder
            0, 0,     # urgent pointer, padding
        )[:20]
        cksum = inet_cksum(pseudo + header + self.payload)
        return header[:16] + struct.pack(">H", cksum) + header[18:] + self.payload


@dataclass
class TCPConnection:
    local_ip:   bytes
    remote_ip:  bytes
    local_port: int
    remote_port: int
    state:      TCPState = TCPState.CLOSED
    snd_seq:    int = field(default_factory=lambda: random.randint(0, 2**32 - 1))
    snd_ack:    int = 0
    snd_wnd:    int = 65535
    rcv_buf:    bytearray = field(default_factory=bytearray)
    _rx_event:     asyncio.Event = field(default_factory=asyncio.Event)
    _accept_event: asyncio.Event = field(default_factory=asyncio.Event)
    _connected:    asyncio.Future | None = None

    async def send_segment(self, flags: int, payload: bytes = b"") -> None:
        seg = TCPSegment(
            src_port=self.local_port,
            dst_port=self.remote_port,
            seq=self.snd_seq,
            ack=self.snd_ack,
            flags=flags,
            window=self.snd_wnd,
            payload=payload,
        )
        from kernel.net import stack
        await stack.send_tcp_segment(seg, self.local_ip, self.remote_ip)
        if payload or (flags & (F_SYN | F_FIN)):
            self.snd_seq = (self.snd_seq + max(len(payload), 1)) & 0xFFFFFFFF

    def send_segment_nowait(self, flags: int, payload: bytes = b"") -> bool:
        seg = TCPSegment(
            src_port=self.local_port,
            dst_port=self.remote_port,
            seq=self.snd_seq,
            ack=self.snd_ack,
            flags=flags,
            window=self.snd_wnd,
            payload=payload,
        )
        from kernel.net import stack
        ok = stack.send_tcp_segment_nowait(seg, self.local_ip, self.remote_ip)
        if ok and (payload or (flags & (F_SYN | F_FIN))):
            self.snd_seq = (self.snd_seq + max(len(payload), 1)) & 0xFFFFFFFF
        return ok

    async def recv(self, n: int = 4096) -> bytes:
        while True:
            if self.rcv_buf:
                break
            if self.state in (TCPState.CLOSE_WAIT, TCPState.CLOSED):
                return b""
            self._rx_event.clear()
            if self.rcv_buf:
                break
            if self.state in (TCPState.CLOSE_WAIT, TCPState.CLOSED):
                return b""
            await self._rx_event.wait()
        data = bytes(self.rcv_buf[:n])
        del self.rcv_buf[:n]
        return data

    async def send(self, data: bytes) -> None:
        data = bytes(data)
        for off in range(0, len(data), TCP_MAX_PAYLOAD):
            await self.send_segment(F_ACK | F_PSH,
                                    data[off:off + TCP_MAX_PAYLOAD])

    def send_nowait(self, data: bytes) -> bool:
        data = bytes(data)
        from kernel.net import stack
        for off in range(0, len(data), TCP_MAX_PAYLOAD):
            if not self.send_segment_nowait(
                    F_ACK | F_PSH, data[off:off + TCP_MAX_PAYLOAD]):
                return False
            stack.poll_once()
        return True

    def close(self) -> None:
        if self.state == TCPState.ESTABLISHED:
            asyncio.ensure_future(self.send_segment(F_FIN | F_ACK))
            self.state = TCPState.FIN_WAIT_1
        elif self.state == TCPState.CLOSE_WAIT:
            asyncio.ensure_future(self.send_segment(F_FIN | F_ACK))
            self.state = TCPState.LAST_ACK

    def handle_segment(self, seg: TCPSegment, sync: bool = False) -> None:
        if self.state == TCPState.SYN_RCVD:
            if seg.flags & F_ACK:
                self.state = TCPState.ESTABLISHED
                self._accept_event.set()
            return
        if self.state == TCPState.SYN_SENT:
            if seg.flags & F_SYN and seg.flags & F_ACK:
                self.snd_ack = (seg.seq + 1) & 0xFFFFFFFF
                asyncio.ensure_future(self.send_segment(F_ACK))
                self.state = TCPState.ESTABLISHED
                if self._connected and not self._connected.done():
                    self._connected.set_result(self)
            elif seg.flags & F_RST:
                self.state = TCPState.CLOSED
                if self._connected and not self._connected.done():
                    self._connected.set_exception(ConnectionRefusedError())
        elif self.state == TCPState.ESTABLISHED:
            if seg.payload:
                self.snd_ack = (seg.seq + len(seg.payload)) & 0xFFFFFFFF
                self.rcv_buf.extend(seg.payload)
                self._rx_event.set()
                if sync:
                    self.send_segment_nowait(F_ACK)
                else:
                    asyncio.ensure_future(self.send_segment(F_ACK))
            if seg.flags & F_FIN:
                self.snd_ack = (seg.seq + 1) & 0xFFFFFFFF
                if sync:
                    self.send_segment_nowait(F_ACK)
                else:
                    asyncio.ensure_future(self.send_segment(F_ACK))
                self.state = TCPState.CLOSE_WAIT
                self._rx_event.set()   # wake any blocked recv()


class TCPListener:
    """Server-side listener returned by tcp.listen(port)."""

    def __init__(self, stack, port: int) -> None:
        self._stack = stack
        self.port = port
        self._queue: asyncio.Queue = asyncio.Queue()

    async def accept(self) -> TCPConnection:
        """Wait for the next completed incoming connection."""
        return await self._queue.get()

    def close(self) -> None:
        self._stack.unlisten(self.port, self)

    async def _on_syn(self, conn: TCPConnection) -> None:
        log.info(f"tcp: sending SYN-ACK to :{conn.remote_port}")
        try:
            await conn.send_segment(F_SYN | F_ACK)
        except Exception as e:
            import traceback as _tb
            log.info(f"tcp: send_segment raised: {e}\n{_tb.format_exc()}")
            self._stack.remove_connection(conn)
            return
        try:
            await asyncio.wait_for(conn._accept_event.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            conn.state = TCPState.CLOSED
            log.info(f"tcp: SYN-ACK timeout for :{conn.remote_port}")
            self._stack.remove_connection(conn)
            return
        if conn.state in (TCPState.ESTABLISHED, TCPState.CLOSE_WAIT):
            log.info(f"tcp: connection established with :{conn.remote_port}")
            await self._queue.put(conn)


class TCPStack:
    def __init__(self) -> None:
        self._connections: dict[tuple, TCPConnection] = {}
        self._listeners:   dict[int, TCPListener]     = {}
        self._next_port   = 49152   # ephemeral range

    def _alloc_port(self) -> int:
        port = self._next_port
        self._next_port = (self._next_port + 1 - 49152) % 16384 + 49152
        return port

    async def connect(self, remote_ip_str: str, remote_port: int,
                      local_ip: bytes = bytes(4)) -> TCPConnection:
        remote_ip   = ip_from_str(remote_ip_str)
        local_port  = self._alloc_port()
        conn = TCPConnection(
            local_ip=local_ip,
            remote_ip=remote_ip,
            local_port=local_port,
            remote_port=remote_port,
            state=TCPState.SYN_SENT,
        )
        conn._connected = asyncio.get_event_loop().create_future()
        key = (local_ip, local_port, remote_ip, remote_port)
        self._connections[key] = conn

        try:
            await conn.send_segment(F_SYN)
            await asyncio.wait_for(conn._connected, timeout=10.0)
        except Exception:
            self._connections.pop(key, None)
            raise
        return conn

    async def listen(self, port: int) -> TCPListener:
        """Start listening on *port*; return a TCPListener for accept()."""
        if port in self._listeners:
            raise OSError("port already in use: " + str(port))
        listener = TCPListener(self, port)
        self._listeners[port] = listener
        return listener

    def unlisten(self, port: int, listener: TCPListener | None = None) -> None:
        current = self._listeners.get(port)
        if current is not None and (listener is None or current is listener):
            self._listeners.pop(port, None)

    def remove_connection(self, conn: TCPConnection) -> None:
        key = (conn.local_ip, conn.local_port, conn.remote_ip, conn.remote_port)
        self._connections.pop(key, None)

    def handle_ip_packet(self, pkt: IPv4Packet, sync: bool = False) -> None:
        if pkt.proto != PROTO_TCP:
            return
        seg = TCPSegment.decode(pkt.payload, pkt.src, pkt.dst)
        if not seg:
            return
        key = (pkt.dst, seg.dst_port, pkt.src, seg.src_port)
        conn = self._connections.get(key)
        if conn:
            if TRACE_SEGMENTS:
                log.info(f"tcp: seg to :{seg.src_port} state={conn.state.name} flags={seg.flags:#04x} seq={seg.seq} ack={seg.ack}")
            conn.handle_segment(seg, sync=sync)
            return
        # Incoming SYN to a listening port — start three-way handshake
        if seg.flags & F_SYN and not (seg.flags & F_ACK):
            listener = self._listeners.get(seg.dst_port)
            if listener:
                log.info(f"tcp: SYN on port {seg.dst_port} from :{seg.src_port}")
                conn = TCPConnection(
                    local_ip=pkt.dst,
                    remote_ip=pkt.src,
                    local_port=seg.dst_port,
                    remote_port=seg.src_port,
                    state=TCPState.SYN_RCVD,
                    snd_ack=(seg.seq + 1) & 0xFFFFFFFF,
                )
                self._connections[key] = conn
                asyncio.ensure_future(listener._on_syn(conn))

# Module-level singleton
tcp = TCPStack()
