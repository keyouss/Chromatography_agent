# V1 静态装配契约

装配输入是根目录 `builds/*.json` 中的不可变 profile。装配器先读取 UTF-8 JSON，再以严格 Pydantic DTO 和语义规则校验 `manifest_version/build_id/modules/chromatography_types/prediction_tools/utilities/export_formats/auth_provider`；校验失败必须停止，不得猜测或补字段。

校验后的依赖闭包由 `core`、`structure_core`、`artifact_core`、`task_core`、`model_runtime` 及所选模块计算。装配输出应固定列出前端/后端源码、路由、Pydantic schema、handler、迁移、Worker、依赖锁和模型/解析器资产。未选模块的源码、schema、路由、迁移、依赖、Worker 和资产不得进入精简包；运行时不得从环境变量恢复被裁掉模块。

当前静态入口：`backend/src/chrom_platform/assembly/manifest.py::load_manifest` 与 `validate_manifest`；应用入口：`backend/src/chrom_platform/app.py::create_app(manifest_path=...)`。运行时只能接收显式路径或精简包内 `chrom_platform/build-manifest.json`，不得向上搜索源码仓库的 `builds/`。可选模块必须提供注册函数，注册结果只能来自已校验 profile；基础服务注册不自动注册管理页面。结构核心由所有 profile 保留，其他业务只有真实实现时才注册路由和 schema。

OpenAPI 生成：

```powershell
& 'D:\Anaconda\envs\SFC\python.exe' -m chrom_platform.generate_openapi --builds-dir '..\builds' --output-dir '.\generated'
```

输出为 `backend/generated/<build_id>/openapi.json`（调用方传入的 `--output-dir` 下的 `<build_id>/openapi.json`）。每个文档只包含该 profile 实际注册的 operation，OpenAPI `info.x-build-id` 必须等于 profile `build_id`，并使用 3.1.x。目标清单中的未实现模块不会被能力发现接口或 OpenAPI 伪装为可用；当前基线仅注册系统、健康、项目及结构规范化路由。

发布候选必须先生成物理 profile，再从同一物理包隔离启动并生成静态 OpenAPI，避免开发源码和裁剪运行时漂移：

```powershell
& 'D:\Anaconda\envs\SFC\python.exe' -m chrom_platform.generate_profile_package `
  --manifest '..\builds\sfc-v1.json' `
  --output-dir '.generated\profile-packages' `
  --source-root '.' `
  --openapi-output-dir '.generated\m2-openapi'
```

`--packages-dir` 也可传给 `chrom_platform.generate_openapi` 批量从既有物理包生成。每个候选的
`summary.json` 同时记录物理包 `SOURCE_MANIFEST.json`、`build-manifest.json`、依赖锁存在状态及哈希，
并记录原始与规范化 OpenAPI 哈希。

可发布判定要求：目标模块必须有已注册实现、锁定依赖、对应迁移、真实模型/解析器资产和可复核契约测试；任一缺失生成未完成报告并判定不可发布。profile 目标清单只表达目标，不得把 `UNAVAILABLE` 能力宣称为完整生产交付。

供外部装配脚本调用的最小契约：

```python
from chrom_platform.assembly import load_manifest, validate_manifest
manifest = load_manifest(path)
validated = validate_manifest(raw_json)
```

函数只返回严格 `BuildManifest`；未知字段、重复值、非法依赖关系抛出 `ValueError`，文件不存在抛出 `FileNotFoundError`。
