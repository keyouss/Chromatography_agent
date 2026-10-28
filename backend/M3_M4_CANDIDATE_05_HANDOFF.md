# M3/M4 candidate-05 接口与组件证据交接

日期：2026-09-21（Asia/Shanghai）  
候选根：`D:\Jupyter\Chromatography_agent\backend\.generated\snapshots\m3-m4-v1-candidate-20260921-05`  
仓库相对路径：`backend/.generated/snapshots/m3-m4-v1-candidate-20260921-05`  
本材料只绑定 candidate-05 实际字节、实际脚本结果和后端测试证据；没有修改产品源码、候选、contracts、testing 或冻结记录。

## 冻结结论与边界

candidate-05 已作为 M3/M4 interface baseline 冻结到 `contracts`。冻结记录为 `docs/V1_M3_M4_CONTRACT_FREEZE.json`，其字节 SHA-256 为 `A947062146176B98EF117E9731D3DA74FEF479235BBB355967DFFF34FC222BF0`。universal 冻结文件为 `contracts/universal-v1/openapi.json`，当前字节 SHA-256 为 `CB4F9B92DDE639FC5188A4D898DE23EFB1E20B22733722BD50D7EFF580F84D1A`；其 normalized SHA-256 为 `CB4F9B92DDE639FC5188A4D898DE23EFB1E20B22733722BD50D7EFF580F84D1A`。

M2 归档基线仍位于 `docs/contract-baselines/m2/`，其中 universal M2 OpenAPI normalized/字节 SHA-256 为 `28EF8C678AB2110FC6BEF907F52C13EB52EBB62BF3973DBA57572F12A9F06A7D`。本交接结论是接口、DTO、组件和候选物理装配层冻结，不是 release-ready；真实 MySQL、Redis/Dramatiq、PydanticAI provider、真实模型科学评测、离线干净安装和生产业务 E2E 尚未被证明。

## candidate-05 实际包指纹

下表由 candidate-05 文件实际读取并用 SHA-256 计算；`raw` 是静态 OpenAPI 原字节，`norm` 由 `contracts/freeze_openapi.normalize` 计算，`summary` 是对应 `summary.json` 原字节，`build`、`SOURCE`、`requirements` 是 canonical 包内对应文件原字节。`paths/ops` 也是实际 JSON 计数。

| profile | paths/ops | SOURCE_MANIFEST SHA-256 | summary.json SHA-256 | raw OpenAPI SHA-256 | normalized OpenAPI SHA-256 | build-manifest SHA-256 | requirements-profile SHA-256 | tracked files | migrations |
|---|---:|---|---|---|---|---|---|---:|---|
| `minimal-structure-v1` | 25/26 | `92E4478332C3A0CB05C5371B74FDF284A8F33E925ACB4C406DD6CFFF3E7AF152` | `32522533FA8AF866CF203512DA87DB0A819E2AA825FC89A7778BEF38E0BB58C0` | `E217447C96357B758C866306EE4074E736E6364DFB37D37FE2A9B05A2D41D454` | `F5C57950F6C0CFD0A619E375EFB191549D78B1EF07DA0E645A26B28456F6F45A` | `7723C27B8EB72057A3EEDF12D8B681D95D03612A5FA88A3048335F6825D8EEF1` | `9CB05EADBD0C1AB24F02F8F4DD4E88E4E95EA4849D819F13CE43A853E6E449AB` | 47 | `0001_m1_persistence.py` |
| `sfc-v1` | 35/36 | `3E0028576A641C374170A99479F02E0B43C6C02218D84F24C206C0A0071D8CDB` | `06C3464FEC6281A6ACA2DB069D478F1AACA35A64319214589B9F0D6AF8124A6F` | `8DFC88B1F04C16A827B7B04021EA84A14B963163191EB2F224058EAEAA809154` | `778AE39484EB6DFD48BEBCEB1D105E76DC3DED832470CA46C2A71546A21A1F23` | `4BFBD76D9DABC77F3B1EB81C9419D6B512FA6931891443F9E2105D7AFBB6A321` | `43435D3051B87DF9E57090BB16937987CBF241ED2372E9C6D01E272445E70F7B` | 67 | `0001_m1_persistence.py`, `0002_m2_prediction_data.py` |
| `cc-v1` | 35/36 | `38B15D3143C3D63C72C636AA16102F21934CFE9D0A09A200BC27F9C8DF52A898` | `67181B755EA4022082DE83442DA5A3F7490F310477E939A70A699EBE74E3DD32` | `BD4665935A34B72D576705C5EB8ABDB069F9168255087A330771C1230BFFFBA4` | `635D75970F64E6D1E25F1BD0A82AF4B3B37181F30F174A88C8CD2DF5F901330D` | `DC75A514B283F811D2BDBF0D0B27F3D4E3AEE3DCB4C3B1E1138494E41D0AC2CB` | `6F0A6C7E64A26EF8A7868C2E9BAB5974C7C198B5F2D8CABEC645849D91C0EA38` | 67 | `0001_m1_persistence.py`, `0002_m2_prediction_data.py` |
| `universal-v1` | 104/112 | `4872E6DE3E56EA8593C1F4FFAD5795A5FBF1418FAB874891DF65CD91BB569BD9` | `AA963C6E1C2973F9DC376E39C0BEE47AB08B6D0A3F7113A1B640DAD22BA50148` | `4F4D8A1A87CE84B7E1964933956BA79EE8AD7A38CE79C4B25D17440E3612C7AB` | `CB4F9B92DDE639FC5188A4D898DE23EFB1E20B22733722BD50D7EFF580F84D1A` | `3D86E9E8D1F4B16D4B7029FEBA9C757D343F2B84BD01298CF1643F533147D031` | `246DDC31EF92273E8E59ECA9C49EA6FBDB2100DC2F8E8635E464490F7D701433` | 130 | `0001_m1_persistence.py`–`0006_m4_assistant.py` |

