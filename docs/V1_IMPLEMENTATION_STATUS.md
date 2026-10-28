# V1 实施与协作台账

> 历史台账：下方阶段表、负责人快照和过程结论主要记录 2026-09-20 起的实施状态，不代表当前 M1–M5 状态。当前证据以 [开发计划](plan.md) 和 [测试阶段摘要](../testing/STATUS.md) 顶部最新日期为准；已保存服务/PID 状态不是今日运行身份。

基线：PROJECT_PRD.md v0.12、PROJECT_FRONTEND_BACKEND_API_SPEC.md v0.2。
用户补充：先用现有 Python 3.11；mo-group 前端与本地 Ketcher 纳入复用。

## 文件责任

| 负责人 | 模型 | 写入范围 |
|---|---|---|
| 总控（当前会话） | 当前模型 | builds、contracts、deploy、总体验收文档、根配置 |
| 前端负责人：01a0be50-6eef-71a3-a4d0-41d8678c6596 | gpt-5.6-sol / medium | frontend |
| 后端负责人：01a0be51-01d9-7b80-9759-e194b8c6017c | gpt-5.6-sol / medium | backend |

负责人可创建 gpt-5.6-luna / high 执行子智能体。所有执行者先读 AGENTS.md 与文档，先分析再修改；同一文件只允许一个负责人分配写入。参考仓库只读，不复制 .env、客户数据或未来 Coding/Sandbox 功能。

后端已由 01a0be52-518f-78e3-9fa0-e9cb8455dccb 交接至 01a0be51-01d9-7b80-9759-e194b8c6017c；前者的 contract_audit/runtime_audit/reuse_audit 已确认 interrupted，后者为唯一后端负责人。

## 顺序与准入

| 阶段 | 交付 | 当前状态 |
|---|---|---|
| M0 | DTO/OpenAPI/生成 TS/fixtures/四 profile/真实裁剪 | 开始：构建清单和协作规范；未验收 |
| M1 | ActorContext/结构/制品/任务/确认/审计 | 未实现 |
| M2 | 类型独立预测/批量/记录/导出/数据版本 | 未实现 |
| M3 | 知识/模板实验/模型治理 | 未实现 |
| M4 | PydanticAI/工具集/持久事件/确认恢复 | 未实现 |
| M5 | Trace/恢复演练/离线交付 | 未实现 |

前后端共同以冻结 OpenAPI 为准。后端在 backend 内提供生成器和临时输出，总控审核后更新 contracts；前端只生成类型，不手写另一套 DTO。每次阶段报告必须区分实现、实际测试、未测试和外部阻塞。

## 固定边界

- 新业务只使用 /api/v1；默认 API_PORT=9012，从项目配置读取。
- 无旧 HTTP 自动回退；推理由 Worker 调用固定适配器。
- 不注册 Coding/Workspace/Sandbox/API 或未来专属表、依赖、Worker。
- universal-v1、sfc-v1、cc-v1、minimal-structure-v1 均保留结构核心。
- 无真实资产不输出演示预测；未实现可选模块不进入可发布构建。
- builds 中 profile 是目标清单，不是完成证明；缺源码/契约/依赖锁时交付必须失败。
- 不升级现有 SFC/LR 环境；先提交精确依赖方案，经用户允许后安装。

## 已执行与环境检查

- 用户已添加项目。前端使用同目录派生会话，后端使用已保存项目的本地会话；两者均显式设置为 Sol medium。
- builds/manifest.py 校验四 profile 的语义；3 项 unittest 通过（包含多个非法组合子场景）。这不是源码裁剪验收。
- docs/V1_ACCEPTANCE_MATRIX.json 已建立全部 69 项验收记录，初始均 NOT_RUN；不得批量标记通过。
- docs/V1_REUSE_SOURCES.json 已记录 19 个参考文件指纹；REFERENCE_ONLY 不代表已复制或科学验收通过。
- Docker info 返回 linux；尚未证明 MySQL/Redis/Worker 可用。
- 已向同一前后端会话发送继续指令；索引持久化在 V1_SESSION_INDEX.json，禁止重复创建监督会话。
- deploy/v1 预检 2 项单测通过，contracts OpenAPI 结构检查 2 项单测通过；合计总控检查 7 项通过。它们不是 69 项业务验收的替代品。
- 使用示例配置实际运行预检按预期返回 BLOCKED：数据库占位、asyncmy/Dramatiq 缺失。Linux Docker 可达，尚无容器/镜像；未安装任何依赖。

