from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace

import pytest

from chrom_platform.assembly.prediction_worker_registry import export_prediction_metadata
from chrom_platform.core.dto import CapabilityHealth
from chrom_platform.modules.prediction.adapters import (
    AdapterManifest, ModelOption, ModelVerificationEvidence, VerifiedModelHandle,
    issue_verified_model_handle,
)


def registry_fixture():
    manifest = AdapterManifest(
        adapter_id="tlc", model_id="test-tlc", model_version="1", business_scope_id="test-scope",
        manifest_version="1", input_schema_version="1.0", output_schema_version="1.0",
        artifact_sha256="a" * 64, schema_available=True, assets_available=True, data_origin="production",
    )
    schemas = {"single": {"input_schema": {"type": "object"}, "output_schema": {"type": "object"}}}
    evidence = ModelVerificationEvidence(
        **{key: getattr(manifest, key) for key in (
            "adapter_id", "model_id", "model_version", "business_scope_id", "input_schema_version",
            "output_schema_version", "artifact_sha256",
        )},
        manifest_verified=True, schema_verified=True, artifact_sha_verified=True,
        environment_verified=True, golden_sample_verified=True, anomaly_checks_verified=True,
        schema_by_tool=schemas,
    )
    handle = issue_verified_model_handle(manifest, evidence)
    model = ModelOption(
        id="test-tlc", version="1", task_type="tlc", scope="test-scope", artifact_sha256="a" * 64,
        input_schema_version="1.0", output_schema_version="1.0",
        health=CapabilityHealth(status="READY", checked_at=datetime.now(timezone.utc)),
    )
    leaf = SimpleNamespace(verified_handle=handle, manifest=manifest,
                           capability=SimpleNamespace(tools=[SimpleNamespace(id="single")]))
    capability = SimpleNamespace(id="tlc", models=[model], model_dump=lambda **_: {"id": "tlc"})
    registry = SimpleNamespace(capabilities=lambda: [capability], adapter=lambda _: leaf)
    return registry, leaf, model


def test_export_reads_real_handle_without_executing_or_serializing_it():
    registry, leaf, model = registry_fixture()
    payload = export_prediction_metadata(registry)
    binding = payload["bindings"][0]
    assert isinstance(leaf.verified_handle, VerifiedModelHandle)
    assert binding["attestation"]["artifact_sha256"] == model.artifact_sha256
    assert "verified_handle" not in binding
    schema = binding["tool_schemas"]["single"]
    encoded = json.dumps(schema["input_schema"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert schema["input_schema_sha256"] == hashlib.sha256(encoded).hexdigest()
    json.dumps(payload)


@pytest.mark.parametrize("mismatch", ["missing_handle", "changed_manifest"])
def test_export_rejects_ready_bindings_without_matching_process_local_verification(mismatch):
    registry, leaf, _ = registry_fixture()
    if mismatch == "missing_handle":
        leaf.verified_handle = SimpleNamespace()
    else:
        leaf.manifest = leaf.manifest.model_copy(update={"artifact_sha256": "b" * 64})
    with pytest.raises(ValueError, match="READY_METADATA"):
        export_prediction_metadata(registry)
