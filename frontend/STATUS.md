# Frontend M3/M4 实施状态

## 已实现

- Vue 3、TypeScript、Vite、Pinia、Vue Router 的精确依赖声明与入口。
- `/api/v1` 同源统一客户端：响应/错误封装、`X-Request-Id`、写操作幂等键、`If-Match`、JSON 与二进制响应处理；`requestWithMeta` 保留 warnings、分页、响应头和 ETag。
- 编译期 `VITE_BUILD_PROFILE` 选择四个清单；一级导航固定为色谱预测、数据与知识中心、模型训练、模型中心、实用工具、色谱智能助手，records/datasets/knowledge 归入数据与知识中心，系统设置固定左下角。
- 应用启动并行读取 capabilities/version/access-context，build 不匹配和服务错误均保留为状态，不生成业务成功数据。
- `StructureInput` 支持 SMILES 规范化、复制、立体信息状态和后端真实结果；默认加载已打包的同源 `/ketcher/index.html`，环境变量仅在通过同源校验时覆盖，绘制器导出仍需后端校验。
- Job store/状态组件支持真实详情、取消、事件流；Confirmation store/card 使用服务端预览、revision、config hash、ETag 和幂等键。
- SSE 代码实现 `after_seq` 与 `Last-Event-ID`、同源业务路径校验、多行 data 换行、按 stream/job + seq 去重、断线 3 秒重连、失败时每 2 秒读取任务详情、游标过期（410）回退普通详情，并在恢复或终态时停止轮询/计时器；对应测试已实际运行。
- Vitest 最小测试覆盖 API URL/headers/error、导航裁剪边界、SSE 解析和 Job 生命周期。
- `src/app/generated/registry.ts` 采用 profile 生成器写出的显式静态 imports；各已存在模块提供 `module.ts` 注册入口，路由和导航从 generated registry 读取。
- M2 页面已接入：prediction 模块读取类型/工具/模型能力并用各 profile 生成的判别联合创建任务，支持预测详情、记录游标列表、服务端确认后归档和 replay-draft；universal 的 datasets 模块接入数据域 schema、导入草稿、validate、confirmation 绑定 commit 和数据版本列表。批量/反推及未被能力清单覆盖的工具显示不可用，不创建静态成功结果。
- `src/generated/contracts/<profile>/api-schema.ts` 由锁定的 `openapi-typescript@7.13.0` 从四份 M3/M4 冻结 OpenAPI 生成；生成脚本校验 SHA、normalized SHA 和数量：minimal 25/26、sfc 35/36、cc 35/36、universal 104/112；universal normalized SHA 为 `cb4f9b92dde639fc5188a4d898de23efb1e20b22733722bd50d7eff580f84d1a`。minimal/sfc/cc 既有输入和生成结果未漂移。
- universal 已接入知识检索/引用/文档登记与索引、真实模板和评测协议选择的训练草稿/校验/确认提交、模型中心 store、助手会话/消息/Trace/SSE/Resume/Context Inspector。新增 DTO 别名均来自冻结 operation/schema；M3/M4 之外的能力保持不可用或展示真实服务错误。
- Artifact store 已按生成 operation/schema 提供上传创建、`application/octet-stream` 分块上传、空 JSON `{}` 完成请求、Blob 下载和真实服务响应保存；Prediction store 接入能力/工具 schema、判别联合创建、详情、记录、归档与 replay-draft；records 页面接入游标列表及确认后归档；universal datasets 页面接入数据域、导入草稿、validate、confirmation 绑定 commit 和不可变版本 API。Confirmation store 使用生成 preview/create request，并保留 If-Match 与响应 ETag；API client 保留分页、warnings、202 Location/ETag 和二进制 Blob 元数据。真实制品扫描、模型推理、DB/Worker 完成和服务不可用场景仍依赖后端，前端不伪造成功。
- profile assembler 会先精简 staged `package.json`，删除生成/校验脚本和生成期 `openapi-typescript`，再从 frontend 已验收 `package-lock.json` 按依赖闭包确定性裁剪 `package-lock.json`，保留 resolved/integrity，不运行 npm 重新选版；每个 staged 包拥有匹配的锁文件。
- `scripts/generate-registry.mjs`、`scripts/assemble-profile.mjs` 与 `scripts/verify-profiles.mjs` 可对四个 build manifest 做 Node 静态装配和源码复制校验；非 TLC profile 不导入 TLC extension、不复制 TLC 源目录，未选模块不进入该 profile 的 staged `src/modules`。
- TLC 注册入口位于 `src/modules/prediction/tlc-entry.ts`，保留 TLC 算法目录只读；只有声明 TLC 的 universal profile 才静态导入该 extension。

## 当前限制

