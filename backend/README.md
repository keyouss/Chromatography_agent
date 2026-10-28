# Chromatography Platform Backend

V1 后端源码。当前基线只声明本机已经存在且已实测可导入的依赖；缺失依赖见
`DEPENDENCY_PROPOSAL.md`，未获授权前不安装。

生成各构建的 OpenAPI（只生成契约，不表示该构建可发布）：

```powershell
& 'D:\Anaconda\envs\SFC\python.exe' -m chrom_platform.generate_openapi `
  --builds-dir '..\builds' --output-dir '.\generated'
```

运行时必须显式提供不可变构建清单。源码 checkout 的 `builds/` 不会被运行时向上搜索；精简包应将清单放在包内 `chrom_platform/build-manifest.json`，或通过环境变量指定：

```powershell
$env:CHROM_BUILD_MANIFEST='D:\path\to\assembled\build-manifest.json'
& 'D:\Anaconda\envs\SFC\python.exe' -m uvicorn chrom_platform.app:create_app_from_environment --factory --port 9012
```

部署主体的项目范围通过服务端装配配置提供（例如 `CHROM_PROJECT_IDS`），请求 JSON 中的
`project_id` 只作为业务选择并会再次做范围校验。生产应用不注入内存数据库、内存队列或假模型。
缺少真实探测器时，已配置但未探测的基础设施显示 `UNKNOWN`；缺少 MySQL/Redis/Worker/模型资产时，
readiness 和能力接口会如实返回不可用或未知。结构规范化在未配置持久化及幂等记录存储时明确失败。


## 源码学习：app.py 与 worker_cli.py 完整源码教学

本阶段要解决的真实问题是：API 如何依据不可变 manifest 装配可见能力，以及 Worker 如何把数据库 Outbox 中的任务安全地交给 Redis/Dramatiq，再由固定 handler 执行并确认。学完后应能从 `create_app` 或 `main` 追踪入口、对象、状态和失败路径，解释为什么三字段 payload 不能替代服务端 Job/resource_ref，也能判断一次 ack 失败属于重试还是租约失效。

心智模型：`manifest → app/plan → engine/session → static handlers → Outbox lease → stable payload → resolve_active/heartbeat → handler → ack/retry/fail`。下面的片段按当前源码行号摘录，文档已确认源码关系；MySQL、Redis、Dramatiq、模型和重启行为未运行验证。

仓库地图（只列本次调用链会实际经过的边界）：

| 子目录 | 职责 | `app.py` / `worker_cli.py` 的关系 |
| --- | --- | --- |
| `assembly` | 按 manifest 拼装 M1–M4、路由和能力集合 | `app.py` 的装配主线；Worker 复用其契约和模块选择结果 |
| `core` | 配置、上下文、错误和公共协议 | 两个入口读取环境、传递 ActorContext/稳定消息契约 |
| `infrastructure` | 数据库、Redis、制品与运行时探测 | `app.py` 建立 API 依赖；Worker 建立 engine、session 和 transport |
| `task_core` | Outbox、lease、任务状态和调度协议 | `worker_cli.py` 调用 `claim_due`、`resolve_active`、`ack/retry/fail` |
| `modules` | prediction、records、datasets 等业务实现 | `app.py` 依据 manifest 暴露路由；Worker 注册对应生产 handler |
| `adapters` | 色谱算法与外部能力适配器 | 通常由模块 handler 间接调用，不由入口直接实现算法 |
| `artifact_core` | 文件/制品校验、引用与持久化边界 | API 侧产生并校验 `resource_ref`，Worker 只消费服务端引用 |
| `structure_core` | SMILES/结构规范化与结构领域规则 | API 装配结构服务；Worker 通过任务 handler 间接使用 |

### 1. app 工厂的完整装配

进入 `app.py:create_app` 时，已有输入是可选的 manifest 路径和 `contract_generation` 标志，尚无请求、任务或模型结果。函数先调用 `load_manifest`，再创建 FastAPI、ActorContext、数据库 session factory 和 M1 assembly，并把对象放入 `app.state`。输出是一套供 router、middleware 和服务使用的依赖，不是一次预测结果。

M1 总是装配。若同步数据库不可用，部分持久化对象保留 `None`；若环境提供 `CHROM_PROJECT_IDS`，则可形成只读的 `ConfiguredProjectReader`。M2 的条件是 `prediction/records/datasets` 与 manifest 有交集；M2 建立 prediction registry、结构 port、数据中心和 batch 开关。M3 只在 `knowledge/training/model_center` 被选中时装配；M4 只在 assistant 被选中时装配，并接收 assistant features 和 M2/M3 是否存在的信息。

```python
# backend/src/chrom_platform/app.py:create_app (函数 169；片段 206–233)
    m2_modules = frozenset(manifest.modules) & frozenset({"prediction", "records", "datasets"})
    if m2_modules:
        from .assembly.m2 import assemble_m2
        from .assembly.prediction import SQLAlchemyPredictionStructurePort, build_prediction_registry
        prediction_registry = build_prediction_registry(session_factory=session_factory)
        app.state.prediction_registry = prediction_registry
        m2 = assemble_m2(
            session_factory,
            structure=lambda session: SQLAlchemyPredictionStructurePort(session),
            capability_registry=prediction_registry,
            artifact=app.state.artifact_service,
            confirmation=m1.confirmation_service,
            modules=frozenset(manifest.modules),
            chromatography_types=frozenset(manifest.chromatography_types),
            batch_enabled=any("batch" in tools for tools in manifest.prediction_tools.values()),
        )
