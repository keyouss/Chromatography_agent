from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path
from types import SimpleNamespace

import pytest

from chrom_platform.adapters.sfc.runtime import SfcCandidate
from chrom_platform.assembly import prediction as assembly

MODEL_ID = "rs12-extra-trees-s80-seed42"
REFERENCE_CANDIDATE_IDS = ["candidate-cellulose-2", "candidate-oz"]
NATIVE_CANDIDATE_IDS = ["sfc-candidate-001", "sfc-candidate-002"]
FEATURES = [
    "column", "stationary_phase", "mode", "length_mm", "inner_diameter_mm", "particle_size_um",
    "column_temperature", "flow_rate", "mw_modi", "tpsa_modi", "nrotbs_modi", "hba_modi", "hbd_modi",
    "logp_modi", "rpsa_modi", "rasa_modi", "mdec_modi", "mats_modi", "modifier_ratio",
    "mw_smiles_pair_value", "mw_smiles_mean", "mw_smiles_median", "tpsa_smiles_mean", "tpsa_smiles_median",
    "nrotbs_smiles_pair_value", "nrotbs_smiles_mean", "nrotbs_smiles_median", "hba_smiles_pair_value",
    "hba_smiles_mean", "hba_smiles_median", "hbd_smiles_pair_value", "hbd_smiles_mean", "hbd_smiles_median",
    "logp_smiles_pair_value", "logp_smiles_mean", "logp_smiles_median", "rpsa_smiles_mean", "rpsa_smiles_median",
    "rasa_smiles_mean", "rasa_smiles_median", "mdec_smiles_pair_value", "mdec_smiles_mean", "mdec_smiles_median",
    "mats_smiles_pair_value", "mats_smiles_mean", "mats_smiles_median", "mw_smiles_abs_diff", "mw_smiles_range",
    "tpsa_smiles_abs_diff", "tpsa_smiles_range", "nrotbs_smiles_abs_diff", "nrotbs_smiles_range",
    "hba_smiles_abs_diff", "hba_smiles_range", "hbd_smiles_abs_diff", "hbd_smiles_range",
    "logp_smiles_abs_diff", "logp_smiles_range", "rpsa_smiles_abs_diff", "rpsa_smiles_range",
    "rasa_smiles_abs_diff", "rasa_smiles_range", "mdec_smiles_abs_diff", "mdec_smiles_range",
    "mats_smiles_abs_diff", "mats_smiles_range",
]
WARNINGS = [
    "FEATURE_VALUE_MISSING:mdec_modi", "FEATURE_VALUE_MISSING:mdec_smiles_pair_value",
    "FEATURE_VALUE_MISSING:mdec_smiles_mean", "FEATURE_VALUE_MISSING:mdec_smiles_median",
    "FEATURE_VALUE_MISSING:mdec_smiles_abs_diff", "FEATURE_VALUE_MISSING:mdec_smiles_range",
]
CONDITIONS = [
    {"column": "Cellulose 2", "stationary_phase": "cellulose tris (3-chloro-4-methylphenylcarbamate)", "mode": "coated", "length_mm": 150.0, "inner_diameter_mm": 3.0, "particle_size_um": 3.0, "column_temperature_c": 25.0, "flow_rate_ml_min": 1.5},
    {"column": "OZ", "stationary_phase": "cellulose tris (3-chloro-4-methylphenylcarbamate)", "mode": "coated", "length_mm": 150.0, "inner_diameter_mm": 3.0, "particle_size_um": 3.0, "column_temperature_c": 25.0, "flow_rate_ml_min": 1.5},
]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _feature_digest(names: list[str]) -> str:
    return hashlib.sha256(json.dumps(names, separators=(",", ":")).encode()).hexdigest()


def _candidate(candidate_id: str, conditions: dict[str, object]) -> SfcCandidate:
    return SfcCandidate(
        candidate_id=candidate_id, column=conditions["column"], stationary_phase=conditions["stationary_phase"], mode=conditions["mode"],
        length_mm=conditions["length_mm"], inner_diameter_mm=conditions["inner_diameter_mm"], particle_size_um=conditions["particle_size_um"],
        column_temperature=conditions["column_temperature_c"], flow_rate=conditions["flow_rate_ml_min"],
    )


