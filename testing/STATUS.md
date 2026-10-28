# 当前测试与验收状态

更新日期：2026-10-29

> 整理前的完整状态原文保存在同目录 [STATUS.snapshot-2026-10-29.md](STATUS.snapshot-2026-10-29.md)。快照中的历史 PASS、LIMITED PASS、FAIL、UNKNOWN、授权和运行状态均是当时证据，不因归档变为当前状态。历史相对路径仍按原工作区解释；本工作树未复制 `testing/runs/` 全量材料。

## 历史业务验收摘要（旧工作区证据）

以下事实来自 `D:\Jupyter\Chromatography_agent` 中已存在的记录，不代表本轮新工作树验收：

| 日期 | 已有证据与限定结论 | 未覆盖或保留的边界 |
| --- | --- | --- |
| 2026-10-22 | 五类固定批量报告 LIMITED PASS：每类两行成功、一行跳过；10 条成功记录及导出/GET 刷新有限通过。见[原报告](<D:/Jupyter/Chromatography_agent/testing/runs/2026-10-22-five-types-batch/final-window-revision-02.md>)。 | 未覆盖运行时 PARTIAL、真实取消、重复投递、主动 SSE 重连，未签收全 V1。 |
| 2026-10-26 | 助手 A14 TLC 同轮预测闭环 LIMITED PASS；A15 复用历史摘要证据；A16 因缺真实校验模型包 BLOCKED。见[阶段汇总](<D:/Jupyter/Chromatography_agent/testing/runs/2026-10-26-assistant-minimal-acceptance/acceptance-stage-summary.md>)与[批量边界](<D:/Jupyter/Chromatography_agent/testing/runs/2026-10-26-assistant-minimal-acceptance/batch-next-minimal.md>)。 | A14 不证明主动 SSE 重连；A16 仍受资产阻塞；批量 PARTIAL/取消/中断恢复等分支未签。 |
| 2026-10-27 | 真实数据导入限定完成至 DataImport COMMITTED、DataVersion v1 READY，刷新后数据一致；审计读取 `audit_event=0`，存在 `AUDIT_EVENT_PERSISTENCE_GAP`。见[原 QA 报告](<D:/Jupyter/Chromatography_agent/testing/runs/2026-10-27-data-import-success/qa-success01-real-acceptance-01.md>)。 | 不称完整 PRD/V1 PASS；审计事件持久化缺口保留。 |
| 2026-10-28 | audit03 在绑定阶段失败关闭，未写配置；原平台经 QA 单次恢复。后续恢复准备 QA 为 32 项通过，但未激活。见[audit03 执行状态](<D:/Jupyter/Chromatography_agent/testing/runs/2026-10-28-data-import-audit03/audit03-execution-status-01.md>)与[恢复准备报告](<D:/Jupyter/Chromatography_agent/testing/runs/2026-10-28-data-import-audit03/audit03-successor-recovery-preparation-result-01.md>)。 | 无真实 CSV scan 或业务 E2E；旧 native 原因仍 UNKNOWN；恢复准备通过不等于真实绑定、配置切换或业务验收。 |

这些绝对路径指向旧工作区的历史证据原件；新工作树未复制 `testing/runs/` 全量材料。

## 当前工作树：源码与包检查有限通过，数据候选未激活

状态：`SOURCE_AND_PACKAGE_LIMITED_PASS_DATA_PROFILE_NOT_ACTIVATED`

工作树为 `D:\Jupyter\Chromatography_agent_worktrees\structure-cleanup-20261029`，分支 `codex/structure-cleanup-20261029`。初始创建点为 `a1815fd2f99153889d813958adcb550b8d9a859e`；完整构建输入基线为 `345506e70a03580460622b7ee39a967728e98cc5`（标签 `codex/structure-baseline-complete-20261029`）。

### 源码与裁剪包

- backend 定向检查第一组：86 passed。命令在新工作树 `backend/` 执行：

  ```powershell
  & 'D:\Anaconda\envs\SFC\python.exe' -B -m pytest -o addopts='' -q tests/test_profile_resources.py tests/test_batch_csv_window_profile.py tests/test_startup_check_profile.py tests/test_gc_scientific_source_preservation.py tests/test_dataset_csv_elevated_runner.py tests/test_data_import_integration.py
  ```

- 后续另一组检查报告 25 项通过，并发现既有 SFC/CC 裁剪 OpenAPI 生成问题：组装步骤收到已裁掉的 `data_csv_window` 参数。仅在 build 不包含 `datasets` 时由 `profile._trim_app_source` 去掉该 keyword，保留 DTO/API 与科学实现；修后针对性复验 6 passed：

  ```powershell
  & 'D:\Anaconda\envs\SFC\python.exe' -B -m pytest -o addopts='' -q tests/test_profile_resources.py tests/test_prediction_profile_compat.py::test_trimmed_sfc_and_cc_packages_generate_openapi_in_isolated_process
  ```

  两批测试不相加为总通过数；未记录第二组的完整命令，不据此声称完整测试套件通过。
- PowerShell 7 `deploy/v1/test_data_accept_profile.ps1` 检查通过。首次使用 Windows PowerShell 5.1 的工具链问题已纠正，非产品阻塞。
- root 在隔离临时目录实际构建 wheel：7 个 JSON 资源均进入 wheel 且原字节保持；使用已有 setuptools 82.0.1 / wheel 0.46.3，未安装依赖。
- 隔离读取检查在临时 cwd 禁止读取 `testing/` 与 `.generated/` 后，通过 5 次 metadata/profile 资源读取，覆盖 GC、SFC refs、SFC 单柱、TLC 与 batch manifest。harness 初始的严格 UTF-8 假设与 TLC 原 fixture 编码不一致；改为匹配既有 consumer 的 `errors=replace` 后通过，fixture 未转码或修改。
- 独立 workspace 审查：包内 84 个文件，manifest 83 项源码均匹配；已登记 GC scientific source 及其 model profile/source pins 未修改；新 data 复制候选的 source 摘要已单列。审查和测试不激活数据候选。

### 未运行或未签收

- 数据 profile 与候选包未激活；旧服务、运行配置未切换。后续运行/浏览器验收另行安排；仅未激活 data 候选若需变更 protected source pin，遵守既有受控窗口批准边界。
- 未调用真实模型；未连接真实业务 MySQL/Redis（定向测试使用隔离替身或临时数据）；未运行浏览器业务流程。
- 以上源码、裁剪包、PowerShell 与 wheel 结果不代表数据导入真实验收或完整 V1 验收。历史 audit、业务状态与 `UNKNOWN` 按上表保留。

## 后续收口

本轮未切换服务，后续运行/浏览器验收另行安排；未激活 data 候选如需变更 protected source pin，遵守既有受控窗口批准边界。本轮结果不能替代 fresh identity、配置/资源门、审计和运行状态检查。