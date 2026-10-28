"""Fixed child-side entry point for model package verification."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from chrom_platform.artifact_core.model_package_manifest import (
    FIXED_LOADER_ID,
    InspectedPackage,
    PackageValidationError,
    RESOURCE_LIMITS_APPROVED,
    inspect_model_package,
)


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REQUEST_FIELDS = frozenset({
    "op", "project_id", "artifact_id", "task_type", "package_path",
    "expected_sha256", "expected_size_bytes", "allowed_loader_ids",
})
_REQUIRED_STEPS = (
    "manifest/hash", "allowlisted loader/format", "dependency/runtime",
    "schema", "load", "golden", "finite values",
)


def _failed_evidence(
    request: Mapping[str, object], code: str, *, inspected: InspectedPackage | None = None,
) -> dict[str, object]:
    sha = request.get("expected_sha256")
    manifest: dict[str, object] = {
        "format": "safetensors",
        "loader_id": FIXED_LOADER_ID,
        "artifact_sha256": sha if isinstance(sha, str) and _SHA256.fullmatch(sha) else None,
    }
    checks = [{"step": step, "status": "FAILED", "message": code} for step in _REQUIRED_STEPS]
    result: dict[str, object] = {
        "valid": False,
        "project_id": request.get("project_id"),
        "artifact_id": request.get("artifact_id"),
        "task_type": request.get("task_type"),
        "artifact_sha256": manifest["artifact_sha256"],
        "manifest": manifest,
        "checks": checks,
        "error_code": code,
    }
    if inspected is not None:
        snapshot = dict(inspected.manifest)
        snapshot["artifact_sha256"] = inspected.container_sha256
        result["manifest"] = snapshot
        result["member_hashes"] = dict(inspected.member_hashes)
        result["member_sizes"] = dict(inspected.member_sizes)
        result["container_size_bytes"] = inspected.container_size_bytes
    return result


def _validate_request(request: Mapping[str, object]) -> tuple[Path, str, int]:
    if set(request) != _REQUEST_FIELDS or request.get("op") != "verify_gc_safetensors":
        raise PackageValidationError("PACKAGE_REQUEST_INVALID")
    for field in ("project_id", "artifact_id"):
        if not isinstance(request.get(field), str) or not request[field]:
            raise PackageValidationError("PACKAGE_REQUEST_INVALID")
    if request.get("task_type") != "gc":
        raise PackageValidationError("PACKAGE_TASK_UNSUPPORTED")
    path = request.get("package_path")
    digest = request.get("expected_sha256")
    size = request.get("expected_size_bytes")
    loaders = request.get("allowed_loader_ids")
    if (not isinstance(path, (str, Path)) or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None or type(size) is not int or size <= 0
            or not isinstance(loaders, (list, tuple))
            or any(not isinstance(loader, str) for loader in loaders)
            or tuple(loaders) != (FIXED_LOADER_ID,)):
        raise PackageValidationError("PACKAGE_REQUEST_INVALID")
    resolved_path = Path(path)
    if not resolved_path.is_absolute():
        raise PackageValidationError("PACKAGE_REQUEST_INVALID")
    return resolved_path, digest, size


def verify_package_request(
    request: Mapping[str, object], *, loader: object | None = None,
) -> dict[str, object]:
    """Inspect one server-bound package and emit only verifier-owned evidence.

    This is the fixed child entry.  It accepts no module, root, command, or
    entrypoint supplied by the package or request.  A build-provided fixed
    Loader performs model execution only after the reviewed resource gate.
    """
    try:
        path, expected_sha256, expected_size = _validate_request(request)
        if loader is None or getattr(loader, "loader_id", None) != FIXED_LOADER_ID:
            raise PackageValidationError("PACKAGE_LOADER_UNAVAILABLE")
        verify_integrity = getattr(loader, "verify_integrity", None)
        validate_manifest = getattr(loader, "validate_manifest", None)
        verify = getattr(loader, "verify", None)
        if not callable(verify_integrity) or not callable(validate_manifest) or not callable(verify):
            raise PackageValidationError("PACKAGE_LOADER_INTERFACE_INVALID")
        verify_integrity()
        inspected = inspect_model_package(
            path, expected_sha256=expected_sha256, expected_size_bytes=expected_size,
        )
        validate_manifest(inspected.manifest)
        if not inspected.limits_approved or not RESOURCE_LIMITS_APPROVED:
            raise PackageValidationError("PACKAGE_RESOURCE_BASELINE_UNAPPROVED")
        loader_result = verify(
            inspected,
            expected_container_sha256=expected_sha256,
            expected_size_bytes=expected_size,
        )
        if not isinstance(loader_result, Mapping):
            raise PackageValidationError("PACKAGE_LOADER_EVIDENCE_INVALID")
        result = dict(loader_result)
        result.update({
            "project_id": request["project_id"],
            "artifact_id": request["artifact_id"],
            "task_type": request["task_type"],
            "artifact_sha256": expected_sha256,
            "manifest": {**dict(inspected.manifest), "artifact_sha256": expected_sha256},
            "member_hashes": dict(inspected.member_hashes),
            "member_sizes": dict(inspected.member_sizes),
            "container_size_bytes": expected_size,
        })
        return result
    except PackageValidationError as exc:
        return _failed_evidence(request, exc.code, inspected=locals().get("inspected"))
    except Exception:
        return _failed_evidence(request, "MODEL_PACKAGE_VERIFICATION_FAILED", inspected=locals().get("inspected"))


__all__ = ["verify_package_request"]
