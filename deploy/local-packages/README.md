# Local package candidate

`data-center-invalid-window-20261026/` is a relocation candidate copied from the frozen datasets-only package. Its only runtime-source change is `dataset_csv_elevated_runner.py` deriving `PACKAGE_ROOT` from its own package location; the corresponding file digest in `SOURCE_MANIFEST.json` was updated.

- Frozen source manifest SHA-256: `31675275a79398770cbcb8238b3ee09d4580cb953dc1f6c2e0ebf82edddf7886`
- Relocation candidate source manifest SHA-256: `40dc53cdcae822859698be67c0493a84e392a4e6cf00595ae1a5c4ba732595e5`
- State: **not activated**. The existing protected runtime pin remains unchanged and therefore does not authorize this candidate. Do not start the data acceptance profile from this copy until a separate controlled candidate and protected pin are reviewed and selected.

The original frozen package and its evidence remain under `testing/runs/2026-10-27-data-import-audit/backend-runtime-package-data-window-08/data-center-invalid-window-20261026/` unchanged. No service, database, scanner, or model was started for this relocation.
