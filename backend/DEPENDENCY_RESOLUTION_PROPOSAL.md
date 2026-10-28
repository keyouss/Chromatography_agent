# 后端依赖精确解析提案（只读结果）

> **实施状态修订（2026-09-21）**：上方内容是授权前的只读解析记录。用户随后授权仅修改 SFC 环境中除 Torch 外的依赖；实际安装与最终验证见本文件末尾“授权后 SFC 实施记录”。当前 SFC 最终组合为 `pydantic-ai-slim[openai]==2.31.1`、`pydantic-graph==2.31.1`、`openai==2.54.0`、`tiktoken==0.13.0`，并保留 `asyncmy==0.2.14` 与 `dramatiq[redis]==2.2.1`。旧的 2.46.0/3.16.2/0.14.0 解析结果没有作为最终安装约束。

核对时间：2026-09-21（Asia/Shanghai；解析输出文件最后写入约 09:19:56）  
状态：仅完成 PyPI 元数据读取、wheel metadata 读取和 `uv pip compile` 解析；没有安装包、没有导入新增包、没有创建 `backend/.venv`，也没有修改 `pyproject.toml` 或任何锁文件。新增的 `constraints-v1-proposed.txt` 只是 50 包的约束提案，不是安装记录或正式锁文件。

## 输入解释器与约束

解析解释器是 `D:\Anaconda\envs\SFC\python.exe`，Python 3.11.15 (64-bit)。解析器是 `uv 0.12.13`。临时输入约束文件为系统临时目录下的 `chrom-pydantic-meta\full-numpy.in`，内容如下：

```text
fastapi==0.136.3
uvicorn==0.49.0
pydantic==2.13.4
sqlalchemy==2.0.50
alembic==1.18.5
redis==8.0.1
rdkit==2026.3.3
pytest==9.1.1
httpx==0.28.1
asyncmy==0.2.14
dramatiq[redis]==2.2.1
pydantic-ai-slim[openai]==2.46.0
numpy==1.26.4
```

实际解析命令（输出也只写入系统临时目录）：

```powershell
uv pip compile `
  --python 'D:\Anaconda\envs\SFC\python.exe' `
  --no-header `
  --output-file "$env:TEMP\chrom-pydantic-meta\full-numpy.lock" `
  "$env:TEMP\chrom-pydantic-meta\full-numpy.in"
```

解析结果为 50 个包，未执行安装和 import 验证：

```text
alembic==1.18.5
annotated-doc==0.0.5
annotated-types==0.8.0
anyio==4.15.1
asyncmy==0.2.14
certifi==2026.7.22
charset-normalizer==3.5.1
click==8.5.0
colorama==0.4.6
dramatiq==2.2.1
fastapi==0.136.3
genai-prices==0.1.7
greenlet==3.5.6
griffelib==2.3.0
h11==0.16.0
httpcore==1.0.9
httpcore2==2.13.0
httpx==0.28.1
httpx2==2.13.0
idna==3.20
iniconfig==2.3.0
jiter==0.17.0
logfire-api==5.1.0
mako==1.4.1
markupsafe==3.0.3
numpy==1.26.4
openai==3.16.2
opentelemetry-api==1.44.0
packaging==26.3
pillow==12.3.0
pluggy==1.6.0
pydantic==2.13.4
pydantic-ai-slim==2.46.0
pydantic-core==2.46.4
pydantic-graph==2.46.0
pygments==2.21.0
pytest==9.1.1
rdkit==2026.3.3
redis==8.0.1
regex==2026.9.10
requests==2.34.2
sniffio==1.3.1
sqlalchemy==2.0.50
starlette==1.6.0
tiktoken==0.14.0
truststore==0.10.4
typing-extensions==4.16.0
typing-inspection==0.4.4
urllib3==2.8.0
uvicorn==0.49.0
```

其中 `numpy==1.26.4` 是显式约束。未加该约束时，当前 RDKit 元数据允许解析到 NumPy 2.4.6；保留 1.26.4 与现有 SFC 科学环境及项目 profile 依赖一致。解析结果不代表这些版本已安装或可用于真实模型服务。

