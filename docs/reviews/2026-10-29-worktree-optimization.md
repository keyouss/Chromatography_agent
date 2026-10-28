# 2026-10-29 工作树与结构优化记录

## 工作区与文档入口

本轮在 `D:\Jupyter\Chromatography_agent_worktrees\structure-cleanup-20261029`（分支 `codex/structure-cleanup-20261029`）工作。初始创建点为 `a1815fd2f99153889d813958adcb550b8d9a859e`；已补齐构建输入的基线为 `345506e70a03580460622b7ee39a967728e98cc5`，标签 `codex/structure-baseline-complete-20261029`。原目录 `D:\Jupyter\Chromatography_agent` 保持只读。

整理前完整 plan 与 STATUS 原文以同目录快照保留。当前正式入口仍是 `docs/plan.md` 与 `testing/STATUS.md`，README 指向这两个入口并说明新旧工作区的启动边界。没有新建并行 current 索引、哈希系统或机器化归档清单。STATUS 保留简明历史业务矩阵，直接链接已验证存在于旧工作区的原始证据。

## 源码结构优化

- 将资源定位改为包内定位。已登记的 GC scientific source 及其 model profile/source pins 未改；旧冻结 receipt 未改，新 data 复制候选的 source 摘要已单列。
- SFC/CC 构建裁掉 `datasets` 时，`profile._trim_app_source` 去掉指向已裁掉 `data_csv_window` 的 keyword，使裁剪包能生成 OpenAPI；不改 DTO、API 契约或科学实现。
- GC 正式 assembly 显式传入包内 fixture；历史无参离线 helper 仍保留旧目录 fallback，仅供离线兼容调用，未作为普通运行入口。本轮不声称清除了全仓 `testing` 路径文字。数据 profile 未激活，普通运行未切换；包审查确认 84 个文件、manifest 83 项源码匹配。
- 未改 PRD、目标 API、冻结契约、测试 fixture、数据、已发布 deliverables、旧运行证据或全局环境；未安装依赖。

## 核验结果

- backend 第一组定向检查 86 passed，命令见 [测试状态](../../testing/STATUS.md)。后续另一组摘要为 25 项通过并发现一个 SFC/CC 裁剪 OpenAPI 问题；做局部条件修正后，仅重跑 profile resources 与该 OpenAPI 用例，6 passed。批次不相加，不声称全量 pytest 通过。
- PowerShell 7 `test_data_accept_profile.ps1` 通过。root 在隔离临时目录完成 7 个 JSON resource 的 wheel 检查，确认都包含且字节不变，未安装依赖。
- root 的隔离资源 consumer 检查以 Path.open guard 禁止读取 `testing/` 与 `.generated/` 后，5 次 metadata/profile refs 读取均成功。harness 一开始要求严格 UTF-8，与原 TLC fixture 的历史编码备注不符；改为匹配真实 consumer 的替换错误策略后通过，fixture 字节未改。
- 未连接真实业务 MySQL/Redis、未启动或切换服务、未调用真实模型、未运行浏览器业务验收。单元检查只使用隔离替身或临时数据。源码和包层通过不表示真实业务链或全 V1 已验收。

## 保留范围与边界

没有搬动或删除 `docs/handoffs/`、`testing/runs/`、`testing/checkpoints/`、fixture、契约/候选、模型资产、数据、备份或 `deliverables/`。新工作树没有复制完整 `testing/runs/`；历史绝对路径仍指向旧工作区。2026-10-22 至 2026-10-28 的历史有限通过、失败关闭、审计缺口及 `UNKNOWN` 均在 STATUS 中保留原意。

数据 profile/candidate 未激活；旧 API、Worker、前端服务和运行配置未切换。后续真实业务或模型运行/浏览器验收另行安排，不从本轮源码测试推断运行成功。未激活 data 候选如需变更 protected source pin，遵守既有受控窗口批准边界。