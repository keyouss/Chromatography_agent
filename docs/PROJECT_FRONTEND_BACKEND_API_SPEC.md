# 通用色谱智能平台前后端接口文档

文档状态：TO-BE · 首版可实施接口契约  
文档版本：0.2  
目标发布：V1 · 业务助手版，不含 Coding Agent  
更新日期：2026-10-13  
产品范围：`PROJECT_PRD.md` v0.12  
后续升级：`CODING_AGENT_UPGRADE_PLAN.md` v0.1（不作为首版路由清单）

> Codex 必须先根据本文件建立 Pydantic DTO、OpenAPI、前端类型及契约测试，再实现业务。此文是新通用产品契约，不是旧服务说明；不兼容旧端口、旧服务、旧字段别名或原型中的临时接口。
> 实施状态补记（2026-10-13）：本文件定义产品目标接口，不代表每个 operation 已冻结或通过。正式契约以 `contracts/` 中已冻结快照为准；`contracts/candidates/` 下的变更是候选，必须完成契约审查、生成类型与构建装配审核后单独冻结。当前阶段按已批准的 Windows API/Worker 真实验收推进；本注释不改变字段/schema，也不批准 Linux 容器或新增依赖。当前功能证据和验收门槛见 [开发计划](plan.md)。

## 0. 生效范围与显式变更

### 0.1 唯一新调用关系

```text
浏览器同源 /api/v1 → Nginx/Vite proxy → FastAPI（API_PORT，默认 9012）
FastAPI → Application Services → MySQL / ArtifactStore / Outbox
Outbox → Redis + Dramatiq → 已构建 Worker → 固定模型适配器/模板
Agent Worker → PydanticAI adapter → 同一 Application Services
```

没有必需的旧 `127.0.0.1:9012` 推理服务。新 9012 只表示默认 FastAPI 业务端口；浏览器不可绕业务层直连内部模型、MySQL、Redis 或制品目录。开发测试客户端可访问配置后的业务 API。

旧 `8010` 等接口、旧 `Services/unified_server.py`、旧 Gradio API、旧 `/api/tools/...` 不做代理、别名或自动回退。不保留旧文档磁盘路径作为运行依赖。

### 0.2 相比用户提供的 v0.1，本轮明确调整

| 原设计中的不确定/冲突 | 本版决定 |
|---|---|
| 业务 API 与旧推理服务都指向 9012 | 业务 API 唯一默认 9012；模型默认由 Worker 直接调用 |
| 单次预测可能同步也可能异步 | 统一 202 + prediction_id/job_id |
| 请求同时包含 structure_refs 与多处原始 SMILES | 一般预测只引用结构对象；仅 HPLC single schema 1.1 保存与两个结构引用逐槽绑定的有序原始输入，canonical 仍是身份事实 |
| TLC 固定五元素向量与 PE/EA 原型混淆 | 使用命名溶剂及比例；适配器内部才映射旧模型向量，禁止 PE→hexane 静默替换 |
| confirmation_token 与多处 confirm endpoint | 统一 Confirmation 资源 + 资源版本/哈希 + 一次性消费；不注册旧别名 |
| 助手消息无独立执行对象 | POST 消息返回 Agent Run；Run/Job/ToolCall/LLM Call 分离 |
| SSE 可选且无重放契约 | 首版 SSE + 持久有序事件；轮询降级；连接不决定任务生命周期 |
| 是否保存预测记录依赖按钮 | 创建预测即持久化记录；不提供 `/save-record` |
| `CANDIDATE_CREATED` 与训练终态混合 | Experiment.status 为训练状态；candidate_status/candidate_model_id 单独表达 |
| 模型三维状态可能误当全局单值 | 部署和 Champion 绑定 scope，多 scope 返回数组 |
| 模型登记可以含任意载入行为 | 首版仅可信预注册 Loader/数据型权重；拒绝任意代码与不可信反序列化 |
| 首版 Coding 预留程度 | 不注册任何 Coding/Sandbox/Workspace API，不建专属表，不暴露假状态 |

这是本轮新设计，不是对真实旧数据/模型资产的自动迁移承诺。模型科学输入和指标仍须经真实 Adapter Manifest/评测协议确认；未提供的材料应报告阻塞。

## 1. 通用协议

### 1.1 地址、版本与编码

所有下文业务路径都位于 `/api/v1` 下。唯一例外是 FastAPI 规范文件 `/openapi.json` 与可按部署关闭的交互文档 `/docs`。

```dotenv
API_HOST=0.0.0.0
API_PORT=9012
FRONTEND_HOST=127.0.0.1
FRONTEND_PORT=5173
WEB_PORT=8080
```

以上是模板默认值，不得硬编码在前端或客户端中。API 端口不应在生产宿主机公开映射，除非运维明确需要。所有 JSON 使用 UTF-8 和 `snake_case`；前端生成类型也保持 `snake_case`，不在页面内做各自转换。

时间统一 UTC RFC3339，例如 `2026-09-20T06:00:00Z`。JSON number 禁止 NaN/Infinity；未知值用 `null`，不以 0 或 `-` 替代。

### 1.2 本文类型记法

- 字段后 `?`：请求可省略；`T | null`：字段存在但值可空。两者不同。
- 除明确 `?` 的字段外，表中字段必填。
- 所有外部请求 `extra=forbid`，禁止未知字段。更新只允许各资源列出的可变字段。
- `ID`：服务端生成的不透明字符串，最大 96 字符；客户端不可从 ID 推断权限、路径或主机。
- `Hash`：64 个小写十六进制字符，字段名使用 `sha256` 或 `config_hash`，不混用带前缀/无前缀。
- `Revision`：正整数，从 1 开始；`etag` 由服务端生成并包含双引号的 HTTP ETag。
- `ResourceRef`：`{type: string, id: ID}`；type 必须在本构建注册白名单中。
- 只有服务端冻结的 `input_schema`/`parameters_schema` 等允许动态 JSON Schema；不得把任何客户端 JSON 当成待执行代码或任意 Python 导入配置。

### 1.3 请求头

| Header | 规则 |
|---|---|
| Content-Type | JSON 写请求为 application/json；上传内容为 application/octet-stream |
| Accept | JSON 用 application/json；流接口用 text/event-stream |
| X-Request-Id | 可选；校验长度/字符，服务端生成或接受合法值并回显 |
| Idempotency-Key | 下文标 I 的写操作必需，1–128 字符；缺失 400 IDEMPOTENCY_KEY_REQUIRED |
| If-Match | 下文标 E 的操作必需；更新资源或确认目标的当前 ETag；缺失 428，过期 412 |
| Last-Event-ID | SSE 重连使用该流已消费的 seq；不得放 Cookie/Token |
| Authorization | 后续 API/Bearer 认证入口；首版不签发 Token |

生产同源浏览器后续优先 HttpOnly/Secure/SameSite Cookie；启用 Cookie 认证时执行 CSRF 防护。首版默认主体部署仍须限制 Host/Origin/网络暴露；这些限制不等于已实现账号认证。

### 1.4 成功封装

```json
{
  "data": {"id": "pred_example", "status": "QUEUED"},
  "warnings": [],
  "request_id": "req_example"
}
```

所有 JSON 成功响应使用该封装。列表增加 `page`：

```json
{
  "data": [],
  "page": {"next_cursor": null, "limit": 50, "has_more": false},
  "warnings": [],
  "request_id": "req_example"
}
```

Warning：`{code, message, resource_ref?: ResourceRef, field?: string}`。204 无响应体；文件/SVG/SSE 不使用 JSON 成功封装。所有响应含 `X-Request-Id`。

### 1.5 错误封装

```json
{
  "error": {
    "code": "VALIDATION_FAILED",
    "message": "流速必须大于 0",
    "field_errors": [
      {"path": "input.flow_rate_ml_min", "code": "OUT_OF_RANGE", "message": "必须大于 0"}
    ],
    "retryable": false,
    "details": {}
  },
  "request_id": "req_example"
}
```

FastAPI/Pydantic 错误统一映射到该结构；不得透出内部路径、SQL、密钥或栈。`details` 使用按 error code 白名单字段，不转储异常对象。

| HTTP | 使用规则 |
|---:|---|
| 200 / 201 / 202 / 204 | 查询/更新、资源创建、异步受理、无体完成 |
| 400 | 语义错误、缺少幂等键 |
| 401 | 认证已启用而凭证无效；首版默认模式不伪造登录态 |
| 403 | 当前主体/操作通道无权执行，或数据发送策略禁止 |
| 404 | 资源不存在/不可见/路由未构建；避免泄露跨项目存在性 |
| 409 | 状态冲突、会话占用、哈希冲突、确认失效或生产版本冲突 |
| 410 | 已授权但内容已到期、SSE 游标已超重放窗口 |
| 412 / 428 | If-Match 不匹配 / 必需条件头缺失 |
| 413 / 415 / 422 | 内容超限 / 类型不支持 / schema 字段错误 |
| 429 | 限流或配额，返回 Retry-After |
| 500 / 503 | 未预期错误 / 已构建能力的依赖未就绪 |

关键错误码：`VALIDATION_FAILED`、`CAPABILITY_UNAVAILABLE`、`MODEL_UNAVAILABLE`、`MODEL_CONTRACT_MISSING`、`PERMISSION_DENIED`、`STATE_CONFLICT`、`VERSION_CONFLICT`、`IDEMPOTENCY_KEY_REUSED`、`CONFIRMATION_INVALID`、`CONFIRMATION_EXPIRED`、`ARTIFACT_NOT_AVAILABLE`、`UNSAFE_MODEL_FORMAT`、`RESOURCE_LIMIT_EXCEEDED`、`CONVERSATION_BUSY`、`CONTEXT_LIMIT_EXCEEDED`、`PROVIDER_UNAVAILABLE`、`EXTERNAL_SEND_DENIED`、`EVENT_CURSOR_EXPIRED`。

未构建路由不返回“未启用模块”占位资源；其不存在于 OpenAPI，实际请求为普通 404。

### 1.6 分页与排序

默认 `limit=50`，最大 200；`cursor` 不透明且签名，绑定过滤条件/项目范围/排序。修改筛选必须重置 cursor；绑定不符返回 400。

资源列表默认 `(created_at DESC, id DESC)`；消息/事件读取使用明确 seq 顺序。搜索不默认计算全量 total；确需统计时使用相应汇总字段/接口。

## 2. ActorContext、操作通道与权限

### 2.1 服务端上下文

```text
ActorContext
  actor_id: ID
  tenant_id: ID | null
  project_ids: ID[]
  auth_method: deployment_default | session | bearer | service
  permissions: string[]
  policy_version: string

OperationContext（只能由服务端构造）
  actor: ActorContext
  interaction: human | agent_tool | worker | service
  request_id: ID
  conversation_id: ID | null
  agent_run_id: ID | null
  tool_call_id: ID | null
```

首版只实例化受控 `deployment_default` AuthProvider，不交付账号页面、登录、Token 或 RBAC 管理。不得接受客户端在 JSON 中传 actor/tenant/permissions/interaction。项目 ID 是业务选择，仍须校验。

Agent 可以继承用户的业务读取范围，但其工具通道不允许 `human confirmation`、系统配置、批准/发布/回滚；服务方法检查 `interaction` 与权限，不能只依赖模型可见工具列表。

首版默认上下文不能区分真实多人；所有人工确认仅可归属于同一部署操作者，不宣称双人授权。在不可信多人环境必须先实施升级认证。

### 2.2 权限命名

采用 `resource:action`，如 `prediction:create/read`、`dataset:read/import`、`knowledge:read/write`、`training:create/submit/cancel`、`model:register/evaluate/approve/deploy`、`artifact:read/write/delete`、`assistant:use`、`trace:read`、`trace:content:read`、`settings:write`、`ops:read/write`、`audit:read`。

权限不由数据库里客户端可改字段决定；首版来自受控服务端策略文件。每次资源读取、下载、队列执行和确认消费重新检查 scope。长期任务只能保存已校验的主体与授权引用/快照，不保存原始凭证。

## 3. 修订、幂等与统一 Confirmation

### 3.1 修订与配置哈希

可修改资源详情包含 `revision`、`config_hash`、`etag`；HTTP GET/PATCH 返回 ETag 头。PATCH 采用普通 JSON 的部分字段更新，不采用任意 JSON Patch 指针。

配置哈希由服务端按固定规范化算法计算：JSON 字段排序、UTF-8、紧凑序列化、拒绝非有限数；先单位规范化并解析不可变版本，再计算。哈希内容包含全部执行相关字段与 schema/version，排除 UI 折叠状态、标题等纯展示字段。算法版本存储为 `hash_schema_version`。

涉及可用模型别名（如 Champion）时，验证/确认必须将其解析到具体模型 ID、版本和哈希；提交不再重新悄悄换模型。标题编辑可以不改变执行 hash，但仍增加 resource revision，并使与旧 revision 绑定的确认失效。

### 3.2 幂等规则

I 操作以 `(actor_id, project_id, method, route_identity, key)` 为作用域，记录规范化请求哈希与结果资源引用，默认保留 24 小时。哈希包含执行相关路径参数、query参数及JSON正文，不忽略DELETE中的confirmation_id。同键同请求返回原资源与原状态码，附 `Idempotency-Replayed: true`；同键不同请求返回 409。

服务端先校验当前访问权，再识别有效重放；成功重放不因原 If-Match 现已过期而产生假冲突。并发处理中同键可返回 409 `OPERATION_IN_PROGRESS` 与 Retry-After，不能新建第二个任务。

业务唯一约束（client_message_id、确认消费、数据导入提交、tool execution ledger、模型发布指针）长期生效，不依赖 24 小时缓存。

### 3.3 ConfirmationPreview

`POST /confirmations/previews`，200，读/计算动作，不要求 I。

```json
{
  "resource_ref": {"type": "experiment", "id": "exp_example"},
  "action": "training.submit"
}
```

返回 `ConfirmationPreview`：

| 字段 | 类型/含义 |
|---|---|
| resource_ref / action | 对应真实资源与允许动作 |
| revision / config_hash / etag | 当前目标版本 |
| summary | 服务端产生的字段列表 `{label,value,unit?}[]`，不含模型编造摘要 |
| dependencies | `{type,id,version,sha256}[]`，绑定实际模型/数据/模板 |
| impact | `{row_count?,excluded_rows?,combinations?,scope?,budget?}` |
| can_confirm | boolean |
| blockers | `{code,message}[]` |
| policy_version | 当前策略版本 |

前端必须展示 summary、影响与预算再确认。预览不是审批，也不执行任务。

### 3.4 创建、读取与撤销

| 方法 | 路径 | 请求 → data | HTTP | 约束 |
|---|---|---|---:|---|
| POST | `/confirmations` | ConfirmationCreate → Confirmation | 201 | I、E（目标 ETag）、human 通道 |
| GET | `/confirmations/{id}` | — → Confirmation | 200 | 当前资源可访问 |
| POST | `/confirmations/{id}/revoke` | `{reason}` → Confirmation | 200 | I；仅未消费 |

```json
{
  "resource_ref": {"type": "experiment", "id": "exp_example"},
  "action": "training.submit",
  "revision": 3,
  "config_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "policy_version": "policy-v1"
}
```

Confirmation 返回：`id, project_id, actor_id, resource_ref, action, revision, config_hash, policy_version, summary_snapshot, status, created_at, expires_at, consumed_at|null, consumed_result_ref|null`。

状态：`CONFIRMED / CONSUMED / EXPIRED / INVALIDATED / REVOKED`。TTL 默认 30 分钟。没有客户端自由选择 `approved=true` 或传执行命令的入口。

### 3.5 消费

需要确认的提交体统一为：

```json
{"confirmation_id": "confirm_example"}
```

服务端再次比较主体/项目、动作、资源、revision/hash、依赖、预算和策略；在同一事务中消费确认并创建目标 Job/结果和 Outbox。配置变化返回 409，过期返回 409 `CONFIRMATION_EXPIRED`。

同一 Confirmation 已成功消费且请求语义完全相同，返回已绑定结果，不再产生副作用；不同动作/配置使用同一 ID 一律拒绝。

首版需确认动作白名单：`batch.submit`、`batch.retry_failed`、`data_import.commit`、`training.submit`、`job.retry`（高成本任务）、`model_registration.register`、`model_deployment.publish`、`resource.archive`、`conversation.delete`、`artifact.delete`。模型审批决策另受治理权限校验。

## 4. 应用启动与能力发现

