"""Priority launch assistant surface.

This module is deliberately a thin adapter over the existing prediction, data
center and model center application services.  It owns no business state and
never accepts file paths or raw provider credentials.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Annotated, Any, Iterable, Literal, Mapping
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from chrom_platform.core.dto import ActorContext, ResourceRef
from chrom_platform.core.errors import APIError
from chrom_platform.task_core.service import OperationContext
from chrom_platform.modules.model_center.errors import (
    ModelCenterError,
    ModelCenterNotFoundError,
    ModelCenterPermissionError,
    ModelCenterUnavailableError,
)
from chrom_platform.modules.model_center.schemas import AuditEvent, ModelRegistrationCreate, new_id, now_utc
from chrom_platform.modules.prediction.schemas import ExactModelSelection, PredictionCreate
from chrom_platform.structure_core.schemas import NormalizeStructureRequest, WarningView

from .adapter import secure_provider_request
from .errors import AssistantError, unavailable
from .runtime import RuntimeConfigSnapshot
from .security import trusted_provider_url
from .tools import ToolSpec, ToolSet


def _registration_read_error(exc: ModelCenterError) -> AssistantError:
    """Map model-center read failures to safe assistant-boundary errors."""

    if isinstance(exc, ModelCenterNotFoundError):
        return AssistantError(404, "RESOURCE_NOT_FOUND", "登记草稿不存在或当前主体不可见")
    if isinstance(exc, ModelCenterPermissionError):
        return AssistantError(403, "PERMISSION_DENIED", "当前主体无权访问登记草稿")
    if isinstance(exc, ModelCenterUnavailableError):
        return AssistantError(503, "DEPENDENCY_UNAVAILABLE", "模型中心服务暂不可用", retryable=True)
    return AssistantError(exc.status_code, exc.code, "登记草稿读取失败", retryable=exc.status_code >= 500)


class PriorityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AssistantRawPredictionArguments(PriorityModel):
    """Only user-owned raw structures and complete business inputs cross LLM boundary."""

    chromatography_type: Literal["tlc", "cc", "hplc", "chiral_lc", "gc", "sfc"]
    smiles: list[str] = Field(min_length=1, max_length=2)
    input: dict[str, Any]
    model_selection: ExactModelSelection | None = None
    tool: Literal["single_column", "full_column", "condition_compare", "reverse_solvent"] | None = None

    @classmethod
    def checked(cls, arguments: Mapping[str, Any]) -> "AssistantRawPredictionArguments":
        value = cls.model_validate(arguments)
        count = 2 if value.chromatography_type in {"cc", "hplc", "sfc"} else 1
        if len(value.smiles) != count or any(not raw.strip() or len(raw) > 20_000 for raw in value.smiles):
            raise AssistantError(422, "VALIDATION_FAILED", "结构数量或原始 SMILES 无效")
        if value.chromatography_type == "sfc" and value.tool not in {"single_column", "full_column"}:
            raise AssistantError(422, "VALIDATION_FAILED", "SFC 必须明确选择单柱或全柱工具")
        if value.chromatography_type == "tlc" and value.tool not in {None, "condition_compare", "reverse_solvent"}:
            raise AssistantError(422, "VALIDATION_FAILED", "TLC 单次、条件比较和反向溶剂工具参数不匹配")
        if value.chromatography_type not in {"sfc", "tlc"} and value.tool is not None:
            raise AssistantError(422, "VALIDATION_FAILED", "所选色谱类型不接受 tool 参数")
        return value


@dataclass(frozen=True)
class PriorityOperationContext(OperationContext):
    """Server-bound scope shared by sync and async worker runtimes."""

    server_project_id: str | None = None
    server_artifact_id: str | None = None
    idempotency_key: str | None = None
    confirmation_id: str | None = None
    agent_attempt_id: str | None = None
    execution_token: str | None = None
    fencing_token: int | None = None
    # Bound by AgentRuntime for each provider tool call.  It is deliberately
    # separate from the run-level idempotency key: two normalize calls in one
    # run must reserve two independent records, while a retry of one call
    # keeps the same key.
    tool_call_attempt: int | None = None


# The worker and the M4 request path bind one request-scoped service under the
# ``priority`` key.  Keep the executable registry beside the public tool
# surface so a declaration cannot silently outrun its service port.
PRIORITY_TOOL_NAMES = (
    "normalize_structure",
    "get_prediction_capabilities",
    "create_prediction",
    "get_prediction",
    "get_data_version_summary",
    "get_prediction_record_summary",
    "list_models",
    "get_model_card",
    "create_registration_draft",
    "get_registration_draft",
)


class NumericStat(PriorityModel):
    count: int = Field(ge=0)
    minimum: float | None = None
    maximum: float | None = None
    mean: float | None = None
    unit: str | None = None


class SummarySource(PriorityModel):
    source_type: Literal["data_version", "prediction_record"]
    source_ids: list[str] = Field(default_factory=list)
    version: str | None = None


class DataVersionSummary(PriorityModel):
    project_id: str
    domain_id: str | None = None
    count: int = Field(ge=0)
    status_counts: dict[str, int] = Field(default_factory=dict)
    missing: dict[str, int] = Field(default_factory=dict)
    numeric: dict[str, NumericStat] = Field(default_factory=dict)
    units: dict[str, str] = Field(default_factory=dict)
    source: SummarySource


class SfcSummaryPredictionRef(ResourceRef):
    """Only prediction IDs generated by the public prediction write path."""

    type: Literal["prediction"]
    id: str = Field(pattern=r"^pred_[0-9a-f]{32}$", min_length=37, max_length=37)


class PredictionRecordSummary(PriorityModel):
    project_id: str
    chromatography_type: str | None = None
    count: int = Field(ge=0, description="本次 reader 窗口内去重后的记录数，不代表全库总数。")
    summary_scope: Literal["read_window"] = "read_window"
    read_window_count: int = Field(ge=0, description="本次 reader 窗口内去重后的记录数。")
    truncated: bool = Field(
        description="本次读取超过200条并截取窗口；只表示当前 reader 窗口截断，不代表全库总量。",
    )
    status_counts: dict[str, int] = Field(default_factory=dict)
    missing: dict[str, int] = Field(default_factory=dict)
    numeric: dict[str, NumericStat] = Field(default_factory=dict)
    units: dict[str, str] = Field(default_factory=dict)
    source: SummarySource
    sfc_warning_record_counts: dict[
        Literal["UNOBSERVED_CONDITION_COMBINATION", "COMBINATION_GENERALIZATION_NOT_ESTABLISHED"],
        Annotated[int, Field(ge=0)],
    ] = Field(
        default_factory=dict,
        description="本次 reader 窗口内按包含受约束组合警告的 SFC 记录计数，不代表全库，也不是候选数或 Top-k 行数。",
    )
    sfc_result_refs: list[SfcSummaryPredictionRef] = Field(
        default_factory=list,
        max_length=200,
        description="本次 reader 窗口内包含受约束组合警告的可见 SFC 记录的预测结果引用；按预测结果 ID 稳定去重排序。",
    )


class NormalizedStructureResult(PriorityModel):
    """Persisted structure metadata consumed by the prediction tool."""

    structure_ref: ResourceRef
    resource_ref: ResourceRef
    canonical_smiles: str
    snapshot_hash: str
    has_stereochemistry: bool
    stereochemistry_status: Literal["specified", "unspecified", "not_applicable"]
    warnings: list[WarningView] = Field(default_factory=list)


class ProviderConfigCheck(PriorityModel):
    key: str
    status: str


class ProviderReadiness(PriorityModel):
    status: str
    checks: list[ProviderConfigCheck]
    url_reachable: bool | None = None
    model_id_valid: bool | None = None
    key_reference_resolvable: bool | None = None
    config_revision: str | None = None
    blocked_reason: str | None = None


class ProviderProbeEvidence(PriorityModel):
    status: str
    request_shape: dict[str, object]
    response_shape: dict[str, object] | None = None
    blocked_reason: str | None = None
    http_status_class: str | None = None
    error_category: str | None = None
    variants: dict[str, dict[str, object]] = Field(default_factory=dict)
    created_at: datetime


PriorityToolName = Literal[
    "normalize_structure",
    "get_prediction_capabilities",
    "create_prediction",
    "get_prediction",
    "get_data_version_summary",
    "get_prediction_record_summary",
    "list_models",
    "get_model_card",
    "create_registration_draft",
    "get_registration_draft",
]


class PriorityToolView(PriorityModel):
    name: PriorityToolName
    description: str
    schema_version: str
    permission: str
    parameters: dict[str, object]
    requires_confirmation: bool


class PriorityToolsView(PriorityModel):
    tools: list[PriorityToolView]
    request_id: str


_CONFIG_KEYS = ("LLM_PROVIDER_URL", "LLM_MODEL_ID", "LLM_KEY_REFERENCE", "LLM_CONFIG_REVISION")
_URL_ALIASES = ("LLM_PROVIDER_URL", "BASE_URL", "OPENAI_API_BASE", "SILICONFLOW_API_BASE", "OLLAMA_API_BASE")
_MODEL_ALIASES = ("LLM_MODEL_ID", "MODEL_ID", "OPENAI_MODEL_ID", "SILICONFLOW_MODEL_ID", "OLLAMA_MODEL_ID")
_SECRET_WORDS = ("secret=", "token=", "apikey=", "api_key=", "password=")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _safe_mapping(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _safe_mapping(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {str(k): _safe_mapping(v) for k, v in value.items() if not any(word in str(k).lower() for word in ("secret", "token", "password", "api_key", "apikey", "credential"))}
    if isinstance(value, (list, tuple)):
        return [_safe_mapping(v) for v in value[:100]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _normalized_provider_url(value: str) -> str:
    """Keep only the non-sensitive provider origin/path for revision hashing."""
    try:
        parsed = urlsplit(value.strip())
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))
    except ValueError:
        return value.split("?", 1)[0].split("#", 1)[0].strip().lower().rstrip("/")


def _resolve_key_reference(values: Mapping[str, str]) -> tuple[str, str]:
    """Return (reference name, secret) without treating the reference as a secret.

    ``LLM_KEY_REFERENCE`` is deliberately an environment/secret-manager key
    name.  The resolved value is used only while constructing the outbound
    request and is never put in a runtime config, revision, or evidence DTO.
    Direct aliases such as ``API_KEY`` are intentionally not used as an
    implicit fallback: callers must explicitly name the reference.
    """
    explicit = values.get("LLM_KEY_REFERENCE", "").strip()
    if explicit:
        valid_name = (
            len(explicit) <= 128
            and explicit == explicit.upper()
            and explicit[0].isalpha()
            and all(ch.isalnum() or ch == "_" for ch in explicit)
        )
        if not valid_name or any(word in explicit.lower() for word in _SECRET_WORDS):
            return "", ""
        return explicit, values.get(explicit, "").strip()
    # One-time compatibility boundary for deployments that predate
    # LLM_KEY_REFERENCE. An explicitly present but empty reference never
    # reaches this branch and therefore cannot silently fall back.
    if "LLM_KEY_REFERENCE" not in values:
        legacy_secret = values.get("API_KEY", "").strip()
        if legacy_secret:
            return "API_KEY", legacy_secret
    return "", ""


def provider_config_revision(*, url: str, model_id: str, key_reference_name: str, source: str) -> str:
    """Derive a stable server-side revision without reading secret values."""
    payload = {
        "url": _normalized_provider_url(url),
        "model_id": model_id.strip(),
        "key_reference_name": key_reference_name,
        "source": source,
    }
    return "sha256:" + hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _config_revision(values: Mapping[str, str], *, url_key: str | None, model_key: str | None, key_key: str | None, source: str) -> str:
    configured = values.get("LLM_CONFIG_REVISION", "").strip()
    if configured and len(configured) <= 128 and not any(word in configured.lower() for word in _SECRET_WORDS):
        return configured
    return provider_config_revision(
        url=values.get(url_key or "", ""),
        model_id=values.get(model_key or "", ""),
        key_reference_name=key_key or "unconfigured",
        source=source,
    )


def resolve_runtime_config(environ: Mapping[str, str] | None = None, *, dotenv_path: str | os.PathLike[str] | None = None) -> RuntimeConfigSnapshot:
    """Bind the effective server config and derive its revision automatically."""
    env = dict(environ or os.environ)
    values = _dotenv_values(dotenv_path)
    values.update(env)
    url_key = next((key for key in _URL_ALIASES if values.get(key, "").strip()), None)
    model_key = next((key for key in _MODEL_ALIASES if values.get(key, "").strip()), None)
    key_key, _ = _resolve_key_reference(values)
    configured = url_key is not None
    url = values.get(url_key or "", "https://unconfigured.invalid/v1").strip()
    if url_key is not None:
        url = trusted_provider_url(url, allowed_hosts=values.get("LLM_PROVIDER_ALLOWED_HOSTS"))
    model = values.get(model_key or "", "unconfigured-model").strip()
    key_reference = key_key or "unconfigured-key-reference"
    source = "environment" if any(key and env.get(key, "").strip() for key in (url_key, model_key, key_key)) else "dotenv"
    revision = _config_revision(values, url_key=url_key, model_key=model_key, key_key=key_key, source=source)
    return RuntimeConfigSnapshot(url=url, model_id=model, key_reference=key_reference, config_revision=revision, provider_allowed_hosts=values.get("LLM_PROVIDER_ALLOWED_HOSTS"), configuration_state="CONFIGURED" if configured else "UNAVAILABLE")


def _objects(values: Iterable[Any]) -> list[Mapping[str, Any]]:
    result: list[Mapping[str, Any]] = []
    for value in values:
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        if isinstance(value, Mapping):
            result.append(value)
    return result


def _value(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _registration_tool_value(value: Any) -> dict[str, Any]:
    """Preserve the registration DTO and attach its durable resource identity."""
    raw = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    if not isinstance(raw, Mapping):
        raise AssistantError(502, "TOOL_RESULT_INVALID", "模型登记服务返回无效结果")
    result = dict(raw)
    registration_id = result.get("id")
    if not isinstance(registration_id, str) or not registration_id:
        raise AssistantError(502, "TOOL_RESULT_INVALID", "模型登记服务未返回登记 ID")
    result["resource_ref"] = {"type": "model_registration", "id": registration_id}
    return result


def _summary(values: Iterable[Any], *, project_id: str, source_type: str, source_ids: list[str], version: str | None, domain_id: str | None = None, chromatography_type: str | None = None) -> DataVersionSummary | PredictionRecordSummary:
    rows = _objects(values)
    status_counts = dict(Counter(str(row.get("status", "UNKNOWN")) for row in rows))
    missing: Counter[str] = Counter()
    numbers: dict[str, list[float]] = {}
    units: dict[str, str] = {}
    for row in rows:
        for key, value in row.items():
            if value is None or value == "":
                missing[key] += 1
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                numbers.setdefault(key, []).append(float(value))
            if key in {"unit", "units", "schema_version"} and isinstance(value, str):
                units[key] = value
        # Nested input/result records are summarized without exposing raw data.
        for parent in ("input_snapshot", "result", "model_snapshot"):
            nested = row.get(parent)
            if isinstance(nested, Mapping):
                for key, value in nested.items():
                    dotted = f"{parent}.{key}"
                    if value is None or value == "":
                        missing[dotted] += 1
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
                        numbers.setdefault(dotted, []).append(float(value))
    numeric = {
        key: NumericStat(count=len(items), minimum=min(items), maximum=max(items), mean=sum(items) / len(items), unit=units.get(key))
        for key, items in numbers.items() if items
    }
    common = dict(project_id=project_id, count=len(rows), status_counts=status_counts, missing=dict(missing), numeric=numeric, units=units, source=SummarySource(source_type=source_type, source_ids=source_ids, version=version))
    if domain_id is not None:
        return DataVersionSummary(domain_id=domain_id, **common)
    return PredictionRecordSummary(
        chromatography_type=chromatography_type,
        summary_scope="read_window",
        read_window_count=len(rows),
        truncated=False,
        **common,
    )


def summarize_data_versions(values: Iterable[Any], *, project_id: str, domain_id: str | None = None) -> DataVersionSummary:
    rows = list(values)
    ids = [str(getattr(v, "id", v.get("id", "")) if isinstance(v, Mapping) else getattr(v, "id", "")) for v in rows]
    versions = [str(value) for value in (_value(v, "version") for v in rows) if value is not None]
    return _summary(rows, project_id=project_id, domain_id=domain_id, source_type="data_version", source_ids=ids, version=versions[-1] if versions else None)  # type: ignore[return-value]


def summarize_prediction_records(values: Iterable[Any], *, project_id: str, chromatography_type: str | None = None) -> PredictionRecordSummary:
    read_rows = list(values)
    truncated = len(read_rows) > 200
    read_rows = read_rows[:200]
    by_id: dict[str, Any] = {}
    without_id: list[Any] = []
    for row in read_rows:
        value = _value(row, "id")
        record_id = str(value).strip() if value is not None else ""
        if record_id:
            by_id.setdefault(record_id, row)
        else:
            without_id.append(row)
    rows = [by_id[key] for key in sorted(by_id)] + without_id
    ids = sorted(by_id)
    versions = [str(value) for value in (_value(v, "schema_version") for v in rows) if value is not None]
    summary = _summary(rows, project_id=project_id, chromatography_type=chromatography_type, source_type="prediction_record", source_ids=ids, version=versions[-1] if versions else None)
    warning_counts: Counter[str] = Counter()
    result_refs: list[SfcSummaryPredictionRef] = []
    seen: set[tuple[str, str]] = set()
    codes = {"UNOBSERVED_CONDITION_COMBINATION", "COMBINATION_GENERALIZATION_NOT_ESTABLISHED"}
    for row in rows:
        if _value(row, "chromatography_type") != "sfc":
            continue
        record_codes = {
            warning.partition(":")[0]
            for warning in (_value(row, "warnings") or [])
            if isinstance(warning, str) and warning.partition(":")[0] in codes
        }
        warning_counts.update(record_codes)
        if not record_codes:
            continue
        raw_ref = _value(row, "result_ref")
        if raw_ref is None:
            continue
        if isinstance(raw_ref, BaseModel):
            raw_ref = raw_ref.model_dump(mode="python")
        try:
            ref = SfcSummaryPredictionRef.model_validate(raw_ref)
        except ValueError:
            continue
        identity = (ref.type, ref.id)
        if identity not in seen:
            seen.add(identity)
            result_refs.append(ref)
    result_refs.sort(key=lambda ref: (ref.type, ref.id))
    return summary.model_copy(update={
        "summary_scope": "read_window",
        "read_window_count": len(rows),
        "truncated": truncated,
        "sfc_warning_record_counts": dict(warning_counts),
        "sfc_result_refs": result_refs[:200],
    })  # type: ignore[return-value]


def inspect_provider_config(environ: Mapping[str, str] | None = None, *, dotenv_path: str | os.PathLike[str] | None = None, timeout_seconds: float = 3.0) -> ProviderReadiness:
    """Inspect config names and reachability without returning credential data."""
    env = dict(environ or os.environ)
    dotenv_values = _dotenv_values(dotenv_path)
    values: dict[str, str] = dict(dotenv_values)
    # Keep the effective environment in memory so an explicit reference such
    # as ``API_KEY`` can resolve its secret; only names/statuses leave this
    # function.
    values.update(env)
    url_key = next((key for key in _URL_ALIASES if values.get(key, "").strip()), None)
    model_key = next((key for key in _MODEL_ALIASES if values.get(key, "").strip()), None)
    secret_key, secret = _resolve_key_reference(values)
    url = values.get(url_key or "", "").strip()
    model = values.get(model_key or "", "").strip()
    key_ref = secret_key
    source_keys = (url_key, model_key, secret_key)
    source = "environment" if any(key and env.get(key, "").strip() for key in source_keys) else "dotenv"
    revision = _config_revision(values, url_key=url_key, model_key=model_key, key_key=secret_key, source=source)
    checks = [ProviderConfigCheck(key=key, status="configured" if values.get(key, "").strip() else "missing") for key in _CONFIG_KEYS]
    for item in checks:
        aliases = _URL_ALIASES if item.key == "LLM_PROVIDER_URL" else _MODEL_ALIASES if item.key == "LLM_MODEL_ID" else ("LLM_KEY_REFERENCE",) if item.key == "LLM_KEY_REFERENCE" else ("LLM_CONFIG_REVISION",)
        source = next((key for key in aliases if values.get(key, "").strip()), None)
        if item.key == "LLM_KEY_REFERENCE" and secret_key and source is None:
            source = secret_key
        if source and source != item.key:
            checks[checks.index(item)] = ProviderConfigCheck(key=item.key, status=f"configured_via_{source}")
        elif item.key == "LLM_CONFIG_REVISION" and not source and revision:
            checks[checks.index(item)] = ProviderConfigCheck(key=item.key, status="generated_sha256")
    model_ok = bool(model and len(model) <= 128 and all(ord(ch) >= 32 for ch in model))
    key_ok = bool(key_ref and secret and not any(word in key_ref.lower() for word in _SECRET_WORDS) and len(secret) <= 4096)
    blocked = None
    if not url:
        blocked = "缺少配置键 LLM_PROVIDER_URL 或其现有别名"
    elif not model_ok:
        blocked = "缺少或无效配置键 LLM_MODEL_ID"
    elif not key_ok:
        blocked = "缺少或不可解析配置键 LLM_KEY_REFERENCE"
    else:
        try:
            trusted_provider_url(url, allowed_hosts=values.get("LLM_PROVIDER_ALLOWED_HOSTS"))
        except ValueError:
            blocked = "LLM_PROVIDER_URL 不在受信 HTTPS allowlist"
    if blocked is not None:
        return ProviderReadiness(status="BLOCKED", checks=checks, url_reachable=None, model_id_valid=model_ok, key_reference_resolvable=key_ok, config_revision=revision, blocked_reason=blocked)
    url_reachable: bool | None = None
    try:
        secure_provider_request(url, method="GET", timeout_seconds=timeout_seconds, allowed_hosts=values.get("LLM_PROVIDER_ALLOWED_HOSTS"))
        # A provider base URL may reject GET while still accepting the
        # OpenAI-compatible POST endpoint; any HTTP response proves reachability.
        url_reachable = True
    except (OSError, ValueError, ConnectionError):
        url_reachable = False
    if url_reachable is False:
        blocked = "LLM_PROVIDER_URL 不可达"
    return ProviderReadiness(status="READY" if blocked is None else "BLOCKED", checks=checks, url_reachable=url_reachable, model_id_valid=model_ok, key_reference_resolvable=key_ok, config_revision=revision, blocked_reason=blocked)


def _dotenv_values(dotenv_path: str | os.PathLike[str] | None) -> dict[str, str]:
    if not dotenv_path or not Path(dotenv_path).is_file():
        return {}
    result: dict[str, str] = {}
    for line in Path(dotenv_path).read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip().strip('"\'')
    return result


def probe_configured_provider(*, environ: Mapping[str, str] | None = None, dotenv_path: str | os.PathLike[str] | None = None, timeout_seconds: float = 10.0) -> ProviderProbeEvidence:
    """Perform one real OpenAI-compatible structured-tool request in memory.

    Only HTTP status class and response shape are retained.  Credential values
    are read for the request and never returned, logged or written to evidence.
    """
    values = _dotenv_values(dotenv_path)
    values.update(dict(environ or os.environ))
    url = next((values.get(key, "").strip() for key in _URL_ALIASES if values.get(key, "").strip()), "")
    model = next((values.get(key, "").strip() for key in _MODEL_ALIASES if values.get(key, "").strip()), "")
    key_reference, key = _resolve_key_reference(values)
    shape = {"messages": 1, "tools": ["get_prediction_capabilities"], "structured": True, "max_tokens": 128, "tool_choice_variants": ["required", "named"]}
    if not url or not model or not key_reference or not key:
        return ProviderProbeEvidence(status="BLOCKED", request_shape=shape, blocked_reason="provider配置缺少现有键", error_category="CONFIG_MISSING", created_at=datetime.now(timezone.utc))
    try:
        url = trusted_provider_url(url, allowed_hosts=values.get("LLM_PROVIDER_ALLOWED_HOSTS"))
    except ValueError:
        return ProviderProbeEvidence(status="BLOCKED", request_shape=shape, blocked_reason="LLM_PROVIDER_URL 不在受信 HTTPS allowlist", error_category="CONFIG_INVALID", created_at=datetime.now(timezone.utc))
    endpoint = url.rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint += "/chat/completions"
    tool = {"type": "function", "function": {"name": "get_prediction_capabilities", "description": "Read prediction capabilities", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}
    variants: dict[str, dict[str, object]] = {}
    for variant, tool_choice in (("required", "required"), ("named", {"type": "function", "function": {"name": "get_prediction_capabilities"}})):
        payload = {"model": model, "messages": [{"role": "user", "content": "Call the get_prediction_capabilities tool now. Do not answer in text."}], "tools": [tool], "tool_choice": tool_choice, "max_tokens": 128}
        request_shape = {"keys": sorted(payload), "messages": [{"role": "user", "content_type": "string"}], "tools": ["get_prediction_capabilities"], "tool_choice": variant, "max_tokens": 128}
        try:
            status, response_headers, raw = secure_provider_request(
                endpoint, method="POST", body=_canonical(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
                timeout_seconds=timeout_seconds,
                allowed_hosts=values.get("LLM_PROVIDER_ALLOWED_HOSTS"),
            )
            status_class = f"{status // 100}xx"
            request_id = response_headers.get("x-request-id") or response_headers.get("request-id")
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("provider response is not JSON") from exc
            choices = parsed.get("choices") if isinstance(parsed, Mapping) else None
            tool_call_count = 0
            if isinstance(parsed, Mapping) and isinstance(parsed.get("tool_calls"), list):
                tool_call_count += len(parsed["tool_calls"])
            if isinstance(choices, list):
                for item in choices:
                    message = item.get("message") if isinstance(item, Mapping) else None
                    if isinstance(message, Mapping) and isinstance(message.get("tool_calls"), list):
                        tool_call_count += len(message["tool_calls"])
            variants[variant] = {"request_shape": request_shape, "http_status_class": status_class, "request_id": request_id[:128] if request_id else None, "response_keys": sorted(parsed.keys()) if isinstance(parsed, Mapping) else [], "tool_calls_count": tool_call_count, "status": "READY" if tool_call_count else "BLOCKED", "error_category": None if tool_call_count else "TOOL_CALL_NOT_RETURNED"}
        except Exception as exc:
            status = getattr(exc, "http_status", None)
            request_id = None
            headers = getattr(exc, "headers", None)
            if headers is not None:
                request_id = headers.get("x-request-id") or headers.get("request-id")
            category = getattr(exc, "category", None) or ("TIMEOUT" if isinstance(exc, TimeoutError) else "NETWORK_OR_PROTOCOL_ERROR")
            provider_code = getattr(exc, "provider_error_code", None)
            diagnostic = {"request_shape": request_shape, "http_status_class": f"{status // 100}xx" if isinstance(status, int) else None, "request_id": request_id[:128] if request_id else None, "response_keys": [], "tool_calls_count": 0, "status": "BLOCKED", "error_category": category}
            if isinstance(provider_code, str) and len(provider_code) <= 64:
                diagnostic["provider_error_code"] = provider_code
            variants[variant] = diagnostic
    ready = all(value.get("status") == "READY" for value in variants.values())
    return ProviderProbeEvidence(
        status="READY" if ready else "BLOCKED",
        request_shape=shape,
        response_shape={"variants": {name: {"response_keys": value.get("response_keys", []), "tool_calls_count": value.get("tool_calls_count", 0)} for name, value in variants.items()}},
        blocked_reason=None if ready else "真实 provider 结构化工具调用未全部就绪",
        http_status_class="2xx" if all(value.get("http_status_class") == "2xx" for value in variants.values()) else next((str(value.get("http_status_class")) for value in variants.values() if value.get("http_status_class")), None),
        error_category=None if ready else next((str(value.get("error_category")) for value in variants.values() if value.get("error_category")), "TOOL_CALL_NOT_RETURNED"),
        variants=variants,
        created_at=datetime.now(timezone.utc),
    )


def probe_provider(provider: object, config: RuntimeConfigSnapshot, *, timeout_seconds: float = 10.0) -> ProviderProbeEvidence:
    """Send one real provider request carrying a non-empty structured tool schema."""
    request_shape = {"messages": 1, "tools": ["priority.get_prediction_capabilities"], "structured": True}
    method = getattr(provider, "complete", None) or getattr(provider, "run", None)
    if not callable(method):
        return ProviderProbeEvidence(status="BLOCKED", request_shape=request_shape, blocked_reason="provider client 未配置", created_at=datetime.now(timezone.utc))
    tool = {"name": "get_prediction_capabilities", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}
    try:
        response = method(config=config, messages=[{"role": "user", "content": "Return the available prediction capabilities."}], tools=[tool], response_schema={"type": "object"}, timeout_seconds=timeout_seconds)
        if hasattr(response, "model_dump"):
            response = response.model_dump(mode="json")
        shape = {"type": type(response).__name__, "has_tool_calls": bool(isinstance(response, Mapping) and (response.get("tool_calls") or response.get("tools"))), "has_structured": bool(isinstance(response, Mapping) and (response.get("structured") is not None or response.get("output") is not None))}
        return ProviderProbeEvidence(status="READY", request_shape=request_shape, response_shape=shape, created_at=datetime.now(timezone.utc))
    except Exception:
        return ProviderProbeEvidence(status="BLOCKED", request_shape=request_shape, blocked_reason="真实 provider 请求失败", created_at=datetime.now(timezone.utc))


def priority_toolset(*, build: str = "universal-v1", mode: str = "business", permissions: set[str] | frozenset[str] = frozenset({"*"})) -> ToolSet:
    """Allowlist for the first launch; legacy tools remain available only to old runtimes."""
    prediction_schema = _raw_prediction_arguments_schema()
    structure_schema = TypeAdapter(NormalizeStructureRequest).json_schema()
    registration_schema = ModelRegistrationCreate.model_json_schema()
    specs = [
        ToolSpec("normalize_structure", "规范化并持久化化学结构；返回真实 structure_ref、resource_ref、canonical_smiles 和 snapshot_hash。单次预测直接接收本轮有序原始 SMILES 并在服务端规范化；HPLC 的原始输入必须保留，不能以 canonical SMILES 替代。", permission="prediction:create", service="priority", modes=frozenset({"business"}), builds=frozenset({build, "full", "universal", "universal-v1"}), argument_schema=structure_schema),
        ToolSpec("get_prediction_capabilities", "按用户要求的色谱类型查询当前构建能力。结果列出可用工具、输入 schema 和 READY 模型选项。多个模型时须先请用户明确选择；SFC还须明确单柱/全柱工具及候选 scope。读取一次后直接准备预测，不要重复查询。", permission="prediction:read", service="priority", builds=frozenset({build, "full", "universal", "universal-v1"}), argument_schema={"type": "object", "properties": {"chromatography_type": {"type": "string", "enum": ["tlc", "cc", "hplc", "chiral_lc", "gc", "sfc"]}}, "additionalProperties": False}),
        ToolSpec("create_prediction", "优先使用原始输入形态：传 chromatography_type、按槽位有序的本轮原始 smiles 和完整 input；SFC另传 tool。HPLC原文只放在顶层 smiles，input 禁止 model_input_smiles。模型选择可省略仅当对应工具恰有一个 READY 选项；多个选项时先请用户选择，并把所选 exact model/version/profile pair 放入 model_selection。服务端会先校验完整目标 DTO，再保序规范化结构并调用真实预测服务；不得猜条件、模型或SFC候选 scope。TLC comparison/reverse 使用各自工具和 DTO。", schema_version="1.1", permission="prediction:create", service="priority", modes=frozenset({"business"}), builds=frozenset({build, "full", "universal", "universal-v1"}), argument_schema=prediction_schema),
        ToolSpec("get_prediction", "查询 prediction_id 的真实状态与结果；QUEUED/RUNNING 只表示进行中，只有真实 SUCCEEDED 且带结果时才能声称预测完成。", permission="prediction:read", service="priority", builds=frozenset({build, "full", "universal", "universal-v1"}), argument_schema={"type": "object", "required": ["prediction_id"], "properties": {"prediction_id": {"type": "string"}}, "additionalProperties": False}),
        ToolSpec("get_data_version_summary", "汇总已有数据版本", permission="data:read", service="priority", builds=frozenset({build, "full", "universal", "universal-v1"}), argument_schema={"type": "object", "properties": {"domain_id": {"type": "string"}}, "additionalProperties": False}),
        ToolSpec("get_prediction_record_summary", "汇总已有预测记录", permission="data:read", service="priority", builds=frozenset({build, "full", "universal", "universal-v1"}), argument_schema={"type": "object", "properties": {"chromatography_type": {"type": "string"}}, "additionalProperties": False}),
        ToolSpec("list_models", "查询当前项目已验证模型；相同 task_type 和上下文已有结果时不要重复查询。", permission="model:read", service="priority", builds=frozenset({build, "full", "universal", "universal-v1"}), argument_schema={"type": "object", "properties": {"task_type": {"type": "string"}}, "additionalProperties": False}),
        ToolSpec("get_model_card", "查询模型卡", permission="model:read", service="priority", builds=frozenset({build, "full", "universal", "universal-v1"}), argument_schema={"type": "object", "required": ["model_id"], "properties": {"model_id": {"type": "string"}}, "additionalProperties": False}),
        # Creating a DRAFT has no registration side effect.  Confirmation is
        # consumed by the model-center register transition after validation.
        ToolSpec("create_registration_draft", "引用已校验制品创建模型登记草稿", permission="model:register", service="priority", modes=frozenset({"business"}), builds=frozenset({build, "full", "universal", "universal-v1"}), argument_schema=registration_schema),
        ToolSpec("get_registration_draft", "查询模型登记草稿", permission="model:read", service="priority", builds=frozenset({build, "full", "universal", "universal-v1"}), argument_schema={"type": "object", "required": ["registration_id"], "properties": {"registration_id": {"type": "string"}}, "additionalProperties": False}),
    ]
    toolset = ToolSet(specs, build=build, mode=mode, permissions=permissions)
    if mode == "business" and {spec.name for spec in toolset.specs} != set(PRIORITY_TOOL_NAMES):
        raise RuntimeError("priority tool declaration and executable registry differ")
    return toolset


_PREDICTION_SCHEMA_TITLES = {
    "tlc": ("TlcPredictionCreate",),
    "cc": ("CcPredictionCreate",),
    "hplc": ("HplcPredictionCreate",),
    "chiral_lc": ("ChiralLcPredictionCreate",),
    "gc": ("GcPredictionCreate",),
    "sfc": ("SfcSingleColumnPredictionCreate", "SfcFullColumnPredictionCreate"),
}

_PREDICTION_SCHEMA_TOOL_IDS = {
    "tlc": {"TlcPredictionCreate": "single", "TlcConditionComparisonCreate": "condition_compare", "TlcReverseCreate": "reverse_solvent"},
    "cc": {"CcPredictionCreate": "single"},
    "hplc": {"HplcPredictionCreate": "single"},
    "chiral_lc": {"ChiralLcPredictionCreate": "single"},
    "gc": {"GcPredictionCreate": "single"},
    "sfc": {"SfcSingleColumnPredictionCreate": "single_column", "SfcFullColumnPredictionCreate": "full_column"},
}


def _prediction_create_arguments_schema(chromatography_type: str) -> dict[str, Any]:
    """Return only the real PredictionCreate branch requested by the model."""
    schema = TypeAdapter(PredictionCreate).json_schema()
    definitions = schema.get("$defs", {})
    branches = []
    selected_definitions: dict[str, Any] = {}
    pending: list[str] = []
    for title in _PREDICTION_SCHEMA_TITLES[chromatography_type]:
        if title not in definitions:
            continue
        branches.append(definitions[title])
        pending.append(title)
    while pending:
        name = pending.pop()
        node = selected_definitions.get(name) or definitions.get(name)
        if node is None or name in selected_definitions:
            continue
        selected_definitions[name] = node
        stack = [node]
        while stack:
            current = stack.pop()
            if isinstance(current, Mapping):
                ref = current.get("$ref")
                if isinstance(ref, str) and ref.startswith("#/$defs/"):
                    referenced = ref.rsplit("/", 1)[-1]
                    if referenced not in selected_definitions:
                        pending.append(referenced)
                stack.extend(current.values())
            elif isinstance(current, list):
                stack.extend(current)
    if not branches:
        return {}
    result: dict[str, Any] = {"$defs": selected_definitions}
    if len(branches) == 1:
        result.update(branches[0])
    else:
        result.update({"oneOf": branches, "title": f"{chromatography_type.upper()}PredictionCreate"})
    return result


def _raw_prediction_arguments_schema(chromatography_type: str | None = None) -> dict[str, Any]:
    """Expose original SMILES plus the matching DTO's business-input branch."""
    schema = TypeAdapter(PredictionCreate).json_schema()
    definitions = schema.get("$defs", {})
    branches: list[dict[str, Any]] = []
    type_titles = {
        "tlc": ("TlcPredictionCreate", "single"),
        "cc": ("CcPredictionCreate", "single"),
        "hplc": ("HplcPredictionCreate", "single"),
        "chiral_lc": ("ChiralLcPredictionCreate", "single"),
        "gc": ("GcPredictionCreate", "single"),
        "sfc": ("SfcSingleColumnPredictionCreate", "single_column"),
    }
    if chromatography_type is None or chromatography_type == "sfc":
        type_titles["sfc"] = ("SfcSingleColumnPredictionCreate", "single_column")
    if chromatography_type == "sfc":
        selected_types = ("sfc",)
    elif chromatography_type is None:
        selected_types = tuple(type_titles)
    else:
        selected_types = (chromatography_type,)

    for kind in selected_types:
        if kind == "sfc":
            title_tools = (("SfcSingleColumnPredictionCreate", "single_column"), ("SfcFullColumnPredictionCreate", "full_column"))
        elif kind == "tlc":
            title_tools = (("TlcPredictionCreate", "single"), ("TlcConditionComparisonCreate", "condition_compare"), ("TlcReverseCreate", "reverse_solvent"))
        else:
            title_tools = (type_titles[kind],)
        count = 2 if kind in {"cc", "hplc", "sfc"} else 1
        for title, tool in title_tools:
            dto = definitions.get(title)
            if not isinstance(dto, Mapping):
                continue
            input_schema = deepcopy(dto.get("properties", {}).get("input", {}))
            if tool in {"condition_compare", "reverse_solvent"}:
                excluded = {"project_id", "schema_version", "model_selection", "chromatography_type", "tool", "structure_ref", "structure_refs"}
                dto_properties = dto.get("properties", {})
                input_properties = {key: deepcopy(value) for key, value in dto_properties.items() if key not in excluded}
                dto_required = dto.get("required", [])
                input_schema = {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": input_properties,
                    "required": [key for key in dto_required if key not in excluded],
                }
            if kind == "hplc":
                hplc = deepcopy(definitions.get("HplcInput", {}))
                if isinstance(hplc.get("properties"), dict):
                    hplc["properties"].pop("model_input_smiles", None)
                if isinstance(hplc.get("required"), list):
                    hplc["required"] = [name for name in hplc["required"] if name != "model_input_smiles"]
                definitions["AssistantRawHplcInput"] = hplc
                input_schema = {"$ref": "#/$defs/AssistantRawHplcInput"}
            properties: dict[str, Any] = {
                "chromatography_type": {"const": kind, "type": "string"},
                "smiles": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 20_000},
                    "minItems": count,
                    "maxItems": count,
                    "description": "用户本轮提供的原始 SMILES，按模型输入槽位保持原字符和顺序。",
                },
                "input": input_schema,
                "model_selection": {"$ref": "#/$defs/ExactModelSelection"},
            }
            required = ["chromatography_type", "smiles", "input"]
            if kind in {"sfc", "tlc"} and tool != "single":
                properties["tool"] = {"const": tool, "type": "string"}
                required.append("tool")
            branches.append({"type": "object", "additionalProperties": False, "required": required, "properties": properties})
    return {"$defs": definitions, "oneOf": branches}