## 总控复核补记：2026-09-20，装配器整改与 M1 候选

- 前端装配器已限制输出到专用目录后代、拒绝 symlink/junction 逃逸，并从现有锁文件裁剪依赖闭包。总控独立执行 `node scripts/verify-assembly-boundaries.mjs`，exit 0，危险输出 sentinel 保留。四 profile 离线安装、测试、构建通过为前端负责人证据，见 frontend/STATUS.md；不代表平台业务或完整离线交付验收。
- 总控在 backend 目录使用 SFC Python 执行 `-m pytest tests -p no:cacheprovider -q`，92 项通过、exit 0；仅为本地单元/契约证据，不包含真实 MySQL/Redis/Worker/科学模型验收。
- M1 候选暂不冻结：二进制上传未声明 requestBody、下载200错误声明JSON；ArtifactAccepted任务绑定和Location不完整；完成上传的空对象请求未拒绝未知字段；部分资源/警告字段使用宽泛字典。另发现公共异步路由直接调用同步健康/数据库操作，项目列表真实存储分支缺少游标实现。整改已统一交由唯一后端负责人，不直接派发其子智能体。
- contracts/ 当前冻结版本保持原八条公共路由；前端不得将 backend/.generated 候选直接用作正式契约。后端负责人负责修正后提交一次汇总审查材料。

## 总控冻结补记：2026-09-20，M1 第二轮接口子集

上一条“暂不冻结”决定已被本次复审取代。总控独立 backend cwd 执行 SFC Python `-m pytest tests -p no:cacheprovider -q`，129 项通过、exit 0。只读源码检查确认线程池端点、SQL keyset 项目分页、制品缺少真实事务任务端口时返回503；所有候选通过normalize、旧路径保留、二进制请求/响应、严格空对象、required job_id、202 Location以及幂等/ETag头专项断言。

四 profile 已经 freeze_openapi.freeze 写入 contracts/ 及 index.json；每份25 paths/26 operations。以下是规范化后冻结哈希，与后端美化排版候选文件哈希不同：

- cc-v1：e40a672a3e1d7cdac895f058246eab95baa2461e7da126b145b687096bf491a9
- minimal-structure-v1：f5c57950f6c0cfd0a619e375efb191549d78b1ef07da0e645a26b28456f6f45a
- sfc-v1：37d361cd19597c7c4c96026b8c5c208d9d100c072c968b298d645fd5ac7f167e
- universal-v1：ce98dd25b04ca7cfc2be5fb72333945b84a36ef0921f3e381f172a0e0a3a60d2

这是前端类型生成与后续实现的接口基线，不是M1功能完成或上线验收。真实MySQL/Redis/Worker、制品Job+Outbox同Session装配、scanner、TTL、SSE/logs、真实policy/resolver及科学模型仍按 backend/STATUS.md 登记。前端负责人负责生成类型、替换已冻结DTO并完成类型/客户端验证；后端负责人继续实现真实运行闭环，禁止以fixture代表生产可用。

## 总控前端契约迁移复核：2026-09-20

前端负责人完成M1生成类型迁移后，总控在frontend目录独立执行三个检查，全部exit 0：`node scripts/generate-contract-types.mjs --verify`（四profile冻结输入/生成输出无漂移）、`node node_modules/vue-tsc/bin/vue-tsc.js --noEmit`、`node node_modules/vitest/vitest.mjs run`（4文件14测试通过）。测试运行出现Node关于未提供localstorage-file的实验性提示，无失败。此证据覆盖类型及客户端本地行为，不包含真实制品Worker、扫描、数据库或SSE联调；/events/stream与logs仍不在本次冻结子集。

## M2 架构决定：模型验证与业务范围绑定

