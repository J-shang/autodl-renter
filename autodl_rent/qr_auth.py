"""AutoDL's native QR sign-in over HTTPS. No browser profile access."""
from __future__ import annotations

import logging
import platform
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .api import APIError, AutoDL, NoRedirect
from .config import ConfigError

LOG = logging.getLogger("autodl_rent")


def qr_image(url: str, path: Path) -> Path:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname != "mp.weixin.qq.com":
        raise ConfigError("登录二维码图片不是预期的微信 HTTPS 地址")
    request = urllib.request.Request(url, headers={"Accept": "image/*", "User-Agent": "AutoDL-Rent/0.1"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
            content = response.read(2 * 1024 * 1024 + 1)
    except (OSError, urllib.error.URLError):
        raise ConfigError("登录二维码图片下载失败，请重试") from None
    if len(content) > 2 * 1024 * 1024:
        raise ConfigError("登录二维码图片超过大小上限")
    if content.startswith(b"\xff\xd8\xff"):
        path = path.with_suffix(".jpg")
    elif content.startswith(b"\x89PNG\r\n\x1a\n"):
        path = path.with_suffix(".png")
    else:
        raise ConfigError("登录二维码响应不是 JPEG / PNG 图片")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(content)
    path.chmod(0o600)
    return path.resolve()


def login_with_qr(api: AutoDL, path: Path, *, timeout: float = 300,
                  clock=time.monotonic, sleep=time.sleep, show=None) -> str:
    challenge = api.request("GET", "/api/v1/wx/login")
    if not isinstance(challenge, dict) or not isinstance(challenge.get("uuid"), str) or not isinstance(challenge.get("qrcode_url"), str):
        raise ConfigError("AutoDL 二维码登录响应结构变化")
    image_path = qr_image(challenge["qrcode_url"], path)
    if show:
        show(image_path)
    else:
        print(f"等待微信扫码登录：{image_path}", flush=True)
    expiry = challenge.get("expire_seconds", timeout)
    if type(expiry) not in (int, float) or expiry <= 0:
        expiry = timeout
    end = clock() + min(timeout, expiry)
    while clock() < end:
        data = api.request("POST", "/api/v1/wx/polling/query", {
            "uuid": challenge["uuid"], "device_fingerprint": "",
        })
        if isinstance(data, dict) and data.get("ticket"):
            auth = api.request("POST", "/api/v1/passport", {
                "ticket": data["ticket"],
                "device": {
                    "os": {"name": platform.system(), "version": platform.release()},
                    "browser": {"name": "AutoDL-Rent Python", "version": "0.1", "major": "0"},
                },
            })
            if not isinstance(auth, dict) or not isinstance(auth.get("token"), str) or not auth["token"]:
                raise ConfigError("登录响应缺少 Token")
            # The caller decides whether to persist it; the rental job keeps it in memory.
            return auth["token"]
        if isinstance(data, dict) and data.get("openId") and not data.get("ticket"):
            raise ConfigError("该微信尚未关联 AutoDL 账号。可运行 python3 -m autodl_rent auth 导入网页登录 Token，无需绑定微信；也可在官方页面关联账号后重新扫码")
        sleep(min(3, max(0, end - clock())))
    raise ConfigError("扫码登录等待超时，未发起任何租用")
