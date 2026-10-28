# 隔离浏览器诊断夹具

此页面只用于本地浏览器诊断。它复用实际 `PredictionView`、`SfcSearchForm`、`AssistantText` 和 `AssistantView` 组件；网络、业务状态与助手发送边界由 fixture 内的合成响应或本地 store 替身提供。它不运行真实预测，也不验证生产 API、真实 Ketcher 或操作系统 IME。

## 启动与停止

从 `frontend/` 目录启动：

```powershell
.\node_modules\.bin\vite.cmd --config tests\browser-fixtures\vite.config.ts
```

页面地址是 `http://127.0.0.1:5185/prediction/sfc`。端口已被占用时先核对监听进程归属，不要直接结束占用端口的进程：

```powershell
$listeners = Get-NetTCPConnection -LocalPort 5185 -State Listen -ErrorAction SilentlyContinue
foreach ($listener in $listeners) {
  Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)" |
    Select-Object ProcessId, ExecutablePath, CommandLine
}
```

只停止自己启动且命令行明确指向 `tests/browser-fixtures/vite.config.ts` 的 Vite 进程。前台运行时用 `Ctrl+C`；需要停止后台进程时，先将下面的 PID 换成上一步核实的 PID：

```powershell
$fixturePid = 12345
$owner = Get-CimInstance Win32_Process -Filter "ProcessId = $fixturePid"
if ($owner.CommandLine -match 'tests[\\/]browser-fixtures[\\/]vite\.config\.ts') {
  Stop-Process -Id $fixturePid
} else {
  throw 'PID 归属未核实；未停止任何进程。'
}
```

## 诊断控件

- **准备导航滚动 40**：等待 400 ms 后清空 navigation 记录，将页面滚动到纵向 40 px，再写入 `armed-scroll-40` 标记。随后进行一次目标 tab 导航或键盘操作。
- **读取诊断记录**：在页面上显示 navigation 与 observer 记录的 JSON。读取动作本身也会打 `read-output` 标记。
- **执行 compositionstart → Enter → compositionend**：在真实 `AssistantView` 上派发脚本合成的组合输入事件；期望组合期间 Enter 不触发本地 `sendMessage` 边界。
- **检查 isComposing / 229 / Shift+Enter**：派发三个合成按键保护场景；期望均不触发本地 `sendMessage` 边界。
- **记录普通 Enter 边界（合成）**：派发普通 Enter；期望只记录一次本地 `sendMessage` 替身调用和合成文本，不发网络请求。

`window.browserFixture.navigation` 与 `window.browserFixture.observer` 暴露的诊断 API 都提供 `read()`、`clear()`、`stop()`；navigation 另有 `mark(label)`。模块导出分别是 `installNavigationDiagnostics` / `navigationDiagnostics` 与 `installObserverDiagnostics` / `observerDiagnostics`。Observer 记录原始 `observe` 调用位置；原生异常会被记录并原样重抛。

## 证据边界

SFC 与 Markdown 输入/响应是本地合成数据；助手 `AssistantView` 使用隔离 Pinia，并替换 bootstrap、能力列表及 `sendMessage` 等 API 边界。IME 操作由 JavaScript 派发事件，不能称为原生 IME 验收。fixture 使用 memory history，不能代表浏览器原生 back/forward 或生产 router 的滚动恢复。synthetic UI、原生键盘/滚动行为与历史恢复是不同证据，报告时不要混称。

诊断 bootstrap 只从 `tests/browser-fixtures/main.ts` 引入，不属于产品源码或生产构建入口；任何夹具结果都不能表述为真实预测、真实 API 或生产验收。
