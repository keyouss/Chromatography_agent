# Chromatography Agent

本项目把本地色谱预测服务与 LLM Agent 接在一起，支持三类任务：

- TLC 薄层色谱 `Rf` 预测
- CC 柱色谱分离效果预测
- HPLC 手性色谱柱分离概率与保留时间预测

用户通过 `UI_app.py` 进入 Gradio 聊天界面，Agent 会根据问题自动路由到对应工具，再调用 `Services/` 下的真实 FastAPI 预测服务。

## 项目结构

```text
D:\Jupyter\Chromatography_agent
|-- UI_app.py
|-- chemistry_agent.py
|-- tlc_tool.py
|-- cc_tool.py
|-- hplc_tool.py
|-- start_services.py
|-- Chromatography_agent.bat
|-- launch_component.ps1
|-- config.py
|-- .env.example
|-- Services/
|   |-- tlc_server.py
|   |-- cc_server.py
|   |-- hplc_server.py
|   |-- model_service_utils.py
|   |-- train_neural_best_models.py
|   |-- best_tlc_model.pth
|   |-- best_cc_model.pth
|   |-- best_hplc_model.pth
|   `-- hplc_column_descriptor.npy
`-- Log/
```

## 运行架构

推荐使用两套 Python 环境分别运行服务和 Agent：

- `DE` 环境：预测服务、模型推理、模型训练
- `LR` 环境：LangChain Agent、Gradio UI、LLM 调用

这样做的原因是两边依赖侧重点不同：

- 服务端更依赖 `torch`、`torch_geometric`、`rdkit`、`mordred`、`fastapi`
- Agent/UI 更依赖 `langchain`、`langchain-openai`、`gradio`、`python-dotenv`

### 服务环境（DE）关键包

至少应确认这些包可用：

- `torch`
- `torch_geometric`
- `fastapi`
- `uvicorn`
- `rdkit`
- `mordred`
- `numpy`
- `pandas`
- `scikit-learn`
- `joblib`

### Agent 环境（LR）关键包

至少应确认这些包可用：

- `gradio`
- `langchain`
- `langchain-openai`
- `langchain-core`
- `python-dotenv`
- `requests`
- `pydantic`
- `torch`（如果本地还有少量模型相关依赖）

## 服务端口与模型文件

固定端口：

- TLC: `127.0.0.1:8010`
- CC: `127.0.0.1:8011`
- HPLC: `127.0.0.1:8012`
- UI: `127.0.0.1:7860`

服务默认加载的模型副本统一放在 `Services` 根目录：

- `Services/best_tlc_model.pth`
- `Services/best_cc_model.pth`
- `Services/best_hplc_model.pth`
- `Services/hplc_column_descriptor.npy`

请求期间只做推理，不做训练。

## `.env` 配置

复制 `.env.example` 为 `.env`，按实际环境填写。不要提交真实密钥。

```powershell
Copy-Item -LiteralPath 'D:\Jupyter\Chromatography_agent\.env.example' -Destination 'D:\Jupyter\Chromatography_agent\.env'
```

### 必填或常用变量

#### LLM 相关

- `LLM_TYPE`
- `API_KEY`
- `BASE_URL`
- `MODEL_ID`

本地 Ollama 可设置 `LLM_TYPE=ollama`，并使用：

- `OLLAMA_API_BASE=http://127.0.0.1:11434/v1`
- `OLLAMA_MODEL_ID=deepseek-r1:8b`
- `OLLAMA_API_KEY=ollama`

如果不用 `siliconflow`，还可能需要：

- `OPENAI_API_KEY`
- `OPENAI_API_BASE`
- `QIANFAN_AK`
- `QIANFAN_SK`
- `DASHSCOPE_API_KEY`
- `ZHIPU_API_KEY`
- `MOONSHOT_API_KEY`

#### 可选搜索能力

- `TAVILY_API_KEY`
- `SERPAPI_API_KEY`

#### 本地预测服务地址

- `TLC_API_URL=http://127.0.0.1:8010/predict`
- `CC_API_URL=http://127.0.0.1:8011/predict`
- `HPLC_API_URL=http://127.0.0.1:8012/predict`

#### Agent 行为

- `AGENT_MAX_ITERATIONS`
- `AGENT_MEMORY_K`
- `AGENT_TEMPERATURE`

#### Gradio

- `GRADIO_SERVER_NAME`
- `GRADIO_SERVER_PORT`
- `GRADIO_SHARE`

## 启动方式

### 1. 启动真实预测服务

