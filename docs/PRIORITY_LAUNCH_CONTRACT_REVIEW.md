# 优先上线契约评审

## revision-20260921-01

候选位置：`backend/.generated/priority-launch-contract/revision-20260921-01/`。
结论：路径规划可用于继续实施；当前 JSON 是手写片段，不能替换 active OpenAPI，也不能作为前端正式生成类型依据。历史冻结快照保留。

### 已裁决的跨端边界

- CC 使用两个待分离结构，不要求二者互为对映体。正式接口 CC single 明确 `structure_refs=2`，现有 adapter 声明 `structure_count.min=max=2`，runtime 也校验长度为 2。优先计划表中的“分子结构”按此真实能力解释；不擅改模型输入数量。
- 默认项目复用 access-context，后端返回经访问校验的默认项目与可访问项目。前端自动管理 ID，多项目可按名称选择；不得将组件的 `project-default` 回退值当成真实项目。
- 接受 `/sfc/conditions/options`、`/sfc/candidate-scopes`、`/data-versions/summary`、`/prediction-records/summary` 的路径方向，业务前缀统一 `/api/v1`。
- SFC 候选范围接收专用条件轴，由服务端校验、展开、限额、生成候选 ID，并持久绑定项目及解析后的模型版本；浏览器不负责构造大规模候选集合。
- 资产缺少再分发许可影响外部交付，不阻塞已授权的本机开发与验证。

### 正式冻结前必须修正

1. 从实际 FastAPI 路由及 Pydantic DTO 生成完整 OpenAPI，补齐 parameters、requestBody、responses 和错误响应。现候选操作没有这些定义。
2. `SfcSingleColumnInput` 不可用当前 allOf 扩展关闭额外字段的父对象，否则 `column_id` 被父对象拒绝；通过真实模型生成完整单柱 schema。
3. 批量采用 chromatography_type 判别联合，每类绑定自己的 common_input 和模板；不得任意组合色谱类型与另一类型条件。
4. 摘要需明确数量、状态分布、缺失、适用数值统计、单位及来源版本；禁止以数组裸 object 代替类型化统计。无数据返回真实空摘要。
5. 条件选项、模型快照和预测输出含义均类型化，并保留幂等、草稿 revision 确认和未知字段拒绝。

三位负责人已收到 CC / 默认项目裁决，后端已收到全部评审意见。研发可继续，无需等待现片段冻结；新候选完成后再审核、生成前端类型与契约测试。

## 当前证据界限

后端报告 SFC 真实模型离线推理通过，不经过 8010。此结果不代表 API、MySQL、Redis、Linux Worker、前端及真实 LLM 成功链路已完成。上线验收仍以优先计划规定的真实浏览器操作为准。

## SFC 首链路装配复核

后端新增装配及 TestClient 条件查询返回 200，仍不能替代生产成功链路。总控复核发现以下事项，已交后端负责人修复、测试负责人独立验证：

- `assembly/prediction.py` 在加载模型后直接将环境、黄金样例、异常检查的验证标志置为 True，缺少相应实际检查与证据绑定；不得以此放行模型。
- 配对结构仅检查两个不同 ID 与 `has_stereochemistry`，不足以证明互为对映体；需要复用实际结构配对校验并拒绝不符合条件的结构。
- 新增路由 `/predictions/sfc/...` 与已裁决的 `/sfc/...` 不一致；尚未冻结，应统一。GET 候选目录不替代 POST 创建不可变范围。
- Linux 建议清单沿用旧 v1.1，含 PyMySQL、缺新平台 asyncmy/Dramatiq/Redis 等必要依赖，不能直接用来安装。需基于当前平台与真实 SFC 导入提交最小精确清单，分别定义 factory API 与 Worker CLI 启动命令。

现有 MySQL、Redis 容器健康。新增 Linux 镜像/环境依赖仍待形成可审查方案及必要授权；没有实施安装，也没有把 Windows 推理标为 Linux Worker 验收通过。