class _Classifier:
    n_estimators = 80
    random_state = 42


class _Imputer:
    def __init__(self, strategy: str):
        self.strategy = strategy


class _Pipeline:
    def __init__(self, steps):
        self.named_steps = steps


class _Preprocessor:
    def __init__(self):
        self.named_transformers_ = {"num": _Pipeline({"imputer": _Imputer("median")}), "cat": _Pipeline({"imputer": _Imputer("most_frequent")})}


class _Model:
    def __init__(self):
        self.feature_names_in_ = FEATURES
        self.n_features_in_ = 66
        self.named_steps = {"preprocess": _Preprocessor(), "model": _Classifier()}


class _Catalog:
    DEFAULT_SCOPE_ID = "sfc_conditions_default"

    def __init__(self, path: Path, candidates):
        self.path = path.resolve()
        self.candidates = tuple(candidates)
        self.bound = None

    def bind_model_scope(self, **kwargs):
        self.bound = kwargs

    def resolve(self, scope_id):
        assert scope_id == self.DEFAULT_SCOPE_ID
        return self.candidates


class _Registry:
    def __init__(self, registry_path: Path, artifact_path: Path):
        self.registry_path = registry_path
        self.artifact_path = artifact_path

    def artifact_for(self, entry):
        return self.artifact_path


