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

- TLC: `127.0.0.1:8000`
- CC: `127.0.0.1:8001`
- HPLC: `127.0.0.1:8002`
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

- `TLC_API_URL=http://127.0.0.1:8000/predict`
- `CC_API_URL=http://127.0.0.1:8001/predict`
- `HPLC_API_URL=http://127.0.0.1:8002/predict`

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
2. 等待 `8000/8001/8002` 健康检查通过
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
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health'
Invoke-RestMethod -Uri 'http://127.0.0.1:8001/health'
Invoke-RestMethod -Uri 'http://127.0.0.1:8002/health'
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

## GitHub 发布建议

当前建议把 `D:\Jupyter\Chromatography_agent` 作为独立仓库发布，不要把 `D:\Jupyter` 父目录下其它项目一起提交。

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
- 端口 `8000/8001/8002` 是否被其它进程占用

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
