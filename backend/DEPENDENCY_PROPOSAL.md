# V1 后端依赖提案

> **2026-09-21 SFC 授权实施修订**：用户已明确授权仅修改 `D:\Anaconda\envs\SFC` 中除 Torch 外的库。本轮实际安装已完成并已收敛：`asyncmy==0.2.14`、`dramatiq[redis]==2.2.1`、`pydantic-ai-slim[openai]==2.31.1`、`pydantic-graph==2.31.1`、`openai==2.54.0`，以及解析所需 `genai-prices==0.1.7`、`logfire-api==5.1.0`、`jiter==0.17.0`。`tiktoken` 保持 SFC 基线 `0.13.0`；没有按旧隔离解析结果强行升级到 `0.14.0`。第一次安装的 `pydantic-ai-slim==2.46.0` 要求 `openai>=3.8.0`，与既有 `langchain-openai`/`mineru` 的 `openai<3` 冲突，随后已在不触碰 Torch 的前提下收敛到上述 2.31.1/2.54.0 组合。详见下方“本轮 SFC 实施记录”和 `backend/evidence/dependency-install-20260921/`；旧提案段落保留为授权前历史记录，不能覆盖本修订事实。

核查日期：2026-09-21  
核查解释器：`D:\Anaconda\envs\SFC\python.exe`，Python 3.11.15（64-bit）  
依据：`AGENTS.md`、`docs/PROJECT_PRD.md` v0.12、`docs/PROJECT_FRONTEND_BACKEND_API_SPEC.md` v0.2、`docs/delivery_readiness.md`、三个 SFC deployment 的后端 requirements，以及 SFC 环境的 `pip list --format=freeze` 和逐包 import 检查。

## 结论与授权边界

本文件是依赖环境变更申请材料，不是安装记录。没有得到用户明确授权前，不运行 pip/conda，不修改全局环境，不修改 SFC 环境，也不把下面的候选版本写成已安装或已验证。

SFC 环境只用于本次只读开发验证；不得向现有 SFC/LR 环境安装或升级依赖。获得授权后，建议使用现有 Python 3.11 创建项目隔离环境 `D:\Jupyter\Chromatography_agent\backend\.venv`，最终交付还必须由受控构建生成独立依赖锁、wheel/镜像清单和哈希。Python 3.11 是当前允许的开发基线；Python 3.12 兼容性未测试，不能从 3.11 结果推断。

官方 PyPI 元数据和临时 wheel metadata 已核对候选固定版本：`asyncmy==0.2.14`、`dramatiq[redis]==2.2.1`、`pydantic-ai-slim[openai]==2.46.0`。这些版本仍只是提案；完整的 50 包解析清单、输入约束、命令、来源和许可证记录见 [DEPENDENCY_RESOLUTION_PROPOSAL.md](DEPENDENCY_RESOLUTION_PROPOSAL.md)。安装前仍需用户明确授权、隔离环境安装、wheel 哈希/许可证复核、Python 3.11 兼容性和契约测试；不能把解析结果写成安装或真实集成验收。

## 当前 SFC Python 3.11 已有版本

以下版本来自 `importlib.metadata.version()`；同时对运行时 import 做了只读检查。`import=OK` 不是 MySQL、队列或模型服务验收。