| 方法 | 路径 | data | HTTP |
|---|---|---|---:|
| GET | `/system/capabilities` | Capabilities | 200 |
| GET | `/system/version` | VersionInfo | 200 |
| GET | `/system/access-context` | AccessContextView | 200 |
| GET | `/projects` | Project[]，列表 | 200 |
| GET | `/health/live` | `{status:"UP"}` | 200 |
| GET | `/health/ready` | Readiness | 200 / 503 |
| GET | `/system/dependencies` | DependencyHealth[] | 200 |

`AccessContextView` 是已核验上下文的非敏感只读展示，不是请求认证材料；含 `actor_id, auth_mode, accessible_projects, allowed_actions, is_multi_user_authenticated=false`。`Project` 含 `id,name,status`。

```json
{
  "data": {
    "build_id": "universal-v1",
    "api_version": "1.0",
    "modules": ["prediction", "records", "datasets", "knowledge", "training", "model_center", "utilities", "assistant", "settings"],
    "chromatography_types": ["tlc", "cc", "hplc", "sfc", "gc"],
    "assistant_modes": ["knowledge", "business"],
    "export_formats": ["json", "csv", "xlsx"],
    "auth_mode": "deployment_default",
    "data_origin": "production",
    "capabilities": [
      {"id": "prediction.tlc.single", "status": "READY", "schema_version": "1.0", "reason": null},
      {"id": "assistant.business", "status": "UNAVAILABLE", "schema_version": "1.0", "reason": "PROVIDER_NOT_CONFIGURED"}
    ]
  },
  "warnings": [],
  "request_id": "req_example"
}
```

能力只列已构建项目；未构建的 coding 不以 `coding:false`、`disabled` 或占位 schema 出现。主导航来自编译期注册清单，服务端用于核对 build_id/schema 和健康，不下载或激活未构建代码。

Ready 区分 `core_ready` 与每项业务能力，`core_ready=false` 才整体 503；某可选模型缺失可保持 API 就绪，但对应能力与预测请求必须不可用。缺少 Worker 时不得伪造队列正常。

## 5. 通用 Job、事件与恢复

### 5.1 Job DTO

```text
Job
  id, project_id, owner_id: ID
  kind: 已构建 handler 类型
  resource_ref: ResourceRef
  status: JobStatus
  attempt: int >= 1
  progress: {completed:int, total:int|null, unit:string, stage:string|null}
  cancelable: boolean
  retryable: boolean
  resume_mode: none | restart | checkpoint
  created_at, updated_at: timestamp
  started_at, finished_at, deadline_at: timestamp | null
  result_ref: ResourceRef | null
  error: {code,message,retryable} | null
  revision: Revision
  etag: string
```

JobStatus：`QUEUED / RUNNING / CANCEL_REQUESTED / SUCCEEDED / PARTIAL / FAILED / CANCELED / TIMED_OUT / INTERRUPTED`。`PARTIAL` 只用于 batch 等注册允许类型，训练/模型发布不能使用。

合法主要转换：QUEUED→RUNNING/CANCELED；RUNNING→终态/CANCEL_REQUESTED；CANCEL_REQUESTED→CANCELED 或实际已完成的终态；租约失效经协调后可 INTERRUPTED。重试不覆写旧 Attempt，Job attempt 增加并进入 QUEUED，业务投影随之更新。已成功任务不可“重试”；重新计算创建新业务请求。

### 5.2 路由

| 方法 | 路径 | 请求 → data | HTTP | 约束 |
|---|---|---|---:|---|
| GET | `/jobs` | project_id、kind、status、cursor → Job[] | 200 | 仅当前范围 |
| GET | `/jobs/{id}` | — → Job | 200 | 同上 |
| POST | `/jobs/{id}/cancel` | `{reason?}` → Job | 202 | I、E，校验业务权限 |
| POST | `/jobs/{id}/retry` | `{confirmation_id?}` → Job | 202 | I、E；handler 决定需确认 |
| GET | `/jobs/{id}/attempts` | cursor → JobAttempt[] | 200 | 保留旧尝试 |
| GET | `/jobs/{id}/events` | after_seq、limit → EventPage | 200 | 升序 |
| GET | `/jobs/{id}/events/stream` | after_seq → SSE | 200 | 规则见 §17 |
| GET | `/jobs/{id}/logs` | cursor、limit → LogEntry[] | 200 | 脱敏且检查内容权限 |

`JobAttempt`：`id,job_id,attempt,status,resume_mode,checkpoint_ref|null,started_at,finished_at,error,result_ref`。`LogEntry`：`seq,timestamp,level,message,stage,truncated`，不返回任意服务器路径。

提交类接口返回相应业务对象的 Accepted DTO，至少有 `id,status,job_id,resource_ref`，并设置 `Location` 为业务详情地址。SSE 与轮询都只是查看，不推动执行。

### 5.3 队列实施要求

MySQL 事务写 Job + Outbox；队列只传 `job_id, attempt, dispatch_id`。Dramatiq actor 根据 DB 固定 handler 注册执行；不从消息传 Python import path、Shell 或原始认证信息。

所有业务Dramatiq actor显式设置 `max_retries=0`，避免默认自动重跑带副作用任务。业务失败由handler落库，受控retry/恢复协调器创建新Attempt；这不消除Worker崩溃后的消息重投，幂等账本仍必需。Outbox传输失败只重发同一dispatch/job引用并退避，不创建新业务任务。

Worker 使用租约/fencing token；每 15 秒心跳，默认 60 秒租约。任务提交/候选登记/数据提交/发布有唯一键及执行账本。消息重投可能发生，不能将 Dramatiq 的消息 ack 当成业务 exactly-once。

固定训练/解析 handler 使用受控子进程和硬截止监督；取消不只设置 Redis 标记。无 checkpoint 能力时只能受控 restart，不声称原地恢复 Python 栈。

## 6. 化学结构公共接口

### 6.1 规范化

`POST /structures/normalize`，201，I；请求：

```json
{
  "project_id": "project-default",
  "input_type": "smiles",
  "value": "C[C@H](O)c1ccc(cc1)C#N",
  "preserve_stereochemistry": true
}
```

首版 `input_type` 只接受 `smiles`；`preserve_stereochemistry` 只允许 true。`value` 非空且最大 20,000 字符。返回 `Structure`：

```text
id, project_id: ID
schema_version: string
original_input: {input_type,value,source_artifact_id|null}
canonical_smiles: string              # 规范化且保留立体信息
is_valid: boolean                    # 成功创建为 true；无效输入返回 422，不建假结构
has_stereochemistry: boolean
stereochemistry_status: specified | unspecified | not_applicable
stereochemistry_warnings: Warning[]
snapshot_hash: Hash
created_at: timestamp
```

### 6.2 结构文件与配对

| 方法 | 路径 | 请求 → data | HTTP | 约束 |
|---|---|---|---:|---|
| POST | `/structures/parse-file` | StructureFileParse → Accepted | 202 | I；artifact AVAILABLE |
| GET | `/structure-parses/{id}` | — → StructureParse | 200 | 结果分页引用 |
| GET | `/structure-parses/{id}/structures` | cursor → Structure[] | 200 | project 过滤 |
| GET | `/structures/{id}` | — → Structure | 200 | 项目可访问 |
| GET | `/structures/{id}/render` | format=svg\|png → binary | 200 | 不回传路径 |
| POST | `/structures/validate-pair` | PairRequest → PairValidation | 200 | 不执行训练/预测 |

`StructureFileParse = {project_id,artifact_id,format:cdx|cdxml|mol|sdf}`；`StructureParse = {id,job_id,status,total_count,valid_count,invalid_count,error_report_artifact_id|null}`。

`PairRequest = {project_id,structure_ids:[ID,ID],purpose:enantiomer_pair}`；返回 `is_pair:boolean, reasons:Warning[], structure_ids, validation_version`。不支持判定的情况明确返回 `is_pair=false` 和原因，不猜测。

### 6.3 可选手性 LC 对映体预览

仅构建 chiral_lc 时注册：

| 方法 | 路径 | 请求 → data | HTTP |
|---|---|---|---:|
| POST | `/structures/enantiomer-proposals` | `{project_id,structure_id,adapter_id}` → EnantiomerProposal | 201 |
| POST | `/structures/enantiomer-proposals/{id}/confirm` | `{revision}` → EnantiomerProposal | 200 |

两者 I，confirm 另需 E 且 human 通道。Proposal 含 `id,source_structure_id,proposed_structure_ids,method,revision,status:DRAFT|CONFIRMED,warnings,expires_at`。

仅对适配器声明且经测试的立体结构提供生成；复杂或不明确结构返回 422 `STEREOCHEMISTRY_UNSUPPORTED`。确认的是化学配对，不是训练预算；预测引用已确认 proposal_id，不能改变结果结构。模型调用前仍校验 proposal 和源结构匹配。

## 7. 文件与制品

### 7.1 唯一首版上传流程

不提供另一套 multipart 简化接口。流程如下：

| 方法 | 路径 | 请求 → data | HTTP | 约束 |
|---|---|---|---:|---|
| POST | `/artifacts/uploads` | UploadCreate → Upload | 201 | I |
| PUT | `/artifacts/uploads/{id}/content` | 二进制 → UploadReceipt | 200 | 受控上传，不接路径/URL |
| POST | `/artifacts/uploads/{id}/complete` | `{}` → ArtifactAccepted | 202 | I |
| GET | `/artifacts/uploads/{id}` | — → Upload | 200 | 恢复查询 |
| GET | `/artifacts/{id}` | — → Artifact | 200 | 状态/元数据 |
| GET | `/artifacts/{id}/download` | — → binary | 200 | 每次授权，敏感 kind 另加权限 |
| POST | `/artifacts/{id}/archive` | `{confirmation_id}` → Artifact | 200 | I |
| DELETE | `/artifacts/{id}` | query confirmation_id → ArtifactDeleteAccepted | 202 | I，引用/保留策略允许 |

```json
{
  "project_id": "project-default",
  "kind": "batch_input",
  "file_name": "sfc_batch.xlsx",
  "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "size_bytes": 18342,
  "expected_sha256": null
}
```

`UploadCreate.kind` 对客户端仅允许 `structure_input | batch_input | dataset_package | knowledge_document | model_package | assistant_attachment`；不能上传自称 `context_snapshot` 或 `algorithm_source`。`size_bytes > 0`；expected_sha256 可空。

`Upload = {id,artifact_id,status:CREATED|RECEIVED|COMPLETED|EXPIRED|FAILED,content_url,allowed_media_types,max_size_bytes,expires_at,received_bytes}`；content_url 为同源受控相对路径。

首版整文件上传，不实现分块/断点续传。内容 PUT 重试仅允许相同字节摘要，不同内容返回409；完成后不可覆盖。用户中断可重新创建 upload，旧临时文件按TTL清理。

### 7.2 制品对象与状态

```text
Artifact
  id, project_id, owner_id: ID
  kind, file_name, media_type: string
  size_bytes: integer
  sha256: Hash | null
  status: UPLOADING | VALIDATING | AVAILABLE | QUARANTINED | FAILED | ARCHIVED | DELETING | DELETED
  scan_status: PENDING | PASSED | REJECTED | NOT_CONFIGURED | FAILED
  validation_report_ref: ResourceRef | null
  source_ref: ResourceRef | null
  retention_until: timestamp | null
  created_at, updated_at: timestamp
  download_allowed: boolean
  warnings: Warning[]
```

scan 未配置不等于通过；输入制品（包括 dataset_package 与 model_package）要求实际扫描通过后才能 AVAILABLE，不能用服务器生成标识绕过。仅服务器固定生成器 `data_import_validation_report_v1` 产生的验证报告允许 `AVAILABLE + NOT_CONFIGURED`：必须绑定真实且当前可读的 import/validation，报告的 revision、config_hash 和源制品 SHA-256 必须与其所属权威 validation 及输入绑定一致（不要求属于 import 的最新 revision），由同一受控策略核验项目、`data:read` 与 `artifact:read`、报告 schema、内容 SHA-256 和大小后，才可返回 `download_allowed=true`。kind、MIME 或客户端可写的 generation 字符串本身均不足以授权；NOT_CONFIGURED 仍表示未配置扫描，不得展示为扫描通过。数据快照暂限内部受控读取，不继承这一浏览器下载例外。artifact 通用校验与知识切片/数据导入/模型加载是不同任务，不能用一个 parse_status 代替全部业务状态。

下载设置安全 Content-Disposition、nosniff，HTML/SVG 附件不作为同源可执行页面展示。系统生成结构 SVG 采用安全渲染白名单。敏感 Trace 制品要求 `trace:content:read`，不能仅 `artifact:read` 即下载。上述服务器验证报告的 `download_allowed` 与实际下载共用源绑定及内容授权策略，读取时再次核验，不把先前元数据响应当作永久授权；报告 JSON 版本为 `data-import-validation-report-1`，最多 2 MiB、展示前 500 条问题，截断标记不改变全量错误计数或使无效导入可提交。原字段预览截断时保留其完整解码值按 UTF-8 编码的 SHA-256，原文件字节 SHA-256 独立记录；不把预览视为完整原值。

模型包允许格式由 Loader manifest 公布。归档保留历史引用；永久删除有引用阻断与审计。删除通过异步清理任务先记录DELETING，返回202与job_id；成功后DELETED。受权元数据查询可返回最小墓碑状态，内容下载返回410。仅实际清理成功才标记DELETED；不宣称硬件级物理擦除。

## 8. 色谱能力与预测

### 8.1 能力路由

| 方法 | 路径 | data |
|---|---|---|
| GET | `/prediction/types` | ChromatographyTypeSummary[] |
| GET | `/prediction/types/{type}` | TypeCapability |
| GET | `/prediction/types/{type}/tools/{tool}/schema` | ToolSchema |
| GET | `/prediction/types/{type}/models` | 可访问 ModelOption[] |
| GET | `/sfc/conditions/options` | SfcConditionsOptions；query 仅允许 `scope_id` 或完整 `model_id+model_version` 二选一，可附 `project_id` |
| POST | `/sfc/candidate-scopes` | `{project_id?,model_selection:ExactModelSelection,axes,max_candidates?}` → SfcCandidateScopeView；I |
| POST | `/sfc/candidate-scopes/preview` | 同 SfcCandidateScopeCreate → SfcCandidateScopePreview；无幂等键，不创建范围或任务、不推理 |

Type 初始为 `tlc,cc,hplc,sfc,gc`，chiral_lc 仅构建纳入时存在。Tool 如 `single,batch,condition_compare,reverse_solvent,full_column,single_column`，以真实注册为准。

`ToolSchema` 必含：`schema_version,input_schema,output_schema,structure_count,model_constraints,units,supported_visualizations,limits,template_version|null,health`。`limits` 含实际行数/候选数/总计算量上限。

`ModelOption` 包含 `id,version,task_type,scope,artifact_sha256,input_schema_version,output_schema_version,health`，不含权重路径；SFC 另含判别联合 `sfc_domain`。SFC 多个模型可以共享同一个真实业务 `scope`，模型必须用 `id+version` 精确区分。只有已验证句柄完全匹配的模型可为 READY；其他模型各自保持 UNAVAILABLE，不能借用该句柄。未提供 model contract 时 health=UNAVAILABLE，预测返回503，不临时猜参数。

SFC `sfc_domain` 按 `model_scope` 判别：`single_column` 必须带 `group_id,group_label,stationary_phase,mode,allowed_columns`；`all_column` 仅标明其域类型。阈值仍由 `threshold` 表达，业务 scope 仍由 `scope` 表达，两者不用于编码模型分组。

`GET /sfc/conditions/options` 必须且只能使用以下一种选择方式：`scope_id`（服务器校验当前项目及该冻结范围所绑定的模型版本），或完整的 `model_id+model_version`（只读返回该模型域内真实观测到的选项，不创建范围）。缺少/重复选择器或不完整模型版本返回 422；无权项目或不存在的冻结范围按项目/资源错误返回。可返回轴值不代表所有笛卡尔组合都有效，创建候选范围时服务端仍须验证组合、模型域和上限。

`POST /sfc/candidate-scopes` 必须提交 `model_selection: {mode:"exact",model_id,model_version}`。服务端按精确模型校验项目访问、模型健康、工具域、模型版本和已验证句柄，再创建不可变范围；客户端不能提交业务 scope 或服务器路径。SFC 新预测与候选范围创建不接受 Champion。

