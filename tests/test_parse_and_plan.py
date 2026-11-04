import json
import unittest

from egress_switcher.apply import (
    Completed,
    apply_payload,
    build_commands,
    build_netsh,
    build_powershell,
    validate_payload,
)
from egress_switcher.demo import load_demo_state, read_fixture
from egress_switcher.elevate import applescript_admin, windows_shell_execute_params, write_helper_script
from egress_switcher.linux import parse_linux
from egress_switcher.macos import parse_macos
from egress_switcher.models import InterfaceKind
from egress_switcher.planner import (
    assert_payload_safe,
    plan_automatic,
    plan_restore,
    plan_set,
    resolve_target,
    snapshot_from_state,
)
from egress_switcher.windows import POWERSHELL_QUERY, parse_windows_json, parse_windows_text


def windows_state():
    return parse_windows_text(
        read_fixture("windows", "route_print.txt"),
        read_fixture("windows", "get_netipinterface.txt"),
        read_fixture("windows", "get_netipaddress.txt"),
        read_fixture("windows", "get_netipaddress6.txt"),
    )


def macos_state():
    return parse_macos(
        read_fixture("macos", "networksetup_order.txt"),
        read_fixture("macos", "networksetup_getinfo.txt"),
        read_fixture("macos", "netstat_rn.txt"),
        read_fixture("macos", "route_get_default.txt"),
    )


def linux_state():
    return parse_linux(
        read_fixture("linux", "ip_route.txt"),
        read_fixture("linux", "ip_addr.txt"),
        read_fixture("linux", "ip_addr6.txt"),
        read_fixture("linux", "nmcli_devices.txt"),
        read_fixture("linux", "nmcli_metrics.txt"),
    )


