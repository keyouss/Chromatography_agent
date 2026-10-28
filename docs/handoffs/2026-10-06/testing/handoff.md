# 测试负责人交接（2026-10-06）

核对时点：2026-10-06 15:51（Asia/Shanghai）。本文是测试负责人只读盘点与暂停交接；工作区文件、证据日志、Git 元数据、数据库/容器/服务均未由本次交接修改或启动。绝对根路径：D:\Jupyter\Chromatography_agent。

## 负责人、会话与暂停

- 测试负责人：会话 01a0bf2f-10c1-7d51-b9c9-6876ed36a26c；负责独立检查、测试分层、证据哈希和接受边界；本交接文件是其唯一写入。
- 架构负责人：会话 01a0be42-382e-73a1-9abe-55f069bd35ad；负责总架构、契约/候选协调及实施边界。
- 前端负责人：会话 01a0be50-6eef-71a3-a4d0-41d8678c6596；负责前端实现、契约类型接线及浏览器验证。
- 后端负责人：会话 01a0be51-01d9-7b80-9759-e194b8c6017c；负责 API、数据库迁移、Worker 与后端实现。
- 用户于 2026-10-06 发出同一暂停指令；本地没有分别证明上述负责人精确到分钟的停机时间，故不臆造。只读检查时，活动代理列表仅显示总控 /root 与本测试交接任务；没有发现仍运行的负责人子代理。其他 AI 接手前应先确认这些会话仍处于暂停状态。

## 当前优先级、依赖和授权边界

最高优先级是暂停三位负责人及全部实施/验证任务，并提供可供其他 AI 对照 PRD 和契约审查的自包含交接。此前 V1 M0–M5 实施与验收服从此优先级；除本交接必要只读核对外不继续工作。需恢复时，由总控明确恢复范围并安排负责人协作。

冲突顺序遵循仓库 AGENTS.md：用户当前指令 → docs/PROJECT_PRD.md → docs/PROJECT_FRONTEND_BACKEND_API_SPEC.md → 原型 → 现有代码。关键边界：

- PRD §0.1 要求先读范围/架构/构建裁剪/安全/验收，再读 API；preview_coding_runtime.html 只是后续研发助手布局参考，CODING_AGENT_UPGRADE_PLAN.md 仅用于理解未来 Coding Runtime 边界。它们不能授权本轮实现 Workspace、Sandbox、Coding API 或任意代码执行。
- PRD §17–18 与 API §24 要求区分契约/静态/组件与真实数据库、队列、模型及浏览器流程证据；PRD §19 和 API §25.2 禁止用示例或 mock 补造真实科学能力。
- 生产 B、实际模型推理/科学验收、依赖安装或升级分别仍需其原有明确授权。不得由隔离 A、SQLite、静态/单元测试或演示 UI 推导这些授权或结论。
- 不显示密钥、连接串、密码或客户数据。任何当前服务/DB 身份只能引用受控摘要；不得复制 secret 值到交接。

## 契约基线与不可变候选

正式合同是 contracts/ 和 builds/ 当前正式 profile；PRD 与 API 文档是产品目标基线。候选包不可冒充正式合同或发布。当前关键文件哈希（SHA-256）：

