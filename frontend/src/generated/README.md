# Generated API types
本目录由冻结 OpenAPI 契约生成。`src/generated/contracts/<profile>/api-schema.ts` 只能由 `npm run generate:contracts` 更新，脚本先核对 `contracts/index.json` 中的 SHA-256，再调用锁定版本 `openapi-typescript@7.13.0`。源码包只复制当前 profile 的 schema 和 `api-schema.ts` 别名，不携带其他 profile 契约。

当前 M3/M4 冻结基线为：`minimal-structure-v1` 25 paths/26 operations，`sfc-v1` 与 `cc-v1` 各 35 paths/36 operations，`universal-v1` 104 paths/112 operations。生成脚本读取 `docs/V1_M3_M4_CONTRACT_FREEZE.json`，同时校验四份冻结 OpenAPI 的 SHA、normalized SHA 和数量。知识、训练、模型中心、助手/Trace/Context DTO 均从 selected profile 的生成 schema 映射；不从旧 snapshot-04 恢复类型。

预测模块按 profile 使用生成的判别联合请求体；数据中心只在 universal 注册数据域、导入校验/提交和数据版本页面。批量、反推及未被当前能力清单覆盖的工具保持不可用状态。Assistant 的 `/agent-runs/{run_id}/events` 与 `/events/stream` 已冻结，store 使用生成 Event DTO、after_seq/Last-Event-ID、序号去重、断线恢复和 410 详情回退。
