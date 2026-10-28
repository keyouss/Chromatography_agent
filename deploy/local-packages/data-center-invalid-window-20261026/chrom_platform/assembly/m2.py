"""M2 assembly with request-scoped persistence bindings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from fastapi import APIRouter
from sqlalchemy import MetaData
from sqlalchemy.orm import Session

from chrom_platform.infrastructure.metadata import m2_metadata
from chrom_platform.modules.data_center.persistence import (
    SQLAlchemyDataDomainRegistry, SQLAlchemyDataRepository,
    SQLAlchemyPredictionRecordRepository, SQLAlchemyPredictionRecordWriter,
)
from chrom_platform.modules.data_center.router import create_router as create_data_center_router
from chrom_platform.modules.data_center.errors import DataValidationError
from chrom_platform.modules.data_center.service import DataImportService, DataVersionService, ExportService, PredictionRecordService

if TYPE_CHECKING:
    from chrom_platform.modules.prediction.persistence import SQLAlchemyPredictionPersistence
    from chrom_platform.modules.prediction.service import PredictionCreateService


class CsvImportProfiles:
    """Build-owned profiles; no production domain is enabled in this slice."""

    def __init__(self, profiles: dict[tuple[str, str], object] | None = None, *, data_csv_window: object | None = None) -> None:
        self._profiles = dict(profiles or {})
        self.data_csv_window = data_csv_window

    @classmethod
    def for_data_csv_window(cls, window: object) -> "CsvImportProfiles":
        domain = getattr(window, "domain")
        profile = getattr(window, "profile")
        return cls({(domain.id, domain.version): profile}, data_csv_window=window)

    def resolve(self, domain_id: str, schema_version: str):
        return self._profiles.get((domain_id, schema_version))


class _ImportableDomainRegistry:
    """Expose only domains backed by a reviewed append/reject CSV profile."""

    def __init__(self, source: object, profiles: CsvImportProfiles) -> None:
        self.source = source
        self.profiles = profiles

    def _approved(self, domain):
        if domain is None or self.profiles.resolve(domain.id, domain.version) is None:
            return None
        window = self.profiles.data_csv_window
        if window is not None:
            expected = getattr(window, "domain")
            if domain.model_dump(mode="json") != expected.model_dump(mode="json"):
                return None
        if domain.deduplication_policy != "reject" or "append" not in domain.supported_import_modes:
            return None
        return domain.model_copy(update={"supported_import_modes": ["append"]})

    def list(self):
        return tuple(value for row in self.source.list() if (value := self._approved(row)) is not None)

    def get(self, domain_id: str, schema_version: str | None = None):
        return self._approved(self.source.get(domain_id, schema_version))


class _WindowBoundDataImportService(DataImportService):
    """Enforce the one reviewed source mapping and project for this build."""

    def __init__(self, repository, domains, *, window, **kwargs) -> None:
        super().__init__(repository, domains, **kwargs)
        self._window = window

    def _check_window(self, *, project_id: str, domain_id: str, schema_version: str, import_mode: str, field_mapping: dict[str, str]) -> None:
        if domain_id != self._window.domain.id:
            return
        if (
            project_id != self._window.project_id
            or schema_version != self._window.domain.version
            or import_mode != "append"
            or field_mapping != self._window.source_column_mapping
        ):
            raise DataValidationError("TLC 导入必须使用受控项目、schema、append 模式及固定源列映射")

    def create(self, request):
        self._check_window(
            project_id=request.project_id, domain_id=request.domain_id,
            schema_version=request.schema_version, import_mode=request.import_mode,
            field_mapping=request.field_mapping,
        )
        return super().create(request)

    def patch(self, project_id, import_id, patch, *, expected_revision=None, if_match=None):
        current = self.get(project_id, import_id)
        self._check_window(
            project_id=current.project_id, domain_id=current.domain_id,
            schema_version=patch.schema_version or current.schema_version,
            import_mode=patch.import_mode or current.import_mode,
            field_mapping=patch.field_mapping if patch.field_mapping is not None else current.field_mapping,
        )
        return super().patch(
            project_id, import_id, patch,
            expected_revision=expected_revision, if_match=if_match,
        )


class _DataImportRetryTaskPolicy:
    """Delegate policy behavior, resolving only the optional import retry target."""

    def __init__(self, delegate: object, resolver: Callable) -> None:
        self._delegate = delegate
        self._resolver = resolver

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    def resolve_target(self, ctx, resource_ref, action: str):
        if action == "job.retry" and resource_ref.type == "data_import":
            return self._resolver(ctx, resource_ref, action)
        return self._delegate.resolve_target(ctx, resource_ref, action)



@dataclass(frozen=True)
class M2Assembly:
    """Factories and ports; a bound Session exists only for one request."""

    session_factory: Callable[[], Session] | None = None
    auth: object | None = None
    access: object | None = None
    structure: object | None = None
    capability_registry: object | None = None
    model_port: object | None = None
    domain_registry: object | None = None
    parser: object | None = None
    artifact: object | None = None
    confirmation: object | None = None
    idempotency_store: object | None = None
    modules: frozenset[str] = frozenset()
    chromatography_types: frozenset[str] = frozenset()
    session: Session | None = None
    prediction_persistence: SQLAlchemyPredictionPersistence | None = None
    prediction_service: PredictionCreateService | None = None
    prediction_record_repository: SQLAlchemyPredictionRecordRepository | None = None
    prediction_record_service: PredictionRecordService | None = None
    data_repository: SQLAlchemyDataRepository | None = None
    data_domain_registry: SQLAlchemyDataDomainRegistry | None = None
    data_import_service: DataImportService | None = None
    data_import_workflow: object | None = None
    csv_import_profiles: CsvImportProfiles | None = None
    data_version_service: DataVersionService | None = None
    export_service: ExportService | None = None
    routers: tuple[APIRouter, APIRouter] = ()

    @property
    def metadata(self) -> MetaData:
        return m2_metadata()


def _filter_router(router: APIRouter, prefixes: set[str]) -> APIRouter:
    router.routes[:] = [route for route in router.routes if any(route.path.startswith(p) for p in prefixes)]
    return router


def _prediction_record_archive_target(record):
    import hashlib
    import json
    from chrom_platform.core.dto import ResourceRef
    from chrom_platform.task_core.service import TargetSnapshot

    config_hash = hashlib.sha256(json.dumps(
        {"record_id": record.id, "etag": record.etag}, sort_keys=True,
        separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    return TargetSnapshot(
        project_id=record.project_id,
        resource_ref=ResourceRef(type="prediction_record", id=record.id),
        revision=record.revision, config_hash=config_hash, etag=record.etag,
        policy_version="deployment-default-v1",
        summary=[{"label": "预测记录", "value": record.id}, {"label": "状态", "value": record.status}],
        can_confirm=record.status != "ARCHIVED",
        blockers=[] if record.status != "ARCHIVED" else [{"code": "ALREADY_ARCHIVED", "message": "预测记录已归档"}],
    )


def create_m2_routers(
    *, prediction_service: PredictionCreateService | None = None,
    prediction_read_service: object | None = None, prediction_registry: object | None = None,
    data_import_service: DataImportService | None = None, data_version_service: DataVersionService | None = None,
    data_domain_registry: object | None = None, prediction_record_service: PredictionRecordService | None = None,
    export_service: ExportService | None = None,
    import_workflow: object | None = None,
    data_repository: object | None = None, prediction_repository: object | None = None,
    idempotency_store: object | None = None,
    modules: set[str] | frozenset[str] | None = None,
    chromatography_types: set[str] | frozenset[str] | None = None,
) -> tuple[APIRouter, APIRouter]:
    """Create M2 routers without registering them on an application."""
    selected = set(modules) if modules is not None else {"prediction", "records", "datasets"}

    if "prediction" in selected:
        from chrom_platform.modules.prediction.router import create_router as create_prediction_router

        prediction = create_prediction_router(
            prediction_service=prediction_service,
            read_service=prediction_read_service,
            registry=prediction_registry,
            enabled_types=chromatography_types,
            batch_metadata_provider=_batch_metadata_provider if batch_enabled else None,
        )
    else:
        prediction = APIRouter()
    data_center = create_data_center_router(
        data_import_service=data_import_service, data_version_service=data_version_service,
        domain_registry=data_domain_registry, prediction_record_service=prediction_record_service,
        export_service=export_service,
        import_workflow=import_workflow,
        data_repository=data_repository, prediction_repository=prediction_repository,
        idempotency_store=idempotency_store,
    )
    if "prediction" not in selected:
        prediction.routes[:] = []
    prefixes: set[str] = set()
    if "datasets" in selected:
        prefixes.update({"/data-domains", "/data-versions", "/data-imports"})
    if "records" in selected:
        prefixes.update({"/prediction-records", "/prediction-drafts", "/exports"})
    _filter_router(data_center, prefixes)
    return prediction, data_center


def assemble_m2_metadata() -> MetaData:
    return m2_metadata()


def assemble_m2(
    session_factory: Callable[[], Session] | None = None, *, auth: object | None = None,
    access: object | None = None, structure: object | None = None,
    capability_registry: object | None = None, model_port: object | None = None,
    domain_registry: object | None = None, parser: object | None = None,
    artifact: object | None = None, confirmation: object | None = None,
    idempotency_store: object | None = None,
    modules: set[str] | frozenset[str] | None = None,
    chromatography_types: set[str] | frozenset[str] | None = None,
    csv_import_profiles: CsvImportProfiles | None = None,
    data_csv_window: object | None = None,
) -> M2Assembly:
    """Assemble only factories; never capture a long-lived Session."""
    if data_csv_window is not None and csv_import_profiles is not None:
        raise ValueError("provide either data_csv_window or csv_import_profiles")
    profiles = (
        CsvImportProfiles.for_data_csv_window(data_csv_window)
        if data_csv_window is not None
        else csv_import_profiles or CsvImportProfiles()
    )
    routers = create_m2_routers(
        prediction_registry=capability_registry,
        idempotency_store=idempotency_store,
        modules=modules,
        chromatography_types=chromatography_types,
        
        
    )
    if "records" in (modules if modules is not None else {"prediction", "records", "datasets"}) and session_factory is not None and confirmation is not None:
        from chrom_platform.modules.data_center.persistence import SQLAlchemyPredictionRecordRepository
        from chrom_platform.task_core.confirmation_target_resolver import register_confirmation_target_resolver
        from chrom_platform.task_core.service import TaskError

        def resolve_prediction_record_confirmation(ctx, resource_ref, action):
            if action != "resource.archive" or resource_ref.type != "prediction_record":
                raise TaskError(400, "VALIDATION_FAILED", "预测记录仅支持归档确认")
            if not ctx.actor.can("prediction:read") or not ctx.actor.can("prediction:archive"):
                raise TaskError(403, "PERMISSION_DENIED", "当前主体无权确认预测记录归档")
            if not ctx.actor.project_ids:
                raise TaskError(404, "RESOURCE_NOT_FOUND", "预测记录不存在或当前主体不可见")
            with session_factory() as session:
                record = SQLAlchemyPredictionRecordRepository(session).get_prediction_any(ctx.actor.project_ids, resource_ref.id)
                if record is None:
                    raise TaskError(404, "RESOURCE_NOT_FOUND", "预测记录不存在或当前主体不可见")
                return _prediction_record_archive_target(record)

        register_confirmation_target_resolver(confirmation, "prediction_record", resolve_prediction_record_confirmation)
    if "datasets" in (modules if modules is not None else {"prediction", "records", "datasets"}) and session_factory is not None and confirmation is not None:
        from chrom_platform.modules.data_center.import_workflow import SQLAlchemyImportWorkflow
        from chrom_platform.task_core.confirmation_target_resolver import register_confirmation_target_resolver

        artifact_store = getattr(artifact, "store", None)

        def resolve_data_import_confirmation(ctx, resource_ref, action):
            with session_factory() as session:
                return SQLAlchemyImportWorkflow(
                    session, artifact_store, confirmation, profile_resolver=profiles.resolve,
                ).resolve_target(ctx, resource_ref, action)

        register_confirmation_target_resolver(confirmation, "data_import", resolve_data_import_confirmation)
        job_service = getattr(confirmation, "job_service", None)
        policy = getattr(job_service, "policy", None)
        resolver = getattr(confirmation, "resolver", None)
        if policy is not None and resolver is not None:
            job_service.policy = _DataImportRetryTaskPolicy(policy, resolver)
    return M2Assembly(
        session_factory=session_factory, auth=auth, access=access, structure=structure,
        capability_registry=capability_registry, model_port=model_port,
        domain_registry=domain_registry, parser=parser, artifact=artifact,
        confirmation=confirmation, idempotency_store=idempotency_store, routers=routers,
        modules=frozenset(modules) if modules is not None else frozenset({"prediction", "records", "datasets"}),
        chromatography_types=frozenset(chromatography_types or ()),
        csv_import_profiles=profiles,
    )


def bind_m2(assembly: M2Assembly, session: Session, *, idempotency_store: object | None = None) -> M2Assembly:
    """Build services against one caller-owned request Session."""
    domains = _ImportableDomainRegistry(
        assembly.domain_registry or SQLAlchemyDataDomainRegistry(session),
        assembly.csv_import_profiles or CsvImportProfiles(),
    )
    data_repository = SQLAlchemyDataRepository(session)
    record_repository = None
    prediction_persistence = None
    prediction_service = None
    if "prediction" in assembly.modules:
        from chrom_platform.modules.data_center.persistence import SQLAlchemyPredictionRecordRepository, SQLAlchemyPredictionRecordWriter
        from chrom_platform.modules.prediction.persistence import SQLAlchemyPredictionPersistence
        from chrom_platform.modules.prediction.service import PredictionCreateService

        record_repository = SQLAlchemyPredictionRecordRepository(session)
        prediction_persistence = SQLAlchemyPredictionPersistence(session, record_writer=SQLAlchemyPredictionRecordWriter())
        structure_port = assembly.structure(session) if callable(assembly.structure) else assembly.structure
        prediction_service = PredictionCreateService(
            access=assembly.access or prediction_persistence, structures=structure_port,
            capabilities=assembly.capability_registry, models=assembly.model_port or assembly.capability_registry,
            idempotency=prediction_persistence, atomic=prediction_persistence,
        )
    service_type = _WindowBoundDataImportService if assembly.csv_import_profiles.data_csv_window is not None else DataImportService
    import_service_kwargs = {}
    if service_type is _WindowBoundDataImportService:
        import_service_kwargs["window"] = assembly.csv_import_profiles.data_csv_window
    import_service = service_type(
        data_repository, domains, validation_port=assembly.parser,
        atomic_commit_port=data_repository, artifact_port=assembly.artifact,
        **import_service_kwargs,
    )
    import_workflow = None
    if "datasets" in assembly.modules and assembly.confirmation is not None:
        from chrom_platform.modules.data_center.import_workflow import SQLAlchemyImportWorkflow

        import_workflow = SQLAlchemyImportWorkflow(
            session, getattr(assembly.artifact, "store", None), assembly.confirmation,
            profile_resolver=(assembly.csv_import_profiles or CsvImportProfiles()).resolve,
        )
    version_service = DataVersionService(data_repository)
    from chrom_platform.task_core.service import OperationContext

    def consume_prediction_record_archive(confirmation_id, actor, record, caller_session):
        if assembly.confirmation is None:
            raise RuntimeError("confirmation service unavailable")
        if record.status == "ARCHIVED":
            from chrom_platform.task_core.service import TaskError
            raise TaskError(409, "CONFIRMATION_INVALID", "预测记录已归档")
        assembly.confirmation.consume_for_mutation(
            OperationContext(actor=actor), confirmation_id,
            target=_prediction_record_archive_target(record),
            action="resource.archive",
            result_ref={"record_id": record.id, "status": "ARCHIVED", "revision": record.revision + 1},
            session=caller_session,
        )

    record_service = None
    export_service = None
    if "records" in assembly.modules and record_repository is not None:
        from chrom_platform.modules.data_center.exports import SQLAlchemyExportRepository

        record_service = PredictionRecordService(
            record_repository, archive=record_repository, replay_source=prediction_persistence,
            archive_confirmation=consume_prediction_record_archive,
        )
        export_service = ExportService(SQLAlchemyExportRepository(session))
    routers = create_m2_routers(
        prediction_service=prediction_service, prediction_read_service=prediction_persistence,
        prediction_registry=assembly.capability_registry, data_import_service=import_service,
        data_version_service=version_service, data_domain_registry=domains,
        prediction_record_service=record_service, data_repository=data_repository,
        export_service=export_service,
        import_workflow=import_workflow,
        prediction_repository=record_repository, idempotency_store=idempotency_store if idempotency_store is not None else assembly.idempotency_store,
        modules=assembly.modules,
        chromatography_types=assembly.chromatography_types,
    )
    return M2Assembly(
        session_factory=assembly.session_factory, auth=assembly.auth, access=assembly.access,
        structure=assembly.structure, capability_registry=assembly.capability_registry,
        model_port=assembly.model_port, domain_registry=assembly.domain_registry,
        parser=assembly.parser, artifact=assembly.artifact, confirmation=assembly.confirmation,
        idempotency_store=idempotency_store if idempotency_store is not None else assembly.idempotency_store, session=session,
        modules=assembly.modules,
        chromatography_types=assembly.chromatography_types,
        prediction_persistence=prediction_persistence, prediction_service=prediction_service,
        prediction_record_repository=record_repository, prediction_record_service=record_service,
        export_service=export_service,
        data_repository=data_repository, data_domain_registry=domains,
        data_import_service=import_service, data_version_service=version_service, routers=routers,
        data_import_workflow=import_workflow, csv_import_profiles=assembly.csv_import_profiles,
    )


__all__ = ["CsvImportProfiles", "M2Assembly", "assemble_m2", "assemble_m2_metadata", "bind_m2", "create_m2_routers"]
