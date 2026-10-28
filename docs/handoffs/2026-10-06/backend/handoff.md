# 后端暂停交接

最后核验时间：2026-10-06 15:56（Asia/Shanghai，源码/合同哈希及本文件状态只读复核）  
运行时快照时间：2026-10-06 15:48（Asia/Shanghai；只适用于下文容器、Docker daemon 和进程观察）  
交接文件：`D:\Jupyter\Chromatography_agent\docs\handoffs\2026-10-06\backend\handoff.md`

## 当前决定与暂停点

总架构师要求立即暂停后端开发。本交接仅记录恢复所需状态，不继续产品编码、合同修改、测试、迁移或服务操作。目标文件此前不存在；此次仅创建本文件。暂停时未启动模型推理、数据库迁移、全面测试或任何服务。

第八窄列演练的最新日志实际记录了续跑到 0019；其迁移主体、负例、回滚和幂等断言通过，但末尾只读验收 harness 查询因字段错误失败。由于 Docker Desktop Linux Engine 当前不可连接，数据库当前状态无法重新核实。因此不能把第八轮记成整体通过，也不能把历史日志中的版本状态当作当前实时数据库状态。第九个真实 unknown-stored-revision 负例没有执行记录。

本项目曾有“Not a git repo, skipping”的开发者提示，但本次安全只读验证确认仓库目录是 Git 工作树。验证命令使用单次 `-c safe.directory=...`，返回 `true`；普通 Git 命令会因 dubious ownership 拒绝。此次没有创建 Git checkpoint、提交或修改 Git 全局配置/权限。

## 参与者与文件责任

| 角色 | 会话 ID | 交接责任 |
|---|---|---|
| 后端负责人 | `01a0be51-01d9-7b80-9759-e194b8c6017c`（本次后端会话；按总架构师提供的准确 ID） | 后端源码、后端迁移和后端测试相关工作；当前遵守暂停，只写此交接文件。 |
| 总架构师 | `01a0be42-382e-73a1-9abe-55f069bd35ad` | 统一范围、合同版本、审批边界与恢复决策。 |
| 前端负责人 | `01a0be50-6eef-71a3-a4d0-41d8678c6596` | 前端页面、路由、状态、API 客户端及生成类型；跨端变更由所有者协商。 |
| QA | `01a0bf2f-10c1-7d51-b9c9-6876ed36a26c` | 独立复核、分层验收与证据报告；不得把组件或隔离测试标成 E2E。 |

前端交接文件现已固定：`docs/handoffs/2026-10-06/frontend/handoff.md`，SHA-256 `82374DDA1D78AA1660A55113D81B75C4AB9306B7BE947E454AC3AE82DF36E5F3`；前端负责人报告无活动执行子智能体并已暂停。QA 交接由 QA 会话直接确认并补齐。

暂停时产品执行 agent 均无活跃工作；唯一活动的 agent 是本交接文档 agent，负责本文件写入。完成本交接并回报后，该 agent 停止。没有其他代理获得本轮产品代码或迁移文件写权限。总架构师拥有本交接状态的调度权。恢复工作前由负责人直接与前端、QA核对各自现有文件和结果，避免重复写入。

## 授权与安全边界

- 仅新建、隔离的 MySQL 容器 A 迁移演练曾获授权。生产数据库 B 未获迁移授权；不可推断 fresh A 或第七 schema 的 PASS 代表生产 GO。
- 可使用已存在的 SFC Python 3.11 环境。不得升级全局 Python、安装或升级依赖、改全局环境变量；任何依赖变更需先说明影响并取得用户明确授权。
- 必须保留旧 Agent、权重、数据、训练记录、制品、备份、旧容器/卷和现存迁移证据。不得盲目重跑、自动清理、手工改 Alembic 版本标记，或在现场状态未知时继续 DDL。
- 不读取、复制或输出 `.env`、API Key、Token、密码、连接串、secret 文件内容、客户数据或完整敏感环境。报告只记录非敏感资源身份摘要。
- 本次未连接或启动任何模型/服务。真实模型推理和浏览器 E2E 均未运行；不将静态 OpenAPI、TestClient、单元测试、组件测试、容器健康或 SQLite 结果称作完整 E2E。