`POST /sfc/candidate-scopes/preview` 复用创建请求及 `prediction:create`、项目、READY 模型、版本、工具和模型域校验，返回 `{project_id,model_id,model_version,matched_count,max_candidates,expansion_mode:"observed_distinct_rows",status:"READY"|"EMPTY"|"OVER_LIMIT"}`。`matched_count` 是全部合法匹配行数，不截断；`max_candidates` 是本次实际生效预算。状态仅表达数量：零行为 EMPTY，超过预算为 OVER_LIMIT，其余为 READY；权限、字段、模型域或依赖失败仍返回正常 HTTP 错误，不伪装 EMPTY。预检不创建 scope、Job 或确认，也不运行模型；创建时必须重新校验，不能依赖先前预检。

当前候选机制只筛选实际观测的完整条件元组：同轴多值为 OR，轴间为 AND；未选择的轴不参与筛选。它不生成笛卡尔组合。SfcCandidateScopeView 的历史 `expansion:"server_cartesian_or_historical_distinct_rows"` 仅保留兼容命名，当前实现对应其中 historical distinct rows，不能据此宣称支持 Cartesian。创建时空匹配返回 422 `SFC_CANDIDATE_SCOPE_EMPTY`，超过本次预算返回 422 `SFC_CANDIDATE_SCOPE_LIMIT_EXCEEDED`；两者 `error.details` 均含 `matched_count,max_candidates,expansion_mode`。请求预算上限仍为 100,000，不接受通过预检绕过限制。

候选范围 ID 由项目、精确模型版本及规范化轴值确定；同一绑定与轴值重复创建会复用既有不可变范围及首次保存的 `max_candidates` 预算。每次创建仍先按本次请求预算验证候选数；响应中的 `candidate_count` 是该冻结范围的实际候选数，`max_candidates` 是首次保存的预算，故重复请求时它可能不同于本次提交值。客户端应确认实际候选数不超过本次请求预算，不能把较大的历史预算作为本次请求的授权。`conditions` 最多返回 500 条预览，`truncated` 仅表示预览被截断，不代表候选范围被删减；实际候选集合以服务端冻结范围为准。

### 8.2 创建预测

`POST /predictions`，202，I，权限 prediction:create。

```json
{
  "project_id": "project-default",
  "chromatography_type": "tlc",
  "tool": "single",
  "schema_version": "1.0",
  "model_selection": {"mode": "champion", "scope_id": "scope_tlc_default"},
  "structure_refs": ["str_example"],
  "input": {
    "solvent_components": [
      {"solvent_id": "hexane", "fraction": 0.8},
      {"solvent_id": "ethyl_acetate", "fraction": 0.2}
    ]
  }
}
```

`model_selection` 是判别联合：`{mode:"champion",scope_id}` 或 `{mode:"exact",model_id,model_version}`；能力要求精确预处理版本的模型，必须在 exact 分支携带 `preprocessing_profile_id` 与 `preprocessing_profile_sha256`。HPLC 1.1、GC 1.1 和 CC 当前基线都要求能力返回的 exact profile pair。创建时解析并持久化实际版本，未来查询不会受 Champion 变化影响。

HPLC single schema 1.1 的请求 DTO 将 `model_selection` 限定为 exact 分支，OpenAPI 不接受 Champion 分支；`structure_refs` 在 schema 中固定为恰好两项，且服务继续校验互异并逐槽绑定 raw 输入。

公共字段均必填。`input` 必须匹配本构建选中类型/tool/schema 的具体 Pydantic 类型；OpenAPI 对各类型生成 oneOf，不能输出一个不受约束的 `dict[str,Any]`。

除 HPLC single schema 1.1 的明确版本化例外外，structure_refs 与 input 不重复接受 smiles/enantiomer_smiles 等原始结构。HPLC 1.1 的 `input.model_input_smiles` 必须是恰好两条有序、非空字符串，每条 1–20,000 字符；拒绝纯空白，接受后原样保存且不 trim/重写。`structure_refs` 同样恰好两项且互异，顺序与 raw pair 一一对应。canonical/isomeric identity、完整 stereo 与 CIP 只负责同槽身份和对映体校验，模型接收 raw pair。其他类型/版本收到该字段仍因 strict DTO 被拒绝。来源 web/assistant、conversation_id、actor 等由服务端 OperationContext 保存，不由客户端自报。

### 8.3 类型输入契约（产品基线，不替代真实模型范围）

下面定义新公共字段形态；字段允许值/精确数量/可选扩展必须由真实 Adapter Manifest 冻结。构建中缺少实际 schema 的 adapter 不能接受真实预测，只能报告不可用。

#### TLC single

```text
TlcInput
  solvent_components: [{solvent_id:string, fraction:number}]   # 2–5，ID 不重复
```

fraction 在 [0,1]，和在 1±1e-6；不自动归一化，错误要求用户修正。是否允许某二元体系由模型白名单决定。`petroleum_ether` 与 `hexane` 是不同 ID，不转换。展开距离等仅在模型输出/可视化契约明确需要时作为版本化字段增加。

#### CC single

```json
{
  "eluent_system_id": "pe_ea_40_1",
  "loading_solvent_id": "petroleum_ether",
  "sample_mass_mg": 99.0,
  "loading_solvent_volume_ul": 200.0,
  "flow_rate_ml_min": 10.0,
  "column_spec_id": "silica_cs_4g"
}
```

全部必填，数值>0，ID 必须在模型范围内。基线配对模型 structure_refs=2；其他输入数量只有新版本 adapter 明确声明后允许。CC 派生时间 = 体积(mL)×60/流速(mL/min)，派生字段注明 derived。

#### HPLC single (schema 1.1)

```json
{"model_input_smiles":["N[C@@H](C)C(O)=O","N[C@H](C)C(O)=O"],"isopropanol_fraction":0.1,"flow_rate_ml_min":1.0,"column_ids":["ADH","IA"]}
```

`structure_refs=2` 且 `input.model_input_smiles=2`；两组严格保序并逐槽绑定。`model_selection.mode` 必须为 `exact`，客户端携带能力声明的 `model_id`、`model_version`、`preprocessing_profile_id` 和 `preprocessing_profile_sha256`；模型制品哈希由服务端解析、校验并绑定到模型快照，不作为客户端 `model_selection` 字段提交。raw 与同槽项目结构的 isomeric canonical identity、完整 stereo/CIP 和对映体关系经服务端校验；canonical 不得替代模型输入。fraction 在 [0,1]、流速>0、候选柱不重复且在能力中声明。模型输入对 SMILES 表示可能敏感，正式结果必须包含表示敏感性、训练端表示/cache provenance 未确认、实验确认和无独立泛化证据 warnings。缺少 profile pair、raw 或 stale pair 均拒绝。

HPLC schema 1.0 只作为已保存记录的 legacy read DTO，保留历史 GET、列表、详情和导出，不回填 raw/profile，不重放、不 retry、不执行；需要重做时用户重新提供本次两条原始 SMILES 创建 schema 1.1。HPLC 现有 batch 模板没有本次 raw pair，按 template health 返回 UNAVAILABLE；不由 batch 模板读取 canonical 来代跑。

#### chiral_lc single（可选独立 adapter）

```json
{"enantiomer_proposal_id":"ep_example","conditions":{"method_profile_id":"method_profile_example"}}
```

structure_refs=1，指向含明确立体信息的原始对象；proposal 必须与其匹配且已确认。`conditions` 是该 adapter 的具体版本化模型，不是任意 map；未提供 method profile 规范时阻断相应能力。不要转而使用 HPLC 表单/字段绕过。

#### SFC single_column / full_column

```json
{
  "project_id":"project-default",
  "chromatography_type":"sfc",
  "tool":"single_column",
  "schema_version":"1.1",
  "model_selection":{"mode":"exact","model_id":"sc-rs12-amylose-dmpc-coated","model_version":"1"},
  "structure_refs":["str_enantiomer_a","str_enantiomer_b"],
  "input":{
  "modifier_total_fraction":0.2,
  "modifier_components":[{"structure_ref":"str_methanol","fraction":1.0}],
  "candidate_scope_id":"sfc_scope_example",
  "column_id":"AD",
  "top_k":5
  }
}
```

single_column 与 full_column 新请求使用 schema 1.1 和 exact `model_id+model_version`；结构引用必须是两条不同且经服务端校验的结构。modifier_components fraction 和=1，total_fraction 在模型允许区间，默认不自动归一化。单柱工具还要求 `column_id`，且其值须属于精确模型声明的 `allowed_columns`；候选范围由服务端创建并绑定项目、模型及版本，不接受任意 SQL/库路径。候选总数先计算后执行。full_column 仅在真实模型能力明确支持时开放其候选范围。

SFC schema 1.0 只作为历史记录读取格式。既有已接受任务的 Worker 只可从原请求、输入和模型快照验证后，临时转换为绑定快照模型的 exact 执行请求；不得更改持久化快照或按当前默认/Champion 重解析。若当前已验证模型句柄与原快照不匹配则 fail-closed。旧 1.0 请求不能新建或通过重放创建新请求。

#### GC single（schema 1.1）

```json
{
  "temperature_program":{
    "mode":"linear",
    "initial_temperature_c":40,
    "final_temperature_c":200,
    "heating_rate_c_per_min":20,
    "initial_hold_min":2
  }
}
```

另一联合为 `{mode:"series",temperature_series_c:number[]}`；不得混传两种模式字段。当前参考模型 series 长度30，但公共校验应读取该模型 schema 中的 minItems/maxItems=30，而不是假设未来全部 GC 模型如此。linear 要求升温率>0、终温>=初温、保持>=0，数值在能力范围内；当前参考模型的保持时间声明 `multipleOf=1` 分钟，客户端不得静默取整。single structure_refs=1；多分子走批量或独立声明的工具。

GC schema 1.1 的 `model_selection` 只接受 `mode="exact"`，必须提交能力返回的 `model_id`、`model_version`、`preprocessing_profile_id` 与 `preprocessing_profile_sha256`；制品哈希由服务端校验并绑定模型快照，不作为客户端字段提交。服务端校验结构所属项目，保序冻结规范化立体结构；Worker 使用冻结快照，不重新读取可变结构。请求、输入快照和模型快照的 schema/profile 不一致时拒绝执行。旧 GC schema 1.0 仅用于既有记录查询及导出，不允许创建、重放、重试或自动补造 profile。静态 profile 与 schema 不构成 READY 证明，能力健康状态仍由真实验证结果决定。

### 8.4 Prediction 与结果

```text
Prediction
  id, project_id, prediction_record_id, job_id: ID
  chromatography_type, tool, schema_version: string
  status: JobStatus
  model: {id,version,artifact_sha256,input_schema_version,output_schema_version}
  input_snapshot: {structure_refs,normalized_structures,input}
  result: TypedPredictionResult | null
  warnings: Warning[]
  error: PublicErrorSummary | null
  source: web | assistant | service
  conversation_id, agent_run_id: ID | null
  created_at, completed_at: timestamp | null
  data_origin: production | fixture
```

HPLC 1.1 的 snapshot 原样保存 `input.model_input_smiles`，模型快照保存 exact model/profile pair。现有完整 request payload hash 已覆盖 raw、顺序和 profile pair，不另建 HPLC digest。Worker/replay 对照冻结 request 与 input snapshot 的 raw/ref/顺序，并核对 model selection 与 model snapshot 的 exact pair；adapter 再以冻结 canonical identity 校验 raw。完整同 payload 幂等 replay 返回原资源与原快照；raw、任何顺序或 profile pair 改变都冲突。Replay 仅读取原冻结快照，不用当前能力重解析并替换 profile。历史 HPLC 1.0 缺 raw/profile 时，replay source、worker retry 和 execution 均 fail-closed。

结果统一外壳为：

```text
TypedPredictionResult
  schema_version: string
  task_type: string
  metrics: [{key,label,value:number|string|null,unit:string|null,origin:model|derived}]
  rows: 该模型输出 schema 的强类型行数组
  visualizations: [{kind,schema_version,data,origin:predicted|simulated|measured}]
  applicability: {supported_scope,warnings}
```

TLC 行至少包含实际 Rf；CC 包含模型实际体积/派生时间；HPLC/SFC 包含实际候选/分离结果；GC 包含模型实际提供的分位数/时间。不得要求每个模型捏造同一套 confidence/interval。详细行类型由真实输出 schema 生成并测试，`data` 不允许任意 HTML。

### 8.5 查询、取消、重算

| 方法 | 路径 | 请求 → data | HTTP | 约束 |
|---|---|---|---:|---|
| GET | `/predictions/{id}` | — → Prediction | 200 | prediction:read |
| POST | `/predictions/{id}/cancel` | `{reason?}` → Prediction | 202 | I、E；映射同一 Job 取消服务 |
| POST | `/predictions/{id}/recalculate` | `{model_selection?}` → PredictionAccepted | 202 | I；新 prediction、新记录，不改旧值 |
| POST | `/predictions/{id}/exports` | ExportCreateFields → ExportAccepted | 202 | I |

技术失败的重试使用 `/jobs/{id}/retry` 保留同一 prediction 的新 Attempt；成功结果的重新计算使用 recalculate，创建新资源并记 `source_prediction_id`。

## 9. 批量预测

### 9.1 路由

| 方法 | 路径 | 请求 → data | HTTP | 约束 |
|---|---|---|---:|---|
| GET | `/prediction/types/{type}/batch-template` | tool、model_id、format、template_version? → binary | 200 | 返回实际模板版本/哈希响应头；未选 batch 类型不提供 |
| POST | `/batch-predictions/drafts` | BatchDraftCreate → BatchDraft | 201 | I |
| GET | `/batch-predictions/{id}` | — → BatchPrediction | 200 | 包含 revision/etag |
| PATCH | `/batch-predictions/{id}` | BatchDraftPatch → BatchDraft | 200 | I、E；仅未提交草稿 |
| POST | `/batch-predictions/{id}/validate` | `{}` → ValidationAccepted | 202 | I、E |
| GET | `/batch-predictions/{id}/validation` | — → BatchValidation | 200 | 指定当前 revision |
| GET | `/batch-predictions/{id}/validation/rows` | status、cursor → RowValidation[] | 200 | 分页错误/警告 |
| POST | `/batch-predictions/{id}/submit` | `{confirmation_id}` → BatchAccepted | 202 | I；batch.submit |
| GET | `/batch-predictions/{id}/rows` | status、cursor → BatchRowResult[] | 200 | 保留原行号 |
| POST | `/batch-predictions/{id}/cancel` | `{reason?}` → BatchPrediction | 202 | I、E |
| POST | `/batch-predictions/{id}/retry-plans` | `{row_ids:ID[]}` → BatchRetryPlan | 201 | I、E；当前 attempt 的失败行 |
| POST | `/batch-predictions/{id}/retry-failed` | BatchRetryFailed → BatchAccepted | 202 | I、E、确认 |
| POST | `/batch-predictions/{id}/exports` | BatchExportCreate → BatchExportAccepted | 202 | I、E |
| GET | `/batch-predictions/exports/{export_id}` | — → BatchExportView | 200 | 同项目权限 |
| GET | `/batch-predictions/exports/{export_id}/download` | — → binary | 200 | 仅成功且制品可用；附件下载 |

```json
{
  "project_id":"project-default",
  "name":"SFC 批量筛选",
  "chromatography_type":"sfc",
  "tool":"batch",
  "schema_version":"1.1",
  "model_selection":{"mode":"exact","model_id":"rs12-extra-trees-s80-seed42","model_version":"1"},
  "input_artifact_id":"art_batch_example",
  "template_version":"sfc-batch-2",
  "field_mapping":{"structure_ref_a":"compound_a","structure_ref_b":"compound_b","tool":"prediction_tool"},
  "common_input":{"candidate_scope_id":"sfc_conditions_frozen","top_k":1,"modifier_total_fraction":0.2,"modifier_components":[{"structure_ref":"str_modifier","fraction":1.0}]},
  "invalid_row_policy":"block",
  "resource_budget":{"max_runtime_seconds":3600,"max_evaluations":100000}
}
```

`field_mapping` 左边是模板固定目标字段 ID，右边是上传文件列名；不能映射成表名/SQL表达式。`common_input` 使用该类型批量 schema，不是跨色谱万能参数。

TLC 批量新操作仅使用 `tlc-batch-2` 的 `smiles,solvent_1,fraction_1,solvent_2,fraction_2` 五列；`tlc-batch-1` 仅保留历史读取及支持的导出，不再下载为可执行模板或受理新建、验证、提交、重试。执行需按 v2 新建草稿并重新校验确认，不补写旧快照。下载未指定 `template_version` 时返回该类型当前版本，显式指定时必须存在且可执行。v2 比例是归一化 fraction，两个不同溶剂的比例之和为 1，且必须满足当前模型的二元体系和有效范围，不能把 1/0 当作受支持二元体系。SMILES 在验证阶段通过核心结构服务规范化、保留立体信息并生成同项目结构引用；不让浏览器提供服务器路径或伪造结构 ID。

