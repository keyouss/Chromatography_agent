from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from chrom_platform.adapters.sfc import adapter as sfc_adapter_module
from chrom_platform.adapters.sfc.adapter import SfcCatalogSourceProof, SfcVerificationResult
from chrom_platform.adapters.sfc.runtime import SfcAssetUnavailable
from chrom_platform.assembly import prediction as prediction_assembly
from chrom_platform.modules.prediction.adapters import CapabilityRegistry


CATALOG_PATH = Path(r"D:\fixture\conditions.sqlite")
CATALOG_SHA = "a" * 64
REFERENCE_PATH = Path(r"D:\fixture\reference-v2.json")
REFERENCE_SHA = "b" * 64
FULL_ID = "rs12-extra-trees-s80-seed42"
SINGLE_ID = "sc-rs12-amylose-dmpc-coated"


@dataclass(frozen=True)
class FakeEntry:
    model_id: str
    version: str
    scope: str
    artifact_sha256: str
    allowed_columns: tuple[str, ...] = ("AD",)
    stationary_phase: str | None = "amylose"
    mode: str | None = "coated"
    single_column_slug: str = "ad-coated"
    single_column_group: str = "AD coated"
    feature_names: tuple[str, ...] = tuple(f"feature_{i}" for i in range(66))
    target: str = "label_success_rs12"
    threshold: str = "1.2"
    score_semantics: str = "positive_class_probability"


class FakeModelRegistry:
    def __init__(self, entries: list[FakeEntry], **_kwargs) -> None:
        self.entries = entries
        self.registry_path = Path(r"D:\fixture\registry.json")

    def entry(self, model_id: str, *, version: str) -> FakeEntry:
        return next(item for item in self.entries if (item.model_id, item.version) == (model_id, version))


class FakeCatalog:
    def __init__(self, path: Path = CATALOG_PATH, accepted_sha: str = CATALOG_SHA) -> None:
        self.path = path
        self.accepted_sha = accepted_sha
        self.verify_arguments: list[str | None] = []

    def bind_model_scope(self, **_kwargs) -> None:
        return None

    def verify_source_hash(self, expected_sha256: str | None = None) -> None:
        self.verify_arguments.append(expected_sha256)
        expected = expected_sha256 or "legacy-default"
        if expected != self.accepted_sha and expected != "legacy-default":
            raise SfcAssetUnavailable("CANDIDATE_DATABASE_HASH_MISMATCH")

    def resolve(self, _scope_id: str):
        return []


class FakeRuntime:
    def __init__(self, registry, bound_model_id, bound_model_version, bound_artifact_sha256, business_scope_id, **_kwargs):
        self.registry = registry
        self.bound_model_id = bound_model_id
        self.bound_model_version = bound_model_version
        self.bound_artifact_sha256 = bound_artifact_sha256.lower()
        self.business_scope_id = business_scope_id
        entry = registry.entry(bound_model_id, version=bound_model_version)
        self.allowed_tool = "full_column" if entry.scope == "all_column" else "single_column"


def _proof(entry: FakeEntry, *, catalog_sha: str = CATALOG_SHA, catalog_path: Path = CATALOG_PATH) -> SfcCatalogSourceProof:
    return SfcCatalogSourceProof(
        model_id=entry.model_id,
        model_version=entry.version,
        artifact_sha256=entry.artifact_sha256,
        business_scope_id="priority-local",
        input_schema_version="1.1",
        output_schema_version="1.0",
        catalog_path=str(catalog_path.resolve()),
        catalog_sha256=catalog_sha,
        reference_path=str(REFERENCE_PATH.resolve()),
        reference_sha256=REFERENCE_SHA,
        fixture_version="sfc-rs12-full-column-reference-v2",
        check_flags=tuple((name, True) for name in (
            "manifest", "schema", "artifact", "environment", "golden", "anomaly", "preprocessing",
        )),
    )


