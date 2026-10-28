# Batch profile candidate matrix

**Status: review candidate only.** No file under `builds/` was changed. The manifest inventory below comes from `builds/README.md` and the four files currently present in `builds/`.

| Current profile | Current batch selection | Candidate intent | Evidence and gates |
| --- | --- | --- | --- |
| `universal-v1` | Off for TLC, CC, HPLC, SFC, GC | This candidate enables `batch` for each of the five already selected adapters. The OpenAPI delta is additive. A route/schema being present does not claim that a model can execute it: each selected type still requires a real READY capability and configured artifact storage. | PRD §4.1 lists single, batch, and type-specific tools at the third navigation level. `builds/manifest.py` allows `batch` for all five adapters. The default batch template/worker has explicit schemas and dispatch paths for those five types. Model readiness remains a runtime gate. |
| `sfc-v1` | Off; SFC `single_column` and `full_column` only | **Recommend for a future SFC-only candidate, not this frozen-manifest change.** Add `batch` only in a separate future candidate after full SFC scientific-domain coverage, model capability, deployment storage, and real end-to-end gates pass. The pinned golden is a temporary reference slice only; it does not prove full-column candidate coverage or SFC batch readiness. The formal manifest and active SFC contract remain unchanged here. | PRD §4.1 supports a batch tool per type; the manifest allowlist includes `sfc.batch`, and the existing batch DTO/worker defines `SfcBatchRow`. That establishes implementation intent, not scientific acceptance or model readiness. The pinned golden is a temporary reference slice; full-column candidate coverage and SFC batch acceptance remain incomplete. |
| `cc-v1` | Off; CC `single` only | Keep batch routes, worker registration, and batch migration omitted from this candidate. | The checked-in profile selects only CC single prediction. The general PRD provides future batch intent, but this task's candidate does not widen this profile. |
| `minimal-structure-v1` | No prediction module or adapter | Keep all batch routes, DTOs, worker registration, and migrations trimmed. | The manifest contains only `utilities`; it has no prediction tools. Existing profile topology tests assert that the assembled minimal package has no batch routes or worker. |
| `records-only-v1` | **No formal manifest exists in this checkout.** | No manifest or package is created here. If a records-only profile is proposed separately, keep batch omitted unless that profile explicitly selects prediction adapters and batch. | `builds/README.md` lists four current profiles, and the fourth is `cc-v1`; there is no `records-only-v1` in `builds/` or the generated profile package inventory. `cc-v1` is a CC prediction plus records profile and is not treated as records-only. |

The universal batch OpenAPI candidate is generated from a temporary copy of the universal manifest with batch added to the five selected adapter tool lists. Its comparison is recorded in `semantic-diff.json`. Profile-specific future intent above is not represented as an active contract or a formal manifest edit.

## Capability boundary

Batch template registration and route presence are build capabilities. Execution is conditional on a real model/capability snapshot for that chromatography type, a valid schema/template, budget validation, and a configured production `ArtifactStore`. The batch template registry reports deferred/unknown health until runtime capability checks complete. Do not report the five universal routes as five executable models based only on the manifest.

## Source evidence

- `docs/PROJECT_PRD.md` §4.1: five core types and optional chiral LC, with single/batch/type-specific third-level tools.
- `builds/README.md`: the four checked-in build profiles and their non-delivery caveat.
- `builds/manifest.py`: supported tool allowlist; batch is permitted for TLC, CC, HPLC, SFC, and GC.
- `contracts/candidates/m2-export/revision-20261003-02/freeze-metadata.json`: prior universal contract freeze records unchanged non-universal profiles; this batch candidate remains separate and unfrozen.