## 依据与需求映射

本次只读核对了项目说明的章节目录及适用规则，没有修改产品说明。冲突优先级为用户当前指令、PRD、前后端 API spec、原型、现有实现。

| 后端工作面 | PRD 主要章节 | API spec 主要章节 |
|---|---|---|
| 架构、构建裁剪、manifest 与 profile | §2 技术架构；§3 构建裁剪；§16 部署与配置 | §1 通用协议；§4 启动和能力发现；§20 DTO 与 OpenAPI 生成；§25 交付与停止条件 |
| 预测、批量、记录和异步任务 | §6 预测；§11 任务与可靠性 | §5 Jobs/事件/恢复；§8 预测；§9 批量；§10 记录与导出；§17 SSE |
| 数据、知识和制品 | §7 数据与知识；§14 文件与安全；§15 持久化 | §7 制品；§11 数据版本；§12 知识与引用；§21 数据表与一致性 |
| 模板训练、模型中心与确认 | §8 模板化训练；§9 模型中心；§12 Confirmation；§13 Trace/Inspector | §3 修订/幂等/Confirmation；§13 训练；§14 模型中心；§18 Context Inspector；§19 设置与审计 |
| 科学适配器、模型结果和验收 | §5 结构核心；§6.2 类型边界；§18 验收清单；§19 科学/客户验收 | §6 结构；§8.3 各类型 schema；§20 DTO；§24 契约与验收测试 |

项目主线为 Vue 3/Vite/Pinia、FastAPI/Uvicorn、MySQL、Redis/Worker、Nginx，业务接口 `/api/v1`，默认端口 9012 从 `.env` 读取。新平台不得代理、依赖或兼容旧 `Services/unified_server.py`、8010 或旧 HTTP 接口。正式结果必须来自真实模型并保留模型/输入/版本/warning/审计；失败不能由 mock 伪装成功。更多安全、模块和化学结构要求见 `AGENTS.md`。

## 合同、manifest 与生成类型状态

以下哈希为本次只读重算或绑定报告中的完整 SHA-256。formal revision 04 仍是活动正式合同锚点；revision 06 已发生候选原地重建期间漂移，不能作为恢复基线；revision 07 是新目录中的候选，仍为预览且未冻结。

