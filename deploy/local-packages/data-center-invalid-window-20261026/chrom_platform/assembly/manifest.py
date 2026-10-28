from __future__ import annotations

import json
from pathlib import Path

from chrom_platform.core.dto import BuildManifest

_MODULES = set(['datasets'])
_TYPES = set([])
_TOOLS = {}
_UTILITIES = set([])
_EXPORTS = set([])


def _unique(values: list[str], allowed: set[str], label: str) -> None:
    if len(set(values)) != len(values) or set(values) - allowed:
        raise ValueError(f"{label}: duplicate or unsupported value")


def validate_manifest(raw: object) -> BuildManifest:
    manifest = BuildManifest.model_validate(raw)
    _unique(manifest.modules, _MODULES, "modules")
    _unique(manifest.chromatography_types, _TYPES, "chromatography_types")
    _unique(manifest.utilities, _UTILITIES, "utilities")
    _unique(manifest.export_formats, _EXPORTS, "export_formats")
    if set(manifest.prediction_tools) != set(manifest.chromatography_types):
        raise ValueError("prediction_tools must exactly match selected adapters")
    for kind, selected_tools in manifest.prediction_tools.items():
        if kind not in _TOOLS or set(selected_tools) - _TOOLS[kind]:
            raise ValueError(f"prediction_tools.{kind}: unsupported tool")
        _unique(selected_tools, _TOOLS[kind], f"prediction_tools.{kind}")
        if not selected_tools:
            raise ValueError(f"prediction_tools.{kind}: at least one tool required")
    if "prediction" in manifest.modules and not manifest.chromatography_types:
        raise ValueError("prediction requires at least one adapter")
    if manifest.chromatography_types and "prediction" not in manifest.modules:
        raise ValueError("adapters require prediction module")
    if "utilities" not in manifest.modules and manifest.utilities:
        raise ValueError("optional utilities require utilities module")
    if "assistant" in manifest.modules:
        features = manifest.assistant_features or []
        _unique(features, {"knowledge", "business", "trace"}, "assistant_features")
        if not set(features) & {"knowledge", "business"}:
            raise ValueError("assistant requires an executable mode")
        if "knowledge" in features and "knowledge" not in manifest.modules:
            raise ValueError("knowledge mode requires knowledge module")
    elif manifest.assistant_features is not None:
        raise ValueError("assistant_features must be absent without assistant")
    return manifest


def _default_manifest_path() -> Path:
    """Return only an explicit environment or package-local manifest.

    Searching parent directories made a trimmed deployment accidentally load
    the source checkout's ``builds`` directory.  Production therefore needs
    ``CHROM_BUILD_MANIFEST`` or a packaged ``build-manifest.json``.
    """

    import os

    configured = os.getenv("CHROM_BUILD_MANIFEST")
    if configured:
        return Path(configured).resolve()
    # ``manifest.py`` lives at ``chrom_platform/assembly``.  A generated
    # profile places ``build-manifest.json`` beside ``chrom_platform``; using
    # ``parent.parent`` incorrectly looks inside the package and makes an
    # otherwise self-contained profile fail when imported without an env var.
    packaged = Path(__file__).resolve().parents[2] / "build-manifest.json"
    if packaged.is_file():
        return packaged
    raise FileNotFoundError(
        "build manifest is not configured; set CHROM_BUILD_MANIFEST or package build-manifest.json"
    )


def load_manifest(path: str | Path | None = None) -> BuildManifest:
    manifest_path = Path(path).resolve() if path is not None else _default_manifest_path()
    if not manifest_path.is_file() or manifest_path.suffix.lower() != ".json":
        raise FileNotFoundError(f"build manifest not found: {manifest_path}")
    return validate_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
