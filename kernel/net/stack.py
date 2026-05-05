"""
kernel.net.stack — Network stack glue.

Receives Ethernet frames from the NIC driver, demultiplexes by EtherType,
and hands off to ARP, IP, and higher-level protocol handlers.
Also provides the send path: TCP → IP → Ethernet → NIC.
"""


import asyncio
from kernel.net.ethernet import EtherFrame, ETHERTYPE_IPv4, ETHERTYPE_ARP
from kernel.net.ip       import IPv4Packet, ip_str
from kernel.net.arp      import arp_table
import kernel.log as log

# Our interface configuration (set by DHCP or static config at boot)
local_ip:  bytes = bytes(4)   # 0.0.0.0 until configured
local_mac: bytes = bytes(6)
gateway:   bytes = bytes(4)
netmask:   bytes = bytes(4)

# The active NIC driver (set by net_init())
_nic = None
TRACE_PACKETS = False

async def net_init(nic, ip: str, gw: str, mask: str = "255.255.255.0") -> None:
    """Configure the network stack with a static IP."""
    global local_ip, local_mac, gateway, netmask, _nic
    from kernel.net.ip import ip_from_str
    _nic       = nic
    local_mac  = nic.mac
    local_ip   = ip_from_str(ip)
    gateway    = ip_from_str(gw)
    netmask    = ip_from_str(mask)
    log.info(f"net: configured {ip} gw={gw}")
    asyncio.ensure_future(_rx_loop())

async def _rx_loop() -> None:
    """Continuously receive and dispatch Ethernet frames."""
    while True:
        raw = await _nic.recv()
        if len(raw) < 14:
            continue
        try:
            frame = EtherFrame.decode(raw)
            await _dispatch(frame)
        except Exception as e:
            log.warn(f"net: rx error: {e}")

def poll_once() -> bool:
    """Synchronously receive and dispatch one pending Ethernet frame.

    Normal network services run through ``_rx_loop``. The GUI bridge has a
    synchronous API surface today, so its TCP transport calls this while it is
    blocked waiting for host responses; that keeps ACKs and inbound payloads
    moving without requiring every bridge caller to become async.
    """
    if _nic is None or not hasattr(_nic, "recv_nowait"):
        return False
    raw = _nic.recv_nowait()
    if raw is None:
        return False
    if len(raw) < 14:
        return True
    try:
        frame = EtherFrame.decode(raw)
        _dispatch_sync(frame)
    except Exception as e:
        log.warn(f"net: sync rx error: {e}")
    return True

async def _dispatch(frame: EtherFrame) -> None:
    if frame.ethertype == ETHERTYPE_ARP:
        log.info(f"net: rx ARP len={len(frame.payload)}")
        arp_table.handle_frame(frame.payload)
        await _maybe_send_arp_reply(frame.payload)
    elif frame.ethertype == ETHERTYPE_IPv4:
        pkt = IPv4Packet.decode(frame.payload)
        log.info(f"net: rx IPv4 proto={pkt.proto} src={ip_str(pkt.src)}")
        # Learn src IP→MAC opportunistically so outbound SYN-ACK doesn't need ARP
        arp_table.learn(pkt.src, frame.src)
        from kernel.net.tcp import tcp
        tcp.handle_ip_packet(pkt)
    elif frame.ethertype != 0x86DD:   # ignore IPv6 silently
        log.info(f"net: ignoring frame ethertype={frame.ethertype:#06x}")

def _dispatch_sync(frame: EtherFrame) -> None:
    if frame.ethertype == ETHERTYPE_ARP:
        if TRACE_PACKETS:
            log.info(f"net: sync rx ARP len={len(frame.payload)}")
        arp_table.handle_frame(frame.payload)
        _maybe_send_arp_reply_sync(frame.payload)
    elif frame.ethertype == ETHERTYPE_IPv4:
        pkt = IPv4Packet.decode(frame.payload)
        if TRACE_PACKETS:
            log.info(f"net: sync rx IPv4 proto={pkt.proto} src={ip_str(pkt.src)}")
        arp_table.learn(pkt.src, frame.src)
        from kernel.net.tcp import tcp
        tcp.handle_ip_packet(pkt, sync=True)
    elif frame.ethertype != 0x86DD:
        if TRACE_PACKETS:
            log.info(f"net: sync ignoring frame ethertype={frame.ethertype:#06x}")

