# V1 后端状态与证据交接

更新时间：2026-09-21（Asia/Shanghai）

## SFC 依赖实施证据（2026-09-21）

用户已授权本轮只修改 `D:\Anaconda\envs\SFC` 中除 Torch 外的库；LR、全局环境、业务代码和 `pyproject.toml` 未改。最终实际版本为：`asyncmy==0.2.14`、`dramatiq==2.2.1`、`pydantic-ai-slim==2.31.1`、`pydantic-graph==2.31.1`、`openai==2.54.0`、`tiktoken==0.13.0`，以及其解析新增的 `genai-prices==0.1.7`、`logfire-api==5.1.0`、`jiter==0.17.0`。`pydantic-ai-slim==2.46.0` 的第一次安装结果已按兼容性调查收敛，不作为最终组合；其 `openai==3.16.2` 冲突已消除。证据目录为 `backend/evidence/dependency-install-20260921/`，包含 baseline、dry-run、安装日志、最终 freeze/conda、import、结构化工具 schema、Torch/CUDA 对照和 SHA 清单。

最终证据索引为 `backend/evidence/dependency-install-20260921/FINAL_EVIDENCE.md`；最终 pip 依据是 `pip-check-final.txt`，而 `pip-check.txt` 仅是 `openai==3.16.2` 首轮中间态。统一 Torch 对照为 `torch-final-comparison-v2.json`，并保留 46/46 独立 SHA 验证报告及全部过程证据。

Torch 恒等性保持：基线与最终均为 `2.6.0+cu124`、CUDA `12.4`、`cuda_available=true`、1 张 `NVIDIA GeForce RTX 4060 Laptop GPU`；`torch-final-comparison.json` 为 `exact_match=true`。关键科学包与后端模块 baseline/final import 均为 OK；`pydantic_ai` 的 `OpenAIProvider(base_url=...)`、`OpenAIChatModel` 和无网络结构化工具 schema 组件测试通过。最终 `pip check` 仍返回 1，但剩余 protobuf/networkx/Gradio/Kubernetes/OpenCV 冲突来自既有 SFC 基线；没有新增 OpenAI 冲突。该证据只证明包安装/import/本地结构化组件，不证明真实 MySQL、Redis/Dramatiq broker、Worker、OpenAI-compatible 服务或模型集成验收。运行中的服务如需加载新库，必须按部署流程重启；本轮未启动或重启服务。

## 当前结论

M2 contracts 仍保持冻结。冻结范围是契约基线，不是完整 V1 验收或发布批准；冻结记录见 `docs/V1_M2_CONTRACT_FREEZE.json`。candidate-05 是只读候选，等待总控决定是否冻结，当前不是 release-ready。

