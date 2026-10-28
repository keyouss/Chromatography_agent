# 通用色谱智能平台 V1（开发中）

基于 Vue 3、FastAPI、PydanticAI、MySQL 和 Redis + Worker 的本地色谱平台。当前优先建设五类色谱预测和色谱智能助手，浏览器入口为 **http://127.0.0.1:5174/**。

需求以 [产品 PRD](docs/PROJECT_PRD.md) 为准，接口以 [前后端契约](docs/PROJECT_FRONTEND_BACKEND_API_SPEC.md) 为准。后续开发从 [计划总入口](docs/plan.md) 开始，当前验收证据见 [测试状态](testing/STATUS.md)。

## 主要功能

- **色谱预测**：TLC、CC、HPLC、GC、SFC 专用表单、结构校验、Ketcher 绘图、预测结果与历史记录；包含 TLC 条件比较和溶剂反推、SFC 单柱与全柱候选搜索。
- **批量预测**：五类独立模板，制品校验、逐行预览、确认提交、任务进度及结果导出。五类集成契约已冻结，真实批量验收仍在进行。
- **色谱智能助手**：聊天、真实预测工具、已有数据摘要、模型登记草稿创建与查询，以及会话检索、归档和删除。Markdown 渲染、流式输出及刷新恢复沿用统一业务接口。
- **系统设置**：Provider 配置创建、连接测试和激活；密钥使用当前 Windows 用户的 DPAPI 保护，页面不回显旧密钥。

页面能力以当前构建、模型及运行状态为准。具体通过范围和限制以测试报告为准，契约冻结或构建通过不代表全部业务已完成验收。

## 启动方式

### 一键启动（本机推荐）

先打开 Docker Desktop，确保现有 MySQL、Redis 容器可用，再双击仓库根目录的 **`启动色谱平台.cmd`**，或在 PowerShell 中执行：

```powershell
& 'D:\Jupyter\Chromatography_agent\启动色谱平台.cmd'
```

入口会使用 **PowerShell 7**，依次检查并启动前端、API 和 Worker，复用已受管的运行进程。全部就绪后打开浏览器。首次初始化可能需要几分钟，请等待启动窗口输出结果。

| 服务 | 当前本机入口 / 环境 |
| --- | --- |
| 前端 | http://127.0.0.1:5174/ |
| API | http://127.0.0.1:9012/api/v1 |
| API 文档 | http://127.0.0.1:9012/docs |
| API 就绪检查 | http://127.0.0.1:9012/api/v1/health/ready |
| API / Worker Python | `D:\Anaconda\envs\SFC\python.exe` |
| MySQL / Redis | 复用现有本机 Docker 容器 |

当前本机入口使用隔离验收配置（`-Acceptance`），API、Worker 在 Windows 运行。启动器会检查配置、模型绑定、内存及 Worker 启动前的遗留任务；遇到阻断会保留已启动的服务并输出原因。前端能打开不代表 API 和 Worker 已就绪。

### 手动冷启动（不调用启动脚本）

以下步骤面向**本机已经配置完成的隔离验收环境**：依赖已安装、数据库已迁移、模型资产和受保护配置均已存在；不会创建数据库或安装依赖。打开三个独立的 **PowerShell 7** 终端，使用创建 DPAPI 密钥的同一 Windows 用户。每个服务前台运行并占用自己的终端。所有环境变量仅设置到当前终端及其子进程，关闭终端即失效，不使用全局环境变量或整批导入 `.env`。

若当前窗口是 Windows PowerShell 5.1，先进入 PowerShell 7（每个终端各执行一次）：

```powershell
$pwshPath = Join-Path $env:ProgramFiles 'PowerShell\7\pwsh.exe'
if (!(Test-Path -LiteralPath $pwshPath)) {
    $pwshPath = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\native\powershell\pwsh.exe'
}
if (!(Test-Path -LiteralPath $pwshPath)) { throw '未找到 PowerShell 7' }
& $pwshPath -NoProfile
```

**先准备 MySQL、Redis 和空闲运行环境。** 打开 Docker Desktop；用以下命令查看现有容器。如果数据库容器已停止，按列表中的实际名称分别执行 `& $docker start '容器名'`，不要启动其他项目或新建容器。数据库和 Redis 必须已健康。确认本项目没有其他 API、Worker 或 Vite 实例；不要让手动实例和受管实例同时运行。

