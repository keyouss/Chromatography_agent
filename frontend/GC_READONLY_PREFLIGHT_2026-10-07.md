# GC 前端只读前提检查

日期：2026-10-07。范围：现有源码、PRD/API、rev15 候选 OpenAPI 与后端提供的只读 GET 证据；未修改产品、执行测试、加载模型或提交预测。

## 当前能力

证据：`testing/runs/2026-10-07-gc-readonly-preflight/live-gc-type-model-schema.json`，SHA-256 `BDF0DC40A91012867AB1BFFA15EA147D01899564132C4C436F0662DF3D7C3C39`，负责人已只读核对。

- 当前 type/tool/model 均为 `UNAVAILABLE / MODEL_VERIFICATION_REQUIRED`，不能将历史 seed42 复现等同当前批准。
- 当前模型声明 linear/series、30 个温度点、q10/q50/q90、CPU、固定 5% phenyl/95% dimethylpolysiloxane；载气影响不受支持。模型输入 1.0、输出 gc-retention-time-1，公开 profile 为 null。
- 不可用工具返回的是简化能力描述，不能据缺少 properties 推断正式字段不存在。完整静态 DTO 参考 rev15 `GcInput`、`GcLinearTemperatureProgram`、`GcSeriesTemperatureProgram`、`GcPredictionResult`；READY 能力与科学门禁仍由后端及 QA 核对。
- 通用 series DTO 长度为 1～10000，当前模型要求 30；前端不得把 30 写成所有模型的全局限制。线性升温率必须大于 0、保持时间不小于 0、终温不低于初温；未声明的绝对温度范围不得猜测。

## 已确认的前端缺口与最小修复设计

| 位置 | 当前问题 | 最小设计 |
|---|---|---|
| `src/modules/prediction/forms/basic/GcSingleForm.vue` | 默认序列仅 5 点，过滤非法数字，空 token 可能变 0；不读取模型限制；终温使用严格大于初温 | 严格解析每个 token；读取当前能力模式、序列长度、单位及明确范围；允许合同内终温等于初温；不补点、丢点或 clamp |
| `src/modules/prediction/PredictionView.vue` GC form 调用 | busy 合并运行与禁用原因；缺 GC 专属 tool/schema 健康保护 | busy 仅代表提交；其他原因单独 disabled，模式和关键能力形状不明时禁止提交 |
| `src/shared/prediction/result.ts` | 通用 guard 要求 GC result 重复包含 model_id/model_version/artifact_sha256，实际结果 DTO 没有这些字段 | 增加 GC DTO 分支，验证实际 schema/rows/applicability；模型身份读取 Prediction.model，不增加伪造字段；输出版本绑定策略与后端对齐 |
| 预测及记录结果表 | quantiles 嵌套对象直接 JSON 展示 | 按正式结果中存在的分位数与单位展示业务字段，不补值、不重算、不生成未支持曲线 |
| GC replay 输入校验 | 仅检查字段/有限数，不核对当前模式、序列长度和条件限制 | 当前能力校验失败时保留原草稿只读并禁止执行，不改写历史原文或条件 |

当前 type 不可用时，页面会隐藏表单并阻止提交；上述缺口尚未触发真实 GC 请求。

## 测试缺口与后续边界

现有类型可用性测试的 active profile 只含 CC/HPLC，不能视为 GC 页面覆盖；结果测试未覆盖真实 GC DTO；记录测试不等同 GC 表单和结果验收。

确定实施范围后补最小测试：非法 token/空项、当前声明长度与其他模型长度、线性等值条件、busy/disabled、type/tool/schema 不可用时零 POST、真实 GC rows/quantiles、旧草稿与当前能力不匹配时只读。模型科学批准和真实浏览器预测需另行明确门禁；本报告仅为缺口分析，不是测试通过或 READY 证明。

已验 HPLC 文件、服务及所有历史失败证据保持固定。主要后续修改继续由原执行子智能体承担，完成后主动汇报哈希、实际测试及停写状态，由负责人接续独立 QA。

## 后续授权与实施状态

总控随后批准有界前端修复：严格温度解析、busy/disabled 分离、GC DTO 结果校验与分位数展示先实施；模型能力、版本选择和 replay 在后端新候选契约固定后接入。上述只读时刻的 null-profile 状态仍是历史事实，不代表新任务可沿用无 profile 身份。

新 GC 任务按后续裁定采用 schema 1.1、exact 模型及必需不可变 preprocessing profile pair；旧无 profile 记录保留只读，不回填，重放执行须失败关闭。新候选未固定前，前端不手写另一套 DTO，也不覆盖 rev15。

