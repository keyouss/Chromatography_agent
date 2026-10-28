"""Allowlisted assistant tools.

Tools are deliberately boring closures around injected application-service
ports.  They do not import a module repository and they have no shell,
network, SQL, Python or confirmation-management escape hatch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import inspect
import json
import math
import re
from threading import RLock
from typing import Any, Callable, Mapping, Protocol

from chrom_platform.core.errors import APIError

from ..errors import AssistantError, unavailable


_FORBIDDEN = frozenset({
    "confirmation:create", "approve", "publish", "rollback", "shell", "exec",
    "eval", "python", "sql", "http fetch", "http_fetch", "http.fetch",
    "file_write", "filesystem", "subprocess",
})
_SECRET_PARTS = frozenset({"password", "secret", "token", "credential", "authorization", "api_key", "apikey"})


def _assistant_error_from_api(error: APIError) -> AssistantError:
    """Preserve a formal application-service error at the assistant boundary."""
    payload = error.payload
    # Application-service details are not trusted for the assistant public
    # boundary.  The stable code/message/retryability are sufficient for the
    # tool lifecycle and are already the public error contract.
    return AssistantError(
        error.status_code,
        payload.code,
        payload.message,
        retryable=payload.retryable,
    )


def _raise_on_error_result(value: Any) -> Any:
    """Reject an error envelope returned as a business result.

    A prediction record may legitimately contain ``status_counts`` with an
    ``INTERNAL_ERROR`` domain status; that nested summary is not an execution
    error.  Only a top-level ``error`` member is an operation failure.
    """
    if isinstance(value, Mapping):
        error = value.get("error")
        if error not in (None, {}, ""):
            if isinstance(error, Mapping):
                code = error.get("code") if isinstance(error.get("code"), str) else "BUSINESS_SERVICE_FAILED"
                message = error.get("message") if isinstance(error.get("message"), str) else "业务服务返回失败结果"
                retryable = error.get("retryable") if isinstance(error.get("retryable"), bool) else False
            else:
                code, message, retryable = "BUSINESS_SERVICE_FAILED", "业务服务返回失败结果", False
            raise AssistantError(502, code[:96], message[:2000], retryable=retryable)
    return value


def _forbidden_name(name: str) -> bool:
    normalized = name.strip().lower().replace("_", " ")
    parts = set(re.split(r"[^a-z0-9]+", normalized))
    for item in _FORBIDDEN:
        if item in {"http fetch", "http_fetch", "http.fetch", "confirmation:create"}:
            if item.replace("_", " ").replace(".", " ") in normalized or normalized == item:
                return True
        elif normalized == item or item in parts:
            return True
    return False


def _canonical(value: Any) -> str:
    return json.dumps(_plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump(mode="json"))
    if hasattr(value, "as_dict"):
        return _plain(value.as_dict())
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, set):
        return sorted((_plain(v) for v in value), key=repr)
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AssistantError(400, "VALIDATION_FAILED", "工具参数包含非法数值")
        return value
    return str(value)


def normalize_arguments(arguments: Mapping[str, Any] | None) -> dict[str, Any]:
    if arguments is None:
        return {}
    if not isinstance(arguments, Mapping):
        raise AssistantError(400, "VALIDATION_FAILED", "工具参数必须是对象")
    value = _plain(arguments)
    assert isinstance(value, dict)
    return value


def _safe_key_part(value: str) -> str:
    return re.sub(r"[^a-z0-9_.:-]", "_", value.lower())


def revision_hash(target_revision: Any) -> str:
    """Hash the server-resolved target revision, never a client result."""

    return hashlib.sha256(_canonical(target_revision).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    schema_version: str = "1.0"
    permission: str = "assistant:use"
    service: str = "*"
    modes: frozenset[str] = frozenset({"knowledge", "business"})
    builds: frozenset[str] = frozenset({"full", "universal", "universal-v1", "minimal-structure-v1"})
    requires_confirmation: bool = False
    action: str | None = None
    argument_schema: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name or _forbidden_name(self.name):
            raise ValueError(f"forbidden assistant tool: {self.name!r}")
        if self.requires_confirmation and not self.action:
            raise ValueError("confirmation tool requires an action")

    def visible(self, *, build: str, mode: str, permissions: set[str] | frozenset[str]) -> bool:
        return (build in self.builds or "*" in self.builds) and mode in self.modes and ("*" in permissions or self.permission in permissions)

    def public_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "schema_version": self.schema_version,
            "permission": self.permission,
            "parameters": _plain(self.argument_schema),
            "requires_confirmation": self.requires_confirmation,
        }


@dataclass(frozen=True, slots=True)
class ToolPending:
    tool_name: str
    action: str
    normalized_args: Mapping[str, Any]
    target_revision_hash: str
    resource_ref: Any = None
    revision: int | None = None
    config_hash: str | None = None
    status: str = "WAITING_CONFIRMATION"


@dataclass(frozen=True, slots=True)
class ToolExecution:
    tool_name: str
    operation_key: str
    result: Any = None
    replayed: bool = False
    status: str = "SUCCEEDED"


class ToolExecutionLedger:
    """In-memory reference implementation of the server execution ledger."""

    is_test_only = True

    def __init__(self) -> None:
        self._lock = RLock()
        self._records: dict[str, ToolExecution] = {}

    @staticmethod
    def operation_key(*, tool_name: str, normalized_args: Mapping[str, Any], target_revision: Any, run_id: str | None = None) -> str:
        payload = {"run_id": run_id or "", "tool": tool_name, "args": normalize_arguments(normalized_args), "target_revision_hash": revision_hash(target_revision)}
        return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()

    def get(self, operation_key: str) -> ToolExecution | None:
        with self._lock:
            return self._records.get(operation_key)

    def put(self, operation_key: str, value: ToolExecution) -> ToolExecution:
        with self._lock:
            return self._records.setdefault(operation_key, value)

    def execute(self, *, tool_name: str, normalized_args: Mapping[str, Any], target_revision: Any, fn: Callable[[], Any], run_id: str | None = None) -> ToolExecution:
        key = self.operation_key(tool_name=tool_name, normalized_args=normalized_args, target_revision=target_revision, run_id=run_id)
        with self._lock:
            old = self._records.get(key)
            if old is not None:
                return ToolExecution(old.tool_name, old.operation_key, old.result, True, old.status)
            # The lock serializes duplicate side effects in this test-only
            # implementation. Production uses a durable unique operation key
            # and records UNKNOWN when a side-effecting call fails after it
            # may have reached the business service.
            try:
                result = fn()
            except BaseException:
                self._records[key] = ToolExecution(tool_name, key, None, False, "UNKNOWN")
                raise
            value = ToolExecution(tool_name, key, result)
            self._records[key] = value
            return value

    run = execute


InMemoryToolExecutionLedger = ToolExecutionLedger


class ToolAuthorization(Protocol):
    def authorize(self, ctx: object, *, tool_name: str, permission: str) -> None: ...


class BoundTool:
    def __init__(self, spec: ToolSpec, service: object, *, ledger: ToolExecutionLedger | object | None = None, authorization: ToolAuthorization | None = None) -> None:
        self.spec, self.service, self.ledger, self.authorization = spec, service, ledger, authorization

    def __call__(self, ctx: object, arguments: Mapping[str, Any] | None = None, *, target_revision: Any = None, confirmation_id: str | None = None, resource_ref: Any = None, run_id: str | None = None) -> Any:
        self._authorize(ctx)
        args = normalize_arguments(arguments)
        if self.ledger is not None:
            key_builder = getattr(self.ledger, "operation_key", None)
            getter = getattr(self.ledger, "get", None)
            if callable(key_builder) and callable(getter):
                key_kwargs = {"tool_name": self.spec.name, "normalized_args": args, "target_revision": target_revision}
                if run_id is not None:
                    key_kwargs["run_id"] = run_id
                key = key_builder(**key_kwargs)
                old = getter(key)
                if old is not None:
                    return ToolExecution(self.spec.name, key, getattr(old, "result", old), True, getattr(old, "status", "SUCCEEDED"))
        validate = getattr(self.service, "validate", None)
        execute = getattr(self.service, "execute", None)
        if not callable(validate) or not callable(execute):
            raise unavailable(f"工具 {self.spec.name} 的 ApplicationService 端口未配置")
        _validate_schema(args, self.spec.argument_schema)
        try:
            validate(ctx, tool_name=self.spec.name, arguments=args, resource_ref=resource_ref)
        except AssistantError:
            raise
        except APIError as exc:
            raise _assistant_error_from_api(exc) from exc
        except Exception as exc:  # service details may contain SQL/path/secret
            raise AssistantError(400, "VALIDATION_FAILED", "工具参数校验失败") from exc
        if self.spec.requires_confirmation and not confirmation_id:
            return ToolPending(self.spec.name, self.spec.action or "", args, revision_hash(target_revision), resource_ref=resource_ref, revision=_revision(target_revision), config_hash=_config_hash(target_revision))

        def invoke() -> Any:
            kwargs = {"tool_name": self.spec.name, "arguments": args, "resource_ref": resource_ref, "confirmation_id": confirmation_id}
            try:
                signature = inspect.signature(execute)
            except (TypeError, ValueError):
                signature = None
            if signature is not None and not any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
                kwargs = {key: value for key, value in kwargs.items() if key in signature.parameters}
            try:
                return _raise_on_error_result(execute(ctx, **kwargs))
            except AssistantError:
                raise
            except APIError as exc:
                raise _assistant_error_from_api(exc) from exc
            except Exception as exc:
                raise AssistantError(502, "TOOL_EXECUTION_FAILED", "工具执行失败", retryable=True) from exc

        if self.ledger is None:
            result = invoke()
            return ToolExecution(self.spec.name, "", result)
        runner = getattr(self.ledger, "execute", None) or getattr(self.ledger, "run", None)
        if not callable(runner):
            raise unavailable("工具执行账本未配置")
        kwargs = {"tool_name": self.spec.name, "normalized_args": args, "target_revision": target_revision, "fn": invoke}
        if run_id is not None:
            kwargs["run_id"] = run_id
        try:
            signature = inspect.signature(runner)
        except (TypeError, ValueError):
            signature = None
        if signature is not None and not any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
            kwargs = {key: value for key, value in kwargs.items() if key in signature.parameters}
        return runner(**kwargs)

    def _authorize(self, ctx: object) -> None:
        if self.authorization is not None:
            self.authorization.authorize(ctx, tool_name=self.spec.name, permission=self.spec.permission)
            return
        actor = getattr(ctx, "actor", ctx)
        can = getattr(actor, "can", None)
        if callable(can) and not can(self.spec.permission):
            raise AssistantError(403, "PERMISSION_DENIED", "当前主体无权使用该工具")


class ToolSet:
    def __init__(self, specs: Mapping[str, ToolSpec] | list[ToolSpec] | tuple[ToolSpec, ...], *, build: str = "full", mode: str = "business", permissions: set[str] | frozenset[str] = frozenset({"*"})) -> None:
        self.build, self.mode, self.permissions = build, mode, frozenset(permissions)
        source = specs.items() if isinstance(specs, Mapping) else ((spec.name, spec) for spec in specs)
        self._specs = {name: spec for name, spec in source if spec.visible(build=build, mode=mode, permissions=self.permissions)}
        self._assert_safe()

    def _assert_safe(self) -> None:
        for name in self._specs:
            if _forbidden_name(name):
                raise AssistantError(403, "PERMISSION_DENIED", "工具不在允许列表中")

    @property
    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(self._specs.values())

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        return [item.public_schema() for item in self._specs.values()]

    def bind(self, services: Mapping[str, object], *, ledger: ToolExecutionLedger | object | None = None, authorization: ToolAuthorization | None = None) -> dict[str, BoundTool]:
        result: dict[str, BoundTool] = {}
        for name, spec in self._specs.items():
            service = services.get(spec.service) or services.get(name) or services.get("*")
            if service is not None:
                result[name] = BoundTool(spec, service, ledger=ledger, authorization=authorization)
        return result


def _revision(value: Any) -> int | None:
    if isinstance(value, Mapping):
        candidate = value.get("revision")
    else:
        candidate = getattr(value, "revision", None)
    try:
        return int(candidate) if candidate is not None else None
    except (TypeError, ValueError):
        return None


def _config_hash(value: Any) -> str | None:
    if isinstance(value, Mapping):
        candidate = value.get("config_hash")
    else:
        candidate = getattr(value, "config_hash", None)
    return str(candidate) if candidate is not None else None


def _validate_schema(arguments: Mapping[str, Any], schema: Mapping[str, Any]) -> None:
    """Validate the small server-owned JSON schema subset used by tools."""
    if not schema:
        return
    properties = schema.get("properties", {})
    required = schema.get("required", ())
    if not isinstance(properties, Mapping) or not isinstance(required, (list, tuple)):
        raise AssistantError(500, "MODEL_CONTRACT_MISSING", "工具 schema 无效")
    missing = [str(key) for key in required if key not in arguments]
    if missing:
        raise AssistantError(400, "VALIDATION_FAILED", "工具参数缺少必填字段")
    if schema.get("additionalProperties") is False:
        unknown = set(arguments) - set(properties)
        if unknown:
            raise AssistantError(400, "VALIDATION_FAILED", "工具参数包含未声明字段")
    for key, definition in properties.items():
        if key not in arguments or not isinstance(definition, Mapping):
            continue
        expected = definition.get("type")
        value = arguments[key]
        valid = expected == "object" and isinstance(value, Mapping) or expected == "array" and isinstance(value, list) or expected == "string" and isinstance(value, str) or expected == "integer" and isinstance(value, int) and not isinstance(value, bool) or expected == "number" and isinstance(value, (int, float)) and not isinstance(value, bool) or expected == "boolean" and isinstance(value, bool) or expected in (None, "")
        if not valid:
            raise AssistantError(400, "VALIDATION_FAILED", f"工具参数 {key} 类型无效")


def _specs() -> dict[str, ToolSpec]:
    read = {"knowledge", "business"}
    full = frozenset({"full", "universal", "universal-v1"})
    structure_builds = full | {"minimal-structure-v1"}
    values = [
        ToolSpec("normalize_structure", "规范化并校验化学结构", permission="prediction:create", service="structure", modes=frozenset({"business"}), builds=structure_builds),
        ToolSpec("validate_enantiomer_pair", "校验对映体结构对", permission="structure:read", service="structure", modes=frozenset(read), builds=structure_builds),
        ToolSpec("search_knowledge", "检索已授权知识资料", permission="knowledge:read", service="knowledge", modes=frozenset({"knowledge", "business"}), builds=full),
        ToolSpec("read_citation", "读取已授权引用快照", permission="knowledge:read", service="knowledge", modes=frozenset({"knowledge", "business"}), builds=full),
        ToolSpec("get_prediction_capabilities", "查询预测能力", permission="prediction:read", service="prediction", builds=full),
        ToolSpec("create_prediction", "创建受控单次预测", permission="prediction:create", service="prediction", builds=full),
        ToolSpec("get_prediction", "查询预测结果", permission="prediction:read", service="prediction", builds=full),
        ToolSpec("create_batch_draft", "创建批量预测草稿", permission="batch:create", service="prediction", builds=full),
        ToolSpec("update_batch_draft", "修改批量预测草稿", permission="batch:write", service="prediction", builds=full),
        ToolSpec("validate_batch", "校验批量预测草稿", permission="batch:read", service="prediction", builds=full),
        ToolSpec("submit_batch", "提交批量预测", permission="batch:submit", service="prediction", builds=full, requires_confirmation=True, action="batch.submit"),
        ToolSpec("get_batch", "查询批量预测", permission="batch:read", service="prediction", builds=full),
        ToolSpec("list_data_versions", "查询数据版本", permission="data:read", service="data", builds=full),
        ToolSpec("get_data_version", "查询数据版本详情", permission="data:read", service="data", builds=full),
        ToolSpec("create_data_import_draft", "创建数据导入草稿", permission="data:create", service="data", builds=full),
        ToolSpec("validate_data_import", "校验数据导入", permission="data:read", service="data", builds=full),
        ToolSpec("commit_data_import", "提交数据导入", permission="data:submit", service="data", builds=full, requires_confirmation=True, action="data-import.commit"),
        ToolSpec("list_training_templates", "查询已构建训练模板", permission="training:read", service="training", builds=full),
        ToolSpec("create_experiment_draft", "创建训练实验草稿", permission="training:create", service="training", builds=full),
        ToolSpec("validate_experiment", "校验训练实验", permission="training:read", service="training", builds=full),
        ToolSpec("submit_training", "提交训练实验", permission="training:submit", service="training", builds=full, requires_confirmation=True, action="training.submit"),
        ToolSpec("get_experiment", "查询训练实验", permission="training:read", service="training", builds=full),
        ToolSpec("get_model_card", "查询模型卡", permission="model:read", service="model", builds=full),
        ToolSpec("create_registration_draft", "创建模型登记草稿", permission="model:register", service="model", builds=full),
        ToolSpec("validate_registration", "校验模型登记", permission="model:read", service="model", builds=full),
        ToolSpec("register_candidate", "登记模型候选", permission="model:register", service="model", builds=full, requires_confirmation=True, action="model_registration.register"),
        ToolSpec("get_evaluation", "查询模型评测", permission="model:read", service="model", builds=full),
        ToolSpec("prepare_approval_request", "准备审批申请摘要", permission="model:read", service="model", builds=full),
    ]
    # Knowledge mode only exposes read/context tools.  Business services are
    # still permission-checked by the server when a tool is called.
    from dataclasses import replace
    return {item.name: replace(item, modes=frozenset({"business"})) if item.service not in {"structure", "knowledge"} else item for item in values}


TOOL_SPECS = _specs()
DEFAULT_TOOL_SPECS = TOOL_SPECS


def make_toolset(*, build: str = "full", mode: str = "business", permissions: set[str] | frozenset[str] = frozenset({"*"})) -> ToolSet:
    return ToolSet(TOOL_SPECS, build=build, mode=mode, permissions=permissions)


build_toolset = make_toolset
ToolSetFactory = make_toolset


__all__ = [
    "BoundTool", "DEFAULT_TOOL_SPECS", "InMemoryToolExecutionLedger", "TOOL_SPECS",
    "ToolExecution", "ToolExecutionLedger", "ToolPending", "ToolSet", "ToolSetFactory",
    "ToolSpec", "build_toolset", "make_toolset", "normalize_arguments", "revision_hash",
]