M3/M4 已冻结的 Assistant Event/Resume/Context/Knowledge/Training/Model DTO 通过 generated aliases 暴露；未冻结的扩展没有伪造类型。主 `frontend/src` 保留开发源码；交付包应运行 profile assembler，只复制 generated registry 和当前 profile 契约选中的模块目录、按模块映射的 view 与公共壳。staged 包移除 `core/build/profiles.ts` 和其他 profile 契约；`app/profile.ts` 仍保留完整 ModuleId 联合类型作为公共编译类型，这不等同于打包模块、路由或资源已恢复。Ketcher 静态站点已随 frontend 同源打包，但绘制器导出仍需后端校验。

## 实际检查

- 环境：Node `v26.8.1`、npm `11.19.0`。已在 frontend 执行 `npm install --no-audit --no-fund`，成功生成 `package-lock.json`；npm 报告了依赖弃用提示，并提示 esbuild 安装脚本未被 allowScripts 覆盖，但安装命令成功。
- 按已批准提案精确安装 `openapi-typescript@7.13.0`（仅 devDependency），锁文件已更新；未安装提案外依赖。
- `npm run verify:contracts` 已通过：四份冻结 OpenAPI 输入 SHA-256、M3/M4 normalized SHA 和数量与冻结清单一致，四个生成 schema 与 universal alias 无漂移。
- `node scripts/verify-profiles.mjs` 已通过：universal-v1、sfc-v1、cc-v1、minimal-structure-v1 均完成临时源码 staging，generated registry 静态导入校验、模块/页面裁剪、staged tests、共享目录禁用引用扫描、确定性锁顶层版本校验、必需 `src/styles.css` 与 `public/ketcher/index.html` 资产校验通过；边界测试覆盖 `src`、`public`、`src/public/逃逸目标`、根目录、junction 和 `--release-ready` 拒绝，sentinel 均保留。该命令本身是静态装配门槛，实际 staged ci/test/build 结果见下一条。
- `npm test` 已通过：6 个测试文件、23 个测试通过；新增 M3/M4 覆盖知识 project scope/登记幂等键/索引空 body+If-Match/归档 confirmation、训练 validate 空 `{}`/If-Match/项目 query/submit 202 Location、模型校验/部署确认边界、Assistant Resume 判别联合和 202 Location；客户端传给 fetch 的 options 已剥离内部字段。
- 已将 `src/views/SettingsView.vue` 与 `src/views/StructureView.vue` 的单行 script/template 重排为标准 SFC 结构，并修正生成器中 `model_center` 注册变量名漂移；`npm run typecheck` 通过。
- `npm run build` 已通过：生成 registry、vue-tsc 与 Vite 生产构建均成功，产物位于 `frontend/dist/`。
- 本轮 `npm run typecheck`、`npm run verify:contracts`、`npm test`、`npm run build` 和 `npm run verify:profiles` 均已实际通过；四 profile 静态装配通过当前冻结契约门禁。staged 包的独立 ci/test/build 结果见下一条。
- 四个 staged 包也已实际执行离线 `npm ci --offline --ignore-scripts --no-audit --no-fund`、`npm test` 和 `npm run build`：universal-v1 6 files/23 tests，sfc-v1、cc-v1、minimal-structure-v1 各 5 files/17 tests，四者构建均通过；临时 `.profile-check` 已清理。
- universal staged 包已实际通过 `npm ci --offline --ignore-scripts --no-audit --no-fund`（187 包）；本轮 staged lock 未重新解析版本。
- M3/M4 收口后四个 staged profile 均实际通过独立源码 `npm ci --offline --ignore-scripts --no-audit --no-fund`、`npm test` 和 `npm run build`（含 vue-tsc）：universal-v1 为 6 个测试文件/23 个测试，sfc-v1、cc-v1、minimal-structure-v1 各为 5 个测试文件/17 个测试；staged 包仍由静态装配门槛标记为 `release_ready:false`。
- 装配输出仅允许 frontend 下专用的 `build-output/<profile>` 或 `.profile-check/<profile>` 后代；拒绝根目录、`src`、`public`、`dist`、`src/public/逃逸目标` 和 junction/symlink 路径。`--release-ready` 对含未完成模块的 profile 在任何删除/复制前拒绝，staged 元数据明确为 `static-assembly`/`release_ready:false`。
- `dist/ketcher/index.html` 与四个 profile staged 的 `public/ketcher/index.html` 均存在；临时无后端静态服务器烟测返回 `/index.html HTTP 200` 和 `/ketcher/index.html HTTP 200` 后立即停止。Ketcher iframe 默认路径为 `/ketcher/index.html`，桌面高度使用 `clamp(480px, 68vh, 620px)`，窄屏为 480px。
- 浏览器证据（非完整验收）：桌面与 390×844 窄屏均完成 Ketcher 加载，相关静态资源返回 200；独立 Playwright 仅观察到预期的三个 `/api/v1` 404，后端未启动时界面显示真实服务不可用/错误状态，没有静态成功数据。应用内浏览器报告的 MutationObserver 异常属于观测层差异，Ketcher 源码仍存在 ref 与 ResizeObserver.observe 的竞态风险，不能据此宣称完整浏览器验收。
- 安装授权记录：用户本任务消息明确授权“让一个子智能体执行安装，并继续任务”；主应用依赖和 `openapi-typescript@7.13.0` 已按提案安装，未新增提案外依赖。
- Vite 已关闭现代浏览器不需要的 modulepreload polyfill；生产构建主 JS 扫描确认不含 `MutationObserver` 或 `modulepreload` polyfill 片段，构建入口 HTML 显式使用 `/ketcher/favicon.ico`，根 favicon 404 已消除。
- 备用 Ketcher ZIP 已只读核验，未解压或覆盖当前产物：根 `package.json` 未声明版本，`ketcher-core`、`ketcher-react`、`ketcher-standalone` 均为 `3.20.0-rc.1`。源码中的 `ColorSlider`、`TemplateTable` 和大分子属性图表均有进入 effect 时的 ref 空值判断，但随后仍直接以 `ref.current` 调用 `ResizeObserver.observe`；对应构建 bundle 仍可见该调用，因此不能把当前版本描述为已彻底修复空值竞态。推荐的最小修复是把 ref 当前节点捕获为局部常量并在 `observe` 前再次以该常量判空，或将观察逻辑封装为安全 helper，再重新构建 Ketcher 产物。
- `D:\Jupyter\ketcher` 的 `package.json`、`LICENSE`、`NOTICE` SHA-256 均与 `REUSE_MANIFEST.json` 登记值匹配，package-lock 存在；目录没有 Git 元数据，但本轮按路径与哈希证据继续复用。其唯一安装生命周期为 `postinstall: husky install`，已在单个 PowerShell 进程临时设置 `HUSKY=0` 执行 `npm ci --no-audit --no-fund`，成功安装 4011 包。
- Ketcher 直接 standalone Rollup 曾因 workspace 类型解析报 `TS2307: Cannot find module 'ketcher-core'`；最后一次 example-only 受控构建临时固定已核验版本 `3.20.0-rc.1` 后成功生成 32 个文件。`example/config/webpack.config.js` 原 SHA-256 `653f5b533dc18e78854f6d2db2ca6d026a75045e7aac38a2dff7f84876108603` 已核验恢复，临时备份已删除。完整站点已复制到 `frontend/public/ketcher`，包含 `ASSET_MANIFEST.json`、`licenses/LICENSE` 和 `licenses/NOTICE`；源/产物树哈希已登记在 `REUSE_MANIFEST.json`。此前根 `npm run build` 的 example 阶段仍会因来源目录无 `.git` 触发版本插件错误，但不影响已成功的 example-only 产物。
- 对生产源码的禁用旧接口扫描未发现 `8010`、`localhost:8000` 或 `/api/predict`；`Workspace` 命中仅为合法的 `TlcWorkspaceView` 文件和裁剪脚本标识，不存在 Coding/Workspace/Sandbox 业务路由。
# 优先上线恢复说明

