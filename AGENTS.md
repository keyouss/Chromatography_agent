# AGENTS.md

## 开发主线

- 后续主线是“通用色谱智能平台”。需求以 `docs/PROJECT_PRD.md` 为准，接口以 `docs/PROJECT_FRONTEND_BACKEND_API_SPEC.md` 为准，交互参考 `docs/preview_v2.html`；冲突时依次服从用户当前指令、PRD、接口、原型、现有代码。可自行启动docker
- 目标架构：Vue 3 + Vite + Pinia，FastAPI + Uvicorn，MySQL，Redis + Worker，Nginx。业务 API 使用 `/api/v1`，默认端口 `9012`，必须从 `.env` 读取。
- 新平台不依赖、代理或兼容 `Services/unified_server.py`、8010 及旧 HTTP 接口。可复制经检查的算法到新适配器，由 Worker 直接调用。旧 Gradio、Agent、RAG、MCP 保留，不擅自删除。
- 旧接口文档仅描述旧服务。产品变更应同步检查 PRD、目标接口、OpenAPI、原型和 README。

## 产品边界

- 色谱预测按“一级模块 → TLC/CC/HPLC/GC/SFC 二级导航 → 单次、批量及特有工具三级导航”组织；齐鲁手性 LC 使用独立 `chiral_lc` 适配器。
- 化学结构输入是所有构建必含核心，包括 SMILES 规范化、立体信息保留、2D 渲染、Ketcher、ChemDraw/SDF 和复制 SMILES。
- 数据与知识中心包括预测记录、色谱数据和知识中心。一个分子可关联多类色谱数据；数据导入必须指定目标数据域，已被训练或评测引用的数据版本不得覆盖。
- 模型训练只负责“不可变数据版本 → Candidate”，采用受控模板及草稿、校验、确认、异步执行流程，不运行任意脚本、不直接发布。
- 模型中心负责登记至回滚；生命周期、部署状态、Champion/Challenger 分开记录。
- 助手复用页面相同业务 API，可创建业务草稿，但不得绕过校验、确认、权限或审批，不自动发布或回滚模型。
- 系统设置固定在左下角，管理非敏感运行信息和 LLM URL、`model_id`、密钥引用；不得在页面回显密钥。租户隔离仅留接口，首版不宣称已启用。

## 目录与模块

新平台开始编码时优先采用以下边界；已有等价结构时直接复用，不为目录完整而创建空模块：

```text
frontend/                 Vue 前端
backend/src/chrom_platform/core/       配置、上下文、错误、公共协议
backend/src/chrom_platform/modules/    独立业务模块
backend/src/chrom_platform/adapters/   各类色谱科学适配器
backend/src/chrom_platform/infrastructure/ 数据库、Redis、制品存储
backend/tests/            后端与契约测试
builds/                   构建清单与裁剪装配
contracts/                OpenAPI、生成类型依据、契约 fixtures
deploy/                   Nginx、Compose、离线部署
docs/					  相关记录文档
```

前端模块独立拥有页面、路由、状态和 API 客户端；后端模块独立拥有路由、业务服务、存储适配和任务处理。模块间只通过公共契约协作，不直接引用其他模块内部实现。

## 构建与接口

- 功能只做构建裁剪。未选模块的代码、路由、资源、依赖、Worker、模型资产和服务不得进入交付包；精简源码须可独立安装、测试和运行，不能依赖主仓库中被裁掉的模块。
- 前后端模块通过稳定契约和注册入口协作，公共底座不得反向依赖可选模块。前端只通过统一客户端调用 `/api/v1`；各色谱类型使用独立 schema，不建立万能请求或万能 Excel。
- 长任务使用稳定任务 ID，支持查询、取消、重试和恢复；创建与提交支持幂等键，确认绑定草稿 revision。
- 文件先经制品服务完成类型、大小、哈希、解析和隔离校验，再以 `artifact_id` 引用；禁止客户端提交服务器绝对路径。API 字段和前端类型以 OpenAPI 为准。
- 能力发现接口只返回当前构建真实包含的功能及健康状态，不能用于恢复已裁掉代码。页面、助手和 MCP 共用业务服务，不能分别实现同一流程。

