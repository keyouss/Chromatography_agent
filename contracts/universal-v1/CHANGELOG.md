# universal-v1 契约冻结记录

## revision-20261003-02

冻结对象来自已审候选：`contracts/candidates/m2-export/revision-20261003-02/candidate-openapi.json`。

- 候选 raw SHA-256：`c9e1d65c2199dcd57f004c2700db11800f5bb3a1dcbdac4c52b7aa8e7624ee01`
- 候选 normalized / 新 active SHA-256：`4a14fcabaef14acf20ec7a1297631e0cc503a9ab2c612ed1ffaed4a0281064ec`
- 冻结前 active SHA-256：`931f2cb5ebd929864f370d196f4355eac9c6aa83efb9ef42e3c7320bbe7f98a2`；已保存为 `rollback-snapshots/revision-20261003-02-baseline/openapi.json`
- counts：`119 paths / 127 operations / 329 schemas`；相对冻结前 `+2 / +2 / +5`，删除 `0 / 0 / 0`
- 严格语义差异仅新增 `POST /api/v1/exports`、`GET /api/v1/exports/{id}`，新增 `ExportCreate`、`ExportAccepted`、`ExportView`、`SuccessEnvelope_ExportAccepted_`、`SuccessEnvelope_ExportView_`，并在 `ArtifactView.kind.enum` 末尾追加 `export_report`
- `UploadCreate` 与其他既有路径和 schema 均未改变；其他 profiles 未修改
- 冻结后检查：`check_openapi.py` 为 `STRUCTURE_PASS`；`test_check_openapi.py` + `test_freeze_openapi.py` 为 `9 passed, 8 subtests passed`；backend artifact/worker/migration 审查集为 `76 passed`，export 定向集为 `10 passed`。严格语义 diff 以冻结前不可变快照为基线通过；详见 `contracts/candidates/m2-export/revision-20261003-02/SHA256SUMS.txt`
- 本次只冻结 OpenAPI 契约和元数据；未执行数据库迁移、未重启服务，不代表生产集成或发布验收
- 前端 profile 门禁对账：保留旧 M3/M4 universal 元数据快照 `docs/contract-freeze-snapshots/revision-20261003-02-pre-sync/V1_M3_M4_CONTRACT_FREEZE.json`（117 paths / 125 operations，SHA-256 `b1c396dbaa927ec58910ac42a2713868b2cebc6872837b85006f8f6aab3b4e98`）；当前 M2 更新同步为 revision `revision-20261003-02`、119 paths / 127 operations，其他 profiles 未变
- 同步后标准 `generate:contracts` 与 `verify:contracts` 在临时输出目录对四个 profiles 全部通过；`verify:profiles` 的静态装配检查通过。生成物未写入 `frontend/src`；该检查不代表 staged packages 已 release-ready

## revision-20261003-01

冻结对象来自不可变候选目录：
`backend/.generated/priority-launch-contract/revision-20261003-01`。

- 候选 raw `candidate-openapi.json`：`375b7729bb4e0335ee5793cb8d1053dbc7a866e469e6d8138b7fc71da3cb6b90`
- 候选 normalized：`931f2cb5ebd929864f370d196f4355eac9c6aa83efb9ef42e3c7320bbe7f98a2`
- active `universal-v1/openapi.json`：`931f2cb5ebd929864f370d196f4355eac9c6aa83efb9ef42e3c7320bbe7f98a2`
- counts：`117 paths / 125 operations / 324 schemas`；相对旧 active 为 `+0 / +0 / +0`，删除 `0 / 0`
- 严格递归语义 diff 仅两项：`PriorityToolView.name.enum` 增加 `normalize_structure`；`GET /api/v1/assistant/priority/models` 增加可选 `limit`（默认 50，1–200）和 `offset`（默认 0，最小 0）
- 明确不加入 `total`、`expected_effect`、`outcome` 或其他字段
- 候选 `MANIFEST.json`：`d10626997ed12da679386c26188c39be4faa331237631f4bb6cae3277bfa5637`；候选 `SHA256SUMS`：`393038c1d6ccb7a03f3ec0e7e51b36d24c5e54ffb3771ad8142027904d81f74b`；9 项记录全部匹配
- 候选 audit：`fee5f0ecd1a3e3517c04345d1a3fd9a94edc0c4ee6d5d731cb323d759cc7d204`；source-route-audit：`b2336448a43b3ed6e4871f10cdb90abc2e51e680662b1afc9bd4a4b8ffd4b1b1`；handoff：`b77acd8a32dfcb69b143edf376f9c17aa9bb9b52a0f67460cfd4c88ae1a2c568`
- 冻结前旧 active `revision-20261002-04` 已复制为不可变回滚快照，快照 SHA 为 `ad3506ebf484d59fae92d396cf57114c22638f52ba79086cee2472a94af126f1`；更早的 `cb4f9b92dde639fc5188a4d898de23efb1e20b22733722bd50d7eff580f84d1a` 快照继续保留；详见 `rollback-index.json`
- 旧 sidecar QA 报告的哈希属于已保留的 `revision-20261003-01-superseded` 候选，纠正记录见 `testing/runs/2026-10-03-contract-candidate-revision-20261003-01-qa-correction.md`