```powershell
$docker = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
& $docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

**终端 A：前端。** 只从受保护配置提取前端所需的非敏感字段。沿用工作区现有已生成的前端模块清单，直接启动 Vite；不运行会重新生成清单的 `npm run dev`。

```powershell
$ErrorActionPreference = 'Stop'
$repo = 'D:\Jupyter\Chromatography_agent'
$runtimeRoot = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ChromatographyPlatform\assistant-accept-20261006'
$configPath = Join-Path $runtimeRoot 'runtime.json'
if (!(Test-Path -LiteralPath $configPath)) { throw '受保护验收配置不存在' }
$config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($key in @('API_PORT', 'FRONTEND_PORT', 'VITE_BUILD_PROFILE')) {
    if ($config.PSObject.Properties[$key]) {
        [Environment]::SetEnvironmentVariable($key, [string]$config.$key, 'Process')
    }
}
# 前端不得继承后端连接串或解密后的密钥。
foreach ($key in @('DATABASE_URL', 'SYNC_DATABASE_URL', 'API_KEY', 'CHROM_BATCH_CURSOR_SIGNING_KEY')) {
    [Environment]::SetEnvironmentVariable($key, $null, 'Process')
}
Set-Location (Join-Path $repo 'frontend')
foreach ($path in @('node_modules\vite\bin\vite.js', 'src\app\generated\registry.ts', 'src\app\generated\build-manifest.json')) {
    if (!(Test-Path -LiteralPath $path)) { throw "缺少前端依赖或生成文件：$path" }
}
& 'C:\Program Files\nodejs\node.exe' '.\node_modules\vite\bin\vite.js' --host 127.0.0.1 --port $env:FRONTEND_PORT --strictPort
```

**终端 B、C：各自先执行下面的公共准备块。** B 用于 API，C 用于 Worker；两个终端都必须加载，不能假定另一个终端的环境变量会同步过来。验收配置不直接存数据库连接串，因此按现有规则从 `priority-local/runtime.json` 派生到固定验收库，密码只在进程内传递，不打印配置或环境变量。

```powershell
$ErrorActionPreference = 'Stop'
$repo = 'D:\Jupyter\Chromatography_agent'
$python = 'D:\Anaconda\envs\SFC\python.exe'
$base = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ChromatographyPlatform'
$runtimeRoot = Join-Path $base 'assistant-accept-20261006'
$configPath = Join-Path $runtimeRoot 'runtime.json'
$databaseConfigPath = Join-Path $base 'priority-local\runtime.json'

