---
doc_id: SERVICE-CC-MODEL
title: Local column chromatography GeoGNN service and dataset organization
source_path: D:\Jupyter\Chromatography_agent\Services\column_chromatography_prediction-main\README.md
tags: project, CC, GeoGNN, service
status: ok
---

# Local column chromatography GeoGNN service and dataset organization

## Source
D:\Jupyter\Chromatography_agent\Services\column_chromatography_prediction-main\README.md

## Why It Matters
Use as model card background for the local CC tool.

## Extracted Evidence
# Intelligent Column Chromatography Prediction Model

## Citation
If you use this work in your research, please cite:
```bibtex
@misc{wu2024intelligentchemicalpurificationtechnique,
 title={Intelligent Chemical Purification Technique Based on Machine Learning}, 
 author={Wenchao Wu and Hao Xu and Dongxiao Zhang and Fanyang Mo},
 year={2024},
 eprint={2404.09114},
 archivePrefix={arXiv},
 primaryClass={cs.LG},
 url={https://arxiv.org/abs/2404.09114}, 
}
```

### Recommended Installation
```bash
# Create conda environment
conda create -n chromatography python=3.9
conda activate chromatography

## Project Reuse
- Supports citation-backed answers in the chromatography Agent.
- Helps explain why tool calling, RAG, and structured prediction services are separated.
- Can be used in interview discussion about context engineering and token-efficient retrieval.
