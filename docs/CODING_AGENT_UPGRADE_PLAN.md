# 色谱算法研发助手：Coding Agent 后续升级方案

文档状态：FUTURE DESIGN · 后续升级设计，不属于首版实施范围  
文档版本：0.1  
更新日期：2026-09-20  
前置基线：`PROJECT_PRD.md` v0.12、`PROJECT_FRONTEND_BACKEND_API_SPEC.md` v0.2  
目标：在现有业务助手上增加受控算法研发，不替换预测、知识、训练和模型治理服务

> **给执行首版任务的 Codex：读本文仅为理解边界。不要根据本文安装 Harness、创建 Workspace/Sandbox 表、注册 Coding API、挂 Docker socket 或制作算法研发菜单。只有明确启动后续升级任务后，才实施本文未来能力。**

## 0. 来源、假设与实施性质

原 PRD v0.11 的助手只允许结构化业务工具，并在训练章节明确将受限 Coding Agent 排除于首阶段、要求未来独立评审隔离/制品/路径/预算/人工接受。本方案沿用该约束，以本轮确定的 Python/PydanticAI、FastAPI、MySQL、Redis/Dramatiq、Artifact/Job/Confirmation 底座为前提。

下文 Workspace、Algorithm SDK、SandboxRun、算法包审核、代码训练执行器及未来 API 均为**新增架构设计**，不是现有代码已具备的能力。没有检查真实仓库和客户执行环境；具体科学算法、硬件、镜像、许可证和安全隔离方案必须通过升级阶段验证。

本文不依赖旧 8010 等服务，不恢复旧统一推理网关，不承诺兼容旧临时接口。未来对“本次新建 V1 产品”的升级与历史旧版本兼容是不同事项。

## 1. 目标产品与非目标

### 1.1 目标：受控色谱算法研发工作台

用户可以要求助手：检索论文/SOP → 调用现有预测工具 → 查看授权数据摘要 → 提出研发计划 → 在独立工作区编写新特征/算法实现 → 运行受控测试 → 封装算法包 → 经人类接受后创建训练实验 → 产出 Candidate → 进入现有模型治理。

保留三类能力同时存在：

```text
业务工具：色谱预测、数据摘要、模板训练、模型查询
知识工具：资料检索、来源引用、适用范围说明
研发工具：授权 Workspace 内读/改代码、测试、打包、创建代码型实验
```

不是只能二选一的“聊天助手”和“写代码助手”。可用单一 Agent 的受限研发模式实现，首轮不必引入多 Agent Supervisor。

### 1.2 不做的事情

不将平台变成任意网站/应用开发IDE；不让Agent修改平台认证、数据库迁移、FastAPI源码、生产模型目录、部署脚本或系统配置；不直接连接/控制生产仪器；不让模型自动批准/发布新模型。

不提供宿主Shell或自由Docker API；不动态下载任意依赖；不直接在可信Training Worker进程import Agent写的算法；不把“测试通过/人类点击接受”当作已证明代码安全。

### 1.3 成功定义

能够完成一个经领域负责人定义的数据契约/算法契约的真实研发流程，且所有代码、输入、环境、测试、训练和审批可追溯。成功运行演示代码不等于新色谱算法有科学有效性，也不等于具备对恶意多租户代码的强隔离能力。

## 2. V1 必须保留但不要过度实现的边界

| 首版已有/本次应实现 | 后续复用方式 | 首版不能提前做的事 |
|---|---|---|
| AgentRuntimeAdapter | 同一Agent扩展研发ToolSet或替换内部框架 | 并装Pi/PydanticAI Harness |
| Application Services | Coding Agent仍调同一预测/知识/训练服务 | 在研发模块重写预测算法服务 |
| Job/Attempt/Outbox/事件 | 承载sandbox test/train的异步生命周期 | 先交付空sandbox队列/worker |
| ArtifactStore | 保存代码快照、算法包、报告、模型产物 | 让普通上传接口接受可执行算法类型 |
| ConfirmationService | 绑定代码hash/环境/数据/预算/动作 | 在现有confirm里开任意shell批准字段 |
| ResourceRef注册表 | 新增workspace/snapshot/algorithm等资源 | 未构建类型提前加入首版公共枚举 |
| Experiment.execution.kind=template | 后续增加代码型实验契约 | 首版接受kind=code_artifact |
| ToolCall/LLM Call/Context | 跟踪代码修改、测试反馈、压缩历史 | 将内部思维链作为“代码计划”展示 |
| AuthProvider/项目权限 | 升级为真实研究员/审核人/执行服务身份 | 以默认主体宣称多用户安全执行 |
| Vue详情区注册机制 | 新增Workspace/Run/制品/变更审阅组件 | 首版打包代码编辑器与隐藏菜单 |

这些预留主要是稳定接口与职责分离，不是到处添加永远为null的coding字段，也不是先创建一套未来数据库表。

## 3. 推荐未来技术路线

### 3.1 继续以 Python 为核心

