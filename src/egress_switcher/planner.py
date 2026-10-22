"""把「要切到哪张网卡」变成可预览、可恢复的修改计划。这里不碰系统。"""

from __future__ import annotations

from dataclasses import dataclass, field

from egress_switcher.errors import ResolveError
from egress_switcher.models import Interface, InterfaceKind, SystemState
from egress_switcher.validate import validate_device, validate_ipv4, validate_label, validate_metric

IPV4_NOTE = "只调整 IPv4。IPv6 地址只展示，不修改。"
ELEVATION_NOTE = "需要管理员权限。只有你确认，并且在系统弹窗里同意之后才会修改；取消授权则系统保持原样。"
REAPPLY_NOTE = "NetworkManager 需要重新应用连接，网卡可能闪断一下。"
IPROUTE_NOTE = "这些网卡不由 NetworkManager 管理，将改用 ip route。重启后可能丢失，请在重启前使用恢复。"


@dataclass
class Plan:
    action: str
    target: str | None
    changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    payload: dict | None = None
    noop: bool = False
    error: str | None = None

    def preview_text(self) -> str:
        if self.error:
            return self.error
        lines: list[str] = []
        if self.changes:
            lines.append("将要修改：")
            lines.extend(f"· {item}" for item in self.changes)
        else:
            lines.append("没有需要修改的项目。")
        if self.warnings:
            lines.append("")
            lines.append("请注意：")
            lines.extend(f"· {item}" for item in self.warnings)
        return "\n".join(lines)


def resolve_target(state: SystemState, token: str) -> Interface:
    text = token.strip()
    if not text:
        raise ResolveError("请指定要设为出口的网卡。")
    if text.casefold() in {"auto", "automatic", "自动"}:
        raise ResolveError("自动不是一张网卡。请使用 automatic 命令。")
    exact = [iface for iface in state.interfaces if text in _keys(iface)]
    if len(exact) == 1:
        return exact[0]
    folded = text.casefold()
    folded_hits = [iface for iface in state.interfaces if folded in {key.casefold() for key in _keys(iface)}]
    if len(folded_hits) == 1:
        return folded_hits[0]
    if not exact and not folded_hits:
        raise ResolveError(f"找不到网卡：{text}")
    raise ResolveError(f"「{text}」对应多张网卡，请改用列表里的名称。")


def plan_set(state: SystemState, target: Interface) -> Plan:
    if target.kind in {InterfaceKind.VPN_TUN, InterfaceKind.VIRTUAL, InterfaceKind.LOOPBACK} or not target.selectable:
        return _error(
            "set",
            target.name,
            f"不能把「{target.name}」设为出口。{target.reason or target.kind_label()}",
        )
    if not target.is_up:
        return _error("set", target.name, f"「{target.name}」当前没有连接。")
    if not target.ipv4:
        return _error("set", target.name, f"「{target.name}」没有 IPv4 地址。")
    if state.platform == "windows":
        return _plan_windows_metrics(state, target)
    if state.platform == "macos":
        return _plan_macos_order(state, target.name)
    if state.platform == "linux":
        return _plan_linux_set(state, target)
    return _error("set", target.name, f"不支持的系统：{state.platform}")


def plan_automatic(state: SystemState, snapshot: dict | None) -> Plan:
    if state.platform == "windows":
        return _plan_windows_automatic(state)
    if state.platform == "macos":
        if not snapshot:
            return _noop("automatic", "还没有用本程序改过服务顺序，当前就是系统现有顺序。")
        return _plan_macos_restore(state, snapshot, action="automatic")
    if state.platform == "linux":
        return _plan_linux_automatic(state, snapshot)
    return _error("automatic", None, f"不支持的系统：{state.platform}")


def plan_restore(state: SystemState, snapshot: dict | None) -> Plan:
    if not snapshot:
        return _error("restore", None, "还没有保存过原始状态。只有用本程序改过之后才能恢复。")
    if snapshot.get("platform") != state.platform:
        return _error("restore", None, "保存的原始状态属于另一套系统，不能在这里恢复。")
    if state.platform == "windows":
        return _plan_windows_restore(state, snapshot)
    if state.platform == "macos":
        return _plan_macos_restore(state, snapshot, action="restore")
    if state.platform == "linux":
        return _plan_linux_restore(state, snapshot)
    return _error("restore", None, f"不支持的系统：{state.platform}")