2026-09-20，总控确认prediction中央registry的验证句柄未绑定ModelOption.scope。M2冻结前须将business_scope_id纳入manifest、verification evidence及句柄，统一校验注册/选择/执行，禁止适配器层补偿作为唯一约束。业务scope不能代替Actor项目权限；公开champion/exact请求仍遵从现有接口，exact不增加客户端必填scope。受理时持久化真实模型版本/哈希/schema与内部scope，Worker不因Champion切换重新选择模型。句柄及嵌套schema需不可变或防御性深拷贝，不能将slots或浅复制等同不可变。

后端负责人负责实施、自测和适配器迁移；独立回归交测试负责人，覆盖scope错配、直接execute绕过、exact歧义、篡改及排队后Champion切换。该决定不改变已冻结M1，其他本地开发继续。结果仍待实施和测试，不登记为已通过。

## 独立测试职责交接与首批证据：2026-09-20

常规独立测试及接口/E2E/回归验收默认归第三位测试负责人（01a0bf2f-10c1-7d51-b9c9-6876ed36a26c）；总控审核证据与重大决策，不重复执行无变更的基线。当前详细验收映射与缺陷台账以testing/ACCEPTANCE_MAPPING.md、testing/DEFECTS.md和testing/STATUS.md为准；docs/V1_ACCEPTANCE_MATRIX.json为初始需求索引，不能当作最新执行结果。

测试负责人首批报告已由总控核对本地状态记录：69个唯一验收ID全部映射，22 RUNNABLE_PENDING、35 BLOCKED_CAPABILITY、12 BLOCKED_INFRA，无完整验收项目被标为通过；后端159 passed/1 conditional skip，前端14测试及类型/契约检查通过。Apifox CLI 2.2.8仅确认本地可用，未声称已执行Apifox接口套件。隔离19012端口真实HTTP检查验证live200、缺失DB/queue时ready503及真实不可用响应，记录其进程已停止。详细证据在testing/runs/2026-09-20-baseline.md和2026-09-20-http-smoke.md。以上为测试负责人执行证据，总控本轮未重复跑测试，也未扩大为数据库/Worker/完整端到端或科学验收通过。

## M2 scope 修复进入独立复验：2026-09-20

后端负责人报告已实现manifest/evidence/handle/model option/snapshot业务范围绑定，统一resolve/execute/execute_snapshot校验与不可变schema副本，并迁移TLC/CC/HPLC/SFC/GC；其自测报告188 passed/1默认关闭的外部TLC smoke skip，TLC显式烟测另13 passed。此处仅记录负责人报告，不视为总控或独立测试验收。测试负责人已接续既有M2_ARCHITECTURE_REGRESSION验证，问题状态为“已提交修复，独立复验中”，不提前关闭。生产预测路由、真实持久化/Worker/Outbox E2E未完成；快照端口测试不能替代真实异步执行验收。M1冻结契约保持不变。

## M2 中央 scope 缺口组件级关闭：2026-09-20

总控读取testing/runs/2026-09-20-m2-architecture.md及架构回归计划，核对测试脚本、adapters.py、schemas.py、service.py四个当前文件SHA-256与测试证据完全一致。独立9项架构测试通过，覆盖scope绑定、零predictor调用、exact歧义、句柄/嵌套schema隔离、缺scope快照拒绝、Champion改变不重解析及项目权限优先。中央绑定的代码级缺口据此关闭；本轮总控未重复执行测试。独立全量188 passed/1 skipped为同次报告证据。

关闭范围仅为当前指纹对应组件实现。真实预测HTTP、数据库持久化、队列/Worker执行及重投恢复仍未验收；生产快照执行与科学黄金标签/独立评测继续列为开放验收项。M2整体未完成，尚未申请新OpenAPI冻结。

## M2 共享装配审查决定：2026-09-21

已审核候选assembly/m2.py、prediction/data_center路由及prediction持久化/Worker。允许后端负责人按既有授权实施app集成，但暂不冻结M2，M3独立推进。当前仍只有m1-openapi候选目录。

架构边界：app.state只保存工厂和无Session共享资源；请求在单个同步工作单元内创建/绑定/提交或回滚/关闭Session，持久化适配器仅flush。Worker使用短claim事务、无DB长锁的推理、新短finish事务，需attempt/dispatch/lease-fencing一致性。按manifest分别装配prediction/records/datasets；底层服务依赖不增加管理路由，公共metadata不得反向引用可选模块。

