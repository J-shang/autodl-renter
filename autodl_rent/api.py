from __future__ import annotations

import email.utils
import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .config import ConfigError, is_cpu_model

ORIGIN = "https://www.autodl.com"


class APIError(RuntimeError):
    def __init__(self, code: str, message: str = "", *, transient: bool = False, retry_after: float = 0):
        self.code, self.transient, self.retry_after = code, transient, retry_after
        super().__init__(f"{code}: {message}")


class TransportError(APIError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a login token to a redirected host or replay a creation POST.
        return None


def retry_delay(value: str | None) -> float:
    if not value:
        return 0
    try:
        return max(0, float(value))
    except ValueError:
        try:
            dt = email.utils.parsedate_to_datetime(value)
            return max(0, (dt - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return 0


class AutoDL:
    def __init__(self, token: str = "", *, timeout: float = 20, request_gap: float = 1):
        self.token = token.strip()
        self.timeout, self.request_gap = timeout, request_gap
        self._last_request = 0.0
        self._opener = urllib.request.build_opener(NoRedirect())
        self.app_version = "0.0.0"

    def request(self, method: str, path: str, body: Any = None, query: dict | None = None) -> Any:
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("API path must be relative to the fixed AutoDL origin")
        wait = self.request_gap - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        url = ORIGIN + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {
            "Accept": "application/json", "Content-Type": "application/json",
            "User-Agent": "AutoDL-Rent/0.1 (local user tool)",
            "Origin": ORIGIN, "Referer": ORIGIN + "/market/list",
            "AppVersion": self.app_version,
        }
        if self.token:
            headers["Authorization"] = self.token
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        self._last_request = time.monotonic()
        try:
            with self._opener.open(req, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            # Error bodies can contain private account data: do not log them.
            raise TransportError(
                f"HTTP{exc.code}", path, transient=exc.code == 429 or exc.code >= 500,
                retry_after=retry_delay(exc.headers.get("Retry-After")),
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
            raise TransportError("NetworkError", f"{path} ({type(exc).__name__})", transient=True) from None
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise TransportError("InvalidJSON", f"{path} 响应不是 JSON", transient=True) from None
        if not isinstance(result, dict):
            raise TransportError("InvalidResponse", f"{path} 响应结构变化")
        if path == "/version.json":
            self.app_version = str(result.get("version", "0.0.0"))
            return result
        code = result.get("code")
        if code != "Success":
            message = str(result.get("msg", ""))
            if self.token:
                # Redact before truncating: a long token can cross the log limit.
                message = message.replace(self.token, "[redacted]")
            if code == "AuthorizeFailed":
                message = "网页登录已过期或未提供 Token；请重新运行 login"
            raise APIError(str(code), message[:300])
        return result.get("data")

    def version(self):
        return self.request("GET", "/version.json")

    def regions(self) -> list[dict]:
        data = self.request("GET", "/api/v1/common")
        if not isinstance(data, dict) or not isinstance(data.get("region_list"), list):
            raise APIError("SchemaChanged", "地区目录结构变化")
        return data["region_list"]

    def gpus(self) -> list[dict]:
        data = self.request("GET", "/api/v1/machine/gpu_type")
        if not isinstance(data, list):
            raise APIError("SchemaChanged", "GPU 目录结构变化")
        return data

    def images(self) -> list[dict]:
        data = self.request("POST", "/api/v1/image/all", {"chip_corp": ["nvidia"], "cpu_arch": ["x86"]})
        if not isinstance(data, list):
            raise APIError("SchemaChanged", "基础镜像目录结构变化")
        result = flatten_images(data)
        if data and not result:
            raise APIError("SchemaChanged", "基础镜像树结构变化，未找到可用 UUID")
        return result

    def machines(self, target, regions: list[str], page: int, page_size: int, machine_id: str = "") -> dict:
        body = {
            "charge_type": "payg", "region_sign": "", "region_sign_list": regions,
            "gpu_type_name": list(target.gpu_models), "gpu_idle_num": target.gpus_per_instance,
            "machine_tag_name": [], "mount_net_disk": False,
            "cpu_arch": ["x86"], "chip_corp": [] if any(is_cpu_model(m) for m in target.gpu_models) else ["nvidia"],
            "instance_disk_size_order": "", "date_range": "", "date_from": "", "date_to": "",
            "page_index": page, "page_size": page_size, "pay_price_order": "asc",
            "gpu_idle_type": "", "default_order": False,
        }
        if machine_id:
            body["machine_id"] = machine_id
        data = self.request("POST", "/api/v1/user/machine/list", body)
        if not isinstance(data, dict) or not isinstance(data.get("list"), list):
            raise APIError("SchemaChanged", "市场列表结构变化")
        return data

    def quote(self, machine_id: str, gpu_count: int) -> Decimal:
        data = self.request("POST", "/api/v1/order/price/preview", price_info(machine_id, gpu_count))
        if not isinstance(data, dict) or type(data.get("origin_price")) is not int or data["origin_price"] <= 0:
            raise APIError("SchemaChanged", "报价 origin_price 缺失或单位结构变化，停止租用")
        # AutoDL's web UI divides origin_price/current_price by 1000 to show yuan.
        # Use undiscounted total as a conservative hourly ceiling, with no extra disk.
        return Decimal(data["origin_price"]) / 1000

    def create(self, machine_id: str, gpu_count: int, image: dict, name: str) -> str:
        info = {
            "machine_id": machine_id, "charge_type": "payg", "req_gpu_amount": gpu_count,
            "base_image_uuid": image["uuid"] if image.get("kind", "base") == "base" else "",
            "private_image_uuid": image["uuid"] if image.get("kind") == "private" else "",
            "reproduction_uuid": "", "reproduction_id": 0, "cg_application_uuid": "",
            "cg_application_info": {"image_name": "", "current_version_id": 0, "current_version": "", "image_id": 0},
            "instance_name": name[:40], "expand_data_disk": 0,
        }
        data = self.request("POST", "/api/v1/order/instance/create/payg", {
            "instance_info": info, "price_info": price_info(machine_id, gpu_count),
        })
        if not isinstance(data, str) or not data.strip():
            raise TransportError("UnknownCreateResult", "创建返回 Success，但实例 ID 结构变化")
        return data

    def exists(self, instance_uuid: str) -> bool:
        data = self.request("GET", "/api/v1/instance/exist", query={"instance_uuid": instance_uuid})
        if type(data) is not bool:
            raise APIError("SchemaChanged", "实例存在状态不再是布尔值")
        return data


def price_info(machine_id: str, gpu_count: int) -> dict:
    return {"coupon_id_list": [], "machine_id": machine_id, "charge_type": "payg",
            "duration": 1, "num": gpu_count, "expand_data_disk": 0}


def flatten_images(rows: list[dict], trail: tuple[str, ...] = ()) -> list[dict]:
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise APIError("SchemaChanged", "镜像节点应为对象")
        label = row.get("label_name")
        if isinstance(label, str) and isinstance(row.get("children"), list):
            result.extend(flatten_images(row["children"], trail + (label,)))
        elif isinstance(label, dict) and label.get("uuid"):
            result.append({
                "uuid": label["uuid"], "cuda_version": str(label.get("v", "")),
                "chip_corp": label.get("chip_corp", "nvidia"), "cpu_arch": label.get("cpu_arch", "x86"),
                "label": " / ".join(trail + ("CUDA " + str(label.get("v", "")),)),
            })
    return result


def resolve_regions(selected: tuple[str, ...], catalog: list[dict]) -> list[str]:
    result = []
    for value in selected:
        matches = [r["region_sign"] for r in catalog if value in (r.get("region_name"), r.get("region_sign"))]
        if not matches:
            raise ConfigError(f"未知地区 {value!r}；请先运行 catalog regions，使用完整名称或 region_sign")
        result.extend(matches)
    return list(dict.fromkeys(result))


def cuda_tuple(value: Any) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{1,2})(?:\.(\d{1,2}))?", str(value))
    if not match:
        raise ValueError("CUDA version must look like 12.8")
    return int(match[1]), int(match[2] or 0)
