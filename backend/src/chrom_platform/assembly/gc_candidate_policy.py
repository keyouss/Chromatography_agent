"""Narrow, assembly-owned policy for the reviewed GC profile candidate.

The review SHA is recorded only as an audit anchor. Executability comes from a
fresh runtime-local receipt, its full bindings, and the runtime's one-use typed
approval path; this module never accepts serialized receipts or approvals.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class _GcCandidatePolicy:
    profile_id: str = "gc-gnn-seq240408-250c-rdkit-seed42-v1"
    profile_sha256: str = "a3c1dc6d576973682357818e2424e0640617ec4a959d40d71fbbb40a733b8799"
    business_scope_id: str = "gc-default"
    model_id: str = "gc-gnn-seq240408-250c"
    model_version: str = "gc-gnn-seq240408-250c-600-prep-rdkit-seed42-v1"
    artifact_sha256: str = "0433756eab33689981c6d881e3b76c34a8043eb2f47a6adf6ed4a65dd9d5758c"
    author_input_schema_version: str = "1.0"
    platform_input_schema_version: str = "1.1"
    input_schema_id: str = "GcPredictionCreate"
    output_schema_id: str = "GcPredictionResult"
    output_schema_version: str = "gc-retention-time-1"
    fixture_id: str = "gc-reference-preprocessing-v2.json"
    fixture_sha256: str = "06035bb25618b14a3b2547bb238167b19d3b593e7a2e0e4c3d40b08409d3e597"
    environment_sha256: str = "0c30c1c8a3a82b34eca217d994a7b41c4bbeab0d3b0c4db0754a575cdbc170eb"
    asset_inventory_sha256: str = "b242c2595332dcab90365b9c748212059965f1d00b80c38c5ac6dcc68516da85"
    adapter_source_sha256: str = "6a66fd42e98291cb082f385fb251cd62768e1151b573517290a26e468c4ae839"
    runtime_source_sha256: str = "d63f1f523d355b1e5a13a5dfb4d938cdc237e66f2ce7454a21f3b308bc54eea2"
    qa_review_sha256_audit_anchor: str = "ddbe8c981842c64fcc5781e4b1235e96733f7420a057954c7b5cf0fd817a4760"


_POLICY = _GcCandidatePolicy()
_REFERENCE_ENVIRONMENT = {
    "python": "3.11.15",
    "rdkit": "2026.03.3",
    "torch": "2.6.0+cu124",
    "numpy": "1.26.4",
    "mordred": "1.2.0",
}


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _matches_gc_review_policy(
    *,
    adapter: Any,
    runtime: Any,
    receipt: Any,
    descriptor: dict[str, Any],
    current_environment: dict[str, str],
    current_asset_inventory_sha256: str,
    current_adapter_source_sha256: str,
    current_runtime_source_sha256: str,
    fixture_path: Path | None,
    verification_output_snapshots: Any,
    global_qa_approved: object,
) -> bool:
    """Check synthetic facts or an actual runtime receipt against the fixed policy."""
    if global_qa_approved is not False:
        return False
    if adapter is None or runtime is None or receipt is None or not isinstance(descriptor, dict):
        return False
    if getattr(adapter, "runtime", None) is not runtime:
        return False
    if getattr(adapter, "verified_handle", None) is not None or getattr(runtime, "_verified_handle", None) is not None:
        return False
    if any(
        getattr(runtime, name, None) is not None
        for name in ("_bound_model_id", "_bound_model_version", "_bound_artifact_sha256")
    ):
        return False
    if getattr(runtime, "_profile_receipt", None) is not receipt:
        return False

    preprocessing = descriptor.get("preprocessing")
    if not isinstance(preprocessing, dict):
        return False
    expected_descriptor_fields = {
        "profile_id": _POLICY.profile_id,
        "model_id": _POLICY.model_id,
        "model_version": _POLICY.model_version,
        "artifact_sha256": _POLICY.artifact_sha256,
        "platform_input_schema_version": _POLICY.platform_input_schema_version,
        "author_input_schema_version": _POLICY.author_input_schema_version,
        "output_schema_version": _POLICY.output_schema_version,
        "input_schema_id": _POLICY.input_schema_id,
        "output_schema_id": _POLICY.output_schema_id,
        "reference_fixture_id": _POLICY.fixture_id,
        "reference_fixture_sha256": _POLICY.fixture_sha256,
    }
    if any(descriptor.get(key) != value for key, value in expected_descriptor_fields.items()):
        return False
    if _canonical_sha256(descriptor) != _POLICY.profile_sha256:
        return False
    if current_environment != _REFERENCE_ENVIRONMENT or _canonical_sha256(current_environment) != _POLICY.environment_sha256:
        return False
    if current_asset_inventory_sha256 != _POLICY.asset_inventory_sha256:
        return False
    if current_adapter_source_sha256 != _POLICY.adapter_source_sha256:
        return False
    if current_runtime_source_sha256 != _POLICY.runtime_source_sha256:
        return False
    if preprocessing.get("adapter_source_sha256") != _POLICY.adapter_source_sha256:
        return False
    if preprocessing.get("runtime_source_sha256") != _POLICY.runtime_source_sha256:
        return False
    if preprocessing.get("environment_reference") != _REFERENCE_ENVIRONMENT:
        return False
    if descriptor.get("preprocessing_protocol_id") != "gc-preprocessing-rdkit-seed42-v1":
        return False

    if (
        getattr(receipt, "profile_id", None) != _POLICY.profile_id
        or getattr(receipt, "profile_sha256", None) != _POLICY.profile_sha256
        or getattr(receipt, "model_id", None) != _POLICY.model_id
        or getattr(receipt, "model_version", None) != _POLICY.model_version
        or getattr(receipt, "artifact_sha256", None) != _POLICY.artifact_sha256
        or getattr(receipt, "author_input_schema_version", None) != _POLICY.author_input_schema_version
        or getattr(receipt, "platform_input_schema_version", None) != _POLICY.platform_input_schema_version
        or getattr(receipt, "input_schema_id", None) != _POLICY.input_schema_id
        or getattr(receipt, "input_schema_sha256", None) != descriptor.get("input_schema_sha256")
        or getattr(receipt, "output_schema_id", None) != _POLICY.output_schema_id
        or getattr(receipt, "output_schema_sha256", None) != descriptor.get("output_schema_sha256")
        or getattr(receipt, "fixture_id", None) != _POLICY.fixture_id
        or getattr(receipt, "fixture_sha256", None) != _POLICY.fixture_sha256
        or getattr(receipt, "environment_sha256", None) != _POLICY.environment_sha256
        or getattr(receipt, "asset_inventory_sha256", None) != _POLICY.asset_inventory_sha256
        or getattr(receipt, "adapter_source_sha256", None) != _POLICY.adapter_source_sha256
        or getattr(receipt, "runtime_source_sha256", None) != _POLICY.runtime_source_sha256
    ):
        return False
    if (
        getattr(receipt, "runtime_nonce", None) != getattr(runtime, "_runtime_nonce", None)
        or getattr(receipt, "generation", None) != getattr(runtime, "_review_generation", None)
        or not isinstance(getattr(receipt, "receipt_sha256", None), str)
    ):
        return False

    counters = (
        "verification_predict_attempts",
        "verification_predict_successes",
        "verification_forward_attempts",
        "verification_forward_successes",
    )
    if any(getattr(receipt, name, None) != 2 for name in counters):
        return False
    if any(getattr(runtime, f"_{name}", None) != 2 for name in counters):
        return False

    if not isinstance(verification_output_snapshots, (tuple, list)) or len(verification_output_snapshots) != 2:
        return False
    first, second = verification_output_snapshots
    if first != second:
        return False
    expected_output_sha256s = (_canonical_sha256(first), _canonical_sha256(second))
    if getattr(receipt, "output_sha256s", None) != expected_output_sha256s:
        return False

    if fixture_path is None:
        return False
    try:
        fixture_sha256 = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    except OSError:
        return False
    if fixture_sha256 != _POLICY.fixture_sha256:
        return False

    receipt_fields = vars(receipt)
    receipt_payload = {key: value for key, value in receipt_fields.items() if key != "receipt_sha256"}
    if _canonical_sha256(receipt_payload) != receipt.receipt_sha256:
        return False
    # GcRuntime creates a private receipt only after all verification report
    # checks are true; the private identity, counters, snapshots, and receipt
    # digest preserve that runtime-owned proof without accepting a report copy.
    return True


def maybe_build_reviewed_gc_candidate(adapter: Any) -> Any:
    """Promote only the single allowlisted profile from its fresh runtime receipt."""
    if adapter is None:
        return adapter
    runtime = getattr(adapter, "runtime", None)
    if runtime is None:
        return adapter
    try:
        from chrom_platform.adapters.gc.adapter import (
            GcPredictionAdapter,
            build_gc_profile_candidate_adapter,
        )
        from chrom_platform.adapters.gc.manifest import GC_PREPROCESSING_PROTOCOL_QA_APPROVED
        from chrom_platform.adapters.gc.runtime import GcProfileVerificationReceipt, GcRuntime
        from chrom_platform.adapters.gc.serving_profile import gc_serving_profile_descriptor

        if type(adapter) is not GcPredictionAdapter or type(runtime) is not GcRuntime:
            return adapter
        receipt = getattr(runtime, "_profile_receipt", None)
        if (
            type(receipt) is not GcProfileVerificationReceipt
            or adapter.verified_handle is not None
            or runtime._verified_handle is not None
        ):
            return adapter
        descriptor = gc_serving_profile_descriptor()
        source_hashes = runtime._platform_source_hashes()
        environment = runtime._current_environment()
        asset_inventory_sha256 = runtime._verified_asset_inventory_sha256()
        output_snapshots = runtime.verification_output_snapshots
        fixture_path = runtime._verified_fixture_path
        if not _matches_gc_review_policy(
            adapter=adapter,
            runtime=runtime,
            receipt=receipt,
            descriptor=descriptor,
            current_environment=environment,
            current_asset_inventory_sha256=asset_inventory_sha256,
            current_adapter_source_sha256=source_hashes[0],
            current_runtime_source_sha256=source_hashes[1],
            fixture_path=fixture_path,
            verification_output_snapshots=output_snapshots,
            global_qa_approved=GC_PREPROCESSING_PROTOCOL_QA_APPROVED,
        ):
            return adapter
        # Revalidate live fixture/assets/environment/source before creating the
        # runtime-local approval; the candidate builder consumes it immediately.
        runtime._recheck_consumed_receipt(receipt)
        approval = runtime._issue_profile_review_approval(
            receipt, _POLICY.qa_review_sha256_audit_anchor,
        )
        candidate = build_gc_profile_candidate_adapter(
            runtime=runtime,
            structure_resolver=adapter.structure_resolver,
            approval=approval,
        )
        handle = candidate.verified_handle
        if (
            candidate.runtime is not runtime
            or handle is None
            or handle.model_id != _POLICY.model_id
            or handle.model_version != _POLICY.model_version
            or handle.business_scope_id != _POLICY.business_scope_id
            or handle.artifact_sha256 != _POLICY.artifact_sha256
            or handle.input_schema_version != _POLICY.platform_input_schema_version
            or handle.output_schema_version != _POLICY.output_schema_version
            or handle.preprocessing_profile_id != _POLICY.profile_id
            or handle.preprocessing_profile_sha256 != _POLICY.profile_sha256
        ):
            return adapter
        return candidate
    except Exception:
        # A mismatch or consumed-approval failure leaves the original adapter
        # registered with its initial unavailable capability.
        return adapter


__all__ = ["maybe_build_reviewed_gc_candidate"]