冻结前整改：data-import validate/commit的202响应改为真实Accepted和job_id/Location；commit按规范I+confirmation执行，不擅增If-Match要求；references留内部服务；鉴权后识别有效幂等重放，再检查非重放的ETag；data-versions/prediction-records实现有界游标分页。生成四profile候选和差异后由测试负责人独立验证，总控再冻结。真实MySQL迁移和Redis/Worker E2E仍未验收，以上决定不构成依赖安装授权。

## M2 独立测试回报与 BE-004 冻结门槛：2026-09-21

测试负责人报告聚焦12 passed、后端294 passed/1 skipped、四物理包隔离10 passed，BE-001/002/003关闭；详细证据见testing/runs/2026-09-21-m2-final-acceptance.md。新开BE-004：sfc/cc/universal静态M2候选与隔离包运行时OpenAPI哈希不一致，minimal一致。该项作为冻结前阻断，由后端/测试负责人直接闭环。

总控要求先按冻结工具的规范化规则比较，排除排版/键顺序，再记录实际字段差异；最终候选从各profile实际隔离产物app.openapi()生成并绑定同次输入/源码/契约指纹。缺失依赖锁必须如实标记，不能以清单等同锁文件。未变行为无需重复无关全量测试。组件检查通过不代表M2整体、M3/M4、真实数据库/队列/模型验收；全部包保持非release-ready。

## BE-004 独立复验关闭、待正式 M2 冻结材料：2026-09-21

测试负责人已在testing/runs/2026-09-21-m2-be004-revalidation.md记录：normalize/JSON Pointer门禁4 passed（10 deselected），四包受影响专项14 passed；各profile静态候选与隔离package运行时契约规范化bytes一致、字段差异0，同次SOURCE/build/requirements摘要及契约指纹一致。BE-001至004均已关闭，未重复无关全量。总控已要求唯一后端负责人提交稳定快照的正式路径、规范化SHA、M1→M2契约差异、锁文件性质及范围说明；此次记录不自动覆盖contracts/。M3/M4、真实MySQL/Redis/Worker/模型验收及release-ready仍未通过。

## M2 接口子集正式冻结：2026-09-21

总控完成正式材料复核并实际更新contracts/<profile>/openapi.json与index.json。minimal保持25 paths/26 operations，sfc/cc各35/36，universal45/47；规范化SHA与BE-004复验证据一致。无M1路径删除，非minimal的artifact ArchiveRequest仅因同名类型分拆而改引用名，字段定义完全相同。专项核对包括候选/SOURCE/summary/build/直接依赖清单指纹、I/E唯一且required非nullable、202 Location、data-import commit未擅加E、内部references未公开。详细审查快照见docs/V1_M2_CONTRACT_FREEZE.json。

前端负责人已获正式类型生成和页面接入通知，由其Luna实施，测试负责人接手独立客户端/页面验证。requirements-profile.txt仅为固定版本直接依赖清单（minimal6项、sfc/cc各10项、universal17项），不是完整传递依赖/平台wheel锁；完整锁、离线干净安装、真实MySQL/Redis/Worker/模型科学验收继续未通过。M3/M4不在本次冻结范围，全部交付包仍非release-ready。此决定取代前文“等待M2冻结材料”状态，但不扩大为M2全功能验收。

## 重要更正：M2 物理包裁剪结论暂停采信（2026-09-21）

后端报告M3/M4执行者覆盖生成目录且未选assistant的minimal/sfc/cc包混入0006_m4_assistant.py。总控随后只读核验：当前四个SOURCE_MANIFEST文件哈希仍与正式M2冻结材料完全相同，且各manifest.files本身已列0006，实际文件也存在。因此当前证据不能证明这是仅在冻结之后发生的漂移，原迁移裁剪测试断言与时间线须重新审计；不能沿用“此前完整迁移闭包通过”结论。

已冻结contracts四个实际文件hash仍与index及冻结记录一致，M2接口子集继续有效，前端契约接入无需回退。backend/.generated/profile-packages全部暂不可用作交付/通过验收的物理基线，原物理裁剪通过结论暂停采信。原报告和哈希保留，禁止覆盖旧产物后冒充旧快照。后端负责人负责修生成器并输出唯一新snapshot目录；测试负责人负责保留证据、针对当前坏包证明精确迁移集合测试失败，再验证新包的迁移链和文件集合双向完整性。无需重复无关全量。真实基础设施、完整锁和离线安装仍未验收。

