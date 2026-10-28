# 后端结构与工作区整理审计

日期：2026-10-29

范围：后端结构、构建裁剪、前端集中页面、历史记录与缓存整理边界。

目标：找到对当前模块化单体最有收益、影响面可控的渐进优化方式。

## 审计结论

当前平台已经具备真实的模块化单体结构；主要问题不是缺少新架构，而是少数特殊验收路径落在通用底座、Worker 入口职责过密、批量预测单文件职责较多，以及开发/运行材料有历史耦合。

建议先解除生产启动对 testing 和生成目录的硬路径依赖，再把受控验收配置从通用 task/artifact 底座移到部署接线；随后整理有明确边界的批量模型和解析、Worker 入口。

不建议重写 FastAPI + Worker 架构、拆微服务、强行统一成全异步，或为了 SOLID 给每个对象增加接口和注册层。

本审计为只读静态审查；没有启动服务、导入全套业务包、运行测试、模型、扫描或代码图服务，也没有联系项目负责人。

动态性能、启动恢复行为、隐藏导入循环和生产依赖完整性均未在本审计中验证。

## 保留的架构决策

FastAPI 应用由 [app.py](D:/Jupyter/Chromatography_agent/backend/src/chrom_platform/app.py:237) 按 build manifest 装配；M1–M4 负责接线，模块路由按 profile 注册。这种组合根可以依赖业务模块，是其职责所在。

后台任务使用独立 Worker 与 Outbox；模型执行位于 Worker 侧。保留“API 负责业务入口和状态，Worker 执行模型”的边界。

五类主要色谱 TLC、CC、HPLC、GC、SFC 各有类型化请求/结果及 adapter；chiral_lc 另有独立契约。保留不同科学类型的 DTO，不合并成万能请求。

统一 API 客户端、构建能力清单和物理裁剪仍符合产品边界。裁剪规则明确且可追溯，比运行时任意发现代码更适合当前交付约束。

API 请求中间件绑定同一 SQLAlchemy Session，返回成功时提交、错误时回滚；任务/业务服务接收该 session 后不自行提交。

Worker 使用异步 session 承担队列/运行时职责，部分领域工作保留同步 SQLAlchemy。现有同步/异步混合有成本，但本次没有证据表明整体改异步能带来实际收益。

API/Worker 重复科学模型初始化已做去重，API 当前读取 Worker 元数据；本报告不将其列为待修项。

## 按收益和变更风险排序

### 1. 解除生产资源对 testing 与 generated 路径的依赖

总控核对发现，[deploy/v1/run-local.ps1](D:/Jupyter/Chromatography_agent/deploy/v1/run-local.ps1:30) 和 [deploy/v1/startup-check.py](D:/Jupyter/Chromatography_agent/deploy/v1/startup-check.py:24) 硬指向 `testing/runs/2026-10-27-data-import-audit/backend-runtime-package-data-window-08`。

[backend/src/chrom_platform/assembly/prediction_metadata.py](D:/Jupyter/Chromatography_agent/backend/src/chrom_platform/assembly/prediction_metadata.py:56) 与 `run-local.ps1:495` 还读取 testing/fixtures；`artifact_core/batch_csv_window.py:25` 读取测试 manifest。

这种写法让本地启动和验收依赖某个历史运行包的位置。目录清理、另一个开发机或正式部署切换路径时，容易出现文件缺失或启动检查失败。

当前配置入口还有文档口径冲突：根 AGENTS.md:6 要求业务 API 端口从 `.env` 读取，而 README.md:67、94、282–285 说明 Windows 受管启动从受保护 runtime.json 派生配置且不合并根 `.env`。后续应以实际受管启动约束统一开发规范和 README；不因清理改写或覆盖现有受保护配置。`README.md` 约 20 KiB，手动启动块较长，可评估稳定命令封装为短入口，本轮不改脚本。
建议将 package 根、fixture 根、manifest/hash 与验收窗口绑定作为显式部署配置输入；生产默认值指向部署包自身或配置的数据目录，不从测试树回退查找。

迁移时保留既有精确 build ID、SHA-256、manifest、项目和 CSV 映射检查；只改变路径所有权，不放宽安全校验。

可验收收益：把 testing/runs 移出或不可访问后，目标部署仍能解析自身配置；当前验收窗口仍因清单或哈希不符而 fail closed。

### 2. 把单次验收窗口从通用底座移到受控部署接线

