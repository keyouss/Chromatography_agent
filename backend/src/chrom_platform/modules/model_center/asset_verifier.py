"""Production-bound model asset verifier for the fixed GC package path.

The verifier accepts only an existing, project-scoped AVAILABLE model_package
artifact with a real successful scan report.  It resolves stored bytes through
ArtifactStore and delegates package inspection to the controlled executor.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from chrom_platform.artifact_core.model_package_manifest import EXPECTED_MEMBERS, FIXED_LOADER_ID


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REQUIRED_CHECKS = (
    "manifest/hash", "allowlisted loader/format", "dependency/runtime",
    "schema", "load", "golden", "finite values",
)
_SCAN_SCHEMA_VERSION = "1.0"


class _ArtifactReader(Protocol):
    def get(self, project_id: str, artifact_id: str) -> object | None: ...


class _Storage(Protocol):
    def prepare_scan_target(self, storage_key: str, *, expected_sha256: str, expected_size: int) -> object: ...
    def assert_scan_target_unchanged(self, target: object) -> None: ...


class _ScanEvidenceReader(Protocol):
    def get(self, *, project_id: str, report_ref: Mapping[str, object]) -> Mapping[str, object] | None: ...


class _ControlledExecutor(Protocol):
    def execute(
        self,
        request: Mapping[str, object],
        *, cancelled: Callable[[], bool] | None = None,
        check_fence: Callable[[], None] | None = None,
        before_launch: Callable[[str, str], None] | None = None,
        stop_sink: Callable[[str, str, Mapping[str, object]], None] | None = None,
    ) -> Mapping[str, object]: ...


class _RecoveryPort(Protocol):
    def before_launch(self, binding: Mapping[str, object], request_ref: str, request_sha256: str) -> None: ...
    def stop_sink(
        self, binding: Mapping[str, object], request_ref: str, request_sha256: str,
        termination: Mapping[str, object],
    ) -> None: ...
    def confirmed_stop(
        self, binding: Mapping[str, object], request_ref: str, request_sha256: str,
    ) -> bool: ...


class _CancellationReadFailed(RuntimeError):
    pass


def _cancel_requested(cancelled: Callable[[], bool] | None) -> bool:
    if cancelled is None:
        return False
    try:
        return bool(cancelled())
    except Exception as exc:
        raise _CancellationReadFailed("MODEL_PACKAGE_CANCEL_STATE_UNAVAILABLE") from exc


def _get(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _valid_sha(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _failed(code: str, *, project_id: str, artifact_id: str | None, task_type: str) -> dict[str, object]:
    return {
        "valid": False,
        "project_id": project_id,
        "artifact_id": artifact_id,
        "task_type": task_type,
        "checks": [
            {"step": step, "status": "FAILED", "message": code}
            for step in _REQUIRED_CHECKS
        ],
        "error_code": code,
    }


def _with_execution_receipt(
    evidence: Mapping[str, object], receipt: Mapping[str, object],
) -> dict[str, object]:
    return {**dict(evidence), "_execution_receipt": dict(receipt)}


def _preflight_receipt(
    *, project_id: str, source: Mapping[str, object], task_type: str, operation_key: str,
    error_code: str,
) -> dict[str, object]:
    """Attest this verifier invocation rejected before executor.execute()."""
    binding = {
        "op": "verify_gc_safetensors",
        "project_id": project_id,
        "source_kind": source.get("kind"),
        "artifact_id": source.get("artifact_id"),
        "task_type": task_type,
        "operation_key": operation_key,
    }
    request_sha256 = hashlib.sha256(
        json.dumps(binding, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    request_ref = f"preflight:{request_sha256[:48]}"
    termination = {
        "request_ref": request_ref,
        "request_sha256": request_sha256,
        "started": False,
        "never_started": True,
        "confirmed": True,
        "process_id": None,
        "creation_filetime": None,
        "exit_filetime": None,
        "exit_code": None,
        "job_active_processes": 0,
        "output_drained": True,
        "cleanup_completed": True,
        "stop_cause": "preflight_rejected",
    }
    return {
        "request_ref": request_ref,
        "request_sha256": request_sha256,
        "termination": termination,
        "executor_status": "NOT_STARTED",
        "executor_error_code": error_code,
        "operation_key": operation_key,
    }


def _preflight_failed(
    code: str, *, project_id: str, artifact_id: str | None, task_type: str,
    source: Mapping[str, object], operation_key: str,
) -> dict[str, object]:
    failed = _failed(code, project_id=project_id, artifact_id=artifact_id, task_type=task_type)
    return _with_execution_receipt(
        failed,
        _preflight_receipt(
            project_id=project_id, source=source, task_type=task_type,
            operation_key=operation_key, error_code=code,
        ),
    )


def _metadata(value: object) -> Mapping[str, object]:
    raw = _get(value, "metadata_json", {})
    return raw if isinstance(raw, Mapping) else {}


def _scan_report_is_bound(
    report: Mapping[str, object], *, project_id: str, artifact_id: str,
    sha256: str, size_bytes: int, identity: object, approved_profile_ids: frozenset[str],
) -> tuple[bool, dict[str, tuple[str, int]]]:
    """Validate the report projection required from the real job event."""
    if (report.get("schema_version") != _SCAN_SCHEMA_VERSION
            or report.get("project_id") != project_id
            or report.get("artifact_id") != artifact_id
            or report.get("validation_status") != "SUCCEEDED"
            or report.get("status") != "SUCCEEDED"
            or not isinstance(report.get("validation_job_id"), str)
            or not isinstance(report.get("scan_attempt_id"), str)):
        return False, {}
    container = report.get("container")
    if not isinstance(container, Mapping):
        return False, {}
    if (container.get("sha256") != sha256 or container.get("size_bytes") != size_bytes
            or container.get("identity") != list(identity) or container.get("verdict") != "CLEAN"
            or not isinstance(container.get("profile_id"), str)
            or container.get("profile_id") not in approved_profile_ids):
        return False, {}
    raw_members = report.get("members")
    if not isinstance(raw_members, list) or len(raw_members) != len(EXPECTED_MEMBERS):
        return False, {}
    members: dict[str, tuple[str, int]] = {}
    for item in raw_members:
        if not isinstance(item, Mapping):
            return False, {}
        name = item.get("name")
        digest = item.get("sha256")
        size = item.get("size_bytes")
        if (name not in EXPECTED_MEMBERS or name in members or not _valid_sha(digest)
                or type(size) is not int or size <= 0 or item.get("verdict") != "CLEAN"
                or not isinstance(item.get("profile_id"), str)
                or item.get("profile_id") not in approved_profile_ids):
            return False, {}
        members[str(name)] = (str(digest), size)
    return set(members) == EXPECTED_MEMBERS, members


class ModelPackageAssetVerifier:
    """Real parent-side verifier; never parses or loads model package content."""

    def __init__(
        self,
        *,
        artifacts: _ArtifactReader,
        storage: _Storage,
        scan_evidence: _ScanEvidenceReader,
        executor: _ControlledExecutor,
        allowed_loader_ids: tuple[str, ...] = (FIXED_LOADER_ID,),
        approved_scan_profile_ids: tuple[str, ...] = (),
    ) -> None:
        self.artifacts = artifacts
        self.storage = storage
        self.scan_evidence = scan_evidence
        self.executor = executor
        # The build can narrow this list, but cannot introduce an arbitrary ID.
        self.allowed_loader_ids = tuple(
            loader for loader in allowed_loader_ids if loader == FIXED_LOADER_ID
        )
        self.approved_scan_profile_ids = frozenset(
            profile for profile in approved_scan_profile_ids if isinstance(profile, str) and profile
        )

    def verify(
        self,
        *,
        project_id: str,
        source: Mapping[str, Any],
        task_type: str,
        operation_key: str = "service",
        cancelled: Callable[[], bool] | None = None,
        check_fence: Callable[[], None] | None = None,
        recovery_port: _RecoveryPort | None = None,
        recovery_binding: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any]:
        execution_receipt: dict[str, object] | None = None
        executor_attempted = False
        source_kind = source.get("kind")
        artifact_id = source.get("artifact_id")
        if source_kind != "external_package" or not isinstance(artifact_id, str) or not artifact_id:
            return _preflight_failed(
                "MODEL_PACKAGE_SOURCE_UNSUPPORTED", project_id=project_id, artifact_id=None,
                task_type=task_type, source=source, operation_key=operation_key,
            )
        if task_type != "gc" or not self.allowed_loader_ids:
            return _preflight_failed(
                "MODEL_PACKAGE_LOADER_UNAVAILABLE", project_id=project_id, artifact_id=artifact_id,
                task_type=task_type, source=source, operation_key=operation_key,
            )
        try:
            if check_fence is not None:
                check_fence()
            if _cancel_requested(cancelled):
                return _preflight_failed(
                    "MODEL_PACKAGE_VALIDATION_CANCELLED", project_id=project_id, artifact_id=artifact_id,
                    task_type=task_type, source=source, operation_key=operation_key,
                )
            artifact = self.artifacts.get(project_id, artifact_id)
            if artifact is None:
                return _preflight_failed(
                    "MODEL_PACKAGE_ARTIFACT_MISSING", project_id=project_id, artifact_id=artifact_id,
                    task_type=task_type, source=source, operation_key=operation_key,
                )
            row_project = _get(artifact, "project_id")
            row_id = _get(artifact, "id")
            kind = _get(artifact, "kind")
            status = _get(artifact, "status")
            scan_status = _get(artifact, "scan_status")
            sha256 = _get(artifact, "sha256")
            size = _get(artifact, "size_bytes")
            storage_key = _get(artifact, "storage_key")
            metadata = _metadata(artifact)
            report_ref = metadata.get("validation_report_ref")
            if (row_project != project_id or row_id != artifact_id or kind != "model_package"
                    or status != "AVAILABLE" or scan_status != "PASSED"
                    or not _valid_sha(sha256) or type(size) is not int or size <= 0
                    or not isinstance(storage_key, str) or not storage_key
                    or not isinstance(report_ref, Mapping) or report_ref.get("type") != "job"
                    or not isinstance(report_ref.get("id"), str) or not report_ref["id"]):
                return _preflight_failed(
                    "MODEL_PACKAGE_ARTIFACT_BINDING_INVALID", project_id=project_id, artifact_id=artifact_id,
                    task_type=task_type, source=source, operation_key=operation_key,
                )
            report = self.scan_evidence.get(project_id=project_id, report_ref=report_ref)
            if (not isinstance(report, Mapping)
                    or report.get("validation_job_id") != report_ref.get("id")):
                return _preflight_failed(
                    "MODEL_PACKAGE_SCAN_EVIDENCE_MISSING", project_id=project_id, artifact_id=artifact_id,
                    task_type=task_type, source=source, operation_key=operation_key,
                )
            target = self.storage.prepare_scan_target(storage_key, expected_sha256=sha256, expected_size=size)
            target_path = _get(target, "path")
            target_sha = _get(target, "sha256")
            target_size = _get(target, "size_bytes")
            target_identity = _get(target, "identity")
            if (target_sha != sha256 or target_size != size or not isinstance(target_identity, tuple)
                    or len(target_identity) != 2 or any(type(part) is not int for part in target_identity)
                    or not isinstance(target_path, (str, bytes))
                    and not hasattr(target_path, "__fspath__")):
                return _preflight_failed(
                    "MODEL_PACKAGE_STORAGE_BINDING_INVALID", project_id=project_id, artifact_id=artifact_id,
                    task_type=task_type, source=source, operation_key=operation_key,
                )
            report_ok, scanned_members = _scan_report_is_bound(
                report, project_id=project_id, artifact_id=artifact_id,
                sha256=sha256, size_bytes=size, identity=target_identity,
                approved_profile_ids=self.approved_scan_profile_ids,
            )
            if not report_ok:
                return _preflight_failed(
                    "MODEL_PACKAGE_MEMBER_SCAN_BINDING_INVALID", project_id=project_id, artifact_id=artifact_id,
                    task_type=task_type, source=source, operation_key=operation_key,
                )
            request: dict[str, object] = {
                "op": "verify_gc_safetensors",
                "project_id": project_id,
                "artifact_id": artifact_id,
                "task_type": task_type,
                "package_path": str(target_path),
                "expected_sha256": sha256,
                "expected_size_bytes": size,
                "allowed_loader_ids": list(self.allowed_loader_ids),
            }
            if recovery_port is None or not isinstance(recovery_binding, Mapping):
                return _preflight_failed(
                    "MODEL_PACKAGE_RECOVERY_UNAVAILABLE", project_id=project_id, artifact_id=artifact_id,
                    task_type=task_type, source=source, operation_key=operation_key,
                )
            execution_binding = {
                **dict(recovery_binding),
                "artifact_sha256": sha256,
                "artifact_size_bytes": size,
            }

            def before_launch(request_ref: str, request_sha256: str) -> None:
                if check_fence is not None:
                    check_fence()
                recovery_port.before_launch(execution_binding, request_ref, request_sha256)

            def stop_sink(
                request_ref: str, request_sha256: str,
                termination: Mapping[str, object],
            ) -> None:
                recovery_port.stop_sink(execution_binding, request_ref, request_sha256, termination)

            executor_attempted = True
            result = self.executor.execute(
                request, cancelled=cancelled, check_fence=check_fence,
                before_launch=before_launch, stop_sink=stop_sink,
            )
            if not isinstance(result, Mapping):
                return _failed("MODEL_PACKAGE_EXECUTOR_FAILED", project_id=project_id, artifact_id=artifact_id, task_type=task_type)
            request_ref = result.get("request_ref")
            request_sha256 = result.get("request_sha256")
            termination = result.get("termination")
            execution_receipt = {
                "request_ref": request_ref,
                "request_sha256": request_sha256,
                "termination": dict(termination) if isinstance(termination, Mapping) else None,
                "executor_status": result.get("status"),
                "executor_error_code": result.get("error_code"),
                "operation_key": operation_key,
                "ledger_stop_confirmed": False,
            }
            if isinstance(termination, Mapping) and (
                termination.get("request_ref") != request_ref
                or termination.get("request_sha256") != request_sha256
            ):
                return _with_execution_receipt(
                    _failed("MODEL_PACKAGE_EXECUTOR_RECEIPT_BINDING_INVALID", project_id=project_id, artifact_id=artifact_id, task_type=task_type),
                    execution_receipt,
                )
            if isinstance(termination, Mapping) and isinstance(request_ref, str) and isinstance(request_sha256, str):
                execution_receipt["ledger_stop_confirmed"] = recovery_port.confirmed_stop(
                    execution_binding, request_ref, request_sha256,
                )
            if check_fence is not None:
                check_fence()
            self.storage.assert_scan_target_unchanged(target)
            if result.get("status") != "SUCCEEDED":
                code = "MODEL_PACKAGE_EXECUTOR_FAILED"
                if result.get("error_code") == "CANCELLED":
                    code = "MODEL_PACKAGE_VALIDATION_CANCELLED"
                elif result.get("error_code") == "TERMINATION_UNCONFIRMED":
                    code = "MODEL_PACKAGE_TERMINATION_UNCONFIRMED"
                return {
                    **_failed(code, project_id=project_id, artifact_id=artifact_id, task_type=task_type),
                    "_execution_receipt": execution_receipt,
                }
            evidence = result.get("evidence")
            if not isinstance(evidence, Mapping):
                return _with_execution_receipt(
                    _failed("MODEL_PACKAGE_EXECUTOR_EVIDENCE_INVALID", project_id=project_id, artifact_id=artifact_id, task_type=task_type),
                    execution_receipt,
                )
            if (evidence.get("project_id") != project_id or evidence.get("artifact_id") != artifact_id
                    or evidence.get("task_type") != task_type or evidence.get("artifact_sha256") != sha256):
                return _with_execution_receipt(
                    _failed("MODEL_PACKAGE_VERIFIER_BINDING_INVALID", project_id=project_id, artifact_id=artifact_id, task_type=task_type),
                    execution_receipt,
                )
            if evidence.get("valid") is not True:
                return {**dict(evidence), "_execution_receipt": execution_receipt}
            manifest = evidence.get("manifest")
            if (not isinstance(manifest, Mapping) or manifest.get("artifact_sha256") != sha256
                    or manifest.get("loader_id") != FIXED_LOADER_ID
                    or manifest.get("format") != "safetensors"):
                return _with_execution_receipt(
                    _failed("MODEL_PACKAGE_VERIFIER_BINDING_INVALID", project_id=project_id, artifact_id=artifact_id, task_type=task_type),
                    execution_receipt,
                )
            checks = evidence.get("checks")
            check_map = {item.get("step"): item.get("status") for item in checks if isinstance(item, Mapping)} if isinstance(checks, list) else {}
            if (not isinstance(checks, list) or len(checks) != len(_REQUIRED_CHECKS)
                    or len(check_map) != len(_REQUIRED_CHECKS)
                    or set(check_map) != set(_REQUIRED_CHECKS)
                    or any(check_map.get(step) != "PASSED" for step in _REQUIRED_CHECKS)):
                return _with_execution_receipt(
                    _failed("MODEL_PACKAGE_VERIFIER_CHECKS_INVALID", project_id=project_id, artifact_id=artifact_id, task_type=task_type),
                    execution_receipt,
                )
            member_hashes = evidence.get("member_hashes")
            member_sizes = evidence.get("member_sizes")
            if not isinstance(member_hashes, Mapping) or not isinstance(member_sizes, Mapping):
                return _with_execution_receipt(
                    _failed("MODEL_PACKAGE_MEMBER_EVIDENCE_MISSING", project_id=project_id, artifact_id=artifact_id, task_type=task_type),
                    execution_receipt,
                )
            for name, (digest, member_size) in scanned_members.items():
                if member_hashes.get(name) != digest or member_sizes.get(name) != member_size:
                    return _with_execution_receipt(
                        _failed("MODEL_PACKAGE_MEMBER_SCAN_HASH_MISMATCH", project_id=project_id, artifact_id=artifact_id, task_type=task_type),
                        execution_receipt,
                    )
            snapshot = dict(manifest)
            snapshot["artifact_sha256"] = sha256
            snapshot["member_hashes"] = dict(member_hashes)
            snapshot["container_size_bytes"] = size
            return {**dict(evidence), "manifest": snapshot, "_execution_receipt": execution_receipt}
        except _CancellationReadFailed:
            raise
        except Exception:
            # A lease/fence failure must escape to the worker instead of being
            # converted into a normal validation result. Other post-executor
            # failures retain the physical stop receipt for safe recovery.
            if check_fence is not None:
                check_fence()
            failure = _failed("MODEL_PACKAGE_VERIFICATION_UNAVAILABLE", project_id=project_id, artifact_id=artifact_id, task_type=task_type)
            if execution_receipt is not None:
                return _with_execution_receipt(failure, execution_receipt)
            # A raised executor call has unknown physical outcome. Only a
            # failure before attempting the executor may carry preflight proof.
            if not executor_attempted:
                return _with_execution_receipt(
                    failure,
                    _preflight_receipt(
                        project_id=project_id, source=source, task_type=task_type,
                        operation_key=operation_key, error_code="MODEL_PACKAGE_VERIFICATION_UNAVAILABLE",
                    ),
                )
            return failure


__all__ = ["ModelPackageAssetVerifier"]
