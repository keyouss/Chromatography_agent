from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parents[1]
_MIGRATION_PATH = _ROOT / "alembic" / "versions" / "0019_batch_export_status_repair.py"
_SPEC = importlib.util.spec_from_file_location("batch_export_status_repair", _MIGRATION_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MIGRATION = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MIGRATION)


def _export(export_id: str = "export-1", *, status: str = "QUEUED", artifact_id: str | None = None) -> dict:
    return {"id": export_id, "project_id": "project-1", "job_id": f"job-{export_id}", "status": status, "artifact_id": artifact_id}


def _job(export: dict, *, status: str = "QUEUED", project_id: str = "project-1", kind: str = "batch_prediction", job_id: str | None = None, result_ref: dict | None = None) -> dict:
    return {
        "id": job_id or export["job_id"],
        "project_id": project_id,
        "kind": kind,
        "resource_ref": {"type": "batch_export", "id": export["id"]},
        "status": status,
        "result_ref": result_ref if result_ref is not None else ({"type": "artifact", "id": "artifact-1"} if status == "SUCCEEDED" else None),
    }


def _artifact(export: dict, *, artifact_id: str = "artifact-1", project_id: str = "project-1", kind: str = "batch_export", status: str = "AVAILABLE", scan_status: str = "PASSED") -> dict:
    return {
        "id": artifact_id,
        "project_id": project_id,
        "kind": kind,
        "size_bytes": 10,
        "sha256": "a" * 64,
        "storage_key": "batch/export/artifact-1",
        "status": status,
        "scan_status": scan_status,
        "metadata_json": {"source_ref": {"type": "batch_export", "id": export["id"]}},
    }


def test_empty_and_all_five_job_states_are_classified_without_guessing() -> None:
    assert _MIGRATION.plan_repairs([], [], []) == []
    exports = [_export(f"export-{index}") for index in range(5)]
    statuses = ("QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED")
    jobs = [_job(export, status=status) for export, status in zip(exports, statuses)]
    artifacts = [_artifact(exports[2])]

    assert _MIGRATION.plan_repairs(exports, jobs, artifacts) == [
        ("export-1", "RUNNING", None),
        ("export-2", "SUCCEEDED", "artifact-1"),
        ("export-3", "FAILED", None),
        ("export-4", "CANCELED", None),
    ]


def test_already_resolved_states_are_protected() -> None:
    exports = [_export(f"export-{status}", status=status) for status in ("RUNNING", "SUCCEEDED", "FAILED", "CANCELED")]
    assert _MIGRATION.plan_repairs(exports, [], []) == []


@pytest.mark.parametrize(
    ("case", "jobs", "artifacts"),
    [
        ("missing job", [], []),
        ("wrong project", lambda export: [_job(export, project_id="project-2")], []),
        ("wrong kind", lambda export: [_job(export, kind="batch_export")], []),
        ("wrong job id", lambda export: [_job(export, job_id="other-job")], []),
        ("unknown status", lambda export: [_job(export, status="PARTIAL")], []),
        ("multiple jobs", lambda export: [_job(export), _job(export, job_id="job-duplicate")], []),
        ("missing succeeded artifact", lambda export: [_job(export, status="SUCCEEDED")], []),
        ("multiple artifacts", lambda export: [_job(export, status="SUCCEEDED")], lambda export: [_artifact(export), _artifact(export, artifact_id="artifact-2")]),
        ("wrong artifact project", lambda export: [_job(export, status="SUCCEEDED")], lambda export: [_artifact(export, project_id="project-2")]),
        ("wrong artifact kind", lambda export: [_job(export, status="SUCCEEDED")], lambda export: [_artifact(export, kind="dataset_package")]),
        ("unverified artifact", lambda export: [_job(export, status="SUCCEEDED")], lambda export: [_artifact(export, scan_status="PENDING")]),
        ("non-success artifact conflict", lambda export: [_job(export, status="FAILED")], lambda export: [_artifact(export)]),
    ],
)
def test_ambiguous_or_invalid_evidence_aborts(case, jobs, artifacts) -> None:
    export = _export()
    job_rows = jobs(export) if callable(jobs) else jobs
    artifact_rows = artifacts(export) if callable(artifacts) else artifacts
    with pytest.raises(_MIGRATION.RepairConflict):
        _MIGRATION.plan_repairs([export], job_rows, artifact_rows)