## 认证与安全

- 首版不实现登录、Token、完整 RBAC 或租户页面，但统一使用 `AuthProvider → ActorContext`。以后浏览器优先用 HttpOnly Cookie，API/MCP 可用 Bearer；业务 JSON 不提交或信任用户、租户和凭证字段。
- 项目范围由服务端校验；全链路传递访问上下文，不保存原始 Token 或密码。
- 正式结果必须来自真实模型并保留模型、输入、版本、warning 和审计；失败时不得用 mock 伪装成功。
- 不泄露密钥、连接串、堆栈、权重路径或客户数据；不执行用户、模型包或助手提供的任意代码和动态安装命令。
- 发布、回滚、删除、导出、配置变更和助手业务操作必须审计。模型包登记前须完成清单、SHA-256、manifest/schema、环境、加载、黄金样例和异常值检查。

## 模型约束

- HPLC 基线接收两个对映体 SMILES；`chiral_lc` 接收一条含立体信息的 SMILES，二者不得共用表单或 schema。
- GC 字段必须来自能力契约，不承诺未支持的参数。色谱模拟图只有模型或明确后处理支持时才作为正式结果。
- CC 派生时间固定为 `time_seconds = volume_ml × 60 / flow_rate_ml_min`，单位换算不能改变预测体积。回归样例不得宣称为独立泛化评测。

## 修改与验证

- 修改前阅读相关源码与文档；使用 Windows PowerShell、UTF-8、绝对路径，并用 `Test-Path` 验证路径。
- 不修改全局环境变量。新增或升级依赖前说明影响并取得用户同意；优先使用项目工具链。
- 真实模型服务与推理测试优先使用 `D:\Anaconda\envs\SFC\python.exe`；仅处理旧 Agent/RAG/MCP 时使用 `D:\Anaconda\envs\LR\python.exe`。
- 用户已允许先用现有 Python 3.11 开发；不因 PRD 的 Python 3.12 基线阻塞实施。不得未经测试宣称已通过 3.12 验收，新增/升级依赖仍需明确授权。
- 新功能须有最小可运行检查，重点覆盖契约、状态机、权限、单位、数据版本和构建裁剪。服务改动同步检查健康接口、Worker、适配器和启动脚本；运行中的服务不会自动加载代码，交付时提示重启。
- 前端至少检查导航、空/加载/成功/失败状态、窄屏布局和控制台错误。真实 `.env`、API Key、Token、密码与客户数据不得入库。
- 不删除或覆盖旧 Agent、权重、数据、训练记录、制品和备份。`deliverables/` 已发布版本只读；修改时使用新版本，运行验收并更新清单与 ZIP 哈希。离线工具通过各自 `predict.py` 独立运行，不得改为依赖 `8010`。

## 代码审查

- 每次执行大的代码更新时，可以调用`D:\Anaconda\envs\LR\Scripts\uvx.exe code-review-graph serve`命令快速搞懂指定仓库逻辑
- 日常在 `backend/`、`frontend/` 分别建立/更新独立图，覆盖各自 `src` 与 `tests`；勿以仓库根目录建全仓图，跨端分析才用轻量组合图。轻量组合图仅用于跨端分析：先将 `backend/src`、`backend/tests`、`frontend/src`、`frontend/tests` 复制到 `.code-review-graph\light-input` 的对应相对路径；只复制这四棵目录，排除 `.git`、`node_modules`、`dist`、`.generated`、`deliverables`、`__pycache__` 和临时目录。首次执行 `D:\Anaconda\envs\LR\Scripts\uvx.exe code-review-graph build --repo <仓库>\.code-review-graph\light-input --data-dir <仓库>\.code-review-graph\light-db --skip-flows`，后续先刷新上述副本，再将 `build` 改为 `update`；不得在仓库根目录建图。
