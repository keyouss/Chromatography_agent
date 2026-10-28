"""Application health port.

The active probes live in ``infrastructure.health``.  This module only keeps
the application-facing structural protocol so core does not implement a
second database or queue health path.
"""

from __future__ import annotations

from typing import Any, Protocol


class DependencyHealthProbe(Protocol):
    def check(self) -> list[Any]: ...


__all__ = ["DependencyHealthProbe"]
