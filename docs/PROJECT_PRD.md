# 通用色谱智能平台项目 PRD

文档状态：TO-BE · 首版开发基线 · 可交给 Codex 实施  
文档版本：0.12  
目标发布：V1（业务助手版，不含 Coding Agent）  
更新日期：2026-10-13  
勘误说明：基线版本仍为 0.12；本次仅修订 §16.2 的 Windows 本地验收与 Linux 后续部署边界，不改变首版范围和接口字段。
配套契约：`PROJECT_FRONTEND_BACKEND_API_SPEC.md` v0.2  
后续规划：`CODING_AGENT_UPGRADE_PLAN.md` v0.1

> **实施指令：本文件与接口文档定义首版；升级文档只定义后续演进。不要把后续 Coding Agent、Workspace、Shell、Sandbox Worker、算法代码上传或代码编辑器提前做进首版。**

## 0. 阅读顺序、依据与变更性质

### 0.1 给 Codex 的阅读顺序

1. 先读本 PRD 的范围、架构、构建裁剪、安全与验收章节。
2. 再读接口文档，建立 Pydantic DTO、状态机、数据库迁移与 OpenAPI，再生成前端类型。
3. 最后读升级文档，只检查首版接口边界是否妨碍后续扩展，不实施其未来功能。
4. `preview_v2.html` 仅作为现有页面组织、配色和交互参考；`preview_coding_runtime.html` 只作为后续研发助手布局参考。原型中的演示数值、假健康状态、算法研发模式、Sandbox 面板均不是首版验收事实。

文档优先级：用户本轮明确要求 > 本 PRD 的产品范围/安全边界 > 接口文档的字段/路由契约 > 原型 > 旧材料。两份正式文档冲突时先登记冲突，不自行混合两套行为。

### 0.2 来源与本轮新增决策

本版基于用户提供的 `PROJECT_PRD.md` v0.11、`PROJECT_FRONTEND_BACKEND_API_SPEC.md` v0.1，以及现有 HTML 原型整理。原 PRD 的模块化、化学结构必含能力、共享业务服务、不可变版本、异步任务、人工确认、模型生命周期与离线交付原则继续有效。

以下是**本轮新增的实施决策**，不是原文已实现能力：

| 决策 | 本版确定内容 |
|---|---|
| Agent 技术 | Python PydanticAI，首版单 Agent + 按构建组合装配的 ToolSet |
| 后端基础 | FastAPI + Pydantic v2 + SQLAlchemy 2 + Alembic + MySQL |
| 异步执行 | Redis + Dramatiq；MySQL 为任务状态与恢复事实来源 |
| 助手传输 | 创建消息立即返回 Agent Run；持久事件 + SSE，轮询作为降级 |
| 请求观测 | 首版实现 Agent Run、LLM Call、Tool Call、脱敏 Context Snapshot |
| 确认机制 | 统一服务端 Confirmation 资源；确认不是一句聊天文本或客户端布尔值 |
| 文件存储 | 首版受控本地制品存储 + MySQL 元数据，封装 ArtifactStore 端口 |
| 首版检索 | MySQL FULLTEXT ngram 关键词检索 + 权限过滤 + 可追溯引用；不强制新增向量数据库 |
| 升级预留 | 保留 AgentRuntimeAdapter、业务服务、任务、制品、确认、事件和版本化关联契约 |
| Coding 范围 | 首版不注册 Coding 路由、不建专属表、不安装 Harness/Sandbox 专属依赖、不交付专属 Worker |

关键词检索是首版工程基线，能够支持“检索资料后回答”的流程，但不等同于向量语义检索，不承诺同义改写召回效果。其算法可以随后在 `KnowledgeRetriever` 后替换，不改变文档、引用和问答契约。

本次仅交付开发文档；未检查完整代码仓库、客户原合同或真实模型包，不宣称现有代码符合设计，也不承诺尚未提供的科学模型性能。

### 0.3 明确排除旧版本兼容

- 按新的通用产品独立开发；不复刻、不代理、不兼容旧 `8010` 等历史接口和端口约定。
- 旧 `Services/unified_server.py`、Gradio 路由、`/api/tools/...`、`/predict` 等不是新产品依赖，也不设置兼容别名。
- 旧文档中的客户磁盘绝对路径不再作为实施前提；不得要求 Codex 找到那些文件才能搭建通用底座。
- 可以复用经评审的科学算法实现，但必须通过新的模型适配器、参数校验与结果契约；这不是复用旧服务接口。
- 新接口唯一业务前缀为 `/api/v1`。默认 FastAPI 端口统一为 `9012`，来自项目 `.env`，不再同时指代某个旧统一推理服务。
- 原文中产品约束与新契约的具体调整见接口文档第 0 节；没有双写、双读或新旧接口自动回退。

## 1. 定位与交付边界

### 1.1 产品定位

面向实验研究员、方法开发人员、模型工程人员和部署管理员，提供可裁剪的色谱预测、批量计算、结构处理、数据与知识管理、业务智能助手、模板化训练及模型生命周期管理。

平台用于实验前筛选、条件建议和研发辅助；不替代实验验证，不直接控制生产仪器。所有模型建议必须呈现适用范围与实验验证提醒。

### 1.2 首版“做什么”与“不做什么”

| 能力 | 首版 V1 | 后续 |
|---|---|---|
| 五类色谱适配器、可选手性 LC | 实现模块与契约；真实可用性取决于合规模型资产 | 新方法/新模型扩展 |
| 化学结构输入、转换和可视化 | 必含核心能力 | 扩充支持格式和复杂立体化学情形 |
| 预测、批量、记录、导出 | 实现 | 更大规模调度 |
| 数据导入、不可变数据版本 | 实现 | 完整租户体系 |
| 知识资料、关键词检索、带引用问答 | 实现 | 向量/混合检索、重排、OCR |
| PydanticAI 业务助手 | 实现 | 受限算法研发工具集 |
| 模板化训练与模型中心 | 按构建清单实现 | 受审算法包训练 |
| Agent Run / Tool Trace / Context Inspector | 实现，受权限与保留策略约束 | Coding/Sandbox Trace |
| 账号登录、Token、完整 RBAC | 只预留 AuthProvider；受控默认主体 | 身份认证升级 |
| Workspace、代码编辑、Shell、动态装包 | **不实现** | 独立评审后实施 |
| Algorithm SDK、用户算法包、Sandbox Worker | **不实现**；只保留通用边界 | 见升级文档 |
| Pi、PydanticAI Harness、LangGraph、多 Agent | **非首版依赖** | 有明确收益后评估 |

“按构建清单实现”不是只做空页面。纳入的模块应完成其可用流程；缺失模型、解析器或服务时明确不可用，不以随机数、静态 JSON 或空成功响应代替真实能力。

## 代码可参考来源与需求方

