"""判断 TUN/拆分路由有没有把真实流量从物理默认路由上抢走。

这里只读路由表，不删除路由，也不动代理进程。
"""

from __future__ import annotations

from dataclasses import dataclass

from egress_switcher.classify import classify
from egress_switcher.models import Interface, InterfaceKind, SystemState, finish_selectable

PREVIEW_NOTE = "切换只改变代理自己出门走哪张网卡（如果代理开了自动检测网卡），不改变应用直接的流量。"
ADVICE = (
    "不要在这里删除它的路由，也不要关掉代理。"
    "请到代理客户端里把出站绑到物理网卡：sing-box 用 route.default_interface 或 auto_detect_interface，"
    "mihomo 用 interface-name。改完之后，再回来切换出口。"
)

# 这些名字按长短排列。只命中一个时才写进提示，对不上就只写接口名。
_CLIENTS = (
    ("clash-meta", "Clash Meta"),
    ("clash meta", "Clash Meta"),
    ("sing-box", "sing-box"),
    ("singbox", "sing-box"),
    ("shadowrocket", "Shadowrocket"),
    ("quantumult", "Quantumult"),
    ("wireguard", "WireGuard"),
    ("tailscale", "Tailscale"),
    ("openvpn", "OpenVPN"),
    ("mihomo", "mihomo"),
    ("v2ray", "v2ray"),
    ("xray", "xray"),
    ("surge", "Surge"),
    ("stash", "Stash"),
    ("clash", "Clash"),
)

_FULL = 1 << 32
_EXCEPT_ZERO_SLASH_8 = _FULL - (1 << 24)


@dataclass
class RouteEntry:
    device: str
    network: int
    length: int
    gateway: str | None = None
    metric: int | None = None


@dataclass
class TunCapture:
    device: str
    title: str
    process: str | None = None

    def banner(self) -> str:
        return f"⚠️ 现在流量被 {self.title} 的虚拟网卡接管，切换出口不会生效。\n{ADVICE}"


def parse_prefix(token: str) -> tuple[int, int] | None:
    text = token.strip()
    if not text or text.lower() in {"internet:", "internet6:", "routing"}:
        return None
    if text.lower() == "default":
        return 0, 0
    if "/" not in text:
        return None
    left, right = text.split("/", 1)
    try:
        length = int(right)
    except ValueError:
        return None
    if not 0 <= length <= 32:
        return None
    octets = [part for part in left.split(".") if part != ""]
    if not octets or len(octets) > 4:
        return None
    while len(octets) < 4:
        octets.append("0")
    try:
        nums = [int(part) for part in octets]
    except ValueError:
        return None
    if any(part < 0 or part > 255 for part in nums):
        return None
    network = (nums[0] << 24) | (nums[1] << 16) | (nums[2] << 8) | nums[3]
    return network, length


def prefix_from_mask(destination: str, mask: str) -> tuple[int, int] | None:
    if destination == "0.0.0.0" and mask == "0.0.0.0":
        return 0, 0
    network = parse_prefix(f"{destination}/32")
    if network is None:
        return None
    length = 0
    try:
        parts = [int(part) for part in mask.split(".")]
    except ValueError:
        return None
    if len(parts) != 4:
        return None
    for part in parts:
        length += bin(part).count("1")
    return network[0], length


def match_process(text: str) -> str | None:
    low = text.lower()
    found: list[str] = []
    for needle, label in _CLIENTS:
        if needle in low and label not in found:
            found.append(label)
    if "Clash Meta" in found and "Clash" in found:
        found.remove("Clash")
    if len(found) == 1:
        return found[0]
    return None


