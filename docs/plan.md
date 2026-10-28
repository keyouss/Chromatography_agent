# 通用色谱智能平台开发计划与当前证据入口

日期：2026-10-29

本文件保留当前计划入口。产品需求、目标接口和正式冻结契约分别以 [PRD](PROJECT_PRD.md)、[前后端接口规范](PROJECT_FRONTEND_BACKEND_API_SPEC.md) 和 `contracts/` 中的冻结内容为准；本轮未修改这些规范。

> 整理前的完整计划原文保存在同目录 [plan.snapshot-2026-10-29.md](plan.snapshot-2026-10-29.md)。快照正文和其中的历史引用原样保留；历史绝对路径继续指向旧工作区。完整状态快照见 [testing/STATUS.snapshot-2026-10-29.md](../testing/STATUS.snapshot-2026-10-29.md)，当前状态入口为 [testing/STATUS.md](../testing/STATUS.md)。

## 当前工作

本轮在独立 Git 工作树 `D:\Jupyter\Chromatography_agent_worktrees\structure-cleanup-20261029`（分支 `codex/structure-cleanup-20261029`）完成源码结构优化。工作树最初从 `a1815fd2f99153889d813958adcb550b8d9a859e` 建立；完整构建输入基线现为 `345506e70a03580460622b7ee39a967728e98cc5`，标签 `codex/structure-baseline-complete-20261029`。完整回退建议从该标签新建工作树。原目录 `D:\Jupyter\Chromatography_agent` 保持为旧运行和历史证据来源；本轮没有切换旧目录服务或配置。

目标是围绕真实修改联动做小步整理，保留现有色谱业务链、契约、测试夹具、交付物和证据边界。SOLID 仅用于诊断变化原因，不要求逐条形式化达标；不为目录完整或抽象数量而拆分。

## 阶段与结果

1. **基线与边界：完成。** 使用独立 Git 工作树；源码范围清单由审计记录维护。
2. **逐模块审查与最小修改：完成。** 保留现有入口与业务 DTO/API；仅修正包资源定位及 SFC/CC 裁剪包 OpenAPI 生成时对已裁掉 `data_csv_window` 的引用。已登记的 GC scientific source 及其 model profile/source pins 未改；旧冻结 receipt 未改，新 data 复制候选的 source 摘要已单列。数据候选未激活。
3. **定向核验：部分通过。** backend 源码测试、裁剪包回归定向复验、PowerShell 7 脚本检查及隔离 wheel 构建通过，具体命令和范围见 [测试状态](../testing/STATUS.md)。一次较广测试中发现的 SFC/CC OpenAPI 问题，经局部条件修正后由 6 项定向检查复验通过；不把各批次结果相加为一个总数。
4. **收口：本轮运行与浏览器验收 NOT RUN。** 数据候选未激活；旧 API、Worker、前端服务与运行配置未切换。后续运行/浏览器验收另行安排；仅未激活 data 候选若需变更 protected source pin，继续遵守既有受控窗口批准边界。本轮没有调用真实模型或连接真实业务 MySQL/Redis。

## 本轮范围边界

- 未改 PRD、目标 API 规范、冻结 OpenAPI、迁移基线或已发布 `deliverables/`。
- 未删除、改写或搬动历史运行报告、测试 fixture、模型/数据资产、备份或已发布制品。
- 未启动/停止或切换旧工作区的 API、Worker、前端、数据库、Redis 或模型推理，也未修改旧运行配置。新工作树内为源码结构优化所需的启动脚本路径解耦属于本轮范围。
- 未安装或升级依赖、修改全局环境变量、激活数据 profile 或执行真实业务操作。

旧运行证据原件保留在旧工作区的 `testing/runs/`。新工作树不声明已复制该目录全量内容；只有明确复制并核对过的材料才可标为新树副本。