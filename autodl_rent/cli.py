from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import sys
import tempfile
import time
import webbrowser
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from . import __version__
from .api import APIError, AutoDL
from .config import ConfigError, config_from_dict, example_config, is_cpu_model, load_config, number
from .onboarding import create_config as build_config
from .output import Result, agent_manifest, config_schema, envelope, plan, resource_text, say, snapshot, table
from .paths import ROOT, default_auth_file, user_home
from .qr_auth import login_with_qr
from .runner import Runner, UncertainResult
from .state import State, state_lock, write_private_json

LOG = logging.getLogger("autodl_rent")


class UsageError(ValueError):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


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


def make_api(args, *, required=False, config=None) -> AutoDL:
    token = read_token(args.auth_file)
    if required and not token:
        raise ConfigError("尚未认证：已关联微信可运行 login；未关联微信可运行 auth 输入网页登录 Token，或在本机设置 AUTODL_WEB_TOKEN；Token 不应通过聊天发送")
    poll = config.poll if config else {}
    api = AutoDL(token, timeout=poll.get("timeout_seconds", 20), request_gap=poll.get("request_gap_seconds", 1))
    try:
        api.version()
    except APIError as exc:
        if not exc.transient or exc.retry_after:
            raise
        LOG.warning("暂时无法获取网页版本 (%s)，目录/库存查询将独立检查连接", exc.code)
    return api


def task_config(args):
    if not args.config.expanduser().is_file():
        raise ConfigError(f"没有找到配置 {args.config}；先运行 init，或用 --config 指定已有配置")
    config = load_config(args.config)
    if config.path == args.auth_file or config.state_file == args.auth_file:
        raise ConfigError("配置文件和 state_file 不能覆盖认证文件")
    return config


def authenticate(args):
    token = validate_token(os.environ.get("AUTODL_WEB_TOKEN", ""))
    if not token:
        if args.non_interactive:
            raise ConfigError("非交互 auth 需要本机 AUTODL_WEB_TOKEN；未设置时，请在本机终端运行 auth，通过隐藏输入提供网页登录 Token")
        say(args, "在已登录的 AutoDL 网页开发者工具 Console 执行：copy(localStorage.getItem('token'))")
        say(args, "在下方粘贴结果（隐藏输入；不要将 Token 发到聊天中）。")
        token = validate_token(getpass.getpass("AutoDL 网页登录 Token: "))
    if not token:
        raise ConfigError("Token 不能为空")
    AutoDL(token).images()
    write_private_json(args.auth_file, {"token": token})
    say(args, f"登录验证成功，已保存到本机：{args.auth_file}")
    return Result({"authenticated": True, "auth_file": str(args.auth_file)})


def browser_login(args):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ConfigError("浏览器登录助手需要可选依赖 Playwright；无需安装依赖的方式为 auth 导入网页登录 Token，或 login 使用已关联微信扫码") from None
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
            print("请在弹出的浏览器中手动完成登录/验证码。", file=sys.stderr, flush=True)
            end = time.monotonic() + args.timeout
            rejected_tokens = set()
            while time.monotonic() < end:
                for candidate_page in context.pages:
                    if candidate_page.url.startswith("https://www.autodl.com/"):
                        try:
                            token = candidate_page.evaluate("() => localStorage.getItem('token') || ''")
                        except Exception:
                            continue
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
    say(args, f"登录成功，已保存到本机：{args.auth_file}")
    return Result({"authenticated": True, "auth_file": str(args.auth_file), "method": "browser"})


def login(args):
    number(args.timeout, "--timeout", 1, 3600)
    if args.method == "browser" or args.browser:
        return browser_login(args)
    api = AutoDL()

    def show(path):
        if args.json:
            print(json.dumps({"event": "login_qr", "path": str(path)}, ensure_ascii=False), file=sys.stderr, flush=True)
        else:
            print(f"请用已关联 AutoDL 账号的微信扫描二维码：{path}", flush=True)
        if not args.no_open and not args.non_interactive:
            webbrowser.open(path.as_uri())

    token = validate_token(login_with_qr(api, args.auth_file.parent / "login-qr.jpg", timeout=args.timeout, show=show))
    if not token:
        raise ConfigError("扫码登录未返回有效 Token")
    api.token = token
    api.images()
    write_private_json(args.auth_file, {"token": token})
    say(args, f"登录成功，认证已保存在本机：{args.auth_file}\n下一步：init 交互配置，或 quick 预览低于 1 元的资源。")
    return Result({"authenticated": True, "auth_file": str(args.auth_file), "method": "qr"})


