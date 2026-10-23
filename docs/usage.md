# 使用与任务恢复

[返回首页](../README.md) · [配置说明](configuration.md) · [Agent 使用](agents.md) · [隐私与本地文件](privacy.md)

## 开始使用

运行环境为 Python 3.9 或更高版本。核心功能仅依赖 Python 标准库，可在下载或克隆的源码目录直接运行。以下命令均在仓库根目录执行；Windows 环境可将 `python3` 替换为 `py -3` 或本机的 `python`。

```bash
# 可选：运行离线演示，不发送网络请求，不产生费用
python3 -m autodl_rent demo --policy any
python3 -m autodl_rent demo --policy all

# 1. 按提示选择微信扫码或输入网页登录 Token
python3 -m autodl_rent login

# 2. 按提示选择地区、型号、数量、价格、停止策略和基础镜像
python3 -m autodl_rent init

# 3. 查看一轮匹配结果，不创建实例
python3 -m autodl_rent run

# 4. 按生成的配置执行租用任务
python3 -m autodl_rent run --execute
```

### 登录方式

在本机交互终端运行 `python3 -m autodl_rent login`，会显示以下菜单：

```text
请选择登录方式
   1. 微信扫码（需已关联 AutoDL 账号）
   2. 输入网页登录 Token（无需微信绑定）
输入编号:
```

输入 `1` 或 `2` 后继续相应流程。也可跳过菜单，直接指定方式：

```bash
python3 -m autodl_rent login --method qr
python3 -m autodl_rent login --method token
```

`--json`、`--non-interactive` 或非终端输入会禁止菜单，必须显式指定 `--method`；Token 方式还需要本机已设置 `AUTODL_WEB_TOKEN`，不会等待输入。原有 `auth` 命令继续兼容，等同于 `login --method token`。

### 微信扫码登录

在菜单选择 `1`，或运行 `login --method qr`。程序通过 Python HTTPS 请求获取 AutoDL 原生登录二维码，自动在本机打开图片；需要手动打开时加 `--no-open`。账号必须已关联该微信。默认最长等待 300 秒，可用 `--timeout` 调整。扫码成功后只读验证登录并保存认证，无需 Playwright。未关联微信的账号可选择下述 Token 方式。

### 网页登录 Token 认证

该方式使用已有网页登录会话，不要求账号关联微信：

