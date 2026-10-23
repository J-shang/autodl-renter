from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path

from .api import APIError, AutoDL
from .config import ConfigError, example_config, is_cpu_model, load_config
from .runner import Runner, UncertainResult
from .state import State, state_lock, write_private_json

ROOT = Path(__file__).resolve().parent.parent
LOG = logging.getLogger("autodl_rent")


def read_token(path: Path) -> str:
    token = os.environ.get("AUTODL_WEB_TOKEN", "").strip()
    if not token and path.exists():
        try:
            auth = json.loads(path.read_text(encoding="utf-8"))
            token = auth["token"]
        except (KeyError, TypeError, ValueError, OSError):
            raise ConfigError("认证文件无效，请重新运行 auth / login") from None
    return validate_token(token)


def validate_token(token: str) -> str:
    if not isinstance(token, str):
        raise ConfigError("网页登录 Token 必须是字符串")
    token = token.strip()
    if any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ConfigError("Token 含有无效字符；只填写网页登录 Token 的原始值")
    return token


def make_api(args, *, required: bool = False, config=None) -> AutoDL:
    token = read_token(args.auth_file)
    if required and not token:
        raise ConfigError("请先运行 auth / login，或在本机设置 AUTODL_WEB_TOKEN 环境变量")
    poll = config.poll if config else {}
    api = AutoDL(token, timeout=poll.get("timeout_seconds", 20), request_gap=poll.get("request_gap_seconds", 1))
    try:
        api.version()
    except APIError as exc:
        if not exc.transient or exc.retry_after:
            raise
        LOG.warning("暂时无法获取网页版本 (%s)，目录/库存查询将独立检查连接", exc.code)
    return api


def choose_many(title: str, values: list[str]) -> list[str]:
    if not values:
        raise ConfigError("目录中没有可选项，请稍后重试或检查账号权限")
    print(title)
    for n, value in enumerate(values, 1):
        print(f"  {n:>2}. {value}")
    while True:
        raw = input("输入编号，可用逗号多选: ").replace("，", ",")
        try:
            indices = list(dict.fromkeys(int(i.strip()) - 1 for i in raw.split(",")))
            if not indices or any(i < 0 or i >= len(values) for i in indices):
                raise ValueError
            return [values[i] for i in indices]
        except ValueError:
            print("请输入列表中的有效编号，例如 1,3")


def prompt_int(title: str, default: int = 1) -> int:
    while True:
        raw = input(f"{title} [{default}]: ").strip()
        try:
            result = int(raw or str(default))
            if result < 1:
                raise ValueError
            return result
        except ValueError:
            print("请输入正整数")


def prompt_money(title: str, default: Decimal | None = None) -> float:
    while True:
        raw = input(title + (f" [{default}]" if default is not None else "") + ": ").strip()
        try:
            result = Decimal(raw) if raw else default
            if result is None or not result.is_finite() or result <= 0:
                raise ValueError
            return float(result)
        except (ValueError, ArithmeticError):
            print("请输入正金额，单位为元")


def create_config(args):
    path = args.config.expanduser().resolve()
    if path.exists():
        raise ConfigError(f"配置已存在：{path}；请直接编辑，或用 --config 指定新文件")
    raw = example_config()
    if not args.template:
        api = make_api(args)
        regions = list(dict.fromkeys(r["region_name"] for r in api.regions() if r.get("visible") == "all"))
        gpus = [g["gpu_name"] for g in api.gpus() if g.get("chip_corp") == "nvidia" or is_cpu_model(g["gpu_name"])]
        selected_regions = choose_many("选择允许租用的地区（稍后可在配置中分别调整）", regions)
        models = choose_many("选择 GPU / CPU 型号（每种型号建立一个目标）", gpus)
        policy = choose_many("选择一种停止策略", ["any：成功任意一个实例", "all：每个目标达标", "total：累计实例数达标"])
        if len(policy) != 1:
            raise ConfigError("停止策略只能选一种，请重新运行 init")
        raw["stop"]["policy"] = policy[0].split("：")[0]
        raw["targets"] = []
        budget = Decimal(0)
        print("quantity 计算实例数；每个实例可以有多块 GPU。")
        for model in models:
            quantity = prompt_int(f"{model} 需要几个实例")
            cards = 1 if is_cpu_model(model) else prompt_int(f"{model} 每个实例需要几块 GPU")
            cap = prompt_money(f"{model} {'单个实例' if is_cpu_model(model) else '每块 GPU'}的小时价格上限")
            raw["targets"].append({"name": model, "regions": selected_regions, "gpu_models": [model],
                                   "quantity": quantity, "gpus_per_instance": cards, "max_price_per_gpu_hour": cap})
            budget += quantity * cards * Decimal(str(cap))
        if raw["stop"]["policy"] == "total":
            raw["stop"]["total_instances"] = prompt_int("累计成功几个实例后停止")
        raw["max_total_price_per_hour"] = prompt_money("本任务新租实例的小时总价上限", budget)
        if api.token:
            images = api.images()
            labels = [f"{i['label']} | {i['uuid']}" for i in images]
            selected = choose_many("选择一个基础镜像（CUDA 必须兼容所选型号）", labels)
            if len(selected) != 1:
                raise ConfigError("镜像只能选一个，请重新运行 init")
            image = images[labels.index(selected[0])]
            raw["image"] = {k: image[k] for k in ("uuid", "cuda_version", "chip_corp", "cpu_arch")}
            raw["image"]["kind"] = "base"
    # A separate state file per config keeps explicit independent tasks independent.
    raw["state_file"] = f".local/{path.stem}.state.json"
    write_private_json(path, raw)
    try:
        load_config(path)
    except ConfigError:
        print(f"已保存待修改配置：{path}")
        raise
    print(f"已生成配置：{path}")
    if not raw["image"]["uuid"]:
        print("请在真实租用前填写 image.uuid；运行 catalog images 可查看基础镜像。")