def catalog(args):
    api = make_api(args, required=args.kind == "images")
    if args.kind == "regions":
        rows = api.regions()
        headers = ("地区", "分区标识", "数据中心")
        cells = [(r["region_name"], r["region_sign"], r.get("data_center", "")) for r in rows]
    elif args.kind == "gpus":
        rows = api.gpus()
        headers = ("型号", "显存 GiB", "芯片")
        cells = [(r["gpu_name"], f"{r.get('gpu_memory', 0) / 1024**3:g}", r.get("chip_corp", "")) for r in rows]
    else:
        rows = api.images()
        headers = ("镜像 UUID", "镜像")
        cells = [(r["uuid"], r["label"]) for r in rows]
    say(args, table(headers, cells))
    return Result({"kind": args.kind, "items": rows})


def create_config(args):
    return build_config(args, make_api)


def show_plan(args, config):
    say(args, "真实租用 · 允许创建按量付费实例" if args.execute else "预览 · 只查询库存")
    say(args, table(("目标", "地区", "型号", "实例配额", "每实例单位数", "单单位上限/时"), [
        (t.name, ", ".join(t.regions), ", ".join(t.gpu_models), t.quantity, t.gpus_per_instance,
         f"¥{t.max_price_per_gpu_hour}") for t in config.targets]))
    say(args, f"停止策略：{config.policy} · 小时总价上限：¥{config.max_total_price_per_hour} · 最长等待：{config.max_wait_seconds:g} 秒")


def run_task(args, config, state, api):
    if args.wait_seconds is not None:
        config = replace(config, max_wait_seconds=number(args.wait_seconds, "--wait-seconds", 1, 31536000))
    show_plan(args, config)
    runner = Runner(config, api, state, execute=args.execute)
    args.result = Result({"mode": "execute" if args.execute else "preview", "plan": plan(config)})
    try:
        code = runner.run(once=args.once)
    finally:
        args.result.data.update(snapshot(config, state))
        if not args.execute:
            args.result.data.update(offers=runner.preview, matches=runner.preview_count,
                                    offers_truncated=runner.preview_count > len(runner.preview))
    if not args.execute:
        say(args, table(("目标", "地区", "型号", "单位数", "原价/时", "主机"), [
            (r["target"], r["region_name"], r["gpu_model"], r["gpus"], f"¥{r['price_per_hour']}", r["machine_id"])
            for r in runner.preview[:20]]))
        say(args, f"找到 {runner.preview_count} 个候选，显示前 {min(20, runner.preview_count)} 个；预览不会创建实例。")
        if code:
            say(args, "预览未完成；请检查错误信息，修复连接或配置后重试。")
        elif state.done():
            say(args, "停止条件已满足；没有提交新请求。")
        elif not runner.preview_count:
            say(args, "当前没有符合条件的库存；可检查筛选条件，或加 --execute 在限定条件内等待。")
    elif state.count():
        say(args, table(("实例 ID", "型号", "地区", "原价/时"), [
            (r["instance_uuid"], r["gpu_model"], r["region_name"], f"¥{r['price_per_hour']}")
            for r in state.data["successes"]]))
        say(args, f"已记录 {state.count()} 个实例：{state.path}；停止工具不会关闭这些实例。")
    if code == 3:
        say(args, "本次等待结束，目标尚未达标；继续运行同一配置可恢复进度。")
    args.result.code = code
    if code and runner.last_error:
        exc = runner.last_error
        args.result.error = {"type": "api", "message": str(exc), "code": exc.code,
                             "transient": exc.transient, "retry_after": exc.retry_after}
    return args.result