| 对象 | 绝对路径 | SHA-256 |
|---|---|---|
| 正式 Universal OpenAPI | D:\Jupyter\Chromatography_agent\contracts\universal-v1\openapi.json | 9D2D4033793BDA426BE5EA7BC9325D64EAD90417718967C336DF65C1D5047EFA |
| 正式 SFC OpenAPI | D:\Jupyter\Chromatography_agent\contracts\sfc-v1\openapi.json | E88954FB90593596CBB99765DA03C99A6C4E0C01DB05095CD04A73F5FDCB0414 |
| 正式 CC OpenAPI | D:\Jupyter\Chromatography_agent\contracts\cc-v1\openapi.json | 3CFFBDFB7B6A34DB7A1B413635BA7330D31126C1729E59EB6434637B67F55599 |
| 正式 minimal OpenAPI | D:\Jupyter\Chromatography_agent\contracts\minimal-structure-v1\openapi.json | BF35AF8FD7661B9ED2C5709B1DEB8629D79AB0A3153F644AF8D649E4A73D63AD |
| 正式合同索引 | D:\Jupyter\Chromatography_agent\contracts\index.json | 33F12D2056F35682F4331B69AB9CFA5B6308DAE51F0682E6C0688EF804E368C2 |
| 正式 Universal build manifest | D:\Jupyter\Chromatography_agent\builds\universal-v1.json | 569613E69680A4458E1319449AD98C00E82519D4BCF9B0AEB930F58E24C3A332 |
| rev07 Candidate OAS | D:\Jupyter\Chromatography_agent\contracts\candidates\universal-v1-batch-revision-20261004-07\universal-v1\openapi.json | 41C071B83334F1BC6BCA83EBEB14A93F04A74518C32B61C16F3CB1B6897DF869 |
| rev07 Candidate manifest | D:\Jupyter\Chromatography_agent\builds\candidates\universal-v1-batch-revision-20261004-07\universal-v1.json | 72CE2B4498B1AE5279D0EB909F7EAA1C2CA8C144B41357ED032DAD37582D4B42 |
| rev07 source lock | D:\Jupyter\Chromatography_agent\testing\runs\2026-10-04-universal-batch-revision-20261004-07\source-lock.json | 70BCEDB686628AB006BE7EEF453369CD8496B41D23C38CE2655475E93F1A5F8A |
| rev07 semantic diff | D:\Jupyter\Chromatography_agent\testing\runs\2026-10-04-universal-batch-revision-20261004-07\semantic-diff-rev04-rev05-rev07.json | 02EDAB00E2381470FC73C06BA269C3DD8998FC31D5DC5527005CDDD40F3C5DAA |
| rev07 metadata | D:\Jupyter\Chromatography_agent\testing\runs\2026-10-04-universal-batch-revision-20261004-07\candidate-metadata.json | C23AA2690662386B91F14FE1F0A2179A73BC8A240BF4BDC0D64201152ACBF191 |
| rev07 physical universal source manifest | D:\Jupyter\Chromatography_agent\backend\.generated\universal-v1-batch-revision-20261004-07\universal-v1\SOURCE_MANIFEST.json | CEC35A86A8F33AAA299C38FFB29AE2D82025036C4BA5B7D3E1F5126119D79160 |

正式 universal manifest SHA 对应当前不含 batch 的正式构建；rev07 是包含 batch 的 19-migration PREVIEW 候选（135 paths / 144 operations / 371 schemas，物理包 170 files，tree SHA FE15ED208802FC09D8C958C774CCA856B645615E0DE5FAF100C935C59149682E）。rev07 仍标记 PREVIEW_NOT_FROZEN。元数据把 rev05→rev07 的 changed operations 写为 6，但固定 semantic diff 和完整对象比较为 8 operations、9 schemas；外部 errata 保留此更正，不原地改候选。rev06 原地重建漂移观察 SHA 为 C38BED2763606B6B1F9D6EA5CEF10C83D0EDCBBA5508A58283341D70FCD14BBD；不要把 rev06 旧 pin 或 drift 后文件当已验收基线。现有 SQLite 动态 QA 支持隔离 Candidate07 公开链路，但不改变候选 PREVIEW 状态。

Job SSE 尚有当前源码/生成包与正式合同的差异：实现及隔离四 profile 组装包含 stream 路由，并把 seq/next_seq/after_seq 改成字符串 cursor；正式 OAS 仍保留旧分页 integer schema。合同与前端接线须由责任人审定和同步，不能把候选物理装配 PASS 当合同 freeze。

## 测试层级与当前证据

状态按证据实际覆盖层级记录；没有以下内容的证据不得外推。