def _prediction_capability_guidance(value: Any, chromatography_type: Any) -> dict[str, Any]:
    """Keep the model-facing capability result useful and below runtime bounds.

    The full capability registry includes input and output schemas for every
    built type. Sending that list through the runtime's bounded serializer can
    discard all entries, so this adapter exposes a compact menu until the
    model identifies the requested type, then returns the exact create DTO
    branch and only verified READY model options for that type.
    """
    raw = _safe_mapping(value)
    rows = raw.get("capabilities", []) if isinstance(raw, Mapping) else raw
    if not isinstance(rows, list):
        return {"capabilities": rows, "next_step": "读取结果不是能力列表；如实说明当前无法确认能力。"}
    if any(not isinstance(row, Mapping) for row in rows):
        return raw if isinstance(raw, dict) else {"capabilities": rows}
    compact = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        models = row.get("models", [])
        ready_models = [
            model for model in models
            if isinstance(model, Mapping)
            and isinstance(model.get("health"), Mapping)
            and model["health"].get("status") == "READY"
        ] if isinstance(models, list) else []
        compact.append({
            "id": row.get("id"),
            "display_name": row.get("display_name"),
            "health": row.get("health"),
            "tool_ids": [tool.get("id") for tool in row.get("tools", []) if isinstance(tool, Mapping)] if isinstance(row.get("tools"), list) else [],
            "ready_models": [
                {key: model.get(key) for key in (
                    "id", "version", "task_type", "scope", "health",
                    "preprocessing_profile_id", "preprocessing_profile_sha256",
                ) if key in model}
                for model in ready_models
            ],
        })
    if not isinstance(chromatography_type, str) or not chromatography_type:
        return {
            "capabilities": compact,
            "next_step": "根据用户请求选定 chromatography_type 后再次调用本工具；不要重复空参数查询。",
        }
    requested = chromatography_type.strip().lower()
    selected = next((row for row in rows if isinstance(row, Mapping) and row.get("id") == requested), None)
    if selected is None:
        return {"requested_type": requested, "capabilities": compact, "next_step": "该类型未出现在已构建能力中；不要调用 create_prediction。"}
    selected_health = selected.get("health") if isinstance(selected.get("health"), Mapping) else {}
    models = selected.get("models", [])
    ready_models = [
        {key: model.get(key) for key in (
            "id", "version", "task_type", "scope", "health",
            "preprocessing_profile_id", "preprocessing_profile_sha256",
        ) if key in model}
        for model in models
        if isinstance(model, Mapping)
        and isinstance(model.get("health"), Mapping)
        and model["health"].get("status") == "READY"
    ] if isinstance(models, list) else []
    supported_tools = _PREDICTION_SCHEMA_TOOL_IDS.get(requested)
    if supported_tools is None:
        return {
            "requested_type": requested,
            "next_step": "该类型虽在能力列表中，但助手没有对应的预测 schema；不要调用 create_prediction。",
        }
    schema_tool_ids = set(supported_tools.values())
    ready_tool_versions = {
        tool["id"]: tool["schema_version"]
        for tool in selected.get("tools", [])
        if isinstance(tool, Mapping)
        and tool.get("id") in schema_tool_ids
        and isinstance(tool.get("schema_version"), str)
        and tool["schema_version"].strip()
        and isinstance(tool.get("health"), Mapping)
        and tool["health"].get("status") == "READY"
    } if isinstance(selected.get("tools"), list) else {}
    if selected_health.get("status") != "READY" or not ready_models or not ready_tool_versions:
        return {
            "requested_type": requested,
            "capability": {"id": requested, "display_name": selected.get("display_name"), "health": selected_health},
            "ready_models": ready_models,
            "next_step": "当前类型、预测工具或模型没有 READY 状态；不要调用 create_prediction，向用户说明不可用。",
        }
    single_schema_version = ready_tool_versions.get("single")
    if single_schema_version is None and len(set(ready_tool_versions.values())) == 1:
        single_schema_version = next(iter(ready_tool_versions.values()))
    # The full request schema is already on the ToolSpec; keep this metadata view compact.
    create_prediction = {
        "tool_name": "create_prediction",
        "tool_schema_versions": ready_tool_versions,
    }
    if single_schema_version is not None:
        create_prediction["schema_version"] = single_schema_version
    return {
        "requested_type": requested,
        "capability": {
            "id": requested,
            "display_name": selected.get("display_name"),
            "health": selected_health,
            "tool_ids": [tool.get("id") for tool in selected.get("tools", []) if isinstance(tool, Mapping)] if isinstance(selected.get("tools"), list) else [],
        },
        "ready_models": ready_models,
        "create_prediction": create_prediction,
            "next_step": "用本轮原始有序 smiles 和完整业务 input 调用 create_prediction。恰有一个 READY 模型时可省略 model_selection；多个 READY 选项时先让用户选择并传 exact model_id/model_version/profile pair。SFC 必须让用户明确选择 tool 与 input.candidate_scope_id；服务端会绑定该 scope 到兼容模型。",
    }


