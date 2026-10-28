# Ketcher 集成完整性审计（2026-10-03）

## 结论

当前 Ketcher standalone 交付资源完整，未从备用 ZIP 解压或复制文件，也未安装依赖、重建 Ketcher、修改业务契约或业务源码。`StructureInput` 默认 iframe 路径为同源 `/ketcher/index.html`，`VITE_KETCHER_URL` 只接受同源绝对路径；入口引用的 favicon、manifest、JS 和 CSS 均存在。

本轮修复了 `public/ketcher/ASSET_MANIFEST.json` 末尾的两个字节字面量 `\\n`（十六进制 `5C 6E`）。修复后文件可被严格 JSON 解析，manifest 的 34 条文件记录全部逐项通过 SHA-256；随后通过项目既有 `npm run build` 同步 `dist`，并通过 `npm run verify:profiles` 同步和校验 profile 包。

## 来源、产物与许可证证据

| 项目 | 结果 |
|---|---|
| 源目录 | `D:\\Jupyter\\ketcher\\example\\dist\\standalone` 存在，32 个文件；manifest 登记的源树 SHA-256 为 `e9db705dacb7eebce90c4506225e15f8b1471b9042ba7ef4177ceca720114a0c` |
| `public` 产物 | 排除 `ASSET_MANIFEST.json` 后 34 个文件，manifest 列出的 34 个路径全部存在且 SHA-256 全部匹配；其中源 standalone 的 32 个文件逐项与 public 对应文件匹配，另含 `licenses/LICENSE` 和 `licenses/NOTICE` |
| `dist` 产物 | 与 `public/ketcher` 的 35 个文件（含 manifest）逐项同名、同 SHA-256 |
| 源 `LICENSE` | SHA-256 `3d54b234fa88c11d1d96380ff5fe29f3c5d8e1078ef914dfe8185ce4b1fbeac7`；与 `public/ketcher/licenses/LICENSE` 相同 |
| 源 `NOTICE` | SHA-256 `ab82986438fc66fa2b40836415b734ff018b1e42ee3a18434b27bdf1c42eb368`；与 `public/ketcher/licenses/NOTICE` 相同 |
| 备用来源 | `D:\\Jupyter\\ketcher-master.zip` 存在，SHA-256 `50f9063dfb7ae4cb05dad0cf76a88ac90ad2a57b6db1246af217278f9c1556ba`，`tar -tf` 共 37,450 条；含根 `LICENSE`/`NOTICE`，未发现 `example/dist/standalone/index.html`，因此未作为当前产物来源 |

入口 `frontend/public/ketcher/index.html` 的标题为 `Ketcher 3.20.0-rc.1`，引用的 `./static/js/main.5ccbee00.js`（7,834,142 bytes）和 `./static/css/main.861db57e.css`（184,475 bytes）均存在；favicon、PNG 和 `manifest.json` 相对引用也均存在。`frontend/src/core/structure/ketcher.ts` 与 `frontend/src/components/StructureInput.vue` 的同源限制和 sandbox 配置保持不变。

## 测试与边界

- `npm test -- --run tests/navigation.test.ts`：1 个文件、3 个测试通过，覆盖默认路径及外部协议/协议相对路径拒绝。
- `npm run build`：契约生成、registry 生成、vue-tsc 和 Vite 生产构建通过；`dist/ketcher` 由现有 build 流程同步。
- `npm run verify:profiles`：四个 profile 的静态装配和 Ketcher 必需资源存在性检查通过；该命令是静态装配门禁，不是后端或真实模型 E2E。
- 修复后只读核验：strict JSON=True、manifest 34 条、34 条资产哈希通过，`public/ketcher` 与 `dist/ketcher` 各 35 个文件逐项同 SHA-256；profile staging 由 `verify:profiles` 生成并检查。
- 既有 `frontend/STATUS.md` 记录桌面与 390×844 窄屏 Ketcher iframe 加载、静态资源 200；同时明确完整浏览器成功链路未验收，Ketcher 源/构建 bundle 仍有 `ResizeObserver` ref 竞态观察风险。本报告不把静态加载或失败态证据升级为业务 E2E。

## 可复核命令

```powershell
$root = 'D:\\Jupyter\\Chromatography_agent'
$pub = Join-Path $root 'frontend\\public\\ketcher'
$src = Join-Path 'D:\\Jupyter\\ketcher' 'example\\dist\\standalone'
Test-Path $src
Get-FileHash (Join-Path 'D:\\Jupyter\\ketcher' 'LICENSE') -Algorithm SHA256
Get-FileHash (Join-Path 'D:\\Jupyter\\ketcher' 'NOTICE') -Algorithm SHA256
Get-FileHash 'D:\\Jupyter\\ketcher-master.zip' -Algorithm SHA256
Get-ChildItem $src -File -Recurse | Measure-Object
Get-ChildItem $pub -File -Recurse | Measure-Object
Get-Content (Join-Path $pub 'index.html') -Encoding UTF8
tar -tf 'D:\\Jupyter\\ketcher-master.zip' | Measure-Object
Set-Location (Join-Path $root 'frontend')
npm test -- --run tests/navigation.test.ts
npm run verify:profiles
```

修复后的严格 JSON 和尾部状态可用以下只读命令确认，不会修改文件：

```powershell
$manifest = Get-Content (Join-Path $pub 'ASSET_MANIFEST.json') -Raw -Encoding UTF8 | ConvertFrom-Json
@($manifest.files).Count
$bytes = [IO.File]::ReadAllBytes((Join-Path $pub 'ASSET_MANIFEST.json'))
($bytes[($bytes.Length-2)..($bytes.Length-1)] | ForEach-Object { $_.ToString('X2') }) -join ' '
```