## 前端 M2 当前可测范围独立验收：2026-09-21

总控已读取testing/runs/2026-09-21-frontend-m2-acceptance.md。测试负责人证据：verify:contracts、typecheck、test、build、verify:profiles五项npm检查exit 0，Vitest 5文件17项通过，M2静态契约/union/路由/头/202/structure_refs/confirmation审计通过；四profile契约规模及hash一致。浏览器桌面与390px服务不可用状态及无横向溢出已验证。这些属于当前可测范围，不覆盖成功端到端、真实结构响应、预测/导入结果、数据库/Worker/科学模型。

Ketcher MutationObserver异常目前仅在Codex in-app宿主嵌入路径观察到，直接页面和标准Playwright对照未复现；按测试台账FE-001撤销为ENV-002环境观察，根因不据此定论，不修改压缩产物。若标准浏览器/真实用户复现再升级产品缺陷。后端BUILD-001仍高优先级OPEN、物理裁剪验收暂停，前端通过不解除交付阻塞，全部包非release-ready。

## BUILD-001 新快照修复通过；M3/M4 因兼容性暂缓冻结：2026-09-21

测试负责人在testing/runs/2026-09-21-m3-m4-candidate-revalidation.md对唯一snapshot m3-m4-v1-candidate-20260921-01记录增强裁剪通过：实际/manifest双向文件集合、精确迁移链、未选模块边界、隔离导入及运行时/静态契约一致。minimal仅0001，sfc/cc为0001+0002，universal为0001..0006；BUILD-001针对新快照关闭，旧坏包继续不可用。

随后后端负责人增加frozen→candidate比较，发现已冻结header minLength/maxLength/title丢失（minimal29处，sfc/cc各31处JSON Pointer差异）。总控暂缓M3/M4冻结，contracts保持M2版本。约束丢失与title元数据差异分开分析；候选内部一致不足以证明向后兼容。后端修复真实来源并生成新唯一revision，测试负责人补充旧冻结接口兼容门禁与运行时请求头边界验证，保留当前快照和失败证据。不重复无关全量。真实基础设施/模型/成功E2E/完整传递锁及离线安装仍未验收，所有包非release-ready。

## candidate-03 请求头门禁裁决：2026-09-21

总控读取API规范§1.3/§1.5及-03失败报告：新增header schema.title未被规范要求，不单独作为CONTRACT-002高优先级冻结阻断。approve/reject原缺失长度约束的缺陷以required/type/non-null/min/max及运行时行为复核，已有冻结元数据保持与新增文档标题需求分开。

明确运行时规则：幂等键完全缺失400 IDEMPOTENCY_KEY_REQUIRED；If-Match完全缺失428；头存在但空/超声明长度统一422 VALIDATION_FAILED；格式有效但版本过期412；先校验当前授权，再识别成功幂等重放，重放不因旧ETag失效变412。不得让缺依赖503掩盖纯头校验错误。data-import缺幂等返回422仍需修复。412可在明确标记的隔离HTTP业务组件fixture中验证，不扩大为真实MySQL/Worker验收。后端修实际来源，测试负责人定向复验；M3/M4冻结继续暂停，但不因新增title缺失而阻塞。

## candidate-04 定向复验通过，待正式 M3/M4 冻结审核：2026-09-21

测试负责人已在testing/runs/2026-09-21-m3-m4-candidate-04-final.md记录：旧冻结兼容5/5，BUILD/normalize/M4定向10 passed，新approve/reject无效头422为4/4，非生产HTTP组件fixture验证stale412、旧ETag幂等成功重放202及同key异payload409为1 passed。缺失/空/超长请求头状态分别为Idem400/422/422、If-Match428/422/422、data-import Idem400/422/422。CONTRACT-001/002关闭，BUILD-001保持关闭。

总控要求后端负责人提交-04同快照指纹及M2到M3/M4契约差异，并区分知识/训练/模型治理/助手的组件业务证据、仅静态或依赖503路径和未完成功能。头校验通过不替代业务或真实端到端验收。当前contracts仍为M2，尚未冻结M3/M4；真实基础设施/模型/完整锁及离线安装/成功E2E仍未验收，非release-ready。

