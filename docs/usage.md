# 使用与任务恢复

[返回首页](../README.md) · [配置说明](configuration.md) · [隐私与本地文件](privacy.md)

## 开始使用

以下命令均在仓库根目录执行：

```bash
# 可选：先运行离线演示，无网络请求，不产生费用
python3 -m autodl_rent demo --policy any
python3 -m autodl_rent demo --policy all

# 1. 在本机隐藏输入网页登录 Token，并只读验证
python3 -m autodl_rent auth

# 2. 按提示选择地区、型号、数量、价格、停止策略和基础镜像
python3 -m autodl_rent init

# 3. 查看一轮匹配结果，不创建实例
python3 -m autodl_rent run

# 4. 使用刚才的配置开始实际抢租
python3 -m autodl_rent run --execute
```

`auth` 使用**主账号网页登录 Token**。在已登录的 [AutoDL 市场](https://www.autodl.com/market/list) 打开浏览器开发者工具的 Console，执行 `copy(localStorage.getItem('token'))`，再粘贴到本机脚本的隐藏输入中。也可以通过本机环境变量 `AUTODL_WEB_TOKEN` 提供。不要把 Token 填入配置或发送到聊天中。这里不是控制台中的开发者 API Token。

认证默认保存在项目 `.local/auth.json`，macOS/Linux 上文件权限为 `600`；`.local/` 已加入 Git 忽略。环境变量优先于认证文件。使用其他认证文件时，将全局参数放在子命令前，例如 `python3 -m autodl_rent --auth-file /path/auth.json run`。

### 可选：弹出浏览器手动登录

如果不想复制 Token，可以安装可选登录助手：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-login.txt
python3 -m playwright install chromium
python3 -m autodl_rent login
```

在弹出的独立浏览器中手动完成登录和验证码。程序随后验证并保存 Token；不自动处理验证码。已有 Chrome 也可以使用 `login --browser chrome`。登录配置目录在 `.local/browser-profile/`，包含登录状态。安装方法依据 [Playwright Python 文档](https://playwright.dev/python/docs/library)。

## 纯程序快速抢租：西北B区、低于1元、成功一个就停

```bash
python3 -m autodl_rent.quick_rent --execute
```

这个专用入口直接通过 Python HTTPS 请求登录、查询、报价和创建，不使用 computer use，也不读取 Chrome 私有数据。若没有本机 Token，会生成 `.local/autodl-rent-login-qr.jpg`，用微信扫描这张 AutoDL 原生登录二维码即可；Token 仅保留在进程内。账号必须已经关联该微信。

条件固定为西北B区、GPU 或 CPU 均可、一个单资源单位实例、小时原价严格低于 1 元，成功一个即停止。用 `0.999` 元上限排除恰好 1 元的资源。采用基础 Miniconda 镜像，不扩容付费数据盘。登录后最多等待 1800 秒，可用 `--wait-seconds` 调整；省略 `--execute` 只预览。程序保存任务配置与状态，同一任务重复运行不会重复租用。

CPU 型号支持 `CPU` 和 `CPU-close-HT`。CPU 的 `gpus_per_instance: 1` 表示一个平台资源单位，实际核心数与内存由主机分配；CPU 不要求显卡或 CUDA 驱动元数据。设置了正的 `min_gpu_memory_gb` 时不会匹配 CPU。

## 查看进度和恢复

```bash
python3 -m autodl_rent status
python3 -m autodl_rent run --execute --once
```

`status` 无需联网，会显示各目标已成功的数量、实例 ID 和待核对请求。`--once` 只扫描一轮，该轮仍可能为不同目标创建多个实例。

程序先把创建意图落盘，再提交创建。相同状态文件使用系统文件锁，防止同时启动两个进程。请求超时、HTTP 500、未知创建响应或确认超时都保留 `pending` 并暂停，避免重试造成重复租用。

若已经取得实例 ID，重新执行 `run --execute` 会先只读确认此实例；也可以单独运行：

```bash
python3 -m autodl_rent resolve
```

若网络中断导致没有拿到实例 ID，先在控制台按 `grab-<attempt_id>` 备注、时间、主机和型号核对。确认已经创建时，用对应实例 ID 记入进度：

```bash
python3 -m autodl_rent resolve --instance <对应实例ID>
```

确认没有创建时，再清除待核对记录：

```bash
python3 -m autodl_rent resolve --not-created --checked-console
```

不要通过删除状态文件来恢复未确认请求。同一任务继续使用同一 `state_file`；新的独立任务使用新状态路径。不要跨账号复用任务状态，也不要为同一任务并行使用不同状态文件，否则文件锁无法互相协调。修改目标、策略、镜像或预算会触发配置指纹校验，避免忘记既有实例后重复租用；轮询参数和单次等待时长可以调整。

退出码：`0` 完成目标或结束预览，`2` 配置/登录/确定的接口错误，`3` 等待超时或单轮未达标，`4` 有待核对创建结果，`130` 手动中断。