def run(args):
    config = task_config(args)
    with state_lock(config.state_file):
        state = State(config)
        return resume_task(args, config, state)


def resume_task(args, config, state, api=None):
    args.result = Result(snapshot(config, state))
    if args.wait_seconds is not None:
        number(args.wait_seconds, "--wait-seconds", 1, 31536000)
    if state.pending and (not args.execute or not state.pending.get("instance_uuid")):
        raise UncertainResult("存在待核对创建结果；请先用 status 查看，再核对控制台并用 resolve 处理")
    if not state.done() or state.pending:
        api = api or make_api(args, required=True, config=config)
    return run_task(args, config, state, api)


def quick(args):
    from .quick_rent import select_quick_image
    if args.wait_seconds is not None:
        number(args.wait_seconds, "--wait-seconds", 1, 31536000)
    path = args.config.expanduser().resolve()
    state_name = "west-under1" if path.name == "config.west-under1.local.json" else path.stem
    state_path = (path.parent / ".local" / f"{state_name}.state.json").resolve()
    if path == args.auth_file or state_path == args.auth_file:
        raise ConfigError("快速任务路径不能覆盖认证文件")
    with state_lock(state_path):
        if state_path.exists() and not path.is_file():
            raise ConfigError("已有任务状态但配置缺失；请先核对旧任务")
        api = None
        if path.exists():
            config = load_config(path)
            if config.state_file != state_path:
                raise ConfigError("配置中的任务状态路径不符")
        else:
            api = make_api(args, required=True)
            models = [g["gpu_name"] for g in api.gpus()
                      if g.get("chip_corp") == "nvidia" or is_cpu_model(g.get("gpu_name", ""))]
            image = select_quick_image(api.images())
            raw = example_config("any")
            raw.update(max_total_price_per_hour=0.999, state_file=str(state_path))
            raw["image"] = dict(kind="base", **{key: image[key] for key in ("uuid", "cuda_version", "chip_corp", "cpu_arch")})
            raw["targets"] = [{"name": "西北B区低于1元任意实例", "regions": ["西北B区"], "gpu_models": models,
                               "quantity": 1, "gpus_per_instance": 1, "max_price_per_gpu_hour": 0.999}]
            raw["poll"].update(interval_seconds=5, jitter_seconds=1)
            config = config_from_dict(raw, path)
            write_private_json(path, raw)
        if config.policy != "any" or len(config.targets) != 1:
            raise ConfigError("quick 只允许任意一个成功即停的单目标任务")
        target = config.targets[0]
        if target.regions != ("西北B区",) or target.quantity != 1 or target.gpus_per_instance != 1:
            raise ConfigError("quick 只允许西北B区的一个单单位实例")
        if target.max_price_per_gpu_hour >= Decimal(1) or config.max_total_price_per_hour >= Decimal(1):
            raise ConfigError("quick 小时价格上限必须严格低于 1 元")
        return resume_task(args, config, State(config), api)


def status(args):
    config = task_config(args)
    with state_lock(config.state_file):
        state = State(config)
        data = snapshot(config, state)
        say(args, f"策略: {config.policy}；停止条件满足: {state.done()}；小时总价上界: ¥{state.hourly_total}")
        for target in config.targets:
            say(args, f"{target.name}: {state.count(target.name)}/{target.quantity} 个实例")
        say(args, json.dumps({"successes": data["successes"], "pending": data["pending"]}, ensure_ascii=False, indent=2))
        return Result(data)


