# 让 agent 直接操作

[返回首页](../README.md) · [配置说明](configuration.md) · [使用与恢复](usage.md)

只要 agent 能读取文件并运行 Python 命令，就可以使用本工具。下载仓库、进入根目录，让它阅读 [AGENTS.md](../AGENTS.md) 即可。正常使用不需要安装 Skill、SDK 或 MCP 服务。

## 给 agent 的提示词

仅查询：

> 阅读 AGENTS.md，帮我找西北B区或北京B区的 RTX 4090，一个单卡实例，每小时原价不超过 3 元，总小时价格上限 3 元。先预览，不要实际租用。如果需要登录，给我展示二维码，我来扫码。

明确授权租用：

> 按刚才核对的配置开始租用，最多等待 20 分钟，成功一个就停。不要增加地区、型号、数量或预算；已有任务继续使用原状态。

处理恢复：

> 读取 config.job.local.json 的任务状态，说明已经创建的实例和未确认请求。先核对 pending，不要删除记录或盲目重试。

授权租用的提示词会产生真实费用。二维码仅用于用户亲自登录，不代表授权租用。

## 发现接口

```bash
python3 -m autodl_rent agent --json
python3 -m autodl_rent agent schema
python3 -m autodl_rent agent skill
```

`agent` 返回可用命令、作用、认证方式和退出码；`agent schema` 返回配置 JSON Schema，供编辑器或 agent 生成配置。Python 的 `validate` 还会检查跨字段约束、任务指纹和状态记录。

`agent skill` 输出独立的 [操作 Skill](../autodl_rent/resources/autodl-renter/SKILL.md)。支持 Skill 的客户端可以读取或自行导入它；一次性使用直接读取文件即可。

## 不弹出输入提示的完整流程

以下价格与型号用于展示参数格式，实际值以用户约束和实时目录为准：

```bash
python3 -m autodl_rent doctor --json
python3 -m autodl_rent catalog regions --json
python3 -m autodl_rent catalog gpus --json
python3 -m autodl_rent catalog images --json

# IMAGE_UUID 和 CUDA 必须来自实际目录
python3 -m autodl_rent init --config config.job.local.json \
  --region '西北B区' --gpu 'RTX 4090' --quantity 1 --gpus-per-instance 1 \
  --max-price 3 --budget 3 --policy any \
  --image IMAGE_UUID --cuda 12.8 --wait-seconds 1200 --json

python3 -m autodl_rent validate --config config.job.local.json --json
python3 -m autodl_rent run --config config.job.local.json --json

# 已获得上述范围的付费租用授权后
python3 -m autodl_rent run --config config.job.local.json --execute --json
python3 -m autodl_rent status --config config.job.local.json --json
```

`--region`、`--gpu` 可以重复；每种 GPU 参数建立一个目标，共用命令中设置的配额和单价。不同目标需要不同单价或地区时，先 `init --template`，再编辑配置并 `validate`。`--policy total` 必须配 `--total N`。

`init --json` 不会进入交互向导；需要同时提供 `--region`、`--gpu`、`--max-price` 和 `--budget`，或使用 `--template`。`init` 不覆盖已有配置，也拒绝使用已有任务记录的状态路径。

## 登录与进程等待

优先让用户在自己的终端执行 `login`，或由 agent 运行：

```bash
python3 -m autodl_rent login --no-open --json
```

登录进程的 **stderr** 会立即输出：

```json
{"event":"login_qr","path":"/path/to/checkout/.local/login-qr.jpg"}
```

agent 展示该本地图片，让用户用已关联 AutoDL 账号的微信扫码。进程默认等待最多 300 秒，登录成功后将认证保存到本机，并向 stdout 输出最终 JSON。不要因单次工具等待结束就重新启动登录进程；保留进程会话并继续读取它的输出。租用进程也使用相同的会话管理方式。

无图片展示能力的 agent 可把图片路径告诉用户，用户在本机打开。Token 不应出现在提示词、命令参数或聊天中；已设置的 `AUTODL_WEB_TOKEN` 会优先于认证文件。

## JSON 协议

`--json` 隐含禁止终端输入提示。每次命令的 stdout 是**一个 JSON 对象**，进度日志写 stderr；`--quiet` 隐藏普通进度。`--help`、`--version` 是文本出口。全局选项可以放在子命令前后。

```json
{
  "schema_version": 1,
  "version": "0.2.0",
  "command": "status",
  "ok": true,
  "exit_code": 0,
  "data": {
    "policy": "any",
    "done": false,
    "count": 0,
    "hourly_total": "0",
    "targets": [],
    "successes": [],
    "pending": null
  },
  "error": null
}
```

不同命令的 `data` 有各自字段，调用方应允许新增字段。金额摘要使用十进制字符串，避免将二进制浮点数当作精确价格。`run` 预览包含 `offers`、`matches` 和 `offers_truncated`，最多返回前 100 个候选；人类表格显示前 20 个。`matches` 是实际扫描到的匹配数，达到页数限制或截止时间时不代表完整市场库存。

| 退出码 | 含义 | agent 如何处理 |
| --- | --- | --- |
| `0` | 预览结束、目标已完成或命令成功 | 看 `data.mode`、`data.done`，不要只凭 0 判断已租到 |
| `2` | 参数、配置、登录或确定的接口错误 | 读取 `error`；`doctor` 的诊断失败看 `data.checks` |
| `3` | 等待结束或单轮未完成目标 | 读取已完成数量，在原任务范围内决定是否继续 |
| `4` | 创建结果待核对 | 停止创建，走恢复流程，禁止直接重试 |
| `130` | 用户中断或输入结束 | 查看状态后再恢复 |

`status` 成功读取 pending 时仍返回 `0`；这只表示读取成功。离线 `doctor` 对未登录、尚未配置等首次使用状态返回 `0` 和 `ready: false`；无效配置或联网检查失败返回 `2`。

遇到未知创建结果，`run` 会在错误结果的 `data.pending` 保留恢复信息。详见 [任务恢复](usage.md#查看进度和恢复)。不要假设 `--checked-console` 是普通确认按钮：它声明控制台已经实际核对。