1. 在本机浏览器登录 [AutoDL](https://www.autodl.com/login)，进入 [算力市场](https://www.autodl.com/market/list)。
2. 打开浏览器开发者工具的 Console，执行以下命令，将网页登录 Token 复制到剪贴板：

   ```javascript
   copy(localStorage.getItem('token'))
   ```

3. 在本机终端执行认证命令：

   ```bash
   python3 -m autodl_rent login --method token
   ```

   也可运行 `python3 -m autodl_rent login`，在菜单选择 `2`。

4. 将剪贴板内容粘贴至隐藏输入并回车。程序会只读验证 Token，验证通过后保存至本机认证文件。

认证使用**主账号网页登录 Token**，与控制台中的开发者 API Token 不同。也可通过本机环境变量 `AUTODL_WEB_TOKEN` 提供，Token 登录会优先读取该变量。Token 不应写入任务配置、命令参数或通过聊天发送。如果浏览器返回空值或 `null`，应先确认该页面已登录 AutoDL，再重新获取。

认证默认保存在项目 `.local/auth.json`，macOS/Linux 上文件权限为 `600`；`.local/` 已加入 Git 忽略。环境变量优先于认证文件。使用其他认证文件时可加 `--auth-file /path/auth.json`；也可用 `AUTODL_RENT_HOME` 改变认证、二维码和可选浏览器配置的默认目录。`--auth-file`、`--config`、`--json` 和 `--non-interactive` 均可放在子命令前后。

`python3 -m autodl_rent doctor` 离线检查本机认证和配置；加 `--online` 只读核对连接与登录。`validate` 检查当前任务配置和状态；`validate --online` 进一步核对地区、型号和镜像，不创建实例。

### 可选浏览器登录

菜单中的扫码和 Token 方式均无需浏览器依赖。如需在独立浏览器中登录，可安装可选助手：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-login.txt
python3 -m playwright install chromium
python3 -m autodl_rent login --method browser
```

在弹出的独立浏览器中手动完成登录和验证码。程序随后验证并保存 Token；不自动处理验证码。已有 Chrome 也可以使用 `login --browser chrome`。登录配置目录在 `.local/browser-profile/`，包含登录状态。安装方法依据 [Playwright Python 文档](https://playwright.dev/python/docs/library)。

## 快速租用预设

```bash
python3 -m autodl_rent quick            # 预览
python3 -m autodl_rent quick --execute  # 执行租用任务
```

该预设通过 Python HTTPS 请求完成库存查询、报价与实例创建，无需界面自动化，也不读取浏览器私有数据。运行前需使用 `login` 完成认证。配置保存为 `config.west-under1.local.json`，状态保存于 `.local/west-under1.state.json`。

固定条件为西北B区、GPU 或 CPU、一个单资源单位实例、小时原价严格低于 1 元。创建并确认一个实例后，任务停止。价格上限设为 `0.999` 元，以排除恰好 1 元的资源。预设采用基础 Miniconda 镜像，不扩容付费数据盘。默认每次最多等待 1800 秒，可通过 `--wait-seconds` 调整；未指定 `--execute` 时仅预览库存。任务配置与状态会持久化，重复运行同一任务不会重复创建已完成的实例。查看或恢复该任务时需指定其配置：

```bash
python3 -m autodl_rent status --config config.west-under1.local.json
python3 -m autodl_rent resolve --config config.west-under1.local.json
```

旧入口 `python3 -m autodl_rent.quick_rent` 仍保留，使用已有配置和状态格式。它缺少认证时会临时扫码，Token 仅保留在进程内。新任务优先使用统一的 `quick` 命令。

CPU 型号支持 `CPU` 和 `CPU-close-HT`。CPU 的 `gpus_per_instance: 1` 表示一个平台资源单位，实际核心数与内存由主机分配；CPU 不要求显卡或 CUDA 驱动元数据。设置了正的 `min_gpu_memory_gb` 时不会匹配 CPU。

## 查看进度和恢复

```bash
python3 -m autodl_rent status
python3 -m autodl_rent run --execute --once
```

`status` 无需联网，会显示各目标已成功的数量、实例 ID 和待核对请求。`--once` 只扫描一轮，该轮仍可能为不同目标创建多个实例。

程序先持久化创建意图，再提交创建请求。访问同一状态文件的进程通过系统文件锁互斥。请求超时、HTTP 500、未识别的创建响应或确认超时均会保留 `pending` 并暂停后续创建，避免重试导致重复租用。

若已经取得实例 ID，重新执行 `run --execute` 会先只读确认此实例；也可以单独运行：

```bash
python3 -m autodl_rent resolve
```

若网络中断导致未取得实例 ID，应先在控制台依据 `grab-<attempt_id>` 备注、提交时间、主机和型号进行核对。确认实例已创建后，使用对应实例 ID 更新任务进度：

```bash
python3 -m autodl_rent resolve --instance <对应实例ID>
```

确认没有创建时，再清除待核对记录：

```bash
python3 -m autodl_rent resolve --not-created --checked-console
```

未确认请求应通过核对流程处理，不应删除状态文件。同一任务必须继续使用同一 `state_file`；独立的新任务应使用新的状态路径。任务状态不应跨账号复用，同一任务也不应并行使用不同状态文件，否则无法通过文件锁实现互斥。修改目标、策略、镜像或预算会触发配置指纹校验，防止已有记录被忽略后重复创建实例；轮询参数和单次等待时长可以调整。

退出码：`0` 完成目标或结束预览，`2` 配置/登录/确定的接口错误，`3` 等待超时或单轮未达标，`4` 有待核对创建结果，`130` 手动中断。
