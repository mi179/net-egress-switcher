"""根据网卡名称、描述和 NetworkManager 类型判断它是不是能拿来上网的物理口。"""

from __future__ import annotations

import re

from egress_switcher.models import InterfaceKind

_TUN_EXACT = {"meta", "clash", "mihomo", "clash-meta", "clashmeta"}
_TUN_WORDS = ("meta", "clash", "mihomo", "vpn")
_TUN_SUBSTR = (
    "wintun",
    "utun",
    "tailscale",
    "wireguard",
    "openvpn",
    "tap-windows",
    "nordlynx",
    "zerotier",
    "netbird",
    "sing-box",
    "singbox",
    "outline",
    "warp",
    "gamevpn",
    "sslvpn",
    "加速",
)
_VIRTUAL_SUBSTR = (
    "vmware",
    "vmnet",
    "vethernet",
    "hyper-v",
    "hyperv",
    "virtualbox",
    "vbox",
    "docker",
    "veth",
    "virbr",
    "vnic",
    "vlan",
    "wsl",
    "default switch",
    "isatap",
    "teredo",
    "6to4",
    "bluetooth",
    "thunderbolt bridge",
    "virtual",
)
_TETHER_SUBSTR = (
    "iphone",
    "ipad",
    "android",
    "rndis",
    "remote ndis",
    "mobile device ethernet",
    "apple mobile",
    "usb tethering",
    "internet sharing",
)
_WIFI_SUBSTR = ("wi-fi", "wifi", "wlan", "wireless", "802.11", "802-11")
_WIRED_SUBSTR = (
    "ethernet",
    "gbe",
    "gigabit",
    "pcie",
    "ax88179",
    "rtl815",
    "realtek",
    "以太网",
    "lan",
)


def classify(
    *,
    name: str,
    description: str = "",
    hardware_port: str = "",
    nm_type: str = "",
) -> tuple[InterfaceKind, str]:
    blob = " ".join(part for part in (name, description, hardware_port, nm_type) if part)
    low = blob.lower()
    name_low = name.lower()
    nm_low = nm_type.lower()

    if _loopback(name_low, low):
        return InterfaceKind.LOOPBACK, "回环接口"
    if _tun(name_low, low, nm_low):
        return InterfaceKind.VPN_TUN, "代理或 VPN 的 TUN 网卡，不作为物理出口"
    if _virtual(name_low, low, nm_low):
        return InterfaceKind.VIRTUAL, "虚拟网卡，不作为上网出口"
    if _tether(name_low, low):
        return InterfaceKind.USB_TETHER, "手机 USB 共享"
    if _wifi(name_low, low, nm_low, hardware_port):
        return InterfaceKind.WIFI, "无线网络"
    if _wired(name_low, low, nm_low, hardware_port):
        return InterfaceKind.WIRED, "有线网络"
    return InterfaceKind.UNKNOWN, "未能识别类型；有 IPv4 网关时仍可手动选择"


def _word(text: str, word: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text) is not None


def _loopback(name_low: str, low: str) -> bool:
    return name_low in {"lo", "lo0", "loopback"} or "loopback" in low or name_low.startswith("loopback")


def _tun(name_low: str, low: str, nm_low: str) -> bool:
    if nm_low in {"tun", "wireguard", "ipsec", "vpn", "ppp"}:
        return True
    if name_low in _TUN_EXACT or name_low.startswith("tun") or name_low.startswith("utun"):
        return True
    if name_low.startswith("wg") or name_low.startswith("tap"):
        return True
    if any(_word(low, word) for word in _TUN_WORDS):
        return True
    return any(token in low for token in _TUN_SUBSTR)


def _virtual(name_low: str, low: str, nm_low: str) -> bool:
    if nm_low in {"bridge", "vlan", "bond", "team", "veth", "tun-virtual"}:
        return True
    if name_low.startswith(("veth", "virbr", "docker", "br-", "vmnet", "vboxnet")):
        return True
    if name_low in {"bridge", "bridge0", "docker0"}:
        return True
    return any(token in low for token in _VIRTUAL_SUBSTR)


def _tether(name_low: str, low: str) -> bool:
    # usb0 / rndis0 是手机共享的常见内核名。USB 千兆网卡不是共享，留给有线判断。
    if "usb 10/100" in low or "ax88179" in low or "rtl815" in low:
        return False
    if name_low in {"usb0", "rndis0"}:
        return True
    return any(token in low for token in _TETHER_SUBSTR)


def _wifi(name_low: str, low: str, nm_low: str, hardware_port: str) -> bool:
    if nm_low in {"wifi", "802-11-wireless"}:
        return True
    if hardware_port.lower() in {"wi-fi", "wifi", "wlan"}:
        return True
    if name_low.startswith(("wlan", "wlp", "wlo")):
        return True
    return any(token in low for token in _WIFI_SUBSTR)


def _wired(name_low: str, low: str, nm_low: str, hardware_port: str) -> bool:
    if nm_low in {"ethernet", "802-3-ethernet"}:
        return True
    port = hardware_port.lower()
    if "ethernet" in port or port.endswith(" lan") or "usb 10/100" in port:
        return True
    if name_low.startswith(("eth", "enp", "ens", "eno", "em")):
        return True
    return any(token in low for token in _WIRED_SUBSTR)