CC 使用 `cc-batch-2` 双不同项目内结构引用及专用条件；HPLC 使用 `hplc-batch-2` 有序 `smiles_a/smiles_b` 原文及专用条件，服务端生成 refs 后仍按原文执行；SFC 使用 `sfc-batch-2`，每行明确 `tool=single_column|full_column`，前者需要 column_id，后者禁止该字段；GC 保留 `gc-batch-1` 结构引用与独立温程，固定 schema 1.1。CC/HPLC/GC 均要求能力中的 exact model/profile id 与 SHA；SFC 一个 batch 只有一个 exact 模型，行工具不兼容则拒绝，不隐式换模型。旧 CC/HPLC/SFC v1 模板不可执行，历史只读，不补造缺失输入。

当构建清单实际包含 TLC batch 时，`/prediction/types/tlc` 的 tools 条目及 `/prediction/types/tlc/tools/batch/schema` 返回 `TlcBatchToolCapability`：必填 `id` 为 `batch`、必填 `chromatography_type` 为 `tlc`，另含 typed `limits:BatchResourceBudget`、`templates:BatchTemplateDefinition[]` 和严格 `model_constraints:TlcBatchModelConstraints`。响应与 `NonBatchToolCapability` 使用互斥 `oneOf`；后者 `id` 不得为 `batch`，仍保留通用 ID 长度上限。其他类型的 batch 不能被投射为 TLC 对象，未选模块/工具不注册或拒绝，已选但依赖未就绪真实返回不可用。

`BatchTemplateDefinition` 包含 `id,chromatography_type,tool,version,row_schema,fields,limits,health,tlc_options|null`，溶剂和候选体系取当前实际选项。`TlcBatchModelConstraints` 必含 `template_versions` 和 `compatible_models`；每个兼容项必含 `model_id,model_version,input_schema_version,template_versions,health`。模型三元组唯一、模板版本唯一且条目模板是父列表子集，未知字段拒绝。前端同时核对路由/对象 literal、精确模型三元组、实际模板版本和各层健康；不因缺字段、非 READY 或版本不匹配回退到其他模型。健康只从实际验证的模型/schema/模板兼容证据投影，不将静态 UNKNOWN 改成伪造 READY，也不宣称 Worker 已在线。其他类型的通用模型约束不增加这些 TLC 字段。

其余四类 batch 使用 `NonTlcBatchToolCapability`，必含 `id=batch`、对应 chromatography_type、limits、templates 与 `NonTlcBatchModelConstraints`；compatible_models 含精确 model/version/schema/profile 对、template_versions、实际 tools 和 health。它与 TLC typed batch、`NonBatchToolCapability(id!=batch)` 互斥。能力仅列当前可执行模板，历史模板不投射 READY。

每行在校验时按“行字段优先、common_input 补缺省”解析并写入不可变 `resolved_input_snapshot`；空白单元格表示缺省，显式 `null` 保留后交给行 schema 校验。身份字段（结构 ID/SMILES）必须来自各行，不能由 common_input 提供或覆盖。预览、确认、提交后的 Worker 与导出都绑定该快照及其聚合哈希，不在执行时重新解析上传文件。

`invalid_row_policy = block | exclude_invalid`；改变该值、映射、模型、input 或预算增加 revision，验证与确认失效。字段补全/文件修正通过新不可变 artifact 引用完成，不修改已上传内容。

### 9.2 校验与执行

`BatchDraft` 包含请求快照及 `id,revision,config_hash,etag,status:DRAFT|VALIDATING|INVALID|READY,validation_id|null`。`config_hash` 为必填的服务端当前草稿配置哈希，不能由客户端自造；确认目标的聚合哈希另外绑定 validation、输入和预算，不要求它与草稿哈希相等。

`BatchValidation`：`id,draft_revision,config_hash,status,template_version,model_snapshot,total_rows,valid_rows,invalid_rows,excluded_rows,total_evaluations,resource_estimate,resolved_input_hash,errors_report_artifact_id,warnings`。`model_snapshot` 固定模型版本、制品 SHA-256 与输入/输出 schema 版本；模板版本、目标行 schema 和资源预算也参与草稿与确认绑定。超出 `max_evaluations` 的草稿不能提交；运行时间受 `max_runtime_seconds` 限制。

validate 为异步；完成时仅在 draft revision/hash 仍匹配时应用结果，否则标记该 validation STALE，不把新草稿错误置 READY。

`RowValidation`：`row_id,source_row_number,status:VALID|INVALID|WARNING,normalized_input_hash,field_errors,warnings,resolved_input_snapshot|null`。TLC 预览采用 `template_version` 判别的 `TlcBatchResolvedInputV1|TlcBatchResolvedInputV2`：均含 `chromatography_type:tlc,template_version,project_id,draft_revision,validation_id,structure_ref,solvent_conditions`，v2 另含 `normalized_smiles`。仅当前有效 validation/revision 的合法行提供该预览；无效行及陈旧结果返回 null。实际结构 refs 在校验后生成并保存，不能只把 Excel 原字符串在执行时重新解释。

制品必须通过当前项目归属、`kind=batch_input`、`status=AVAILABLE`、`scan_status=PASSED` 校验。TLC 比例/体系校验先于结构持久化；验证同时检查结构规范化及预测创建权限。`exclude_invalid` 的无效行记录为验证阶段排除并 `SKIPPED`，不冒充模型执行 FAILED 或 PARTIAL。TLC 在验证时要求精确 READY 模型/模板绑定，提交和失败行重试在确认消费、创建新 Job/Attempt 前重新核对当前兼容绑定以及 validation/batch 两份冻结模型快照；漂移则拒绝，不自动替换模型。

`BatchRowResult.input_snapshot` 与同一 `validation_id` 下的 `resolved_input_snapshot` 一致；提交确认绑定 draft revision/ETag、validation、模型快照、解析输入聚合哈希、选中 row_ids 和预算。提交成功后设置 `submitted_at`，草稿冻结，修改必须创建新草稿。

非 TLC 行预览分别使用 `CcBatchResolvedInputV2`、`HplcBatchResolvedInputV2`、`GcBatchResolvedInputV1`、`SfcBatchResolvedInputV2`，含项目/revision/validation、对应严格单次 request、规范化结构及 evaluation_count；HPLC 原文位于 request.input.model_input_smiles，SFC 另含规范化 modifier 与可空的真实 scope_provenance。Worker 使用这些冻结输入，不重新解析文件、重取结构或转 Champion。SFC provenance 描述来源全量，single 过滤后的 evaluation_count 可小于来源候选数。

默认最多 10,000 行、100,000 业务 evaluations；TLC/GC 每有效行 1、CC 每行 2、HPLC 每行实际柱数、SFC 每行实际 scope 评分数，失败重试同义，不计算 ensemble 内部前向次数。全行无效或预算超限标 INVALID，所有未执行行状态与 counts 一致，不留下隐含 QUEUED 成功假象。每个实际执行逻辑行保存一条稳定 PredictionRecord，retry 更新同一 ID、revision/ETag 并保留原输入/模型与历史 Attempt；已成功行不重建。提交前 SKIPPED 与未执行取消不建记录，执行后取消写 CANCELED，result_ref 指 batch_prediction，归档状态保留。

`BatchPrediction` 的业务草稿状态和 JobStatus 分开字段：`draft_status`、`execution_status|null`；另有 `job_id,attempt,counts:{total,queued,running,succeeded,failed,skipped,canceled},progress`。

`BatchRowResult`：`row_id,source_row_number,input_snapshot,result,error,warnings,attempt,status`。部分取消允许已有成功结果保留，但整批状态 CANCELED，不能把未处理行标记成功。部分失败且所有行已处理可为 PARTIAL。

重试失败行前将选中 row_ids 写入重试计划：`POST /batch-predictions/{id}/retry-plans`，I/E，201，body `{row_ids:ID[]}`；返回可确认 `BatchRetryPlan {id,revision,config_hash,etag,row_ids,parent_batch_id,expected_attempt,estimated_evaluations,validation_id,model_snapshot_hash,resolved_input_hash}`。Confirmation 绑定该 plan、动作 `batch.retry_failed`；计划独立绑定失败 attempt、最新仍失败的行、模型和解析输入快照。retry-failed body 为 `{retry_plan_id,confirmation_id}`；只处理 plan 中仍失败的行，其他行结果不变。

`BatchExportCreate = {format:json|csv|xlsx,include:[input_snapshot|result|model_version|warnings,…]}`；`BatchExportView = {id,batch_id,job_id,format,include,status,source_revision,validation_id,source_attempt,source_snapshot_hash,artifact_id,error,created_at,completed_at}`。导出创建后生成独立 `BatchExport` 长任务和不可变来源快照，完成后返回 `artifact_id`；状态查询与下载使用上述独立 GET 路由。下载需通过制品元数据核验 `kind=batch_export`、项目归属、来源引用及 AVAILABLE 状态，并再次执行项目读取权限校验。

## 10. 预测记录、导出与资源归档

| 方法 | 路径 | 请求 → data | HTTP | 约束 |
|---|---|---|---:|---|
| GET | `/prediction-records` | 项目/类型/模型/状态/来源/时间/cursor → Record[] | 200 | 当前可见范围 |
| GET | `/prediction-records/{id}` | — → PredictionRecord | 200 | 输入/结果/事件/制品 |
| POST | `/prediction-records/{id}/replay-draft` | `{}` → PredictionDraftView | 201 | I；不执行预测 |
| POST | `/prediction-records/{id}/archive` | `{confirmation_id}` → Record | 200 | I |
| POST | `/exports` | ExportCreate → ExportAccepted | 202 | I |
| GET | `/exports/{id}` | — → Export | 200 | 持续校验源资源访问权 |

`PredictionRecord = {id,project_id,prediction_id|null,batch_id|null,source,actor_id,chromatography_type,model_snapshot,input_snapshot,result_ref,warnings,status,archived_at,created_at,artifact_refs,revision,etag}`。

`PredictionDraftView` 是已保存资源，仅供带回输入；包含 `id,source_record_id,project_id,chromatography_type,tool,structure_refs,input,schema_version,model_selection`。前端使用其值创建新预测，不将原记录改成草稿。

```json
{
  "project_id":"project-default",
  "resource_refs":[{"type":"prediction_record","id":"pr_example"}],
  "format":"xlsx",
  "include":["input_snapshot","result","model_version","warnings"]
}
```

`ExportCreateFields` 等于 ExportCreate 去掉由路径决定的 project_id/resource_refs。include 是该资源可导出字段枚举；禁止任意 SQL、模板路径、HTML 或远程 URL。

`Export = {id,job_id,status,format,format_version,source_snapshots,artifact_id|null,error,created_at,completed_at}`。下载使用 Artifact API。被撤销访问权后不能通过既有 export/artifact 绕过源资源权限。

归档资源在 Confirmation 中统一 action=`resource.archive`，绑定资源 revision。归档不立即删除训练/模型/证据引用；普通 UI 不提供跨资源级联永久删除。

## 11. 色谱数据与不可变版本

### 11.1 数据域、分子与实验记录

| 方法 | 路径 | data / 请求 | HTTP |
|---|---|---|---:|
| GET | `/data-domains` | 可用 DataDomain[] | 200 |
| GET | `/data-domains/{id}/schema` | DataDomainSchema | 200 |
| GET | `/molecules` | project_id、q、chromatography_type、cursor → Molecule[] | 200 |
| POST | `/molecules` | MoleculeCreate → Molecule | 201，I |
| GET | `/molecules/{id}` | MoleculeDetail | 200 |
| GET | `/molecules/{id}/chromatography-records` | cursor → ExperimentRecord[] | 200 |
| GET | `/data-versions` | project_id、domain_id、status、cursor → DataVersion[] | 200 |
| GET | `/data-versions/{id}` | DataVersion | 200 |
| GET | `/feature-versions` | task_type、cursor → FeatureVersion[] | 200 |

`MoleculeCreate={project_id,structure_id,display_name?,external_reference?,source}`，source 为用户提供的科学来源描述，不作为认证来源。Molecule 含 `id,project_id,structure_id,canonical_smiles,display_name,chromatography_types,record_count,source,quality_status,created_at`。

统一分子身份可以有多个色谱类型关联，但不同项目数据不能只因 InChIKey 相同而自动合并。系统可内部复用无敏感基础结构缓存，但接口权限和业务记录不可跨项目泄露。

`DataDomainSchema` 必含 `id,version,chromatography_type,record_schema,unit_definitions,identity_fields,deduplication_policy,supported_import_modes`。record_schema 是可信领域规范，不允许用户上传一个 schema 就改变数据库结构。

`DataVersion`：`id,project_id,domain_id,version,sha256,status:READY|ARCHIVED,record_count,source_import_id,snapshot_artifact_id,schema_version,quality_report_ref,created_at,referenced_by_count`。

`FeatureVersion`：`id,version,task_types,implementation_version,schema,dependency_lock_hash,created_at`，实现仅来自已构建注册表。

### 11.2 导入流程

| 方法 | 路径 | 请求 → data | HTTP | 约束 |
|---|---|---|---:|---|
| POST | `/data-imports` | DataImportCreate → DataImport | 201 | I |
| GET | `/data-imports/{id}` | — → DataImport | 200 | 当前范围 |
| PATCH | `/data-imports/{id}` | 可变映射/模式字段 → DataImport | 200 | I、E，未提交 |
| POST | `/data-imports/{id}/validate` | `{}` → Accepted | 202 | I、E |
| GET | `/data-imports/{id}/validation` | — → DataImportValidation | 200 | 差异与错误摘要 |
| POST | `/data-imports/{id}/commit` | `{confirmation_id}` → Accepted | 202 | I，data_import.commit |

`DataImportCreate={project_id,domain_id,artifact_id,schema_version,import_mode:append|upsert,field_mapping,source_description}`；import_mode 还需在 domain 白名单中。替换整个生产库不作为通用 import_mode。

`DataImport={id,revision,config_hash,etag,status:DRAFT|VALIDATING|INVALID|READY|COMMITTING|COMMITTED|FAILED,job_id|null,validation_ref|null,new_data_version_id|null,...请求快照}`。

`DataImportValidation={id,revision,status,counts:{new,updated,unchanged,invalid,duplicate},report_artifact_id,warnings}`。

commit 先确认 diff/revision/目标域，事务性提交业务数据与版本元数据；大量数据可分阶段准备 staging，再通过原子可见指针发布。失败不暴露半导入版本；相同确认/请求不生成重复版本。版本快照不可覆盖。

## 12. 知识中心与引用

### 12.1 文档、索引与查询

| 方法 | 路径 | 请求 → data | HTTP | 约束 |
|---|---|---|---:|---|
| GET | `/knowledge/documents` | project_id、q、status、cursor → KnowledgeDocument[] | 200 | 权限过滤 |
| POST | `/knowledge/documents` | KnowledgeDocumentCreate → KnowledgeDocument | 201 | I |
| GET | `/knowledge/documents/{id}` | — → KnowledgeDocument | 200 | 来源/版本/状态 |
| POST | `/knowledge/documents/{id}/index` | `{}` → Accepted | 202 | I、E |
| GET | `/knowledge/documents/{id}/chunks` | cursor → KnowledgeChunk[] | 200 | 内容权限，分页 |
| POST | `/knowledge/documents/{id}/archive` | `{confirmation_id}` → KnowledgeDocument | 200 | I，立即退出检索 |
| POST | `/knowledge/search` | KnowledgeSearch → KnowledgeSearchResult | 200 | 查询，无 I |
| GET | `/knowledge/citations/{id}` | — → Citation | 200 | 再校验文档/项目权限 |

`KnowledgeDocumentCreate={project_id,artifact_id,title,source_type:internal|paper|standard|other,source_description,tags?:string[]}`。

`KnowledgeDocument={id,project_id,title,artifact_id,version,sha256,status:UPLOADED|INDEXING|READY|FAILED|NEEDS_OCR|ARCHIVED,parser_version,index_version,retrieval_strategy,chunk_count,job_id|null,error|null,revision,etag,created_at}`。

索引失败保留上一个已完成可见 index version，并注明最新尝试失败；无旧成功索引时不可检索。重复 index 请求有幂等性，生成的新切片和版本在完成后原子启用。

### 12.2 首版检索契约

