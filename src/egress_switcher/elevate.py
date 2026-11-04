"""只在应用计划时提权。界面本身保持普通用户权限。"""

from __future__ import annotations

import ctypes
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from egress_switcher.apply import Completed, apply_payload
from egress_switcher.errors import ApplyError


def is_admin() -> bool:
    if sys.platform == "win32":
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            return False
    if hasattr(os, "geteuid"):
        return os.geteuid() == 0
    return False


def module_argv(plan_path: Path, result_path: Path) -> list[str]:
    """提权后要执行的参数。只包含本程序的 apply，不拼用户输入。"""
    if getattr(sys, "frozen", False):
        return [sys.executable, "apply", "--plan", str(plan_path), "--result", str(result_path)]
    return [sys.executable, "-m", "egress_switcher", "apply", "--plan", str(plan_path), "--result", str(result_path)]


def write_helper_script(path: Path, argv: list[str], extra_env: dict | None = None) -> None:
    lines = ["#!/bin/sh"]
    for key, value in (extra_env or {}).items():
        if key == "PYTHONPATH" and value:
            lines.append(f"export PYTHONPATH={shlex.quote(value)}")
    lines.append("exec " + " ".join(shlex.quote(part) for part in argv))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o700)


def applescript_admin(shell_command: str) -> str:
    literal = '"' + shell_command.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return f"do shell script {literal} with administrator privileges"


def windows_shell_execute_params(argv: list[str]) -> tuple[str, str]:
    if not argv:
        raise ApplyError("没有可提权的命令。")
    return argv[0], subprocess.list2cmdline(argv[1:])


def request_apply(payload: dict) -> dict:
    from egress_switcher.snapshot import config_dir

    directory = config_dir() / "plans"
    directory.mkdir(parents=True, exist_ok=True)
    plan_path = directory / "pending-plan.json"
    result_path = directory / "pending-result.json"
    plan_path.write_text(_dump(payload), encoding="utf-8")
    if result_path.exists():
        result_path.unlink()
    if is_admin():
        try:
            apply_payload(payload, _subprocess_run)
        except ApplyError as exc:
            return {"ok": False, "message": str(exc)}
        return {"ok": True, "message": "已按预览修改 IPv4 出口。"}
    try:
        if sys.platform == "win32":
            return _elevate_windows(plan_path, result_path)
        if sys.platform == "darwin":
            return _elevate_macos(plan_path, result_path)
        return _elevate_linux(plan_path, result_path)
    except ApplyError as exc:
        return {"ok": False, "message": str(exc)}


def _elevate_linux(plan_path: Path, result_path: Path) -> dict:
    pkexec = shutil.which("pkexec")
    if not pkexec:
        return {
            "ok": False,
            "message": "没有找到 pkexec，不能弹出图形授权，系统未修改。请安装 pkexec 后重试。",
        }
    argv = module_argv(plan_path, result_path)
    with tempfile.TemporaryDirectory(prefix="egress-switcher-") as directory:
        script = Path(directory) / "apply.sh"
        write_helper_script(script, argv, {"PYTHONPATH": os.environ.get("PYTHONPATH", "")})
        # 执行的是 /bin/sh，计划里的网卡名不会进入 shell。
        completed = subprocess.run([pkexec, "/bin/sh", str(script)], check=False)
    if completed.returncode != 0 and not result_path.is_file():
        if completed.returncode in {126, 127}:
            return {"ok": False, "message": "授权被取消，系统未修改。"}
        return {"ok": False, "message": f"提权失败（退出码 {completed.returncode}），系统未修改。"}
    return _read_result(result_path)


def _elevate_macos(plan_path: Path, result_path: Path) -> dict:
    argv = module_argv(plan_path, result_path)
    with tempfile.TemporaryDirectory(prefix="egress-switcher-") as directory:
        script = Path(directory) / "apply.sh"
        write_helper_script(script, argv, {"PYTHONPATH": os.environ.get("PYTHONPATH", "")})
        shell_command = "/bin/sh " + shlex.quote(str(script))
        completed = subprocess.run(
            ["osascript", "-e", applescript_admin(shell_command)],
            capture_output=True,
            text=True,
            check=False,
        )
    if completed.returncode != 0 and not result_path.is_file():
        detail = (completed.stderr or completed.stdout or "").strip()
        if "cancel" in detail.lower() or "取消" in detail:
            return {"ok": False, "message": "授权被取消，系统未修改。"}
        return {"ok": False, "message": "没有获得管理员权限，系统未修改。" + (f" {detail}" if detail else "")}
    return _read_result(result_path)


def _elevate_windows(plan_path: Path, result_path: Path) -> dict:
    executable, params = windows_shell_execute_params(module_argv(plan_path, result_path))
    code = ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, params, None, 1)
    if code <= 32:
        return {"ok": False, "message": "没有获得管理员权限，系统未修改。"}
    return _wait_result(result_path)


def _wait_result(path: Path, timeout: float = 180) -> dict:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.is_file():
            try:
                return _read_result(path)
            except ValueError:
                time.sleep(0.2)
                continue
        time.sleep(0.2)
    return {"ok": False, "message": "等待授权超时。如果取消了授权窗口，系统不会被修改。"}


def _read_result(path: Path) -> dict:
    import json

    if not path.is_file():
        return {"ok": False, "message": "没有收到修改结果。如果取消了授权，系统不会被修改。"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(data, dict) or "ok" not in data:
        return {"ok": False, "message": "修改结果无法识别。"}
    return {"ok": bool(data.get("ok")), "message": str(data.get("message") or "")}


def _subprocess_run(command: list[str]) -> Completed:
    from egress_switcher.textutil import decode_output

    try:
        completed = subprocess.run(command, capture_output=True, timeout=60, check=False)
    except OSError as exc:
        return Completed(127, "", str(exc))
    return Completed(completed.returncode, decode_output(completed.stdout), decode_output(completed.stderr))


def _dump(payload: dict) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2)