| 需求方/来源           | 文档路径(代码均在D:\Jupyter\SFC\deployments的相应路径)       | 参考范围                                         |
| --------------------- | ------------------------------------------------------------ | ------------------------------------------------ |
| SFC Predictor v0.1    | `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\SFC_Predictor_AS_IS_PRD_2026-09.md` | 实用工具、分子标识与描述符、显式联网查询         |
| SFC Predictor v0.1    | `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\SFC_Predictor_API_Spec_2026-09.md` | 分子工具请求、结果、联网和错误边界               |
| Agent 集成 / mo-group | `D:\1_Undergraduate_111111111111\group tasks\Agent集成\mo-group\在线工具-前后端接口文档.md` | TLC 前端交互、可视化、比较、反推与批量体验       |
| SFC Predictor v1.1    | `D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\SFC_Predictor_AS_IS_PRD_2026-09.md` | 五类色谱工具集中入口、智能助手                   |
| SFC Predictor v1.1    | `D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\SFC_Predictor_API_Spec_2026-09.md` | 工具契约、能力发现、错误表达                     |
| SFC Predictor v2.0    | `D:\Jupyter\SFC\deployments\sfc-predictor-v2.0\SFC_Predictor_AS_IS_PRD_2026-09.md` | 数据版本、训练实验、登记、评测、审批、发布、回滚 |
| SFC Predictor v2.0    | `D:\Jupyter\SFC\deployments\sfc-predictor-v2.0\SFC_Predictor_API_Spec_2026-09.md` | 异步任务与模型生命周期接口边界                   |

| 仅需求无代码           | 文档路径                                                     | 参考范围                                      |
| ---------------------- | ------------------------------------------------------------ | --------------------------------------------- |
| 万华                   | `D:\Jupyter\SFC\docs\business\wanhua_lc_contract_prd_2026-09.md` | 合同目标、架构、环境、安全和追溯要求          |
| 万华                   | `D:\Jupyter\SFC\docs\business\wanhua_lc_software_development_prd_2026-09.md` | 预测、批量任务、数据与模型包接入、模型管理    |
| 万华                   | `D:\Jupyter\SFC\docs\business\wanhua_contract_software_gap_20260825.md` | 当前底座与客户交付之间的差距                  |
| 万华                   | `D:\Jupyter\SFC\docs\business\万华合同-双方盖章版.md`        | 原始业务与合同约束                            |
| 齐鲁制药               | `D:\Jupyter\SFC\docs\business\齐鲁制药.pdf`                  | 合同中的数据、模型训练、软件和验收责任边界    |
| 齐鲁制药               | `D:\Jupyter\SFC\docs\business\色谱保留时间AI预测系统_前端产品需求文档PRD_V1.2.docx` | 手性 LC、部署入口和化学结构输入的前端需求     |
| 齐鲁制药（转换件）     | `D:\Jupyter\Chromatography_agent\docs\色谱保留时间AI预测系统_前端产品需求文档PRD_V1.2.md` | 前端 PRD 的 Markdown 检索副本；以原 DOCX 为准 |
| 齐鲁制药（文字提取件） | `D:\Jupyter\Chromatography_agent\docs\齐鲁制药_mineru\齐鲁制药.md` | 合同文字检索用副本；以原 PDF 为准             |

### 1.3 人员与权限语义

研究员关注预测与资料；方法开发人员关注条件比较和数据；模型工程人员关注模板实验与评测；部署管理员关注配置、制品与运维。以上是需求角色，不代表首版已经有多账号系统。

首版 `deployment_default` 模式仅适用于受控单主体部署：不能识别多个真实用户，也不能证明双人审批。不得公开暴露为多用户 SaaS。对模型发布等重要操作仍执行服务端策略、摘要确认和审计；有真实多人授权要求的交付必须先接入认证 Provider。

## 2. 技术架构与依赖方向

### 2.1 固定技术栈

| 层 | 首版选型 | 实施说明 |
|---|---|---|
| Web | Vue 3 + TypeScript + Vite + Pinia + Vue Router | UI 沿用原型风格；不以原型脚本作为业务实现 |
| HTTP API | Python 3.12 + FastAPI + Uvicorn + Pydantic v2 | 同步科学运算不堵塞 ASGI 事件循环 |
| ORM/迁移 | SQLAlchemy 2 + Alembic + MySQL 8.4 LTS | utf8mb4；UTC；迁移按构建装配 |
| Agent | PydanticAI | 仅在助手模块纳入时安装；具体版本锁定到 lockfile |
| Jobs | Dramatiq + Redis | 分队列/分进程；持久状态在 MySQL |
| 科学计算 | RDKit + 已审核模型/训练模板依赖 | 每个适配器独立依赖组，不全量安装所有 ML 库 |
| 文件 | LocalArtifactStore + SHA-256 + MySQL 元数据 | 工作进程共用受控制品卷，浏览器不接触路径 |
| 知识检索 | MySQL InnoDB FULLTEXT ngram | 索引是知识模块内部能力；检索策略版本化 |
| 可观测性 | OpenTelemetry SDK + 结构化脱敏日志 + 业务 Trace 表 | 默认不发云端；Collector 可选构建 |
| 前端测试 | Vitest + Vue Test Utils + Playwright | 类型/契约 fixture 与真实模型测试分离 |
| 后端测试 | pytest + API/数据库/队列集成测试 | 不用 SQLite 假装覆盖 MySQL 锁与全文检索行为 |
| 部署 | Nginx + Docker Compose | Linux 执行环境；Windows 通过合规 Linux VM/容器方案接入 |

所有 Python/前端依赖及容器镜像在首次实施时锁定精确版本与镜像摘要，生成离线材料清单。文档不冒充已经验证了一组尚未安装的具体依赖版本。禁止部署脚本使用浮动 `latest`；升级必须重新跑契约与集成测试。

### 2.2 逻辑关系

```text
Vue /api/v1
      │
      ▼
FastAPI 路由 → AuthProvider / ActorContext → Application Services
                                        ├─ Prediction / Structure
                                        ├─ Artifact / Data / Knowledge
                                        ├─ Experiment / Model Governance
                                        └─ Conversation / Agent Run
                                                     │
                                               Agent Worker
                                                     │
                                          PydanticAI RuntimeAdapter
                                                     │
                                            受控业务 ToolSets
                                                     │
                                           同一 Application Services

Application Services → MySQL（业务状态、事件、Outbox）
Outbox Dispatcher → Redis/Dramatiq → 各模块 Worker
Worker → 固定模型/固定训练模板/解析器 → 受控制品存储
```

页面和 Agent 都依赖同一业务服务。Agent 在同一 Python 部署内直接调用服务端口，不绕回 HTTP、不复制校验逻辑；未来 MCP 或远程调用只增加边界适配器。

### 2.3 Runtime 的职责

- PydanticAI：模型调用、工具调用循环、类型化工具、结构化输出。
- 自研应用层：ActorContext、工具授权、业务确认、会话/Run 持久化、任务提交、预算、恢复与 Trace。
- Dramatiq Worker：执行固定业务任务；不是用户代码沙箱，也不是持久工作流的完整替代品。
- 首版没有 Python Coding Runtime 实现，更没有通用 `execute(command)` 入口。

`RunContext.deps` 中的数据库/服务/权限对象不得自动串行化进提示词。不得让 PydanticAI 的类型直接成为公共 HTTP 返回 DTO 或数据库全局业务类型。

## 3. 构建裁剪与代码组织

### 3.1 构建规则

采用“最小通用底座 + 独立业务模块 + 色谱适配器 + 构建清单”。只采用构建裁剪，不以 `.env` 功能开关假装移除代码。

构建程序必须生成：前端导航/路由注册、后端路由注册、业务/ToolSet 注册、Pydantic 联合类型、模板清单、迁移集合、依赖锁定子集与 Compose 服务清单。未选模块的源代码、资源、schema、接口、迁移、Worker、依赖和模型资产不进入精简交付。

服务端能力发现只描述已构建能力、访问策略与健康；不能恢复未构建模块。未交付路由返回通用 404，不注册返回 501 的占位路由。

### 3.2 模块归属