```json
{
  "project_id":"project-default",
  "query":"SFC 改性剂对手性分离的影响",
  "document_ids":[],
  "top_k":8
}
```

query 为 2–2,000 字符；top_k=1–20，默认8。document_ids 空数组表示当前有权访问的资料，不表示全库所有项目。输入当普通文本处理，不允许用户传 SQL 或自由布尔表达式执行。

返回：

```json
{
  "data": {
    "search_id":"ks_example",
    "retrieval_strategy":"mysql_ngram_v1",
    "results":[
      {
        "citation_id":"cite_example",
        "document_id":"doc_example",
        "document_version":"1",
        "chunk_id":"chunk_example",
        "title":"示例 SFC 方法说明",
        "locator":{"kind":"page","page_start":12,"page_end":12},
        "snippet":"此处内容必须来自实际解析的资料，不由模型补写。",
        "score":1.25,
        "score_type":"keyword_relevance"
      }
    ],
    "warnings":[]
  },
  "warnings":[],
  "request_id":"req_example"
}
```

示例只说明字段，不证明某真实论文存在。引用保存不可变 document version、chunk hash 和定位；源文档被归档后历史回答仍可显示引用标题/归档标识，但不再参与新检索；内容访问仍需权限。被撤销权限时连引用内容都不能读取。

`Citation.locator` 判别联合：page `{page_start,page_end}`；paragraph `{heading|null,paragraph_start,paragraph_end}`；line `{line_start,line_end}`。不能给 DOCX 伪造 PDF 页码。

`KnowledgeChunk={id,document_id,document_version,ordinal,content,content_sha256,locator}`。检索和内容下载的授权判断不得依赖前端送来的 citation URL。

### 12.3 实现限制

首版 MySQL ngram 为本地关键词检索，使用绑定参数、项目范围和 READY/index version 条件，在 limit 前过滤。不能先取其他项目 top-k，再由前端隐藏。

知识模块不依赖 LangChain 或外部 Embedding API。`KnowledgeRetriever` 提供稳定协议；将来增加向量检索时保留 citation/result 契约并增加策略版本，另立数据发送/离线模型政策。

## 13. 模型训练：仅固定模板

### 13.1 模板与实验路由

| 方法 | 路径 | 请求 → data | HTTP | 约束 |
|---|---|---|---:|---|
| GET | `/training/templates` | task_type → TrainingTemplate[] | 200 | 仅已构建 |
| GET | `/training/templates/{id}` | version? → TrainingTemplate | 200 | 返回真实版本 |
| GET | `/training/experiments` | project_id、task_type、status、cursor → Experiment[] | 200 | 当前范围 |
| POST | `/training/experiments` | ExperimentCreate → Experiment | 201 | I |
| GET | `/training/experiments/{id}` | — → Experiment | 200 | 配置/Job/候选关联 |
| PATCH | `/training/experiments/{id}` | ExperimentPatch → Experiment | 200 | I、E；DRAFT/INVALID/READY |
| POST | `/training/experiments/{id}/validate` | `{}` → ExperimentValidation | 200 | I、E；不训练 |
| POST | `/training/experiments/{id}/submit` | `{confirmation_id}` → ExperimentAccepted | 202 | I；training.submit |
| POST | `/training/experiments/{id}/cancel` | `{reason?}` → Experiment | 202 | I、E |
| GET | `/training/experiments/{id}/events` | after_seq、limit → EventPage | 200 | 与对应任务共享事件服务 |
| GET | `/training/experiments/{id}/logs` | cursor → LogEntry[] | 200 | 脱敏 |
| GET | `/training/experiments/{id}/metrics` | attempt、after_step? → ExperimentMetrics | 200 | 指标序列及终值 |
| GET | `/training/experiments/{id}/artifacts` | cursor → Artifact[] | 200 | 只读权限 |
| POST | `/training/comparisons` | `{experiment_ids,protocol_id,protocol_version}` → ExperimentComparison | 200 | 2–10个兼容实验 |

### 13.2 ExperimentSpec

```json
{
  "project_id":"project-default",
  "name":"SFC 基线训练",
  "task_type":"sfc_separation",
  "execution":{
    "kind":"template",
    "template_id":"sfc_baseline",
    "template_version":"3"
  },
  "data_version_id":"dv_example",
  "feature_version_id":"fv_example",
  "target_field":"separation_label",
  "split":{
    "strategy":"scaffold",
    "train_fraction":0.8,
    "validation_fraction":0.1,
    "test_fraction":0.1,
    "random_seed":42
  },
  "parameters":{},
  "evaluation_protocol":{"id":"ep_sfc_example","version":"1"},
  "resource_budget":{
    "cpu_cores":4,
    "memory_mb":8192,
    "gpu_count":0,
    "max_runtime_seconds":14400
  }
}
```

所有示例 template/data/field ID 必须由实际服务返回后选择，不允许照抄示例登记为真实模型。模板与科学资产未提供则不能提交。

`execution.kind` 首版仅 Literal["template"]；不包含 artifact/command/python/entrypoint/requirements 等字段。`parameters` 按模板已审核 JSON Schema 验证并生成对应 Pydantic DTO；禁止任意可执行表达式。

split.strategy 只允许模板支持的 `random | scaffold | group | predefined`；group 另需 `group_field`，predefined 另需已审核 `split_manifest_artifact_id`，以 discriminated union 表达。fraction 和=1；分割发生在拟合预处理之前，防止数据泄漏。

`TrainingTemplate`：`id,version,task_type,data_schema_id,feature_versions,allowed_targets,parameters_schema,split_schema,output_manifest_schema,evaluation_protocols,resource_limits,code_version,dependency_lock_hash,checkpoint_support,health`。

### 13.3 Experiment 与候选状态

`Experiment={id,revision,config_hash,etag,spec,status,validation_report,job_id|null,current_attempt,candidate_status,candidate_model_id|null,created_at,started_at,finished_at,error}`。

status：`DRAFT|VALIDATING|INVALID|READY|QUEUED|RUNNING|CANCEL_REQUESTED|SUCCEEDED|FAILED|CANCELED|TIMED_OUT|INTERRUPTED`。验证是短时结构/引用检查，200 返回结果；不读取全部数据做训练或长评测，昂贵数据质量预检查应提前在数据导入任务完成。

`candidate_status=NOT_REQUESTED|VALIDATING|CREATED|REJECTED`。SUCCEEDED 后固定流程创建模型校验任务；验证通过后登记 Candidate，失败则 candidate_status=REJECTED，训练 status 仍 SUCCEEDED 并显示验证原因。绝不能把失败训练的残留文件自动登记。

Metric 点：`key,label,value,unit,step,split,attempt,timestamp`。最终指标带 `protocol_id/version,data_version_id,split_manifest_sha256`。候选与 Champion 只有在任务、协议、数据与 split 兼容时可比较；不跨指标类型硬比高低。

### 13.4 重试与中断

技术失败可通过 Job retry，若高成本则先对 Job 新 revision 预览/确认 action=job.retry；保持 spec/hash 不变，新 Attempt 单独记录。修改算法/数据/参数须创建新实验，不能借 retry 改配置。

中断的模板仅在 checkpoint_support 声明支持且哈希/环境一致时续训。checkpoint 文件不是可上传任意 pickle 的后门；格式和 Loader 由模板预注册。

## 14. 模型中心

### 14.1 登记

| 方法 | 路径 | 请求 → data | HTTP | 约束 |
|---|---|---|---:|---|
| GET | `/models` | 项目/任务/lifecycle/cursor → Model[] | 200 | 可按 scope 查询角色 |
| POST | `/models/registrations` | ModelRegistrationCreate → ModelRegistration | 201 | I |
| GET | `/models/registrations/{id}` | — → ModelRegistration | 200 | 阶段/报告 |
| POST | `/models/registrations/{id}/validate` | `{}` → RegistrationAccepted | 202 | I、E |
| POST | `/models/registrations/{id}/register` | `{confirmation_id}` → Model | 201 | I；model_registration.register |
| GET | `/models/{id}` | scope_id? → ModelDetail | 200 | 模型卡/资产/部署 |
| GET | `/models/{id}/artifacts` | cursor → Artifact[] | 200 | 权重下载另受策略限制 |

`ModelRegistrationCreate={project_id,source:{kind:external_package,artifact_id}|{kind:training_result,experiment_id,artifact_id},name,task_type}`。

`ModelRegistration={id,revision,config_hash,etag,status:DRAFT|VALIDATING|VALID|INVALID|REGISTERED,source,manifest_snapshot,checks,job_id,model_id,error_report_ref}`。

`checks` 项含 `step,status:PENDING|RUNNING|PASSED|FAILED,report_ref,checked_at`。步骤至少 manifest/hash、allowlisted loader/format、dependency/runtime、schema、load、golden、finite values。

外部包只引用本构建预注册 `loader_id` 和数据型参数/权重；不接受任意 entrypoint、代码文件加载或动态装包。拒绝不可信 pickle/joblib/自定义反序列化。模型解析与科学运算置于受控 Worker，不在请求事件循环执行。

可信训练流水线的自动 Candidate 产出属于已确认实验的固定后置动作，由 service/worker 通道执行；外部包登记必须人工确认。两者最终调用同一登记服务和验证规则，不重复模型资产。

### 14.2 模型对象

```text
Model
  id, project_id: ID
  name, version, task_type: string
  artifact_id: ID
  sha256: Hash
  lifecycle_status: VALIDATING | CANDIDATE | APPROVED | REJECTED | RETIRED
  input_schema_version, output_schema_version, feature_version: string
  source: {kind,experiment_id|null,registration_id}
  applicability: {scope_ids:string[],limitations:string[]}
  model_card: {summary,training_data_ref,metrics,limitations,license}
  deployments: [{id,scope_id,stage:STAGING|PRODUCTION,is_current:boolean}]
  comparison_roles: [{scope_id,role:CHAMPION|CHALLENGER}]
  revision, etag, created_at
```

无部署时 deployments=[]，UI 可呈现 NOT_DEPLOYED；不在一个全局字段同时声称多个 scope 的状态。模型版本和历史权重不可覆盖。

### 14.3 评测与审批

| 方法 | 路径 | 请求 → data | HTTP | 约束 |
|---|---|---|---:|---|
| GET | `/evaluation-protocols` | task_type → EvaluationProtocol[] | 200 | 已审核协议 |
| POST | `/model-evaluations` | EvaluationCreate → EvaluationAccepted | 202 | I，评测预算检查 |
| GET | `/model-evaluations/{id}` | — → ModelEvaluation | 200 | 固定数据/协议 |
| POST | `/model-comparisons` | ComparisonCreate → ModelComparison | 200 | 同任务/协议/数据 |
| POST | `/models/{id}/approval-requests` | ApprovalRequestCreate → ApprovalRequest | 201 | I |
| GET | `/approval-requests/{id}` | — → ApprovalRequest | 200 | 状态/理由 |
| POST | `/approval-requests/{id}/approve` | `{reason,expected_revision}` → ApprovalRequest | 200 | I、E、human + model:approve |
| POST | `/approval-requests/{id}/reject` | `{reason,expected_revision}` → ApprovalRequest | 200 | I、E、human + model:approve |

`EvaluationProtocol={id,version,task_type,data_version_id,split_manifest_sha256,metrics,thresholds,scope,resource_limit,independence_description}`。协议由部署审核注册；首版不让 Agent 修改阈值或评测集。

`EvaluationCreate={project_id,model_id,protocol_id,protocol_version}`，所用资源不超过协议固定限额；超额不动态放宽，返回429/422。`ModelEvaluation={id,job_id,status,model_snapshot,protocol_snapshot,data_snapshot,metrics,pass_status:PENDING|PASSED|FAILED,report_artifact_id,error}`。

`ComparisonCreate={project_id,model_ids:[ID,ID],protocol_id,protocol_version,scope_id}`；必须已有成功且同协议版本/数据哈希的评测，不隐式在同步 comparison 中启动训练。

`ApprovalRequestCreate={evaluation_id,scope_ids:ID[],reason}`；ApprovalRequest 含 `id,model_id,evaluation_snapshot,scope_ids,status:PENDING|APPROVED|REJECTED,requested_by,decided_by|null,reason,revision,etag,created_at,decided_at|null`。

Agent 可以准备/提交审批申请，但不能 approve/reject。首版默认单主体只记录部署操作者决定，不能宣称完成身份独立的双人审批。

### 14.4 发布草稿与原子切换

| 方法 | 路径 | 请求 → data | HTTP | 约束 |
|---|---|---|---:|---|
| POST | `/model-deployment-drafts` | DeploymentDraftCreate → DeploymentDraft | 201 | I，human + model:deploy |
| GET | `/model-deployment-drafts/{id}` | — → DeploymentDraft | 200 | 当前范围 |
| POST | `/model-deployment-drafts/{id}/validate` | `{}` → DeploymentDraft | 200 | I、E |
| POST | `/model-deployment-drafts/{id}/submit` | `{confirmation_id}` → DeploymentAccepted | 202 | I，model_deployment.publish |
| GET | `/model-deployments` | project_id、scope_id、cursor → Deployment[] | 200 | 历史不可覆盖 |
| GET | `/model-deployments/{id}` | — → Deployment | 200 | Job/结果 |
| POST | `/model-deployments/{id}/rollback-drafts` | RollbackDraftCreate → DeploymentDraft | 201 | I、human + model:deploy |

```json
{
  "project_id":"project-default",
  "scope_id":"scope_sfc_default",
  "target_model_id":"model_candidate",
  "stage":"PRODUCTION",
  "expected_pointer_revision":4,
  "rollback_model_id":"model_current"
}
```

`DeploymentDraft` 加 `id,revision,config_hash,etag,status:DRAFT|READY|INVALID|SUBMITTED,validation_report,source_deployment_id|null,operation:PUBLISH|ROLLBACK`。首次发布 rollback_model_id 可 null，必须在摘要中显示“无历史模型回滚，失败保持未部署”。

`RollbackDraftCreate={target_historical_model_id,expected_pointer_revision,reason}`；返回普通 DeploymentDraft，仍走验证、Confirmation、submit，不直接改指针。

Worker 先预载/校验，再以 `(scope_id,stage,expected_pointer_revision)` CAS 切换 pointer、Champion 和审计；失败保持旧版本。不存在自动覆盖历史制品。发布中断先 reconcile 实际指针和操作账本，不通过通用重试盲目重复。已成功发布只能创建新的回滚/发布操作。

## 15. 实用工具与 TLC 共用服务

### 15.1 分子查找

`POST /utilities/molecule-lookup`，200，查询无 I。

```json
{
  "project_id":"project-default",
  "query":"CC(=O)Oc1ccccc1C(=O)O",
  "input_type":"smiles",
  "allow_external":false
}
```

`input_type=smiles|name|cas`；SMILES 本地 RDKit 校验/描述符，name/cas 先查项目允许的本地分子索引，无结果且不允许外部时返回未找到，不猜结构。启用 name/cas 外部查询必须同时满足构建能力、操作权限、数据策略与固定 Provider allowlist；前端只选择是否允许，不能指定任意 URL。

`MoleculeLookupResult={query,status:FOUND|NOT_FOUND|INVALID|FAILED,structure_ref|null,identifiers:{canonical_smiles,inchi,inchikey,formula,cas},descriptors:[{key,value,unit,version}],external_query_status:NOT_REQUESTED|SUCCEEDED|NOT_FOUND|FAILED,sources:Source[],warnings}`。未查 CAS 用 null 和 NOT_REQUESTED，不输出伪 CAS。

### 15.2 TLC 条件比较与反推

| 方法 | 路径 | 请求 → data | HTTP |
|---|---|---|---:|
| POST | `/tlc/condition-comparisons` | TlcComparisonCreate → PredictionAccepted | 202，I |
| POST | `/tlc/reverse-solvent` | TlcReverseCreate → PredictionAccepted | 202，I |

TlcComparisonCreate：`project_id,structure_ref,model_selection,schema_version,conditions:TlcInput[]`，条件数1–200且不超过模型能力。

TlcReverseCreate：`project_id,structure_ref,model_selection,schema_version,target_rf:{min,max},candidate_system_ids:string[],ratio_step:number,max_candidates:int`。Rf 目标满足0<=min<=max<=1，步长>0且<=1，max_candidates<=200；实际范围由模型声明约束。

反推输出区分直接模型点与插值估计，禁止用插值点冒充模型返回；超出可达到范围时明确 warning。对应预测页面与实用工具入口必须调用同一个服务/接口，不提供两套算法。