| 对象 | 路径（相对仓库） | SHA-256 / 状态 |
|---|---|---|
| PRD | `docs/PROJECT_PRD.md` | `3AA6AA1058C16A44D889A17529B903993DE665CDA9FADE2E707146C0A4E6836B` |
| API spec | `docs/PROJECT_FRONTEND_BACKEND_API_SPEC.md` | `3D37AE3761DA8B8EBDC6BA7451C5B98599282DFE26BEA5A7A180A963799EB94A` |
| Freeze metadata | `docs/V1_M3_M4_CONTRACT_FREEZE.json` | `837E01F9911E0E5FCE99B1D7301674D37AF32F159C9711C12F79AD1D7EF069A1`；revision `20261004-04` |
| Formal universal manifest | `builds/universal-v1.json` | `569613E69680A4458E1319449AD98C00E82519D4BCF9B0AEB930F58E24C3A332` |
| Formal universal OpenAPI | `contracts/universal-v1/openapi.json` | `9D2D4033793BDA426BE5EA7BC9325D64EAD90417718967C336DF65C1D5047EFA` |
| Formal contract index | `contracts/index.json` | `33F12D2056F35682F4331B69AB9CFA5B6308DAE51F0682E6C0688EF804E368C2` |
| Generated root TS alias | `frontend/src/generated/contracts/api-schema.ts` | `93E66BB307BBFEFB9E3BC5AC23FBAABF54468B2C7C3AC590324485F6A0789930` |
| Generated universal TS | `frontend/src/generated/contracts/universal-v1/api-schema.ts` | `A29E880A2D61D3301E511F8F53027B246DE893B83D79EEFE6CDB37C5C008E44B` |
| Rev07 candidate manifest | `builds/candidates/universal-v1-batch-revision-20261004-07/universal-v1.json` | `72CE2B4498B1AE5279D0EB909F7EAA1C2CA8C144B41357ED032DAD37582D4B42` |
| Rev07 candidate OpenAPI | `contracts/candidates/universal-v1-batch-revision-20261004-07/universal-v1/openapi.json` | `41C071B83334F1BC6BCA83EBEB14A93F04A74518C32B61C16F3CB1B6897DF869` |
| Rev07 metadata | `testing/runs/2026-10-04-universal-batch-revision-20261004-07/candidate-metadata.json` | `C23AA2690662386B91F14FE1F0A2179A73BC8A240BF4BDC0D64201152ACBF191`; `PREVIEW_NOT_FROZEN` |
| Rev07 source lock | `testing/runs/2026-10-04-universal-batch-revision-20261004-07/source-lock.json` | `70BCEDB686628AB006BE7EEF453369CD8496B41D23C38CE2655475E93F1A5F8A` |
| Rev07 semantic diff | `testing/runs/2026-10-04-universal-batch-revision-20261004-07/semantic-diff-rev04-rev05-rev07.json` | `02EDAB00E2381470FC73C06BA269C3DD8998FC31D5DC5527005CDDD40F3C5DAA` |
| Rev07 errata | `testing/runs/2026-10-04-universal-batch-revision-20261004-07-acceptance-errata.md` | External immutable correction; corrects metadata count from 6 to 8 changed operations and keeps preview unfrozen. |
| Rev06 drift observation | `testing/runs/2026-10-04-universal-batch-revision-20261004-07/rev06-drift-observation.json` | `C38BED2763606B6B1F9D6EA5CEF10C83D0EDCBBA5508A58283341D70FCD14BBD`; `SUPERSEDED` |
| Rev07 physical universal package tree | `backend/.generated/universal-v1-batch-revision-20261004-07/universal-v1` | `FE15ED208802FC09D8C958C774CCA856B645615E0DE5FAF100C935C59149682E`; 170 files |

Revision 07 adds batch capabilities in a preview contract. The bound metadata's summary count was stale; the external errata and independent QA record 8 changed operations / 9 schemas. Do not patch the candidate, formal contracts or generated types during this pause. The FE revision-04 migration report is `testing/runs/2026-10-04-revision04-frontend-contract-migration.md`, SHA-256 `EF92499DE973DB2A29B9A80022CF61BD3E2B986CAE9C6C29EB364E14E4E24343`; it records the formal four-profile types and tests. Rev07 additions still require owner review and the applicable acceptance gates before any future freeze.

## 最后核验时间与绝对路径/SHA-256索引

下列文件 SHA-256 在 2026-10-06 15:56（Asia/Shanghai）由本交接 agent 以 PowerShell `Get-FileHash -Algorithm SHA256` 只读重算；这些哈希证明被列文件当前字节身份，不表示代码通过测试、部署、数据库迁移或服务运行。Candidate physical tree digest 是 10-04 QA 报告的历史锚点，本次没有重算目录树；旁列的包内 manifest、build manifest 和 Alembic env 文件哈希则为本次当前重算值。所有完整文件路径都从 `D:\Jupyter\Chromatography_agent` 开始。

