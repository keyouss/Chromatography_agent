"""Strict HTTP adapter for fixed-template training."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Header, Query, Request, Response
from chrom_platform.core.cursor import CursorCodec, CursorError
from chrom_platform.core.dto import ActorContext, ListEnvelope, PageInfo, SuccessEnvelope
from chrom_platform.core.errors import APIError
from chrom_platform.core.idempotency import BeginResult, IdempotencyRecord, IdempotencyScope
from .errors import TrainingError
from .schemas import (CancelRequest, EmptyRequest, Experiment, ExperimentAccepted, ExperimentComparison,
    ExperimentComparisonCreate, ExperimentCreate, ExperimentMetrics, ExperimentPatch, ExperimentValidation,
    Artifact, EventPage, LogEntry, SubmitRequest, TrainingTemplate)
from .service import TrainingService

_IF_MATCH = {"in":"header","name":"If-Match","required":True,"schema":{"type":"string","minLength":1,"maxLength":256}}
_IDEMPOTENCY = {"in":"header","name":"Idempotency-Key","required":True,"schema":{"type":"string","minLength":1,"maxLength":128}}
_ASYNC = {"202":{"description":"异步受理","headers":{"Location":{"required":True,"schema":{"type":"string","format":"uri-reference"}}}}}

def _request_id(request: Request) -> str: return getattr(request.state, "request_id", "training-request")
def _envelope(request: Request, value: Any) -> SuccessEnvelope[Any]: return SuccessEnvelope(data=value, warnings=[], request_id=_request_id(request))
def _actor(request: Request) -> ActorContext:
    value = getattr(request.app.state, "actor_context", None)
    if not isinstance(value, ActorContext): raise APIError(503,"DEPENDENCY_UNAVAILABLE","ActorContext 未配置",retryable=True)
    return value
def _service(request: Request, explicit: TrainingService | None) -> TrainingService:
    value = explicit or getattr(request.state,"training_service",None) or getattr(request.app.state,"training_service",None)
    if value is None: raise APIError(503,"DEPENDENCY_UNAVAILABLE","训练服务未配置",retryable=True)
    return value  # type: ignore[return-value]
def _scope(actor: ActorContext, project_id: str, *, write: bool=False, permission: str="training:create") -> None:
    if not actor.can_access_project(project_id): raise APIError(404 if not write else 403,"RESOURCE_NOT_FOUND" if not write else "PERMISSION_DENIED","无权访问当前项目")
    if write and not actor.can(permission) and not actor.can("*"): raise APIError(403,"PERMISSION_DENIED","缺少训练权限")
    if not write and not actor.can("training:read") and not actor.can("*"): raise APIError(403,"PERMISSION_DENIED","缺少训练读取权限")
def _read_scope(actor: ActorContext) -> None:
    if not actor.can("training:read") and not actor.can("*"): raise APIError(403,"PERMISSION_DENIED","缺少训练读取权限")
def _raise(exc: TrainingError) -> None:
    status_code = int(exc.details.get("status_code", exc.status_code)) if isinstance(exc.details, dict) else exc.status_code
    retryable = bool(exc.details.get("retryable", False)) if isinstance(exc.details, dict) else False
    raise APIError(status_code,exc.code,exc.message,details=exc.details,retryable=retryable)
def _header(value: str|None, name: str, *, missing_status: int) -> str:
    if value is None: raise APIError(missing_status,"IF_MATCH_REQUIRED" if name=="If-Match" else "IDEMPOTENCY_KEY_REQUIRED",f"缺少 {name}")
    if value=="" or len(value)>(128 if name=="Idempotency-Key" else 256): raise APIError(422,"VALIDATION_FAILED",f"{name} 长度无效")
    return value
def _digest(route: str, payload: Any) -> str: return hashlib.sha256(json.dumps({"route":route,"payload":payload},ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
def _reserve(request: Request, explicit: object|None, *, actor: ActorContext, project_id: str, method: str, route: str, key: str, payload: Any):
    store=explicit or getattr(request.state,"idempotency_store",None) or getattr(request.app.state,"idempotency_store",None)
    if store is None: raise APIError(503,"DEPENDENCY_UNAVAILABLE","幂等记录存储未配置",retryable=True)
    digest=_digest(route,payload); scope=IdempotencyScope(actor_id=actor.actor_id,project_id=project_id,method=method,route_identity=route,key=key); old=store.get(scope)
    if old is not None:
        if old.payload_hash!=digest: raise APIError(409,"IDEMPOTENCY_KEY_REUSED","幂等键已用于其他请求")
        if old.status=="IN_PROGRESS": raise APIError(409,"OPERATION_IN_PROGRESS","相同幂等请求正在处理中",retryable=True)
        return store,scope,digest,old
    begin=store.begin(scope,digest)
    if not isinstance(begin,BeginResult) or not begin.acquired:
        record=begin.record if isinstance(begin,BeginResult) else None
        if record is None: raise APIError(503,"DEPENDENCY_UNAVAILABLE","幂等存储返回无效结果",retryable=True)
        if record.payload_hash!=digest: raise APIError(409,"IDEMPOTENCY_KEY_REUSED","幂等键已用于其他请求")
        if record.status=="IN_PROGRESS": raise APIError(409,"OPERATION_IN_PROGRESS","相同幂等请求正在处理中",retryable=True)
        return store,scope,digest,record
    return store,scope,digest,None
def _finish(store: object, scope: IdempotencyScope, digest: str, result: Any, code: int) -> None: store.complete(scope,digest,code,result.model_dump(mode="json"))
def _fail(store: object|None, scope: IdempotencyScope|None, digest: str|None, code: str) -> None:
    if store is not None and scope is not None and digest is not None:
        try: store.fail(scope,digest,code)
        except Exception: pass
def _replay(record: IdempotencyRecord, model: Any) -> Any:
    if not record.response: raise APIError(409,"STATE_CONFLICT","幂等记录缺少响应")
    return model.model_validate_json(json.dumps(record.response,ensure_ascii=False))
def _decode(token: str|None, scope: str) -> str|None:
    if not token: return None
    try: return CursorCodec().decode(token,scope=scope)
    except CursorError: raise APIError(400,"VALIDATION_FAILED","cursor 无效或与筛选条件不匹配") from None
def _page(values: list[Any], *, limit: int, cursor: str|None, scope: str, key: str="id"):
    after=_decode(cursor,scope)
    if after is not None:
        boundary_id = after
        try:
            payload = json.loads(after)
            if isinstance(payload, dict) and isinstance(payload.get("id"), str): boundary_id = payload["id"]
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        index=next((i for i,item in enumerate(values) if str(getattr(item,key,""))==boundary_id),None)
        # A durable repository may already have applied the keyset predicate;
        # in that case the boundary is absent and the returned window is
        # already the continuation.  The signed scope check above still
        # rejects cursors bound to another filter/project.
        if index is not None: values=values[index+1:]
    window=values[:limit]; more=len(values)>limit; nxt=None
    if more and window:
        last = window[-1]
        raw = json.dumps({"created_at": last.created_at.isoformat(), "id": str(getattr(last,key))}, ensure_ascii=False, sort_keys=True, separators=(",", ":")) if key == "id" and hasattr(last, "created_at") else str(getattr(last,key))
        nxt=CursorCodec().encode(after_id=raw,scope=scope)
    return window,PageInfo(next_cursor=nxt,limit=limit,has_more=more)

def create_router(*, service: TrainingService|None=None, training_service: TrainingService|None=None, idempotency_store: object|None=None) -> APIRouter:
    app_service=service or training_service; router=APIRouter(tags=["training"])
    @router.get("/training/templates",response_model=ListEnvelope[TrainingTemplate],operation_id="list_training_templates")
    def list_templates(request: Request, task_type: str|None=Query(None,min_length=1,max_length=96), limit: int=Query(50,ge=1,le=200), cursor: str|None=Query(None,min_length=1,max_length=512)):
        _read_scope(_actor(request))
        try:
            values,page=_page(list(_service(request,app_service).list_templates(task_type=task_type)),limit=limit,cursor=cursor,scope=f"training-templates:{task_type or ''}"); return ListEnvelope(data=values,page=page,warnings=[],request_id=_request_id(request))
        except TrainingError as exc: _raise(exc)
    @router.get("/training/templates/{template_id}",response_model=SuccessEnvelope[TrainingTemplate],operation_id="get_training_template")
    def get_template(template_id: str, request: Request, version: str|None=Query(None,min_length=1,max_length=64)):
        _read_scope(_actor(request))
        try: return _envelope(request,_service(request,app_service).get_template(template_id,version))
        except TrainingError as exc: _raise(exc)
    @router.get("/training/experiments",response_model=ListEnvelope[Experiment],operation_id="list_training_experiments")
    def list_experiments(request: Request, project_id: str=Query(...,min_length=1,max_length=96), task_type: str|None=Query(None,min_length=1,max_length=96), status: str|None=Query(None,min_length=1,max_length=32), limit: int=Query(50,ge=1,le=200), cursor: str|None=Query(None,min_length=1,max_length=512)):
        _scope(_actor(request),project_id)
        try:
            values,page=_page(list(_service(request,app_service).list(project_id,task_type=task_type,status=status)),limit=limit,cursor=cursor,scope=f"training-experiments:{project_id}:{task_type or ''}:{status or ''}"); return ListEnvelope(data=values,page=page,warnings=[],request_id=_request_id(request))
        except TrainingError as exc: _raise(exc)
    @router.post("/training/experiments",response_model=SuccessEnvelope[Experiment],status_code=201,operation_id="create_training_experiment",openapi_extra={"parameters":[_IDEMPOTENCY]})
    def create_experiment(payload: ExperimentCreate, request: Request, response: Response, idempotency_key: str|None=Header(None,alias="Idempotency-Key",include_in_schema=False)):
        key=_header(idempotency_key,"Idempotency-Key",missing_status=400); actor=_actor(request); _scope(actor,payload.project_id,write=True)
        try:
            store,scope,digest,old=_reserve(request,idempotency_store,actor=actor,project_id=payload.project_id,method="POST",route="/api/v1/training/experiments",key=key,payload=payload.model_dump(mode="json"))
            if old is not None: response.headers["Idempotency-Replayed"]="true"; return _replay(old,SuccessEnvelope[Experiment])
            value=_service(request,app_service).create(payload); response.headers["ETag"]=value.etag; response.headers["Location"]=f"/api/v1/training/experiments/{value.id}"; result=_envelope(request,value); _finish(store,scope,digest,result,201); return result
        except TrainingError as exc: _fail(locals().get("store"),locals().get("scope"),locals().get("digest"),exc.code); _raise(exc)
    @router.get("/training/experiments/{experiment_id}",response_model=SuccessEnvelope[Experiment],operation_id="get_training_experiment")
    def get_experiment(experiment_id: str, request: Request, response: Response, project_id: str=Query(...,min_length=1,max_length=96)):
        _scope(_actor(request),project_id)
        try: value=_service(request,app_service).get(project_id,experiment_id); response.headers["ETag"]=value.etag; return _envelope(request,value)
        except TrainingError as exc: _raise(exc)
    @router.patch("/training/experiments/{experiment_id}",response_model=SuccessEnvelope[Experiment],operation_id="patch_training_experiment",openapi_extra={"parameters":[_IF_MATCH,_IDEMPOTENCY]})
    def patch_experiment(experiment_id: str,payload: ExperimentPatch,request: Request,response: Response,project_id: str=Query(...,min_length=1,max_length=96),if_match: str|None=Header(None,alias="If-Match",include_in_schema=False),idempotency_key: str|None=Header(None,alias="Idempotency-Key",include_in_schema=False)):
        match=_header(if_match,"If-Match",missing_status=428); key=_header(idempotency_key,"Idempotency-Key",missing_status=400); actor=_actor(request); _scope(actor,project_id,write=True,permission="training:create")
        try:
            store,scope,digest,old=_reserve(request,idempotency_store,actor=actor,project_id=project_id,method="PATCH",route="/api/v1/training/experiments/{id}",key=key,payload={"experiment_id":experiment_id,"project_id":project_id,"body":payload.model_dump(mode="json"),"if_match":match})
            if old is not None: response.headers["Idempotency-Replayed"]="true"; return _replay(old,SuccessEnvelope[Experiment])
            current=_service(request,app_service).get(project_id,experiment_id); value=_service(request,app_service).patch(project_id,experiment_id,payload,expected_revision=current.revision,if_match=match); response.headers["ETag"]=value.etag; result=_envelope(request,value); _finish(store,scope,digest,result,200); return result
        except TrainingError as exc: _fail(locals().get("store"),locals().get("scope"),locals().get("digest"),exc.code); _raise(exc)
    @router.post("/training/experiments/{experiment_id}/validate",response_model=SuccessEnvelope[ExperimentValidation],operation_id="validate_training_experiment",openapi_extra={"parameters":[_IF_MATCH,_IDEMPOTENCY]})
    def validate_experiment(experiment_id: str,payload: EmptyRequest,request: Request,response: Response,project_id: str=Query(...,min_length=1,max_length=96),if_match: str|None=Header(None,alias="If-Match",include_in_schema=False),idempotency_key: str|None=Header(None,alias="Idempotency-Key",include_in_schema=False)):
        match=_header(if_match,"If-Match",missing_status=428); key=_header(idempotency_key,"Idempotency-Key",missing_status=400); actor=_actor(request); _scope(actor,project_id,write=True,permission="training:validate")
        try:
            store,scope,digest,old=_reserve(request,idempotency_store,actor=actor,project_id=project_id,method="POST",route="/api/v1/training/experiments/{id}/validate",key=key,payload={"experiment_id":experiment_id,"project_id":project_id,"if_match":match})
            if old is not None: response.headers["Idempotency-Replayed"]="true"; return _replay(old,SuccessEnvelope[ExperimentValidation])
            current=_service(request,app_service).get(project_id,experiment_id); value=_service(request,app_service).validate(project_id,experiment_id,expected_revision=current.revision,if_match=match); result=_envelope(request,value); _finish(store,scope,digest,result,200); return result
        except TrainingError as exc: _fail(locals().get("store"),locals().get("scope"),locals().get("digest"),exc.code); _raise(exc)
    @router.post("/training/experiments/{experiment_id}/submit",response_model=SuccessEnvelope[ExperimentAccepted],status_code=202,operation_id="submit_training_experiment",openapi_extra={"parameters":[_IDEMPOTENCY],"responses":_ASYNC})
    def submit_experiment(experiment_id: str,payload: SubmitRequest,request: Request,response: Response,project_id: str=Query(...,min_length=1,max_length=96),idempotency_key: str|None=Header(None,alias="Idempotency-Key",include_in_schema=False)):
        key=_header(idempotency_key,"Idempotency-Key",missing_status=400); actor=_actor(request); _scope(actor,project_id,write=True,permission="training:submit")
        try:
            store,scope,digest,old=_reserve(request,idempotency_store,actor=actor,project_id=project_id,method="POST",route="/api/v1/training/experiments/{id}/submit",key=key,payload={"experiment_id":experiment_id,"project_id":project_id,**payload.model_dump(mode="json")})
            if old is not None: response.headers["Idempotency-Replayed"]="true"; replay=_replay(old,SuccessEnvelope[ExperimentAccepted]); response.headers["Location"]=f"/api/v1/training/experiments/{experiment_id}"; return replay
            value=_service(request,app_service).submit(project_id,experiment_id,payload,actor_id=actor.actor_id,idempotency_key=key); response.headers["Location"]=f"/api/v1/training/experiments/{experiment_id}"; result=_envelope(request,value); _finish(store,scope,digest,result,202); return result
        except TrainingError as exc: _fail(locals().get("store"),locals().get("scope"),locals().get("digest"),exc.code); _raise(exc)
    @router.post("/training/experiments/{experiment_id}/cancel",response_model=SuccessEnvelope[Experiment],status_code=202,operation_id="cancel_training_experiment",openapi_extra={"parameters":[_IF_MATCH,_IDEMPOTENCY],"responses":_ASYNC})
    def cancel_experiment(experiment_id: str,payload: CancelRequest,request: Request,response: Response,project_id: str=Query(...,min_length=1,max_length=96),if_match: str|None=Header(None,alias="If-Match",include_in_schema=False),idempotency_key: str|None=Header(None,alias="Idempotency-Key",include_in_schema=False)):
        match=_header(if_match,"If-Match",missing_status=428); key=_header(idempotency_key,"Idempotency-Key",missing_status=400); actor=_actor(request); _scope(actor,project_id,write=True,permission="training:cancel")
        try:
            store,scope,digest,old=_reserve(request,idempotency_store,actor=actor,project_id=project_id,method="POST",route="/api/v1/training/experiments/{id}/cancel",key=key,payload={"experiment_id":experiment_id,"project_id":project_id,**payload.model_dump(mode="json"),"if_match":match})
            if old is not None: response.headers["Idempotency-Replayed"]="true"; replay=_replay(old,SuccessEnvelope[Experiment]); response.headers["Location"]=f"/api/v1/training/experiments/{experiment_id}"; return replay
            current=_service(request,app_service).get(project_id,experiment_id); value=_service(request,app_service).cancel(project_id,experiment_id,reason=payload.reason,expected_revision=current.revision,if_match=match); response.headers["ETag"]=value.etag; response.headers["Location"]=f"/api/v1/training/experiments/{experiment_id}"; result=_envelope(request,value); _finish(store,scope,digest,result,202); return result
        except TrainingError as exc: _fail(locals().get("store"),locals().get("scope"),locals().get("digest"),exc.code); _raise(exc)
    @router.get("/training/experiments/{experiment_id}/events",response_model=SuccessEnvelope[EventPage],operation_id="list_training_experiment_events")
    def events(experiment_id: str,request: Request,project_id: str=Query(...,min_length=1,max_length=96),after_seq: int=Query(0,ge=0),limit: int=Query(100,ge=1,le=200)):
        _scope(_actor(request),project_id)
        try: return _envelope(request,_service(request,app_service).events(project_id,experiment_id,after_seq=after_seq,limit=limit))
        except TrainingError as exc: _raise(exc)
    @router.get("/training/experiments/{experiment_id}/logs",response_model=ListEnvelope[LogEntry],operation_id="list_training_experiment_logs")
    def logs(experiment_id: str,request: Request,project_id: str=Query(...,min_length=1,max_length=96),cursor: str|None=Query(None,min_length=1,max_length=512),limit: int=Query(100,ge=1,le=200)):
        _scope(_actor(request),project_id)
        try:
            scope=f"training-logs:{project_id}:{experiment_id}"; after=_decode(cursor,scope); values,_=_service(request,app_service).logs(project_id,experiment_id,after_seq=int(after) if after else 0,limit=limit+1); window=values[:limit]; more=len(values)>limit; nxt=CursorCodec().encode(after_id=str(window[-1].seq),scope=scope) if more and window else None; return ListEnvelope(data=window,page=PageInfo(next_cursor=nxt,limit=limit,has_more=more),warnings=[],request_id=_request_id(request))
        except TrainingError as exc: _raise(exc)
    @router.get("/training/experiments/{experiment_id}/metrics",response_model=SuccessEnvelope[ExperimentMetrics],operation_id="get_training_experiment_metrics")
    def metrics(experiment_id: str,request: Request,project_id: str=Query(...,min_length=1,max_length=96),attempt: int|None=Query(None,ge=1),after_step: int|None=Query(None,ge=0)):
        _scope(_actor(request),project_id)
        try: return _envelope(request,_service(request,app_service).metrics(project_id,experiment_id,attempt=attempt,after_step=after_step))
        except TrainingError as exc: _raise(exc)
    @router.get("/training/experiments/{experiment_id}/artifacts",response_model=ListEnvelope[Artifact],operation_id="list_training_experiment_artifacts")
    def artifacts(experiment_id: str,request: Request,project_id: str=Query(...,min_length=1,max_length=96),cursor: str|None=Query(None,min_length=1,max_length=512),limit: int=Query(50,ge=1,le=200)):
        _scope(_actor(request),project_id)
        try:
            scope=f"training-artifacts:{project_id}:{experiment_id}"; after=_decode(cursor,scope); values,_=_service(request,app_service).artifacts(project_id,experiment_id,after_id=after,limit=limit+1); window=values[:limit]; more=len(values)>limit; nxt=CursorCodec().encode(after_id=window[-1].id,scope=scope) if more and window else None; return ListEnvelope(data=window,page=PageInfo(next_cursor=nxt,limit=limit,has_more=more),warnings=[],request_id=_request_id(request))
        except TrainingError as exc: _raise(exc)
    @router.post("/training/comparisons",response_model=SuccessEnvelope[ExperimentComparison],operation_id="compare_training_experiments")
    def comparisons(payload: ExperimentComparisonCreate,request: Request):
        actor = _actor(request); _read_scope(actor)
        try: return _envelope(request,_service(request,app_service).compare(payload,project_ids=actor.project_ids))
        except TrainingError as exc: _raise(exc)
    return router

__all__=["create_router"]
