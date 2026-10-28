"""Profile-aware production Worker entrypoint.

The CLI owns transport and outbox delivery only.  Business handlers must be
bound by a build-owned registry; no message field is ever treated as an
import path or executable command.  Optional Dramatiq/Redis integration is
loaded only after configuration and profile validation.
"""

from __future__ import annotations

import os
import asyncio
import ctypes
import hashlib
import json
import inspect
import signal
import re
import sys
import threading
import uuid
from contextvars import ContextVar
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit

from sqlalchemy import select, text, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from chrom_platform.assembly.manifest import load_manifest
from chrom_platform.assembly.handler_registry import FunctionHandlerAdapter, MappingHandlerAdapter, StaticHandlerRegistry
from chrom_platform.task_core.outbox_dispatch import AsyncOutboxDispatcher, LeaseGuard, LeaseLostError
from chrom_platform.infrastructure.models import Job, OutboxEvent
from chrom_platform.core.dto import ActorContext


class WorkerConfigurationError(RuntimeError):
    """A safe, actionable configuration or runtime dependency failure."""


_CURRENT_LEASE_GUARD: ContextVar[LeaseGuard | None] = ContextVar("chrom_current_lease_guard", default=None)


def get_current_lease_guard() -> LeaseGuard | None:
    """Return the internal lease guard for the currently executing handler."""

    return _CURRENT_LEASE_GUARD.get()


PROFILE_ALLOWLIST = frozenset({
    "universal-v1", "sfc-v1", "cc-v1", "minimal-structure-v1",
    "data-center-invalid-window-20261026",
})
_INSTANCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_error(exc: BaseException) -> str:
    """Return a stable error code without URLs, credentials, or SQL text."""

    if isinstance(exc, WorkerConfigurationError) and re.fullmatch(r"[A-Z][A-Z0-9_]{2,95}", str(exc)):
        return str(exc)
    return type(exc).__name__.upper()


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise WorkerConfigurationError(f"MISSING_{name}")
    return value


def _validate_redis_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
        raise WorkerConfigurationError("INVALID_REDIS_URL")


def _validate_database_url(value: str) -> None:
    parsed = urlsplit(value)
    # Production uses the asyncmy dialect.  create_engine may still fail
    # explicitly when the optional dialect is absent; never silently fall
    # back to SQLite or pymysql.
    if parsed.scheme != "mysql+asyncmy" or not parsed.hostname:
        raise WorkerConfigurationError("DATABASE_URL_MUST_USE_MYSQL_ASYNCMY")


def _validate_sync_database_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme != "mysql+pymysql" or not parsed.hostname:
        raise WorkerConfigurationError("SYNC_DATABASE_URL_MUST_USE_MYSQL_PYMYSQL")


def _database_endpoint(value: str) -> tuple[str, int, str]:
    parsed = urlsplit(value)
    return (parsed.hostname or "").lower(), parsed.port or 3306, parsed.path.lstrip("/")


def _validate_database_pair(async_url: str, sync_url: str) -> None:
    if _database_endpoint(async_url) != _database_endpoint(sync_url):
        raise WorkerConfigurationError("DATABASE_URL_SYNC_DATABASE_URL_MISMATCH")


def _deployment_actor_context() -> ActorContext:
    projects = [item.strip() for item in os.getenv("CHROM_PROJECT_IDS", "").split(",") if item.strip()]
    return ActorContext(
        actor_id=os.getenv("CHROM_ACTOR_ID", "deployment-default").strip() or "deployment-default",
        tenant_id=None,
        project_ids=projects,
        auth_method="deployment_default",
        permissions=["*"],
        policy_version="deployment-default-v1",
        default_project_id=os.getenv("CHROM_DEFAULT_PROJECT_ID", "").strip() or None,
    )


def _assistant_secret_resolver(reference: str) -> str:
    """Resolve DPAPI references from the protected runtime root only."""
    if not reference.startswith("dpapi:v1:"):
        raise WorkerConfigurationError("ASSISTANT_SECRET_REFERENCE_INVALID")
    root_value = os.environ.get("CHROM_ASSISTANT_SECRET_ROOT", "").strip()
    if not root_value:
        raise WorkerConfigurationError("ASSISTANT_SECRET_STORE_UNAVAILABLE")
    root = Path(root_value)
    if not root.is_absolute():
        raise WorkerConfigurationError("ASSISTANT_SECRET_STORE_UNAVAILABLE")
    from chrom_platform.modules.assistant.secret_store import WindowsDpapiSecretStore

    return WindowsDpapiSecretStore(root).get(reference)


@dataclass(frozen=True)
class WorkerConfig:
    manifest_path: Path
    database_url: str
    redis_url: str
    ready_file: Path
    sync_database_url: str | None = None
    queue_name: str = "chrom-platform"
    lease_seconds: int = 60
    heartbeat_seconds: float = 15.0
    poll_seconds: float = 1.0
    profile: str | None = None

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        manifest_path = Path(_required_env("CHROM_BUILD_MANIFEST")).resolve()
        if manifest_path.suffix.lower() != ".json" or not manifest_path.is_file():
            raise WorkerConfigurationError("BUILD_MANIFEST_NOT_FOUND")
        database_url = _required_env("DATABASE_URL")
        redis_url = _required_env("REDIS_URL")
        _validate_database_url(database_url)
        manifest = load_manifest(manifest_path)
        profile = os.getenv("BUILD_PROFILE", "").strip() or None
        if profile is not None and profile not in PROFILE_ALLOWLIST:
            raise WorkerConfigurationError("PROFILE_UNSUPPORTED")
        sync_database_url = os.getenv("SYNC_DATABASE_URL", "").strip() or None
        if {"assistant", "prediction"} & set(manifest.modules):
            if "assistant" in manifest.modules and (not os.getenv("CHROM_ACTOR_ID", "").strip() or not os.getenv("CHROM_PROJECT_IDS", "").strip()):
                raise WorkerConfigurationError("MISSING_ASSISTANT_ACTOR_SCOPE")
            if not sync_database_url:
                raise WorkerConfigurationError("MISSING_SYNC_DATABASE_URL")
            _validate_sync_database_url(sync_database_url)
            _validate_database_pair(database_url, sync_database_url)
        _validate_redis_url(redis_url)
        ready_file = Path(_required_env("WORKER_READY_FILE")).resolve()
        try:
            lease_seconds = int(os.getenv("WORKER_LEASE_SECONDS", "60"))
            heartbeat_seconds = float(os.getenv("WORKER_LEASE_HEARTBEAT_SECONDS", "15"))
            poll_seconds = float(os.getenv("WORKER_POLL_SECONDS", "1"))
        except ValueError as exc:
            raise WorkerConfigurationError("INVALID_WORKER_TIMING") from exc
        if lease_seconds < 1 or heartbeat_seconds <= 0 or heartbeat_seconds >= lease_seconds or poll_seconds <= 0:
            raise WorkerConfigurationError("INVALID_WORKER_TIMING")
        queue_name = os.getenv("WORKER_QUEUE", "chrom-platform").strip()
        if not queue_name or len(queue_name) > 64:
            raise WorkerConfigurationError("INVALID_WORKER_QUEUE")
        return cls(manifest_path, database_url, redis_url, ready_file, sync_database_url, queue_name, lease_seconds, heartbeat_seconds, poll_seconds, profile)


_WORKER_CLASSES = {
    "prediction": ("chrom_platform.modules.prediction.production_worker", "PredictionProductionWorker"),
    "knowledge": ("chrom_platform.modules.knowledge.worker", "KnowledgeWorker"),
    "training": ("chrom_platform.modules.training.production_worker", "TrainingProductionWorker"),
    "model_center": ("chrom_platform.modules.model_center.worker", "DeploymentWorker"),
    "assistant": ("chrom_platform.modules.assistant.production_worker", "AssistantProductionWorker"),
    "datasets": ("chrom_platform.modules.data_center.import_worker", "DataImportWorker"),
}

_WORKER_JOB_KINDS = {
    "prediction": frozenset({"prediction"}),
    "knowledge": frozenset({"knowledge_index"}),
    "training": frozenset({"training"}),
    "model_center": frozenset({"model_registration_validation", "model_evaluation", "model_deployment"}),
    "assistant": frozenset({"assistant", "assistant_llm_configuration_test"}),
    "datasets": frozenset({"data_import_validation", "data_import_commit"}),
}
_WORKER_EVENT_TYPES = {
    "prediction": frozenset({"prediction.execute", "job.execute", "job.cancel"}),
    "knowledge": frozenset({"job.execute", "job.cancel"}),
    "training": frozenset({"job.execute", "job.cancel"}),
    "model_center": frozenset({"job.execute", "job.cancel"}),
    "assistant": frozenset({"job.execute", "job.cancel"}),
    "datasets": frozenset({"job.execute", "job.cancel"}),
}


@dataclass(frozen=True)
class ProfileWorkerPlan:
    """The fixed worker set derived from a validated build manifest."""

    build_id: str
    modules: tuple[str, ...]
    worker_modules: tuple[str, ...]
    allowed_job_kinds: frozenset[str]
    allowed_event_types: frozenset[str]
    manifest_sha256: str | None = None
    manifest_path: str | None = None

    @classmethod
    def from_manifest(cls, path: str | Path, *, expected_profile: str | None = None) -> "ProfileWorkerPlan":
        manifest_path = Path(path).resolve()
        manifest = load_manifest(manifest_path)
        try:
            manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        except OSError as exc:
            raise WorkerConfigurationError("BUILD_MANIFEST_READ_FAILED") from exc
        if manifest.build_id not in PROFILE_ALLOWLIST:
            raise WorkerConfigurationError("PROFILE_UNSUPPORTED")
        if expected_profile is not None and manifest.build_id != expected_profile:
            raise WorkerConfigurationError("PROFILE_MANIFEST_MISMATCH")
        selected = tuple(module for module in _WORKER_CLASSES if module in manifest.modules)
        if not selected:
            raise WorkerConfigurationError("NO_WORKER_MODULES_SELECTED")
        package_root = Path(__file__).resolve().parent / "modules"
        missing = [module for module in selected if not (
            (package_root / module / "production_worker.py").is_file()
            if module in {"prediction", "training", "assistant"}
            else (package_root / "data_center" / "import_worker.py").is_file()
            if module == "datasets"
            else (package_root / module / "worker.py").is_file()
        )]
        if missing:
            raise WorkerConfigurationError("WORKER_SOURCE_MISSING")
        allowed_kinds = frozenset({"artifact_validation"}) | frozenset().union(*(_WORKER_JOB_KINDS[module] for module in selected))
        if "records" in manifest.modules and "prediction" in selected:
            allowed_kinds = allowed_kinds | frozenset({"export"})
        return cls(
            manifest.build_id,
            tuple(manifest.modules),
            selected,
            allowed_kinds,
            frozenset({"job.execute", "job.cancel"}) | frozenset().union(*(_WORKER_EVENT_TYPES[module] for module in selected)),
            manifest_sha256,
            str(manifest_path),
        )

    def import_worker_types(self) -> dict[str, type[Any]]:
        """Import only the static worker modules selected by this profile."""

        import importlib

        result: dict[str, type[Any]] = {}
        for module_name in self.worker_modules:
            module_path, class_name = _WORKER_CLASSES[module_name]
            try:
                worker_type = getattr(importlib.import_module(module_path), class_name)
            except (ImportError, AttributeError) as exc:
                raise WorkerConfigurationError(f"WORKER_IMPORT_UNAVAILABLE_{module_name.upper()}") from exc
            result[module_name] = worker_type
        return result


