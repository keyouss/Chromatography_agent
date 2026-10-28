from __future__ import annotations

import math
from pathlib import Path
import hashlib
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from chrom_platform.adapters.sfc.runtime import (
    ALLOWED_NAN_FEATURES,
    FULL_COLUMN_GOLDEN_CONDITIONS,
    SfcAssetUnavailable,
    SfcCandidateCatalog,
    SfcModelRegistry,
    SfcRequestValidationError,
    SfcRuntime,
    safe_float,
    pair_features,
    load_sfc_model,
)
from chrom_platform.adapters.sfc.adapter import SfcAdapter, SfcCatalogSourceProof
from chrom_platform.modules.prediction.adapters import ModelVerificationEvidence
from chrom_platform.modules.prediction.schemas import SfcFullColumnPredictionCreate, SfcSingleColumnPredictionCreate
from chrom_platform.assembly.prediction import _require_enantiomer_pair, _verification_result


MODEL_ROOT = Path(r"D:\Jupyter\SFC\workspace\model_registry\sfc_predictor_v01")
REGISTRY = MODEL_ROOT / "registry.json"
MODEL_ID = "rs12-extra-trees-s80-seed42"
FULL_MODEL_ID = "rs15-extra-trees-s80-seed2024"
CANDIDATE_DB = Path(r"D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\data\result_with_advanced_descriptors.sqlite")


def test_verified_sfc_handle_marks_only_its_exact_registry_model_ready() -> None:
    verified = SimpleNamespace(
        model_id="verified-single", version="1", scope="single_column",
        artifact_sha256="a" * 64, feature_names=tuple(f"f{i}" for i in range(66)),
        target="label", threshold="1.2", score_semantics="positive_class_probability",
        allowed_columns=("AD",), stationary_phase="amylose", mode="coated",
        single_column_slug="amylose-coated", single_column_group="Amylose coated",
    )
    unverified = SimpleNamespace(
        model_id="unverified-single", version="1", scope="single_column",
        artifact_sha256="b" * 64, feature_names=tuple(f"f{i}" for i in range(66)),
        target="label", threshold="1.5", score_semantics="positive_class_probability",
        allowed_columns=("AMY 1",), stationary_phase="amylose", mode="coated",
        single_column_slug="amylose-coated", single_column_group="Amylose coated",
    )

    class Registry:
        entries = (verified, unverified)

        @staticmethod
        def entry(model_id: str, *, version: str):
            return next(item for item in Registry.entries if item.model_id == model_id and item.version == version)

    runtime = SimpleNamespace(
        registry=Registry(), bound_model_id=verified.model_id, bound_model_version=verified.version,
        bound_artifact_sha256=verified.artifact_sha256, business_scope_id="sfc-business",
        allowed_tool="single_column",
    )
    evidence = ModelVerificationEvidence(
        adapter_id="sfc", model_id=verified.model_id, model_version=verified.version,
        business_scope_id="sfc-business", input_schema_version="1.1", output_schema_version="1.0",
        artifact_sha256=verified.artifact_sha256, manifest_verified=True, schema_verified=True,
        artifact_sha_verified=True, environment_verified=True, golden_sample_verified=True,
        anomaly_checks_verified=True,
        schema_by_tool={"single_column": {"input_schema": {}, "output_schema": {}}},
    )

    adapter = SfcAdapter(
        runtime=runtime, model_id=verified.model_id, model_version=verified.version,
        artifact_sha256=verified.artifact_sha256, business_scope_id="sfc-business",
        verification_evidence=evidence,
    )

    health_by_id = {model.id: model.health.status for model in adapter.capability.models}
    assert health_by_id == {"verified-single": "READY", "unverified-single": "UNAVAILABLE"}
    assert adapter.capability.health.status == "READY"


def test_sfc_structure_pair_requires_true_enantiomers() -> None:
    _require_enantiomer_pair(["C[C@H](O)C(=O)O", "C[C@@H](O)C(=O)O"])
    with pytest.raises(ValueError):
        _require_enantiomer_pair(["C[C@H](O)C(=O)O", "C[C@H](O)C(=O)O"])
    with pytest.raises(ValueError):
        _require_enantiomer_pair(["C[C@H](O)C(=O)O", "CC(O)C(=O)O"])


def test_safe_float_preserves_infinity_for_fail_closed_validation() -> None:
    assert math.isinf(safe_float(float("inf")))
    assert math.isinf(safe_float(float("-inf")))
    assert math.isnan(safe_float(float("nan")))