def resolve(args):
    if args.checked_console and not args.not_created:
        raise ConfigError("--checked-console 需要与 --not-created 一起使用")
    config = task_config(args)
    with state_lock(config.state_file):
        state = State(config)
        args.result = Result(snapshot(config, state))
        if not state.pending:
            say(args, "没有待核对请求。")
            return args.result
        if args.not_created:
            if not args.checked_console:
                raise ConfigError("请先在 AutoDL 控制台确认没有创建；确认后加 --checked-console 才能清除 pending")
            state.reject("user-confirmed-not-created")
            say(args, "已记录人工核对结果，可以重新运行。")
        else:
            identifier = args.instance or state.pending.get("instance_uuid")
            if not identifier:
                raise ConfigError("pending 中没有实例 ID；请核对控制台，确认已创建后用 --instance 指定对应 ID")
            existing = state.pending.get("instance_uuid")
            if existing and existing != identifier:
                raise ConfigError("实例 ID 与已记录的创建响应不符，不能替换")
            if not make_api(args, required=True, config=config).exists(identifier):
                raise UncertainResult("实例尚未确认存在，保留 pending，请核对控制台")
            state.attach_instance(identifier)
            state.confirm()
            say(args, f"已确认实例 {identifier} 并计入目标数量。")
        return Result(snapshot(config, state))


def validate(args):
    config = task_config(args)
    with state_lock(config.state_file):
        state = State(config)
        args.result = Result({"valid": True, "plan": plan(config), **snapshot(config, state)})
        if args.online:
            Runner(config, make_api(args, required=True, config=config), state, execute=True).prepare()
        args.result.data.update(image_required=not bool(config.image["uuid"]), online_checked=args.online)
        say(args, f"配置有效：{config.path}\n状态路径：{config.state_file}\n小时总价上限：¥{config.max_total_price_per_hour}；停止策略：{config.policy}")
        if not config.image["uuid"]:
            say(args, "真实执行前还需填写 image.uuid；运行 catalog images 查看目录。")
        if state.pending:
            say(args, "存在待核对创建结果；请运行 status / resolve。")
    return args.result


def doctor(args):
    checks = [{"name": "python", "ok": True, "detail": sys.version.split()[0]}]
    token = ""
    try:
        token = read_token(args.auth_file)
        checks.append({"name": "auth", "ok": bool(token), "detail": "已配置（未联网验证）" if token else "未登录，运行 login"})
    except ConfigError as exc:
        checks.append({"name": "auth", "ok": False, "detail": str(exc)})
    args.result = Result({"checks": checks, "auth_file": str(args.auth_file), "home": str(user_home()),
                          "config_file": str(args.config.expanduser().resolve()), "online_checked": False})
    try:
        if args.config.expanduser().exists():
            config = task_config(args)
            with state_lock(config.state_file):
                state = State(config)
                checks.append({"name": "config", "ok": True, "detail": "配置和状态可读取"})
                checks.append({"name": "image", "ok": bool(config.image["uuid"]), "detail": "镜像已填写" if config.image["uuid"] else "需填写 image.uuid"})
                checks.append({"name": "ledger", "ok": not bool(state.pending), "detail": "有 pending，先运行 resolve" if state.pending else "没有待核对请求"})
        else:
            checks.append({"name": "config", "ok": False, "detail": "尚未配置，运行 init"})
        if args.online:
            api = make_api(args)
            api.regions()
            if token:
                api.images()
            checks.append({"name": "network", "ok": True, "detail": "只读接口可访问"})
            args.result.data["online_checked"] = True
    except (ConfigError, APIError, OSError, ValueError) as exc:
        checks.append({"name": "check", "ok": False, "detail": str(exc)})
        args.result.code = 2
    args.result.data["ready"] = all(c["ok"] for c in checks)
    say(args, table(("检查", "结果", "说明"), [(c["name"], "OK" if c["ok"] else "待处理", c["detail"]) for c in checks]))
    say(args, f"认证文件：{args.auth_file}\n任务配置：{args.config.expanduser().resolve()}")
    return args.result


def demo(args):
    from .demo import DemoAPI
    with tempfile.TemporaryDirectory(prefix="autodl-demo-") as folder:
        path = Path(folder) / "demo.json"
        raw = example_config(args.policy)
        raw["image"]["uuid"] = "demo-image"
        raw["poll"]["jitter_seconds"] = 0
        config = config_from_dict(raw, path)
        state = State(config)
        say(args, "离线模拟：不连接 AutoDL，不产生费用。")
        code = Runner(config, DemoAPI(), state, execute=True).run(once=True)
        data = snapshot(config, state)
        data.pop("state_file")
        data["simulation"] = True
        say(args, f"模拟结果：{state.count()} 个实例；策略 {args.policy}；达标 {state.done()}")
        return Result(data, code)