| 模块/基础包 | 内容 | 依赖原则 |
|---|---|---|
| core | ID、ActorContext、错误、配置、权限策略、通用契约 | 不依赖任何可选业务模块 |
| structure_core | 规范化、立体化学、渲染、受控结构文件处理 | 所有交付必含 |
| artifact_core | 文件/制品、哈希、可用性、访问控制 | 结构处理所需最小子集必含 |
| task_core | Job、Attempt、事件、Outbox、取消 | 被任一异步模块使用时纳入 |
| prediction | 预测与各类型专用工具 | 只依赖已选 adapter 与最小模型运行时 |
| data | 记录、数据导入/版本、知识资料，各自可裁剪 | 管理页面不是底层服务的所有者 |
| training | 受控模板、实验、指标、训练 Worker | 数据版本 + task_core + artifact_core + 最小模型资产登记服务 |
| model_center | 登记、评测、审批、发布/回滚页面与服务 | 最小生产模型解析器不依赖其页面 |
| utilities | 必含结构入口 + 可选分子查找/TLC 反推 | 反推依赖 TLC 服务而不是 TLC 页面 |
| assistant | 会话、Agent、Tool 适配、Context Inspector | 工具集只装配已选业务组合 |
| settings | 只呈现已构建设置/运维项 | 删除页面不删除必要启动配置 |

训练产物所需的最小模型资产登记服务属于可复用基础能力；裁掉“模型中心页面”不得导致训练结果无法保存。是否交付发布治理子能力由清单显式声明。

### 3.3 构建清单最小格式（本版冻结）

后端模块 ID 与一级导航不一一对应：`records/datasets/knowledge` 三个模块装配到同一个“数据与知识中心”容器。基础服务可以被业务依赖纳入，但不自动带入其管理页面/完整管理API。

```json
{
  "manifest_version": "1.0",
  "build_id": "universal-v1",
  "modules": ["prediction", "records", "datasets", "knowledge", "training", "model_center", "utilities", "assistant", "settings"],
  "chromatography_types": ["tlc", "cc", "hplc", "sfc", "gc"],
  "prediction_tools": {
    "tlc": ["single", "batch", "condition_compare", "reverse_solvent"],
    "cc": ["single", "batch"],
    "hplc": ["single", "batch"],
    "sfc": ["single_column", "full_column", "batch"],
    "gc": ["single", "batch"]
  },
  "utilities": ["structure_converter", "molecule_lookup", "tlc_reverse_solvent"],
  "assistant_features": ["knowledge", "business", "trace"],
  "export_formats": ["json", "csv", "xlsx"],
  "auth_provider": "deployment_default"
}
```

构建器解析传递依赖、生成模块源码包和迁移；不使用JSON中的值在运行时import已裁掉的模块。`structure_converter`及结构核心隐式必含；无utilities可选功能时仍生成其最小导航容器。

至少生成四个profile：`universal-v1`（上例）；`sfc-v1`（仅SFC预测/批量、记录和结构工具）；`cc-v1`（仅CC预测/批量、记录和结构工具）；`minimal-structure-v1`（仅结构核心/结构入口及必要文件服务）。可以在主仓库维护更多清单，但交付代码与依赖必须实裁。

未纳入assistant则无assistant_features和LLM配置/会话专属依赖；未纳入knowledge则不注册knowledge模式；未纳入TLC则不能选择tlc_reverse_solvent。非法组合构建失败，不静默把所有模型/业务页面都带入包。

最小模型资产、生产解析、预测快照、数据版本等依赖服务不由其管理页面所有。基础资源仍可返回ID，但当相应管理页/路由未纳入时，前端只显示可访问的摘要，不生成无法访问的跳转。

### 3.4 推荐目录

```text
backend/src/chrom_platform/
  core/                    # 通用协议、错误、访问上下文
  infrastructure/          # DB、Dramatiq、文件、OTel 适配
  assembly/                # 由构建生成的注册入口
  structure_core/
  artifact_core/
  task_core/
  model_runtime/           # 最小模型解析器与加载协议
  modules/
    prediction/
    records/
    datasets/
    knowledge/
    training/
    model_center/
    utilities/
    assistant/
      domain/              # Conversation、AgentRun、ToolCall
      application/         # RunService、Confirmation 适配
      runtime/             # PydanticAI adapter、context policy
      tools/               # 组合装配的业务工具
      api/
    settings/
  adapters/                # TLC/CC/HPLC/SFC/GC/chiral_lc
frontend/src/
  app/                     # 应用壳、注册入口
  shared/                  # API 客户端、错误、公共组件
  modules/                 # 各模块独立路由/页面/store/api
contracts/                 # 冻结的 OpenAPI、fixture、变更记录
builds/                    # 构建清单
```

首版禁止出现为了“预留”而被启动或打包的 `sandbox_worker`、`coding_workspace`、`algorithm_packages` 等实现。未来契约放升级文档；首版只实现已被当前能力使用的通用端口。

## 4. 前端导航与工作区

### 4.1 一级导航

保持：色谱预测 → 数据与知识中心 → 模型训练 → 模型中心 → 实用工具 → 色谱智能助手。只显示已构建模块；助手固定为最后一个业务导航。系统设置固定左下角，不与业务导航混排。

色谱预测二级为 TLC / CC / HPLC / SFC / GC 及可选独立手性 LC；三级为对应类型的单次、批量和特有工具。手性 LC 不作为 HPLC 的同一请求表单伪装实现。

数据与知识中心二级固定顺序：预测记录、色谱数据、知识中心。版本、导出、索引任务归属工作区内部，不再抬升为一级导航。

### 4.2 首版助手界面

采用可折叠会话侧栏 + 对话主区 + 可展开的任务/调用详情区。首版全功能构建支持 `knowledge` 与 `business` 两种模式；具体交付只注册依赖已纳入的模式。`knowledge` 模式依赖知识检索子能力。模式只改变工具可见集合，不能代替服务端权限控制。

详情区呈现：关联业务对象、工具调用、等待确认卡片、Agent Run 时间线、LLM 请求详情。Context Inspector 是受限调试能力，不向所有使用者展示。

不出现“算法研发”“代码编辑”“Workspace”“Sandbox 已运行”等可点击占位能力。未来页签由功能模块构建时注册，不把所有未来页面预先打包再隐藏。

### 4.3 前端数据对象

将 `Conversation`、`Message`、`AgentRun`、`ToolCall`、`Job`、`Confirmation`、`Artifact`、`ContextSnapshot` 分开。业务任务以稳定 ID 关联，不能只存在聊天字符串或 Pinia 内存里。

每个长任务都具备排队/运行/失败/取消/超时/中断等真实状态；不能页面刷新就重提任务。Chat 中点“停止回答”只停止 Agent Run，不隐式取消已确认的训练/批量任务。

## 5. 化学结构公共核心

实现 SMILES 校验、规范化且保留立体信息、二维 SVG/PNG 渲染、Ketcher 绘制输入，以及受控 CDX/CDXML/MOL/SDF 解析。结构对象以 `structure_id` + schema 版本 + 原始输入快照 + 标准化结果哈希保存。

所有预测和数据模块复用结构对象，不在表单与助手中分别实现规范化规则。不允许静默移除 `@/@@`、双键立体信息、盐/电荷或改变输入化学意义。

Ketcher 导出的结构仍需后端校验；浏览器“看起来正常”不构成化学有效性证明。CDX/CDXML 的解析器和离线依赖必须经过样例验证；格式转换是受控解析，不调用用户提供的脚本。解析器缺失时清晰返回不可用，不虚假声称转换成功。

