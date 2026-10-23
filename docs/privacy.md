# 隐私与本地文件

[返回首页](../README.md) · [认证和使用](usage.md)

## 文件边界

源码、离线测试、文档和 `config.example.json` 可以作为仓库内容。实际认证、个人配置、任务记录和浏览器排查资料保存在本机，并由 `.gitignore` 排除。

| 内容 | 默认位置 | 可能包含的信息 |
| --- | --- | --- |
| 网页登录认证 | `.local/auth.json` | 可操作账号的网页登录 Token |
| 登录二维码、扫码挑战 | `.local/*login-qr.*`、`.local/qr-login.json` | 登录挑战标识和二维码链接 |
| 浏览器登录配置 | `.local/browser-profile/` | Cookie、Local Storage 和会话状态 |
| 实际任务配置 | `config.json`、`config.*.local.json` | 地区、预算、镜像和本机路径 |
| 任务状态、日志 | `.local/*.state.json`、`.local/*.log` | 实例 ID、主机 ID、创建时间和报价 |
| 接口和浏览器排查资料 | `.local/inspection/`、`.playwright-mcp/` | 接口快照、网页资源、控制台记录 |

任务状态用于防止重复创建。同一任务继续使用原配置和状态文件；不要为了清理文件而删除或改名这些记录。

## 认证与日志

Token 通过隐藏输入、`AUTODL_WEB_TOKEN` 或微信扫码提供。统一的 `login` / `auth` 命令先只读验证，再保存到本机认证文件；旧的 `autodl_rent.quick_rent` 入口临时扫码所得 Token 只在内存中使用。认证写入采用临时文件与原子替换，在 macOS/Linux 上文件权限为 `600`。程序新建的私有文件父目录使用 `700`；已有目录不会被写入函数随意更改权限。

正常使用不往系统安装文件，默认认证、二维码和任务状态留在项目内。`AUTODL_RENT_HOME` 或 `--auth-file` 可以改变认证位置，`--config` 和 `state_file` 可以改变任务位置；这些自定义路径也需妥善保管。删除源码目录不会关闭 AutoDL 实例，也不应当作处理未知创建结果的方法。

API 请求固定发往 AutoDL 的 HTTPS 地址，禁止自动跟随重定向。HTTP 错误不输出响应正文；业务错误先遮盖当前登录 Token，再截断消息，避免长 Token 的前缀进入日志。

日志和 JSON 结果仍可能包含实例 ID、主机 ID、本机路径及服务端业务信息。分享前应检查具体内容，不能把“已脱敏”理解为结果完全公开。`--json` 将 stdout 留给结果，进度写 stderr；agent 只需获取本地二维码图片路径，无需读取或显示认证 Token。

## Git 与分享

`.gitignore` 同时排除本地目录、个人配置、环境变量文件、认证文件、常见私钥、浏览器 HAR、运行日志及任务状态。使用自定义认证或状态路径时，也应放在 `.local/` 内。

忽略规则只影响未被 Git 跟踪的文件，不能清除已经提交的内容，也不能阻止 `git add -f`。查看当前可能进入 Git 的文件：

```bash
git status --short
git ls-files --cached --others --exclude-standard
```

发布前检查示例配置、文档、测试夹具和准备提交的差异，避免真实 Token、Cookie、账号资料、实例标识或个人绝对路径。测试中的 `fictional-*`、`test-*`、`demo-*` 值均为虚构数据。

常规分支推送只传输该分支提交引用的对象。工具保存的本地快照可能通过其他 Git 引用保留旧文本，不能仅凭“没有提交”判断整个 `.git/` 没有隐私。`git push --mirror` 会推送整个引用空间，可能包含这些快照，不应将它当作普通分支发布命令。

认证文件、忽略的资料和本地 Git 对象仍可能包含在整个目录的压缩包或备份中。需要分享项目时，优先使用审查后的分支内容；不要直接分享含 `.local/` 和 `.git/` 的完整目录。
