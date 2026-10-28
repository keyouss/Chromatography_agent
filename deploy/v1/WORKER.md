# V1 Worker 运行说明

Worker 入口由交付镜像显式配置 `WORKER_COMMAND`，入口实现为 `python -m chrom_platform.worker_cli` 时，进程只接受四个已登记构建 profile：`universal-v1`、`sfc-v1`、`cc-v1`、`minimal-structure-v1`。它从 `CHROM_BUILD_MANIFEST` 加载并校验 profile，只绑定清单中实际包含的静态 Worker 模块；消息不能指定 Python 导入路径或命令。

启动阶段依次校验配置、构建 profile、Worker 模块、数据库健康、静态 handler registry 和 Redis/Dramatiq 初始化。Dramatiq 采用延迟导入；缺少 Dramatiq/Redis 驱动、数据库、Broker 或业务 handler 时进程以明确错误退出。此时不会写入 ready 文件；已有同一路径的过期 ready 文件会先清理。

ready 文件只表示上述真实初始化已经完成。它由进程在同一目录写入临时文件并原子替换为 `WORKER_READY_FILE`，内容是包含 `ready`、`pid` 和唯一 `instance_id` 的 JSON。启动时若 marker 属于仍存活的其他 PID，进程以 `WORKER_ALREADY_RUNNING` fail closed；退出时仅在 marker 仍属于本实例时删除。进程收到 `SIGINT` 或 `SIGTERM` 后停止接收、等待轮询线程退出、停止 Dramatiq Worker 并关闭 Broker。轮询发生未处理异常时，进程清理 ready 后以非零状态退出。容器重启时应让 Compose 重新执行完整初始化；不能把遗留 ready 文件当作存活证明。

当前仓库的 `backend/pyproject.toml` 未声明 Dramatiq 或 `asyncmy`，因此本仓库测试只验证 fail-closed 和可注入 transport 行为，不宣称真实 Redis/Dramatiq broker 或生产 Worker 已验收。生产镜像必须在其审计依赖清单中提供这些运行时依赖，并通过实际 Broker/数据库检查后再部署。

当前源码也没有安全的生产 handler factory；默认 `main()` 不注入 handler registry，会以 `WORKER_HANDLER_BINDING_UNAVAILABLE` fail closed，因而当前 CLI 不能进入 ready。测试中的 handler/transport 注入只用于验证组件边界、ready 归属和退出清理，不代表生产绑定或真实 Broker 已可用。

Outbox 轮询使用数据库租约核心。profile 清单生成静态 `Job.kind` 和 `event_type` allowlist：`sfc-v1`/`cc-v1` 只允许 `prediction`，`universal-v1` 允许已构建的 prediction、knowledge、training、model-center 和 assistant kinds；`minimal-structure-v1` 没有 Worker。SQL claim 同时按 kind、event type 和过期 lease 过滤，未知或未构建事件不会被抢占。`job.execute` 与 `job.cancel` 进入独立 handler key，取消不会复用执行 handler。

Broker payload 永远只有 `job_id`、`attempt`、`dispatch_id`。发送成功后 outbox 保持 `PROCESSING`；consumer 收到消息后在短 AsyncSession 事务中按 dispatch、当前 owner/token、Job attempt、project 和 resource_ref 重新加载并核验，再在事务结束后执行 handler。成功执行后用 owner/token CAS ack，失败用相同租约 CAS retry；过期租约不能覆盖新 owner。当前源码仍未提供真实业务 handler factory，缺少注入时启动继续 fail closed。

总控若将 Compose worker 健康检查接入本 CLI，应在部署配置中把当前的文件存在性检查：

```yaml
healthcheck:
  test: ["CMD", "python", "-m", "chrom_platform.worker_cli", "--healthcheck"]
```

替换为上述只读命令。该建议未修改 `deploy/v1/compose.yaml`；命令不读取数据库配置、不连接 Broker，也不修改 ready marker。Linux 下它还会校验 boot ID、进程 starttime 和 `/proc/<pid>/cmdline` 中的 `chrom_platform.worker_cli`，以降低 PID 复用或容器重启造成的误报。