## 官方元数据与最小 OpenAI extra

读取的官方来源：

- [pydantic-ai-slim 2.46.0 PyPI](https://pypi.org/project/pydantic-ai-slim/2.46.0/) 与 [JSON metadata](https://pypi.org/pypi/pydantic-ai-slim/2.46.0/json)：Python `>=3.10`，基础直接依赖为 `anyio>=4.7.0`、`genai-prices>=0.1.6`、`griffelib>=2.0`、`httpx2>=2.7`、`opentelemetry-api>=1.28.0`、`pydantic-graph==2.46.0`、`pydantic>=2.12`、`typing-inspection>=0.4.0`。
- `pydantic-ai-slim[openai]` 只额外声明 `openai>=3.8.0` 和 `tiktoken>=0.12.0`。本次 Python 3.11 解析得到 `openai==3.16.2`、`tiktoken==0.14.0`，没有解析出 Anthropic、Google、Bedrock、Harness 或 MCP server 包。
- slim wheel `pydantic_ai_slim-2.46.0-py3-none-any.whl` 的 License-Expression 是 MIT；`pydantic-graph==2.46.0` 也是 MIT。
- [OpenAI 3.16.2 PyPI](https://pypi.org/project/openai/3.16.2/) 的 wheel metadata 为 Apache-2.0，Python `>=3.10`；[tiktoken 0.14.0 PyPI](https://pypi.org/project/tiktoken/0.14.0/) 的 wheel metadata 为 MIT License，Python `>=3.9`。
- slim wheel 内含 `OpenAIProvider`/`OpenAICompatibleProvider`，其 provider 构造路径支持 `base_url`；这只证明代码和依赖入口存在，不证明任何外部 OpenAI-compatible 服务已连接或验收。
- [asyncmy 0.2.14 PyPI](https://pypi.org/project/asyncmy/0.2.14/) 的 wheel metadata 为 Apache-2.0，Python `>=3.9`；已有 CPython 3.11 Windows x64 wheel，Linux 目标 wheel、连接和事务仍需另测。
- [Dramatiq 2.2.1 PyPI](https://pypi.org/project/dramatiq/2.2.1/) 的 wheel classifier 为 LGPLv3+，Python `>=3.10`。`dramatiq[redis]` 的直接额外约束为 `redis>=4,<9`，与项目已有 `redis==8.0.1` 相容；Dramatiq/Redis 的真实 broker、重投和 worker 生命周期没有验收。

因此，最小官方 distribution/extras 是：

```text
pydantic-ai-slim[openai]==2.46.0
```

不需要安装完整 `pydantic-ai`，也不应为了 OpenAI-compatible 模型附带安装未选的 provider extras。

## 当前 backend 版本冲突结论

当前 `backend/pyproject.toml` 已有 FastAPI 0.136.3、Uvicorn 0.49.0、Pydantic 2.13.4、SQLAlchemy 2.0.50、Alembic 1.18.5、Redis 8.0.1、RDKit 2026.3.3 和测试 extras。解析显示：

- PydanticAI 的 `pydantic>=2.12` 接受现有 Pydantic 2.13.4；锁中 `pydantic-core==2.46.4` 与该固定版本配套。
- `dramatiq[redis]` 的 Redis 范围接受现有 Redis 8.0.1。
- PydanticAI 使用独立的 `httpx2==2.13.0`，与项目测试用 `httpx==0.28.1` 并存，不是同名版本冲突。
- 当前解析未发现 FastAPI、Uvicorn、SQLAlchemy、Alembic、RDKit、pytest 与新增三项运行依赖的版本冲突。
- 这只是依赖解析结果；没有进行 wheel 安装、Python import、MySQL 连接、Redis/Dramatiq broker 或 PydanticAI 模型请求验收。

## 获得授权后的精确 PowerShell/uv 命令

以下命令只创建项目内 `backend\.venv`，使用 SFC 的 Python 3.11 作为解释器，使用仓库内 `constraints-v1-proposed.txt` 固定已解析的 50 个包，再安装项目 `[test]` extra 以及四项新增约束：`numpy==1.26.4`、`asyncmy==0.2.14`、`dramatiq[redis]==2.2.1`、`pydantic-ai-slim[openai]==2.46.0`。命令目前没有执行：

```powershell
uv venv 'D:\Jupyter\Chromatography_agent\backend\.venv' `
  --python 'D:\Anaconda\envs\SFC\python.exe'

uv pip install `
  --python 'D:\Jupyter\Chromatography_agent\backend\.venv\Scripts\python.exe' `
  --constraint 'D:\Jupyter\Chromatography_agent\backend\constraints-v1-proposed.txt' `
  -e 'D:\Jupyter\Chromatography_agent\backend[test]' `
  'numpy==1.26.4' `
  'asyncmy==0.2.14' `
  'dramatiq[redis]==2.2.1' `
  'pydantic-ai-slim[openai]==2.46.0'
```

该提案不修改全局环境变量，不安装到 `D:\Anaconda\envs\SFC` 或 `D:\Anaconda\envs\LR`，也不改变现有 SFC/LR 环境。执行前必须获得明确授权；执行后仍需重新生成带哈希的项目锁，并分别验收契约、MySQL、Redis/Dramatiq、Worker 和真实 OpenAI-compatible 配置。解析通过不能写成安装、import 或真实集成通过。

构建隔离还必须遵守当前 `backend/pyproject.toml` 的 `[build-system]` 固定要求：`setuptools==82.0.1`。该构建依赖不属于上述 50 个运行/测试包，也未被写入约束提案；正式构建应在受控构建环境中单独解析并记录其 wheel/hash。

## 授权后 SFC 实施记录

上方内容是授权前的只读解析记录。实际安装没有使用旧的 50 包 proposal 约束，也没有改 `pyproject.toml`、锁文件或创建 `backend/.venv`。目标解释器是 `D:\Anaconda\envs\SFC\python.exe`，Python 3.11.15。安装阶段先以 `torch===2.6.0+cu124` 做硬约束 dry-run，再安装 `asyncmy==0.2.14`、`dramatiq[redis]==2.2.1` 和 `pydantic-ai-slim[openai]==2.46.0`；该首轮结果使 `openai==3.16.2` 与 SFC 既有 `langchain-openai==1.3.5`、`mineru==3.4.4` 的 `openai<3` 约束冲突。

随后依据 PyPI requires-dist 元数据逐版本核对：2.32.0 起要求 `openai>=3.0.0`，2.31.1 要求 `openai>=2.45.0`，因此在 `openai<3` 约束下选定最高兼容的 `pydantic-ai-slim==2.31.1` 与 `pydantic-graph==2.31.1`。兼容 dry-run 计划为 `openai==2.54.0`、`pydantic-ai-slim==2.31.1`、`pydantic-graph==2.31.1`；实际收敛安装退出码为 0，没有 `--no-deps`，没有 Torch 安装/卸载/替换。最终关键版本为：

```text
asyncmy==0.2.14
dramatiq==2.2.1
genai-prices==0.1.7
jiter==0.17.0
logfire-api==5.1.0
openai==2.54.0
pydantic-ai-slim==2.31.1
pydantic-graph==2.31.1
tiktoken==0.13.0  # SFC baseline; not upgraded
```

最终 import 验证覆盖 `asyncmy`、Dramatiq、PydanticAI、OpenAI、tiktoken、关键科学包和现有 knowledge/model-center worker 模块；`OpenAIProvider(base_url=...)`、`OpenAIChatModel` 及无网络结构化 tool schema 测试通过。最终 `pip check` 的非零结果仅保留既有 SFC 冲突，详见 `pip-check-final.txt`；这不是干净环境或真实服务集成通过。所有命令、版本、日志、基线/最终快照及限制记录在 `backend/evidence/dependency-install-20260921/`。