## candidate-04 总控新增契约完整性审查：暂不冻结（2026-09-21）

关闭既有header/裁剪缺陷不等于全部M3/M4契约合格。总控直接读取-04OpenAPI后确认：models及model-deployments的ListEnvelope泛型形成二维数组；部分列表无page；模型登记validate缺I/E及严格空对象，202为object且缺Location；评测202错用可空job_id的详情对象；训练submit擅加If-Match且缺202 Location；Deployment、评测协议、LLM/context等正式响应仍用object或宽泛dict而非规范专用DTO。以上影响客户端类型及成功响应，不能因无依赖时503通过而冻结。

后端负责人须集中审查全部59新增operation的规范映射与业务不变量后实施；测试负责人建立规范→候选完整性矩阵，覆盖应有但完全缺失的header、扁平分页、Accepted/Location、严格DTO、正向组件序列化及权限/确认/版本边界。BUILD-001、CONTRACT-001/002的已验证关闭范围保留，新增完整性缺陷另列。当前contracts保持M2；-04不覆盖。

数据勘误：-04实际universal为98paths/106ops，测试报告104paths不准确；minimal SOURCE_MANIFEST实际SHA-256为39001b5dd5db46eb3dc630f31031f5c1f17ffe07fa293ffc32ca620bc20fd92d，报告/消息尾部存在抄录错误，已交测试负责人追加原字节取证勘误。

## M3/M4 完整矩阵与 snapshot-04 新物理阻断：2026-09-21

后端负责人完整审计报告：旧universal47操作，候选106，新增59操作为Knowledge8、Training9、ModelCenter21、AssistantTrace21；规范对应65操作，缺训练experiment events/logs/metrics/artifacts、training/comparisons及models/{id}/artifacts共6，拟纳入集中整改与最终完整性门禁。

后端另报告-04同时有4个直接profile及4个嵌套旧副本，且task_core/router.py与SOURCE_MANIFEST不一致。总控要求测试负责人独立核实并另列物理阻断，原0006裁剪和请求头缺陷修复历史保留，但-04整体物理通过结论不能继续沿用。整个snapshot根精确布局、唯一canonical profile路径、summary/runtime/manifest同路径及测试前后指纹稳定性纳入门禁。不得通过递归选择某一子目录忽略额外旧副本。

后端负责人按知识训练、模型治理、助手trace互斥范围管理Luna集中实施，之后再单独统一生成新revision；测试负责人独立复验65操作及业务不变量。当前contracts维持M2，-04继续只读且不可冻结/交付。真实基础设施及成功E2E仍未验收。

## Resume schema 与训练 If-Match 门禁裁决：2026-09-21

规范未要求Resume请求必须使用命名$ref；语义正确的inline oneOf及reason discriminator可直接验收，测试须同时支持内联和引用并检查分支/必需字段/unknown拒绝/运行时，不为测试结构偏好引入RootModel。

训练PATCH/validate/cancel属于新增未冻结操作，If-Match维持现公共输入上限256（min1/required/nonnullable）；资源etag字段max128不等同请求头输入长度上限。测试硬编码128应纠正；缺失428、空或超256为422、合法旧版本412，已冻结M1/M2约束不变。两项若仅为测试误判不单独制造新snapshot，保留失败证据并追加勘误，其他真实契约/BUILD-002阻断按独立复验结果处理。

## M3/M4 接口基线正式冻结：2026-09-21

总控以唯一candidate-05实际文件完成最终核验：根仅openapi/profile-packages，各build_id唯一；SOURCE_MANIFEST双向文件集合及逐文件hash、四summary构建输入/直接依赖清单/规范化契约hash一致。复核此前DTO、扁平分页、严格空对象、I/E与202 Location等关键缺陷修复，结合独立65操作矩阵、兼容及隔离生成证据，实际写入contracts及index。

minimal25paths/26ops、sfc及cc各35/36均与M2字节不变；universal104/112，新增65操作，规范化SHA-256为cb4f9b92dde639fc5188a4d898de23efb1e20b22733722bd50d7eff580f84d1a。正式审查记录docs/V1_M3_M4_CONTRACT_FREEZE.json；旧M2基线保存至docs/contract-baselines/m2，专属升级比较不再使用会变化的active contracts充当旧基线。

