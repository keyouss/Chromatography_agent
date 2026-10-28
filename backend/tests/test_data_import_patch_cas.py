from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from chrom_platform.artifact_core.models import Artifact
from chrom_platform.infrastructure.db import Base
from chrom_platform.infrastructure.metadata import compose_metadata
from chrom_platform.infrastructure.models import Job, Project
from chrom_platform.modules.data_center.errors import DataConflictError
from chrom_platform.modules.data_center.persistence import (
    DataCenterBase,
    DataDomainRow,
    DataImportRow,
    SQLAlchemyDataRepository,
)
from chrom_platform.modules.data_center.schemas import DataDomainSchema, DataImportPatch
from chrom_platform.modules.data_center.service import DataImportService, StaticDataDomainRegistry


def _database(status: str = "DRAFT"):
    engine = create_engine("sqlite:///:memory:")

    def configure_sqlite(dbapi, _record):
        dbapi.create_function("CHAR_LENGTH", 1, len)

    event.listen(engine, "connect", configure_sqlite)
    compose_metadata(Base.metadata, DataCenterBase.metadata).create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    with sessions.begin() as session:
        session.add(Project(id="project_fixture", slug="patch-cas", name="fixture"))
        session.add(Artifact(
            id="artifact_fixture", project_id="project_fixture", kind="dataset_package",
            title="fixture.csv", media_type="text/csv", size_bytes=1,
            sha256="b" * 64, storage_key="fixture.csv", status="AVAILABLE",
            scan_status="PASSED", metadata_json={}, created_by="actor_fixture",
        ))
        session.add(DataDomainRow(
            id="fixture_domain", version="1", chromatography_type="tlc",
            record_schema={"measurement_id": {"type": "string", "required": True}},
            unit_definitions={}, identity_fields=["measurement_id"],
            deduplication_policy="reject", supported_import_modes=["append"],
        ))
        session.add(DataImportRow(
            id="import_fixture", project_id="project_fixture", domain_id="fixture_domain",
            artifact_id="artifact_fixture", schema_version="1", import_mode="append",
            field_mapping={"measurement_id": "measurement_id"}, source_description="original",
            revision=1, config_hash="a" * 64, etag='"etag-1"', status=status,
            job_id=None, validation_ref="validation_fixture" if status == "READY" else None,
            new_data_version_id=None, request_snapshot={"state": "before"},
            created_at=now, updated_at=now,
        ))
    return engine, sessions


def _domains():
    return StaticDataDomainRegistry([DataDomainSchema(
        id="fixture_domain", version="1", chromatography_type="tlc",
        record_schema={"measurement_id": {"type": "string", "required": True}},
        unit_definitions={}, identity_fields=["measurement_id"],
        deduplication_policy="reject", supported_import_modes=["append"],
    )])


def _accept_import(session, *, status: str, job_id: str):
    now = datetime.now(timezone.utc)
    session.add(Job(
        id=job_id, project_id="project_fixture", owner_id="actor_fixture",
        kind="data_import_commit", resource_ref={"type": "data_import", "id": "import_fixture"},
        status="QUEUED", attempt=1, progress={}, cancelable=True, retryable=False,
        resume_mode="none", revision=1, etag='"job-etag"', created_at=now, updated_at=now,
    ))
    row = session.scalar(select(DataImportRow).where(DataImportRow.id == "import_fixture"))
    row.status = status
    row.job_id = job_id
    row.request_snapshot = {"state": "accepted", "job_id": job_id}


@pytest.mark.parametrize(("initial_status", "accepted_status"), [
    ("DRAFT", "VALIDATING"),
    ("READY", "COMMITTING"),
])
def test_patch_cas_rejects_stale_draft_after_validation_or_commit_acceptance(initial_status, accepted_status):
    engine, sessions = _database(initial_status)
    try:
        with sessions() as patch_session:
            repository = SQLAlchemyDataRepository(patch_session)
            domains = _domains()

            class _InterleavingDomains:
                changed = False

                def get(self, domain_id, schema_version=None):
                    if not self.changed:
                        self.changed = True
                        # End PATCH's read transaction while retaining its ORM identity,
                        # then commit acceptance through a second owner/session.
                        patch_session.commit()
                        with sessions.begin() as accept_session:
                            _accept_import(accept_session, status=accepted_status, job_id="job_accepted")
                    return domains.get(domain_id, schema_version)

                def list(self):
                    return domains.list()

            stale_status = initial_status
            stale = DataImportService(repository, _domains()).get("project_fixture", "import_fixture")
            assert stale.status == stale_status
            service = DataImportService(repository, _InterleavingDomains())

            with pytest.raises(DataConflictError):
                service.patch(
                    "project_fixture", "import_fixture",
                    DataImportPatch(source_description="stale patch"),
                    expected_revision=stale.revision, if_match=stale.etag,
                )

        with sessions() as session:
            row = session.scalar(select(DataImportRow).where(DataImportRow.id == "import_fixture"))
            assert row.status == accepted_status
            assert row.job_id == "job_accepted"
            assert row.request_snapshot == {"state": "accepted", "job_id": "job_accepted"}
            assert row.revision == 1 and row.etag == '"etag-1"'
    finally:
        engine.dispose()


def test_patch_cas_allows_normal_patch_and_rejects_a_second_stale_patch():
    engine, sessions = _database()
    try:
        with sessions() as first, sessions() as second:
            first_repo = SQLAlchemyDataRepository(first)
            second_repo = SQLAlchemyDataRepository(second)
            initial_first = first_repo.get_import("project_fixture", "import_fixture")
            initial_second = second_repo.get_import("project_fixture", "import_fixture")
            first.commit()
            second.commit()

            updated = DataImportService(first_repo, _domains()).patch(
                "project_fixture", "import_fixture",
                DataImportPatch(source_description="first patch"),
                expected_revision=initial_first.revision, if_match=initial_first.etag,
            )
            first.commit()
            assert updated.revision == 2
            assert updated.source_description == "first patch"
            assert updated.status == "DRAFT"

            with pytest.raises(DataConflictError):
                DataImportService(second_repo, _domains()).patch(
                    "project_fixture", "import_fixture",
                    DataImportPatch(source_description="stale second patch"),
                    expected_revision=initial_second.revision, if_match=initial_second.etag,
                )
            second.rollback()

        with sessions() as session:
            row = session.scalar(select(DataImportRow).where(DataImportRow.id == "import_fixture"))
            assert row.source_description == "first patch"
            assert row.revision == 2 and row.etag != '"etag-1"'
    finally:
        engine.dispose()
