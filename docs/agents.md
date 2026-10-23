# Agent 操作指南

[返回首页](../README.md) · [配置说明](configuration.md) · [使用与恢复](usage.md)

支持文件读取与 Python 命令执行的 Agent 可依据 [AGENTS.md](../AGENTS.md) 操作本项目。调用时应以仓库根目录为工作目录。常规使用无需安装 Skill、SDK 或 MCP 服务。

## 指令示例

仅查询：

> 请阅读 AGENTS.md，检索西北B区或北京B区的 RTX 4090。要求为一个单卡实例，单卡小时原价与总小时价格上限均为 3 元。仅预览库存，不创建实例。若需要登录，请展示二维码，由用户完成扫码。

明确授权租用：

> 请按已核对的配置执行租用任务，最长等待 20 分钟，创建并确认一个实例后停止。保持既定地区、型号、数量与价格上限；恢复已有任务时继续使用原状态文件。

处理恢复：

> 请读取 config.job.local.json 的任务状态，列出已创建实例与未确认请求。存在 pending 时应先进行核对，不得删除记录或在结果未知时重新提交创建请求。

执行已获授权的租用任务可能产生实际费用。扫码登录仅用于身份认证，不构成付费租用授权。

## 命令发现

```bash
python3 -m autodl_rent agent --json
python3 -m autodl_rent agent schema
python3 -m autodl_rent agent skill
```

`agent` 返回可用命令、作用、认证方式和退出码；`agent schema` 返回配置 JSON Schema，供编辑器或 Agent 生成配置。`validate` 命令还会校验跨字段约束、任务指纹和状态记录。

`agent skill` 输出独立的 [操作 Skill](../autodl_rent/resources/autodl-renter/SKILL.md)。支持 Skill 的客户端可直接读取或导入该文件，临时调用无需安装。

## 非交互调用

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

用户在本机交互终端执行 `python3 -m autodl_rent login`，即可选择微信扫码或输入网页登录 Token。Agent 和脚本应显式指定 `--method`；`--json`、`--non-interactive` 或非终端输入时不显示菜单，也不会自动选择登录方式。

已有调用 `login --json` 应改为 `login --method qr --json`。命令发现协议中，`login` 现描述方式选择，`requires_wechat_binding` 位于具体的 `login --method qr` 和 `login --method token` 条目；调用方应按所选方式读取。JSON 结果封装与退出码保持协议版本 `1`。

未关联微信的账号使用网页登录 Token 认证：由用户在浏览器登录 AutoDL，并在其本机终端执行 `python3 -m autodl_rent login --method token`，通过隐藏输入提供 Token。具体步骤见 [网页登录 Token 认证](usage.md#网页登录-token-认证)。Agent 不应要求用户通过聊天提供 Token；认证完成后，CLI 会自动读取本机认证文件，Agent 可继续调用任务命令。若本机已设置 `AUTODL_WEB_TOKEN`，可调用 `login --method token --json` 验证并保存；未设置时返回错误，不等待输入。`auth` 作为兼容入口保留。

已关联微信的账号可由 Agent 发起扫码登录进程：

```bash
python3 -m autodl_rent login --method qr --no-open --json
```

登录进程的 **stderr** 会立即输出：

```json
{"event":"login_qr","path":"/path/to/checkout/.local/login-qr.jpg"}
```

Agent 应展示该本地图片，由用户通过已关联 AutoDL 账号的微信扫码。进程默认最多等待 300 秒；登录成功后将认证保存至本机，并向 stdout 输出最终 JSON。单次工具调用等待结束不表示登录进程已终止，应保留进程会话并继续读取输出。租用进程采用相同的会话管理方式。

不具备图片展示能力的 Agent 可提供本地图片路径，由用户自行打开。Token 不应出现在提示词、命令参数或聊天中；已设置的 `AUTODL_WEB_TOKEN` 优先于认证文件。

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

不同命令的 `data` 包含不同字段，调用方应兼容新增字段。金额摘要使用十进制字符串，避免二进制浮点数造成精度误差。`run` 预览包含 `offers`、`matches` 和 `offers_truncated`，最多返回前 100 个候选；终端表格显示前 20 个。`matches` 表示实际扫描到的匹配数量，达到页数限制或截止时间时不代表完整市场库存。

| 退出码 | 含义 | 处理方式 |
| --- | --- | --- |
| `0` | 预览结束、目标已完成或命令成功 | 读取 `data.mode`、`data.done`，不能仅依据退出码判断实例已创建 |
| `2` | 参数、配置、登录或确定的接口错误 | 读取 `error`；`doctor` 的诊断失败信息位于 `data.checks` |
| `3` | 等待结束或单轮未完成目标 | 读取已完成数量，在原任务范围内决定是否继续 |
| `4` | 创建结果待核对 | 停止创建，执行恢复流程，不得直接重试 |
| `130` | 用户中断或输入结束 | 查看状态后再恢复 |

`status` 成功读取 pending 时仍返回 `0`；这只表示读取成功。离线 `doctor` 对未登录、尚未配置等首次使用状态返回 `0` 和 `ready: false`；无效配置或联网检查失败返回 `2`。

创建结果未知时，`run` 会在错误结果的 `data.pending` 中保留恢复信息，详见 [任务恢复](usage.md#查看进度和恢复)。`--checked-console` 表示已在控制台完成实际核对，仅在取得相应依据后方可使用。
