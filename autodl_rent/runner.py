from __future__ import annotations

import logging
import random
import re
import time
import uuid
from dataclasses import replace
from decimal import Decimal, InvalidOperation

from .api import APIError, AutoDL, TransportError, cuda_tuple, resolve_regions
from .config import Config, ConfigError, Target, is_cpu_model
from .state import State

LOG = logging.getLogger("autodl_rent")
GIB = 1024 ** 3


class UncertainResult(RuntimeError):
    """A submitted creation may have succeeded. Never automatically resubmit."""


def allowed_gpu_count(machine: dict, count: int) -> bool:
    idle, total = int(machine["gpu_idle_num"]), int(machine["gpu_number"])
    if idle < count or total < count:
        return False
    mode = machine.get("rent_mode")
    if not mode:
        return True
    match = re.search(r"\d+", str(mode))
    if not match:
        # Some hosts use other multiple-card mode labels. Skip unknown modes.
        return False
    minimum = int(match[0])
    if minimum <= 0:
        return False
    if str(mode).startswith("Base"):
        return count >= minimum or idle < minimum
    return count % minimum == 0 or idle < minimum


def candidate(machine: dict, target: Target, regions: list[str], image: dict, gpu_catalog: dict) -> dict | None:
    """Recheck every constraint locally, even when the server returned a match."""
    try:
        model = machine["gpu_name"]
        cpu_only = is_cpu_model(model)
        if model not in target.gpu_models or machine["region_sign"] not in regions:
            return None
        if machine.get("cpu_arch") != "x86":
            return None
        if machine.get("chip_corp") not in (("nvidia", "cpu") if cpu_only else ("nvidia",)):
            return None
        if type(machine.get("on_line")) is not int or machine["on_line"] in (3, 4, 5, 6):
            return None
        if int(machine["max_instance_num"]) <= int(machine["binding_instance_num"]):
            return None
        if not allowed_gpu_count(machine, target.gpus_per_instance):
            return None
        if cpu_only and target.min_gpu_memory_gb > 0:
            return None
        if not cpu_only and int(machine["gpu_memory"]) / GIB < target.min_gpu_memory_gb:
            return None
        if float(machine["cpu_per_gpu"]) < target.min_cpu_per_gpu:
            return None
        if int(machine["mem_per_gpu"]) / GIB < target.min_memory_per_gpu_gb:
            return None
        if not cpu_only:
            wanted_cuda = cuda_tuple(image["cuda_version"])
            if not machine.get("highest_cuda_version") or cuda_tuple(machine["highest_cuda_version"]) < wanted_cuda:
                return None
            series = gpu_catalog[model].get("gpu_series_type", "")
            minimum_cuda = {"NVIDIA-Ampere": (11, 0), "NVIDIA-Ada": (11, 3), "NVIDIA-Blackwell": (12, 8)}.get(series, (0, 0))
            if wanted_cuda < minimum_cuda:
                return None
        # The current web UI's SKU order is payg, daily, weekly, monthly, yearly.
        sku = machine["machine_sku_info"][0]
        if sku.get("type", "payg") != "payg":
            return None
        raw_price = sku["current_price"]
        if type(raw_price) is not int or raw_price <= 0:
            return None
        price = Decimal(raw_price) / 1000
        if price > target.max_price_per_gpu_hour:
            return None
        identifier = machine["machine_id"]
        if not isinstance(identifier, str) or not identifier:
            return None
        return {"machine_id": identifier, "target": target.name, "gpu_model": model,
                "region_sign": machine["region_sign"], "region_name": machine.get("region_name", machine["region_sign"]),
                "gpus": target.gpus_per_instance, "price_per_hour": str(price * target.gpus_per_instance)}
    except (KeyError, ValueError, TypeError, IndexError, InvalidOperation):
        return None