前端负责人已接到生成类型和知识/训练/模型治理/助手Trace页面接入通知，测试负责人负责独立验证。仅接口基线与已记录组件/物理装配范围通过；真实MySQL/Redis/Dramatiq竞态恢复、PydanticAI provider成功、科学黄金样例/独立评测、完整传递锁/平台wheel/干净离线安装和成功业务E2E继续未验收，仍非release-ready。旧失败快照保留且不可交付。

## Worker 生产接入与健康检查裁决：2026-09-21

后端组件测试报告365通过/1跳过，不构成真实Worker就绪验收。总控核对API §5.3、worker_cli、task_core/service及OutboxEvent后决定保持队列消息严格为job_id/attempt/dispatch_id；模块、event_type和resource_ref仅从数据库读取并经profile静态注册表分发，不另建消息协议或修改冻结HTTP基线。生产handler缺失继续列未完成。

公共任务服务实际还产生job.execute/job.cancel，原盘点仅含prediction.execute/assistant.run并不完整。助手需事务关联Job，执行时查run_id；训练专属outbox可暂保留存储并适配相同三字段，但同一事件只能有一个dispatcher所有者。知识/模型治理的异步接口须补真实producer/handler。分发器仅认领当前profile支持事件，保留DB事件类型，PROCESSING崩溃恢复及过期所有者写入须由租约/fencing约束。AsyncSession.run_sync仅用于同步repository数据库桥接，不能包裹长计算或LLM网络调用。

共享compose原test -s ready存在残留文件误报风险，已改为python -m chrom_platform.worker_cli --healthcheck，README同步约定只读身份校验及非零失败。后端负责人负责实现CLI并将PID复用/容器重启的进程身份纳入检查；仅存活PID不足以验收。该命令实现及Linux真实验证在独立测试通过前保持待验收，未支持它的旧镜像健康检查失败，不作为已交付。测试负责人已接到相关范围，真实数据库/队列/模型成功链路仍未验收。

## 用户依赖环境授权更新：2026-09-21

用户明确允许修改SFC环境以完成依赖冲突处理，条件是不得修改SFC中的TORCH版本，其他库的必要调整可接受。本授权取代此前仅backend/.venv安装的待审批方案；后端负责人安排单一Luna环境写入者直接处理D:\Anaconda\envs\SFC，LR及全局环境配置不在变更范围。

执行前保存实际包清单、Torch完整版本（含CUDA构建标识）和已有科学模型基线；依赖解析约束保持Torch版本不变，不卸载重装或替换Torch。仅处理本任务所需依赖，旧50包隔离环境清单不能作为SFC完整同步锁。执行后记录版本差异与import/pip check/Torch CUDA及模型回归，由测试负责人独立复核。真实数据库、队列、Linux Worker、LLM与科学成功链路验收仍单独进行，不由安装通过替代。

## 当前源码与历史快照边界：candidate-06 生成决定（2026-09-21）

新增0007/0008迁移、profile选择、Worker及M3/M4任务producer后，candidate-05继续只读保存为历史验收及HTTP契约基线，不能代表当前源码物理装配。总控同意后端在全部写入结束、相关回归通过后单一生成candidate-06，以输入/输出指纹及原子提升保证一致性；不得覆盖05或自行改active contracts。

测试负责人独立核对唯一canonical布局、SOURCE_MANIFEST精确文件集合与hash、Worker裁剪/独立导入、四profile HTTP normalized不变。迁移预期minimal保留核心闭包，sfc/cc为0001+0002+0007，universal为0001..0008；须检查真实revision/down_revision/merge父链及正确head，不以文件名集合代替拓扑验证。06尚待生成和验收，真实MySQL迁移/队列/Worker成功链路不由静态通过替代。

## candidate-06 阻断与修复分工：2026-09-21

后端最终审计发现Worker CLI装配遗漏、坏JSON在隔离前异常、minimal部署仍强制Worker，以及Training/Assistant恢复/各Worker共享生命周期与持久Outbox租约缺口；既有定向105项和388通过/1跳过的结果未覆盖这些问题。candidate-06暂不接受；修复和独立回归后生成新candidate-07，保留05和06历史，不覆盖HTTP契约。