class ParseTests(unittest.TestCase):
    def test_windows_text_and_json_agree(self):
        text_state = windows_state()
        json_state = parse_windows_json(read_fixture("windows", "get_netipinterface.json"))
        self.assertEqual(
            [(item.name, item.kind, item.metric, item.gateway, item.is_default, item.selectable) for item in text_state.interfaces],
            [(item.name, item.kind, item.metric, item.gateway, item.is_default, item.selectable) for item in json_state.interfaces],
        )

    def test_windows_fixture_labels_tun_virtual_and_tether(self):
        state = windows_state()
        kinds = {item.name: item.kind for item in state.interfaces}
        self.assertEqual(kinds["以太网"], InterfaceKind.WIRED)
        self.assertEqual(kinds["Wi-Fi"], InterfaceKind.WIFI)
        self.assertEqual(kinds["以太网 2"], InterfaceKind.USB_TETHER)
        self.assertEqual(kinds["以太网 3"], InterfaceKind.WIRED)
        self.assertEqual(kinds["vEthernet (Default Switch)"], InterfaceKind.VIRTUAL)
        self.assertEqual(kinds["VMware Network Adapter VMnet8"], InterfaceKind.VIRTUAL)
        self.assertEqual(kinds["Meta"], InterfaceKind.VPN_TUN)
        self.assertEqual(state.default_interface().name, "Meta")
        self.assertEqual(state.physical_preferred().name, "以太网")
        wifi = state.by_name("Wi-Fi")
        self.assertEqual(wifi.ipv4, ["10.20.30.15"])
        self.assertEqual(wifi.gateway, "10.20.30.1")
        self.assertTrue(wifi.automatic_metric)
        self.assertFalse(state.by_name("以太网 3").selectable)
        ethernet = state.by_name("以太网")
        self.assertIn("2408:4000:1234::50", ethernet.ipv6)
        self.assertTrue(any(item.startswith("fe80:") for item in ethernet.ipv6))
        self.assertNotIn("%", "".join(ethernet.ipv6))

    def test_powershell_query_uses_native_cmdlets(self):
        self.assertIn("Get-NetIPInterface", POWERSHELL_QUERY)
        self.assertIn("Get-NetRoute", POWERSHELL_QUERY)
        self.assertIn("Get-NetAdapter", POWERSHELL_QUERY)
        self.assertIn("ConvertTo-Json", POWERSHELL_QUERY)
        self.assertIn("GetBestRoute2", POWERSHELL_QUERY)
        self.assertIn("8.8.8.8", POWERSHELL_QUERY)
        self.assertNotIn("Set-NetIPInterface", POWERSHELL_QUERY)
        self.assertNotIn("Remove-NetRoute", POWERSHELL_QUERY)

    def test_single_object_json_is_accepted(self):
        payload = {
            "interfaces": {"InterfaceIndex": 12, "InterfaceAlias": "Wi-Fi", "InterfaceMetric": 50, "AutomaticMetric": "Enabled", "ConnectionState": "Connected"},
            "addresses": {"InterfaceIndex": 12, "IPAddress": "10.0.0.8"},
            "routes": {"InterfaceIndex": 12, "NextHop": "10.0.0.1", "RouteMetric": 0, "InterfaceMetric": 50},
            "adapters": {"Name": "Wi-Fi", "InterfaceDescription": "Intel Wi-Fi", "Virtual": False},
        }
        state = parse_windows_json(json.dumps(payload))
        self.assertEqual(state.by_name("Wi-Fi").kind, InterfaceKind.WIFI)
        self.assertTrue(state.by_name("Wi-Fi").is_default)

    def test_macos_service_order_and_tun(self):
        state = macos_state()
        self.assertEqual(
            state.service_order,
            ["Ethernet", "Wi-Fi", "iPhone USB", "Thunderbolt Bridge", "Shadowrocket", "USB 10/100/1000 LAN"],
        )
        kinds = {item.name: item.kind for item in state.interfaces}
        self.assertEqual(kinds["Ethernet"], InterfaceKind.WIRED)
        self.assertEqual(kinds["Wi-Fi"], InterfaceKind.WIFI)
        self.assertEqual(kinds["iPhone USB"], InterfaceKind.USB_TETHER)
        self.assertEqual(kinds["Thunderbolt Bridge"], InterfaceKind.VIRTUAL)
        self.assertEqual(kinds["Shadowrocket"], InterfaceKind.VPN_TUN)
        self.assertEqual(kinds["USB 10/100/1000 LAN"], InterfaceKind.WIRED)
        self.assertEqual(state.default_interface().name, "Shadowrocket")
        self.assertEqual(state.default_interface().device, "utun4")
        self.assertEqual(state.physical_preferred().name, "Ethernet")
        self.assertFalse(state.by_name("USB 10/100/1000 LAN").selectable)
        self.assertEqual(state.by_name("Wi-Fi").device, "en1")
        self.assertIn("2408:4000:1234::50", state.by_name("Ethernet").ipv6)

    def test_linux_nm_and_ip_route(self):
        state = linux_state()
        kinds = {item.name: item.kind for item in state.interfaces}
        self.assertEqual(kinds["enp3s0"], InterfaceKind.WIRED)
        self.assertEqual(kinds["wlp2s0"], InterfaceKind.WIFI)
        self.assertEqual(kinds["usb0"], InterfaceKind.USB_TETHER)
        self.assertEqual(kinds["Meta"], InterfaceKind.VPN_TUN)
        self.assertEqual(kinds["virbr0"], InterfaceKind.VIRTUAL)
        self.assertEqual(kinds["vmnet8"], InterfaceKind.VIRTUAL)
        self.assertEqual(state.default_interface().name, "Meta")
        self.assertEqual(state.physical_preferred().name, "enp3s0")
        self.assertEqual(state.by_name("wlp2s0").nm_connection, "Campus-WiFi")
        self.assertEqual(state.by_name("wlp2s0").configured_metric, 600)
        self.assertEqual(state.by_name("enp3s0").configured_metric, -1)
        self.assertEqual(state.by_name("enp3s0").metric, 100)
        self.assertIn("2408:4000:1234::50", state.by_name("enp3s0").ipv6)
        self.assertTrue(state.nm_available)
        self.assertNotIn("lo", [item.name for item in state.interfaces])

    def test_linux_default_without_metric_still_wins(self):
        state = parse_linux(
            "default via 10.0.0.1 dev eth0 proto dhcp\n",
            "2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 state UP\n    inet 10.0.0.8/24 scope global eth0\n",
        )
        self.assertEqual(state.default_interface().name, "eth0")
        self.assertEqual(state.by_name("eth0").metric, 0)
        self.assertTrue(state.by_name("eth0").is_physical_preferred)

    def test_linux_without_networkmanager_uses_ip_route_only(self):
        state = parse_linux(read_fixture("linux", "ip_route.txt"), read_fixture("linux", "ip_addr.txt"))
        self.assertFalse(state.nm_available)
        self.assertIsNone(state.by_name("wlp2s0").nm_connection)
        plan = plan_set(state, state.by_name("wlp2s0"))
        self.assertIsNone(plan.error, plan.preview_text())
        self.assertEqual(plan.payload["nm"], [])
        devices = [item["dev"] for item in plan.payload["routes"]]
        self.assertEqual(devices[0], "wlp2s0")
        self.assertNotIn("Meta", devices)
        self.assertNotIn("vmnet8", devices)
        self.assertIn("ip route", plan.preview_text())


