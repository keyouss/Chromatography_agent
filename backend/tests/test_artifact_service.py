from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from chrom_platform.artifact_core.models import Artifact
from chrom_platform.artifact_core.repository import SQLAlchemyArtifactRepository
from chrom_platform.artifact_core.schemas import UploadCreate
from chrom_platform.artifact_core.schemas import ArchiveRequest
from chrom_platform.artifact_core.service import ArtifactJobPort, ArtifactService, ArtifactServiceError, JobSubmission, ScanResult
from chrom_platform.artifact_core.storage import LocalArtifactStore, StorageContentConflict, StorageContentInvalid
from chrom_platform.core.dto import ActorContext, ResourceRef


class Repository:
    def __init__(self) -> None:
        self.rows: dict[str, Artifact] = {}

    def create(self, row: Artifact) -> Artifact:
        self.rows[row.id] = row
        return row

    def get(self, artifact_id: str, *, for_update: bool = False) -> Artifact | None:
        return self.rows.get(artifact_id)

    def get_by_upload_id(self, upload_id: str, *, for_update: bool = False) -> Artifact | None:
        return next((row for row in self.rows.values() if row.metadata_json.get("upload_id") == upload_id), None)

    def save(self, row: Artifact) -> Artifact:
        self.rows[row.id] = row
        return row


class Scanner:
    def scan(self, **kwargs):
        return ScanResult("PASSED")


class Confirmation:
    def __init__(self):
        self.actions = []

    def consume(self, confirmation_id, **kwargs):
        self.actions.append(kwargs["action"])


class Audit:
    def __init__(self):
        self.entries = []

    def record(self, **kwargs):
        self.entries.append(kwargs)


class Queue:
    transactional_outbox = True

    def __init__(self):
        self.calls = []

    def enqueue(self, **kwargs):
        self.calls.append(kwargs)
        return "job-1"

    def enqueue_validation(self, **kwargs):
        return JobSubmission(job_id="job-validation-1", resource_ref=ResourceRef(type="artifact", id=kwargs["artifact_id"]))


def actor(*projects: str) -> ActorContext:
    return ActorContext(
        actor_id="actor-1",
        tenant_id=None,
        project_ids=list(projects),
        auth_method="deployment_default",
        permissions=["artifact:write", "artifact:read", "artifact:delete"],
        policy_version="test",
    )


def test_local_store_rejects_traversal_and_publishes_atomically(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path, max_size_bytes=32)
    with pytest.raises(StorageContentInvalid):
        store._path_for("../outside")
    content = b"CCO\n"
    key, size, digest = store.put("art-1", content, expected_size=len(content))
    assert store.read(key) == content
    assert (size, digest) == (len(content), hashlib.sha256(content).hexdigest())
    assert not list(tmp_path.rglob(".upload-*"))
    with pytest.raises(StorageContentConflict):
        store.put("art-1", b"NCC\n", expected_size=len(content))


def test_local_store_rejects_size_and_hash_mismatch(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path, max_size_bytes=4)
    with pytest.raises(StorageContentInvalid):
        store.put("art-1", b"123", expected_size=2)
    with pytest.raises(StorageContentInvalid):
        store.put("art-2", b"123", expected_size=3, expected_sha256="0" * 64)


def test_service_keeps_cross_project_artifacts_invisible(tmp_path: Path) -> None:
    repository = Repository()
    service = ArtifactService(repository, LocalArtifactStore(tmp_path), scanner=Scanner(), jobs=Queue())
    upload = service.create_upload(
        UploadCreate(project_id="project-a", kind="batch_input", file_name="a.csv", media_type="text/csv", size_bytes=3),
        actor("project-a"),
    )
    service.receive_content(upload.id, b"abc", actor("project-a"))
    service.complete_upload(upload.id, actor("project-a"))
    with pytest.raises(ArtifactServiceError) as error:
        service.get_artifact(upload.artifact_id, actor("project-b"))
    assert error.value.status_code == 404


