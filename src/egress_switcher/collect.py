"""调用系统自带命令收集网卡。测试可以换掉 runner，不碰真机。"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass

from egress_switcher.errors import CommandNotFound
from egress_switcher.linux import parse_linux
from egress_switcher.macos import parse_macos
from egress_switcher.models import SystemState
from egress_switcher.textutil import decode_output
from egress_switcher.windows import POWERSHELL_QUERY, parse_windows_json


@dataclass
class CmdResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


class SubprocessRunner:
    def __call__(self, args: list[str], timeout: float = 20) -> CmdResult:
        try:
            completed = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
        except FileNotFoundError as exc:
            raise CommandNotFound(f"找不到命令：{args[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise CommandNotFound(f"命令超时：{args[0]}") from exc
        return CmdResult(list(args), completed.returncode, decode_output(completed.stdout), decode_output(completed.stderr))


def collect_state(runner: SubprocessRunner | None = None) -> SystemState:
    run = runner or SubprocessRunner()
    if sys.platform == "win32":
        return collect_windows(run)
    if sys.platform == "darwin":
        return collect_macos(run)
    return collect_linux(run)


def collect_windows(run: SubprocessRunner) -> SystemState:
    """只读 PowerShell 的 ConvertTo-Json。不解析 route print：中文 Windows 会把表头本地化。"""
    powershell = shutil.which("powershell.exe") or shutil.which("powershell") or "powershell.exe"
    result = run([powershell, "-NoProfile", "-NonInteractive", "-Command", POWERSHELL_QUERY])
    if result.returncode != 0 or not result.stdout.strip():
        detail = result.stderr.strip() or "PowerShell 没有返回 JSON"
        raise CommandNotFound(f"读不到 Windows 网卡（ConvertTo-Json）。{detail}")
    try:
        state = parse_windows_json(result.stdout)
    except ValueError as exc:
        raise CommandNotFound(f"Windows 网卡 JSON 无法解析：{exc}") from exc
    state.source = "windows-live"
    return state


def collect_macos(run: SubprocessRunner) -> SystemState:
    order = run(["networksetup", "-listnetworkserviceorder"])
    netstat = run(["netstat", "-rn", "-f", "inet"])
    route_get = run(["route", "-n", "get", "default"])
    if order.returncode != 0:
        raise CommandNotFound("读不到 macOS 网络服务顺序。")
    services = []
    from egress_switcher.macos import parse_service_order

    blocks = ["# collected"]
    for service in parse_service_order(order.stdout):
        services.append(service["name"])
        info = run(["networksetup", "-getinfo", service["name"]])
        blocks.append(f"=== {service['name']} ===")
        blocks.append(info.stdout)
    state = parse_macos(
        order.stdout,
        "\n".join(blocks),
        netstat.stdout,
        route_get.stdout,
        _optional_stdout(run, ["route", "-n", "get", "1.1.1.1"]),
        _optional_stdout(run, ["ifconfig", "-a"]),
        _optional_stdout(run, ["ps", "-ax", "-o", "comm="]),
    )
    state.source = "macos-live"
    return state


def collect_linux(run: SubprocessRunner) -> SystemState:
    route = run(["ip", "-4", "route", "show"])
    addr = run(["ip", "-4", "addr", "show"])
    addr6 = run(["ip", "-6", "addr", "show"])
    if route.returncode != 0 or addr.returncode != 0:
        raise CommandNotFound("读不到 ip route / ip addr。请安装 iproute2。")
    devices = ""
    metrics = ""
    try:
        device_result = run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"])
        metric_result = run(["nmcli", "-t", "-f", "NAME,ipv4.route-metric", "connection", "show"])
    except CommandNotFound:
        device_result = None
        metric_result = None
    if device_result is not None and device_result.returncode == 0:
        devices = device_result.stdout
    if metric_result is not None and metric_result.returncode == 0:
        metrics = metric_result.stdout
    state = parse_linux(
        route.stdout,
        addr.stdout,
        addr6.stdout,
        devices,
        metrics,
        _optional_stdout(run, ["ip", "route", "get", "1.1.1.1"]),
        _optional_stdout(run, ["ps", "-e", "-o", "comm="]),
    )
    state.source = "linux-live"
    return state


def _optional_stdout(run: SubprocessRunner, args: list[str]) -> str:
    try:
        result = run(args)
    except CommandNotFound:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout
