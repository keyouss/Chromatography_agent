from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from chrom_platform.artifact_core.models import Artifact
from chrom_platform.artifact_core.service import ArtifactService, ArtifactServiceError
from chrom_platform.core.dto import ActorContext
from chrom_platform.infrastructure.db import Base
from chrom_platform.infrastructure.metadata import compose_metadata
from chrom_platform.infrastructure.models import Project
from chrom_platform.modules.data_center.import_artifacts import DataImportArtifactReadPolicy
from chrom_platform.modules.data_center.persistence import DataCenterBase, DataImportRow, DataImportValidationRow


_PROJECT = "project-history-fixture"
_IMPORT = "import-history-fixture"
_VALIDATION = "validation-history-fixture"
_OLD_SOURCE = "artifact-historical-source"
_CURRENT_SOURCE = "artifact-current-source"
_REPORT = "artifact-validation-report"
_PARSER_HASH = "a" * 64
_OLD_CONFIG_HASH = "b" * 64
_CURRENT_CONFIG_HASH = "c" * 64
_OLD_SOURCE_BYTES = b"historical source bytes\n"
_CURRENT_SOURCE_BYTES = b"current source bytes\n"


class _ArtifactRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    def get(self, artifact_id):
        with self.sessions() as session:
            row = session.scalar(select(Artifact).where(Artifact.id == artifact_id))
            if row is not None:
                session.expunge(row)
            return row


class _ArtifactStore:
    configured = True

    def __init__(self, objects):
        self.objects = dict(objects)

    def read(self, key):
        return self.objects[key]


def _report_bytes(*, status: str, validation_status: str) -> bytes:
    counts = {"new": 0, "updated": 0, "unchanged": 0, "invalid": 1, "duplicate": 0}
    if validation_status == "VALID":
        counts = {"new": 1, "updated": 0, "unchanged": 0, "invalid": 0, "duplicate": 0}
    value = {
        "report_schema_version": "data-import-validation-report-1",
        "status": status,
        "project_id": _PROJECT,
        "import_id": _IMPORT,
        "revision": 1,
        "config_hash": _OLD_CONFIG_HASH,
        "domain_id": "fixture-domain",
        "domain_schema_version": "fixture-v1",
        "source_artifact_id": _OLD_SOURCE,
        "source_sha256": hashlib.sha256(_OLD_SOURCE_BYTES).hexdigest(),
        "encoding": "utf-8",
        "parser_revision": "fixture-parser-v1",
        "row_count": 1,
        "counts": counts,
        "issues": [],
        "total_issue_count": 0,
        "issues_truncated": False,
    }
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def _fixture(*, report_status="VALID", validation_status="STALE"):
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        connection.connection.create_function("CHAR_LENGTH", 1, len)
    compose_metadata(Base.metadata, DataCenterBase.metadata).create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    report = _report_bytes(status=report_status, validation_status=validation_status)
    now = datetime.now(timezone.utc)
    source_sha = hashlib.sha256(_OLD_SOURCE_BYTES).hexdigest()
    current_sha = hashlib.sha256(_CURRENT_SOURCE_BYTES).hexdigest()
    binding = {
        "revision": 1,
        "config_hash": _OLD_CONFIG_HASH,
        "source_artifact_id": _OLD_SOURCE,
        "source_sha256": source_sha,
        "parser_hash": _PARSER_HASH,
    }
    report_metadata = {
        "generation": "data_import_validation_report_v1",
        "source_ref": {"type": "data_import_validation", "id": _VALIDATION},
        "validation_binding": binding,
    }
    with sessions.begin() as session:
        session.add(Project(id=_PROJECT, slug="data-import-history", name="import history fixture"))
        session.add_all([
            Artifact(
                id=_OLD_SOURCE, project_id=_PROJECT, kind="dataset_package", title="old.csv",
                media_type="text/csv", size_bytes=len(_OLD_SOURCE_BYTES), sha256=source_sha,
                storage_key="source/old", status="AVAILABLE", scan_status="PASSED",
                metadata_json={}, created_by="fixture-actor",
            ),
            Artifact(
                id=_CURRENT_SOURCE, project_id=_PROJECT, kind="dataset_package", title="current.csv",
                media_type="text/csv", size_bytes=len(_CURRENT_SOURCE_BYTES), sha256=current_sha,
                storage_key="source/current", status="AVAILABLE", scan_status="PASSED",
                metadata_json={}, created_by="fixture-actor",
            ),
            Artifact(
                id=_REPORT, project_id=_PROJECT, kind="export_report", title="validation.json",
                media_type="application/json", size_bytes=len(report), sha256=hashlib.sha256(report).hexdigest(),
                storage_key="report/validation", status="AVAILABLE", scan_status="NOT_CONFIGURED",
                metadata_json=report_metadata, created_by="fixture-actor",
            ),
        ])
        session.add(DataImportRow(
            id=_IMPORT, project_id=_PROJECT, domain_id="fixture-domain", artifact_id=_CURRENT_SOURCE,
            schema_version="fixture-v1", import_mode="append", field_mapping={"sample_id": "sample_id"},
            source_description="synthetic history fixture", revision=2, config_hash=_CURRENT_CONFIG_HASH,
            etag='"current-etag"', status="DRAFT", job_id=None, validation_ref=None,
            new_data_version_id=None, request_snapshot={}, created_at=now, updated_at=now,
        ))
        session.add(DataImportValidationRow(
            id=_VALIDATION, project_id=_PROJECT, import_id=_IMPORT, revision=1,
            config_hash=_OLD_CONFIG_HASH, status=validation_status,
            counts=json.loads(report)["counts"], report_artifact_id=_REPORT, warnings=[],
            parser_revision="fixture-parser-v1",
            parser_hash=_PARSER_HASH, created_at=now,
        ))
    objects = {
        "source/old": _OLD_SOURCE_BYTES,
        "source/current": _CURRENT_SOURCE_BYTES,
        "report/validation": report,
    }
    policy = DataImportArtifactReadPolicy(sessions)
    service = ArtifactService(
        _ArtifactRepository(sessions), _ArtifactStore(objects), export_read_policy=policy,
    )
    actor = ActorContext(
        actor_id="fixture-actor", tenant_id=None, project_ids=[_PROJECT],
        auth_method="service", permissions=["data:read", "artifact:read"],
        policy_version="fixture-policy-v1",
    )
    return engine, sessions, service, actor, report, source_sha


def test_stale_validation_report_uses_its_historical_source_after_import_changes():
    engine, _sessions, service, actor, report, _source_sha = _fixture()
    try:
        view = service.get_artifact(_REPORT, actor)
        downloaded_view, content = service.download(_REPORT, actor)
        assert view.download_allowed is True
        assert downloaded_view.download_allowed is True
        assert content == report
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("validation_status", "report_status", "allowed"),
    [
        ("VALID", "VALID", True),
        ("VALID", "INVALID", False),
        ("INVALID", "INVALID", True),
        ("INVALID", "UNAVAILABLE", True),
        ("INVALID", "UNSUPPORTED_IMPORT_MODE", True),
        ("STALE", "VALID", True),
        ("STALE", "UNAVAILABLE", True),
        ("PENDING", "VALID", False),
    ],
)
def test_report_status_mapping_is_consistent_with_persisted_validation(
    validation_status, report_status, allowed,
):
    engine, _sessions, service, actor, _report, _source_sha = _fixture(
        report_status=report_status, validation_status=validation_status,
    )
    try:
        if allowed:
            view = service.get_artifact(_REPORT, actor)
            assert view.download_allowed is True
            assert service.download(_REPORT, actor)[1]
        else:
            if validation_status == "PENDING":
                with pytest.raises(ArtifactServiceError):
                    service.get_artifact(_REPORT, actor)
            else:
                assert service.get_artifact(_REPORT, actor).download_allowed is False
            with pytest.raises(ArtifactServiceError):
                service.download(_REPORT, actor)
    finally:
        engine.dispose()


def test_report_history_rejects_missing_permission_binding_tampering_and_bad_source():
    engine, sessions, service, actor, report, source_sha = _fixture()
    policy = DataImportArtifactReadPolicy(sessions)
    try:
        no_data_read = actor.model_copy(update={"permissions": ["artifact:read"]})
        no_artifact_read = actor.model_copy(update={"permissions": ["data:read"]})
        for actor_without_permission in (no_data_read, no_artifact_read):
            with pytest.raises(ArtifactServiceError):
                service.get_artifact(_REPORT, actor_without_permission)
            with pytest.raises(ArtifactServiceError):
                service.download(_REPORT, actor_without_permission)

        tampered_store_service = ArtifactService(
            _ArtifactRepository(sessions),
            _ArtifactStore({"report/validation": report + b" "}),
            export_read_policy=policy,
        )
        assert tampered_store_service.get_artifact(_REPORT, actor).download_allowed is False
        with pytest.raises(ArtifactServiceError):
            tampered_store_service.download(_REPORT, actor)

        with sessions.begin() as session:
            row = session.scalar(select(Artifact).where(Artifact.id == _REPORT))
            metadata = dict(row.metadata_json)
            metadata["validation_binding"] = {
                **metadata["validation_binding"], "source_artifact_id": _CURRENT_SOURCE,
            }
            row.metadata_json = metadata
        with pytest.raises(ArtifactServiceError):
            service.get_artifact(_REPORT, actor)

        with sessions.begin() as session:
            row = session.scalar(select(Artifact).where(Artifact.id == _REPORT))
            metadata = dict(row.metadata_json)
            metadata["validation_binding"] = {
                **metadata["validation_binding"], "source_artifact_id": _OLD_SOURCE,
                "source_sha256": "0" * 64,
            }
            row.metadata_json = metadata
        with pytest.raises(ArtifactServiceError):
            service.get_artifact(_REPORT, actor)

        with sessions.begin() as session:
            row = session.scalar(select(Artifact).where(Artifact.id == _REPORT))
            metadata = dict(row.metadata_json)
            metadata["validation_binding"] = {
                **metadata["validation_binding"], "source_sha256": source_sha,
            }
            row.metadata_json = metadata
            source_row = session.scalar(select(Artifact).where(Artifact.id == _OLD_SOURCE))
            source_row.scan_status = "NOT_CONFIGURED"
        with pytest.raises(ArtifactServiceError):
            service.get_artifact(_REPORT, actor)

        with sessions.begin() as session:
            source_row = session.scalar(select(Artifact).where(Artifact.id == _OLD_SOURCE))
            source_row.scan_status = "PASSED"
            source_row.sha256 = "d" * 64
        with pytest.raises(ArtifactServiceError):
            service.get_artifact(_REPORT, actor)
    finally:
        engine.dispose()
