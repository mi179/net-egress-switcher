from __future__ import annotations

import argparse
import json
import sys

from egress_switcher import __version__
from egress_switcher.errors import EgressError
from egress_switcher.models import Interface, SystemState
from egress_switcher.planner import plan_automatic, plan_restore, plan_set, resolve_target
from egress_switcher.textutil import pad

AUTO_WORDS = {"auto", "automatic", "自动"}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(__version__)
        return 0
    if args.cmd in {None, "gui"}:
        from egress_switcher.gui import launch

        return launch(demo=args.demo, screenshot=args.screenshot, select=args.select)
    if args.cmd == "apply":
        from pathlib import Path

        from egress_switcher.apply import apply_plan_file

        return apply_plan_file(Path(args.plan), Path(args.result))
    try:
        return run_command(args)
    except EgressError as exc:
        print(str(exc), file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="egress-switcher",
        description="查看网卡，并在确认后切换 IPv4 默认出口。不加子命令会打开图形界面。",
    )
    parser.add_argument("--version", action="store_true", help="显示版本")
    parser.add_argument("--demo", action="store_true", help="使用录制的 Windows 样例，不读取也不修改本机")
    parser.add_argument("--screenshot", default="", help=argparse.SUPPRESS)
    parser.add_argument("--select", default="", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="cmd")

    list_cmd = sub.add_parser("list", help="列出网卡")
    list_cmd.add_argument("--json", action="store_true")
    list_cmd.add_argument("--no-probe", action="store_true", help="不探测互联网")

    set_cmd = sub.add_parser("set", help="预览并在 --yes 后把某张网卡设为出口")
    set_cmd.add_argument("name", help="网卡名、macOS 服务名或 NetworkManager 连接名")
    set_cmd.add_argument("--yes", action="store_true", help="确认执行预览里的修改")
    set_cmd.add_argument("--json", action="store_true")

    restore_cmd = sub.add_parser("restore", help="恢复到第一次修改前的状态")
    restore_cmd.add_argument("--yes", action="store_true")
    restore_cmd.add_argument("--json", action="store_true")

    automatic = sub.add_parser("automatic", help="交回系统默认策略")
    automatic.add_argument("--yes", action="store_true")
    automatic.add_argument("--json", action="store_true")

    sub.add_parser("gui", help="打开图形界面")
    apply_cmd = sub.add_parser("apply", help="内部命令：应用一份已经写好的计划")
    apply_cmd.add_argument("--plan", required=True)
    apply_cmd.add_argument("--result", required=True)
    return parser


