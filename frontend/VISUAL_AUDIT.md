# V1 前端视觉复核

## 已统一

- 页面背景使用浅色蓝白渐变，主内容区与左侧导航分层明确。
- 左侧导航固定为 236px，使用浅蓝导航底、白色面板和 `#1890ff` 主色；当前项使用 `#003366` 深蓝激活态。
- AppShell 增加固定顶栏、面包屑、构建标识和真实运行状态，设置仍固定在左下角。
- 面板、表单、按钮、状态徽章、空状态、加载状态、错误状态和任务事件统一边框、圆角、阴影和焦点样式。
- 800px 以下收缩导航为图标栏，520px 以下顶栏和内容栈叠，保留键盘焦点与跳转到主内容链接。

## 边界

- 没有加入 Coding、Workspace、Shell、Sandbox 页面或操作控件。
- 没有加入演示数值、静态成功状态或新的 API 行为；现有服务状态和结构规范化结果仍由真实 store/API 驱动。
- 本轮只调整 AppShell、全局 CSS 和现有视图/组件使用的视觉类；模块注册、stores、API、测试和 TLC 算法未修改。

## 检查

- 静态扫描确认允许的前端页面中没有新增 Coding/Workspace/Sandbox UI 或假成功数据。
- 窄屏 CSS 已覆盖 800px 与 520px 断点；主前端依赖已安装，typecheck/test/build 通过，独立 Playwright 证据仅覆盖静态资源和预期 API 404，不构成完整业务验收。
- 应用内浏览器出现的 MutationObserver 报告被记录为观测层差异；Ketcher 源码中仍有 `ref.current` 直接传入 `ResizeObserver.observe` 的竞态风险，当前不把该现象描述为已修复。
- 嵌套路由通过 `route.meta.module` 回溯父导航，`/prediction/tlc` 顶栏显示为“色谱预测 · TLC”。
