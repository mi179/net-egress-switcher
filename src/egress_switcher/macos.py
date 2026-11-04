"""读取 macOS 网络服务顺序。切换出口用 networksetup -ordernetworkservices。"""

from __future__ import annotations

import re

from egress_switcher.capture import RouteEntry, apply_tun_capture, parse_ifconfig_inet, parse_prefix
from egress_switcher.classify import classify
from egress_switcher.models import Interface, InterfaceKind, SystemState, finish_selectable

_SERVICE_RE = re.compile(r"^\((\d+|\*)\)\s+(.+?)\s*$")
_PORT_RE = re.compile(r"^\(Hardware Port:\s*(.*?),\s*Device:\s*(.*?)\)\s*$")


def parse_macos(
    service_order_text: str,
    getinfo_text: str,
    netstat_text: str,
    route_get_text: str,
    route_get_host: str = "",
    ifconfig_text: str = "",
    process_text: str = "",
) -> SystemState:
    services = parse_service_order(service_order_text)
    infos = parse_getinfo_bundle(getinfo_text)
    defaults = parse_netstat_defaults(netstat_text)
    chosen = parse_route_get_default(route_get_text)
    gateway_by_device = {row["device"]: row["gateway"] for row in defaults}

    interfaces: list[Interface] = []
    order_names: list[str] = []
    for service in services:
        order_names.append(service["name"])
        info = infos.get(service["name"], {})
        device = service["device"]
        kind, reason = classify(
            name=device or service["name"],
            description=service["name"],
            hardware_port=service["hardware_port"],
        )
        ipv4 = info.get("ipv4")
        gateway = info.get("gateway") or gateway_by_device.get(device)
        iface = Interface(
            name=service["name"],
            device=device,
            kind=kind,
            ipv4=[ipv4] if ipv4 else [],
            ipv6=list(info.get("ipv6") or []),
            gateway=gateway,
            is_up=bool(ipv4 and ipv4 != "0.0.0.0"),
            service_name=service["name"],
            service_order=service["order"],
            service_enabled=service["enabled"],
            hardware_port=service["hardware_port"],
            description=device,
            reason=reason,
        )
        finish_selectable(iface)
        interfaces.append(iface)

    chosen_device = chosen.get("interface")
    for iface in interfaces:
        iface.is_default = bool(chosen_device and iface.device == chosen_device)
        if iface.is_default and not iface.gateway:
            iface.gateway = chosen.get("gateway")
    _mark_physical_preferred(interfaces)
    state = SystemState(
        platform="macos",
        interfaces=interfaces,
        source="macos-text",
        service_order=order_names,
    )
    probe = parse_route_get_default(route_get_host).get("interface") if route_get_host.strip() else None
    apply_tun_capture(
        state,
        _route_entries(netstat_text),
        probe_device=probe,
        preferred_default_device=chosen_device,
        process_text=process_text,
        addresses=parse_ifconfig_inet(ifconfig_text),
    )
    return state


def parse_service_order(text: str) -> list[dict]:
    services: list[dict] = []
    current: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        service_match = _SERVICE_RE.match(line)
        if service_match:
            if current:
                services.append(current)
            token, name = service_match.group(1), service_match.group(2).strip()
            current = {
                "order": None if token == "*" else int(token),
                "name": name,
                "enabled": token != "*",
                "hardware_port": "",
                "device": "",
            }
            continue
        port_match = _PORT_RE.match(line)
        if port_match and current is not None:
            current["hardware_port"] = port_match.group(1).strip()
            current["device"] = port_match.group(2).strip()
    if current:
        services.append(current)
    return services


def parse_getinfo_bundle(text: str) -> dict[str, dict]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        match = re.match(r"^===\s*(.+?)\s*===\s*$", line.strip())
        if match:
            current = match.group(1)
            sections[current] = []
            continue
        if current is not None:
            sections[current].append(line)
    return {name: parse_getinfo("\n".join(lines)) for name, lines in sections.items()}


def parse_getinfo(text: str) -> dict:
    ipv4 = None
    gateway = None
    ipv6: list[str] = []
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if not value or value.lower() == "none":
            continue
        if key == "ip address":
            ipv4 = value
        elif key == "router":
            gateway = value
        elif key == "ipv6 ip address":
            ipv6.append(value.split("%", 1)[0])
    return {"ipv4": ipv4, "gateway": gateway, "ipv6": ipv6}


def parse_netstat_routes(text: str) -> list[dict[str, str]]:
    routes: list[dict[str, str]] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        dest, gateway, device = parts[0], parts[1], parts[3]
        if dest in {"Destination", "Routing", "Internet:"} or device in {"Netif", "Expire"}:
            continue
        if not re.match(r"^[A-Za-z][A-Za-z0-9]*$", device):
            continue
        routes.append({"dest": dest, "gateway": gateway, "device": device})
    return routes


def parse_netstat_defaults(text: str) -> list[dict[str, str]]:
    return [
        {"gateway": row["gateway"], "device": row["device"]}
        for row in parse_netstat_routes(text)
        if row["dest"] == "default"
    ]


def _route_entries(text: str) -> list[RouteEntry]:
    entries: list[RouteEntry] = []
    for row in parse_netstat_routes(text):
        prefix = parse_prefix(row["dest"])
        if prefix is None:
            continue
        entries.append(RouteEntry(row["device"], prefix[0], prefix[1], row["gateway"]))
    return entries


def parse_route_get_default(text: str) -> dict[str, str | None]:
    info: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        info[key.strip().lower()] = value.strip()
    return {"interface": info.get("interface"), "gateway": info.get("gateway")}


def _mark_physical_preferred(interfaces: list[Interface]) -> None:
    for iface in interfaces:
        iface.is_physical_preferred = False
    ranked = sorted(
        (
            iface
            for iface in interfaces
            if iface.selectable and iface.gateway and iface.service_order is not None
        ),
        key=lambda iface: iface.service_order or 10**9,
    )
    if ranked:
        ranked[0].is_physical_preferred = True
    elif any(iface.is_default and iface.kind in {InterfaceKind.WIRED, InterfaceKind.WIFI, InterfaceKind.USB_TETHER} for iface in interfaces):
        for iface in interfaces:
            if iface.is_default:
                iface.is_physical_preferred = True
                break
