# 2026-10-04 Batch 候选冻结 QA 补充复核

日期：2026-10-04（Asia/Shanghai）  
范围：独立复核后端交接的两个 P1 证据，并核对候选测试集合与已执行证据。只读源码/报告并运行隔离组件测试；未修改代码、contracts、builds，未迁移数据库、启动服务或发请求。

## 结论

- **P1-001：PASS（组件级关闭证据成立）**。0013 遗留行没有完整的 0014 resolved snapshot 时，Worker fail closed；测试证明不调用 adapter、不改变旧行内容/状态，也不把空 batch 标成成功。服务端提供重新 validate 生成新快照的代码路径，submit 只接受 READY 验证及有效 confirmation。
- **P1-002：PASS（组件级关闭证据成立）**。batch-enabled worker 启动前验证 ArtifactStore 已配置、接口完整，并以随机 server key 做 put/readback/size/SHA 校验和 remove；异常 fail closed。测试覆盖缺失/不合规 store、临时根写读删探针，以及旧 lease 清理不得删除新 attempt 已发布文件。
- **冻结建议：可建议冻结 `batch-complete` OpenAPI 候选，限于候选契约版本/hash。** 候选 provenance 与 additive semantic diff 已通过前序独立检查，本补充复核没有发现两个 P1 仍开放的组件证据。冻结不得被描述为 batch 已开放或可部署。真实 0013→0014 migration、部署 `ARTIFACT_ROOT`、生产 API/Worker/导出仍是单独的部署开放门禁。

## P1-001：0013 旧行 guard 与重新验证路径

当前源码行号已复核：

- [batch_worker.py](/D:/Jupyter/Chromatography_agent/backend/src/chrom_platform/modules/prediction/batch_worker.py:94) `_assert_resolved_snapshot` 检查 READY validation 的 item 数与有效/无效计数、每个有效行的 resolved snapshot/hash、aggregate hash，以及每行结果投影与 validation item 一致；缺失/不完整的旧绑定抛 `BATCH_LEGACY_DATA_REQUIRES_REVALIDATION`，结构或 aggregate 不一致抛 `BATCH_VALIDATION_SNAPSHOT_INVALID`。在 `execute()` 变更 Job/Attempt 为 RUNNING 前调用此 guard（153–155 行）。
- [batch_service.py](/D:/Jupyter/Chromatography_agent/backend/src/chrom_platform/modules/prediction/batch_service.py:704) `validate()` 对当前草稿建立新的 validation/job，绑定草稿 revision、config hash 和模型快照；`run_validation()` 在 731–799 行重新读取 artifact，按模板解析行，并物化 `resolved_input_snapshot`、逐行 hash、aggregate hash、row-result 投影与状态。`submit()` 在 905–910 行仅允许 validation 和 draft 均为 `READY`，随后仍要求 confirmation 服务/绑定。
- [test_batch_worker.py](/D:/Jupyter/Chromatography_agent/backend/tests/test_batch_worker.py:320) 的 `test_0013_legacy_batch_rows_are_preserved_but_cannot_succeed_without_resolved_snapshot` 构造 0013 风格的 NULL 绑定行；断言抛出指定错误、adapter 调用数为 0、旧行仍为 QUEUED 且输入不变、Job 和 batch 未转为成功/执行态。

本轮实际执行该定向测试，结果 **1 passed**。它使用测试数据库模拟迁移后 NULL 列和旧行，证明 guard/fail-closed 逻辑，不是对真实 0013 数据库运行 0014。正常的重新 validate→submit 端到端链也未在真实 API/DB/Worker 执行；以源码路径与该 P1 专项回归支持组件缺陷关闭。

## P1-002：ArtifactStore 启动探针与 lease race

当前源码行号已复核：

- [worker_cli.py](/D:/Jupyter/Chromatography_agent/backend/src/chrom_platform/worker_cli.py:499) `_validate_batch_artifact_store()` 要求 store `configured is True`、合法 `max_size_bytes` 及可调用 `key_for/put/read/remove`；用 UUID 生成探针 identity，校验 key、回写大小和 SHA，读回字节完全一致，并在 `finally` 精确删除探针对象。配置、probe 或删除失败均转为 `BATCH_ARTIFACT_STORE_UNAVAILABLE`。`build_production_handlers()` 对 `batch_enabled` profile 在 handler 注册前调用该检查（566–573 行），缺少显式依赖时按 `ARTIFACT_ROOT` 创建 `LocalArtifactStore`。
- [test_worker_cli.py](/D:/Jupyter/Chromatography_agent/backend/tests/test_worker_cli.py:284) 覆盖未配置和不合规 store 拒绝；337–343 行在 pytest 临时根执行真实 `LocalArtifactStore` 探针并断言探针文件已清除；346–378 行覆盖从临时 `ARTIFACT_ROOT` 注入 store、建 handler 和清理探针。
- [batch_worker.py](/D:/Jupyter/Chromatography_agent/backend/src/chrom_platform/modules/prediction/batch_worker.py:657) 导出对象 storage identity 包含 attempt 与 fencing token；异常清理前先查已提交 Artifact 的 storage key，仅删除未被新 attempt 引用的 key（692–700 行）。[test_batch_worker.py](/D:/Jupyter/Chromatography_agent/backend/tests/test_batch_worker.py:51) 通过交错的测试 store 令旧 attempt lease 失效、完成 attempt 2 发布，再触发旧 attempt 清理；断言只移除旧 key，新 artifact 仍 AVAILABLE，字节数/SHA 匹配。