后端已指出当前参考模型保持时间须为整数分钟，但旧通用 JSONSchema 只表达非负。该模型的完整能力将明确 multipleOf=1；前端只读取当前模型声明，不把整数要求或 30 点长度扩展成所有 GC 模型的固定规则。静态 handoff 仅是测试上下文，不能用于运行时启用。

原 Luna 已提交第一表单切片，负责人审查发现 Windows CRLF 被拆为两个分隔符，正补修及边界测试；过渡测试结果不作为最终验收。GC 结果展示切片同步进行。GC 仍不可提交，未新增业务 POST、推理、依赖安装、服务重启或协议批准 flag 变更。

## 前端 A/B 独立验收及 GC1.1 接线方案回交

A 表单/parser 4 项与 B 三文件 51 项定向测试分阶段通过，B typecheck 通过。独立报告 `testing/runs/2026-10-07-gc-readonly-preflight/frontend-incremental-independent-qa.md` 最终 SHA-256 `753F8FEAB348AF6CAEF6B87B08560CDD6DA924ABFF1BA4706A44708C941238EB` 已由前端负责人实际读/hash核对。不是全量/浏览器/科学或发布验收。

原执行子智能体已主动回交下一阶段只读分析并结束，无新增代码或运行操作。负责人审查确认以下最小范围：

- 隔离的 GC1.1 candidate 类型生成器与生成文件、`shared/prediction/gc11.ts`：仅从后端固定候选 OAS 生成，保留正式冻结类型和现有 HPLC 投影。
- `shared/prediction/store.ts`：增加 GC 候选请求分支，使用既有统一 API 客户端，不另建 HTTP 逻辑。
- `PredictionView.vue`：GC 专用精确选择/新建提交、旧1.0与无 profile 草稿只读门禁；项目、类型和能力重载均使迟到响应失效。旧草稿保留原值，不补 profile、不切换模型、不钳制温度。
- `GcSingleForm.vue`、`gc-temperature-program.ts`：从当前模型完整可信 schema 取得允许模式、温度/速率/保持约束及序列长度。缺字段、不可信或 UNAVAILABLE 保持禁用；静态 fixture 不进入运行时。
- `replay.ts`：不全局拒绝旧 profileless 历史；新执行门禁由 GC1.1 专用校验处理，避免破坏其他类型只读恢复。

后端交接需包含固定候选 OAS 路径/版本/SHA、GC1.1 新请求与旧输入联合、必需真实 profile pair、完整模型输入/输出能力 schema、READY 条件以及 replay 快照字段。交接后复用原执行者实施，再由负责人审查与独立 QA；当前不手写未知 DTO、不启动 GC POST 或模型调用。

后端报告误跑真实资产测试的事件独立保留和核实；其未知调用次数与失败不计科学 PASS，不影响本段纯前端静态测试证据。后续科学认可及 runtime READY 等总控/BE/QA核实。

## rev18 候选接线启动

后端已交接并由前端负责人实际读取、核对：

- 候选 `contracts/candidates/universal-v1-gc11-revision-20261007-18/universal-v1/openapi.json` SHA-256 `268923554A761C9C131DD916982094188B5126FACAFD941132838466E8111A15`。
- 静态完整模型契约 `testing/runs/2026-10-07-gc-v11-candidate-rev18/gc-static-profile-contract.json` SHA-256 `9DB1E365D8C15B6C6C470F6A396B18638FCF88DC4D524959B5D2C364CF5EADC4`；profile SHA-256 `a3c1dc6d576973682357818e2424e0640617ec4a959d40d71fbbb40a733b8799`，状态 ready=false、verified_handle_issued=false。
- 独立 cardinality/metadata 报告 `testing/runs/2026-10-07-gc-readonly-preflight/gc-cardinality-rev18-independent-qa.md` SHA-256 `AF6B1CDB5992F5E2C0FF989F24B59DE5C6AA3DE8AD3D8A044D363B39CB95F975`，负责人实际读/hash一致。

GC 新建固定 schema1.1、exact 非空 profile pair、恰好一个结构引用。静态模型 schema 包含线性保持 multipleOf1、序列 min=max30，但运行时约束必须取自当前可信 READY 模型，不能硬编码该值或导入静态 fallback。rev16/rev17保留历史，正式 index/types 与 HPLC rev15不覆盖，三非GC包沿已签来源复用。

原 FE 执行子智能体已获准唯一写 frontend 的局部候选类型、helper、store、view、form及重放门禁，并执行不涉及HTTP/模型的定向测试。完成主动回交 pins 后由负责人审查再交独立 QA；当前尚未将本轮候选接线记为通过，不启动真实预测、服务或依赖修改。