def test_scanner_not_configured_never_marks_available(tmp_path: Path) -> None:
    repository = Repository()
    service = ArtifactService(repository, LocalArtifactStore(tmp_path))
    upload = service.create_upload(
        UploadCreate(project_id="project-a", kind="batch_input", file_name="a.csv", media_type="text/csv", size_bytes=3),
        actor("project-a"),
    )
    service.receive_content(upload.id, b"abc", actor("project-a"))
    assert service.get_upload(upload.id, actor("project-a")).status == "RECEIVED"
    with pytest.raises(ArtifactServiceError) as error:
        service.complete_upload(upload.id, actor("project-a"))
    assert error.value.status_code == 503
    assert repository.rows[upload.artifact_id].status == "RECEIVED"
    assert service.get_upload(upload.id, actor("project-a")).status == "RECEIVED"


def test_completed_upload_query_reports_completed_after_available(tmp_path: Path) -> None:
    repository = Repository()
    service = ArtifactService(repository, LocalArtifactStore(tmp_path), scanner=Scanner(), jobs=Queue())
    upload = service.create_upload(
        UploadCreate(project_id="project-a", kind="batch_input", file_name="a.csv", media_type="text/csv", size_bytes=3),
        actor("project-a"),
    )
    service.receive_content(upload.id, b"abc", actor("project-a"))
    service.complete_upload(upload.id, actor("project-a"))
    assert service.get_upload(upload.id, actor("project-a")).status == "COMPLETED"


def test_public_artifact_dto_does_not_expose_storage_key(tmp_path: Path) -> None:
    repository = Repository()
    service = ArtifactService(repository, LocalArtifactStore(tmp_path), scanner=Scanner(), jobs=Queue())
    upload = service.create_upload(
        UploadCreate(project_id="project-a", kind="batch_input", file_name="a.csv", media_type="text/csv", size_bytes=3),
        actor("project-a"),
    )
    service.receive_content(upload.id, b"abc", actor("project-a"))
    service.complete_upload(upload.id, actor("project-a"))
    payload = service.get_artifact(upload.artifact_id, actor("project-a")).model_dump()
    assert "storage_key" not in payload


def _available_service(tmp_path: Path, confirmation: Confirmation, audit: Audit, queue: Queue | None = None):
    repository = Repository()
    queue = queue or Queue()
    service = ArtifactService(repository, LocalArtifactStore(tmp_path), scanner=Scanner(), confirmation=confirmation, audit=audit, deletion_queue=queue, jobs=queue)
    upload = service.create_upload(
        UploadCreate(project_id="project-a", kind="batch_input", file_name="a.csv", media_type="text/csv", size_bytes=3),
        actor("project-a"),
    )
    service.receive_content(upload.id, b"abc", actor("project-a"))
    service.complete_upload(upload.id, actor("project-a"))
    repository.rows[upload.artifact_id].status = "AVAILABLE"
    repository.rows[upload.artifact_id].scan_status = "PASSED"
    return service, upload.artifact_id


def test_archive_and_delete_terminal_retries_do_not_reconsume_or_requeue(tmp_path: Path) -> None:
    confirmation = Confirmation()
    audit = Audit()
    service, artifact_id = _available_service(tmp_path, confirmation, audit)
    first_archive = service.archive(artifact_id, ArchiveRequest(confirmation_id="c-1"), actor("project-a"))
    second_archive = service.archive(artifact_id, ArchiveRequest(confirmation_id="c-2"), actor("project-a"))
    assert first_archive.status == second_archive.status == "ARCHIVED"
    assert confirmation.actions == ["resource.archive"]

    confirmation = Confirmation()
    audit = Audit()
    queue = Queue()
    service, artifact_id = _available_service(tmp_path / "delete", confirmation, audit, queue)
    first_delete = service.delete(artifact_id, "c-3", actor("project-a"))
    second_delete = service.delete(artifact_id, "c-4", actor("project-a"))
    assert first_delete.job_id == second_delete.job_id == "job-1"
    assert confirmation.actions == ["artifact.delete"]
    assert len(queue.calls) == 1
    assert audit.entries[-1]["before"] == {"status": "AVAILABLE"}


def test_mark_deleted_requires_explicit_transaction_boundary(tmp_path: Path) -> None:
    confirmation = Confirmation()
    audit = Audit()
    service, artifact_id = _available_service(tmp_path, confirmation, audit)
    with pytest.raises(ArtifactServiceError) as error:
        service.mark_deleted(artifact_id)
    assert error.value.status_code == 503


def test_artifact_repository_rejects_session_factory_for_write_scope() -> None:
    with pytest.raises(TypeError):
        SQLAlchemyArtifactRepository(lambda: None)
