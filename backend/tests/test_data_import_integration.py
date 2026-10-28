from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys
from datetime import datetime, timezone
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from chrom_platform.assembly.m2 import CsvImportProfiles, _DataImportRetryTaskPolicy, _ImportableDomainRegistry
from chrom_platform.assembly.profile import _copy_tree
from chrom_platform.core.dto import ActorContext, BuildManifest, ResourceRef
from chrom_platform.artifact_core.models import Structure
from chrom_platform.infrastructure.db import Base
from chrom_platform.infrastructure.metadata import compose_metadata
from chrom_platform.infrastructure.models import Job, JobAttempt, OutboxEvent, Project
from chrom_platform.modules.data_center.import_workflow import SQLAlchemyImportWorkflow
from chrom_platform.modules.data_center.import_worker import DataImportWorker
from chrom_platform.modules.data_center.persistence import DataCenterBase, DataDomainRow, DataImportRow
from chrom_platform.modules.data_center.schemas import DataDomainSchema
from chrom_platform.task_core.policy import DeploymentTaskPolicy
from chrom_platform.task_core.service import OperationContext, TargetSnapshot, TaskError
from chrom_platform.worker_cli import (
    ProfileWorkerPlan,
    WorkerConfigurationError,
    _RESOURCE_TYPES,
    _WORKER_EVENT_TYPES,
    _WORKER_JOB_KINDS,
    _ensure_import_structure,
    _normalize_import_smiles,
)


class _Domains:
    def __init__(self, *domains):
        self.domains = {domain.id: domain for domain in domains}

    def list(self):
        return tuple(self.domains.values())

    def get(self, domain_id, schema_version=None):
        value = self.domains.get(domain_id)
        return value if value is not None and (schema_version is None or value.version == schema_version) else None


def _domain(domain_id: str, *, policy: str = "reject", modes: list[str] | None = None):
    return DataDomainSchema(
        id=domain_id,
        version="1",
        chromatography_type="tlc",
        record_schema={"measurement_id": {"type": "string", "required": True}},
        unit_definitions={},
        identity_fields=["measurement_id"],
        deduplication_policy=policy,
        supported_import_modes=modes or ["append", "upsert"],
    )


def test_production_profile_registry_exposes_no_unreviewed_domain():
    domain = _domain("legacy-domain")
    registry = _ImportableDomainRegistry(_Domains(domain), CsvImportProfiles())

    assert registry.list() == ()
    assert registry.get("legacy-domain", "1") is None


def test_profile_filter_only_exposes_append_reject_domain_and_modes():
    append_domain = _domain("fixture-domain")
    update_domain = _domain("update_domain", policy="update_existing")
    profile = SimpleNamespace(domain_id="fixture-domain", schema_version="1")
    profiles = CsvImportProfiles({("fixture-domain", "1"): profile})
    registry = _ImportableDomainRegistry(_Domains(append_domain, update_domain), profiles)

    values = registry.list()
    assert [value.id for value in values] == ["fixture-domain"]
    assert values[0].supported_import_modes == ["append"]
    assert registry.get("update_domain") is None


def test_data_import_retry_policy_preserves_confirmation_and_delegates_only_import_retry():
    delegate = DeploymentTaskPolicy()
    calls = []

    def resolve_import_retry(ctx, resource_ref, action):
        calls.append((ctx, resource_ref.type, resource_ref.id, action))
        return TargetSnapshot(
            project_id="project_fixture", resource_ref=resource_ref, revision=1,
            config_hash="a" * 64, etag='"retry-fixture"',
            policy_version="deployment-default-v1",
        )

    policy = _DataImportRetryTaskPolicy(delegate, resolve_import_retry)
    actor = ActorContext(
        actor_id="actor_fixture", project_ids=["project_fixture"], auth_method="service",
        permissions=["task:retry"], policy_version="fixture-v1",
    )
    context = OperationContext(actor=actor)
    import_ref = ResourceRef(type="data_import", id="import_fixture")

    assert policy.retry_requires_confirmation(kind="data_import_commit", resource_ref={"type": "data_import"}) is True
    assert policy.required_permission(
        "retry", kind="data_import_commit", resource_ref={"type": "data_import"}, action="job.retry",
    ) == "task:retry"
    target = policy.resolve_target(context, import_ref, "job.retry")
    assert target.resource_ref == import_ref
    assert policy.authorize_resource(context, target, action="job.retry") is True
    assert calls == [(context, "data_import", "import_fixture", "job.retry")]

    for resource_ref, action in (
        (import_ref, "job.cancel"),
        (ResourceRef(type="training", id="training_fixture"), "job.retry"),
    ):
        with pytest.raises(TaskError) as error:
            policy.resolve_target(context, resource_ref, action)
        assert error.value.code == "POLICY_UNAVAILABLE"
    assert len(calls) == 1