@dataclass(frozen=True)
class StableWorkerMessage:
    job_id: str
    attempt: int
    dispatch_id: str
    fencing_token: int | None = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], *, event_type: str | None = None) -> "StableWorkerMessage":
        fields = set(payload)
        base = {"job_id", "attempt", "dispatch_id"}
        if fields != base:
            raise WorkerConfigurationError("INVALID_WORKER_PAYLOAD")
        job_id, dispatch_id, attempt = payload["job_id"], payload["dispatch_id"], payload["attempt"]
        if not isinstance(job_id, str) or not job_id or not isinstance(dispatch_id, str) or not dispatch_id:
            raise WorkerConfigurationError("INVALID_WORKER_PAYLOAD")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise WorkerConfigurationError("INVALID_WORKER_PAYLOAD")
        return cls(job_id, attempt, dispatch_id, payload.get("fencing_token"))


class Broker(Protocol):
    def send(self, payload: Mapping[str, Any]) -> None: ...


Handler = Callable[[StableWorkerMessage], Any]


def _invoke_handler(handler: Handler, message: StableWorkerMessage, guard: LeaseGuard | None) -> Any:
    """Pass the internal guard only to handlers that explicitly accept it."""

    if guard is None:
        return handler(message)
    try:
        parameters = inspect.signature(handler).parameters
    except (TypeError, ValueError):
        return handler(message)
    if "lease_guard" in parameters:
        return handler(message, lease_guard=guard)  # type: ignore[call-arg]
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return handler(message, lease_guard=guard)  # type: ignore[call-arg]
    positional = [parameter for parameter in parameters.values() if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)]
    if len(positional) >= 2:
        return handler(message, guard)  # type: ignore[call-arg]
    return handler(message)


@dataclass(frozen=True)
class ProductionWorkerDependencies:
    """Reviewed runtime ports; omitted ports keep the matching route blocked."""

    artifact: object | None = None
    artifact_store: object | None = None
    data_import_profile_resolver: Callable[[str, str], Any | None] | None = None
    data_import_structure_normalizer: Callable[[str], Mapping[str, Any]] | None = None
    data_import_ensure_structure: Callable[..., Mapping[str, Any]] | None = None
    scanner: object | None = None
    parser: object | None = None
    verifier: object | None = None
    protocols: object | None = None
    runner: object | None = None
    loader: object | None = None
    # Prediction stays an explicit build-owned injection point.  The worker
    # CLI does not invent a model or silently turn a missing binding into a
    # successful result.
    prediction_execute: Handler | None = None
    prediction_cancel: Handler | None = None
    training_execute: Handler | None = None
    training_cancel: Handler | None = None
    assistant_execute: Handler | None = None
    assistant_cancel: Handler | None = None
    prediction_registry: object | None = None
    prediction_registry_factory: Callable[[], object] | None = None
    training_templates: object | None = None
    training_candidate_registration: object | None = None
    training_data_loader: object | None = None
    training_executor: Callable[..., Any] | None = None
    assistant_runtime: object | None = None
    assistant_runtime_factory: Callable[..., Any] | None = None
    assistant_llm_test_runner: Callable[..., Any] | None = None
    # Per-job ToolSet binding.  The factory receives AssistantRunScope and
    # must return server-owned service ports; an omitted factory leaves tools
    # unbound and therefore fail closed.
    assistant_services_factory: Callable[..., Any] | None = None
    assistant_cancel_probe: object | None = None


_MODEL_JOB_ROUTES = {
    "model_registration_validation": "model_registration",
    "model_evaluation": "model_evaluation",
    "model_deployment": "model_deployment",
}
_RESOURCE_TYPES = {
    "artifact_validation": "artifact",
    "prediction": "prediction",
    "knowledge_index": "knowledge_document",
    "model_registration_validation": {"model_registration", "registration"},
    "model_evaluation": {"model_evaluation", "evaluation"},
    "model_deployment": {"model_deployment", "deployment", "deployment_draft"},
    "training": "experiment",
    "assistant": "agent_run",
    "assistant_llm_configuration_test": "llm_test",
    "data_import_validation": "data_import",
    "data_import_commit": "data_import",
    "export": "export",
}


def _safe_failure(store: object, item: object | None, code: str, message: str) -> object:
    if item is None:
        raise WorkerConfigurationError("WORKER_JOB_NOT_FOUND")
    marker = {"code": code, "message": message, "retryable": True}
    failed = getattr(store, "mark_failed", None)
    if not callable(failed):
        raise WorkerConfigurationError("WORKER_SHARED_LIFECYCLE_UNAVAILABLE")
    failed(item, marker)
    return marker


def _cancel_data_import_job(
    sync_session_factory, payload: Mapping[str, Any], lease_guard=None, *, allowed_project_id: str | None = None,
) -> Mapping[str, Any]:
    """Project a fenced Job cancellation onto its matching import draft."""
    from chrom_platform.infrastructure.models import JobAttempt, Project
    from chrom_platform.modules.data_center.persistence import DataImportRow
    from chrom_platform.task_core.repository import SQLAlchemyTaskRepository
    from chrom_platform.task_core.service import JobStatus, sync_job_lifecycle

    if (
        set(payload) != {"job_id", "attempt", "dispatch_id"}
        or not isinstance(payload.get("job_id"), str) or not payload["job_id"]
        or not isinstance(payload.get("attempt"), int) or isinstance(payload.get("attempt"), bool)
        or payload["attempt"] < 1
        or not isinstance(payload.get("dispatch_id"), str) or not payload["dispatch_id"]
    ):
        return {"status": "STALE"}
    job_id, attempt, dispatch_id = payload["job_id"], payload["attempt"], payload["dispatch_id"]
    with sync_session_factory() as session:
        with session.begin():
            observed_job = session.scalar(select(Job).where(Job.id == job_id))
            if observed_job is None or (
                allowed_project_id is not None and observed_job.project_id != allowed_project_id
            ):
                return {"status": "STALE"}
            session.scalar(select(Project).where(Project.id == observed_job.project_id).with_for_update())
            job = session.scalar(select(Job).where(Job.id == job_id).with_for_update().execution_options(populate_existing=True))
            cancel_dispatch = session.scalar(select(OutboxEvent).where(
                OutboxEvent.dispatch_id == dispatch_id,
                OutboxEvent.aggregate_type == "job",
                OutboxEvent.aggregate_id == job_id,
                OutboxEvent.event_type == "job.cancel",
            ).with_for_update())
            if (
                job is None or (allowed_project_id is not None and job.project_id != allowed_project_id)
                or cancel_dispatch is None or job.kind not in {"data_import_validation", "data_import_commit"}
                or job.attempt != attempt
                or dict(cancel_dispatch.payload or {}) != dict(payload)
                or dict(job.resource_ref or {}).keys() != {"type", "id"}
                or (job.resource_ref or {}).get("type") != "data_import"
                or not isinstance((job.resource_ref or {}).get("id"), str)
            ):
                return {"status": "STALE"}
            if lease_guard is not None:
                lease_guard.check()
            import_id = job.resource_ref["id"]
            imported = session.scalar(select(DataImportRow).where(
                DataImportRow.id == import_id,
                DataImportRow.project_id == job.project_id,
            ).with_for_update())
            if imported is None or imported.project_id != job.project_id or imported.id != import_id or imported.job_id != job_id:
                return {"status": "STALE"}
            execute_dispatches = session.scalars(select(OutboxEvent).where(
                OutboxEvent.aggregate_type == "job", OutboxEvent.aggregate_id == job_id,
                OutboxEvent.event_type == "job.execute",
            ).order_by(OutboxEvent.created_at.desc()).with_for_update()).all()
            execute_dispatch = next((row for row in execute_dispatches if (row.payload or {}) == {
                "job_id": job_id, "attempt": attempt, "dispatch_id": row.dispatch_id,
            }), None)
            if execute_dispatch is None:
                return {"status": "STALE"}
            if job.status in {JobStatus.CANCEL_REQUESTED.value, JobStatus.CANCELED.value}:
                updated = sync_job_lifecycle(
                    SQLAlchemyTaskRepository(lambda: session), session,
                    job_id=job_id, status=JobStatus.CANCELED,
                    attempt=attempt, expected_attempt=attempt,
                    expected_dispatch_id=execute_dispatch.dispatch_id,
                    resource_ref=job.resource_ref,
                )
                if updated is None:
                    raise WorkerConfigurationError("DATA_IMPORT_CANCEL_FENCE_INVALID")
                current_attempt = session.scalar(select(JobAttempt).where(
                    JobAttempt.job_id == job_id,
                    JobAttempt.attempt == attempt,
                ).with_for_update())
                if current_attempt is None or current_attempt.status != JobStatus.CANCELED.value:
                    raise WorkerConfigurationError("DATA_IMPORT_CANCEL_ATTEMPT_INVALID")
            elif job.status in {
                JobStatus.SUCCEEDED.value, JobStatus.PARTIAL.value, JobStatus.FAILED.value,
                JobStatus.TIMED_OUT.value, JobStatus.INTERRUPTED.value,
            }:
                return {"status": job.status, "job_id": job_id}
            else:
                return {"status": "STALE"}
            if lease_guard is not None:
                lease_guard.check()
            expected_import_status = {
                "data_import_validation": "VALIDATING",
                "data_import_commit": "COMMITTING",
            }[job.kind]
            if imported.status == expected_import_status:
                snapshot = dict(imported.request_snapshot or {})
                snapshot["terminal_projection"] = {
                    "terminal_status": JobStatus.CANCELED.value,
                    "job_id": job_id,
                    "attempt": attempt,
                    "dispatch_id": dispatch_id,
                    "reason_code": "JOB_CANCELED",
                }
                imported.request_snapshot = snapshot
                imported.status = "FAILED"
                imported.updated_at = _utc_now()
            if lease_guard is not None:
                lease_guard.check()
            return {"status": job.status, "job_id": job_id}