本冻结仅覆盖 A+B universal 契约变更。批量 §9 仍为 `UNIMPLEMENTED_AND_UNVERIFIED_IN_THIS_SLICE`，不进入本次冻结。

## revision-20261002-04

冻结对象来自不可变候选目录：
`backend/.generated/priority-launch-contract/revision-20261002-04`。

- 候选 raw `candidate-openapi.json`：`c474c426987b8da3ec9d826020164edd3a1453dc24f20f35202dc44a9be8f8cd`
- 候选 normalized：`ad3506ebf484d59fae92d396cf57114c22638f52ba79086cee2472a94af126f1`
- active `universal-v1/openapi.json`：`ad3506ebf484d59fae92d396cf57114c22638f52ba79086cee2472a94af126f1`
- counts：`117 paths / 125 operations / 324 schemas`；相对旧 active 为 `+13 / +13 / +30`，删除 `0 / 0`
- 候选 MANIFEST：`36dda79327edf4dc36add065ae05d58513a94369820f7e40f494ac63c5a34118`
- 候选 SHA256SUMS：`a8b30455c46a1ca3126936a1931b4473c08659dbfc254737f3ad6aedbe940934`；9 项记录全部匹配
- 候选 source tree：138 个 Python 文件，`672aac39e813c0579581b913a49611c02ba1a53374584fc6950f46dc36ccbccc`
- 候选 QA 报告 SHA：`fc939bb93aab4b71ac676b550d8f1ced6829d9780d09a19add416a09bb050fc7`
- FE 已接受协调迁移：AccessContext/project selection/default/null、TLC/SFC 结果元数据、`TlcSolventComponent` 类型名和 `CANCELED` 枚举；本记录不代替 FE 生成或修改类型

兼容分类沿用候选 `audit.json` 的 `MIXED_WITH_BREAKING_CHANGES`。新增 assistant priority、SFC scope、TLC options/comparison/reverse-solvent operation；登记草稿不要求/消费 confirmation，model-center register transition 要求并消费绑定 revision 的 confirmation。候选 audit 的 batch roots 明确为 `UNIMPLEMENTED_AND_UNVERIFIED_IN_THIS_SLICE`，batch §9 不进入本次冻结。

候选生成后测试树仅有已审授权差异：`backend/tests/test_hplc_adapter.py`，最终 SHA `17cb8387f0f397187b8555cba9452eac3bd379fa6691e50adf50e888e68e48cb`；其余 TLC、契约和 worker 测试文件保持候选绑定 SHA。候选生成前测试树基线 SHA 为 `839bfa9204654e34d4285a62f89fbf3a57faf9d8120c2b0a846bd31d43f0a4f`。

旧 active 已复制为不可变回溯快照，详见 `rollback-index.json`；旧快照 SHA 为 `cb4f9b92dde639fc5188a4d898de23efb1e20b22733722bd50d7eff580f84d1a`。普通沙箱下的历史 snapshot ACL/WinError 5 只作为执行环境限制保留，不宣称 ACL 已修复。

冻结后检查：`check_openapi` PASS；`contracts/test_check_openapi.py` 与 `contracts/test_freeze_openapi.py` 为 `9 passed`；candidate 经 `freeze_openapi.normalize` 后与 active 字节和 SHA 一致。既有 `audit_contract_117_slice.py` 因其历史 baseline snapshot 的普通沙箱 WinError 5 返回 BLOCKED。前端 `npm run verify:contracts` 已运行但因 `docs/V1_M3_M4_CONTRACT_FREEZE.json` 仍记录旧 universal normalized SHA 而停止；本次按授权未生成或修改 FE 类型，FE 迁移接受仍以 QA/FE 批准为准。