def automatic_blurb(platform: str) -> str:
    if platform == "windows":
        return "重新启用自动跃点，由 Windows 按网卡速度决定。虚拟网卡和 TUN 不动。"
    if platform == "macos":
        return "恢复到本程序第一次切换前的网络服务顺序。"
    if platform == "linux":
        return "把 NetworkManager 的 ipv4.route-metric 设回自动（-1）。没有 NM 时恢复到第一次切换前的路由。"
    return "恢复系统自己的默认策略。"


def _plan_windows_metrics(state: SystemState, target: Interface) -> Plan:
    others = _physical_competitors(state, target.name)
    items: list[dict] = []
    changes: list[str] = []
    if not (target.metric == 1 and target.automatic_metric is False):
        items.append({"alias": target.name, "automatic": False, "metric": 1})
        changes.append(f"「{target.name}」IPv4 接口跃点：{_metric_phrase(target)} → 1（手动）")
    for index, iface in enumerate(others, start=1):
        metric = 10 + index * 10
        if iface.metric == metric and iface.automatic_metric is False:
            continue
        items.append({"alias": iface.name, "automatic": False, "metric": metric})
        changes.append(f"「{iface.name}」IPv4 接口跃点：{_metric_phrase(iface)} → {metric}（手动）")
    if not items:
        return _noop("set", f"「{target.name}」已经是手动跃点 1，其他物理网卡不用再改。", target.name)
    untouched = _untouched_phrase(state)
    if untouched:
        changes.append(untouched)
    warnings = _common_warnings(state)
    if not target.gateway:
        warnings.append(f"「{target.name}」没有 IPv4 网关，改完跃点也不一定能上网。")
    return Plan(
        action="set",
        target=target.name,
        changes=changes,
        warnings=warnings,
        payload={"version": 1, "platform": "windows", "action": "set_interface_metrics", "interfaces": items},
    )


def _plan_windows_automatic(state: SystemState) -> Plan:
    items = []
    changes = []
    for iface in _selectable(state):
        if iface.automatic_metric is True:
            continue
        items.append({"alias": iface.name, "automatic": True, "metric": iface.metric or 25})
        changes.append(f"「{iface.name}」改回自动跃点（现在是 {_metric_phrase(iface)}）")
    if not items:
        return _noop("automatic", "这些物理网卡已经在使用自动跃点。")
    untouched = _untouched_phrase(state)
    if untouched:
        changes.append(untouched)
    return Plan(
        action="automatic",
        target=None,
        changes=changes,
        warnings=_common_warnings(state),
        payload={"version": 1, "platform": "windows", "action": "set_interface_metrics", "interfaces": items},
    )


def _plan_windows_restore(state: SystemState, snapshot: dict) -> Plan:
    by_name = {iface.name: iface for iface in state.interfaces}
    items = []
    changes = []
    for saved in snapshot.get("windows") or []:
        alias = str(saved.get("alias") or "")
        iface = by_name.get(alias)
        if iface is None:
            continue
        automatic = bool(saved.get("automatic"))
        metric = int(saved.get("metric") or 0)
        if iface.automatic_metric is automatic and (automatic or iface.metric == metric):
            continue
        items.append({"alias": alias, "automatic": automatic, "metric": metric})
        goal = "自动" if automatic else f"手动 {metric}"
        changes.append(f"「{alias}」IPv4 接口跃点：{_metric_phrase(iface)} → {goal}")
    if not items:
        return _noop("restore", "已经是第一次切换前的接口跃点。")
    untouched = _untouched_phrase(state)
    if untouched:
        changes.append(untouched)
    return Plan(
        action="restore",
        target=None,
        changes=changes,
        warnings=_common_warnings(state),
        payload={"version": 1, "platform": "windows", "action": "set_interface_metrics", "interfaces": items},
    )