| 层级/主题 | 当前状态 | 依据及边界 |
|---|---|---|
| 静态合同/构建裁剪 | PASS（限 pin 与报告） | rev07 OAS/source-lock/物理包独立 QA；正式构建与 candidate 区分。metadata 计数差异已由外部 errata 更正；候选未冻结。 |
| 单元/实现级迁移防护 | PASS（隔离测试） | Alembic version-width/lock 定向 QA；29 tests、含 fake connection/SQLite/static SQL，不是真 MySQL。 |
| 隔离 SQLite / TestClient Confirmation | PASS（限 Candidate07 副本） | preview→create→batch submit/retry 正负链、绑定/陈旧快照拒绝、幂等重放、SQLite 事务回滚通过；没有覆盖 MySQL 锁隔离、Redis、Worker、撤销/过期、权限角色矩阵或并发。 |
| 第七 fresh MySQL schema，隔离 A | PASS（仅该演练迁移行为） | 0013→0019 在线轨迹、五态旧记录 repair、回滚/失败闭锁与重复运行证据；缺原始 docker inspect JSON，身份复核依赖脱敏摘要。非生产 B、非业务流或科学验收。 |
| 第八既有窄列 schema，隔离 A | 迁移动作有日志；总体 QA 未最终接受 | 第八 schema chrom_v1_migration_a_narrow_20261004_c2a7e91b4d6f 曾将 alembic_version.version_num 从 VARCHAR(32) 扩到 VARCHAR(128)，值仍为 0013_merge_m2_batch_export。后续日志显示 0013→0019 迁移和五态 repair 成功；最后审计因 Unknown column 'table_name' in 'field list' fatal。不得把迁移中段/终点日志等同最终审计 PASS；第八 independent QA 正待/刚派审，签字未见。 |
| 第九 unknown-head 真实 MySQL 负例 | NOT_RUN | 没有创建第九 schema，也没有实际在线未知 head 拒绝证据。现有 fake guard 单测 PASS 不能替代此负例。 |
| 模型/科学准确度、真实适配器 | NOT_RUN / 门禁关闭 | GC seed42 仅作者证据完整性/文案一致性 PASS；GC_PREPROCESSING_PROTOCOL_QA_APPROVED=False、verified_handle_issued=false。本轮没有真实模型预测或科学批准。 |
| Worker/Redis 及浏览器业务 E2E | NOT_RUN | Job SSE 有实现级 TestClient/SQLite、测试通过和四 profile 临时物理装配/health；未验真实 Worker、Redis、MySQL并发、Nginx/SSE代理或浏览器 EventSource 重连。FE 视觉 QA 实测布局和错误态，9012 未监听、API 请求返回 500/ECONNREFUSED，明确不是业务 E2E。 |

### 验收编号映射（按 PRD/API 原文核对）

PRD 编号来自 §18 验收清单；API 测试编号来自 §24。基础条文所在章节同时列明。映射说明需求覆盖关系，不表示该验收项已通过。

| 主题 | PRD V1 编号 | API 编号与正文 | 当前证据层级 / 未验部分 |
|---|---|---|---|
| rev07 batch confirmation、草稿变更幂等、失败行 retry | V1-08、V1-17 | API-05、API-06、API-15、API-16；API §3、§9、§21、§24 | Candidate07 隔离 SQLite/TestClient 公开链路 PASS，覆盖错绑定、revision/config 变化、同 key 重放、失败行状态变化和事务回滚。API-06 的并发请求、Redis/MySQL 下的幂等竞争及 Worker retry NOT_RUN。 |
| SSE 恢复、Job/Worker 重投与恢复 | V1-20、V1-21、V1-22、V1-23 | API-23、API-24、API-26；API §5、§17、§21、§24 | Job SSE 实现级 TestClient/SQLite 与定向测试 PASS；Last-Event-ID/事件窗口有组件证据。真实 Worker/Redis 重投、MySQL 并发、Nginx buffering 和浏览器 EventSource 业务恢复 NOT_RUN。 |
| 构建裁剪与 OpenAPI→TypeScript 生成 | V1-01、V1-02、V1-32 | API-01、API-34、API-35；API §4、§20、§24 | 固定 pins 的四 profile 物理装配/裁剪、contracts verify 与类型生成检查有 PASS 报告。rev07 仍 PREVIEW；空环境完整安装验收、候选冻结及所有页面业务 E2E 未完成。 |
| GC/真实模型与科学验收 | V1-07；PRD §19 真实材料门禁 | API-12；API §8、§24 | GC seed42 只读证据完整性 PASS，不是模型准确度或可用性批准；GC 科学门禁仍关闭。真实模型推理、独立科学评测和 production adapter NOT_RUN。 |
| MySQL schema 迁移、列宽与数据版本 | V1-10（业务数据版本不可变）、V1-33（部署/备份恢复；不等同 Alembic 列宽验收） | API §21、§24 的真实 DB/队列证据分层；没有专门的 Alembic schema migration API-编号。API-26 指 Worker 重投，不是迁移验收。 | 第七 fresh 隔离 A 的 0013→0019 在线迁移行为 PASS；第八已有窄列 32→128 且迁移日志到 0019，但最后 audit fatal、QA 未签收；unknown-head 真实 MySQL 负例 NOT_RUN。 |
| FE 视觉检查与浏览器业务 E2E | V1-06、V1-20、V1-32；PRD 未给单独视觉验收编号 | API-23（仅 SSE 恢复场景）、API-35（类型生成）；API §22、§24 | 视觉 QA 在 1440/390/320 视口检查布局/导航/错误态 PASS；后端不可用时显示错误正确。API 业务流程、成功态和浏览器 EventSource/Worker E2E NOT_RUN。 |