```text
Vue 3 + TypeScript + Pinia
          │
          ▼
FastAPI / AgentRunService
          │
          ▼
PydanticAI RuntimeAdapter
          ├─ 现有预测/知识/业务ToolSets → 现有Application Services
          └─ CodingToolSet
                    │
                    ▼
              自研CodingRuntime
              Workspace / Snapshot / Review
              Policy / AlgorithmPackage / Run
                    │
               Job + Outbox
                    │
               Redis/Dramatiq
                    │
          可信Sandbox控制Worker
                    │
          ┌─────────▼─────────┐
          │ 隔离执行环境       │
          │ 不可信算法/测试    │
          │ 无平台密钥/DB权限  │
          └─────────┬─────────┘
                    │ 受控输出回收
                    ▼
             Artifact / Experiment / Model Center
```

“Python Coding Runtime”是平台应用服务层，不是Python解释器本身，也不是必须购买/引入的某个SDK名称。

### 3.2 是否必须使用 Pi 或同类框架

不必须。后续最小方案仍可用 PydanticAI 的 Agent/工具循环，自己提供 `read_workspace_file`、`apply_patch`、`run_contract_tests`、`package_algorithm` 等工具。框架完成模型循环，平台完成领域契约、权限、审计和执行隔离。

Pi可作为设计参考，但引入TypeScript/Node运行时并非该Python平台的必要步骤。即使以后改用Pi，也应作为内部Agent adapter，不能接管业务数据、权限或生产发布。

### 3.3 PydanticAI Harness 的使用边界

升级时可以评估官方Harness的文件工具、上下文裁剪/压缩、输出限制与会话能力，锁定版本并写兼容测试。它是可选实现材料，不是本方案依赖前提。

尤其不能把其本地FileSystem/Shell直接指向FastAPI/Agent Worker宿主目录。采用第三方工具前应确认能否代理到平台的Workspace/SandboxBackend；不支持远程隔离时，只复用上下文管理等不越界部分。

同理，其他厂商SDK的sandbox能力不是自动符合内网、数据保密、Windows交付和项目权限要求，必须按实际Backend评估。

### 3.4 未来新增依赖（仅升级构建）

| 层 | 第一轮升级选择 | 可替换边界 |
|---|---|---|
| Agent | 继续PydanticAI | AgentRuntimeAdapter |
| Coding控制层 | 自研Python服务 | 不暴露框架内部类型 |
| 算法SDK | 自研版本化chromatography-algorithm-sdk | 科学任务特定契约 |
| 代码编辑UI | Monaco Editor或CodeMirror，经离线包评估选择其一 | Workspace组件 |
| Patch | 受控统一diff解析/应用；哈希并发检查 | WorkspaceFileService |
| 执行调度 | 继续Dramatiq/Redis，DB账本 | JobQueue |
| 沙箱 | 专用Linux节点上的加固Docker Backend起步 | SandboxBackend；安全场景不足则VM/microVM等 |
| 环境 | 固定Python/RDKit/sklearn或经审核PyTorch镜像 | EnvironmentCatalog |
| 代码检查 | 固定版本ruff/pytest及项目契约检查器 | ValidationProfile |
| 网络/依赖 | 默认禁网、固定镜像、离线wheelhouse（受控构建阶段） | DependencyPolicy |

不要一开始引入K8s、Temporal、多Agent、动态插件平台和全套云Sandbox，除非负载、恢复或隔离要求已证明需要。

## 4. 信任模型：控制面与执行面

### 4.1 四类主体

| 主体 | 可持有能力 | 不应持有 |
|---|---|---|
| FastAPI / Agent Worker | 访问业务服务、Provider密钥、经授权数据摘要 | Docker控制权、宿主任意执行接口 |
| Coding控制服务 / Sandbox Worker | 根据固定策略调度执行、取得受限输入/输出授权 | 把平台密钥暴露给不可信程序 |
| Sandbox内程序 | 读取已授予数据、改工作区、写指定输出、有限CPU/RAM/GPU | MySQL/Redis/生产模型目录/宿主socket/平台Token |
| 模型生成文本/代码/工具参数 | 提出请求，经过验证后由服务执行 | 决定自己的权限、改变策略或审批自己 |

检索文档、仓库文本、测试日志和模型生成的代码均作为不可信数据，不作为新的系统权限来源。

### 4.2 两层Worker必须区别

可信Sandbox控制Worker可以使用内部认证访问任务/制品服务，并控制专用执行节点；它的管理凭证不得出现在sandbox环境变量、命令行、挂载文件、日志或HTTP代理中。

真正运行Agent代码的是sandbox子环境。**不能把一个能直接读MySQL/Redis、拿着业务凭证的Training Worker改名为Sandbox Worker就声称完成隔离。**

### 4.3 测试命令白名单不是沙箱

即使只允许 `pytest`，测试文件仍能执行任意Python；即使只允许 `python -m trusted_runner`，其加载的算法也能执行任意Python。白名单减少操作面，但不能替代进程/文件/网络/资源隔离。

人类审批解决“是否授权这次操作”，sandbox解决“获准运行后最多影响哪里”。静态扫描、单测、审批和隔离需要叠加，没有一项单独证明绝对安全。

## 5. 新的领域对象与状态

### 5.1 对象关系

```text
Conversation → AgentRun
                   └─ Workspace
                         ├─ WorkingTree（可修改）
                         ├─ WorkspaceSnapshot（不可变）
                         └─ SandboxRun（只引用不可变snapshot）
                                  ├─ TestReport Artifact
                                  └─ AlgorithmPackage
                                            └─ CodeReview / Acceptance
                                                       └─ Experiment
                                                            └─ Candidate Model
                                                                  └─ 现有评测/审批/发布
```

