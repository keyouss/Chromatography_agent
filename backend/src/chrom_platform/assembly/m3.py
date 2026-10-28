"""M3 (knowledge/training/model-center) composition.

The assembly owns registration only.  A caller supplies a request or worker
``Session`` to :func:`bind_m3`; repositories then share that transaction and
never commit or close it.  No in-memory repository is selected implicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from fastapi import APIRouter
from sqlalchemy import ForeignKeyConstraint, MetaData
from sqlalchemy.orm import Session

from chrom_platform.infrastructure.metadata import Base
from chrom_platform.modules.knowledge.persistence import (
    KnowledgeBase,
    SQLAlchemyKnowledgeJobQueue,
    SQLAlchemyKnowledgeExecutionStore,
    SQLAlchemyKnowledgeJobCoordinator,
    SQLAlchemyKnowledgeRepository,
)
from chrom_platform.modules.knowledge.service import KnowledgeService
from chrom_platform.modules.knowledge.router import create_router as create_knowledge_router
from chrom_platform.modules.model_center.persistence import (
    MODEL_CENTER_METADATA,
    SQLAlchemyModelCenterJobPort,
    SQLAlchemyModelCenterRepository,
)
from chrom_platform.modules.model_center.service import ModelCenterService
from chrom_platform.modules.model_center.worker import DeploymentWorker, ModelCenterConsumer
from chrom_platform.modules.model_center.router import create_router as create_model_center_router
from chrom_platform.artifact_core.repository import SQLAlchemyProjectArtifactReader
from chrom_platform.modules.training.persistence import SQLAlchemyTrainingRepository, StaticTrainingTemplateRegistry, TrainingBase, TrainingOutboxDispatcher
from chrom_platform.modules.training.router import create_router as create_training_router
from chrom_platform.modules.training.service import TrainingService
from chrom_platform.modules.training.worker import TrainingWorker
from chrom_platform.task_core.repository import SQLAlchemyTaskRepository
from chrom_platform.task_core.service import JobService


_MODULES = frozenset({"knowledge", "training", "model_center"})


def _selected_modules(modules: set[str] | frozenset[str] | None) -> set[str]:
    selected = set(modules) if modules is not None else set(_MODULES)
    unknown = selected - _MODULES
    if unknown:
        raise ValueError(f"unknown M3 modules: {sorted(unknown)}")
    return selected


def _copy_metadata(*registries: MetaData) -> MetaData:
    # The source registries already resolved their names.  Reapplying the M1
    # convention here rewrites explicit M3 check names and drifts from Alembic.
    result = MetaData()
    seen: set[str] = set()
    for registry in registries:
        for name, table in registry.tables.items():
            if name in seen:
                raise ValueError(f"duplicate table ownership: {name}")
            table.to_metadata(result)
            seen.add(name)
    return result


def _fk(metadata: MetaData, name: str, table: str, columns: tuple[str, ...], target: tuple[str, ...]) -> None:
    if table not in metadata.tables or any(item.split(".", 1)[0] not in metadata.tables for item in target):
        # A trimmed profile may intentionally omit an upstream optional table
        # (for example training without datasets). Do not leave a dangling
        # metadata reference in that profile.
        return
    metadata.tables[table].append_constraint(
        ForeignKeyConstraint(columns, target, name=name, ondelete="RESTRICT")
    )


def m3_metadata(
    modules: set[str] | frozenset[str] | None = None,
    *,
    include_m2: bool = True,
) -> MetaData:
    """Compose selected M3 registries with the already-built M1/M2 schema."""

    selected = _selected_modules(modules)
    # Keep the historical default (M1+M2+M3) for callers that ask for the
    # complete M3 registry.  Trimmed builds can explicitly compose M1+M3
    # without importing M2-owned registries.
    if include_m2:
        try:
            from chrom_platform.infrastructure.metadata import m2_metadata

            registries: list[MetaData] = [m2_metadata()]
        except ModuleNotFoundError:
            registries = [Base.metadata]
    else:
        registries = [Base.metadata]
    if "knowledge" in selected:
        registries.append(KnowledgeBase.metadata)
    if "training" in selected:
        registries.append(TrainingBase.metadata)
    if "model_center" in selected:
        registries.append(MODEL_CENTER_METADATA)
    result = _copy_metadata(*registries)
    tables = result.tables

    if "knowledge" in selected:
        _fk(result, "fk_knowledge_document_project_id_project", "knowledge_document", ("project_id",), ("project.id",))
        _fk(result, "fk_knowledge_document_project_artifact", "knowledge_document", ("project_id", "artifact_id"), ("artifact.project_id", "artifact.id"))
        _fk(result, "fk_knowledge_document_job_id_job", "knowledge_document", ("job_id",), ("job.id",))
        _fk(result, "fk_citation_snapshot_project_id_project", "citation_snapshot", ("project_id",), ("project.id",))
    if "training" in selected:
        _fk(result, "fk_training_experiment_project_id_project", "training_experiment", ("project_id",), ("project.id",))
        _fk(result, "fk_training_experiment_data_version_id_data_version", "training_experiment", ("data_version_id",), ("data_version.id",))
        _fk(result, "fk_training_experiment_job_id_job", "training_experiment", ("job_id",), ("job.id",))
        _fk(result, "fk_training_job_id_job", "training_job", ("id",), ("job.id",))
        _fk(result, "fk_training_job_project_id_project", "training_job", ("project_id",), ("project.id",))
        _fk(result, "fk_training_job_experiment_id_training_experiment", "training_job", ("experiment_id",), ("training_experiment.id",))
        _fk(result, "fk_training_outbox_job_id_training_job", "training_outbox", ("job_id",), ("training_job.id",))
        _fk(result, "fk_training_audit_event_project_id_project", "training_audit_event", ("project_id",), ("project.id",))
        _fk(result, "fk_training_candidate_project_id_project", "training_candidate", ("project_id",), ("project.id",))
        _fk(result, "fk_training_candidate_project_artifact", "training_candidate", ("project_id", "artifact_id"), ("artifact.project_id", "artifact.id"))
        _fk(result, "fk_training_candidate_experiment_id_training_experiment", "training_candidate", ("experiment_id",), ("training_experiment.id",))
        _fk(result, "fk_experiment_metric_experiment_id_training_experiment", "experiment_metric", ("experiment_id",), ("training_experiment.id",))
    if "model_center" in selected:
        for table in ("registration", "model", "evaluation", "approval_request", "deployment_draft", "deployment", "production_pointer", "deployment_ledger", "model_center_audit_event", "model_comparison"):
            _fk(result, f"fk_{table}_project_id_project", table, ("project_id",), ("project.id",))
        _fk(result, "fk_registration_job_id_job", "registration", ("job_id",), ("job.id",))
        _fk(result, "fk_evaluation_job_id_job", "evaluation", ("job_id",), ("job.id",))
        _fk(result, "fk_model_project_artifact", "model", ("project_id", "artifact_id"), ("artifact.project_id", "artifact.id"))
        _fk(result, "fk_evaluation_protocol_data_version_id_data_version", "evaluation_protocol", ("data_version_id",), ("data_version.id",))
        _fk(result, "fk_evaluation_project_artifact", "evaluation", ("project_id", "report_artifact_id"), ("artifact.project_id", "artifact.id"))

    return result


@dataclass(frozen=True)
class M3Assembly:
    """Factories only; a bound Session is deliberately absent."""

    modules: frozenset[str] = _MODULES
    include_m2: bool = True
    knowledge_router_factory: Callable[..., APIRouter] = create_knowledge_router
    training_router_factory: Callable[..., APIRouter] = create_training_router
    model_center_router_factory: Callable[..., APIRouter] = create_model_center_router
    routers: tuple[APIRouter, APIRouter, APIRouter] = ()

    @property
    def metadata(self) -> MetaData:
        return m3_metadata(self.modules, include_m2=self.include_m2)


@dataclass(frozen=True)
class M3RequestPorts:
    """Ports bound to one caller-owned request/worker transaction."""

    session: Session
    knowledge_repository: SQLAlchemyKnowledgeRepository | None = None
    knowledge_execution_store: SQLAlchemyKnowledgeExecutionStore | None = None
    knowledge_job_coordinator: SQLAlchemyKnowledgeJobCoordinator | None = None
    knowledge_service: KnowledgeService | None = None
    training_repository: object | None = None
    training_dispatcher: TrainingOutboxDispatcher | None = None
    model_center_repository: SQLAlchemyModelCenterRepository | None = None
    training_service: TrainingService | None = None
    model_center_service: ModelCenterService | None = None
    model_center_consumer: ModelCenterConsumer | None = None
    job_service: JobService | None = None
    training_worker: TrainingWorker | None = None
    model_center_worker: DeploymentWorker | None = None
    routers: tuple[APIRouter, APIRouter, APIRouter] = ()


def _selected_router(router: APIRouter, selected: set[str], module: str) -> APIRouter:
    if module not in selected:
        router.routes[:] = []
    return router


def create_m3_routers(
    *,
    knowledge_service: object | None = None,
    training_service: object | None = None,
    model_center_service: object | None = None,
    idempotency_store: object | None = None,
    modules: set[str] | frozenset[str] | None = None,
) -> tuple[APIRouter, APIRouter, APIRouter]:
    """Create independent router factories without registering an app."""

    selected = _selected_modules(modules)
    return (
        _selected_router(create_knowledge_router(service=knowledge_service, idempotency_store=idempotency_store), selected, "knowledge"),
        _selected_router(create_training_router(service=training_service), selected, "training"),
        _selected_router(create_model_center_router(service=model_center_service), selected, "model_center"),
    )


def assemble_m3(
    *,
    modules: set[str] | frozenset[str] | None = None,
    include_m2: bool = True,
    knowledge_service: object | None = None,
    training_service: object | None = None,
    model_center_service: object | None = None,
) -> M3Assembly:
    selected = frozenset(_selected_modules(modules))
    routers = create_m3_routers(
        knowledge_service=knowledge_service,
        training_service=training_service,
        model_center_service=model_center_service,
        modules=selected,
    )
    return M3Assembly(modules=selected, include_m2=include_m2, routers=routers)


def bind_m3(assembly: M3Assembly, session: Session, *, idempotency_store: object | None = None) -> M3RequestPorts:
    """Bind SQL ports for exactly one request/worker transaction."""

    if not isinstance(session, Session):
        raise TypeError("bind_m3 requires a caller-owned SQLAlchemy Session")
    training_repository = SQLAlchemyTrainingRepository(session) if "training" in assembly.modules else None
    training_dispatcher = TrainingOutboxDispatcher(training_repository, owner="m3-training-dispatcher") if training_repository is not None else None
    knowledge_repository = SQLAlchemyKnowledgeRepository(session) if "knowledge" in assembly.modules else None
    knowledge_execution_store = SQLAlchemyKnowledgeExecutionStore(session) if knowledge_repository is not None else None
    knowledge_job_coordinator = SQLAlchemyKnowledgeJobCoordinator(session) if knowledge_repository is not None else None
    model_center_repository = SQLAlchemyModelCenterRepository(session) if "model_center" in assembly.modules else None
    model_center_service = ModelCenterService(
        model_center_repository,
        artifacts=SQLAlchemyProjectArtifactReader(session),
        jobs=SQLAlchemyModelCenterJobPort(session),
    ) if model_center_repository is not None else None
    model_center_consumer = ModelCenterConsumer(model_center_repository) if model_center_repository is not None else None
    job_service = None
    if knowledge_job_coordinator is not None:
        job_service = JobService(
            SQLAlchemyTaskRepository(lambda: session),
            queue_available=True,
            lifecycle_coordinator=knowledge_job_coordinator,
        )
    knowledge_service = KnowledgeService(
        knowledge_repository,
        jobs=SQLAlchemyKnowledgeJobQueue(session) if knowledge_repository is not None else None,
    ) if knowledge_repository is not None else None
    training_service = TrainingService(
        training_repository,
        templates=StaticTrainingTemplateRegistry(),
        candidate_registration=model_center_service,
    ) if training_repository is not None else None
    training_worker = TrainingWorker(
        training_repository,
        StaticTrainingTemplateRegistry(),
        candidate_registration=model_center_service,
    ) if training_repository is not None else None
    model_center_worker = DeploymentWorker(model_center_repository) if model_center_repository is not None else None
    routers = create_m3_routers(
        knowledge_service=knowledge_service,
        training_service=training_service,
        model_center_service=model_center_service,
        idempotency_store=idempotency_store,
        modules=assembly.modules,
    )
    return M3RequestPorts(
        session=session,
        knowledge_repository=knowledge_repository,
        knowledge_execution_store=knowledge_execution_store,
        knowledge_job_coordinator=knowledge_job_coordinator,
        knowledge_service=knowledge_service,
        training_repository=training_repository,
        training_dispatcher=training_dispatcher,
        model_center_repository=model_center_repository,
        training_service=training_service,
        model_center_service=model_center_service,
        model_center_consumer=model_center_consumer,
        job_service=job_service,
        training_worker=training_worker,
        model_center_worker=model_center_worker,
        routers=routers,
    )


assemble_m3_metadata = m3_metadata

__all__ = [
    "M3Assembly", "M3RequestPorts", "assemble_m3", "assemble_m3_metadata",
    "bind_m3", "create_m3_routers", "m3_metadata",
]
