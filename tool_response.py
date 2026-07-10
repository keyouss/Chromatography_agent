"""Shared JSON response helpers for Agent tools."""

from __future__ import annotations

import json
from typing import Any


def tool_response(
    *,
    ok: bool,
    tool: str,
    input_data: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    summary: str = "",
    warnings: list[str] | None = None,
    error: str | None = None,
    sources: list[dict[str, Any]] | None = None,
) -> str:
    """Return a stable JSON envelope for LangChain tool observations."""
    payload = {
        "ok": ok,
        "tool": tool,
        "input": input_data or {},
        "result": result or {},
        "summary": summary,
        "warnings": warnings or [],
        "error": error,
        "sources": sources or [],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
