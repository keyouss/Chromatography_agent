# SFC 搜索表单来源与契约边界

- 当前页面由 `frontend/src/modules/prediction/PredictionView.vue` 装配 `SfcSearchForm.vue`；提交流程创建服务端候选范围、复核模型/项目/轴值/候选预算绑定，再创建 SFC 1.1 单柱或全柱预测。历史 `SfcPredictionForm.vue` 不用于描述当前产品页面。
- 前端 SFC 搜索请求和响应类型来自 `frontend/src/shared/prediction/contracts/sfc-v11-exact-revision20261008-02.generated.ts`，经 `sfc11.ts` 与 `sfc-search.types.ts` 引用。该 revision 02 是未冻结的候选契约，不替代 `universal-v1` 既有正式契约；类型生成也不代表运行验收通过。
- 科学特征函数来源路径记录在 `backend/REUSE_MANIFEST.json`：`D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\backend\app\inference.py`。平台适配器移除原应用的 HTTP、配置和 SQLite 边界；来源路径不构成当前平台部署就绪或模型验收证据。
- 全柱金标准输入保留为启动/回归检查样例，不定义全柱候选域。当前后端全柱候选适用性声明针对数据源中观察到的完整条件元组，并带有泛化尚未确立的提示；轴上的观测值不能被解释为任意笛卡尔组合都有效。
- 本文只记录源码与契约边界。不能据此宣称全柱能力已在部署环境通过真实验收，也不能宣称 22 个模型均已验证或处于 `READY`。
