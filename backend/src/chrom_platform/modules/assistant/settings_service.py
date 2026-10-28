"""Persistence and policy operations for assistant LLM settings."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from chrom_platform.core.dto import ActorContext
from chrom_platform.core.errors import APIError
from chrom_platform.infrastructure.models import AuditEvent, Job, JobAttempt, OutboxEvent

from .persistence import LlmConfigurationActiveRow, LlmConfigurationRow, LlmTestResultRow
from .security import trusted_provider_url
from .secret_store import SecretStoreError, WindowsDpapiSecretStore, default_secret_store
from .settings_schemas import (
    LlmConfigurationCreate, LlmConfigurationView,
    LlmSettingsView, LlmTestAccepted, LlmTestResult,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _config_hash(*, name: str, api_format: str, base_url: str, model_id: str, key_reference: str, timeout_seconds: int, max_retries: int, max_output_tokens: int, external_send_policy: str) -> str:
    payload = {
        "name": name, "api_format": api_format, "base_url": base_url,
        "model_id": model_id, "key_reference": key_reference,
        "timeout_seconds": timeout_seconds, "max_retries": max_retries,
        "max_output_tokens": max_output_tokens,
        "external_send_policy": external_send_policy, "schema_version": "assistant-llm-v1",
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _check_approved_endpoint(value: str, external_send_policy: str) -> str:
    try:
        return trusted_provider_url(
            value,
            external_send_policy=external_send_policy,
            approved_endpoints=os.environ.get("LLM_PROVIDER_APPROVED_ENDPOINTS"),
        )
    except ValueError as exc:
        raise APIError(422, "VALIDATION_FAILED", "模型服务地址未获部署管理员按完整 base endpoint 批准", details={"field": "base_url"}) from exc


def _reference_has_key(reference: str | None) -> bool:
    return bool(reference and reference != "unconfigured-key-reference")


def _view(row: LlmConfigurationRow) -> LlmConfigurationView:
    settings = dict(row.settings or {})
    return LlmConfigurationView(
        id=row.id, name=str(settings.get("name", "")), revision=row.revision,
        etag=f'"{row.revision}"', api_format="openai_chat", base_url=row.url,
        model_id=row.model_id, has_api_key=_reference_has_key(row.key_reference),
        status=row.status, limits={
            "timeout_seconds": int(settings.get("timeout_seconds", 90)),
            "max_retries": int(settings.get("max_retries", 2)),
            "max_output_tokens": int(settings.get("max_output_tokens", 4096)),
        }, external_send_policy=str(settings.get("external_send_policy", "deny_external")),
        last_test_id=row.last_test_id, created_at=row.created_at,
    )


def resolve_project(actor: ActorContext) -> str:
    selected = actor.default_project_id
    if selected and actor.can_access_project(selected):
        return selected
    if len(actor.project_ids) == 1:
        return actor.project_ids[0]
    raise APIError(400, "VALIDATION_FAILED", "需要服务器配置的默认项目范围")


def _require_permission(actor: ActorContext, permission: str) -> None:
    if not actor.can(permission):
        raise APIError(403, "PERMISSION_DENIED", "当前主体无权执行此操作")


def _audit(session: Session, *, actor: ActorContext, project_id: str, request_id: str, action: str, resource_id: str, after: dict[str, object]) -> None:
    session.add(AuditEvent(
        id=_id("audit"), project_id=project_id, actor_id=actor.actor_id,
        auth_method=actor.auth_method, interaction="human", request_id=request_id,
        action=action, resource_type="llm_configuration", resource_id=resource_id,
        resource_ref={"type": "llm_configuration", "id": resource_id},
        before_summary={}, after_summary=after, confirmation_id=None,
        config_hash=after.get("config_hash") if isinstance(after.get("config_hash"), str) else None,
        job_id=after.get("job_id") if isinstance(after.get("job_id"), str) else None,
        agent_run_id=None, result="SUCCEEDED", timestamp=_now(),
    ))


def get_settings(session: Session, actor: ActorContext) -> LlmSettingsView:
    if not (actor.can("settings:read") or actor.can("settings:write")):
        raise APIError(403, "PERMISSION_DENIED", "当前主体无权读取 LLM 配置")
    project_id = resolve_project(actor)
    row, pointer = _active_configuration(session, project_id)
    active_config = _view(row) if row is not None else None
    rows = list(session.scalars(select(LlmConfigurationRow).where(
        LlmConfigurationRow.project_id == project_id,
        LlmConfigurationRow.status.in_(("CANDIDATE", "TESTED")),
    ).order_by(LlmConfigurationRow.created_at.desc(), LlmConfigurationRow.id.desc()).limit(100)))
    return LlmSettingsView(
        active_configuration=active_config,
        active_revision=pointer.revision if pointer is not None else 0,
        candidate_configurations=[_view(row) for row in rows],
    )


def _active_configuration(
    session: Session,
    project_id: str,
) -> tuple[LlmConfigurationRow | None, LlmConfigurationActiveRow | None]:
    pointer = session.get(LlmConfigurationActiveRow, project_id)
    if pointer is None:
        return None, None
    row = session.scalar(select(LlmConfigurationRow).where(
        LlmConfigurationRow.id == pointer.configuration_id,
        LlmConfigurationRow.project_id == project_id,
        LlmConfigurationRow.revision == pointer.configuration_revision,
    ))
    if row is None:
        raise APIError(503, "STATE_CONFLICT", "活动 LLM 配置指针无法解析", retryable=True)
    return row, pointer


def get_provider_readiness(
    session: Session,
    actor: ActorContext,
    *,
    secret_store: WindowsDpapiSecretStore | None = None,
):
    """Report local readiness from the active DB snapshot without network I/O."""
    if not (actor.can("settings:read") or actor.can("settings:write")):
        raise APIError(403, "PERMISSION_DENIED", "当前主体无权读取 LLM 配置")
    from .priority import ProviderConfigCheck, ProviderReadiness

    project_id = resolve_project(actor)
    row, _ = _active_configuration(session, project_id)
    check_keys = ("LLM_PROVIDER_URL", "LLM_MODEL_ID", "LLM_KEY_REFERENCE", "LLM_CONFIG_REVISION")
    if row is None:
        return ProviderReadiness(
            status="BLOCKED",
            checks=[ProviderConfigCheck(key=key, status="not_activated") for key in check_keys],
            url_reachable=None, model_id_valid=False, key_reference_resolvable=False,
            config_revision=None, blocked_reason="LLM_CONFIGURATION_NOT_ACTIVATED",
        )

    settings = dict(row.settings or {})
    model_ok = bool(row.model_id and len(row.model_id) <= 128 and all(ord(ch) >= 32 for ch in row.model_id))
    endpoint_ok = False
    try:
        _check_approved_endpoint(row.url, str(settings.get("external_send_policy", "deny_external")))
        endpoint_ok = True
    except APIError:
        pass

    store = secret_store if secret_store is not None else default_secret_store()
    key_ok = False
    if store is not None and _reference_has_key(row.key_reference):
        try:
            key_ok = bool(store.get(row.key_reference))
        except (SecretStoreError, ValueError):
            key_ok = False

    config_hash = settings.get("config_hash")
    revision = f"sha256:{config_hash}" if isinstance(config_hash, str) and len(config_hash) == 64 else None
    statuses = {
        "LLM_PROVIDER_URL": "configured" if endpoint_ok else "unavailable",
        "LLM_MODEL_ID": "configured" if model_ok else "missing",
        "LLM_KEY_REFERENCE": "configured" if key_ok else "unavailable",
        "LLM_CONFIG_REVISION": "configured" if revision else "unavailable",
    }
    blocked_reason = None
    if not endpoint_ok:
        blocked_reason = "LLM_PROVIDER_ENDPOINT_NOT_APPROVED"
    elif not model_ok:
        blocked_reason = "LLM_MODEL_ID_MISSING_OR_INVALID"
    elif not key_ok:
        blocked_reason = "LLM_SECRET_REFERENCE_UNAVAILABLE"
    elif not revision:
        blocked_reason = "LLM_CONFIGURATION_REVISION_UNAVAILABLE"
    ready = blocked_reason is None
    return ProviderReadiness(
        status="READY" if ready else "BLOCKED",
        checks=[ProviderConfigCheck(key=key, status=statuses[key]) for key in check_keys],
        url_reachable=None, model_id_valid=model_ok,
        key_reference_resolvable=key_ok, config_revision=revision,
        blocked_reason=blocked_reason,
    )


def create_configuration(
    session: Session, actor: ActorContext, request: LlmConfigurationCreate,
    *, secret_store: WindowsDpapiSecretStore | None, request_id: str,
) -> LlmConfigurationView:
    _require_permission(actor, "settings:write")
    project_id = resolve_project(actor)
    base_url = _check_approved_endpoint(request.base_url, request.external_send_policy)
    if request.api_key is not None and request.reuse_secret_from_configuration_id is not None:
        raise APIError(422, "VALIDATION_FAILED", "api_key 与已有密钥引用不能同时提供")
    key_reference = "unconfigured-key-reference"
    if request.api_key is not None:
        if secret_store is None:
            raise APIError(503, "SECRET_STORE_UNAVAILABLE", "受保护的密钥存储未装配", retryable=True)
        try:
            key_reference = secret_store.put(request.api_key.get_secret_value())
        except SecretStoreError as exc:
            raise APIError(503, "SECRET_STORE_UNAVAILABLE", "受保护的密钥存储不可用", retryable=True) from exc
    elif request.reuse_secret_from_configuration_id:
        old = session.scalar(select(LlmConfigurationRow).where(
            LlmConfigurationRow.id == request.reuse_secret_from_configuration_id,
            LlmConfigurationRow.project_id == project_id,
        ))
        if old is None:
            raise APIError(404, "RESOURCE_NOT_FOUND", "密钥来源配置不可见")
        key_reference = old.key_reference
    digest = _config_hash(
        name=request.name.strip(), api_format=request.api_format, base_url=base_url,
        model_id=request.model_id.strip(), key_reference=key_reference,
        timeout_seconds=request.timeout_seconds, max_retries=request.max_retries,
        max_output_tokens=request.max_output_tokens,
        external_send_policy=request.external_send_policy,
    )
    revision = f"sha256:{digest}"
    settings = {
        "name": request.name.strip(), "api_format": request.api_format,
        "timeout_seconds": request.timeout_seconds, "max_retries": request.max_retries,
        "max_output_tokens": request.max_output_tokens,
        "external_send_policy": request.external_send_policy, "config_hash": digest,
        "schema_version": "assistant-llm-v1",
    }
    row = LlmConfigurationRow(
        id=_id("llmcfg"), project_id=project_id, revision=revision, url=base_url,
        model_id=request.model_id.strip(), key_reference=key_reference,
        status="CANDIDATE", last_test_id=None, settings=settings, created_at=_now(),
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError as exc:
        raise APIError(409, "VERSION_CONFLICT", "相同配置 revision 已存在") from exc
    _audit(session, actor=actor, project_id=project_id, request_id=request_id, action="settings.llm.configuration.created", resource_id=row.id, after={"status": row.status, "revision": row.revision, "config_hash": digest, "has_api_key": _reference_has_key(row.key_reference)})
    return _view(row)


def submit_test(session: Session, actor: ActorContext, configuration_id: str, *, request_id: str) -> LlmTestAccepted:
    _require_permission(actor, "settings:write")
    project_id = resolve_project(actor)
    row = session.scalar(select(LlmConfigurationRow).where(
        LlmConfigurationRow.id == configuration_id,
        LlmConfigurationRow.project_id == project_id,
    ).with_for_update())
    if row is None:
        raise APIError(404, "RESOURCE_NOT_FOUND", "LLM 配置不可见")
    if row.status == "RETIRED":
        raise APIError(409, "STATE_CONFLICT", "已退休的 LLM 配置不能重新测试")
    settings = dict(row.settings or {})
    _check_approved_endpoint(row.url, str(settings.get("external_send_policy", "deny_external")))
    now = _now()
    test_id, job_id, dispatch_id = _id("llmtest"), _id("job"), _id("dispatch")
    config_hash_value = str((row.settings or {}).get("config_hash", ""))
    if len(config_hash_value) != 64:
        raise APIError(409, "STATE_CONFLICT", "LLM 配置缺少有效 hash")
    job = Job(
        id=job_id, project_id=project_id, owner_id=actor.actor_id,
        kind="assistant_llm_configuration_test",
        resource_ref={"type": "llm_test", "id": test_id}, status="QUEUED",
        attempt=1, progress={}, cancelable=False, retryable=False,
        resume_mode="restart", created_at=now, updated_at=now,
        revision=1, etag='"1"', deadline_at=now + timedelta(seconds=90),
    )
    attempt = JobAttempt(
        id=_id("attempt"), job_id=job_id, attempt=1, status="QUEUED",
        resume_mode="restart", execution_token=None, fencing_token=0,
        lease_until=None, checkpoint_ref=None, started_at=None, finished_at=None,
        error=None, result_ref=None,
    )
    outbox = OutboxEvent(
        dispatch_id=dispatch_id, aggregate_type="job", aggregate_id=job_id,
        event_type="job.execute", payload={"job_id": job_id, "attempt": 1, "dispatch_id": dispatch_id},
        status="PENDING", attempts=0, available_at=now, dispatched_at=None,
        last_error=None, created_at=now, lease_owner=None, lease_token=0,
        lease_expires_at=None,
    )
    result = LlmTestResultRow(
        id=test_id, configuration_id=row.id, configuration_revision=row.revision,
        configuration_hash=config_hash_value, job_id=job_id, status="QUEUED",
        checks={}, error=None, created_at=now, finished_at=None,
    )
    row.last_test_id = test_id
    session.add_all([job, attempt, outbox, result])
    session.flush()
    _audit(session, actor=actor, project_id=project_id, request_id=request_id, action="settings.llm.configuration.test_queued", resource_id=row.id, after={"status": "QUEUED", "test_id": test_id, "job_id": job_id, "config_hash": config_hash_value})
    return LlmTestAccepted(
        id=test_id, job_id=job_id, status="QUEUED",
        resource_ref={"type": "llm_test", "id": test_id},
    )


def get_test(session: Session, actor: ActorContext, test_id: str) -> LlmTestResult:
    if not (actor.can("settings:read") or actor.can("settings:write")):
        raise APIError(403, "PERMISSION_DENIED", "当前主体无权读取 LLM 测试")
    project_id = resolve_project(actor)
    row = session.scalar(select(LlmTestResultRow).join(
        LlmConfigurationRow, LlmConfigurationRow.id == LlmTestResultRow.configuration_id,
    ).where(
        LlmTestResultRow.id == test_id, LlmConfigurationRow.project_id == project_id,
    ))
    if row is None:
        raise APIError(404, "RESOURCE_NOT_FOUND", "LLM 测试不可见")
    return LlmTestResult(
        id=row.id, configuration_id=row.configuration_id,
        configuration_revision=row.configuration_revision, status=row.status,
        checks=dict(row.checks or {
            "connectivity": "NOT_SUPPORTED", "streaming": "NOT_SUPPORTED",
            "tool_calling": "NOT_SUPPORTED", "structured_output": "NOT_SUPPORTED",
        }), error=dict(row.error) if row.error else None,
        created_at=row.created_at, finished_at=row.finished_at,
    )


def activate_configuration(
    session: Session, actor: ActorContext, configuration_id: str,
    *, test_id: str, expected_active_revision: int, if_match: str,
    request_id: str,
) -> LlmSettingsView:
    _require_permission(actor, "settings:write")
    project_id = resolve_project(actor)
    pointer = session.scalar(select(LlmConfigurationActiveRow).where(
        LlmConfigurationActiveRow.project_id == project_id,
    ).with_for_update())
    current_revision = pointer.revision if pointer is not None else 0
    current_etag = pointer.etag if pointer is not None else '"active-0"'
    if current_revision != expected_active_revision:
        raise APIError(409, "VERSION_CONFLICT", "活动配置 revision 已变化")
    if current_etag != if_match:
        raise APIError(412, "ETAG_MISMATCH", "If-Match 与当前活动配置不一致")
    candidate = session.scalar(select(LlmConfigurationRow).where(
        LlmConfigurationRow.id == configuration_id,
        LlmConfigurationRow.project_id == project_id,
    ).with_for_update())
    if candidate is None:
        raise APIError(404, "RESOURCE_NOT_FOUND", "LLM 配置不可见")
    if candidate.status != "TESTED":
        raise APIError(409, "STATE_CONFLICT", "配置必须由成功的持久测试标记为 TESTED 后才能激活")
    test = session.scalar(select(LlmTestResultRow).where(
        LlmTestResultRow.id == test_id,
        LlmTestResultRow.configuration_id == configuration_id,
    ).with_for_update())
    config_hash_value = str((candidate.settings or {}).get("config_hash", ""))
    settings = dict(candidate.settings or {})
    _check_approved_endpoint(candidate.url, str(settings.get("external_send_policy", "deny_external")))
    checks = dict(test.checks or {}) if test is not None else {}
    if (
        test is None or test.status != "SUCCEEDED"
        or test.configuration_revision != candidate.revision
        or test.configuration_hash != config_hash_value
        or any(checks.get(name) != "PASSED" for name in ("connectivity", "streaming", "tool_calling"))
        or checks.get("structured_output") not in {"PASSED", "NOT_SUPPORTED"}
    ):
        raise APIError(409, "STATE_CONFLICT", "必须通过与当前配置 hash 绑定的测试后才能激活")
    now = _now()
    next_revision = (pointer.revision + 1) if pointer is not None else 1
    next_etag = f'"active-{next_revision}"'
    try:
        with session.begin_nested():
            if pointer is not None:
                previous = session.get(LlmConfigurationRow, pointer.configuration_id, with_for_update=True)
                if previous is None or previous.project_id != project_id or previous.revision != pointer.configuration_revision:
                    raise APIError(409, "STATE_CONFLICT", "现有活动配置指针不一致")
                previous.status = "RETIRED"
                pointer.configuration_id = candidate.id
                pointer.configuration_revision = candidate.revision
                pointer.revision = next_revision
                pointer.etag = next_etag
                pointer.updated_at = now
            else:
                pointer = LlmConfigurationActiveRow(
                    project_id=project_id, configuration_id=candidate.id,
                    configuration_revision=candidate.revision, revision=next_revision,
                    etag=next_etag, updated_at=now,
                )
                session.add(pointer)
            candidate.status = "ACTIVE"
            session.flush()
    except IntegrityError as exc:
        raise APIError(409, "VERSION_CONFLICT", "活动配置已被并发更新") from exc
    _audit(session, actor=actor, project_id=project_id, request_id=request_id, action="settings.llm.configuration.activated", resource_id=candidate.id, after={"status": "ACTIVE", "revision": candidate.revision, "config_hash": config_hash_value, "active_pointer_revision": next_revision})
    return get_settings(session, actor)


__all__ = [
    "activate_configuration", "create_configuration", "get_provider_readiness", "get_settings", "get_test",
    "resolve_project", "submit_test",
]
