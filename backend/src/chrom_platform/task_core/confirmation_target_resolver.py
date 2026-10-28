"""Explicit optional-resource target resolver for shared confirmations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from chrom_platform.core.dto import ResourceRef

from .service import OperationContext, TargetSnapshot, TaskError


TargetHandler = Callable[[OperationContext, ResourceRef, str], TargetSnapshot]


class ConfirmationTargetResolver:
    """Dispatch known resource kinds and preserve fail-closed fallback behavior."""

    def __init__(self, fallback: TargetHandler | None = None) -> None:
        self._fallback = fallback
        self._handlers: dict[str, TargetHandler] = {}

    def register(self, kind: str, handler: TargetHandler) -> None:
        if not kind or kind in self._handlers:
            raise ValueError("confirmation target kind must be unique")
        self._handlers[kind] = handler

    def __call__(self, ctx: OperationContext, resource_ref: ResourceRef, action: str) -> TargetSnapshot:
        handler = self._handlers.get(resource_ref.type)
        if handler is not None:
            return handler(ctx, resource_ref, action)
        if self._fallback is not None:
            return self._fallback(ctx, resource_ref, action)
        raise TaskError(503, "POLICY_UNAVAILABLE", "确认目标策略未配置", retryable=True)


def register_confirmation_target_resolver(service: Any, kind: str, handler: TargetHandler) -> None:
    """Register one optional module without changing other resource policies."""

    current = getattr(service, "resolver", None)
    if isinstance(current, ConfirmationTargetResolver):
        current.register(kind, handler)
        return
    policy = getattr(service, "policy", None)
    fallback = current or (getattr(policy, "resolve_target", None) if policy is not None else None)
    resolver = ConfirmationTargetResolver(fallback)
    resolver.register(kind, handler)
    service.resolver = resolver


__all__ = ["ConfirmationTargetResolver", "register_confirmation_target_resolver"]