### 重要报告和哈希

以下相对路径均以 D:\Jupyter\Chromatography_agent 为根目录解析；SHA-256 是报告正文哈希。优先阅读其完整边界说明：

- rev07 固定合同/物理包独立 QA：testing/runs/2026-10-04-rev07-contract-independent-qa.md — 725DED55E2B37E4BDEE197BD98ACC163DB08A19294639AF8073947DBB8810B4A。
- rev07 Confirmation 隔离 SQLite 动态 QA：testing/runs/2026-10-04-rev07-confirmation-isolated-sqlite-dynamic-qa.md — A2D3F8A8F77644607E0643821E1A09FB327FA11220226F6AA45CEB41AECBCD59。
- rev07 acceptance errata 独立复核：testing/runs/2026-10-04-rev07-acceptance-errata-independent-qa.md — C5720BE705D4D21768A9F38C93816ADCE8686BDA5B6E60ED287E0E9686E1C8F2。
- 第七 fresh MySQL A 独立 QA：testing/runs/2026-10-04-mysql-0013-0019-a-seventh-schema-independent-qa.md — A6C3C54220545EA25376EF5FE5A7C4EBE21ADD797592CEF352C1346236D30B6D。
- 第八早期停在 0013 报告（后续已由日志推进，故仅作历史）：testing/runs/2026-10-04-mysql-0013-0019-a-eighth-harness-stop-at-0013.md — 3A868C4E24859A5BEAF64C59194C5CCFFA4056AF9CFC35E8B9CB7A86FB00058B。
- Job SSE 实施报告：testing/runs/2026-10-04-job-sse-implementation.md — E8AAE01CFA804DD5733661CE83DB2D12F921520E87C1A153A79CF20CBE556AEB。报告记载三个相关 pytest 分组 33/33、33/33、8/8 及 compileall 通过；均为组件/TestClient/SQLite 层。
- Job SSE 四 profile 物理装配独立 QA：testing/runs/2026-10-04-job-sse-profile-physical-assembly-independent-qa.md — 3DB9A68C56DC227235AF31194801F94F8905AA92185827B6BD4994892BBA070F。
- 前端 Preview 视觉修复独立 QA：testing/runs/2026-10-04-preview-style-fix-independent-qa.md — 27BBD5210386765C3222BFD4A47EB4F8CD36A97B364B6AB8A85D7330C82AA4D1；只验布局、导航、错误态、profile build/前端回归；后端 API 不可用，未完成业务 E2E。
- GC seed42 证据完整性 QA：testing/runs/2026-10-04-gc-seed42-evidence-integrity-independent-qa.md — 0CB23ECC542ACF4C15892F96092097AF96A552148EA2A5FA5134C2432FB8AD0E；不是模型或科学有效性批准。
- version width/revision graph 静态 QA：testing/runs/2026-10-04-alembic-version-width-revision-graph-closure-qa.md — 374A18F8C76B5C809596B298F8030379911A03AC5576769CBD0AA8C61105F71A；真实 MySQL NOT_RUN。
- MySQL lock/version width 最终 pin QA：testing/runs/2026-10-04-alembic-version-width-mysql-lock-final-pin-qa.md — 9E939ABADB1D71C0DF26BBABF39C6657C9F7E7A89CF051CFCD18E7AF866F4DC1；29 passed 为隔离测试，无 MySQL 在线证据。

