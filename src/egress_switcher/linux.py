"""读取 Linux 的 ip route / nmcli。优先改 NetworkManager 的 ipv4.route-metric。"""

from __future__ import annotations

import re

from egress_switcher.classify import classify
from egress_switcher.models import Interface, SystemState, finish_selectable, mark_route_defaults

_IFACE_RE = re.compile(r"^\d+:\s+(\S+):\s+<([^>]*)>(.*)$")
_INET_RE = re.compile(r"\binet\s+(\d+\.\d+\.\d+\.\d+)\b")
_INET6_RE = re.compile(r"\binet6\s+([0-9a-fA-F:]+)")


def parse_linux(
    ip_route: str,
    ip_addr: str,
    ip_addr6: str = "",
    nmcli_devices: str = "",
    nmcli_metrics: str = "",
) -> SystemState:
    addresses = parse_ip_addr(ip_addr)
    addresses6 = parse_ip_addr(ip_addr6) if ip_addr6 else {}
    routes = parse_ip_route(ip_route)
    devices = parse_nmcli_devices(nmcli_devices) if nmcli_devices.strip() else {}
    metrics = parse_nmcli_metrics(nmcli_metrics) if nmcli_metrics.strip() else {}
    nm_available = bool(nmcli_devices.strip())

    default_by_dev: dict[str, dict] = {}
    link_metric: dict[str, int] = {}
    for route in routes:
        dev = route.get("dev")
        if not dev:
            continue
        if route["dst"] == "default" and route.get("via"):
            if route.get("metric") is None:
                route["metric"] = 0
            current = default_by_dev.get(dev)
            metric = route.get("metric")
            if current is None or (metric is not None and metric < current.get("metric", 10**9)):
                default_by_dev[dev] = route
        elif route.get("metric") is not None and dev not in link_metric:
            link_metric[dev] = route["metric"]

    names = list(dict.fromkeys([*addresses.keys(), *default_by_dev.keys(), *devices.keys()]))
    interfaces: list[Interface] = []
    for name in names:
        if name == "lo":
            continue
        addr = addresses.get(name, {})
        device = devices.get(name, {})
        flags = set(addr.get("flags", "").split(",")) if addr.get("flags") else set()
        is_up = ("UP" in flags and "NO-CARRIER" not in flags) or device.get("state") == "connected"
        nm_type = device.get("type", "")
        connection = device.get("connection") or ""
        kind, reason = classify(name=name, nm_type=nm_type, description=connection)
        default = default_by_dev.get(name)
        metric = None
        if default and default.get("metric") is not None:
            metric = default["metric"]
        elif name in link_metric:
            metric = link_metric[name]
        configured = metrics.get(connection) if connection else None
        automatic = configured == -1
        iface = Interface(
            name=name,
            device=name,
            kind=kind,
            ipv4=list(addr.get("ipv4") or []),
            ipv6=list((addresses6.get(name) or {}).get("ipv6") or []),
            gateway=default.get("via") if default else None,
            metric=metric,
            automatic_metric=automatic if configured is not None else None,
            configured_metric=configured,
            route_metric=default.get("metric") if default else None,
            is_up=is_up,
            nm_connection=connection or None,
            nm_type=nm_type,
            ifindex=addr.get("ifindex"),
            reason=reason,
        )
        finish_selectable(iface)
        interfaces.append(iface)
    mark_route_defaults(interfaces)
    return SystemState(
        platform="linux",
        interfaces=interfaces,
        source="linux-text",
        nm_available=nm_available,
    )


def parse_ip_route(text: str) -> list[dict]:
    routes: list[dict] = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        info: dict = {"dst": parts[0], "via": None, "dev": None, "metric": None, "proto": None}
        index = 1
        while index < len(parts) - 1:
            key = parts[index]
            value = parts[index + 1]
            if key in {"via", "dev", "proto", "scope", "src"}:
                info[key] = value
                index += 2
                continue
            if key == "metric":
                try:
                    info["metric"] = int(value)
                except ValueError:
                    info["metric"] = None
                index += 2
                continue
            index += 1
        routes.append(info)
    return routes


def parse_ip_addr(text: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    current: str | None = None
    for line in text.splitlines():
        match = _IFACE_RE.match(line)
        if match:
            name = match.group(1).split("@", 1)[0]
            current = name
            index_match = re.match(r"^(\d+):", line)
            result[name] = {
                "flags": match.group(2),
                "ifindex": int(index_match.group(1)) if index_match else None,
                "ipv4": [],
                "ipv6": [],
            }
            continue
        if current is None:
            continue
        inet = _INET_RE.search(line)
        if inet and not inet.group(1).startswith("127."):
            result[current]["ipv4"].append(inet.group(1))
            continue
        inet6 = _INET6_RE.search(line)
        if inet6:
            ip = inet6.group(1).split("%", 1)[0]
            if ip != "::1":
                result[current]["ipv6"].append(ip)
    for item in result.values():
        preferred = [ip for ip in item["ipv4"] if not ip.startswith("169.254.")]
        item["ipv4"] = preferred or item["ipv4"]
    return result


def parse_nmcli_devices(text: str) -> dict[str, dict]:
    devices: dict[str, dict] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("DEVICE"):
            continue
        parts = _split_nmcli(line)
        if len(parts) < 3:
            continue
        device, nm_type, state = parts[0], parts[1], parts[2]
        connection = parts[3] if len(parts) > 3 else ""
        if not device or device == "--":
            continue
        devices[device] = {
            "type": nm_type,
            "state": state,
            "connection": connection if connection and connection != "--" else "",
        }
    return devices


def parse_nmcli_metrics(text: str) -> dict[str, int]:
    metrics: dict[str, int] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("NAME"):
            continue
        parts = _split_nmcli(line)
        if len(parts) < 2:
            continue
        name, raw_metric = parts[0], parts[1].strip()
        if not name:
            continue
        try:
            metrics[name] = int(raw_metric)
        except ValueError:
            continue
    return metrics


def _split_nmcli(line: str) -> list[str]:
    parts: list[str] = []
    buffer: list[str] = []
    escaped = False
    for char in line:
        if escaped:
            buffer.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            parts.append("".join(buffer))
            buffer = []
        else:
            buffer.append(char)
    parts.append("".join(buffer))
    return parts
