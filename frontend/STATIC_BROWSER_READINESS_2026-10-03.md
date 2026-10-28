# 前端静态/失败态浏览器验收记录（2026-10-03）

## 范围与生命周期

- 仅启动前端临时实例，未启动、停止或修改后端、MySQL、Redis、Worker。
- 启动前核查 5173 无监听；Vite 实际监听 `127.0.0.1:5173` 的 PID 为 `51592`，命令行为 `frontend/node_modules/.bin/../vite/bin/vite.js --host 127.0.0.1 --port 5173`。
- 验收结束后已停止精确 PID `51592`，再次核查 5173 监听数为 `0`。9012 既存服务 PID `14880` 未操作。

## 浏览器观察

- 桌面导航顺序为：色谱预测、色谱智能助手、数据与知识中心、模型训练、模型中心、实用工具；系统设置固定在左下角。主导航没有 batch 路由或 batch 注册项。
- 9012 当前能力接口返回 `build_id=universal-v1`、五类色谱声明，但唯一能力为 `structure.normalize: UNAVAILABLE`，原因是 `MissingGreenlet`。预测页因此只显示服务端实际返回的 TLC 能力；未把缺失的 CC/HPLC/GC/SFC 表单伪装成可用。
- 预测页批量按钮进入明确不可用占位：“后端批量 DTO 与任务接口尚未冻结……不会向未发布路径提交”，没有伪造结果。SFC batch 的源码提交边界也明确阻断，未注册为交付能力。
- 首屏加载态显示“正在连接/加载能力”；服务信息缺失时保留真实错误或“服务端能力尚未就绪”，数据、知识、训练、模型中心均没有静态成功记录。助手显示“还没有业务会话”和真实工具阻塞说明。
- `/assistant` 刷新后仍保持 `/assistant`，助手空会话状态可恢复；本轮不创建业务会话、不提交预测、不触发真实 SSE，以免把未就绪后端当作业务 E2E 通过。
- 390px 视口巡检 `/`、`/prediction`、`/assistant`、`/data`、`/data/datasets`、`/data/knowledge`、`/training`、`/model-center`、`/structure`、`/settings`，均无横向溢出；桌面和窄屏浏览器控制台均无新增 error/warn。

## 门禁与证据

- `npm run verify:contracts`：通过。
- `npm run typecheck`：通过。
- `npm test`：7 files / 29 tests 通过。
- TLC 专项 Vitest：5/5 通过，覆盖比例和非法输入门禁、空/部分失败曲线态。
- SFC 专项 Vitest：4/4 通过，覆盖双对映体必填、Modifier 归一化和 batch 边界。
- `npm run build`：通过，Vite 110 modules。
- `npm run verify:profiles`：四 profile 静态装配及边界检查通过。
- 截图：[预测页失败/不可用态](./static-audit-prediction.jpg)，SHA-256 `CC10D67F088E6E0D170C92D6BF65D22BE6F5B3EA414FF6CAA3F3463AD6A4A3A2`；[助手空会话态](./static-audit-assistant.jpg)，SHA-256 `B8EDE3AD80A39AD76BC7DF706412010C275F95B7C875C7BB0D7FF49E81545812`。

## 门禁结论

前端静态导航、加载/空/失败保护、batch 不伪装、刷新和 390px 布局检查通过。五类真实表单提交、服务不可用恢复和真实 SSE 链路仍因后端能力返回 `MissingGreenlet` 而阻塞；本记录不将这些检查称为 E2E。
