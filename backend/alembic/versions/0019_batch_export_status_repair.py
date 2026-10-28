"""Repair legacy batch-export status values introduced by 0014.

Migration 0014 supplied ``QUEUED`` as a server default for rows created by
0011.  This migration reconstructs only those legacy rows from their exact job
and artifact references.  It deliberately refuses to guess when the
historical evidence is incomplete or ambiguous.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from alembic import context, op
import sqlalchemy as sa


revision = "0019_batch_export_status_repair"
down_revision = "0018_merge_full_replay_heads"
branch_labels = None
depends_on = None

_VALID_STATUSES = frozenset({"QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED"})
_BATCH_JOB_KIND = "batch_prediction"
_BATCH_EXPORT_TYPE = "batch_export"
_ARTIFACT_RESULT_TYPE = "artifact"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RepairConflict(RuntimeError):
    """Historical evidence is missing, ambiguous, or inconsistent."""


def _safe_id(value: Any) -> str:
    """Return a short diagnostic token without exposing a business id."""

    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def _mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return None
        return decoded if isinstance(decoded, Mapping) else None
    return None


def _canonical_ref(value: Any, *, type_name: str, resource_id: str) -> bool:
    ref = _mapping(value)
    return ref == {"type": type_name, "id": resource_id}


def _artifact_source_ref(artifact: Mapping[str, Any]) -> Any:
    metadata = _mapping(artifact.get("metadata_json"))
    return metadata.get("source_ref") if metadata is not None else None


def classify_export(
    export: Mapping[str, Any],
    jobs: Iterable[Mapping[str, Any]],
    artifacts: Iterable[Mapping[str, Any]],
) -> tuple[str, str | None]:
    """Return the evidence-backed status and artifact id for one legacy row."""

    export_id = export.get("id")
    project_id = export.get("project_id")
    expected_job_id = export.get("job_id")
    if not all(isinstance(value, str) and value for value in (export_id, project_id, expected_job_id)):
        raise RepairConflict("batch_export identity is incomplete")

    matching_jobs = [
        job for job in jobs
        if _canonical_ref(job.get("resource_ref"), type_name=_BATCH_EXPORT_TYPE, resource_id=export_id)
    ]
    if len(matching_jobs) != 1:
        raise RepairConflict(f"export[{_safe_id(export_id)}]: expected exactly one canonical job, got {len(matching_jobs)}")
    job = matching_jobs[0]
    if (
        job.get("id") != expected_job_id
        or job.get("project_id") != project_id
        or job.get("kind") != _BATCH_JOB_KIND
    ):
        raise RepairConflict(f"export[{_safe_id(export_id)}]: canonical job identity or project mismatch")

    status = job.get("status")
    if status not in _VALID_STATUSES:
        raise RepairConflict(f"export[{_safe_id(export_id)}]: unsupported job status {status!r}")

    matching_artifacts = [
        artifact for artifact in artifacts
        if _canonical_ref(
            _artifact_source_ref(artifact),
            type_name=_BATCH_EXPORT_TYPE,
            resource_id=export_id,
        )
    ]
    existing_artifact_id = export.get("artifact_id")
    if status != "SUCCEEDED":
        if job.get("result_ref") is not None or matching_artifacts or existing_artifact_id is not None:
            raise RepairConflict(f"export[{_safe_id(export_id)}]: non-success export has artifact evidence")
        return status, None

    if len(matching_artifacts) != 1:
        raise RepairConflict(f"export[{_safe_id(export_id)}]: expected exactly one canonical artifact, got {len(matching_artifacts)}")
    artifact = matching_artifacts[0]
    artifact_id = artifact.get("id")
    result_ref = job.get("result_ref")
    if (
        not isinstance(artifact_id, str)
        or not _canonical_ref(result_ref, type_name=_ARTIFACT_RESULT_TYPE, resource_id=artifact_id)
        or artifact.get("project_id") != project_id
        or artifact.get("kind") != _BATCH_EXPORT_TYPE
        or artifact.get("status") != "AVAILABLE"
        or artifact.get("scan_status") != "PASSED"
        or not isinstance(artifact.get("size_bytes"), int)
        or isinstance(artifact.get("size_bytes"), bool)
        or artifact.get("size_bytes") <= 0
        or not isinstance(artifact.get("storage_key"), str)
        or not artifact.get("storage_key")
        or not isinstance(artifact.get("sha256"), str)
        or _SHA256_RE.fullmatch(artifact["sha256"]) is None
    ):
        raise RepairConflict(f"export[{_safe_id(export_id)}]: job result or canonical artifact is not verified")
    if existing_artifact_id not in (None, artifact_id):
        raise RepairConflict(f"export[{_safe_id(export_id)}]: existing artifact id conflicts with canonical artifact")
    return status, artifact_id


def plan_repairs(
    exports: Iterable[Mapping[str, Any]],
    jobs: Iterable[Mapping[str, Any]],
    artifacts: Iterable[Mapping[str, Any]],
) -> list[tuple[str, str, str | None]]:
    """Classify all rows before any update is attempted."""

    export_rows = list(exports)
    job_rows = list(jobs)
    artifact_rows = list(artifacts)
    for export in export_rows:
        status = export.get("status")
        if status not in _VALID_STATUSES:
            raise RepairConflict(f"export[{_safe_id(export.get('id'))}]: unsupported export status {status!r}")

    plan: list[tuple[str, str, str | None]] = []
    for export in export_rows:
        if export.get("status") != "QUEUED":
            continue
        status, artifact_id = classify_export(export, job_rows, artifact_rows)
        export_id = export.get("id")
        if not isinstance(export_id, str) or not export_id:
            raise RepairConflict("batch_export id is incomplete")
        if status != "QUEUED" or artifact_id is not None:
            plan.append((export_id, status, artifact_id))
    return plan


def _table_definitions() -> tuple[sa.TableClause, sa.TableClause, sa.TableClause]:
    exports = sa.table(
        "batch_export",
        sa.column("id", sa.String(96)),
        sa.column("project_id", sa.String(96)),
        sa.column("job_id", sa.String(96)),
        sa.column("status", sa.String(16)),
        sa.column("artifact_id", sa.String(96)),
    )
    jobs = sa.table(
        "job",
        sa.column("id", sa.String(96)),
        sa.column("project_id", sa.String(96)),
        sa.column("kind", sa.String(96)),
        sa.column("resource_ref", sa.JSON()),
        sa.column("status", sa.String(20)),
        sa.column("result_ref", sa.JSON()),
    )
    artifacts = sa.table(
        "artifact",
        sa.column("id", sa.String(96)),
        sa.column("project_id", sa.String(96)),
        sa.column("kind", sa.String(64)),
        sa.column("size_bytes", sa.BigInteger()),
        sa.column("sha256", sa.String(64)),
        sa.column("storage_key", sa.String(512)),
        sa.column("status", sa.String(16)),
        sa.column("scan_status", sa.String(20)),
        sa.column("metadata_json", sa.JSON()),
    )
    return exports, jobs, artifacts


def upgrade() -> None:
    # A data repair cannot be represented faithfully as an offline SQL script.
    # Never let an offline upgrade mark this revision as applied without
    # performing evidence classification against live database rows.
    if context.is_offline_mode():
        raise RuntimeError("0019 batch export status repair requires online Alembic execution")

    bind = op.get_bind()
    exports, jobs, artifacts = _table_definitions()
    export_rows = [dict(row) for row in bind.execute(sa.select(*exports.c)).mappings()]
    job_rows = [dict(row) for row in bind.execute(sa.select(*jobs.c)).mappings()]
    artifact_rows = [dict(row) for row in bind.execute(sa.select(*artifacts.c)).mappings()]
    plan = plan_repairs(export_rows, job_rows, artifact_rows)

    for export_id, status, artifact_id in plan:
        values: dict[str, Any] = {"status": status}
        if status == "SUCCEEDED":
            values["artifact_id"] = artifact_id
        result = bind.execute(
            sa.update(exports)
            .where(exports.c.id == export_id, exports.c.status == "QUEUED")
            .values(**values)
        )
        if result.rowcount != 1:
            raise RepairConflict(f"export[{_safe_id(export_id)}]: row changed while applying repair")


def downgrade() -> None:
    # The repair is intentionally irreversible; the original status is not
    # recoverable without an immutable pre-migration snapshot.  Refuse both
    # online and offline downgrade so Alembic cannot move the version marker
    # back while leaving repaired rows in place.
    raise RuntimeError("0019 batch export status repair is irreversible; restore an immutable snapshot instead")

