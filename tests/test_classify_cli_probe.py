import io
import shutil
import socket
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from egress_switcher.classify import classify
from egress_switcher.cli import main
from egress_switcher.collect import CmdResult, collect_linux
from egress_switcher.demo import read_fixture
from egress_switcher.models import Interface, InterfaceKind
from egress_switcher.probe import probe_interface
from egress_switcher.snapshot import ensure_snapshot, load_snapshot
from egress_switcher.textutil import decode_output
from egress_switcher.planner import snapshot_from_state
from egress_switcher.demo import load_demo_state


class ClassifyTests(unittest.TestCase):
    def test_names_from_the_three_systems(self):
        cases = [
            ({"name": "以太网", "description": "Realtek PCIe GbE"}, InterfaceKind.WIRED),
            ({"name": "Wi-Fi", "description": "Intel(R) Wi-Fi 6"}, InterfaceKind.WIFI),
            ({"name": "以太网 2", "description": "Remote NDIS based Internet Sharing Device"}, InterfaceKind.USB_TETHER),
            ({"name": "en5", "hardware_port": "iPhone USB"}, InterfaceKind.USB_TETHER),
            ({"name": "en6", "hardware_port": "USB 10/100/1000 LAN"}, InterfaceKind.WIRED),
            ({"name": "以太网 3", "description": "AX88179 USB 3.0 Gigabit"}, InterfaceKind.WIRED),
            ({"name": "vEthernet (Default Switch)", "description": "Hyper-V Virtual Ethernet Adapter"}, InterfaceKind.VIRTUAL),
            ({"name": "VMware Network Adapter VMnet8"}, InterfaceKind.VIRTUAL),
            ({"name": "Meta", "description": "Meta Tunnel"}, InterfaceKind.VPN_TUN),
            ({"name": "sing-box"}, InterfaceKind.VPN_TUN),
            ({"name": "utun4"}, InterfaceKind.VPN_TUN),
            ({"name": "wlp2s0", "nm_type": "wifi"}, InterfaceKind.WIFI),
            ({"name": "enp3s0", "nm_type": "ethernet"}, InterfaceKind.WIRED),
            ({"name": "usb0"}, InterfaceKind.USB_TETHER),
            ({"name": "virbr0"}, InterfaceKind.VIRTUAL),
            ({"name": "docker0"}, InterfaceKind.VIRTUAL),
            ({"name": "lo"}, InterfaceKind.LOOPBACK),
            ({"name": "Bluetooth Network Connection"}, InterfaceKind.VIRTUAL),
        ]
        for kwargs, kind in cases:
            found, _reason = classify(**kwargs)
            self.assertEqual(found, kind, kwargs)


class CliTests(unittest.TestCase):
    def test_demo_list_and_set_do_not_touch_the_system(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--demo", "list", "--no-probe"])
        self.assertEqual(code, 0)
        self.assertIn("Meta", stdout.getvalue())
        self.assertIn("USB 共享", stdout.getvalue())
        self.assertIn("当前默认路由", stdout.getvalue())

        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr), mock.patch("egress_switcher.elevate.request_apply") as apply:
            code = main(["--demo", "set", "Wi-Fi"])
            apply.assert_not_called()
        self.assertEqual(code, 3)
        self.assertIn("→ 1（手动）", stdout.getvalue())
        self.assertIn("加上 --yes", stderr.getvalue())

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), mock.patch("egress_switcher.elevate.request_apply") as apply:
            code = main(["--demo", "set", "Wi-Fi", "--yes"])
            apply.assert_not_called()
        self.assertEqual(code, 0)

    def test_demo_refuses_tun_and_json_plan_has_no_payload_execution(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            code = main(["--demo", "set", "Meta", "--json"])
        self.assertEqual(code, 2)
        self.assertIn("不能把", stdout.getvalue())
        self.assertIn('"payload": null', stdout.getvalue())


class ProbeTests(unittest.TestCase):
    def test_probe_binds_the_interface_address_and_stops_on_success(self):
        calls = []

        class FakeSocket:
            def settimeout(self, _timeout):
                calls.append("timeout")

            def setsockopt(self, *_args):
                calls.append("opt")

            def bind(self, address):
                calls.append(("bind", address))

            def connect(self, address):
                calls.append(("connect", address))
                if address[0] != "223.5.5.5":
                    raise OSError("nope")

            def close(self):
                calls.append("close")

        iface = Interface(name="enp3s0", device="enp3s0", kind=InterfaceKind.WIRED, ipv4=["192.168.1.50"], ifindex=2)
        with mock.patch("egress_switcher.probe.socket.socket", return_value=FakeSocket()):
            self.assertTrue(probe_interface(iface, timeout=0.2))
        self.assertIn(("bind", ("192.168.1.50", 0)), calls)
        self.assertIn(("connect", ("223.5.5.5", 443)), calls)
        self.assertNotIn(("connect", ("119.29.29.29", 443)), calls)
        self.assertIsInstance(socket.AF_INET, int)

    def test_decode_gbk_and_utf16(self):
        self.assertEqual(decode_output("以太网".encode("gbk")), "以太网")
        self.assertEqual(decode_output("以太网".encode("utf-16")), "以太网")


class SnapshotTests(unittest.TestCase):
    def test_snapshot_is_written_once(self):
        state = load_demo_state()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "original-state.json"
            ensure_snapshot(state, path)
            first = load_snapshot(path)
            state.by_name("Wi-Fi").metric = 1
            ensure_snapshot(state, path)
            second = load_snapshot(path)
        self.assertEqual(first, second)
        self.assertEqual(first["platform"], "windows")
        self.assertTrue(any(item["alias"] == "Wi-Fi" and item["metric"] == 50 for item in first["windows"]))
        self.assertFalse(any(item["alias"] == "Meta" for item in first["windows"]))
        self.assertEqual(snapshot_from_state(state)["platform"], "windows")


class CollectTests(unittest.TestCase):
    def test_linux_runner_wires_recorded_commands(self):
        outputs = {
            ("ip", "-4", "route", "show"): read_fixture("linux", "ip_route.txt"),
            ("ip", "-4", "addr", "show"): read_fixture("linux", "ip_addr.txt"),
            ("ip", "-6", "addr", "show"): read_fixture("linux", "ip_addr6.txt"),
            ("nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"): read_fixture("linux", "nmcli_devices.txt"),
            ("nmcli", "-t", "-f", "NAME,ipv4.route-metric", "connection", "show"): read_fixture("linux", "nmcli_metrics.txt"),
        }

        def run(args, timeout=20):
            return CmdResult(args, 0, outputs[tuple(args)], "")

        state = collect_linux(run)
        self.assertEqual(state.default_interface().name, "Meta")
        self.assertEqual(state.by_name("wlp2s0").nm_connection, "Campus-WiFi")

    def test_windows_collector_parses_powershell_json(self):
        payload = read_fixture("windows", "get_netipinterface.json")

        def run(args, timeout=20):
            if "Get-NetIPInterface" in " ".join(args):
                return CmdResult(list(args), 0, payload, "")
            raise AssertionError(args)

        from egress_switcher.collect import collect_windows

        state = collect_windows(run)
        self.assertEqual(state.default_interface().name, "Meta")
        self.assertEqual(state.by_name("以太网 2").kind.value, "usb_tether")

    def test_live_linux_collect_is_readonly(self):
        if not shutil.which("ip"):
            self.skipTest("没有 ip 命令")
        from egress_switcher.collect import collect_state

        state = collect_state()
        self.assertEqual(state.platform, "linux")
        self.assertIsInstance(state.interfaces, list)
