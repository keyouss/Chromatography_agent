# M2 科学代码复用审计

核查日期：2026-09-20  
依据：`D:\Jupyter\Chromatography_agent\docs\PROJECT_PRD.md` v0.12、仓库 `AGENTS.md`。  
审计范围：PRD 来源清单中的 SFC Predictor v0.1、v1.1、v2.0，以及 `mo-group`。  
审计目的：为 M2 预测适配器登记可审查的科学代码、模型加载边界、输入校验、批量/结果转换和真实资产证据。

本次只读检查了来源文件、资产存在性、SHA-256、代码调用边界和随附 README/manifest/schema。没有复制代码、加载权重做推理、安装依赖或宣称科学性能验收。`8010`、旧 `Services/unified_server.py`、旧 HTTP 服务和 Coding/Sandbox 路径不在复用范围；旧部署中的 HTTP 包装器只能作为“不可复用”的边界证据。

## 1. 路径与资产存在性

下列路径均已用 PowerShell `Test-Path -LiteralPath` 验证为存在：

| 来源 | 结果 | 备注 |
|---|---|---|
| `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1` | 存在 | SFC 科学实现、22 个 joblib 模型和 SQLite 历史数据 |
| `D:\Jupyter\SFC\deployments\sfc-predictor-v1.1` | 存在 | SFC 同一模型资产；新增 TLC/CC/HPLC/GC 离线入口和权重 |
| `D:\Jupyter\SFC\deployments\sfc-predictor-v2.0` | 存在 | SFC 业务/治理代码；模型注册表引用外部 workspace artifact |
| `D:\1_Undergraduate_111111111111\group tasks\Agent集成\mo-group` | 存在 | Hugo 静态站点；没有后端 Python、模型权重或推理包 |

审计对象以 PRD 代码参考表为准。`mo-group` 的文档和前端路径也已验证存在：

* `D:\1_Undergraduate_111111111111\group tasks\Agent集成\mo-group\在线工具-前后端接口文档.md`，SHA-256 `6ee00abcec7bd1fc69AAFADC18E43EFC49BECD3E822064F1D35DAAE000A984F3`。
* `D:\1_Undergraduate_111111111111\group tasks\Agent集成\mo-group\js\platform.js`，SHA-256 `edd62243c062ab2af7cdd275ed65a442aa8722ebda365f884f955e27204ee0e2`。
* `D:\1_Undergraduate_111111111111\group tasks\Agent集成\mo-group\platform\index.html`，SHA-256 `7af626db6c39319324f3e4b5d68b1f3ce993e5b33278fabb8add88318cb548cc`。

对 `mo-group` 执行 `rg --files` 搜索 `*.py/*.pkl/*.pth/*.joblib/*.pt/*.onnx` 的结果为 `NO_BACKEND_OR_MODEL_FILES_FOUND`。因此它不能提供可复制的后端算法或真实生产模型。

## 2. 复用结论分级

* **需改造**：科学计算或受控离线入口有实际输入/输出和资产，但必须重写为 `PredictionAdapter`/Worker 调用，绑定新 DTO、artifact ID、模型 manifest、版本和验证证据。
* **仅参考**：可帮助确定特征、字段、训练/注册或前端交互语义，但不应复制为运行时代码或直接当作模型资产。
* **不可复用**：旧 HTTP/API、动态路径/客户端路径、混合旧服务边界、缺失真实资产或与新产品边界冲突的代码。
* **直接复制**：本次没有发现可不经改造直接进入新平台的候选。所有真实模型都需要新适配器契约、artifact 验证和黄金样例检查。

目标位置均为建议位置，不表示当前文件已创建：

