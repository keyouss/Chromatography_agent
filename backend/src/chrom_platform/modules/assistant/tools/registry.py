"""Compatibility import surface for the assistant ToolSet registry."""

from . import DEFAULT_TOOL_SPECS, TOOL_SPECS, ToolSet, ToolSpec, build_toolset, make_toolset


def priority_toolset(*, build: str = "universal-v1", permissions=frozenset({"*"})):
    from ..priority import priority_toolset as _priority_toolset

    return _priority_toolset(build=build, permissions=permissions)

__all__ = ["DEFAULT_TOOL_SPECS", "TOOL_SPECS", "ToolSet", "ToolSpec", "build_toolset", "make_toolset", "priority_toolset"]