def test_worker_plan_registers_data_import_jobs_only_for_datasets_module():
    assert _WORKER_JOB_KINDS["datasets"] == frozenset({"data_import_validation", "data_import_commit"})
    assert _WORKER_EVENT_TYPES["datasets"] == frozenset({"job.execute", "job.cancel"})
    assert _RESOURCE_TYPES["data_import_validation"] == "data_import"
    assert _RESOURCE_TYPES["data_import_commit"] == "data_import"
    assert "datasets" not in _WORKER_JOB_KINDS["prediction"]


def test_dataset_worker_is_physically_optional_by_build_manifest():
    root = Path(__file__).resolve().parents[2]
    dataset_plan = ProfileWorkerPlan.from_manifest(root / "builds" / "universal-v1.json")
    assert "datasets" in dataset_plan.worker_modules
    assert {"data_import_validation", "data_import_commit"} <= dataset_plan.allowed_job_kinds

    with pytest.raises(WorkerConfigurationError, match="NO_WORKER_MODULES_SELECTED"):
        ProfileWorkerPlan.from_manifest(root / "builds" / "minimal-structure-v1.json")


def test_datasets_profile_contains_only_its_optional_worker_closure():
    root = Path(__file__).resolve().parents[1]
    manifest = BuildManifest(
        manifest_version="1.0", build_id="datasets-fixture-v1", modules=["datasets"],
        chromatography_types=[], prediction_tools={}, utilities=[], export_formats=[],
        auth_provider="deployment_default",
    )
    with TemporaryDirectory(dir=root) as temporary:
        output = Path(temporary) / "package"
        _copy_tree(root / "src", output, manifest)
        package = output / "chrom_platform"
        assert (package / "worker_cli.py").is_file()
        assert (package / "assembly" / "m2.py").is_file()
        assert (package / "modules" / "data_center" / "import_worker.py").is_file()
        assert not (package / "modules" / "prediction").exists()
        assert not (package / "assembly" / "prediction_worker.py").exists()
        result = subprocess.run(
            [sys.executable, "-c", "import chrom_platform.assembly.m2; import chrom_platform.worker_cli"],
            cwd=output, env={**os.environ, "PYTHONPATH": str(output)},
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, result.stderr


def test_workflow_writes_real_job_attempt_and_outbox_in_caller_transaction():
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        connection.connection.create_function("CHAR_LENGTH", 1, len)
    compose_metadata(Base.metadata, DataCenterBase.metadata).create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    source = b"fixture csv input"

    class _Store:
        def read(self, _key):
            return source

    with sessions.begin() as session:
        session.add(Project(id="project_fixture", slug="fixture", name="fixture"))
        from chrom_platform.artifact_core.models import Artifact

        session.add(Artifact(
            id="artifact_fixture", project_id="project_fixture", kind="dataset_package",
            title="fixture.csv", media_type="text/csv", size_bytes=len(source),
            sha256=__import__("hashlib").sha256(source).hexdigest(), storage_key="source.csv",
            status="AVAILABLE", scan_status="PASSED", metadata_json={}, created_by="actor_fixture",
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
            field_mapping={"measurement_id": "measurement_id"}, source_description="fixture only",
            revision=1, config_hash="a" * 64, etag='"etag-fixture"', status="DRAFT",
            job_id=None, validation_ref=None, new_data_version_id=None, request_snapshot={},
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ))

    actor = ActorContext(
        actor_id="actor_fixture", project_ids=["project_fixture"], auth_method="service",
        permissions=["*"], policy_version="fixture-v1",
    )
    context = SimpleNamespace(actor=actor)
    with sessions() as session:
        workflow = SQLAlchemyImportWorkflow(
            session, _Store(), object(),
            profile_resolver=lambda domain_id, version: SimpleNamespace(domain_id=domain_id, schema_version=version),
            id_factory=lambda prefix: f"{prefix}_fixture",
        )
        session.begin()
        accepted = workflow.submit_validation(context, "project_fixture", "import_fixture", if_match='"etag-fixture"')
        assert session.scalar(select(Job).where(Job.id == accepted.job_id)) is not None
        assert session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == accepted.job_id)) is not None
        session.rollback()

    with sessions() as session:
        assert session.scalar(select(Job).where(Job.id == "job_fixture")) is None
        assert session.scalar(select(DataImportRow).where(DataImportRow.id == "import_fixture")).status == "DRAFT"
    with sessions() as session:
        session.begin()
        accepted = SQLAlchemyImportWorkflow(
            session, _Store(), object(),
            profile_resolver=lambda domain_id, version: SimpleNamespace(domain_id=domain_id, schema_version=version),
            id_factory=lambda prefix: f"{prefix}_fixture",
        ).submit_validation(context, "project_fixture", "import_fixture", if_match='"etag-fixture"')
        session.commit()
        job = session.scalar(select(Job).where(Job.id == accepted.job_id))
        attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == accepted.job_id))
        outbox = session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == accepted.job_id, OutboxEvent.event_type == "job.execute"))
        assert accepted.id == "import_fixture"
        assert accepted.resource_ref.model_dump(mode="json") == {"type": "data_import", "id": "import_fixture"}
        assert job is not None and job.kind == "data_import_validation"
        assert attempt is not None and attempt.attempt == 1
        assert outbox is not None and outbox.payload["job_id"] == job.id


