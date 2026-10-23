# 配置与停止策略

[返回首页](../README.md) · [使用与恢复](usage.md) · [价格与接口说明](implementation.md)

## 停止策略

| `stop.policy` | 行为 | 适合的需求 |
| --- | --- | --- |
| `any` | 任意一个实例创建并确认存在后立即停止 | “4090 或 3090，抢到任意一个就行” |
| `all` | 每个目标都达到各自的 `quantity` 后停止 | “4090 一台、3090 一台，两种都要” |
| `total` | 累计成功 `total_instances` 个实例后停止，同时遵守各目标配额 | “两种型号都可，累计抢到三台” |

**数量单位是实例。** `quantity: 2, gpus_per_instance: 1` 是两个单卡实例；`quantity: 1, gpus_per_instance: 2` 是一个双卡实例。`any` 总是在第一个实例成功后停止，即使该目标的 `quantity` 大于 1。

一个目标中多个 `regions` 是任选地区，多个 `gpu_models` 是任选型号。需要“每种型号都抢到”时，将每种型号写成独立目标；需要“每个地区都抢到”时，也应拆成独立目标。一次创建只计入一个目标。

`selection: "priority"` 按目标在配置中的顺序尝试，同一目标按列表原价从低到高寻找；发现可用候选立即提交前校验。`selection: "cheapest"` 先扫描所有目标，再按单实例小时原价排序；扫描范围更大，首次提交可能更慢。

## 配置

`init` 自动生成 `config.json`。也可以先生成离线模板，或复制 [config.example.json](../config.example.json) 后修改：

```bash
python3 -m autodl_rent init --template
```

模板中的价格是**配置示例，不是当前市场报价**。模板的 `image.uuid` 留空，需要先选择实际镜像，才允许真实执行。

主要字段：

| 字段 | 含义 |
| --- | --- |
| `targets[].name` | 唯一的目标名称，用于记录进度 |
| `targets[].regions` | 完整地区名或精确的 `region_sign`，非空数组 |
| `targets[].gpu_models` | 完整 GPU / CPU 名称，精确匹配，非空数组 |
| `targets[].quantity` | 此目标最多创建多少个实例 |
| `targets[].gpus_per_instance` | 每个实例的 GPU 数量；同时检查主机起租/倍数规则 |
| `targets[].max_price_per_gpu_hour` | 每块 GPU 每小时的原价上限，元 |
| `targets[].min_gpu_memory_gb` | 可选，最低单卡显存，GiB |
| `targets[].min_cpu_per_gpu` | 可选，每 GPU 至少分配的 CPU 核数 |
| `targets[].min_memory_per_gpu_gb` | 可选，每 GPU 至少分配的内存，GiB |
| `max_total_price_per_hour` | 本任务所有已记录成功实例的小时原价合计上限，元 |
| `max_wait_seconds` | 此次启动最多等待多少秒，默认 1800；重启重新计时 |
| `image.kind` | `base` 基础镜像，或 `private` 私有镜像 |
| `image.uuid` | 实际镜像 UUID，真实执行必填 |
| `image.cuda_version` | 实际镜像的 CUDA 版本，例如字符串 `"12.8"` |
| `image.chip_corp` / `image.cpu_arch` | 当前支持 `nvidia` / `x86` |
| `state_file` | 持久化任务进度；相对路径以配置文件目录为基准 |

例如将配置中的停止条件改成以下内容，就会等待每个目标达标：

```json
"stop": {"policy": "all"}
```

设置累计三个实例时，需要同时给出足够的目标配额，例如 4090 的 `quantity` 为 2，3090 的 `quantity` 为 2：

```json
"stop": {"policy": "total", "total_instances": 3}
```

查询实时目录：

```bash
python3 -m autodl_rent catalog regions
python3 -m autodl_rent catalog gpus
python3 -m autodl_rent catalog images
```

地区完整名称会展开成该名称下的全部分区，例如一个“西北B区”名称可能对应多个 `region_sign`。若只允许其中一个分区，直接填写它的 `region_sign`。GPU 完整名称互不混用：`RTX 4090` 不会匹配到 `RTX 4090D` 或 `vGPU-48GB`。

基础镜像在真实执行前会核对 UUID、CUDA 和架构。私有镜像可从网页获得 UUID，并填写它的实际 CUDA 版本；私有镜像元数据由用户提供，当前未自动查询校验。GPU 候选主机必须支持所填 CUDA，且不能低于网站为对应 GPU 架构提示的 CUDA 下限；CPU 候选不检查 CUDA 驱动。

默认轮询参数可以完整展开为（专用快速入口使用 5 秒间隔与最多 1 秒抖动）：

```json
"poll": {
  "interval_seconds": 10,
  "jitter_seconds": 3,
  "request_gap_seconds": 1,
  "timeout_seconds": 20,
  "max_backoff_seconds": 120,
  "confirm_timeout_seconds": 90,
  "page_size": 90,
  "max_pages": 20
}
```

相邻请求至少间隔 `request_gap_seconds`，轮次间隔加随机抖动。完整检查价格后，优先轮询价格合适的型号（包括暂时租完的主机），每 120 秒重新检查全部型号，发现降价的资源。只读查询遇到限流或暂时网络故障会退避，并遵守服务器的 `Retry-After`；轮询间隔最小 5 秒，请求间隔最小 0.5 秒。达到 `max_pages` 会提示，不会静默宣称已扫描全部主机。