工作区不等于运行环境；关闭一个sandbox不必删除工作区。会话也不拥有唯一工作区生命周期，删除/归档会话不能级联删除已被实验引用的代码制品。

### 5.2 Workspace

字段：`id,project_id,owner_id,name,task_type,base_template_id/version,working_revision,status,latest_snapshot_id,created_at,updated_at,archived_at`。

状态：`CREATING / READY / LOCKED / ARCHIVED / FAILED`。LOCKED表示受控合并/归档等暂不可写，不表示处于可执行内核快照。

工作树只在受控目录，前端仅收到工作区内相对路径；不返回服务器绝对路径。代码文件有内容hash/etag；并发patch必须基于已读取的hash，否则409/412。

### 5.3 WorkspaceSnapshot

字段：`id,workspace_id,workspace_revision,source_artifact_id,source_sha256,manifest_sha256,dependency_lock_hash,created_by,created_at`。

快照不可变；所有测试、训练、接受和确认绑定快照/hash，不能只绑定一个仍可被Agent改动的workspace_id。

### 5.4 SandboxRun

字段：`id,project_id,workspace_id,snapshot_id,job_id,execution_profile_id,environment_id,image_digest,input_grants,policy_version,resource_budget,confirmation_id,status,exit_code,output_artifact_ids,started_at,finished_at,error`。

状态使用通用JobStatus，不再另造难以映射的字符串。`RUNNING`只是程序正在运行，不表示环境安全策略已正确应用；Backend预检失败必须拒绝创建运行。

### 5.5 AlgorithmPackage

字段：`id,project_id,name,version,task_type,source_snapshot_id,source_sha256,manifest_version,sdk_version,environment_id,image_digest,data_contract_id,feature_contract_id,input_schema_version,output_schema_version,validation_report_ids,accepted_snapshot_hash,status`。

状态：`DRAFT / VALIDATING / VALIDATED / ACCEPTED / REJECTED / RETIRED`。

ACCEPTED仅说明允许用这一不可变包执行规定范围的研发训练，不等于生产模型APPROVED，不等于可以在宿主进程安全import，也不允许动态注入平台业务代码。

### 5.6 DatasetGrant

字段：`id,project_id,data_version_id,snapshot_hash,allowed_columns,allowed_rows_spec,access_mode:read_only,purpose,expires_at,granted_by,policy_version`。

授权数据由控制面准备、校验和挂载；sandbox不拿数据库连接串自行查询。不可因为“算法需要更多数据”就允许Agent使用任意SQL或全量客户原始数据。

## 6. Workspace、文件API与快照

### 6.1 工作区结构

```text
workspace-root/
  README.md
  algorithm_manifest.json
  src/
    algorithm.py
    features.py
  tests/                  # Agent可以补充的测试，不是最终可信验收测试
  configs/
    experiment_defaults.json
  notes/
    research_notes.md
```

数据输入、固定SDK与可信验收测试由执行时挂载，不混入Agent可写文件树。平台源代码、服务密钥、生产权重、部署配置不出现在可访问根目录。

### 6.2 允许的文件操作

初期提供list/read/search/apply_patch，不提供任意filesystem mount、symlink或shell。写入由服务端校验UTF-8、文件数、大小、扩展名、允许目录和当前hash；二进制生成交给受控Artifact通道。

路径只能是workspace相对路径；拒绝 `..`、绝对路径、Windows盘符/UNC、空字节和不允许的分隔形式。文件打开/写入应使用目录句柄/不跟随符号链接的安全操作，防止符号链接与校验后替换的TOCTOU风险，不能仅做字符串prefix检查。

apply_patch必须确认目标文件版本；并发冲突显示diff/冲突原因。提交快照时冻结working_revision，归档完整文件清单与hash；运行时挂载快照副本而非可继续编辑的live目录。

### 6.3 限额建议（升级预设，待负载验证）

工作区源码总量默认20MiB、单文本文件1MiB、文件数2,000；单次read输出64KiB；超限提供分页/范围读取或受控artifact，不把完整日志塞进LLM。限额随策略版本记录，不能由LLM参数临时提高。

## 7. SandboxBackend 与执行协议

### 7.1 自研端口，而非复制一套Agent框架

下面是未来平台接口草图，不是要求首版写实现，也不是某第三方库的实际API：

```python
from typing import Protocol

class SandboxBackend(Protocol):
    async def submit(self, spec: "SandboxRunSpec") -> "SandboxHandle": ...
    async def inspect(self, handle: "SandboxHandle") -> "SandboxStatus": ...
    async def cancel(self, handle: "SandboxHandle", reason: str) -> None: ...
    async def collect_outputs(self, handle: "SandboxHandle") -> "OutputManifest": ...
    async def destroy(self, handle: "SandboxHandle") -> None: ...
```

Job服务决定何时提交/重试/确认，Backend负责受限环境创建、状态、停止和输出收集。Checkpoint支持必须通过Backend capability声明，不能每个Backend都假称支持VM内存快照或GPU训练恢复。

### 7.2 SandboxRunSpec