# 验证现有受保护输入；只读，不更改 ACL。
function Assert-ManualProtectedPath([string]$Path) {
    if (!(Test-Path -LiteralPath $Path)) { throw '缺少受保护配置或密钥文件' }
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw '受保护路径不能是重解析点' }
    $acl = Get-Acl -LiteralPath $Path
    $allowed = @('S-1-5-18', [Security.Principal.WindowsIdentity]::GetCurrent().User.Value)
    $seen = @()
    foreach ($rule in $acl.Access) {
        $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        if ($rule.IsInherited -or $rule.AccessControlType -ne 'Allow' -or $sid -notin $allowed -or
            ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -ne [Security.AccessControl.FileSystemRights]::FullControl) {
            throw '受保护路径权限不符合当前用户与 SYSTEM 限制'
        }
        $seen += $sid
    }
    if (@($allowed | Where-Object { $_ -notin $seen }).Count) { throw '受保护路径权限不完整' }
}
if (!(Test-Path -LiteralPath $python)) { throw 'SFC Python 不存在' }
if ((Get-Item -LiteralPath $base -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw '运行配置父目录不能是重解析点' }
foreach ($path in @($runtimeRoot, $configPath, (Split-Path $databaseConfigPath), $databaseConfigPath)) {
    Assert-ManualProtectedPath $path
}
$config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$databaseConfig = Get-Content -LiteralPath $databaseConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($config.CHROM_ACCEPTANCE_DATABASE_SCHEMA -cne 'chrom_assistant_accept_20261006') { throw '验收库绑定不匹配' }
foreach ($property in $config.PSObject.Properties) {
    if ($property.Name -cnotmatch '^[A-Z][A-Z0-9_]*$' -or $property.Value -isnot [string] -or
        $property.Name -in @('HOME', 'USERPROFILE', 'CODEX_HOME', 'PATH', 'PSMODULEPATH', 'COMSPEC', 'API_KEY', 'CHROM_BATCH_CURSOR_SIGNING_KEY', 'DATABASE_URL', 'SYNC_DATABASE_URL')) {
        throw '验收配置包含不支持的字段；请核查，不要输出配置内容'
    }
    [Environment]::SetEnvironmentVariable($property.Name, $property.Value, 'Process')
}
foreach ($spec in @(@{Key='DATABASE_URL'; Driver='asyncmy'}, @{Key='SYNC_DATABASE_URL'; Driver='pymysql'})) {
    $pattern = '^(mysql\+' + $spec.Driver + '://[^/]+@127\.0\.0\.1:3306/)(?<schema>[^/?#]+)(?<query>\?.*)?$'
    if ([string]$databaseConfig.($spec.Key) -notmatch $pattern -or $Matches.schema -cne 'chrom_platform') { throw '数据库来源绑定不匹配' }
    $connection = $Matches[1] + $config.CHROM_ACCEPTANCE_DATABASE_SCHEMA + $Matches.query
    [Environment]::SetEnvironmentVariable($spec.Key, $connection, 'Process')
}
$connection = $null
$databaseConfig = $null
$env:CHROM_ASSISTANT_RUNTIME_ROOT = $runtimeRoot
$env:PYTHONPATH = Join-Path $repo 'backend\src'
$env:PYTHONUTF8 = '1'
[Environment]::SetEnvironmentVariable('API_KEY', $null, 'Process')
[Environment]::SetEnvironmentVariable('CHROM_BATCH_CURSOR_SIGNING_KEY', $null, 'Process')
if (!(Test-Path -LiteralPath $env:CHROM_BUILD_MANIFEST)) { throw '当前构建清单不存在' }
Set-Location $repo

# 在启动 API 前、启动 Worker 前分别执行，沿用现有 3.5 GiB 提交内存余量门槛。
$memory = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory
if (($memory.CommitLimit - $memory.CommittedBytes) / 1GB -lt 3.5) { throw '提交内存余量不足 3.5 GiB，请释放资源后重新检查' }
```

**终端 B：解密 API 游标签名密钥并启动 API。** 这里读取既有 DPAPI 文件，仅注入当前 API 进程环境；不读取或导出助手 Provider 的密钥。助手通过数据库中已激活的配置及其受保护密钥引用工作。

```powershell
$cursorPath = [IO.Path]::GetFullPath([string]$config.CHROM_BATCH_CURSOR_KEY_SOURCE_PATH)
if (!$cursorPath.StartsWith($runtimeRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw '游标密钥引用超出受保护目录' }
Assert-ManualProtectedPath (Split-Path $cursorPath)
Assert-ManualProtectedPath $cursorPath
if ((Get-Item -LiteralPath $cursorPath).Length -gt 1MB) { throw 'DPAPI 文件大小异常' }
Add-Type -AssemblyName System.Security
$plain = $null
try {
    try {
        $plain = [Security.Cryptography.ProtectedData]::Unprotect(
            [IO.File]::ReadAllBytes($cursorPath), $null,
            [Security.Cryptography.DataProtectionScope]::CurrentUser)
        $env:CHROM_BATCH_CURSOR_SIGNING_KEY = [Text.Encoding]::UTF8.GetString($plain)
    } catch { throw '当前 Windows 用户无法读取 DPAPI 游标密钥' }
    if ($env:CHROM_BATCH_CURSOR_SIGNING_KEY -cnotmatch '^[A-Za-z0-9_-]{43}$') { throw '游标签名密钥格式无效' }
    & $python -m uvicorn chrom_platform.app:create_app_from_environment --factory --host 127.0.0.1 --port $env:API_PORT
} finally {
    if ($plain) { [Array]::Clear($plain, 0, $plain.Length) }
    [Environment]::SetEnvironmentVariable('CHROM_BATCH_CURSOR_SIGNING_KEY', $null, 'Process')
}
```

**终端 C：先检查 API 就绪和遗留任务，再启动 Worker。** 在公共准备块后执行。下面的 Python 块只读数据库与 Redis，输出计数；发现排队、在途或待确认任务时停止，先确认任务归属，不能清队列或盲目重放。历史死信 `.XQ` 不作为可执行队列计数。

```powershell
Invoke-RestMethod -Uri ('http://127.0.0.1:' + $env:API_PORT + '/api/v1/health/ready')
@'
import json, os, sys
import redis
from sqlalchemy import create_engine, text
def check_idle():
    counts = {}
    engine = create_engine(os.environ['SYNC_DATABASE_URL'], connect_args={'connect_timeout': 5, 'read_timeout': 5, 'write_timeout': 5})
    try:
        with engine.connect() as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            for table in ('job', 'job_attempt', 'agent_run', 'agent_attempt'):
                states = "'QUEUED','RUNNING','CANCEL_REQUESTED'"
                if table.startswith('agent_'):
                    states += ",'WAITING_CONFIRMATION'"
                counts[table] = db.execute(text(f'SELECT COUNT(*) FROM {table} WHERE status IN ({states})')).scalar_one()
            counts['outbox'] = db.execute(text("SELECT COUNT(*) FROM outbox_event WHERE status IN ('PENDING','PROCESSING')")).scalar_one()
            db.rollback()
    finally:
        engine.dispose()
    client = redis.Redis.from_url(os.environ['REDIS_URL'], socket_timeout=5, socket_connect_timeout=5)
    try:
        queue = os.environ['WORKER_QUEUE']
        counts['queue'] = counts['inflight'] = 0
        for name in (queue, queue + '.DQ', queue + '-assistant', queue + '-assistant.DQ'):
            key = 'dramatiq:' + name
            kind = client.type(key)
            if kind == b'list': counts['queue'] += client.llen(key)
            elif kind == b'zset': counts['queue'] += client.zcard(key)
            for ack in client.scan_iter(match='dramatiq:__acks__.*.' + name, count=50):
                counts['inflight'] += client.scard(ack)
    finally:
        client.close()
    print(json.dumps({'idle': not any(counts.values()), 'counts': counts}))
    return 0 if not any(counts.values()) else 3
try:
    code = check_idle()
except Exception as exc:
    print('idle_check_failed:' + type(exc).__name__)
    code = 2
sys.exit(code)
'@ | & $python -
if ($LASTEXITCODE -ne 0) { throw '空闲检查未通过，暂不启动 Worker' }
$memory = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory
if (($memory.CommitLimit - $memory.CommittedBytes) / 1GB -lt 3.5) { throw '启动 Worker 前提交内存余量不足 3.5 GiB' }
& $python -m chrom_platform.worker_cli
```

三个终端均保持打开。另开普通终端检查 API；Worker 初始化后，可在另一个已执行公共准备块的终端运行健康检查（不会启动新 Worker）：

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:9012/api/v1/health/ready'
& $python -m chrom_platform.worker_cli --healthcheck
```

访问 **http://127.0.0.1:5174/**。上述命令不会写受管 PID 状态，`run-local.ps1 -Action status/stop` 不能管理这些手动进程。手动停止时先在另一个配置好的终端执行上面的只读空闲检查，确认空闲后在 C、B、A 各自终端按 `Ctrl+C`，等待退出；重启时重新执行相应配置准备与启动块。前端终端不注入后端密钥；后端终端关闭后其进程级环境随之销毁。不要打印 `$config`、数据库连接串、`Env:` 全表或 DPAPI 解密结果。

本节命令依据当前源码核对并做语法检查；文档更新不代表已额外启动一套服务或完成新一轮冷启动验收。

### 受管启动方式的状态与健康

```powershell
# 查看三项受管进程状态
& .\deploy\v1\run-local.ps1 -Acceptance -Action status -Service all

# API 就绪状态
Invoke-RestMethod -Uri 'http://127.0.0.1:9012/api/v1/health/ready'

# Worker 身份健康检查
& 'D:\Anaconda\envs\SFC\python.exe' .\deploy\v1\startup-check.py --worker-healthcheck
```

API 返回 `core_ready: true` 且 Worker 健康检查成功，才表示相应服务通过启动健康检查；这不等于真实预测任务验收。以下受管命令只适用于由启动器创建的进程；手动进程使用上一节的检查与停止方式。

### 停止与重启

先在 **PowerShell 7**、仓库根目录确认任务、Outbox 和队列空闲，再停止服务。`run-local.ps1` 的停止操作会使用 `Stop-Process -Force`；不要在检查失败或仍有在途任务时停止或重启 Worker。检查为空闲后，再停止全部应用服务（不停止 MySQL / Redis）：

```powershell
Set-Location 'D:\Jupyter\Chromatography_agent'
& 'D:\Anaconda\envs\SFC\python.exe' .\deploy\v1\startup-check.py --idle
if ($LASTEXITCODE -ne 0) { throw '空闲检查未通过；保持服务运行并先处理检查结果。' }
& .\deploy\v1\run-local.ps1 -Acceptance -Action stop -Service all
```

`run-local.ps1 -Action restart` 会直接强制停止再启动，不包含根启动器的提交内存门禁或 Worker 空闲检查，因此不要直接使用。需要重启或让代码改动生效时，在上面的安全停止完成后重新执行完整启动器：

```powershell
& .\deploy\v1\start-platform.ps1 -NoBrowser
```

受管进程状态与健康检查不能替代真实任务验收。

## 当前配置与日志

当前一键启动读取仓库外的受保护配置：

```text
%LOCALAPPDATA%\ChromatographyPlatform\assistant-accept-20261006\runtime.json
```

同一目录下的 `logs\` 保存运行日志，`state\` 保存受管进程和 Worker 状态。以启动窗口输出的实际路径为准。

- API、前端端口、构建清单、数据库、Redis、制品目录及模型绑定由受保护运行配置管理。
- 助手新 Run 使用数据库中已激活的 LLM 配置；API 和 Worker 需使用同一 Windows 用户解密 DPAPI 密钥。
- 当前受管启动入口不加载或合并仓库根目录 `.env`。不要照搬旧配置，也不要覆盖已有受保护配置。
- `deploy/v1/.env.example` 用于容器部署模板，并非当前 Windows 一键启动的配置入口。
- 新机器需要重新准备受保护配置、密钥、数据库和模型资产，不能只复制 README 命令即完成部署。

部署文件见 [deploy/v1](deploy/v1/)。其中历史运行记录可能使用其他配置模式；当前本机使用本节所述的固定隔离验收配置，可选择一键启动或上面的完整手动冷启动方式。

## 项目结构

```text
frontend/                 Vue 页面、组件、状态与统一 API 客户端
backend/src/chrom_platform/
  core/                   配置、访问上下文与公共协议
  modules/                预测、助手、制品、记录等业务模块
  adapters/               各类色谱科学适配器
  infrastructure/         数据库、Redis 与制品存储
backend/tests/            后端与契约测试
contracts/                OpenAPI、契约快照与类型生成依据
builds/                   构建清单与模块装配
deploy/v1/               Windows 受管启动及部署脚本
docs/                    PRD、接口、开发计划和实施记录
testing/                 验收状态、样例与证据
```

业务 API 统一使用 `/api/v1`。页面和助手复用相同业务服务，科学推理由 Worker 直接调用适配器。旧 Gradio、Agent 和 `Services/` 源码仍保留供历史追溯及代码复用，不作为当前平台的启动入口。

## 开发与验收资料

- [开发总计划](docs/plan.md)
- [产品 PRD](docs/PROJECT_PRD.md) / [前后端接口规范](docs/PROJECT_FRONTEND_BACKEND_API_SPEC.md)
- [正式契约索引](contracts/index.json) / [契约冻结记录](docs/V1_M3_M4_CONTRACT_FREEZE.json)
- [当前测试状态](testing/STATUS.md) / [交付就绪清单](docs/delivery_readiness.md)
- [五类批量验收证据](testing/runs/2026-10-22-five-types-batch/)

首版不包含 Coding Agent、Shell、Sandbox 或代码编辑器。训练、知识检索、评测发布等延期范围见开发计划。缺少真实模型或配置时应显示不可用；正式结果必须保留模型版本、输入条件、数值、单位和警告。真实密钥、连接凭证和受保护运行文件不得提交到仓库。
