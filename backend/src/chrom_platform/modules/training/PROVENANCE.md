# Training provenance

This vertical slice contains only the lifecycle boundary and a server-side
allowlist.  It does not register a production scientific template because no
training template, immutable customer data contract, reviewed evaluation
protocol, model artifact, or redistribution license was supplied.

The following files were inspected as read-only references:

| Source | SHA-256 | Use | License status |
|---|---|---|---|
| `D:/Jupyter/SFC/dist/sfc-predictor-v2.0/backend/modeling/trainers/fixed.py` | `be997ca85ae1a1b4a5a5bb4995ad0aaea16c10fa426d646d1d1af21b506d4df` | fixed ExtraTrees shape only | no LICENSE/NOTICE found in audited bundle |
| `D:/Jupyter/SFC/dist/sfc-predictor-v2.0/backend/modeling/experiments/splitter.py` | `68b447f8db19faa6219363769a2674d0bc847b27fe9eb426b5729b774b336273` | split semantics only | no LICENSE/NOTICE found in audited bundle |
| `D:/Jupyter/SFC/dist/sfc-predictor-v2.0/backend/modeling/datasets/validator.py` | `81ef7bc947ba045059a9af1b449f06cfeeeb5a43da9bbd1d2aaa697e636cba436` | data validation semantics only | no LICENSE/NOTICE found in audited bundle |

AutoML, MLflow, sandbox execution, dynamic imports, uploaded code, command
fields, and arbitrary dependency installation are deliberately excluded.
