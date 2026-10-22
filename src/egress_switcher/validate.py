"""提权进程只接受这份校验过的计划，不执行任意命令。"""

from __future__ import annotations

import ipaddress
import re

_LABEL = re.compile(r"^(?!-)[\w .\-()/]{1,128}$", re.UNICODE)
_DEVICE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,31}$")


def validate_label(value: str, what: str = "名称") -> str:
    if not isinstance(value, str) or not _LABEL.fullmatch(value):
        raise ValueError(f"{what}无法安全使用：{value!r}")
    return value


def validate_device(value: str) -> str:
    if not isinstance(value, str) or not _DEVICE.fullmatch(value):
        raise ValueError(f"网卡名无法安全使用：{value!r}")
    return value


def validate_ipv4(value: str) -> str:
    try:
        ip = ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, TypeError) as exc:
        raise ValueError(f"不是可用的 IPv4 地址：{value!r}") from exc
    if ip.is_unspecified or ip.is_multicast or ip.is_loopback:
        raise ValueError(f"不是可用的 IPv4 网关：{value}")
    return str(ip)


def validate_metric(value: object, *, allow_auto: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"跃点必须是整数：{value!r}")
    if allow_auto and value == -1:
        return value
    if not 0 <= value <= 9999:
        raise ValueError(f"跃点超出范围：{value}")
    return value


def require_unique(labels: list[str], what: str) -> None:
    seen: set[str] = set()
    for label in labels:
        if label in seen:
            raise ValueError(f"{what}重复：{label}")
        seen.add(label)