```

这段的控制流是 `if m2_modules` 选择性导入和装配；数据流是模块集合、色谱类型、registry 和 batch 标志进入 M2；状态流是 `app.state.m2_assembly` 被设置。未选 M2 时，精简构建不会加载 M2 ORM/Worker 代码。随后 M3/M4 按相同原则装配，M1 structure/artifact/task router 总是挂到 `/api/v1`；M2 router按 prediction、records、datasets 挂载，M3只挂有 routes 的 router，M4挂 assistant router。

`custom_openapi` 还把 `manifest.build_id` 写入 `x-build-id`，按 `chromatography_types` 调整 prediction input/result union。源码事实是契约随构建清单裁剪；设计推断是避免精简包在 OpenAPI 中宣称未包含能力。contract generation 仍不注入假 repository、队列或模型。

### 2. Worker 阶段一：配置是启动前的边界

进入 `WorkerConfig.from_env`（`worker_cli.py` 137–169 行）时没有 engine、session 或 broker。它必须先解析 `CHROM_BUILD_MANIFEST`、`DATABASE_URL` 和 `REDIS_URL`，再读取 manifest；profile 和同步数据库条件都依赖 manifest 的模块集合。

```python
# backend/src/chrom_platform/worker_cli.py:WorkerConfig.from_env (函数 137；片段 137–147)
    @classmethod
    def from_env(cls) -> "WorkerConfig":
        manifest_path = Path(_required_env("CHROM_BUILD_MANIFEST")).resolve()
        if manifest_path.suffix.lower() != ".json" or not manifest_path.is_file():
            raise WorkerConfigurationError("BUILD_MANIFEST_NOT_FOUND")
        database_url = _required_env("DATABASE_URL")
        redis_url = _required_env("REDIS_URL")
        _validate_database_url(database_url)
        manifest = load_manifest(manifest_path)
        profile = os.getenv("BUILD_PROFILE", "").strip() or None
        if profile is not None and profile not in PROFILE_ALLOWLIST:
            raise WorkerConfigurationError("PROFILE_UNSUPPORTED")