优先上线切换前快照位于 `checkpoints/pre-priority-launch/frontend-20260921-1941/`。恢复延期功能时必须先阅读其中的 `RESTORE_NOTES.md`，在新目录解压并比较后选择性迁移；禁止把旧快照直接覆盖当前上线树或总控共享契约。

# 优先上线阶段前端状态（2026-09-21）

- Luna 已完成优先上线阶段的预测、助手和导航行为：预测页面保持真实能力驱动；助手默认业务模式，支持会话恢复、消息发送/停止、SSE 工具进度、预测结果、数据摘要、模型草稿/查询卡片及必要确认；助手导航顺序调整为 prediction(10) 后的 order 15。
- 验证证据：`npm run typecheck` 通过；Vitest 23/23 通过；生产 build 通过（105 modules）；`verify:profiles` 四 profile 通过。
- 关键文件 SHA-256：PredictionView `c8bd...b37`；SFC `9c87...7a4`；TLC `1210...6a8`；AssistantView `ff0d...946`；assistant store `4bbf...d7a`。
- FE-PRIORITY-001 已修复：navigation 定向测试 3/3、全量 Vitest 23/23、typecheck 和 build 均通过；`module.ts` 新 SHA-256 为 `514E59B786BFBF692B25E298ABD578E23F329E1B129D2EC5E409504861C69484`。
- 助手确认流程已接入正式 confirmation 校验：GET 返回对象必须匹配 resource_ref、action、revision、config_hash、当前 project，状态为 CONFIRMED 且未过期；已消费、过期、篡改和跨项目确认均在恢复前拒绝。刷新会重新查询会话、最新 run 和 tool calls。助手定向测试 9/9、全量 Vitest 23/23、typecheck 均通过；store SHA-256 为 `0C1BDC0D52F4BF8408012A8B83131983A05092244D087DA645D77136DB17124A`，assistant.test SHA-256 为 `AF0AA70D895B5BC05354169486F8DE030131A23757CA3E48766EEA8EFA5E4F2C`。
- active frozen contract 仍缺少按 `resource_ref` 重查模型登记草稿的接口；未新增候选路径或手写 active 类型，界面保留明确阻塞。
- 最终助手状态：confirmation 为 `CONSUMED` 时不重复 resume，而是恢复 run、events、tool calls 和 messages；notice 绑定 run，并在新 conversation、message 或 run 时清空。助手定向 9/9、全量 Vitest 23/23、typecheck 复验通过。最终 hash：store `448E40AB8E74A6FC188C22E61ABF13BD882CAF3FE7A000489AF25AA5DA87AF09`、AssistantView `A938F20DAD62828B4BC0963C3926054F63426FB67158D0A7AA014491613EE541`、assistant.test `DC243EBF19321BC331D113E9646C43CAEB64599CCB144B7050798ADB57EB5ADD`。active 登记草稿按 `resource_ref` 查询接口仍待冻结。
- 真实浏览器成功链路仍受 API assembly、`default_project`、active 契约和 Worker 阻塞；候选接口未写入 active generated types。Ketcher 备用 ZIP 已核验但未启用。

