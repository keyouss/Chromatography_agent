from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from chrom_platform.infrastructure.models import Confirmation, Project
from chrom_platform.modules.data_center.errors import (
    ConfirmationRequiredError,
    DataConflictError,
    DataUnavailableError,
)
from chrom_platform.modules.data_center.persistence import (
    DataCenterBase,
    DataImportRow,
    DataVersionReferenceRow,
    DataVersionRow,
    SQLAlchemyDataRepository,
)
from chrom_platform.modules.data_center.schemas import (
    ConfirmationConsumeRequest,
    DataCommitRequest,
    DataDomainSchema,
    DataImport,
    DataImportValidation,
    DataVersionDraft,
    DomainField,
    ResourceRef,
    ValidationCounts,
)


class SpySession(Session):
    commits = 0
    closes = 0

    def commit(self):
        type(self).commits += 1
        return super().commit()

    def close(self):
        type(self).closes += 1
        return super().close()


def _session() -> SpySession:
    engine = create_engine("sqlite:///:memory:")
    # The shared M1 confirmation check uses MySQL CHAR_LENGTH; register the
    # equivalent SQLite function for this contract-level persistence test.
    event.listen(engine, "connect", lambda dbapi, _: dbapi.create_function("CHAR_LENGTH", 1, len))
    # The shared confirmation model is needed only by the atomic commit test.
    Project.__table__.create(engine)
    Confirmation.__table__.create(engine)
    DataCenterBase.metadata.create_all(engine)
    return SpySession(bind=engine)


def _draft(project: str = "p1", domain: str = "sfc") -> DataVersionDraft:
    return DataVersionDraft(
        project_id=project, domain_id=domain, source_import_id="import-1",
        snapshot_artifact_id="snapshot-1", schema_version="1.0",
        sha256="a" * 64, record_count=1,
    )


def _import(*, revision: int = 1, config_hash: str = "c" * 64, status: str = "DRAFT") -> DataImport:
    now = datetime.now(timezone.utc)
    return DataImport(
        id="import-1", project_id="p1", domain_id="sfc", artifact_id="artifact-1",
        schema_version="1.0", import_mode="append", field_mapping={"sample": "sample"},
        source_description="fixture", revision=revision, config_hash=config_hash,
        etag=f'"{config_hash[:48]}-{revision}"', status=status,
        request_snapshot=None, created_at=now, updated_at=now,
    )


def _validation(import_value: DataImport, *, parser_revision: str = "parser-1", parser_hash: str = "d" * 64) -> DataImportValidation:
    return DataImportValidation(
        id="validation-1", import_id=import_value.id, revision=import_value.revision,
        config_hash=import_value.config_hash, status="VALID",
        counts=ValidationCounts(new=1, updated=0, unchanged=0, invalid=0, duplicate=0),
        parser_revision=parser_revision, parser_hash=parser_hash,
        created_at=datetime.now(timezone.utc),
    )


def test_repository_never_owns_external_session_and_enforces_project_scope():
    SpySession.commits = SpySession.closes = 0
    session = _session()
    repository = SQLAlchemyDataRepository(session)
    value = repository.allocate_and_save_new_data_version(_draft())
    assert repository.get_data_version("p1", value.id) is not None
    assert repository.get_data_version("p2", value.id) is None
    assert SpySession.commits == 0
    assert SpySession.closes == 0
    session.rollback()
    session.close()


def test_version_scope_unique_constraint_and_reference_idempotency_blocks_archive():
    session = _session()
    repository = SQLAlchemyDataRepository(session, id_factory=lambda prefix: f"{prefix}-fixed")
    value = repository.allocate_and_save_new_data_version(_draft())
    duplicate = DataVersionRow(
        id="other", project_id="p1", domain_id="sfc", version=1, sha256="b" * 64,
        status="READY", record_count=1, source_import_id="other-import", snapshot_artifact_id="s2",
        schema_version="1.0", created_at=datetime.now(timezone.utc), referenced_by_count=0,
    )
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(duplicate)
            session.flush()
    assert repository.add_data_version_reference("p1", value.id, "training-1").referenced_by_count == 1
    assert repository.add_data_version_reference("p1", value.id, "training-1").referenced_by_count == 1
    with pytest.raises(DataConflictError):
        repository.archive_data_version("p1", value.id)
    session.rollback()
    session.close()


def test_parser_revision_and_hash_mismatch_persist_as_stale():
    session = _session()
    repository = SQLAlchemyDataRepository(session, parser_revision="parser-2", parser_hash="e" * 64)
    current = _import()
    repository.save_import(current)
    result = repository.save_validation("p1", _validation(current))
    assert result.status == "STALE"
    session.rollback()
    session.close()


def test_confirmation_binding_and_atomic_rollback_do_not_leave_consumed_state():
    session = _session()
    session.add(Project(id="p1", slug="p1", name="Project 1", status="ACTIVE", revision=1))
    current = _import()
    repository = SQLAlchemyDataRepository(session, snapshot_provider=lambda request, confirmation: {
        "snapshot_artifact_id": "snapshot-1", "sha256": "a" * 64, "record_count": -1,
    })
    repository.save_import(current)
    validation = _validation(current)
    repository.save_validation("p1", validation)
    session.add(Confirmation(
        id="confirmation-1", project_id="p1", actor_id="actor-1",
        resource_ref={"type": "data_import", "id": current.id}, action="data_import.commit",
        revision=1, config_hash=current.config_hash, binding_hash="f" * 64,
        policy_version="policy-v1", summary_snapshot={}, status="CONFIRMED",
        created_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    ))
    session.flush()
    request = DataCommitRequest(data_import=current, validation=validation, domain=DataDomainSchema(
        id="sfc", version="1.0", chromatography_type="sfc",
        record_schema={"sample": DomainField(type="string")}, unit_definitions={},
        identity_fields=["sample"], deduplication_policy="reject", supported_import_modes=["append"],
    ))
    confirmation = ConfirmationConsumeRequest(
        confirmation_id="confirmation-1", project_id="p1",
        resource_ref=ResourceRef(type="data_import", id=current.id), action="data_import.commit",
        revision=1, config_hash=current.config_hash,
    )
    with pytest.raises(Exception):
        with session.begin():
            repository.commit(request, confirmation)
    row = session.scalar(select(Confirmation).where(Confirmation.id == "confirmation-1"))
    assert row.status == "CONFIRMED"
    assert session.scalar(select(DataVersionRow).where(DataVersionRow.source_import_id == current.id)) is None

    wrong = confirmation.model_copy(update={"config_hash": "0" * 64})
    with pytest.raises(ConfirmationRequiredError):
        repository._consume_confirmation(wrong)
    session.rollback()
    session.close()
