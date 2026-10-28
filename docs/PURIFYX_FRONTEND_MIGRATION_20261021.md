# PurifyX 前端行为迁移记录（2026-10-21）

本轮将获准的 PurifyX 行为增量迁入本地平台，保留 Vue 平台壳、五类预测、批量入口、数据摘要、模型草稿及现有会话搜索、归档、真正删除。批量保持原有能力边界与诚实不可用提示，本轮没有额外启用批量。来源为 `D:/1_Undergraduate_111111111111/group_tasks/purifyx-work/purifyx-site`，只读取源码，不操作其运行环境。

## 范围与实现边界

- 工作区按 actor、project、chromatography type、tool 隔离，持久化原始结构文本、表单草稿、模型选择和受理引用；正式预测数值始终由 GET 取得。
- 恢复不自动 POST，不复用持久化的结构 ID、SFC 范围或确认；SFC 轴与模型选择恢复后重新校验、预检和绑定。
- 提交在途时先锁定发起操作的工作区；受理后按原工作区保存任务 ID，明确拒绝后解锁，未知响应保持锁。切换或刷新不解除未知提交锁、不重投任务。
- HPLC 普通点击复选多柱；GC 温度示例读取能力长度，仅填入可编辑等间隔温度点。
- TLC Rf、CC 体积/时间、HPLC 原生推荐及保留时间、GC Q50 分层显示；补充数值折叠，保留真实版本、警告、错误，不从舍入显示值反算。
- Markdown 手性 SMILES 保护复用源 Renderer 的化学分支识别；保留本地原文查看、原文复制、流式更新、DOMPurify 和合法链接。未迁入源项目的回答重试或聊天持久化替换。
- 自动校验及路由 scrollBehavior 已有，不照搬营销壳或常驻页面实例。
- 排除匿名 Cookie、额度、邀请码、Provider 切换、快速/深度模式、Linux 部署及 HPLC 非对映体政策；不新增依赖，不修改 SFC Python/Torch。

## 实施与证据

产品源码已完成并 STOPWRITE，类型检查与生产构建通过。本轮 QA 结论为 **LIMITED_PASS**，详见[限定验收报告](../testing/runs/2026-10-21-purifyx-migration/acceptance-report.md)。组件证据与实际浏览器验收分别记录，不据此宣称通用平台或科学泛化验收通过。

- `AssistantText.vue`：新增手性分支保护，保留原链接渲染及净化。`assistant-text.test.ts` 6 项通过，包括裸写多手性片段、流式更新、合法链接并存、XSS、原文复制。
- 表单和结果高亮：CC/HPLC/GC 表单、结果提取 4 个文件 23 项通过；之后修复 HPLC 回声更新，HPLC 5 项通过。两组存在重叠，不相加。
- 最终 SFC 集成 43/43：包含原有科学绑定、提交、恢复与新部分轴草稿恢复；`sfc-integration-final.log`。最终工作区 4/4：actor/tool 隔离、存储不可用退化、身份错配、原文与结构引用分离；`prediction-workspace-final.log`。
- 追加受影响检查原有 85 项中，SFC 35 项失败、其余 50 项通过；修复后仅复验相关 SFC/工作区，不重复未改项。失败与修复日志均保留。
- `vue-tsc --noEmit` 最终通过：`typecheck-final.log`。
- 唯一生产构建 `node node_modules/vite/bin/vite.js build` 通过：`production-build.log`，158 modules，3.64 秒；输出 `dist/index.html`、`dist/assets/index-BB75C24j.css`、`dist/assets/index-DiGNxSp5.js`。大于 500 kB 的 chunk 为非阻断提示，本轮不额外拆包。

上述日志位于 `frontend/.tmp-purifyx-migration-20261021/`。较早的 34+4+2 项通过日志最初误写入 `frontend/frontend/.tmp-purifyx-migration-20261021/`，已核实并以 `pre-sfc-` 前缀复制到正确目录；原件保留，明确它们是最终 SFC 修复之前的阶段证据。各阶段计数不累计成全仓验收总数。

