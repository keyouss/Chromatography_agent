# V1 契约冻结规则

唯一字段依据为 docs/PROJECT_FRONTEND_BACKEND_API_SPEC.md v0.2。生成链为后端 Pydantic DTO → FastAPI OpenAPI → 总控核查 → contracts/<build_id>/openapi.json → 前端生成类型。

## 交接要求

- 后端在 backend/generated 暂存各构建 OpenAPI，并提供可重复生成命令；总控拥有此目录的冻结快照。
- 每条业务路由必须以 /api/v1 开头；/openapi.json 与 /docs 是框架入口，不写入业务路径。
- 每种 profile 的文档只包含实际纳入的模块、适配器、schema。不得为未来 Coding/Sandbox/Workspace 注册路由或类型。
- 每个 operation_id 唯一、稳定；request/response 必须有真实 schema，不允许通用空成功对象代替实现。
- fixtures 明确为测试数据，须验证符合对应冻结 schema，不可进入生产 seed。
- 契约变更记录原因、影响 profile、前端再生成与测试结果；不能手工修改生成文件掩盖源 DTO 错误。

本目录暂未冻结任何 OpenAPI，不代表 M0 已完成。

候选契约由总控使用标准库脚本校验并冻结：

```powershell
& 'D:\Anaconda\envs\SFC\python.exe' 'D:\Jupyter\Chromatography_agent\contracts\freeze_openapi.py' `
  --source 'D:\Jupyter\Chromatography_agent\backend\generated\universal-v1\openapi.json' `
  --build-id universal-v1
```

脚本拒绝 OpenAPI 3.1 以外的文档、构建 ID 不匹配、业务前缀之外的路由，以及 Coding、Workspace、Sandbox 和旧接口路径；冻结结果写入 `index.json` 并记录规范化文件 SHA-256。