[artifact_core/data_csv_window.py](D:/Jupyter/Chromatography_agent/backend/src/chrom_platform/artifact_core/data_csv_window.py:86) 延迟导入 `modules.data_center.tlc_author_domain`，并只接受指定构建、模块、项目、schema 与列映射。

这是单个受控数据导入窗口，不是每个 artifact 操作都依赖 data_center；精确 build/profile 条件让它局限于特定验收路径。

然而把窗口实现放在 `artifact_core` 会让通用制品层承载具体业务域和用户/项目特例，增加可移植、精简构建和清理该窗口时的检查面。

建议把窗口定义及配置放到 deploy/profile 专属接线，再通过现有 `DataImportService`/验证端口传入；不改变 append/reject、用户项目、文件解析和版本不可覆盖规则。

[task_core/outbox_dispatch.py](D:/Jupyter/Chromatography_agent/backend/src/chrom_platform/task_core/outbox_dispatch.py:268) 在 Assistant 作业失败路径导入 assistant persistence，同步 Assistant 运行状态。

该逻辑有同事务更新状态的理由，但 `task_core` 是共享底座，直接知道 Assistant ORM 会妨碍裁剪和独立理解。

建议复用现有 handler/dependency 接线，注入一个可选的窄生命周期回调，参数传当前 session 与失败状态；保证共享事务和 fencing 语义不变。

可验收收益：不选 Assistant 的构建不导入 assistant persistence；选中 Assistant 时 Job 与 AgentRun 仍在同一个数据库事务内完成同步。

### 3. 先拆批量模型和纯行准备，导出晚些处理

[modules/prediction/batch_service.py](D:/Jupyter/Chromatography_agent/backend/src/chrom_platform/modules/prediction/batch_service.py:196) 内定义六个 ORM 模型，463–599 行实现 CSV/XLSX 解析和逐行校验；600 行后由 `BatchPredictionService` 处理草稿、校验、提交、取消、重试和导出。

它确有不同的变更原因，但“批量预测”本身仍是一个连贯业务模块，不能只按 2,000 行就拆成多个服务。

模型迁移的主要直接引用在 `modules/prediction/batch_worker.py:31`；`infrastructure/metadata.py:47` 导入 batch_service 以登记这些表。

`assembly/m2.py` 和 `batch_router.py` 使用服务或模板能力，不直接导入 ORM 行类；迁移不需要重建整个 M2 组合层。

第一步可建立 `batch_models.py`，显式更新 Worker 与 metadata 登记；第二步把纯 CSV/XLSX 行解析抽到 `row_prepare.py`。

批量导出与状态、权限、快照、Task、Artifact 和审计交织；先保留在服务内。等有清晰事务端口后再抽导出应用服务。

可验收收益：调整表定义或解析规则时，不必在服务生命周期和 Worker 处理中定位；batch状态、行错误及导出快照保持既有语义。

### 4. 拆分 Worker 入口，不改变执行模型

[worker_cli.py](D:/Jupyter/Chromatography_agent/backend/src/chrom_platform/worker_cli.py:140) 起包含配置与 profile 计划，约 816 行构造生产 handlers，1243 行开始 OutboxDispatcher，1818 行开始 WorkerRuntime，2061 行为 CLI 入口。

同一文件兼顾部署配置校验、feature handler 装配、运行时和 transport 生命周期，修改某个 handler 时容易扩大审查面，也不便于分层做静态导入检查。

建议先拆出 handler 构造和 runtime 生命周期两个已有职责，保留 `worker_cli.py` 作为薄入口与公共导出兼容层。

不将 Worker 改成动态 import 插件；继续只由 build manifest 与静态注册决定可执行处理器。

可验收收益：独立 profile 不选某模块时不导入其 handler；CLI 参数行为与 Worker 开停行为不变；单项 handler 变更的源码 diff/审阅范围缩小。

### 5. profile 源码裁剪器等模块职责稳定后再简化

[assembly/profile.py](D:/Jupyter/Chromatography_agent/backend/src/chrom_platform/assembly/profile.py:22) 有类型名表，262 行后包含多组 `_trim_*`，结合 AST 与正则生成独立源码包；2339 行开始 `assemble_profile`。

物理裁剪、模块路由、依赖子集、迁移与哈希清单是当前产品交付要求，所以 profile builder 需要存在。

大段按源码文字重写容易受被裁剪文件格式变化影响，但现有 profile 测试和裁剪语义不应因“追求架构整洁”被一次性重写。

先做前四项，让运行期依赖与职责稳定；之后按业务模块拆裁剪步骤，逐步降低跨模块文本替换，并用目标 profile 的源码闭包与 OpenAPI 核对。

