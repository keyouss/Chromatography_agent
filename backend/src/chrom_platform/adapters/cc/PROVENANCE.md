# CC 受控复用记录

来源根：`D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\cc_column_chromatography`

适配器仅复制并改造离线科学逻辑，不复制 HTTP、动态客户端路径、checkpoint 或 work directory：

| 来源 | SHA-256 | 用途 |
|---|---|---|
| `predict.py` | `b27d578f964f0c29afebee907d0377a37e982bfe0cfe2b9c889e255d48cbaabd` | PE/EA、loading solvent、3D 图构造、模型输出与单位派生 |
| `cc_qgeognn_runtime/bundle.py` | `373813c67e5e8cf5ba7e707115e7717d7be8c671239590bd3e0e018a84d228db` | metadata/preprocessing 读取、`weights_only=True` 状态加载与模型 API 校验 |
| `cc_qgeognn_runtime/legacy.py` | `d86f4ad53d5cfce623487fd3a6bd379b597b78f5f9a5fc28adfe53a0a52b5c2c` | 作者 QGeoGNN 模型类与图特征入口 |
| `cc_qgeognn_runtime/schema.json` | `18ced088c3677d5c4fba603981c77759a52386c8bf9cb54dc44f9d854b29a2db` | 输入特征、V1/V2 q10/q50/q90 schema |
| `application/QGeoGNN.py` | `d77638e4ad615e4b27dc3435d7f1b175BA8C4F3DC139C5043BB2F9D0D37BA896` | 实际加载的作者模型定义；运行前由 CC 可信根校验 |

实际推理加载文件的可信根清单（除 `QGeoGNN.py` 外均为 UTF-8 JSON/文本元数据）如下；模型权重摘要同时必须与部署根 `MODEL_SHA256.txt` 和对应 bundle `checksums.json` 一致：

| 文件 | SHA-256 |
|---|---|
| `MODEL_SHA256.txt` | `921ea94276a0e6b289eefe13c4694f90cbf3f2d44f00a10612776819b01ad0e5` |
| `cc_qgeognn_artifacts/qgeognn_silica_4g/{metadata,preprocessing,checksums}.json` | `330bb668ba9c7934439f5e1bfe23bcf892180c269f788082ccc6ef3c6004f0af` / `017a98b926d80f4a3d9cacc6e23a3e1ae32206903d4082c39be7049db06a898d` / `a3e48d69733db219f53793e501065c6fd8b9e641dbf7c64974a95a79eddd0272` |
| `cc_qgeognn_artifacts/qgeognn_silica_8g_ft/{metadata,preprocessing,checksums}.json` | `1088d2d4a5d683d18915468733bcac42f4eec5c526e377b5b19cbe97b5f5d659` / `da560b112f6b771132589ffbbf145815bb1db2b028b9e5eb6409e940a189b3fd` / `337bd92344b82cf6bae6ec6c0083dd2b684802428d428e26a1a5e94b11452ab1` |
| `cc_qgeognn_artifacts/qgeognn_silica_25g_ft/{metadata,preprocessing,checksums}.json` | `926eaafe022d55603338ed12c787c1e77264d0bc92d8afdaa34abb1d7ea59173` / `4587f11ddb11d034faf8280ef120ba458849a3e8a5b46a53f6ad9d50c6454c35` / `369e2ea6608bd2ec9defe92303d02fd8bda4f39a1879f3aaa2b61fd6a0d7c874` |
| `cc_qgeognn_artifacts/qgeognn_silica_40g_ft/{metadata,preprocessing,checksums}.json` | `394ef48548a2f3dc35e214889351046b623b6932fcb4dd6b6add1249ed92e372` / `1317cb3a138a55927484041ef59c6046bec2a2c295f917f6f344cca1a6dca5f0` / `d007104d35f0caf979fa50dc97a34a9d29a924e4d727474614df48bb4eb66965` |
| `model_state.pt`（4g / 8g / 25g / 40g） | `d6c4c113b2d02b4275b7d1cf3a572ac202d0bd8a1927d0158d0e7ab188ee4c99` / `c8a410a9dcbf55c4d986f27d4849aa07fb4d35a3f2005324f88398cf49333105` / `86f648fcb39ba47c70f52279b111712e45aee23203ba68bd6e812bd0cb38c0b3` / `2afead03ebedbe0e1efd2be6664d669469d1360d2a267c1b8c02dc90a45db9b6` |

函数改动：将旧单 SMILES 离线入口改为可信结构服务提供的双结构逐一推理；将条件解析改为服务端 `CcCondition`；将模型根、bundle、checksums、`MODEL_SHA256.txt`、metadata、preprocessing、schema 和模型 API 绑定校验；固定 CPU 与 `weights_only=True`；保留 `time_seconds = volume_ml * 60 / flow_rate_ml_min`，不改模型 volume；q10/q50/q90 保留为 model metrics，不压成 confidence。

模型文件不复制入仓库。来源部署 README 将 CC 资产标为内部非商业研究，未发现作者 LICENSE/NOTICE；因此本适配器不能据此宣称商业再分发许可。审计与黄金样例/独立泛化证据不足时能力保持 `UNAVAILABLE`。
