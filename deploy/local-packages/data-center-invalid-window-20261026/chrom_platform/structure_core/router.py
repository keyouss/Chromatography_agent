from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Header, Request, Response, status
from fastapi.concurrency import run_in_threadpool

from chrom_platform.core.dto import ActorContext
from chrom_platform.core.errors import APIError
from chrom_platform.core.idempotency import BeginResult, IdempotencyRecord, IdempotencyScope
from chrom_platform.core.projects import ConfiguredProjectReader, ProjectSelectionError, resolve_project
from chrom_platform.infrastructure.models import AuditEvent

from .schemas import NormalizeStructureRequest, StructureEnvelope
from .service import InvalidStructure, StructureRepositoryUnavailable, StructureService


def _service(request: Request) -> StructureService:
    return request.app.state.structure_service


def _idempotency_key(value: str | None) -> str:
    if value is None:
        raise APIError(400, "IDEMPOTENCY_KEY_REQUIRED", "缺少 Idempotency-Key")
    if value == "" or len(value) > 128:
        raise APIError(422, "VALIDATION_FAILED", "Idempotency-Key 长度无效")
    return value


_IDEMPOTENCY_PARAMETER = {
    "in": "header", "name": "Idempotency-Key", "required": True,
    "schema": {"type": "string", "minLength": 1, "maxLength": 128, "title": "Idempotency-Key"},
}


def _replay(record: IdempotencyRecord, request_id: str) -> StructureEnvelope:
    if record.response is None:
        raise APIError(409, "STATE_CONFLICT", "幂等记录缺少响应")
    replay = StructureEnvelope.model_validate(record.response)
    replay.request_id = request_id
    return replay


def _normalize_in_transaction(
    *,
    service: StructureService,
    uow: object,
    payload: NormalizeStructureRequest,
    scope: IdempotencyScope,
    payload_hash: str,
    structure_id: str,
    request_id: str,
    agent_run_id: str | None = None,
    attempt: int | None = None,
) -> tuple[StructureEnvelope, bool]:
    """Run the complete synchronous unit of work on one worker thread."""

    try:
        transaction_factory = getattr(uow, "transaction", None)
        if not callable(transaction_factory):
            raise APIError(503, "DEPENDENCY_UNAVAILABLE", "结构事务单元未配置", retryable=True)
        with transaction_factory() as transaction:
            store = transaction.idempotency
            try:
                record: IdempotencyRecord | None = store.get(scope)
            except Exception:
                raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等存储暂不可用", retryable=True)
            if record is not None:
                if record.payload_hash != payload_hash:
                    raise APIError(409, "IDEMPOTENCY_KEY_REUSED", "幂等键已用于其他请求")
                if record.status == "IN_PROGRESS":
                    raise APIError(
                        409,
                        "OPERATION_IN_PROGRESS",
                        "相同幂等请求正在处理中",
                        retryable=True,
                    )
                if record.status == "COMPLETED":
                    return _replay(record, request_id), True

            try:
                reservation = store.begin(scope, payload_hash)
            except Exception:
                raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等存储暂不可用", retryable=True)
            if not isinstance(reservation, BeginResult):
                raise APIError(500, "STATE_CONFLICT", "幂等存储返回无效结果")
            if not reservation.acquired and reservation.record.payload_hash != payload_hash:
                raise APIError(409, "IDEMPOTENCY_KEY_REUSED", "幂等键已用于其他请求")
            if not reservation.acquired and reservation.record.status == "IN_PROGRESS":
                raise APIError(
                    409,
                    "OPERATION_IN_PROGRESS",
                    "相同幂等请求正在处理中",
                    retryable=True,
                )
            if not reservation.acquired and reservation.record.status == "FAILED":
                raise APIError(
                    503,
                    "OPERATION_RETRY_REQUIRED",
                    "上一次结构操作失败，可稍后重试",
                    retryable=True,
                )
            if not reservation.acquired:
                return _replay(reservation.record, request_id), True

            try:
                result = service.normalize(payload, structure_id, transaction.structures)
            except InvalidStructure as exc:
                raise APIError(422, "VALIDATION_FAILED", str(exc))
            except StructureRepositoryUnavailable as exc:
                raise APIError(503, "DEPENDENCY_UNAVAILABLE", str(exc), retryable=True)
            except Exception:
                raise APIError(
                    503,
                    "DEPENDENCY_UNAVAILABLE",
                    "结构持久化服务暂不可用",
                    retryable=True,
                )
            envelope = StructureEnvelope(
                data=result,
                warnings=result.stereochemistry_warnings,
                request_id=request_id,
            )
            audit = getattr(transaction, "audit", None)
            record_audit = getattr(audit, "record", None)
            if callable(record_audit):
                # The audit contains only server-owned references and hashes;
                # the submitted SMILES is intentionally absent.
                record_audit(AuditEvent(
                    id=f"audit_{hashlib.sha256((request_id + ':' + result.id + ':' + scope.actor_id + ':' + scope.project_id + ':' + scope.key).encode('utf-8')).hexdigest()[:32]}",
                    project_id=scope.project_id,
                    actor_id=scope.actor_id,
                    auth_method="actor_context",
                    interaction="agent_tool" if agent_run_id else "service",
                    request_id=request_id,
                    action="structure.normalize",
                    resource_type="structure",
                    resource_id=result.id,
                    resource_ref={"type": "structure", "id": result.id},
                    before_summary={},
                    after_summary={
                        "status": "SUCCEEDED",
                        "snapshot_hash": result.snapshot_hash,
                        "attempt": attempt,
                    },
                    agent_run_id=agent_run_id,
                    result="SUCCEEDED",
                ))
            try:
                store.complete(scope, payload_hash, status.HTTP_201_CREATED, envelope.model_dump(mode="json"))
            except Exception:
                raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等存储暂不可用", retryable=True)
            return envelope, False
    except APIError:
        raise
    except Exception:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "结构事务暂不可用", retryable=True)


