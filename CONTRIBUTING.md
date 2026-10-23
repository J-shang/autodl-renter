# 贡献指南

本项目作为可随时下载、直接运行的小工具维护。Python 3.9+，普通使用与测试只需要标准库；不要为常规功能引入安装步骤或后台服务。

## 开发与验证

```bash
python3 -m unittest discover -s tests -v
python3 -S -m autodl_rent demo --json --quiet
python3 -m autodl_rent agent --json
python3 -m autodl_rent agent schema

git diff --check
```

`-S` 不加载 site-packages，用来确认普通入口不依赖第三方包。新增行为使用临时目录、`DemoAPI` 或 mock 验证，不调用真实创建接口。联网检查仅在明确需要时执行 `doctor --online` 或 `validate --online`。

GitHub Actions 在 Linux 的 Python 3.9 / 3.14，以及 macOS、Windows 的 Python 3.12 上运行离线测试与免依赖入口检查，不需要账号凭据。工作流使用 [GitHub 官方 Python CI 方式](https://docs.github.com/en/actions/tutorials/build-and-test-code/python)。

## 修改放在哪里

| 文件 | 职责 |
| --- | --- |
| `autodl_rent/cli.py` | 参数、命令分发、登录、退出码与命令结果 |
| `autodl_rent/onboarding.py` | 人类交互向导和等价的非交互配置生成 |
| `autodl_rent/output.py` | JSON 协议、计划/状态摘要、终端表格、agent 发现协议 |
| `autodl_rent/config.py` | 配置校验与任务合同指纹 |
| `autodl_rent/api.py` / `qr_auth.py` | 固定 HTTPS 地址的接口与原生扫码登录 |
| `autodl_rent/runner.py` | 库存筛选、轮询、报价检查、依次创建与确认 |
| `autodl_rent/state.py` | 文件锁、原子持久化与 pending 恢复 |
| `autodl_rent/paths.py` | 本机文件默认位置 |
| `autodl_rent/resources/` | 可导出的 agent Skill 与配置 JSON Schema |

`quick_rent.py` 保留旧的专用入口；新用户使用统一的 `quick` 子命令。旧入口的任务配置和状态仍兼容。

## 提交要求

保留租用行为的关键约束：提交前写入创建意图；未知结果不自动重试；同任务使用同一份状态；每次创建前重新核对报价和配额；默认预览。不要通过更换状态文件来修复未确认请求。

CLI 行为变更应同时更新帮助、文档、agent 发现协议和相关测试。`schema_version: 1` 允许新增字段；移除字段、改变语义或退出码时，需要明确版本迁移方案。

提交前检查 `.gitignore` 和暂存差异。不要提交真实 Token、实例资料、日志或排查快照。报告接口变化时给出脱敏后的结构、发生时间及可复现步骤。仓库采用 [MIT License](LICENSE)。
