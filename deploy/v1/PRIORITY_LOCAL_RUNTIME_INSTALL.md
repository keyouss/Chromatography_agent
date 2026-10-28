# 本机优先上线：隔离 Linux Worker 安装方案

状态：用户明确暂不批准 Linux 容器；本方案延期，未拉取新镜像、未安装新容器依赖。本轮 API 和 Worker 改用现有 Windows SFC 环境，复用现有 MySQL/Redis 完成本机浏览器验收。以下 Linux 方案仅保留供后续恢复，不得作为本轮安装授权。

## 安装位置与影响

- API 使用现有 Windows SFC Python；其 Torch 保持 `2.6.0+cu124`。
- Worker 在 Docker Desktop 的新 Linux 镜像/容器运行，拟使用 `python:3.11-slim-bookworm`，首次成功取得镜像后记录实际 digest。
- 本次只新增项目 Worker 镜像、容器与项目运行配置；不修改 Windows SFC/LR、全局变量或现有 MySQL/Redis 数据卷。
- 复用已健康的 MySQL/Redis，模型资产只读挂载。项目数据库已经迁移至 0010，不重复初始化或清库。

## 依赖范围

平台与科学计算直接版本以 `backend/requirements-worker-priority.txt` 为输入：FastAPI、Uvicorn、Pydantic、SQLAlchemy、Alembic、asyncmy、Redis、Dramatiq、RDKit、Torch、NumPy、Pandas、multipart、Pillow、scikit-learn、joblib、LightGBM、XGBoost、Mordred、torch-geometric。Torch 固定 `2.6.0+cu124`。

助手 Worker 另需与现有已验证版本一致的 `pydantic-ai-slim==2.31.1`、`pydantic-graph==2.31.1`、`openai==2.54.0`、`tiktoken==0.13.0`；由后端负责人补入实际清单及所需 provider extra，安装前检查解析结果。包含这些包所需传递依赖，安装后记录完整 freeze 与一致性检查。

补充同步业务端口：采用 `PyMySQL==1.2.0`（已只读核实当前 SFC 安装版本），仅在新 Linux 镜像安装。独立 `SYNC_DATABASE_URL` 使用 `mysql+pymysql`，与异步 `DATABASE_URL` 指向同一业务库；不替代 asyncmy 队列/outbox，也不静默回退。进程复用 Engine，每个任务的同步 Session 在受控执行线程内独立创建、使用、关闭；不能跨线程共享 AsyncSession。真实运行需验证租约心跳、事务和幂等边界。

Linux 系统运行库范围：`ca-certificates`、`libgomp1`、`libglib2.0-0`、`libxrender1`、`libxext6`、`libsm6`、`libx11-6`、`libstdc++6`。不默认安装编译器，不静默替换科学包版本；若无兼容 wheel 或需范围外依赖，记录失败后重新审查。

## 命令与校验边界

- API：现有解释器运行 `uvicorn chrom_platform.app:create_app_from_environment --factory`，仅绑定本机，端口读取项目配置，默认 9012。
- Worker：容器运行 `python -m chrom_platform.worker_cli`，独立进程，通过 `DATABASE_URL`、`REDIS_URL`、构建清单和模型验证配置启动。
- Worker 健康检查使用 `python -m chrom_platform.worker_cli --healthcheck`；API readiness 不代替 Worker readiness。
- 秘密值通过本机受保护配置注入，不写文档、构建层或测试输出。
- 先解析依赖并记录镜像、wheel/版本；成功安装后检查导入、Worker 健康及真实异步任务，再验证浏览器。

## 已知未验证项

- Docker Hub 的 `python:3.11-slim-bookworm` 元数据查询返回 EOF，当前尚未取得可验证 digest；这是网络失败，不能断言镜像不存在。
- RDKit `2026.3.3` 等指定版本的 Linux wheel 可用性仍待验证，Windows 成功不能替代 Linux 安装验收。
- 助手依赖版本已补齐；同步业务端口另需上述 PyMySQL，后端正在更新实际清单。若版本解析冲突，不修改 SFC Torch 或用不同科学版本偷偷绕过。

授权对象是上述新 Linux 项目运行环境及其依赖安装；不包含外部分发、商业许可确认或修改现有宿主环境。