条件比较结果 `TlcConditionComparisonResult` 与反推结果 `TlcReverseResult` 均在结果顶层返回 `warnings:string[]`，采用 `CODE: message` 表达真实模型适用性及执行过程警告。比较结果的该字段默认空列表，兼容此前未保存此字段的历史结果；不回填历史数据。两种工具均保留固定模型的实验确认和独立泛化证据限制，反推还保留实际产生的 `TARGET_UNREACHABLE`、`PARTIAL_FAILURE`。成功提交时，同一结果的警告同步至 Prediction 的结构化 `Warning[]` 与预测记录的 `string[]`；取消或失败不发布未提交结果的警告。警告不改变 Rf、比例、评估预算或科学验收状态。

## 16. 色谱智能助手：Conversation、Message、Agent Run

### 16.1 核心对象不能混用

- Conversation：持续会话，绑定一个 project，项目不可事后切换。
- Message：用户/助手可展示消息，append-only；不允许客户端提交 system/tool 角色。
- AgentRun：处理一次用户输入或继续同一待确认操作的业务执行对象。
- AgentAttempt：一次实际 Worker/框架执行尝试；恢复产生新 attempt，不创建重复用户消息。
- LlmCall：一次实际模型请求尝试，包括失败/网络重试；不是整个会话。
- ToolCall：模型的工具提议及服务端校验/执行记录。
- Job：训练、批量等长业务任务；独立于 AgentRun 与聊天连接。

公共 API 不暴露 PydanticAI 自有节点对象、序列化类名或 Python traceback。

### 16.2 会话路由

| 方法 | 路径 | 请求 → data | HTTP | 约束 |
|---|---|---|---:|---|
| GET | `/conversations` | project_id、q、archived、cursor → Conversation[] | 200 | 按 updated_at/id 倒序 |
| POST | `/conversations` | ConversationCreate → Conversation | 201 | I |
| GET | `/conversations/{id}` | — → Conversation | 200 | 附 current_run_id |
| PATCH | `/conversations/{id}` | `{title?}` → Conversation | 200 | I、E |
| POST | `/conversations/{id}/archive` | `{confirmation_id}` → Conversation | 200 | I；不取消业务 Job |
| DELETE | `/conversations/{id}` | `ConversationDelete={confirmation_id}` → `ConversationDeleted={id,deleted:true}` | 200 | I、E；`conversation.delete` 确认；真实删除助手私有内容 |
| GET | `/conversations/{id}/messages` | cursor、limit → Message[] | 200 | seq 升序，先加载最近一页 |
| POST | `/conversations/{id}/messages` | UserMessageCreate → MessageAccepted | 202 | I |
| GET | `/conversations/{id}/runs` | cursor → AgentRun[] | 200 | 平台执行记录 |
| GET | `/conversations/{id}/tool-calls` | cursor → ToolCall[] | 200 | 不回显秘密 |

`ConversationCreate={project_id,title?:string,mode:knowledge|business}`；title 最大120字符；空标题服务端置“新对话”，可后续用确定性截取首条用户文本生成，不额外调用模型猜标题。mode 在首版会话创建后固定；切换模式创建新会话，避免历史授权混淆。

`Conversation={id,project_id,owner_id,title,mode,status:ACTIVE|ARCHIVED,current_run_id|null,last_message_at,created_at,updated_at,revision,etag}`。

会话删除补充（2026-10-20）：ACTIVE 与 ARCHIVED 会话均可删除，必须经所有者与项目权限校验；删除预览 action=`conversation.delete`，其 etag 等于当前 Conversation.etag，确认绑定 revision/config_hash/policy_version。DELETE 必须提交 Idempotency-Key、If-Match 和严格 `{confirmation_id}`，未知字段拒绝。存在非终态 Run、AgentAttempt 或该 Run 自有助手承载 Job/JobAttempt 时返回 409 `CONVERSATION_BUSY`，提示先停止回答并等终态；不取消独立业务任务。DELETE 版本失效返回 412 `ETAG_MISMATCH`；创建 Confirmation 时预览绑定已变化，沿公共创建接口返回 409 `CONFIRMATION_INVALID`，必须重新预览并人工确认；已有确认在 DELETE 消费时绑定失效返回 409 `CONFIRMATION_INVALIDATED`。其他确认错误沿公共规则返回。

删除与消息提交、重命名、归档、恢复沿会话行锁串行；确认消费、私有内容删除、无原文审计和幂等完成同事务。成功不返回 ETag；同键同目标、body 与 If-Match 重放返回 200 和 `Idempotency-Replayed:true`，不同签名返回 409 `IDEMPOTENCY_KEY_REUSED`。删除授权墓碑仍校验 actor/project，不向其他主体泄露历史存在信息。删除后列表、搜索、归档不返回该会话；会话、消息、Run/Attempt、事件/SSE、工具及 Context Inspector 的直接读取/恢复不能恢复原文，返回 404。旧助手写请求的持久幂等响应不再包含可恢复的标题或消息。

删除范围为会话聊天内容及助手私有框架历史、消息、事件、工具/模型调用副本、上下文快照；预测记录、已提交业务 Job、共享制品与必要业务审计保留。助手自有终态 Job 只保留无聊天内容的引用墓碑并禁止重试/恢复。业务副作用 execution ledger 作为业务幂等证明保留，不按无法反查的哈希猜删。发现无法安全区分的私有内容副本时针对受影响会话阻断，不级联删除共享业务数据。不承诺删除备份、外部 Provider 副本、日志或磁盘安全擦除。

### 16.3 发消息与受理

```json
{
  "content":"用这个附件创建 SFC 批量预测草稿，先给我确认配置。",
  "attachment_ids":["art_batch_example"],
  "client_message_id":"client_msg_01"
}
```

content 为1–20,000字符；attachment_ids 最多10个且必须同项目/当前可读，类型与任务用途相符。客户端不能追加 tools/model/system_prompt/role/history/approved/user_id/tenant_id 等字段。

事务内创建用户 Message、AgentRun、Job/Outbox，并占用会话执行槽。返回：

```json
{
  "data":{
    "message_id":"msg_user_example",
    "agent_run_id":"arun_example",
    "status":"QUEUED",
    "events_url":"/api/v1/agent-runs/arun_example/events",
    "stream_url":"/api/v1/agent-runs/arun_example/events/stream"
  },
  "warnings":[],
  "request_id":"req_example"
}
```

唯一约束 `(conversation_id,client_message_id)`；相同内容重放返回已有 accepted，不重复计费请求；相同 client_message_id 不同内容409。会话有非终态 Run 时新消息409 `CONVERSATION_BUSY` 并返回 current_run_id；前端不能通过换 Idempotency-Key 绕过。

新会话不继承旧会话附件、草稿、确认或工具执行状态。关联附件只是受控引用，不是把文件内容无条件送给 LLM。

### 16.4 Message DTO 与可展示 block

```text
Message
  id, conversation_id: ID
  seq: 十进制字符串
  role: user | assistant
  agent_run_id: ID | null
  status: FINAL | PARTIAL | FAILED
  blocks: MessageBlock[]
  attachment_ids: ID[]
  created_at: timestamp
```

MessageBlock 是判别联合：

| type | 必填字段 |
|---|---|
| text | `text`（纯文本/受限 Markdown） |
| citation | `citation_id,title,locator` |
| prediction_result | `prediction_id,title` |
| job | `job_id,resource_ref,title` |
| confirmation_request | `agent_run_id,tool_call_id,resource_ref,action,revision,config_hash` |
| warning | `code,message` |
| artifact | `artifact_id,title` |
| navigation | `resource_ref,label`，前端通过已知资源注册表生成路由 |

模型不得提供任意 HTML 或 JavaScript；navigation 不接受任意 URL。引用/资源 ID 经过服务端验证才落库。无法核对的 citation 不伪装有效，可触发一次受控修正后报错/移除并警告。

所有工具事件独立存储，不把隐藏思维链塞进 text。模型内部 reasoning/thinking 字段不映射成 SSE 或 MessageBlock。

### 16.5 AgentRun DTO 与状态

```text
AgentRun
  id,conversation_id,project_id,input_message_id: ID
  mode: knowledge | business
  status: QUEUED | RUNNING | WAITING_CONFIRMATION | CANCEL_REQUESTED |
          SUCCEEDED | FAILED | CANCELED | TIMED_OUT | INTERRUPTED
  attempt: int
  provider_configuration_id,provider_configuration_revision: string
  model_id: string
  prompt_version,toolset_version,policy_version: string
  pending_actions: [{tool_call_id,resource_ref,action,revision,config_hash,expires_at}]
  related_resources: ResourceRef[]
  usage: {input_tokens:int|null,output_tokens:int|null,model_requests:int,tool_calls:int}
  last_event_seq: string
  error: PublicErrorSummary | null
  started_at,finished_at: timestamp | null
  created_at,updated_at: timestamp
  revision,etag
```

首版不提供自动后台监督训练完成后不断调用 LLM 的无限模式。工具提交训练返回 job_id 后，Agent 可告知已提交并完成本次 Run；前端订阅 Job，用户发新消息再解释结果。

等待确认不占 Worker 线程/协程长时间睡眠，而是持久化 pending action 和可信恢复状态后释放执行。等待超时30分钟进入 TIMED_OUT并保留草稿；不自行批准。

### 16.6 Run 路由

| 方法 | 路径 | 请求 → data | HTTP | 约束 |
|---|---|---|---:|---|
| GET | `/agent-runs/{id}` | — → AgentRun | 200 | assistant:use，当前范围 |
| POST | `/agent-runs/{id}/cancel` | `{reason?}` → AgentRun | 202 | I、E |
| POST | `/agent-runs/{id}/resume` | ResumeAgentRun → AgentRun | 202 | I、E，等待确认/可恢复中断 |
| GET | `/agent-runs/{id}/events` | after_seq、limit → EventPage | 200 | 可展示事件 |
| GET | `/agent-runs/{id}/events/stream` | after_seq → SSE | 200 | §17 |
| GET | `/agent-runs/{id}/tool-calls` | cursor → ToolCall[] | 200 | 工具提议/执行结果 |
| GET | `/agent-runs/{id}/llm-calls` | cursor → LlmCallSummary[] | 200 | trace:read |

ResumeAgentRun 判别联合：

```json
{"reason":"confirmation","confirmation_id":"confirm_example"}
```

或 `{reason:"recovery"}`；后者只能用于服务端认定可恢复的 INTERRUPTED Run，不能重试已成功、已拒绝或已取消操作。失败类型不支持恢复时409，用户新建消息。

恢复确认必须匹配服务端 pending tool_call/resource/action/revision/hash；不能传 raw message_history、DeferredToolResults、approve_all 或工具结果正文。

取消 AgentRun 只阻止后续 LLM/工具动作；已经提交的业务 Job 继续执行，需通过其 cancel API 单独取消。任何已完成副作用不能在 UI 中伪装撤销。

### 16.7 ToolCall 与工具幂等

```text
ToolCall
  id,agent_run_id,agent_attempt_id: ID
  provider_tool_call_id: string
  tool_name,tool_schema_version: string
  status: PROPOSED | VALIDATING | WAITING_CONFIRMATION | RUNNING |
          SUCCEEDED | FAILED | REJECTED | INTERRUPTED
  arguments_summary: 脱敏且版本化的结构
  resource_ref: ResourceRef | null
  confirmation_id: ID | null
  result_summary: 允许展示的结构 | null
  job_id: ID | null
  error: PublicErrorSummary | null
  started_at,finished_at: timestamp | null
```

首版默认工具串行执行，避免同一草稿并发修改。模型原始参数先 Pydantic 校验，再业务授权/确认，再执行。非幂等写操作的 `operation_key` 由服务端根据平台 Run、工具名、规范化参数和目标 revision 生成；执行账本持久保存提议/执行/结果。同一 Run 重放同一写动作返回原结果，不因 provider tool_call_id 改变而重复创建训练任务。

读工具可以再次执行以获得新状态，不被永久结果缓存误导。需要用户明确“再次创建”的同配置业务写操作应进入新用户消息/新 Run，而不是在重试中重复副作用。

### 16.8 首版 ToolSet 清单

下表为内部工具 ID，不是另一套 HTTP 路由：

| ToolSet | 工具 ID 示例 | 权限/边界 |
|---|---|---|
| structure | normalize_structure、validate_enantiomer_pair | 共用结构服务 |
| knowledge | search_knowledge、read_citation | 只读当前项目资料 |
| prediction | create_prediction、get_prediction、get_prediction_capabilities | 参数不全补问；真实模型 |
| batch | create_batch_draft、update_batch_draft、validate_batch、submit_batch、get_batch | submit 需服务端 Confirmation |
| training | list_training_templates、create_experiment_draft、validate_experiment、submit_training、get_experiment | 固定模板；submit 需确认 |
| model | get_model_card、create_registration_draft、validate_registration、register_candidate、get_evaluation、prepare_approval_request | 登记需确认；不批准/发布 |
| utilities | molecule_lookup、tlc_reverse_solvent | 外发受策略限制 |

实际清单由模块组合生成。内部工具不直接读写其他模块数据库，也不绕回旧 HTTP 服务。工具没有通用 shell/exec/eval/python/file_write/docker/git/SQL/HTTP fetch 能力。

`knowledge` 模式仅装配只读资料/允许的结构说明工具；`business` 可装配当前构建所有已授权业务工具。不论模式，服务端仍逐次授权。

### 16.9 PydanticAI 适配实现要求

固定首版 adapter 使用 PydanticAI Agent、typed deps 和结构化工具。部署与请求生命周期分别管理客户端/服务与 Run 上下文，避免每个模型调用重新初始化重资源。

消息历史只从服务端存储加载；使用框架消息格式时必须保存 `framework_name,framework_version,serialization_version`，并封装序列化 adapter。公开 Message DTO 与内部恢复历史分开。

使用 deferred tool 功能挂起等待确认时，可信历史、pending tool call 和确认结果必须由服务端配对生成。框架的工具审批不替代 AuthorizationPolicy 或业务 ConfirmationService。

首版不依赖任何 Harness 的文件/终端能力；不安装 Pi；不要求 OpenAI Agents SDK 与 PydanticAI 双框架并存。

## 17. SSE、事件重放与前端恢复

### 17.1 Event 与流标识

每个 stream 独立递增 seq，MySQL BIGINT 保存，JSON 用十进制字符串避免 JS 精度损失。唯一键 `(stream_type,stream_id,seq)`。

```json
{
  "seq":"15",
  "type":"tool.completed",
  "schema_version":"1.0",
  "timestamp":"2026-09-20T06:00:00Z",
  "agent_run_id":"arun_example",
  "data":{
    "tool_call_id":"tc_example",
    "resource_ref":{"type":"experiment","id":"exp_example"},
    "job_id":"job_example"
  }
}
```

Job stream 使用 `job_id` 代替 agent_run_id。EventPage data 为 `{events:Event[],next_seq:string,has_more:boolean,stream_status:string}`，不是普通倒序资源分页。

### 17.2 首版事件类型

| type | data 必含 |
|---|---|
| run.queued / run.started / run.completed / run.failed / run.canceled / run.interrupted | `status,attempt,error?` |
| message.delta | `message_id,block_index,delta,offset` |
| message.completed | `message_id`（最终 Message 从普通 API 读取） |
| llm.started / llm.completed / llm.failed | `llm_call_id,request_index,attempt,usage?,error?`，不含请求正文 |
| tool.proposed / tool.started / tool.completed / tool.failed | `tool_call_id,tool_name,status,resource_ref?,job_id?,error?` |
| confirmation.required | `tool_call_id,resource_ref,action,revision,config_hash,expires_at` |
| confirmation.resolved | `confirmation_id,outcome` |
| job.linked | `job_id,resource_ref` |
| job.status_changed | `status,attempt,error?` |
| job.progress | `completed,total,unit,stage` |
| artifact.created | `artifact_id,kind` |
| warning | `code,message` |

unknown event schema/type 前端保留日志并提示升级客户端，不执行其中任意脚本。Event data 中不包含内部思维链、Key 或全部客户数据。

### 17.3 SSE 格式

```text
id: 15
event: tool.completed
data: {"seq":"15","type":"tool.completed","schema_version":"1.0","timestamp":"2026-09-20T06:00:00Z","agent_run_id":"arun_example","data":{"tool_call_id":"tc_example","tool_name":"submit_training","status":"SUCCEEDED","job_id":"job_example"}}

: heartbeat

```

