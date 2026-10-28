# V1 构建清单

四个 JSON 是 PRD 的目标 profile。它们通过语义校验不代表其模块已实现或已可交付。

```powershell
& 'D:\Anaconda\envs\SFC\python.exe' 'D:\Jupyter\Chromatography_agent\builds\manifest.py' 'D:\Jupyter\Chromatography_agent\builds\universal-v1.json' 'D:\Jupyter\Chromatography_agent\builds\sfc-v1.json' 'D:\Jupyter\Chromatography_agent\builds\cc-v1.json' 'D:\Jupyter\Chromatography_agent\builds\minimal-structure-v1.json'
& 'D:\Anaconda\envs\SFC\python.exe' -m unittest discover -s 'D:\Jupyter\Chromatography_agent\builds' -p 'test_*.py'
```

构建后续必须输出独立源码、固定注册入口、匹配 OpenAPI/前端类型、依赖锁子集、迁移集合、Worker/Compose 服务和哈希清单。运行时能力发现不能激活未纳入的模块。

最小结构构建仍需任务基础服务：结构文件解析和上传校验在首版契约中均为异步操作。训练依赖数据版本/最小模型登记服务，但不自动带入数据管理或模型中心页面。