def _plan_macos_order(state: SystemState, service_name: str) -> Plan:
    if service_name not in state.service_order:
        return _error("set", service_name, f"网络服务列表里没有「{service_name}」。")
    order = [service_name] + [name for name in state.service_order if name != service_name]
    if order == state.service_order:
        return _noop("set", f"「{service_name}」已经排在网络服务的第一位。", service_name)
    changes = [
        f"把网络服务「{service_name}」放到第一位。",
        f"现在：{' → '.join(state.service_order)}",
        f"改为：{' → '.join(order)}",
        "调用 networksetup -ordernetworkservices。不修改 DNS，不关闭 VPN。",
    ]
    return Plan(
        action="set",
        target=service_name,
        changes=changes,
        warnings=_common_warnings(state),
        payload={"version": 1, "platform": "macos", "action": "order_services", "order": order},
    )


def _plan_macos_restore(state: SystemState, snapshot: dict, *, action: str) -> Plan:
    saved = [str(name) for name in snapshot.get("macos_order") or []]
    if not saved:
        return _error(action, None, "保存的状态里没有网络服务顺序。")
    order = [name for name in saved if name in state.service_order]
    for name in state.service_order:
        if name not in order:
            order.append(name)
    if order == state.service_order:
        message = "已经是第一次切换前的网络服务顺序。" if action == "restore" else "服务顺序已经是保存下来的系统顺序。"
        return _noop(action, message)
    changes = [
        "恢复网络服务顺序。",
        f"现在：{' → '.join(state.service_order)}",
        f"改为：{' → '.join(order)}",
        "调用 networksetup -ordernetworkservices。不修改 DNS，不关闭 VPN。",
    ]
    return Plan(
        action=action,
        target=None,
        changes=changes,
        warnings=_common_warnings(state),
        payload={"version": 1, "platform": "macos", "action": "order_services", "order": order},
    )


def _plan_linux_set(state: SystemState, target: Interface) -> Plan:
    if not target.gateway and not target.nm_connection:
        return _error("set", target.name, f"「{target.name}」没有 IPv4 网关，也不能交给 NetworkManager。")
    others = _physical_competitors(state, target.name)
    nm_items: list[dict] = []
    route_items: list[dict] = []
    changes: list[str] = []
    competitors = [iface for iface in others if iface.metric is not None]
    lowest = min((iface.metric for iface in competitors), default=100)
    target_metric = 50 if 50 < lowest else max(1, lowest - 10)
    next_metric = max(target_metric + 50, 200)

    def assign(iface: Interface, metric: int, *, primary: bool) -> None:
        nonlocal next_metric
        if iface.nm_connection and (state.nm_available or iface.nm_connection):
            if iface.configured_metric == metric and not primary:
                return
            if primary and iface.configured_metric == metric:
                return
            nm_items.append({"name": iface.nm_connection, "device": iface.name if iface.is_up else "", "metric": metric})
            changes.append(
                f"「{iface.nm_connection}」（{iface.name}）的 ipv4.route-metric："
                f"{_nm_phrase(iface)} → {metric}"
            )
            return
        if not iface.gateway:
            return
        if iface.metric == metric and not primary:
            return
        route_items.append({"dev": iface.name, "via": iface.gateway, "metric": metric})
        changes.append(f"ip route：{iface.name} 经 {iface.gateway} 的默认路由跃点 {_metric_phrase(iface)} → {metric}")

    assign(target, target_metric, primary=True)
    for iface in others:
        assign(iface, next_metric, primary=False)
        next_metric += 100
    if not nm_items and not route_items:
        return _noop("set", f"「{target.name}」的 IPv4 路由优先级已经是计划中的值。", target.name)
    untouched = _untouched_phrase(state)
    if untouched:
        changes.append(untouched)
    warnings = _common_warnings(state)
    if nm_items:
        warnings.append(REAPPLY_NOTE)
    if route_items:
        warnings.append(IPROUTE_NOTE)
    if not target.gateway:
        warnings.append(f"「{target.name}」没有 IPv4 网关，改完也不一定能上网。")
    return Plan(
        action="set",
        target=target.name,
        changes=changes,
        warnings=warnings,
        payload={
            "version": 1,
            "platform": "linux",
            "action": "linux_egress",
            "nm": nm_items,
            "routes": route_items,
        },
    )