def _normalize_import_smiles(value: str) -> Mapping[str, Any]:
    from dataclasses import asdict
    from chrom_platform.structure_core.service import normalize_smiles

    return {**asdict(normalize_smiles(value)), "original_smiles": value}


def _ensure_import_structure(*, session, project_id: str, normalized_structure: Mapping[str, Any], source_artifact_id: str) -> Mapping[str, Any]:
    from chrom_platform.infrastructure.repositories import SQLAlchemyStructureRepository
    from chrom_platform.structure_core.schemas import NormalizeStructureRequest
    from chrom_platform.structure_core.service import StructureService

    smiles = normalized_structure.get("original_smiles")
    if not isinstance(smiles, str) or not smiles.strip():
        raise WorkerConfigurationError("DATA_IMPORT_STRUCTURE_BINDING_INVALID")
    request = NormalizeStructureRequest(
        project_id=project_id, input_type="smiles", value=smiles,
        preserve_stereochemistry=True,
    )
    structure = StructureService(None).normalize(
        request, f"structure_{uuid.uuid4().hex}", SQLAlchemyStructureRepository(session),
    )
    if (
        structure.canonical_smiles != normalized_structure.get("canonical_smiles")
        or structure.snapshot_hash != normalized_structure.get("snapshot_hash")
        or structure.has_stereochemistry != normalized_structure.get("has_stereochemistry")
        or structure.stereochemistry_status != normalized_structure.get("stereochemistry_status")
    ):
        raise WorkerConfigurationError("DATA_IMPORT_STRUCTURE_NORMALIZATION_MISMATCH")
    # The input artifact and import/record retain source provenance; the shared
    # project-scoped Structure identity remains deduplicated by its snapshot.
    return {"structure_id": structure.id, "canonical_smiles": structure.canonical_smiles}


class _CommitBeforeCall:
    """Keep synchronous provider calls outside the current DB transaction."""

    def __init__(self, session: Session, target: object):
        self._session, self._target = session, target

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._target, name)
        if not callable(value):
            return value

        def call(*args: Any, **kwargs: Any) -> Any:
            self._session.commit()
            return value(*args, **kwargs)

        # Preserve the wrapped adapter's callable contract.  Model Center's
        # side-effect ports use this signature to fail closed when an adapter
        # cannot accept its stable operation key; read-only ports remain able
        # to omit it.
        try:
            call.__signature__ = inspect.signature(value)  # type: ignore[attr-defined]
        except (TypeError, ValueError):
            pass

        return call


def _dispatch_database_sync(
    session: Session,
    message: StableWorkerMessage,
    *,
    event_type: str,
    deps: ProductionWorkerDependencies,
    fresh_session_factory: Callable[[], Session] | None = None,
) -> object:
    """Route Knowledge/Model Center through reviewed synchronous adapters."""

    job = session.scalar(select(Job).where(Job.id == message.job_id).with_for_update())
    if job is None or int(job.attempt) != message.attempt:
        raise WorkerConfigurationError("WORKER_JOB_FENCE_INVALID")
    dispatch = session.scalar(select(OutboxEvent).where(
        OutboxEvent.dispatch_id == message.dispatch_id,
        OutboxEvent.aggregate_type == "job",
        OutboxEvent.aggregate_id == job.id,
        OutboxEvent.event_type == event_type,
    ).with_for_update())
    expected = {"job_id": job.id, "attempt": message.attempt, "dispatch_id": message.dispatch_id}
    if dispatch is None or dict(dispatch.payload or {}) != expected:
        raise WorkerConfigurationError("WORKER_DISPATCH_FENCE_INVALID")
    ref = job.resource_ref if isinstance(job.resource_ref, Mapping) else {}
    lease_guard = get_current_lease_guard()
    expected_ref = _RESOURCE_TYPES.get(job.kind)
    if expected_ref is None or ref.get("type") not in ({expected_ref} if isinstance(expected_ref, str) else expected_ref):
        raise WorkerConfigurationError("WORKER_JOB_RESOURCE_INVALID")

    if job.kind == "export":
        from chrom_platform.modules.data_center.exports import execute_export_job
        if event_type not in {"job.execute", "job.cancel"}:
            raise WorkerConfigurationError("WORKER_EVENT_UNSUPPORTED")
        return execute_export_job(
            session, job=job, store=deps.artifact_store, dispatch_id=message.dispatch_id,
            lease_guard=lease_guard,
        )

    if job.kind == "knowledge_index":
        from chrom_platform.modules.knowledge.persistence import SQLAlchemyKnowledgeExecutionStore, SQLAlchemyKnowledgeRepository
        from chrom_platform.modules.knowledge.worker import KnowledgeWorker

        store = SQLAlchemyKnowledgeExecutionStore(session)
        item = store.load_index(message.job_id)
        if event_type == "job.cancel":
            if item is None:
                raise WorkerConfigurationError("WORKER_JOB_NOT_FOUND")
            return KnowledgeWorker(
                repository=SQLAlchemyKnowledgeRepository(session), artifact=None, scanner=None, parser=None, store=store,
            ).handle(item, attempt=message.attempt, dispatch_id=message.dispatch_id, lease_guard=lease_guard)
        if event_type != "job.execute":
            raise WorkerConfigurationError("WORKER_EVENT_UNSUPPORTED")
        if deps.artifact is None or deps.scanner is None or deps.parser is None:
            return _safe_failure(store, item, "KNOWLEDGE_DEPENDENCY_UNAVAILABLE", "知识索引依赖暂不可用")
        return KnowledgeWorker(
            repository=SQLAlchemyKnowledgeRepository(session),
            artifact=_CommitBeforeCall(session, deps.artifact),
            scanner=_CommitBeforeCall(session, deps.scanner),
            parser=_CommitBeforeCall(session, deps.parser),
            store=store,
        ).handle(message.job_id, attempt=message.attempt, dispatch_id=message.dispatch_id, lease_guard=lease_guard)

    if job.kind in _MODEL_JOB_ROUTES:
        from chrom_platform.modules.model_center.persistence import SQLAlchemyModelCenterRepository
        from chrom_platform.modules.model_center.worker import ModelCenterConsumer, ModelCenterWorkerMessage

        repository = SQLAlchemyModelCenterRepository(session, fresh_session_factory=fresh_session_factory)
        verifier = deps.verifier
        if (event_type == "job.execute"
                and job.kind == "model_registration_validation"
                and verifier is None):
            registration_id = ref.get("id")
            registration = (
                repository.get_registration(job.project_id, registration_id)
                if isinstance(registration_id, str) and registration_id else None
            )
            if (registration is not None
                    and registration.job_id == job.id
                    and registration.attempt == message.attempt
                    and registration.dispatch_id == message.dispatch_id
                    and registration.task_type == "gc"
                    and registration.source.kind == "external_package"
                    and registration.source.artifact_id):
                from chrom_platform.modules.model_center.production_verifier import (
                    build_production_model_package_verifier,
                )

                # The session-bound readers are created on this worker thread
                # for this execution only. Recovery keeps its own fresh-session
                # factory below; neither object is cached in shared deps.
                verifier = build_production_model_package_verifier(
                    session=session, artifact_store=deps.artifact_store,
                )
        package_recovery = None
        if job.kind == "model_registration_validation" and callable(fresh_session_factory):
            from chrom_platform.modules.model_center.package_execution_ledger import SQLAlchemyModelPackageExecutionLedger
            package_recovery = SQLAlchemyModelPackageExecutionLedger(fresh_session_factory)
        consumer = ModelCenterConsumer(
            repository,
            verifier=None if verifier is None else _CommitBeforeCall(session, verifier),
            protocols=None if deps.protocols is None else _CommitBeforeCall(session, deps.protocols),
            runner=None if deps.runner is None else _CommitBeforeCall(session, deps.runner),
            loader=None if deps.loader is None else _CommitBeforeCall(session, deps.loader),
            package_recovery=package_recovery,
        )
        model_message = ModelCenterWorkerMessage(message.job_id, message.attempt, message.dispatch_id)
        if event_type == "job.cancel":
            return consumer.handle_cancel(model_message, lease_guard=lease_guard)
        if event_type != "job.execute":
            raise WorkerConfigurationError("WORKER_EVENT_UNSUPPORTED")
        return consumer.handle(model_message, lease_guard=lease_guard)
    raise WorkerConfigurationError("WORKER_JOB_KIND_UNSUPPORTED")


async def _dispatch_database_async(
    session_factory: async_sessionmaker[AsyncSession],
    payload: Mapping[str, Any],
    *,
    event_type: str,
    deps: ProductionWorkerDependencies,
    fresh_session_factory: Callable[[], Session] | None = None,
) -> object:
    message = StableWorkerMessage.from_payload(payload, event_type=event_type)
    async with session_factory() as session:
        result = await session.run_sync(lambda sync_session: _dispatch_database_sync(
            sync_session, message, event_type=event_type, deps=deps,
            fresh_session_factory=fresh_session_factory,
        ))
        await session.commit()
        return result


def _dispatch_model_registration_sync(
    sync_session_factory: Callable[[], Session], payload: Mapping[str, Any], *,
    event_type: str, deps: ProductionWorkerDependencies,
) -> object:
    """Own the synchronous Session entirely inside the worker thread."""
    if not callable(sync_session_factory):
        raise WorkerConfigurationError("MODEL_REGISTRATION_SYNC_SESSION_FACTORY_UNAVAILABLE")
    message = StableWorkerMessage.from_payload(payload, event_type=event_type)
    session = sync_session_factory()
    try:
        result = _dispatch_database_sync(
            session, message, event_type=event_type, deps=deps,
            fresh_session_factory=sync_session_factory,
        )
        session.commit()
        return result
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