candidate 根实际只有 `openapi/` 与 `profile-packages/` 两个直接目录；canonical profile 恰为四个 build id，没有 nested profile、重复 build id 或 `profile-packages/openapi`。根目录按相对路径、文件大小、文件 SHA-256 排序的实际指纹为 **323 files / `D90884119768F07FDF6FF2268BEF4E880F522CBF06A163E8EC1FC7049B08984F`**。构建器规则计算的 `backend/src` 与 `scripts` 生成源码 aggregate fingerprint 为 `29C978E4E9267D351249BCCCE69CECF79DF388CE2EC85F6C2EAA2ABD65D8FFF8`。四个包的 SOURCE_MANIFEST 双向文件集合、逐文件 hash 和 canonical runtime import 均通过；Python cache 不属于交付集合。

## OpenAPI 差集与 65 operation 绑定

candidate universal 相对 M2 universal 的冻结 47 operations 保留 47/47 条 route key，新增集合精确为 65 条，分组为 Knowledge 8、Training 14、Model Center/Governance 22、Assistant/Trace 21。candidate-05 的完整逐 operation 表、规范章节/行号、I/E、请求 DTO、成功状态、Location、列表 page 和实际 `$ref` 结果见 [`testing/M3_M4_OPERATION_MATRIX.md`](../testing/M3_M4_OPERATION_MATRIX.md)，该表不是手抄摘要。

candidate-04 缺失而 candidate-05 补齐的六条路由为：

| 模块 | 补齐路由 |
|---|---|
| Training | `GET /api/v1/training/experiments/{experiment_id}/events` |
| Training | `GET /api/v1/training/experiments/{experiment_id}/logs` |
| Training | `GET /api/v1/training/experiments/{experiment_id}/metrics` |
| Training | `GET /api/v1/training/experiments/{experiment_id}/artifacts` |
| Training | `POST /api/v1/training/comparisons` |
| Model Center | `GET /api/v1/models/{model_id}/artifacts` |

M2 resolved compatibility 用 candidate-05 专属 `testing/test_frozen_candidate_compatibility.py` 检查四 profile 的 frozen operations 和 reachable schemas；candidate-05 parity 还比较静态/runtime normalized JSON Pointer。相关结果为 5 passed、退出码 0；不能把旧 candidate-04 硬编码的 frozen test 失败当成 candidate-05 schema 失败。M3/M4 矩阵在 contracts 冻结前运行 candidate-05 为 6 passed；冻结后旧矩阵仍把 `contracts/universal-v1/openapi.json` 当作 47-operation M2 基线，重跑会得到 2 failed/4 passed（candidate added=0，规范 E=65 全部被误报 missing），这是测试基线未随冻结迁移的 stale fixture。

## CONTRACT-003、BUILD-002 与 parity 台账对照

`CONTRACT-003` 原因是 candidate-04 相对 M3/M4 规范缺 6 条 operation，并伴随旧 header、page、Location 和 DTO 误判；candidate-05 在冻结前的 65-operation 门禁为 6 passed，完整 65 条集合为 K8/T14/M22/A21，故本材料把 candidate-05 作为该接口完整性修复证据。冻结后旧矩阵的失败由 contracts 基线已迁移到 candidate-05 导致，不能重复计入产品缺陷。