def _plan_linux_automatic(state: SystemState, snapshot: dict | None) -> Plan:
    nm_items = []
    changes = []
    for iface in _selectable(state):
        if not iface.nm_connection:
            continue
        if iface.configured_metric in {None, -1}:
            continue
        nm_items.append({"name": iface.nm_connection, "device": iface.name if iface.is_up else "", "metric": -1})
        changes.append(f"「{iface.nm_connection}」（{iface.name}）的 ipv4.route-metric：{_nm_phrase(iface)} → 自动（-1）")
    route_items: list[dict] = []
    if not state.nm_available:
        if not snapshot:
            return _noop("automatic", "没有 NetworkManager，也还没有保存过原始路由。当前就是系统现状。")
        return plan_restore(state, snapshot)
    if not nm_items and not route_items:
        return _noop("automatic", "NetworkManager 管理的物理网卡已经是自动 route-metric。")
    untouched = _untouched_phrase(state)
    if untouched:
        changes.append(untouched)
    warnings = _common_warnings(state)
    warnings.append(REAPPLY_NOTE)
    return Plan(
        action="automatic",
        target=None,
        changes=changes,
        warnings=warnings,
        payload={"version": 1, "platform": "linux", "action": "linux_egress", "nm": nm_items, "routes": route_items},
    )


def _plan_linux_restore(state: SystemState, snapshot: dict) -> Plan:
    by_name = {iface.name: iface for iface in state.interfaces}
    by_conn = {iface.nm_connection: iface for iface in state.interfaces if iface.nm_connection}
    nm_items = []
    route_items = []
    changes = []
    for saved in snapshot.get("nm") or []:
        name = str(saved.get("name") or "")
        device = str(saved.get("device") or "")
        iface = by_conn.get(name) or by_name.get(device)
        if iface is None or not iface.nm_connection or not state.nm_available:
            continue
        metric = int(saved.get("metric"))
        if iface.configured_metric == metric:
            continue
        nm_items.append({"name": iface.nm_connection, "device": iface.name if iface.is_up else "", "metric": metric})
        goal = "自动（-1）" if metric == -1 else str(metric)
        changes.append(f"「{iface.nm_connection}」（{iface.name}）的 ipv4.route-metric：{_nm_phrase(iface)} → {goal}")
    for saved in snapshot.get("routes") or []:
        device = str(saved.get("dev") or "")
        iface = by_name.get(device)
        if iface is None or not iface.selectable:
            continue
        if state.nm_available and iface.nm_connection:
            continue
        metric = int(saved.get("metric"))
        via = str(saved.get("via") or "")
        if iface.metric == metric and iface.gateway == via:
            continue
        route_items.append({"dev": device, "via": via, "metric": metric})
        changes.append(f"ip route：{device} 经 {via} 的默认路由跃点恢复为 {metric}")
    if not nm_items and not route_items:
        return _noop("restore", "已经是第一次切换前的 IPv4 路由优先级。")
    untouched = _untouched_phrase(state)
    if untouched:
        changes.append(untouched)
    warnings = _common_warnings(state)
    if nm_items:
        warnings.append(REAPPLY_NOTE)
    if route_items:
        warnings.append(IPROUTE_NOTE)
    return Plan(
        action="restore",
        target=None,
        changes=changes,
        warnings=warnings,
        payload={"version": 1, "platform": "linux", "action": "linux_egress", "nm": nm_items, "routes": route_items},
    )