| 能力 | 建议目标适配器位置 |
|---|---|
| TLC | `D:\Jupyter\Chromatography_agent\backend\src\chrom_platform\adapters\tlc.py` |
| CC | `D:\Jupyter\Chromatography_agent\backend\src\chrom_platform\adapters\cc.py` |
| HPLC 双对映体 | `D:\Jupyter\Chromatography_agent\backend\src\chrom_platform\adapters\hplc.py` |
| chiral_lc | `D:\Jupyter\Chromatography_agent\backend\src\chrom_platform\adapters\chiral_lc.py`（本次来源未发现独立资产） |
| GC | `D:\Jupyter\Chromatography_agent\backend\src\chrom_platform\adapters\gc.py` |
| SFC | `D:\Jupyter\Chromatography_agent\backend\src\chrom_platform\adapters\sfc.py` |
| 公共模型包校验 | `D:\Jupyter\Chromatography_agent\backend\src\chrom_platform\infrastructure\` 下的 artifact/model verification port；不能复用旧 registry loader |

## 3. SFC v0.1/v1.1：科学特征、模型注册和真实模型

### 3.1 科学代码

| 来源绝对路径 | SHA-256 | 依赖/输入输出 | 结论 |
|---|---|---|---|
| `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\backend\app\inference.py` | `581f0a8683df18bb07dcbf6c76357b5fc0b2bd0f644fde2b7940604405b9f286` | `numpy/pandas/mordred/rdkit/sqlite3`；两个对映体 SMILES、Modifier 结构/比例、候选柱条件 → 66 特征、概率排序、历史匹配和 top-k/all-results | **需改造**；科学特征逻辑可作为 SFC adapter 候选，旧 `PredictRequest`、SQLite 绝对路径和结果结构必须绑定新 DTO/artifact/记录服务 |
| `D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\backend\app\inference.py` | 同上 | 与 v0.1 字节相同；不是第二套实现 | **仅参考**；以 v0.1 记录为唯一科学代码来源 |
| `D:\Jupyter\SFC\deployments\sfc-predictor-v2.0\backend\app\inference.py` | `7fba6f055e71fb4125429f6a4ee46b1472ee500066200b9e22b9cb93aed9e39f` | 同一 SFC 特征/候选生成，增加显式 `feature_names` 参数；`numpy/pandas/mordred/rdkit/sqlite3` | **需改造**；可参考显式特征绑定，但不能复制旧 app 边界或旧 HTTP 路由 |
| `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\backend\app\registry.py` | `6932912c0641cbe73c66def0559d9a4b92d8894140f3b5613b1ab1d91e42779d` | `json/joblib/threading`；从 `registry.json` 加载 joblib 并缓存 | **仅参考**；新平台需要 artifact SHA、manifest/schema/environment/golden-sample/anomaly 验证，不能直接 `joblib.load` |
| `D:\Jupyter\SFC\deployments\sfc-predictor-v2.0\backend\app\model_runtime.py` | `55a8a85df41afde9cf7b318781658a347c820c04815d28f5d62ead6d097abc1b` | `joblib`；按 V2 registry URI 解析并加载 | **仅参考**；registry/lifecycle 语义可参考，直接反序列化和外部 URI 不符合新 artifact 端口 |

v0.1 和 v1.1 的以下两个 SFC 资产字节完全相同，均为真实随附文件而非测试 fixture：

* `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\assets\models\registry.json` 和 `D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\models\registry.json`，各 86,904 bytes，SHA-256 `42f04a4ceb63024e685d0d8a4a8bd1ca57b12216f4f7f1050216f7ec04228429`。两者均声明 22 个模型、8 个 `all_column`、14 个 `single_column`，特征顺序为 66 列。
* `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\assets\data\result_with_advanced_descriptors.sqlite` 和 `D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\data\result_with_advanced_descriptors.sqlite`，各 28,758,016 bytes，SHA-256 `266a6d00df80166c3faf64be9dc21748d9a39a9f7106a24b8741fdd628eb9c1d`。该 SQLite 是历史数据/匹配来源，不应直接成为新平台数据版本。

为避免长路径中的视觉歧义，实际连续 SHA-256 值为：

* registry：`42f04a4ceb63024e685d0d8a4a8bd1ca57b12216f4f7f1050216f7ec04228429`。
* SQLite：`266A6D00DF80166C3FAF64BE9DC21748D9A39A9F7106A24B8741FDD628EB9C1D`。

### 3.2 22 个 SFC joblib 模型资产

根目录为 `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\assets\models`；v1.1 同名目录中的每个文件也存在且与 v0.1 相同。以下路径、存在性和实际 SHA-256 来自逐文件 `Get-FileHash -Algorithm SHA256`：

| 绝对模型路径（v0.1） | scope/algorithm | SHA-256 |
|---|---|---|
| `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\assets\models\rs12-extra-trees-s80-seed42\model.joblib` | all/extra_trees | `f25d39ec61d0b8d4198d4d2bc2993b76f2fc33ba60f13176d475ceb6d654e860` |
| `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\assets\models\rs12-random-forest-s80-seed42\model.joblib` | all/random_forest | `2661c463853f14a014de9be8d098a8c10c789d3e0ce6ae5a87d48283d7e7f371` |
| `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\assets\models\rs12-logistic-s80-seed42\model.joblib` | all/logistic_regression | `a423034bd260aa2728b6f6f795ba1b52b1b2a218df9fda9414dc31372313c621` |
| `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\assets\models\rs12-extra-trees-s70-seed42\model.joblib` | all/extra_trees | `cf91cdf6f70d3d599a623648425fe00043122abf6756c6a57542043c9a24f61c` |
| `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\assets\models\rs15-extra-trees-s80-seed2024\model.joblib` | all/extra_trees | `1e642a9d9aefc9fd811ded8e3a1e833faf34464d40b8a224c2ec72252312d88e` |
| `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\assets\models\rs15-random-forest-s80-seed2024\model.joblib` | all/random_forest | `8b2c5881878505ff6f69524aefa1893fa23b4707c6df60f80337274da770ad7f` |
| `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\assets\models\rs15-logistic-s80-seed2024\model.joblib` | all/logistic_regression | `ad6859428983b2e7b7a693db9b690faf146766ada9509d094bd30dc9e74e557a` |
| `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\assets\models\rs15-extra-trees-s80-seed2026\model.joblib` | all/extra_trees | `673ac5de24a794097b0eed38cacc95485112b1d6000f4c019a1f88188c25d13e` |
| `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\assets\models\sc-rs12-amylose-dmpc-coated\model.joblib` | single/extra_trees | `035cb8cb6fba463a845506cfdee8271bc4dd3e2c7529fd79b5f8642fb1799c40` |
| `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\assets\models\sc-rs12-amylose-dmpc-immobilized\model.joblib` | single/extra_trees | `ce129704287c3b514c7e151b5dfdafc2e06277b5be1c34da0a58511cb4188a4f` |
| `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\assets\models\sc-rs12-cellulose-cmpc-coated\model.joblib` | single/extra_trees | `11d192b0a382dc60e1e861707f50d13bb73365d1489251e45adea413c2dc00e0` |
| `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\assets\models\sc-rs12-cellulose-dcpc-immobilized\model.joblib` | single/extra_trees | `d7d8df3693fdc0401f37d3bfccbbf4dd0b68eda1c19d7a57818d6d2e31987817` |
| `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\assets\models\sc-rs12-cellulose-dmpc-coated\model.joblib` | single/random_forest | `2cf2adc5a6acf0e9535a013fe2d86d9b781787f131934aeabbbbf0d67cf16d3e` |
| `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\assets\models\sc-rs12-cellulose-dmpc-immobilized\model.joblib` | single/extra_trees | `44ab08036d308923d4cd1a3399b7d5d15ce832d39092c5006b88bf4c2ca132bd` |
| `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\assets\models\sc-rs12-cellulose-methylbenzoate-coated\model.joblib` | single/extra_trees | `7cdbf792c17eceb9cee0c10002860d21723ccf1880535fd24954eefcf4e041ed` |
| `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\assets\models\sc-rs15-amylose-dmpc-coated\model.joblib` | single/extra_trees | `c64076ceea523a61c726372b6e51f9146bd6dc11977ac098cd881629015299f2` |
| `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\assets\models\sc-rs15-amylose-dmpc-immobilized\model.joblib` | single/extra_trees | `8c92cd446ac96f35a8508d90e1e8ecb2b2a2851b9395e501f6a29e225803a31c` |
| `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\assets\models\sc-rs15-cellulose-cmpc-coated\model.joblib` | single/extra_trees | `5bcf8d5bbd2ccf4ca4702b841ed373a015aae0c3fb9c81a1e9cf12646a48d678` |
| `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\assets\models\sc-rs15-cellulose-dcpc-immobilized\model.joblib` | single/random_forest | `726a2b09f55a988a92b15ae50a9978eaf576438e196caa947ee60d379e1e3691` |
| `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\assets\models\sc-rs15-cellulose-dmpc-coated\model.joblib` | single/extra_trees | `7367a00922a3dabe843577bf144a6d2fd4573a085c53b86ce9ad7c5bcec8bf0c` |
| `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\assets\models\sc-rs15-cellulose-dmpc-immobilized\model.joblib` | single/extra_trees | `d8fbb2f434baf9e1c61106a10f597ef7a7888bb5d5a8959d1ac18c8d51a9f7f1` |
| `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\assets\models\sc-rs15-cellulose-methylbenzoate-coated\model.joblib` | single/extra_trees | `ce5c1e44ebd0bc3c33eab6728736fde00d00679aefa682e9e64aae4901e38bb7` |

这些是二进制真实资产；没有发现随部署目录提供的独立源代码许可证或 NOTICE。v0.1/v1.1 README 将模型/SQLite 描述为运行时资产，但不足以证明可商业再分发。`model.joblib` 使用 Python 反序列化，因此只能在经过制品校验的受控 Worker 中加载。

## 4. v1.1 四类色谱离线实现

### 4.1 TLC Rf

* 代码：`D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\tlc_rf\predict.py`，13,374 bytes，SHA-256 `f6fb085756b6082c8db7730f9d3ce3b4625347d7cea7e8e43ef0b6b3fb5b2862`。
* 模型资产：`D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\tlc_rf\model_LGB\model_LGB (3).pkl`（SHA `b2f3ff9952be9f4460c87ebf3809ae3083f2d9b61147a13464ddab8e8aabb696`）、`D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\tlc_rf\model_LGB\model_XGB (3).pkl`（`4f5f29c1eff772ca7e26d0b8f970bf859ab5a3d6ae9f72b9dce055f90850bb55`）、`D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\tlc_rf\model_LGB\model_RF (3).pkl`（`6daacbbe71571f60f3f68cd256b693e78fc447240d875c70a97036f29e9246fb`）、`D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\tlc_rf\model_LGB\model_ANN (3).pkl`（`e44869aef53d7d680be1c395bb95cf9851de89b70e2ab3c9bb32314238de843d`）。四个实际文件均存在，且与 `D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\tlc_rf\MODEL_SHA256.txt`（SHA-256 `f9e20d6af6d8c9b45e3758083189b7116addd6780ee27a76d34922f7543696d5`）一致。
* 依赖：`numpy/torch/rdkit/lightgbm/xgboost/scikit-learn/joblib`；其中 v1.1 `backend\requirements.txt` 没有 torch、torch-geometric、lightgbm、xgboost，部署自检也明确是 local-source-only。
* 输入校验/转换：非空 SMILES；五种溶剂比例为有限非负数；只允许论文验证的三种二元体系；归一化后生成 167 位 MACCS + 6 位溶剂描述符 + 6 位分子描述符 = 179 维。
* 输出：`predicted_rf`、四个 component predictions、ensemble `predicted_rf`、normalized mobile phase、模型路径/名称。
* 等级：**需改造**。可复用特征和四模型集成规则，需绑定 TLC schema、artifact_id、模型 manifest 和 output warning；禁止复制旧 `/api/predict` 包装。
* 目标：`D:\Jupyter\Chromatography_agent\backend\src\chrom_platform\adapters\tlc.py`。

### 4.2 CC 柱层析 QGeoGNN

* 入口：`D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\cc_column_chromatography\predict.py`，8,442 bytes，SHA-256 `b27d578f964f0c29afebee907d0377a37e982bfe0cfe2b9c889e255d48cbaabd`。
* 模型加载边界：`D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\cc_column_chromatography\cc_qgeognn_runtime\bundle.py`（1,809 bytes，SHA `373813c67e5e8cf5ba7e707115e7717d7be8c671239590bd3e0e018a84d228db`）读取 `metadata.json`/`preprocessing.json`，CPU `torch.load(..., weights_only=True)`；`D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\cc_column_chromatography\cc_qgeognn_runtime\legacy.py`（1,868 bytes，SHA `d86f4ad53d5cfce623487fd3a6bd379b597b78f5f9a5fc28adfe53a0a52b5c2c`）提供作者模型类和图特征。两者均为科学代码候选，但不能直接作为新平台运行时。
* schema：`D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\cc_column_chromatography\cc_qgeognn_runtime\schema.json`，898 bytes，SHA `18ced088c3677d5c4fba603981c77759a52386c8bf9cb54dc44f9d854b29a2db`。输出目标为 V1/V2 两个洗脱体积，每个 q10/q50/q90，且 `time_seconds = volume_ml * 60 / flow_rate_ml_min`；这是 CC 适配器必须保留的单位规则。
* 真实资产：`D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\cc_column_chromatography\cc_qgeognn_artifacts\qgeognn_silica_4g\model_state.pt` SHA `d6c4c113b2d02b4275b7d1cf3a572ac202d0bd8a1927d0158d0e7ab188ee4c99`；`D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\cc_column_chromatography\cc_qgeognn_artifacts\qgeognn_silica_8g_ft\model_state.pt` SHA `c8a410a9dcbf55c4d986f27d4849aa07fb4d35a3f2005324f88398cf49333105`；`D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\cc_column_chromatography\cc_qgeognn_artifacts\qgeognn_silica_25g_ft\model_state.pt` SHA `86f648fcb39ba47c70f52279b111712e45aee23203ba68bd6e812bd0cb38c0b3`；`D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\cc_column_chromatography\cc_qgeognn_artifacts\qgeognn_silica_40g_ft\model_state.pt` SHA `2afead03ebedbe0e1efd2be6664d669469d1360d2a267c1b8c02dc90a45db9b6`。四个均 2,299,254 bytes，均存在，且与 `MODEL_SHA256.txt` 和各 bundle `checksums.json` 一致。
* 输入校验/转换：SMILES → RDKit 加氢、3D 构象、MMFF；PE/EA 比例、PE/EA/DCM loading solvent、密度、样品体积、loading volume、flow、4g/8g/25g/40g column spec；Mordred 索引和条件 scaler 来自每个 bundle。
* 依赖：`numpy/torch/torch-geometric/rdkit/mordred`；这些依赖未被 v1.1 基础 requirements 完整锁定。
* 许可证/来源风险：未发现部署内 LICENSE/NOTICE；bundle metadata 的 `source_run`/`parent_bundle` 指向 `D:\Jupyter\Chromatography_agent\Services\column_chromatography_prediction-main\cc_qgeognn_training\runs\...`，该字段只是训练来源元数据，且旧 Services 不得作为新平台依赖。README 只支持内部非商业研究，不能据此做商业再分发判断。
* 等级：**需改造**。真实 bundle 和 schema 是最完整的 M2 候选；需将 PE/EA、sample mass、volume/time、artifact hash 与新 CC DTO/manifest 绑定，并确认许可证和黄金样例。目标：`D:\Jupyter\Chromatography_agent\backend\src\chrom_platform\adapters\cc.py`。

### 4.3 HPLC 双对映体

* 入口：`D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\hplc_enantioseparation\predict.py`，9,644 bytes，SHA-256 `5b1b17a7265313136750e80ded2d5f8db43d2c40445e47476991e2b58b36083c`。
* 运行时/模型类：`D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\hplc_enantioseparation\runtime.py`，78,518 bytes，SHA `b7cce7ab5c3901be03b8c2cd2aab487bd11d7fa68f39234a2de8e7e0f4d8b4f9`；作者完整代码 `D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\hplc_enantioseparation\application\Multi_column_prediction.py`，65,504 bytes，SHA `66f238b036b009aafcbfdada3b08bff199726b18754af67fb21073c8fa380b93`。`runtime.py` 混有旧 TLC/CC 兼容路径和 surrogate 函数，不能整体复制；仅按真实 HPLC GeoGNN 入口逐段提取并审查。
* 真实资产：`D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\hplc_enantioseparation\application\GeoGNN_model.pth`，3,139,843 bytes，SHA `3ff5277fd1a9269b3166882941438f3f68d43b2a8eefedf1d4b61b2801a66bf8`；`D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\hplc_enantioseparation\application\column_descriptor.npy`，365,328 bytes，SHA `b263dd8713acc0b863b76ba295f5d5828b350323f4dc304115d196b9cc1fa969`。两个均存在并与 `D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\hplc_enantioseparation\MODEL_SHA256.txt`（SHA `952ba3ed4b3e9c80e50b65366edd46d5147e64d6a8e9145f2eccb8be4bc0fdff`）一致。
* 输入校验/输出：恰好两个非空 SMILES（允许逗号字符串转换）、`ipa_fraction` 在 0–1、正 flow、25 个受支持柱名中的一个或多个；输出每柱 separation probability、sp>0.38 布尔标记、两个化合物的 RT q10/q50/q90、median RT 和 recommended column。
* 依赖：`numpy/torch/torch-geometric/rdkit/mordred`；v1.1 基础 requirements 未锁定 torch/torch-geometric。
* 等级：**需改造**。可作为 HPLC 双对映体 adapter 的科学候选；必须保持两个对映体独立，并禁止与单结构 `chiral_lc` schema 合并。目标：`D:\Jupyter\Chromatography_agent\backend\src\chrom_platform\adapters\hplc.py`。

### 4.4 GC 程序升温保留时间

* 入口：`D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\gc_retention_time\predict.py`，12,079 bytes，SHA-256 `96725a4c2b1010caceae2ac89229a3f816fe59dc0b7c5c6f63bdc1baef12cc4e`。
* 模型和依赖代码：`D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\gc_retention_time\Model\data_process.py`（36,800 bytes，SHA `2af7dca63f5b4ddb4cf324fc62fb53e738c27db8733d00e59169fc3d831c06c5`）、`D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\gc_retention_time\Model\model.py`（39,780 bytes，SHA `642d9a3376cced4e615b0eb07f81e7cf2c157cb401cd1f873b2e32ef8ab3bbbd`）、`D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\gc_retention_time\Model\GIN.py`（16,848 bytes，SHA `412381895c0db9568faa78e12945d426b8f32e7e1aca3b31788e871fd1d368a6`）。
* 真实权重：`D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\gc_retention_time\Outcome\GNN_seq240408_250c\model_save_600.pth`，4,602,037 bytes，SHA `0433756eab33689981c6d881e3b76c34a8043eb2f47a6adf6ed4a65dd9d5758c`，与 `D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\gc_retention_time\MODEL_SHA256.txt`（SHA `ee2bdf82e71d7444f4466e7567a10c7cef47f672b64e4bec36dbc2553ea72395`）一致。
* 输入校验/批量/输出：一个或多个 SMILES；二选一的 30 点温度序列或完整线性升温程序；逐行生成 q10/q50/q90 分钟值，返回 `failed_smiles`、温度序列和 warning。代码会把输入写入临时 Excel，并允许用户传入 `checkpoint`/`work_dir` 路径；后者不能进入新 API，必须改成 artifact ID/受控工作目录。
* 依赖：`numpy/pandas/openpyxl/torch/torch-geometric/rdkit/mordred`；v1.1 基础 requirements 未锁定这些推理依赖。
* 等级：**需改造**。科学图模型和 30 点程序转换可作为 GC adapter 候选，但字段必须以新 GC capability contract 为准，不能承诺未声明的参数。目标：`D:\Jupyter\Chromatography_agent\backend\src\chrom_platform\adapters\gc.py`。

## 5. v1.1 旧后端包装器与 v0.1/v1.1 API

`D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\backend\app\chromatography.py` 存在，16,651 bytes，SHA-256 `b68bb7d53a8c9dc14e72aed8ca5988aea2c484a32b1a78864039d40910300f10`。它提供 `/api/chromatography/tlc|cc|hplc|gc|sfc/predict` 和 `/api/chromatography-ai/chat`，以 `importlib` 动态从 `assets/chromatography/*/predict.py` 加载模块，并在同一个 FastAPI router 中做旧 envelope、AI tool calls 和本地源码健康检查。

该文件的 Pydantic 字段校验、单次输入边界和结果 warning 可作为契约审查参考；代码整体结论为 **不可复用**：

* 路由不是新平台唯一 `/api/v1` 前缀；SFC/旧主服务也仍带历史 API 语义。
* 动态导入、源码目录发现和 `SFC_CHROMATOGRAPHY_ASSETS` 路径选择不满足构建 manifest/显式注册/裁剪要求。
* `HPLCRequest` 接收两个 SMILES，不能用于 `chiral_lc` 单结构；GC/CC 字段也只是旧工具输入，不是新 capability schema。
* AI chat 直接在旧 API 内部调用工具，绕过新 Agent Run、Confirmation、审计和同服务权限流程。

新平台应只从上节离线科学入口提取特征/模型调用，经 Worker 的 `PredictionAdapter` 运行；不复制该 HTTP router。

## 6. v2.0：注册治理、数据校验和生产资产可用性

### 6.1 v2 SFC 科学路径

`D:\Jupyter\SFC\deployments\sfc-predictor-v2.0\backend\app\inference.py` 是新的 SFC 特征候选（见第 3 节），但 v2 本身没有 TLC/CC/HPLC/GC 的资产目录或独立离线入口。v2 `backend\requirements.txt` 锁定了 `numpy/pandas/scikit-learn/joblib/rdkit/mordred` 等 SFC 依赖，但不代表模型科学验收已完成。

### 6.2 注册表和真实 artifact

* `D:\Jupyter\SFC\deployments\sfc-predictor-v2.0\runtime\registry\registry-v23.json` 存在，162,119 bytes，SHA-256 `6ca2d297bf719321003a22e1af632eb229069ac17e194d55fefa501eff614c48`。解析得到 22 个模型条目：16 `published`、6 `legacy`。
* 22 个 `contract.artifact_uri` 都以绝对路径指向 `D:\Jupyter\SFC\workspace\model_registry\sfc_predictor_v01\<model_id>\model.joblib`；逐一 `Test-Path` 均为 True。具体 `<model_id>` 和 SHA 已在第 3.2 节按绝对 v0.1 路径列出；这些是外部 workspace 真实资产，不在 v2 checkout 内，不能成为新包的隐式依赖。
* v2 `backend\mlruns\0\416e1cad4999488282cdccea8f01d71d\artifacts\model.joblib` 存在，172,955 bytes，SHA-256 `11740414f846472782076a8a71f3e08dc9ca58a3925529b808a96611546eb63f`；旁边的 `experiment.json`/`evaluation.json` 是 `ds-test`、80 行小样本、validation/test ROC-AUC 0.5 的训练记录，属于测试/开发 fixture 证据，不能当作生产科学资产或泛化评测。
* `D:\Jupyter\SFC\deployments\sfc-predictor-v2.0\backend\modeling\registry\service.py` 存在，8,335 bytes，SHA-256 `d8193ca52c7d4e312b431a125702922fa95a237206c4aafa5a1ffa17c51ff8da`；可参考模型登记、状态和 artifact digest 字段，但 `ProductionModelResolver`/joblib loader 不能直接复制为新适配器。

### 6.3 数据 manifest 与 fixture/生产区分

* `D:\Jupyter\SFC\deployments\sfc-predictor-v2.0\runtime\datasets\ds-20260829161230-87938090\manifest.json` 存在，sha 字段 `87938090205df3ebb2e856bd9c1459073c9f06f36342be7eac700c8ec28fcbf3`，状态 `invalid`；只能作为数据校验失败样例。
* `D:\Jupyter\SFC\deployments\sfc-predictor-v2.0\runtime\datasets\ds-20260829161353-5619f6b0\manifest.json` 存在，sha 字段 `5619f6b002af5b8f9d4cb0eaf0f17177af0b7f2031f9d265a30a163b6ff6e662`，状态 `ready`、160 行；它是 v2 运行时数据版本样例，不能在未确认来源/授权/独立评测集前标称生产模型数据。
* `D:\Jupyter\SFC\deployments\sfc-predictor-v2.0\backend\modeling\datasets\validator.py` 存在，2,458 bytes，SHA-256 `81ef7bc947ba045059a9af1b449f06cfEEb5a43da9bbd1d2aaa697e636cba436`；可参考 RDKit SMILES、列、缺失、重复、标签和泄漏风险检查，目标应是数据中心的 domain-specific validator，等级 **仅参考**。

## 7. HPLC 与 chiral_lc 边界

本次四类模型资产中的 HPLC 入口明确接收两个对映体 SMILES，适合新 `hplc` 双结构契约。没有在 v0.1、v1.1、v2.0 或 mo-group 中发现名称、目录或权重可以证明“单条含立体信息 SMILES”的独立 `chiral_lc` 模型；因此 `chiral_lc` 适配器应登记为 **资产缺失/不可用**，不能用 HPLC 双对映体模型冒充。

## 8. mo-group 的实际可复用内容

`在线工具-前后端接口文档.md` 记录了 TLC `/api/predict` 的历史 FormData 契约：单 SMILES、固定五溶剂顺序 PE/EA/DCM/MeOH/Et₂O、`example=true` 语义、返回 `predicted_rf/model/molecule_svg/solvents`，以及批量并发 3、比较 3 套体系×15 比例、反推溶剂的前端交互。

`platform/index.html` 与 `js/platform.js` 只有 Canvas 绘制、批量调度、比较/反推交互；文档明确写出后端不在备份包内。结论：

* TLC 的溶剂顺序、批量错误显示、曲线反推和前端结果展示可作为 **仅参考** 的 UI/契约输入。
* 不能复制 `/api/predict`、`localhost:8000`、同源 nginx 旧 API 或 `example` 写统计的旧后端行为；新平台使用 `/api/v1`、artifact/record/job 服务，示例 fixture 必须隔离并标记 `data_origin=fixture`。
* mo-group 不提供 CC/HPLC/SFC/GC/chiral_lc 算法、模型加载、模型 manifest、后端批量实现或结果转换代码。

## 9. 许可证、依赖和风险结论

* v1.1 README 将 TLC/CC/HPLC/GC 资产标为“内部非商业研究”，并警告预测不能替代实验；部署目录未发现作者 LICENSE/NOTICE。CC bundle 还引用了 `Services` 下训练来源。商业使用、再分发和模型权重交付权限均为 **未证实**。
* HPLC/GC/CC/TLC 的实际权重存在且 SHA 清单匹配；这只证明文件完整性，不证明许可证、环境、黄金样例、异常值、独立泛化集或客户验收。
* v1.1 基础 requirements 没有完整锁定四类新增工具所需的 torch/torch-geometric/lightgbm/xgboost/openpyxl；不能把“源码存在”写成当前 build READY。
* 旧入口可接收任意 `checkpoint`、`work_dir` 或动态路径的地方，必须在新适配器中改成服务端已注册 artifact ID；客户端不能提交服务器绝对路径。
* 真实模型包登记前仍需清单、SHA-256、manifest/schema、环境、加载、黄金样例、异常值和许可证核查；本文件没有执行推理，不能宣称科学验收。

## 10. 建议后续 M2 动作

1. 先为每个能力建立独立 adapter manifest：输入/输出 schema、模型/前处理版本、artifact SHA、依赖组和许可证证据；缺失 `chiral_lc` 资产则能力状态为 unavailable。
2. 以 v1.1 四个 `predict.py` 的离线函数为科学参考，在 Worker 中实现受控调用；只复制经审查的特征/结果转换，不复制旧 FastAPI router、AI chat、动态 import 或路径参数。
3. SFC 选择一个经登记的真实模型作为候选 Champion，显式绑定 66 列特征和 22 模型 registry，不把 v2 `ds-test` joblib 或静态预测结果当成生产证据。
4. 为 TLC/CC/HPLC/GC/SFC 分别准备真实黄金样例、异常值和单位测试；CC 必须验证 `time_seconds = volume_ml * 60 / flow_rate_ml_min`，HPLC 必须验证双对映体，GC 必须验证程序温度字段和失败行，TLC 必须验证 179 维特征与三种二元体系。
5. 在许可证/NOTICE 和来源授权未补齐前，所有四类新增色谱能力保持 `UNAVAILABLE` 或受控内部构建；不能因权重文件存在而发布。

## 11. 本次文件变更

只新增本文件 `D:\Jupyter\Chromatography_agent\backend\M2_SCIENTIFIC_REUSE_AUDIT.md`。没有修改 `backend/src`、测试、运行脚本、部署文件、模型、数据、`backend/REUSE_MANIFEST.json` 或任何外部来源目录。
