# GC 科学适配器复用记录

来源根：`D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\gc_retention_time`

运行时复用离线 GC 程序升温入口的 30 点温度程序、3D 分子图、Mordred 描述符和
`MultiSeqModel` 推理逻辑；没有复用旧 HTTP 服务、动态路径参数或旧 API envelope。
加载前会校验下列所有执行源码和权重的 SHA-256，模型固定 CPU，权重只使用
`torch.load(..., weights_only=True)`。

| 来源文件 | SHA-256 | 适配用途 |
|---|---|---|
| `predict.py` | `96725a4c2b1010caceae2ac89229a3f816fe59dc0b7c5c6f63bdc1baef12cc4e` | 30 点线性程序规则、输出语义参考 |
| `Model/compound_tools.py` | `50e62add29877733a8871967471149f3d7401e0712e3dfdd1a1879de04463af3` | 受控分子图与 3D 特征 |
| `Model/config.py` | `7e22519f7fc62fa125b5821c00cad7316f0cbfd54e3573ec9245dc8ba072480d` | 作者模型配置模块 |
| `Model/data_process.py` | `2af7dca63f5b4ddb4cf324fc62fb53e738c27db8733d00e59169fc3d831c06c5` | 数据集与图/序列构造 |
| `Model/GIN.py` | `412381895c0db9568faa78e12945d426b8f32e7e1aca3b31788e871fd1d368a6` | GIN 图模型定义 |
| `Model/model.py` | `642d9a3376cced4e615b0eb07f81e7cf2c157cb401cd1f873b2e32ef8ab3bbd2` | `MultiSeqModel` 定义 |
| `Outcome/GNN_seq240408_250c/model_save_600.pth` | `0433756eab33689981c6d881e3b76c34a8043eb2f47a6adf6ed4a65dd9d5758c` | 固定权重 |

最小改动：将可信结构 resolver、固定临时工作目录、central manifest/handle、CPU
加载、哈希校验和严格结果校验包在新 adapter/runtime；没有修改来源目录。

模型训练固定相为 5% phenyl/95% dimethylpolysiloxane。当前来源未发现独立
`LICENSE`/`NOTICE`，README 的内部非商业研究说明不足以证明商业使用、再分发或
权重交付许可；因此能力须有中央验证证据后才可 READY。结果仅用于实验规划，
不宣称载气效应、柱型效应或独立泛化验收。