“单条手性结构生成对映体”只在适配器声明支持的情形下进行，提供预览、警告和人工确认。多个立体中心、未指定中心或不支持的立体形式不允许随意反转其中一个中心充当对映体。

结构核心在全部精简版本中保留，“实用工具”至少保留其最小入口。

## 6. 色谱预测与记录

### 6.1 共用流程

选择类型与工具 → 读取能力/schema → 输入结构与专用条件 → 服务端验证 → 创建预测 Job → 查询结果 → 保存记录和导出。

单次预测也统一采用异步 `202` 契约，避免前端因模型耗时不同实现两套流程。创建后自动持久化预测记录；“保存”改为标记/归档交互，不再存在结果未保存却显示成功的分叉。

所有响应显示实际解析到的模型 ID/版本、模型制品哈希、输入快照、schema 版本、状态、warning 与错误。不返回不存在的置信区间、模拟色谱图或置信度。可视化依据模型能力，预测曲线与实验曲线明确区分。

### 6.2 类型边界

| 类型 | 保留业务意义 | 不允许的简化 |
|---|---|---|
| TLC | Rf、条件比较、支持体系内反推 | 将石油醚自动视为正己烷；虚构模型不支持的体系 |
| CC | 上样、柱规格、流动相、流速、体积/时间 | 固定所有模型同一柱或把原型数值当结果 |
| HPLC | 对映体配对、候选手性柱、比例/流速 | 与单条手性 LC 请求混用 |
| chiral_lc | 独立单条含立体信息结构契约 | 静默转成 HPLC 双结构输入 |
| SFC | 对映体、Modifier、候选条件、Top-k | 无上限笛卡尔积或无授权全库扫描 |
| GC | 分子与模型支持的温度程序 | 前端自行补充模型未支持的柱、载气、检测器 |

HPLC 单次预测采用 schema 1.1：`structure_refs` 是恰好两个有序项目内结构引用，`input.model_input_smiles` 是两条本次提交的原始字符串，两者逐槽对应并绑定；请求只接受 `model_selection.mode="exact"`，并要求能力返回的精确 model/profile id 与 SHA-256。SMILES 保持本次提交的原字符串，canonical/isomeric 结构只用于身份、立体化学与 CIP 校验。旧 HPLC schema 1.0 记录只读展示与导出，不从历史结构记录补造 raw，也不允许 replay、retry 或重新执行。HPLC 当前输入表示可能影响结果；训练端原始表示/缓存 provenance 尚未确认，结果持续显示该限制及实验确认、无独立泛化证据 warnings。缺 raw 的既有 HPLC batch 模板保持 UNAVAILABLE。

GC 单次预测采用 schema 1.1，要求能力声明的精确 model/profile id 与 SHA-256；服务端绑定项目内结构并冻结规范化立体结构，Worker 使用该快照执行。旧 GC schema 1.0 记录仅支持只读展示与导出，不自动补造 profile，也不允许 replay、retry 或重新执行。温度序列长度与保持时间步长来自模型能力；当前参考模型声明 30 点及整数分钟保持时间。静态契约不代表模型已验证或可用；验证通过前仍显示 UNAVAILABLE。

CC 的单位派生仍为 `time_seconds = volume_ml * 60 / flow_rate_ml_min`，不改变原模型体积预测。各模型实质输入/输出范围必须由其版本化 Adapter Manifest 与验证过的 schema 冻结，不能从原型图推断。

### 6.3 批量

每类色谱独立模板与 schema，CSV/XLSX 上传、字段映射、预览、逐行校验、确认行数/模型/组合/预算后异步执行。有效行、无效行、跳过行和失败行明确区分。

允许“只提交有效行”，但必须显示并确认排除数量；不可在用户不知情时静默跳过。重试失败行生成新 Attempt，保留原结果与行级关联，不重复覆盖成功行。

本轮执行模板为 TLC v2、CC v2、HPLC v2、SFC v2、GC v1。CC 每行包含两个不同结构引用；HPLC 每行保留两条有序原始 SMILES，服务端规范化后冻结结构引用与原文身份；GC 使用 schema 1.1 的精确模型/预处理版本；SFC 显式指定单柱或全柱工具，整批只绑定一个实际兼容的精确模型。结构引用模板须提供项目内结构准备或选择入口。旧 TLC/CC/HPLC/SFC v1 模板仅保留历史读取及支持的导出，不能补造输入后重新执行。

业务计算量按 TLC/GC 每行 1、CC 每行 2、HPLC 每行实际柱数、SFC 每行实际评分候选数计算；不能用 Top-k 返回条数代替评分预算。失败行重试使用同一冻结计算量。全行无效或超预算不可提交，未执行行以真实跳过状态显示。每个实际执行的逻辑有效行自动生成一条稳定预测记录；重试更新同一记录并保留行级 Attempt 历史，成功行不重建记录，归档不丢失。提交前排除与未执行取消不生成记录，实际执行后取消保留 CANCELED 记录。

TLC 条件比较和小规模反推使用受限预测任务；超出能力公布的候选上限应转入有确认的批量流程，不无限自动扫描。TLC 反推两个页面入口调用同一业务服务。

### 6.4 预测记录与导出

记录支持项目、类型、模型、主体、时间、状态、来源过滤、游标分页、详情和带回表单。归档/软删除需要明确确认；永久删除受保留与引用约束。

首版通用构建交付 CSV、JSON、XLSX；PDF、PNG、组合 ZIP 通过独立导出子能力构建。PDF/图表产物只在实际渲染依赖已交付时列为可用。导出绑定输入、结果、模型/数据版本、格式版本与生成时间；表格导出处理公式注入。

## 7. 数据与知识中心

### 7.1 色谱数据

使用统一分子身份关联多类色谱实验数据，但项目、来源、权限与用途独立，不因相同结构自动共享客户数据。逻辑数据域映射由服务端控制，不暴露任意数据库连接、表名或 SQL。

数据包经过文件清单、SHA-256、编码、schema、字段、单位、结构、重复 ID 与关系校验；显示差异后确认提交。提交产生不可变数据版本；被训练、评测或正式结果引用的版本不能被覆盖。

`DatasetSchema`/`DataDomainManifest` 明确标签、单位、实验条件字段、主键/去重策略。未提供真实数据规范时，先完成框架与测试 fixture；不得自动猜测客户的标签和单位。

### 7.2 知识资料与首版检索

资料类型包含 SOP、论文、方法说明、内部经验与模型说明，与训练数据分开管理。

首版解析 TXT、Markdown、文本型 PDF、DOCX；扫描 PDF 无可用文本时返回 `NEEDS_OCR` 状态/原因，本版不悄悄用外部 OCR。默认不批量解析 ZIP 中的任意未知文件。页码仅来自真实分页来源；DOCX/Markdown 使用段落、标题或行定位，不能杜撰 PDF 页码。

解析、切片、索引通过知识 Worker。首版以 MySQL FULLTEXT ngram 建立本地关键词索引，默认按段落/标题切分，目标 1,200 字符、最大 2,000 字符、重叠 150 字符；这是字符数，不伪称 token 数。策略 ID 与版本保留，重新索引生成新版本，切换成功后才替换可检索版本。

检索必须将项目/权限/资料状态条件纳入数据库查询；读取引用时再次校验。返回 `citation_id`、文档/版本、切片 ID、定位与内容摘要。分数仅表示当前检索算法的相关性，不是答案正确概率。