| 包/运行时 | 当前版本 | 当前事实 | V1 用途 | 许可证/风险记录 |
|---|---:|---|---|---|
| Python | 3.11.15 | 可用 | 后端开发与模型适配器运行时 | 目标 PRD 写 3.12；3.12 尚未测试 |
| fastapi | 0.136.3 | import OK | `/api/v1` HTTP API | 发行元数据未给出明确 License 字段；授权构建需保留上游许可证 |
| uvicorn | 0.49.0 | import OK | ASGI 启动 | 已在项目 `backend/pyproject.toml` 中声明；交付构建仍需保留上游许可证 |
| pydantic | 2.13.4 | import OK | DTO、契约和配置校验 | 发行元数据未给出明确 License 字段；需随依赖清单核对上游文本 |
| SQLAlchemy | 2.0.50 | import OK | ORM、事务和 MySQL 访问抽象 | MIT；需保留许可证 |
| alembic | 1.18.5 | import OK | 按构建装配的迁移 | 发行元数据未给出明确 License 字段；需保留许可证 |
| redis | 8.0.1 | import OK | Redis 通知/队列客户端 | OSI MIT；Redis 服务本身另做部署与许可证审查 |
| PyMySQL | 1.2.0 | import OK | 现有同步 MySQL 驱动候选 | 仅有同步驱动；不能替代目标 `mysql+asyncmy`。发行元数据未给出明确 License 字段 |
| rdkit | 2026.3.3 | import OK | SMILES 标准化、结构验证和描述符 | BSD-3-Clause；模型/数据库及其权利不由此包许可证覆盖 |
| torch | 2.6.0+cu124 | import metadata available | 仅选定 PyTorch 模型 adapter 可能需要 | 现有 CC 要求 2.1.0，HPLC/GC 要求 `<2.4`；当前 CUDA 12.4 版本不构成兼容或科学验收，且不能把 GPU 运行时全量带入所有 profile |
| torch-geometric | 2.8.0 | import metadata available | 图神经网络 adapter 可能需要 | 现有 CC 要求 2.5.2，HPLC/GC 要求 `<2.6`；当前版本超出参考约束，需按 adapter 单独隔离并实测 |
| lightgbm | 4.7.0 | import metadata available | TLC 参考模型可能需要 | BSD-3-Clause；只随 TLC profile 纳入，不能因全局已装而默认进入 V1 |
| xgboost | 2.1.4 | import metadata available | TLC 参考模型可能需要 | Apache-2.0；参考要求 `<2.2`，仍需模型序列化和特征顺序验证 |
| openpyxl | 3.1.5 | import metadata available | 选定批量/GC 文件处理可能需要 | MIT；只随实际选定文件能力纳入 |
| numpy | 1.26.4 | import OK | 科学计算基础 | BSD-3-Clause 及其随附第三方材料，交付需保留 notices |
| pandas | 2.3.3 | import OK | 数据表校验/批量处理 | BSD-3-Clause；交付需保留 notices |
| scikit-learn | 1.6.1 | import OK | 已有传统模型适配可能用到 | BSD-3-Clause；二进制包包含额外运行库，需核对 notices |
| joblib | 1.5.3 | import OK | 仅可信、已审核模型资产可能用到 | 上游许可证需随 wheel 核对；禁止借此加载不可信 pickle/joblib |
| mordred | 1.2.0 | import OK | 复用参考代码中的分子描述符 | BSD-3-Clause；应单独锁定并记录传递依赖 |
| python-multipart | 0.0.32 | import OK | 受控上传解析 | Apache-2.0 classifier；需保留许可证 |
| httpx | 0.28.1 | import OK | 测试客户端/受控外部连接 | BSD-3-Clause；不能成为任意 URL 抓取能力 |
| pytest | 9.1.1 | import OK | 测试工具，仅测试依赖 | 上游许可证需随测试环境核对；不进入生产运行包 |
| nistchempy | 2.0.0 | import OK | 参考 SFC 分子工具的可选外部查询 | 许可证字段未明确；V1 默认禁用外部查询，启用前需单独审查 |
| PubChemPy | 1.0.5 | import OK | 参考 SFC 分子工具的可选外部查询 | 许可证字段未明确；不把公网查询当核心能力 |

当前环境还含有大量 Agent/RAG/ML 工具，但“已安装”不代表 V1 依赖。构建必须按 profile 裁剪，不能把整套 SFC/LR 环境复制进交付包。

## 缺失依赖及建议固定版本

三项均已通过 `importlib.import_module` 确认缺失。授权后的建议安装位置统一为项目隔离环境 `D:\Jupyter\Chromatography_agent\backend\.venv`，但目前没有创建环境或执行安装；现有 `D:\Anaconda\envs\SFC` 仅用于只读核查。

| 包 | 建议固定版本 | 当前状态 | 安装位置/阶段 | V1 用途与为何需要 | 影响、许可证与风险 |
|---|---:|---|---|---|---|
| asyncmy | 0.2.14 | `ModuleNotFoundError`，未安装 | 用户授权后仅进入项目隔离环境 `D:\Jupyter\Chromatography_agent\backend\.venv`；项目锁和镜像由构建流水线生成；不安装到 SFC/LR | PRD 规定 SQLAlchemy AsyncSession + `mysql+asyncmy`；支持异步 API/Worker 数据库访问，避免用同步驱动假装满足目标架构 | wheel metadata 为 Apache-2.0；CPython 3.11 Windows x64 wheel 已存在，Linux 目标 wheel/连接、事务和 async SQLAlchemy 行为仍需隔离测试。未安装前 MySQL 能力保持阻塞 |
| dramatiq[redis] | 2.2.1 | `ModuleNotFoundError`，未安装 | 用户授权后仅进入项目隔离环境 `D:\Jupyter\Chromatography_agent\backend\.venv`；仅纳入选择 Worker 的 build profile；不安装到 SFC/LR | PRD/API 要求 Redis + Dramatiq Outbox/Worker，消息只携带 `job_id, attempt, dispatch_id`；需要固定 actor 注册、租约、幂等账本和受控重试 | Dramatiq wheel classifier 为 LGPLv3+；`redis` extra 要求 `redis>=4,<9`，与当前 `redis==8.0.1` 相容。2.x 的 broker、消息重投、`max_retries=0` 和 worker 生命周期仍需验证；不等于持久工作流或 Sandbox |
| pydantic-ai-slim[openai] | 2.46.0 | `ModuleNotFoundError`，未安装 | 仅在包含 assistant 的 build profile 中进入项目隔离环境 `D:\Jupyter\Chromatography_agent\backend\.venv`；不进入未选 assistant 的裁剪包，也不安装到 SFC/LR | `openai` extra 提供 OpenAI/OpenAI-compatible provider 所需 SDK；wheel 中的 `OpenAIProvider` 支持 `base_url`，业务服务不得依赖框架类型 | slim wheel 为 MIT；`openai==3.16.2` 为 Apache-2.0，`tiktoken==0.14.0` 为 MIT。未选 provider 的 Anthropic/Google/Bedrock/MCP extras 不进入解析；脱敏、历史恢复和 tool confirmation 仍需真实验收 |

