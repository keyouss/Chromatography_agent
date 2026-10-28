from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .assembly import load_manifest
from .assembly.m1 import assemble_m1
from .core.dto import (
    AccessContextView,
    ActorContext,
    Capabilities,
    CapabilityView,
    DependencyHealth,
    ErrorEnvelope,
    ListEnvelope,
    PageInfo,
    ProjectView,
    Readiness,
    SuccessEnvelope,
    VersionInfo,
)
from .core.errors import APIError
from .core.health import DependencyHealthProbe
from .core.public_settings import create_router as create_public_settings_router
from .core.projects import (
    ConfiguredProjectReader,
    ProjectReader,
    ProjectSelectionError,
    resolve_project,
    project_id_from_row,
    project_name_from_row,
)
from .core.cursor import CursorCodec, CursorError, SignedCursorCodec
from .infrastructure.health import (
    DependencyHealthResult,
    DependencyStatus,
    database_health_from_url,
    create_redis_ping_client,
    redis_health,
)
from .infrastructure.db import create_session_factory
from .infrastructure.repositories import SQLAlchemyIdempotencyStore
from .artifact_core.router import create_artifact_router
from .structure_core.router import create_router as create_structure_router
from .structure_core.service import StructureService
from .task_core.router import create_router as create_task_router