class Runner:
    def __init__(self, config: Config, api: AutoDL, state: State, *, execute: bool = False,
                 monotonic=time.monotonic, sleep=time.sleep, jitter=random.uniform):
        self.config, self.api, self.state, self.execute = config, api, state, execute
        self.clock, self.sleep, self.jitter = monotonic, sleep, jitter
        self.deadline = 0.0
        self.regions = {}
        self.gpus = {}
        self.price_models = {}
        self.price_scan_at = {}
        self.preview = []
        self.preview_count = 0
        self.last_error = None

    def prepare(self):
        regions = self.api.regions()
        self.gpus = {g["gpu_name"]: g for g in self.api.gpus()}
        for target in self.config.targets:
            self.regions[target.name] = resolve_regions(target.regions, regions)
            for model in target.gpu_models:
                if model not in self.gpus:
                    raise ConfigError(f"未知 GPU 型号 {model!r}，请先运行 catalog gpus")
                if not is_cpu_model(model) and self.gpus[model].get("chip_corp") != "nvidia":
                    raise ConfigError(f"当前仅支持 NVIDIA GPU 和 CPU 实例，不支持 {model}")
        if self.execute:
            image = self.config.image
            if not image["uuid"] or "填" in image["uuid"] or image["uuid"].startswith("YOUR_"):
                raise ConfigError("真实执行前必须选择 image.uuid；可运行 init 或 catalog images")
            if image.get("kind", "base") == "base":
                choices = self.api.images()
                selected = next((i for i in choices if i["uuid"] == image["uuid"]), None)
                if selected is None:
                    raise ConfigError("基础镜像 UUID 不在当前目录；请用 catalog images 重新选择")
                if cuda_tuple(selected["cuda_version"]) != cuda_tuple(image["cuda_version"]):
                    raise ConfigError("image.cuda_version 与基础镜像目录不符")
                if selected["chip_corp"] != image["chip_corp"] or selected["cpu_arch"] != image["cpu_arch"]:
                    raise ConfigError("基础镜像与配置中的芯片/架构不符")

    def wait(self, seconds: float, *, deadline: float | None = None):
        limit = self.deadline if deadline is None else deadline
        end = min(self.clock() + seconds, limit)
        while self.clock() < end:
            self.sleep(min(1.0, end - self.clock()))

    def confirm_pending(self):
        pending = self.state.pending
        if not pending:
            return
        identifier = pending.get("instance_uuid")
        if not identifier:
            raise UncertainResult("上次创建结果未知；请先核对 AutoDL 控制台，再用 resolve 处理，不能自动重试")
        end = self.clock() + self.config.poll["confirm_timeout_seconds"]
        while self.clock() < end:
            retry_after = 0.0
            try:
                if self.api.exists(identifier):
                    self.state.confirm()
                    LOG.info("已确认实例 %s；累计 %d 个，小时总价上界 ¥%s", identifier, self.state.count(), self.state.hourly_total)
                    return
            except APIError as exc:
                if not exc.transient:
                    raise UncertainResult(f"实例 {identifier} 待确认，查询失败 ({exc.code})，状态已保存") from None
                retry_after = exc.retry_after
            self.wait(max(3, retry_after), deadline=end)
        raise UncertainResult(f"实例 {identifier} 尚未确认，保留待核对记录；不会再次创建")

    def scan_target(self, target: Target):
        if self.state.count(target.name) >= target.quantity:
            return
        full_scan = target.name not in self.price_models or self.clock() >= self.price_scan_at[target.name]
        query_models = target.gpu_models if full_scan else tuple(sorted(self.price_models[target.name]))
        if not query_models:
            return
        query_target = replace(target, gpu_models=query_models)
        cheap_models = set()
        seen = set()
        for page in range(1, self.config.poll["max_pages"] + 1):
            if self.clock() >= self.deadline:
                return
            if self.execute and (self.state.done() or self.state.count(target.name) >= target.quantity):
                return
            data = self.api.machines(query_target, self.regions[target.name], page, self.config.poll["page_size"])
            rows = data["list"]
            page_choices = []
            for machine in rows:
                if full_scan:
                    skus = machine.get("machine_sku_info") or []
                    raw_price = skus[0].get("current_price") if skus and isinstance(skus[0], dict) else None
                    if (type(raw_price) is int and 0 < Decimal(raw_price) / 1000 <= target.max_price_per_gpu_hour
                            and machine.get("gpu_name") in target.gpu_models
                            and machine.get("region_sign") in self.regions[target.name]):
                        cheap_models.add(machine["gpu_name"])
                match = candidate(machine, target, self.regions[target.name], self.config.image, self.gpus)
                if match and match["machine_id"] not in seen:
                    seen.add(match["machine_id"])
                    if self.state.hourly_total + Decimal(match["price_per_hour"]) <= self.config.max_total_price_per_hour:
                        page_choices.append(match)
            page_choices.sort(key=lambda offer: (Decimal(offer["price_per_hour"]), offer["machine_id"]))
            for offer in page_choices:
                if self.execute and (self.state.done() or self.state.count(target.name) >= target.quantity):
                    return
                yield target, offer
            total = data.get("result_total")
            complete = not rows or len(rows) < self.config.poll["page_size"] or (type(total) is int and page * self.config.poll["page_size"] >= total)
            if complete:
                if full_scan:
                    # Watch affordable models on the short path, including sold-out
                    # hosts, and periodically recheck every model for price changes.
                    self.price_models[target.name] = cheap_models
                    self.price_scan_at[target.name] = self.clock() + 120
                    LOG.info("目标 %s：后续优先轮询 %d 种价格合适的型号，每120秒重新检查全部型号", target.name, len(cheap_models))
                return
        LOG.warning("目标 %s 达到 max_pages；本轮仅检查这些页，可调大配置", target.name)

    def candidates(self):
        if self.config.selection == "priority":
            # Submit as soon as the preferred target has a match, without waiting
            # for other targets or all inventory pages to finish scanning.
            for target in self.config.targets:
                yield from self.scan_target(target)
        else:
            choices = [choice for target in self.config.targets for choice in self.scan_target(target)]
            priority = {t.name: n for n, t in enumerate(self.config.targets)}
            choices.sort(key=lambda item: (Decimal(item[1]["price_per_hour"]), priority[item[0].name], item[1]["machine_id"]))
            yield from choices

    def try_create(self, target: Target, offer: dict) -> bool:
        # The scanned stock may be stale. Recheck the exact host and all constraints.
        fresh = self.api.machines(target, self.regions[target.name], 1, 1, offer["machine_id"])
        fresh_offer = next((candidate(m, target, self.regions[target.name], self.config.image, self.gpus)
                            for m in fresh["list"] if m.get("machine_id") == offer["machine_id"]), None)
        if fresh_offer is None or self.clock() >= self.deadline:
            return False
        price = self.api.quote(offer["machine_id"], target.gpus_per_instance)
        if self.clock() >= self.deadline:
            return False
        if price > target.max_price_per_gpu_hour * target.gpus_per_instance:
            LOG.info("跳过 %s：最新报价 ¥%s/时 超过目标上限", offer["machine_id"], price)
            return False
        if self.state.hourly_total + price > self.config.max_total_price_per_hour:
            LOG.info("跳过 %s：将超过小时总价上限", offer["machine_id"])
            return False
        record = dict(fresh_offer, price_per_hour=str(price), attempt_id=uuid.uuid4().hex[:12])
        LOG.info("提交：%s / %s / %s × %d，报价上界 ¥%s/时", target.name,
                 record["region_name"], record["gpu_model"], record["gpus"], price)
        self.state.begin(record)
        try:
            identifier = self.api.create(record["machine_id"], record["gpus"], self.config.image,
                                         f"grab-{record['attempt_id']}")
        except TransportError as exc:
            raise UncertainResult(f"创建请求结果不确定 ({exc.code})；已保存 pending，请核对控制台后 resolve") from None
        except APIError as exc:
            if exc.code == "MachineGpuNumUpdateFailed":
                self.state.reject(exc.code)
                LOG.info("%s 库存已被租走，继续等待", record["machine_id"])
                return False
            if exc.code in ("AuthorizeFailed", "TORecharge", "TORealName", "OrderUnpaidMoreThanThree"):
                self.state.reject(exc.code)
                raise
            # An undocumented error code is not sufficient evidence to resubmit.
            raise UncertainResult(f"创建返回未识别错误 ({exc.code})；保留 pending，请核对后 resolve") from None
        self.state.attach_instance(identifier)
        self.confirm_pending()
        return True

    def run(self, *, once: bool = False) -> int:
        self.deadline = self.clock() + self.config.max_wait_seconds
        if self.state.pending:
            if not self.execute:
                raise UncertainResult("存在待核对请求，请先用 status 查看，再运行 --execute 只读确认或 resolve")
            self.confirm_pending()
        if self.state.done():
            LOG.info("停止条件已经满足，已记录 %d 个实例", self.state.count())
            return 0
        initialization_failures = 0
        while self.clock() < self.deadline:
            try:
                self.prepare()
                self.last_error = None
                break
            except APIError as exc:
                self.last_error = exc
                if not exc.transient:
                    raise
                initialization_failures += 1
                delay = max(exc.retry_after, min(self.config.poll["max_backoff_seconds"],
                            self.config.poll["interval_seconds"] * 2 ** min(initialization_failures, 10)))
                LOG.warning("读取目录失败 (%s)，退避 %.1f 秒", exc.code, delay)
                if once or not self.execute:
                    return 3 if self.execute else 2
                self.wait(delay)
        else:
            LOG.info("等待目录恢复达到 max_wait_seconds，未提交新的创建请求")
            return 3
        failures, round_number = 0, 0
        LOG.info("%s；策略 %s；小时总价上限 ¥%s；已完成 %d 个",
                 "离线模拟" if getattr(self.api, "is_simulation", False) else
                 "真实租用" if self.execute else "预览（不会创建实例）",
                 self.config.policy, self.config.max_total_price_per_hour, self.state.count())
        while self.clock() < self.deadline:
            round_number += 1
            delay = self.config.poll["interval_seconds"]
            try:
                LOG.info("第 %d 轮：检查库存", round_number)
                attempted_hosts = set()
                matches = 0
                for target, offer in self.candidates():
                    matches += 1
                    if not self.execute:
                        self.preview_count += 1
                        if len(self.preview) < 100:
                            self.preview.append(offer)
                        continue
                    if self.state.done():
                        return 0
                    if self.clock() >= self.deadline:
                        break
                    if self.state.count(target.name) >= target.quantity or offer["machine_id"] in attempted_hosts:
                        continue
                    attempted_hosts.add(offer["machine_id"])
                    self.try_create(target, offer)
                    if self.state.done():
                        LOG.info("已满足 %s 停止条件，停止提交；共 %d 个实例", self.config.policy, self.state.count())
                        return 0
                LOG.info("本轮共 %d 个符合条件的候选%s", matches, "，最多显示前 20 个" if matches > 20 and not self.execute else "")
                if not self.execute:
                    return 0
                failures = 0
                self.last_error = None
            except APIError as exc:
                self.last_error = exc
                if not exc.transient:
                    raise
                failures += 1
                delay = max(exc.retry_after, min(self.config.poll["max_backoff_seconds"],
                                                  delay * 2 ** min(failures, 10)))
                LOG.warning("读取失败 (%s)，退避 %.1f 秒", exc.code, delay)
            if once or not self.execute:
                return 3 if self.execute else 2
            self.wait(delay + self.jitter(0, self.config.poll["jitter_seconds"]))
        LOG.info("达到 max_wait_seconds，停止等待；已租实例继续按平台规则计费")
        return 3