| 对象 | 当前绝对路径 | 当前重算 SHA-256 / 状态 |
|---|---|---|
| 项目规则 | `D:\Jupyter\Chromatography_agent\AGENTS.md` | `6D4AD2EBD4D986805ADB49F7E59D17FC0E5E61F296713DC166498865BAABE179` |
| PRD | `D:\Jupyter\Chromatography_agent\docs\PROJECT_PRD.md` | `3AA6AA1058C16A44D889A17529B903993DE665CDA9FADE2E707146C0A4E6836B` |
| 前后端 API spec | `D:\Jupyter\Chromatography_agent\docs\PROJECT_FRONTEND_BACKEND_API_SPEC.md` | `3D37AE3761DA8B8EBDC6BA7451C5B98599282DFE26BEA5A7A180A963799EB94A` |
| Alembic 在线环境 | `D:\Jupyter\Chromatography_agent\backend\alembic\env.py` | `84AA7EDFE95646E266395F228BC906ABE38C71C95B5A5F226C2BC846B25BD42B` |
| Migration compatibility | `D:\Jupyter\Chromatography_agent\backend\src\chrom_platform\infrastructure\migration_compat.py` | `7D7659E59437F6194BE5C4D928FEB24E86D38D3E2F3F51BFA3F156509F9254D3` |
| 0010 fencing migration | `D:\Jupyter\Chromatography_agent\backend\alembic\versions\0010_model_center_job_fencing.py` | `A1F923E65A3A50FA2B57A90E8E14FB9336DCF8948FC42508659B59309171E121` |
| 0014 batch snapshot migration | `D:\Jupyter\Chromatography_agent\backend\alembic\versions\0014_batch_resolved_snapshot_export.py` | `E2F4A5E7DA9D208BB30E1FA1B297DCB101ACB2FE60DC86C6E571A912A08A14F7` |
| 0019 batch status repair migration | `D:\Jupyter\Chromatography_agent\backend\alembic\versions\0019_batch_export_status_repair.py` | `5E529DC69866C9F44331BAE962472A52399C5AE2824067A7E4E22F1042DEE208` |
| Profile assembler | `D:\Jupyter\Chromatography_agent\backend\src\chrom_platform\assembly\profile.py` | `811AB80EC508B03CC030DCEF812282BC15C355E5D23688777DD7493A00E83B85` |
| Task core schemas | `D:\Jupyter\Chromatography_agent\backend\src\chrom_platform\task_core\schemas.py` | `7E59D26AE25F7D205C338E2C40789E4AB63CFEBFE7E2C2FA2330316D44C07BA9` |
| Task core router | `D:\Jupyter\Chromatography_agent\backend\src\chrom_platform\task_core\router.py` | `D26A28D338DFB49C3194EA1F3EF8278F4FEEC0677902AA99AF61E43EFBEAE826` |
| Task core SSE | `D:\Jupyter\Chromatography_agent\backend\src\chrom_platform\task_core\sse.py` | `AAD35880DEFC846B2D150F93A255163B40F826F2F73E450D4CFD0C144C34428A` |
| Model-center fencing 定向测试 | `D:\Jupyter\Chromatography_agent\backend\tests\test_model_center_job_fencing_migration.py` | `04A0C63D29945621ABA477021128E85633994F9A43DD66D0FB0A1AEAB62B3E85` |
| Batch-export repair 定向测试 | `D:\Jupyter\Chromatography_agent\backend\tests\test_batch_export_status_repair_migration.py` | `F08E82D344B22C6A111BB380F6454D196E38BF1619AFF91C9D1C55DA5845846C` |
| Task router 定向测试 | `D:\Jupyter\Chromatography_agent\backend\tests\test_task_router.py` | `ACE8B0274DF2BB6C962124E5A97B37023CEB7DBCA4CE8C53EAC582BD2F02BB82` |
| Job SSE 定向测试 | `D:\Jupyter\Chromatography_agent\backend\tests\test_job_sse.py` | `07D8B4AADF8C10563A2361B80125BCC8FB35D95D907075D6FAA4271758AF6335` |
| General migration tests | `D:\Jupyter\Chromatography_agent\backend\tests\test_migrations.py` | `C0DC6539EBE9EC230B7CA85FBB43CF6D0DE3719160589349EFDDFDE4349CF787` |
| Build manifest tests | `D:\Jupyter\Chromatography_agent\backend\tests\test_manifest.py` | `FF853E285D829036AC78BB642E759FD247F94A42161ED181BD0F9F57E03D4B07` |
| Minimal active manifest | `D:\Jupyter\Chromatography_agent\builds\minimal-structure-v1.json` | `AA34132508DEE8CAD2A17B0AF30EE0019AA06D4D553389851E86D0B90B88BA15` |
| SFC active manifest | `D:\Jupyter\Chromatography_agent\builds\sfc-v1.json` | `005AD94228473704842C0C80502752A30107CE83D94AA03D77D0211F62542619` |
| CC active manifest | `D:\Jupyter\Chromatography_agent\builds\cc-v1.json` | `F2BA2C7A6D50E69316C83B8ABE280541F45385CD9FE5DDFA3836B29327C319EA` |
| Universal active manifest | `D:\Jupyter\Chromatography_agent\builds\universal-v1.json` | `569613E69680A4458E1319449AD98C00E82519D4BCF9B0AEB930F58E24C3A332` |
| Minimal formal OAS | `D:\Jupyter\Chromatography_agent\contracts\minimal-structure-v1\openapi.json` | `BF35AF8FD7661B9ED2C5709B1DEB8629D79AB0A3153F644AF8D649E4A73D63AD` |
| SFC formal OAS | `D:\Jupyter\Chromatography_agent\contracts\sfc-v1\openapi.json` | `E88954FB90593596CBB99765DA03C99A6C4E0C01DB05095CD04A73F5FDCB0414` |
| CC formal OAS | `D:\Jupyter\Chromatography_agent\contracts\cc-v1\openapi.json` | `3CFFBDFB7B6A34DB7A1B413635BA7330D31126C1729E59EB6434637B67F55599` |
| Universal formal OAS | `D:\Jupyter\Chromatography_agent\contracts\universal-v1\openapi.json` | `9D2D4033793BDA426BE5EA7BC9325D64EAD90417718967C336DF65C1D5047EFA` |
| Formal contracts index | `D:\Jupyter\Chromatography_agent\contracts\index.json` | `33F12D2056F35682F4331B69AB9CFA5B6308DAE51F0682E6C0688EF804E368C2` |
| Formal freeze metadata | `D:\Jupyter\Chromatography_agent\docs\V1_M3_M4_CONTRACT_FREEZE.json` | `837E01F9911E0E5FCE99B1D7301674D37AF32F159C9711C12F79AD1D7EF069A1` |
| Rev07 candidate manifest | `D:\Jupyter\Chromatography_agent\builds\candidates\universal-v1-batch-revision-20261004-07\universal-v1.json` | `72CE2B4498B1AE5279D0EB909F7EAA1C2CA8C144B41357ED032DAD37582D4B42` |
| Rev07 candidate OAS | `D:\Jupyter\Chromatography_agent\contracts\candidates\universal-v1-batch-revision-20261004-07\universal-v1\openapi.json` | `41C071B83334F1BC6BCA83EBEB14A93F04A74518C32B61C16F3CB1B6897DF869` |
| Rev07 candidate metadata | `D:\Jupyter\Chromatography_agent\testing\runs\2026-10-04-universal-batch-revision-20261004-07\candidate-metadata.json` | `C23AA2690662386B91F14FE1F0A2179A73BC8A240BF4BDC0D64201152ACBF191` |
| Rev07 source lock | `D:\Jupyter\Chromatography_agent\testing\runs\2026-10-04-universal-batch-revision-20261004-07\source-lock.json` | `70BCEDB686628AB006BE7EEF453369CD8496B41D23C38CE2655475E93F1A5F8A` |
| Rev07 profile impact | `D:\Jupyter\Chromatography_agent\testing\runs\2026-10-04-universal-batch-revision-20261004-07\profile-impact.json` | `CF893AA196E61DADCF1F62BCCFD5E606B3CA03744D8D51F4036693B193D80AC4` |
| Rev07 physical package `build-manifest.json` | `D:\Jupyter\Chromatography_agent\backend\.generated\universal-v1-batch-revision-20261004-07\universal-v1\build-manifest.json` | `FEE6087C02BB6296B46CC54580A16C631F66BE51F6F44E8E84EBD8BF6C6909F8` |
| Rev07 physical package `SOURCE_MANIFEST.json` | `D:\Jupyter\Chromatography_agent\backend\.generated\universal-v1-batch-revision-20261004-07\universal-v1\SOURCE_MANIFEST.json` | `CEC35A86A8F33AAA299C38FFB29AE2D82025036C4BA5B7D3E1F5126119D79160` |
| Rev07 physical package Alembic env | `D:\Jupyter\Chromatography_agent\backend\.generated\universal-v1-batch-revision-20261004-07\universal-v1\alembic\env.py` | `84AA7EDFE95646E266395F228BC906ABE38C71C95B5A5F226C2BC846B25BD42B` |
| Rev07 physical universal package directory | `D:\Jupyter\Chromatography_agent\backend\.generated\universal-v1-batch-revision-20261004-07\universal-v1` | **历史 QA tree digest** `FE15ED208802FC09D8C958C774CCA856B645615E0DE5FAF100C935C59149682E`（170 files；本次未重算目录树）；物理清单与 build manifest 哈希见上两行。 |
| FE root generated type alias | `D:\Jupyter\Chromatography_agent\frontend\src\generated\contracts\api-schema.ts` | `93E66BB307BBFEFB9E3BC5AC23FBAABF54468B2C7C3AC590324485F6A0789930` |
| FE universal generated types | `D:\Jupyter\Chromatography_agent\frontend\src\generated\contracts\universal-v1\api-schema.ts` | `A29E880A2D61D3301E511F8F53027B246DE893B83D79EEFE6CDB37C5C008E44B` |
| FE SFC generated types | `D:\Jupyter\Chromatography_agent\frontend\src\generated\contracts\sfc-v1\api-schema.ts` | `8DBD5606049F8D7AED260EACB21D2C1EF62D8DD2ABA5B6006E1D189586181C80` |
| FE CC generated types | `D:\Jupyter\Chromatography_agent\frontend\src\generated\contracts\cc-v1\api-schema.ts` | `3F27C8115DA941FEB10536192D8161483BBB8807E02D610A3D91077C4A1CE9B7` |
| FE minimal generated types | `D:\Jupyter\Chromatography_agent\frontend\src\generated\contracts\minimal-structure-v1\api-schema.ts` | `FA8C45198F45AF32CD6D9D3287102A6B5B904EC2351B441C80AA2829F8477738` |

