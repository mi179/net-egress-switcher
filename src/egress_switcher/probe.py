"""从指定 IPv4 发一次很短的 TCP 连接，用来判断这张网卡能不能出去。"""

from __future__ import annotations

import socket
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from egress_switcher.models import Interface, InterfaceKind

# 校园网常用的国内地址。443 比 ICMP 不容易被整个禁掉。
PROBE_TARGETS = (("223.5.5.5", 443), ("119.29.29.29", 443))


def probe_all(interfaces: list[Interface], timeout: float = 1.0) -> None:
    targets = [iface for iface in interfaces if iface.ipv4 and iface.kind != InterfaceKind.LOOPBACK]
    if not targets:
        return
    workers = min(6, len(targets))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(probe_interface, iface, timeout): iface for iface in targets}
        for future in as_completed(futures):
            iface = futures[future]
            try:
                iface.has_internet = future.result()
            except Exception:
                iface.has_internet = False


def probe_interface(iface: Interface, timeout: float = 1.0) -> bool:
    if not iface.ipv4:
        return False
    source = iface.ipv4[0]
    for host, port in PROBE_TARGETS:
        if _try_connect(source, iface.device or iface.name, iface.ifindex, host, port, timeout):
            return True
    return False


def _try_connect(source: str, device: str, ifindex: int | None, host: str, port: int, timeout: float) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        _bind_interface(sock, source, device, ifindex)
        sock.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _bind_interface(sock: socket.socket, source: str, device: str, ifindex: int | None) -> None:
    if sys.platform == "darwin":
        index = ifindex
        if index is None and device:
            try:
                index = socket.if_nametoindex(device)
            except OSError:
                index = None
        if index is not None:
            try:
                # IP_BOUND_IF，普通用户可用。
                sock.setsockopt(socket.IPPROTO_IP, 25, index)
            except OSError:
                pass
    elif sys.platform.startswith("linux") and device:
        try:
            # SO_BINDTODEVICE。没有权限时退回绑定源地址。
            sock.setsockopt(socket.SOL_SOCKET, 25, device.encode() + b"\0")
        except OSError:
            pass
    sock.bind((source, 0))