可验收收益：profile 构建的变更能定位到模块，选中/未选模块的文件、路由、Worker、迁移和依赖仍可追溯。

### 6. 按修改原因整理前端集中页面

当前文件统计显示 `PredictionView.vue` 1776 行、`BatchWorkspace.vue` 1176 行、`AssistantView.vue` 1360 行。

这些文件不仅长，还把页面展示、业务状态、批量操作/校验和分散交互等不同修改原因放在一起；建议先检查状态归属和组件责任，再分离局部展示或表单流程。

不要重写全局 Pinia store，也不要将所有页面切成大量微组件。仅抽出已有稳定职责，保持数据流和页面 API 行为。

可验收收益：某一子流程改动只影响其局部组件/状态，空、载入、成功、失败及窄屏表现不退化。

## 契约与生成代码

`frontend/src/generated` 与各模块 `contracts/*.generated.ts` 有大体量生成文件，单文件或总量约 31k 行不能据此判断为手写坏代码。

OpenAPI 生成的接口类型应继续作为契约来源，不手工改写冻结产物，也不为缩短行数改变规范。

少数手写候选类型、SSE 事件映射和历史版本合同如确有重复，可在下次契约变更中收敛到生成输入或一个薄映射层；不单独启动全量类型清理。

可验收收益：生成产物可由其源契约再现，手写类型只保留确需的运行时适配，且正式 API schema 不变。

## 整理与清理边界

本次整理区分“已执行的缓存移走”和“后续需评估的历史归档”，不把目录大小直接等同于可删除内容。

| 内容 | 当前观察 | 处理建议 |
|---|---|---|
| pytest / Python `__pycache__` | 根/后端 pytest cache 与源码/测试 pycache 可再生 | 可按白名单清理，保留逐项映射与回退信息 |
| testing | 约 25,188 文件、369.71 MiB；读取有 4 处错误；有 102 个 run 目录 | 不递归删除；逐次确认 run、fixture、receipt 是否被引用 |
| backend/.generated | 约 9,679 文件、148.24 MiB；读取有 3 处错误 | 不把“generated”当成都可删；检查被提交、构建或导入的产物 |
| contracts | 约 5,023 文件、149.25 MiB | 历史合同和冻结版本先保留；清理需检查 profile/部署引用 |
| deliverables、旧服务、模型资产与数据 | 与正式交付、科学结果及旧 Agent/RAG/MCP 边界有关 | 不粗暴清理，不用通用缓存策略处理 |
| 不可读目录 | 权限状态及具体内容未获证 | 保持未知，不估算可清理量，不尝试绕过权限 |

截至本文写作，总控报告的白名单缓存整理已将 22 个缓存目录、309 个文件、10,092,289 字节（约 9.62 MiB）可逆移至 `.local-backups/workspace-hygiene-20261029/cache-round-01`。

同目录 `cache-move-map.json` 记录逐项源/目标/数量/字节及回退说明；事后数量与大小相符，194 个后端源码 Python 文件和 180 个测试源码 Python 文件均保留。

该次操作未永久删除文件，也未触碰 testing、`.generated`、contracts、deliverables、`.runtime` 或不可读 `.pytest-tmp`；这是已执行的缓存整理，不代表历史 run 或报告已经归档。

后续可单独建立历史归档清单：先把被入口文档、脚本和验收记录引用的关系迁移到新位置，再归档旧报告；不在本次直接移动历史报告。

Git 核对显示，backend/src、backend/tests、frontend/src、docs、contracts、builds、deploy、testing 这些范围的 tracked count 均为 0，testing 整体受 ignore 规则影响。这是当前索引状态，不代表磁盘没有其他备份，也不代表所有资料丢失；但新平台文件不能依靠当前 Git 基线回退。本次未更改 Git 索引。任何后续大重构或批量归档前，先建立可回退源码/证据基线：选择性跟踪源码或制作完整快照；不要 git add 全仓，以免纳入密钥、模型或数据。

当前工作区已有大量修改与删除；因此不应通过 `git clean`、`reset` 或全局配置来“恢复整洁”；后续重构/批量归档须先建立上一段所述基线。本次写入只限于本报告，没有改动 Git 索引、执行 git add/reset/clean 或改全局 Git 配置。

## 文档入口维护

总控统计 `plan.md` 约 219 KiB、688 行、66 个标题；`STATUS.md` 约 289.5 KiB、918 行、107 个标题，当前实施与历史记录混在同一入口，阅读成本已高。