def authenticate(args):
    print("在已登录的 AutoDL 网页打开开发者工具 Console，执行：")
    print("  copy(localStorage.getItem('token'))")
    print("在下方粘贴结果（输入不回显；不要把 Token 发到聊天中）。")
    token = validate_token(getpass.getpass("AutoDL 网页登录 Token: "))
    if not token:
        raise ConfigError("Token 不能为空")
    api = AutoDL(token)
    api.images()  # Read-only authenticated check before saving.
    write_private_json(args.auth_file, {"token": token})
    print(f"登录验证成功，已保存到本机：{args.auth_file}")


def browser_login(args):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ConfigError("浏览器登录需先安装：python3 -m pip install -r requirements-login.txt；python3 -m playwright install chromium") from None
    profile = args.auth_file.parent / "browser-profile"
    profile.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        profile.chmod(0o700)
    token = ""
    with sync_playwright() as browser:
        options = {"headless": False}
        if args.browser == "chrome":
            options["channel"] = "chrome"
        context = browser.chromium.launch_persistent_context(str(profile), **options)
        try:
            page = context.new_page()
            page.goto("https://www.autodl.com/login?url=/market/list", wait_until="domcontentloaded")
            print("请在弹出的浏览器中手动完成登录/验证码；程序只保存本站的登录结果。")
            end = time.monotonic() + args.timeout
            rejected_tokens = set()
            while time.monotonic() < end:
                for candidate_page in context.pages:
                    if candidate_page.url.startswith("https://www.autodl.com/"):
                        try:
                            token = candidate_page.evaluate("() => localStorage.getItem('token') || ''")
                        except Exception:
                            continue  # Login redirects can destroy the page execution context.
                        if token and token not in rejected_tokens:
                            token = validate_token(token)
                            try:
                                AutoDL(token).images()
                            except APIError as exc:
                                if exc.code != "AuthorizeFailed":
                                    raise
                                rejected_tokens.add(token)
                                token = ""
                                continue
                            break
                        token = ""
                if token and token not in rejected_tokens:
                    break
                page.wait_for_timeout(1000)
            if not token:
                raise ConfigError("登录等待超时，尚未取得本站 Token")
            write_private_json(args.auth_file, {"token": token})
        finally:
            context.close()
    print(f"登录成功，Token 已保存到本机：{args.auth_file}")


def catalog(args):
    api = make_api(args, required=args.kind == "images")
    if args.kind == "regions":
        for row in api.regions():
            print(f"{row['region_name']}\t{row['region_sign']}\t{row.get('data_center', '')}")
    elif args.kind == "gpus":
        for row in api.gpus():
            print(f"{row['gpu_name']}\t{row.get('gpu_memory', 0) / 1024**3:g} GiB\t{row.get('chip_corp', '')}")
    else:
        for row in api.images():
            print(f"{row['uuid']}\t{row['label']}")


def run(args):
    config = load_config(args.config)
    if config.state_file == args.auth_file:
        raise ConfigError("state_file 不能覆盖认证文件")
    # Lock before loading the ledger: two processes cannot both see an empty job.
    with state_lock(config.state_file):
        state = State(config)
        api = make_api(args, required=True, config=config)
        result = Runner(config, api, state, execute=args.execute).run(once=args.once)
        if args.execute and state.count():
            print(f"实例清单已写入 {config.state_file}；停止脚本不会关闭这些实例。")
        return result


def status(args):
    config = load_config(args.config)
    with state_lock(config.state_file):
        state = State(config)
        print(f"策略: {config.policy}；停止条件满足: {state.done()}；小时总价上界: ¥{state.hourly_total}")
        for target in config.targets:
            print(f"{target.name}: {state.count(target.name)}/{target.quantity} 个实例")
        print(json.dumps({"successes": state.data["successes"], "pending": state.pending}, ensure_ascii=False, indent=2))


