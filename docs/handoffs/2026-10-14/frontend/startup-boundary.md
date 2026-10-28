# 2026-10-14 前端启动截止边界收据

北京时间启动观察止点为 12:09:15。本片仅前端服务操作及本地只读身份检查，无 HTTP 或业务提交。

- 调用前时间检查：`2026-10-14T12:09:13.8337582+08:00`，未拒绝启动。
- 唯一启动命令：`deploy/v1/run-local.ps1 -Action start -Service frontend -Acceptance`，exit 0，输出 `STARTED service=frontend pid=37652`。
- 原生进程创建时间：`2026-10-14T12:09:17.3814743+08:00`，UTC `2026-10-14T04:09:17.3814743Z`；FILETIME `134364245573814743`。
- 可执行文件：`C:\Program Files\nodejs\node.exe`。启动后本地核查 `127.0.0.1:5174` 的 listener owner 为 37652。
- 日志尾部显示 Vite 6.3.5 ready in 6954 ms，stderr 尾部为空；未取得独立 ready 时间戳，不计算精确 ready 时间。

结论：调用前时间检查没有覆盖启动脚本内部初始化；实际进程创建已晚于截止，不能签窗口内启动/ready PASS。分类为 `STARTUP_WINDOW_EXCEEDED`，后续 UI 不执行。前端负责人承担该时间门禁位置不足的责任。

未追加 start、stop、restart、进程树终止、HTTP、Provider、业务 POST 或运行配置变更。晚启动进程的后续处置交服务操作负责人及总控；不自动顺延窗口，不用晚就绪恢复业务操作。此处记录的是检查当时状态，不保证之后持续存活。

原前端预检中组合 status/空端口查询 exit 1：status 输出 STOPPED、pid null；后续独立全 Listen 过滤确认 5174 listener_count=0、exit 0。该预检不是启动尝试。

这是 10 月 14 日新窗的独立收据；不改写 10 月 13 日两窗历史。跨服务结果和整窗请求计数由 QA 单份最终收据维护。

## 后续总控裁定

总控随后明确允许本窗口一次启动观察延长最多 10 分钟，保留原时点证据，启动次数、科学调用与业务预算不变。上文 `STARTUP_WINDOW_EXCEEDED` 仅记录原五分钟观察阶段，不是最终产品或安全失败结论。前端 37652 已执行唯一启动，不追加 start、kill 或重启，也不因这次人为截止边界修改产品代码。当前身份和就绪由 FE 交 BE/QA；只有 API、Worker、FE 与空闲门槛均满足后，QA 才可进入原唯一业务流程。该裁定不清零此前累计操作。