```

配置分支是：

- assistant 或 prediction 被选中时，必须有 `SYNC_DATABASE_URL`；assistant 还必须有 actor id 和 project scope。
- async URL 必须是 `mysql+asyncmy`，sync URL 必须是 `mysql+pymysql`，并检查 host/port/database 一致；Redis必须是 `redis` 或 `rediss`。
- lease、heartbeat、poll 必须满足 `0 < heartbeat < lease`；队列名非空且不超过 64 个字符。
- 输出是 `WorkerConfig` 的 path、URL、ready file、queue、时间和 profile 字段。此阶段没有外部副作用；错误被 `main` 转成 `worker_not_ready:<CODE>`。

### 3. Worker 阶段二：manifest 到 ProfileWorkerPlan

`assembly/manifest.py:load_manifest`（89–93 行）只读取显式路径或包内 manifest；`validate_manifest`（22–61 行）检查模块、适配器、工具、assistant feature 的组合。结果 `BuildManifest` 仍是数据，不是可执行导入路径。

`ProfileWorkerPlan.from_manifest` 检查 profile 与 manifest build id，按照 `_WORKER_CLASSES` 从 manifest modules选出 Worker，计算 `batch_enabled`，检查 `production_worker.py/worker.py` 是否存在，然后合并 allowed job kinds 和 allowed event types：

```python
# backend/src/chrom_platform/worker_cli.py:ProfileWorkerPlan.from_manifest (函数 208；片段 208–223)
        manifest = load_manifest(path)
        if manifest.build_id not in PROFILE_ALLOWLIST:
            raise WorkerConfigurationError("PROFILE_UNSUPPORTED")
        if expected_profile is not None and manifest.build_id != expected_profile:
            raise WorkerConfigurationError("PROFILE_MANIFEST_MISMATCH")
        selected = tuple(module for module in _WORKER_CLASSES if module in manifest.modules)
        batch_enabled = any("batch" in tools for tools in manifest.prediction_tools.values())
        if not selected:
            raise WorkerConfigurationError("NO_WORKER_MODULES_SELECTED")
        package_root = Path(__file__).resolve().parent / "modules"
        missing = [module for module in selected if not (
            (package_root / module / "production_worker.py").is_file()
            if module in {"prediction", "training", "assistant"}
            else (package_root / module / "worker.py").is_file()
        )]
```

batch + prediction 会加入 `batch_prediction`；records + prediction 会加入 `export`。随后 `import_worker_types()` 只按固定 module/class 对导入，不接受消息中的模块名。输出 plan 同时服务三个消费者：handler factory 用它注册实现，claim 用它过滤 job/event，consumer resolve 用它再次确认边界。

### 4. Worker 阶段三：engine、session factory 和健康

`WorkerRuntime.initialize`（1382–1441 行）先清理/检查 ready marker，生成 event loop，并由 `DATABASE_URL` 建 async engine。若 plan含 assistant 或 prediction，必须建立 sync engine；batch 属于 prediction路径，因此也需要 sync factory。接着 `_check_database_health()` 用 async engine执行 `SELECT 1`。

```python
# backend/src/chrom_platform/worker_cli.py:WorkerRuntime.initialize (函数 1382；片段 1382–1394)
    def initialize(self) -> None:
        _linux_execution_layer_required()
        self.ready_file.clear_existing()
        self.plan = ProfileWorkerPlan.from_manifest(self.config.manifest_path, expected_profile=self.config.profile)
        self.plan.import_worker_types()
        try:
            self.loop = asyncio.new_event_loop()
            self.async_engine = create_async_engine(self.config.database_url, pool_pre_ping=True)
            if {"assistant", "prediction"} & set(self.plan.worker_modules):
                if not self.config.sync_database_url:
                    raise WorkerConfigurationError("MISSING_SYNC_DATABASE_URL")
                self.sync_engine = create_engine(self.config.sync_database_url, pool_pre_ping=True, future=True)
            healthy, error = self.loop.run_until_complete(self._check_database_health())
