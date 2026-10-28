# V1 部署与离线交付基线（M5）

本目录只描述新平台 `/api/v1` 的部署。它不启动旧 `8010` 服务，也不读取主仓库的绝对路径。Compose 需要一个已经验收的离线运行时镜像和一个 profile staged 包；镜像、Python/Node 依赖和科学模型资产均由交付清单锁定，部署脚本不会联网拉取、安装或生成模型。

## 生成 profile staged 包

后端和前端先分别完成静态 profile 装配，再交给离线装配器。四个可选 profile 为 `universal-v1`、`sfc-v1`、`cc-v1` 和 `minimal-structure-v1`。下面命令只复制已存在的 staged 目录：

```powershell
& 'D:\Anaconda\python.exe' 'D:\Jupyter\Chromatography_agent\scripts\assemble_offline_bundle.py' `
  --profile 'minimal-structure-v1' `
  --backend-package 'D:\staged\minimal-structure-v1\backend' `
  --frontend-package 'D:\staged\minimal-structure-v1\frontend' `
  --image-lock 'D:\staged\minimal-structure-v1\image-lock.json' `
  --output-dir 'D:\offline\minimal-structure-v1'
```

装配器要求后端 `build-manifest.json`、`SOURCE_MANIFEST.json`、前端 `ASSEMBLED_PROFILE.json`、前端 `dist/index.html`、不可变镜像摘要和所有选中色谱类型的模型资产清单。它还会核对源码文件哈希、模块/路由对应的物理裁剪、迁移闭包、worker 集合和依赖锁；缺任一项只输出 `OFFLINE_BUNDLE.json` 的 `BLOCKED` 报告，不输出 ZIP。`.env.example` 必须包含非空的已审计 `WORKER_COMMAND`，装配后镜像变量会与 `image-lock.json` 严格一致。不会用 mock、静态 JSON 或空 worker 冒充可交付能力。包含预测适配器的当前源码 profile 没有随包提供经验证模型资产，因此现阶段会明确报告 `MODEL_ASSETS_MISSING`，这是科学功能交付阻塞。

## 校验与 ZIP SHA-256

装配成功会输出：

```text
chrom-platform-<profile>.zip
chrom-platform-<profile>.zip.sha256
<output>/SHA256SUMS
<output>/OFFLINE_BUNDLE.json
```

校验目录或 ZIP：

```powershell
& 'D:\Anaconda\python.exe' 'D:\Jupyter\Chromatography_agent\scripts\verify_offline_bundle.py' `
  'D:\offline\minimal-structure-v1\chrom-platform-minimal-structure-v1.zip'
```

校验器会检查 profile 一致性、裁剪清单及模型资产 SHA-256、每个文件的 SHA-256、ZIP 内路径/重复项/符号链接、镜像锁与 `.env.example` 一致性、Compose 服务是否只挂载 `profiles/<profile>`，并拒绝旧端口、Docker socket、Coding/Shell/Sandbox 运行组件、主仓库绝对路径和未声明文件。导入隔离网络前应将校验输出和 `.zip.sha256` 一并归档。

## 配置与启动

在离线包根目录复制 `.env.example` 为 `.env`，填写实际的镜像摘要、数据库密码、部署密钥文件和已审计 worker 命令。`API_PORT` 默认 `9012`，它从 `.env` 读取并只控制宿主机映射；容器内 API 固定监听 `9012`，Nginx 将 `/api/v1` 转发到该端口。公开入口默认 `WEB_PORT=8080`，API、前端、MySQL 和 Redis 默认只绑定回环地址或 Compose 内网。

迁移必须在 API 镜像和 MySQL 健康后由管理员显式执行：

```powershell
docker compose --env-file .env run --rm api alembic -c alembic/alembic.ini upgrade head
docker compose --env-file .env up -d mysql redis api worker frontend nginx
docker compose --env-file .env ps
```

检查入口和就绪状态：

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/health/live'
Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/health/ready'
```

`health/live` 只表示 API 进程存活；`health/ready` 只有 MySQL 和 Redis 探测为 `READY` 才返回 200。API、前端、Nginx、MySQL、Redis 和 worker 都有 Compose 健康检查。Worker 镜像必须提供 `python -m chrom_platform.worker_cli --healthcheck`：只读检查 `WORKER_READY_FILE` 的就绪标记及所属进程身份，缺失、无效、过期或身份不匹配时返回非零；仅文件存在或 PID 存活不能作为通过依据。健康检查不得启动 Worker 或推动业务任务。入口及真实运行验收状态见 [WORKER.md](WORKER.md)；尚未支持该检查的镜像不能通过本部署配置验收。

## 重启、升级与停止

代码、profile、镜像或 `.env` 变更后执行 `docker compose --env-file .env up -d --force-recreate api worker frontend nginx`；迁移版本变化时先重新执行迁移，再重启业务服务。查看日志使用 `docker compose --env-file .env logs --tail=200 api worker nginx`。停止服务使用 `docker compose --env-file .env down`；带数据卷的删除属于数据破坏操作，必须另行确认并按备份流程执行。

运行中的容器不会自动加载 staged 包修改。发布新 ZIP 时应先做 SHA 校验、备份数据和审计清单，再替换整个 profile 包并重建容器。

## 当前交付阻塞

本仓库已有后端/前端静态装配器，但未提供可供本目录直接发布的完整运行时镜像摘要、worker 入口和真实模型资产。`compose.yaml` 中的镜像和 `WORKER_COMMAND` 使用必填变量，缺失时 Compose 会拒绝启动；预测 profile 的离线装配也会因模型资产缺失而返回 `MODEL_ASSETS_MISSING`。这保证健康检查和部署说明不会把不可用科学能力宣称为已交付。

### 当前验收限制（2026-09-21）

candidate-06 因 Worker 装配及任务生命周期缺口暂不接受。后续候选必须验证迁移的完整父链、Worker 入口物理存在，以及 minimal 构建实际移除 Worker 服务、专属卷、镜像和环境配置；不能仅用 Compose profiles 隐藏服务。通用 compose.yaml 是带 Worker 的部署模板，不能原样用于不含 Worker 的 minimal 包。离线装配器与校验器正在同步这些规则，在独立验收完成前不作为发布依据。`.env.example` 的空 `WORKER_COMMAND` 供含 Worker 的构建填写经审查入口，不意味着已有可就绪的生产处理器。