```powershell
Set-Location -LiteralPath 'D:\Jupyter\Chromatography_agent'
& 'D:\Anaconda\envs\DE\python.exe' 'D:\Jupyter\Chromatography_agent\start_services.py'
```

### 2. 只启动 UI

```powershell
Set-Location -LiteralPath 'D:\Jupyter\Chromatography_agent'
& 'D:\Anaconda\envs\LR\python.exe' 'D:\Jupyter\Chromatography_agent\UI_app.py'
```

### 3. 启动 UI 时顺带拉起服务

命令行参数方式：

```powershell
Set-Location -LiteralPath 'D:\Jupyter\Chromatography_agent'
$env:CHROMA_SERVICE_PYTHON='D:\Anaconda\envs\DE\python.exe'
& 'D:\Anaconda\envs\LR\python.exe' 'D:\Jupyter\Chromatography_agent\UI_app.py' --with-services
```

环境变量方式：

```powershell
Set-Location -LiteralPath 'D:\Jupyter\Chromatography_agent'
$env:START_SERVICES_WITH_UI='true'
$env:CHROMA_SERVICE_PYTHON='D:\Anaconda\envs\DE\python.exe'
& 'D:\Anaconda\envs\LR\python.exe' 'D:\Jupyter\Chromatography_agent\UI_app.py'
```

### 4. 一键启动

```powershell
& 'D:\Jupyter\Chromatography_agent\Chromatography_agent.bat'
```

该脚本会：

1. 用 `DE` 启动 `start_services.py`
2. 等待 `8010/8011/8012` 健康检查通过
3. 用 `LR` 启动 `UI_app.py`
4. 等待 `http://127.0.0.1:7860` 可访问
5. 用系统默认浏览器打开 UI

## 新设备迁移时最小修改项

如果别人换了设备，通常只需要改这两类内容：

### 1. 修改 `Chromatography_agent.bat`

重点检查这些变量：

```bat
set "ROOT=D:\Jupyter\Chromatography_agent"
set "DE_PY=D:\Anaconda\envs\DE\python.exe"
set "LR_PY=D:\Anaconda\envs\LR\python.exe"
set "LOG_DIR=%ROOT%\Log"
set "PS_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "LAUNCHER=%ROOT%\launch_component.ps1"
```

新机器至少要把：

- `ROOT`
- `DE_PY`
- `LR_PY`
- `PS_EXE`
- `LAUNCHER`

改成自己的实际路径。

### 2. 修改 `.env`

至少检查：

- LLM 提供商和 API 变量
- `BASE_URL`
- `MODEL_ID`
- 三个预测服务 URL
- Gradio 监听地址和端口

## 模型准备与训练

如果 `Services` 根目录缺少模型副本，可重新训练或复制：

```powershell
Set-Location -LiteralPath 'D:\Jupyter\Chromatography_agent\Services'
& 'D:\Anaconda\envs\DE\python.exe' 'D:\Jupyter\Chromatography_agent\Services\train_neural_best_models.py' --epochs 300 --seed 324
```

当前约定：

- TLC 训练后复制到 `Services/best_tlc_model.pth`
- CC GeoGNN 训练后复制到 `Services/best_cc_model.pth`
- HPLC 使用已有权重并复制到 `Services/best_hplc_model.pth`

训练记录保存在各自子项目的 `training_records/` 下。

## 健康检查与测试

