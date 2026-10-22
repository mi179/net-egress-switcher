"""窗口版入口。打包后的 GUI 如果被提权流程叫来执行 apply，就转给命令行。"""

import sys


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in {
        "apply",
        "list",
        "set",
        "restore",
        "automatic",
        "--help",
        "-h",
        "--version",
    }:
        from egress_switcher.cli import main as cli_main

        return cli_main()
    from egress_switcher.dpi import enable_per_monitor_v2

    enable_per_monitor_v2()
    from egress_switcher.gui import launch

    return launch()


if __name__ == "__main__":
    raise SystemExit(main())
