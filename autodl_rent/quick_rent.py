"""Execute the user's one-instance rental request through Python HTTPS APIs."""
from __future__ import annotations

import argparse
import logging
import re
import sys
from decimal import Decimal
from pathlib import Path

from .api import APIError, AutoDL, cuda_tuple
from .cli import ROOT, read_token, validate_token
from .config import ConfigError, example_config, is_cpu_model, load_config
from .qr_auth import login_with_qr
from .runner import Runner, UncertainResult
from .state import State, state_lock, write_private_json

LOG = logging.getLogger("autodl_rent")


def select_quick_image(images: list[dict]) -> dict:
    choices = []
    for image in images:
        if "miniconda" not in image["label"].lower():
            continue
        try:
            cuda = cuda_tuple(image["cuda_version"])
        except ValueError:
            # The live catalog also contains variants such as 11.3(cudagl).
            # A plain compute image is sufficient for this rental request.
            continue
        if cuda > (11, 8):
            continue
        match = re.search(r" / (\d+)\.(\d+)(?:[^\d.]|$)", image["label"])
        python_version = tuple(map(int, match.groups())) if match else (0, 0)
        choices.append((cuda, python_version, image["uuid"], image))
    if not choices:
        raise ConfigError("没有找到合适的基础 Miniconda 镜像，尚未租用")
    return max(choices, key=lambda item: item[:3])[3]


def main(argv=None):
    parser = argparse.ArgumentParser(description="纯 Python 抢租西北B区 1 元以下的一个 GPU 或 CPU 实例")
    parser.add_argument("--config", type=Path, default=ROOT / "config.west-under1.local.json")
    parser.add_argument("--wait-seconds", type=float, default=1800)
    parser.add_argument("--execute", action="store_true", help="允许创建一个符合条件的按量实例")
    args = parser.parse_args(argv)
    if not 1 <= args.wait_seconds <= 86400:
        parser.error("--wait-seconds 必须在 1–86400 之间")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    path = args.config.expanduser().resolve()
    state_path = path.parent / ".local/west-under1.state.json"
    try:
        # One lock covers login, configuration generation, and every paid request.
        with state_lock(state_path):
            api = AutoDL(read_token(ROOT / ".local/auth.json"))
            if not api.token:
                api.token = validate_token(login_with_qr(api, ROOT / ".local/autodl-rent-login-qr.jpg", timeout=300))
            api.version()
            if state_path.exists():
                if not path.is_file():
                    raise ConfigError("已有该任务状态但配置缺失；先核对旧任务，不创建新任务")
                config = load_config(path)
                if config.state_file != state_path.resolve():
                    raise ConfigError("配置中的任务状态路径不符")
            else:
                models = [g["gpu_name"] for g in api.gpus()
                          if g.get("chip_corp") == "nvidia" or is_cpu_model(g.get("gpu_name", ""))]
                # A small general-purpose image can boot CPU and older NVIDIA hosts.
                image = select_quick_image(api.images())
                raw = example_config("any")
                raw.update(max_total_price_per_hour=0.999, max_wait_seconds=args.wait_seconds,
                           state_file=str(state_path.resolve()), selection="priority")
                raw["image"] = dict(kind="base", **{key: image[key] for key in ("uuid", "cuda_version", "chip_corp", "cpu_arch")})
                raw["targets"] = [{"name": "西北B区低于1元任意实例", "regions": ["西北B区"],
                                   "gpu_models": models, "quantity": 1, "gpus_per_instance": 1,
                                   "max_price_per_gpu_hour": 0.999}]
                raw["poll"].update(interval_seconds=5, jitter_seconds=1)
                write_private_json(path, raw)
                config = load_config(path)
            # Reject accidental reuse with a broader purchase contract.
            if config.policy != "any" or len(config.targets) != 1:
                raise ConfigError("此命令只允许任意一个成功就停止的单目标任务")
            target = config.targets[0]
            if target.regions != ("西北B区",) or target.quantity != 1 or target.gpus_per_instance != 1:
                raise ConfigError("此命令只允许西北B区的一个单单位实例")
            if target.max_price_per_gpu_hour >= Decimal(1) or config.max_total_price_per_hour >= Decimal(1):
                raise ConfigError("此命令的每实例小时价格上限必须严格低于1元")
            LOG.info("Python 接口模式；西北B区，GPU / CPU 均可，小时原价严格低于 ¥1；成功一个即停")
            state = State(config)
            result = Runner(config, api, state, execute=args.execute).run()
            print(f"任务状态：{state.path}", flush=True)
            if state.count():
                for row in state.data["successes"]:
                    print(f"已创建 {row['instance_uuid']} | {row['gpu_model']} | {row['region_name']} | ¥{row['price_per_hour']}/时", flush=True)
            return result
    except UncertainResult as exc:
        LOG.error("%s", exc)
        return 4
    except (APIError, ConfigError, OSError, ValueError) as exc:
        LOG.error("%s", exc)
        return 2
    except (KeyboardInterrupt, EOFError):
        LOG.info("已停止；已提交的请求请先核对任务状态")
        return 130


if __name__ == "__main__":
    sys.exit(main())