实际复用来源、SHA-256、依赖和适配理由见 [source-provenance.json](PURIFYX_FRONTEND_MIGRATION_20261021.source-provenance.json)。该清单只覆盖本轮读取并复用的前端实现，不是全仓完整性核对。

### 集成审查发现与修复

- HPLC 的 `initialValue` 深监听与 `update:value` 回传曾反复创建同值数组；改为只在候选柱列表确有变化时替换。父子组件实际绑定回归 5 项通过。
- SFC 部分轴草稿被存成空集合，首次本地编辑回声又触发初始恢复，造成首轴被清空。修复为保存各轴已选值，本地编辑开始后不再应用初始恢复回声；历史恢复夹具改为新 actor/project/type/tool 键，保留原业务断言。最终 SFC 43 项通过，原 `integration-tests.log` 失败证据保留。
- TLC 面板按工作区重建，避免空工作区残留上一项目输入；SFC Modifier 恢复原文但不恢复结构引用；共享恢复模块位于 `shared/prediction`，不让 utilities 独立反推依赖可裁掉的 prediction 页面模块。
- 只为替换测试 storage 对象新增的内存清理层已移除，测试各用独立 actor/key；没有增加依赖、后台重试或兼容性包装。

## 启动与剩余验收

- `5174` 是 Vite 开发入口，恢复运行后读取本轮 `src`，不把 `dist` 复制成开发源码。API/Worker/前端启动由后端负责人统一复用现有受保护入口，先检查资源与在途任务；本前端会话不另起真实服务。
- QA 已用独立标签页验证 HPLC 普通复选（未预测）、GC 三种预设 30 点且 40→200（未预测）、TLC 390px 无根横向溢出及切换/刷新恢复。控制台捕获为 `[]`，只代表该次捕获未记录 warning/error。原用户标签未触碰，自建标签已关闭并恢复视口。
- 真实最小链路：完整 TLC 一条消息创建一条真实预测，科学任务先于助手 Run 完成；另发缺条件消息仅补问、零新增科学预测。两次刷新后 BE 核对共 2 条用户消息、2 个 Run、1 条预测，没有重复创建。原失败依赖记录保留，修复后以新样例验证，没有重投原任务。
- GC 初次切换曾观察到草稿阻断提示，但稳定 DOM 恢复正常，填入原始结构后控件启用；未持续复现，故没有因此解冻、清理用户缓存或新增代码分支。
- 当前前端无新增修复项。五类预测、批量、数据摘要、模型草稿和会话管理为保留原边界，不表示本轮逐项真实预测或重新验收；未执行的 stop/timeout、多预测并发、其他科学预测及独立裁剪包仍未验收。

## 后续真实页面验收：退出历史重放入口

CC→HPLC→GC→SFC 继续验收时，QA 在 HPLC 观察到同类型旧重放指针恢复旧草稿，页面缺少安全的新建入口。现补“新建预测”：仅清除与当前已查看 draft ID 匹配的本地指针，清空当前 actor/project/type/tool 工作区输入和结果，移除 URL draft_id，重新读取当前能力。不修改或删除服务端旧草稿、预测记录及其他工作区，不绕过未知提交锁，不自动 POST。

新增两项定向检查覆盖隐式指针和显式 draft_id 路由，通过；类型检查通过。日志为 `new-prediction-test.log`、`new-prediction-typecheck.log`，同前述前端证据目录。首轮新增测试漏等初始异步加载，修正 fixture 后通过，初始日志以 `new-prediction-test-initial-fixture.log` 保留。未重跑 TLC 或科学回归，未重启服务；5174 通过 Vite HMR 读取此次源码。最终生产 bundle 将在本轮必要修复汇总后更新，前一阶段 dist 不能当作包含本按钮的交付物。产品源码再次 STOPWRITE，由 QA 通过 UI 继续 HPLC 验收。

### SFC 刷新冻结范围数量

