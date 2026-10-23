# autodl-renter

AutoDL 普通算力市场的命令行租用工具。支持按地区、资源型号、实例数量和小时原价上限筛选库存，并提供交互配置、任务恢复与 Agent 调用接口。

运行环境为 Python 3.9 或更高版本。核心功能仅依赖 Python 标准库，可在源码目录直接运行，无需安装项目或第三方依赖。

## 快速开始

[下载源码](https://github.com/J-shang/autodl-renter/archive/refs/heads/main.zip)并解压，或使用 Git 获取仓库：

```bash
git clone https://github.com/J-shang/autodl-renter.git
cd autodl-renter
```

在项目根目录执行以下命令：

```bash
python3 -m autodl_rent demo           # 离线演示
python3 -m autodl_rent login          # 微信扫码；未绑定微信时改用 auth
python3 -m autodl_rent init           # 交互生成任务配置
python3 -m autodl_rent run            # 预览匹配结果
python3 -m autodl_rent run --execute  # 按配置执行租用任务
```

Windows 环境可将 `python3` 替换为 `py -3` 或本机的 `python`。

身份认证支持两种方式：已关联微信的账号使用 `login` 扫码；未关联微信的账号可在浏览器登录 AutoDL 后，运行 `python3 -m autodl_rent auth`，在本机终端的隐藏输入中提供网页登录 Token。Token 获取步骤见 [网页登录 Token 认证](docs/usage.md#网页登录-token-认证)。两种方式均无需安装额外依赖。

默认运行模式为预览，仅添加 `--execute` 时才允许创建按量计费实例。停止程序不会关闭已创建的实例；小时价格上限不等同于累计支出限额。

## Agent 接口

支持文件读取与命令执行的 Agent 可依据 [AGENTS.md](AGENTS.md) 操作本项目，无需额外安装 Skill 或 MCP 服务。调用示例：

> 请阅读 AGENTS.md，检索西北B区的 RTX 4090，要求为一个单卡实例，小时原价不超过 3 元。仅预览库存，不创建实例。

CLI 提供非交互参数、JSON 输出、配置 Schema 和固定退出码：

```bash
python3 -m autodl_rent agent --json
python3 -m autodl_rent demo --json
```

调用流程与结果处理见 [Agent 操作指南](docs/agents.md)。支持 Skill 的客户端可直接读取仓库内的 [操作 Skill](autodl_rent/resources/autodl-renter/SKILL.md)。

## 预设配置

`quick` 命令使用固定条件：西北B区，GPU 或 CPU，一个单资源单位实例，小时原价严格低于 1 元。创建并确认一个实例后，任务即停止。

```bash
python3 -m autodl_rent quick            # 预览
python3 -m autodl_rent quick --execute  # 执行租用任务
```

任务配置保存在 `config.west-under1.local.json`，状态记录用于恢复进度并防止重复创建。可通过以下命令查询：

```bash
python3 -m autodl_rent status --config config.west-under1.local.json
```

## 文档

- [使用与恢复](docs/usage.md)：身份认证、任务执行与异常结果核对。
- [配置说明](docs/configuration.md)：筛选条件、停止策略、价格上限与轮询参数。
- [Agent 操作指南](docs/agents.md)：非交互调用、输出协议与进程管理。
- [实现说明](docs/implementation.md)：成功条件、计费边界与接口来源。
- [隐私说明](docs/privacy.md)：本地认证、任务记录与数据共享范围。

完整命令列表见 `python3 -m autodl_rent --help`。各子命令均支持 `--help`。

## 开发

```bash
python3 -m unittest discover -s tests -v
```

项目结构、验证方式与提交规范见 [贡献指南](CONTRIBUTING.md)。

## 许可

本项目采用 [MIT 许可](LICENSE)，为个人维护的非官方工具，使用 AutoDL 普通算力市场网页接口。