candidate-05 的绝对路径为 `D:\Jupyter\Chromatography_agent\backend\.generated\snapshots\m3-m4-v1-candidate-20260921-05\`，相对路径为 `backend/.generated/snapshots/m3-m4-v1-candidate-20260921-05/`。最新独立证据见：

- `testing/runs/2026-09-21-m3-m4-candidate-05-spec-matrix-revalidation.md`
- `testing/runs/2026-09-21-candidate-05-parity.md`
- `testing/runs/2026-09-21-snapshot-05-root-integrity.md`

后端全量测试当前为 **346 collected、345 passed、1 skipped、0 failed**。该结果及候选门禁只证明源码、静态契约和隔离组件边界，不等于规范冻结、真实基础设施、真实模型或发布验收。

## candidate-05 规范与物理完整性

candidate-05 universal 为 **104 paths / 112 operations**，相对冻结 universal 的 47 operations 新增 **65 operations**，规范分解为 Knowledge **K8**、Training **T14**、ModelCenter **M22**、Assistant/Trace **A21**；规范集合 missing=**0**、extra=**0**。candidate-05 的 65-operation 矩阵复验为 6 passed，运行时聚焦复核为 2 passed。

冻结 M2 旧操作与 candidate-05 的四 profile parity/兼容门禁通过：minimal `25/26`、sfc `35/36`、cc `35/36`、universal `104/112`；专属 parity 为 5 passed，M2 frozen compatibility 为 5 passed。CONTRACT-003 与 BUILD-002 已由独立测试**仅针对 candidate-05**关闭，不能外推到旧候选、未审范围、真实部署或发布状态。

candidate-05 根只有 `openapi` 与四个唯一 canonical profile 包：`cc-v1`、`minimal-structure-v1`、`sfc-v1`、`universal-v1`。四包实际文件集合与 `SOURCE_MANIFEST.files` 双向一致，逐文件 hash 一致，missing=0、extra=0；tracked files 分别为 67、47、67、130，共 **311 个包文件**。候选根非缓存文件清单指纹为 **323 files**，前后均为 `D90884119768F07FDF6FF2268BEF4E880F522CBF06A163E8EC1FC7049B08984F`，复验窗口未观察到候选漂移。

迁移集合为：minimal=`0001`；sfc/cc=`0001、0002`；universal=`0001..0006`。完整 revision chain、actual files↔SOURCE_MANIFEST、未选模块/Worker/schema/table 和直接依赖边界均通过 candidate-05 根级门禁。历史 `backend/.generated/profile-packages/*` 仍保留 BUILD-001 的多余 `0006_m4_assistant.py` 失败证据，旧目录禁止交付，不得作为 candidate-05 的替代物理基线。

## candidate-05 文件指纹

以下 SHA-256 均从 candidate-05 文件实际字节计算；normalized OpenAPI 使用 `contracts/freeze_openapi.normalize` 计算。profile 顺序为 `minimal-structure-v1`、`sfc-v1`、`cc-v1`、`universal-v1`。

| profile | raw OpenAPI | normalized OpenAPI | summary.json | SOURCE_MANIFEST.json | build-manifest.json | requirements-profile.txt |
|---|---|---|---|---|---|---|
| `minimal-structure-v1` | `e217447c96357b758c866306ee4074e736e6364dfb37d37fe2a9b05a2d41d454` | `f5c57950f6c0cfd0a619e375efb191549d78b1ef07da0e645a26b28456f6f45a` | `32522533fa8af866cf203512da87db0a819e2aa825fc89a7778bef38e0bb58c0` | `92e4478332c3a0cb05c5371b74fdf284a8f33e925acb4c406dd6cfff3e7af152` | `7723c27b8eb72057a3eedf12d8b681d95d03612a5fa88a3048335f6825d8eef1` | `9cb05eadbd0c1ab24f02f8f4dd4e88e4e95ea4849d819f13ce43a853e6e449ab` |
| `sfc-v1` | `8dfc88b1f04c16a827b7b04021ea84a14b963163191eb2f224058eaeaa809154` | `778ae39484eb6dfd48bebceb1d105e76dc3ded832470ca46c2a71546a21a1f23` | `06c3464fec6281a6aca2db069d478f1aaca35a64319214589b9f0d6af8124a6f` | `3e0028576a641c374170a99479f02e0b43c6c02218d84f24c206c0a0071d8cdb` | `4bfbd76d9dabc77f3b1eb81c9419d6b512fa6931891443f9e2105d7afbb6a321` | `43435d3051b87df9e57090bb16937987cbf241ed2372e9c6d01e272445e70f7b` |
| `cc-v1` | `bd4665935a34b72d576705c5eb8abdb069f9168255087a330771c1230bfffba4` | `635d75970f64e6d1e25f1bd0a82af4b3b37181f30f174a88c8cd2df5f901330d` | `67181b755ea4022082de83442da5a3f7490f310477e939a70a699ebe74e3dd32` | `38b15d3143c3d63c72c636aa16102f21934cfe9d0a09a200bc27f9c8df52a898` | `dc75a514b283f811d2bdbf0d0b27f3d4e3aee3dcb4c3b1e1138494e41d0ac2cb` | `6f0a6c7e64a26ef8a7868c2e9bab5974c7c198b5f2d8cabec645849d91c0ea38` |
| `universal-v1` | `4f4d8a1a87ce84b7e1964933956ba79ee8ad7a38ce79c4b25d17440e3612c7ab` | `cb4f9b92dde639fc5188a4d898de23efb1e20b22733722bd50d7eff580f84d1a` | `aa963c6e1c2973f9dc376e39c0bee47ab08b6d0a3f7113a1b640dad22ba50148` | `4872e6de3e56ea8593c1f4ffad5795a5fbf1418fab874891df65cd91bb569bd9` | `3d86e9e8d1f4b16d4b7029feba9c757d343f2b84bd01298cf1643f533147d031` | `246ddc31ef92273e8e59eca9c49ea6fbdb2100dc2f8e8635e464490f7d701433` |

`requirements-profile.txt` 是与 manifest 同快照核对的固定版本**直接依赖清单**，不是完整传递依赖锁；上述一致性不代表平台 wheel 集、干净环境安装或离线安装通过。

## M4 SQL/组件证据边界

`backend/tests/test_m4_assembly_integration.py` 与 `backend/tests/test_m4_integration.py` 的组件聚焦复验为 **24 passed**。证据覆盖：

- SQLAlchemy 组装的持久幂等键与 conversation/message replay；Outbox 写入、消费、成功/失败状态和缺 provider 的 `DEPENDENCY_UNAVAILABLE`；
- Agent Worker 的 outbox 消费、provider transport retry、两次 transport attempt 的 LLM call 生命周期、usage 持久化和 context snapshot；
- execution ledger 的 operation key、revision fence、未知状态 `UNKNOWN` 和外部 job replay 阻断；
- Trace 的 run/project/owner scope、权限、游标、事件/SSE replay、脱敏和 provider request/context snapshot 成对记录；
- SQL repository 的 project scope、非空 flush、Trace store port 绑定，以及取消/恢复和外部 job 独立存活语义。

这些证据使用 SQLite、in-memory/测试 provider、SQLAlchemy 组件和隔离 TestClient；它们不是生产 MySQL 约束/锁竞争、Redis、Dramatiq Worker、真实 PydanticAI provider 或端到端部署证据。真实 MySQL、Redis、Dramatiq/PydanticAI 成功运行、真实模型资产与推理、训练执行、模型治理、交付资产授权、干净离线安装和成功业务 E2E 仍未验收。

## M5 与发布判定

M5 的 Nginx、Compose、API/worker/frontend/MySQL/Redis 服务清单、`.env` 端口、迁移/重启说明、profile 选择、离线包检查和 ZIP/SHA-256 校验组件已完成静态基线，详见 `docs/M5_OFFLINE_DELIVERY.md`。尚无真实部署、真实迁移、完整离线 ZIP 或干净离线安装通过证据；缺真实模型资产、模型合同、镜像锁或 worker 入口的 profile 必须保持 BLOCKED。

当前发布判定为 **非 release-ready**。M2 contracts 仍冻结；candidate-05 的 65-operation 矩阵、四 profile parity、M2 compatibility、CONTRACT-003/BUILD-002 局部关闭、346/345/1 测试结果和 M4 组件证据，均等待总控冻结决定，不能替代真实 MySQL/Redis/Dramatiq/PydanticAI、真实模型/训练/治理、交付资产、离线安装和端到端验收。

### M5 Worker CLI 追加状态

本轮 Worker CLI 变更范围严格为三个文件：`backend/src/chrom_platform/worker_cli.py`、`backend/tests/test_worker_cli.py`、`deploy/v1/WORKER.md`。`deploy/v1/.env.example` 中前一执行者新增的 `WORKER_COMMAND` 行及 `deploy/v1/README.md` 末尾新增的 Worker CLI/ready/fail-closed 专属段落已撤销；Compose 和其他部署文件未改。

Worker 专项 `test_worker_cli.py` 与 profile `test_manifest.py` 共 **22 passed**；独立复核总结果为 **365 passed、1 skipped、0 failed**。最终文件 SHA-256：

- `backend/src/chrom_platform/worker_cli.py`: `7361B4ABA76BACCC50EAFD4733E3FDD47FF09757BA60C6CF9AB8C7CEC41248F4`
- `backend/tests/test_worker_cli.py`: `A96D9A333B62DA597383784F4BFA0CD8781C99881BF0A360AFCB684817BBA337`
- `deploy/v1/WORKER.md`: `58C0291427CB041F7B36115089B0B1C2385A2C4E3A0D07EEA569E44B569B229C`

Worker 仅接受既有四个 profile allowlist，使用延迟 Dramatiq 导入和 async SQLAlchemy outbox；默认 `main()` 没有安全的生产 handler factory，会以 `WORKER_HANDLER_BINDING_UNAVAILABLE` fail-closed，注入 runner 只用于组件边界测试。`asyncmy`、Dramatiq、真实 Redis/MySQL broker/数据库和生产 Worker 仍未验收，也未安装或升级依赖。当前配置校验在 runtime 初始化之前执行，早期配置失败时可能遗留旧 marker；部署健康检查仍必须解析 ready JSON 并校验 PID 存活及实例归属，不能只检查文件存在。

## candidate-06 构建交接

candidate-06 已由 `scripts/build_candidate_06.py` 从当前源码原子生成：`D:\Jupyter\Chromatography_agent\backend\.generated\snapshots\m3-m4-v1-candidate-20260921-06`。
源码/构建输入 fingerprint：`409326CDE2F57058EA8594A1A792F39F1FB31F3F5003AA68D28B47A4188EEAD0`；候选根 fingerprint：`11047711723D1D305B7B79470593ED075C20A0EF0CC800E7FE27F2FB097512B7`（338 files）。
OpenAPI normalized digest 逐 profile 与 active contracts、candidate-05 相同；contracts 未修改。迁移闭包为 minimal=`0001`、sfc/cc=`0001+0002+0007`、universal=`0001..0008`，universal `0008` 使用 `0006/0007` merge parents。
candidate-06 的 sfc/cc/universal 包含当前 Worker CLI 与选中 worker 源码；生产 handler binding、真实 MySQL/Redis/Dramatiq 尚未绑定或验收，worker 状态保持 BLOCKED，未宣称 READY。精确制品 SHA 与未完成功能见 `backend/CANDIDATE_06_HANDOFF.md`。

## candidate-07 构建交接

candidate-07 已由 `scripts/build_candidate_07.py` 原子生成：`D:\Jupyter\Chromatography_agent\backend\.generated\snapshots\m3-m4-v1-candidate-20260921-07`。
输入 fingerprint（before/after 相同）：`A7CB3C184BBB04C0317CA4EAD43838284C3F22A3F8B7C3FDD8FC773F85D26C63`；候选根 fingerprint：`E6172E36D08F8D9F9AEB46C0B971A98E732D0A42C3683E18CD4A246CB44D54CD`（342 files）。
OpenAPI normalized digest 逐 profile 与 active contracts、candidate-05、candidate-06 相同；contracts 未修改。minimal=`0001`，sfc/cc=`0001+0002+0007`，universal=`0001..0009`、唯一 head=`0009_training_outbox_lease`。
sfc/cc/universal 包含 worker_cli、选中 worker 和静态 handler 依赖；生产 handler、真实 MySQL/Redis/Dramatiq 未验收，worker 状态为 BLOCKED。精确 SHA 与限制见 `backend/CANDIDATE_07_HANDOFF.md`。

## 后端优先切换 checkpoint（2026-09-21）

已建立只读审计 checkpoint：`D:\Jupyter\Chromatography_agent\backend\checkpoints\pre-priority-launch\revision-001`。其快照 59 个文件、592013 字节；根 fingerprint 为 `207a49edac05b7b4f8f2ee2886ecdb00e6b4ae15eb4f5520de60b083a4dcc3e0`，`SNAPSHOT_MANIFEST.json` 清单 hash 为 `ffc542bd528e8028492a369081dcc61324073eab4299c48fc43b3d2eb8ca8398`。该 checkpoint 保留五类 handler/lease、0010 唯一 head、冻结 OpenAPI 与定向测试证据；未包含 `.env`、密钥/token、模型资产、generated 大快照、数据库、上传、日志或 zip。真实 MySQL/Redis、Linux Worker、真实模型/LLM 与生产 E2E 仍保持阻塞边界。
