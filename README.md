# autodl-renter

AutoDL 算力抢租工具。

用 Python 按地区、GPU / CPU 型号、数量和价格筛选 AutoDL 普通算力市场，轮询库存并依次创建按量计费实例。支持“任意一个成功”“所有目标达标”“累计数量达标”三种停止策略。

Python **3.9+**。核心功能、Token 登录、离线演示和测试只使用标准库。默认预览，只有 `--execute` 才会提交真实租用。

## 快速开始

在仓库根目录执行：

```bash
# 离线演示，不连接 AutoDL
python3 -m autodl_rent demo --policy any

# 在本机隐藏输入网页登录 Token，并只读验证
python3 -m autodl_rent auth

# 交互选择地区、型号、数量、价格、停止策略和基础镜像
python3 -m autodl_rent init

# 预览一轮匹配结果
python3 -m autodl_rent run

# 按配置开始实际抢租
python3 -m autodl_rent run --execute
```

认证保存在被 Git 忽略的 `.local/auth.json`。不要将 Token 写入源码或示例配置。登录步骤、可选浏览器助手和任务恢复见 [使用说明](docs/usage.md)。

## 西北B区低于 1 元的专用入口

```bash
# 默认只预览；添加 --execute 才会实际租用
python3 -m autodl_rent.quick_rent
```

此入口通过 Python HTTPS 请求匹配西北B区、GPU / CPU 均可、每小时严格低于 1 元的单资源单位实例，成功一个即停止；同一任务复用原有记录。具体条件和等待时长见 [快速抢租说明](docs/usage.md#纯程序快速抢租西北b区低于1元成功一个就停)。

## 文档

- [使用与任务恢复](docs/usage.md)：认证、运行、进度、异常结果核对。
- [配置与停止策略](docs/configuration.md)：字段、数量语义、筛选和轮询。
- [价格与接口依据](docs/implementation.md)：成功条件、计费边界、验证范围和接口来源。
- [隐私与本地文件](docs/privacy.md)：认证、日志、忽略规则和分享范围。

## 项目结构

```text
autodl_rent/               Python 包与命令行入口
tests/                     离线测试
docs/                      使用、配置、接口和隐私说明
config.example.json        无账号信息的配置模板
requirements-login.txt     可选浏览器登录助手依赖
.local/                    本机认证、任务记录和排查资料（Git 忽略）
```

运行测试：

```bash
python3 -m unittest discover -s tests -v
```

停止脚本只会停止继续租用，已创建的实例仍由 AutoDL 管理。未知创建结果会保留待核对记录；恢复前请按 [任务恢复流程](docs/usage.md#查看进度和恢复)核对。