def resolve(args):
    config = load_config(args.config)
    with state_lock(config.state_file):
        state = State(config)
        if not state.pending:
            print("没有待核对请求。")
            return
        if args.not_created:
            if not args.checked_console:
                raise ConfigError("请先在 AutoDL 控制台确认没有创建；确认后加 --checked-console 才能清除 pending")
            state.reject("user-confirmed-not-created")
            print("已记录人工核对结果，可以重新运行。")
        else:
            identifier = args.instance or state.pending.get("instance_uuid")
            if not identifier:
                raise ConfigError("pending 中没有实例 ID。确认已创建后使用 --instance 指定控制台中的对应实例 ID")
            existing = state.pending.get("instance_uuid")
            if existing and existing != identifier:
                raise ConfigError("实例 ID 与已记录的创建响应不符，不能替换")
            api = make_api(args, required=True, config=config)
            if not api.exists(identifier):
                raise ConfigError("该实例尚未确认存在，保留 pending，请核对控制台")
            state.attach_instance(identifier)
            state.confirm()
            print(f"已确认实例 {identifier} 并计入目标数量。")


def demo(args):
    from .demo import DemoAPI
    with tempfile.TemporaryDirectory(prefix="autodl-demo-") as folder:
        path = Path(folder) / "demo.json"
        raw = example_config(args.policy)
        raw["image"]["uuid"] = "demo-image"
        raw["poll"]["jitter_seconds"] = 0
        write_private_json(path, raw)
        config = load_config(path)
        state = State(config)
        print("离线模拟：所有接口使用内存假数据，不连接 AutoDL，不产生费用。", flush=True)
        result = Runner(config, DemoAPI(), state, execute=True).run(once=True)
        print(f"模拟结果：{state.count()} 个实例；策略 {args.policy}；达标 {state.done()}")
        return result


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="AutoDL 普通算力市场租用工具，默认只预览；真实租用需 --execute")
    root.add_argument("--auth-file", type=Path, default=ROOT / ".local/auth.json", help="本机认证文件（默认项目下 .local/auth.json）")
    sub = root.add_subparsers(dest="command", required=True)
    item = sub.add_parser("auth", help="隐藏输入网页登录 Token，并只读验证")
    item.set_defaults(handler=authenticate)
    item = sub.add_parser("login", help="打开独立浏览器手动登录（可选 Playwright 依赖）")
    item.add_argument("--browser", choices=("chromium", "chrome"), default="chromium")
    item.add_argument("--timeout", type=int, default=300)
    item.set_defaults(handler=browser_login)
    item = sub.add_parser("catalog", help="查询当前地区、GPU 和基础镜像目录")
    item.add_argument("kind", choices=("regions", "gpus", "images"))
    item.set_defaults(handler=catalog)
    item = sub.add_parser("init", help="交互选择地区/型号/配额/策略，生成配置")
    item.add_argument("--config", type=Path, default=Path("config.json"))
    item.add_argument("--template", action="store_true", help="只生成离线配置模板")
    item.set_defaults(handler=create_config)
    item = sub.add_parser("run", help="预览一轮；--execute 持续真实租用")
    item.add_argument("--config", type=Path, default=Path("config.json"))
    item.add_argument("--execute", action="store_true", help="允许提交按量付费实例创建请求")
    item.add_argument("--once", action="store_true", help="真实执行也只检查一轮（可能创建多个实例）")
    item.set_defaults(handler=run)
    for name, handler in (("status", status), ("resolve", resolve)):
        item = sub.add_parser(name, help="查看任务进度" if name == "status" else "核对并处理未确认请求")
        item.add_argument("--config", type=Path, default=Path("config.json"))
        if name == "resolve":
            choice = item.add_mutually_exclusive_group()
            choice.add_argument("--instance", help="人工核对后指定对应实例 ID，再只读验证存在")
            choice.add_argument("--not-created", action="store_true", help="人工核对确实没有创建")
            item.add_argument("--checked-console", action="store_true", help="确认已在控制台检查没有创建")
        item.set_defaults(handler=handler)
    item = sub.add_parser("demo", help="离线演示策略，不访问网络，不产生费用")
    item.add_argument("--policy", choices=("any", "all", "total"), default="any")
    item.set_defaults(handler=demo)
    return root


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    args = parser().parse_args(argv)
    args.auth_file = args.auth_file.expanduser().resolve()
    try:
        return args.handler(args) or 0
    except UncertainResult as exc:
        LOG.error("%s", exc)
        return 4
    except (ConfigError, APIError, OSError, ValueError) as exc:
        LOG.error("%s", exc)
        return 2
    except (KeyboardInterrupt, EOFError):
        LOG.info("已停止；如果已有创建请求，请先用 status 查看记录。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