def test_unknown_existing_export_status_aborts() -> None:
    with pytest.raises(_MIGRATION.RepairConflict):
        _MIGRATION.plan_repairs([_export(status="PARTIAL")], [], [])


def test_rerunning_after_status_and_artifact_update_is_a_noop() -> None:
    export = _export()
    jobs = [_job(export, status="SUCCEEDED")]
    artifacts = [_artifact(export)]
    first_plan = _MIGRATION.plan_repairs([export], jobs, artifacts)
    assert first_plan == [("export-1", "SUCCEEDED", "artifact-1")]

    repaired = {**export, "status": "SUCCEEDED", "artifact_id": "artifact-1"}
    assert _MIGRATION.plan_repairs([repaired], jobs, artifacts) == []


def test_existing_conflicting_artifact_id_aborts() -> None:
    export = _export(artifact_id="artifact-other")
    with pytest.raises(_MIGRATION.RepairConflict):
        _MIGRATION.plan_repairs([export], [_job(export, status="SUCCEEDED")], [_artifact(export)])


@pytest.mark.parametrize(
    "job_update",
    [
        {"result_ref": None},
        {"result_ref": {"type": "artifact", "id": "other-artifact"}},
        {"result_ref": {"type": "artifact", "id": "artifact-1", "extra": "unexpected"}},
    ],
)
def test_succeeded_requires_job_result_ref_to_bind_artifact(job_update) -> None:
    export = _export()
    job = _job(export, status="SUCCEEDED")
    job["result_ref"] = job_update["result_ref"]
    with pytest.raises(_MIGRATION.RepairConflict):
        _MIGRATION.plan_repairs([export], [job], [_artifact(export)])


@pytest.mark.parametrize("field", ["sha256", "storage_key", "size_bytes"])
def test_succeeded_requires_artifact_hash_format_and_storage_metadata(field: str) -> None:
    export = _export()
    artifact = _artifact(export)
    artifact[field] = None if field != "size_bytes" else 0
    with pytest.raises(_MIGRATION.RepairConflict):
        _MIGRATION.plan_repairs([export], [_job(export, status="SUCCEEDED")], [artifact])


def test_protected_state_does_not_hide_an_untrusted_queued_row() -> None:
    protected = _export("protected", status="SUCCEEDED", artifact_id="artifact-existing")
    untrusted = _export("untrusted")
    with pytest.raises(_MIGRATION.RepairConflict):
        _MIGRATION.plan_repairs([protected, untrusted], [], [])


def test_protected_state_is_skipped_even_with_untrusted_evidence() -> None:
    protected = _export("protected", status="SUCCEEDED", artifact_id="artifact-existing")
    bad_job = _job(protected, status="PARTIAL")
    assert _MIGRATION.plan_repairs([protected], [bad_job], []) == []


def test_non_success_rejects_ghost_artifact_result_ref() -> None:
    export = _export()
    job = _job(export, status="FAILED", result_ref={"type": "artifact", "id": "ghost-artifact"})
    with pytest.raises(_MIGRATION.RepairConflict):
        _MIGRATION.plan_repairs([export], [job], [])


def test_conflict_diagnostics_do_not_expose_export_id() -> None:
    export = _export("sensitive-export-id")
    with pytest.raises(_MIGRATION.RepairConflict) as exc_info:
        _MIGRATION.plan_repairs([export], [], [])
    assert export["id"] not in str(exc_info.value)


def test_upgrade_fails_closed_in_offline_mode(monkeypatch) -> None:
    monkeypatch.setattr(_MIGRATION.context, "is_offline_mode", lambda: True)
    with pytest.raises(RuntimeError, match="requires online Alembic execution"):
        _MIGRATION.upgrade()


def test_downgrade_fails_closed_without_moving_version_or_mutating_data() -> None:
    with pytest.raises(RuntimeError, match="irreversible"):
        _MIGRATION.downgrade()