def create_router() -> APIRouter:
    router = APIRouter(prefix="/structures", tags=["structures"])

    @router.post(
        "/normalize",
        response_model=StructureEnvelope,
        status_code=status.HTTP_201_CREATED,
        operation_id="normalize_structure",
        openapi_extra={"parameters": [_IDEMPOTENCY_PARAMETER]},
    )
    async def normalize_structure(
        payload: NormalizeStructureRequest,
        request: Request,
        response: Response,
        idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    ) -> StructureEnvelope:
        idempotency_key = _idempotency_key(idempotency_key)
        service = _service(request)
        actor: ActorContext = request.app.state.actor_context
        reader = getattr(request.app.state, "project_reader", None)
        if reader is None and actor.project_ids:
            reader = ConfiguredProjectReader(actor.project_ids)
        if reader is None:
            raise APIError(503, "DEPENDENCY_UNAVAILABLE", "项目存储未配置", retryable=True)
        try:
            project = resolve_project(actor, reader, payload.project_id, default_project_id=actor.default_project_id)
        except ProjectSelectionError as exc:
            raise APIError(exc.status_code, exc.code, exc.message, details=exc.details) from None
        if not actor.can("prediction:create"):
            raise APIError(403, "PERMISSION_DENIED", "缺少 prediction:create 权限")
        payload = payload.model_copy(update={"project_id": project.project_id})
        if service is None:
            request.app.state.raise_api_error(503, "DEPENDENCY_UNAVAILABLE", "结构持久化存储未配置", retryable=True)
        uow = getattr(request.app.state, "structure_uow", None)
        if uow is None:
            request.app.state.raise_api_error(
                503,
                "DEPENDENCY_UNAVAILABLE",
                "结构事务单元未配置",
                retryable=True,
            )

        payload_hash = hashlib.sha256(
            json.dumps(
                {"path": "/structures/normalize", "payload": payload.model_dump(mode="json")},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        scope = IdempotencyScope(
            actor_id=actor.actor_id,
            project_id=payload.project_id,
            method="POST",
            route_identity="/api/v1/structures/normalize",
            key=idempotency_key,
        )
        structure_id = "str_" + hashlib.sha256(
            f"{actor.actor_id}|{payload.project_id}|{scope.key}|{payload_hash}".encode("utf-8")
        ).hexdigest()[:32]
        result, replayed = await run_in_threadpool(
            _normalize_in_transaction,
            service=service,
            uow=uow,
            payload=payload,
            scope=scope,
            payload_hash=payload_hash,
            structure_id=structure_id,
            request_id=request.state.request_id,
        )
        if replayed:
            response.headers["Idempotency-Replayed"] = "true"
        return result

    return router
