"""Controlled SFC scientific adapter.

The package has no default asset path.  A build supplies a trusted registry,
model root, and server-side structure/candidate resolvers explicitly.
"""

from .adapter import SfcAdapter, build_sfc_adapter
from .runtime import (
    SfcAssetUnavailable,
    SfcCandidate,
    SfcModelRegistry,
    SfcRuntime,
    load_sfc_model,
)

__all__ = [
    "SfcAdapter",
    "SfcAssetUnavailable",
    "SfcCandidate",
    "SfcModelRegistry",
    "SfcRuntime",
    "build_sfc_adapter",
    "load_sfc_model",
]