总控已补deploy/v1/.env.example空WORKER_COMMAND，最小离线装配测试2项通过；生产入口仍需真实验证。临时委托后端负责人指定唯一Luna修改scripts/assemble_offline_bundle.py及verify_offline_bundle.py，负责迁移真实拓扑、Worker物理装配及minimal服务/镜像/env裁剪，禁止其修改共享compose/README/env。脚本另须拒绝非空/链接/输入重叠/发布目录输出，禁止清空任意旧output_dir，失败不能污染输入。测试负责人负责相应独立回归。

新增生命周期修复可能需要0009迁移；07按实际经审查revision/down_revision父链和选中模块闭包验收，不能沿用06文件数量作为通过依据。总控曾误判deploy/v1/test_offline_bundle.py的parents[2]，最小执行已证实原路径正确，尝试修改已立即恢复，不列为原产品缺陷。

## 离线装配失败报告验收边界：2026-09-21

测试报告candidate-07输入无效时在新output留下OFFLINE_BUNDLE.json并判P2。总控核对脚本及既有交付说明后明确：在已通过安全路径检查的新输出目录仅保存release_ready:false和blockers诊断是允许且有意的行为；不得修改输入或旧产物、不得输出ZIP或成功包。不能将“失败不污染输入”扩展为“禁止任何失败报告”。测试负责人须保留历史失败证据并追加期望勘误、复核实际文件集合和sentinel；如有其他真实污染另列，不为该误判改掉产品诊断能力。

独立物理门禁继续逐项执行，不因互不相关失败停止后续项；控制流使用最小临时fixture，实际profile完整性单独核验。用于测试允许新输出的路径应在仓库外，避免仓库路径拒绝掩盖本欲验证的分支，并记录具体拒绝码、脚本路径和前后hash。

## 稳定源码独立回归通过，批准生成candidate-08：2026-09-21

测试负责人独立报告：backend/tests 61/61文件、409 collected，408 passed/1 skipped/0 failed或timeout，唯一skip为显式外部TLC真实模型烟测。lease/registry独立门禁16通过，覆盖旧owner失败/取消不投影、双owner重领、迟到CAS、最后检查后失租、operation_key传递及同操作重投；healthcheck/0010/profile/PublicErrorSummary检查通过，源码/Alembic/deploy指纹稳定，无剩余已知P1/P2。

总控批准后端单一生成不可变candidate-08并交测试核验物理构建；05/06/07不覆盖，active HTTP契约不变。08须绑定此次实际输入指纹并通过迁移父链/profile闭包、Worker入口与静态生产注册、四profile物理裁剪/独立导入及精确hash验证。此时08尚未生成验收，不由源码通过推断快照通过；真实MySQL/Redis/Dramatiq多进程、实际外部副作用和科学成功链路、实际离线bundle及reparse限制继续单列未验收，不可发布。

## 用户批准优先上线预测与助手，已下发实施：2026-09-21

执行依据docs/PRIORITY_LAUNCH_PLAN.md；本机浏览器真实使用优先，五类预测全部13个工具入口及助手预测、已有数据摘要、模型登记草稿/查询。本轮不增加任意文件摘要，不实施chiral_lc。Knowledge/Training/完整模型治理和离线交付等延期，保留在途有界收尾与checkpoint，不再继续全平台候选整改循环。

已向既有前端、后端、测试负责人下发checkpoint→真实成功链路任务，明确Sol medium监督、Luna high执行及单一文件写入者。三方checkpoint请求路径和状态记录于docs/PRIORITY_CHECKPOINT_INDEX.json，尚未收到材料前不标完成。原候选08在途允许结束，本次检查快照目录仍只到07，不能宣称08已生成验收。

总控核对当前本机MySQL/Redis容器healthy，9012/5173/8080未发现监听，Docker本地镜像仅MySQL/Redis；Linux Worker及真实模型/LLM尚需接通。SFC Torch2.6.0+cu124保持，既有安装授权仅按原范围执行。总控接管优先版契约与本机部署协调，旧发布产物只读。