def agent(args):
    if args.action == "skill":
        text = resource_text("autodl-renter/SKILL.md")
        say(args, text.rstrip())
        return Result({"skill": text})
    data = config_schema() if args.action == "schema" else agent_manifest()
    say(args, json.dumps(data, ensure_ascii=False, indent=2))
    return Result({"schema": data} if args.action == "schema" else data)


def add_common(item, *, root=False):
    default = lambda value: value if root else argparse.SUPPRESS
    item.add_argument("--json", action="store_true", default=default(False), help="输出单个 JSON 结果；进度写入 stderr，不弹出输入提示")
    item.add_argument("--non-interactive", action="store_true", default=default(False), help="禁止终端输入提示，适用于 agent 和脚本")
    item.add_argument("--quiet", action="store_true", default=default(False), help="隐藏常规进度日志")
    item.add_argument("--auth-file", type=Path, default=default(default_auth_file() if root else None), help="本机认证文件")
    item.add_argument("--config", type=Path, default=default(Path("config.json")), help="任务配置（默认当前目录 config.json）")


def parser() -> argparse.ArgumentParser:
    root = Parser(prog="python3 -m autodl_rent", description="AutoDL 算力抢租 · 零依赖，默认预览，--execute 才会真实租用",
                  epilog="无需安装，在源码目录直接运行。\n身份认证：login（已关联微信）或 auth（网页登录 Token，无需微信绑定）。\n使用流程：demo → 认证 → init → run → run --execute",
                  formatter_class=argparse.RawDescriptionHelpFormatter, allow_abbrev=False)
    root.add_argument("--version", action="version", version=f"autodl-renter {__version__}")
    add_common(root, root=True)
    sub = root.add_subparsers(dest="command", title="命令")

    def command(name, help_text, handler):
        item = sub.add_parser(name, help=help_text, description=help_text, allow_abbrev=False)
        add_common(item)
        item.set_defaults(handler=handler)
        return item

    command("auth", "导入并验证网页登录 Token，无需微信绑定", authenticate)
    item = command("login", "微信扫码登录（默认，零依赖），或选择浏览器登录", login)
    item.add_argument("--method", choices=("qr", "browser"), default="qr")
    item.add_argument("--browser", choices=("chromium", "chrome"), help="使用可选浏览器登录助手")
    item.add_argument("--timeout", type=float, default=300, help="登录最长等待秒数（默认 300）")
    item.add_argument("--no-open", action="store_true", help="只显示二维码路径，不自动打开")
    item = command("catalog", "查询实时地区、GPU / CPU 和基础镜像目录", catalog)
    item.add_argument("kind", choices=("regions", "gpus", "images"))
    item = command("init", "交互向导，或通过完整参数生成配置；不会覆盖已有文件", create_config)
    item.add_argument("--template", action="store_true", help="离线生成待编辑模板")
    item.add_argument("--region", action="append", help="完整地区名或分区标识，可重复")
    item.add_argument("--gpu", action="append", help="完整 GPU / CPU 型号，可重复；每种型号建立一个目标")
    item.add_argument("--max-price", type=float, help="每资源单位的小时原价上限（元）")
    item.add_argument("--budget", type=float, help="本任务实例的小时总价上限（元），不是累计支出预算")
    item.add_argument("--quantity", type=int, default=1, help="每种型号的实例配额（默认 1）")
    item.add_argument("--gpus-per-instance", type=int, default=1, help="每实例资源单位数（默认 1）")
    item.add_argument("--policy", choices=("any", "all", "total"), default="any")
    item.add_argument("--total", type=int, help="total 策略的累计实例数")
    item.add_argument("--image", help="基础镜像 UUID，来自 catalog images")
    item.add_argument("--cuda", default="12.8", help="镜像实际 CUDA 版本（默认 12.8）")
    item.add_argument("--wait-seconds", type=float, default=1800, help="每次启动最长等待秒数（默认 1800）")
    item.add_argument("--selection", choices=("priority", "cheapest"), default="priority")
    item.add_argument("--state-file", type=Path, help="独立任务状态文件，相对于配置所在目录")
    for name, handler, help_text in (
        ("run", run, "预览库存；--execute 在配置的范围内实际抢租"),
        ("quick", quick, "西北B区、GPU / CPU、严格低于 1 元/时、成功一个即停；默认预览"),
    ):
        item = command(name, help_text, handler)
        if name == "quick":
            item.set_defaults(quick_default=True)
        item.add_argument("--execute", action="store_true", help="允许创建按量付费实例")
        item.add_argument("--once", action="store_true", help="实际租用也只扫描一轮，可能创建多个实例")
        item.add_argument("--wait-seconds", type=float, help="仅覆盖本次最长等待秒数，不重置任务")
    command("status", "离线查看任务进度、实例和待核对请求", status)
    item = command("resolve", "核对并处理未确认的创建结果", resolve)
    choice = item.add_mutually_exclusive_group()
    choice.add_argument("--instance", help="控制台中核对得到的实例 ID，随后只读确认存在")
    choice.add_argument("--not-created", action="store_true", help="确认请求没有创建实例")
    item.add_argument("--checked-console", action="store_true", help="已经在控制台核对确实没有创建")
    for name, handler, help_text in (
        ("validate", validate, "离线校验配置和任务记录；可选联网核对目录与镜像"),
        ("doctor", doctor, "检查本机配置和认证；可选只读联网诊断"),
    ):
        item = command(name, help_text, handler)
        item.add_argument("--online", action="store_true", help="额外执行只读联网检查，不创建实例")
    item = command("demo", "离线体验停止策略，不连接 AutoDL，不产生费用", demo)
    item.add_argument("--policy", choices=("any", "all", "total"), default="any")
    item = command("agent", "查看 agent 协议、配置 JSON Schema，或导出操作 Skill", agent)
    item.add_argument("action", choices=("describe", "schema", "skill"), nargs="?", default="describe")
    return root


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in argv
    args = None
    result, error = Result(), None
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    old_level, old_propagate = LOG.level, LOG.propagate
    LOG.addHandler(handler)
    LOG.propagate = False
    LOG.setLevel(logging.INFO)
    try:
        root = parser()
        args = root.parse_args(argv)
        json_mode = args.json
        args.auth_file = args.auth_file.expanduser().resolve()
        args.non_interactive = args.non_interactive or args.json or not sys.stdin.isatty()
        if args.quiet:
            LOG.setLevel(logging.ERROR)
        if getattr(args, "quick_default", False) and not any(v == "--config" or v.startswith("--config=") for v in argv):
            args.config = Path("config.west-under1.local.json")
        if args.command is None:
            if json_mode:
                result = Result(agent_manifest())
            else:
                root.print_help()
        else:
            result = args.handler(args)
    except (UncertainResult, ConfigError, APIError, OSError, ValueError, KeyboardInterrupt, EOFError) as exc:
        result = getattr(args, "result", Result())
        if isinstance(exc, UncertainResult):
            result.code, kind = 4, "pending_reconciliation"
        elif isinstance(exc, (KeyboardInterrupt, EOFError)):
            result.code, kind = 130, "interrupted"
        else:
            result.code, kind = 2, "usage" if isinstance(exc, UsageError) else "api" if isinstance(exc, APIError) else "config"
        message = str(exc) or "已停止；已有创建请求请先用 status 查看记录。"
        error = {"type": kind, "message": message}
        if isinstance(exc, APIError):
            error.update(code=exc.code, transient=exc.transient, retry_after=exc.retry_after)
        LOG.error("%s", message)
    finally:
        LOG.removeHandler(handler)
        handler.close()
        LOG.setLevel(old_level)
        LOG.propagate = old_propagate
    if json_mode:
        print(json.dumps(envelope(getattr(args, "command", None), result, error), ensure_ascii=False, allow_nan=False))
    return result.code


if __name__ == "__main__":
    sys.exit(main())