### 第八现场日志锚点

证据目录：D:\Jupyter\Chromatography_agent\backend\.generated\migration-a-0013-0019-6d66cb5c3b46\eighth-c2a7e91b4d6f。

- eighth-online-widen-target-0013.log SHA-256 0BD59B4A58D6C58B02B331F1F2DC88D32C2AAFB71EA3EB3309EBBEBDCE0CB698：目标精确限定第八 schema；VARCHAR(32) → VARCHAR(128)；版本值仍为 0013；即时回读成功。
- eighth-shrink-width32.log SHA-256 76776E3253E40027D19CA5C6D1A2B82116F177E970AE6822926C46E417737229：运行前窄列状态日志，记载仅该 schema 的 128→32 缩列和参数。它属于已记录的测试准备；不要重复执行。
- eighth-online-resume-0013-to-0019.log SHA-256 4493074548579496DD5E988768E83964AB963430801C994F683FA8FBE8704489：显示从 0013 续演，0019 多个负例 fail-closed，成功修复五态数据、重跑幂等，终端 version=0019；但随后的 final audit query 报 OperationalError 1054 Unknown column 'table_name'。日志标题/run_complete=PASS_CONTROLLED_RESUME 不能覆盖最后 fatal 或代替独立 QA。
- 该 A schema、数据、容器/卷和受控 secret 现场按报告保留；本交接没有连接、清理或重跑。这里不记录 secret 值。不要重建该 schema、重复播种五态夹具或再执行缩列/迁移；接手人先等待第八 QA 独立结论和明确恢复授权。
- 第九 unknown-head schema 尚未创建。不得因该场景方便而擅用正式/生产 schema。

## Git、运行现场与恢复顺序

.git 隐藏目录存在。普通 Git 读取因 dubious ownership 被拒；命令级 -c safe.directory='D:/Jupyter/Chromatography_agent' 的只读 rev-parse --is-inside-work-tree 返回 true，分支为 main...origin/main。只读 status --short 显示大量既有 modified、deleted、staged 与 untracked 项，范围包含旧 Service、Agent、权重记录和新平台目录；其中还报告若干 .pytest-tmp-* 目录访问拒绝。本次没有改 safe.directory 全局配置，没有清理这些内容，没有提交，也没有创建 Git checkpoint。交接文件只记录证据，不代表工作区干净或可安全重置。

当前资料没有一份经本交接新采集的实时进程/端口、MySQL/Redis、Worker、模型服务或容器快照。不要据此声称服务在线/离线；已有 QA 的容器身份、数据库和 secret mount 只以报告中的脱敏摘要为准，凭据值没有收入本文。

恢复工作时按此顺序：

1. 重新确认用户是否解除暂停及允许的范围，再检查负责人会话的实际状态。
2. 先读取本 handoff、PRD、正式 API spec、AGENTS.md，校验上述 formal/candidate 文件 SHA；rev07 维持 PREVIEW。
3. 接收并独立审阅第八 MySQL QA 的新报告及附件 hashes；准确区分 0013→0019 迁移日志、最后 audit fatal 与 QA 接受决定。未知-head MySQL 负例须作为独立、隔离场景处理并经授权后运行。
4. 保持正式生产 B、真实模型科学验收、Worker/browser E2E 为分开的待办；按实际运行证据逐层关闭。
5. 恢复前端/后端 owner 协作前，先核对 SSE formal contract 与生成候选 schema 的差异、FE cursor 接线状态、rev07 errata 与冻结门禁；不得修改已绑定的 Candidate07 或 deliverables/ 正式发布版本。

待接手决定/批准项：第八 QA 是否接受及具体残余缺口；是否另行授权创建隔离第九 unknown-head schema 负例；是否启动生产 B 或真实模型验收；是否通过正式变更流程同步 SSE cursor/API 与 Candidate07 元数据更正并推进冻结。未获相应授权前，暂停状态持续有效。

