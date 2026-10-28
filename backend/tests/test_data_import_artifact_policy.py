from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from chrom_platform.artifact_core.service import ArtifactService, ArtifactServiceError
from chrom_platform.core.dto import ActorContext


class _Store:
    configured = True

    def __init__(self, content: bytes):
        self.content = content

    def read(self, _key):
        return self.content


class _Repository:
    def __init__(self, row):
        self.row = row

    def get(self, artifact_id):
        return self.row if artifact_id == self.row.id else None


class _ReportPolicy:
    def can_read_export(self, row, actor):
        source = row.metadata_json.get("source_ref", {})
        return bool(
            actor.can("data:read")
            and row.metadata_json.get("generation") == "data_import_validation_report_v1"
            and source == {"type": "data_import_validation", "id": "validation-1"}
        )

    def validate_export_body(self, row, actor, content):
        if not self.can_read_export(row, actor):
            return False
        try:
            report = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        return bool(
            report.get("report_schema_version") == "data-import-validation-report-1"
            and report.get("project_id") == row.project_id
            and report.get("import_id") == "import-1"
            and report.get("revision") == 2
        )


class _LegacyExportPolicy:
    def can_read_export(self, _row, _actor):
        return True

    def validate_export_body(self, _row, _actor, _content):
        return True


def _service(content: bytes, *, metadata=None, sha256=None, kind="export_report", scan_status="NOT_CONFIGURED"):
    now = datetime.now(timezone.utc)
    row = SimpleNamespace(
        id="artifact-1",
        project_id="project-1",
        kind=kind,
        title="validation-report.json",
        media_type="application/json",
        size_bytes=len(content),
        sha256=sha256 if sha256 is not None else hashlib.sha256(content).hexdigest(),
        storage_key="server-key",
        status="AVAILABLE",
        scan_status=scan_status,
        metadata_json=metadata if metadata is not None else {
            "generation": "data_import_validation_report_v1",
            "source_ref": {"type": "data_import_validation", "id": "validation-1"},
        },
        created_by="owner-1",
        retention_until=None,
        created_at=now,
        updated_at=now,
    )
    actor = ActorContext(
        actor_id="actor-1", project_ids=["project-1"], auth_method="service",
        permissions=["artifact:read", "data:read"], policy_version="test-v1",
    )
    service = ArtifactService(
        _Repository(row), _Store(content), export_read_policy=_ReportPolicy(),
    )
    return service, actor


def _body() -> bytes:
    return json.dumps({
        "report_schema_version": "data-import-validation-report-1",
        "project_id": "project-1",
        "import_id": "import-1",
        "revision": 2,
    }).encode("utf-8")


def test_unscanned_server_validation_report_uses_same_body_policy_for_view_and_download():
    service, actor = _service(_body())

    view = service.get_artifact("artifact-1", actor)
    downloaded_view, body = service.download("artifact-1", actor)

    assert view.scan_status == "NOT_CONFIGURED"
    assert view.download_allowed is True
    assert downloaded_view.download_allowed is True
    assert body == _body()


@pytest.mark.parametrize("case", ["body_sha_mismatch", "invalid_schema", "wrong_generation"])
def test_report_download_flag_and_download_reject_invalid_binding(case):
    body = _body()
    metadata = None
    digest = None
    if case == "body_sha_mismatch":
        digest = "0" * 64
    elif case == "invalid_schema":
        body = b'{"report_schema_version":"wrong"}'
    elif case == "wrong_generation":
        metadata = {
            "generation": "user_claimed_report",
            "source_ref": {"type": "data_import_validation", "id": "validation-1"},
        }
    service, actor = _service(body, metadata=metadata, sha256=digest)

    if case == "wrong_generation":
        with pytest.raises(ArtifactServiceError):
            service.get_artifact("artifact-1", actor)
    else:
        assert service.get_artifact("artifact-1", actor).download_allowed is False
    with pytest.raises(ArtifactServiceError):
        service.download("artifact-1", actor)


def test_legacy_export_policy_cannot_grant_not_configured_report_exception():
    service, actor = _service(
        _body(), metadata={"generation": "legacy_export_v1", "source_ref": {"type": "export", "id": "export-1"}},
    )
    service.export_read_policy = _LegacyExportPolicy()

    assert service.get_artifact("artifact-1", actor).download_allowed is False
    with pytest.raises(ArtifactServiceError):
        service.download("artifact-1", actor)


def test_passed_legacy_export_keeps_its_existing_policy_path():
    service, actor = _service(
        _body(), metadata={"generation": "legacy_export_v1", "source_ref": {"type": "export", "id": "export-1"}},
        scan_status="PASSED",
    )
    service.export_read_policy = _LegacyExportPolicy()

    assert service.get_artifact("artifact-1", actor).download_allowed is True
    assert service.download("artifact-1", actor)[1] == _body()


def test_data_version_snapshot_is_internal_but_internal_reader_can_access_it():
    service, actor = _service(
        b'{"snapshot_schema_version":"data-import-snapshot-1"}',
        metadata={"generation": "data_import_snapshot_v1", "source_ref": {"type": "data_version", "id": "version-1"}},
        kind="dataset_package",
    )

    with pytest.raises(ArtifactServiceError):
        service.get_artifact("artifact-1", actor)
    with pytest.raises(ArtifactServiceError):
        service.download("artifact-1", actor)
    row, body = service.download_content("artifact-1", actor)
    assert row.id == "artifact-1"
    assert body == b'{"snapshot_schema_version":"data-import-snapshot-1"}'