复用既有两个入口：`docs/plan.md` 保持简短计划与决策，`testing/STATUS` 保持当前验收概览；历史按日期归档。不要再造并行 current index。归档前检查脚本及报告引用，保留原证据内容、链接和冻结 SHA。

README 中批量功能仍写“进行中”，而五类功能已有 limited-pass 记录；统一状态前应厘清 limited-pass 是静态/受限验收还是完整业务验收，不能把它直接改写成全面通过。

本审计不修改 README、plan 或 STATUS；后续根据已有正式契约和验收报告核对口径，再按单写入范围更新入口文档。

## 精简版 SOLID 诊断附表

这些原则用于指出实际变化成本，不计分，也不要求逐项添加接口。

| 原则 | 本项目证据与判断 | 实际动作 |
|---|---|---|
| S 单一职责 | BatchPredictionService 兼有配置、验证、执行、重试和导出；这是可逐步拆分的业务职责，不等于整个批量模块不内聚 | 先拆模型、纯解析，导出后置 |
| O 开闭 | 新色谱类型要补 DTO/adapter/profile 类型表和裁剪分支；固定 allowlist 是受控交付约束 | 保留显式清单，不做任意运行时插件 |
| L 里氏替换 | PredictionAdapter 有 Protocol 和注册校验，但没有真实替换运行证据 | 标为静态无法全证，不列整改任务 |
| I 接口隔离 | BatchPredictionService 多处接收 `object`，以 `getattr` 探测协作者能力；灵活但静态类型约束弱 | 只在可复用边界补窄接口，不为每个依赖造一套端口 |
| D 依赖倒置 | 预测服务有 ports；task_core→assistant、artifact_core→特定数据域、prediction→assembly 是少数反向依赖 | 用现有依赖接线移出可选功能耦合，保留组合根向模块接线 |

从 KISS 看，优先换成明确部署配置、少量窄回调和两个低耦合文件拆分，比新建框架更短、更容易恢复。

从 YAGNI 看，没有性能数据支撑全异步、缓存层、微服务或运行时插件注册；本轮不引入这些技术改造。

从 DRY 看，可逐步减少 profile/type 手写映射的重复来源，但不能把科学 schema 变成万能模式；以 OpenAPI 与 profile manifest 为明确事实源。

## 分步顺序与验收指标

第一步：把生产 package、fixtures、window manifest 从 testing 路径解耦；API import 不读取测试目录，测试资源不可访问时，正式 profile 的配置解析仍能工作。

第二步：将 one-shot 验收 profile 的窗口定义移入 deploy/build 接线；选中该窗口时，精确 manifest/hash/项目/字段映射校验仍拒绝越界输入。

第三步：将 Assistant 失败状态同步改为可选窄回调；同一 session、同一事务、同一 fencing 条件下 Job 与 AgentRun 状态仍一致。

第四步：拆 batch models 与 row_prepare；比较 OpenAPI、数据库表/索引、CSV/XLSX 错误、行序、幂等及状态变化；导出服务暂不抽取。

第五步：拆 Worker handler 构造和运行时入口；各 profile 的 Worker 列表与固定任务处理器一致，未选功能不加载。

第六步：模块边界稳定后，再简化 profile 源码裁剪；逐个构建目标 profile，核对独立 import、文件清单、依赖闭包、迁移闭包、OpenAPI 和能力发现。

第七步：再按真实变更原因拆 PredictionView、BatchWorkspace 和 AssistantView，保留现有导航、状态行为和窄屏布局。

验收尽量只运行受改动影响的静态检查和小范围契约/状态检查，不重跑科学模型样例来证明无关的 UI 或目录变化。

核心 import 不应依赖历史报告或缓存；清理归档后配置可解析；业务操作仍保留确认 revision、幂等键、任务 ID、取消/重试恢复及审计状态。

## 不承诺事项

本报告没有性能测量，不声称这些整理一定提高吞吐或降低延迟。

没有运行导入图或动态启动，不能承诺不存在任何运行时导入环或所有 profile 都能脱离历史路径运行。

没有验证所有已记录的业务状态，也没有重建、重跑或重新发布 deliverable。

本轮未执行代码重构；文中方案是基于可读源码和给定审计证据的渐进建议。

关于文章 article89、article113、article77、article103、article71，本次仅将职责边界、最小业务闭环、确定性验证/恢复与复杂度权衡作为设计启发；不把文章当作工程架构标准，也没有打开外部链接。