async def _dispatch_model_registration_thread(
    sync_session_factory: Callable[[], Session] | None,
    payload: Mapping[str, Any], *,
    deps: ProductionWorkerDependencies,
    slot: asyncio.Semaphore,
) -> object:
    """Keep DB/child waits off the event loop and supervise cancellation."""
    if not callable(sync_session_factory):
        raise WorkerConfigurationError("MODEL_REGISTRATION_SYNC_SESSION_FACTORY_UNAVAILABLE")
    async with slot:
        task = asyncio.create_task(asyncio.to_thread(
            _dispatch_model_registration_sync,
            sync_session_factory,
            dict(payload),
            event_type="job.execute",
            deps=deps,
        ))
        was_cancelled = False
        while True:
            try:
                result = await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                was_cancelled = True
                if task.done():
                    break
        if was_cancelled:
            if task.done():
                task.result()
            raise asyncio.CancelledError
        return result


def build_production_handlers(
    plan: ProfileWorkerPlan,
    *,
    async_session_factory: async_sessionmaker[AsyncSession] | None = None,
    session_factory: Callable[[], Session] | None = None,
    sync_session_factory: Callable[[], Session] | None = None,
    dependencies: ProductionWorkerDependencies | None = None,
    legacy_sync: bool = False,
    prediction_registry_observer: Callable[[object], None] | None = None,
) -> dict[str, Handler]:
    """Build the fixed Job/resource_ref router used by the production CLI.

    Only the three transport fields reach this function.  Every business ID
    is reloaded from the locked Job row, and every route owns one outer SQL
    transaction.  ``dependencies`` is an explicit reviewed binding; absent
    ports remain handlers and record a safe shared-lifecycle failure.
    """

    if async_session_factory is None and session_factory is None:
        raise WorkerConfigurationError("WORKER_HANDLER_SESSION_UNAVAILABLE")
    if async_session_factory is None and session_factory is not None and not legacy_sync:
        raise WorkerConfigurationError("LEGACY_SYNC_WORKER_DISABLED")
    deps = dependencies or ProductionWorkerDependencies()
    store_requested = True
    if deps.artifact_store is None and store_requested:
        artifact_root = os.getenv("ARTIFACT_ROOT", "").strip()
        if artifact_root:
            from chrom_platform.artifact_core.storage import LocalArtifactStore
            deps = replace(deps, artifact_store=LocalArtifactStore(artifact_root))
    if async_session_factory is not None:
        # Claim/finish transactions live in the production workers.  This
        # router only reads Job.kind, closes that read session, and then calls
        # scientific/provider code outside any database transaction.
        if "assistant" in plan.worker_modules:
            from chrom_platform.assembly.assistant_worker import build_assistant_worker
        if "prediction" in plan.worker_modules:
            from chrom_platform.assembly.prediction_worker_registry import build_prediction_worker_registry
            from chrom_platform.assembly.prediction_worker import build_prediction_worker
        if "training" in plan.worker_modules:
            from chrom_platform.assembly.training_worker import build_training_worker
            from chrom_platform.modules.training.persistence import StaticTrainingTemplateRegistry

        selected: dict[str, MappingHandlerAdapter] = {}
        data_csv_window = None
        if plan.build_id == "data-center-invalid-window-20261026":
            from chrom_platform.artifact_core.data_csv_window import build_data_csv_window

            data_csv_window = build_data_csv_window(
                plan.build_id, plan.manifest_sha256, plan.modules,
                manifest_path=plan.manifest_path,
            )
            if data_csv_window is None:
                raise WorkerConfigurationError("DATA_CSV_WINDOW_BINDING_INVALID")
        worker_actor = _deployment_actor_context()
        if data_csv_window is not None and not worker_actor.can_access_project(data_csv_window.project_id):
            raise WorkerConfigurationError("DATA_IMPORT_PROJECT_SCOPE_INVALID")
        prediction_registry = None
        if "datasets" in plan.worker_modules:
            if sync_session_factory is None:
                raise WorkerConfigurationError("DATA_IMPORT_SYNC_SESSION_FACTORY_UNAVAILABLE")
            from chrom_platform.modules.data_center.import_worker import DataImportWorker
            import_worker = DataImportWorker(
                sync_session_factory,
                deps.artifact_store,
                profile_resolver=(
                    data_csv_window.resolve_profile if data_csv_window is not None
                    else deps.data_import_profile_resolver or (lambda _domain, _version: None)
                ),
                structure_normalizer=deps.data_import_structure_normalizer or _normalize_import_smiles,
                ensure_structure=deps.data_import_ensure_structure or _ensure_import_structure,
                allowed_project_id=data_csv_window.project_id if data_csv_window is not None else None,
                cancel_handler=lambda *, payload, lease_guard, allowed_project_id=None: _cancel_data_import_job(
                    sync_session_factory, payload, lease_guard,
                    allowed_project_id=allowed_project_id,
                ),
            )

            async def run_data_import(payload, *, event_type: str):
                return await asyncio.to_thread(
                    import_worker.handle, dict(payload), event_type=event_type,
                    lease_guard=get_current_lease_guard(),
                )

            selected["data_import_validation"] = FunctionHandlerAdapter(
                execute_handler=lambda payload: run_data_import(payload, event_type="job.execute"),
                cancel_handler=lambda payload: run_data_import(payload, event_type="job.cancel"),
            )
            selected["data_import_commit"] = selected["data_import_validation"]
        if "prediction" in plan.worker_modules:
            if sync_session_factory is None and deps.prediction_registry is None and deps.prediction_registry_factory is None:
                raise WorkerConfigurationError("PREDICTION_SYNC_SESSION_FACTORY_UNAVAILABLE")
            prediction_registry = deps.prediction_registry
            if prediction_registry is None and deps.prediction_registry_factory is not None:
                prediction_registry = deps.prediction_registry_factory()
            if prediction_registry is None:
                prediction_registry = build_prediction_worker_registry(session_factory=sync_session_factory)
            if prediction_registry_observer is not None:
                prediction_registry_observer(prediction_registry)
        if "assistant" in plan.worker_modules and deps.assistant_services_factory is None:
            if sync_session_factory is None:
                raise WorkerConfigurationError("ASSISTANT_SYNC_SESSION_FACTORY_UNAVAILABLE")
            from chrom_platform.assembly.m4 import _worker_priority_service
            from chrom_platform.modules.assistant.priority import PriorityAssistantToolService

            def default_services_factory(*, scope, **_kwargs):
                priority = _worker_priority_service(
                    sync_session_factory, model_center_enabled=True,
                    prediction_registry=prediction_registry,
                    build_prediction_registry_if_missing=False,
                )
                if priority is None:
                    raise WorkerConfigurationError("ASSISTANT_PRIORITY_SERVICES_UNAVAILABLE")
                return {"priority": PriorityAssistantToolService(priority)}

            assistant_services_factory = default_services_factory
        else:
            assistant_services_factory = deps.assistant_services_factory
        if "prediction" in plan.worker_modules:
            selected["prediction"] = MappingHandlerAdapter(build_prediction_worker(
                session_factory=async_session_factory,
                registry=prediction_registry,
            ))
        if "training" in plan.worker_modules:
            selected["training"] = MappingHandlerAdapter(build_training_worker(
                session_factory=async_session_factory,
                templates=deps.training_templates or StaticTrainingTemplateRegistry(),
                candidate_registration=deps.training_candidate_registration,
                data_loader=deps.training_data_loader,
                executor=deps.training_executor,
            ))
        if "assistant" in plan.worker_modules:
            assistant_runtime = deps.assistant_runtime
            assistant_runtime_factory = deps.assistant_runtime_factory
            if assistant_runtime is None and assistant_runtime_factory is None:
                if sync_session_factory is None:
                    raise WorkerConfigurationError("ASSISTANT_SYNC_SESSION_FACTORY_UNAVAILABLE")
                from chrom_platform.modules.assistant.adapter import PydanticAIAdapter
                from chrom_platform.modules.assistant.priority import priority_toolset
                from chrom_platform.modules.assistant.persistence import SQLAlchemyAssistantRepository
                from chrom_platform.modules.assistant.runtime import AgentRuntime, RuntimeConfigSnapshot
                from chrom_platform.modules.assistant.trace import ContextInspector
                from chrom_platform.assembly.m4 import _FreshEventStore, _FreshTraceStore, _WorkerHistory

                assistant_repository = SQLAlchemyAssistantRepository(session_factory=sync_session_factory)
                history_loader = _WorkerHistory(assistant_repository)
                event_store = _FreshEventStore(sync_session_factory)
                trace_inspector = ContextInspector(_FreshTraceStore(sync_session_factory), mode="redacted")

                def assistant_runtime_factory(*, config=None, scope=None, **_kwargs):
                    if config is not None and getattr(config, "status", None) in {"ACTIVE", "RETIRED"}:
                        settings = dict(getattr(config, "settings", {}) or {})
                        snapshot = RuntimeConfigSnapshot(
                            str(config.url), str(config.model_id), str(config.key_reference),
                            str(config.revision), provider_configuration_id=str(config.id),
                            provider_configuration_hash=str(settings.get("config_hash", "")),
                            external_send_policy=str(settings.get("external_send_policy", "deny_external")),
                            approved_endpoints=os.environ.get("LLM_PROVIDER_APPROVED_ENDPOINTS"),
                        )
                    else:
                        # Keep the runtime structurally valid so the worker can
                        # finish the fenced Job with a safe configuration error.
                        snapshot = RuntimeConfigSnapshot(
                            "https://unconfigured.invalid/v1", "unconfigured-model",
                            "UNCONFIGURED_KEY", "unconfigured-revision", configuration_state="UNAVAILABLE",
                        )
                    return AgentRuntime(
                        PydanticAIAdapter(
                            secret_resolver=_assistant_secret_resolver,
                            approved_endpoints_resolver=lambda: os.environ.get("LLM_PROVIDER_APPROVED_ENDPOINTS"),
                        ), snapshot, toolset=priority_toolset(mode=getattr(scope, "mode", "business")),
                        history_loader=history_loader, event_store=event_store,
                        context_inspector=trace_inspector, trace_metadata_only=True,
                    )

                assistant_runtime_factory = assistant_runtime_factory
            selected["assistant"] = MappingHandlerAdapter(build_assistant_worker(
                session_factory=async_session_factory,
                runtime=assistant_runtime,
                runtime_factory=assistant_runtime_factory,
                services_factory=assistant_services_factory,
                actor_context=_deployment_actor_context(),
                cancel_probe=deps.assistant_cancel_probe,
            ))
            from chrom_platform.modules.assistant.production_worker import AssistantLlmConfigurationTestWorker
            selected["assistant_llm_configuration_test"] = MappingHandlerAdapter(AssistantLlmConfigurationTestWorker(
                session_factory=async_session_factory,
                test_runner=deps.assistant_llm_test_runner,
                secret_resolver=_assistant_secret_resolver,
                approved_endpoints_resolver=lambda: os.environ.get("LLM_PROVIDER_APPROVED_ENDPOINTS"),
            ))
        if "knowledge" in plan.worker_modules:
            selected["knowledge_index"] = FunctionHandlerAdapter(
                execute_handler=lambda payload: _dispatch_database_async(
                    async_session_factory, payload, event_type="job.execute", deps=deps,
                ),
                cancel_handler=lambda payload: _dispatch_database_async(
                    async_session_factory, payload, event_type="job.cancel", deps=deps,
                ),
            )
        if "export" in plan.allowed_job_kinds:
            selected["export"] = FunctionHandlerAdapter(
                execute_handler=lambda payload: _dispatch_database_async(
                    async_session_factory, payload, event_type="job.execute", deps=deps,
                ),
                cancel_handler=lambda payload: _dispatch_database_async(
                    async_session_factory, payload, event_type="job.cancel", deps=deps,
                ),
            )
        if "model_center" in plan.worker_modules:
            model_registration_slot = asyncio.Semaphore(1)
            for model_kind in _MODEL_JOB_ROUTES:
                if model_kind == "model_registration_validation":
                    selected[model_kind] = FunctionHandlerAdapter(
                        execute_handler=lambda payload, _slot=model_registration_slot: _dispatch_model_registration_thread(
                            sync_session_factory, payload, deps=deps, slot=_slot,
                        ),
                        cancel_handler=lambda payload: _dispatch_database_async(
                            async_session_factory, payload, event_type="job.cancel", deps=deps,
                            fresh_session_factory=sync_session_factory,
                        ),
                    )
                    continue
                selected[model_kind] = FunctionHandlerAdapter(
                    execute_handler=lambda payload: _dispatch_database_async(
                        async_session_factory, payload, event_type="job.execute", deps=deps,
                    ),
                    cancel_handler=lambda payload: _dispatch_database_async(
                        async_session_factory, payload, event_type="job.cancel", deps=deps,
                    ),
                )
        from chrom_platform.artifact_core.validation_worker import ArtifactValidationWorker
        scanner = deps.scanner
        if scanner is None:
            try:
                from chrom_platform.artifact_core.scanner_defender import DefenderCommandScanner
                scanner = DefenderCommandScanner()
            except (ImportError, OSError):
                scanner = None
        selected["artifact_validation"] = MappingHandlerAdapter(ArtifactValidationWorker(
            session_factory=async_session_factory,
            store=deps.artifact_store,
            scanner=scanner,
            dataset_scanner=data_csv_window.scanner if data_csv_window is not None else None,
            dataset_csv_policy=data_csv_window.scan_policy if data_csv_window is not None else None,
            actor=worker_actor,
            reviewed_dataset_profile_ids=(
                frozenset({"dataset-csv-tlc-header-v1-20261026"})
                if data_csv_window is not None else frozenset()
            ),
        ))
        registry = StaticHandlerRegistry(selected)

        async def route(payload: Mapping[str, Any] | StableWorkerMessage, *, event_type: str) -> object:
            if isinstance(payload, StableWorkerMessage):
                stable_payload: dict[str, Any] = {
                    "job_id": payload.job_id,
                    "attempt": payload.attempt,
                    "dispatch_id": payload.dispatch_id,
                }
                if event_type == "job.cancel" and payload.fencing_token is not None:
                    stable_payload["fencing_token"] = payload.fencing_token
            else:
                stable_payload = payload
            job_id = stable_payload.get("job_id") if isinstance(stable_payload, Mapping) else None
            if not isinstance(job_id, str) or not job_id:
                raise WorkerConfigurationError("INVALID_WORKER_PAYLOAD")
            async with async_session_factory() as session:
                kind = await session.scalar(select(Job.kind).where(Job.id == job_id))
            message = StableWorkerMessage.from_payload(
                stable_payload, event_type=event_type,
            )
            handler = registry.handler(str(kind), event_type)
            if handler is None:
                raise WorkerConfigurationError("WORKER_HANDLER_BINDING_UNAVAILABLE")
            return await handler(stable_payload)

        routes: dict[str, Handler] = {}
        for event_type in plan.allowed_event_types:
            if event_type == "prediction.execute":
                routes[event_type] = lambda message: route(message, event_type="prediction.execute")
            elif event_type in {"job.execute", "job.cancel"}:
                # Keep the captured event type keyword-only.  Otherwise the
                # consumer's optional positional lease guard is mistaken for
                # this closure default and reaches the registry as an object.
                routes[event_type] = lambda message, *, _event_type=event_type: route(message, event_type=_event_type)
        return routes

    def dispatch_sync(session: Session, message: StableWorkerMessage, *, event_type: str) -> object:
        lease_guard = get_current_lease_guard()
        with session.begin():
            job = session.scalar(select(Job).where(Job.id == message.job_id).with_for_update())
            if job is None or int(job.attempt) != message.attempt:
                raise WorkerConfigurationError("WORKER_JOB_FENCE_INVALID")
            dispatch = session.scalar(select(OutboxEvent).where(OutboxEvent.dispatch_id == message.dispatch_id, OutboxEvent.aggregate_type == "job", OutboxEvent.aggregate_id == job.id, OutboxEvent.event_type == event_type).with_for_update())
            if dispatch is None or dict(dispatch.payload or {}) != {"job_id": job.id, "attempt": message.attempt, "dispatch_id": message.dispatch_id}:
                raise WorkerConfigurationError("WORKER_DISPATCH_FENCE_INVALID")
            ref = job.resource_ref if isinstance(job.resource_ref, Mapping) else {}
            expected_ref = _RESOURCE_TYPES.get(job.kind)
            if expected_ref is None or ref.get("type") not in ({expected_ref} if isinstance(expected_ref, str) else expected_ref):
                raise WorkerConfigurationError("WORKER_JOB_RESOURCE_INVALID")
            if job.kind in {"prediction", "training", "assistant"}:
                handler = {
                    ("prediction", "job.execute"): deps.prediction_execute,
                    ("prediction", "job.cancel"): deps.prediction_cancel,
                    ("training", "job.execute"): deps.training_execute,
                    ("training", "job.cancel"): deps.training_cancel,
                    ("assistant", "job.execute"): deps.assistant_execute,
                    ("assistant", "job.cancel"): deps.assistant_cancel,
                }.get((job.kind, event_type))
                if handler is None:
                    raise WorkerConfigurationError(f"{job.kind.upper()}_HANDLER_BINDING_UNAVAILABLE")
                return handler(message)
            if job.kind == "knowledge_index":
                from chrom_platform.modules.knowledge.persistence import SQLAlchemyKnowledgeExecutionStore, SQLAlchemyKnowledgeRepository
                from chrom_platform.modules.knowledge.worker import KnowledgeWorker

                store = SQLAlchemyKnowledgeExecutionStore(session)
                item = store.load_index(message.job_id)
                if event_type == "job.cancel":
                    if item is None:
                        raise WorkerConfigurationError("WORKER_JOB_NOT_FOUND")
                    item_dispatch = item.get("dispatch_id") if isinstance(item, Mapping) else getattr(item, "dispatch_id", None)
                    return KnowledgeWorker(repository=SQLAlchemyKnowledgeRepository(session), artifact=None, scanner=None, parser=None, store=store).handle(item, attempt=message.attempt, dispatch_id=item_dispatch, lease_guard=lease_guard)
                if event_type != "job.execute":
                    raise WorkerConfigurationError("WORKER_EVENT_UNSUPPORTED")
                if deps.artifact is None or deps.scanner is None or deps.parser is None:
                    return _safe_failure(store, item, "KNOWLEDGE_DEPENDENCY_UNAVAILABLE", "知识索引依赖暂不可用")
                return KnowledgeWorker(repository=SQLAlchemyKnowledgeRepository(session), artifact=deps.artifact, scanner=deps.scanner, parser=deps.parser, store=store).handle(message.job_id, attempt=message.attempt, dispatch_id=message.dispatch_id, lease_guard=lease_guard)
            if job.kind == "artifact_validation":
                raise WorkerConfigurationError("ARTIFACT_VALIDATION_ASYNC_REQUIRED")
            if job.kind in _MODEL_JOB_ROUTES:
                from chrom_platform.modules.model_center.persistence import SQLAlchemyModelCenterRepository
                from chrom_platform.modules.model_center.worker import ModelCenterConsumer, ModelCenterWorkerMessage

                repository = SQLAlchemyModelCenterRepository(session)
                consumer = ModelCenterConsumer(repository, verifier=deps.verifier, protocols=deps.protocols, runner=deps.runner, loader=deps.loader)
                msg = ModelCenterWorkerMessage(message.job_id, message.attempt, message.dispatch_id)
                if event_type == "job.cancel":
                    return consumer.handle_cancel(msg, lease_guard=lease_guard)
                missing = {"model_registration_validation": deps.verifier is None, "model_evaluation": deps.protocols is None or deps.runner is None, "model_deployment": deps.loader is None}[job.kind]
                if missing:
                    resolved = consumer._resolve(msg, event_type="job.execute")
                    if resolved is None:
                        raise WorkerConfigurationError("WORKER_JOB_RESOURCE_INVALID")
                    record = resolved["record"]
                    if job.kind == "model_registration_validation":
                        return consumer.registration._fail(record, "model asset verifier is not configured", lease_guard=lease_guard)
                    if job.kind == "model_evaluation":
                        return consumer.evaluation._fail(record, "evaluation runner or protocol registry is not configured", lease_guard=lease_guard)
                    return consumer.deployment._fail(record, "deployment loader is not configured", lease_guard=lease_guard)
                return consumer.handle(msg, lease_guard=lease_guard)
            raise WorkerConfigurationError("WORKER_JOB_KIND_UNSUPPORTED")

    async def dispatch_async(message: StableWorkerMessage, *, event_type: str) -> object:
        assert async_session_factory is not None
        async with async_session_factory() as session:
            return await session.run_sync(lambda sync_session: dispatch_sync(sync_session, message, event_type=event_type))

    def dispatch(message: StableWorkerMessage, *, event_type: str) -> object:
        if async_session_factory is not None:
            return dispatch_async(message, event_type=event_type)
        assert session_factory is not None
        with session_factory() as session:
            return dispatch_sync(session, message, event_type=event_type)

    routes = {
        "job.execute": lambda message: dispatch(message, event_type="job.execute"),
        "job.cancel": lambda message: dispatch(message, event_type="job.cancel"),
    }
    if "prediction" in plan.worker_modules and deps.prediction_execute is not None:
        routes["prediction.execute"] = deps.prediction_execute
    return {event_type: handler for event_type, handler in routes.items() if event_type in plan.allowed_event_types}