### 服务健康检查

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8010/health'
Invoke-RestMethod -Uri 'http://127.0.0.1:8011/health'
Invoke-RestMethod -Uri 'http://127.0.0.1:8012/health'
```

### 服务测试

```powershell
Set-Location -LiteralPath 'D:\Jupyter\Chromatography_agent'
& 'D:\Anaconda\envs\DE\python.exe' 'D:\Jupyter\Chromatography_agent\test_services.py'
```

### UI 端到端测试

```powershell
Set-Location -LiteralPath 'D:\Jupyter\Chromatography_agent'
& 'D:\Anaconda\envs\LR\python.exe' 'D:\Jupyter\Chromatography_agent\test_ui_e2e_cases.py'
```

## 本地 RAG 知识库

本项目内置一个最小 RAG 闭环，用于回答色谱论文背景、模型方法、工具迁移和上下文工程问题。

知识来源：

- `C:\Users\13459\Documents\发表` 中与色谱、自动化、TLC、CC、HPLC、retention time 相关的 PDF。
- `Services/` 三个子项目中的 README、数据组织说明和本地模型服务信息。

构建知识库：

```powershell
Set-Location -LiteralPath 'D:\Jupyter\Chromatography_agent'
& 'D:\Anaconda\envs\LR\python.exe' 'D:\Jupyter\Chromatography_agent\knowledge_base\build_knowledge_base.py'
```

构建产物：

- `knowledge_base/docs/*.md`：可提交的小型知识卡片，不包含论文全文。
- `knowledge_base/sources_manifest.json`：知识源清单。
- Chroma 索引默认写入 `%TEMP%\Chromatography_agent\chroma`，可用 `RAG_CHROMA_DIR` 覆盖。

Agent 中新增 `Knowledge_Base_Search` 工具。涉及论文、方法解释、项目迁移、上下文工程的问题会优先检索知识库，并在回答中使用 `[doc_id]` 引用来源。

评测问题：

```powershell
Set-Location -LiteralPath 'D:\Jupyter\Chromatography_agent'
& 'D:\Anaconda\envs\LR\python.exe' 'D:\Jupyter\Chromatography_agent\eval\run_rag_eval.py'
```

默认只检查 `eval/rag_eval_questions.jsonl` 格式。加 `--run-agent` 才会真实调用 Agent 和 LLM。

## Function Calling / MCP 化思路

TLC、CC、HPLC 和知识库检索都作为独立工具暴露给 Agent。工具层只负责参数校验、调用本地服务或检索知识库，并统一返回 JSON envelope：

- `ok`
- `tool`
- `input`
- `result`
- `summary`
- `warnings`
- `error`
- `sources`

这样可以把当前 LangChain Tool Calling 平滑迁移到 MCP 或其它函数调用协议：工具 schema、输入参数和返回结构已经稳定，外层只需要替换调用适配器。

## 迁移到苏仁智能场景

这个色谱 Agent 可以迁移为健康监护垫 / App / 健康管理平台的 AI 助手原型：

- 健康监护垫：把传感器信号、设备状态、异常趋势检测封装为工具，RAG 检索设备说明、指标解释和护理流程。
- App：面向用户解释睡眠、体征、风险提示和健康建议，使用引用式回答降低幻觉。
- 健康管理平台：面向医生、运营和交付团队做知识问答、工单辅助、报告生成和文档交付。
- 部门内部 AI 提效：把 SOP、产品文档、交付模板、常见问题和历史案例构建为知识库，按任务检索少量上下文，减少长 prompt 和重复沟通。

## 上下文工程继续深挖

后续可以把当前 RAG 做成更完整的上下文记忆系统：

- 模型自身知识：负责通用语言理解、基础化学/健康常识和工具选择判断。
- RAG：负责论文、本地项目、企业私有文档、产品 SOP 等可引用知识。
- Tool Calling：负责确定性计算、预测服务、数据库查询和外部系统动作。
- 短期记忆：保留当前会话内的用户目标、已补充参数和工具结果摘要。
- 长期记忆：沉淀用户偏好、历史实验、健康趋势、常用报告风格。
- Token 节省：把工具说明和领域资料从系统 prompt 中移出，按 query 检索 top-k 小片段，再让模型基于小上下文回答。

## GitHub 发布建议

发布前应保证：

- `.env` 不进入版本控制
- `Log/` 不进入版本控制
- 本地日志、PID 文件、IDE 文件夹已忽略
- 只提交当前项目目录中的代码、文档、必要模型和脚本

推荐流程：

1. 在 `D:\Jupyter\Chromatography_agent` 初始化独立 git 仓库
2. 提交首个 commit
3. 在 GitHub 网页创建一个新的公开仓库
4. 添加 `origin`，使用 HTTPS URL
5. 用 HTTPS + PAT 推送 `main`

不要把 PAT、API Key、真实 `.env` 内容写进仓库或 README。

## 常见问题

### 1. 服务启动后 `/health` 不通

先检查：

- 模型文件是否在 `Services` 根目录
- 当前 Python 环境是否装了 `fastapi`、`uvicorn`、`torch`、`torch_geometric`
- 端口 `8010/8011/8012` 是否被其它进程占用

### 2. UI 能开但 Agent 不能回答

先检查：

- `.env` 里的 LLM 配置是否完整
- `LLM_TYPE`、`API_KEY`、`BASE_URL`、`MODEL_ID` 是否匹配
- `config.py` 读取到的服务 URL 是否指向正确端口

### 3. 一键启动脚本在新机器失效

优先检查：

- `Chromatography_agent.bat` 中的 `ROOT`
- `DE_PY`
- `LR_PY`
- `LAUNCHER`
- PowerShell 路径是否正确

### 4. 更换模型后服务仍然使用旧结果

可能是旧服务进程仍在运行。重启 `start_services.py` 或重新执行 bat 脚本，让服务重新加载模型。
