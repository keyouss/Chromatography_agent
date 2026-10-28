# TLC 科学复用来源与许可记录

来源入口（交付包 canonical copy）：
`D:\Jupyter\Chromatography_agent\deliverables\chromatography_tools_windows_cc_hplc_gc_tlc\tlc_rf\predict.py`

入口 SHA-256：
`978c690b4ddc3bc12087269af0b81b24c7103080a0e66678041757b2d7d6c3ff`

四个外部模型资产（仅装配时从受控 artifact 根读取，未复制到本仓库）：

| 组件 | 相对路径 | SHA-256 |
| --- | --- | --- |
| ANN | `model_LGB/model_ANN (3).pkl.gz` | `05119d440f0c224671beb4faec470700598214dbd3fdc56119214efa0da3d234` |
| LGB | `model_LGB/model_LGB (3).pkl.gz` | `4174408259cbb1605a4b57c0cacc0026c265427f22224d887f9e17164c24b69d` |
| XGB | `model_LGB/model_XGB (3).pkl.gz` | `ca24624473fd617c462d1ad687a9b3c082aa2110796cc5c244b265078877d396` |
| RF | `model_LGB/model_RF (3).pkl.gz` | `10dbad5457e8961418f28b42dd5c4350e55dc367574d38dba421b16a481d7559` |

适配改动：保留经审查的 167 位 MACCS + 6 位溶剂描述符 + 6 位分子描述符顺序和
LGB/XGB/RF/ANN（0.2/0.2/0.2/0.4）集成；改为服务端注册根内的显式 artifact
根，加载前逐文件校验 SHA、固定 179 维模型 API 和验证证据；显式支持模型声明的
三种二元体系；拒绝 `petroleum_ether` 别名，服务端显式归一化正比例并返回原始/归一化值；移除旧 HTTP、CLI、
动态源码发现、客户端路径参数和可视化输出。

许可事实：v1.1 README 将新增色谱资产描述为内部非商业研究材料；审计未发现作者
LICENSE/NOTICE。商业使用和再分发授权未证实，因此本适配器默认 `UNAVAILABLE`，
没有独立标签集时也不得宣称科学泛化验收。
