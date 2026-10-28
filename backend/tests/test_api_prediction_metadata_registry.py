"""Synthetic metadata projection checks; these fixtures never execute a model."""
from datetime import datetime, timezone
from types import SimpleNamespace
import copy
import hashlib
from pathlib import Path

import pytest

from chrom_platform.assembly import prediction_metadata
from chrom_platform.core.dto import CapabilityHealth
from chrom_platform.modules.prediction.adapters import (
    AdapterManifest, ModelOption, PredictionLimits, StructureCount, ToolCapability, TypeCapability,
)
from chrom_platform.modules.prediction.api_registry import ApiPredictionRegistry, _canonical_sha256
from chrom_platform.modules.prediction.adapters import ModelUnavailableError, InputValidationError


def metadata_fixture(kind):
    health = CapabilityHealth(status="READY", checked_at=datetime.now(timezone.utc))
    version = "1.1" if kind in {"hplc", "gc", "sfc"} else "1.0"
    profile = {"preprocessing_profile_id": "fixture-profile", "preprocessing_profile_sha256": "b" * 64} if kind in {"cc", "hplc", "gc"} else {}
    model = ModelOption(id=f"fixture-{kind}", version="1", task_type=kind, scope="fixture-scope",
                        artifact_sha256="a" * 64, input_schema_version=version, output_schema_version="1.0",
                        health=health, sfc_domain={"model_scope": "all_column"} if kind == "sfc" else None, **profile)
    manifest = AdapterManifest(adapter_id=kind, model_id=model.id, model_version="1", business_scope_id=model.scope,
                               manifest_version="1", input_schema_version=version, output_schema_version="1.0",
                               artifact_sha256=model.artifact_sha256, schema_available=True, assets_available=True,
                               data_origin="production", **profile)
    limits = PredictionLimits(max_rows=1, max_candidates=10, max_evaluations=10)
    tool = ToolCapability(id="full_column" if kind == "sfc" else "single", schema_version=version,
                          input_schema={"type": "object"}, output_schema={"type": "object"},
                          structure_count=StructureCount(min=2, max=2, semantics="pair"),
                          model_constraints={}, units={}, supported_visualizations=[], limits=limits, health=health)
    capability = TypeCapability(id=kind, display_name=kind, tools=[tool], models=[model], limits=limits, health=health)
    fields = ("adapter_id", "model_id", "model_version", "business_scope_id", "input_schema_version",
              "output_schema_version", "artifact_sha256", "preprocessing_profile_id", "preprocessing_profile_sha256")
    schemas = {name: getattr(tool, name) for name in ("input_schema", "output_schema")}
    schemas.update({f"{name}_sha256": _canonical_sha256(value) for name, value in list(schemas.items())})
    binding = {"adapter_id": kind, "model_id": model.id, "model_version": "1", "manifest": manifest.model_dump(mode="json"),
               "attestation": {field: getattr(manifest, field) for field in fields}, "tool_schemas": {tool.id: schemas}}
    if kind == "sfc":
        binding.update(allowed_tool="full_column", sfc_domain={"model_scope": "all_column"},
                       registry_entry={"scope": "all_column", "artifact_sha256": model.artifact_sha256}, catalog={})
    envelope = {"build": {"build_id": "fixture-build", "modules": ["prediction"]},
                "registry": {"capabilities": [capability.model_dump(mode="json", by_alias=True)], "bindings": [binding]}}
    return envelope, model, tool


@pytest.mark.parametrize("kind", ["tlc", "cc", "hplc", "gc", "sfc"])
def test_worker_json_metadata_roundtrip_keeps_five_type_health_and_schema(monkeypatch, kind):
    envelope, model, tool = metadata_fixture(kind)
    reads = []
    monkeypatch.setattr(prediction_metadata, "read_prediction_metadata", lambda: reads.append(True) or envelope)
    # Catalog assets are tested separately; this test covers only typed metadata projection.
    monkeypatch.setattr(ApiPredictionRegistry, "_load_sfc_catalog", staticmethod(lambda *_: object()))
    registry = ApiPredictionRegistry(manifest=SimpleNamespace(build_id="fixture-build", modules=["prediction"], chromatography_types=[kind]))
    assert registry.metadata_only
    assert registry.capability(kind).models[0].health.status == "READY"
    assert registry.get_tool(kind, tool.id, model.input_schema_version).health.status == "READY"
    assert registry.binding_for(kind, model.id, model.version).attestation["artifact_sha256"] == model.artifact_sha256
    assert len(reads) >= 3  # Parsing may be cached, but live reader checks must recur.
    monkeypatch.setattr(prediction_metadata, "read_prediction_metadata", lambda: None)
    assert registry.capabilities() == []
    with pytest.raises(ModelUnavailableError):
        registry.get_tool(kind, tool.id, model.input_schema_version)