响应设置 `Content-Type: text/event-stream`、`Cache-Control: no-cache`、`X-Accel-Buffering: no`；Nginx 为该路由关闭 buffering 与压缩缓冲，read timeout 至少90秒；15秒 heartbeat。不要把整个站点连接永久开放。

### 17.4 重连与降级

优先使用 URL `after_seq`；首次不传则从该 Run 的0开始。重连可用 Last-Event-ID，两者都传且不一致返回400。服务端先验证授权/游标，再发 HTTP 200；游标超7天重放窗口返回410与资源详情地址。

客户端按 `(stream_id,seq)` 去重，断线3秒后重连；不重新 POST 消息。`message.delta.offset` 可检查文本断点，发现缺口则重新拉取 Message/Run。终态事件持久化后流可关闭；用户切页不影响后端任务。

SSE不可用时每2秒 GET events/状态，终态停止；后台页可降为10秒。Redis Pub/Sub 只能用作“有新事件”的通知，真正重放从 MySQL 读取，避免订阅切换丢失事件。高频 delta 可每100ms或累计256字符合并落库，最终消息必须完整持久化。

客户端恢复顺序：会话详情 → 最近 Message → current_run → 从本地最后 seq 订阅 → 关联 Job 查询。业务数据库是事实来源，localStorage 只保存 UI 偏好/最后可见 ID，不保存 API Key 或替代消息数据库。

## 18. LLM 调用与 Context Inspector

### 18.1 路由

仅助手的 trace 子能力纳入构建时注册：

| 方法 | 路径 | data | HTTP | 权限 |
|---|---|---|---:|---|
| GET | `/llm-calls/{id}` | LlmCallDetail（元数据） | 200 | trace:read |
| GET | `/llm-calls/{id}/context` | ContextSnapshotView | 200 | trace:content:read |
| GET | `/context-snapshots/{id}` | ContextSnapshotMetadata | 200 | trace:read |
| GET | `/context-snapshots/{id}/content` | ContextSnapshotContent | 200 / 410 | trace:content:read |
| POST | `/context-snapshots/compare` | ContextDiff | 200 | 两个内容均可读 |

compare 请求 `{left_id,right_id}`，只比较已保存快照，不触发 LLM 或工具重放。首版不提供“按旧 context 自动重跑”按钮。

### 18.2 LlmCallDetail

```text
id,agent_run_id,agent_attempt_id: ID
request_index,transport_attempt: int
provider_configuration_id,provider_configuration_revision,model_id: string
status: CREATED | RUNNING | SUCCEEDED | FAILED | INTERRUPTED
logical_context_snapshot_id: ID | null
provider_request_snapshot_id: ID | null
model_parameters_summary: 安全白名单字段
usage: {input_tokens:int|null,output_tokens:int|null,cached_input_tokens:int|null}
estimates: {input_tokens:int|null,method:string|null,estimator_version:string|null}
latency_ms: int | null
response_summary: {text_excerpt,tool_names,finish_reason} | null
error: PublicErrorSummary | null
created_at,finished_at
```

一次网络重试也是实际请求尝试，有独立 LlmCall ID/transport_attempt；费用未知用null，不盲加重复失败请求估计。Provider 实报 usage 与本地 estimate 不互相覆盖。

### 18.3 Context 内容结构

```json
{
  "id":"ctx_example",
  "llm_call_id":"llm_example",
  "view":"provider_request",
  "capture_state":"AVAILABLE",
  "capture_mode":"redacted",
  "payload_schema_version":"openai_chat_v1",
  "payload":{
    "model":"configured-model",
    "messages":[
      {"role":"system","content":"你是色谱业务助手，只调用已授权工具。"},
      {"role":"user","content":"解释刚才的预测结果。"}
    ],
    "tools":[],
    "stream":true
  },
  "sources":[
    {"json_pointer":"/messages/0/content","kind":"system_instruction","source_version":"assistant-prompt-v1"},
    {"json_pointer":"/messages/1/content","kind":"user_message","resource_ref":{"type":"message","id":"msg_example"}}
  ],
  "redactions":[{"category":"secret","count":0}],
  "truncated":false,
  "hash_scope":"redacted_payload",
  "sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "created_at":"2026-09-20T06:00:00Z",
  "expires_at":"2026-09-27T06:00:00Z"
}
```

上述是形状示例，不代表真实调用发生。capture_state：`AVAILABLE | DISABLED | NOT_CAPTURED | EXPIRED | FAILED`。关闭采集时 metadata 仍返回state，payload=null；已过期内容接口410，不伪造完整内容。

`logical_context` 记录工具过滤、系统指令/历史、检索、工具结果与裁剪之后的模型输入；`provider_request` 在 SDK 最后序列化发送边界捕获，经脱敏保存。前者不能冒充后者。供应商服务端追加上下文或隐藏推理不在本系统可观测范围。

sources.kind：`system_instruction | user_message | history | tool_definition | tool_result | knowledge_chunk | attachment_excerpt | runtime_note`。runtime_note 只允许服务端明确选择的非敏感字段，不能序列化整个 ActorContext/服务对象。

### 18.4 采集安全

运行配置 `TRACE_CONTENT_MODE=off|redacted` 默认off，首版不允许 raw。API Key/Authorization/Cookie/Set-Cookie 从不进入日志、Trace payload 或导出；输出端 reasoning/thinking/opaque恢复字段以白名单策略排除。检测到敏感业务字段按项目策略脱敏，内容访问仍按敏感数据处理，不承诺正则能识别一切秘密。

默认单快照最大2 MiB；超限要记录 truncated 和截断位置，不伪称全量。脱敏摘要/正文哈希只证明保存后的内容完整性，不是未脱敏原始请求的审计签名。

通用 artifact API 必须对 context kind 强制相同权限。每次读取、比较、下载内容记录审计；生产环境禁用内容采集时仍能看调用次数、耗时、错误与关联对象。

### 18.5 Diff

`ContextDiff={left_id,right_id,summary:{added,removed,changed},changes:[{path,kind:added|removed|changed,before?,after?}],truncated}`。仅对脱敏 JSON 比较，两个版本各自权限先校验；工具结果体过大时只返回摘要与受控引用。

## 19. 系统设置、LLM 配置与运维

### 19.1 LLM 配置生命周期

首版支持一个活动 LLM 配置以及候选/历史 revision，不做复杂多 Provider 路由 UI。多个 Provider 实例与路由策略属于后续扩展，不影响 Run 保存实际配置版本。

| 方法 | 路径 | 请求 → data | HTTP | 约束 |
|---|---|---|---:|---|
| GET | `/settings/public` | — → PublicSettings | 200 | 无秘密 |
| GET | `/settings/llm` | — → LlmSettingsView | 200 | 配置只读权限 |
| POST | `/settings/llm/configurations` | LlmConfigurationCreate → LlmConfiguration | 201 | I、human/settings:write |
| POST | `/settings/llm/configurations/{id}/test` | `{}` → LlmTestAccepted | 202 | I；使用最小测试输入 |
| GET | `/settings/llm/tests/{id}` | — → LlmTestResult | 200 | 脱敏 |
| POST | `/settings/llm/configurations/{id}/activate` | `{test_id,expected_active_revision}` → LlmSettingsView | 200 | I、E、human/settings:write |
| GET | `/settings/runtime` | — → RuntimeSettingsView | 200 | 运行值/来源，非任意环境变量 |

```json
{
  "name":"internal-llm",
  "api_format":"openai_chat",
  "base_url":"https://provider.example/v1",
  "model_id":"configured-model",
  "api_key":null,
  "reuse_secret_from_configuration_id":null,
  "timeout_seconds":90,
  "max_retries":2,
  "max_output_tokens":4096,
  "external_send_policy":"deny_external"
}
```

`external_send_policy` 固定为 `deny_external` 或 `approved_endpoint_only`。`deny_external` 禁止外部 Provider 测试和执行；`approved_endpoint_only` 仅允许部署管理员通过受保护运行配置 `LLM_PROVIDER_APPROVED_ENDPOINTS` 显式批准的完整 HTTPS base endpoint。匹配包含 scheme、host、有效 port 和完整 base path，仅忽略尾斜杠；相似前缀、额外路径、userinfo、query、fragment、重定向目标均不得放行。列表缺失或不匹配时必须 fail-closed；不得以内置公共 host 白名单或前端选择代替部署批准。示例 URL 仅说明字段形状，不表示该 endpoint 已获批准。写入 Key 仅本接口一次性接收，返回只能有 `has_api_key`，不能回显秘密。`api_key` 与 `reuse_secret_from_configuration_id` 互斥。

`LlmConfiguration={id,name,revision,etag,api_format,base_url,model_id,has_api_key,status:CANDIDATE|TESTED|ACTIVE|RETIRED,limits,external_send_policy,last_test_id,created_at}`。base_url 对一般使用者可显示 endpoint label，不返回所有内网细节。

配置不可就地覆盖历史 revision，修改创建新候选。测试校验连接、文本、工具schema/工具结果循环与流式兼容性，不能执行有业务副作用的真实工具。测试失败只更新该 test，不替换活动配置。

activate 要求成功测试绑定同一配置/hash，expected_active_revision匹配；旧配置保留。新 Run 使用新活动 revision，执行中的 Run 仍用创建时快照。若强制撤销旧配置，应显式中止相关Run并审计，不静默切模型。

`PublicSettings`：产品名、build_id、默认项目、已构建的输入/导出限制、允许模式、功能健康；不含 secrets。`RuntimeSettingsView`：API/Web 端口及配置来源、存储/队列概览、采集模式；不提供在线改端口并重启按钮。

### 19.2 运维与审计

| 方法 | 路径 | 请求 → data | HTTP | 约束 |
|---|---|---|---:|---|
| GET | `/ops/queues` | — → QueueSummary[] | 200 | ops:read |
| GET | `/ops/jobs/{id}` | — → JobDiagnostic | 200 | ops:read，脱敏 |
| GET | `/ops/artifact-store` | — → StorageSummary | 200 | ops:read |
| POST | `/ops/diagnostic-bundles` | `{project_id?,include:allowed_enum[]}` → Accepted | 202 | I、ops:write |
| GET | `/ops/backups` | cursor → BackupRecord[] | 200 | ops:read |
| POST | `/ops/restore-checks` | `{backup_id}` → Accepted | 202 | I；仅预检，不覆盖生产 |
| POST | `/ops/upgrade-checks` | `{artifact_id}` → Accepted | 202 | I；离线包校验不任意执行 |
| GET | `/audit-events` | project/resource/action/time/cursor → AuditEvent[] | 200 | audit:read |

`QueueSummary={name,pending,running,failed,oldest_pending_at,worker_heartbeat_at,status}`；数据缺失用null/UNKNOWN，不用0假装正常。

`AuditEvent={id,project_id,actor_id,auth_method,interaction,request_id,action,resource_ref,before_summary,after_summary,confirmation_id|null,config_hash|null,job_id|null,agent_run_id|null,result:SUCCEEDED|FAILED,timestamp}`。审计不含密码/Token/客户完整原始数据，摘要按字段白名单。

备份还原执行、用户管理、任意运维Shell不在首版HTTP路由中；离线操作按受控runbook实施。诊断包是敏感artifact，下载再次授权。

### 19.3 首版不注册的认证与 Coding 路由

不注册 `/auth/login`、`/auth/logout`、`/auth/token` 等；不注册 `/coding/*`、`/workspaces/*`、`/sandbox-runs/*`、`/algorithm-packages/*`。不存在任何可接受任意 Python/Shell 字符串的后端执行接口。

后续新增认证保持业务 JSON schema 不变；认证由 Cookie/Bearer 与 AuthProvider 完成。新增 Coding 则通过升级文档的独立模块、路由和版本化契约完成，不靠现有 `/settings` 开关启用。

## 20. DTO 补充规则与开放规范生成

### 20.1 资源公共字段

所有可修改/可取消资源（包括 Prediction、Job、Conversation、AgentRun、草稿、ModelRegistration、ApprovalRequest）除前述业务字段外，一律返回 `revision,etag,updated_at`；需要 Confirmation 的资源还返回/可预览 `config_hash`。前述 DTO 简表未逐项重复这些字段，不表示省略。

`PublicErrorSummary={code:string,message:string,retryable:boolean}`。完整HTTP错误有 field_errors/details，资源内 error 只放该摘要，避免重复回传敏感异常。

### 20.2 Accepted DTO 的准确字段

| DTO | data 必含 |
|---|---|
| PredictionAccepted | `id`（prediction ID）、`prediction_record_id,job_id,status,resource_ref` |
| BatchAccepted | `id`（batch ID）、`job_id,attempt,execution_status,resource_ref` |
| ExperimentAccepted | `id`（experiment ID）、`job_id,status,resource_ref` |
| RegistrationAccepted | `id`（registration ID）、`job_id,status,resource_ref` |
| DeploymentAccepted | `id`（deployment ID）、`job_id,status,resource_ref` |
| ExportAccepted | `id`（export ID）、`job_id,status,resource_ref` |
| ArtifactAccepted | `id`（artifact ID）、`artifact_id,job_id,status,resource_ref` |
| ArtifactDeleteAccepted | `id`（artifact ID）、`job_id,status`（DELETING）、`resource_ref` |
| ValidationAccepted | `id`（validation ID）、`job_id,status,resource_ref` |
| LlmTestAccepted | `id`（test ID）、`job_id,status,resource_ref` |
| 通用 Accepted | `id`（对应业务对象 ID）、`job_id,status,resource_ref` |
| MessageAccepted | `message_id,agent_run_id,status,events_url,stream_url`，内部队列 Job 不暴露给普通聊天控制 |

Accepted 不返回“最终成功”；status 按对应业务资源为 QUEUED、VALIDATING 等。GET 详情是最终事实来源。

`LlmSettingsView={active_configuration:LlmConfiguration|null,active_revision:int,candidate_configurations:LlmConfiguration[]}`。`LlmTestResult={id,configuration_id,configuration_revision,status:QUEUED|RUNNING|SUCCEEDED|FAILED,checks:{connectivity,streaming,tool_calling,structured_output},error|null,created_at,finished_at}`，check 值为 PASSED/FAILED/NOT_SUPPORTED/SKIPPED。SKIPPED 仅表示因明确的前置认证或授权失败而未执行该项，不得解释为能力不支持、已失败的独立调用或通过；整体测试仍为 FAILED，错误原因须使用稳定脱敏代码。激活业务助手至少需要 connectivity、tool_calling 与 streaming 的实际测试通过，不能“连接200”即认为能运行 Agent；SKIPPED 永不满足激活条件。

`VersionInfo={application_version,build_id,api_schema_version,migration_version,dependency_lock_hash}`。`Readiness={core_ready,checked_at,capabilities:[{id,status,reason|null}]}`。`DependencyHealth={id,status:READY|DEGRADED|UNAVAILABLE|UNKNOWN,checked_at,reason|null}`。

`StorageSummary={status,total_bytes|null,used_bytes|null,available_bytes|null,last_integrity_check_at|null}`；`BackupRecord={id,status,created_at,manifest_artifact_id,verified_at|null}`；`JobDiagnostic={job,attempts,lease_state,outbox_state,public_errors}`，不能含进程秘密或任意环境变量。

### 20.3 其他简表DTO的最小字段

| DTO | 字段 |
|---|---|
| UploadReceipt | `upload_id,artifact_id,received_bytes,sha256,received_at`；校验前摘要不表示文件已AVAILABLE |
| ChromatographyTypeSummary | `id,display_name,tools:string[],health` |
| TypeCapability | `id,display_name,tools:[{id,schema_version,health}],models:ModelOption[],limits` |
| CapabilityHealth（各资源health字段） | `status:READY\|DEGRADED\|UNAVAILABLE\|UNKNOWN,reason:string\|null,checked_at:timestamp` |
| ToolSchema.structure_count | `{min:int,max:int,semantics:single\|pair\|batch}`；科学配对仍须专门验证 |
| ExperimentValidation | `experiment_id,revision,etag,config_hash,valid,status:READY\|INVALID,field_errors,warnings,resource_estimate` |
| ExperimentMetrics | `experiment_id,attempt,protocol_snapshot,series:MetricPoint[],final_metrics:MetricPoint[],next_step:int\|null` |
| ExperimentComparison | `experiment_ids,protocol_snapshot,data_snapshot,metrics:[{key,unit,values:[{resource_id,value}],direction:higher\|lower\|none}],warnings` |
| ModelComparison | 与ExperimentComparison相同，但资源字段为`model_ids`；缺少可比评测时422，不伪造比较 |
| ModelDetail | Model + `evaluations,approval_refs,registration_ref,active_scope_id\|null` |
| Record | PredictionRecord的简表别名，不另建语义不同DTO |
| ExperimentRecord | `id,project_id,molecule_ids,chromatography_type,domain_id,data_schema_version,conditions,measurements,source,quality_status,data_version_ids`；conditions/measurements由可信domain schema校验 |
| Source | `kind,display_name,reference,queried_at\|null`；reference不是可由客户端指定的抓取URL |
| LlmCallSummary | `id,agent_run_id,request_index,transport_attempt,status,model_id,usage,latency_ms,created_at` |
| ContextSnapshotMetadata | `id,llm_call_id,view,capture_state,capture_mode,sha256\|null,truncated,created_at,expires_at`，不含payload |
| ContextSnapshotView | 同§18.3的结构，可含受权payload；未采集时payload=null |
| ContextSnapshotContent | `id,view,payload_schema_version,payload,sources,redactions,truncated,hash_scope,sha256` |