## 已有但需在项目锁中明确的包

`backend/pyproject.toml` 当前声明 FastAPI、Uvicorn、Pydantic、SQLAlchemy、Alembic、Redis、RDKit 以及测试 extras；它没有声明 `asyncmy`、`dramatiq[redis]`、`pydantic-ai-slim[openai]`、`numpy/pandas/mordred` 等全部运行边界。该文件本轮不修改。授权安装后必须由负责人把实际选中模块的直接依赖写入项目 lock/构建清单，并在全新的 `backend\.venv` 或离线构建环境验证，不能依赖“本机 SFC 恰好装过”。

`PyMySQL==1.2.0` 虽已存在，不满足目标异步 URL；禁止把 `mysql+pymysql` 作为 V1 的无声替代。`redis==8.0.1` 已存在，但没有 Redis 服务连通性或 Dramatiq Worker 证据。

## V1 不纳入的依赖

以下内容不能因为 v2.0 deployment 的 `requirements.txt` 存在就进入 V1：

- `rq`、`qdrant-client`、`langgraph`、`mlflow`、`flaml`：分别属于旧 RQ/向量检索/未来或旧 Agent/训练实现；V1 知识检索基线是 MySQL FULLTEXT ngram，异步基线是 Dramatiq。
- Coding/Sandbox/Harness/Pi、任意代码上传或执行依赖：PRD 明确排除，不注册路由、表、Worker 或专属依赖。
- 旧 `Services/unified_server.py`、旧 `/predict`、`/api/tools/...`、8010 及任何 HTTP 兼容层：科学代码若经审核可迁移到新 adapter，但不能复制旧服务边界。
- 训练数据、客户数据、SQLite、模型权重、大文件和真实 `.env`：依赖审查不复制这些材料；模型资产必须以独立制品清单和授权证据登记。

## 授权后最小验证门槛

安装前须获得用户明确授权；安装只允许发生在项目隔离环境或受控离线构建环境，绝不向现有 SFC/LR 环境 pip install。安装后必须重新生成完整锁和哈希，完成包 import、依赖冲突检查、API 契约检查、迁移检查、隔离 MySQL 事务/锁/全文检索测试、Redis/Dramatiq 重投与幂等测试，并分别记录真实模型/科学验收。任何一项未执行都不能标为通过。Windows 本机无法提供目标 Linux Worker 时，应在 readiness 中明确阻塞，不能以本机 import 代替部署验收。

## 本轮 SFC 实施记录（授权后）

实际目标解释器为 `D:\Anaconda\envs\SFC\python.exe`（Python 3.11.15）。执行前记录了 `pip freeze`、`conda list --json`、Torch/CUDA/device、关键科学包和后端模块 import；证据均在 `backend/evidence/dependency-install-20260921/`。dry-run 使用 Torch 硬约束 `torch===2.6.0+cu124`，仅请求 `asyncmy==0.2.14`、`dramatiq[redis]==2.2.1` 和 `pydantic-ai-slim[openai]==2.46.0`，报告未计划任何 Torch 操作。

首轮安装后发现 `openai==3.16.2` 与已有 `langchain-openai==1.3.5`、`mineru==3.4.4` 的 `openai<3` 约束冲突；未卸载这些既有工具，也未使用 `--no-deps`。只读 PyPI 元数据核对显示 `pydantic-ai-slim==2.31.1` 是仍支持 `openai<3` 的最高可用 2.x 版本，要求 `openai>=2.45.0`。兼容 dry-run 退出码为 0，随后完成收敛安装。

最终 `pip check` 仍报告基线已有的 protobuf/networkx、Gradio、Kubernetes 和 OpenCV 约束冲突；OpenAI 相关冲突已消失。`openai==2.54.0` 为 Apache-2.0，`tiktoken==0.13.0` 为 SFC 原有版本，PydanticAI slim/graph 为 MIT，Dramatiq 为 LGPLv3+，asyncmy 为 Apache-2.0。没有连接外部服务、读取秘密、创建 `.venv`、修改项目依赖声明或宣称真实集成通过。
