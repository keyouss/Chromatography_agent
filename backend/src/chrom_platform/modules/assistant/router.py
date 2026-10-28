"""HTTP adapter for the Assistant domain (no SSE/runtime routes)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from chrom_platform.core.dto import ListEnvelope, PageInfo, SuccessEnvelope
from chrom_platform.core.errors import APIError
from chrom_platform.task_core.service import OperationContext

from .errors import AssistantError, public_error, public_event_data, unavailable
from .events import MySQLAgentEventStore, events_page
from .sse import create_sse_response
from .schemas import (
    AgentRun, CancelAgentRun, Conversation, ConversationArchive, ConversationDelete, ConversationDeleted,
    ContextDiff, ContextSnapshotCompareRequest, ContextSnapshotContent,
    ContextSnapshotMetadata, ContextSnapshotViewDTO, ConversationCreate, ConversationPatch,
    EventPage, LlmCallDetail, LlmCallSummary, Message, MessageAccepted, ResumeAgentRunPayload, ToolCall,
    UserMessageCreate,
)
from .service import AssistantService


def _service(request: Request, configured: AssistantService | None) -> AssistantService:
    value = configured or getattr(request.state, "assistant_service", None) or getattr(request.app.state, "assistant_service", None)
    if value is None:
        raise unavailable("Assistant service 未装配")
    return value


def _ctx(request: Request) -> OperationContext:
    actor = getattr(request.app.state, "actor_context", None)
    if actor is None:
        raise unavailable("ActorContext 未装配")
    return OperationContext(actor, interaction="human", request_id=getattr(request.state, "request_id", "service-request"))


def _raise(exc: AssistantError) -> None:
    raise exc


def _envelope(request: Request, data):
    return SuccessEnvelope(data=data, warnings=[], request_id=getattr(request.state, "request_id", "service-request"))


def _set_etag(response: Response, data: object) -> None:
    etag = getattr(data, "etag", None)
    if etag:
        response.headers["ETag"] = etag


def _set_replay_header(response: Response, service: AssistantService) -> None:
    if service.idempotency_replayed:
        response.headers["Idempotency-Replayed"] = "true"


def _list_response(request: Request, values: list, *, limit: int, cursor_key, scope: str, filters: dict[str, object]):
    from .service import encode_cursor
    has_more = len(values) > limit
    data = values[:limit]
    next_cursor = encode_cursor(scope, cursor_key(data[-1]), filters=filters) if has_more and data else None
    return ListEnvelope(data=data, page=PageInfo(next_cursor=next_cursor, limit=limit, has_more=has_more), warnings=[], request_id=getattr(request.state, "request_id", "service-request"))


def _trace_metadata(raw: dict) -> ContextSnapshotMetadata:
    from datetime import datetime
    raw = {key: raw[key] for key in ("id", "llm_call_id", "project_id", "view", "capture_state", "capture_mode", "sha256", "truncated", "created_at", "expires_at") if key in raw}
    for key in ("created_at", "expires_at"):
        if isinstance(raw.get(key), str):
            raw[key] = datetime.fromisoformat(raw[key].replace("Z", "+00:00"))
    return ContextSnapshotMetadata.model_validate(raw)


def _trace_content(raw: dict, *, view: bool = False):
    redactions = raw.get("redactions") or {}
    if isinstance(redactions, dict):
        redactions = [{"category": key, "count": value} for key, value in redactions.items() if key in {"secret", "hidden"}]
    sources = []
    for source in raw.get("sources") or []:
        value = dict(source)
        value.setdefault("json_pointer", "/")
        value.setdefault("kind", "runtime_note")
        sources.append({key: value[key] for key in ("json_pointer", "kind", "source_version", "resource_ref") if key in value})
    allowed = {key: raw[key] for key in ("id", "project_id", "view", "payload_schema_version", "payload", "truncated", "hash_scope", "sha256") if key in raw}
    allowed.update({"sources": sources, "redactions": redactions})
    if view:
        allowed.update({key: raw[key] for key in ("llm_call_id", "capture_state", "capture_mode", "created_at", "expires_at") if key in raw})
    value = allowed
    from datetime import datetime
    for key in ("created_at", "expires_at"):
        if isinstance(value.get(key), str):
            value[key] = datetime.fromisoformat(value[key].replace("Z", "+00:00"))
    return (ContextSnapshotViewDTO if view else ContextSnapshotContent).model_validate(value)


def _trace_detail(raw: dict) -> LlmCallDetail:
    from datetime import datetime
    value = dict(raw)
    value.setdefault("usage", {})
    value.setdefault("estimates", {})
    value.setdefault("model_parameters_summary", {})
    if value.get("response_summary") is not None:
        value["response_summary"] = {k: value["response_summary"].get(k) for k in ("text_excerpt", "tool_names", "finish_reason")}
    value["model_parameters_summary"] = {k: value["model_parameters_summary"].get(k) for k in ("temperature", "top_p", "max_output_tokens", "stream")}
    value["estimates"] = {k: value["estimates"].get(k) for k in ("input_tokens", "method", "estimator_version")}
    if value.get("error") is not None:
        value["error"] = public_error(value["error"])
    for key in ("created_at", "finished_at"):
        if isinstance(value.get(key), str):
            value[key] = datetime.fromisoformat(value[key].replace("Z", "+00:00"))
    return LlmCallDetail.model_validate(value)


def _scope_snapshot(request: Request, service: AssistantService, inspector: object, snapshot_id: str, *, permission: str) -> dict[str, str]:
    resolver = getattr(inspector, "snapshot_scope", None)
    if not callable(resolver):
        raise unavailable("Context snapshot scope 端口未配置")
    scope = resolver(snapshot_id, actor=request.app.state.actor_context, permission=permission)
    checker = getattr(service, "scope_run", None) or getattr(service, "get_run", None)
    if not callable(checker):
        raise unavailable("Assistant project scope 端口未配置")
    checker(_ctx(request), scope["agent_run_id"])
    return scope


def _trace_summary(raw: dict) -> LlmCallSummary:
    from datetime import datetime
    value = {key: raw[key] for key in ("id", "agent_run_id", "request_index", "transport_attempt", "status", "model_id", "usage", "latency_ms", "created_at") if key in raw}
    value.setdefault("usage", {})
    if isinstance(value.get("created_at"), str):
        value["created_at"] = datetime.fromisoformat(value["created_at"].replace("Z", "+00:00"))
    return LlmCallSummary.model_validate(value)


def _event_page(raw: dict) -> EventPage:
    from datetime import datetime
    value = dict(raw)
    events = []
    for event in value.get("events", []):
        item = dict(event)
        if item.get("data") is not None:
            item["data"] = public_event_data(item["data"], event_type=item.get("type"))
        if isinstance(item.get("timestamp"), str):
            item["timestamp"] = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00"))
        events.append(item)
    value["events"] = events
    return EventPage.model_validate(value)


def _require_key(value: str | None) -> str:
    if value is None:
        raise APIError(400, "IDEMPOTENCY_KEY_REQUIRED", "缺少 Idempotency-Key")
    if value == "" or len(value) > 128:
        raise APIError(422, "VALIDATION_FAILED", "Idempotency-Key 长度无效")
    return value


def _require_if_match(value: str | None) -> str:
    if value is None:
        raise APIError(428, "IF_MATCH_REQUIRED", "缺少 If-Match")
    if value == "" or len(value) > 256:
        raise APIError(422, "VALIDATION_FAILED", "If-Match 长度无效")
    return value


_IDEMPOTENCY_PARAMETER = {
    "in": "header", "name": "Idempotency-Key", "required": True,
    "schema": {"type": "string", "minLength": 1, "maxLength": 128},
}
_IF_MATCH_PARAMETER = {
    "in": "header", "name": "If-Match", "required": True,
    "schema": {"type": "string", "minLength": 1, "maxLength": 256},
}
_ASYNC_RESPONSE = {
    "202": {
        "description": "任务已接受",
        "headers": {"Location": {"required": True, "schema": {"type": "string", "format": "uri-reference"}}},
    }
}


def create_router(*, assistant_service: AssistantService | None = None, trace_enabled: bool = False) -> APIRouter:
    router = APIRouter(tags=["assistant"])

    def service_dependency(request: Request) -> AssistantService:
        return _service(request, assistant_service)

    def event_store(request: Request):
        value = getattr(request.state, "assistant_event_store", None) or getattr(request.app.state, "assistant_event_store", None)
        if value is None:
            raise unavailable("Assistant event store 未装配")
        return value

    def trace_inspector(request: Request):
        value = getattr(request.state, "assistant_trace_inspector", None) or getattr(request.app.state, "assistant_trace_inspector", None)
        if value is None:
            raise unavailable("Context Inspector 未装配")
        return value

    @router.get("/conversations", response_model=ListEnvelope[Conversation], operation_id="list_conversations")
    def list_conversations(request: Request, project_id: str | None = Query(default=None), q: str | None = Query(default=None, max_length=200), archived: bool | None = None, cursor: str | None = Query(default=None, max_length=512), limit: int = Query(default=50, ge=1, le=200), service: AssistantService = Depends(service_dependency)):
        values = service.list_conversations(_ctx(request), project_id=project_id, q=q, archived=archived, cursor=cursor, limit=limit + 1)
        return _list_response(request, values, limit=limit, scope="conversations", filters={"project_id": project_id, "q": q, "archived": archived}, cursor_key=lambda v: (v.updated_at.isoformat(), v.id))

    @router.post("/conversations", response_model=SuccessEnvelope[Conversation], status_code=status.HTTP_201_CREATED, operation_id="create_conversation", openapi_extra={"parameters": [_IDEMPOTENCY_PARAMETER]})
    def create_conversation(payload: ConversationCreate, request: Request, response: Response, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
        _require_key(idempotency_key)
        service = _service(request, assistant_service)
        value = service.create_conversation(_ctx(request), payload, idempotency_key=idempotency_key)
        _set_replay_header(response, service)
        response.headers["Location"] = f"/api/v1/conversations/{value.id}"
        _set_etag(response, value)
        return _envelope(request, value)

    @router.get("/conversations/{conversation_id}", response_model=SuccessEnvelope[Conversation], operation_id="get_conversation")
    def get_conversation(conversation_id: str, request: Request, response: Response, service: AssistantService = Depends(service_dependency)):
        value = service.get_conversation(_ctx(request), conversation_id)
        _set_etag(response, value)
        return _envelope(request, value)

    @router.patch("/conversations/{conversation_id}", response_model=SuccessEnvelope[Conversation], operation_id="patch_conversation", openapi_extra={"parameters": [_IF_MATCH_PARAMETER, _IDEMPOTENCY_PARAMETER]})
    def patch_conversation(conversation_id: str, payload: ConversationPatch, request: Request, response: Response, if_match: str | None = Header(default=None, alias="If-Match"), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
        _require_key(idempotency_key)
        match = _require_if_match(if_match)
        service = _service(request, assistant_service)
        value = service.patch_conversation(_ctx(request), conversation_id, payload, expected_etag=match, idempotency_key=idempotency_key)
        _set_replay_header(response, service)
        _set_etag(response, value)
        return _envelope(request, value)

    @router.post("/conversations/{conversation_id}/archive", response_model=SuccessEnvelope[Conversation], operation_id="archive_conversation", openapi_extra={"parameters": [_IDEMPOTENCY_PARAMETER]})
    def archive_conversation(conversation_id: str, payload: ConversationArchive, request: Request, response: Response, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
        _require_key(idempotency_key)
        service = _service(request, assistant_service)
        value = service.archive_conversation(_ctx(request), conversation_id, payload, idempotency_key=idempotency_key)
        _set_replay_header(response, service)
        _set_etag(response, value)
        return _envelope(request, value)

    @router.delete("/conversations/{conversation_id}", response_model=SuccessEnvelope[ConversationDeleted], operation_id="delete_conversation", openapi_extra={"parameters": [_IF_MATCH_PARAMETER, _IDEMPOTENCY_PARAMETER]})
    def delete_conversation(conversation_id: str, payload: ConversationDelete, request: Request, response: Response, if_match: str | None = Header(default=None, alias="If-Match"), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
        key = _require_key(idempotency_key)
        match = _require_if_match(if_match)
        service = _service(request, assistant_service)
        value = service.delete_conversation(_ctx(request), conversation_id, payload, expected_etag=match, idempotency_key=key)
        _set_replay_header(response, service)
        return _envelope(request, value)

    @router.get("/conversations/{conversation_id}/messages", response_model=ListEnvelope[Message], operation_id="list_conversation_messages")
    def list_messages(conversation_id: str, request: Request, limit: int = Query(default=50, ge=1, le=200), before: str | None = Query(default=None, pattern=r"^[0-9]+$"), cursor: str | None = Query(default=None, max_length=512), service: AssistantService = Depends(service_dependency)):
        data = service.messages(_ctx(request), conversation_id, limit=limit + 1, before=before, cursor=cursor)
        has_more = len(data) > limit
        page_data = data[-limit:] if has_more else data
        next_cursor = None
        if has_more and page_data:
            from .service import encode_cursor
            next_cursor = encode_cursor("messages", int(page_data[0].seq), filters={"conversation_id": conversation_id})
        return ListEnvelope(data=page_data, page=PageInfo(next_cursor=next_cursor, limit=limit, has_more=has_more), warnings=[], request_id=getattr(request.state, "request_id", "service-request"))

    @router.post("/conversations/{conversation_id}/messages", response_model=SuccessEnvelope[MessageAccepted], status_code=status.HTTP_202_ACCEPTED, operation_id="create_conversation_message", openapi_extra={"parameters": [_IDEMPOTENCY_PARAMETER], "responses": _ASYNC_RESPONSE})
    def send_message(conversation_id: str, payload: UserMessageCreate, request: Request, response: Response, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
        key = _require_key(idempotency_key)
        service = _service(request, assistant_service)
        accepted = service.send_message(_ctx(request), conversation_id, payload, idempotency_key=key)
        _set_replay_header(response, service)
        response.headers["Location"] = f"/api/v1/agent-runs/{accepted.agent_run_id}"
        return _envelope(request, accepted)

    @router.get("/conversations/{conversation_id}/runs", response_model=ListEnvelope[AgentRun], operation_id="list_conversation_runs")
    def list_runs(conversation_id: str, request: Request, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = Query(default=None, max_length=512), service: AssistantService = Depends(service_dependency)):
        data = service.runs(_ctx(request), conversation_id, limit=limit + 1, cursor=cursor)
        return _list_response(request, data, limit=limit, scope="runs", filters={"conversation_id": conversation_id}, cursor_key=lambda v: (v.created_at.isoformat(), v.id))

    @router.get("/conversations/{conversation_id}/tool-calls", response_model=ListEnvelope[ToolCall], operation_id="list_conversation_tool_calls")
    def list_conversation_tools(conversation_id: str, request: Request, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = Query(default=None, max_length=512), service: AssistantService = Depends(service_dependency)):
        service.get_conversation(_ctx(request), conversation_id)
        data = service.tool_calls(_ctx(request), conversation_id=conversation_id, limit=limit + 1, cursor=cursor)
        return _list_response(request, data, limit=limit, scope="tool_calls", filters={"run_id": None, "conversation_id": conversation_id}, cursor_key=lambda v: ((v.started_at.isoformat() if v.started_at else ""), v.id))

    @router.get("/agent-runs/{run_id}", response_model=SuccessEnvelope[AgentRun], operation_id="get_agent_run")
    def get_run(run_id: str, request: Request, response: Response, service: AssistantService = Depends(service_dependency)):
        value = service.get_run(_ctx(request), run_id)
        _set_etag(response, value)
        return _envelope(request, value)

    @router.post("/agent-runs/{run_id}/cancel", response_model=SuccessEnvelope[AgentRun], status_code=status.HTTP_202_ACCEPTED, operation_id="cancel_agent_run", openapi_extra={"parameters": [_IDEMPOTENCY_PARAMETER, _IF_MATCH_PARAMETER], "responses": _ASYNC_RESPONSE})
    def cancel_run(run_id: str, payload: CancelAgentRun, request: Request, response: Response, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), if_match: str | None = Header(default=None, alias="If-Match")):
        _require_key(idempotency_key); match = _require_if_match(if_match)
        service = _service(request, assistant_service)
        response.headers["Location"] = f"/api/v1/agent-runs/{run_id}"
        value = service.cancel_run(_ctx(request), run_id, payload, idempotency_key=idempotency_key, expected_etag=match)
        _set_replay_header(response, service)
        _set_etag(response, value)
        return _envelope(request, value)

    @router.post("/agent-runs/{run_id}/resume", response_model=SuccessEnvelope[AgentRun], status_code=status.HTTP_202_ACCEPTED, operation_id="resume_agent_run", openapi_extra={"parameters": [_IDEMPOTENCY_PARAMETER, _IF_MATCH_PARAMETER], "responses": _ASYNC_RESPONSE})
    def resume_run(run_id: str, payload: ResumeAgentRunPayload, request: Request, response: Response, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), if_match: str | None = Header(default=None, alias="If-Match")):
        _require_key(idempotency_key); match = _require_if_match(if_match)
        service = _service(request, assistant_service)
        response.headers["Location"] = f"/api/v1/agent-runs/{run_id}"
        value = service.resume_run(_ctx(request), run_id, payload, idempotency_key=idempotency_key, expected_etag=match)
        _set_replay_header(response, service)
        _set_etag(response, value)
        return _envelope(request, value)

    @router.get("/agent-runs/{run_id}/tool-calls", response_model=ListEnvelope[ToolCall], operation_id="list_agent_run_tool_calls")
    def list_run_tools(run_id: str, request: Request, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = Query(default=None, max_length=512), service: AssistantService = Depends(service_dependency)):
        data = service.tool_calls(_ctx(request), run_id=run_id, limit=limit + 1, cursor=cursor)
        return _list_response(request, data, limit=limit, scope="tool_calls", filters={"run_id": run_id, "conversation_id": None}, cursor_key=lambda v: ((v.started_at.isoformat() if v.started_at else ""), v.id))

    @router.get("/agent-runs/{run_id}/events", response_model=SuccessEnvelope[EventPage], response_model_exclude_none=True, operation_id="list_agent_run_events")
    def list_events(run_id: str, request: Request, after_seq: str | None = Query(None, pattern=r"^[0-9]+$"), limit: int = Query(default=100, ge=1, le=500), service: AssistantService = Depends(service_dependency)):
        run = service.get_run(_ctx(request), run_id)
        cursor = int(after_seq or "0")
        return _envelope(request, _event_page(events_page(event_store(request), "agent_run", run_id, after_seq=cursor, limit=limit, authoritative_status=getattr(run, "status", None), recovery_url=f"/api/v1/agent-runs/{run_id}/events")))

    @router.get(
        "/agent-runs/{run_id}/events/stream",
        response_class=StreamingResponse,
        operation_id="stream_agent_run_events",
        responses={200: {"description": "Server-sent event stream", "content": {"text/event-stream": {"schema": {"type": "string"}}}}},
    )
    def stream_events(run_id: str, request: Request, after_seq: str | None = Query(None, pattern=r"^[0-9]+$"), last_event_id: str | None = Header(None, alias="Last-Event-ID"), limit: int = Query(default=100, ge=1, le=500), service: AssistantService = Depends(service_dependency)):
        service.get_run(_ctx(request), run_id)
        store = event_store(request)
        store_factory = None
        session_factory = getattr(request.app.state, "session_factory", None)
        if isinstance(store, MySQLAgentEventStore) and callable(session_factory):
            def store_factory():
                return MySQLAgentEventStore(
                    session_factory(),
                    store.row_model,
                    clock=store.clock,
                    retention_days=store.retention_days,
                )
        return create_sse_response(store, "agent_run", run_id, after_seq=after_seq, last_event_id=last_event_id, limit=limit, recovery_url=f"/api/v1/agent-runs/{run_id}/events", store_factory=store_factory)

    if trace_enabled:
        @router.get("/agent-runs/{run_id}/llm-calls", response_model=ListEnvelope[LlmCallSummary], operation_id="list_agent_run_llm_calls")
        def list_llm_calls(run_id: str, request: Request, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = Query(default=None, max_length=512), service: AssistantService = Depends(service_dependency)):
            if not request.app.state.actor_context.can("trace:read"):
                raise AssistantError(403, "PERMISSION_DENIED", "缺少 trace:read 权限")
            (getattr(service, "scope_run", None) or service.get_run)(_ctx(request), run_id)
            inspector = trace_inspector(request)
            listed = getattr(inspector, "list_llm_calls", None)
            if callable(listed):
                raw_values = listed(run_id, actor=request.app.state.actor_context, limit=limit + 1, cursor=cursor)
                values = [_trace_summary(value) for value in raw_values]
                return _list_response(request, values, limit=limit, scope="llm_calls", filters={"run_id": run_id}, cursor_key=lambda v: (v.created_at.isoformat(), v.id))
            getter = getattr(inspector.store, "list_llm_calls", None)
            if not callable(getter):
                raise unavailable("LLM 调用列表存储端口未配置")
            try:
                raw_values = getter(run_id=run_id, limit=limit + 1, cursor=cursor)
            except TypeError:
                raw_values = getter(run_id=run_id, limit=limit + 1)
            values = [_trace_summary(value.as_dict() if hasattr(value, "as_dict") else value) for value in raw_values]
            return _list_response(request, values, limit=limit, scope="llm_calls", filters={"run_id": run_id}, cursor_key=lambda v: (v.created_at.isoformat(), v.id))

        @router.get("/llm-calls/{call_id}", response_model=SuccessEnvelope[LlmCallDetail], operation_id="get_llm_call")
        def get_llm_call(call_id: str, request: Request, service: AssistantService = Depends(service_dependency)):
            raw = trace_inspector(request).llm_call(call_id, actor=request.app.state.actor_context)
            scope = getattr(service, "scope_run", None) or service.get_run
            scope(_ctx(request), raw["agent_run_id"])
            return _envelope(request, _trace_detail(raw))

        @router.get("/llm-calls/{call_id}/context", response_model=SuccessEnvelope[ContextSnapshotViewDTO], operation_id="get_llm_call_context")
        def get_llm_call_context(call_id: str, request: Request, service: AssistantService = Depends(service_dependency)):
            inspector = trace_inspector(request)
            detail = inspector.llm_call(call_id, actor=request.app.state.actor_context, permission="trace:content:read")
            scope = getattr(service, "scope_run", None) or service.get_run
            scope(_ctx(request), detail["agent_run_id"])
            snapshot_id = detail.get("logical_context_snapshot_id") or detail.get("provider_request_snapshot_id")
            if not snapshot_id:
                raise AssistantError(404, "RESOURCE_NOT_FOUND", "Context 快照不存在")
            return _envelope(request, _trace_content(inspector.content(snapshot_id, actor=request.app.state.actor_context), view=True))

        @router.get("/context-snapshots/{snapshot_id}", response_model=SuccessEnvelope[ContextSnapshotMetadata], operation_id="get_context_snapshot_metadata")
        def get_context_snapshot(snapshot_id: str, request: Request, service: AssistantService = Depends(service_dependency)):
            inspector = trace_inspector(request)
            _scope_snapshot(request, service, inspector, snapshot_id, permission="trace:read")
            return _envelope(request, _trace_metadata(inspector.metadata(snapshot_id, actor=request.app.state.actor_context)))

        @router.get("/context-snapshots/{snapshot_id}/content", response_model=SuccessEnvelope[ContextSnapshotContent], operation_id="get_context_snapshot_content")
        def get_context_snapshot_content(snapshot_id: str, request: Request, service: AssistantService = Depends(service_dependency)):
            inspector = trace_inspector(request)
            _scope_snapshot(request, service, inspector, snapshot_id, permission="trace:content:read")
            return _envelope(request, _trace_content(inspector.content(snapshot_id, actor=request.app.state.actor_context)))

        @router.post("/context-snapshots/compare", response_model=SuccessEnvelope[ContextDiff], operation_id="compare_context_snapshots")
        def compare_context_snapshots(payload: ContextSnapshotCompareRequest, request: Request, service: AssistantService = Depends(service_dependency)):
            left_id, right_id = payload.left_id, payload.right_id
            inspector = trace_inspector(request)
            _scope_snapshot(request, service, inspector, left_id, permission="trace:content:read")
            _scope_snapshot(request, service, inspector, right_id, permission="trace:content:read")
            return _envelope(request, ContextDiff.model_validate(inspector.compare(left_id, right_id, actor=request.app.state.actor_context)))

    return router


create_assistant_router = create_router

__all__ = ["create_assistant_router", "create_router"]
