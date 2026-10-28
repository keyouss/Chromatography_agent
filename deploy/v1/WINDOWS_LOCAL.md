# Windows 本机验收运行

本机优先验收只使用仓库外的受保护配置：

`C:\Users\13459\AppData\Local\ChromatographyPlatform\priority-local\runtime.json`

`run-local.ps1` 会拒绝仓库 `.runtime/dev.env`、仓库根目录 `.env`、`deploy/v1/.env` 及其他配置路径；脚本不读取、合并或 dot-source 这些旧配置，也不修改全局环境变量。JSON 只在当前 PowerShell 进程中注入给所启动的子进程。

provider 密钥迁移由 `provision-priority-secret.ps1` 一次性执行。它只读取项目根 `.env` 中精确匹配且各出现一次的 `BASE_URL`、`MODEL_ID`、`API_KEY`，并要求 URL/model 与受保护 runtime 中的 `LLM_PROVIDER_URL`、`LLM_MODEL_ID`、`LLM_KEY_REFERENCE=API_KEY` 一致；`.env` 不会被修改。`API_KEY` 使用当前 Windows 用户的原生 DPAPI `CurrentUser` 范围加密到 runtime 目录下的 `secret-source.dpapi`，runtime 只新增 `LLM_SECRET_SOURCE_PATH`，同时创建不含密钥值的时间戳备份。secret 文件、runtime 文件和 runtime 目录均只允许当前用户与 `SYSTEM` 的显式 Allow ACL，拒绝重解析点、越界路径、继承规则和其他身份。换 Windows 用户后 DPAPI 无法解密，必须在新用户下重新迁移。

本实现支持的身份边界只有执行迁移和解密的当前 Windows 用户。`-NoProfile` 只表示不加载 PowerShell profile 脚本，不会卸载 Windows 用户 profile；真实当前用户运行带 `-NoProfile` 的命令仍应成功。本机不创建测试用户、不修改系统 profile。

```powershell
$script = "D:\Jupyter\Chromatography_agent\deploy\v1\test_dpapi_secret_source.ps1"
pwsh.exe -NoProfile -ExecutionPolicy Bypass -File $script
$LASTEXITCODE  # 真实当前用户上下文应为 0
```

`DPAPI_TEST_BLOCKED ... category=current-user-profile-or-identity` 和退出码 `1` 只在已知的模拟身份/Windows 用户 profile 未加载的隔离执行器中观察到；普通终端无法仅靠 `-NoProfile` 强制复现该阻断。本次 Codex 沙箱属于该隔离执行器，真实当前用户命令输出 `DPAPI_TEST_OK` 且退出码为 `0`。错误用户或无 profile 的跨身份解密没有独立实测，结论保持 `NOT_VERIFIED/环境限制`；受控损坏密文、ACL 不安全和 runtime root 不匹配会在联网前 fail-closed，但不等于跨身份验证。需要换用户时重新执行 provision。

启动前会逐项 `Test-Path` 并校验完整 SHA-256：TLC registry 必须是目录，TLC 的 `predict.py`、四个模型文件和 `MODEL_SHA256.txt` 必须匹配审计绑定；SFC registry 必须位于 model root 内，选定模型、候选 SQLite 和 golden fixture 必须匹配绑定；provider URL、model、`LLM_KEY_REFERENCE=API_KEY` 和 DPAPI secret 引用必须来自受保护 JSON，URL 不得包含凭据或秘密查询参数。校验失败时不会启动任何服务。只有 API 和 Worker 的 `Start-One` 分支在本地启动子进程前解密 secret，并通过当前 PowerShell 进程临时注入 `API_KEY` 供子进程继承，`Start-Process` 返回后立即恢复或删除父进程的原值；frontend 分支不解密，并在启动子进程期间清除任何调用方已有的 `API_KEY`。