class PriorityAssistantService:
    """Adapter used by priority assistant tools and their HTTP facade."""

    def __init__(self, *, prediction: object | None = None, capabilities: object | None = None, structures: object | None = None, data_versions: object | None = None, records: object | None = None, models: object | None = None, artifacts: object | None = None, confirmation: object | None = None, audit: object | None = None) -> None:
        self.prediction, self.capabilities_port, self.structures, self.data_versions, self.records, self.models = prediction, capabilities, structures, data_versions, records, models
        self.artifacts, self.confirmation, self.audit = artifacts, confirmation, audit

    @staticmethod
    def _project(ctx: Any, project_id: str) -> None:
        actor = getattr(ctx, "actor", ctx)
        if not isinstance(actor, ActorContext) or not actor.can_access_project(project_id):
            raise AssistantError(404, "RESOURCE_NOT_FOUND", "项目不存在或当前主体不可见")

    def capabilities(self, ctx: Any = None):
        value = self.capabilities_port or self.prediction
        registry = getattr(value, "capabilities", None) or getattr(value, "capability_registry", None)
        if callable(registry):
            return registry()
        if value is not None and callable(getattr(value, "capabilities", None)):
            return value.capabilities()
        raise unavailable("预测能力端口未配置")

    def create_prediction(self, ctx: Any, request: PredictionCreate, *, idempotency_key: str):
        if self.prediction is None or not callable(getattr(self.prediction, "create", None)):
            raise unavailable("预测服务未配置")
        return self.prediction.create(ctx, request, idempotency_key=idempotency_key)

    def normalize_structure(self, ctx: Any, request: NormalizeStructureRequest, *, idempotency_key: str) -> NormalizedStructureResult:
        project_id = request.project_id or getattr(ctx, "server_project_id", None) or getattr(ctx, "project_id", None)
        if not project_id:
            raise AssistantError(404, "RESOURCE_NOT_FOUND", "项目不存在或当前主体不可见")
        self._project(ctx, project_id)
        actor = getattr(ctx, "actor", ctx)
        if not actor.can("prediction:create"):
            raise AssistantError(403, "PERMISSION_DENIED", "缺少 prediction:create 权限")
        if self.structures is None or not callable(getattr(self.structures, "normalize", None)):
            raise unavailable("结构持久化服务未配置")
        bound = request.model_copy(update={"project_id": project_id})
        value = self.structures.normalize(ctx, bound, idempotency_key=idempotency_key)
        data = getattr(value, "data", value)
        structure_ref = ResourceRef(type="structure", id=str(data.id))
        return NormalizedStructureResult(
            structure_ref=structure_ref,
            resource_ref=structure_ref,
            canonical_smiles=data.canonical_smiles,
            snapshot_hash=data.snapshot_hash,
            has_stereochemistry=data.has_stereochemistry,
            stereochemistry_status=data.stereochemistry_status,
            warnings=list(data.stereochemistry_warnings),
        )

    def get_prediction(self, ctx: Any, prediction_id: str):
        if self.prediction is None or not callable(getattr(self.prediction, "get", None)):
            raise unavailable("预测读取服务未配置")
        return self.prediction.get(ctx, prediction_id)

    def data_version_summary(self, ctx: Any, project_id: str, *, domain_id: str | None = None):
        self._project(ctx, project_id)
        if self.data_versions is None or not callable(getattr(self.data_versions, "list", None)):
            raise unavailable("数据版本读取服务未配置")
        if domain_id is None:
            raise APIError(422, "VALIDATION_FAILED", "domain_id 是数据版本摘要的必要筛选条件")
        return summarize_data_versions(self.data_versions.list(project_id, domain_id), project_id=project_id, domain_id=domain_id)

    def prediction_record_summary(self, ctx: Any, project_id: str, *, chromatography_type: str | None = None):
        self._project(ctx, project_id)
        if self.records is None or not callable(getattr(self.records, "list", None)):
            raise unavailable("预测记录读取服务未配置")
        values = self.records.list(project_id, chromatography_type=chromatography_type, limit=201)
        return summarize_prediction_records(values, project_id=project_id, chromatography_type=chromatography_type)

    def list_models(self, ctx: Any, project_id: str, *, task_type: str | None = None):
        self._project(ctx, project_id)
        if self.models is None or not callable(getattr(self.models, "list_models", None)):
            raise unavailable("模型读取服务未配置")
        actor = getattr(ctx, "actor", ctx)
        return [value for value in self.models.list_models(project_id, task_type=task_type) if self._model_artifact_is_verified(value, actor=actor, project_id=project_id)]

    def _model_artifact_is_verified(self, value: Any, *, actor: Any, project_id: str | None = None) -> bool:
        model_project_id = _value(value, "project_id")
        expected_project_id = project_id or model_project_id
        if not expected_project_id or model_project_id != expected_project_id:
            return False
        artifact_id = _value(value, "artifact_id")
        if not artifact_id or self.artifacts is None or not callable(getattr(self.artifacts, "get_artifact", None)):
            return False
        try:
            artifact = self.artifacts.get_artifact(str(artifact_id), actor)
        except Exception:
            return False
        return (
            _value(artifact, "kind") == "model_package"
            and _value(artifact, "project_id") == expected_project_id
            and _value(artifact, "status") == "AVAILABLE"
            and _value(artifact, "scan_status") == "PASSED"
        )

    def _artifact_is_verified(self, artifact_id: str | None, *, actor: Any, project_id: str) -> bool:
        if not artifact_id or self.artifacts is None or not callable(getattr(self.artifacts, "get_artifact", None)):
            return False
        try:
            artifact = self.artifacts.get_artifact(str(artifact_id), actor)
        except Exception:
            return False
        return (
            _value(artifact, "kind") == "model_package"
            and _value(artifact, "project_id") == project_id
            and _value(artifact, "status") == "AVAILABLE"
            and _value(artifact, "scan_status") == "PASSED"
        )

    def get_model_card(self, ctx: Any, project_id: str, model_id: str):
        self._project(ctx, project_id)
        if self.models is None or not callable(getattr(self.models, "get_model", None)):
            raise unavailable("模型读取服务未配置")
        value = self.models.get_model(project_id, model_id)
        model = getattr(value, "model", value)
        if not self._model_artifact_is_verified(model, actor=getattr(ctx, "actor", ctx), project_id=project_id):
            raise AssistantError(409, "ARTIFACT_NOT_VERIFIED", "模型制品必须处于 AVAILABLE 且扫描 PASSED")
        return getattr(model, "model_card", model)

    def create_registration_draft(self, ctx: Any, request: ModelRegistrationCreate, *, idempotency_key: str, confirmation_id: str | None = None):
        self._project(ctx, request.project_id)
        actor = getattr(ctx, "actor", ctx)
        if self.artifacts is None or not callable(getattr(self.artifacts, "get_artifact", None)):
            raise unavailable("制品读取服务未配置")
        artifact_id = getattr(request.source, "artifact_id", None)
        if not artifact_id:
            raise AssistantError(422, "VALIDATION_FAILED", "登记草稿必须引用 artifact_id")
        if not self._artifact_is_verified(artifact_id, actor=actor, project_id=request.project_id):
            raise AssistantError(409, "ARTIFACT_NOT_VERIFIED", "制品必须先通过可用性和扫描校验")
        if self.models is None or not callable(getattr(self.models, "create_registration", None)):
            raise unavailable("模型中心服务未配置")
        # Resolve a durable idempotency replay before touching the registration
        # side effect. Draft creation has no registration/publish side effect,
        # so API §14.1 confirmation is deferred to the register operation.
        replay_lookup = getattr(self.models, "get_registration_replay", None)
        if callable(replay_lookup):
            replay = replay_lookup(request, idempotency_key=idempotency_key, actor=actor)
            if replay is not None:
                return replay
        value = self.models.create_registration(request, idempotency_key=idempotency_key, actor=actor)
        if getattr(value, "_idempotency_replayed", False):
            return value
        self._record_draft_audit(actor=actor, project_id=request.project_id, registration_id=value.id, confirmation_id=None, artifact_id=artifact_id, config_hash=value.config_hash)
        return value

    def _record_draft_audit(self, *, actor: Any, project_id: str, registration_id: str, confirmation_id: str | None, artifact_id: str, config_hash: str) -> None:
        details = {"status": "DRAFT", "artifact_id": artifact_id}
        if self.audit is not None and callable(getattr(self.audit, "record", None)):
            try:
                self.audit.record(actor=actor, interaction="assistant", action="model_registration.draft", project_id=project_id, resource_id=registration_id, result="SUCCEEDED", confirmation_id=confirmation_id, after=details)
                return
            except TypeError:
                pass
        repository = getattr(self.models, "repository", None)
        record_audit = getattr(repository, "record_audit", None)
        if callable(record_audit):
            record_audit(AuditEvent(id=new_id("audit"), project_id=project_id, actor_id=actor.actor_id, interaction="assistant", action="model_registration.draft", resource_ref=ResourceRef(type="model_registration", id=registration_id), result="SUCCEEDED", after_summary=details, confirmation_id=confirmation_id, config_hash=config_hash, timestamp=now_utc()))

    def get_registration_draft(self, ctx: Any, project_id: str, registration_id: str):
        self._project(ctx, project_id)
        if self.models is None or not callable(getattr(self.models, "get_registration", None)):
            raise unavailable("模型中心服务未配置")
        try:
            value = self.models.get_registration(project_id, registration_id)
        except ModelCenterError as exc:
            raise _registration_read_error(exc) from exc
        if value is None:
            raise AssistantError(404, "RESOURCE_NOT_FOUND", "登记草稿不存在或当前主体不可见")
        source = _value(value, "source")
        artifact_id = _value(source, "artifact_id")
        if not self._artifact_is_verified(artifact_id, actor=getattr(ctx, "actor", ctx), project_id=project_id):
            raise AssistantError(409, "ARTIFACT_NOT_VERIFIED", "登记草稿制品必须处于 AVAILABLE 且扫描 PASSED")
        return value