## Rev04 active contract migration (2026-10-02)

- 正式 active 输入已按冻结清单重新生成：`contracts/universal-v1/openapi.json` SHA-256 `ad3506ebf484d59fae92d396cf57114c22638f52ba79086cee2472a94af126f`，`contracts/index.json` SHA-256 `394cce1bdf788e803b9fcc3d0a0b4af5bd8c7b1a8b93f7c2d1c525d1c4aaaaf3`；生成结果仍由 `frontend/scripts/generate-contract-types.mjs` 产生，未手写 `src/generated/**`。
- 项目上下文使用 `accessible_projects: {id,name}[]`；显式选择优先，其次使用服务端 `default_project_id`；`needs_selection`、`no_accessible_projects`、空/缺失上下文均阻断业务提交。移除了 `accessible_projects[0]` 和 `project-default` 请求 fallback，结构输入由父层传递已验证项目。
- CC 提交要求两个已规范化且不同的结构引用。TLC 已接入冻结 options、condition comparison、reverse-solvent；UI 支持 `8:2`/`80:20` 输入，提交前明确转换到 `[0,1]` 且总和 `1±1e-6`，仅允许 `hexane`、`ethyl_acetate`、`dichloromethane`、`methanol`、`diethyl_ether`，拒绝 `petroleum_ether` 且不做映射。SFC 已接入 typed condition options、candidate scope 创建和 single/full-column prediction 请求；batch 保持明确未实现并阻断提交。
- 预测结果渲染按所有结果行联合列集合展示；TLC/SFC 缺必填 result metadata 时不显示伪成功。助手工具集合改为冻结名称，数据/预测摘要与模型登记草稿按 `resource_ref.id` 查询；草稿创建不发送 confirmation，正式 register 才使用 confirmation，既有 SSE、刷新、确认和恢复流程保留。状态展示统一覆盖 `CANCELED` 等终态。
- 实际检查：`npm run generate:contracts` 通过；`npm run verify:contracts` 通过；`npm run typecheck` 通过；`npm test -- --run` 通过（7 files / 27 tests）；TLC 专项 Vitest 通过（5/5）；SFC 专项 Vitest 通过（4/4）；Assistant 专项 Vitest 通过（9/9）；`npm run build` 通过（Vite 110 modules）；`npm run verify:profiles` 通过（universal-v1、sfc-v1、cc-v1、minimal-structure-v1）。
- 生成后的非 universal 文件与既有 staged 副本内容一致：`cc-v1/api-schema.ts` `548FAA324CEDDECB54E368056E3CE18DC06C554D7789D30A13D28261270B3BB5`，`sfc-v1/api-schema.ts` `4C4DE7BE3315918E36A28D7380BE819E26812BBEDA63E527CE02FD7D6EB0E1EC`，`minimal-structure-v1/api-schema.ts` `FFABC29DAB8571E5AC0BEEEB563F1C23B7236E96696939BB9AE197BEA12AE946`。
- 本轮未实现 batch§9，未做浏览器 E2E；`verify-profiles` 仍按静态装配门禁标记 staged 包 `release_ready:false`。

## Rev04 P1 follow-up (2026-10-02)

