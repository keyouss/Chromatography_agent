# 前端静态装配约定

构建器通过 profile 生成 `src/app/generated/registry.ts`，其中仅包含所选模块的**显式静态 import**、导航和路由。业务代码不得使用 `import.meta.glob`、运行时模块字符串导入或能力响应恢复未打包页面。

每个可选模块只公开一个 `module.ts`，形状为：

```ts
export interface FrontendModule {
  id: ModuleId
  navigation: NavigationEntry[]
  routes: RouteRecordRaw[]
}
```

构建器完成以下工作：

1. 校验 `builds/<profile>.json` 及传递依赖。
2. 复制公共壳、结构核心、必要制品/任务组件和所选模块源码到独立输出目录。
3. 写出只导入已复制模块的 `generated/registry.ts` 与不可变 `generated/build-manifest.json`。
4. 生成与该 profile 后端 OpenAPI 对应的 `generated/api-schema.ts`。
5. 安装锁文件的依赖子集并独立执行 typecheck、unit、build；扫描输出确保未选模块 ID、路由和资源不存在。

运行时 `/system/capabilities` 只用于核对 `build_id`、schema 与健康状态。返回额外模块属于构建不匹配错误；前端不据此 import 页面。后端报告已构建能力 `UNAVAILABLE` 时保留页面并展示真实原因，不能改成成功状态。

导航顺序固定为色谱预测、数据与知识中心、模型训练、模型中心、实用工具、色谱智能助手；助手是最后一个业务入口，设置固定左下。最小结构 profile 仍生成结构入口、文件服务所需 UI 和错误/任务基础组件。

Ketcher 是结构核心静态资产，通过相对地址 `/ketcher/index.html` 的同源 iframe 使用；构建器在每个 profile 中复制经哈希验证的 standalone 产物及许可证。找不到产物时构建失败，不输出“绘图可用”的占位交付包。