def _fixture(tmp_path: Path) -> tuple[dict, _Registry, _Catalog, object, str]:
    registry_path = tmp_path / "registry.json"
    metadata_path = tmp_path / "metadata.json"
    artifact_path = tmp_path / "model.joblib"
    platform_catalog_path = tmp_path / "platform.sqlite"
    author_catalog_path = tmp_path / "author.sqlite"
    author_inference_path = tmp_path / "author_inference.py"
    author_output_path = tmp_path / "author_output.json"
    registry_path.write_text("{}", encoding="utf-8")
    artifact_path.write_bytes(b"fake model bytes")
    platform_catalog_path.write_bytes(b"fake platform catalog")
    author_catalog_path.write_bytes(b"fake author catalog")
    author_inference_path.write_text("# pinned fake author inference", encoding="utf-8")
    author_output_path.write_text("{}", encoding="utf-8")
    preprocessing = {
        "pipeline_class": f"{type(_Model()).__module__}.{type(_Model()).__name__}",
        "numeric_imputer": "median", "categorical_imputer": "most_frequent",
        "classifier": f"{_Classifier.__module__}.{_Classifier.__name__}", "n_estimators": 80, "random_state": 42,
    }
    artifact_sha = _sha(artifact_path)
    entry = SimpleNamespace(
        model_id=MODEL_ID, version="1", artifact_sha256=artifact_sha, feature_names=tuple(FEATURES),
        target="label_success_rs12", artifact_path=str(artifact_path), scope="all_column", threshold="1.2",
        score_semantics="positive_class_probability", allowed_columns=(), stationary_phase=None, mode=None,
    )
    metadata = {"id": MODEL_ID, "scope": "all_column", "available": True, "model_sha256": artifact_sha,
                "feature_names": FEATURES, "target": "label_success_rs12", "threshold": "1.2"}
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    from chrom_platform.adapters.sfc.adapter import _schemas
    input_schema, output_schema = _schemas("full_column")
    schema_hashes = {
        key: hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        for key, value in (("input_schema_sha256", input_schema), ("output_schema_sha256", output_schema))
    }
    ids = ["candidate-cellulose-2", "candidate-oz"]
    input_rows = []
    expected_rows = []
    for candidate_id, conditions, score, rank in zip(ids, CONDITIONS, (0.693154489720161, 0.6723835009484533), (1, 2)):
        input_rows.append({
            "candidate_id": candidate_id, "column": conditions["column"], "stationary_phase": conditions["stationary_phase"],
            "mode": conditions["mode"], "length_mm": conditions["length_mm"], "inner_diameter_mm": conditions["inner_diameter_mm"],
            "particle_size_um": conditions["particle_size_um"], "column_temperature": conditions["column_temperature_c"],
            "flow_rate": conditions["flow_rate_ml_min"],
        })
        expected_rows.append({"candidate_id": candidate_id, "conditions": conditions, "expected_probability": score, "author_rank": rank})
    package_names = ("numpy", "pandas", "joblib", "mordred", "scikit-learn", "rdkit", "pydantic")
    packages = {name: importlib.metadata.version(name) for name in package_names}
    fixture = {
        "fixture_status": "AUTHOR_REFERENCE", "fixture_version": "sfc-rs12-full-column-reference-v2",
        "reference_kind": "author_v1.1_implementation_software_reference",
        "model": {"id": MODEL_ID, "version": "1", "scope": "all_column", "target": "label_success_rs12", "threshold": "1.2",
                  "score_semantics": "positive_class_probability", "artifact_sha256": artifact_sha,
                  "registry_sha256": _sha(registry_path), "metadata_sha256": _sha(metadata_path), "feature_count": 66,
                  "feature_names": FEATURES, "feature_names_sha256": _feature_digest(FEATURES), **schema_hashes,
                  "preprocessing": preprocessing},
        "environment": {"python_version": platform.python_version(), "package_versions": packages},
        "candidate_source": {"platform_catalog_path": str(platform_catalog_path.resolve()), "platform_catalog_sha256": _sha(platform_catalog_path),
                             "author_catalog_path": str(author_catalog_path.resolve()), "author_catalog_sha256": _sha(author_catalog_path),
                             "candidate_binding_policy": "reference_local_id_matched_by_complete_observed_tuple"},
        "source_pins": [
            {"role": "registry", "path": str(registry_path.resolve()), "sha256": _sha(registry_path)},
            {"role": "metadata", "path": str(metadata_path.resolve()), "sha256": _sha(metadata_path)},
            {"role": "artifact", "path": str(artifact_path.resolve()), "sha256": artifact_sha},
            {"role": "author_inference_source", "path": str(author_inference_path.resolve()), "sha256": _sha(author_inference_path)},
            {"role": "author_reference_output", "path": str(author_output_path.resolve()), "sha256": _sha(author_output_path)},
        ],
        "input": {"schema_version": "1.1", "tool": "full_column", "structure_smiles": {"a": "C[C@H](O)F", "b": "C[C@@H](O)F", "modifier": "CO"},
                  "modifier_total_fraction": 0.2, "modifier_components": [{"structure_ref": "modifier", "fraction": 1.0}],
                  "candidate_scope_id": "author-rs12-two-row", "top_k": 2, "candidates": input_rows},
        "author_reference": {"candidate_results": expected_rows, "expected_warnings": WARNINGS,
                              "observed_author_feature_nan_columns": [item.split(":", 1)[1] for item in WARNINGS],
                              "warning_basis": {"source_role": "author_inference_source", "source_sha256": _sha(author_inference_path),
                                                "result_role": "author_reference_output", "result_sha256": _sha(author_output_path),
                                                "observed_nan_features": [item.split(":", 1)[1] for item in WARNINGS],
                                                "explanation": "Pinned fake author output records these missing feature columns."},
                              "tolerance": {"absolute": 1e-12, "relative": 1e-12,
                                            "rule": "Python math.isclose(actual, reference, abs_tol=1e-12, rel_tol=1e-12)"}},
    }
    fixture_path = tmp_path / "reference.json"
    fixture_path.write_text(json.dumps(fixture, allow_nan=False), encoding="utf-8")
    candidates = [_candidate(candidate_id, conditions) for candidate_id, conditions in zip(NATIVE_CANDIDATE_IDS, CONDITIONS)]
    return fixture, _Registry(registry_path, artifact_path), _Catalog(platform_catalog_path, candidates), entry, str(fixture_path)


