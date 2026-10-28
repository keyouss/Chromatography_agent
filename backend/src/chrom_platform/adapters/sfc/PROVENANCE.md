# SFC scientific provenance

The feature functions in `runtime.py` are adapted from the reviewed source
file below. The adapter removes the source application's HTTP/configuration
and SQLite boundaries; model artifacts are loaded only after the new trusted
registry digest, root containment, artifact SHA-256, 66-column feature order,
and model API
checks pass.

| Source | SHA-256 | Target/functions | Function-level change |
|---|---|---|---|
| `D:\Jupyter\SFC\deployments\sfc-predictor-v0.1\backend\app\inference.py` | `581f0a8683df18bb07dcbf6c76357b5fc0b2bd0f644fde2b7940604405b9f286` | `runtime.py`: `safe_float`, `canonicalize`, `molecular_descriptors`, `pair_features`, `normalize_modifiers` → `modifier_features` | Preserved descriptor equations and pair-feature construction; removed application imports, source DTOs, SQLite/history access, auto-normalization, and external defaults; dependencies are lazy and missing packages report unavailable. |

The inspected deployment has no source `LICENSE` file. Its README identifies
the added chromatography assets as internal non-commercial research and does
not establish commercial redistribution permission. This implementation only
claims model loading and deterministic smoke evidence; no golden scientific
label acceptance is claimed.

The reviewed runtime asset set is
`D:\Jupyter\SFC\workspace\model_registry\sfc_predictor_v01`: its registry SHA-256 is
`42f04a4ceb63024e685d0d8a4a8bd1ca57b12216f4f7f1050216f7ec04228429`, with 22
entries (8 `all_column`, 14 `single_column`). Runtime registration requires
each sibling `metadata.json` to match the registry id, scope, feature order,
and `model_sha256`; each `model.joblib` is then SHA-256 checked before the
single permitted deserialization. These assets remain subject to the source
deployment's internal/non-commercial license limitation.

## SFC v1.1 registry and options reuse boundary (2026-10-07)

The author v1.1 registry is metadata-only until `ModelRegistry.load()` is
called: `registry.py::ModelRegistry.list()` filters the JSON `models` array by
threshold, scope, and single-column group. Its `/api/models` projection builds
the seven single-column group descriptions from `single_column_group`,
`single_column_slug`, `stationary_phase`, `mode`, and `allowed_columns`.
`registry.json` contains 22 entries: 8 full-column models and 14 single-column
models (seven groups at thresholds 1.2 and 1.5). The author entries omit a
version, so the platform registry's documented fallback version is `1`.
Author-side `available: true` and the presence of an artifact are metadata,
not proof that the platform has verified a model or may report it `READY`.

The candidate source is the v1.1
`assets/data/result_with_advanced_descriptors.sqlite` table
`result_with_advanced_descriptors`. The platform catalog reads only distinct,
non-null rows for `column_2`, `stationary_phase`, `mode`, `length_mm`,
`inner_diameter_mm`, `particle_size_um`, `column_temperature`, and `flow_rate`
through a SQLite `mode=ro` connection. A read-only query on 2026-10-07 found
372 distinct source rows globally. For `sc-rs12-amylose-dmpc-coated`, applying
its metadata `allowed_columns` together with its exact `stationary_phase` and
`mode` yielded 65 rows:

| Axis | Observed options | Unit |
|---|---|---|
| Column | `AD`, `AMY 1`, `Amycoat`, `ChromegaChiralTM` | — |
| Length | 50, 100, 150, 250 | mm |
| Inner diameter | 2.1, 3.0, 4.6 | mm |
| Particle size | 2.5, 3.0, 5.0, 10.0 | µm |
| Temperature | 25, 26, 28, 29, 30, 32, 34, 35, 36, 38, 40, 41, 45, 50, 55 | °C |
| Flow rate | 1.0, 1.2, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.5, 3.0, 4.0, 5.0 | mL/min |

The global source includes 30 mm inner diameter and 70 mL/min flow values, but
neither occurs in this model's filtered 65 rows. They must not be shown as
options for this model. Author `inference.py::conditions_options()` filters
by fixed phase and mode, while author `main.py` separately intersects the
column axis with `allowed_columns`; it does not constrain every numeric axis
to that column list. The platform options projection therefore applies all
three model-domain restrictions (`allowed_columns`, phase, and mode) before
collecting any axis. Returned axes are observed values, not an assurance that
every Cartesian combination is a valid source row; candidate-scope creation
must still validate selected combinations against the catalog.

This catalog supports read-only option aggregation only. It does not load a
model, create a candidate scope, or establish search capability. In
particular, the full-column fixture's one pinned condition remains a fixture
domain, not evidence of a general full-column search.
