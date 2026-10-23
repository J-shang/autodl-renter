from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    pass


def is_cpu_model(model: str) -> bool:
    return model in ("CPU", "CPU-close-HT")


def keys(value: Any, allowed: str, where: str) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{where} 必须是 JSON 对象")
    unknown = set(value) - set(allowed.split())
    if unknown:
        raise ConfigError(f"{where} 包含未知字段: {', '.join(sorted(unknown))}")
    return value


def integer(value: Any, where: str, minimum: int = 1, maximum: int = 10000) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ConfigError(f"{where} 必须是 {minimum}–{maximum} 的整数")
    return value


def number(value: Any, where: str, minimum: float = 0, maximum: float = 1e9) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where} 必须是数值")
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ConfigError(f"{where} 必须在 {minimum}–{maximum} 之间")
    return float(value)


def money(value: Any, where: str, allow_zero: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ConfigError(f"{where} 必须是有效金额") from None
    if not result.is_finite() or result < 0 or (not allow_zero and result == 0):
        raise ConfigError(f"{where} 必须是{'非负' if allow_zero else '正'}金额")
    return result


def strings(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{where} 必须是非空字符串数组")
    if any(not isinstance(v, str) or not v.strip() for v in value):
        raise ConfigError(f"{where} 不能有空值")
    normalized = tuple(v.strip() for v in value)
    if len(set(normalized)) != len(normalized):
        raise ConfigError(f"{where} 不能有重复值")
    return normalized


@dataclass(frozen=True)
class Target:
    name: str
    regions: tuple[str, ...]
    gpu_models: tuple[str, ...]
    quantity: int
    gpus_per_instance: int
    max_price_per_gpu_hour: Decimal
    min_gpu_memory_gb: float = 0
    min_cpu_per_gpu: float = 0
    min_memory_per_gpu_gb: float = 0


@dataclass(frozen=True)
class Config:
    path: Path
    raw: dict
    targets: tuple[Target, ...]
    policy: str
    total_instances: int
    max_total_price_per_hour: Decimal
    selection: str
    image: dict
    poll: dict
    state_file: Path
    max_wait_seconds: float

    @property
    def fingerprint(self) -> str:
        # Poll timing and output path may change without forgetting prior rentals.
        contract = {k: self.raw.get(k) for k in (
            "targets", "stop", "image", "max_total_price_per_hour", "selection"
        )}
        data = json.dumps(contract, ensure_ascii=False, sort_keys=True).encode()
        return hashlib.sha256(data).hexdigest()


def load_config(path: Path) -> Config:
    path = path.expanduser().resolve()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"无法读取配置 {path}: {exc}") from None
    keys(raw, "targets stop image poll state_file max_wait_seconds selection max_total_price_per_hour", "配置")
    stop = keys(raw.get("stop", {}), "policy total_instances", "stop")
    policy = stop.get("policy", "any")
    if policy not in ("any", "all", "total"):
        raise ConfigError("stop.policy 只能是 any / all / total")
    targets = []
    if not isinstance(raw.get("targets"), list) or not raw["targets"]:
        raise ConfigError("targets 至少需要一个目标")
    names = set()
    for index, row in enumerate(raw["targets"]):
        label = f"targets[{index}]"
        keys(row, "name regions gpu_models quantity gpus_per_instance max_price_per_gpu_hour min_gpu_memory_gb min_cpu_per_gpu min_memory_per_gpu_gb", label)
        name = row.get("name")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ConfigError(f"{label}.name 必须非空且不重复")
        names.add(name)
        targets.append(Target(
            name=name,
            regions=strings(row.get("regions"), f"{label}.regions"),
            gpu_models=strings(row.get("gpu_models"), f"{label}.gpu_models"),
            quantity=integer(row.get("quantity", 1), f"{label}.quantity"),
            gpus_per_instance=integer(row.get("gpus_per_instance", 1), f"{label}.gpus_per_instance", maximum=16),
            max_price_per_gpu_hour=money(row.get("max_price_per_gpu_hour"), f"{label}.max_price_per_gpu_hour"),
            **{field: number(row.get(field, 0), f"{label}.{field}") for field in (
                "min_gpu_memory_gb", "min_cpu_per_gpu", "min_memory_per_gpu_gb"
            )},
        ))
    total = integer(stop.get("total_instances", 1), "stop.total_instances")
    if policy == "total" and total > sum(t.quantity for t in targets):
        raise ConfigError("total_instances 超过所有目标 quantity 的总和，无法完成")
    selection = raw.get("selection", "priority")
    if selection not in ("priority", "cheapest"):
        raise ConfigError("selection 只能是 priority / cheapest")
    image = keys(raw.get("image", {}), "kind uuid cuda_version chip_corp cpu_arch", "image")
    if image.get("kind", "base") not in ("base", "private"):
        raise ConfigError("image.kind 只能是 base / private")
    for field in ("uuid", "cuda_version", "chip_corp", "cpu_arch"):
        if not isinstance(image.get(field), str):
            raise ConfigError(f"image.{field} 必须是字符串")
    if image["chip_corp"] != "nvidia" or image["cpu_arch"] != "x86":
        raise ConfigError("当前版本仅支持 NVIDIA / x86 镜像与 GPU")
    try:
        cuda = Decimal(image["cuda_version"])
        if not cuda.is_finite() or not 1 <= cuda < 100 or not re.fullmatch(r"\d{1,2}(?:\.\d{1,2})?", image["cuda_version"]):
            raise InvalidOperation
    except InvalidOperation:
        raise ConfigError("image.cuda_version 应形如 11.8、12.8") from None
    poll = {
        "interval_seconds": 10.0, "jitter_seconds": 3.0,
        "request_gap_seconds": 1.0, "timeout_seconds": 20.0,
        "max_backoff_seconds": 120.0, "confirm_timeout_seconds": 90.0,
        "page_size": 90, "max_pages": 20,
    }
    keys(raw.get("poll", {}), " ".join(poll), "poll")
    poll.update(raw.get("poll", {}))
    for field, lo, hi in (
        ("interval_seconds", 5, 3600), ("jitter_seconds", 0, 600),
        ("request_gap_seconds", 0.5, 60), ("timeout_seconds", 1, 120),
        ("max_backoff_seconds", 5, 3600), ("confirm_timeout_seconds", 1, 600),
    ):
        poll[field] = number(poll[field], f"poll.{field}", lo, hi)
    poll["page_size"] = integer(poll["page_size"], "poll.page_size", maximum=90)
    poll["max_pages"] = integer(poll["max_pages"], "poll.max_pages", maximum=1000)
    state_value = raw.get("state_file", ".local/autodl-state.json")
    if not isinstance(state_value, str) or not state_value:
        raise ConfigError("state_file 必须是路径字符串")
    state = (path.parent / Path(state_value).expanduser()).resolve()
    if state == path:
        raise ConfigError("state_file 不能覆盖配置文件")
    return Config(
        path, raw, tuple(targets), policy, total,
        money(raw.get("max_total_price_per_hour"), "max_total_price_per_hour"),
        selection, image, poll, state,
        number(raw.get("max_wait_seconds", 1800), "max_wait_seconds", 1, 31536000),
    )


def example_config(policy: str = "any") -> dict:
    return {
        "stop": {"policy": policy, "total_instances": 2},
        "selection": "priority",
        "max_total_price_per_hour": 10,
        "max_wait_seconds": 1800,
        "state_file": ".local/autodl-state.json",
        "image": {"kind": "base", "uuid": "", "cuda_version": "12.8", "chip_corp": "nvidia", "cpu_arch": "x86"},
        "poll": {"interval_seconds": 10, "jitter_seconds": 3, "request_gap_seconds": 1},
        "targets": [
            {"name": "4090", "regions": ["西北B区", "北京B区"], "gpu_models": ["RTX 4090"], "quantity": 1, "gpus_per_instance": 1, "max_price_per_gpu_hour": 3},
            {"name": "3090", "regions": ["西北B区", "北京B区"], "gpu_models": ["RTX 3090"], "quantity": 1, "gpus_per_instance": 1, "max_price_per_gpu_hour": 2},
        ],
    }