问答使用检索结果生成答案并展示可核对引用。无证据时明确说未检索到资料，允许给出标为一般知识的说明，不冒充来自企业知识库。不把文档中的指令提升成系统指令。

## 8. 模板化模型训练

### 8.1 首版仅允许预注册模板

训练配置选择：任务/模板版本、不可变数据版本、特征版本、标签、划分策略、模板允许的超参数、评测协议、随机种子和资源预算。

`TrainingTemplate` 是部署时由可信开发者审核并纳入构建的 Python 实现，禁止从 HTTP 参数或上传包动态导入任意类/模块。请求不得包含脚本、命令、服务器路径、自由依赖清单或任意 entrypoint。

模板列表必须来自真实登记；原型中的 `sfc-separation-baseline-v3` 只是例子，不是已存在模型资产。未提供某领域真实模板时，其能力显示不可用，不能拿通用示例算法假装已满足客户科学验收。

### 8.2 实验流程与状态

```text
DRAFT → VALIDATING → READY → QUEUED → RUNNING → SUCCEEDED
             └→ INVALID                ├→ FAILED
                                       ├→ CANCEL_REQUESTED → CANCELED
                                       ├→ TIMED_OUT
                                       └→ INTERRUPTED
```

确认记录是独立对象，不再增加一个与 READY 混淆的“已确认”实验状态。修改草稿使验证/确认失效；提交绑定配置 revision/hash、数据/模板/特征/协议版本与预算。

成功训练不等于候选模型已经通过验证。实验使用独立 `candidate_status = NOT_REQUESTED / VALIDATING / CREATED / REJECTED` 和 `candidate_model_id`，代替把 `CANDIDATE_CREATED` 混成训练执行终态。训练失败、取消或超时不得创建 Candidate。

成功产物经模型登记校验后才能成为 Candidate；生产发布仍在模型中心独立执行。

### 8.3 执行与恢复

训练在独立 Training Worker 的固定模板子进程中执行。HTTP/聊天不承载训练；硬超时和取消由受控进程监督处理，不只依赖队列库的软超时。

记录数据、代码版本、依赖锁、模板、特征、配置、split manifest、指标和产物哈希。模板支持 checkpoint 时明确声明格式、兼容性和恢复点；不支持时中断任务标记 `INTERRUPTED`，用户受控重试从头训练，不宣称任意任务都能断点续训。

## 9. 模型中心与发布治理

### 9.1 入口与验证

接收可信模板产物和受控外部模型包。校验清单/哈希、manifest/schema、任务/scope、依赖/运行环境、允许的序列化格式、黄金样例、数值有效性和独立评测协议。

**首版不允许“上传 Python 代码并运行以验证模型”。** 禁止加载不可信 pickle/joblib、自定义导入模块或任意模型启动命令。外部包只能引用已构建、已审核的 Loader/模型实现及其数据型权重格式；不符合者拒绝，不以未来 Sandbox 尚未实现为由在 API 进程中尝试执行。

黄金样例用于功能回归，独立评测用于泛化；不能用训练集样例通过来声称模型泛化良好。具体科学指标由任务协议定义，不把 AUROC/MAE/RMSE 强行推广到所有色谱任务。

### 9.2 三个维度

- 资产生命周期：`VALIDATING / CANDIDATE / APPROVED / REJECTED / RETIRED`。
- 部署状态：`NOT_DEPLOYED / STAGING / PRODUCTION`，按部署资源而非模型单字段猜测。
- 比较角色：`CHALLENGER / CHAMPION / null`，绑定项目、任务与 scope。

一个模型可能在不同 scope 有不同部署/角色，查询必须带 scope 或返回 `deployments[]`，不能只用全局 boolean。

### 9.3 发布/回滚

评测与审批、发布草稿、配置确认、异步发布、回滚分开。发布必须同时校验当前生产指针版本、目标已审批模型、适用 scope、可用制品与回滚目标。

先预加载/健康检查，再事务性切换生产模型指针与 Champion 关系，并记录不可变发布事件；失败保持旧版本。请求重试不能再次发起另一笔部署。回滚明确目标历史版本，不覆盖旧文件。

首版助手最多查询评测、准备审批申请并跳转页面；工具清单中不包含批准、发布和回滚动作。

## 10. 色谱智能助手：PydanticAI 首版实现

### 10.1 对外与内部边界

首版一个 `ChromatographyAssistant`，不先做 Supervisor/多 Agent。业务工具按构建与权限组合装配：结构/分子工具、知识工具、预测工具、数据摘要、训练草稿/查询、模型登记草稿/查询。

`AgentRuntimeAdapter` 是平台端口，PydanticAI 是唯一首版实现。Provider 配置、HTTP 客户端、工具映射、消息序列化与框架 hook 放在 adapter 内；业务服务不 import PydanticAI。

首版只承诺经集成测试的 OpenAI-compatible Chat Completions 接口形态，通过配置连接批准的本地/内网或外部 Provider。协议兼容不等于功能完全兼容；必须分别测试工具调用、流式、结构化输出和超时行为。其他 Provider 适配后续增加，不影响公共会话接口。

### 10.2 会话与 Run

会话管理保留归档这一独立管理状态，并支持真正删除。删除前须针对目标会话人工确认，并绑定确认时的当前 revision/ETag；版本过期时拒绝操作，同一授权操作按幂等语义处理。删除从应用管理的持久化存储中清除聊天消息和助手私有历史/上下文副本，不能仅隐藏或改成 ARCHIVED；预测记录、已提交业务任务、共享制品及必要且不含原文的业务审计保留。存在非终态 Run/Attempt 或该会话自有 assistant Job 时拒绝删除，提示先停止回答并等待终态，不自动取消独立预测。删除后应用内列表、搜索、归档读取及消息/历史读取不得恢复原文。该要求不承诺备份或磁盘层物理擦除，也不保证外部 Provider 或日志副本擦除。截至2026-10-20，受控 synthetic 会话删除取得SQLite、单一真实API、BE owner MySQL及指定Chrome交互路径的分层 LIMITED_PASS；不代表全V1、科学质量或Provider验收完成。证据与未覆盖边界见[测试状态](../testing/STATUS.md)和[专项目报告](../testing/runs/2026-10-20-conversation-delete/acceptance-report.md)。

前端只提交用户文本、附件 ID、客户端消息去重 ID，不提交系统提示词、完整消息历史、工具执行结果、角色或权限。

服务端持久化会话/消息，创建 `AgentRun` 与 Outbox 后返回 202。Agent Worker 读取可信历史执行模型循环；同一会话同时只允许一个非终态 Run。等待确认也占用该位置，用户可拒绝或取消后继续，不允许两个 Agent 修改同一草稿而相互覆盖。

Run 每次恢复使用新的内部 attempt/framework run ID，但保持同一平台 Agent Run；所有 LLM Call、ToolCall 都保留不可变记录。传输层 SSE 重连不创建新 Run。

### 10.3 工具执行与确认

读操作和低成本已授权预测可以直接执行；批量提交、训练、数据导入提交、模型登记等需确认。

工具先创建真实业务草稿与摘要，再将 Run 置为 `WAITING_CONFIRMATION`。前端调用统一确认接口，后端验证权限、资源 revision/hash 和操作内容；Run resume 只接受服务端确认 ID，不能接受客户端传回的“已批准工具结果”。

PydanticAI 的 deferred tools 可以用于挂起/继续，但不作为唯一授权机制；业务服务本身每次执行仍校验。Agent 不持有 `confirmation:create`、模型批准/发布、系统配置等高权限工具。