```

健康成功后输出两个 factory：`async_sessionmaker(bind=self.async_engine, ...)` 和可选 `sessionmaker(bind=self.sync_engine, ...)`。它们是短事务的会话创建器，不是跨全程共享的 session。任何 WorkerConfigurationError 或其他异常都会关闭 loop/engine；错误分别归为原错误或 `DATABASE_INITIALIZATION_FAILED_<CODE>`。

### 5. Worker 阶段四：静态 handler registry

若 `self.handlers` 未预注入，initialize 调用默认 `build_production_handlers`。它按 plan 注册 prediction、batch_prediction、training、assistant、knowledge_index、export 和 model-center handlers；静态 registry 的 key 是 event type，闭包内部再按锁定 Job 的 kind/resource_ref 选择业务实现。

```python
# backend/src/chrom_platform/worker_cli.py:build_production_handlers (函数 544；片段 609–621)
            selected["prediction"] = MappingHandlerAdapter(build_prediction_worker(
                session_factory=async_session_factory,
                registry=prediction_registry,
            ))
            if getattr(plan, "batch_enabled", False):
                from chrom_platform.modules.prediction.batch_worker import BatchPredictionProductionWorker
                if sync_session_factory is None:
                    raise WorkerConfigurationError("BATCH_SYNC_SESSION_FACTORY_UNAVAILABLE")
                selected["batch_prediction"] = MappingHandlerAdapter(BatchPredictionProductionWorker(
                    session_factory=async_session_factory,
                    sync_session_factory=sync_session_factory,
                    registry=prediction_registry,
```

当前 batch 实现是 `backend/src/chrom_platform/modules/prediction/batch_worker.py:BatchPredictionProductionWorker`，其 `handle/execute/cancel/retry` 在约 756–806 行；这是源码事实。缺少 artifact store、sync factory、assistant service 等 reviewed port 时，handler 会绑定为安全失败或初始化直接失败，而不是用 mock 结果继续。

### 6. Worker 阶段五：Outbox 的数据库 lease

`AsyncOutboxDispatcher.claim_due`（`task_core/outbox_dispatch.py` 315 行）进入时已有 owner、允许的 event/job kinds 和当前时间。due 条件包括 `PENDING + available_at <= now`，以及 `PROCESSING + lease_expires_at <= now`。查询把 OutboxEvent 和 Job 按 aggregate id 连接，按可用时间排序，`limit(32)` 并使用 `FOR UPDATE SKIP LOCKED`。

每行先校验三字段 payload 与 Job 的 aggregate/attempt/dispatch 关联，还要求 project_id 和 resource_ref 是服务端有效对象。坏行被 quarantine/FAILED；attempt 超限会标记 shared Job FAILED。成功 claim 将行写为 PROCESSING，递增 attempts，写 owner、lease token 和 expiry，返回含 `payload`、`resource_ref`、`project_id` 的 `OutboxClaim`。

```python
# backend/src/chrom_platform/task_core/outbox_dispatch.py:AsyncOutboxDispatcher.claim_due (函数 315；片段 401–419)
                    token = int(event.lease_token or 0) + 1
                    attempts = int(event.attempts or 0) + 1
                    event.status = OutboxStatus.PROCESSING.value
                    event.attempts = attempts
                    event.lease_owner = owner
                    event.lease_token = token
                    event.lease_expires_at = expires
                    return OutboxClaim(
                        dispatch_id=event.dispatch_id,
                        payload=payload,
                        event_type=event.event_type,
                        aggregate_type=event.aggregate_type,
                        aggregate_id=event.aggregate_id,
                        project_id=job.project_id,
                        resource_ref=dict(job.resource_ref),
                        attempts=attempts,
                        lease_owner=owner,
```

三字段 payload 是跨进程稳定关联，不承载业务输入。resource_ref、project_id、Job kind由数据库返回；因此消息重放时，consumer仍必须回库确认，不得信任队列中的业务快照。

### 7. Worker 阶段六：poll_once 到 Redis/Dramatiq

`OutboxDispatcher.poll_once`（worker_cli.py 876–897 行）先调用 `claim_due`。无 claim返回 False；有 claim时再次调用 `StableWorkerMessage.from_payload`，随后 `broker.send(claim.payload)`。发送异常根据 attempts计算退避并 `store.retry`；发送成功保持 Outbox PROCESSING，等待consumer执行和 ack。

```python
# backend/src/chrom_platform/worker_cli.py:OutboxDispatcher.poll_once (函数 876；片段 876–891)
    async def poll_once(self, now: datetime | None = None) -> bool:
        now = now or _utc_now()
        claim = await self.store.claim_due(
            owner=self.owner,
            allowed_event_types=self.allowed_event_types,
            allowed_job_kinds=self.allowed_job_kinds,
            now=now,
        )
        if claim is None:
            return False
        try:
            StableWorkerMessage.from_payload(
                claim.payload, event_type=claim.event_type,
```

`DramatiqTransport.create`（1321–1345 行）延迟导入 Dramatiq 和 RedisBroker，创建固定 queue actor；当前 actor 配置 `max_retries=0`，所以数据库 Outbox 的 lease/retry才是业务重投递机制。Redis只传稳定 payload，不能替代数据库的 Job/Attempt/resource_ref。

### 8. Worker 阶段七：consumer 的 resolve、heartbeat 和 handler

Dramatiq actor调用 `backend/src/chrom_platform/worker_cli.py:OutboxDispatcher.consume(payload)` 后，第一步是 `store.resolve_active`，不是直接执行业务。resolve按 dispatch_id、PROCESSING、owner、未过期 lease、允许 event/job kind重新锁定 OutboxEvent + Job，并比较数据库 payload和入站 payload。

```python
# backend/src/chrom_platform/task_core/outbox_dispatch.py:AsyncOutboxDispatcher.resolve_active (函数 433；片段 453–466)
        statement = (
            select(OutboxEvent, Job)
            .join(Job, and_(Job.id == OutboxEvent.aggregate_id, OutboxEvent.aggregate_type == "job"))
            .where(
                OutboxEvent.dispatch_id == payload["dispatch_id"],
                OutboxEvent.status == OutboxStatus.PROCESSING.value,
                OutboxEvent.lease_owner == owner,
                OutboxEvent.lease_expires_at > now,
                OutboxEvent.event_type.in_(allowed),
            )
            .with_for_update()
        )
        if kinds is not None:
            statement = statement.where(Job.kind.in_(kinds))
```

resolve失败意味着消息已过期、被其他 owner接管或关联已不一致。resolve成功后解析 `StableWorkerMessage`，从静态 handlers按 event type取 handler；缺少 handler调用 `fail_permanent`（若可用）并抛 `WORKER_HANDLER_BINDING_UNAVAILABLE`。有 handler时 `start_heartbeat` 创建 `LeaseGuard`，heartbeat用相同 owner/token更新 lease。续租失败会把 guard标记为 lost，后续 `guard.check()`阻止旧执行确认。

### 9. ack、retry、永久失败和租约丢失

handler返回结果后，consume停止 heartbeat，再检查 guard，然后调用 `store.ack(claim)`。ack是带 owner、lease token、PROCESSING和未过期条件的 CAS；rowcount为 0就说明旧 Worker已经失去 lease，不能把 Outbox写成 SENT。

```python
# backend/src/chrom_platform/task_core/outbox_dispatch.py:AsyncOutboxDispatcher.ack (函数 497；片段 497–510)
    async def ack(self, claim: OutboxClaim, *, now: datetime | None = None) -> bool:
        now = _utc(now)
        async with self.session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    update(OutboxEvent)
                    .where(
                        OutboxEvent.dispatch_id == claim.dispatch_id,
                        OutboxEvent.status == OutboxStatus.PROCESSING.value,
                        OutboxEvent.lease_owner == claim.lease_owner,
                        OutboxEvent.lease_token == claim.lease_token,
                        OutboxEvent.lease_expires_at > now,
```

异常有三个主要出口：LeaseLostError/guard lost转换为 `STALE_OUTBOX_LEASE`；WorkerConfigurationError调用 `fail_permanent`，把确定性 dispatch错误投影为失败；其他异常调用 `retry`。retry用相同 owner/token CAS，未达 max attempts回到 PENDING，超过上限变成 FAILED并调用 `_mark_shared_job_failed`。因此状态不是“broker成功即完成”，而是 `PROCESSING → SENT/PENDING/FAILED`。

### 10. event loop、ready marker、信号和 shutdown

`WorkerRuntime.run`要求 transport/dispatcher已初始化，启动 `outbox-dispatcher`线程；线程绑定 Worker loop并执行 `_poll_loop_async`，按 poll seconds调用 poll_once。poll loop异常设置 fatal_error 和 stop event，是 Worker级停止，不等同于单条消息retry。

```python
# backend/src/chrom_platform/worker_cli.py:WorkerRuntime.run (函数 1443；片段 1443–1455)
    def run(self) -> None:
        if self.transport is None or self.dispatcher is None:
            raise WorkerConfigurationError("WORKER_NOT_INITIALIZED")
        self.poll_thread = threading.Thread(target=self._poll_loop, name="outbox-dispatcher", daemon=True)
        self.poll_thread.start()
        if not self._loop_ready.wait(timeout=max(5.0, self.config.lease_seconds)):
            raise WorkerConfigurationError("WORKER_EVENT_LOOP_UNAVAILABLE")
        self.transport.worker.start()
        with self.ready_file:
            self.ready_file.mark_ready()
            if self.ready_file.startup_conflict or not self.ready_file.created:
                raise WorkerConfigurationError("WORKER_ALREADY_RUNNING")
            self.stop_event.wait()
```

`ReadyFile.mark_ready` 只在 poll loop和Dramatiq worker启动后写入 marker。Windows使用 mutex，Unix使用 flock；healthcheck还核验 ready、PID、process starttime和平台信息。SIGTERM/SIGINT只设置 stop event；shutdown随后 join poll thread、停止 Dramatiq worker、关闭 broker、删除自有 marker并释放 async/sync engine。

### 11. 三条流：把局部代码放回系统

**控制流**：`main → WorkerConfig → WorkerRuntime.initialize → handler factory/dispatcher/transport → run → poll_once → broker actor → consume → handler → ack`。初始化失败不会进入 run；poll loop的未捕获异常会停止 Worker。

**数据流**：manifest → plan；plan → allowed kinds/events；Outbox → stable payload + server-side resource_ref；Redis只转发 `job_id/attempt/dispatch_id`；consumer回库得到 Job、Attempt、project和resource_ref；handler写业务结果、事件和错误；ack只确认当前 lease仍可完成。

**状态流**：进程从未 ready到 ready再到 shutdown；Outbox从 PENDING/过期 PROCESSING到 PROCESSING，再到 SENT、PENDING retry或FAILED；Job/Attempt由业务 handler或 dispatch failure投影为运行、成功、失败、取消。跨异步边界必须同时看 attempt、dispatch_id、owner、lease_token，不能只看 job_id。

### 12. 学习边界

必须理解：app的 M1/M2/M3/M4分支；WorkerConfig的 manifest依赖条件；ProfileWorkerPlan的 allowed kinds/events；async/sync engine和两个 session factory；静态 handler registry；`claim_due → poll_once → resolve_active → heartbeat → handler → ack/retry/fail_permanent`。

建议理解：custom_openapi的 schema裁剪、`_CURRENT_LEASE_GUARD`、consume gate的重复消息缓存、ReadyFile的 Windows mutex、batch export的 artifact store。暂时跳过：具体模型特征、LLM provider、导出渲染、旧 `Services/unified_server.py` 和未被当前 profile选中的 Worker。

源码事实是函数定义、调用关系、错误码、状态条件和当前 batch注册；设计推断是“manifest前置裁剪降低误路由风险”和“稳定三字段让数据库成为业务数据源”。未验证仍包括真实 MySQL/Redis/Dramatiq连接、模型推理、batch提交、重启恢复和端到端浏览器链路。

### 13. 练习、迁移自测和唯一下一入口

标准练习（不看答案）：给定 prediction + batch manifest，`DATABASE_URL`格式正确但没有 `SYNC_DATABASE_URL`。沿 `main → from_env → initialize`画控制流，写出最先错误码，判断 async engine是否已创建、哪些资源会关闭，以及为什么还未进入 handler factory。

迁移自测（不提供答案）：普通 prediction handler已经写入业务结果，但 ack前 lease过期，另一 Worker重新 claim并处理同一 dispatch。分别追踪两个 Worker的 owner/token、guard、ack CAS、Outbox状态和业务幂等责任，指出哪一步阻止旧 Worker把消息标成 SENT。不要只回答“会重试”。

唯一下一入口是 `backend/src/chrom_platform/task_core/outbox_dispatch.py:AsyncOutboxDispatcher.claim_due`（315 行），下一轮从 due SQL条件、`FOR UPDATE SKIP LOCKED`、payload校验和 lease CAS开始。