```json
{
  "project_id":"project-default",
  "snapshot_id":"wsnap_example",
  "execution_profile_id":"algorithm-contract-test-v1",
  "environment_id":"chrom-cpu-reviewed-v1",
  "image_digest":"sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  "input_grant_ids":["grant_fixture_example"],
  "resource_budget":{
    "cpu_cores":2,
    "memory_mb":4096,
    "gpu_count":0,
    "max_runtime_seconds":300,
    "max_output_bytes":104857600,
    "pids_limit":128
  },
  "policy_version":"sandbox-policy-v1",
  "confirmation_id":"confirm_example"
}
```

execution_profile_id由服务端映射到固定argv/runner，不接受command字符串。镜像、挂载、网络模式、容器权限由策略目录确定，不是模型可以自由填的字段。上例是形状示例，HTTP层可以只接受profile/environment选择，实际digest与resource上限必须由服务端解析并校验。

### 7.3 第一轮Backend安全基线

专用Linux执行节点；每Run隔离环境；非root用户；只读rootfs；按需可写tmpfs/输出卷；capabilities全部drop；no-new-privileges；进程/内存/CPU/磁盘/超时限制；默认无网络；seccomp/LSM按部署能力启用；禁止privileged、host PID/network、宿主任意目录、Docker socket和平台密钥挂载。

输入数据与可信SDK/test以只读方式注入；工作副本与output在独立配额卷。代码不能与API共享制品存储的全目录；只能访问本次输入/输出视图。控制面输出收集不得跟随sandbox生成的恶意符号链接到宿主文件。

Docker共享宿主内核，不等同于对恶意多租户代码的绝对隔离。若处理不可信用户任意代码或客户提出更强威胁模型，必须在上线前升级隔离或使用专用VM/其他验证方案，不能仅以Compose服务分开即通过安全评审。

### 7.4 GPU

GPU训练会增加驱动、设备暴露、资源配额与清理风险。CPU研发测试可先交付；GPU作为独立经验证执行profile，明确允许GPU数量、显存/时间限制的实际可强制程度与并发策略。

不能只在JSON写 `gpu_memory_mb` 就声称已实现显存硬限制；无法强制的字段显示软预算或不支持，资源策略按实际Backend能力冻结。

## 8. Algorithm SDK 与科学任务契约

### 8.1 SDK的作用

把任务从“任意写Python”约束为“实现已审核的数据、训练、推理和产物协议”。SDK不负责证明代码安全，仍在sandbox中执行。

避免一个万能 `fit(dataset)` 接口吞掉不同色谱任务的单位与含义。定义共同生命周期，再以task-specific contracts明确回归/分类/排序、结构输入、条件、标签与结果。

### 8.2 推荐分层

```text
chromatography-algorithm-sdk/
  contracts/              # DatasetView、SplitManifest、PredictionBatch、单位
  tasks/                  # retention_regression / separation_classification / ...
  manifest/               # 算法包、环境、依赖、schema版本
  runner/                 # 可信训练/预测适配runner
  validation/             # 固定契约检查、有限值/形状/单位
  serialization/          # 允许的模型产物格式
```

任务ID不凭文档示例自动创建；由领域负责人根据真实模型/数据确定。SDK接口版本、数据契约与输出schema都需锁定到算法包。

### 8.3 生命周期草图

```text
validate_config(config) → 显式错误，不进行训练
fit(training_view, validation_view, config) → TrainingResult
predict(structured_batch) → TypedPredictionResult
export_model(output_handle) → ModelManifest
```

评测由受信任的外部评测器调用predict并计算指标，不直接相信算法自行报告的evaluate分数。预处理器只在training_view拟合，validation/test不得反向进入训练。

### 8.4 算法manifest示例

```json
{
  "manifest_version":"1.0",
  "algorithm_id":"gc_retention_research",
  "algorithm_version":"0.1.0",
  "task_type":"gc_retention_regression",
  "sdk_version":"1.0",
  "implementation_ref":"src/algorithm.py:Algorithm",
  "data_contract_id":"gc_dataset_contract_v1",
  "input_schema_version":"gc-input-1",
  "output_schema_version":"gc-output-1",
  "environment_id":"chrom-cpu-reviewed-v1",
  "dependency_lock_sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
  "source_snapshot_id":"wsnap_example",
  "model_format_id":"reviewed_model_format_v1"
}
```

implementation_ref是未来已封装包内部的受限引用，不是首版API可接受的import path；只能在sandbox中由可信runner验证/加载。上述task/format是示例，真实规范未提供前不能声称支持。

### 8.5 依赖管理

第一轮只选择预构建环境目录，不允许运行时pip/npm/conda装包。需要新依赖时产生环境变更申请，由可信构建流水线解析固定版本/哈希、许可证与安全检查，构建新镜像digest。

环境变化即新算法包版本；源代码hash不变但环境不同也要重新测试/接受/确认。不通过“允许pip命令”把供应链审核绕过。

## 9. 测试、验证与防止自证正确

### 9.1 三类测试

| 层 | 谁拥有 | 能证明什么 |
|---|---|---|
| Agent自己写的单元测试 | Workspace，可修改 | 辅助开发，不构成准入依据 |
| SDK/平台契约测试 | 只读可信测试挂载，Agent不能修改 | 输入输出、形状、单位、错误处理、产物结构 |
| 独立科学评测 | 平台管理，固定数据/协议/阈值 | 在给定数据和协议上的泛化/业务表现 |