本轮实际运行 5 项 ArtifactStore 专项测试（缺 store、未审查/未配置 store、临时根 probe、配置根注入 probe、旧 lease/新 attempt race），结果 **5 passed**。所有文件写入都在 pytest 临时目录，不触及部署 `ARTIFACT_ROOT`。

## 测试集合、实际执行文件与缺口

候选 `generation-summary.json` 记载的 144 项来自 `--collect-only`，明确没有执行：

| 收集组 | 收集数 | 文件范围 | 实际证据与本次覆盖 |
|---|---:|---|---|
| batch/worker/store/router | 114 | `test_batch_contracts.py`、`test_batch_worker.py`、`test_worker_cli.py`、`test_artifact_router.py`、`test_artifact_service.py`、`test_prediction_router.py`、`test_prediction_production_worker.py`、`test_prediction_assembly_scope_storage.py` | 先前独立 QA 实跑前两文件 30 项；本次额外实跑旧行 guard 与 worker_cli/lease 相关 6 项（与前序 30 项有重合，不能简单相加为独立总数）。BE handoff 报告 112 项 executed，但本地当前候选目录没有该 112 项的逐项节点清单、完整输出及输入 hash sidecar，故记作“BE 报告，未独立逐项核验”。其余文件和 API/生产 worker 场景未由本补充重新运行。 |
| profile/OpenAPI/migration/topology | 19 | `test_manifest.py`、`test_openapi.py`、`test_migrations.py`、`test_worker_migration_topology.py` | 先前独立 QA 实跑这 4 文件，共同列下加 `test_prediction_profile_compat.py`，合计 21 passed；仍是当前源码/组件测试，不是四份发布包的隔离运行。 |
| profile compatibility | 2 | `test_prediction_profile_compat.py` | 上述 21 项包含该文件。 |
| OpenAPI contract gates | 9 | `contracts/test_check_openapi.py`、`contracts/test_freeze_openapi.py` | 本补充未重跑。前序独立检查静态核了 14/15/39 additive diff；因此本报告只支持静态候选语义差异，不把 9 项测试说成通过。若冻结审批要求执行 contract gates，应单独补跑并绑定候选 hash；这与本次已通过的两个 P1 组件缺陷、以及生产部署门禁分开记录。 |

因此，**144 仅为更宽范围的收集总数**；BE 报告 112 个已执行与 QA 前序 51 个已执行不是相同文件集合；本次 6 个 P1 定向测试用于具体关闭断言，不把这些数字相加或把 collect-only 记成 PASS。就这两个 P1 所需断言而言，当前逐点证据足够通过；其他列出的文件范围、真实 API/Worker 链与完整 144 集合没有被本补充声称通过。

## 冻结范围与部署开放边界

候选静态事实仍绑定原报告记录的 `candidate-openapi.json` SHA-256 `7d636941bd62ae45fd320feecb411395dcce1547920c0df58c4272f3da6cc03e` 与 normalized SHA-256 `0586d1ef8213e654ce9caa81cbb89fe13becb9b8fc576fa981270920c09f0386`：相对 active 加 14 paths、15 operations、39 schemas，既有内容无删改；四个正式 build manifests 未被本轮修改。候选冻结只应发布这组契约 hash 和清楚的未部署状态。

真实数据库 revision/head、0014 DDL 对现有 0013 数据的行为以及部署进程实际 `ARTIFACT_ROOT` 均未检查。测试中的 0013 数据为 SQLite/组件模拟，ArtifactStore root 为 pytest 临时目录。因此生产 batch 仍保持关闭；需要后续真实部署门禁验证，不能由契约冻结或组件 PASS 自动放行。


## 候选冻结直接相关的 OpenAPI contract gates（补充执行）

本节更新并覆盖上文“本补充未重跑”的状态。使用现有 `D:\Anaconda\envs\SFC\python.exe`，未安装依赖、未修改产品或契约文件、未迁移数据库、未启动服务，也未发 HTTP/业务 POST。

完整命令与工作目录：

```powershell
# 工作目录 D:\Jupyter\Chromatography_agent\contracts
& 'D:\Anaconda\envs\SFC\python.exe' -m pytest -q test_check_openapi.py test_freeze_openapi.py
```

结果：退出码 `0`；`9 passed, 8 subtests passed in 0.11s`。因此 generation-summary 中这组 9 项不再只是 collect-only：本次已对 checkout 的 contracts gate 实际执行并通过。测试验证范围仍是这些测试定义的本地 OpenAPI checks，不代表真实 API/Worker 或数据库行为。

执行后重算候选文件 SHA-256，并与候选 metadata / 本报告绑定值比较：

- `candidate-openapi.json`：`7d636941bd62ae45fd320feecb411395dcce1547920c0df58c4272f3da6cc03e`，与报告记录及 `candidate-metadata.json` 一致。
- `candidate-openapi.normalized.json`：`0586d1ef8213e654ce9caa81cbb89fe13becb9b8fc576fa981270920c09f0386`，与报告记录及 `candidate-metadata.json` 一致。
- metadata 仍标记 `candidate_only_not_frozen`，semantic status 为 `PASS_EXPECTED_ADDITIVE_BATCH_DELTA`。

本次 contract-gate 结果支持对上述精确 candidate hash 提出规范冻结建议；数据库 0013→0014 的真实升级与部署 `ARTIFACT_ROOT` 仍是独立生产开放门禁，不由该规范冻结或组件测试放行。