其他字段只允许在明确版本化schema中添加，不由页面临时拼接。路线中的field error与warning沿用通用DTO。列表DTO可以是资源摘要，但必须包含id与列表呈现的真实状态，不能把示例展示字段捏造为后端实值。

### 20.4 DTO 实施要求

- 每一路由定义 request/response Pydantic Model 与稳定 operation_id；不得只返回任意 dict。
- discriminated union 使用显式 discriminator，至少覆盖 model_selection、temperature_program、split、MessageBlock、ResumeAgentRun、Experiment execution。
- built adapter 的输入/输出 schema 必须实际进入该构建 OpenAPI；Schema 自身作为服务端只读JSON返回时无需再次展开成模型，但用于处理预测请求时必须严格验证。
- UUID/ULID 等内部实现可选，但公开 ID 不要求前端解析格式；禁止以顺序数据库主键推断访问权。
- 不在源代码写死 API端口、客户端绝对主机、模型文件路径或客户名字；测试 fixture 中出现的样例 ID 不进入生产 seed。
- 若前端原型字段与冻结 adapter 不符，调整对应表单并登记契约变更，不为适配原型伪造模型字段。

## 21. 首版数据表与一致性约束

以下是实现模型的最小关系要求，不是要求所有内容放同一 JSON 表。实际迁移必须按模块生成。

| 表/对象 | 关键约束 |
|---|---|
| project | 稳定 ID，状态；默认项目不等于多租户 |
| artifact / upload | 项目、哈希、状态、用途、保留；上传一次性完成 |
| structure | 项目、规范化结果/快照哈希、来源；禁止跨项目自动暴露 |
| job / job_attempt | Job ID，attempt唯一，执行token/租约/截止；旧Attempt不可覆盖 |
| job_event / agent_event | stream_id+seq唯一，有序提交 |
| outbox_event | dispatch ID唯一，业务事务同写；重复消息不重复副作用 |
| idempotency_record | 作用域+key唯一，payload_hash，已受理资源引用 |
| execution_ledger | operation_key唯一，STARTED/SUCCEEDED/FAILED/UNKNOWN结果；UNKNOWN先协调 |
| confirmation | 资源/版本/hash/action/主体/过期/消费结果；CAS消费 |
| prediction / prediction_record | 一次创建即关联记录，原始输入/实际模型快照不可变 |
| batch_draft / batch_row / batch_retry_plan | 配置revision、原行号、输入hash、attempt及重试范围 |
| data_import / data_version | import提交唯一；版本不可变；指针发布原子 |
| knowledge_document / document_version / knowledge_chunk | 可见index version、定位/哈希、FTS索引 |
| citation_snapshot | 文档版本/切片/定位/权限引用；不能仅存一段无来源文本 |
| training_template / experiment | execution仅template；提交后spec不可改 |
| model_registration / model | 登记与资产状态分离、制品/Loader/契约/哈希 |
| evaluation_protocol / evaluation | 固定数据/协议/模型与hash；黄金样例和泛化评测分开 |
| approval_request | 基于明确模型/评测/scope的决定 |
| deployment_draft / deployment / production_pointer | `(project_id,scope_id,stage)` 指针唯一，revision并发检查 |
| conversation / message | 会话项目不变；client_message_id唯一；消息角色受限 |
| agent_run / agent_attempt | 会话执行槽互斥；平台Run与框架attempt分离 |
| llm_call / tool_call | 请求/工具各自ID、结果账本/实际Provider版本 |
| context_snapshot | 元数据+受控artifact；内容保留期/敏感权限 |
| llm_configuration / llm_test_result | 配置不可变revision、一次活动指针、Key密文/引用 |
| audit_event | 动作/主体/通道/资源/结果，脱敏且不可由普通用户修改 |

会话互斥在事务里锁 conversation 行并设置 current_run_id，不能依赖 Redis 临时锁而无DB约束。新Run受理、message持久化和Outbox应原子；同会话多个请求竞争只能一个成功。

ResourceRef 注册依据实际纳入的基础服务，而非仅一级菜单；管理页面未构建时不生成其跳转。ResourceRef 首版注册类型包括 project、structure、artifact、prediction、prediction_record、prediction_draft、batch_prediction、batch_retry_plan、data_import、data_version、knowledge_document、knowledge_citation、experiment、model_registration、model、evaluation、approval_request、deployment_draft、deployment、job、conversation、message、agent_run、context_snapshot、export，以及当前流程用到的structure_parse、validation、batch_validation、data_import_validation、feature_version、training_template、evaluation_protocol、llm_configuration、llm_test、diagnostic_bundle、restore_check、upgrade_check、backup。每种type须有固定解析器/权限策略，不接受未登记字符串；未构建类型从本构建schema/注册表移除。

模板、协议、模型配置都是部署可审核的元数据，不存在任意客户端修改其Python实现的API。状态列与外键约束需在迁移/测试中真实落实。

## 22. 前端组件与接口映射

| 页面/组件 | 主要接口 | 前端状态规则 |
|---|---|---|
| AppShell / NavigationRegistry | capabilities/version/access-context | 核对编译build，不运行时恢复模块 |
| StructureInput / StructurePreview | structures/normalize/parse-file/render | 所有业务复用，结构ID不是全局当前值 |
| PredictionWorkspace | prediction/types/schema + predictions | 状态驱动结果；无假成功 |
| BatchWizard | artifacts + batch drafts/validate/submit | revision改变清空确认；错误行可见 |
| RecordsWorkspace | prediction-records + exports | 分页、快照、归档/带回输入 |
| DatasetWorkspace | data-domains/imports/versions | 差异预览、确认提交、不可变版本 |
| KnowledgeWorkspace | knowledge/documents/search/citations | 页码/段落定位明确，失败不隐藏 |
| ExperimentWizard | training/templates/experiments | 只选template；禁任意脚本 |
| ExperimentRunDetail | jobs/metrics/logs/artifacts | 成功训练与candidate_status分开 |
| ModelCenter | registrations/evaluations/approval/deployment | 三维状态；独立发布确认 |
| AssistantConversationPanel | conversations/messages/runs | 刷新恢复；不把历史从客户端传后端 |
| AssistantMessageBlocks | MessageBlock联合 | 卡片引用真实资源，Markdown净化 |
| ConfirmationCard | previews + confirmations + submit/resume | 只有真实确认接口能授权动作 |
| AgentRunDrawer | agent-runs/events/tool-calls | 终态/重连/确认，不显示内部推理 |
| ContextInspector | llm-calls/context-snapshots | 专用权限、脱敏状态、token估算标签 |
| LlmSettingsPanel | settings/llm configurations/test/activate | 候选测试不覆盖活动配置 |
| OpsWorkspace | ops + audit | 不提供任意命令终端 |

Pinia按模块拆store；ConversationStore只管UI消息/选择，不持有模型客户端；TaskStore按job_id复用。公共API层统一错误/请求ID/幂等key/etag处理，页面不得各自拼接端口。

首版不渲染算法研发mode、Workspace/Code/Terminal/Sandbox页签。不通过隐藏按钮保留这些专属代码；只保留future extension插槽的通用组件注册机制。

## 23. 关键端到端流程与故障结果

### 23.1 页面提交训练

```text
GET template/data/feature/protocol
POST training/experiments → DRAFT, revision=1
PATCH ... If-Match → revision=2
POST validate → READY, config_hash固定
POST confirmations/previews → 展示数据/模板/预算
POST confirmations (I + If-Match目标ETag) → confirmation_id
POST experiments/{id}/submit (I, confirmation_id) → 202,job_id
GET/SSE Job → RUNNING → SUCCEEDED
固定后置模型验证 → candidate_status=CREATED 或 REJECTED
```

两个页面同时编辑导致412，前端提示冲突、重新拉取并让用户合并，不能静默覆盖。确认后改参数导致409 CONFIRMATION_INVALID，必须重新验证/确认。

### 23.2 助手提交同一训练

```text
POST conversations/{id}/messages → 202, agent_run_id
SSE: run.started → tool.proposed(create_experiment_draft) → tool.completed
SSE: confirmation.required → Run WAITING_CONFIRMATION
前端调用同一 Confirmation 服务展示并确认具体实验版本
POST agent-runs/{id}/resume (confirmation_id)
工具执行同一 ExperimentService.submit → job_id
SSE: job.linked → message.completed → run.completed
训练Worker继续；浏览器/Agent结束不终止训练
```

服务端保存 pending tool，不接受前端自己组一个名为 submit_training 的工具。前端确认后若网络断开，重新GET Confirmation/Run/Job能恢复，不新建训练。

### 23.3 宕机恢复

| 故障点 | 必须结果 |
|---|---|
| DB提交成功、Redis发送失败 | Outbox待发；后续重发同一Job，不丢任务 |
| Redis发送成功、Outbox未标记 | 可重投；Worker DB租约/账本阻止重复业务副作用 |
| 工具提交成功、LLM回复前崩溃 | 恢复先读tool ledger/job_id，不重复submit |
| 模型响应流中断 | Message标PARTIAL；Run失败/可恢复，不把半句当FINAL |
| 候选模型写文件后DB失败 | 临时/孤儿制品可清理，不登记半完成Candidate |
| 发布切指针后worker失联 | 读取deployment ledger与pointer协调，不盲重跑 |
| 训练无checkpoint中断 | INTERRUPTED，受控retry从头，不显示“已续训” |
| SSE连接中断 | 任务不变；按seq重放 |

## 24. 契约与验收测试（Codex 必须实现）

| ID | 测试 | 预期 |
|---|---|---|
| API-01 | 未构建模块路径/OpenAPI/前端bundle | 路由404、schema/依赖/资源不存在 |
| API-02 | 对新API传旧SMILES重复字段、command、tenantId | 422 extra_forbidden |
| API-03 | prediction I缺失/同键重放/同键异体 | 400 / 同资源 / 409 |
| API-04 | PATCH无If-Match/旧ETag | 428 / 412 |
| API-05 | 确认后改预算、数据、模型、scope | 提交409；无新Job |
| API-06 | 同confirmation不同key并发submit | 同一结果；仅一业务副作用 |
| API-07 | 伪造actor、tool result、完整history | 422；不影响模型历史 |
| API-08 | 不同project的artifact/citation/model | 404或403；无存在性泄漏 |
| API-09 | file upload内容与size/hash不符 | FAILED/QUARANTINED，不能用于业务 |
| API-10 | scanner未配置 | NOT_CONFIGURED，不标PASSED |
| API-11 | 压缩炸弹/路径穿越/不可信模型序列化 | 拒绝；无宿主执行 |
| API-12 | 缺实际模型资产的预测 | 503 MODEL_UNAVAILABLE，不返回fixture |
| API-13 | TLC petroleum_ether与hexane | 视为不同solvent，不静默转换 |
| API-14 | HPLC pair与chiral_lc single | 独立schema与错误，不混用 |
| API-15 | 批量invalid_row_policy改变 | revision变更、确认失效、排除数可见 |
| API-16 | 批量失败行重试 | 新attempt，只处理选中失败行 |
| API-17 | 训练输入execution.kind=code_artifact | 422；首版无代码路径 |
| API-18 | 训练失败/取消/超时 | 不产生Candidate |
| API-19 | 成功训练但模型验证失败 | experiment SUCCEEDED + candidate REJECTED |
| API-20 | 两个发布同时CAS同pointer | 一个成功，一个409，Champion一致 |
| API-21 | Agent调用模型approve/deploy或创建human confirmation | 工具不存在/业务层拒绝 |
| API-22 | 两条并发消息/重复client_message_id | 一Run成功/重放，另一409 BUSY |
| API-23 | SSE断线后Last-Event-ID恢复 | 不丢持久事件；客户端去重；不新LLM |
| API-24 | SSE超过重放窗口 | 410并要求普通详情恢复 |
| API-25 | cancel Agent而训练已提交 | Agent停止，训练不隐式取消 |
| API-26 | Dramatiq同消息重复投递/Worker断电 | 按账本恢复，不重复导入/训练提交/发布 |
| API-27 | OTel/日志/Context/SSE含Key或reasoning字段 | 字段被禁止/脱敏，测试失败即阻断交付 |
| API-28 | 无trace content权限访问Context或同artifact下载 | 403/404，不能绕过 |
| API-29 | Context逻辑输入与实际发送请求 | 测试HTTP transport捕获与provider_request脱敏视图对应 |
| API-30 | Token usage缺失 | null与estimate分开，不伪造cost |
| API-31 | LLM候选连接测试失败 | 原活动配置和新Run不受影响 |
| API-32 | 未批准外部API URL/重定向/元数据地址 | 拒绝；无客户数据外发 |
| API-33 | 知识归档/权限撤销/索引失败 | 新检索排除；历史引用状态真实 |
| API-34 | 生产构建残留Coding路由、table、worker或Docker socket | 构建/验收失败 |
| API-35 | OpenAPI→TS类型生成、example schema验证 | CI自动通过；无手写重复DTO |
| API-36 | Windows目标环境无可用Worker执行层 | 部署预检明确失败，不虚称完整交付 |

测试必须区分：纯契约fixture、真实DB/队列集成、真实模型/数据科学验收。使用TestModel/FunctionModel等测试替身时仅测试包可注入，不提供生产HTTP“切到假模型”入口。

## 25. Codex 交付物与停止条件

### 25.1 本次要求开发生成的工程产物

- 各构建profile对应OpenAPI JSON、TypeScript API类型/客户端、请求/响应fixture。
- SQLAlchemy模型、Alembic迁移、初始schema/model/template注册数据（只允许真实已审核资产）。
- Vue各模块页面、状态/错误/确认卡片、SSE客户端和Context Inspector。
- PydanticAI RuntimeAdapter、按构建ToolSets、服务端可信历史、执行账本和请求采集适配。
- Dramatiq handler、Outbox dispatcher、reconciler、任务租约、取消/受控重试。
- Docker Compose构建产物、.env.example、依赖lockfile、离线资源清单、健康/备份/恢复runbook。
- API-01至API-36的测试结果，与PRD的V1验收映射。

### 25.2 不允许用自由发挥填补的缺口

真实模型的数学实现、允许条件、预测输出、训练标签、数据域schema、模板算法、评测阈值、客户资源与许可证必须有可信材料。缺失时记录 `MODEL_CONTRACT_MISSING` 或具体交付阻塞，UI显示不可用；不能从前端演示数据反推“真实接口”。

所有可选且未实现功能都必须从构建中裁掉，不留假接口。不得为了后续Coding预留而把任意执行能力装到本次V1。

## 附录：来源与技术依据

业务来源：用户提供的接口v0.1和PRD v0.11；本版的显式契约变更见§0，固定技术/默认资源值属于本轮新增设计，不代表原文件已实现。

相关官方资料（2026-09-20核查；具体调用签名以实施时锁定版本为准）：

```text
PydanticAI typed dependencies
https://pydantic.dev/docs/ai/core-concepts/dependencies/
PydanticAI Model Providers / OpenAI-compatible接口边界
https://pydantic.dev/docs/ai/models/overview/
PydanticAI Deferred Tools / 服务端授权边界
https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/
PydanticAI message history / 不信任客户端历史
https://pydantic.dev/docs/ai/core-concepts/message-history/
PydanticAI Instrumentation
https://pydantic.dev/docs/ai/capabilities/instrumentation/
Dramatiq retries / best-effort time limits
https://dramatiq.io/guide.html
Dramatiq idempotent actors
https://dramatiq.io/best_practices.html
MySQL 8.4 FULLTEXT / CJK ngram
https://dev.mysql.com/doc/refman/8.4/en/fulltext-natural-language.html
```