- `needs_selection` 访问上下文保留可访问项目选择器；只有用户显式选择已授权项目后才允许结构输入、模型加载和预测提交。显式项目仍优先于服务端默认值；无项目上下文才完全阻断。
- `result.ts` 对 CC/HPLC/GC/TLC/SFC 统一检查冻结 DTO 的 `task_type`、`schema_version`、`rows`、`applicability.supported_scope`；TLC 额外检查模型、制品、流动相和集成权重，SFC 额外检查 `score_semantics=positive_class_probability`。结果元数据错误时预测页不显示结果表或“已完成”成功态。
- 预测状态轮询覆盖冻结终态 `SUCCEEDED`、`PARTIAL`、`FAILED`、`CANCELED`、`TIMED_OUT`、`INTERRUPTED`，状态标签保留真实服务端状态。
- Assistant 消息块改用生成 `ContractMessage` 联合类型分支；SFC 动态候选组通过明确字段 guard 解析，移除本轮触及的 `as unknown as` 转换。
- 新增/扩展纯函数测试：单项目、多项目、无项目和 `needs_selection` 状态；CC/HPLC/GC 基础结果元数据缺失；TLC/SFC 专用元数据缺失；预测全部冻结终态。
- 已通过：`npm run verify:contracts`；`npm run typecheck`；`npm test -- --run`（7 files / 29 tests）；TLC 专项（5/5）；SFC 专项（4/4）；Assistant 专项（9/9）；`npm run verify:profiles`。按总控要求等待 BE handoff，未重新运行会改写 frontend generated manifest 的 build。
- 本次修复文件 SHA-256：`src/core/project-context.ts` `320a00ed5e561d9ddae16b83c79daf0561d7d5d58008dad4239af1a9343f5b7a`；`src/modules/prediction/PredictionView.vue` `afb901bb0e793ede8e06c5008b25410a3cbd9f8f8cdf1a6ea478f1e3e7a0f9a2`；`src/modules/prediction/result.ts` `a14ed1d9984317a4ff56e0b4f6855cdb92203ba20ec00c86106c2a40641505db`；`src/modules/assistant/AssistantView.vue` `d7c6d7d1b099aa596b69bd0f61e35f3dd180d25b8b369eafc281251b73ba11cd`；`tests/rev04-contract-migration.test.ts` `dd3351693d9713fd3bcb31684addcf150690068aebede064afc14d03dca46fdb`。frontend 外目录、`builds/**` 和 generated manifest 均未手工修改。

## Static browser readiness (2026-10-03)

- 在未操作后端、DB、Redis、Worker 的前提下完成一次临时 5173 浏览器巡检；Vite 精确 PID `51592` 已在结束时停止，5173 监听数为 0。9012 既存服务未操作。
- 桌面导航顺序、batch 不注册/不伪装、加载/空/失败保护、助手空会话、刷新保持、390px 无横向溢出和控制台 error/warn 巡检均有记录；详细观察、截图路径、哈希和门禁见 `STATIC_BROWSER_READINESS_2026-10-03.md`。
- 9012 能力接口当前仅返回 `structure.normalize: UNAVAILABLE (MissingGreenlet)`；因此五类真实表单、服务不可用恢复和真实 SSE 没有被算作 E2E。TLC 5/5、SFC 4/4 专项组件测试以及 verify:contracts、typecheck、npm test（29）、build、verify:profiles 均通过。

## Build manifest handoff (2026-10-02)

- BE handoff 后核对四份 build 源清单 SHA：universal `569613e69680a4458e1319449ad98c00e82519d4bcf9b0aeb930f58e24c3a332`，sfc `005ad94228473704842c0c80502752a30107ce83d94aa03d77d0211f62542619`，cc `f2ba2c7a6d50e69316c83b8abe280541f45385cd9fe5ddfa3836b29327c319ea`，minimal `aa34132508dee8cad2a17b0af30ee0019aa06d4d553389851e86d0b90b88ba15`；与本地 `builds/*.json` 一致。
- 使用既有 `npm run generate:registry` 和 production build 流程重新生成 `src/app/generated/registry.ts`、`profile.ts`、`build-manifest.json`；未手改 generated 文件。最新 SHA：registry `43f033075adb8c22f3244df8d60c20e81dc26f608f1bcc021af18d784632d069`，profile `4746c9ef02245c3f2005205c18be16a973d062aee59ce717360a923b4cf0484b`，build-manifest `b3b53808db507f337fc3c2852357aa7796a795372b1ae9bc1747c5c6ed8cb163`。
- 四份 build 清单及生成 registry/profile 不再声明 batch 入口或能力；batch 表单/不可用状态组件源码仍保留在 prediction forms 中，未进入正式 capability 或提交路径。
- handoff 后门禁全部通过：`node scripts/generate-registry.mjs --verify`；`npm run verify:contracts`；`npm run typecheck`；`npm test -- --run`（7 files / 29 tests）；TLC（5/5）；SFC（4/4）；Assistant（9/9）；`npm run build`（Vite 110 modules）；`npm run verify:profiles`。

