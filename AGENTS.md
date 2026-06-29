# AGENTS.md

## 基本要求

- 回答使用中文。
- 修改代码前先分析：先读 `README.md`、本文件和相关源码，再动手。
- Windows + PowerShell；优先使用绝对路径。
- 有空格的路径必须加引号；关键路径先用 `Test-Path` 验证。
- 不修改全局环境变量；临时变量只在当前 PowerShell 会话中设置。
- 不删除、移动或覆盖模型权重、数据集、训练记录，除非用户明确要求。

## 项目概览

本项目是本地色谱预测 Agent：

- `UI_app.py`：Gradio 聊天入口。
- `chemistry_agent.py`：LLM、LangChain 工具注册、Agent 执行与 trace 逻辑。不要新增独立 `agent_trace.py`。
- `tlc_tool.py`、`cc_tool.py`、`hplc_tool.py`：工具层，只调用 `config.py` 中的 API URL。
- `start_services.py`：启动真实 FastAPI 服务。
- `test_services.py`：服务连通性测试。
- `Chromatography_agent.bat`：一键启动，DE 启动服务，LR 启动 UI。
- `Services/`：真实模型服务、模型副本和训练子项目。

固定服务端口：

- TLC: `127.0.0.1:8000`
- CC: `127.0.0.1:8001`
- HPLC: `127.0.0.1:8002`
- UI: `127.0.0.1:7860`

## 本机环境

- Conda: `D:\Anaconda\Scripts\conda.exe`
- DE: `D:\Anaconda\envs\DE\python.exe`，优先用于真实服务、模型推理和服务测试。
- LR: `D:\Anaconda\envs\LR\python.exe`，优先用于 LLM、Agent 和 UI。
- DL: `D:\Anaconda\envs\DL\python.exe`，可用于训练或旧流程兼容。

临时指定服务 Python：

```powershell
$env:CHROMA_SERVICE_PYTHON='D:\Anaconda\envs\DE\python.exe'
```

## 模型与服务约定

- 三个服务只从 `Services` 根目录加载统一模型副本。
- 服务请求期间只做推理，不训练模型。
- 当前服务文件名为 `tlc_server.py`、`cc_server.py`、`hplc_server.py`；不要恢复 `mock_*_server.py`。
- HPLC GeoGNN 默认使用 CPU 推理；`/health` 应包含 `inference_device: "cpu"`。
- CC 工具层可保留 `model_path` 兼容参数，但真实服务使用 `Services/best_cc_model.pth`。
- 缺少模型或依赖时，返回清晰错误并说明应训练、复制模型或安装哪个库。

核心模型副本：

- `Services/best_tlc_model.pth`
- `Services/best_cc_model.pth`
- `Services/best_hplc_model.pth`
- `Services/hplc_column_descriptor.npy`

训练记录放在对应子项目的 `training_records/` 中，并将最佳模型复制到 `Services` 根目录。

## 常用命令

启动真实服务：

```powershell
Set-Location -LiteralPath 'D:\Jupyter\Chromatography_agent'
& 'D:\Anaconda\envs\DE\python.exe' 'D:\Jupyter\Chromatography_agent\start_services.py'
```

启动 UI：

```powershell
Set-Location -LiteralPath 'D:\Jupyter\Chromatography_agent'
& 'D:\Anaconda\envs\LR\python.exe' 'D:\Jupyter\Chromatography_agent\UI_app.py'
```

启动 UI 并顺带启动服务：

```powershell
Set-Location -LiteralPath 'D:\Jupyter\Chromatography_agent'
$env:CHROMA_SERVICE_PYTHON='D:\Anaconda\envs\DE\python.exe'
& 'D:\Anaconda\envs\LR\python.exe' 'D:\Jupyter\Chromatography_agent\UI_app.py' --with-services
```

一键启动：

```powershell
& 'D:\Jupyter\Chromatography_agent\Chromatography_agent.bat'
```

健康检查：

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health'
Invoke-RestMethod -Uri 'http://127.0.0.1:8001/health'
Invoke-RestMethod -Uri 'http://127.0.0.1:8002/health'
```

服务测试：

```powershell
Set-Location -LiteralPath 'D:\Jupyter\Chromatography_agent'
& 'D:\Anaconda\envs\DE\python.exe' 'D:\Jupyter\Chromatography_agent\test_services.py'
```

语法检查：

```powershell
& 'D:\Anaconda\envs\DE\python.exe' -m py_compile 'D:\Jupyter\Chromatography_agent\Services\hplc_server.py' 'D:\Jupyter\Chromatography_agent\Services\model_service_utils.py'
& 'D:\Anaconda\envs\LR\python.exe' -m py_compile 'D:\Jupyter\Chromatography_agent\chemistry_agent.py' 'D:\Jupyter\Chromatography_agent\UI_app.py'
```

## 修改与验证

- 服务端改动：同步考虑 `Services/*_server.py`、`Services/model_service_utils.py`、`start_services.py`、`test_services.py`。
- Agent/UI 改动：同步考虑 `chemistry_agent.py`、`UI_app.py`、三个 `*_tool.py`、`config.py`。
- HPLC 改动：至少确认 `/health` 的 `ok=True`、`surrogate_only=False`、`inference_device="cpu"`，并跑一次 `/predict` 或 `test_services.py`。
- 启动逻辑改动：测试端口已占用时不会重复启动服务。
- 完成后按改动范围更新 `README.md`。

## 安全边界

- 不把 API key、token、secret 写进代码或 README。
- 不重新引入 mock 服务作为主流程。
- 不随意更改端口。
- 不全局安装依赖；缺库时说明缺少的库和建议安装环境，等待用户处理。
- 遇到已启动的旧服务进程，要提醒用户重启服务后新代码才会生效。