文字“确认”“好的”本身不直接触发状态改变：首版显示具体版本的确认卡片，用户点击后才创建 Confirmation。

### 10.4 输出

对话消息使用文本、预测结果卡、资料引用卡、任务卡、确认卡、告警卡等结构化 blocks；不接收模型生成的可执行 HTML/JS。Markdown 必须过滤危险链接/HTML。

数值只能来自真实工具结果；展示来源工具、模型、版本、warning 和引用。模型的计划摘要可以展示，但不是隐含推理链。不展示模型隐藏思维链或敏感恢复数据。

### 10.5 预算与失败

每 Run 默认最多 12 次 LLM 请求、24 次工具调用、600 秒累计执行预算；单次模型请求 90 秒，输出上限 4,096 tokens。等待人工确认不计执行时间，但确认最长等待 30 分钟。实际更低限制由部署/Provider 限额取最小值。

LLM 网络重试集中管理，默认最多 2 次，不与多层 SDK/队列重试相乘。工具执行使用业务幂等账本，不以“重跑整个 Agent”处理已产生副作用的失败。

模型超时/上下文超限/工具失败有明确可恢复或终态原因。供应商不返回 usage 时写 `null`，不伪造 token 数和费用。

## 11. 通用任务与可靠性

### 11.1 状态与队列

Job 状态：`QUEUED / RUNNING / CANCEL_REQUESTED / SUCCEEDED / PARTIAL / FAILED / CANCELED / TIMED_OUT / INTERRUPTED`。业务草稿状态不混入 Job；`PARTIAL` 只用于允许部分成功的任务。

队列按构建装配为 `agent`、`prediction`、`batch`、`training`、`knowledge`、`artifact`、`model_validation`、`exports`、`maintenance`。无 `sandbox` 队列。

任务参数只包含 ID、配置版本和受控执行类型，不带 Cookie、API Key、大文件正文或任意函数/命令。Worker 根据固定注册表选择 handler。

### 11.2 Outbox、租约和幂等

同一数据库事务写业务状态、Job、审计事件与 Outbox；Dispatcher 发送消息成功后标记。发送/确认间宕机可能重复投递，因此 Worker 获取 DB 租约并检查任务终态、attempt/fencing token。

首版所有业务Dramatiq actor显式配置 `max_retries=0`，不采用库默认自动重跑整个业务。捕获业务失败后落库，由Job服务/恢复协调器按handler策略决定新的Attempt；消息重投仍可能发生，必须幂等。LLM的有限网络重试在模型adapter内另计预算。Outbox发送失败保持待发并按有界退避重试，不丢弃业务事实。

首版目标是至少一次投递 + 业务幂等，不声称 exactly-once。最终产物通过临时文件、校验和原子发布记录落地；孤儿文件可清理，不能在 DB 失败时声称已保存。

每个运行 Worker 心跳与租约定期更新；恢复协调器检查过期租约、进程/任务实际状态、产物和副作用账本。不能仅因 Redis key 过期就盲目重复模型发布/数据提交。

### 11.3 取消与恢复

取消请求进入 `CANCEL_REQUESTED`；只有实际停止或确认尚未开始才进入 `CANCELED`。完成与取消竞争时，以服务端原子终态提交为准，不能同时显示成功和已取消。

恢复包括重新查询、幂等重投、安全阶段重跑；不等同于任意 Python 栈恢复。固定模板的重试、新 Attempt 与 checkpoint 支持情况必须在接口中明确。

## 12. Confirmation 与权限策略

Confirmation 绑定：项目、主体、资源类型/ID、revision、配置哈希、动作、选中数据/模型/行集合、预算、策略版本、创建/到期时间及消费结果。

状态：`CONFIRMED / CONSUMED / EXPIRED / INVALIDATED / REVOKED`。审批和确认不同：确认表达操作者接受本次动作，审批表达模型治理准入。

更新配置、重新解析模型别名、修改数据、预算、scope 或目标版本都使旧确认失效。消费与创建目标 Job/业务事件在同一事务完成。相同幂等键重试返回同一提交结果。

权限由服务器生成的 `ActorContext` 和策略判定，不信任 JSON 中的 `user_id/tenant_id/permissions/source`。未来认证使用 Cookie/Bearer；首版禁止把一个客户端自报管理员字段当作“预留接口”。

## 13. Trace 与 Context Inspector

### 13.1 首版交付

以 Run/Attempt 组织：LLM 请求、工具提议/校验/执行、工具结果、等待确认、业务 Job 关联、错误与状态转换。OpenTelemetry 用于关联，不代替应用持久事件。

每次模型请求记录实际 Provider 配置版本、model_id、最终请求消息/工具 schema 的**脱敏快照**、模型参数、输入来源、裁剪信息、结果摘要、usage、耗时与错误。

定义两个视图：

- `logical_context`：完成工具筛选、历史裁剪、知识注入后的应用模型输入。
- `provider_request`：在最后序列化边界捕获的实际 Provider 请求体，脱敏后保存。首版支持的 Chat Completions adapter 应实现；不支持的 adapter 明确 `NOT_CAPTURED`，不能用逻辑输入假称完整线上请求。

不记录认证头、API Key、Cookie、模型隐藏推理或客户端不可见的内部密钥。用于恢复的框架状态可能含不透明厂商字段，只能加密内部保存，不能由 Context API 返回。

### 13.2 安全与保留

默认元数据追踪；内容快照默认关闭，开发环境由受控部署配置选择 `redacted`，生产需经数据策略允许。开关只控制已构建 Trace 的采集级别，不恢复被裁掉的模块。

快照以 artifact 元数据管理，但通用下载接口必须识别敏感 artifact kind 并要求 `trace:content:read`；不能绕过 Trace API 权限下载。读取、导出、清理均审计。

默认事件/元数据保留 90 天，脱敏内容 7 天，审计 365 天；正式客户策略可覆盖，覆盖不得悄悄破坏已承诺保留要求。历史 content 已到期仍返回元数据及 `EXPIRED`，不返回空字符串伪装完整内容。

Context Diff 比较两个实际请求快照，只提供应用输入差异；token 分项标明估算算法/版本，usage 总数才是 Provider 实报。长内容可截断但必须标记并提供受控完整脱敏内容查询。

## 14. 通用文件、安全与外部发送

文件有稳定 ID、项目/主体、类型、SHA-256、大小、状态、扫描/解析状态、来源、保留期限与引用。上传先进入隔离区，完成后服务端计算哈希、内容校验，再按策略释放。

首版不将“未配置恶意文件扫描”显示为“扫描通过”。`scan_status=NOT_CONFIGURED` 时按项目策略拒绝或人工受控接受，并记录 warning；强制扫描构建必须真实交付扫描能力。

禁止路径穿越、压缩炸弹、压缩包绝对路径/符号链接、超大/深度递归文件、扩展名伪装与表格公式执行。模型包/数据包失败不创建半完成业务对象。

默认外部网络关闭。LLM、分子外查分别有批准的 endpoint allowlist 与数据发送策略；禁止用户/模型指定任意 URL，避免 SSRF。内网地址可以由管理员显式批准，但不能自动访问所有内网/云元数据地址。

API 密钥写入受控密钥存储，数据库保存引用或密文；解密根密钥不与数据库备份一起明文保存。前端只见 `has_api_key` 等元数据，不见完整 Key；日志与诊断包不泄露配置秘密。

## 15. 数据持久化与首版扩展点

### 15.1 首版需要的对象

