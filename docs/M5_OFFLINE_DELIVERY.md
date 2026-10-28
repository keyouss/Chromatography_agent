# M5 离线交付记录

M5 的交付边界是可审计的部署与装配基线：Nginx、Compose（API、worker、frontend、MySQL、Redis）、从 `.env` 读取的端口、迁移与重启操作、四 profile 选择、离线包检查和 ZIP SHA-256。它不安装依赖、不拉取镜像、不启动旧 `8010` 服务，也不携带 Docker socket、Coding/Shell/Sandbox 运行面板。

`scripts/assemble_offline_bundle.py` 只接受已经 staged 的 frontend/backend profile package，然后复制到：

```text
profiles/<profile>/backend
profiles/<profile>/frontend
```

Compose 的 volume 路径只指向这个相对目录。装配器会检查 profile manifest 对齐、`SOURCE_MANIFEST.json` 的源码哈希和物理裁剪（模块、路由、迁移、worker、依赖）、前端生产产物、镜像锁定、`.env.example` 的 worker 命令、主仓库绝对路径、旧接口引用、模型资产 manifest 和资产 SHA-256。缺失模型或模型合同返回阻塞报告，不产生成功包。`scripts/verify_offline_bundle.py` 可对目录或 ZIP 做完整复核，`SHA256SUMS` 覆盖包内文件，ZIP 旁的 `.zip.sha256` 覆盖归档本身。

当前能力事实：`minimal-structure-v1` 在具备已审计运行时镜像后可以走部署基线；`universal-v1`、`sfc-v1`、`cc-v1` 等包含预测适配器的 profile 需要真实模型资产、模型合同、镜像锁和 worker 入口，缺失时必须保持 `BLOCKED`。静态 Compose 配置通过不代表镜像、迁移、MySQL/Redis/worker 集成或科学模型验收已经完成。
