"""Interactive setup and its equivalent deterministic command-line flags."""
from __future__ import annotations

from decimal import Decimal

from .config import ConfigError, config_from_dict, example_config, is_cpu_model
from .output import Result, plan, say
from .state import write_private_json


def choose_many(title: str, values: list[str], *, single=False) -> list[str]:
    if not values:
        raise ConfigError("目录中没有可选项，请稍后重试或检查账号权限")
    print(title, flush=True)
    for n, value in enumerate(values, 1):
        print(f"  {n:>2}. {value}")
    while True:
        raw = input("输入编号: " if single else "输入编号，可用逗号多选: ").replace("，", ",")
        try:
            indices = list(dict.fromkeys(int(i.strip()) - 1 for i in raw.split(",")))
            if not indices or any(i < 0 or i >= len(values) for i in indices) or (single and len(indices) != 1):
                raise ValueError
            return [values[i] for i in indices]
        except ValueError:
            print("请选择一个有效编号" if single else "请输入有效编号，例如 1,3")


def prompt_int(title: str, default=1, maximum=10000) -> int:
    while True:
        raw = input(f"{title} [{default}]: ").strip()
        try:
            result = int(raw or str(default))
            if not 1 <= result <= maximum:
                raise ValueError
            return result
        except ValueError:
            print(f"请输入 1–{maximum} 的整数")


def prompt_money(title: str, default=None) -> float:
    while True:
        raw = input(title + (f" [{default}]" if default is not None else "") + ": ").strip()
        try:
            result = Decimal(raw) if raw else default
            if result is None or not result.is_finite() or not 0 < result <= Decimal("1e100"):
                raise ValueError
            return float(result)
        except (ValueError, ArithmeticError):
            print("请输入有效正金额，单位为元")


def create_config(args, api_factory) -> Result:
    path = args.config.expanduser().resolve()
    if path.exists():
        raise ConfigError(f"配置已存在：{path}；请直接编辑，或用 --config 指定新文件")
    if path == args.auth_file:
        raise ConfigError("配置文件不能覆盖认证文件")
    raw = example_config(args.policy)
    fields = (args.region, args.gpu, args.max_price, args.budget)
    has_flags = any(value is not None for value in fields)
    if args.template and has_flags:
        raise ConfigError("--template 与 --region / --gpu / --max-price / --budget 不能同时使用")
    if has_flags:
        if any(value is None for value in fields):
            raise ConfigError("非交互配置需要同时指定 --region、--gpu、--max-price、--budget")
        if args.policy == "total" and args.total is None:
            raise ConfigError("--policy total 需要 --total 指定累计实例数")
        if args.total is not None and args.policy != "total":
            raise ConfigError("--total 仅用于 --policy total")
        raw["targets"] = [{"name": model, "regions": args.region, "gpu_models": [model],
                           "quantity": args.quantity, "gpus_per_instance": args.gpus_per_instance,
                           "max_price_per_gpu_hour": args.max_price} for model in args.gpu]
        raw["stop"] = {"policy": args.policy}
        if args.total is not None:
            raw["stop"]["total_instances"] = args.total
        raw["max_total_price_per_hour"] = args.budget
        raw["image"].update(uuid=args.image or "", cuda_version=args.cuda)
    elif not args.template:
        if args.non_interactive:
            raise ConfigError("非交互模式不能打开向导；使用 init --template 或指定 --region、--gpu、--max-price、--budget")
        api = api_factory(args)
        regions = list(dict.fromkeys(r["region_name"] for r in api.regions() if r.get("visible", "all") == "all"))
        gpus = [g["gpu_name"] for g in api.gpus() if g.get("chip_corp") == "nvidia" or is_cpu_model(g["gpu_name"])]
        print("配置向导 · 选择资源 → 设置预算 → 选择镜像；Ctrl+C 可退出。")
        selected_regions = choose_many("[1/4] 允许租用的地区", regions)
        models = choose_many("[2/4] GPU / CPU 型号（每种型号建立独立目标）", gpus)
        policy = choose_many("[3/4] 停止策略", ["any：成功任意一个实例", "all：每个目标达标", "total：累计实例数达标"], single=True)
        raw["stop"]["policy"] = policy[0].split("：")[0]
        raw["targets"] = []
        budget = Decimal(0)
        for model in models:
            quantity = prompt_int(f"{model} 需要几个实例")
            cards = 1 if is_cpu_model(model) else prompt_int(f"{model} 每个实例需要几块 GPU", maximum=16)
            cap = prompt_money(f"{model} 每资源单位的小时原价上限（元）")
            raw["targets"].append({"name": model, "regions": selected_regions, "gpu_models": [model],
                                   "quantity": quantity, "gpus_per_instance": cards, "max_price_per_gpu_hour": cap})
            budget += quantity * cards * Decimal(str(cap))
        if raw["stop"]["policy"] == "total":
            raw["stop"]["total_instances"] = prompt_int("累计几个实例后停止", maximum=sum(t["quantity"] for t in raw["targets"]))
        raw["max_total_price_per_hour"] = prompt_money("本任务所有实例的小时总价上限（元）", budget)
        if api.token:
            images = api.images()
            labels = [f"{i['label']} | {i['uuid']}" for i in images]
            selected = choose_many("[4/4] 基础镜像（CUDA 必须兼容所选型号）", labels, single=True)
            image = images[labels.index(selected[0])]
            raw["image"] = dict(kind="base", **{k: image[k] for k in ("uuid", "cuda_version", "chip_corp", "cpu_arch")})
        else:
            print("[4/4] 尚未登录，保留空镜像；登录后通过 catalog images 选择。")
    raw["selection"] = args.selection
    raw["max_wait_seconds"] = args.wait_seconds
    raw["state_file"] = str(args.state_file) if args.state_file else f".local/{path.stem}.state.json"
    config = config_from_dict(raw, path)
    if config.state_file == args.auth_file:
        raise ConfigError("state_file 不能覆盖认证文件")
    # A path with an existing ledger is never a fresh task, even without config.
    if config.state_file.exists():
        raise ConfigError("该 state_file 已有任务记录；请保留旧任务，使用不同配置和状态路径")
    write_private_json(path, raw)
    say(args, f"已生成配置：{path}")
    if not raw["image"]["uuid"]:
        say(args, "下一步：运行 catalog images，将镜像 UUID 和 CUDA 版本填入配置。")
    say(args, f"预览：python3 -m autodl_rent run --config {path.name}")
    return Result({"plan": plan(config), "image_required": not bool(raw["image"]["uuid"])})