最终评测数据不直接作为开发可读文件提供给Agent。隐藏标签、约束重试次数和验证轮次，避免反复调参“学会”测试集；必要时独立验证服务执行新包的predict。

### 9.2 色谱专项检查

需由任务协议选择：SMILES与立体信息保持、分子/实验/条件级分组划分、跨split重复结构检查、标签/单位一致、训练预处理泄漏检查、适用条件范围、无效/缺失输入、NaN/Inf、模型结果与既有字段映射。

Scaffold split不是所有任务自动正确的答案；具体分组粒度和评测协议由科学负责人冻结。不能把单个MAE下降或AUROC上升推广为所有客户方法有效。

### 9.3 报告和证据

每份TestReport绑定源代码快照、环境digest、测试profile版本、输入fixture/data版本、执行Run、退出码、报告hash。Agent可以解释报告，但不能修改已保存报告或重新命名失败为通过。

静态检查或扫描结果不证明任意程序安全；证据包同时包含隔离策略、运行预算与实际执行记录。

## 10. 从算法包到训练实验

### 10.1 关键安全修正

```text
可信首版模板 → 现有TrainingExecutor → 固定可信Worker流程
Agent生成代码包 → SandboxTrainingExecutor → 隔离训练环境
```

两条路线共享Experiment/Job/Artifact/模型生命周期，不共享“不可信代码可在宿主import”的权限。

**不得因代码包状态是ACCEPTED就把它拷贝进首版Training Worker进程执行。** 接受只授权规定的隔离运行。

### 10.2 未来ExperimentSpec

升级增加代码型执行分支：

```json
{
  "execution":{
    "kind":"code_artifact",
    "algorithm_package_id":"alg_example",
    "algorithm_package_version":"0.1.0",
    "source_snapshot_sha256":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    "environment_id":"chrom-cpu-reviewed-v1"
  },
  "data_version_id":"dv_example",
  "evaluation_protocol":{"id":"ep_example","version":"1"},
  "resource_budget":{"cpu_cores":4,"memory_mb":8192,"gpu_count":0,"max_runtime_seconds":3600}
}
```

与首版模板分支互斥。创建后服务器解析确切image digest、输入grant、SDK与包hash，形成不可变spec；模型不能只传 `workspace_id` 请求训练最新可变代码。

### 10.3 Future API版本策略

本升级设计建议新增 `/api/v2/coding/...` 和 `/api/v2/training/experiments`，以明确区分首版严格template schema。实际实施时一起升级对应前端类型和build版本；不偷偷放宽首版请求校验。

V1模板接口继续执行其原有契约；新代码型实验不以V1无法识别的execution类型混入其列表。共享数据库应用服务可按API版本投影，或升级客户端统一用V2。模型中心对代码来源展示、Artifact/ResourceRef新类型也必须同步生成新版OpenAPI与前端类型。

这属于对本次新产品的有计划版本升级，不是为历史8010等旧服务保留兼容层。

### 10.4 完整提交链

编写代码 → 冻结snapshot → 测试 → 封装algorithm package → 人类review/accept确切hash → 创建代码型experiment → 验证数据/环境/预算 → Confirmation → Job/SandboxTrainingExecutor → 受控收集模型产物 → ModelRegistration验证 → Candidate。

失败、取消、超时仍不创建Candidate；不能用部分生成的权重冒充完整模型。正式独立评测和生产审批继续在现有治理服务执行。

## 11. 生产推理不能留下执行后门

新算法训练成功后可能需要自定义推理逻辑；这不能靠“把algorithm.py放到生产目录”解决。

第一轮建议约束生成的模型符合已审核生产Loader与输入输出契约。对于需要全新推理实现的算法，有两条受控路径：

1. 可信开发者审核并将推理适配器纳入新的构建、依赖锁和发布流程，重新交付；不是运行时热插用户代码。
2. 后续实现受控隔离推理服务，让新算法仍在隔离环境运行，并冻结性能、权限、数据输入与发布健康检查。

未具备上述任一路径时，算法包只能用于研发实验，Candidate不得发布生产。Code ACCEPTED、模型CANDIDATE与生产APPROVED是三套不同意义的状态。

## 12. 代码接受、运行确认与模型审批

### 12.1 四个独立检查点

| 检查点 | 绑定对象 | 作用 |
|---|---|---|
| 研发计划/工作区授权 | 项目、目标、允许数据、基本预算 | 限定任务范围 |
| Code Acceptance | 源代码snapshot/hash、manifest、测试、环境digest | 允许该包进入规定研发执行 |
| Execution Confirmation | 确切包/数据/环境/预算/动作/策略 | 授权一次训练或高成本运行 |
| Model Approval / Publish | Candidate、独立评测、scope、当前生产指针 | 生产准入与切换 |

不能把一个“我同意”复用为后续无限次运行；审批过代码也不自动批准使用其他客户数据。

### 12.2 确认指纹

指纹至少包含：project/actor、algorithm package/version、source snapshot hash、manifest/SDK/依赖hash、image digest、数据grant/version/hash、资源预算、执行profile、输出白名单、网络策略、policy_version。