旧报告中出现的 source-lock、migration/source manifest、运行日志及测试报告 hashes 属于各自报告生成时的固定 evidence hashes；不要把旧日志中的容器运行状态或报告 hash 误称为本次重算。源码和 contracts 上表为当前逐文件重算值。除为了 SHA/路径核验外，此次没有运行测试、服务、DB、模型或 profile builder。
## 验收证据分层

| 证据层 | 结果 | 边界 |
|---|---|---|
| 前端正式 rev04 合同迁移 | 报告称通过 | `testing/runs/2026-10-04-revision04-frontend-contract-migration.md`，SHA `EF92499DE973DB2A29B9A80022CF61BD3E2B986CAE9C6C29EB364E14E4E24343`；包含 typecheck、前端报告列明 `npm test` 为 16 files / 153 tests（均为前端局部验证）。仅为前端合同/组件/build 检查，不是后端或浏览器端到端。 |
| Candidate rev07 物理包与隔离 app | PASS（限定） | `testing/runs/2026-10-04-universal-batch-revision-20261004-07-physical-package-qa.md`，SHA `D3E94AE878AA51C89373BF14D40E33EFBA608EC4AFFF1F5E3B4AE1E060579EAE`；包树、manifest、profile 裁剪、OpenAPI 对齐及 import/live 检查通过；ready 为 503 `STRUCTURE_UOW_NOT_CONFIGURED`。候选仍未冻结。 |
| rev07 OAS 独立 QA | PASS（合同/隔离响应范围） | `testing/runs/2026-10-04-rev07-contract-independent-qa.md`，SHA `725DED55E2B37E4BDEE197BD98ACC163DB08A19294639AF8073947DBB8810B4A`；存在 Confirmation preview 负例因缺 DB 注入返回 503，不能当作绑定规则负测通过。 |
| rev07 Confirmation 隔离 SQLite HTTP | PASS（隔离 SQLite/TestClient） | `testing/runs/2026-10-04-rev07-confirmation-isolated-sqlite-dynamic-qa.md`，SHA `A2D3F8A8F77644607E0643821E1A09FB327FA11220226F6AA45CEB41AECBCD59`；不证明 MySQL、Redis、Worker、浏览器或真实模型。 |
| MySQL A 第七 fresh schema | PASS（合成数据/隔离 A） | 演练报告 `testing/runs/2026-10-04-mysql-0013-0019-a-seventh-schema-pass.md` SHA `0EF80719C4EA48809B3A1F1AFD9F3786901D866A0A087F36421E7EC5B989CC0A`；独立 QA `...-seventh-schema-independent-qa.md` SHA `A6C3C54220545EA25376EF5FE5A7C4EBE21ADD797592CEF352C1346236D30B6D`。涵盖在线 0013→0019、状态映射、坏例 fail-closed、原子回滚、幂等和锁竞争；不覆盖生产 B、窄列恢复或业务 E2E。 |
| MySQL A 第八窄列 schema | 主体断言通过；整体验收未通过/不完整 | 原始暂停报告 `testing/runs/2026-10-04-mysql-0013-0019-a-eighth-harness-stop-at-0013.md` SHA `3A868C4E24859A5BEAF64C59194C5CCFFA4056AF9CFC35E8B9CB7A86FB00058B`；最新续跑日志见下文。 |
| 真实 SFC 模型推理 | NOT_RUN | 不以 Import/OpenAPI、SQLite、模型包静态检查代替。 |
| 浏览器真实前后端 E2E | NOT_RUN | 无本轮真实浏览器/API/数据库/Worker/model 全链路证据。 |
| 生产 MySQL B 迁移/恢复 | NOT_RUN / 未获授权 | 需要单独审批与生产备份/恢复门禁；fresh A 通过不构成授权或 GO。 |
| 第九真实 unknown-stored-revision MySQL 负例 | NOT_RUN | 只有 fake guard 定向测试历史报告；没有第九真实 schema 执行记录。 |

前端迁移报告也明言未启动服务/Worker，未运行浏览器或在线 API E2E。

## MySQL A 现场与第八轮最新实测

非敏感资源历史身份：专用容器名 `chrom-v1-mig-a-6d66cb5c3b46`；MySQL image `mysql:8.4.5`；仅回环发布 `127.0.0.1:52160`；专用卷名 `chrom-v1-mig-a-data-6d66cb5c3b46`。第七 schema 名 `chrom_v1_migration_a_dfed18d7f6b5`；第八窄列 schema 名 `chrom_v1_migration_a_narrow_20261004_c2a7e91b4d6f`。这些是历史证据中的身份，不代表容器/卷/DB 当前还存在或正在运行。不得记录 secret 路径或内容。

2026-10-06 15:48（+08）只读状态：Docker CLI 无法连接 Docker Desktop Linux Engine 命名管道，daemon unavailable。没有启动 Docker。故容器、专用卷、两个 schema 目前均标为 **未能实时核实**。同一时点 `Get-Process` 可见多个 uv 管理的 Python 3.10 进程；未见 SFC Python 3.11 进程。进程存在不证明具体服务健康，未执行或记录服务 HTTP health check。

第八轮最新证据必须以目录 `backend/.generated/migration-a-0013-0019-6d66cb5c3b46/eighth-c2a7e91b4d6f/` 内原始日志为准：

1. `eighth-online-empty-to-0013.log`：SHA `1CD324EBA393403D8AEE5D766A827A21621B50E1941A8B57669B28B58FD4F3C0`；第八 schema 在线到达 0013，旧 batch_export 七列与合成五态 fixture 已写入。之后第一次 harness 因 `datetime` 无法 JSON 序列化中断，未进入 0014。
2. `eighth-shrink-width32.log`：SHA `76776E3253E40027D19CA5C6D1A2B82116F177E970AE6822926C46E417737229`；经授权在同一 schema 将 `version_num` 从 128 缩至 32，保留字符集、排序规则、主键和 0013 值。
3. `eighth-online-widen-target-0013.log`：SHA `0BD59B4A58D6C58B02B331F1F2DC88D32C2AAFB71EA3EB3309EBBEBDCE0CB698`；经在线入口重新扩至 128；schema tree 未变且版本仍为 0013。
4. **最新** `eighth-online-resume-0013-to-0019.log`：SHA `4493074548579496DD5E988768E83964AB963430801C994F683FA8FBE8704489`；日志显示准确读取现有 fixture、`no_reseed=true`，运行到 0019；三种 RepairConflict 负例、errno 1644 原子回滚、五状态映射和再次 upgrade 幂等均记录通过，并出现 `run_complete=PASS_CONTROLLED_RESUME`。但结尾立即出现 `fatal_type=OperationalError`，错误为查询不存在的 `table_name` 字段。它证明迁移主体与定向断言曾通过，不能证明最终只读审计成功或当前现场状态。不得重复播种或再次迁移。
5. 第八现场此前的只读状态日志 `eighth-after-failure-readonly.log` SHA `DD8C4BB3932F641DE0A95C9110F6339F0C2294E702BD09C841D8691B6EB72083` 锚定在第一次 harness 失败后的 0013 状态，不是后续 shrink/widen/resume 后状态。
6. 第八当前 fixture/schema 最新只读摘要文件没有在本次观察到；Docker 不可用期间不得连接、DDL 或猜测版本。恢复连接后先由负责人用现有受限凭据做只读元数据、版本和行数核对，QA 独立复核日志修复及最终状态，再决定是否可以继续任何工作。

## 停止缺陷与延期

- Candidate rev07 remains `PREVIEW_NOT_FROZEN`；其 OAS/manifest 与正式 revision 04 是两个不同对象。rev06 漂移记录是 superseded 历史，不要把 rev06 报告当 rev07 或当前 formal。
- 第八轮 temporary JSON harness 的 datetime 序列化错误已绕开，但末尾 final audit harness 的 SQL 字段错误尚未闭环。现在不能在数据库不可用时修改或试跑任何 harness。
- 第九真实 unknown stored revision 负例没有跑；也没有生产 B 迁移、备份恢复、真实模型、浏览器 E2E 或 Worker 完整链路。
- 0019 fresh A 第七 schema 的 PASS 是隔离合成数据证据。它不清除第八 schema 的最终审计缺口，也不证明真实数据安全或生产就绪。

## 恢复顺序与安全只读命令

恢复后先核验用户/总架构师的新指令是否解除暂停，并在独立责任人间更新文件所有权。保持暂停时，只能继续文档/证据只读复核。任何生产 B 迁移、依赖改动、候选冻结或服务操作都要先满足各自授权；不能从本交接推定授权。

可复核工作树、报告和进程的 PowerShell 命令：

```powershell
$root = 'D:\Jupyter\Chromatography_agent'
$target = Join-Path $root 'docs\handoffs\2026-10-06\backend\handoff.md'
Test-Path -LiteralPath $target
Get-FileHash -LiteralPath (Join-Path $root 'contracts\universal-v1\openapi.json') -Algorithm SHA256
Get-FileHash -LiteralPath (Join-Path $root 'testing\runs\2026-10-04-universal-batch-revision-20261004-07\candidate-metadata.json') -Algorithm SHA256
Get-Content -LiteralPath (Join-Path $root 'backend\.generated\migration-a-0013-0019-6d66cb5c3b46\eighth-c2a7e91b4d6f\eighth-online-resume-0013-to-0019.log') -Encoding UTF8 -Tail 30
Get-Process | Where-Object { $_.ProcessName -match 'python|uvicorn|docker|mysqld' } | Select-Object ProcessName,Id,StartTime
& 'D:\Program Files (x86)\Git\cmd\git.exe' -c safe.directory='D:/Jupyter/Chromatography_agent' -C $root rev-parse --is-inside-work-tree
```

这些是文件、进程与 Git 元信息只读核验。需要重新连接 MySQL 时，先确认 Docker daemon 和容器身份，不要打印 secret/DSN；之后只用获批的受控凭据入口执行 SELECT/SHOW 类只读核验。不得在这一交接步骤中运行 `docker start`、`docker compose up`、Alembic upgrade/downgrade、SQL ALTER/INSERT/UPDATE、测试套件、模型推理或服务启动。

交接收件人应先确认第八现场最新只读状态与最终审计 harness 修复方式，之后由总架构师分配后续写权限；生产 B 保持待用户另行批准。暂停继续有效，本文完成即停止工作。



