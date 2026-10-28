from __future__ import annotations

from typing import Any, Mapping

from chrom_platform.core.errors import APIError


class AssistantError(APIError):
    """Safe application error for the assistant boundary."""

    def __init__(self, status_code: int, code: str, message: str, *, retryable: bool = False, details: dict[str, object] | None = None) -> None:
        super().__init__(status_code, code, message, retryable=retryable, details=details or {})


_PRIVATE_DIAGNOSTIC_KEYS = frozenset({"provider", "http_status", "provider_error_code", "category"})
_DIAGNOSTIC_EVENT_TYPES = frozenset({
    "llm.started", "llm.completed", "llm.failed", "run.failed",
})
_PUBLIC_DIAGNOSTIC_KEYS = frozenset({"code", "message", "retryable"})
_SAFE_ERROR = {"code": "INTERNAL_ERROR", "message": "内部错误", "retryable": False}


def _is_diagnostic_event(event_type: str | None) -> bool:
    value = str(event_type or "")
    return value in _DIAGNOSTIC_EVENT_TYPES or value.endswith(".diagnostic") or value.startswith("diagnostic.")


def public_error(value: Any) -> Any:
    """Project internal error diagnostics to the strict public error DTO."""
    if not isinstance(value, Mapping):
        return dict(_SAFE_ERROR)
    code = value.get("code")
    message = value.get("message")
    retryable = value.get("retryable")
    return {
        "code": code[:96] if isinstance(code, str) and code else _SAFE_ERROR["code"],
        "message": message[:2000] if isinstance(message, str) and message else _SAFE_ERROR["message"],
        "retryable": retryable if isinstance(retryable, bool) else _SAFE_ERROR["retryable"],
    }


def _public_diagnostic(value: Any) -> dict[str, Any]:
    """Keep only the public error-shaped part of an explicit diagnostic node."""
    if not isinstance(value, Mapping):
        return dict(_SAFE_ERROR)
    return {key: value[key] for key in _PUBLIC_DIAGNOSTIC_KEYS if key in value}


def public_event_data(value: Any, *, event_type: str | None = None) -> Any:
    """Project event data without deleting business fields by key name.

    ``provider`` and ``category`` are valid business result fields.  They are
    removed only as known top-level transport diagnostics on explicitly
    diagnostic event types.  Error and diagnostic subtrees have their own
    strict public projections; all other event data is preserved recursively.
    """
    diagnostic_event = _is_diagnostic_event(event_type)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if diagnostic_event and name in _PRIVATE_DIAGNOSTIC_KEYS:
                continue
            if name == "error":
                result[name] = None if item is None and event_type in {"run.completed", "run.canceled"} else public_error(item)
            elif name == "diagnostic" and diagnostic_event:
                result[name] = _public_diagnostic(item)
            else:
                result[name] = public_event_data(item, event_type=event_type)
        return result
    if isinstance(value, list):
        return [public_event_data(item, event_type=event_type) for item in value]
    if isinstance(value, tuple):
        return [public_event_data(item, event_type=event_type) for item in value]
    return value


def unavailable(message: str = "Assistant 持久化或业务端口未配置") -> AssistantError:
    return AssistantError(503, "DEPENDENCY_UNAVAILABLE", message, retryable=True)


AssistantServiceError = AssistantError

__all__ = ["AssistantError", "AssistantServiceError", "public_error", "public_event_data", "unavailable"]
