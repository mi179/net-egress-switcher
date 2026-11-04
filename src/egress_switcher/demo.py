"""图形界面和 CLI 的演示数据来自同一份录制输出。"""

from __future__ import annotations

from pathlib import Path

from egress_switcher.models import SystemState
from egress_switcher.windows import parse_windows_text

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def read_fixture(*parts: str) -> str:
    return FIXTURES.joinpath(*parts).read_text(encoding="utf-8")


def load_demo_state() -> SystemState:
    state = parse_windows_text(
        read_fixture("windows", "route_print.txt"),
        read_fixture("windows", "get_netipinterface.txt"),
        read_fixture("windows", "get_netipaddress.txt"),
        read_fixture("windows", "get_netipaddress6.txt"),
    )
    state.source = "demo"
    internet = {
        "以太网": True,
        "Wi-Fi": True,
        "以太网 2": True,
        "Meta": True,
        "vEthernet (Default Switch)": False,
        "VMware Network Adapter VMnet8": False,
    }
    for iface in state.interfaces:
        if iface.name in internet:
            iface.has_internet = internet[iface.name]
    state.notes.insert(0, "演示数据：录制的 Windows 样例，不是本机网卡。联网结果也是样例。")
    return state
