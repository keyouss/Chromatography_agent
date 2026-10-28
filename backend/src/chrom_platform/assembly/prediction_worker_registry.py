"""Worker-side prediction registry assembly and verified metadata export."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Callable

from sqlalchemy.orm import Session

from chrom_platform.modules.prediction.adapters import CapabilityRegistry, VerifiedModelHandle


def build_prediction_worker_registry(*, session_factory: Callable[[], Session] | None = None) -> CapabilityRegistry:
    """Observe actual cold-load sites while preserving the existing builder."""
    from chrom_platform.assembly import prediction as original
    from chrom_platform.core.model_load_observation import record_model_load

    prediction_source = os.path.normcase(str(Path(original.__file__).resolve()))
    cc_source = os.path.normcase(str(Path(original.__file__).resolve().parents[1] / "adapters" / "cc" / "runtime.py"))
    gc_source = os.path.normcase(str(Path(original.__file__).resolve().parents[1] / "adapters" / "gc" / "runtime.py"))
    sfc_source = os.path.normcase(str(Path(original.__file__).resolve().parents[1] / "adapters" / "sfc" / "runtime.py"))
    proofs: dict[tuple[str, str], object] = {}
    previous_profile = sys.getprofile()

    def observe(frame, event, argument):
        filename = os.path.normcase(os.path.abspath(frame.f_code.co_filename))
        if filename == cc_source and event == "call" and frame.f_code.co_name == "load_model":
            try:
                runtime = frame.f_locals["self"]
                bundle = runtime.registry.bundle(frame.f_locals["model_id_or_spec"])
                if bundle.model_id not in runtime._models:
                    record_model_load("cc", bundle.model_id)
            except Exception:
                pass
        if filename == gc_source and event == "call" and frame.f_code.co_name == "_load_model":
            runtime = frame.f_locals.get("self")
            if runtime is not None and getattr(runtime, "_model", object()) is None:
                from chrom_platform.adapters.gc.runtime import GC_ARTIFACT_ID

                record_model_load("gc", getattr(runtime, "_bound_model_id", None) or GC_ARTIFACT_ID)
        if filename == sfc_source and event == "return" and frame.f_code.co_name == "load_sfc_model":
            if isinstance(argument, tuple) and len(argument) == 2:
                entry = argument[1]
                record_model_load("sfc", f"{entry.model_id}@{entry.version}")
        if filename == prediction_source and event == "return" and frame.f_code.co_name == "_verification_result":
            proof = getattr(argument, "catalog_source_proof", None)
            if proof is not None:
                proofs[(proof.model_id, proof.model_version)] = proof
        if previous_profile is not None:
            previous_profile(frame, event, argument)

    sys.setprofile(observe)
    try:
        registry = original.build_prediction_worker_registry(session_factory=session_factory)
    finally:
        sys.setprofile(previous_profile)

    if proofs:
        try:
            adapter = registry.adapter("sfc")
        except (KeyError, LookupError):
            adapter = None
        resolver = getattr(adapter, "binding_for", None)
        for (model_id, model_version), proof in proofs.items():
            leaf = resolver(model_id, model_version) if callable(resolver) else adapter
            if leaf is not None and (leaf._model_id, leaf._model_version) == (model_id, model_version):
                leaf.catalog_source_proof = proof
    return registry


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def export_prediction_metadata(registry: CapabilityRegistry) -> dict[str, object]:
    """Export bindings from the already verified Worker registry without loading models."""
    from dataclasses import asdict

    capabilities = registry.capabilities()
    bindings: list[dict[str, object]] = []
    identity_fields = (
        "adapter_id", "model_id", "model_version", "business_scope_id",
        "input_schema_version", "output_schema_version", "artifact_sha256",
        "preprocessing_profile_id", "preprocessing_profile_sha256",
    )
    entry_fields = (
        "model_id", "version", "artifact_sha256", "scope", "feature_names", "target",
        "stationary_phase", "mode", "allowed_columns", "single_column_group",
        "single_column_slug", "threshold", "score_semantics",
    )

    def digest(value: object) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()

    for capability in capabilities:
        adapter = registry.adapter(capability.id)
        for model in capability.models:
            if model.health.status != "READY":
                continue
            resolver = getattr(adapter, "binding_for", None)
            leaf = resolver(model.id, model.version) if callable(resolver) else adapter
            handle = getattr(leaf, "verified_handle", None)
            manifest = getattr(leaf, "manifest", None)
            if not isinstance(handle, VerifiedModelHandle) or manifest is None:
                raise ValueError("READY_METADATA_REQUIRES_VERIFIED_WORKER_BINDING")
            attestation = {name: getattr(handle, name) for name in identity_fields}
            if any(getattr(manifest, name) != value for name, value in attestation.items()) or (
                model.id != handle.model_id or model.version != handle.model_version
                or model.scope != handle.business_scope_id
                or model.input_schema_version != handle.input_schema_version
                or model.output_schema_version != handle.output_schema_version
                or model.artifact_sha256 != handle.artifact_sha256
                or model.preprocessing_profile_id != handle.preprocessing_profile_id
                or model.preprocessing_profile_sha256 != handle.preprocessing_profile_sha256
            ):
                raise ValueError("READY_METADATA_BINDING_MISMATCH")
            schemas = {}
            leaf_tools = getattr(getattr(leaf, "capability", None), "tools", ())
            for tool in leaf_tools:
                verified = handle.schema_for(tool.id)
                if verified is not None:
                    schemas[tool.id] = {
                        **verified,
                        "input_schema_sha256": digest(verified["input_schema"]),
                        "output_schema_sha256": digest(verified["output_schema"]),
                    }
            if not schemas:
                raise ValueError("READY_METADATA_SCHEMAS_MISSING")
            binding: dict[str, object] = {
                "adapter_id": capability.id, "model_id": model.id, "model_version": model.version,
                "manifest": manifest.model_dump(mode="json"),
                "attestation": attestation, "tool_schemas": schemas,
            }
            if capability.id == "sfc":
                runtime = leaf.runtime
                catalog = leaf.candidate_catalog
                entry = runtime.registry.entry(model.id, version=model.version)
                binding.update(
                    allowed_tool=runtime.allowed_tool,
                    sfc_domain=model.sfc_domain.model_dump(mode="json") if model.sfc_domain else None,
                    registry_entry={name: getattr(entry, name) for name in entry_fields},
                    catalog={"path": str(catalog.path), "sha256": _sha256_file(catalog.path),
                             "scope_storage_path": str(catalog.scope_storage_path), "max_candidates": catalog.max_candidates},
                )
                proof = getattr(leaf, "catalog_source_proof", None)
                binding["catalog_source_proof"] = asdict(proof) if proof is not None else None
            bindings.append(binding)
    return json.loads(json.dumps({
        "capabilities": [item.model_dump(mode="json", by_alias=True) for item in capabilities],
        "bindings": bindings,
    }, ensure_ascii=False))


__all__ = ["build_prediction_worker_registry", "export_prediction_metadata"]
