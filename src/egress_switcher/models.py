from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class InterfaceKind(str, Enum):
    WIRED = "wired"
    WIFI = "wifi"
    USB_TETHER = "usb_tether"
    VIRTUAL = "virtual"
    VPN_TUN = "vpn_tun"
    LOOPBACK = "loopback"
    UNKNOWN = "unknown"


KIND_LABELS = {
    InterfaceKind.WIRED: "有线",
    InterfaceKind.WIFI: "Wi-Fi",
    InterfaceKind.USB_TETHER: "USB 共享",
    InterfaceKind.VIRTUAL: "虚拟网卡",
    InterfaceKind.VPN_TUN: "VPN/TUN",
    InterfaceKind.LOOPBACK: "回环",
    InterfaceKind.UNKNOWN: "未识别",
}

SELECTABLE_KINDS = {
    InterfaceKind.WIRED,
    InterfaceKind.WIFI,
    InterfaceKind.USB_TETHER,
    InterfaceKind.UNKNOWN,
}


@dataclass
class Interface:
    name: str
    kind: InterfaceKind
    device: str = ""
    ipv4: list[str] = field(default_factory=list)
    ipv6: list[str] = field(default_factory=list)
    gateway: str | None = None
    metric: int | None = None
    automatic_metric: bool | None = None
    configured_metric: int | None = None
    route_metric: int | None = None
    is_default: bool = False
    is_physical_preferred: bool = False
    is_up: bool = False
    has_internet: bool | None = None
    selectable: bool = False
    service_name: str | None = None
    service_order: int | None = None
    service_enabled: bool = True
    nm_connection: str | None = None
    nm_type: str = ""
    ifindex: int | None = None
    description: str = ""
    hardware_port: str = ""
    reason: str = ""

    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, "未识别")

    def metric_text(self) -> str:
        if self.service_order:
            return f"顺序 {self.service_order}"
        if self.service_name and not self.service_enabled:
            return "已禁用"
        if self.configured_metric == -1 and self.metric is not None:
            return f"{self.metric}（自动）"
        if self.metric is None:
            return "—"
        if self.automatic_metric:
            return f"{self.metric}（自动）"
        return str(self.metric)

    def internet_text(self) -> str:
        if not self.ipv4:
            return "无 IPv4"
        if self.has_internet is None:
            return "检测中"
        return "通" if self.has_internet else "不通"

    def ipv6_text(self) -> str:
        globals_ = [item for item in self.ipv6 if not item.lower().startswith("fe80:")]
        links = [item for item in self.ipv6 if item.lower().startswith("fe80:")]
        if globals_:
            text = ", ".join(globals_)
            if links:
                text += "（另有链路本地）"
            return text
        if links:
            return f"{links[0]}（链路本地，仅展示）"
        return "无"


@dataclass
class SystemState:
    platform: str
    interfaces: list[Interface] = field(default_factory=list)
    source: str = "live"
    service_order: list[str] = field(default_factory=list)
    nm_available: bool = False
    notes: list[str] = field(default_factory=list)
    capture: object | None = None

    def by_name(self, name: str) -> Interface | None:
        for iface in self.interfaces:
            if iface.name == name:
                return iface
        return None

    def tun_interfaces(self) -> list[Interface]:
        return [iface for iface in self.interfaces if iface.kind == InterfaceKind.VPN_TUN and iface.is_up]

    def default_interface(self) -> Interface | None:
        for iface in self.interfaces:
            if iface.is_default:
                return iface
        return None

    def physical_preferred(self) -> Interface | None:
        for iface in self.interfaces:
            if iface.is_physical_preferred:
                return iface
        return None


def mark_route_defaults(interfaces: list[Interface]) -> None:
    """Windows / Linux：有效跃点最低的默认路由获胜。"""
    for iface in interfaces:
        iface.is_default = False
        iface.is_physical_preferred = False
    candidates = [iface for iface in interfaces if iface.gateway and iface.route_metric is not None]
    if not candidates:
        return
    winner = min(candidates, key=_route_rank)
    winner.is_default = True
    physical = [iface for iface in candidates if iface.kind in SELECTABLE_KINDS]
    if physical:
        min(physical, key=_route_rank).is_physical_preferred = True


def finish_selectable(iface: Interface) -> None:
    kind_ok = iface.kind in SELECTABLE_KINDS
    iface.selectable = bool(kind_ok and iface.is_up and iface.service_enabled and iface.ipv4)


def _route_rank(iface: Interface) -> tuple:
    return (
        iface.route_metric if iface.route_metric is not None else 10**9,
        iface.ifindex if iface.ifindex is not None else 10**9,
        iface.name,
    )