`BUILD-002` 的 candidate-05 根级物理完整性由 `testing/test_snapshot_root_integrity.py` 的 14 passed 证明：四个 canonical profile、SOURCE_MANIFEST 双向 hash、唯一根目录和 323-file 指纹均通过。`testing/test_candidate05_parity.py` 的 5 passed 证明四 profile static/runtime normalized parity；其余 24 passed 汇总命令同时覆盖冻结兼容、parity 和 root integrity。

## 可复现命令与结果

```powershell
$env:M3_M4_SNAPSHOT='D:\Jupyter\Chromatography_agent\backend\.generated\snapshots\m3-m4-v1-candidate-20260921-05'
& 'D:\Anaconda\envs\SFC\python.exe' -m pytest -c backend\pyproject.toml -o addopts='' testing\test_frozen_candidate_compatibility.py testing\test_candidate05_parity.py testing\test_snapshot_root_integrity.py -q
```

结果：**24 passed in 17.84s，退出码 0**。对应报告分别为：

- [`testing/runs/2026-09-21-candidate-05-parity.md`](../testing/runs/2026-09-21-candidate-05-parity.md)：专属 parity `5 passed`；静态/runtime normalized universal 均为 `CB4F9B92DDE639FC5188A4D898DE23EFB1E20B22733722BD50D7EFF580F84D1A`。
- [`testing/runs/2026-09-21-m3-m4-candidate-05-spec-matrix-revalidation.md`](../testing/runs/2026-09-21-m3-m4-candidate-05-spec-matrix-revalidation.md)：candidate-05 矩阵 `6 passed`，65 operation 无 operation-level failure。
- [`testing/runs/2026-09-21-snapshot-05-root-integrity.md`](../testing/runs/2026-09-21-snapshot-05-root-integrity.md)：root integrity `14 passed`，BUILD-002 的 candidate-05 物理完整性门禁通过。

当前全量后端命令：

```powershell
& 'D:\Anaconda\envs\SFC\python.exe' -m pytest backend/tests -q
```

当前 collect-only 为 53 files / 346 tests；运行结果为 **344 passed、1 failed、1 skipped**。唯一失败为 `backend/tests/test_frozen_contract_compat.py::test_universal_candidate_preserves_frozen_operations_after_resolving_renamed_schemas`（line 71）：该测试固定读取 candidate-04，而 contracts 已切换为 candidate-05，故比较的是不同候选代际。跳过项为 `test_tlc_adapter.py:184` 外部四资产真实模型 smoke 需显式环境变量。此前 register 缺 If-Match 的旧断言已不作为 candidate-05 结论；API-05/06/08/17-33 口径为 registration register 仅 I。

## 四模块组件 fixture 证据矩阵

以下“组件/SQLite”表示测试使用 InMemory、fake port、SQLAlchemy SQLite 或 FastAPI TestClient；它们证明序列化、状态机、权限和事务边界可表达，不等于真实 MySQL/Redis/Worker/provider E2E。标为“缺口”的项目没有在现有测试中形成可接受的真实证据。

