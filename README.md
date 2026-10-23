# autodl-renter

一个下载后就能用的 AutoDL 算力抢租小工具。**已有 Python 3.9+ 即可，不需要 pip、虚拟环境或安装命令。**

按地区、GPU / CPU 型号、数量和小时价格筛选库存，支持交互配置、持续等待、断点续跑，也可以直接交给 agent 操作。默认只预览，添加 `--execute` 才会实际租用。

这是个人维护的非官方工具，使用 AutoDL 普通算力市场的网页接口。

## 下载即用

在 GitHub 点击 **Code → Download ZIP**，解压后进入项目目录；也可以 clone：

```bash
git clone https://github.com/J-shang/autodl-renter.git
cd autodl-renter

# 先离线体验，不登录、不联网、不产生费用
python3 -m autodl_rent demo
```

以下命令都在项目根目录执行。Windows 可以将 `python3` 换成 `py -3` 或本机的 `python`。

```bash
python3 -m autodl_rent login          # 微信扫码登录，无需浏览器依赖
python3 -m autodl_rent init           # 按提示选地区、型号、数量、预算、镜像
python3 -m autodl_rent run            # 预览匹配结果
python3 -m autodl_rent run --execute  # 在配置的条件内实际抢租
```

扫码微信需已关联 AutoDL 账号。认证保存在本机 `.local/auth.json`，配置和状态也留在项目内，不往系统安装文件。用完可以删除源码目录；**已有任务请先处理完待核对请求，并保留所需的实例记录。删除目录或停止工具不会关闭已创建的实例。**

## 想先抢一个便宜的实例

登录后直接运行固定预设，无需手写配置：

```bash
python3 -m autodl_rent quick            # 西北B区，GPU / CPU 均可，严格低于 ¥1/时，只预览
python3 -m autodl_rent quick --execute  # 成功一个单资源单位实例就停止
```

任务保存在 `config.west-under1.local.json`。再次执行会继续同一任务，成功后不会重复租用。查看它的进度：

```bash
python3 -m autodl_rent status --config config.west-under1.local.json
```

## 直接交给 agent

把仓库目录交给支持运行命令的 agent，告诉它：

> 请阅读这个项目的 AGENTS.md，帮我找西北B区的 RTX 4090，单卡、一个实例、每小时不超过 3 元。先预览库存，尚未授权实际租用。

无需安装 Skill 或 MCP 服务。根目录的 [AGENTS.md](AGENTS.md) 会把 agent 引导到操作说明；CLI 提供非交互参数、JSON 结果、配置 Schema 和固定退出码。支持 Skill 的 agent 也可以直接读取随仓库提供的 [SKILL.md](autodl_rent/resources/autodl-renter/SKILL.md)。

```bash
python3 -m autodl_rent agent --json  # agent 发现命令和协议
python3 -m autodl_rent demo --json   # 离线验证结果解析
```

具体流程和可复制的 agent 提示词见 [Agent 使用指南](docs/agents.md)。

## 常用命令

| 命令 | 用途 |
| --- | --- |
| `login` | 零依赖扫码登录；`auth` 可隐藏输入网页登录 Token |
| `init` | 交互配置；`--template` 离线生成模板，也支持完整参数 |
| `catalog regions / gpus / images` | 查询实时地区、型号、基础镜像 |
| `validate` | 离线检查配置和任务状态；`--online` 加做只读联网检查 |
| `run` / `quick` | 默认预览；`--execute` 实际租用，`--once` 仅扫描一轮 |
| `status` / `resolve` | 查看进度，核对未知创建结果 |
| `doctor` | 本机诊断；`--online` 检查连接和登录 |
| `demo` | 离线体验 `any / all / total` 三种停止策略 |
| `agent` | 查看 agent 协议、配置 Schema、操作 Skill |

`python3 -m autodl_rent --help` 查看全部命令，子命令后加 `--help` 查看参数。`--config`、`--auth-file`、`--json` 和 `--non-interactive` 可以放在子命令前后。

## 文档与项目结构

- [使用与恢复](docs/usage.md)：登录、执行、状态和异常结果核对。
- [配置与停止策略](docs/configuration.md)：多目标、资源单位、价格上限、轮询。
- [Agent 使用指南](docs/agents.md)：命令协议、提示词和结果处理。
- [实现与接口依据](docs/implementation.md)：模块分工、计费边界和验证范围。
- [隐私与本地文件](docs/privacy.md)、[贡献指南](CONTRIBUTING.md)。

```text
autodl_rent/          Python 标准库实现；入口、引导、API、筛选、任务状态
  resources/          agent 操作 Skill 与配置 JSON Schema
tests/                不联网、不租用的离线测试
docs/                 用户、agent、配置、实现与隐私说明
.github/workflows/    Linux / macOS / Windows 自动测试
AGENTS.md             agent 进入仓库后的操作与开发说明
config.example.json   无账号信息的配置模板
.local/               本机认证、状态和日志（Git 忽略）
```

普通使用和测试均不需要安装依赖：

```bash
python3 -m unittest discover -s tests -v
```

价格上限按每小时原价核对，不是累计消费限额；工具不会自动关机。具体限制见 [价格与成功条件](docs/implementation.md#价格和成功的定义)。本项目采用 [MIT License](LICENSE)。