| 范畴 | 主要对象 |
|---|---|
| 基础 | project、artifact/upload、structure、audit_event、confirmation |
| 任务 | job、job_attempt、job_event、outbox_event、idempotency_record、execution_ledger |
| 预测 | prediction、prediction_record、batch_draft、batch_row、export |
| 数据 | molecule、experiment_record、data_import、data_version、feature_version |
| 知识 | knowledge_document、document_version、knowledge_chunk、index_run、citation_snapshot |
| 训练/模型 | training_template、experiment、experiment_metric、model_registration、model、evaluation_protocol、evaluation、approval_request、deployment_draft、deployment、production_pointer |
| 助手 | conversation、message、agent_run、agent_attempt、agent_event、llm_call、tool_call、context_snapshot |
| 配置 | llm_configuration、llm_configuration_revision、llm_test_result、runtime_policy |

可选模块的专属对象与迁移随模块裁剪。底层 JSON 只用于版本化配置/事件/结果快照，不用一个万能 JSON 表替代所有关系和状态约束。

### 15.2 首版真正要保留的端口

`AuthProvider`、`AuthorizationPolicy`、`ArtifactStore`、`JobQueue`、`ConfirmationService`、`PredictionAdapter`、`KnowledgeRetriever`、`TrainingExecutor`、`AgentRuntimeAdapter`、`ContextCapturePort`。

资源关联使用受控 `ResourceRef {type,id}`，服务端按已构建白名单验证并校验访问权；不允许客户端任意扩展类型。后续增加 Coding 类型通过模块注册/版本升级完成。

首版 `ExperimentSpec.execution.kind` 仅 `template`，`Conversation.mode` 仅 `knowledge | business`。不要提前接受 `code_artifact`/`coding` 再返回“不支持”。

## 16. 部署、端口与运行参数

### 16.1 唯一新架构

FastAPI `API_PORT=9012`；开发前端默认 `5173`；Nginx Web 入口默认 `8080`，均可通过项目配置更改且启动时检测冲突。浏览器应用只写 `/api/v1`，由 Vite/Nginx代理。开发 API 客户端可以直接访问配置后的 FastAPI 地址，不把“禁止浏览器访问旧推理服务”误写为无法调试业务 API。

推理默认由 Worker 直接调用注册适配器；没有必需的旧 HTTP 推理网关。以后确需独立推理进程时，由内部端口单独配置，不能复用同一监听地址/9012。

### 16.2 交付

实施口径修订日期：2026-10-13（仅更新本节验收路径与部署边界；PRD v0.12 产品范围保持不变）。

Compose 按构建包含 Nginx、API、MySQL、Redis、Outbox/恢复协调器和必要 Worker。首版 API、Agent Worker、Training Worker 均不挂 Docker socket，不具备创建任意容器能力。

本阶段按用户批准先在 Windows 本地验收 API 与 Worker，使用现有 MySQL/Redis；不要求 Linux API/Worker 容器，也不据此宣称 Windows 与 Linux 部署等价。真实业务验收仍须检查 Worker、持久化结果、恢复与科学边界，不能仅凭前端/API启动签收。Linux VM/容器离线部署属于后续目标，须在另行批准的部署阶段验证；未批准的 Linux 容器、镜像或相应依赖不得作为本阶段前置或擅自启用。Conda/conda-pack 的说明仅适用于其实际覆盖的固定服务，不能推出未来沙箱或进程隔离等价。

### 16.3 默认运行参数（本版新决定）

| 参数 | 默认值 | 约束 |
|---|---:|---|
| API_PORT / FRONTEND_PORT / WEB_PORT | 9012 / 5173 / 8080 | 均来自项目配置 |
| 普通分页 | 50；最大 200 | Cursor，不默认昂贵 total |
| 消息正文 | 最大 20,000 字符 | 超限 413；附件走 artifact |
| 单文件上传 | 100 MiB；模型/数据 2 GiB | 后端和 Nginx 同步限制，可更低 |
| 批量行数 | 默认最大 10,000 | 各能力可更低，不是科学模型承诺 |
| 每批展开总计算量 | 最大 100,000 | 行数 × 条件组合，提交前确认 |
| 单次条件扫描 | 最大 200 候选 | 超出转批量 |
| Confirmation TTL | 30 分钟 | 修改配置立即失效 |
| Idempotency-Key 保留 | 24 小时 | 业务唯一约束长期保留 |
| Job 心跳 / 租约 | 15 秒 / 60 秒 | 租约过期先协调，不立即重跑 |
| SSE heartbeat / 重连 | 15 秒 / 3 秒 | 默认传输降级轮询 2 秒 |
| SSE 可重放事件 | 7 天 | 不等于所有审计仅留 7 天 |

部署级资源值不等于业务模块开关；构建裁剪仍由构建清单决定。

### 16.4 .env 与秘密配置边界

Codex生成`.env.example`，至少定义下列键；实际连接/路径由部署填写。不得自动修改全局环境变量，也不得把整份.env传给前端或模型。

```dotenv
API_HOST=0.0.0.0
API_PORT=9012
FRONTEND_HOST=127.0.0.1
FRONTEND_PORT=5173
WEB_PORT=8080
DATABASE_URL=mysql+asyncmy://APP_USER:REPLACE_ME@mysql:3306/chrom_platform
REDIS_URL=redis://redis:6379/0
ARTIFACT_ROOT=/var/lib/chrom-platform/artifacts
SECRET_KEY_FILE=/run/secrets/chrom-platform-key
TRACE_CONTENT_MODE=off
TRACE_CONTENT_RETENTION_DAYS=7
TRACE_METADATA_RETENTION_DAYS=90
AUDIT_RETENTION_DAYS=365
OTEL_EXPORTER_OTLP_ENDPOINT=
```

上例秘密为占位而非可用密码。MySQL/Redis账户与网络按进程职责配置；生产Redis按实际ACL/密码要求补齐连接认证，不公开映射其端口。私密配置不进入Vite变量。OTel endpoint留空表示不向任何collector/云端发送；如配置，必须为批准的内部目标。

`ARTIFACT_ROOT`/`SECRET_KEY_FILE`只供可信服务内部解析，不能出现在业务响应；模型资产同样由服务端注册表解析。构建身份来自产物内的不可变build manifest，不由.env选择模块。

推荐SQLAlchemy AsyncSession + asyncmy；Dramatiq进程通过明确异步执行桥接/进程生命周期复用客户端，每个任务独立session/事务，不跨线程复用同一AsyncSession，不把阻塞科学计算放入ASGI事件循环。

## 17. 实施顺序与完成定义

| 里程碑 | Codex 必交付 | 完成标准 |
|---|---|---|
| M0 契约/构建 | DTO、OpenAPI、模块依赖清单、生成前端类型、测试 fixtures | full/sfc/cc/minimal 构建无残留依赖，旧接口无注册 |
| M1 公共底座 | ActorContext、文件、结构、任务/Outbox、确认、审计 | 真实 DB/队列持久化、幂等/并发/隔离测试 |
| M2 预测/数据 | 各类表单/适配器、批量、记录/导出、数据版本 | 真实模型资产验收与 fixture 验收分开 |
| M3 知识/训练/治理 | 知识索引、模板实验、模型登记/评测/发布 | 缺资产不造假；完整状态与审批闭环 |
| M4 助手 | PydanticAI adapter、ToolSets、会话/Run、SSE、确认恢复 | 不执行任意代码；同服务校验一致 |
| M5 观测/离线交付 | Context Inspector、恢复演练、离线安装、诊断 | 脱敏、权限、重启恢复与最小包验收 |

