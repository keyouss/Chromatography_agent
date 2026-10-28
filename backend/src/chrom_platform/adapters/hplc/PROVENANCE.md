# HPLC adapter provenance

This adapter reuses the audited scientific implementation from the SFC
Predictor v1.1 HPLC bundle.  The controlled runtime imports only the reviewed
`runtime.py` after checking every source and binary digest; it does not import
the old HTTP server or accept its path/device options.

| item | absolute source path | SHA-256 |
|---|---|---|
| offline entrypoint | `D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\hplc_enantioseparation\predict.py` | `5b1b17a7265313136750e80ded2d5f8db43d2c40445e47476991e2b58b36083c` |
| reviewed runtime | `D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\hplc_enantioseparation\runtime.py` | `b7cce7ab5c3901be03b8c2cd2aab487bd11d7fa68f39234a2de8e7e0f4d8b4f9` |
| author application | `D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\hplc_enantioseparation\application\Multi_column_prediction.py` | `66f238b036b009afcbfbdada3b08bff199726b18754af67fb21073c8fa380b93` |
| GeoGNN weights | `D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\hplc_enantioseparation\application\GeoGNN_model.pth` | `3ff5277fd1a9269b3166882941438f3f68d43b2a8eefedf1d4b61b2801a66bf8` |
| column descriptors | `D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\hplc_enantioseparation\application\column_descriptor.npy` | `b263dd8713acc0b863b76ba295f5d5828b350323f4dc304115d196b9cc1fa969` |
| asset digest manifest | `D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\hplc_enantioseparation\MODEL_SHA256.txt` | `952ba3ed4b3e9c80e50b65366edd46d5147e64d6a8e9145f2eccb8be4bc0fdff` |

The adapter preserves the source model's two ordered structures, q10/q50/q90
retention times, and interval based separation probability.  Runtime output is
validated for shape, ordering, finite values and nondecreasing quantiles.

No LICENSE or NOTICE file was found in the audited source bundle.  Commercial
use, redistribution and model-weight delivery rights therefore remain
unconfirmed; this capability must stay unavailable until the build supplies
the central verification evidence and the required authorization.
