---
doc_id: SERVICE-CC-DATASET
title: Column chromatography dataset organization
source_path: D:\Jupyter\Chromatography_agent\Services\column_chromatography_prediction-main\dataset\README.md
tags: project, CC, dataset, eluent
status: ok
---

# Column chromatography dataset organization

## Source
D:\Jupyter\Chromatography_agent\Services\column_chromatography_prediction-main\dataset\README.md

## Why It Matters
Use to explain CC dataset categories, columns, eluents, and column types.

## Extracted Evidence
## Dataset Organization
The dataset is categorized by:
- **Column Specifications**: 4g/8g/25g/40g
- **Column Types**: 
 - Normal-phase (Silica-CS)
 - Reverse-phase (C18/NH2/CN)
- **Eluent Systems**: 
 - PE/EA (Petroleum Ether/Ethyl Acetate)
 - DCM/MeOH (Dichloromethane/Methanol)
 - MeOH/H2O (Methanol/Water)

Each combination category maintains a dedicated CSV file for data storage and access.
```

## Project Reuse
- Supports citation-backed answers in the chromatography Agent.
- Helps explain why tool calling, RAG, and structured prediction services are separated.
- Can be used in interview discussion about context engineering and token-efficient retrieval.
