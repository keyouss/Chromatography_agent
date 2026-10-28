# 五类批量前端集成检查（2026-10-22）

## 当前边界

五类批量前端已接入正式冻结契约；这些是源码、组件和构建证据，不是实际 CSV 扫描、真实模型或浏览器业务闭环验收。未执行真实批量提交，未修改 Python/Node 依赖或运行环境，未启动或重启 API/Worker。

正式 universal-v1 契约 SHA-256：`4c7cdd28248ef7507c187edca269f19ea5ce6eb0ca88cdd5bb1a82704b568fe5`。候选原始字节摘要与正式规范化摘要不同，冻结审核确认 JSON 对象一致。运行清单和健康检查由后端负责人管理，dist 构建不作为已部署证据。

## 实现与复用

- `frontend/src/shared/batch/`：一次共享工作区、生成 DTO 别名、统一客户端、actor/project/type 隔离的本地输入与恢复指针、真实结构引用准备。
- `frontend/src/modules/prediction/BatchPredictionPage.vue`、`batch-entry.ts`：类型下的批量三级路由；构建清单含 batch 才注册。返回单次保留项目和合法 SFC 工具游标。
- `PredictionView.vue`：只增加构建门控导航；严格 HPLC/GC 1.1 路径保留，移除已不可达的旧请求 fallback。
- `PredictionRecordsView.vue`：合法 batch_id/result_ref 跳整批 GET 恢复；批量记录不走单次重放 POST；未装配时保留记录快照和明确提示。
- `core/contract-types.ts`：能力别名从正式 operation 200 响应提取，兼容各 profile 的实际能力 schema；不复制 DTO 或扩大精简契约。

工作区状态机复用来源：`frontend/candidates/tlc-batch-revision20261008-01/src/modules/prediction/forms/tlc/TlcBatchWorkspace.vue`。本轮补充登记的来源 SHA-256：`639d3dd1527093cfb71fe308e9f9b492543df9a06f9b27d634215413389a5117`。保留原文件；迁移理由为移除 TLC 固定类型、改用正式五类联合并补齐重试/异步导出。继续复用现有 artifact、confirmation、结构编辑器和 Ketcher，不新增依赖。

写操作保留 ETag、确认版本和幂等边界；制品只有 AVAILABLE/PASSED 才能引用。提交/重试前持久化未知状态锁，网络/408 不解锁；旧 TLC v1 只读。SFC 行工具由原 exact 模型的 tools 能力验证，不一概禁止兼容的 single/full 混行。

## 已执行检查

| 检查 | 结果与范围 |
| --- | --- |
| 正式源码 vue-tsc --noEmit | PASS；修复公共能力别名和严格联合接线后通过 |
| 根前端 Vite build | PASS；173 modules |
| batch-client + batch-workspace | 7 PASS；transport/持久化组件证据 |
| records 新批量路由分支 | 2 PASS；旧 27 项未重复执行 |
| BatchWorkspace 新恢复检查 | 3 PASS；GET-only、拒绝错项目、TLC v1 只读 |
| universal 装配源码 typecheck / Vite build | PASS / PASS；使用现有项目工具链，不是全新环境独立 npm ci 验收 |
| 非 batch 包新增代码裁剪 | sfc/cc/minimal 的 batch 页面、entry、shared/batch、batch 测试均不存在，registry 无批量入口引用 |

独立装配源码位置：`frontend/build-output/five-types-batch-20261022-frozen-02/universal-v1`。

## 未通过与延期

完整 sfc-v1、cc-v1、minimal-structure-v1 前端装配联编未通过：前序整棵 prediction/共享结果代码和测试的裁剪不完整，严格的精简契约使未选类型变成 never；部分测试仍引用已裁模块。总控决定不将这次既存重构债扩入五类批量 universal 主线；保留真实失败，不用 universal 契约、any 或忽略生产源码来绕过。后端四 profile 物理包导入/契约检查与这些前端失败分层记录。

装配包排除了五个依赖仓库外部报告或历史候选目录的专用验收 harness；仓库原件保留：gc11-page、qa-signed-cursor-20261019、三份 tlc-batch-candidate 测试。正式批量测试仍在 universal 包内。第一版装配目录 frozen-01 保留其失败状态，修复装配依赖后使用 frozen-02。

真实上传与扫描授权、五类 Worker/模型/记录闭环、浏览器窄屏与刷新恢复、独立新环境安装和最终离线 ZIP 均不在以上 PASS 范围内。

QA 最新交接：人类已授权本轮最多 5 个固定 CSV；首次扫描状态读取返回 DEFENDER_STATUS_UNAVAILABLE，按失败即停暂停上传/扫描，当前两项计数均为 0。此时阻塞是扫描状态不可核实，不是缺少人类授权；不更换扫描器或绕过门控。

## 根 dist 摘要

| 文件 | 字节 | SHA-256 |
| --- | ---: | --- |
| index.html | 524 | b350e6a4f8ccfdbb02dc6d44629ed1a222edef536be196a113bb8457f9fb0a93 |
| assets/index-Cczg4Fwn.js | 711801 | f578ca8589b2af2a101c763f077b60322fc00194af194b407d0c144f101d9162 |
| assets/index-YXUsPEpC.css | 78664 | 9b60fd8203d83bbaa896ad19538258cf060335b1f0aa3759ee23e2c03e28fa7a |

Vite 保留大于 500 kB 的 chunk 提示；未引入动态模块发现或借此改变本轮构建边界。

## 首份 TLC 上传后的前端小修

QA 在真实首份上传观察到两项展示问题，已限于 BatchWorkspace.vue 修复：顶部改用选中批量工具/模板共同预算，不再显示单次能力的 1 行/1 候选限制；QUARANTINED 且 scan PASSED 时明确提示扫描已通过但制品被隔离，不能用于预测，不再暗示等待扫描。其他拒绝/失败/归档/删除状态也按实际状态提示。

新增 2 项组件回归检查通过（原 3 项未重复运行），vue-tsc --noEmit 通过。未重建 dist 或切换运行包，上方 dist 摘要仍是此前构建；若运行页面使用 Vite 源码，HMR 可能加载改动，需由 QA 只读核对。

QA/BE 最新交接取代前述扫描状态：首份 TLC 实际扫描 CLEAN/PASSED 1 次，CSV 尾部额外空记录导致后续 CSV_ROW_SHAPE_INVALID，制品保持 QUARANTINED；当前窗口已关闭，不重传、不重扫、不修改制品状态。此次组件通过不作为真实批量执行闭环 PASS。

QA 随后仅在原标签 5 读取 DOM，确认 HMR 后顶部已显示批量预算 100000 次计算 / 3600 秒。HMR 同时清除了尚未建立草稿时的 accepted/artifact/selectedFile 临时状态，原隔离制品未再显示；隔离提示仅有上述 focused 组件证据，未通过浏览器复核。此次 DOM 核对没有新增 GET/POST、上传或扫描，原后端收据仍保留。不据此推导完整上传恢复或真实业务闭环通过。