def _install_fake_assembly(monkeypatch, *, full_proof: SfcCatalogSourceProof | None, catalog_sha: str = CATALOG_SHA):
    single = FakeEntry(SINGLE_ID, "1", "single_column", "c" * 64)
    full = FakeEntry(FULL_ID, "1", "all_column", "d" * 64, allowed_columns=(), stationary_phase=None, mode=None)
    entries = [single, full]
    catalogs: list[FakeCatalog] = []
    events: list[str] = []

    class TrackedModelRegistry(FakeModelRegistry):
        def __init__(self, *args, **kwargs):
            events.append("registry")
            super().__init__(entries, **kwargs)

    class AdapterRegistry(FakeModelRegistry):
        def __init__(self, *_args, **_kwargs):
            super().__init__(entries)

    monkeypatch.setattr(prediction_assembly, "SfcModelRegistry", TrackedModelRegistry)
    monkeypatch.setattr(prediction_assembly, "_sfc_scope_storage_path", lambda **_kwargs: (events.append("scope"), Path(r"D:\fixture\scope.json"))[1])
    monkeypatch.setattr(prediction_assembly, "_register_optional_adapters", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(prediction_assembly, "_register_verified_tlc", lambda *_args, **_kwargs: None)

    def catalog_factory(*_args, **_kwargs):
        events.append("catalog")
        catalog = FakeCatalog(accepted_sha=catalog_sha)
        catalogs.append(catalog)
        return catalog

    monkeypatch.setattr(prediction_assembly, "SfcCandidateCatalog", catalog_factory)

    def verification(**kwargs):
        events.append(f"verification:{kwargs['entry'].model_id}")
        entry = kwargs["entry"]
        if entry.model_id == FULL_ID:
            return SfcVerificationResult(
                {name: True for name in ("manifest", "schema", "artifact", "environment", "golden", "anomaly", "preprocessing")},
                catalog_source_proof=full_proof,
            )
        return {name: True for name in ("manifest", "schema", "artifact", "environment", "golden", "anomaly")}

    monkeypatch.setattr(prediction_assembly, "_verification_result", verification)
    monkeypatch.setattr(sfc_adapter_module, "SfcModelRegistry", AdapterRegistry)
    monkeypatch.setattr(sfc_adapter_module, "SfcRuntime", FakeRuntime)
    build_errors: list[str] = []
    original_builder = prediction_assembly.build_sfc_adapter

    def tracked_builder(**kwargs):
        try:
            return original_builder(**kwargs)
        except Exception as exc:
            build_errors.append(f"{kwargs.get('model_id')}:{type(exc).__name__}:{exc}")
            raise

    monkeypatch.setattr(prediction_assembly, "build_sfc_adapter", tracked_builder)
    for key, value in {
        "CHROM_SFC_REGISTRY_PATH": r"D:\fixture\registry.json",
        "CHROM_SFC_MODEL_ROOT": r"D:\fixture\models",
        "CHROM_SFC_REGISTRY_SHA256": "e" * 64,
        "CHROM_SFC_CANDIDATE_DB_PATH": str(CATALOG_PATH),
        "CHROM_SFC_SCOPE_STORAGE_PATH": r"D:\fixture\scope.json",
        "CHROM_SFC_MODEL_ID": SINGLE_ID,
        "CHROM_SFC_MODEL_VERSION": "1",
        "CHROM_SFC_ARTIFACT_SHA256": single.artifact_sha256,
        "CHROM_SFC_BUSINESS_SCOPE_ID": "priority-local",
        "CHROM_SFC_FULL_MODEL_ID": FULL_ID,
        "CHROM_SFC_FULL_MODEL_VERSION": "1",
        "CHROM_SFC_FULL_ARTIFACT_SHA256": full.artifact_sha256,
        "CHROM_SFC_FULL_REFERENCE_PATH": str(REFERENCE_PATH),
        "CHROM_SFC_FULL_REFERENCE_SHA256": REFERENCE_SHA,
    }.items():
        monkeypatch.setenv(key, value)
    return entries, catalogs, build_errors, events


def test_registry_builder_passes_verified_full_catalog_proof_and_dispatches_ready(monkeypatch):
    entries = [
        FakeEntry(SINGLE_ID, "1", "single_column", "c" * 64),
        FakeEntry(FULL_ID, "1", "all_column", "d" * 64, allowed_columns=(), stationary_phase=None, mode=None),
    ]
    proof = _proof(entries[1])
    _entries, catalogs, build_errors, events = _install_fake_assembly(monkeypatch, full_proof=proof)

    registry = prediction_assembly.build_prediction_registry(session_factory=lambda: None)

    assert not build_errors, build_errors
    assert events == ["registry", "scope", "catalog", "verification:sc-rs12-amylose-dmpc-coated", "catalog", f"verification:{FULL_ID}"], events
    capability = registry.capability("sfc")
    assert {tool.id: tool.health.status for tool in capability.tools} == {
        "single_column": "READY",
        "full_column": "READY",
    }
    assert {model.id: model.health.status for model in capability.models if model.id in (SINGLE_ID, FULL_ID)} == {
        SINGLE_ID: "READY",
        FULL_ID: "READY",
    }
    full_catalog = next(catalog for catalog in catalogs if catalog.verify_arguments)
    assert full_catalog.verify_arguments == [CATALOG_SHA]


def test_registry_builder_rejects_full_catalog_proof_mismatch_but_keeps_single(monkeypatch):
    entries = [
        FakeEntry(SINGLE_ID, "1", "single_column", "c" * 64),
        FakeEntry(FULL_ID, "1", "all_column", "d" * 64, allowed_columns=(), stationary_phase=None, mode=None),
    ]
    proof = _proof(entries[1], catalog_sha="f" * 64)
    _entries, _catalogs, build_errors, _events = _install_fake_assembly(monkeypatch, full_proof=proof)

    registry = prediction_assembly.build_prediction_registry(session_factory=lambda: None)

    assert build_errors and FULL_ID in build_errors[0], build_errors
    capability = registry.capability("sfc")
    assert [tool.id for tool in capability.tools] == ["single_column"]
    assert next(model for model in capability.models if model.id == FULL_ID).health.status == "UNAVAILABLE"


def test_legacy_full_and_single_catalog_checks_keep_existing_default(monkeypatch):
    entries = [
        FakeEntry(SINGLE_ID, "1", "single_column", "c" * 64),
        FakeEntry("rs15-extra-trees-s80-seed2024", "1", "all_column", "d" * 64),
    ]
    full_catalog = FakeCatalog(accepted_sha="legacy-default")
    runtime = FakeRuntime(FakeModelRegistry(entries), entries[1].model_id, "1", entries[1].artifact_sha256, "priority-local")
    evidence = sfc_adapter_module.ModelVerificationEvidence(
        adapter_id="sfc", model_id=entries[1].model_id, model_version="1", business_scope_id="priority-local",
        input_schema_version="1.1", output_schema_version="1.0", artifact_sha256=entries[1].artifact_sha256,
        manifest_verified=True, schema_verified=True, artifact_sha_verified=True, environment_verified=True,
        golden_sample_verified=True, anomaly_checks_verified=True,
        schema_by_tool={"full_column": {"input_schema": {}, "output_schema": {}}},
    )
    sfc_adapter_module.SfcAdapter(
        runtime=runtime, model_id=entries[1].model_id, model_version="1", artifact_sha256=entries[1].artifact_sha256,
        business_scope_id="priority-local", verification_evidence=evidence, candidate_catalog=full_catalog,
    )
    assert full_catalog.verify_arguments == [None]

    single_catalog = FakeCatalog(accepted_sha="legacy-default")
    single_runtime = FakeRuntime(FakeModelRegistry(entries), SINGLE_ID, "1", entries[0].artifact_sha256, "priority-local")
    sfc_adapter_module.SfcAdapter(
        runtime=single_runtime, model_id=SINGLE_ID, model_version="1", artifact_sha256=entries[0].artifact_sha256,
        business_scope_id="priority-local", candidate_catalog=single_catalog,
    )
    assert single_catalog.verify_arguments == []


def test_full_catalog_proof_is_required_and_bound_to_exact_catalog_path():
    entry = FakeEntry(FULL_ID, "1", "all_column", "d" * 64, allowed_columns=(), stationary_phase=None, mode=None)
    registry = FakeModelRegistry([entry])
    runtime = FakeRuntime(registry, FULL_ID, "1", entry.artifact_sha256, "priority-local")
    catalog = FakeCatalog()

    with pytest.raises(SfcAssetUnavailable, match="MODEL_VERIFICATION_MISMATCH"):
        sfc_adapter_module.SfcAdapter(
            runtime=runtime, model_id=FULL_ID, model_version="1", artifact_sha256=entry.artifact_sha256,
            business_scope_id="priority-local", candidate_catalog=catalog,
        )

    with pytest.raises(SfcAssetUnavailable, match="MODEL_VERIFICATION_MISMATCH"):
        sfc_adapter_module.SfcAdapter(
            runtime=runtime, model_id=FULL_ID, model_version="1", artifact_sha256=entry.artifact_sha256,
            business_scope_id="priority-local", candidate_catalog=catalog,
            catalog_source_proof=_proof(entry, catalog_path=Path(r"D:\fixture\other.sqlite")),
        )


def test_rs12_proof_cannot_be_reused_for_another_full_model():
    entry = FakeEntry("rs15-extra-trees-s80-seed2024", "1", "all_column", "d" * 64)
    registry = FakeModelRegistry([entry])
    runtime = FakeRuntime(registry, entry.model_id, "1", entry.artifact_sha256, "priority-local")
    catalog = FakeCatalog(accepted_sha="legacy-default")
    proof = _proof(FakeEntry(FULL_ID, "1", "all_column", "d" * 64, allowed_columns=(), stationary_phase=None, mode=None))

    with pytest.raises(SfcAssetUnavailable, match="MODEL_VERIFICATION_MISMATCH"):
        sfc_adapter_module.SfcAdapter(
            runtime=runtime, model_id=entry.model_id, model_version="1", artifact_sha256=entry.artifact_sha256,
            business_scope_id="priority-local", candidate_catalog=catalog, catalog_source_proof=proof,
        )