真实 single 页面首次显示冻结候选数 1，刷新变“未知”。根因是观测模式的数量只在页面内存保存，无 result provenance 时恢复路径没有读取冻结范围。最小修复为：恢复 schema 1.1 预测时，用该预测输入绑定的 scope ID 读取现有 GET，核对项目、scope 与精确模型 id/version 后显示服务端 `candidate_count`。不从 rows、实际评分、TopK 或舍入值推算；读取失败或错绑仍为“未知”，原持久结果保持可读。已有耦合范围的严格 provenance 校验保持。

定向检查 3 项通过（观测模式安全 GET、错绑不采纳数量、原耦合恢复）；其余 42 项跳过，未重跑科学。类型检查通过；日志 `sfc-scope-count-restore-test.log`、`sfc-scope-count-typecheck.log` 位于同前端证据目录。源码再次 STOPWRITE；QA 仅 GET/刷新已完成 single 验证，再唯一发 full，最终生产 bundle 等 full 终态后统一更新。

### SFC 当前工具刷新游标

Full 唯一真实预测成功 2/2/2 后，刷新却恢复旧 single 1/1/1。原因是工具工作区虽已独立持久化，当前工具没有路由游标，重载默认选中了 single。最小修复为切换工具时写入 URL 的 `tool=single_column|full_column`，页面 setup 与访问上下文初始化保留合法工具选择；继续通过原 READY/schema 校验和 actor/project/type/tool 键 GET 恢复原任务。没有新增本地存储游标，也不使用结果条数猜工具。

新增定向检查 1 项通过：切换 full 后，按相同 URL 以新组件和新 Pinia 重挂载，恢复 full 的原任务 ID，只有 GET；其余 45 项跳过。首修仍被访问上下文初始化清空工具，已由此用例捕获并补齐，失败日志 `sfc-tool-cursor-first-fix.log` 保留。最终日志 `sfc-tool-cursor-test.log`、`sfc-tool-cursor-typecheck.log` 均通过。源码再次 STOPWRITE，QA 仅切 full 和刷新既有任务确认，不重跑 single/full 科学；最后一次生产构建继续等待此 GET 验收及 BE 空闲资源窗口。

## 四类真实页面验收后最终构建

QA 已报告 CC、HPLC、GC 页面提交与刷新恢复，以及 SFC single/full 两个唯一科学样例终态成功；BE 各自只读回执位于 `testing/runs/2026-10-21-four-types-browser/backend-{cc,hplc,gc}-readonly.json` 等本轮目录。TLC 未重复预测。三个实际前端缺口已最小修复并经定向检查，QA 实页确认：HPLC 新建入口退出旧草稿、single 冻结候选数刷新恢复 1、full URL 保留 `tool=full_column` 后刷新恢复原任务和 2/2/2、排序一致。single/full 恢复复验均只有 GET，没有重跑科学。上述运行结论以 QA/BE 本轮限定报告为准，不扩展为科学泛化、批量或独立裁剪包通过。

BE 在科学任务全终态、最终 idle 含 ack 为零后开放唯一构建窗口，已执行最后一次 `node node_modules/vite/bin/vite.js build`：exit 0，158 modules，3.13 秒；未重跑 tests/typecheck/模型、未重启服务。三处必要小修已纳入新的 dist；前一阶段的 `index-DiGNxSp5.js` 不再是本轮最终交付 bundle。

| 产物 | 字节 | SHA-256 |
|---|---:|---|
| `dist/index.html` | 524 | `34bf9bbfe710b66591ffc5645e7415fe40c68e792032204678dd95d5b62640d3` |
| `dist/assets/index-BmhrWPL8.js` | 638821 | `313a803419211eded4a1b23b5da67bfe4f886c147b81d6f57a84822acf6d312f` |
| `dist/assets/index-CUNSDtcn.css` | 73551 | `bc0ec70c510213861d25075394b2f983525bc45a060bfd2a2d5c254af64f8246` |

日志 `production-build-final-four-types.log` 与产物清单 `final-build-artifacts.json` 位于 `frontend/.tmp-purifyx-migration-20261021/`。既有大 chunk 提示非阻断，本轮不额外拆包。当前源码和 dist 均 STOPWRITE，5174 保留原 Vite 源码入口，构建窗口已释放给 BE/QA 收尾。