def snapshot_from_state(state: SystemState) -> dict:
    data: dict = {
        "version": 1,
        "platform": state.platform,
        "windows": [],
        "macos_order": list(state.service_order),
        "nm": [],
        "routes": [],
    }
    for iface in state.interfaces:
        if iface.kind == InterfaceKind.LOOPBACK or not iface.selectable:
            continue
        if state.platform == "windows":
            data["windows"].append(
                {
                    "alias": iface.name,
                    "metric": iface.metric if iface.metric is not None else 25,
                    "automatic": True if iface.automatic_metric is None else bool(iface.automatic_metric),
                }
            )
        if iface.nm_connection:
            data["nm"].append(
                {
                    "name": iface.nm_connection,
                    "device": iface.name,
                    "metric": -1 if iface.configured_metric is None else iface.configured_metric,
                }
            )
        if iface.gateway and iface.device:
            data["routes"].append(
                {
                    "dev": iface.device,
                    "via": iface.gateway,
                    "metric": iface.metric if iface.metric is not None else 100,
                }
            )
    return data


def _physical_competitors(state: SystemState, target_name: str) -> list[Interface]:
    found = [
        iface
        for iface in state.interfaces
        if iface.selectable and iface.name != target_name and iface.is_up
    ]
    return sorted(found, key=lambda iface: (iface.metric if iface.metric is not None else 10**9, iface.name))


def _selectable(state: SystemState) -> list[Interface]:
    return [iface for iface in state.interfaces if iface.selectable and iface.is_up]


def _untouched_phrase(state: SystemState) -> str:
    names = [
        iface.name
        for iface in state.interfaces
        if iface.is_up and iface.kind in {InterfaceKind.VPN_TUN, InterfaceKind.VIRTUAL}
    ]
    if not names:
        return ""
    return "不修改：" + "、".join(names)


def _common_warnings(state: SystemState) -> list[str]:
    warnings = [IPV4_NOTE, ELEVATION_NOTE]
    tuns = [_tun_label(iface) for iface in state.tun_interfaces()]
    if tuns:
        warnings.append(
            f"检测到正在运行的 TUN/代理网卡：{'、'.join(tuns)}。"
            "普通程序的流量仍会进入该 TUN，这次切换不会关闭它，也不会改它的路由。"
            "改的是物理网卡之间谁优先；TUN 开着时，通常只影响代理自己的上游，以及没有走 TUN 的程序。"
        )
    return warnings


def _tun_label(iface: Interface) -> str:
    if iface.device and iface.device != iface.name:
        return f"{iface.name}（{iface.device}）"
    return iface.name


def _metric_phrase(iface: Interface) -> str:
    return iface.metric_text()


def _nm_phrase(iface: Interface) -> str:
    if iface.configured_metric == -1:
        live = f"，当前路由跃点 {iface.metric}" if iface.metric is not None else ""
        return f"自动{live}"
    if iface.configured_metric is None:
        return "未知"
    return str(iface.configured_metric)


def _keys(iface: Interface) -> set[str]:
    values = {iface.name, iface.device}
    if iface.service_name:
        values.add(iface.service_name)
    if iface.nm_connection:
        values.add(iface.nm_connection)
    values.discard("")
    return values


def _noop(action: str, message: str, target: str | None = None) -> Plan:
    return Plan(action=action, target=target, changes=[message], warnings=[], payload=None, noop=True)


def _error(action: str, target: str | None, message: str) -> Plan:
    return Plan(action=action, target=target, error=message, payload=None)


def assert_payload_safe(payload: dict) -> None:
    """规划结果必须能通过和提权进程同一套校验。"""
    from egress_switcher.apply import validate_payload

    validate_payload(payload)
    if payload["platform"] == "windows":
        for item in payload["interfaces"]:
            validate_label(item["alias"], "接口")
            validate_metric(int(item["metric"]))
    elif payload["platform"] == "macos":
        for name in payload["order"]:
            validate_label(name, "网络服务")
    elif payload["platform"] == "linux":
        for item in payload.get("nm") or []:
            validate_label(item["name"], "连接")
            if item.get("device"):
                validate_device(item["device"])
            validate_metric(int(item["metric"]), allow_auto=True)
        for item in payload.get("routes") or []:
            validate_device(item["dev"])
            validate_ipv4(item["via"])
            validate_metric(int(item["metric"]))