def test_worker_structure_adapter_is_real_and_uses_caller_transaction():
    engine = create_engine("sqlite:///:memory:")
    def configure_sqlite(dbapi, _record):
        dbapi.isolation_level = None
        dbapi.create_function("CHAR_LENGTH", 1, len)

    event.listen(engine, "connect", configure_sqlite)
    event.listen(engine, "begin", lambda connection: connection.exec_driver_sql("BEGIN"))
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions.begin() as session:
        session.add(Project(id="project_structure_fixture", slug="structure-fixture", name="fixture"))

    worker = DataImportWorker(
        sessions, None, profile_resolver=lambda *_: None,
        structure_normalizer=_normalize_import_smiles,
        ensure_structure=_ensure_import_structure,
    )
    assert worker.commit_handler is not None

    with sessions() as session:
        session.begin()
        simple = _normalize_import_smiles("CCO")
        simple_result = _ensure_import_structure(
            session=session, project_id="project_structure_fixture",
            normalized_structure=simple, source_artifact_id="source-one",
        )
        stereo = _normalize_import_smiles("C[C@H](O)C(=O)O")
        stereo_result = _ensure_import_structure(
            session=session, project_id="project_structure_fixture",
            normalized_structure=stereo, source_artifact_id="source-two",
        )
        rows = session.scalars(select(Structure).order_by(Structure.id)).all()
        assert len(rows) == 2
        assert simple_result["canonical_smiles"] == simple["canonical_smiles"]
        assert stereo_result["canonical_smiles"] == stereo["canonical_smiles"]
        assert any(row.has_stereochemistry for row in rows)
        assert all(row.original_input["source_artifact_id"] is None for row in rows)
        session.rollback()

    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Structure)) == 0
