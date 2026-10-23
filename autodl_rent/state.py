from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .config import Config, ConfigError


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_private_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            if hasattr(os, "fchmod"):
                os.fchmod(stream.fileno(), 0o600)
            json.dump(data, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            # Persist the directory entry too, including the pre-submit intent.
            directory = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def state_lock(path: Path):
    """Kernel lock: crashes release it automatically; the lock file can remain."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = Path(str(path) + ".lock")
    with lock_path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise ConfigError(f"已有进程正在使用状态文件 {path}") from None
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ConfigError(f"已有进程正在使用状态文件 {path}") from None
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class State:
    def __init__(self, config: Config):
        self.config, self.path = config, config.state_file
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise ConfigError("状态文件无法读取；请保留文件并先核对控制台，不能当作空任务继续") from None
            if not isinstance(self.data, dict) or self.data.get("version") != 1:
                raise ConfigError("状态文件版本不支持")
            if self.data.get("config_fingerprint") != config.fingerprint:
                raise ConfigError("地区、型号、数量、镜像或预算已变更。请先核对旧任务；新任务使用不同 state_file")
            self._validate()
        else:
            self.data = {"version": 1, "config_fingerprint": config.fingerprint,
                         "created_at": now(), "successes": [], "pending": None, "events": []}

    def _validate(self):
        successes = self.data.get("successes")
        if not isinstance(successes, list) or not isinstance(self.data.get("events"), list):
            raise ConfigError("状态文件结构损坏")
        names = {t.name for t in self.config.targets}
        ids = set()
        try:
            for row in successes:
                if row["target"] not in names or not row["instance_uuid"] or row["instance_uuid"] in ids:
                    raise ValueError
                ids.add(row["instance_uuid"])
                price = Decimal(row["price_per_hour"])
                if not price.is_finite() or price <= 0:
                    raise ValueError
            pending = self.data.get("pending")
            if pending is not None and (not isinstance(pending, dict) or pending["target"] not in names):
                raise ValueError
        except (KeyError, TypeError, ValueError, ArithmeticError):
            raise ConfigError("状态文件记录损坏；请先核对控制台") from None

    def save(self):
        write_private_json(self.path, self.data)

    @property
    def pending(self) -> dict | None:
        return self.data["pending"]

    def count(self, name: str | None = None) -> int:
        return sum(1 for r in self.data["successes"] if name is None or r["target"] == name)

    @property
    def hourly_total(self) -> Decimal:
        return sum((Decimal(r["price_per_hour"]) for r in self.data["successes"]), Decimal(0))

    def done(self) -> bool:
        if self.config.policy == "any":
            return self.count() >= 1
        if self.config.policy == "all":
            return all(self.count(t.name) >= t.quantity for t in self.config.targets)
        return self.count() >= self.config.total_instances

    def begin(self, record: dict):
        if self.pending:
            raise ConfigError("存在未确认的创建请求，禁止继续提交")
        self.data["pending"] = dict(record, submitted_at=now(), instance_uuid=None)
        self.save()  # Must be durable BEFORE the paid API call.

    def attach_instance(self, instance_uuid: str):
        if not self.pending:
            raise ConfigError("不存在待确认请求")
        self.pending["instance_uuid"] = instance_uuid
        self.save()

    def confirm(self):
        if not self.pending or not self.pending.get("instance_uuid"):
            raise ConfigError("不能确认没有实例 ID 的请求")
        identifier = self.pending["instance_uuid"]
        if any(r["instance_uuid"] == identifier for r in self.data["successes"]):
            raise ConfigError("该实例已经记录过，不能重复计数")
        self.data["successes"].append(dict(self.pending, confirmed_at=now()))
        self.data["pending"] = None
        self.save()

    def reject(self, reason: str):
        self.data["events"].append({"at": now(), "reason": reason, "attempt": self.pending})
        self.data["events"] = self.data["events"][-100:]
        self.data["pending"] = None
        self.save()
