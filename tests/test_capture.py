import json
import unittest

from egress_switcher.capture import PREVIEW_NOTE
from egress_switcher.cli import format_table
from egress_switcher.demo import read_fixture
from egress_switcher.linux import parse_linux
from egress_switcher.macos import parse_macos
from egress_switcher.models import InterfaceKind
from egress_switcher.planner import plan_set, resolve_target
from egress_switcher.windows import parse_windows_json

BANNER = "⚠️ 现在流量被 {title} 的虚拟网卡接管，切换出口不会生效。"


def macos_split(process_text: str = ""):
    return parse_macos(
        read_fixture("macos", "split_order.txt"),
        read_fixture("macos", "split_getinfo.txt"),
        read_fixture("macos", "split_netstat.txt"),
        read_fixture("macos", "split_route_default.txt"),
        read_fixture("macos", "split_route_host.txt"),
        read_fixture("macos", "split_ifconfig.txt"),
        process_text,
    )


def linux_split(process_text: str = ""):
    return parse_linux(
        read_fixture("linux", "ip_route_split.txt"),
        read_fixture("linux", "ip_addr_split.txt"),
        "",
        "",
        "",
        read_fixture("linux", "ip_route_get_split.txt"),
        process_text,
    )


class TunTakeoverTests(unittest.TestCase):
    def test_macos_split_routes_are_the_real_egress(self):
        state = macos_split()
        self.assertEqual(state.default_interface().name, "utun1024")
        self.assertEqual(state.default_interface().device, "utun1024")
        self.assertEqual(state.default_interface().ipv4, ["198.18.0.1"])
        self.assertEqual(state.default_interface().gateway, "198.18.0.1")
        self.assertEqual(state.default_interface().kind, InterfaceKind.VPN_TUN)
        self.assertFalse(state.default_interface().selectable)
        self.assertEqual(state.by_name("Wi-Fi").device, "en0")
        self.assertFalse(state.by_name("Wi-Fi").is_default)
        self.assertEqual(state.physical_preferred().name, "Ethernet")
        self.assertIsNotNone(state.capture)
        self.assertEqual(state.capture.title, "utun1024")
        text = format_table(state)
        self.assertIn(BANNER.format(title="utun1024"), text)
        self.assertIn("sing-box 用 route.default_interface 或 auto_detect_interface", text)
        self.assertIn("mihomo 用 interface-name", text)
        self.assertIn("实际出口：utun1024", text)
        self.assertNotIn("当前默认路由", text)
        self.assertNotIn("当前默认路由：Wi-Fi", text)

    def test_macos_names_the_client_when_only_one_matches(self):
        named = macos_split("1234 /Applications/mihomo.app/Contents/MacOS/mihomo\n")
        self.assertEqual(named.capture.title, "mihomo（utun1024）")
        ambiguous = macos_split("mihomo\nsing-box\n")
        self.assertEqual(ambiguous.capture.title, "utun1024")

    def test_macos_set_preview_explains_and_does_not_block(self):
        state = macos_split()
        plan = plan_set(state, resolve_target(state, "Wi-Fi"))
        self.assertIsNone(plan.error, plan.preview_text())
        self.assertFalse(plan.noop)
        self.assertIn(PREVIEW_NOTE, plan.preview_text())
        self.assertEqual(plan.payload["action"], "order_services")
        self.assertEqual(plan.payload["order"][0], "Wi-Fi")
        self.assertNotIn("utun1024", plan.payload["order"])
        self.assertNotIn("route", json.dumps(plan.payload))

    def test_windows_sing_tun_split_routes(self):
        state = parse_windows_json(read_fixture("windows", "sing_tun.json"))
        self.assertEqual(state.default_interface().name, "sing-tun")
        self.assertEqual(state.default_interface().kind, InterfaceKind.VPN_TUN)
        self.assertFalse(state.by_name("Wi-Fi").is_default)
        self.assertEqual(state.physical_preferred().name, "以太网")
        self.assertEqual(state.capture.title, "sing-box（sing-tun）")
        self.assertEqual(state.by_name("sing-tun").ipv4, ["198.18.0.1"])
        text = format_table(state)
        self.assertIn(BANNER.format(title="sing-box（sing-tun）"), text)
        self.assertIn("实际出口：sing-tun", text)
        self.assertNotIn("当前默认路由", text)
        plan = plan_set(state, resolve_target(state, "Wi-Fi"))
        self.assertIsNone(plan.error, plan.preview_text())
        self.assertIn(PREVIEW_NOTE, plan.preview_text())
        aliases = [item["alias"] for item in plan.payload["interfaces"]]
        self.assertIn("Wi-Fi", aliases)
        self.assertNotIn("sing-tun", aliases)
        self.assertEqual(plan.payload["action"], "set_interface_metrics")
        self.assertNotIn("routes", plan.payload)

    def test_windows_onlink_halves_still_capture(self):
        payload = {
            "interfaces": [
                {"InterfaceIndex": 12, "InterfaceAlias": "Wi-Fi", "InterfaceMetric": 25, "AutomaticMetric": "Enabled", "ConnectionState": "Connected"},
                {"InterfaceIndex": 40, "InterfaceAlias": "sing-tun", "InterfaceMetric": 0, "AutomaticMetric": "Disabled", "ConnectionState": "Connected"},
            ],
            "addresses": [
                {"InterfaceIndex": 12, "IPAddress": "192.168.0.20"},
                {"InterfaceIndex": 40, "IPAddress": "198.18.0.1"},
            ],
            "routes": [
                {"InterfaceIndex": 12, "InterfaceAlias": "Wi-Fi", "NextHop": "192.168.0.1", "RouteMetric": 0, "InterfaceMetric": 25, "DestinationPrefix": "0.0.0.0/0"},
                {"InterfaceIndex": 40, "InterfaceAlias": "sing-tun", "NextHop": "0.0.0.0", "RouteMetric": 0, "InterfaceMetric": 0, "DestinationPrefix": "0.0.0.0/1"},
                {"InterfaceIndex": 40, "InterfaceAlias": "sing-tun", "NextHop": "0.0.0.0", "RouteMetric": 0, "InterfaceMetric": 0, "DestinationPrefix": "128.0.0.0/1"},
            ],
            "adapters": [
                {"Name": "Wi-Fi", "InterfaceDescription": "Intel Wi-Fi", "Virtual": False},
                {"Name": "sing-tun", "InterfaceDescription": "Wintun", "Virtual": True},
            ],
            "best": {"InterfaceIndex": 40},
        }
        state = parse_windows_json(json.dumps(payload))
        self.assertEqual(state.default_interface().name, "sing-tun")
        self.assertEqual(state.by_name("Wi-Fi").gateway, "192.168.0.1")
        self.assertIsNone(state.by_name("sing-tun").gateway)
        self.assertEqual(state.capture.device, "sing-tun")
        self.assertIn(PREVIEW_NOTE, plan_set(state, resolve_target(state, "Wi-Fi")).preview_text())

    def test_linux_split_routes_follow_ip_route_get(self):
        state = linux_split()
        self.assertEqual(state.default_interface().name, "utun1024")
        self.assertEqual(state.default_interface().ipv4, ["198.18.0.1"])
        self.assertEqual(state.default_interface().gateway, "198.18.0.1")
        self.assertFalse(state.by_name("eth0").is_default)
        self.assertEqual(state.physical_preferred().name, "eth0")
        self.assertEqual(state.capture.title, "utun1024")
        text = format_table(state)
        self.assertIn(BANNER.format(title="utun1024"), text)
        self.assertIn("实际出口：utun1024", text)
        self.assertNotIn("当前默认路由", text)
        named = linux_split("mihomo\n")
        self.assertEqual(named.capture.title, "mihomo（utun1024）")
        plan = plan_set(state, resolve_target(state, "enp3s0"))
        self.assertIsNone(plan.error, plan.preview_text())
        self.assertIn(PREVIEW_NOTE, plan.preview_text())
        devices = [item["dev"] for item in plan.payload["routes"]]
        self.assertIn("enp3s0", devices)
        self.assertNotIn("utun1024", devices)
        self.assertEqual(plan.payload["nm"], [])

    def test_plain_physical_default_is_not_a_takeover(self):
        state = parse_linux(
            "default via 10.0.0.1 dev eth0 proto dhcp\n",
            "2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 state UP\n    inet 10.0.0.8/24 scope global eth0\n",
        )
        self.assertIsNone(state.capture)
        self.assertEqual(state.default_interface().name, "eth0")
        self.assertIn("实际出口：eth0", format_table(state))
        self.assertNotIn("接管", format_table(state))
