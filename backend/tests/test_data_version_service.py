from __future__ import annotations

from datetime import datetime, timezone

import pytest

from chrom_platform.modules.data_center.errors import DataConflictError, DataUnavailableError
from chrom_platform.modules.data_center.schemas import (
    ConfirmationConsumeRequest,
    DataCommitRequest,
    DataDomainSchema,
    DataImportCreate,
    DataImportPatch,
    DataImportValidation,
    DataVersion,
    DataVersionDraft,
    DomainField,
    ValidationCounts,
)
from chrom_platform.modules.data_center.service import DataImportService, DataVersionService, StaticDataDomainRegistry
from chrom_platform.modules.data_center.testing import InMemoryDataRepository


def domain() -> DataDomainSchema:
    return DataDomainSchema(
        id="sfc-experiment", version="1.0", chromatography_type="sfc",
        record_schema={"sample_id": DomainField(type="string"), "rt": DomainField(type="number", unit="min")},
        unit_definitions={"rt": "min"}, identity_fields=["sample_id"],
        deduplication_policy="reject", supported_import_modes=["append"],
    )


class FakeValidation:
    def __init__(self, repo=None):
        self.repo = repo

    def validate(self, data_import, domain):
        return DataImportValidation(
            id="div_1", import_id=data_import.id, revision=data_import.revision,
            config_hash=data_import.config_hash, status="VALID",
            counts=ValidationCounts(new=1, updated=0, unchanged=0, invalid=0, duplicate=0),
            created_at=datetime.now(timezone.utc),
        )


class FakeAtomicCommit:
    def __init__(self, repo, *, reject=False, patch_during_validate=None):
        self.repo, self.reject, self.requests = repo, reject, []

    def commit(self, request: DataCommitRequest, confirmation: ConfirmationConsumeRequest):
        self.requests.append((request, confirmation))
        if self.reject:
            raise DataConflictError("confirmation rejected inside atomic transaction")
        version = self.repo.allocate_and_save_new_data_version(DataVersionDraft(
            project_id=request.data_import.project_id, domain_id=request.data_import.domain_id,
            source_import_id=request.data_import.id, snapshot_artifact_id="artifact_snapshot",
            schema_version=request.data_import.schema_version, sha256="b" * 64, record_count=1,
        ))
        self.repo.save_import(request.data_import.model_copy(update={"status": "COMMITTED", "new_data_version_id": version.id}))
        return version


def create_service(*, validation=True, commit=True):
    repo = InMemoryDataRepository()
    service = DataImportService(
        repo, StaticDataDomainRegistry([domain()]),
        validation_port=FakeValidation(repo) if validation else None,
        atomic_commit_port=FakeAtomicCommit(repo) if commit else None,
    )
    return repo, service


def request(project="p1"):
    return DataImportCreate(
        project_id=project, domain_id="sfc-experiment", artifact_id="artifact_input",
        schema_version="1.0", import_mode="append",
        field_mapping={"sample_id": "sample", "rt": "retention time"}, source_description="fixture",
    )


def test_import_revision_hash_etag_and_stale_validation():
    repo, service = create_service()
    created = service.create(request())
    result = service.validate("p1", created.id)
    assert result.status == "VALID"
    changed = service.patch("p1", created.id, DataImportPatch(source_description="changed"), expected_revision=created.revision, if_match=created.etag)
    assert changed.revision == 2
    assert changed.config_hash != created.config_hash
    assert service.validation("p1", created.id).status == "STALE"


def test_validate_rereads_import_after_parser_changes_revision():
    repo, service = create_service()

    class PatchingParser(FakeValidation):
        def validate(self, data_import, domain):
            current = repo.get_import("p1", data_import.id)
            repo.save_import(current.model_copy(update={"revision": 2, "config_hash": "c" * 64, "etag": '"changed"', "status": "DRAFT"}))
            return super().validate(data_import, domain)

    service._validation_port = PatchingParser(repo)
    created = service.create(request())
    result = service.validate("p1", created.id)
    assert result.status == "STALE"


def test_missing_parser_or_atomic_commit_is_unavailable():
    _, no_parser = create_service(validation=False)
    created = no_parser.create(request())
    with pytest.raises(DataUnavailableError):
        no_parser.validate("p1", created.id)

    _, no_commit = create_service(commit=False)
    created = no_commit.create(request())
    no_commit.validate("p1", created.id)
    with pytest.raises(DataUnavailableError):
        no_commit.commit("p1", created.id, confirmation_id="c1")


def test_atomic_commit_publishes_new_immutable_version_and_scoped_reads():
    repo, service = create_service()
    created = service.create(request())
    service.validate("p1", created.id)
    version = service.commit("p1", created.id, confirmation_id="confirm-1")
    assert version.status == "READY"
    assert service.get("p1", created.id).status == "COMMITTED"
    assert service.get("p1", created.id).new_data_version_id == version.id
    with pytest.raises(DataConflictError):
        DataVersionService(repo).overwrite("p1", version.id, record_count=99)
    with pytest.raises(Exception):
        service.get("p2", created.id)


def test_reference_is_idempotent_and_committed_import_cannot_create_second_version():
    repo, service = create_service()
    created = service.create(request())
    service.validate("p1", created.id)
    version = service.commit("p1", created.id, confirmation_id="confirm-1")
    versions = DataVersionService(repo)
    assert versions.reference("p1", version.id, reference_id="training-1").referenced_by_count == 1
    assert versions.reference("p1", version.id, reference_id="training-1").referenced_by_count == 1
    repo.save_import(repo.get_import("p1", created.id).model_copy(update={"new_data_version_id": "missing", "status": "COMMITTED"}))
    with pytest.raises(DataConflictError):
        service.commit("p1", created.id, confirmation_id="confirm-2")
