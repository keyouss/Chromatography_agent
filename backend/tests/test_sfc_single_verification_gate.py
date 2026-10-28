from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import chrom_platform.assembly.prediction as prediction
from chrom_platform.adapters.sfc.runtime import SfcCandidate, SfcRuntime


FIXTURE = Path(prediction.SFC_SINGLE_REFERENCE_PATH)
PINNED = json.loads(FIXTURE.read_text(encoding="utf-8"))
EXPECTED_CONDITIONS = PINNED["input"]["conditions"]


class FakeCatalog:
    DEFAULT_SCOPE_ID = "sfc_conditions_default"

    def __init__(self, path: Path, candidates: list[SfcCandidate]) -> None:
        self.path = path.resolve()
        self.candidates = candidates

    def resolve(self, _scope_id: str) -> list[SfcCandidate]:
        return list(self.candidates)


def _candidate(**updates) -> SfcCandidate:
    conditions = dict(EXPECTED_CONDITIONS)
    conditions.update(updates)
    return SfcCandidate(
        candidate_id="fixture-catalog-row",
        column=conditions["column"],
        stationary_phase=conditions["stationary_phase"],
        mode=conditions["mode"],
        length_mm=float(conditions["length_mm"]),
        inner_diameter_mm=float(conditions["inner_diameter_mm"]),
        particle_size_um=float(conditions["particle_size_um"]),
        column_temperature=float(conditions["column_temperature_c"]),
        flow_rate=float(conditions["flow_rate_ml_min"]),
    )


def _setup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, candidates: list[SfcCandidate] | None = None):
    registry_path = tmp_path / "registry.json"
    artifact_path = tmp_path / "model.joblib"
    metadata_path = tmp_path / "metadata.json"
    catalog_path = tmp_path / "conditions.sqlite"
    registry_path.write_text("{}", encoding="utf-8")
    artifact_path.write_bytes(b"stub model artifact; never loaded")
    metadata_path.write_text("{}", encoding="utf-8")
    catalog_path.write_bytes(b"stub catalog identity; never opened")
    entry = SimpleNamespace(
        model_id=PINNED["model"]["id"],
        version=PINNED["model"]["version"],
        scope=PINNED["model"]["scope"],
        target=PINNED["model"]["target"],
        threshold=PINNED["model"]["threshold"],
        artifact_sha256=PINNED["model"]["artifact_sha256"],
        feature_names=tuple(PINNED["model"]["feature_names"]),
        allowed_columns=(EXPECTED_CONDITIONS["column"],),
        stationary_phase=EXPECTED_CONDITIONS["stationary_phase"],
        mode=EXPECTED_CONDITIONS["mode"],
    )

    class FakeRegistry:
        def __init__(self) -> None:
            self.registry_path = registry_path

        def artifact_for(self, _entry):
            return artifact_path

        def entry(self, _model_id, *, version):
            assert version == entry.version
            return entry

    registry = FakeRegistry()
    catalog = FakeCatalog(catalog_path, list(candidates if candidates is not None else [_candidate()]))
    actual_sha = prediction._sha256_file
    fixed = {
        registry_path.resolve(): PINNED["model"]["registry_sha256"],
        artifact_path.resolve(): PINNED["model"]["artifact_sha256"],
        metadata_path.resolve(): PINNED["model"]["metadata_sha256"],
        catalog_path.resolve(): PINNED["candidate_source"]["runtime_catalog_sha256"],
    }

    def sha256(path: Path) -> str:
        resolved = Path(path).resolve()
        if resolved == Path(prediction.SFC_SINGLE_REFERENCE_PATH).resolve():
            return actual_sha(resolved)
        return fixed.get(resolved, "0" * 64)

    monkeypatch.setattr(prediction, "_sha256_file", sha256)
    monkeypatch.setattr(prediction, "_env", lambda name: str(catalog.path) if name == "CHROM_SFC_CANDIDATE_DB_PATH" else None)
    monkeypatch.setattr(prediction.platform, "python_version", lambda: PINNED["environment"]["python_version"])
    monkeypatch.setattr(prediction.importlib.metadata, "version", lambda name: PINNED["environment"]["package_versions"][name])
    return registry, entry, catalog, artifact_path