任何一项变化都重新验证/确认。Confirmation校验、Job创建、授权消费与Outbox同一事务；运行时使用已冻结快照，防止审批后更改代码。

### 12.3 身份升级门槛

只在受控单人研究环境验证时可暂用单主体策略，但要标注无法证明独立审核。真实多人或客户不可信代码上线前，必须接入身份认证、项目授权、角色/职责分离、执行服务认证和资源配额；不能继续把deployment_default当作完整权限体系。

## 13. 后续Coding工具与API草案

### 13.1 给模型的工具

第一轮工具：`create_workspace`、`list_workspace_files`、`read_workspace_file`、`search_workspace`、`apply_workspace_patch`、`create_workspace_snapshot`、`run_contract_tests`、`get_sandbox_run`、`package_algorithm`、`create_code_experiment_draft`。

保留现有 `search_knowledge`、`get_model_card`、`create_prediction`、`get_dataset_summary` 等业务工具。模型不获得 `accept_algorithm`、`confirm_execution`、`approve_model` 或 `publish_model` 工具。

默认不给通用shell。以后开放受限shell仍必须在同一SandboxBackend内，不能回退为本地subprocess。命令白名单只能作为额外策略，不替代隔离。

### 13.2 未来HTTP端点（首版不得注册）

以下路径以 `/api/v2` 为前缀：

| 方法/路径 | 用途 | 必要控制 |
|---|---|---|
| POST `/coding/workspaces` | 从已审核模板创建workspace | 项目权限、数量/空间额度、I |
| GET `/coding/workspaces/{id}` | 元数据/working_revision | 权限过滤 |
| GET `/coding/workspaces/{id}/files` | 文件树 | 受控相对路径，不泄露主机路径 |
| GET `/coding/workspaces/{id}/file` | path+offset+limit读取 | 文件hash/大小，访问白名单 |
| POST `/coding/workspaces/{id}/patches` | 应用diff | I、If-Match、base file hash |
| POST `/coding/workspaces/{id}/snapshots` | 封存不可变源码 | I、If-Match，原子文件清单/hash |
| GET `/coding/snapshots/{id}` | 快照元数据 | 只读 |
| POST `/coding/runs` | 对快照运行固定profile | I、确认/预算、无任意command |
| GET `/coding/runs/{id}` | 状态与输出 | 同Job规则 |
| POST `/coding/runs/{id}/cancel` | 请求停止sandbox进程 | I、If-Match、实际停止后终态 |
| GET `/coding/runs/{id}/events/stream` | 日志/进度事件 | 复用持久seq与重放；输出限流/脱敏 |
| POST `/coding/algorithm-packages` | 从快照/报告打包 | I，可信manifest校验 |
| GET `/coding/algorithm-packages/{id}` | 包/测试/状态 | 只读 |
| POST `/coding/algorithm-packages/{id}/accept` | 人类接受确切版本 | 独立权限、I、If-Match，审计 |
| POST `/coding/algorithm-packages/{id}/reject` | 驳回 | 理由、版本、审计 |
| POST `/training/experiments` | V2创建template或code_artifact实验 | 互斥schema，固定数据/环境 |

本表是升级设计草案，启动升级开发前仍需生成字段级OpenAPI和测试；不能从表格直接创建返回success的占位接口。

### 13.3 示例Patch请求

```json
{
  "base_revision":3,
  "changes":[
    {
      "path":"src/algorithm.py",
      "base_sha256":"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
      "patch_artifact_id":"art_patch_example"
    }
  ]
}
```

patch_artifact只能是已验证文本diff，并校验每个涉及路径；即使外层path合法，diff内部包含别的绝对路径也应拒绝。未知文件默认需明确create操作与允许目录；禁止隐式覆盖可信测试/配置。

### 13.4 新错误码

`WORKSPACE_REVISION_CONFLICT`、`PATH_NOT_ALLOWED`、`SNAPSHOT_REQUIRED`、`ALGORITHM_NOT_ACCEPTED`、`ENVIRONMENT_NOT_APPROVED`、`SANDBOX_POLICY_UNSUPPORTED`、`SANDBOX_UNAVAILABLE`、`DATA_GRANT_DENIED`、`OUTPUT_LIMIT_EXCEEDED`、`EXECUTION_FINGERPRINT_CHANGED`。

Backend缺少所需安全限制必须fail closed，不能自动降级为宿主执行；数据/环境不足也不能临时开放网络补齐。

## 14. 前端升级：研发工作台，而不是生产IDE

保留现有导航和业务页面；在助手模块实际构建coding子能力时注册“算法研发”模式。

推荐布局：左侧会话/任务；中间对话与结构化计划/工具卡；右侧按需展开Workspace、变更审阅、Run、制品；Context Inspector仍单独受权。

### 14.1 各面板必须承载的事实

| 面板 | 内容 |
|---|---|
| Workspace | 允许目录树、文件hash、working_revision、只读/可写状态 |
| Changes | base snapshot与当前diff，人工接受/拒绝，修改后接受失效 |
| Run | sandbox/job ID、实际环境digest、预算、状态、退出码、日志与取消 |
| Artifacts | source snapshot、algorithm package、测试报告、训练候选及hash关联 |
| Data access | 本次授权数据版本/字段范围、用途、到期，不是服务器路径 |
| Confirmation | 代码/环境/数据/预算指纹与影响范围 |
| Context | 每次LLM实际可见应用输入，裁剪/引用/工具schema，不是隐藏推理 |