async def _maybe_send_arp_reply(payload: bytes) -> None:
    """Send an ARP reply if the request is for our IP."""
    import struct as _s
    if len(payload) < 28:
        return
    op         = _s.unpack(">H", payload[6:8])[0]
    sender_mac = payload[8:14]
    sender_ip  = payload[14:18]
    target_ip  = payload[24:28]
    if op != 1 or target_ip != local_ip or _nic is None:  # op=1 is ARP_REQUEST
        return
    log.info(f"net: sending ARP reply for {ip_str(target_ip)}")
    reply_arp = _s.pack(">HHBBH6s4s6s4s",
        1, 0x0800, 6, 4, 2,   # Ethernet/IPv4, op=REPLY
        local_mac, local_ip,
        sender_mac, sender_ip,
    )
    reply_frame = EtherFrame(dst=sender_mac, src=local_mac,
                             ethertype=ETHERTYPE_ARP,
                             payload=reply_arp).encode()
    await _nic.send(reply_frame)

def _maybe_send_arp_reply_sync(payload: bytes) -> None:
    import struct as _s
    if len(payload) < 28 or _nic is None:
        return
    op         = _s.unpack(">H", payload[6:8])[0]
    sender_mac = payload[8:14]
    sender_ip  = payload[14:18]
    target_ip  = payload[24:28]
    if op != 1 or target_ip != local_ip:
        return
    log.info(f"net: sync sending ARP reply for {ip_str(target_ip)}")
    reply_arp = _s.pack(">HHBBH6s4s6s4s",
        1, 0x0800, 6, 4, 2,
        local_mac, local_ip,
        sender_mac, sender_ip,
    )
    reply_frame = EtherFrame(dst=sender_mac, src=local_mac,
                             ethertype=ETHERTYPE_ARP,
                             payload=reply_arp).encode()
    _send_frame_nowait(reply_frame)

async def send_tcp_segment(seg, src_ip: bytes, dst_ip: bytes) -> None:
    """Wrap a TCP segment in IP and Ethernet and transmit it."""
    if _nic is None:
        log.info("net: send_tcp_segment: _nic is None")
        return
    # Resolve next-hop MAC via ARP
    from kernel.net.ip import ip_from_str
    if dst_ip[:3] == local_ip[:3]:   # same /24 subnet (rough check)
        next_hop = dst_ip
    else:
        next_hop = gateway

    mac = arp_table.lookup(next_hop)
    log.info(f"net: send_tcp_segment dst={ip_str(dst_ip)} mac={mac and mac.hex()}")
    if mac is None:
        arp_req = arp_table.build_request(local_mac, local_ip, next_hop)
        await _nic.send(arp_req)   # full Ethernet frame; NIC prepends only VirtIO header
        mac = await arp_table.resolve(next_hop)
        if mac is None:
            log.warn(f"net: ARP timeout for {ip_str(next_hop)}")
            return

    ip_pkt   = IPv4Packet(src=src_ip or local_ip, dst=dst_ip,
                          proto=6, ttl=64,
                          payload=seg.encode(src_ip or local_ip, dst_ip))
    eth_frame = EtherFrame(dst=mac, src=local_mac,
                           ethertype=ETHERTYPE_IPv4,
                           payload=ip_pkt.encode())
    log.info(f"net: calling _nic.send len={len(eth_frame.encode())}")
    await _nic.send(eth_frame.encode())

def _send_frame_nowait(frame: bytes) -> bool:
    if _nic is None:
        return False
    if hasattr(_nic, "send_nowait"):
        _nic.send_nowait(frame)
        return True
    return False

def send_tcp_segment_nowait(seg, src_ip: bytes, dst_ip: bytes) -> bool:
    """Synchronous TCP transmit path used by the native GUI bridge."""
    if _nic is None:
        log.info("net: send_tcp_segment_nowait: _nic is None")
        return False
    if dst_ip[:3] == local_ip[:3]:
        next_hop = dst_ip
    else:
        next_hop = gateway

    mac = arp_table.lookup(next_hop)
    if TRACE_PACKETS:
        log.info(f"net: send_tcp_segment_nowait dst={ip_str(dst_ip)} mac={mac and mac.hex()}")
    if mac is None:
        arp_req = arp_table.build_request(local_mac, local_ip, next_hop)
        _send_frame_nowait(arp_req)
        return False

    ip_pkt = IPv4Packet(src=src_ip or local_ip, dst=dst_ip,
                        proto=6, ttl=64,
                        payload=seg.encode(src_ip or local_ip, dst_ip))
    eth_frame = EtherFrame(dst=mac, src=local_mac,
                           ethertype=ETHERTYPE_IPv4,
                           payload=ip_pkt.encode()).encode()
    return _send_frame_nowait(eth_frame)