def test_pair_features_do_not_turn_infinite_descriptor_into_allowed_nan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "chrom_platform.adapters.sfc.runtime.molecular_descriptors",
        lambda _smiles: {key: (float("inf") if key == "mdec" else 0.0) for key in (
            "mw", "tpsa", "nrotbs", "hba", "hbd", "logp", "rpsa", "rasa", "mdec", "mats",
        )},
    )
    values = pair_features("a", "b")
    assert all(math.isinf(values[f"mdec_smiles_{suffix}"]) for suffix in (
        "pair_value", "mean", "median", "abs_diff", "range",
    ))


def registry_sha256() -> str:
    return hashlib.sha256(REGISTRY.read_bytes()).hexdigest()


def test_registry_rejects_registry_outside_trusted_model_root(tmp_path: Path) -> None:
    root = tmp_path / "models"
    root.mkdir()
    with pytest.raises(SfcAssetUnavailable, match="MODEL_REGISTRY_OUTSIDE_ROOT"):
        SfcModelRegistry(registry_path=REGISTRY, model_root=root, expected_registry_sha256=registry_sha256())


def test_registry_digest_is_checked_before_json_is_used(tmp_path: Path) -> None:
    if not REGISTRY.is_file():
        pytest.skip("reviewed SFC source asset is unavailable on this machine")
    root = tmp_path / "models"
    root.mkdir()
    copied = root / "registry.json"
    copied.write_text(REGISTRY.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(SfcAssetUnavailable, match="MODEL_REGISTRY_HASH_MISMATCH"):
        SfcModelRegistry(
            registry_path=copied,
            model_root=root,
            expected_registry_sha256=registry_sha256(),
        )


def test_artifact_digest_is_checked_before_joblib_deserialization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if not REGISTRY.is_file():
        pytest.skip("reviewed SFC source asset is unavailable on this machine")
    trusted_root = tmp_path / "trusted-models"
    model_dir = trusted_root / FULL_MODEL_ID
    model_dir.mkdir(parents=True)
    source_entry = SfcModelRegistry(
        registry_path=REGISTRY, model_root=MODEL_ROOT, expected_registry_sha256=registry_sha256(),
    ).entry(FULL_MODEL_ID, version="1")
    source_metadata = MODEL_ROOT / FULL_MODEL_ID / "metadata.json"
    (model_dir / "metadata.json").write_bytes(source_metadata.read_bytes())
    (model_dir / "model.joblib").write_bytes(b"not the registered model")
    registry_path = trusted_root / "registry.json"
    registry_path.write_text(json.dumps({"schema_version": 1, "models": [{
        "id": FULL_MODEL_ID, "scope": "all_column", "target": source_entry.target,
        "threshold": source_entry.threshold, "artifact_sha256": source_entry.artifact_sha256,
        "feature_names": list(source_entry.feature_names),
    }]}), encoding="utf-8")
    registry = SfcModelRegistry(
        registry_path=registry_path, model_root=trusted_root,
        expected_registry_sha256=hashlib.sha256(registry_path.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr("joblib.load", lambda *args, **kwargs: pytest.fail("unverified bytes must not be deserialized"))
    with pytest.raises(SfcAssetUnavailable, match="MODEL_ARTIFACT_HASH_MISMATCH"):
        load_sfc_model(registry, FULL_MODEL_ID, model_version="1")


def test_runtime_uses_registered_feature_order_and_real_model_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    if not REGISTRY.is_file():
        pytest.skip("reviewed SFC source asset is unavailable on this machine")
    registry = SfcModelRegistry(registry_path=REGISTRY, model_root=MODEL_ROOT, expected_registry_sha256=registry_sha256())
    model, entry = load_sfc_model(registry, MODEL_ID)
    assert entry.model_id == MODEL_ID
    assert len(entry.feature_names) == 66
    assert getattr(model, "n_features_in_", None) == 66
    reference_path = (
        Path(__file__).resolve().parents[2]
        / "testing" / "fixtures" / "priority-launch" / "sfc-rs12-full-column-reference-v3.json"
    ).resolve()
    assert reference_path.is_file()

    runtime = SfcRuntime(
        registry=registry,
        bound_model_id=MODEL_ID,
        bound_model_version="1",
        bound_artifact_sha256=entry.artifact_sha256,
        business_scope_id="scope",
        structure_resolver={"a": "C[C@H](O)F", "b": "C[C@@H](O)F", "m": "CO"},
        candidate_resolver={
            "scope": [
                {
                    "candidate_id": "candidate-1",
                    "column": "AD",
                    "stationary_phase": "amylose tris (3,5-dimethylphenylcarbamate)",
                    "mode": "coated",
                    "length_mm": 150.0,
                    "inner_diameter_mm": 4.6,
                    "particle_size_um": 5.0,
                    "column_temperature": 35.0,
                    "flow_rate": 2.5,
                }
            ]
        },
    )
    adapter = SfcAdapter(
        runtime=runtime, model_id=MODEL_ID, model_version="1",
        artifact_sha256=entry.artifact_sha256, business_scope_id="scope",
        verification_evidence=ModelVerificationEvidence(
            adapter_id="sfc", model_id=MODEL_ID, model_version="1",
            business_scope_id="scope", input_schema_version="1.1", output_schema_version="1.0",
            artifact_sha256=entry.artifact_sha256, manifest_verified=True, schema_verified=True,
            artifact_sha_verified=True, environment_verified=True, golden_sample_verified=True,
            anomaly_checks_verified=True,
            schema_by_tool={"full_column": {"input_schema": {}, "output_schema": {}}},
        ),
        candidate_catalog=SfcCandidateCatalog(CANDIDATE_DB),
        catalog_source_proof=SfcCatalogSourceProof(
            model_id=MODEL_ID, model_version="1", artifact_sha256=entry.artifact_sha256,
            business_scope_id="scope", input_schema_version="1.1", output_schema_version="1.0",
            catalog_path=str(CANDIDATE_DB.resolve()), catalog_sha256=hashlib.sha256(CANDIDATE_DB.read_bytes()).hexdigest(),
            reference_path=str(reference_path),
            reference_sha256=hashlib.sha256(reference_path.read_bytes()).hexdigest(),
            fixture_version="sfc-rs12-full-column-reference-v2",
            check_flags=tuple((name, True) for name in ("manifest", "schema", "artifact", "environment", "golden", "anomaly", "preprocessing")),
        ),
    )
    assert [tool.id for tool in adapter.capability.tools] == ["full_column"]
    full_schema = adapter.capability.tools[0].input_schema
    assert full_schema["properties"]["input"] == {"$ref": "#/$defs/SfcFullColumnInput"}
    full_input = full_schema["$defs"]["SfcFullColumnInput"]
    assert set(full_input["required"]) == {
        "modifier_total_fraction", "modifier_components", "candidate_scope_id", "top_k"
    }
    assert "column_id" not in full_input["properties"]
    assert adapter.capability.tools[0].output_schema["properties"]["rows"]
    assert adapter.capability.tools[0].model_constraints["feature_count"] == 66
    assert adapter.capability.tools[0].model_constraints["feature_value_policy"]["allowed_nan_features"] == list(ALLOWED_NAN_FEATURES)
    assert adapter.capability.tools[0].model_constraints["applicability"] == "observed_complete_candidate_tuples"
    assert adapter.capability.models[0].scope == "scope"
    request = SfcFullColumnPredictionCreate.model_validate(
        {
            "project_id": "project-a",
            "schema_version": "1.1",
            "model_selection": {"mode": "exact", "model_id": MODEL_ID, "model_version": "1"},
            "structure_refs": ["a", "b"],
            "chromatography_type": "sfc",
            "tool": "full_column",
            "input": {
                "modifier_total_fraction": 0.2,
                "modifier_components": [{"structure_ref": "m", "fraction": 1.0}],
                "candidate_scope_id": "scope",
                "top_k": 1,
            },
        }
    )
    rows, warnings, used = runtime.predict(request)
    registry._champions["scope"] = "rs15-extra-trees-s80-seed2024"
    assert registry.resolve_champion("scope").model_id != MODEL_ID
    repeat_rows, repeat_warnings, repeat_used = runtime.predict(request)
    assert used.model_id == MODEL_ID
    assert warnings == [
        "FEATURE_VALUE_MISSING:mdec_modi",
        "FEATURE_VALUE_MISSING:mdec_smiles_pair_value",
        "FEATURE_VALUE_MISSING:mdec_smiles_mean",
        "FEATURE_VALUE_MISSING:mdec_smiles_median",
        "FEATURE_VALUE_MISSING:mdec_smiles_abs_diff",
        "FEATURE_VALUE_MISSING:mdec_smiles_range",
    ]
    assert repeat_rows == rows
    assert repeat_warnings == warnings
    assert repeat_used.model_id == used.model_id
    assert rows[0]["candidate_id"] == "candidate-1"
    assert 0.0 <= rows[0]["separation"] <= 1.0
    runtime.structure_resolver = lambda _ref: pytest.fail("queued SFC execution must not resolve structures")
    snapshot_rows, snapshot_warnings, _ = runtime.predict(
        request,
        normalized_structures=["C[C@H](O)F", "C[C@@H](O)F"],
        normalized_modifier_structures={"m": "CO"},
        require_snapshots=True,
    )
    assert snapshot_rows == rows
    assert snapshot_warnings == warnings
    with pytest.raises(SfcAssetUnavailable, match="SFC_INPUT_SNAPSHOT_MISSING"):
        runtime.predict(
            request,
            normalized_structures=["C[C@H](O)F", "C[C@@H](O)F"],
            require_snapshots=True,
        )
    champion_payload = request.model_dump(mode="python") | {
        "model_selection": {"mode": "champion", "scope_id": "scope"},
    }
    with pytest.raises(ValidationError):
        SfcFullColumnPredictionCreate.model_validate(champion_payload)
    wrong_scope_champion_payload = request.model_dump(mode="python") | {
        "model_selection": {"mode": "champion", "scope_id": "wrong-scope"},
    }
    with pytest.raises(ValidationError):
        SfcFullColumnPredictionCreate.model_validate(wrong_scope_champion_payload)


def test_bound_runtime_rejects_a_different_exact_model_without_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    if not REGISTRY.is_file():
        pytest.skip("reviewed SFC source asset is unavailable on this machine")
    registry = SfcModelRegistry(registry_path=REGISTRY, model_root=MODEL_ROOT, expected_registry_sha256=registry_sha256())
    runtime = SfcRuntime(
        registry=registry,
        bound_model_id=MODEL_ID,
        bound_model_version="1",
        bound_artifact_sha256=registry.entry(MODEL_ID).artifact_sha256,
        business_scope_id="scope",
        structure_resolver={},
        candidate_resolver={},
    )
    request = SfcFullColumnPredictionCreate.model_validate(
        {
            "project_id": "project-a",
            "schema_version": "1.1",
            "model_selection": {"mode": "exact", "model_id": "rs15-extra-trees-s80-seed2024", "model_version": "1"},
            "structure_refs": ["a", "b"],
            "chromatography_type": "sfc",
            "tool": "full_column",
            "input": {
                "modifier_total_fraction": 0.2,
                "modifier_components": [{"structure_ref": "m", "fraction": 1.0}],
                "candidate_scope_id": "scope",
                "top_k": 1,
            },
        }
    )
    monkeypatch.setattr("chrom_platform.adapters.sfc.runtime.load_sfc_model", lambda *args, **kwargs: pytest.fail("model must not load"))
    with pytest.raises(SfcAssetUnavailable, match="MODEL_SELECTION_MISMATCH"):
        runtime.predict(request)


def test_single_column_scope_only_accepts_single_column_tool() -> None:
    if not REGISTRY.is_file():
        pytest.skip("reviewed SFC source asset is unavailable on this machine")
    model_id = "sc-rs12-amylose-dmpc-coated"
    registry = SfcModelRegistry(registry_path=REGISTRY, model_root=MODEL_ROOT, expected_registry_sha256=registry_sha256())
    entry = registry.entry(model_id)
    runtime = SfcRuntime(
        registry=registry,
        bound_model_id=model_id,
        bound_model_version="1",
        bound_artifact_sha256=entry.artifact_sha256,
        business_scope_id="single-business-scope",
        structure_resolver={"a": "C[C@H](O)F", "b": "C[C@@H](O)F", "m": "CO"},
        candidate_resolver={
            "scope": [{
                "candidate_id": "candidate-1",
                "column": "AD",
                "stationary_phase": "amylose tris (3,5-dimethylphenylcarbamate)",
                "mode": "coated", "length_mm": 150.0, "inner_diameter_mm": 4.6,
                "particle_size_um": 5.0, "column_temperature": 35.0, "flow_rate": 2.0,
            }]
        },
    )
    assert runtime.allowed_tool == "single_column"
    adapter = SfcAdapter(
        runtime=runtime, model_id=model_id, model_version="1",
        artifact_sha256=entry.artifact_sha256, business_scope_id="single-business-scope",
    )
    assert [tool.id for tool in adapter.capability.tools] == ["single_column"]
    single_schema = adapter.capability.tools[0].input_schema
    assert single_schema["properties"]["input"] == {"$ref": "#/$defs/SfcSingleColumnInput"}
    assert "column_id" in single_schema["$defs"]["SfcSingleColumnInput"]["required"]
    assert adapter.capability.models[0].scope == "single-business-scope"
    request = SfcSingleColumnPredictionCreate.model_validate({
        "project_id": "project-a", "schema_version": "1.1",
        "model_selection": {"mode": "exact", "model_id": model_id, "model_version": "1"},
        "structure_refs": ["a", "b"], "chromatography_type": "sfc", "tool": "single_column",
        "input": {
            "modifier_total_fraction": 0.2,
            "modifier_components": [{"structure_ref": "m", "fraction": 1.0}],
            "candidate_scope_id": "scope", "top_k": 1,
                "column_id": "AD",
        },
    })
    rows, _, used = runtime.predict(request)
    repeat_rows, _, repeat_used = runtime.predict(request)
    assert used.model_id == model_id
    assert repeat_rows == rows
    assert repeat_used.model_id == used.model_id
    assert rows[0]["candidate_id"] == "candidate-1"


def test_runtime_rejects_nonfinite_model_output(monkeypatch: pytest.MonkeyPatch) -> None:
    if not REGISTRY.is_file():
        pytest.skip("reviewed SFC source asset is unavailable on this machine")
    registry = SfcModelRegistry(registry_path=REGISTRY, model_root=MODEL_ROOT, expected_registry_sha256=registry_sha256())
    entry = registry.entry(MODEL_ID)
    runtime = SfcRuntime(
        registry=registry,
        bound_model_id=MODEL_ID,
        bound_model_version="1",
        bound_artifact_sha256=entry.artifact_sha256,
        business_scope_id="scope",
        structure_resolver={"a": "C[C@H](O)F", "b": "C[C@@H](O)F", "m": "CO"},
        candidate_resolver={"scope": [{
            "candidate_id": "candidate-1",
            "column": "AD",
            "stationary_phase": "amylose tris (3,5-dimethylphenylcarbamate)",
            "mode": "coated",
            "length_mm": 150.0,
            "inner_diameter_mm": 4.6,
            "particle_size_um": 5.0,
            "column_temperature": 35.0,
                "flow_rate": 2.5,
        }]},
    )

    class InvalidModel:
        classes_ = (0, 1)

        def predict_proba(self, frame):
            return [[float("nan"), 1.0]]

    monkeypatch.setattr(
        "chrom_platform.adapters.sfc.runtime.load_sfc_model",
        lambda *args, **kwargs: (InvalidModel(), entry),
    )
    request = SfcFullColumnPredictionCreate.model_validate({
        "project_id": "project-a",
        "schema_version": "1.1",
        "model_selection": {"mode": "exact", "model_id": MODEL_ID, "model_version": "1"},
        "structure_refs": ["a", "b"],
        "chromatography_type": "sfc",
        "tool": "full_column",
        "input": {
            "modifier_total_fraction": 0.2,
            "modifier_components": [{"structure_ref": "m", "fraction": 1.0}],
            "candidate_scope_id": "scope",
            "top_k": 1,
        },
    })
    with pytest.raises(SfcAssetUnavailable, match="MODEL_OUTPUT_INVALID"):
        runtime.predict(request)


def test_full_column_golden_is_bound_to_author_fixture_and_current_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    if not REGISTRY.is_file() or not CANDIDATE_DB.is_file():
        pytest.skip("reviewed SFC full-column source assets are unavailable on this machine")
    registry = SfcModelRegistry(registry_path=REGISTRY, model_root=MODEL_ROOT, expected_registry_sha256=registry_sha256())
    entry = registry.entry(FULL_MODEL_ID, version="1")
    catalog = SfcCandidateCatalog(CANDIDATE_DB)
    # The historical RS15 golden cannot satisfy the current RS12 full-column proof gate.
    # Keep its identity readable, but assert the exact checks that block READY.
    flags = _verification_result(
        model_registry=registry, entry=entry, catalog=catalog,
        reference_path=Path(__file__).resolve().parents[2] / "testing" / "fixtures" / "priority-launch" / "sfc-full-reference.json",
        reference_sha256="b317a88d05d1aedb165adbcf1353b0775bb5f5e171e43a763db2a962e5c9e246",
        expected_artifact_sha256=entry.artifact_sha256,
    )
    assert flags == {
        "manifest": True, "schema": False, "artifact": True,
        "environment": False, "golden": False, "anomaly": False,
    }

    monkeypatch.setattr("chrom_platform.assembly.prediction.SFC_FULL_REFERENCE_SHA256", "0" * 64)
    rejected = _verification_result(model_registry=registry, entry=entry, catalog=catalog)
    assert rejected["golden"] is False


def test_full_column_preflight_accepts_general_valid_input_without_inference() -> None:
    entry = SimpleNamespace(
        scope="all_column", artifact_sha256="a" * 64, allowed_columns=(),
        stationary_phase=FULL_COLUMN_GOLDEN_CONDITIONS["stationary_phase"], mode="coated",
    )

    class Registry:
        def entry(self, _model_id, *, version):
            assert version == "1"
            return entry

    candidate = {
        "candidate_id": "observed-non-golden",
        "column": FULL_COLUMN_GOLDEN_CONDITIONS["column"],
        "stationary_phase": FULL_COLUMN_GOLDEN_CONDITIONS["stationary_phase"],
        "mode": FULL_COLUMN_GOLDEN_CONDITIONS["mode"],
        "length_mm": FULL_COLUMN_GOLDEN_CONDITIONS["length_mm"],
        "inner_diameter_mm": FULL_COLUMN_GOLDEN_CONDITIONS["inner_diameter_mm"],
        "particle_size_um": FULL_COLUMN_GOLDEN_CONDITIONS["particle_size_um"],
        "column_temperature": FULL_COLUMN_GOLDEN_CONDITIONS["column_temperature_c"],
        "flow_rate": 2.0,
    }
    runtime = SfcRuntime(
        registry=Registry(), bound_model_id="model-a", bound_model_version="1",
        bound_artifact_sha256="a" * 64, business_scope_id="scope-a",
        structure_resolver={"modifier": "CO"},
        candidate_resolver={"default": [candidate]},
    )
    request = SfcFullColumnPredictionCreate.model_validate({
        "project_id": "project-a", "schema_version": "1.1",
        "model_selection": {"mode": "exact", "model_id": "model-a", "model_version": "1"},
        "structure_refs": ["a", "b"], "chromatography_type": "sfc", "tool": "full_column",
        "input": {
            "modifier_total_fraction": 0.2,
            "modifier_components": [{"structure_ref": "modifier", "fraction": 1.0}],
            "candidate_scope_id": "default", "top_k": 1,
        },
    })
    authorized = {"modifier": "CO"}
    assert runtime.preflight(request, authorized_modifier_structures=authorized) is None

    def with_input(**updates):
        return SfcFullColumnPredictionCreate.model_validate(
            request.model_dump(mode="python")
            | {"input": request.input.model_dump(mode="python") | updates}
        )

    with pytest.raises(SfcRequestValidationError):
        runtime.preflight(with_input(candidate_scope_id="missing"), authorized_modifier_structures=authorized)
    assert runtime.preflight(
        with_input(modifier_total_fraction=0.3), authorized_modifier_structures=authorized
    ) is None
    multi_modifier = with_input(modifier_components=[
        {"structure_ref": "modifier", "fraction": 0.7},
        {"structure_ref": "modifier-2", "fraction": 0.3},
    ])
    assert runtime.preflight(
        multi_modifier,
        authorized_modifier_structures={"modifier": "CO", "modifier-2": "CCO"},
    ) is None

    nonfinite = request.model_copy(update={
        "input": request.input.model_copy(update={"modifier_total_fraction": float("inf")}),
    })
    with pytest.raises(SfcRequestValidationError, match="SFC_INPUT_NONFINITE"):
        runtime.preflight(nonfinite, authorized_modifier_structures=authorized)

    class BrokenCatalog:
        def __call__(self, _scope_id, *, project_id, model_id, model_version):
            raise KeyError("catalog backend unavailable")

    dependency_runtime = SfcRuntime(
        registry=Registry(), bound_model_id="model-a", bound_model_version="1",
        bound_artifact_sha256="a" * 64, business_scope_id="scope-a",
        structure_resolver={"modifier": "CO"}, candidate_resolver=BrokenCatalog(),
    )
    with pytest.raises(KeyError, match="catalog backend unavailable"):
        dependency_runtime.preflight(request, authorized_modifier_structures=authorized)


def test_full_column_applicability_keeps_observed_domain_and_generalization_warnings() -> None:
    applicability = SfcAdapter._applicability(
        SimpleNamespace(scope="all_column", model_id="full-model"), [],
    )
    assert "observed complete candidate tuples" in applicability.supported_scope
    assert {item.code for item in applicability.warnings} == {
        "SFC_OBSERVED_CANDIDATE_DOMAIN", "SFC_GENERALIZATION_NOT_ESTABLISHED",
    }


def test_full_column_schema_drift_blocks_model_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    if not REGISTRY.is_file() or not CANDIDATE_DB.is_file():
        pytest.skip("reviewed SFC full-column source assets are unavailable on this machine")
    registry = SfcModelRegistry(registry_path=REGISTRY, model_root=MODEL_ROOT, expected_registry_sha256=registry_sha256())
    entry = registry.entry(FULL_MODEL_ID, version="1")
    catalog = SfcCandidateCatalog(CANDIDATE_DB)
    monkeypatch.setattr("chrom_platform.adapters.sfc.adapter._schemas", lambda tool: ({"type": "object"}, {"type": "object"}))
    rejected = _verification_result(model_registry=registry, entry=entry, catalog=catalog)
    assert rejected["schema"] is False
    assert rejected["environment"] is False
    assert rejected["golden"] is False


@pytest.mark.parametrize(("feature", "value"), [("logp_modi", float("nan")), ("mdec_modi", float("inf"))])
def test_runtime_fails_closed_for_unverified_missing_or_infinite_features(
    monkeypatch: pytest.MonkeyPatch, feature: str, value: float,
) -> None:
    if not REGISTRY.is_file():
        pytest.skip("reviewed SFC source asset is unavailable on this machine")
    import pandas as pd

    registry = SfcModelRegistry(registry_path=REGISTRY, model_root=MODEL_ROOT, expected_registry_sha256=registry_sha256())
    entry = registry.entry(FULL_MODEL_ID, version="1")
    runtime = SfcRuntime(
        registry=registry, bound_model_id=FULL_MODEL_ID, bound_model_version="1",
        bound_artifact_sha256=entry.artifact_sha256, business_scope_id="scope",
        structure_resolver={"a": "a", "b": "b", "m": "CO"},
        candidate_resolver={"scope": [{
            "candidate_id": "candidate-1", "column": "AD",
            "stationary_phase": "amylose tris (3,5-dimethylphenylcarbamate)", "mode": "coated",
            "length_mm": 150.0, "inner_diameter_mm": 4.6, "particle_size_um": 5.0,
            "column_temperature": 35.0, "flow_rate": 2.5,
        }]},
    )

    class MustNotPredict:
        def predict_proba(self, frame):
            pytest.fail("invalid features must be rejected before model inference")

    columns = {name: 0.0 for name in entry.feature_names}
    columns.update({"column": "AD", "stationary_phase": "amylose tris (3,5-dimethylphenylcarbamate)", "mode": "coated"})
    columns[feature] = value
    monkeypatch.setattr("chrom_platform.adapters.sfc.runtime.candidate_frame", lambda *args, **kwargs: pd.DataFrame([columns]))
    monkeypatch.setattr("chrom_platform.adapters.sfc.runtime.load_sfc_model", lambda *args, **kwargs: (MustNotPredict(), entry))
    request = SfcFullColumnPredictionCreate.model_validate({
        "project_id": "project-a", "schema_version": "1.1",
        "model_selection": {"mode": "exact", "model_id": FULL_MODEL_ID, "model_version": "1"},
        "structure_refs": ["a", "b"], "chromatography_type": "sfc", "tool": "full_column",
        "input": {
            "modifier_total_fraction": 0.2,
            "modifier_components": [{"structure_ref": "m", "fraction": 1.0}],
            "candidate_scope_id": "scope", "top_k": 1,
        },
    })
    expected_error = "MODEL_FEATURE_INFINITE" if value == float("inf") else "MODEL_FEATURE_NAN_UNEXPECTED"
    with pytest.raises(SfcAssetUnavailable, match=expected_error):
        runtime.predict(request)