class OutboxDispatcher:
    """Send stable IDs and finish leases only after the consumer succeeds."""

    def __init__(
        self,
        store: AsyncOutboxDispatcher,
        broker: Broker,
        handlers: Mapping[str, Handler],
        *,
        owner: str = "worker",
        allowed_event_types: frozenset[str] = frozenset({"job.execute", "job.cancel"}),
        allowed_job_kinds: frozenset[str] | None = None,
        async_runner: Callable[[Any], Any] | None = None,
        max_backoff_seconds: float = 60.0,
    ) -> None:
        self.store = store
        self.broker = broker
        self.handlers = dict(handlers)
        self.owner = owner
        self.allowed_event_types = allowed_event_types
        self.allowed_job_kinds = allowed_job_kinds
        self.async_runner = async_runner
        self.max_backoff_seconds = max_backoff_seconds
        # The broker payload intentionally has no lease generation.  Keep a
        # bounded process-local consume gate keyed by its immutable dispatch
        # id so a same-owner lease reclaim cannot run the same side effect
        # twice while this worker is alive. Durable exactly-once still belongs
        # to the handler's database idempotency contract.
        self._consume_gate = threading.Lock()
        self._consuming: set[str] = set()
        self._completed: OrderedDict[str, Any] = OrderedDict()
        self._completed_limit = 1024

    def _run_async(self, operation: Any) -> Any:
        if self.async_runner is not None:
            return self.async_runner(operation)
        # ``AsyncOutboxDispatcher`` always exposes heartbeat support.  A
        # synchronous consumer cannot safely drive that task with a transient
        # asyncio.run loop: the lease would stop renewing between calls.
        if callable(getattr(self.store, "start_heartbeat", None)):
            close = getattr(operation, "close", None)
            if callable(close):
                close()
            raise WorkerConfigurationError("LEASE_HEARTBEAT_LOOP_UNAVAILABLE")
        return asyncio.run(operation)

    async def poll_once(self, now: datetime | None = None) -> bool:
        now = now or _utc_now()
        claim = await self.store.claim_due(
            owner=self.owner,
            allowed_event_types=self.allowed_event_types,
            allowed_job_kinds=self.allowed_job_kinds,
            now=now,
        )
        if claim is None:
            return False
        try:
            StableWorkerMessage.from_payload(
                claim.payload, event_type=claim.event_type,
            )
            send_routed = getattr(self.broker, "send_routed", None)
            if callable(send_routed):
                send_routed(
                    claim.payload,
                    assistant=(claim.resource_ref or {}).get("type") in {"agent_run", "llm_test"},
                )
            else:
                self.broker.send(claim.payload)
        except Exception as exc:  # transport failure must remain retryable
            delay = min(self.max_backoff_seconds, float(2 ** min(max(1, claim.attempts) - 1, 6)))
            await self.store.retry(claim, error_code=_safe_error(exc), delay_seconds=delay, now=now)
            return False
        # Keep PROCESSING until the broker consumer reloads and acks the lease.
        return True

    def consume(self, payload: Mapping[str, Any]) -> Any:
        claim = self._run_async(
            self.store.resolve_active(
                payload,
                owner=self.owner,
                allowed_event_types=self.allowed_event_types,
                allowed_job_kinds=self.allowed_job_kinds,
            )
        )
        message = StableWorkerMessage.from_payload(
            claim.payload, event_type=claim.event_type,
        )
        handler = self.handlers.get(claim.event_type)
        if handler is None:
            fail_permanent = getattr(self.store, "fail_permanent", None)
            if callable(fail_permanent):
                self._run_async(fail_permanent(claim, error_code="WORKER_HANDLER_BINDING_UNAVAILABLE"))
            raise WorkerConfigurationError("WORKER_HANDLER_BINDING_UNAVAILABLE")
        guard: LeaseGuard | None = None
        start_heartbeat = getattr(self.store, "start_heartbeat", None)
        if callable(start_heartbeat):
            guard = self._run_async(start_heartbeat(claim))
        with self._consume_gate:
            completed = claim.dispatch_id in self._completed
            result = self._completed.get(claim.dispatch_id)
            if not completed:
                if claim.dispatch_id in self._consuming:
                    raise WorkerConfigurationError("DUPLICATE_WORKER_MESSAGE")
                self._consuming.add(claim.dispatch_id)
        if completed:
            if guard is not None:
                self._run_async(guard.stop())
                guard.check()
            if not self._run_async(self.store.ack(claim)):
                raise WorkerConfigurationError("STALE_OUTBOX_LEASE")
            return result
        context_token = _CURRENT_LEASE_GUARD.set(guard)
        try:
            result = _invoke_handler(handler, message, guard)
            if inspect.isawaitable(result):
                result = self._run_async(result)
        except Exception as exc:
            _CURRENT_LEASE_GUARD.reset(context_token)
            if guard is not None:
                self._run_async(guard.stop())
            with self._consume_gate:
                self._consuming.discard(claim.dispatch_id)
            if isinstance(exc, LeaseLostError) or (guard is not None and guard.lease_lost):
                raise WorkerConfigurationError("STALE_OUTBOX_LEASE") from exc
            if isinstance(exc, WorkerConfigurationError):
                fail_permanent = getattr(self.store, "fail_permanent", None)
                if callable(fail_permanent):
                    self._run_async(fail_permanent(claim, error_code=_safe_error(exc)))
            else:
                self._run_async(self.store.retry(claim, error_code=_safe_error(exc), delay_seconds=1.0))
            raise
        _CURRENT_LEASE_GUARD.reset(context_token)
        if guard is not None:
            self._run_async(guard.stop())
            try:
                guard.check()
            except LeaseLostError as exc:
                with self._consume_gate:
                    self._consuming.discard(claim.dispatch_id)
                raise WorkerConfigurationError("STALE_OUTBOX_LEASE") from exc
        acknowledged = self._run_async(self.store.ack(claim))
        with self._consume_gate:
            self._consuming.discard(claim.dispatch_id)
            # Cache only a successful durable CAS. If ack loses its lease
            # race, leave the dispatch uncached so a legitimate retry is not
            # swallowed.
            if acknowledged:
                self._completed[claim.dispatch_id] = result
                self._completed.move_to_end(claim.dispatch_id)
                while len(self._completed) > self._completed_limit:
                    self._completed.popitem(last=False)
        if not acknowledged:
            raise WorkerConfigurationError("STALE_OUTBOX_LEASE")
        return result


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (OSError, ProcessLookupError):
        return False
    return True