关闭浏览器/会话不停止已提交训练；停止Agent不等于销毁workspace；取消sandbox与归档工作区必须是两个明确操作。

### 14.2 状态真实性

没有Backend或没建立隔离时显示“执行环境不可用”，不能显示绿色Sandbox Ready；只保存代码尚未执行时显示未运行；单元测试通过不能显示“模型已发布”。

候选模型跳转现有Model Center，正式发布不在聊天中偷偷发生。前端并发编辑、确认失效、文件冲突、输出超限和资源等待都要有可操作状态。

## 15. Context Engineering 与长任务

Coding任务的context包含按需读取代码、测试输出、知识引用、数据摘要、当前计划和上次快照信息；不能把整个仓库、原始数据集或上千行日志一次性塞进模型。

以V1 ContextCapturePort继续保存每次最终输入：文件来源明确到 snapshot_id/path/hash/range；测试输出来源明确到 SandboxRun/artifact；提示词与工具schema按版本记录。

读文件和日志分页；大结果卸载到Artifact并返回摘要/引用；历史裁剪必须保持工具调用/结果配对与重要约束。压缩摘要标记为生成摘要并保留来源，不把摘要冒充原始实验数据。

研究计划可在工作区notes保存，但这是显式用户可见工作产物，不是抓取/展示模型隐藏思维链。

Agent只提交job_id并查询关键阶段；长训练不占着LLM请求或HTTP连接。后台自动续跑要有总预算、最大迭代次数和终止条件，第一轮升级可先不做自动循环优化。

## 16. 输出制品与可复现性

执行完成后由可信收集器读取允许输出清单，校验文件类型、大小、实际路径/符号链接、manifest与hash，进入通用Artifact服务。失败输出可以作为诊断制品保存，但不能进入Candidate。

最小证据包包含：源码snapshot、manifest、SDK/依赖版本、镜像digest、数据版本和split manifest、配置/随机种子、执行策略、资源预算与实际耗时、测试/评测报告、模型产物hash、确认与接受记录。

固定seed与环境有助于复现，但GPU/并行计算可能非确定性；报告明确确定性设置与容差，不承诺所有训练bitwise一致。

有保留引用的snapshot/package不可被清理。临时sandbox和缓存设置TTL；销毁执行环境不是删除审计/产物，也不是证明硬件级数据完全擦除。

## 17. Windows内网与离线部署

### 17.1 推荐部署方式

```text
Windows浏览器/应用服务器
          │ 内网、受认证连接
          ▼
Linux执行节点（专用）
  Sandbox控制服务
       └─ 每Run隔离容器/VM
```

或在客户批准的Linux VM/容器执行环境上集中部署。是否允许WSL2、虚拟化、Docker、GPU直通、网络隔离与镜像导入必须在升级前检查。

不能把Conda虚拟环境当作安全沙箱；它只隔离部分Python依赖。Windows受限环境若无法满足后端隔离需求，保留业务助手/模板训练，不开放代码执行模式。

### 17.2 控制面认证与网络

FastAPI到执行服务采用内部服务认证（例如mTLS或短期服务凭证）；执行节点只接受固定RunSpec，不暴露Docker Remote API。API不持有daemon socket；控制Worker的高权限仅局限专用执行主机。

Sandbox默认断网；必要的输入输出由控制服务搬运，避免给不可信代码发通用ArtifactStore凭证。后续网络例外必须限定目的地、数据策略与审计，不能一个allow_network=true放开整个内网。

### 17.3 离线环境目录

环境镜像、工具链、wheelhouse、SDK、模型/算法依赖与许可证在可信构建侧锁定后带入，使用签名/哈希校验与离线升级预检。运行中缺依赖返回明确环境不可用，不执行临时公网安装。

## 18. 升级实施阶段与准入门槛

| 阶段 | 交付 | 开启下一阶段的证据 |
|---|---|---|
| U0 威胁模型/范围 | 数据分级、用户类型、网络/Windows/GPU约束、权限方案 | 真实部署预检、明确隔离强度，不把Docker等同绝对安全 |
| U1 SDK/算法包 | 真人按SDK实现一个领域基线、manifest、固定测试 | 不需要LLM即可完成打包/契约验证；真实数据规范冻结 |
| U2 Workspace | 目录/patch/hash/snapshot/Artifact关联 | 路径穿越、符号链接、并发/冻结测试通过 |
| U3 SandboxBackend | 专用执行节点、资源/网络/文件限制、取消/回收 | 不可信测试程序不能接触宿主/平台秘密，配额与故障测试通过 |
| U4 Coding ToolSet | 将已验证服务暴露给PydanticAI，增加UI | 工具拒绝/确认/重放/Context可观测，无宿主回退 |
| U5 代码型训练 | SandboxTrainingExecutor、实验/候选链 | 已接受不可变包训练，无可信Worker import代码后门 |
| U6 发布路径与试点 | 固定Loader或隔离推理、独立评测、审批/回滚 | 发布真实性/性能/安全验证，完成项目试点 |

先手工跑通SDK和Sandbox，再接LLM；不要先给模型bash，再补权限。多Agent、自由Shell、自动搜索依赖和自动模型迭代不是这条最小升级链的前置条件。

