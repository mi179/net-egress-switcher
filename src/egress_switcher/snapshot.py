"""原始状态只在第一次修改前写一次，恢复始终回到那一版。"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from egress_switcher.models import SystemState
from egress_switcher.planner import snapshot_from_state


def config_dir() -> Path:
    if sys.platform == "win32":
        root = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(root) / "net-egress-switcher"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "net-egress-switcher"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "net-egress-switcher"


def snapshot_path(path: Path | None = None) -> Path:
    return path or (config_dir() / "original-state.json")


def load_snapshot(path: Path | None = None) -> dict | None:
    file = snapshot_path(path)
    if not file.is_file():
        return None
    return json.loads(file.read_text(encoding="utf-8"))


def ensure_snapshot(state: SystemState, path: Path | None = None) -> Path:
    file = snapshot_path(path)
    if file.is_file():
        return file
    data = snapshot_from_state(state)
    data["saved_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(file)
    return file
