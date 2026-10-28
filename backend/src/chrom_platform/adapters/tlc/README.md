# TLC adapter reuse notice

The TLC feature order and four-model ensemble in `runtime.py` are adapted from
the audited offline source and its checked-in delivery copy:

`D:\Jupyter\Chromatography_agent\deliverables\chromatography_tools_windows_cc_hplc_gc_tlc\tlc_rf\predict.py`

Source SHA-256: `978c690b4ddc3bc12087269af0b81b24c7103080a0e66678041757b2d7d6c3ff`.
The four model asset hashes are recorded in `manifest.py` and are checked
before loading.  The source deployment describes these assets as internal
non-commercial research material and no author license/NOTICE was found.
Commercial use or redistribution permission remains unverified; the central
verification handle is still required before execution.

Adaptation changes are intentional: old HTTP/CLI wrappers, dynamic imports,
source-relative model discovery, client path arguments, implicit ratio
normalization, and visualization output were excluded. The new runtime takes
only a trusted registry-bound root and exposes the versioned platform DTO.
