"""读取 Windows 的 IPv4 接口跃点，并生成 Set-NetIPInterface / netsh 计划。"""

from __future__ import annotations

import json
import re
from typing import Any

from egress_switcher.capture import RouteEntry, apply_tun_capture, parse_prefix, prefix_from_mask
from egress_switcher.classify import classify
from egress_switcher.models import Interface, InterfaceKind, SystemState, finish_selectable, mark_route_defaults

POWERSHELL_QUERY = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$ProgressPreference = 'SilentlyContinue'

function EnabledText($v) {
  if ($v -eq $true -or "$v" -eq 'Enabled') { 'Enabled' } else { 'Disabled' }
}

$ifaces = @(Get-NetIPInterface -AddressFamily IPv4 | ForEach-Object {
  [PSCustomObject]@{
    InterfaceIndex = $_.InterfaceIndex
    InterfaceAlias = $_.InterfaceAlias
    InterfaceMetric = $_.InterfaceMetric
    AutomaticMetric = (EnabledText $_.AutomaticMetric)
    ConnectionState = "$($_.ConnectionState)"
  }
})
$addrs = @(Get-NetIPAddress -AddressFamily IPv4 | ForEach-Object {
  [PSCustomObject]@{
    InterfaceIndex = $_.InterfaceIndex
    InterfaceAlias = $_.InterfaceAlias
    IPAddress = $_.IPAddress
    PrefixOrigin = "$($_.PrefixOrigin)"
  }
})
$addrs6 = @(Get-NetIPAddress -AddressFamily IPv6 | ForEach-Object {
  [PSCustomObject]@{
    InterfaceAlias = $_.InterfaceAlias
    IPAddress = $_.IPAddress
  }
})
function PrefixLength($prefix) {
  if ($null -eq $prefix) { return 99 }
  $text = [string]$prefix
  if ($text -match '/(\d+)$') { return [int]$Matches[1] }
  $prop = $prefix.PSObject.Properties['PrefixLength']
  if ($prop) { return [int]$prop.Value }
  return 99
}
# 0.0.0.0/0 以及 /1、/8 这类拆分路由。更长的前缀不是「接管整段上网地址」。
$routes = @(Get-NetRoute -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object {
  (PrefixLength $_.DestinationPrefix) -le 16
} | ForEach-Object {
  [PSCustomObject]@{
    InterfaceIndex = $_.InterfaceIndex
    InterfaceAlias = $_.InterfaceAlias
    NextHop = $_.NextHop
    RouteMetric = $_.RouteMetric
    InterfaceMetric = $_.InterfaceMetric
    DestinationPrefix = "$($_.DestinationPrefix)"
  }
})
# 只问 8.8.8.8 会走哪张网卡。不改路由，不改跃点。
$best = $null
try {
  if (-not ('BestRoute2' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class BestRoute2 {
  [DllImport("iphlpapi.dll")]
  public static extern int GetBestRoute2(
    IntPtr interfaceLuid,
    uint interfaceIndex,
    IntPtr sourceAddress,
    IntPtr destinationAddress,
    uint addressSortOptions,
    IntPtr bestRoute,
    IntPtr bestSourceAddress);
}
'@
  }
  $dest = [Runtime.InteropServices.Marshal]::AllocHGlobal(28)
  $row = [Runtime.InteropServices.Marshal]::AllocHGlobal(512)
  $src = [Runtime.InteropServices.Marshal]::AllocHGlobal(28)
  try {
    $zero28 = New-Object byte[] 28
    $zeroRow = New-Object byte[] 512
    [Runtime.InteropServices.Marshal]::Copy($zero28, 0, $dest, 28)
    [Runtime.InteropServices.Marshal]::Copy($zero28, 0, $src, 28)
    [Runtime.InteropServices.Marshal]::Copy($zeroRow, 0, $row, 512)
    # SOCKADDR_IN：family=AF_INET(2)，地址 8.8.8.8
    $addr = [byte[]](2, 0, 0, 0, 8, 8, 8, 8)
    [Runtime.InteropServices.Marshal]::Copy($addr, 0, $dest, 8)
    $code = [BestRoute2]::GetBestRoute2([IntPtr]::Zero, [uint32]0, [IntPtr]::Zero, $dest, [uint32]0, $row, $src)
    if ($code -eq 0) {
      $idx = [Runtime.InteropServices.Marshal]::ReadInt32($row, 8)
      $alias = $null
      foreach ($iface in $ifaces) {
        if ([int]$iface.InterfaceIndex -eq $idx) { $alias = [string]$iface.InterfaceAlias }
      }
      $best = [PSCustomObject]@{ InterfaceIndex = $idx; InterfaceAlias = $alias }
    }
  } finally {
    [Runtime.InteropServices.Marshal]::FreeHGlobal($dest)
    [Runtime.InteropServices.Marshal]::FreeHGlobal($row)
    [Runtime.InteropServices.Marshal]::FreeHGlobal($src)
  }
} catch {
  $best = $null
}
$adapters = @()
try {
  $adapters = @(Get-NetAdapter | ForEach-Object {
    [PSCustomObject]@{
      Name = $_.Name
      InterfaceDescription = $_.InterfaceDescription
      Status = "$($_.Status)"
      Virtual = [bool]$_.Virtual
      HardwareInterface = [bool]$_.HardwareInterface
    }
  })
} catch {
  $adapters = @()
}
$json = @{
  interfaces = $ifaces
  addresses = $addrs
  addresses6 = $addrs6
  routes = $routes
  adapters = $adapters
  best = $best
} | ConvertTo-Json -Depth 5 -Compress
# 直接写 UTF-8 字节。中文 Windows 的控制台是 GBK，若让宿主再编码，网卡名会坏。
$bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($json + "`n")
$stdout = [Console]::OpenStandardOutput()
$stdout.Write($bytes, 0, $bytes.Length)
$stdout.Flush()
""".strip()


def parse_windows_json(text: str) -> SystemState:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Get-NetIPInterface 的 JSON 无法解析：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Get-NetIPInterface 的 JSON 不是对象")

    descriptions = {
        str(_pick(item, "Name", "InterfaceAlias")): str(_pick(item, "InterfaceDescription", "Description") or "")
        for item in _as_list(data.get("adapters"))
    }
    virtual_names = {
        str(_pick(item, "Name", "InterfaceAlias"))
        for item in _as_list(data.get("adapters"))
        if _as_bool(_pick(item, "Virtual")) is True
    }
    ipv4_by_index: dict[int, list[str]] = {}
    for item in _as_list(data.get("addresses")):
        index = _as_int(_pick(item, "InterfaceIndex", "ifIndex"))
        ip = str(_pick(item, "IPAddress", "ip") or "")
        if index is None or not _usable_ipv4(ip):
            continue
        ipv4_by_index.setdefault(index, []).append(ip)
    ipv6_by_alias: dict[str, list[str]] = {}
    for item in _as_list(data.get("addresses6")):
        alias = str(_pick(item, "InterfaceAlias", "Name") or "")
        ip = str(_pick(item, "IPAddress", "ip") or "").split("%", 1)[0]
        if not alias or not ip or ip.lower().startswith("::1"):
            continue
        ipv6_by_alias.setdefault(alias, []).append(ip)

    routes_by_index: dict[int, dict[str, Any]] = {}
    for item in _as_list(data.get("routes")):
        index = _as_int(_pick(item, "InterfaceIndex", "ifIndex"))
        hop = str(_pick(item, "NextHop", "Gateway") or "")
        if index is None or hop in {"", "0.0.0.0"} or not _is_default_prefix(item):
            continue
        route_metric = _as_int(_pick(item, "RouteMetric")) or 0
        interface_metric = _as_int(_pick(item, "InterfaceMetric")) or 0
        effective = route_metric + interface_metric
        current = routes_by_index.get(index)
        if current is None or effective < current["effective"]:
            routes_by_index[index] = {"gateway": hop, "effective": effective}

    interfaces: list[Interface] = []
    for item in _as_list(data.get("interfaces")):
        alias = str(_pick(item, "InterfaceAlias", "Name") or "")
        index = _as_int(_pick(item, "InterfaceIndex", "ifIndex"))
        if not alias or index is None:
            continue
        description = descriptions.get(alias, "")
        kind, reason = classify(name=alias, description=description)
        if alias in virtual_names and kind == InterfaceKind.UNKNOWN:
            kind, reason = InterfaceKind.VIRTUAL, "网卡报告自己是虚拟设备"
        state_text = str(_pick(item, "ConnectionState", "Status") or "")
        metric = _as_int(_pick(item, "InterfaceMetric", "metric"))
        route = routes_by_index.get(index)
        iface = Interface(
            name=alias,
            device=alias,
            kind=kind,
            ipv4=_prefer_ipv4(ipv4_by_index.get(index, [])),
            ipv6=_unique(ipv6_by_alias.get(alias, [])),
            gateway=route["gateway"] if route else None,
            metric=metric,
            automatic_metric=_enabled(_pick(item, "AutomaticMetric")),
            configured_metric=metric,
            route_metric=route["effective"] if route else None,
            is_up=_connected(state_text),
            service_name=alias,
            ifindex=index,
            description=description,
            reason=reason,
        )
        finish_selectable(iface)
        interfaces.append(iface)
    mark_route_defaults(interfaces)
    state = SystemState(platform="windows", interfaces=interfaces, source="windows-json")
    apply_tun_capture(state, _json_route_entries(data), probe_device=_best_device(data, interfaces))
    return state


def parse_windows_text(
    route_print: str,
    get_netipinterface: str,
    get_netipaddress: str,
    get_netipaddress6: str = "",
) -> SystemState:
    descriptions = parse_route_print_interfaces(route_print)
    route_rows = parse_route_print_defaults(route_print)
    blocks = _parse_format_list(get_netipinterface)
    addresses = _parse_format_list(get_netipaddress)
    addresses6 = _parse_format_list(get_netipaddress6) if get_netipaddress6 else []

    ipv4_by_index: dict[int, list[str]] = {}
    for item in addresses:
        index = _as_int(item.get("InterfaceIndex") or item.get("ifIndex"))
        ip = item.get("IPAddress", "")
        if index is None or not _usable_ipv4(ip):
            continue
        ipv4_by_index.setdefault(index, []).append(ip)
    ip_to_index = {ip: index for index, ips in ipv4_by_index.items() for ip in ips}
    routes_by_index: dict[int, dict[str, Any]] = {}
    for row in route_rows:
        index = ip_to_index.get(row["interface_ip"])
        if index is None:
            continue
        current = routes_by_index.get(index)
        if current is None or row["effective"] < current["effective"]:
            routes_by_index[index] = {"gateway": row["gateway"], "effective": row["effective"]}

    ipv6_by_alias: dict[str, list[str]] = {}
    for item in addresses6:
        alias = item.get("InterfaceAlias") or item.get("Name") or ""
        ip = (item.get("IPAddress") or "").split("%", 1)[0]
        if alias and ip and not ip.lower().startswith("::1"):
            ipv6_by_alias.setdefault(alias, []).append(ip)

    interfaces: list[Interface] = []
    for item in blocks:
        alias = item.get("InterfaceAlias") or item.get("Name") or ""
        index = _as_int(item.get("InterfaceIndex") or item.get("ifIndex"))
        if not alias or index is None:
            continue
        description = descriptions.get(index, "")
        kind, reason = classify(name=alias, description=description)
        metric = _as_int(item.get("InterfaceMetric") or item.get("metric"))
        route = routes_by_index.get(index)
        iface = Interface(
            name=alias,
            device=alias,
            kind=kind,
            ipv4=_prefer_ipv4(ipv4_by_index.get(index, [])),
            ipv6=_unique(ipv6_by_alias.get(alias, [])),
            gateway=route["gateway"] if route else None,
            metric=metric,
            automatic_metric=_enabled(item.get("AutomaticMetric")),
            configured_metric=metric,
            route_metric=route["effective"] if route else None,
            is_up=_connected(item.get("ConnectionState", "")),
            service_name=alias,
            ifindex=index,
            description=description,
            reason=reason,
        )
        finish_selectable(iface)
        interfaces.append(iface)
    mark_route_defaults(interfaces)
    state = SystemState(platform="windows", interfaces=interfaces, source="windows-text")
    ip_to_name = {ip: iface.device for iface in interfaces for ip in iface.ipv4}
    entries: list[RouteEntry] = []
    for row in parse_route_print_ipv4(route_print):
        device = ip_to_name.get(row["interface_ip"])
        if not device:
            continue
        entries.append(RouteEntry(device, row["network"], row["length"], row["gateway"], row["effective"]))
    apply_tun_capture(state, entries)
    return state


def parse_route_print_interfaces(text: str) -> dict[int, str]:
    descriptions: dict[int, str] = {}
    in_list = False
    for line in text.splitlines():
        if "Interface List" in line:
            in_list = True
            continue
        if not in_list:
            continue
        if line.startswith("===="):
            break
        match = re.match(r"^\s*(\d+)\.\.\.(.*?)\.{3,}\s*(.*?)\s*$", line)
        if match:
            descriptions[int(match.group(1))] = match.group(3).strip()
    return descriptions


def parse_route_print_ipv4(text: str) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    section = "none"
    for line in text.splitlines():
        if "IPv4 Route Table" in line:
            section = "pre"
            continue
        if "IPv6 Route Table" in line:
            break
        if section == "pre" and "Network Destination" in line:
            section = "ipv4"
            continue
        if section == "ipv4" and line.startswith("Persistent Routes"):
            break
        if section != "ipv4":
            continue
        parts = line.split()
        if len(parts) < 5 or not re.match(r"^\d", parts[0]):
            continue
        dest, mask, gateway, iface_ip, metric_text = parts[:5]
        prefix = prefix_from_mask(dest, mask)
        if prefix is None:
            continue
        try:
            metric = int(metric_text)
        except ValueError:
            continue
        routes.append(
            {
                "network": prefix[0],
                "length": prefix[1],
                "gateway": gateway,
                "interface_ip": iface_ip,
                "effective": metric,
            }
        )
    return routes


def parse_route_print_defaults(text: str) -> list[dict[str, Any]]:
    return [
        {"gateway": row["gateway"], "interface_ip": row["interface_ip"], "effective": row["effective"]}
        for row in parse_route_print_ipv4(text)
        if row["length"] == 0 and row["gateway"] not in {"On-link", "0.0.0.0"}
    ]


def _parse_format_list(text: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            if current:
                blocks.append(current)
                current = {}
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        current[key.strip()] = value.strip()
    if current:
        blocks.append(current)
    return blocks


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


def _pick(item: Any, *keys: str) -> Any:
    if not isinstance(item, dict):
        return None
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _enabled(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"enabled", "true", "yes", "1"}:
        return True
    if text in {"disabled", "false", "no", "0"}:
        return False
    return None


def _connected(value: str) -> bool:
    return value.strip().lower() in {"connected", "up"}


def _is_default_prefix(item: Any) -> bool:
    """没有 DestinationPrefix 的旧数据按 0.0.0.0/0 处理。拆分前缀不参与默认网关。"""
    raw = _pick(item, "DestinationPrefix")
    if raw is None or str(raw).strip() == "":
        return True
    parsed = parse_prefix(str(raw))
    return parsed == (0, 0)


def _json_route_entries(data: dict) -> list[RouteEntry]:
    entries: list[RouteEntry] = []
    for item in _as_list(data.get("routes")):
        alias = str(_pick(item, "InterfaceAlias", "Name") or "").strip()
        index = _as_int(_pick(item, "InterfaceIndex", "ifIndex"))
        device = alias or (str(index) if index is not None else "")
        if not device:
            continue
        raw = _pick(item, "DestinationPrefix")
        if raw is None or str(raw).strip() == "":
            parsed = (0, 0)
        else:
            parsed = parse_prefix(str(raw))
            if parsed is None:
                continue
        hop = str(_pick(item, "NextHop", "Gateway") or "")
        route_metric = _as_int(_pick(item, "RouteMetric")) or 0
        interface_metric = _as_int(_pick(item, "InterfaceMetric")) or 0
        metric = route_metric + interface_metric if parsed[1] == 0 else route_metric
        entries.append(RouteEntry(device, parsed[0], parsed[1], hop or None, metric))
    return entries


def _best_device(data: dict, interfaces: list[Interface]) -> str | None:
    best = data.get("best")
    if not isinstance(best, dict):
        return None
    alias = str(_pick(best, "InterfaceAlias") or "").strip()
    if alias:
        return alias
    index = _as_int(_pick(best, "InterfaceIndex"))
    if index is None:
        return None
    for iface in interfaces:
        if iface.ifindex == index:
            return iface.device
    return None


def _usable_ipv4(ip: str) -> bool:
    return bool(ip) and not ip.startswith("127.")


def _prefer_ipv4(items: list[str]) -> list[str]:
    unique = _unique(items)
    preferred = [ip for ip in unique if not ip.startswith("169.254.")]
    return preferred or unique


def _unique(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen
