# 前端负责人暂停交接（2026-10-06）

本文件为前端独立交接记录，便于未参与此前会话的 AI 接手审阅。记录只反映写入时检查到的内容和证据；候选、环境、服务状态后续都需按哈希和只读命令复核。

## 1. 负责人、范围与暂停状态

- 负责人会话：`V1 前端开发与监督`，thread ID `01a0be50-6eef-71a3-a4d0-41d8678c6596`，工作目录 `D:\Jupyter\Chromatography_agent`。
- 总架构师：`ASTRA 总架构师`，`01a0be42-382e-73a1-9abe-55f069bd35ad`；后端负责人：`V1 后端开发与监督`，`01a0be51-01d9-7b80-9759-e194b8c6017c`；测试负责人：`V1 测试负责人`，`01a0bf2f-10c1-7d51-b9c9-6876ed36a26c`。
- FE 角色为需求分解、UI/接口边界、合同审核、跨端协调和验收；按用户要求，前端代码由 GPT-6 Luna high 执行子智能体编写，负责人不直接编辑产品代码。已知完成的写入任务包括 `candidate06_fe_migration`（revision04 合同迁移与守卫）、视觉修复执行者（修复记录及 pin 见 §4）。其余 FE 执行/审计代理包括 `candidate05_fe_freeze_preparation`、`frontend_visual_alignment_audit`、`assistant_a_frontend_audit`、`batch05_fe_candidate_compare`、`batch06_fe_preview_audit`、`batch07_fe_immutable_preview_audit`、`batch_fe_compat_review`、`batch_frontend_candidate_plan`、`batch_profile_baseline_audit`、`batch_profile_baseline_recheck`。除交接写入者外，当前 FE 树中没有运行中的子智能体；已收到“暂停”指令，不再派发工作。
- 暂停时间：2026-10-06 15:50（Asia/Shanghai，交接准备期间）。前端负责人尚待其会话显式确认已停；当前执行本交接的唯一 Luna 写入者在文档校验后停止。代码/构建/合同写入均暂停。全机可见有 Codex `node`/`python` runtime 进程，但本次不读取命令行或环境以免暴露无关信息；没有把它们归类为项目服务。只读监听检查未发现 `5173/5174/9332` 前端、`9012/9001` API、`8000/8010` 旧服务、`3306` MySQL、`6379` Redis 或 `52160` 隔离 MySQL 映射端口。

## 2. 当前优先级、授权和安全边界

- 需求优先级按项目 `AGENTS.md`：用户当前指令 → `docs/PROJECT_PRD.md` → `docs/PROJECT_FRONTEND_BACKEND_API_SPEC.md` → UI 原型 → 现有代码。首版只理解 `docs/CODING_AGENT_UPGRADE_PLAN.md` 的升级边界，不实现未来 Coding Agent、Workspace、Shell、Sandbox 或代码编辑器。
- 用户特别要求界面风格参照 `preview_coding_runtime.html`；允许将 `D:\1_Undergraduate_111111111111\group tasks\Agent集成\mo-group` 的溶剂反推等前端实现纳入参考；Ketcher 主源码取 `D:\Jupyter\ketcher`，可参考备用 `D:\Jupyter\ketcher-master.zip`。目前本交接没有复查外部源码，没有完成 Ketcher 或 mo-group 产品迁移；沿用时需保留许可证/NOTICE、来源署名并按新 API 改造。
- 用户要求 Windows 上 SFC/Torch 等既有环境保持不被升级或破坏。先前用户曾要求由一个子智能体处理安装；本 FE 本轮使用仓库现有 `frontend/node_modules`，没有改全局环境或启动安装。本项目依赖任何新增/升级仍应遵循 `AGENTS.md` 的“修改环境依赖前先说明并取得明确批准”。用户已授权的隔离数据库实验不等于生产迁移授权；生产数据库、`chrom_test` 和非本次隔离目标 schema 未获迁移/清理授权。不得重跑现有第八 MySQL 夹具或清理 Docker/volume/secret。
- 不提交真实 `.env`、密钥、token、密码、连接串、绝对权重路径。无服务运行证明时不能宣称 FE/业务已就绪；模型缺失、API 失败必须如实展示。
- 本仓库当前确有隐藏 `.git` 目录，但常规 Git 操作遇到 dubious ownership。本次没有改 safe.directory 全局设置、没有使用 Git 状态/提交作为证据，也没有建立 Git checkpoint。不要声称已有 checkpoint；若需要 Git 只读检查，应使用一次性 `-c safe.directory=...` 并避免全局配置变更。

## 3. PRD 与 API 需求对应

先按项目指定顺序读取下列文件；写入前以 UTF-8 复核：

1. `D:\Jupyter\Chromatography_agent\docs\PROJECT_PRD.md`，SHA-256 `3AA6AA1058C16A44D889A17529B903993DE665CDA9FADE2E707146C0A4E6836B`。
2. `D:\Jupyter\Chromatography_agent\docs\PROJECT_FRONTEND_BACKEND_API_SPEC.md`，SHA-256 `3D37AE3761DA8B8EBDC6BA7451C5B98599282DFE26BEA5A7A180A963799EB94A`。
3. `D:\Jupyter\Chromatography_agent\docs\CODING_AGENT_UPGRADE_PLAN.md`，SHA-256 `D860341983041EC29F18CE161C152AA66AABD7B95BE3548CC08939B22D50338F`。
4. 界面参考：仓库中的 `preview_coding_runtime.html`（如路径变化先 `rg --files` 查找；用户指定它作样式参考，不是 API 契约）。

| 前端范围 | PRD | API spec | 当前证据/缺口 |
|---|---|---|---|
| 产品边界、profile 裁剪和导航 | §§1.2、3、4 | §§4、22、24–25 | 四 profile 静态装配与导航组件覆盖；不表示精简交付包独立可发布。全局搜索、共享项目选择没有真实契约，不可放演示控件冒充。 |
| 化学结构、立体信息、编辑器和制品 | §5、§14 | §§6–7 | 公共结构 UI/API 尚需端到端审查；Ketcher iframe 与上传/解析正式链路本轮未验收。 |
| 单次色谱预测、类型边界、记录/导出 | §§6.1–6.4 | §§8、10、15 | revision04 类型与结果/记录/导出守卫已对齐；真实各模型预测、文件下载和完整浏览器流程未验收。SFC 两对映体与 `chiral_lc` 独立 schema 必须保持。 |
| 批量预测 | §6.3 | §9 | 五类 batch FE 页面/store/API 均未实施；当前显示 `BatchUnavailable`。rev07 仅作为候选预览，不能生成正式类型或接线。 |
| 数据/知识中心、训练、模型中心 | §§7–9 | §§11–14 | 页面/API 前端已有部分结构；真实导入、检索引用、训练/审批/发布/回滚流程须逐项核对，当前不能据组件存在宣称业务完成。 |
| 助手、工具确认、任务事件、上下文检查 | §§10–13 | §§3、5、16–18 | 助手 UI 和通用 confirmation/SSE 客户端存在；与真实 PydanticAI、工具、SSE replay、Context Inspector 联调未完成。 |
| 窄屏、刷新恢复、交付与验收 | §§16–19 | §§17、23–25 | 有限的 1440/390/320 布局浏览器证据和静态构建证据；没有整体用户流程、真实 API、模型科学验收或生产/离线包验收。 |

## 4. 当前版本、合同与关键哈希

### 正式基线（仍是前端生成类型来源）

- 正式 `contracts/index.json` SHA-256：`33F12D2056F35682F4331B69AB9CFA5B6308DAE51F0682E6C0688EF804E368C2`。
- `contracts/universal-v1/openapi.json`（revision04）SHA-256：`9D2D4033793BDA426BE5EA7BC9325D64EAD90417718967C336DF65C1D5047EFA`；冻结元数据 `docs/V1_M3_M4_CONTRACT_FREEZE.json` SHA-256：`837E01F9911E0E5FCE99B1D7301674D37AF32F159C9711C12F79AD1D7EF069A1`。
- 其他冻结 OpenAPI：`contracts/minimal-structure-v1/openapi.json` `BF35AF8FD7661B9ED2C5709B1DEB8629D79AB0A3153F644AF8D649E4A73D63AD`；`contracts/sfc-v1/openapi.json` `E88954FB90593596CBB99765DA03C99A6C4E0C01DB05095CD04A73F5FDCB0414`；`contracts/cc-v1/openapi.json` `3CFFBDFB7B6A34DB7A1B413635BA7330D31126C1729E59EB6434637B67F55599`。
- 当前 root 构建 manifest `builds/universal-v1.json` SHA-256 `569613E69680A4458E1319449AD98C00E82519D4BCF9B0AEB930F58E24C3A332`；build_id 是 `universal-v1`，含 TLC/CC/HPLC/GC/SFC 和预测、记录、数据集、知识、训练、模型中心、实用工具、助手、设置等模块。它仍是现有 profile manifest，不含本候选五类 batch API。
- 正式生成 types 的报告基线是 revision04；当前逐文件复核：root alias `frontend/src/generated/contracts/api-schema.ts` `93E66BB307BBFEFB9E3BC5AC23FBAABF54468B2C7C3AC590324485F6A0789930`；Universal `.../universal-v1/api-schema.ts` `A29E880A2D61D3301E511F8F53027B246DE893B83D79EEFE6CDB37C5C008E44B`；SFC `.../sfc-v1/api-schema.ts` `8DBD5606049F8D7AED260EACB21D2C1EF62D8DD2ABA5B6006E1D189586181C80`；CC `.../cc-v1/api-schema.ts` `3F27C8115DA941FEB10536192D8161483BBB8807E02D610A3D91077C4A1CE9B7`；Minimal `.../minimal-structure-v1/api-schema.ts` `FA8C45198F45AF32CD6D9D3287102A6B5B904EC2351B441C80AA2829F8477738`。
- `frontend/src/app/generated/build-manifest.json` SHA-256 `B3B53808DB507F337FC3C2852357AA7796A795372B1AE9BC1747C5C6ED8CB163`，registry `EF043B44CFCEE712834B54B366C9C4B08201761132B3EE25483E27F6AC83836B`，profile `208E63DFF749775499B377EF862FBF7B54C4FDE50DEC8C2A84547525B782160B`。它们为生成前端 build registry 的检查基点；运行 `npm run dev/build` 会生成它们，暂停期间不要重生成或覆盖。

### batch rev07（不可变 PREVIEW，不是 formal）

- manifest：`builds/candidates/universal-v1-batch-revision-20261004-07/universal-v1.json` SHA `72CE2B4498B1AE5279D0EB909F7EAA1C2CA8C144B41357ED032DAD37582D4B42`。
- candidate OAS：`contracts/candidates/universal-v1-batch-revision-20261004-07/universal-v1/openapi.json` SHA `41C071B83334F1BC6BCA83EBEB14A93F04A74518C32B61C16F3CB1B6897DF869`；rev04→rev07 静态差异 +15 paths/+16 operations/+39 schemas，无删除。candidate 有五种独立 batch DTO、模板版本与哈希响应头、确认样例、导出专用下载路由和 Job SSE/字符串游标。
- source-lock `testing/runs/2026-10-04-universal-batch-revision-20261004-07/source-lock.json` SHA `70BCEDB686628AB006BE7EEF453369CD8496B41D23C38CE2655475E93F1A5F8A`；semantic diff `.../semantic-diff-rev04-rev05-rev07.json` SHA `02EDAB00E2381470FC73C06BA269C3DD8998FC31D5DC5527005CDDD40F3C5DAA`；profile matrix `.../profile-impact.json` SHA `CF893AA196E61DADCF1F62BCCFD5E606B3CA03744D8D51F4036693B193D80AC4`；metadata `.../candidate-metadata.json` SHA `C23AA2690662386B91F14FE1F0A2179A73BC8A240BF4BDC0D64201152ACBF191`。物理包 `backend/.generated/universal-v1-batch-revision-20261004-07/universal-v1`，历史 pinned tree SHA `FE15ED208802FC09D8C958C774CCA856B645615E0DE5FAF100C935C59149682E` / 170 files。上述为对象身份，不代表本次重算物理包；恢复工作时应先复核。
- metadata `changed_operations=6` 与 semantic diff 的实际 8 个 operation / 9 个 schema 不同。不可变外部附录 `testing/runs/2026-10-04-universal-batch-revision-20261004-07-acceptance-errata.md` SHA `003957103572F95529B883A442E8B8562C9DF17EA71C6EFE60AB058484807A86` 已解释摘要笔误并绑定对象；其独立 QA `.../2026-10-04-rev07-acceptance-errata-independent-qa.md` 当前 SHA `C5720BE705D4D21768A9F38C93816ADCE8686BDA5B6E60ED287E0E9686E1C8F2` 未发现 P1。
- rev06 曾被并发原地重建，旧候选路径内容哈希前后改变；漂移记录 `testing/runs/2026-10-04-universal-batch-revision-20261004-07/rev06-drift-observation.json` 预期 SHA `C38BED2763606B6B1F9D6EA5CEF10C83D0EDCBBA5508A58283341D70FCD14BBD`，状态 SUPERSEDED。rev07 是新目录的替代候选。rev07 仍为 `PREVIEW_NOT_FROZEN`，metadata 的 `dynamic_mysql_migration` 是 `PENDING`；暂停前不得改候选、正式合同、index、manifest 或生成类型。

## 5. 前端完成、延期和验收证据

### 已完成（只在对应证据层级成立）

- Revision04 前端合同迁移：四 profile generated types、requiredness、replay 和预测 DTO 守卫、SFC axes/result 校验、CC result-row 公式/未知字段校验、records archive/export 的 headers 与 body、Export Location 精确同源验证、Replay 18 字段 allowlist。报告 `testing/runs/2026-10-04-revision04-frontend-contract-migration.md` 当前 SHA `EF92499DE973DB2A29B9A80022CF61BD3E2B986CAE9C6C29EB364E14E4E24343`，包含最终 21 个 FE 文件 SHA 清单。
- Revision04 独立增量 QA：`testing/runs/2026-10-04-revision04-frontend-two-p2-fix-incremental-qa.md` SHA `4E6E3F3F706992948FA03F8D18FE9DB21627EAEC5703E5080327A77DDF0F54D9`。QA 复核 21/21 文件与六个正式合同锚点；Location 外站/prefix/query/hash/错 ID 负例及 Replay 未知顶层字段负例通过；相关 65 tests、`verify:contracts`、`typecheck` 通过。先前全量 QA 报告 `...revision04-frontend-independent-qa-pre-export-location.md` SHA `74C01034C70145E2C529389526DB49D9851AF7C6C6C8C201F918C2B42A74656C`（旧 P2 修复前）；最终 21-pin / 两 P2 复核应以最终迁移报告和 4E6E... 增量 QA 为准。
- 原有完整前端命令证据在迁移报告：`npm run verify:contracts` PASS；`npm run typecheck` PASS；`npm test` 16 files / 153 tests PASS；`npm run verify:profiles` 四 profile 静态装配和 staged typecheck PASS；`npm run build` 121 modules PASS。检查当时使用已有 node_modules；没有服务、数据库、Worker、真实模型或真实 API E2E。
- 视觉按 `preview_coding_runtime.html` 的修复记录 `testing/runs/2026-10-04-preview-style-fix.md` SHA `FBD2D49311CE4782FB407B51F9A4EE35B596956FEB552269B6CDF83932EB278C`。实际改动：`frontend/src/styles.css`、`frontend/src/modules/model_center/ModelCenterView.vue`、`frontend/src/modules/assistant/module.ts`、`frontend/src/modules/assistant/AssistantView.vue`、`frontend/tests/navigation.test.ts`。当前五项 pin 已只读复核，与修复记录一致。独立 QA `testing/runs/2026-10-04-preview-style-fix-independent-qa.md` SHA `27BBD5210386765C3222BFD4A47EB4F8CD36A97B364B6AB8A85D7330C82AA4D1`：1440/390/320 布局、模型中心、320 助手和移动菜单浏览器布局检查通过；console error/warning 0。Vite 代理对 9012 的三个启动接口返回 HTTP 500/ECONNREFUSED，页面展示服务不可用，因此仅浏览器布局/错误态，不是业务成功 E2E。`.main-content` 内部宽度有 6px 页头背景出血，但被 `.app-shell` 裁切，document 无水平滚动；记录认为不是可见横向溢出。
- Batch07 FE 静态审核 `testing/runs/2026-10-04-universal-batch07-fe-preview-audit.md` SHA `BB92A92A2F3E6278305676BBEADEE32C562D80455977B70CB1E6035D17B606E2`。只读：candidate OAS/profile/source-lock/物理包 pin 核对通过，formal rev04→07 静态无删除；FE batch 仍 `BatchUnavailable`，尚无 batch 页面/store/API。报告提到待附录已由独立 errata QA 完成，当前状态以本节 §4 最新不可变附录为准。没有为批量接线改代码、类型或正式 manifest。
- Job 十进制字符串游标影响审查 `testing/runs/2026-10-04-job-event-string-seq-fe-impact.md` SHA `EA7A63FE11968F6406F5C122F55588637E630F3532BBB3BC50DC49397EFC63CD`，只读、未跑测试。发现正式 revision04 generated Job cursor 仍是 number，SSE transport 虽接收字符串但 store 用到达顺序末项续接、无分页续接/乱序最大游标验证；待候选合同冻结后按 OAS 生类型、用 BigInt 或十进制字符串比较，不能 Number 转换。
- 后端/QA 证据可帮助接续但不是 FE E2E：rev07 合同/模板独立 QA `testing/runs/2026-10-04-rev07-contract-independent-qa.md` SHA `725DED55E2B37E4BDEE197BD98ACC163DB08A19294639AF8073947DBB8810B4A`，实际物理包隔离模板 GET 两种格式 HTTP 200 且 body/header hash 匹配；Confirmation 探针因缺 DB fixture 返回 503，记 NOT_RUN。后续在隔离 SQLite session 下 submit/retry confirmation 正负链动态 QA `testing/runs/2026-10-04-rev07-confirmation-isolated-sqlite-dynamic-qa.md` SHA `A2D3F8A8F77644607E0643821E1A09FB327FA11220226F6AA45CEB41AECBCD59` 通过，没有发现 P1；仅 Candidate07 + TestClient + SQLite，测试模型快照是 fixture，不含 Worker/真实模型/SSE 浏览器/并发 MySQL。
- 隔离 MySQL A 第七次 fresh schema 的在线迁移行为独立 QA `testing/runs/2026-10-04-mysql-0013-0019-a-seventh-schema-independent-qa.md` SHA `A6C3C54220545EA25376EF5FE5A7C4EBE21ADD797592CEF352C1346236D30B6D` 判定迁移行为通过，但缺原始 Docker inspect 输出；不是生产迁移 GO，也不是业务/模型 E2E。

### 未完成 / 阻塞 / 不可外推

- 五类批量页面、模板下载与头校验、制品上传、draft/validate、confirmation、Job SSE 断线恢复、行级状态/重试、export 状态和专用下载尚未接入。生成 candidate07 类型前必须等总控与 QA 正式 freeze 通知及新正式合同 hash；不可从 preview 类型冒充正式合同。
- 对批量候选的公开浏览器联调、Job SSE 与分页续接/游标大数和乱序回归、下载字节和权限、五模型状态、刷新/项目切换缓存等尚未运行。
- SFC/Torch 等真实模型结果没有在本 FE 验收；不从页面/fixture 推断科学性能。需要科学验收数据、真实模型、独立测试集和负责人结果。
- API spec §22–25、PRD §18–19 全部验收未做。模型中心/训练/assistant 等前端组件存在，但完整业务状态机、权限/confirmation、重启恢复、SSE 重连、无真实模型行为均未全面验收。
- 空间/搜索能力缺真实契约、Ketcher/mo-group 代码移植与 license review 未完成、移动宽度之外的页面视觉未全覆盖；PRD 中真实性能和客户材料依旧需数据所有者确认。

## 6. 最近停点、不可重放现场与复核顺序

- 最近一个可复核 FE 停点是：batch rev07 静态审核已完，用户暂停全部开发/验收；FE 尚未生成候选类型或批量 UI。接续前先阅读本文件引用的 PRD、API、revision04 FE 报告、batch07 FE audit 和 errata QA；再向总架构师确认优先级/暂停解除、向后端确认冻结 revision/hash、向 QA 确认 candidate 门禁。
- 最新 FE 写入集合：revision04 21 个文件见 `testing/runs/2026-10-04-revision04-frontend-contract-migration.md` 的“最终文件 SHA-256”表。视觉修复增加的不同文件为 `frontend/src/styles.css`、`frontend/src/modules/assistant/module.ts`、`frontend/src/modules/assistant/AssistantView.vue`、`frontend/tests/navigation.test.ts`；其中 `ModelCenterView.vue` 也在 21-pin 集合。各项实现 pin 及哈希以对应报告为证。本轮仅新增本 handoff 文件，不改 FE 源、测试、manifest、合同或依赖。
- 不要把旧候选原地“修复”成新证据，也不要生成 types：rev06 漂移目录已 superseded；rev07 immutable candidate 和 errata 只读；formal `contracts/index.json`、四个 formal OAS、`docs/V1_M3_M4_CONTRACT_FREEZE.json`、active manifest 和当前五份 generated schema 暂不可改。
- MySQL 状态需小心：第七 fresh-schema A 的隔离迁移通过（上列 QA）；第八次受控旧窄列 schema `chrom_v1_migration_a_narrow_20261004_c2a7e91b4d6f` 已在线到 `0013_merge_m2_batch_export` 并提交合成夹具，后续仅因为临时 harness 序列化 datetime 的错误而退出，迁移本身未失败。该现场**必须从现存 0013 与既有夹具继续**，不得重建 schema、重新播种、运行 0014 或执行任何缩列动作；报告 `testing/runs/2026-10-04-mysql-0013-0019-a-eighth-harness-stop-at-0013.md` 当前 SHA `3A868C4E24859A5BEAF64C59194C5CCFFA4056AF9CFC35E8B9CB7A86FB00058B`。主机检查未发现 52160 listener；数据库容器/volume 是否还运行需 DBA/后端负责人读确认。不要清理现场或触碰生产/`chrom_test`。
- 页面测试/开发服务器在本次只读检查时没有相关端口监听；未启前端、API、MySQL、Redis 或 Docker 服务。没有运行中的 FE 子智能体。不要代替其他会话停服务或取消业务任务。
- Git：隐藏 `.git` 目录存在；Git 默认检查以 dubious ownership 失败。本次未创建提交或 checkpoint，也未改配置。该目录是 Git 工作树而不是“无 Git 项目”；不要声称本交接有 Git checkpoint。

### 可复核的只读命令

PowerShell UTF-8 / 绝对路径示例，先核验 `Test-Path`：

```powershell
$root = 'D:\Jupyter\Chromatography_agent'
$paths = @(
  'contracts\index.json',
  'contracts\universal-v1\openapi.json',
  'builds\universal-v1.json',
  'builds\candidates\universal-v1-batch-revision-20261004-07\universal-v1.json',
  'contracts\candidates\universal-v1-batch-revision-20261004-07\universal-v1\openapi.json',
  'testing\runs\2026-10-04-universal-batch-revision-20261004-07\source-lock.json'
)
foreach ($rel in $paths) {
  $p = Join-Path $root $rel
  if (Test-Path -LiteralPath $p) { Get-FileHash -LiteralPath $p -Algorithm SHA256 }
  else { Write-Error "Missing: $p" }
}
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object { $_.LocalPort -in @(5173,5174,9332,9012,9001,8000,8010,3306,6379,52160) } |
  Select-Object LocalAddress,LocalPort,OwningProcess
```

Git is optional; no checkout-wide trust setting was changed. If read-only identification is necessary, use only a command-scoped exception and do not commit during pause:

```powershell
& 'D:\Program Files (x86)\Git\cmd\git.exe' -c safe.directory='D:/Jupyter/Chromatography_agent' -C 'D:\Jupyter\Chromatography_agent' rev-parse --is-inside-work-tree
```

恢复开发时顺序：先由用户明确恢复暂停；再由总架构师给继续优先级和负责人；后端固定新 formal batch/job OAS 与 index/freeze 哈希，QA 审查 exact diff、physical package 和运行证据；FE 再由唯一 Luna high 生成候选/正式 types 并实施 batch 页面；随后由 QA 独立复核，才做浏览器与真实服务验收。未经明确生产授权，不迁移生产数据库，不升级 Windows SFC/Torch 环境，不安装新依赖。

## 7. 交给其他两位负责人

- **后端负责人（`01a0be51-01d9-7b80-9759-e194b8c6017c`）**：请保持暂停；继续保存 rev07 candidate、MySQL 第八 0013 夹具及容器/volume 现场；不得把 rev07 当已 freeze。恢复后需先给 FE 正式 revision、OAS/manifest/index/freeze SHA 和 profile 影响，再协调五类 batch API、Job SSE seq 字符串语义和下载流程。说明隔离 SQLite/MySQL 成功证据的边界，不外推到生产、Redis/Worker、真实模型。
- **测试负责人（`01a0bf2f-10c1-7d51-b9c9-6876ed36a26c`）**：请保持暂停并保存已有 QA；恢复后依 rev07 新 revision 独立核 hash、schema/example、模板 bytes/header、confirmation binding、SSE/游标、各 profile 裁剪、浏览器导航/窄屏/错误状态。将组件测试、隔离 SQLite/MySQL、真实服务、Worker、模型与浏览器业务流程分层报告。
- **总架构师（`01a0be42-382e-73a1-9abe-55f069bd35ad`）**：FE 当前状态已交接；重大优先级、是否恢复、batch formal freeze、生产迁移授权和真实模型科学验收仍需由总控/用户明确。恢复时请把最新文件 pin/哈希发三位负责人，避免重新读取或重建本次受保护现场。

---

交接文件只记录当前证据，不表示 V1 前端完成或生产可用。暂停期间仅进行只读复核和此交接文件写入。