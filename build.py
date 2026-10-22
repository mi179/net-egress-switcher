#!/usr/bin/env python3
"""把图形界面和命令行打成当前系统上的单文件程序。

Windows 上产物是：
  dist/net-egress-switcher-gui.exe
  dist/net-egress-switcher-cli.exe
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def find_pyinstaller() -> list[str]:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode == 0:
            return [sys.executable, "-m", "PyInstaller"]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return []


def build_one(pyi: list[str], root: Path, name: str, entry: str, windowed: bool) -> Path:
    command = pyi + [
        "--onefile",
        "--clean",
        "--noconfirm",
        "--noupx",
        # 引导程序用隔离配置，不读 PYTHONUTF8。没有这项时，
        # 英文 Windows 的标准输出是 cp1252，中文 --help 会直接崩。
        "--python-option",
        "X utf8",
        "--name",
        name,
        "--paths",
        "src",
        "--collect-submodules",
        "egress_switcher",
        "--collect-all",
        "ttkbootstrap",
        "--hidden-import",
        "tkinter",
        "--hidden-import",
        "tkinter.ttk",
        "--hidden-import",
        "PIL",
        "--hidden-import",
        "pystray",
        "--windowed" if windowed else "--console",
        entry,
    ]
    print(f"[BUILD] {name}")
    result = subprocess.run(command, cwd=root, check=False)
    if result.returncode != 0:
        raise SystemExit(result.returncode)
    filename = f"{name}.exe" if sys.platform == "win32" else name
    output = root / "dist" / filename
    if not output.is_file():
        raise SystemExit(f"没有找到产物：{output}")
    print(f"[OK] {output} ({output.stat().st_size / (1024 * 1024):.1f} MB)")
    return output


def main() -> None:
    root = Path(__file__).resolve().parent
    pyi = find_pyinstaller()
    if not pyi:
        raise SystemExit("没有 PyInstaller。请先 pip install pyinstaller")
    for folder in (root / "build", root / "dist"):
        shutil.rmtree(folder, ignore_errors=True)
    for spec in root.glob("*.spec"):
        spec.unlink()
    build_one(pyi, root, "net-egress-switcher-gui", "gui_main.py", windowed=True)
    build_one(pyi, root, "net-egress-switcher-cli", "cli_main.py", windowed=False)


if __name__ == "__main__":
    main()