SFC 候选 scope 的持久化文件由 `CHROM_SFC_SCOPE_STORAGE_PATH` 显式指定。该路径必须是受保护运行目录内的绝对路径，与 API 和 Worker 共用同一 JSON 值，并且不能位于 `Services`、模型源 SQLite 旁、`deliverables` 或源码目录。PID、启动创建时间、日志和 Worker ready marker 也只放在该受保护运行目录下。

脚本默认使用 `D:\Anaconda\envs\SFC\python.exe`。前端只绑定 `127.0.0.1`；API 只绑定 `127.0.0.1`；前端使用 JSON 中的 `FRONTEND_PORT`，若该端口已有未由脚本记录且 PID/创建时间匹配的监听进程，脚本会拒绝争用。脚本不使用 Compose、不安装依赖、不连接旧 8010 服务。

## 受控命令

```powershell
$script = "D:\Jupyter\Chromatography_agent\deploy\v1\run-local.ps1"
$config = "C:\Users\13459\AppData\Local\ChromatographyPlatform\priority-local\runtime.json"

# 仅在当前 Windows 用户下执行一次 provider secret 迁移；不会启动服务
pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "D:\Jupyter\Chromatography_agent\deploy\v1\provision-priority-secret.ps1"

# 查看 API、Worker、前端状态，不启动服务
powershell.exe -NoProfile -File $script -Action status -Service all -ConfigFile $config

# 后台启动一个服务或全部服务；会创建受保护 PID/log 状态
powershell.exe -NoProfile -File $script -Action start -Service api -ConfigFile $config
powershell.exe -NoProfile -File $script -Action start -Service worker -ConfigFile $config
powershell.exe -NoProfile -File $script -Action start -Service frontend -ConfigFile $config

# 只停止本脚本记录且 PID 与创建时间同时匹配的进程
powershell.exe -NoProfile -File $script -Action stop -Service worker -ConfigFile $config

# 按同一配置停止并重新启动指定服务
powershell.exe -NoProfile -File $script -Action restart -Service api -ConfigFile $config
```

`start` 使用隐藏窗口和独立 stdout/stderr 日志，实际 PID 与创建时间写入受保护 state 目录。`stop` 不按名称批量终止 Python、Node 或 Docker；Windows 探活只读取原生进程 PID 和创建时间，防止 PID 复用误操作。Worker marker 只有在 marker 的 PID 与已核实的本次 Worker PID 相同时才会清理。ready 文件存在本身不能替代 Worker 健康检查或真实任务验收。

`created_at` 现在按带显式 UTC 偏移的 ISO-8601 写入；`status` 也兼容历史的“无偏移但实际为 UTC”状态值，并统一以 UTC 比较。修改 Worker 或其依赖源码后，已有 Worker 不会热加载，必须先按 PID/创建时间身份门禁停止并重新启动，再进行依赖 Worker 代码的浏览器或真实任务验收。

MySQL/Redis 只复用现有本机 Docker Desktop 容器和卷；本脚本不会重建数据库、清理卷、拉取镜像、重复迁移或重复初始化 `priority-local` 项目。当前启动门禁由总控维护：等待 API/Worker 安全 SHA、QA 和安全代码冻结完成后再执行 `start`；本轮配置与脚本核对阶段不启动 API/Worker。

配置键名由受保护 JSON 管理，至少包括：

`API_PORT`、`FRONTEND_PORT`、`CHROM_BUILD_MANIFEST`、`DATABASE_URL`、`SYNC_DATABASE_URL`、`REDIS_URL`、`ARTIFACT_ROOT`、`CHROM_ACTOR_ID`、`CHROM_DEFAULT_PROJECT_ID`、`CHROM_PROJECT_IDS`、`WORKER_READY_FILE`、TLC/SFC registry、model、artifact、scope、fixture 绑定、`CHROM_SFC_SCOPE_STORAGE_PATH`、`LLM_PROVIDER_URL`、`LLM_MODEL_ID`、`LLM_KEY_REFERENCE` 和 `LLM_SECRET_SOURCE_PATH`。runtime、secret 文件、日志、state、报告和文档均不包含 API key 明文、片段或哈希。
