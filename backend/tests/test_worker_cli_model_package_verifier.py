from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from chrom_platform.infrastructure.db import Base
from chrom_platform.infrastructure.models import Job, OutboxEvent, Project
from chrom_platform.modules.model_center.persistence import ModelCenterBase, SQLAlchemyModelCenterRepository
from chrom_platform.modules.model_center.schemas import ModelRegistration, now_utc
import chrom_platform.modules.model_center.worker as model_worker
import chrom_platform.modules.model_center.production_verifier as production_verifier
import chrom_platform.worker_cli as worker_cli
from chrom_platform.worker_cli import StableWorkerMessage


@pytest.fixture()
def dispatch_session():
    path = Path(__file__).with_name(f".package-dispatch-{uuid4().hex}.sqlite3")
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine, tables=[Project.__table__, Job.__table__, OutboxEvent.__table__])
    ModelCenterBase.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as session:
            yield session
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)


def _seed_registration(
    session: Session, *, source: dict[str, str], task_type: str = "gc",
    dispatch_type: str = "job.execute",
) -> StableWorkerMessage:
    now = now_utc()
    project_id, registration_id, job_id, dispatch_id = "project-route", "registration-route", "job-route", "dispatch-route"
    session.add(Project(id=project_id, slug=project_id, name="worker route test"))
    session.flush()
    session.add(Job(
        id=job_id, project_id=project_id, owner_id="owner",
        kind="model_registration_validation",
        resource_ref={"type": "model_registration", "id": registration_id},
        status="RUNNING", attempt=1, progress={}, cancelable=True, retryable=False,
        resume_mode="none", created_at=now, updated_at=now, started_at=now,
        revision=1, etag='"1"',
    ))
    session.add(OutboxEvent(
        dispatch_id=dispatch_id, aggregate_type="job", aggregate_id=job_id,
        event_type=dispatch_type, payload={"job_id": job_id, "attempt": 1, "dispatch_id": dispatch_id},
        status="PROCESSING", attempts=1, available_at=now, created_at=now,
        lease_owner="worker", lease_token=1, lease_expires_at=now + timedelta(minutes=3),
    ))
    SQLAlchemyModelCenterRepository(session).save_registration(ModelRegistration(
        id=registration_id, project_id=project_id, revision=1, config_hash="a" * 64,
        etag='"1"', status="VALIDATING", source=source, name="route test",
        task_type=task_type, job_id=job_id, attempt=1, dispatch_id=dispatch_id,
        created_at=now, updated_at=now,
    ))
    session.flush()
    return StableWorkerMessage(job_id, 1, dispatch_id)


class _CaptureConsumer:
    seen: list[dict[str, object]] = []

    def __init__(self, _repository, **kwargs):
        self.kwargs = kwargs
        self.__class__.seen.append(kwargs)

    def handle(self, _message, *, lease_guard=None):
        return "handled"

    def handle_cancel(self, _message, *, lease_guard=None):
        return "cancelled"


@pytest.fixture(autouse=True)
def capture_consumer(monkeypatch):
    _CaptureConsumer.seen = []
    monkeypatch.setattr(model_worker, "ModelCenterConsumer", _CaptureConsumer)


def _dispatch(session, message, *, deps, event_type="job.execute"):
    return worker_cli._dispatch_database_sync(
        session, message, event_type=event_type, deps=deps,
        fresh_session_factory=None,
    )


def test_worker_builds_session_local_verifier_only_for_gc_external_package_execute(dispatch_session, monkeypatch):
    message = _seed_registration(
        dispatch_session, source={"kind": "external_package", "artifact_id": "artifact-route"},
    )
    sentinel = object()
    calls = []

    def build(*, session, artifact_store):
        calls.append((session, artifact_store))
        return sentinel

    monkeypatch.setattr(production_verifier, "build_production_model_package_verifier", build)
    artifact_store = SimpleNamespace(configured=True)
    assert _dispatch(
        dispatch_session, message,
        deps=worker_cli.ProductionWorkerDependencies(artifact_store=artifact_store),
    ) == "handled"

    assert calls == [(dispatch_session, artifact_store)]
    wrapped = _CaptureConsumer.seen[-1]["verifier"]
    assert isinstance(wrapped, worker_cli._CommitBeforeCall)
    assert wrapped._target is sentinel


def test_worker_keeps_explicit_verifier_precedence_and_does_not_build_for_other_sources(dispatch_session, monkeypatch):
    message = _seed_registration(
        dispatch_session,
        source={"kind": "training_result", "experiment_id": "experiment-route", "artifact_id": "artifact-route"},
    )
    monkeypatch.setattr(
        production_verifier, "build_production_model_package_verifier",
        lambda **_kwargs: pytest.fail("factory must not run for a non-package source"),
    )
    explicit = object()
    assert _dispatch(
        dispatch_session, message, deps=worker_cli.ProductionWorkerDependencies(verifier=explicit),
    ) == "handled"
    assert _CaptureConsumer.seen[-1]["verifier"]._target is explicit


def test_worker_does_not_build_verifier_on_cancel(dispatch_session, monkeypatch):
    message = _seed_registration(
        dispatch_session, source={"kind": "external_package", "artifact_id": "artifact-route"},
        dispatch_type="job.cancel",
    )
    monkeypatch.setattr(
        production_verifier, "build_production_model_package_verifier",
        lambda **_kwargs: pytest.fail("factory must not run for cancel"),
    )
    assert _dispatch(
        dispatch_session, message, deps=worker_cli.ProductionWorkerDependencies(), event_type="job.cancel",
    ) == "cancelled"
    assert _CaptureConsumer.seen[-1]["verifier"] is None