## HPLC 1.1 候选接线与独立复验（2026-10-07）

- 接入 rev15 候选契约的局部生成类型，未覆盖正式冻结类型。提交使用两条原始有序 SMILES、两个有序结构引用和能力返回的精确模型/预处理版本；不从 canonical SMILES 或旧结构 original_input 回填原文。编辑、项目切换、类型切换和能力重载会使旧绑定失效，迟到规范化与提交响应不会污染当前页面。
- 历史 1.0 输入保持只读；缺原文或预处理身份时禁止执行。正式 HPLC 结果的输出 schema 必须与模型快照 output_schema_version 完全一致，否则隐藏结果行及 warnings；限制提示来自实际结果 applicability.warnings。
- 独立 QA 主接线检查通过：42 个候选 schema 校验、85 个定向/跨类型测试和类型检查；最后输出身份保护另通过 23 个定向测试及类型检查。两次测试存在覆盖重叠，不能相加为独立测试总数。证据分别为 `testing/runs/2026-10-07-hplc11-incremental-qa/frontend-rev15-binding-independent-qa.md`（SHA-256 `10769A9EC3BFA07B62FA8231ADD36646DCC764E5A94F98AF28D69AED3AF0BBAE`）及同目录 `frontend-output-schema-guard-independent-qa.md`（`24402BD9E03E766FFCFA1169AD09909C6810BB77C63EF5290AC98F57772FFEA0`）。这些是 mock 组件/状态检查，不是浏览器或真实模型验收。
- 最终固定文件：`src/modules/prediction/PredictionView.vue` SHA-256 `B3AF06234A8441D16095F67F7CE0E42B635F555ED8DE51B84DB6F05119872D09`；`tests/prediction-type-availability.test.ts` `387EB79DA2A45270299DA35C79E32A96B24058D5938BC46CD120F187A1FD2804`。本轮保留无 batch 构建清单，不执行安装或重新生成正式契约。
- 总控已授权受控加载后由 QA 唯一提交一次真实浏览器预测；本条记录时仍待服务交接及浏览器/数据库/Worker 验收，不声明 READY、release ready、训练预处理等价或独立泛化通过。
- 协作要求：执行子智能体完成后先主动向负责人发送文件哈希、实际测试、阻塞与停写状态，再结束子任务；负责人接续审查、独立 QA 和下一阶段，不以子任务完成代替项目完成。

### 首次真实结果校验修复（同日）

- 唯一浏览器预测已得到后端 `SUCCEEDED`，但预测页首次误拒结果：共用 metadata guard 要求 HPLC result 重复携带模型和制品字段，而 rev15 `HplcPredictionResult` 并未定义这些字段，身份在外层 `Prediction.model`。此前 mock 多填字段掩盖了错配；保留首次失败，不记为首次浏览器通过。
- 原执行子智能体已增加 HPLC 专属 DTO 校验并移除测试中伪造的 result 模型字段；模型输出版本守卫保持不变，其他色谱类型的校验未放宽。定向 32 项测试及类型检查通过为执行者证据，独立增量复核和浏览器同任务刷新仍在进行；不重复预测。
- 新最终 pins：`src/shared/prediction/result.ts` SHA-256 `BDB451E82860102BF6A85927F93699FD775ED0B5C0EF587F263604F1D7D31AE3`；`tests/rev04-contract-migration.test.ts` `3478C866618E3D2D6BBE4816F14C54449CB4BBA6129E963C7A0A6ABF56883012`；`tests/prediction-type-availability.test.ts` `44C118FDF74B07D7E4D6230117E11BB23F9CBF8B8414B57DC9E80C096029E2AA`，覆盖上一小节该测试的旧 pin；View 仍为 `B3AF06234A8441D16095F67F7CE0E42B635F555ED8DE51B84DB6F05119872D09`。
- 上述结果校验修复已通过独立 32 项测试及类型检查，报告 `testing/runs/2026-10-07-hplc11-incremental-qa/frontend-live-dto-guard-independent-qa.md` SHA-256 `B5AADEC1D75D2A010CD4C37AC3FB44BFE616C8268DFE0FDC35FED84CA387A336`。QA 只刷新同一真实任务，预测页结果恢复；完整保留时间指标在历史详情展示，不把预测页的两行候选表描述为完整指标表。
- 历史原文带回又发现合法空数组被误拒：无 Modifier 的真实草稿返回 `modifier_structure_snapshots: []`。原执行者修复为接受字段缺省或空数组，继续拒绝非法 null/非数组/非空未绑定快照，保留 SFC 项目与引用绑定检查。`src/shared/prediction/replay.ts` SHA-256 `116FFC8829D874CD1E6F0BB97150F3F43E2A22AC25C88AE881D6DB2A8A8CEECF`；`tests/prediction-profile-replay.test.ts` `50C37D609D5460F0C14A86B58EA88E81D100BB861E665BADC085FAA4183E89EC`。执行者 44 项定向测试及类型检查通过，独立增量与已有草稿 GET 恢复待回交；不重新创建草稿或预测。
- 上一项独立增量已通过 44 项测试及类型检查，报告 `testing/runs/2026-10-07-hplc11-incremental-qa/frontend-live-replay-empty-snapshots-independent-qa.md` SHA-256 `45DC76E0A34A07D05355D791615BF285D168AACD8F4B22B025BCAC2B6D26AF89`。QA 已通过已有草稿的 GET 原文/顺序/模型身份/条件带回及刷新；390px 检查无整页横向溢出，控制台无错误。最终业务计数与队列状态以 QA 总报告为准。
- 最终 QA 报告 `testing/runs/2026-10-07-hplc11-incremental-qa/live-preflight-and-result-independent-qa.md` SHA-256 `909246DA80A6E083419C1371BFDE3804249457E2A29FB838E065BBF77E5D0461` 已由前端负责人只读核对。本轮只有一次新预测、一次 Worker attempt 和一个重放草稿；同预测结果恢复、已有草稿 GET 带回/刷新、390px 及 console 检查通过，最终 DB/queue 空闲。14 个 golden 字段最大差 `4.887695315147766e-7`，在原 `5e-6` 容差内；不宣称 exact。两次首次 UI 失败完整保留。结论仅为指定 HPLC 作者部署的单次软件集成，不代表 batch、旧 1.0 live 历史、Python 3.12、训练缓存等价、泛化或总体发布通过。
## GC 前端增量实现与回交流程（2026-10-07）

