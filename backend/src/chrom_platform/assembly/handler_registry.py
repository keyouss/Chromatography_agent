"""Static production worker registry.

The registry is build-owned: queue payloads are data only and can never select
an import path or a callable.  Each adapter exposes the same three-field
mapping boundary and the worker owns all database fencing.
"""

from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Any, Awaitable, Callable, Mapping


Handler = Callable[[Mapping[str, Any]], Any]


@dataclass(frozen=True)
class MappingHandlerAdapter:
    """Adapt a production worker's execute/cancel/retry methods."""

    worker: object

    async def execute(self, payload: Mapping[str, Any], lease_guard: object | None = None) -> Any:
        return await _call_worker(getattr(self.worker, "execute"), dict(payload), lease_guard)

    async def cancel(self, payload: Mapping[str, Any], lease_guard: object | None = None) -> Any:
        return await _call_worker(getattr(self.worker, "cancel"), dict(payload), lease_guard)

    async def retry(self, payload: Mapping[str, Any], lease_guard: object | None = None) -> Any:
        return await _call_worker(getattr(self.worker, "retry"), dict(payload), lease_guard)

    def for_event(self, event_type: str) -> Handler:
        if event_type == "job.cancel":
            return self.cancel
        if event_type in {"job.execute", "prediction.execute"}:
            return self.execute
        raise KeyError(event_type)


@dataclass(frozen=True)
class FunctionHandlerAdapter:
    """Bind reviewed execute/cancel callables to the static registry."""

    execute_handler: Handler
    cancel_handler: Handler

    async def execute(self, payload: Mapping[str, Any]) -> Any:
        return await _await(self.execute_handler(dict(payload)))

    async def cancel(self, payload: Mapping[str, Any]) -> Any:
        return await _await(self.cancel_handler(dict(payload)))

    async def retry(self, payload: Mapping[str, Any]) -> Any:
        return await self.execute(payload)

    def for_event(self, event_type: str) -> Handler:
        if event_type == "job.cancel":
            return self.cancel
        if event_type in {"job.execute", "prediction.execute"}:
            return self.execute
        raise KeyError(event_type)


async def _await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _call_worker(method: Callable[..., Any], payload: Mapping[str, Any], lease_guard: object | None) -> Any:
    try:
        accepts_guard = "lease_guard" in inspect.signature(method).parameters
    except (TypeError, ValueError):
        accepts_guard = False
    value = method(payload, lease_guard=lease_guard) if accepts_guard else method(payload)
    return await _await(value)


@dataclass(frozen=True)
class StaticHandlerRegistry:
    """Kind-to-worker registry assembled from selected build modules."""

    by_kind: Mapping[str, MappingHandlerAdapter]

    def handler(self, kind: str, event_type: str) -> Handler | None:
        adapter = self.by_kind.get(kind)
        return None if adapter is None else adapter.for_event(event_type)

    def kinds(self) -> frozenset[str]:
        return frozenset(self.by_kind)


__all__ = ["Handler", "MappingHandlerAdapter", "FunctionHandlerAdapter", "StaticHandlerRegistry"]
