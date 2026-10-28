# 总控优先级切换 checkpoint

本记录保存转向预测与助手前的共享架构、契约、构建和部署材料；不是可发布版本，也不是 Git commit。

- 已完成：M3/M4 HTTP candidate-05基线冻结；稳定源码独立回归报告408通过/1跳过及租约16通过；部署健康检查命令已同步。测试报告适用范围不扩大为真实模型/队列验收。
- 未完成：candidate-08生成及其物理验收、五类真实模型成功链路、真实LLM工具调用、本机端到端和完整离线交付。
- 当前迁移：后端报告0010为head；实际迁移运行未由此记录证明。保留05—07历史，不覆盖。
- 阻塞与下一步：Linux Worker执行环境尚缺；新优先接口需后端提交OpenAPI候选并由总控审核；各负责人checkpoint待汇总。
- 环境：SFC Python3.11，Torch2.6.0+cu124保持；本机MySQL/Redis容器healthy。未记录凭证。
- 恢复：首先验证MANIFEST.json中source.zip的SHA256，再解压至新目录比较差异；禁止直接覆盖当前工作树或数据库。两模块上线后按各负责人checkpoint恢复延期项。
- 验证入口：使用SFC Python执行deploy/v1/test_offline_bundle.py；Docker Compose静态核对使用docker compose -f deploy/v1/compose.yaml config --no-interpolate --quiet。旧脚本测试不能证明新版源码和真实服务上线通过。
