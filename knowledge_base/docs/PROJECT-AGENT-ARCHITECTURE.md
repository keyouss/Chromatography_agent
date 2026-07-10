---
doc_id: PROJECT-AGENT-ARCHITECTURE
title: Chromatography Agent architecture and migration framing
source_path: D:\Jupyter\Chromatography_agent\README.md
tags: project, agent, tool calling, migration
status: ok
---

# Chromatography Agent architecture and migration framing

## Source
D:\Jupyter\Chromatography_agent\README.md

## Why It Matters
Use to answer portfolio, migration, and architecture questions.

## Extracted Evidence
- TLC 薄层色谱 `Rf` 预测
- CC 柱色谱分离效果预测
- HPLC 手性色谱柱分离概率与保留时间预测

```text
D:\Jupyter\Chromatography_agent
|-- UI_app.py
|-- chemistry_agent.py
|-- tlc_tool.py
|-- cc_tool.py
|-- hplc_tool.py
|-- start_services.py
|-- Chromatography_agent.bat
|-- launch_component.ps1
|-- config.py
|-- .env.example
|-- Services/
| |-- tlc_server.py
| |-- cc_server.py
| |-- hplc_server.py
| |-- model_service_utils.py
| |-- train_neural_best_models.py
| |-- best_tlc_model.pth
| |-- best_cc_model.pth
| |-- best_hplc_model.pth
| `-- hplc_column_descriptor.npy
`-- Log/
```

- TLC 训练后复制到 `Services/best_tlc_model.pth`
- CC GeoGNN 训练后复制到 `Services/best_cc_model.pth`
- HPLC 使用已有权重并复制到 `Services/best_hplc_model.pth`

- TLC: `127.0.0.1:8010`
- CC: `127.0.0.1:8011`
- HPLC: `127.0.0.1:8012`
- UI: `127.0.0.1:7860`

## Project Reuse
- Supports citation-backed answers in the chromatography Agent.
- Helps explain why tool calling, RAG, and structured prediction services are separated.
- Can be used in interview discussion about context engineering and token-efficient retrieval.
