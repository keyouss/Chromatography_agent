from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from chrom_platform.assembly import prediction as original_prediction
from chrom_platform.assembly.prediction_worker_registry import build_prediction_worker_registry
from chrom_platform.assembly.profile import _trim_prediction_worker_registry


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "backend" / "src" / "chrom_platform"


def _exec_function(source: str, filename: Path, namespace: dict[str, object]) -> object:
    exec(compile(source, str(filename), "exec"), namespace)
    return namespace


def test_worker_builder_preserves_original_injection_and_attaches_returned_sfc_proof(monkeypatch) -> None:
    proof = SimpleNamespace(model_id="sfc-model", model_version="7")
    verification = SimpleNamespace(catalog_source_proof=proof)
    leaf = SimpleNamespace(_model_id="sfc-model", _model_version="7", catalog_source_proof=None)

    class Dispatch:
        def binding_for(self, model_id: str, model_version: str):
            return leaf if (model_id, model_version) == (leaf._model_id, leaf._model_version) else None

    class Registry:
        def adapter(self, type_id: str):
            assert type_id == "sfc"
            return Dispatch()

    registry = Registry()
    seen: list[object] = []

    def fake_builder(*, session_factory=None):
        seen.append(session_factory)
        namespace = {"result": verification}
        _exec_function("def _verification_result():\n    return result\n", Path(original_prediction.__file__), namespace)
        namespace["_verification_result"]()
        return registry

    session_factory = object()
    monkeypatch.setattr(original_prediction, "build_prediction_worker_registry", fake_builder)
    actual = build_prediction_worker_registry(session_factory=session_factory)

    assert actual is registry
    assert seen == [session_factory]
    assert leaf.catalog_source_proof is proof


def test_worker_observes_real_sfc_load_returns_and_cc_cache_misses(monkeypatch, capsys) -> None:
    from chrom_platform.assembly import prediction as original

    class CcRuntime:
        _models: dict[str, object] = {}
        registry = SimpleNamespace(bundle=lambda value: SimpleNamespace(model_id=value))

    entry = SimpleNamespace(model_id="sfc-model", version="3")
    runtime_root = Path(original.__file__).resolve().parents[1] / "adapters"
    seen: list[str] = []

    def fake_builder(*, session_factory=None):
        ns = {"runtime": CcRuntime(), "model": object(), "entry": entry}
        cc_file = runtime_root / "cc" / "runtime.py"
        _exec_function(
            "def load_model(self, model_id_or_spec):\n    return None\n",
            cc_file,
            ns,
        )
        ns["load_model"](ns["runtime"], "cc-model")
        sfc_file = runtime_root / "sfc" / "runtime.py"
        _exec_function(
            "def load_sfc_model():\n    return model, entry\n",
            sfc_file,
            ns,
        )
        ns["load_sfc_model"]()
        seen.append("builder")
        return object()

    monkeypatch.setattr(original, "build_prediction_worker_registry", fake_builder)
    build_prediction_worker_registry()
    output = capsys.readouterr().err

    assert seen == ["builder"]
    assert '"binding": "cc:cc-model"' in output
    assert '"binding": "sfc:sfc-model@3"' in output


def test_worker_observes_gc_only_on_model_cache_miss(monkeypatch, capsys) -> None:
    from chrom_platform.assembly import prediction as original

    class GcRuntime:
        def __init__(self) -> None:
            self._model = None
            self._bound_model_id = None

    runtime = GcRuntime()
    runtime_file = Path(original.__file__).resolve().parents[1] / "adapters" / "gc" / "runtime.py"

    def fake_builder(*, session_factory=None):
        namespace = {"runtime": runtime}
        _exec_function(
            "def _load_model(self, *, for_verification):\n"
            "    if self._model is not None:\n        return self._model\n"
            "    self._model = object()\n    return self._model\n",
            runtime_file,
            namespace,
        )
        namespace["_load_model"](runtime, for_verification=True)
        namespace["_load_model"](runtime, for_verification=False)
        return object()

    monkeypatch.setattr(original, "build_prediction_worker_registry", fake_builder)
    build_prediction_worker_registry()
    output = capsys.readouterr().err

    assert output.count('"binding": "gc:gc-gnn-seq240408-250c"') == 1


def test_worker_profile_chains_and_restores_existing_hook_when_builder_raises(monkeypatch) -> None:
    previous = sys.getprofile()
    events: list[tuple[str, str]] = []

    def prior_hook(frame, event, argument):
        events.append((frame.f_code.co_name, event))

    def failing_builder(*, session_factory=None):
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(original_prediction, "build_prediction_worker_registry", failing_builder)
    sys.setprofile(prior_hook)
    try:
        with pytest.raises(RuntimeError, match="fixture failure"):
            build_prediction_worker_registry()
        assert sys.getprofile() is prior_hook
        assert ("failing_builder", "call") in events
    finally:
        sys.setprofile(previous)


def test_profile_trims_worker_observers_and_exporter_but_leaves_universal_source_unchanged() -> None:
    source = (SOURCE / "assembly" / "prediction_worker_registry.py").read_text(encoding="utf-8")
    assert _trim_prediction_worker_registry(source, {"tlc", "cc", "hplc", "gc", "sfc"}) == source

    trimmed = _trim_prediction_worker_registry(source, {"tlc"})
    ast.parse(trimmed)
    for token in (
        "adapters/cc/runtime.py", "adapters/gc/runtime.py", "adapters/sfc/runtime.py", "_verification_result",
        'if capability.id == "sfc":', "catalog_source_proof", "entry_fields",
    ):
        assert token not in trimmed
    assert "def build_prediction_worker_registry" in trimmed
    assert "original.build_prediction_worker_registry(session_factory=session_factory)" in trimmed


def test_profile_retains_only_selected_runtime_observer() -> None:
    source = (SOURCE / "assembly" / "prediction_worker_registry.py").read_text(encoding="utf-8")
    sfc_only = _trim_prediction_worker_registry(source, {"sfc", "tlc"})
    cc_only = _trim_prediction_worker_registry(source, {"cc", "tlc"})
    gc_only = _trim_prediction_worker_registry(source, {"gc", "tlc"})
    ast.parse(sfc_only)
    ast.parse(cc_only)
    ast.parse(gc_only)
    assert "cc_source =" not in sfc_only
    assert "gc_source =" not in sfc_only
    assert "gc_source =" not in cc_only
    assert "sfc_source =" in sfc_only
    assert "sfc_source =" not in cc_only
    assert "gc_source =" not in cc_only
    assert "cc_source =" in cc_only
    assert "gc_source =" in gc_only
    assert "cc_source =" not in gc_only
    assert "sfc_source =" not in gc_only
    assert "gc_source =" in gc_only
    for trimmed in (cc_only, gc_only):
        assert "prediction_source" not in trimmed
        assert "proofs" not in trimmed