def _windows_process_handle(pid: int):
    """Open a process for read-only identity/status checks."""

    if not sys.platform.startswith("win") or pid <= 0:
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    # PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE
    return kernel32.OpenProcess(0x1000 | 0x00100000, 0, pid)


def _windows_process_alive(pid: int) -> bool:
    handle = _windows_process_handle(pid)
    if not handle:
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        exit_code = ctypes.c_uint32()
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)) and exit_code.value == 259)
    finally:
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)


def _windows_process_starttime(pid: int) -> str | None:
    """Return the Windows process creation FILETIME as a stable identity."""

    if not sys.platform.startswith("win"):
        return None
    handle = _windows_process_handle(pid)
    if not handle:
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        creation = ctypes.c_ulonglong()
        exit_time = ctypes.c_ulonglong()
        kernel_time = ctypes.c_ulonglong()
        user_time = ctypes.c_ulonglong()
        kernel32.GetProcessTimes.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulonglong),
            ctypes.POINTER(ctypes.c_ulonglong),
            ctypes.POINTER(ctypes.c_ulonglong),
            ctypes.POINTER(ctypes.c_ulonglong),
        ]
        kernel32.GetProcessTimes.restype = ctypes.c_int
        if not kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(kernel_time), ctypes.byref(user_time)):
            return None
        return str(creation.value)
    finally:
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)


def _linux_boot_id() -> str | None:
    if not sys.platform.startswith("linux"):
        return None
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    return value or None


def _linux_process_starttime(pid: int) -> str | None:
    if not sys.platform.startswith("linux"):
        return None
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        closing = raw.rfind(")")
        if closing < 0:
            return None
        fields = raw[closing + 2:].split()
        value = fields[19]
    except (OSError, UnicodeError, IndexError):
        return None
    return value if value.isdigit() else None


def _linux_process_cmdline(pid: int) -> str | None:
    if not sys.platform.startswith("linux"):
        return None
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
    except OSError:
        return None