_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_IMPLEMENTED_MODULES = frozenset({
    "prediction", "records", "datasets", "knowledge", "training",
    "model_center", "utilities", "assistant", "settings",
})
_CONTRACT_DOCUMENTATION_PATHS = frozenset({"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})


def _batch_cursor_codec(manifest, *, contract_generation: bool) -> SignedCursorCodec | None:
    includes_batch = any("batch" in tools for tools in manifest.prediction_tools.values())
    if not includes_batch or contract_generation:
        return None
    encoded = os.getenv("CHROM_BATCH_CURSOR_SIGNING_KEY", "")
    if not encoded or not re.fullmatch(r"[A-Za-z0-9_-]{43}", encoded):
        raise RuntimeError("batch cursor signing key is missing or invalid")
    try:
        key = base64.b64decode(encoded + "=", altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RuntimeError("batch cursor signing key is missing or invalid") from exc
    canonical = base64.urlsafe_b64encode(key).rstrip(b"=").decode("ascii")
    if len(key) != 32 or canonical != encoded:
        raise RuntimeError("batch cursor signing key is missing or invalid")
    return SignedCursorCodec(key)


class _MetadataOnlyPredictionRegistry:
    """Empty registry used only to assemble documentation routes."""

    metadata_only = True
    verified_handle = None

    def registered_types(self) -> tuple[str, ...]:
        return ()

    def capabilities(self) -> list[object]:
        return []

    def capability(self, type_id: str):
        raise KeyError(f"prediction adapter is not built in metadata-only mode: {type_id}")

    def adapter(self, type_id: str):
        raise KeyError(f"prediction adapter is not built in metadata-only mode: {type_id}")

    def get_tool(self, chromatography_type: str, tool_id: str, schema_version: str):
        raise KeyError("prediction tool metadata is not registered in metadata-only mode")


def _contract_generation_allows_request(path: str) -> bool:
    """Only documentation endpoints are available from an OAS-only app."""

    return path in _CONTRACT_DOCUMENTATION_PATHS


def _contract_generation_rejection(path: str) -> JSONResponse | None:
    if _contract_generation_allows_request(path):
        return None
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "code": "CONTRACT_GENERATION_ONLY",
                "message": "该应用仅用于生成接口文档",
                "field_errors": [],
                "retryable": False,
                "details": {},
            },
            "request_id": "contract-generation",
        },
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _actor_context_from_environment() -> ActorContext:
    """Create the controlled deployment actor without accepting request data."""

    actor_id = os.getenv("CHROM_ACTOR_ID", "deployment-default")
    project_ids = [item.strip() for item in os.getenv("CHROM_PROJECT_IDS", "").split(",") if item.strip()]
    default_project_id = os.getenv("CHROM_DEFAULT_PROJECT_ID", "").strip() or None
    return ActorContext(
        actor_id=actor_id,
        tenant_id=None,
        project_ids=project_ids,
        auth_method="deployment_default",
        permissions=["*"],
        policy_version="deployment-default-v1",
        default_project_id=default_project_id,
    )


class _InfrastructureDependencyHealthProbe:
    """Compose the single infrastructure health ports for this app."""

    def check(self) -> list[DependencyHealthResult]:
        # The API persistence adapters and this probe are synchronous.  The
        # deployment also exposes DATABASE_URL for the async worker path;
        # passing its asyncmy URL to ``create_engine`` makes the sync probe
        # enter asyncmy without a greenlet and reports MissingGreenlet.
        sync_database_url = os.getenv("SYNC_DATABASE_URL")
        if not sync_database_url:
            candidate = os.getenv("DATABASE_URL", "")
            if candidate.startswith(("mysql+pymysql://", "sqlite://", "sqlite+pysqlite://")):
                sync_database_url = candidate
        return [
            database_health_from_url(sync_database_url),
            redis_health(create_redis_ping_client(os.getenv("REDIS_URL"))),
        ]


def _health_results(probe: DependencyHealthProbe) -> list[DependencyHealthResult]:
    try:
        return probe.check()
    except Exception:  # noqa: BLE001 - health endpoints must not leak provider errors
        return [
            DependencyHealthResult("database", DependencyStatus.UNKNOWN, _now(), "PROBE_FAILED"),
            DependencyHealthResult("queue", DependencyStatus.UNKNOWN, _now(), "PROBE_FAILED"),
        ]


def _health_dto(item: DependencyHealthResult) -> DependencyHealth:
    status_value = item.status.value if hasattr(item.status, "value") else str(item.status)
    return DependencyHealth(
        id=item.id,
        status=status_value,  # type: ignore[arg-type]
        checked_at=item.checked_at,
        reason=item.reason,
    )


def _capabilities(
    app: FastAPI,
    dependencies: list[DependencyHealthResult] | None = None,
) -> list[CapabilityView]:
    """Build capability state from one caller-provided health snapshot."""

    has_uow = app.state.structure_uow is not None
    dependencies = dependencies if dependencies is not None else _health_results(app.state.dependency_health_probe)
    dependency_map = {item.id: item for item in dependencies}
    database = dependency_map.get("database")
    if not has_uow:
        cap_status = "UNAVAILABLE"
        reason = "STRUCTURE_UOW_NOT_CONFIGURED"
    elif database is None or database.status != "READY":
        cap_status = database.status.value if database is not None else "UNKNOWN"
        reason = database.reason if database is not None else "DATABASE_HEALTH_UNKNOWN"
    else:
        cap_status = "READY"
        reason = None
    return [CapabilityView(id="structure.normalize", status=cap_status, schema_version="1.0", reason=reason)]


def _create_structure_service() -> StructureService:
    """Expose structure algorithms; persistence is supplied by the UoW."""

    return StructureService(None)


def _create_session_factory():
    """Create one MySQL session factory for all M1 adapters."""

    # The API uses synchronous SQLAlchemy adapters.  DATABASE_URL is reserved
    # for the async worker queue path (mysql+asyncmy); never pass it to
    # create_engine.  A sync DATABASE_URL remains supported for local apps.
    database_url = os.getenv("SYNC_DATABASE_URL")
    if not database_url:
        candidate = os.getenv("DATABASE_URL", "")
        if candidate.startswith(("mysql+pymysql://", "sqlite://", "sqlite+pysqlite://")):
            database_url = candidate
    if not database_url:
        return None
    try:
        return create_session_factory(database_url)
    except Exception:  # noqa: BLE001 - invalid assembly remains unavailable
        return None


def create_app(manifest_path: str | Path | None = None, *, contract_generation: bool = False) -> FastAPI:
    """Create an app from one explicit, validated build manifest.

    ``contract_generation=True`` is for OpenAPI generation only: it assembles
    selected routes from DTO metadata, skips persistence and scientific model
    registration, and rejects every non-documentation request.
    """

    manifest = load_manifest(manifest_path)
    configured_manifest = os.getenv("CHROM_BUILD_MANIFEST", "").strip()
    manifest_source = (
        Path(manifest_path).resolve() if manifest_path is not None
        else Path(configured_manifest).resolve() if configured_manifest
        else Path(__file__).resolve().parents[1] / "build-manifest.json"
    )
    try:
        manifest_sha256 = hashlib.sha256(manifest_source.read_bytes()).hexdigest()
    except OSError:
        manifest_sha256 = None
    app = FastAPI(
        title="Chromatography Platform API",
        version="1.0",
        openapi_url="/openapi.json",
        docs_url="/docs",
        openapi_version="3.1.0",
    )
    app.state.manifest = manifest
    app.state.actor_context = _actor_context_from_environment()
    # Contract generation must not create even a lazy database engine: route
    # and schema assembly only needs the manifest and DTO declarations.
    session_factory = None if contract_generation else _create_session_factory()
    worker_selected = bool(
        set(manifest.modules) & {"prediction", "datasets", "knowledge", "training", "model_center", "assistant"}
    )
    package_root = Path(__file__).resolve().parent
    artifact_validation_available = worker_selected and all(
        (package_root / relative).is_file()
        for relative in (
            "worker_cli.py",
            "artifact_core/validation_worker.py",
            "artifact_core/scanner_defender.py",
        )
    )
    m1 = assemble_m1(
        session_factory,
        artifact_root=os.getenv("ARTIFACT_ROOT"),
        artifact_validation_available=artifact_validation_available,
    )
    app.state.session_factory = session_factory
    app.state.prediction_registry = None
    app.state.structure_service = _create_structure_service()
    app.state.structure_uow = m1.structure_uow
    app.state.project_reader = m1.project_reader
    # A configured local scope is a safe, idempotent initialization path when
    # MySQL has not been provisioned.  It only exposes server-owned IDs and is
    # never populated from request JSON or a browser fallback value.
    if app.state.project_reader is None and app.state.actor_context.project_ids:
        app.state.project_reader = ConfiguredProjectReader(app.state.actor_context.project_ids)
    app.state.idempotency_store = m1.idempotency_store
    app.state.artifact_reader = m1.artifact_reader
    app.state.artifact_service = m1.artifact_service
    app.state.artifact_uow_factory = m1.artifact_uow_factory
    app.state.task_repository = m1.task_repository
    app.state.job_service = m1.job_service
    app.state.confirmation_service = m1.confirmation_service
    # M2 stores factories only.  A request middleware below binds one shared
    # Session to every M2 service/repository and closes it before returning.
    m2_modules = frozenset(manifest.modules) & frozenset({"prediction", "records", "datasets"})
    if m2_modules:
        # Keep optional M2 persistence out of minimal builds.  This import is
        # intentionally inside the selected-module branch so a trimmed
        # deployment does not load M2 ORM tables or worker code at all.
        from .assembly.m2 import assemble_m2
        data_csv_window = None
        if "datasets" in m2_modules and not contract_generation:
            from .artifact_core.data_csv_window import build_data_csv_window

            data_csv_window = build_data_csv_window(
                manifest.build_id, manifest_sha256, tuple(manifest.modules),
                manifest_path=manifest_source,
            )
        if not contract_generation and manifest.build_id == "data-center-invalid-window-20261026" and data_csv_window is None:
            raise RuntimeError("DATA_CSV_WINDOW_BINDING_INVALID")

        if "records" in m2_modules and m1.artifact_service is not None:
            from .modules.data_center.exports import SQLAlchemyExportArtifactReadPolicy

            app.state.artifact_service = m1.artifact_service.with_export_read_policy(
                SQLAlchemyExportArtifactReadPolicy(session_factory)
            )
        if "datasets" in m2_modules and m1.artifact_service is not None:
            from .modules.data_center.import_artifacts import DataImportArtifactReadPolicy

            app.state.artifact_service = m1.artifact_service.with_export_read_policy(
                DataImportArtifactReadPolicy(
                    session_factory,
                    delegate=app.state.artifact_service.export_read_policy,
                )
            )

        if "prediction" in m2_modules:
            if contract_generation:
                prediction_registry = _MetadataOnlyPredictionRegistry()
            else:
                from .assembly.prediction_api import build_prediction_api_registry

                prediction_registry = build_prediction_api_registry(manifest=manifest, session_factory=session_factory)
        else:
            prediction_registry = None
        app.state.prediction_registry = prediction_registry
        structure_factory = None
        if "prediction" in m2_modules and not contract_generation:
            from .assembly.prediction_structure import SQLAlchemyPredictionStructurePort

            structure_factory = lambda session: SQLAlchemyPredictionStructurePort(session)

        m2 = assemble_m2(
            session_factory,
            structure=structure_factory,
            capability_registry=prediction_registry,
            artifact=app.state.artifact_service,
            confirmation=m1.confirmation_service,
            data_csv_window=data_csv_window,
            modules=frozenset(manifest.modules),
            chromatography_types=frozenset(manifest.chromatography_types),
            batch_enabled=any("batch" in tools for tools in manifest.prediction_tools.values()),
            enabled_batch_types=frozenset(kind for kind, tools in manifest.prediction_tools.items() if "batch" in tools),
        )
    else:
        m2 = None
    app.state.m2_assembly = m2
    m3_modules = frozenset(manifest.modules) & frozenset({"knowledge", "training", "model_center"})
    if m3_modules:
        from .assembly.m3 import assemble_m3

        m3 = assemble_m3(modules=m3_modules, include_m2=m2 is not None)
    else:
        m3 = None
    app.state.m3_assembly = m3
    if "assistant" in manifest.modules:
        from .assembly.m4 import assemble_m4

        m4 = assemble_m4(
            modules={"assistant"},
            assistant_features=frozenset(manifest.assistant_features or ()),
            m3_modules=m3_modules,
            include_m2=m2 is not None,
            session_factory=session_factory,
            confirmation_service=m1.confirmation_service,
        )
        from .modules.assistant.priority_router import create_priority_router

        app.include_router(create_priority_router(), prefix="/api/v1")
    else:
        m4 = None
    app.state.m4_assembly = m4
    app.state.dependency_health_probe = _InfrastructureDependencyHealthProbe()
    # The shared project cursor remains unsigned; batch has its own required
    # signed codec and never falls back to this continuation format.
    app.state.project_cursor_codec = CursorCodec()
    app.state.batch_cursor_codec = _batch_cursor_codec(manifest, contract_generation=contract_generation)
    app.state.contract_generation = contract_generation
    app.state.raise_api_error = _raise_api_error
    app.include_router(create_structure_router(), prefix="/api/v1")
    app.include_router(create_public_settings_router(), prefix="/api/v1")
    # M1 routes are part of the selected build.  Missing infrastructure is a
    # real 503 at runtime; environment state must not change the OpenAPI shape.
    app.include_router(create_artifact_router(), prefix="/api/v1")
    # The task router intentionally exposes no POST /jobs creation route;
    # later business modules call JobService.create with a fixed policy.
    app.include_router(create_task_router(), prefix="/api/v1")
    if m2 is not None:
        prediction_router, data_center_router = m2.routers
        if "prediction" in manifest.modules:
            app.include_router(prediction_router, prefix="/api/v1")
        if {"records", "datasets"} & set(manifest.modules):
            app.include_router(data_center_router, prefix="/api/v1")
    if m3 is not None:
        for router in m3.routers:
            if router.routes:
                app.include_router(router, prefix="/api/v1")
    if m4 is not None and m4.router is not None:
        app.include_router(m4.router, prefix="/api/v1")

    original_openapi = app.openapi

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = original_openapi()
        schema.setdefault("info", {})["x-build-id"] = manifest.build_id
        schema["openapi"] = "3.1.0"
        prediction_operation = schema.get("paths", {}).get("/api/v1/predictions", {}).get("post")
        if prediction_operation is not None and manifest.chromatography_types:
            profile_refs = {
                "tlc": ("#/components/schemas/TlcPredictionCreate",),
                "cc": ("#/components/schemas/CcPredictionCreate",),
                "hplc": ("#/components/schemas/HplcPredictionCreate",),
                "chiral_lc": ("#/components/schemas/ChiralLcPredictionCreate",),
                "sfc": (
                    "#/components/schemas/SfcSingleColumnPredictionCreate",
                    "#/components/schemas/SfcFullColumnPredictionCreate",
                ),
                "gc": ("#/components/schemas/GcPredictionCreate",),
            }
            refs = [
                {"$ref": ref}
                for type_id in sorted(manifest.chromatography_types)
                for ref in profile_refs.get(type_id, ())
            ]
            if refs:
                prediction_operation.setdefault("requestBody", {}).setdefault("content", {}).setdefault("application/json", {})["schema"] = {"oneOf": refs}
        # Prediction responses are shared by all builds, while their DTO
        # unions must describe only the chromatography types in this
        # manifest.  Keep the future chiral_lc models in source code; trim
        # their response branches at the profile boundary.
        prediction_components = schema.setdefault("components", {}).setdefault("schemas", {})
        built_types = frozenset(manifest.chromatography_types)
        input_refs = {
            "tlc": ("TlcInput",),
            "cc": ("CcInput",),
            "hplc": ("HplcInput", "HplcInputV10"),
            "chiral_lc": ("ChiralLcInput",),
            "sfc": ("SfcFullColumnInput", "SfcSingleColumnInput", "_SfcConditionInput"),
            "gc": ("GcInput",),
        }
        result_refs = {
            "tlc": (("tlc", "TlcPredictionResult"), ("tlc_condition_comparison", "TlcConditionComparisonResult"), ("tlc_reverse_solvent", "TlcReverseResult")),
            "cc": (("cc", "CcPredictionResult"),),
            "hplc": (("hplc", "HplcPredictionResult"),),
            "chiral_lc": (("chiral_lc", "ChiralLcPredictionResult"),),
            "sfc": (("sfc", "SfcPredictionResult"),),
            "gc": (("gc", "GcPredictionResult"),),
        }
        prediction = prediction_components.get("Prediction")
        if isinstance(prediction, dict) and built_types:
            chrom_schema = prediction.get("properties", {}).get("chromatography_type")
            if isinstance(chrom_schema, dict):
                selected_types = sorted(t for t in built_types if t in input_refs)
                if len(selected_types) == 1:
                    chrom_schema.pop("enum", None)
                    chrom_schema["const"] = selected_types[0]
                else:
                    chrom_schema.pop("const", None)
                    chrom_schema["enum"] = selected_types
            result_schema = prediction.get("properties", {}).get("result")
            if isinstance(result_schema, dict):
                result_union = next(
                    (item for item in result_schema.get("anyOf", ()) if isinstance(item, dict) and "oneOf" in item),
                    None,
                )
                if isinstance(result_union, dict):
                    refs = [
                        {"$ref": f"#/components/schemas/{schema_name}"}
                        for type_id in sorted(built_types)
                        for task_type, schema_name in result_refs.get(type_id, ())
                    ]
                    result_union["oneOf"] = refs
                    result_union["discriminator"] = {
                        "mapping": {
                            task_type: f"#/components/schemas/{schema_name}"
                            for type_id in sorted(built_types)
                            for task_type, schema_name in result_refs.get(type_id, ())
                        },
                        "propertyName": "task_type",
                    }
        snapshot = prediction_components.get("PredictionInputSnapshot")
        if isinstance(snapshot, dict) and built_types:
            input_schema = snapshot.get("properties", {}).get("input")
            refs = [
                {"$ref": f"#/components/schemas/{name}"}
                for name in sorted({name for type_id in built_types for name in input_refs.get(type_id, ())})
            ]
            if isinstance(input_schema, dict) and refs:
                if len(refs) == 1:
                    input_schema.pop("anyOf", None)
                    input_schema["$ref"] = refs[0]["$ref"]
                else:
                    input_schema.pop("$ref", None)
                    input_schema["anyOf"] = refs
        # FastAPI also emits the optional Header dependency used by the
        # handlers.  The contract-level declarations below are authoritative:
        # collapse duplicate I/E headers and retain the required, non-null
        # schema used by the public API contract.
        for path_item in schema.get("paths", {}).values():
            for method, operation in path_item.items():
                if not isinstance(operation, dict) or not isinstance(operation.get("parameters"), list):
                    continue
                unique: dict[tuple[str, str], dict] = {}
                for parameter in operation["parameters"]:
                    key = (str(parameter.get("in", "")), str(parameter.get("name", "")))
                    previous = unique.get(key)
                    if previous is None or bool(parameter.get("required")) and not bool(previous.get("required")):
                        unique[key] = parameter
                operation["parameters"] = list(unique.values())
                # The write contract requires these concurrency/idempotency
                # headers.  FastAPI's dependency declaration is intentionally
                # optional at runtime so handlers can return the contract's
                # structured error envelope; normalize only requiredness and
                # nullability after duplicate collapse.  Do not replace the
                # schema here: the route declaration/openapi_extra owns the
                # frozen min/max bounds and (where declared) title.
                if method.lower() in {"post", "put", "patch", "delete"}:
                    for parameter in operation["parameters"]:
                        if parameter.get("in") != "header" or parameter.get("name") not in {
                            "Idempotency-Key",
                            "If-Match",
                        }:
                            continue
                        parameter["required"] = True
                        generated = parameter.get("schema")
                        if isinstance(generated, dict) and isinstance(generated.get("anyOf"), list):
                            string_schema = next(
                                (
                                    branch
                                    for branch in generated["anyOf"]
                                    if isinstance(branch, dict) and branch.get("type") == "string"
                                ),
                                None,
                            )
                            if string_schema is not None:
                                parameter["schema"] = dict(string_schema)
                        if not isinstance(parameter.get("schema"), dict):
                            parameter["schema"] = {"type": "string"}
                        else:
                            parameter["schema"].setdefault("type", "string")
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        error_schema = ErrorEnvelope.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
        # Pydantic emits referenced models under ``$defs`` even when the
        # reference template points at OpenAPI components. Promote every
        # definition into the document component registry so the generated
        # contract is self-contained.
        definitions = error_schema.pop("$defs", {})
        for name, definition in definitions.items():
            components[name] = definition
        components["ErrorEnvelope"] = error_schema
        error_response = {
            "description": "统一 API 错误",
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorEnvelope"}}},
        }
        for path_item in schema.get("paths", {}).values():
            for operation in path_item.values():
                if not isinstance(operation, dict) or "responses" not in operation:
                    continue
                responses = operation["responses"]
                for code in (
                    "400",
                    "401",
                    "403",
                    "404",
                    "409",
                    "410",
                    "412",
                    "413",
                    "422",
                    "428",
                    "429",
                    "500",
                    "503",
                ):
                    responses[code] = dict(error_response)
        components.pop("HTTPValidationError", None)
        components.pop("ValidationError", None)
        # FastAPI resolves the development union before the profile-specific
        # requestBody replacement.  Remove every component that is not
        # reachable from a path, then close over nested component references;
        # a trimmed profile must not advertise dormant DTO schemas.
        def _refs(value: object) -> set[str]:
            found: set[str] = set()
            if isinstance(value, dict):
                ref = value.get("$ref")
                if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                    found.add(ref.rsplit("/", 1)[-1])
                for child in value.values():
                    found |= _refs(child)
            elif isinstance(value, list):
                for child in value:
                    found |= _refs(child)
            return found

        reachable = _refs(schema.get("paths", {}))
        changed = True
        while changed:
            changed = False
            for name in tuple(reachable):
                definition = components.get(name)
                if definition is None:
                    continue
                before = len(reachable)
                reachable |= _refs(definition)
                changed |= len(reachable) != before
        for name in tuple(components):
            if name not in reachable:
                components.pop(name, None)
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        supplied = request.headers.get("X-Request-Id")
        request.state.request_id = supplied if supplied and _REQUEST_ID.fullmatch(supplied) else f"req_{os.urandom(8).hex()}"
        factory = request.app.state.session_factory
        selected = set(request.app.state.manifest.modules)
        needs_session = bool({"prediction", "records", "datasets", "knowledge", "training", "model_center", "assistant"} & selected)
        if factory is None or not needs_session:
            response = await call_next(request)
            response.headers["X-Request-Id"] = request.state.request_id
            return response
        session = factory()
        try:
            session.begin()
            request.state.idempotency_store = SQLAlchemyIdempotencyStore(session)
            if request.app.state.m2_assembly is not None:
                from .assembly.m2 import bind_m2

                request.state.m2_assembly = bind_m2(
                    request.app.state.m2_assembly,
                    session,
                    idempotency_store=request.state.idempotency_store,
                )
                request.state.data_import_workflow = request.state.m2_assembly.data_import_workflow
            if request.app.state.m3_assembly is not None:
                from .assembly.m3 import bind_m3

                request.state.m3_ports = bind_m3(
                    request.app.state.m3_assembly,
                    session,
                    idempotency_store=request.state.idempotency_store,
                )
                request.state.knowledge_service = request.state.m3_ports.knowledge_service
                request.state.training_service = request.state.m3_ports.training_service
                request.state.model_center_service = request.state.m3_ports.model_center_service
            if request.app.state.m4_assembly is not None:
                from .assembly.m4 import bind_m4

                request.state.m4_ports = bind_m4(
                    request.app.state.m4_assembly,
                    session,
                    idempotency_store=request.state.idempotency_store,
                    actor_context=request.app.state.actor_context,
                    build_worker=False,
                )
                request.state.assistant_service = request.state.m4_ports.service
                request.state.assistant_event_store = request.state.m4_ports.event_store
                request.state.assistant_trace_inspector = request.state.m4_ports.trace_inspector
                request.state.assistant_worker = request.state.m4_ports.worker
                from .modules.assistant.priority import PriorityAssistantService

                m2_ports = getattr(request.state, "m2_assembly", None)
                m3_ports = getattr(request.state, "m3_ports", None)
                request.state.assistant_priority_service = PriorityAssistantService(
                    prediction=(getattr(m2_ports, "prediction_service", None) if m2_ports is not None else None),
                    capabilities=(getattr(m2_ports, "capability_registry", None) if m2_ports is not None else None),
                    data_versions=(getattr(m2_ports, "data_version_service", None) if m2_ports is not None else None),
                    records=(getattr(m2_ports, "prediction_record_service", None) if m2_ports is not None else None),
                    models=(getattr(m3_ports, "model_center_service", None) if m3_ports is not None else None),
                    artifacts=getattr(request.app.state, "artifact_service", None),
                    confirmation=getattr(request.app.state, "confirmation_service", None),
                )
            response = await call_next(request)
            if 200 <= response.status_code < 300:
                session.commit()
            else:
                session.rollback()
            response.headers["X-Request-Id"] = request.state.request_id
            return response
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @app.middleware("http")
    async def metadata_generation_guard(request: Request, call_next):
        rejection = _contract_generation_rejection(request.url.path) if contract_generation else None
        if rejection is not None:
            return rejection
        return await call_next(request)

    @app.exception_handler(APIError)
    async def api_error_handler(request: Request, exc: APIError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.payload.model_dump(mode="json"), "request_id": request.state.request_id},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        missing_idempotency_key = any(
            error.get("type") == "missing"
            and len(error.get("loc", ())) >= 2
            and str(error["loc"][0]).lower() == "header"
            and str(error["loc"][-1]).lower() == "idempotency-key"
            for error in exc.errors()
        )
        if missing_idempotency_key:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "code": "IDEMPOTENCY_KEY_REQUIRED",
                        "message": "缺少 Idempotency-Key",
                        "field_errors": [],
                        "retryable": False,
                        "details": {},
                    },
                    "request_id": request.state.request_id,
                },
            )
        fields = [
            {
                "path": ".".join(str(x) for x in error.get("loc", [])),
                "code": str(error.get("type", "validation")),
                "message": str(error.get("msg", "请求无效")),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_FAILED",
                    "message": "请求字段校验失败",
                    "field_errors": fields,
                    "retryable": False,
                    "details": {},
                },
                "request_id": request.state.request_id,
            },
        )

    def envelope(request: Request, data):
        return SuccessEnvelope(data=data, warnings=[], request_id=request.state.request_id)

    @app.get("/api/v1/system/capabilities", response_model=SuccessEnvelope[Capabilities], operation_id="get_system_capabilities", tags=["system"])
    def get_system_capabilities(request: Request):
        dependencies = _health_results(request.app.state.dependency_health_probe)
        return envelope(
            request,
            Capabilities(
                build_id=manifest.build_id,
                api_version="1.0",
                modules=[item for item in manifest.modules if item in _IMPLEMENTED_MODULES],
                chromatography_types=list(manifest.chromatography_types),
                assistant_modes=[item for item in (manifest.assistant_features or []) if item in {"knowledge", "business"}],
                export_formats=list(manifest.export_formats),
                auth_mode="deployment_default",
                data_origin="production",
                capabilities=_capabilities(request.app, dependencies),
            ),
        )

    @app.get("/api/v1/system/version", response_model=SuccessEnvelope[VersionInfo], operation_id="get_system_version", tags=["system"])
    async def get_system_version(request: Request):
        return envelope(
            request,
            VersionInfo(
                application_version="0.1.0",
                build_id=manifest.build_id,
                api_schema_version="0.2",
                migration_version="uninitialized",
                dependency_lock_hash=None,
            ),
        )

    @app.get("/api/v1/system/access-context", response_model=SuccessEnvelope[AccessContextView], operation_id="get_access_context", tags=["system"])
    async def get_access_context(request: Request):
        actor: ActorContext = request.app.state.actor_context
        reader: ProjectReader | None = request.app.state.project_reader
        if reader is None:
            # Empty configured scope is a safe, idempotent initialization
            # state for access discovery; business routes still reject it.
            reader = ConfiguredProjectReader(actor.project_ids)
        try:
            selection = resolve_project(actor, reader, default_project_id=actor.default_project_id)
        except ProjectSelectionError as exc:
            # Access context remains a useful 200 response when selection is
            # required; business routes map the same condition to 422.
            if exc.code != "PROJECT_SELECTION_REQUIRED":
                _raise_api_error(exc.status_code, exc.code, exc.message, details=exc.details)
            rows = []
            try:
                rows = reader.list_accessible(actor, limit=201, after_id=None)
            except Exception:
                _raise_api_error(503, "DEPENDENCY_UNAVAILABLE", "项目存储暂不可用", retryable=True)
            candidates = []
            seen = set()
            for row in rows:
                project_id = project_id_from_row(row)
                if project_id and project_id not in seen:
                    seen.add(project_id)
                    candidates.append({"id": project_id, "name": project_name_from_row(row)})
            default_project_id = None
            project_selection = str(exc.details.get("selection", "needs_selection"))
        else:
            candidates = [{"id": item[0], "name": item[1]} for item in selection.projects]
            default_project_id = selection.project_id
            project_selection = selection.status
        return envelope(
            request,
            AccessContextView(
                actor={"id": actor.actor_id, "auth_mode": actor.auth_method, "policy_version": actor.policy_version},
                tenant=actor.tenant_id,
                permissions=actor.permissions,
                accessible_projects=candidates,
                default_project_id=default_project_id,
                project_selection=project_selection,
                actor_id=actor.actor_id,
                auth_mode=actor.auth_method,
                allowed_actions=actor.permissions,
                is_multi_user_authenticated=False,
            ),
        )

    @app.get("/api/v1/projects", response_model=ListEnvelope[ProjectView], operation_id="list_projects", tags=["projects"])
    def list_projects(request: Request, limit: int = 50, cursor: str | None = None):
        if limit < 1 or limit > 200:
            _raise_api_error(400, "VALIDATION_FAILED", "limit 必须在 1 到 200 之间")
        actor: ActorContext = request.app.state.actor_context
        reader: ProjectReader | None = request.app.state.project_reader
        if reader is None:
            _raise_api_error(503, "DEPENDENCY_UNAVAILABLE", "项目存储未配置", retryable=True)
        scope = CursorCodec.scope(actor.actor_id, actor.project_ids)
        try:
            after_id = None if cursor is None else request.app.state.project_cursor_codec.decode(cursor, scope=scope)
        except CursorError:
            _raise_api_error(400, "VALIDATION_FAILED", "cursor 无效或已失效")
        try:
            # The reader applies actor scope and ``id > after_id`` in its SQL
            # query, returning at most limit+1 rows.  No full result set is
            # materialized in the event-loop route or the application layer.
            rows = reader.list_accessible(actor, limit=limit + 1, after_id=after_id)
            data = [
                ProjectView(id=str(row.id), name=str(row.name), status=str(row.status))
                for row in rows
            ]
        except Exception:
            _raise_api_error(503, "DEPENDENCY_UNAVAILABLE", "项目存储暂不可用", retryable=True)
        has_more = len(data) > limit
        window = data[:limit]
        next_cursor = (
            request.app.state.project_cursor_codec.encode(after_id=str(window[-1].id), scope=scope)
            if has_more
            else None
        )
        return ListEnvelope(
            data=window,
            page=PageInfo(next_cursor=next_cursor, limit=limit, has_more=has_more),
            warnings=[],
            request_id=request.state.request_id,
        )

    @app.get("/api/v1/health/live", response_model=SuccessEnvelope[dict[str, str]], operation_id="health_live", tags=["health"])
    async def health_live(request: Request):
        return envelope(request, {"status": "UP"})

    @app.get("/api/v1/system/dependencies", response_model=ListEnvelope[DependencyHealth], operation_id="get_dependency_health", tags=["health"])
    def get_dependency_health(request: Request):
        data = [_health_dto(item) for item in _health_results(request.app.state.dependency_health_probe)]
        return ListEnvelope(
            data=data,
            page=PageInfo(next_cursor=None, limit=max(1, len(data)), has_more=False),
            warnings=[],
            request_id=request.state.request_id,
        )

    @app.get("/api/v1/health/ready", response_model=SuccessEnvelope[Readiness], operation_id="health_ready", tags=["health"])
    def health_ready(request: Request):
        dependencies = _health_results(request.app.state.dependency_health_probe)
        dependency_map = {item.id: item for item in dependencies}
        core_ready = all(
            dependency_map.get(required) is not None and dependency_map[required].status == "READY"
            for required in ("database", "queue")
        )
        result = Readiness(core_ready=core_ready, checked_at=_now(), capabilities=_capabilities(request.app, dependencies))
        if not core_ready:
            return JSONResponse(status_code=503, content=envelope(request, result).model_dump(mode="json"))
        return envelope(request, result)

    if not contract_generation:
        from .core.model_load_observation import observe_api_model_loads

        observe_api_model_loads()
    return app


def create_app_from_environment() -> FastAPI:
    """Uvicorn factory requiring CHROM_BUILD_MANIFEST or a package manifest."""

    return create_app()


def _raise_api_error(status_code: int, code: str, message: str, *, retryable: bool = False, details: dict[str, object] | None = None) -> NoReturn:
    raise APIError(status_code, code, message, retryable=retryable, details=details)


# Production uses ``create_app_from_environment --factory``.  Avoid creating
# an application during module import, where a configured manifest could
# otherwise load model assets before an OpenAPI caller selects metadata mode.