def parse_ifconfig_inet(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        if line and not line.startswith((" ", "\t")) and ":" in line:
            current = line.split(":", 1)[0].strip()
            continue
        if current is None or current in found:
            continue
        if "inet " not in line:
            continue
        parts = line.split()
        if "inet" in parts:
            index = parts.index("inet")
            if index + 1 < len(parts) and parts[index + 1].count(".") == 3:
                found[current] = parts[index + 1]
    return found


def parse_ip_route_get_device(text: str) -> str | None:
    for line in text.splitlines():
        parts = line.split()
        if "dev" not in parts:
            continue
        index = parts.index("dev")
        if index + 1 < len(parts):
            return parts[index + 1]
    return None


def apply_tun_capture(
    state: SystemState,
    routes: list[RouteEntry],
    *,
    probe_device: str | None = None,
    preferred_default_device: str | None = None,
    process_text: str = "",
    addresses: dict[str, str] | None = None,
) -> None:
    grouped: dict[str, list[RouteEntry]] = {}
    for route in routes:
        device = route.device.strip()
        if not device or device in {"lo", "lo0"}:
            continue
        grouped.setdefault(device, []).append(route)

    split_device = None
    for device, items in grouped.items():
        prefixes = [(item.network, item.length) for item in items if 0 < item.length <= 16]
        if _covers_default(prefixes):
            split_device = device
            break

    physical: list[tuple[int, str]] = []
    virtual: list[tuple[int, str]] = []
    for device, items in grouped.items():
        defaults = [item for item in items if item.length == 0]
        if not defaults:
            continue
        metric = min(item.metric if item.metric is not None else 0 for item in defaults)
        if _nonphysical(state, device):
            virtual.append((metric, device))
        else:
            physical.append((metric, device))

    priority_device = None
    if physical and virtual and min(virtual)[0] < min(physical)[0]:
        priority_device = min(virtual)[1]
    if (
        priority_device is None
        and preferred_default_device
        and _nonphysical(state, preferred_default_device)
        and physical
    ):
        priority_device = preferred_default_device

    capture_device = split_device or priority_device
    actual = probe_device or capture_device
    if actual:
        _retarget(state, actual, grouped.get(actual, []), addresses or {}, process_text)
    if not capture_device:
        return
    state.capture = _make_capture(state, capture_device, grouped.get(capture_device, []), process_text)


def _covers_default(prefixes: list[tuple[int, int]]) -> bool:
    ranges = [_span(network, length) for network, length in prefixes]
    return _union_size(ranges) >= _EXCEPT_ZERO_SLASH_8


def _span(network: int, length: int) -> tuple[int, int]:
    size = 1 << (32 - length)
    start = network & ~(size - 1)
    return start, start + size


def _union_size(ranges: list[tuple[int, int]]) -> int:
    if not ranges:
        return 0
    ordered = sorted(ranges)
    total = 0
    start, end = ordered[0]
    for nxt, stop in ordered[1:]:
        if nxt <= end:
            end = max(end, stop)
            continue
        total += end - start
        start, end = nxt, stop
    return total + end - start


def _nonphysical(state: SystemState, device: str) -> bool:
    iface = _find(state, device)
    if iface is not None:
        return iface.kind not in {InterfaceKind.WIRED, InterfaceKind.WIFI, InterfaceKind.USB_TETHER, InterfaceKind.UNKNOWN, InterfaceKind.LOOPBACK}
    low = device.lower()
    return low.startswith(("utun", "tun", "wg", "tailscale")) or "tun" in low or low in {"meta", "clash", "mihomo"}


def _find(state: SystemState, device: str) -> Interface | None:
    for iface in state.interfaces:
        if iface.device == device or iface.name == device:
            return iface
    return None


def _retarget(state: SystemState, device: str, routes: list[RouteEntry], addresses: dict[str, str], process_text: str) -> None:
    iface = _find(state, device)
    if iface is None:
        iface = _synthesize(state, device, routes, addresses, process_text)
    for item in state.interfaces:
        item.is_default = False
    iface.is_default = True
    if not iface.gateway:
        hop = _gateway(routes)
        if hop:
            iface.gateway = hop
    if not iface.ipv4 and device in addresses:
        iface.ipv4 = [addresses[device]]


def _synthesize(state: SystemState, device: str, routes: list[RouteEntry], addresses: dict[str, str], process_text: str) -> Interface:
    process = match_process(process_text)
    kind, reason = classify(name=device, description=process or "")
    if kind != InterfaceKind.VPN_TUN:
        kind, reason = InterfaceKind.VPN_TUN, "拆分路由指向的虚拟网卡"
    iface = Interface(
        name=device,
        device=device,
        kind=kind,
        ipv4=[addresses[device]] if device in addresses else [],
        gateway=_gateway(routes),
        is_up=True,
        reason=reason or "拆分路由覆盖了上网地址",
    )
    finish_selectable(iface)
    state.interfaces.append(iface)
    return iface


def _make_capture(state: SystemState, device: str, routes: list[RouteEntry], process_text: str) -> TunCapture:
    iface = _find(state, device)
    blob = "\n".join(
        part
        for part in (
            process_text,
            device,
            iface.name if iface else "",
            iface.description if iface else "",
            iface.reason if iface else "",
        )
        if part
    )
    process = match_process(blob)
    shown = iface.name if iface and iface.name else device
    if process and process.casefold() not in shown.casefold():
        title = f"{process}（{shown}）"
    elif iface and iface.name != device:
        title = f"{iface.name}（{device}）"
    else:
        title = shown
    return TunCapture(device=device, title=title, process=process)


def _gateway(routes: list[RouteEntry]) -> str | None:
    for route in routes:
        hop = (route.gateway or "").strip()
        if not hop or hop in {"0.0.0.0", "On-link", "link#"} or hop.startswith("link#"):
            continue
        if hop.count(".") == 3:
            return hop
    return None
