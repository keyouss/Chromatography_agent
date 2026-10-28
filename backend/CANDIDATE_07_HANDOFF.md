# candidate-07 构建交接

日期：2026-09-21（Asia/Shanghai）
候选根：`D:\Jupyter\Chromatography_agent\backend\.generated\snapshots\m3-m4-v1-candidate-20260921-07`
输入 fingerprint（before/after 相同）：`A7CB3C184BBB04C0317CA4EAD43838284C3F22A3F8B7C3FDD8FC773F85D26C63`
候选根 SHA-256（相对路径、字节数、文件 SHA 排序）：`E6172E36D08F8D9F9AEB46C0B971A98E732D0A42C3683E18CD4A246CB44D54CD`，文件数 `342`
输入分组：backend source、backend/alembic、contracts、builds、deploy、scripts、candidate-05、candidate-06；完整 before/after 记录位于候选根 `BUILD_FINGERPRINT.json`。

## 逐 profile 制品

| profile | OpenAPI SHA | normalized OpenAPI SHA | package SHA | SOURCE_MANIFEST SHA | migrations | worker status |
|---|---|---|---|---|---|---|
| `minimal-structure-v1` | `E217447C96357B758C866306EE4074E736E6364DFB37D37FE2A9B05A2D41D454` | `F5C57950F6C0CFD0A619E375EFB191549D78B1EF07DA0E645A26B28456F6F45A` | `80609B69927B55E6D779A592EA7CA208F8E6FFA72D922E0D51AE39A5BE094816` | `E27E3A173DF34E48C6186FD53ED1A37069213EAAA629D8B9944197C61A3AC6E8` | `0001_m1_persistence` | `NOT_SELECTED` |
| `sfc-v1` | `8DFC88B1F04C16A827B7B04021EA84A14B963163191EB2F224058EAEAA809154` | `778AE39484EB6DFD48BEBCEB1D105E76DC3DED832470CA46C2A71546A21A1F23` | `F6BF99C6589C353D6931641D30ADD26DDD16C687AF060B8519748BF44C9504EE` | `8145CAA6BA7E0C081C7288CF839F2DF82E895A3DD7C26076981C88B8E6DEC3C4` | `0001_m1_persistence, 0002_m2_prediction_data, 0007_worker_outbox_lease` | `BLOCKED` |
| `cc-v1` | `BD4665935A34B72D576705C5EB8ABDB069F9168255087A330771C1230BFFFBA4` | `635D75970F64E6D1E25F1BD0A82AF4B3B37181F30F174A88C8CD2DF5F901330D` | `47BF641FD99A685EB952053C9CA904250F28674925E76F26591D718C10635615` | `F562052A32312F24FC3DE61DAE261C8255C3D19C4BBCBC8B3E63F850FA2B7E2A` | `0001_m1_persistence, 0002_m2_prediction_data, 0007_worker_outbox_lease` | `BLOCKED` |
| `universal-v1` | `4F4D8A1A87CE84B7E1964933956BA79EE8AD7A38CE79C4B25D17440E3612C7AB` | `CB4F9B92DDE639FC5188A4D898DE23EFB1E20B22733722BD50D7EFF580F84D1A` | `632D5A1F8FCE5763E2206E90B5DDB13F5F694586DEB3552F4031B3E3F705C65A` | `87E1670F7B5C80EFFBE20BEB4B16CEE15D776DAE01FE090C0A06B8CE55A090F6` | `0001_m1_persistence, 0002_m2_prediction_data, 0003_m3_knowledge, 0004_m3_training, 0005_m3_model_center, 0006_m4_assistant, 0007_worker_outbox_lease, 0008_merge_worker_outbox, 0009_training_outbox_lease` | `BLOCKED` |

## 结构与真实状态

- snapshot 根仅有 `openapi/`、`profile-packages/` 和必要的 `BUILD_FINGERPRINT.json`；四 profile 各自只有一个 canonical 包。
- normalized OpenAPI 逐 profile 同时等于 active contracts、candidate-05 和 candidate-06；构建未修改 contracts。
- minimal 仅含 `0001`，无 worker service/volume/image/env/WORKER_COMMAND 或 worker artifact；sfc/cc 为 `0001+0002+0007`；universal 为 `0001..0009` 且唯一 head 为 `0009_training_outbox_lease`。
- sfc/cc/universal 物理包含 `worker_cli.py`、选中 worker 及静态 handler 依赖；生产 handler registry、真实 MySQL/Redis/Dramatiq 尚未验收，状态保持 BLOCKED。

## 定向验证与限制

- 已在构建过程中执行 canonical root、profile physical trim、SOURCE_MANIFEST 双向 hash、隔离 import、migration graph、forbidden legacy reference 和 OpenAPI parity 门禁。
- 后续应使用 candidate-07 根运行 parity/root/contract/migration/profile physical trim/offline script 定向测试；旧测试若固定 candidate-04/05 或旧迁移常量，需标明常量代际差异，不得覆盖真实失败。
- 真实 MySQL、Redis、Dramatiq broker/Worker、生产 handler registry、真实模型/训练/provider、干净离线安装和业务 E2E 仍未验收，因此候选不是 release-ready。

构建脚本：`scripts/build_candidate_07.py`。候选目录为不可变输出，后续变更应生成新的候选编号。