def _persist_fixture(path: str, fixture: dict) -> str:
    target = Path(path)
    target.write_text(json.dumps(fixture, allow_nan=True), encoding="utf-8")
    return _sha(target)


def _guard_model_loads(monkeypatch):
    attempts = {"assembly": 0, "runtime": 0}

    def blocked(layer):
        def fail(*args, **kwargs):
            attempts[layer] += 1
            raise AssertionError(f"unexpected {layer} model load")
        return fail

    monkeypatch.setattr(assembly, "load_sfc_model", blocked("assembly"))
    monkeypatch.setattr("chrom_platform.adapters.sfc.runtime.load_sfc_model", blocked("runtime"))
    return attempts


def test_rs12_full_two_row_author_reference_gate_uses_exact_fake_runtime(tmp_path, monkeypatch):
    fixture, registry, catalog, entry, path = _fixture(tmp_path)
    pin = _sha(Path(path))
    load_calls = []
    monkeypatch.setattr(assembly, "_require_enantiomer_pair", lambda smiles: None)
    monkeypatch.setattr("chrom_platform.adapters.sfc.runtime.load_sfc_model", lambda *args, **kwargs: (load_calls.append(kwargs) or _Model(), entry))

    predict_calls = []
    preflight_fixtures = []
    original_preflight = assembly._rs12_full_preflight

    def capture_preflight(**kwargs):
        result = original_preflight(**kwargs)
        preflight_fixtures.append(result[1])
        return result

    monkeypatch.setattr(assembly, "_rs12_full_preflight", capture_preflight)

    class FakeRuntime:
        def __init__(self, **kwargs):
            assert kwargs["bound_model_id"] == MODEL_ID
            assert len(kwargs["candidate_resolver"]["author-rs12-two-row"]) == 2
            assert [row["candidate_id"] for row in kwargs["candidate_resolver"]["author-rs12-two-row"]] == REFERENCE_CANDIDATE_IDS
            self.registry = kwargs["registry"]

        def _load_bound_model(self):
            from chrom_platform.adapters.sfc import runtime as runtime_module
            return runtime_module.load_sfc_model(self.registry, MODEL_ID, model_version="1")

        def predict(self, request):
            predict_calls.append(request)
            rows = [
                {"candidate_id": "candidate-cellulose-2", "rank": 1, "separation": 0.693154489720161, "conditions": CONDITIONS[0]},
                {"candidate_id": "candidate-oz", "rank": 2, "separation": 0.6723835009484533, "conditions": CONDITIONS[1]},
            ]
            return rows, WARNINGS, entry

    monkeypatch.setattr("chrom_platform.adapters.sfc.runtime.SfcRuntime", FakeRuntime)
    checks = assembly._verification_result(model_registry=registry, entry=entry, catalog=catalog,
                                           reference_path=path, reference_sha256=pin,
                                           expected_artifact_sha256=entry.artifact_sha256)
    assert all(checks.values())
    assert len(load_calls) == 1
    assert len(predict_calls) == 1 and predict_calls[0].input.top_k == 2
    assert catalog.bound == {"model_id": MODEL_ID, "model_version": "1", "scope": "all_column"}
    assert preflight_fixtures[0]["_native_by_reference_id"] == {
        "candidate-cellulose-2": {"candidate_id": "sfc-candidate-001", "conditions": _candidate("native", CONDITIONS[0]).as_conditions()},
        "candidate-oz": {"candidate_id": "sfc-candidate-002", "conditions": _candidate("native", CONDITIONS[1]).as_conditions()},
    }