class PriorityAssistantToolService:
    """Runtime tool port delegating to the request-scoped priority service."""

    def __init__(self, service: PriorityAssistantService, *, project_id: str | None = None, artifact_id: str | None = None) -> None:
        self.service, self.project_id, self.artifact_id = service, project_id, artifact_id

    @classmethod
    def executable_tool_names(cls) -> frozenset[str]:
        """Names served by this single request-scoped priority registry."""

        return frozenset(PRIORITY_TOOL_NAMES)

    def _project_id(self, ctx: Any) -> str:
        project_id = self.project_id or getattr(ctx, "server_project_id", None) or getattr(ctx, "project_id", None)
        if not project_id:
            raise AssistantError(404, "RESOURCE_NOT_FOUND", "项目不存在或当前主体不可见")
        self.service._project(ctx, project_id)
        return project_id

    def _prediction_request(self, arguments: Mapping[str, Any], ctx: Any) -> PredictionCreate:
        request = TypeAdapter(PredictionCreate).validate_python(arguments)
        project_id = self._project_id(ctx)
        if request.project_id is not None and request.project_id != project_id:
            raise AssistantError(403, "RESOURCE_NOT_FOUND", "工具参数超出服务端已解析的项目范围")
        return request.model_copy(update={"project_id": project_id})

    @staticmethod
    def _exact_selection(model: Mapping[str, Any]) -> dict[str, Any]:
        selection = {"mode": "exact", "model_id": model.get("id"), "model_version": model.get("version")}
        for key in ("preprocessing_profile_id", "preprocessing_profile_sha256"):
            if model.get(key) is not None:
                selection[key] = model[key]
        return selection

    def _raw_prediction_request(
        self,
        arguments: Mapping[str, Any],
        ctx: Any,
        *,
        idempotency_key: str,
        normalize: bool,
    ) -> PredictionCreate:
        raw = AssistantRawPredictionArguments.checked(arguments)
        project_id = self._project_id(ctx)
        guidance = _prediction_capability_guidance(self.service.capabilities(ctx), raw.chromatography_type)
        create = guidance.get("create_prediction")
        tool = raw.tool or "single"
        schema_version = create.get("tool_schema_versions", {}).get(tool) if isinstance(create, Mapping) else None
        models = guidance.get("ready_models", []) if isinstance(guidance, Mapping) else []
        if raw.chromatography_type == "sfc":
            expected_scope = "single_column" if tool == "single_column" else "all_column"
            models = [model for model in models if isinstance(model, Mapping) and model.get("scope") == expected_scope]
        if not isinstance(schema_version, str) or not schema_version or not models:
            raise AssistantError(409, "PREDICTION_UNAVAILABLE", "当前色谱类型或所选工具没有 READY 模型", retryable=True)

        selections = [(model, self._exact_selection(model)) for model in models if isinstance(model, Mapping)]
        if raw.model_selection is None:
            if len(selections) != 1:
                choices = [{key: value for key, value in selection.items() if value is not None} for _, selection in selections]
                raise AssistantError(409, "PREDICTION_MODEL_SELECTION_REQUIRED", "READY 模型有多个，请先让用户选择：" + json.dumps(choices, ensure_ascii=False, separators=(",", ":")))
            model, selection = selections[0]
        else:
            requested_selection = raw.model_selection.model_dump(mode="json", exclude_none=True)
            matches = [(model, selection) for model, selection in selections if selection == requested_selection]
            if len(matches) != 1:
                raise AssistantError(409, "PREDICTION_MODEL_UNAVAILABLE", "指定模型、版本或预处理 profile 与当前项目 READY 选项不匹配")
            model, selection = matches[0]

        values = dict(raw.input)
        if raw.chromatography_type == "hplc":
            if "model_input_smiles" in values:
                raise AssistantError(422, "VALIDATION_FAILED", "HPLC 原始 SMILES 只能通过顶层有序 smiles 字段提交")
            values["model_input_smiles"] = list(raw.smiles)
        body: dict[str, Any] = {
            "project_id": project_id,
            "chromatography_type": raw.chromatography_type,
            "tool": tool,
            "schema_version": schema_version,
            "model_selection": selection,
            "structure_refs": [f"assistant-pending-{index}" for index in range(len(raw.smiles))],
        }
        if tool in {"condition_compare", "reverse_solvent"}:
            body.update(values)
            body["structure_ref"] = body["structure_refs"][0]
        else:
            body["input"] = values
        request = self._prediction_request(body, ctx)
        if not normalize:
            return request

        refs = []
        for index, smiles in enumerate(raw.smiles):
            normalized = self.service.normalize_structure(
                ctx,
                NormalizeStructureRequest(
                    project_id=project_id,
                    input_type="smiles",
                    value=smiles,
                    preserve_stereochemistry=True,
                ),
                idempotency_key=f"{idempotency_key}:structure:{index}",
            )
            refs.append(normalized.structure_ref.id)
        body["structure_refs"] = refs
        if tool in {"condition_compare", "reverse_solvent"}:
            body["structure_ref"] = refs[0]
        return self._prediction_request(body, ctx)

    def _validate_registered(self, tool_name: str) -> None:
        if tool_name not in self.executable_tool_names():
            raise AssistantError(400, "VALIDATION_FAILED", f"优先助手工具未注册: {tool_name}")

    def _request(self, arguments: Mapping[str, Any], ctx: Any = None) -> ModelRegistrationCreate:
        request = ModelRegistrationCreate.model_validate(arguments)
        # HTTP binds these values at construction.  A worker binds them on
        # the trusted run context, never from provider arguments alone.
        project_id = self.project_id or getattr(ctx, "server_project_id", None)
        artifact_id = self.artifact_id or getattr(ctx, "server_artifact_id", None)
        if not project_id or not artifact_id or request.project_id != project_id or request.source.artifact_id != artifact_id:
            raise AssistantError(403, "RESOURCE_NOT_FOUND", "工具参数超出服务端已解析的项目或制品范围")
        return request

    def validate(self, ctx: Any, *, tool_name: str, arguments: Mapping[str, Any], resource_ref: Any = None) -> None:
        self._validate_registered(tool_name)
        if tool_name == "normalize_structure":
            request = TypeAdapter(NormalizeStructureRequest).validate_python(arguments)
            project_id = self._project_id(ctx)
            if request.project_id is not None and request.project_id != project_id:
                raise AssistantError(403, "RESOURCE_NOT_FOUND", "工具参数超出服务端已解析的项目范围")
        elif tool_name == "create_registration_draft":
            self._request(arguments, ctx)
        elif tool_name == "create_prediction":
            if "smiles" in arguments:
                self._raw_prediction_request(arguments, ctx, idempotency_key="validation-only", normalize=False)
            else:
                raise AssistantError(409, "RECOVERY_STATE_INVALID", "恢复的预测参数缺少原始 SMILES；请先查询已有预测任务和结果，确认未受理后再补齐原始结构提交")
        elif tool_name in {"get_prediction", "get_model_card", "get_registration_draft"}:
            self._project_id(ctx)
        elif tool_name in {"get_data_version_summary", "get_prediction_record_summary", "list_models"}:
            self._project_id(ctx)
        elif tool_name == "get_prediction_capabilities":
            if set(arguments) - {"chromatography_type"}:
                raise AssistantError(400, "VALIDATION_FAILED", "工具参数包含未声明字段")
            chromatography_type = arguments.get("chromatography_type")
            if chromatography_type is not None and chromatography_type not in _PREDICTION_SCHEMA_TITLES:
                raise AssistantError(400, "VALIDATION_FAILED", "色谱类型不在助手支持范围内")

    def execute(self, ctx: Any, *, tool_name: str, arguments: Mapping[str, Any], resource_ref: Any = None, confirmation_id: str | None = None):
        tool_call_id = getattr(ctx, "tool_call_id", None)
        run_id = getattr(ctx, "agent_run_id", None) or "unknown"
        if tool_call_id:
            # Keep the key bounded and opaque.  In particular, never derive it
            # from provider arguments (which may contain raw SMILES).
            digest = hashlib.sha256(f"{run_id}|{tool_call_id}".encode("utf-8")).hexdigest()
            idempotency_key = f"assistant-tool:{digest}"
        else:
            idempotency_key = getattr(ctx, "idempotency_key", None) or f"assistant-run:{run_id}"
        self._validate_registered(tool_name)
        if tool_name == "normalize_structure":
            request = TypeAdapter(NormalizeStructureRequest).validate_python(arguments)
            project_id = self._project_id(ctx)
            if request.project_id is not None and request.project_id != project_id:
                raise AssistantError(403, "RESOURCE_NOT_FOUND", "工具参数超出服务端已解析的项目范围")
            return self.service.normalize_structure(ctx, request, idempotency_key=idempotency_key)
        if tool_name == "get_prediction_capabilities":
            return _prediction_capability_guidance(
                self.service.capabilities(ctx),
                arguments.get("chromatography_type"),
            )
        if tool_name == "create_prediction":
            if "smiles" in arguments:
                request = self._raw_prediction_request(arguments, ctx, idempotency_key=idempotency_key, normalize=True)
            else:
                raise AssistantError(409, "RECOVERY_STATE_INVALID", "恢复的预测参数缺少原始 SMILES；请先查询已有预测任务和结果，确认未受理后再补齐原始结构提交")
            return self.service.create_prediction(ctx, request, idempotency_key=idempotency_key)
        if tool_name == "get_prediction":
            return self.service.get_prediction(ctx, str(arguments["prediction_id"]))
        project_id = self._project_id(ctx)
        if tool_name == "get_data_version_summary":
            return self.service.data_version_summary(ctx, project_id, domain_id=arguments.get("domain_id"))
        if tool_name == "get_prediction_record_summary":
            return self.service.prediction_record_summary(ctx, project_id, chromatography_type=arguments.get("chromatography_type"))
        if tool_name == "list_models":
            return self.service.list_models(ctx, project_id, task_type=arguments.get("task_type"))
        if tool_name == "get_model_card":
            return self.service.get_model_card(ctx, project_id, str(arguments["model_id"]))
        if tool_name == "create_registration_draft":
            request = self._request(arguments, ctx)
            value = self.service.create_registration_draft(ctx, request, idempotency_key=idempotency_key, confirmation_id=confirmation_id)
            return _registration_tool_value(value)
        if tool_name == "get_registration_draft":
            value = self.service.get_registration_draft(ctx, project_id, str(arguments["registration_id"]))
            return _registration_tool_value(value)
        raise AssistantError(400, "VALIDATION_FAILED", f"优先助手工具未注册: {tool_name}")