def worker_healthcheck(
    ready_path: str | Path | None = None,
    *,
    pid_alive: Callable[[int], bool] | None = None,
    boot_id_reader: Callable[[], str | None] | None = None,
    process_start_reader: Callable[[int], str | None] | None = None,
    cmdline_reader: Callable[[int], str | None] | None = None,
    platform: str | None = None,
) -> int:
    """Read-only readiness check; never initializes DB, Broker, or handlers."""

    try:
        configured = str(ready_path) if ready_path is not None else os.getenv("WORKER_READY_FILE", "").strip()
        if not configured:
            raise WorkerConfigurationError("MISSING_WORKER_READY_FILE")
        marker = json.loads(Path(configured).read_text(encoding="utf-8"))
        expected = {"ready", "pid", "instance_id", "boot_id", "process_starttime"}
        if not isinstance(marker, dict) or set(marker) != expected:
            raise WorkerConfigurationError("READY_MARKER_INVALID")
        if marker["ready"] is not True:
            raise WorkerConfigurationError("READY_MARKER_INVALID")
        pid = marker["pid"]
        instance_id = marker["instance_id"]
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise WorkerConfigurationError("READY_MARKER_INVALID")
        if not isinstance(instance_id, str) or not _INSTANCE_ID_PATTERN.fullmatch(instance_id):
            raise WorkerConfigurationError("READY_MARKER_INVALID")
        current_platform = platform or sys.platform
        if not (pid_alive or _pid_alive)(pid):
            raise WorkerConfigurationError("WORKER_PID_NOT_ALIVE")
        if current_platform.startswith("linux"):
            boot = (boot_id_reader or _linux_boot_id)()
            start = (process_start_reader or _linux_process_starttime)(pid)
            command = (cmdline_reader or _linux_process_cmdline)(pid)
            if not boot or marker["boot_id"] != boot:
                raise WorkerConfigurationError("WORKER_BOOT_ID_INVALID")
            if not start or marker["process_starttime"] != start:
                raise WorkerConfigurationError("WORKER_STARTTIME_INVALID")
            if not command or "chrom_platform.worker_cli" not in command:
                raise WorkerConfigurationError("WORKER_CMDLINE_INVALID")
        elif current_platform.startswith("win"):
            start = (process_start_reader or _windows_process_starttime)(pid)
            if not start or marker["process_starttime"] != start:
                raise WorkerConfigurationError("WORKER_STARTTIME_INVALID")
        return 0
    except WorkerConfigurationError as exc:
        print(f"worker_unhealthy:{exc}", flush=True)
        return 2
    except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError):
        print("worker_unhealthy:READY_MARKER_INVALID", flush=True)
        return 2