允许垂直切片实施，但不能只把所有页面点亮而业务无效。每个里程碑报告已实现、通过测试、未通过与外部阻塞；不得把未交付模型或客户模板写成完成。

## 18. 首版验收清单

| ID | 场景与必须满足的结果 |
|---|---|
| V1-01 | 空环境按文档安装并运行 full/minimal 两种构建，精简源码可独立构建测试 |
| V1-02 | 删除助手/训练/某色谱能力后，前端资源、路由、OpenAPI、依赖、迁移和 Worker 无残留 |
| V1-03 | 旧端口/旧路由不作为依赖、代理或兼容入口；仅新 `/api/v1` 契约 |
| V1-04 | 所有构建保留结构输入、立体信息、渲染与复制；无静默丢失 |
| V1-05 | HPLC 双对映体与独立 chiral_lc 单结构 schema 不混用 |
| V1-05a | HPLC schema 1.1 保存有序双 raw SMILES 与 exact model/profile pair；旧 schema 1.0 仅历史只读 |
| V1-06 | 同一预测从页面和 Agent 提交，得到相同校验、模型解析与结果语义 |
| V1-07 | 模型/解析器缺失返回明确不可用，生产接口不出现演示预测 |
| V1-08 | 批量逐行错误、有效行选择、组合上限、确认摘要与结果关联正确 |
| V1-09 | 记录自动持久化；刷新与 DB 故障不产生“已保存”假状态 |
| V1-10 | 数据更新不改变已引用的数据/特征/评测版本 |
| V1-11 | 知识资料归档/越权后不可被检索或绕过引用接口读取 |
| V1-12 | PDF 页码/DOCX 段落定位真实；无资料时不生成伪引用 |
| V1-13 | 训练只能选择构建内模板；脚本、命令、entrypoint、动态依赖均被拒绝 |
| V1-14 | 训练失败、取消、超时不生成 Candidate；成功产物需通过登记验证 |
| V1-15 | 不可信模型包不在 API/Agent 进程反序列化执行 |
| V1-16 | 生命周期、部署、Champion 三维独立；发布指针切换原子且可回滚 |
| V1-17 | 修改 revision/预算/模型/数据后旧确认失效，重复确认/提交不重复任务 |
| V1-18 | Agent 不能自行创建人类确认、批准模型、发布或回滚 |
| V1-19 | 前端伪造 role、tool result、history、actor/tenant/权限被拒绝 |
| V1-20 | 同会话并发消息返回冲突；SSE 重连/切页不重新调用模型 |
| V1-21 | 停止回答不自动取消已提交业务 Job；取消 Job 有真实终态确认 |
| V1-22 | Worker 重投、崩溃、Redis 重启和 Outbox 重发不重复业务副作用 |
| V1-23 | 中断任务按 handler 能力恢复；无 checkpoint 模板不假称续训 |
| V1-24 | LLM Call 与工具调用分别有 ID，工具执行账本可去重并保留失败记录 |
| V1-25 | Context 来源、最终工具 schema、裁剪信息可查看；估算 token 与实报 usage 区分 |
| V1-26 | Trace 默认不收集敏感内容；内容/制品下载均校验专用权限且审计 |
| V1-27 | Key/Cookie/Token/隐藏思维链不出现在 SSE、日志、诊断包或 Context 视图 |
| V1-28 | 恶意扫描未配置不显示通过；压缩炸弹/路径穿越/公式注入测试通过 |
| V1-29 | 未批准外部 Provider/查询目标不能发送客户数据；连接测试失败不覆盖可用配置 |
| V1-30 | 默认部署不声称多账号或租户隔离；联网暴露预检明确提示或阻断 |
| V1-31 | 首版不存在 Coding 工具、代码上传执行 API、Sandbox Worker、Docker socket 或算法研发菜单 |
| V1-32 | 全部请求/状态/错误与生成 OpenAPI、TypeScript、fixture 一致 |
| V1-33 | 离线包包含锁定依赖/模型清单；安装、健康、备份、恢复与回滚演练可复核 |

## 19. 仍需真实材料的科学与客户验收项

以下不是 Codex 自由发挥空间：各真实模型的输入/输出契约、允许条件/单位、模型文件与 Loader、数据域字段规范、训练模板算法与依赖、独立评测集/阈值、ChemDraw 复杂样例、客户网络/许可证/硬件约束。

以 `delivery_readiness.md` 记录每项的 owner、需要的文件、状态和受影响能力。未提供的真实资产不阻止开发底座，但阻止对应“真实科学功能验收”。

可以构建仅用于测试的 `fixture` Profile，必须在测试包/测试数据库中隔离，响应标记 `data_origin=fixture`；该 Profile 不进入生产交付。前端原型示例不自动导入真实业务数据库。

## 20. 可直接交给 Codex 的任务说明

```text
请按 PROJECT_PRD.md v0.12 与 PROJECT_FRONTEND_BACKEND_API_SPEC.md v0.2
实现新的通用色谱智能平台 V1。

不要兼容旧 8010 等接口/服务；不要复用旧 HTTP 契约。
先产出实施清单与模块构建清单，再实现 Pydantic DTO/OpenAPI/前端类型。
按 M0–M5 交付，并为 V1-01 至 V1-33 提供自动测试或可复核演练记录。

首版使用 Vue3/TypeScript/Vite/Pinia、FastAPI/Pydantic/SQLAlchemy/Alembic、
MySQL、Redis/Dramatiq、PydanticAI、受控制品存储和 OpenTelemetry。

CODING_AGENT_UPGRADE_PLAN.md 只用于检查扩展边界。
不要实现 Coding Agent、Workspace、Shell、Sandbox Worker、动态装包或代码执行；
不要安装 Pi 或把未来 Harness 当成首版依赖。

缺少真实模型/数据规范/模板时登记阻塞，测试使用显式 fixture，
不得捏造科学指标、静态成功结果或声明客户能力已经实现。
```

## 附录 A：来源追溯与技术核查

业务依据：原 `PROJECT_PRD.md` v0.11（模块/结构 §3、助手 §7、训练 §8、模型 §9、技术栈 §12）；原接口 v0.1（通用接口 §3–4、助手 §16、并发 §21）。原型仅供 UI 参考。

原始文件 SHA-256：

```text
PROJECT_PRD.md v0.11
6c74d638a15298b85ad4c545f288993848bb4d697c3b012df965fac13cb0508b
PROJECT_FRONTEND_BACKEND_API_SPEC.md v0.1
9b2882880e51026f2833681fdc77a86c9caad02789f16c9accb348955b817058
```

2026-09-20 核查的官方技术资料（仅支持技术选型，不构成已验收声明）：

```text
PydanticAI Dependencies
https://pydantic.dev/docs/ai/core-concepts/dependencies/
PydanticAI Model Providers
https://pydantic.dev/docs/ai/models/overview/
PydanticAI Deferred Tools（工具审批不替代端点认证与业务授权）
https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/
PydanticAI Messages / History
https://pydantic.dev/docs/ai/core-concepts/message-history/
PydanticAI Instrumentation
https://pydantic.dev/docs/ai/capabilities/instrumentation/
Dramatiq Best Practices（消息可重复投递，业务必须幂等）
https://dramatiq.io/best_practices.html
Dramatiq Guide（time limit 为 best-effort）
https://dramatiq.io/guide.html
MySQL 8.4 FULLTEXT 与 ngram
https://dev.mysql.com/doc/refman/8.4/en/fulltext-natural-language.html
```