class PlanTests(unittest.TestCase):
    def test_windows_set_wifi_changes_only_physical_metrics(self):
        state = windows_state()
        plan = plan_set(state, resolve_target(state, "Wi-Fi"))
        self.assertIsNone(plan.error, plan.preview_text())
        aliases = [item["alias"] for item in plan.payload["interfaces"]]
        self.assertEqual(aliases, ["Wi-Fi", "以太网", "以太网 2"])
        self.assertEqual(plan.payload["interfaces"][0]["metric"], 1)
        self.assertFalse(plan.payload["interfaces"][0]["automatic"])
        preview = plan.preview_text()
        self.assertIn("50（自动） → 1（手动）", preview)
        self.assertIn("25（自动） → 20（手动）", preview)
        self.assertIn("35（自动） → 30（手动）", preview)
        self.assertIn("不修改：", preview)
        self.assertIn("Meta", preview)
        self.assertIn("VMware Network Adapter VMnet8", preview)
        self.assertIn("仍会进入该 TUN", preview)
        self.assertIn("只调整 IPv4", preview)
        assert_payload_safe(plan.payload)

    def test_windows_automatic_and_restore(self):
        state = windows_state()
        automatic = plan_automatic(state, None)
        self.assertTrue(automatic.noop)
        saved = snapshot_from_state(state)
        saved["windows"][1]["metric"] = 5
        saved["windows"][1]["automatic"] = False
        restore = plan_restore(state, saved)
        wifi = next(item for item in restore.payload["interfaces"] if item["alias"] == "Wi-Fi")
        self.assertEqual(wifi["metric"], 5)
        self.assertFalse(wifi["automatic"])
        self.assertNotIn("Meta", [item["alias"] for item in restore.payload["interfaces"]])

    def test_macos_reorders_services_without_dropping_vpn(self):
        state = macos_state()
        plan = plan_set(state, resolve_target(state, "en1"))
        self.assertEqual(plan.target, "Wi-Fi")
        self.assertEqual(plan.payload["order"][0], "Wi-Fi")
        self.assertEqual(plan.payload["order"][1], "Ethernet")
        self.assertIn("Shadowrocket", plan.payload["order"])
        self.assertIn("USB 10/100/1000 LAN", plan.payload["order"])
        self.assertIn("networksetup -ordernetworkservices", plan.preview_text())
        self.assertIn("utun", plan.preview_text())
        command = build_commands(plan.payload)[0]
        self.assertEqual(command[0], "networksetup")
        self.assertEqual(command[1], "-ordernetworkservices")
        self.assertEqual(command[2], "Wi-Fi")
        already = plan_set(state, state.by_name("Ethernet"))
        self.assertTrue(already.noop)
        self.assertIsNone(plan_automatic(state, None).payload)

    def test_linux_nm_plan_leaves_tun_metric_alone(self):
        state = linux_state()
        plan = plan_set(state, resolve_target(state, "Campus-WiFi"))
        self.assertEqual(plan.target, "wlp2s0")
        metrics = {item["name"]: item["metric"] for item in plan.payload["nm"]}
        self.assertEqual(metrics["Campus-WiFi"], 50)
        self.assertEqual(metrics["Wired connection 1"], 200)
        self.assertEqual(metrics["Wired connection 2"], 300)
        self.assertNotIn("Meta", metrics)
        self.assertEqual(plan.payload["routes"], [])
        preview = plan.preview_text()
        self.assertIn("600 → 50", preview)
        self.assertIn("自动", preview)
        self.assertIn("重新应用", preview)
        self.assertIn("Meta", preview)
        commands = build_commands(plan.payload)
        self.assertIn(["nmcli", "connection", "modify", "Campus-WiFi", "ipv4.route-metric", "50"], commands)
        self.assertIn(["nmcli", "device", "reapply", "wlp2s0"], commands)
        self.assertTrue(all(command[0] in {"nmcli", "ip"} for command in commands))

    def test_linux_automatic_resets_manual_metric_only(self):
        state = linux_state()
        plan = plan_automatic(state, None)
        self.assertEqual([item["name"] for item in plan.payload["nm"]], ["Campus-WiFi"])
        self.assertEqual(plan.payload["nm"][0]["metric"], -1)
        self.assertNotIn("Meta", plan.preview_text().split("不修改：", 1)[0])

    def test_refuses_tun_and_virtual_as_egress(self):
        for state, name in (
            (windows_state(), "Meta"),
            (windows_state(), "VMware Network Adapter VMnet8"),
            (macos_state(), "Shadowrocket"),
            (linux_state(), "Meta"),
            (linux_state(), "virbr0"),
        ):
            plan = plan_set(state, resolve_target(state, name))
            self.assertIsNotNone(plan.error, name)
            self.assertIsNone(plan.payload)

    def test_demo_state_is_the_recorded_windows_sample(self):
        state = load_demo_state()
        self.assertEqual(state.source, "demo")
        self.assertEqual(state.default_interface().name, "Meta")
        self.assertTrue(state.by_name("Wi-Fi").has_internet)