def _spy_forbidden_execution(monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], list[str]]:
    loader_calls: list[str] = []
    forward_calls: list[str] = []
    monkeypatch.setattr(prediction, "load_sfc_model", lambda *a, **k: loader_calls.append("load"))
    monkeypatch.setattr(SfcRuntime, "predict", lambda *_a, **_k: forward_calls.append("predict"))
    return loader_calls, forward_calls


def test_single_preflight_rejects_fixture_drift_before_model_load(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry, entry, catalog, _ = _setup(monkeypatch, tmp_path)
    tampered = tmp_path / "tampered-reference.json"
    tampered.write_bytes(FIXTURE.read_bytes() + b" ")
    monkeypatch.setattr(prediction, "SFC_SINGLE_REFERENCE_PATH", tampered)
    loader_calls, forward_calls = _spy_forbidden_execution(monkeypatch)

    flags = prediction._verification_result(model_registry=registry, entry=entry, catalog=catalog)

    assert flags["manifest"] is False
    assert flags["environment"] is False
    assert flags["golden"] is False
    assert loader_calls == []
    assert forward_calls == []


def test_single_preflight_rejects_wrong_fixture_schema_before_model_load(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry, entry, catalog, _ = _setup(monkeypatch, tmp_path)
    tampered = tmp_path / "wrong-schema-reference.json"
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fixture["schema"]["required_condition_keys"].remove("flow_rate_ml_min")
    tampered.write_text(json.dumps(fixture), encoding="utf-8")
    monkeypatch.setattr(prediction, "SFC_SINGLE_REFERENCE_PATH", tampered)
    monkeypatch.setattr(prediction, "SFC_SINGLE_REFERENCE_SHA256", prediction._sha256_file(tampered))
    loader_calls, forward_calls = _spy_forbidden_execution(monkeypatch)

    flags = prediction._verification_result(model_registry=registry, entry=entry, catalog=catalog)

    assert flags["schema"] is False
    assert flags["golden"] is False
    assert loader_calls == []
    assert forward_calls == []


def test_single_preflight_rejects_missing_profile_dependency_before_model_load(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry, entry, catalog, _ = _setup(monkeypatch, tmp_path)

    def missing_sklearn(name: str) -> str:
        if name == "scikit-learn":
            raise prediction.importlib.metadata.PackageNotFoundError(name)
        return PINNED["environment"]["package_versions"][name]

    monkeypatch.setattr(prediction.importlib.metadata, "version", missing_sklearn)
    loader_calls, forward_calls = _spy_forbidden_execution(monkeypatch)

    flags = prediction._verification_result(model_registry=registry, entry=entry, catalog=catalog)

    assert flags["environment"] is False
    assert flags["golden"] is False
    assert loader_calls == []
    assert forward_calls == []


def test_single_preflight_rejects_fixture_condition_outside_filtered_catalog(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry, entry, catalog, _ = _setup(monkeypatch, tmp_path, candidates=[_candidate(flow_rate_ml_min=2.0)])
    loader_calls, forward_calls = _spy_forbidden_execution(monkeypatch)

    flags = prediction._verification_result(model_registry=registry, entry=entry, catalog=catalog)

    assert flags["environment"] is False
    assert flags["golden"] is False
    assert loader_calls == []
    assert forward_calls == []


def test_single_preflight_rejects_catalog_source_not_selected_by_runtime_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry, entry, catalog, _ = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(prediction, "_env", lambda _name: str(tmp_path / "different.sqlite"))
    loader_calls, forward_calls = _spy_forbidden_execution(monkeypatch)

    flags = prediction._verification_result(model_registry=registry, entry=entry, catalog=catalog)

    assert flags["environment"] is False
    assert flags["golden"] is False
    assert loader_calls == []
    assert forward_calls == []


def test_single_preflight_rejects_artifact_drift_before_model_load(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry, entry, catalog, artifact_path = _setup(monkeypatch, tmp_path)
    current_sha = prediction._sha256_file

    def changed_artifact_sha(path: Path) -> str:
        if Path(path).resolve() == artifact_path.resolve():
            return "0" * 64
        return current_sha(path)

    monkeypatch.setattr(prediction, "_sha256_file", changed_artifact_sha)
    loader_calls, forward_calls = _spy_forbidden_execution(monkeypatch)

    flags = prediction._verification_result(model_registry=registry, entry=entry, catalog=catalog)

    assert flags["artifact"] is False
    assert flags["golden"] is False
    assert loader_calls == []
    assert forward_calls == []


def test_single_preflight_rejects_wrong_registry_model_binding_before_model_load(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry, entry, catalog, _ = _setup(monkeypatch, tmp_path)
    entry.model_id = "different-model"
    loader_calls, forward_calls = _spy_forbidden_execution(monkeypatch)

    flags = prediction._verification_result(model_registry=registry, entry=entry, catalog=catalog)

    assert flags["manifest"] is False
    assert flags["golden"] is False
    assert loader_calls == []
    assert forward_calls == []


def test_single_2p5_reference_passes_with_stubbed_loader_and_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry, entry, catalog, _ = _setup(monkeypatch, tmp_path)
    model = SimpleNamespace(feature_names_in_=entry.feature_names, n_features_in_=66)
    load_calls = []
    runtime_calls = []

    def stub_loader(*_args, **_kwargs):
        load_calls.append("stubbed-loader")
        return model, entry

    def stub_predict(runtime, request):
        runtime_calls.append("stubbed-runtime")
        candidates = runtime.candidate_resolver["golden"]
        assert len(candidates) == 1
        assert candidates[0].as_conditions() == EXPECTED_CONDITIONS
        assert request.tool == "single_column"
        assert request.input.column_id == EXPECTED_CONDITIONS["column"]
        return [{"separation": PINNED["expected_score"]}], list(PINNED["expected_warnings"]), entry

    monkeypatch.setattr(prediction, "load_sfc_model", stub_loader)
    monkeypatch.setattr(prediction, "_require_enantiomer_pair", lambda _pair: None)
    monkeypatch.setattr(SfcRuntime, "predict", stub_predict)

    flags = prediction._verification_result(model_registry=registry, entry=entry, catalog=catalog)

    assert flags == {
        "manifest": True,
        "schema": True,
        "artifact": True,
        "environment": True,
        "golden": True,
        "anomaly": True,
    }
    assert load_calls == ["stubbed-loader"]
    assert runtime_calls == ["stubbed-runtime"]


def test_single_anomaly_rejects_any_warning_beyond_fixed_mdec_policy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry, entry, catalog, _ = _setup(monkeypatch, tmp_path)
    model = SimpleNamespace(feature_names_in_=entry.feature_names, n_features_in_=66)
    monkeypatch.setattr(prediction, "load_sfc_model", lambda *_a, **_k: (model, entry))
    monkeypatch.setattr(prediction, "_require_enantiomer_pair", lambda _pair: None)

    def extra_warning(runtime, request):
        assert len(runtime.candidate_resolver["golden"]) == 1
        warnings = list(PINNED["expected_warnings"]) + ["FEATURE_VALUE_MISSING:unexpected_feature"]
        return [{"separation": PINNED["expected_score"]}], warnings, entry

    monkeypatch.setattr(SfcRuntime, "predict", extra_warning)

    flags = prediction._verification_result(model_registry=registry, entry=entry, catalog=catalog)

    assert flags["manifest"] is True
    assert flags["schema"] is True
    assert flags["artifact"] is True
    assert flags["environment"] is True
    assert flags["golden"] is True
    assert flags["anomaly"] is False