| 模块与不变量 | 具体 backend/tests 证据及实际断言 | 层级与缺口 |
|---|---|---|
| 知识：成功 DTO/列表/严格 I/E；项目隔离与权限撤销后不可读 | `test_knowledge_m34_contract.py::test_knowledge_archive_is_idempotent_only_and_lists_use_cursor_page` 检查 archive 仅 I、cursor/page 和 typed envelope；`test_knowledge_vertical.py::test_cross_project_is_checked_before_repository_query` 断言跨项目先 404；`test_knowledge_vertical.py::test_router_checks_project_scope_before_idempotency_lookup` 断言越权时不查幂等表；`test_knowledge_m34_p1.py::test_knowledge_cursor_is_opaque_and_scope_bound` 断言跨 scope cursor 被拒。 | 组件/内存。覆盖项目访问撤销式边界；未覆盖真实登录/RBAC token 撤销传播。 |
| 知识：归档、引用历史与权限保持 | `test_knowledge_vertical.py::test_search_snapshot_is_stable_and_archive_is_visible_in_history` 先检索版本 1，再 archive，citation history 返回 ARCHIVED；`test_knowledge_vertical.py::test_document_error_dto_redacts_legacy_storage_details` 断言内部路径/traceback 不进入错误 DTO。 | 组件/内存；未覆盖多实例权限缓存和真实索引服务。 |
| 知识：索引失败保旧、成功原子换代、stale worker 不发布 | `test_worker_failure_keeps_previous_index_visible` 断言 FAILED 后旧 index_version=1；`test_worker_rejects_parser_content_hash_mismatch` 同样保留旧版本；`test_worker_success_publishes_a_new_immutable_version_atomically` 断言 version 1→2；`test_knowledge_m34_p1.py::test_worker_fences_stale_worker_after_enqueue_compensation` 断言补偿后的 stale worker 不 publish；`test_worker_error_does_not_persist_dependency_exception_text` 断言脱敏错误码。 | 组件 worker/内存；`test_mysql_chunk_index_declares_ngram_parser` 只查 SQLAlchemy MySQL 元数据，未跑真实 MySQL/队列。 |
| 训练：固定模板、禁止任意脚本、不可变 data/feature/protocol、确认失效/一次性 | `test_training_vertical.py::test_contract_rejects_unknown_and_executable_fields` 拒绝 command/script/python/entrypoint 等；`test_validation_requires_immutable_feature_approved_protocol_and_template_parameters` 断言参数、feature version、approved protocol 校验；`test_confirmation_subject_expiry_once_and_transaction_rollback` 断言过期、二次消费和提交失败回滚；`test_lifecycle_freezes_spec_and_duplicate_submit_is_idempotent` 断言 submit 后 spec_locked、patch 失败、重复提交复用 job，Candidate 未发布。 | InMemory repository/confirmation/template component；未证明真实不可变 MySQL transaction 与 Worker lease/fencing。 |
| 训练：失败/取消/超时不产 Candidate | `test_training_vertical.py::test_failure_cancel_timeout_never_registers_candidate` 对 handler 异常断言 FAILED、candidate calls=0、无 candidates；`TrainingWorker` 源码明确对 TimeoutError、取消和执行异常走 `_finish_failure` 并写 `candidate_status=NOT_REQUESTED`。 | 现有运行断言实际覆盖执行失败；测试名包含 cancel/timeout 但没有分别注入 TimeoutError、CANCEL_REQUESTED 和超时预算的独立断言，属于缺口。 |
| 训练：成功但 Candidate 验证失败必须 REJECTED | `test_training_vertical.py::test_candidate_creation_requires_every_worker_check_and_never_publishes` 覆盖 registration check 不完整时拒绝且不发布；Worker 源码有 `CANDIDATE_VALIDATION_FAILED`→REJECTED 分支。 | 组件证据不完整：没有测试把成功训练结果送入 candidate registration failure 后明确断言 experiment=`REJECTED`；建议新增 `test_training_vertical.py::test_success_candidate_validation_failure_marks_rejected`。 |
| 治理：资产清单/SHA/加载/黄金样例校验，Candidate 与发布分离 | `test_model_center_vertical.py::test_dto_is_strict_and_asset_must_be_verified`、`test_artifact_reference_is_identifier_only_and_sha_manifest_must_match`、`test_candidate_creation_requires_every_worker_check_and_never_publishes` 断言验证清单、SHA mismatch、CANDIDATE 且未 published；`test_model_center_persistence.py::test_evaluation_snapshot_and_idempotency_are_immutable` 断言 evaluation snapshot 不可改。 | InMemory + SQLite component；真实模型包加载、黄金样例和环境隔离未做生产验收。 |
| 治理：human approval/reject、禁止 agent tool 直接审批、rollback audit | `test_model_center_vertical.py::test_evaluation_snapshot_and_approval_are_human_bound` 断言 interaction=agent_tool 被拒、human approval 成功且 snapshot 固定；`test_model_center_contract.py::test_human_approval_reject_and_rollback_are_audited_without_direct_publish` 断言 APPROVED/REJECTED、rollback draft 和 `model_deployment.rollback_draft` audit；`test_confirmation_expiry_and_single_use_are_enforced` 断言过期与一次性消费。 | 组件/内存；未覆盖真实审计写入、跨服务审批身份和 MySQL 并发。 |
| 治理：发布 CAS 一个成功、并发 409、旧 Champion 保持 | `test_model_center_contract.py::test_pointer_component_cas_allows_one_revision_and_rejects_stale_without_overwriting` 断言 revision 0→1、stale CAS 抛 ModelCenterConflictError、pointer 仍为 model-a；`test_model_center_vertical.py::test_publish_worker_keeps_old_pointer_on_failure_and_cas_conflict` 断言成功指针、loader failure 后旧 revision 保持。 | SQLite/InMemory component；没有 HTTP 层“一成功一 409”双并发实测，建议新增 CAS concurrent TestClient/SQL test，并明确 409 与旧 Champion body。 |
| 助手：不提供 approve/deploy/human-confirmation 工具，不绕确认/权限 | `test_m4_integration.py::test_forbidden_agent_tools_and_external_job_survives_cancel` 对 approve/deploy/human-confirmation/shell 均断言 PERMISSION_DENIED；`test_assistant_tools.py::test_allowlist_and_confirmation_closed_over_injected_service`、`test_forbidden_capabilities_cannot_be_registered` 断言 allowlist/confirmation closed；`test_assistant_contracts.py::test_resume_accepts_only_confirmation_id` 和 `test_client_message_is_strict_and_cannot_supply_server_history_or_identity` 拒绝客户端伪造。 | InMemory AssistantService/fixture business port；未接真实 PydanticAI provider/MCP。 |
| 助手：client_message_id 幂等、并发 BUSY、取消不联动已提交业务 Job | `test_m4_integration.py::test_client_message_replay_variant_and_busy` 断言相同 client_message 重放、不同内容冲突、非终态 run 返回 CONVERSATION_BUSY；`test_idempotency_key_replays_send_payload_independently_of_client_message_id` 和 `test_idempotency_ledger_is_shared_by_service_adapters_on_one_repository` 断言账本跨 adapter；`test_forbidden_agent_tools_and_external_job_survives_cancel` 断言外部 job 仍执行且 cancel run 不取消已提交 job；`test_assistant_service.py::test_cancel_agent_run_does_not_cancel_submitted_job` 同向验证。 | InMemory component；未证明 Redis lease、真实队列竞态和跨进程 BUSY。 |
| 助手：事件重放、SSE/HTTP 410、脱敏、工具账本 | `test_m4_integration.py::test_event_page_sse_replay_410_and_json_serialization` 和 `test_polling_events_route_returns_410_for_expired_cursor` 断言过期 cursor=410、SSE 序列和 JSON；`test_assistant_sse.py::test_event_sequences_are_atomic_and_sse_is_replayable`、`test_sse_cursor_mismatch_and_expired_window` 覆盖 seq/replay；`test_trace_dtos_permissions_usage_null_redaction_and_transport_pair` 断言 api_key/thinking 不泄露、content 权限分离；`test_execution_ledger_marks_unknown_and_blocks_external_job_replay` 断言 side-effect unknown 阻止重放。 | InMemory trace/event/ledger component；未证明 Redis 事件重放、跨节点 retention 和真实 provider 工具调用。 |
| 助手：Context 与真实 provider request 对应、runtime outbound、outbox/worker/trace | `test_m4_integration.py::test_runtime_history_object_trace_pair_and_public_event_types` 断言 provider outbound messages 与 logical/provider snapshot 一致；`test_runtime_transport_retries_create_distinct_calls_and_update_lifecycle` 断言 retry call/usage/lifecycle；`test_runtime_trace_persistence_failure_is_not_reported_as_provider_success` 断言 trace 写失败不伪装成功；`test_m4_assembly_integration.py::test_m4_binds_durable_idempotency_and_run_outbox`、`test_m4_worker_consumes_outbox_and_reports_missing_provider`、`test_m4_worker_persists_trace_usage_and_transport_retry`、`test_m4_trace_scope_binds_run_project_and_owner`、`test_m4_execution_ledger_persists_unknown_fence` 覆盖 assembly/outbox/trace 组件边界。 | InMemory/SQLite and fake provider component；没有真实 PydanticAI provider request、Redis/Dramatiq worker 或生产网络证据。 |

## 未证明项与后续建议

本候选证明的是 API/DTO、静态 OpenAPI、隔离 runtime parity、组件状态机、SQLite 持久化局部和物理包完整性。以下项目不应从上述 passed 推导为已验收：真实 MySQL 约束/并发事务、Redis stream/lease/fencing、Dramatiq/Worker 多进程恢复、PydanticAI provider 成功与工具调用、真实模型加载和科学 golden/独立评测、离线干净安装及完整传递依赖/platform wheel 锁、真实业务端到端（知识索引、训练、治理发布、助手 provider）。

建议后续仅补测试，不改本冻结材料的边界：训练 TimeoutError/CANCEL_REQUESTED 分支与成功后 Candidate 验证失败→REJECTED；Model Center HTTP CAS 一成功一 409 且旧 Champion 不变；真实 MySQL/Redis/Dramatiq race/recovery；provider outbound 与 PydanticAI/MCP 真实适配；科学评测和离线安装。当前结论保持为：**candidate-05 接口/组件层冻结，contracts 已切换为 M3/M4 baseline，非 release-ready。**