def priority_openapi_fragment() -> dict[str, Any]:
    """Generate candidate schemas from FastAPI/Pydantic source DTOs."""
    return {
        "schemas": {
            "PredictionCreate": TypeAdapter(PredictionCreate).json_schema(),
            "ModelRegistrationCreate": ModelRegistrationCreate.model_json_schema(),
            "DataVersionSummary": DataVersionSummary.model_json_schema(),
            "PredictionRecordSummary": PredictionRecordSummary.model_json_schema(),
            "NormalizeStructureRequest": NormalizeStructureRequest.model_json_schema(),
            "NormalizedStructureResult": NormalizedStructureResult.model_json_schema(),
        }
    }


def priority_fastapi_openapi() -> dict[str, Any]:
    """Build the actual FastAPI route fragment used by the app."""
    from fastapi import FastAPI

    from .priority_router import create_priority_router

    app = FastAPI(title="priority-assistant-evidence")
    app.include_router(create_priority_router(), prefix="/api/v1")
    schema = app.openapi()
    return {"openapi": schema["openapi"], "paths": {key: value for key, value in schema["paths"].items() if key.startswith("/api/v1/assistant/priority")}, "components": schema.get("components", {})}


def write_priority_evidence(directory: str | os.PathLike[str], *, readiness: ProviderReadiness, probe: ProviderProbeEvidence | None = None) -> dict[str, str]:
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    values = {"provider-readiness.json": readiness.model_dump(mode="json"), "openapi-fragment.json": priority_openapi_fragment(), "fastapi-openapi.json": priority_fastapi_openapi()}
    if probe is not None:
        values["provider-probe.json"] = probe.model_dump(mode="json")
    hashes: dict[str, str] = {}
    for name, value in values.items():
        path = root / name
        raw = (_canonical(value) + "\n").encode("utf-8")
        path.write_bytes(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    (root / "SHA256.json").write_text(json.dumps(hashes, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return hashes


__all__ = ["DataVersionSummary", "NormalizedStructureResult", "NormalizeStructureRequest", "NumericStat", "PredictionRecordSummary", "PRIORITY_TOOL_NAMES", "PriorityAssistantService", "PriorityAssistantToolService", "PriorityOperationContext", "PriorityToolsView", "ProviderConfigCheck", "ProviderProbeEvidence", "ProviderReadiness", "SummarySource", "inspect_provider_config", "priority_fastapi_openapi", "priority_openapi_fragment", "priority_toolset", "probe_configured_provider", "probe_provider", "provider_config_revision", "resolve_runtime_config", "summarize_data_versions", "summarize_prediction_records", "write_priority_evidence"]