- 原执行子智能体完成后已主动向前端负责人回交文件哈希、实际测试和停写状态；负责人继续源码审查、独立 QA 和下一阶段分析。子任务完成不代表平台任务完成。
- A 阶段已修复温度序列空项/非有限值、CRLF 解析、线性终温边界及 busy/disabled 提交保护。独立报告 `testing/runs/2026-10-07-gc-readonly-preflight/frontend-incremental-independent-qa.md` SHA-256 `FDB272C08CD6F04372976C2B3C82AA756C31099E28E40AF8B4B42BEFC0F96857` 已实际读取及核对：4 项组件测试通过，范围不含能力长度、浏览器或模型。
- B 阶段已按实际 GC result DTO 展示保留时间和可选分位数（min），共用不可变外层模型快照身份与输出 schema 校验；不再要求 result 携带契约中不存在的模型字段。旧 profileless 快照仅用于历史只读。执行者 4 文件 55 项测试及 typecheck 通过，B 独立增量 QA 已交接，尚未记为通过。
- 当前 B 源码固定：shared/prediction/result.ts `2BA6A111142028321832DE3DB6B4C30B083E1B9DC2DC4185A7DCCAB9C8679112`；PredictionView.vue `111104333AE83A85E42B334018A612CD00017D57AC199D5F598BB1A045DA94BA`；PredictionRecordsView.vue `A2F460DAD35FD9A3682CE7D60941121DF421E371EB374C124D91F90A60691D1A`。这些值覆盖上述历史小节中对应文件的旧 pin。
- 下一阶段等后端 GC1.1 候选 OAS 和真实完整模型能力契约，再接入必需的 exact preprocessing pair、模型温度长度/边界和重放执行保护；不手写候选 DTO、不写死 30 点、不以 UNAVAILABLE 占位 schema 或静态 fixture 作为运行时默认。原执行者当前仅分析下一阶段方案，不与 QA 并发写入。
- 本轮无 GC HTTP/POST、模型推理、服务重启或依赖安装；不宣称 GC READY、浏览器 E2E、科学泛化或发布通过。
- B 独立增量 QA 已完成：三文件 51 项测试与 typecheck 通过，7 个固定文件测试前后哈希一致；A 原 4 项未重跑。追加后的同一报告 SHA-256 为 `753F8FEAB348AF6CAEF6B87B08560CDD6DA924ABFF1BA4706A44708C941238EB`，负责人已实际读取并核对。覆盖共享 HPLC/CC 场景，不构成 GC1.1 接线或 live READY。后端另报告误跑含真实 GC 资产测试的事件，由 BE/QA 核实，未作为科学通过；前端产品保持停写，下一阶段仅只读分析。
- GC1.1 rev18 前端增量已由原执行子智能体完成并停写。新增隔离 OpenAPI 类型投影，提交路径使用 `schema_version=1.1`、GC 专用必需 exact profile pair 和恰好一个结构引用；旧 1.0/profileless 草稿保留只读。温度字段、保持时间倍数与序列长度从当前经验证 READY 模型的完整 schema 解析，静态 unavailable fixture 不作运行 fallback。模型约束温度点字段按真实 adapter 名 `temperature_points` 读取。
- 执行者定向 5 files / 44 tests、typecheck、`node scripts/generate-gc11-candidate-types.mjs --verify` 均通过。负责人对12个固定源/测试文件逐项复核SHA并匹配：gc11 generator `F9D19C6D7EAB1AE549FD497823C2FCBDA299D58AFD5CA998E6A7FF0FC809E6C7`、generated `A1A21B5FD99C2C158EB3B68EA467078A0BBF277C6CE8AF2F2D5618A7621EEAAB`、gc11 helper `9157A19EA35E68B6C9B707D11DCA95E6264D921461FBEB6A5D1BFC77E1C08FDA`、schema resolver `F6E3D12B40C9DE04904BA51691071AE1854C9799D613FBBF50A7847FBFB62245`、GC form `E69769326AA2620BC8ED30D8D7CE56D8BF9E6F22F52C1F3F63A94FF27B25EE61`、store `405802EA9BCBB958569ECF6E9F28D149A585B29923B2394502D0D4BE932B462C`、PredictionView `50E9E102D421E6681E2010C91EC9E7228731946E171A32FFA2D56B29589E1DAA`、`gc11.test.ts` `803BE0F9DB3C6A4A69DBD53C21BC3D941066BE9B5971AB14DF18BD9145F0FD11`、`gc-single-form.test.ts` `2B9CE6662B74E7055CBB82794372DA95D071BA4FA21EE41CDD41BBFF364D1FC5`、`gc11-page.test.ts` `ED24E4814CC4C2EA075FDC4BBDD67A915C848E219FBAA385938D89188DA2E45D`；共享 availability/replay pins仍分别为 `E299235BA7C4CF215D57EEBA708F545C0CD26E26A3B6AFF6770CB57A33F46F1C`、`50C37D609D5460F0C14A86B58EA88E81D100BB861E665BADC085FAA4183E89EC`。
- 新 pins 已交独立 QA 复验。复验前负责人要求核查该次新请求 profile 是否与返回 Prediction 的 immutable model snapshot 一致；不将独立历史记录与当前能力作错误比较。当前无浏览器/ live GC POST/运行模型或服务重启证据，待此次 QA 后再进入已批准的受控加载流程。
- 总控/负责人审查随后发现同一任务 UI 尚未对 GC1.1 返回结果强制 profile 存在。原执行子智能体已补 `gcPredictionModelSnapshotError` 的版本判别：1.1 要求 snapshot input schema 1.1 和非空完整 profile；当次新请求轮询还精确比较返回 id/version/profile 与请求；1.0 既有历史仍允许旧快照。记录详情仅按本记录自身 schema/snapshot 校验。
- 此补丁执行者验证：`npx vitest run tests/gc11-page.test.ts tests/rev04-contract-migration.test.ts tests/prediction-type-availability.test.ts`（3 files / 41 tests PASS）、`npm run typecheck` PASS；原 generator SHA 未改，之前本轮 `node scripts/generate-gc11-candidate-types.mjs --verify` PASS。本补丁新增固定：PredictionView `416D26C02976589C4B61CA014D25952EEEB526275B85C50D2E4C10AFEC6AECEC`；shared result `2DA09733A82EA7A17C146AB25322B337405DAD0FCB4426A6296EEC5D3E24B3F4`；prediction result barrel `F129E344C7744A228971F6D545FD5447CA6EE4CC2FD8D2C26D7395D1D7AAEA55`；records detail `77B7D3F4854BDB2C69BF2ECC2A0530861AEAB459A4C770B7A32B14420F177969`；GC page tests `71ED77463B9D7155F4026E82BE6294B622C3A1BE26658B630C541DA54F2CF4B0`；rev04 tests `10634F92136914871A84FEAE4065D4B0AD1F676D86F01CC127C130FC778D1D47`；type availability `9F692A1022D5CE4958BB5670C9F34D46815779CC2FAE29A2713EEBC4F307CFD1`。最终增量 QA 进行中；尚未签收或作 live 准备结论。
- FE GC1.1 rev18 focused 独立 QA 已完成：`testing/runs/2026-10-07-gc-readonly-preflight/frontend-rev18-focused-independent-qa.md` SHA-256 `6B84B4D2B3CBB3691A0ABDECD4F7B596165A9916D770AD6D308B7A15156957C2`。结论仅接受源码静态接线与当前 mock/fixture 测试证据，不覆盖 READY、浏览器、真实 API、Worker 或模型。QA 发现 5 文件 handoff 记 44 项，但当前源码为 46 个 it 节点；本次独立执行证据限 3 文件/41 项与 typecheck，generator verify 为作者 handoff 证据。已记录后端 packaging 门禁仍阻塞，未进行服务切换或 live 请求。