def test_metadata_schema_or_exact_model_mismatch_does_not_keep_ready(monkeypatch):
    envelope, _, _ = metadata_fixture("gc")
    changed = copy.deepcopy(envelope)
    changed["registry"]["bindings"][0]["attestation"]["artifact_sha256"] = "c" * 64
    monkeypatch.setattr(prediction_metadata, "read_prediction_metadata", lambda: changed)
    registry = ApiPredictionRegistry(manifest=SimpleNamespace(build_id="fixture-build", modules=["prediction"], chromatography_types=["gc"]))
    assert registry.capability("gc").models[0].health.status == "UNAVAILABLE"


def test_ready_metadata_rejects_unknown_tool_as_invalid_contract(monkeypatch):
    envelope, _, _ = metadata_fixture("gc")
    monkeypatch.setattr(prediction_metadata, "read_prediction_metadata", lambda: envelope)
    registry = ApiPredictionRegistry(manifest=SimpleNamespace(build_id="fixture-build", modules=["prediction"], chromatography_types=["gc"]))
    with pytest.raises(InputValidationError):
        registry.get_tool("gc", "unknown", "1.1")


@pytest.mark.parametrize("catalog_alias,scope_alias", [("logical", "logical"), ("physical", "physical"), ("logical", "physical"), ("physical", "logical")])
def test_sfc_catalog_accepts_configured_logical_and_physical_path_aliases(monkeypatch, tmp_path, catalog_alias, scope_alias):
    from chrom_platform.modules.prediction.api_registry import ApiPredictionRegistry

    catalog_logical = tmp_path / "catalog-link.sqlite3"
    catalog_physical = tmp_path / "catalog.sqlite3"
    catalog_physical.write_text("synthetic catalog", encoding="utf-8")
    scope_logical = tmp_path / "scope-link.sqlite3"
    scope_physical = tmp_path / "scope.sqlite3"
    aliases = {catalog_logical: catalog_physical, scope_logical: scope_physical}
    original_resolve = Path.resolve

    def resolve(path, strict=False):
        target = aliases.get(path, path)
        return original_resolve(target, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setenv("CHROM_SFC_CANDIDATE_DB_PATH", str(catalog_logical))
    monkeypatch.setenv("CHROM_SFC_SCOPE_STORAGE_PATH", str(scope_logical))
    monkeypatch.delenv("CHROM_SFC_MODEL_ROOT", raising=False)
    monkeypatch.delenv("CHROM_SFC_REGISTRY_PATH", raising=False)
    monkeypatch.setattr(prediction_metadata, "_reject_reparse_chain", lambda _path: None)

    class FakeCatalog:
        def __init__(self, path, *, max_candidates, scope_storage_path):
            self.path = Path(path)
            self.scope_storage_path = Path(scope_storage_path)
            self.max_candidates = max_candidates

        def bind_model_scope(self, **_kwargs):
            return None

        def verify_source_hash(self, *, expected_sha256):
            assert expected_sha256 == hashlib.sha256(b"synthetic catalog").hexdigest()

    original_import = __import__("importlib").import_module

    def import_module(name, package=None):
        if name == "chrom_platform.adapters.sfc.runtime":
            return SimpleNamespace(SfcCandidateCatalog=FakeCatalog)
        return original_import(name, package)

    monkeypatch.setattr("chrom_platform.modules.prediction.api_registry.importlib.import_module", import_module)
    selected_catalog = catalog_logical if catalog_alias == "logical" else catalog_physical
    selected_scope = scope_logical if scope_alias == "logical" else scope_physical
    catalog = {
        "path": str(selected_catalog), "scope_storage_path": str(selected_scope),
        "sha256": hashlib.sha256(b"synthetic catalog").hexdigest(), "max_candidates": 20,
    }
    entry = {"model_id": "fixture-sfc", "version": "1", "artifact_sha256": "a" * 64,
             "scope": "all_column", "allowed_columns": []}
    result = ApiPredictionRegistry._load_sfc_catalog(
        catalog, entry, None, "fixture-sfc", "1", {"artifact_sha256": "a" * 64},
    )
    assert result.path == catalog_physical
    assert result.scope_storage_path == scope_physical


def test_sfc_catalog_rejects_external_scope_without_touching_its_filesystem(monkeypatch, tmp_path):
    from chrom_platform.modules.prediction.api_registry import ApiPredictionRegistry

    catalog_path = tmp_path / "catalog.sqlite3"
    catalog_path.write_text("synthetic catalog", encoding="utf-8")
    configured_scope = tmp_path / "scope.sqlite3"
    external_scope = tmp_path / "outside" / "scope.sqlite3"
    monkeypatch.setenv("CHROM_SFC_CANDIDATE_DB_PATH", str(catalog_path))
    monkeypatch.setenv("CHROM_SFC_SCOPE_STORAGE_PATH", str(configured_scope))
    monkeypatch.delenv("CHROM_SFC_MODEL_ROOT", raising=False)
    monkeypatch.delenv("CHROM_SFC_REGISTRY_PATH", raising=False)
    resolved_or_checked = []
    original_resolve = Path.resolve
    original_lstat = Path.lstat

    def resolve(path, strict=False):
        resolved_or_checked.append(("resolve", path))
        return original_resolve(path, strict=strict)

    def lstat(path):
        resolved_or_checked.append(("lstat", path))
        return original_lstat(path)

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(prediction_metadata, "_reject_reparse_chain", lambda path: resolved_or_checked.append(("reparse", path)))
    catalog = {"path": str(catalog_path), "scope_storage_path": str(external_scope),
               "sha256": hashlib.sha256(b"synthetic catalog").hexdigest(), "max_candidates": 20}
    entry = {"model_id": "fixture-sfc", "version": "1", "artifact_sha256": "a" * 64,
             "scope": "all_column", "allowed_columns": []}
    with pytest.raises(ValueError, match="SFC catalog path is not configured"):
        ApiPredictionRegistry._load_sfc_catalog(
            catalog, entry, None, "fixture-sfc", "1", {"artifact_sha256": "a" * 64},
        )
    assert all(path != external_scope for _operation, path in resolved_or_checked)


def test_sfc_catalog_rejects_scope_store_at_derived_catalog_path(monkeypatch, tmp_path):
    from chrom_platform.modules.prediction.api_registry import ApiPredictionRegistry

    catalog_path = tmp_path / "catalog.sqlite3"
    catalog_path.write_text("synthetic catalog", encoding="utf-8")
    scope_path = Path(f"{catalog_path}.scopes.sqlite3")
    monkeypatch.setenv("CHROM_SFC_CANDIDATE_DB_PATH", str(catalog_path))
    monkeypatch.setenv("CHROM_SFC_SCOPE_STORAGE_PATH", str(scope_path))
    monkeypatch.delenv("CHROM_SFC_MODEL_ROOT", raising=False)
    monkeypatch.delenv("CHROM_SFC_REGISTRY_PATH", raising=False)
    monkeypatch.setattr(prediction_metadata, "_reject_reparse_chain", lambda _path: None)
    catalog = {"path": str(catalog_path), "scope_storage_path": str(scope_path),
               "sha256": hashlib.sha256(b"synthetic catalog").hexdigest(), "max_candidates": 20}
    entry = {"model_id": "fixture-sfc", "version": "1", "artifact_sha256": "a" * 64,
             "scope": "all_column", "allowed_columns": []}
    with pytest.raises(ValueError, match="SFC scope storage path is not an independent writable store"):
        ApiPredictionRegistry._load_sfc_catalog(
            catalog, entry, None, "fixture-sfc", "1", {"artifact_sha256": "a" * 64},
        )