@pytest.mark.parametrize("failure", ["predict_exception", "malformed_rows", "warning_mismatch", "swapped_rows"])
def test_rs12_runtime_result_fail_closed(tmp_path, monkeypatch, failure):
    _, registry, catalog, entry, path = _fixture(tmp_path)
    monkeypatch.setattr(assembly, "_require_enantiomer_pair", lambda smiles: None)

    class FakeRuntime:
        def __init__(self, **kwargs):
            pass

        def _load_bound_model(self):
            return _Model(), entry

        def predict(self, request):
            if failure == "predict_exception":
                raise RuntimeError("fake predict failure")
            if failure == "malformed_rows":
                return [{"candidate_id": "candidate-cellulose-2"}], WARNINGS, entry
            rows = [
                {"candidate_id": "candidate-cellulose-2", "rank": 1, "separation": 0.693154489720161, "conditions": CONDITIONS[0]},
                {"candidate_id": "candidate-oz", "rank": 2, "separation": 0.6723835009484533, "conditions": CONDITIONS[1]},
            ]
            if failure == "swapped_rows":
                rows.reverse()
                return rows, WARNINGS, entry
            return rows, WARNINGS[:-1], entry

    monkeypatch.setattr("chrom_platform.adapters.sfc.runtime.SfcRuntime", FakeRuntime)
    checks = assembly._verification_result(model_registry=registry, entry=entry, catalog=catalog,
                                           reference_path=path, reference_sha256=_sha(Path(path)),
                                           expected_artifact_sha256=entry.artifact_sha256)
    assert not all(checks.values())
    assert checks["preprocessing"] is True
    if failure in {"predict_exception", "malformed_rows"}:
        assert checks["golden"] is False and checks["anomaly"] is False
    else:
        assert checks["anomaly"] is False


@pytest.mark.parametrize("mutation", [
    "proposal", "missing_binding_policy", "duplicate_id", "duplicate_tuple", "duplicate_native_id", "duplicate_native_match", "unknown_tuple", "unknown_complete_tuple", "unknown_candidate_id", "native_id_as_alias", "wrong_rank", "wrong_warning", "wrong_warning_basis", "wrong_version", "nan_condition", "nan_score",
])
def test_rs12_full_tampered_reference_fails_before_fake_load(tmp_path, monkeypatch, mutation):
    fixture, registry, catalog, entry, path = _fixture(tmp_path)
    fixture = copy.deepcopy(fixture)
    if mutation == "proposal":
        fixture["fixture_status"] = "PROPOSAL_NOT_VERIFIED"
        fixture["proposal_revision"] = "unverified"
    elif mutation == "missing_binding_policy":
        fixture["candidate_source"].pop("candidate_binding_policy")
    elif mutation == "duplicate_id":
        fixture["input"]["candidates"][1]["candidate_id"] = fixture["input"]["candidates"][0]["candidate_id"]
    elif mutation == "duplicate_tuple":
        fixture["input"]["candidates"][1].update(fixture["input"]["candidates"][0])
        fixture["author_reference"]["candidate_results"][1]["conditions"] = copy.deepcopy(fixture["author_reference"]["candidate_results"][0]["conditions"])
    elif mutation == "unknown_tuple":
        fixture["input"]["candidates"][1]["column"] = "unobserved column"
    elif mutation == "unknown_complete_tuple":
        unknown = copy.deepcopy(fixture["input"]["candidates"][1])
        unknown.update(column="unobserved column", stationary_phase="unobserved phase", mode="unobserved mode",
                       length_mm=999.0, inner_diameter_mm=999.0, particle_size_um=999.0,
                       column_temperature=999.0, flow_rate=999.0)
        fixture["input"]["candidates"][1] = unknown
        fixture["author_reference"]["candidate_results"][1]["conditions"] = {
            "column": unknown["column"], "stationary_phase": unknown["stationary_phase"], "mode": unknown["mode"],
            "length_mm": unknown["length_mm"], "inner_diameter_mm": unknown["inner_diameter_mm"],
            "particle_size_um": unknown["particle_size_um"], "column_temperature_c": unknown["column_temperature"],
            "flow_rate_ml_min": unknown["flow_rate"],
        }
    elif mutation == "unknown_candidate_id":
        fixture["input"]["candidates"][1]["candidate_id"] = "candidate-unobserved"
    elif mutation == "duplicate_native_id":
        catalog.candidates = (catalog.candidates[0], catalog.candidates[0])
    elif mutation == "duplicate_native_match":
        catalog.candidates = (catalog.candidates[0], _candidate("sfc-candidate-003", CONDITIONS[0]))
    elif mutation == "native_id_as_alias":
        fixture["input"]["candidates"][0]["candidate_id"] = NATIVE_CANDIDATE_IDS[0]
        fixture["author_reference"]["candidate_results"][0]["candidate_id"] = NATIVE_CANDIDATE_IDS[0]
    elif mutation == "wrong_rank":
        fixture["author_reference"]["candidate_results"][0]["author_rank"] = 2
    elif mutation == "wrong_warning":
        fixture["author_reference"]["expected_warnings"].pop()
    elif mutation == "wrong_warning_basis":
        fixture["author_reference"]["warning_basis"]["source_sha256"] = "0" * 64
    elif mutation == "wrong_version":
        fixture["model"]["version"] = "2"
    elif mutation == "nan_condition":
        fixture["input"]["candidates"][0]["length_mm"] = float("nan")
    elif mutation == "nan_score":
        fixture["author_reference"]["candidate_results"][0]["expected_probability"] = float("nan")
    pin = _persist_fixture(path, fixture)
    load_attempts = _guard_model_loads(monkeypatch)
    checks = assembly._verification_result(model_registry=registry, entry=entry, catalog=catalog,
                                           reference_path=path, reference_sha256=pin,
                                           expected_artifact_sha256=entry.artifact_sha256)
    assert not all(checks.values())
    assert load_attempts == {"assembly": 0, "runtime": 0}