## 19. 安全、可靠性与领域验收

| ID | 场景 | 必须结果 |
|---|---|---|
| U-01 | V1构建检查 | 不包含Coding代码/路由/表/Worker/专属依赖 |
| U-02 | 未构建Coding的运行配置变更 | 无法通过设置/.env恢复研发能力 |
| U-03 | 访问宿主相对/绝对/UNC路径 | 拒绝，无越界读取 |
| U-04 | symlink与TOCTOU路径替换 | 安全打开/写入，不能越出工作区/输出范围 |
| U-05 | 读取平台Key/MySQL/Redis/daemon | Sandbox无这些凭证/网络/挂载 |
| U-06 | 超CPU/RAM/PID/磁盘/输出/时间预算 | 实际限制与明确终态；非仅UI提示 |
| U-07 | 未批准公网/内网访问 | 网络层阻断，不能由Python绕过工具白名单 |
| U-08 | 申请privileged/host mount/任意镜像 | 控制服务拒绝，不自动降级 |
| U-09 | 代码接受后修改文件/环境/数据 | 快照和确认不再匹配，必须重新接受/确认 |
| U-10 | 相同执行请求重投/Worker宕机 | 不重复预算消费/发布/候选登记 |
| U-11 | 修改Agent自己写的测试 | 不能改变平台可信契约测试或正式评测协议 |
| U-12 | 将测试标签混进训练/反复调测试 | 按隔离协议阻断/限制并审计 |
| U-13 | 训练失败、取消、超时但生成了部分权重 | 不进入Candidate |
| U-14 | 把ACCEPTED算法交给可信Training Worker直接import | 架构测试拒绝；只走SandboxTrainingExecutor |
| U-15 | 新模型需任意生产代码但未获审核 | 仅研发可用，不允许生产部署 |
| U-16 | Agent尝试确认/接受自己或发布模型 | 后端授权拒绝 |
| U-17 | 输出符号链接/恶意包/超限日志 | 收集器校验拒绝，artifact隔离 |
| U-18 | 关闭聊天/刷新/停止回答 | 不丢已提交Job，不误取消训练 |
| U-19 | 训练过程/模型输出声称科学成功 | 必须有真实评测报告，不能只引用模型文本 |
| U-20 | Context/日志/Artifact包含秘密或内部思维链 | 脱敏/白名单过滤、访问审计 |
| U-21 | Windows环境缺乏隔离/GPU能力 | 清晰不可用，不用宿主执行替代 |
| U-22 | 数据/源码/依赖/环境/指标版本回溯 | 可以定位到不可变证据包；无法复现须报告原因 |

上述测试需要真实Backend验证；只mock出一个`SANDBOX_READY`返回值不能通过安全验收。

## 20. 升级开发前仍需冻结的事项

隔离目标是受控内部研究还是恶意多租户执行；具体Linux/VM/GPU条件；Algorithm SDK支持的首个科学任务；数据访问字段与评测隔离；允许环境/依赖/模型格式；代码接受权限与真实账号；生产新推理实现的发布路径；单项目资源预算和保留期。

这些项不能由Codex根据通用经验冒充已有客户承诺。可以先在明确标注的内部受控研发环境验证，但正式交付前必须登记决定、资产和测试证据。

## 21. 两条分别使用的Codex指令

### 首版任务中只允许这样使用本文

```text
本任务只实现PROJECT_PRD.md的V1。
阅读CODING_AGENT_UPGRADE_PLAN.md以理解未来边界，
只保证当前Agent/业务服务/Job/Artifact/Confirmation/Trace端口可扩展。
不实现Workspace、Algorithm SDK、SandboxBackend、代码执行和研发UI。
```

### 明确启动后续升级时才使用

```text
本任务启动Coding Agent升级。
先完成U0/U1，冻结威胁模型、真实环境和科学算法SDK；
按U2–U6逐步实现，复用V1业务服务与任务/制品/确认/Trace。

新增CodingRuntime与SandboxBackend；Agent生成代码永远不在
FastAPI、Agent Worker或可信Training Worker进程中直接执行。
生成V2 OpenAPI/前端类型与升级迁移，执行U-01至U-22测试。
未通过隔离和授权验证前，不开放代码执行入口。
```

## 附录：技术依据与阅读材料

本方案的大部分内容是针对该平台的架构设计，而不是第三方框架自动提供的保证。官方资料仅用于核查相关工具定位与限制：

```text
PydanticAI：Agent、工具与类型化依赖
https://pydantic.dev/docs/ai/core-concepts/dependencies/
PydanticAI Harness：可选工作台组件，不替代平台授权/隔离
https://pydantic.dev/docs/ai/harness/
PydanticAI Deferred Tools：人工确认需要服务端授权与真实记录
https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/
Docker Engine security：daemon权限、容器配置与内核隔离边界
https://docs.docker.com/engine/security/
Dramatiq：任务可能重复投递，需业务幂等
https://dramatiq.io/best_practices.html
Dramatiq：执行超时为best-effort，硬终止需额外监督
https://dramatiq.io/guide.html
```

核查日期：2026-09-20。正式开发需锁定实际版本并验证，不按网页上的浮动最新版直接上线。