def run_command(args: argparse.Namespace) -> int:
    state = load_state(args.demo)
    if args.cmd == "list" and not args.demo and not args.no_probe:
        from egress_switcher.probe import probe_all

        probe_all(state.interfaces)
    if args.cmd == "list":
        if args.json:
            print(json.dumps(state_to_json(state), ensure_ascii=False, indent=2))
        else:
            print(format_table(state))
            for note in state.notes:
                print(note)
            if state.capture is None:
                _print_tun(state)
        return 0

    snapshot = None if args.demo else _snapshot()
    if args.cmd == "set":
        if args.name.strip().casefold() in AUTO_WORDS:
            plan = plan_automatic(state, snapshot)
        else:
            plan = plan_set(state, resolve_target(state, args.name))
    elif args.cmd == "restore":
        plan = plan_restore(state, snapshot)
    elif args.cmd == "automatic":
        plan = plan_automatic(state, snapshot)
    else:
        print(f"未知命令：{args.cmd}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(_plan_json(plan), ensure_ascii=False, indent=2))
    else:
        print(plan.preview_text())
    if plan.error:
        return 2
    if plan.noop or plan.payload is None:
        return 0
    if not args.yes:
        print("\n以上只是预览。加上 --yes 才会修改系统。", file=sys.stderr)
        return 3
    if args.demo:
        print("演示模式不会修改系统。", file=sys.stderr)
        return 0
    from egress_switcher.elevate import request_apply
    from egress_switcher.snapshot import ensure_snapshot

    ensure_snapshot(state)
    result = request_apply(plan.payload)
    print(result.get("message", ""))
    return 0 if result.get("ok") else 1


def load_state(demo: bool) -> SystemState:
    if demo:
        from egress_switcher.demo import load_demo_state

        return load_demo_state()
    from egress_switcher.collect import collect_state

    return collect_state()


def format_table(state: SystemState) -> str:
    lines: list[str] = []
    if state.capture is not None:
        lines.append(state.capture.banner())
        lines.append("")
    rows = []
    for iface in state.interfaces:
        if iface.kind.value == "loopback":
            continue
        flags = []
        if iface.is_default:
            flags.append("实际出口")
        elif iface.is_physical_preferred:
            flags.append("物理优先")
        if not iface.selectable:
            flags.append("不可作为出口")
        rows.append(
            [
                iface.name,
                iface.kind_label(),
                ", ".join(iface.ipv4) or "—",
                iface.gateway or "—",
                iface.metric_text(),
                _internet_cell(iface),
                " ".join(flags),
            ]
        )
    headers = ["名称", "类型", "IPv4", "网关", "优先级", "互联网", "状态"]
    widths = [max(display_width(item) for item in [header, *(row[index] for row in rows)]) for index, header in enumerate(headers)] if rows else [display_width(item) for item in headers]
    lines.append("  ".join(pad(header, widths[index]) for index, header in enumerate(headers)))
    lines.append("  ".join("-" * width for width in widths))
    for row in rows:
        lines.append("  ".join(pad(cell, widths[index]) for index, cell in enumerate(row)))
    default = state.default_interface()
    if default:
        lines.append("")
        lines.append(f"实际出口：{default.name}（{default.kind_label()}）")
    return "\n".join(lines)


def _internet_cell(iface: Interface) -> str:
    if iface.has_internet is True:
        return "通"
    if iface.has_internet is False:
        return "不通"
    if iface.ipv4:
        return "未探测"
    return "—"


def display_width(text: str) -> int:
    from egress_switcher.textutil import display_width as width

    return width(text)


def state_to_json(state: SystemState) -> dict:
    return {
        "platform": state.platform,
        "source": state.source,
        "default": state.default_interface().name if state.default_interface() else None,
        "physical_preferred": state.physical_preferred().name if state.physical_preferred() else None,
        "tun": [iface.name for iface in state.tun_interfaces()],
        "capture": None
        if state.capture is None
        else {"device": state.capture.device, "title": state.capture.title, "banner": state.capture.banner()},
        "notes": state.notes,
        "interfaces": [_iface_json(iface) for iface in state.interfaces if iface.kind.value != "loopback"],
    }


def _iface_json(iface: Interface) -> dict:
    return {
        "name": iface.name,
        "device": iface.device,
        "kind": iface.kind.value,
        "kind_label": iface.kind_label(),
        "ipv4": iface.ipv4,
        "ipv6": iface.ipv6,
        "gateway": iface.gateway,
        "metric": iface.metric,
        "automatic_metric": iface.automatic_metric,
        "configured_metric": iface.configured_metric,
        "is_default": iface.is_default,
        "is_physical_preferred": iface.is_physical_preferred,
        "is_up": iface.is_up,
        "has_internet": iface.has_internet,
        "selectable": iface.selectable,
        "service_name": iface.service_name,
        "nm_connection": iface.nm_connection,
        "reason": iface.reason,
    }


def _plan_json(plan) -> dict:
    return {
        "action": plan.action,
        "target": plan.target,
        "noop": plan.noop,
        "error": plan.error,
        "changes": plan.changes,
        "warnings": plan.warnings,
        "payload": plan.payload,
    }


def _snapshot():
    from egress_switcher.snapshot import load_snapshot

    return load_snapshot()


def _print_tun(state: SystemState) -> None:
    tuns = state.tun_interfaces()
    if not tuns:
        return
    names = "、".join(
        f"{iface.name}（{iface.device}）" if iface.device and iface.device != iface.name else iface.name
        for iface in tuns
    )
    print()
    print(f"检测到 TUN/代理：{names}。它仍会接管普通程序的流量，切换物理出口不会关闭它。")
