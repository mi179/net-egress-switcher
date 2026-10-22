"""把校验过的计划变成系统命令。命令参数全部来自列表，不用 shell。"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from egress_switcher.errors import ApplyError
from egress_switcher.validate import (
    require_unique,
    validate_device,
    validate_ipv4,
    validate_label,
    validate_metric,
)

Runner = Callable[[list[str]], "Completed"]


class Completed:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def validate_payload(payload: dict) -> None:
    if not isinstance(payload, dict):
        raise ValueError("计划格式不正确")
    if payload.get("version") != 1:
        raise ValueError("不认识的计划版本")
    platform = payload.get("platform")
    action = payload.get("action")
    if platform == "windows" and action == "set_interface_metrics":
        items = payload.get("interfaces")
        if not isinstance(items, list) or not items:
            raise ValueError("没有要修改的 Windows 接口")
        aliases = []
        for item in items:
            alias = validate_label(str(item.get("alias") or ""), "接口")
            automatic = item.get("automatic")
            if not isinstance(automatic, bool):
                raise ValueError(f"「{alias}」的自动跃点标志不正确")
            validate_metric(int(item.get("metric")))
            item["alias"] = alias
            aliases.append(alias)
        require_unique(aliases, "接口")
        return
    if platform == "macos" and action == "order_services":
        order = payload.get("order")
        if not isinstance(order, list) or not order:
            raise ValueError("没有网络服务顺序")
        names = [validate_label(str(name), "网络服务") for name in order]
        require_unique(names, "网络服务")
        payload["order"] = names
        return
    if platform == "linux" and action == "linux_egress":
        nm_items = payload.get("nm") or []
        routes = payload.get("routes") or []
        if not isinstance(nm_items, list) or not isinstance(routes, list):
            raise ValueError("Linux 计划格式不正确")
        if not nm_items and not routes:
            raise ValueError("Linux 计划是空的")
        names = []
        for item in nm_items:
            name = validate_label(str(item.get("name") or ""), "连接")
            device = str(item.get("device") or "")
            if device:
                validate_device(device)
            metric = validate_metric(int(item.get("metric")), allow_auto=True)
            item["name"] = name
            item["metric"] = metric
            names.append(name)
        require_unique(names, "连接")
        devices = []
        for item in routes:
            device = validate_device(str(item.get("dev") or ""))
            via = validate_ipv4(str(item.get("via") or ""))
            metric = validate_metric(int(item.get("metric")))
            item["dev"] = device
            item["via"] = via
            item["metric"] = metric
            devices.append(device)
        require_unique(devices, "路由网卡")
        return
    raise ValueError("不支持的计划")


def build_powershell(interfaces: list[dict]) -> str:
    validate_payload({"version": 1, "platform": "windows", "action": "set_interface_metrics", "interfaces": interfaces})
    lines = ["$ErrorActionPreference = 'Stop'"]
    for item in interfaces:
        alias = "'" + item["alias"].replace("'", "''") + "'"
        if item["automatic"]:
            lines.append(
                "Set-NetIPInterface -AddressFamily IPv4 "
                f"-InterfaceAlias {alias} -AutomaticMetric Enabled"
            )
        else:
            lines.append(
                "Set-NetIPInterface -AddressFamily IPv4 "
                f"-InterfaceAlias {alias} -AutomaticMetric Disabled -InterfaceMetric {int(item['metric'])}"
            )
    return "\n".join(lines)


def build_netsh(interfaces: list[dict]) -> list[list[str]]:
    validate_payload({"version": 1, "platform": "windows", "action": "set_interface_metrics", "interfaces": interfaces})
    commands = []
    for item in interfaces:
        metric = "auto" if item["automatic"] else str(int(item["metric"]))
        commands.append(
            [
                "netsh",
                "interface",
                "ipv4",
                "set",
                "interface",
                f"interface={item['alias']}",
                f"metric={metric}",
            ]
        )
    return commands


def build_commands(payload: dict) -> list[list[str]]:
    validate_payload(payload)
    if payload["platform"] == "windows":
        script = build_powershell(payload["interfaces"])
        return [["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]]
    if payload["platform"] == "macos":
        return [["networksetup", "-ordernetworkservices", *payload["order"]]]
    commands: list[list[str]] = []
    devices: list[str] = []
    for item in payload.get("nm") or []:
        commands.append(
            ["nmcli", "connection", "modify", item["name"], "ipv4.route-metric", str(item["metric"])]
        )
        if item.get("device"):
            devices.append(item["device"])
    for device in devices:
        commands.append(["nmcli", "device", "reapply", device])
    for item in payload.get("routes") or []:
        commands.append(
            ["ip", "route", "replace", "default", "via", item["via"], "dev", item["dev"], "metric", str(item["metric"])]
        )
    return commands


def apply_payload(payload: dict, run: Runner) -> None:
    validate_payload(payload)
    if payload["platform"] == "windows":
        _apply_windows(payload, run)
        return
    errors: list[str] = []
    for command in build_commands(payload):
        result = run(command)
        if result.returncode != 0:
            errors.append(_failure_text(command, result))
    if errors:
        raise ApplyError("修改没有完成，系统可能只改了一部分。可以再执行一次恢复。\n" + "\n".join(errors))


def apply_plan_file(plan_path: Path, result_path: Path, run: Runner | None = None) -> int:
    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
        validate_payload(payload)
        apply_payload(payload, run or _subprocess_run)
    except Exception as exc:  # noqa: BLE001 - 结果文件要带上给界面看的原因
        result = {"ok": False, "message": str(exc)}
        code = 1
    else:
        result = {"ok": True, "message": "已按预览修改 IPv4 出口。"}
        code = 0
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return code


def _apply_windows(payload: dict, run: Runner) -> None:
    script = build_powershell(payload["interfaces"])
    result = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script])
    if result.returncode == 0:
        return
    errors = [_failure_text(["powershell.exe", "-Command", "Set-NetIPInterface"], result)]
    for command in build_netsh(payload["interfaces"]):
        fallback = run(command)
        if fallback.returncode != 0:
            errors.append(_failure_text(command, fallback))
            raise ApplyError("Set-NetIPInterface 和 netsh 都失败了，系统应保持原样或只改到失败前的一步。\n" + "\n".join(errors))


def _failure_text(command: list[str], result: Completed) -> str:
    detail = (result.stderr or result.stdout or "").strip()
    head = " ".join(command[:6])
    return f"{head} 退出码 {result.returncode}" + (f"：{detail}" if detail else "")


def _subprocess_run(command: list[str]) -> Completed:
    import subprocess

    from egress_switcher.textutil import decode_output

    try:
        completed = subprocess.run(command, capture_output=True, timeout=60, check=False)
    except OSError as exc:
        return Completed(127, "", str(exc))
    return Completed(completed.returncode, decode_output(completed.stdout), decode_output(completed.stderr))