class ApplySafetyTests(unittest.TestCase):
    def test_rejects_shell_metacharacters(self):
        bad_payloads = [
            {"version": 1, "platform": "windows", "action": "set_interface_metrics", "interfaces": [{"alias": "Wi-Fi; calc", "automatic": False, "metric": 1}]},
            {"version": 1, "platform": "windows", "action": "set_interface_metrics", "interfaces": [{"alias": "Wi-Fi", "automatic": False, "metric": "1; calc"}]},
            {"version": 1, "platform": "macos", "action": "order_services", "order": ["Wi-Fi\nEthernet"]},
            {"version": 1, "platform": "linux", "action": "linux_egress", "nm": [{"name": "ok", "device": "eth0;rm", "metric": 50}], "routes": []},
            {"version": 1, "platform": "linux", "action": "linux_egress", "nm": [], "routes": [{"dev": "eth0", "via": "1.2.3.4;id", "metric": 10}]},
            {"version": 1, "platform": "linux", "action": "run", "command": "rm -rf /"},
        ]
        for payload in bad_payloads:
            with self.assertRaises((ValueError, TypeError)):
                validate_payload(payload)

    def test_powershell_quotes_alias_and_netsh_is_argv(self):
        interfaces = [
            {"alias": "以太网 2", "automatic": False, "metric": 30},
            {"alias": "Wi-Fi", "automatic": True, "metric": 50},
        ]
        script = build_powershell(interfaces)
        self.assertIn("-InterfaceAlias '以太网 2'", script)
        self.assertIn("-InterfaceMetric 30", script)
        self.assertIn("-AutomaticMetric Enabled", script)
        self.assertNotIn(";", script)
        netsh = build_netsh(interfaces)
        self.assertEqual(netsh[0][0], "netsh")
        self.assertEqual(netsh[0][-2], "interface=以太网 2")
        self.assertEqual(netsh[1][-1], "metric=auto")

    def test_windows_falls_back_to_netsh_without_shell(self):
        payload = {
            "version": 1,
            "platform": "windows",
            "action": "set_interface_metrics",
            "interfaces": [{"alias": "Wi-Fi", "automatic": False, "metric": 1}],
        }
        calls = []

        def run(command):
            calls.append(command)
            if command[0] == "powershell.exe":
                return Completed(1, "", "The term is not recognized")
            return Completed(0)

        apply_payload(payload, run)
        self.assertEqual(calls[0][0], "powershell.exe")
        self.assertEqual(calls[0][1:4], ["-NoProfile", "-NonInteractive", "-Command"])
        self.assertEqual(calls[1][0], "netsh")
        self.assertTrue(all(isinstance(part, str) for command in calls for part in command))

    def test_helper_script_and_applescript_do_not_split_paths(self):
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "apply.sh"
            write_helper_script(path, ["python3", "-m", "egress_switcher", "apply", "--plan", "/tmp/a;rm.json"])
            body = path.read_text(encoding="utf-8")
            self.assertIn("'/tmp/a;rm.json'", body)
            self.assertTrue(body.startswith("#!/bin/sh\nexec "))
        script = applescript_admin("/bin/sh '/tmp/my plan.sh'")
        self.assertEqual(script, 'do shell script "/bin/sh \'/tmp/my plan.sh\'" with administrator privileges')
        executable, params = windows_shell_execute_params(["C:\\Program Files\\python.exe", "-m", "egress_switcher", "apply", "--plan", "C:\\a b.json"])
        self.assertIn("Program Files", executable)
        self.assertIn('"C:\\a b.json"', params)
