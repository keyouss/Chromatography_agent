# Settings 配置门禁只读复核

日期：2026-10-13（Asia/Shanghai）

## 结论与定位

只读审阅未发现确切的 Settings 前端源码缺陷；不新增镜像 mock，也不重跑既有 Settings 展示或助手恢复测试。页面位置是 [SettingsView.vue](D:/Jupyter/Chromatography_agent/frontend/src/views/SettingsView.vue#L66)，API adapter 是 [llm-api.ts](D:/Jupyter/Chromatography_agent/frontend/src/modules/settings/llm-api.ts#L45)。页面 mounted 发起 public/settings 两个 GET；loadSettings 用请求代际丢弃迟到响应，刷新先清 ETag/标 stale；test poll 以代际、test ID、configuration ID/revision 保护；激活将同一 settings 响应的 ETag 与 active_revision 成对用于 If-Match/body，409/412 后重读。助手 Run 的配置由服务端选择，FE 恢复并保留完整 Run 响应。

已有 FE mock 精准用例（本阶段未执行）：

- [settings-view.test.ts](D:/Jupyter/Chromatography_agent/frontend/tests/settings-view.test.ts)：L175 “ignores a late previous candidate result after switching candidates”；L214 “refresh cancels old candidate polling and restores the latest last_test_id only”；L288 “submits controlled fields and a one-time key, then clears it without persisting it”；L322 “clears the key after a failed create and never renders the service message or key”；L368 “requires a successful same-candidate test with streaming and tool calling before activation”；L402 “creates a candidate, polls the accepted test to terminal success, then activates with fresh revision and ETag”；L446 “marks stale settings after a 412 refresh failure and keeps activation disabled”；L473 “refreshes after a 409 and permits a guarded retry with the newly returned ETag and revision”；L505 “can pause and resume polling the same accepted test ID without declaring a failure”；L530 “clears the complete draft on cancel and clears the one-time key on unmount”。
- [llm-api.test.ts](D:/Jupyter/Chromatography_agent/frontend/src/modules/settings/llm-api.test.ts)：L32 ETag GET；L47 credential-source/response headers；L74 accepted test + poll；L100 If-Match/revision/idempotency/conflicts；L127 invalid credentials/preconditions/headers。

## 证据边界与下一步

[2026-10-06 配置生命周期 QA](D:/Jupyter/Chromatography_agent/testing/runs/2026-10-06-config-assistant-acceptance/live-settings-browser-api-e2e-qa.md) 记录历史真实候选创建、Provider test 先失败后成功、一次 CAS 激活及 audit verifier；失败根因未知，且不证明坏 key 或并发 CAS。[2026-10-13 Settings GET-only QA](D:/Jupyter/Chromatography_agent/testing/runs/2026-10-13-plan-resume/browser-getonly-recovery/browser-settings-getonly-independent-qa.md) 记录 5 个业务 GET 全 200、SSE/非 GET 为 0、active=1 且只显示 key 已配置。[现存对象 GET-only QA](D:/Jupyter/Chromatography_agent/testing/runs/2026-10-13-plan-resume/assistant-existing-objects-getonly/GET-only-evidence-independent-qa.md) 记录 7 个 GET 全 200，单个已存在 TLC Run 的 configuration ID/revision/hash/model 与当时 active 配置匹配；这不是切换前后隔离证据。

BE owner 补充的只读源码结论（非本 FE 实测）：backend/src/chrom_platform/modules/assistant/service.py:527-560 创建 Run 时锁 active/config 并保存 ID/revision/hash；production_worker.py:253-280、623-632 按 Run 保存绑定读取配置，允许 ACTIVE/RETIRED 并核 revision/hash；backend/tests/test_assistant_service.py:84-99 覆盖创建绑定，backend/tests/test_assistant_runtime.py:802-845 的 RETIRED 仅为 fake store。尚无真实 DB 在 active 切换后运行旧 Run 的一体证据。

坏 key、真实并发 CAS、旧/新 Run 快照隔离、真实进程重启及 HTTP/SSE 恢复仍是运行态门禁；mock 或 GET-only 不可结案。最小后续顺序：先只读核现存旧 Run 与配置绑定；坏 key 需合成无效 key 候选+真实 Provider test；并发 CAS 需同一 test/ETag/revision 双请求；旧新 Run 需配置切换前后各创建并比较 Run；重启恢复需受控重启并核 Run/events/SSE/410。先由总控核对运行窗口与副作用预算。

本阶段只读；未运行测试、HTTP、数据库、浏览器、Provider、安装或服务启停。本收据写入不代表新的 runtime 验收。