class ReadyFile:
    """Create readiness only after initialization and remove only our file."""

    def __init__(self, path: str | Path, *, instance_id: str | None = None, pid: int | None = None, pid_alive: Callable[[int], bool] | None = None, boot_id_reader: Callable[[], str | None] | None = None, process_start_reader: Callable[[int], str | None] | None = None) -> None:
        self.path = Path(path)
        self.pid = int(os.getpid() if pid is None else pid)
        self.instance_id = instance_id or uuid.uuid4().hex
        self.pid_alive = pid_alive or _pid_alive
        self.boot_id_reader = boot_id_reader or _linux_boot_id
        self.process_start_reader = process_start_reader or (_windows_process_starttime if sys.platform.startswith("win") else _linux_process_starttime)
        self.created = False
        self._marker: dict[str, object] | None = None
        self._lock_handle: object | None = None
        self.startup_conflict = False
        self._startup_checked = False

    @property
    def marker(self) -> dict[str, object]:
        return {
            "pid": self.pid,
            "instance_id": self.instance_id,
            "boot_id": self.boot_id_reader(),
            "process_starttime": self.process_start_reader(self.pid),
        }

    def mark_ready(self, *, before_publish: Callable[[dict[str, object]], None] | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{self.pid}.{self.instance_id}.tmp")
        acquired, existing_lock = self._acquire_lock()
        if not acquired:
            self.startup_conflict = True
            return
        try:
            identity = self.marker
            if (sys.platform.startswith("linux") and (not identity["boot_id"] or not identity["process_starttime"])) or (sys.platform.startswith("win") and not identity["process_starttime"]):
                raise WorkerConfigurationError("WORKER_IDENTITY_UNAVAILABLE")
            current = self._read_marker()
            # An already-existing mutex belongs to another startup in this
            # process or another process.  Same-PID replacement is allowed
            # for an in-process handoff; a different PID must never win by
            # replacing the marker after the first worker acquired the lock.
            if existing_lock and (current is None or current.get("pid") != self.pid) and (
                self._startup_checked or self._marker_is_live(current or {})
            ):
                self.startup_conflict = True
                return
            if current is not None and not existing_lock and self._marker_is_live(current) and current.get("instance_id") != self.instance_id:
                self.startup_conflict = True
                return
            self._marker = {"ready": True, **identity}
            if before_publish is not None:
                before_publish(dict(identity))
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(self._marker, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            self.created = True
        finally:
            temporary.unlink(missing_ok=True)
            if self.startup_conflict or not self.created:
                self._release_lock()

    def _acquire_lock(self) -> tuple[bool, bool]:
        if self._lock_handle is not None:
            return True, True
        if sys.platform.startswith("win"):
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            kernel32.WaitForSingleObject.restype = ctypes.c_uint32
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int
            name = "Local\\ChromPlatformWorkerReady-" + hashlib.sha256(str(self.path.resolve()).casefold().encode("utf-8")).hexdigest()
            handle = kernel32.CreateMutexW(None, 1, name)
            if not handle:
                raise WorkerConfigurationError("WORKER_LOCK_UNAVAILABLE")
            existing = ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS
            if existing:
                wait = kernel32.WaitForSingleObject(handle, 0)
                if wait not in {0, 0x80}:  # WAIT_OBJECT_0 / WAIT_ABANDONED
                    kernel32.CloseHandle(handle)
                    return False, True
            self._lock_handle = ("win", handle)
            return True, existing
        try:
            import fcntl
            lock_path = self.path.with_name(f".{self.path.name}.lock")
            handle = lock_path.open("a+b")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close()
                return False, True
            self._lock_handle = ("flock", handle)
            return True, False
        except ImportError as exc:
            raise WorkerConfigurationError("WORKER_LOCK_UNAVAILABLE") from exc

    def _release_lock(self) -> None:
        lock = self._lock_handle
        self._lock_handle = None
        if lock is None:
            return
        kind, handle = lock
        if kind == "win":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
            kernel32.ReleaseMutex.restype = ctypes.c_int
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int
            kernel32.ReleaseMutex(handle)
            kernel32.CloseHandle(handle)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def _marker_is_live(self, marker: dict[str, object]) -> bool:
        if marker.get("ready") is not True:
            return False
        pid = marker.get("pid")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            return False
        instance_id = marker.get("instance_id")
        start = marker.get("process_starttime")
        if not isinstance(instance_id, str) or not instance_id or not isinstance(start, str) or not start:
            return False
        if not self.pid_alive(pid):
            return False
        current_start = self.process_start_reader(pid)
        if current_start is not None and current_start != start:
            return False
        if sys.platform.startswith("linux"):
            boot = marker.get("boot_id")
            current_boot = self.boot_id_reader()
            if current_boot is not None and boot != current_boot:
                return False
        return True

    def _read_marker(self) -> dict[str, object] | None:
        try:
            marker = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, TypeError, ValueError):
            return None
        return marker if isinstance(marker, dict) else None

    def clear_existing(self) -> None:
        """Validate ownership without deleting a marker from another process."""

        self._startup_checked = True
        marker = self._read_marker()
        if marker is not None and marker.get("pid") not in {None, self.pid}:
            if self._marker_is_live(marker):
                raise WorkerConfigurationError("WORKER_ALREADY_RUNNING")
        # Invalid/dead markers are intentionally retained.  Removing by path
        # races with another worker's atomic replacement and can delete its
        # valid marker.  mark_ready() replaces this path atomically after the
        # current worker has completed initialization; healthcheck rejects the
        # retained stale marker while initialization is incomplete.

    def _owns_marker(self) -> bool:
        marker = self._read_marker()
        if marker is None:
            return False
        expected = self._marker
        if expected is None:
            return marker.get("pid") == self.pid and marker.get("instance_id") == self.instance_id
        return marker == expected

    def remove(self) -> None:
        try:
            if self.created and self._owns_marker():
                self.path.unlink(missing_ok=True)
        finally:
            self.created = False
            self._release_lock()

    def __enter__(self) -> "ReadyFile":
        return self

    def __exit__(self, *_: object) -> None:
        self.remove()


class DramatiqTransport:
    """Dramatiq adapter; imports are intentionally deferred until runtime."""

    def __init__(self, actor: Any, worker: Any, broker: Any, assistant_actor: Any | None = None) -> None:
        self.actor, self.worker, self.broker = actor, worker, broker
        self.assistant_actor = assistant_actor or actor

    @classmethod
    def create(cls, redis_url: str, queue_name: str, consume: Callable[[Mapping[str, Any]], Any], *, assistant_enabled: bool = False) -> "DramatiqTransport":
        try:
            import dramatiq
            from dramatiq.brokers.redis import RedisBroker
            from dramatiq.worker import Worker
        except ImportError as exc:
            raise WorkerConfigurationError("DRAMATIQ_DEPENDENCY_UNAVAILABLE") from exc
        try:
            broker = RedisBroker(url=redis_url)
            dramatiq.set_broker(broker)
            actor = dramatiq.actor(consume, queue_name=queue_name, max_retries=0)
            assistant_actor = None
            # Keep scientific execution serialized while assistant waits run
            # independently and cannot occupy the science queue's only slot.
            worker = Worker(broker, queues=[queue_name], worker_threads=1)
            if assistant_enabled:
                assistant_actor = dramatiq.actor(consume, actor_name="chrom_assistant", queue_name=queue_name + "-assistant", max_retries=0)
                worker = _WorkerGroup([worker, Worker(broker, queues=[queue_name + "-assistant"], worker_threads=1)])
        except Exception as exc:
            raise WorkerConfigurationError(f"BROKER_INITIALIZATION_FAILED_{_safe_error(exc)}") from exc
        return cls(actor, worker, broker, assistant_actor)

    def send(self, payload: Mapping[str, Any]) -> None:
        self.actor.send(dict(payload))

    def send_routed(self, payload: Mapping[str, Any], *, assistant: bool) -> None:
        (self.assistant_actor if assistant else self.actor).send(dict(payload))


class _WorkerGroup:
    """Start and stop the queue workers as one transport lifecycle."""

    def __init__(self, workers: list[Any]) -> None:
        self.workers = workers
        self._started: list[Any] = []

    def start(self) -> None:
        try:
            for worker in self.workers:
                worker.start()
                self._started.append(worker)
        except Exception:
            try:
                self.stop()
            except Exception:
                pass
            raise

    def stop(self) -> None:
        first_error: Exception | None = None
        started, self._started = self._started, []
        for worker in reversed(started):
            try:
                worker.stop()
            except Exception as exc:
                first_error = first_error or exc
        if first_error is not None:
            raise first_error


def _linux_execution_layer_required() -> None:
    # The local acceptance worker is supported on Windows as well as the
    # deployment Linux profile.  Dependency and connection validation below
    # remains mandatory; there is no platform-specific fallback or install.
    return None


class WorkerRuntime:
    """Initialize a real profile runtime; no fake binding is supplied here."""

    def __init__(
        self,
        config: WorkerConfig,
        handlers: Mapping[str, Handler] | None = None,
        *,
        transport_factory: Callable[..., DramatiqTransport] | None = None,
        handler_factory: Callable[..., Mapping[str, Handler]] | None = None,
    ) -> None:
        self.config = config
        self.handlers = dict(handlers or {})
        self.transport_factory = transport_factory
        self.handler_factory = handler_factory or build_production_handlers
        self.plan: ProfileWorkerPlan | None = None
        self.transport: DramatiqTransport | None = None
        self.dispatcher: OutboxDispatcher | None = None
        self.poll_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.ready_file = ReadyFile(config.ready_file)
        self.loop: asyncio.AbstractEventLoop | None = None
        self.async_engine: AsyncEngine | None = None
        self.sync_engine = None
        self.fatal_error: WorkerConfigurationError | None = None
        self._loop_ready = threading.Event()
        self.prediction_registry: object | None = None

    def initialize(self) -> None:
        _linux_execution_layer_required()
        self.ready_file.clear_existing()
        self.plan = ProfileWorkerPlan.from_manifest(self.config.manifest_path, expected_profile=self.config.profile)
        self.plan.import_worker_types()
        try:
            self.loop = asyncio.new_event_loop()
            self.async_engine = create_async_engine(self.config.database_url, pool_pre_ping=True)
            if {"assistant", "prediction", "datasets"} & set(self.plan.worker_modules):
                if not self.config.sync_database_url:
                    raise WorkerConfigurationError("MISSING_SYNC_DATABASE_URL")
                self.sync_engine = create_engine(self.config.sync_database_url, pool_pre_ping=True, future=True)
            healthy, error = self.loop.run_until_complete(self._check_database_health())
            if not healthy:
                raise WorkerConfigurationError(f"DATABASE_HEALTH_FAILED_{error or 'UNKNOWN'}")
            factory = async_sessionmaker(bind=self.async_engine, expire_on_commit=False, class_=AsyncSession)
            sync_factory = sessionmaker(bind=self.sync_engine, expire_on_commit=False) if self.sync_engine is not None else None
        except WorkerConfigurationError:
            self._close_async_resources()
            raise
        except Exception as exc:
            self._close_async_resources()
            raise WorkerConfigurationError(f"DATABASE_INITIALIZATION_FAILED_{_safe_error(exc)}") from exc
        if not self.handlers:
            try:
                handler_kwargs = {"async_session_factory": factory, "sync_session_factory": sync_factory}
                if self.handler_factory is build_production_handlers:
                    handler_kwargs["prediction_registry_observer"] = self._capture_prediction_registry
                self.handlers = dict(self.handler_factory(self.plan, **handler_kwargs))
            except WorkerConfigurationError:
                self._close_async_resources()
                raise
            except Exception as exc:
                self._close_async_resources()
                raise WorkerConfigurationError(f"WORKER_HANDLER_BINDING_UNAVAILABLE_{_safe_error(exc)}") from exc
        if not self.handlers or not (set(self.handlers) & self.plan.allowed_event_types):
            self._close_async_resources()
            raise WorkerConfigurationError("WORKER_HANDLER_BINDING_UNAVAILABLE")
        # The actor closure calls only the static handler registry supplied by
        # the build; it cannot import a user-supplied path from a message.
        self.dispatcher = OutboxDispatcher(
            AsyncOutboxDispatcher(
                factory,
                lease_seconds=self.config.lease_seconds,
                heartbeat_seconds=self.config.heartbeat_seconds,
            ),
            _DeferredBroker(),
            self.handlers,
            owner=f"worker-{os.getpid()}-{self.ready_file.instance_id}",
            allowed_event_types=self.plan.allowed_event_types,
            allowed_job_kinds=self.plan.allowed_job_kinds,
            async_runner=self._run_async_from_consumer,
        )
        try:
            if self.transport_factory is None:
                transport = DramatiqTransport.create(
                    self.config.redis_url, self.config.queue_name, self.dispatcher.consume,
                    assistant_enabled="assistant" in self.plan.worker_modules,
                )
            else:
                transport = self.transport_factory(self.config.redis_url, self.config.queue_name, self.dispatcher.consume)
        except WorkerConfigurationError:
            self._close_async_resources()
            raise
        except Exception as exc:
            self._close_async_resources()
            raise WorkerConfigurationError(f"BROKER_INITIALIZATION_FAILED_{_safe_error(exc)}") from exc
        self.transport = transport
        self.dispatcher.broker = transport

    def run(self) -> None:
        if self.transport is None or self.dispatcher is None:
            raise WorkerConfigurationError("WORKER_NOT_INITIALIZED")
        self.poll_thread = threading.Thread(target=self._poll_loop, name="outbox-dispatcher", daemon=True)
        self.poll_thread.start()
        if not self._loop_ready.wait(timeout=max(5.0, self.config.lease_seconds)):
            raise WorkerConfigurationError("WORKER_EVENT_LOOP_UNAVAILABLE")
        self.transport.worker.start()
        with self.ready_file:
            metadata_identity: dict[str, object] | None = None
            metadata_published = False

            def publish_metadata(identity: dict[str, object]) -> None:
                nonlocal metadata_identity, metadata_published
                metadata_identity = dict(identity)
                if self.prediction_registry is None:
                    return
                from chrom_platform.assembly.prediction_worker_registry import export_prediction_metadata
                from chrom_platform.assembly.prediction_metadata import publish_prediction_metadata

                registry_snapshot = export_prediction_metadata(self.prediction_registry)
                publish_prediction_metadata(
                    registry_snapshot,
                    ready_path=self.config.ready_file,
                    worker_identity=metadata_identity,
                )
                metadata_published = True

            try:
                self.ready_file.mark_ready(before_publish=publish_metadata)
                if self.ready_file.startup_conflict or not self.ready_file.created:
                    raise WorkerConfigurationError("WORKER_ALREADY_RUNNING")
                self.stop_event.wait()
            finally:
                if metadata_published and metadata_identity is not None:
                    from chrom_platform.assembly.prediction_metadata import remove_prediction_metadata

                    remove_prediction_metadata(ready_path=self.config.ready_file, worker_identity=metadata_identity)
        self.shutdown()
        if self.fatal_error is not None:
            raise self.fatal_error

    def _capture_prediction_registry(self, registry: object) -> None:
        self.prediction_registry = registry

    def _poll_loop(self) -> None:
        assert self.dispatcher is not None
        if self.loop is None:
            raise WorkerConfigurationError("WORKER_EVENT_LOOP_UNAVAILABLE")
        asyncio.set_event_loop(self.loop)
        self._loop_ready.set()
        self.loop.run_until_complete(self._poll_loop_async())

    def _run_async_from_consumer(self, operation: Any) -> Any:
        if self.loop is None or self.loop.is_closed() or not self._loop_ready.is_set():
            close = getattr(operation, "close", None)
            if callable(close):
                close()
            raise WorkerConfigurationError("WORKER_EVENT_LOOP_UNAVAILABLE")
        try:
            future = asyncio.run_coroutine_threadsafe(operation, self.loop)
        except RuntimeError:
            close = getattr(operation, "close", None)
            if callable(close):
                close()
            raise
        return future.result()

    async def _poll_loop_async(self) -> None:
        assert self.dispatcher is not None
        while not self.stop_event.is_set():
            await asyncio.sleep(self.config.poll_seconds)
            if not self.stop_event.is_set():
                try:
                    await self.dispatcher.poll_once()
                except Exception as exc:
                    self.fatal_error = WorkerConfigurationError(f"WORKER_POLL_FAILED_{_safe_error(exc)}")
                    self.stop_event.set()
                    return

    async def _check_database_health(self) -> tuple[bool, str | None]:
        if self.async_engine is None:
            return False, "ENGINE_UNAVAILABLE"
        try:
            async with self.async_engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception as exc:
            return False, type(exc).__name__
        return True, None

    def _close_async_resources(self) -> None:
        if self.loop is None:
            if self.sync_engine is not None:
                self.sync_engine.dispose()
                self.sync_engine = None
            return
        try:
            try:
                # Optional Assistant code must stay absent from trimmed builds.
                adapter = sys.modules.get("chrom_platform.modules.assistant.adapter")
                close_clients = getattr(adapter, "close_native_clients", None)
                if callable(close_clients) and not self.loop.is_closed():
                    self.loop.run_until_complete(close_clients())
            finally:
                if self.async_engine is not None and not self.loop.is_closed():
                    self.loop.run_until_complete(self.async_engine.dispose())
        finally:
            try:
                if self.sync_engine is not None:
                    self.sync_engine.dispose()
            finally:
                self.sync_engine = None
                self.async_engine = None
                try:
                    if not self.loop.is_closed():
                        self.loop.close()
                finally:
                    self.loop = None

    def shutdown(self) -> None:
        self.stop_event.set()
        try:
            if self.poll_thread is not None:
                self.poll_thread.join(timeout=max(1.0, self.config.lease_seconds))
            if self.transport is not None:
                try:
                    self.transport.worker.stop()
                finally:
                    close = getattr(self.transport.broker, "close", None)
                    if callable(close):
                        close()
        finally:
            try:
                self.ready_file.remove()
            finally:
                self._close_async_resources()


class _DeferredBroker:
    """Placeholder replaced by Dramatiq before any outbox poll can run."""

    def send(self, payload: Mapping[str, Any]) -> None:
        raise WorkerConfigurationError("BROKER_NOT_INITIALIZED")


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--healthcheck"]:
        return worker_healthcheck()
    if arguments:
        print("worker_not_ready:INVALID_ARGUMENTS", flush=True)
        return 2
    try:
        config = WorkerConfig.from_env()
        runtime = WorkerRuntime(config)
        runtime.initialize()
    except WorkerConfigurationError as exc:
        print(f"worker_not_ready:{exc}", flush=True)
        return 2
    stop = runtime.stop_event

    def request_stop(_signum: int, _frame: Any) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        runtime.run()
    except Exception as exc:  # safe startup/shutdown summary only
        print(f"worker_stopped:{_safe_error(exc)}", flush=True)
        runtime.shutdown()
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "DramatiqTransport", "OutboxDispatcher", "PROFILE_ALLOWLIST", "ProfileWorkerPlan", "ReadyFile",
    "StableWorkerMessage", "WorkerConfig", "get_current_lease_guard", "worker_healthcheck",
    "WorkerConfigurationError", "WorkerRuntime", "ProductionWorkerDependencies", "build_production_handlers", "main",
]