def test_rs12_full_missing_or_bad_reference_pin_fails_closed_without_load(tmp_path, monkeypatch):
    _, registry, catalog, entry, path = _fixture(tmp_path)
    load_attempts = _guard_model_loads(monkeypatch)
    for reference_path, pin in ((path, None), (path, "0" * 64), (None, "0" * 64)):
        checks = assembly._verification_result(model_registry=registry, entry=entry, catalog=catalog,
                                               reference_path=reference_path, reference_sha256=pin,
                                               expected_artifact_sha256=entry.artifact_sha256)
        assert not any(checks.values())
    assert load_attempts == {"assembly": 0, "runtime": 0}


@pytest.mark.parametrize("case", ["wrong_scope", "rs15_wrong_model"])
def test_reference_cannot_cross_scope_or_rs15_exact_binding(tmp_path, monkeypatch, case):
    _, registry, catalog, entry, path = _fixture(tmp_path)
    if case == "wrong_scope":
        entry.scope = "single_column"
    else:
        entry.model_id = "rs15-extra-trees-s80-seed2024"
    load_attempts = _guard_model_loads(monkeypatch)
    checks = assembly._verification_result(model_registry=registry, entry=entry, catalog=catalog,
                                           reference_path=path, reference_sha256=_sha(Path(path)),
                                           expected_artifact_sha256=entry.artifact_sha256)
    assert not any(checks.values())
    assert load_attempts == {"assembly": 0, "runtime": 0}


def test_other_exact_binding_keeps_single_column_preflight_contract(tmp_path, monkeypatch):
    single = SimpleNamespace(model_id="single-model", version="1", scope="single_column")
    registry = object()
    catalog = object()
    observed = []
    monkeypatch.setattr(assembly, "_single_column_preflight", lambda **kwargs: ({"manifest": False, "schema": False, "artifact": False, "environment": False}, None, None))
    monkeypatch.setattr(assembly, "load_sfc_model", lambda *args, **kwargs: observed.append(kwargs))
    result = assembly._verification_result(model_registry=registry, entry=single, catalog=catalog,
                                          reference_path="ignored", reference_sha256="a" * 64,
                                          expected_artifact_sha256="b" * 64)
    assert set(result) == {"manifest", "schema", "artifact", "environment", "golden", "anomaly"}
    assert not any(result.values())
    assert observed == []
