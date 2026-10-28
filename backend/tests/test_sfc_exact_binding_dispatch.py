from __future__ import annotations

from dataclasses import dataclass
import builtins
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from types import SimpleNamespace
from time import sleep
from pathlib import Path
import ast

import pytest

from chrom_platform.adapters.sfc.adapter import SfcAdapter, SfcDispatchAdapter, _schemas
from chrom_platform.adapters.sfc import runtime as sfc_runtime_module
from chrom_platform.adapters.sfc.runtime import SfcAssetUnavailable, SfcCandidate, SfcRuntime
from chrom_platform.core.errors import InputValidationError
from chrom_platform.modules.prediction.adapters import (
    CapabilityRegistry,
    ModelUnavailableError,
    ModelVerificationEvidence,
    PredictionContractError,
)
from chrom_platform.modules.prediction.schemas import (
    ExactModelSelection,
    ModelSnapshot,
    SfcFullColumnPredictionCreate,
    SfcSingleColumnPredictionCreate,
)


@dataclass(frozen=True)
class FakeEntry:
    model_id: str
    version: str
    scope: str
    artifact_sha256: str
    allowed_columns: tuple[str, ...] = ("AD",)
    stationary_phase: str = "amylose"
    mode: str = "coated"
    single_column_slug: str = "ad-coated"
    single_column_group: str = "AD coated"
    feature_names: tuple[str, ...] = tuple(f"f{i}" for i in range(66))
    target: str = "separation"
    threshold: str = "0.5"
    score_semantics: str = "positive_class_probability"


class FakeCatalog:
    def __init__(self, model_id: str, version: str, scope: str) -> None:
        self.model_id, self.version, self.scope = model_id, version, scope
        self.binding = None
        self.scope_rows: dict[str, dict[str, str]] = {}

    def bind_model_scope(self, **binding) -> None:
        exact = (binding["model_id"], binding["model_version"], binding["scope"])
        if self.binding is not None and self.binding != exact:
            raise RuntimeError("catalog binding was reused across models")
        self.binding = exact

    def verify_source_hash(self) -> None:
        return None

    def scope_binding(self, scope_id: str):
        return self.scope_rows.get(scope_id)


class FakeRegistry:
    def __init__(self, entries: list[FakeEntry]) -> None:
        self.entries = entries

    def entry(self, model_id: str, *, version: str) -> FakeEntry:
        return next(item for item in self.entries if (item.model_id, item.version) == (model_id, version))


class FakeRuntime:
    def __init__(self, entries: list[FakeEntry], entry: FakeEntry, catalog: FakeCatalog) -> None:
        self.registry = FakeRegistry(entries)
        self.bound_model_id = entry.model_id
        self.bound_model_version = entry.version
        self.bound_artifact_sha256 = entry.artifact_sha256
        self.business_scope_id = f"scope-{entry.model_id}-{entry.version}"
        self.allowed_tool = "single_column" if entry.scope == "single_column" else "full_column"
        self.catalog = catalog
        self.calls = 0
        self.preflight_calls = 0
        self.entry_value = entry

    def preflight(self, request, **kwargs) -> None:
        self.preflight_calls += 1

    def predict(self, request, **kwargs):
        self.calls += 1
        return ([{
            "candidate_id": self.entry_value.model_id,
            "rank": 1,
            "separation": 0.5,
            "score_semantics": self.entry_value.score_semantics,
            "conditions": {
                "column": "AD", "stationary_phase": "amylose", "mode": "coated",
                "length_mm": 150.0, "inner_diameter_mm": 4.6, "particle_size_um": 5.0,
                "column_temperature_c": 35.0, "flow_rate_ml_min": 2.5,
            },
        }], [], self.entry_value)


def _binding(entry: FakeEntry, entries: list[FakeEntry], *, verified: bool = True) -> SfcAdapter:
    catalog = FakeCatalog(entry.model_id, entry.version, entry.scope)
    runtime = FakeRuntime(entries, entry, catalog)
    input_schema, output_schema = _schemas(runtime.allowed_tool)
    evidence = ModelVerificationEvidence(
        adapter_id="sfc",
        model_id=entry.model_id,
        model_version=entry.version,
        business_scope_id=runtime.business_scope_id,
        input_schema_version="1.1",
        output_schema_version="1.0",
        artifact_sha256=entry.artifact_sha256,
        manifest_verified=verified,
        schema_verified=verified,
        artifact_sha_verified=verified,
        environment_verified=verified,
        golden_sample_verified=verified,
        anomaly_checks_verified=verified,
        schema_by_tool={runtime.allowed_tool: {"input_schema": input_schema, "output_schema": output_schema}},
    ) if verified else None
    return SfcAdapter(
        runtime=runtime,
        model_id=entry.model_id,
        model_version=entry.version,
        artifact_sha256=entry.artifact_sha256,
        business_scope_id=runtime.business_scope_id,
        verification_evidence=evidence,
        candidate_catalog=catalog,
    )


def _request(entry: FakeEntry):
    full = entry.scope == "all_column"
    payload = {
        "project_id": "project-a",
        "schema_version": "1.1",
        "model_selection": {"mode": "exact", "model_id": entry.model_id, "model_version": entry.version},
        "structure_refs": ["a", "b"],
        "chromatography_type": "sfc",
        "tool": "full_column" if full else "single_column",
        "input": {
            **({} if full else {"column_id": "AD"}),
            "modifier_total_fraction": 0.2,
            "modifier_components": [{"structure_ref": "mod", "fraction": 1.0}],
            "candidate_scope_id": f"scope-{entry.model_id}",
            "top_k": 1,
            **({} if full else {}),
        },
    }
    model = SfcFullColumnPredictionCreate if full else SfcSingleColumnPredictionCreate
    return model.model_validate(payload)


def _snapshot(entry: FakeEntry) -> ModelSnapshot:
    return ModelSnapshot(
        id=entry.model_id,
        version=entry.version,
        business_scope_id=f"scope-{entry.model_id}-{entry.version}",
        artifact_sha256=entry.artifact_sha256,
        input_schema_version="1.1",
        output_schema_version="1.0",
    )


def _setup(entries: list[FakeEntry], *, unverified: set[tuple[str, str]] = frozenset()):
    bindings = [
        _binding(entry, entries, verified=(entry.model_id, entry.version) not in unverified)
        for entry in entries
    ]
    dispatcher = SfcDispatchAdapter(bindings)
    registry = CapabilityRegistry()
    registry.register(dispatcher)
    return registry, dispatcher, bindings


def test_composite_registration_keeps_exact_model_tool_health_independent():
    single = FakeEntry("single-model", "1", "single_column", "a" * 64)
    full = FakeEntry("full-model", "1", "all_column", "b" * 64)
    registry, _dispatcher, _bindings = _setup([single, full], unverified={("full-model", "1")})

    capability = registry.capability("sfc")
    models = {(item.id, item.version): item for item in capability.models}
    tools = {item.id: item for item in capability.tools}
    assert capability.health.status == "READY"
    assert models[("single-model", "1")].health.status == "READY"
    assert models[("full-model", "1")].health.status == "UNAVAILABLE"
    assert tools["single_column"].health.status == "READY"
    assert tools["full_column"].health.status == "UNAVAILABLE"
    assert tools["single_column"].input_schema == _schemas("single_column")[0]
    with pytest.raises(ValueError, match="already registered"):
        registry.register(SfcDispatchAdapter([_binding(single, [single])]))
    with pytest.raises(ValueError, match="duplicate SFC exact"):
        SfcDispatchAdapter([_binding(single, [single]), _binding(single, [single])])


def test_legacy_single_sfc_adapter_registration_still_works():
    single = FakeEntry("single-model", "1", "single_column", "a" * 64)
    binding = _binding(single, [single])
    registry = CapabilityRegistry()
    registry.register(binding)
    result = registry.execute_snapshot(
        "sfc", _request(single), model_snapshot=_snapshot(single), normalized_structures=["C", "N"],
        normalized_modifier_structures={"mod": "O"},
    )
    assert result.rows[0].candidate_id == single.model_id
    assert binding.runtime.calls == 1


def test_exact_request_dispatch_and_cross_binding_fail_closed():
    single = FakeEntry("single-model", "1", "single_column", "a" * 64)
    full = FakeEntry("full-model", "1", "all_column", "b" * 64)
    registry, dispatcher, bindings = _setup([single, full])
    by_key = {(item._model_id, item._model_version): item for item in bindings}

    for entry in (single, full):
        result = registry.execute_snapshot(
            "sfc", _request(entry), model_snapshot=_snapshot(entry), normalized_structures=["C", "N"],
            normalized_modifier_structures={"mod": "O"},
        )
        assert result.rows[0].candidate_id == entry.model_id
        tool_id = "full_column" if entry.scope == "all_column" else "single_column"
        assert by_key[(entry.model_id, entry.version)].verified_handle.schema_for(tool_id)["input_schema"] == _schemas(tool_id)[0]
    assert by_key[("single-model", "1")].runtime.calls == 1
    assert by_key[("full-model", "1")].runtime.calls == 1

    wrong_tool = _request(single).model_copy(update={"tool": "full_column"})
    unknown = _request(single).model_copy(update={
        "model_selection": ExactModelSelection(mode="exact", model_id="missing", model_version="1")
    })
    wrong_dto = _request(full).model_copy(update={
        "model_selection": ExactModelSelection(mode="exact", model_id="single-model", model_version="1")
    })
    with pytest.raises((PredictionContractError, InputValidationError, ModelUnavailableError)):
        dispatcher.predict(wrong_tool)
    with pytest.raises((PredictionContractError, ModelUnavailableError)):
        dispatcher.predict(unknown)
    with pytest.raises(PredictionContractError):
        dispatcher.predict(wrong_dto)
    assert by_key[("single-model", "1")].runtime.calls == 1
    assert by_key[("full-model", "1")].runtime.calls == 1


def test_execute_snapshot_uses_frozen_exact_binding_and_never_falls_back():
    old = FakeEntry("single-model", "1", "single_column", "a" * 64)
    new = FakeEntry("single-model", "2", "single_column", "c" * 64)
    full = FakeEntry("full-model", "1", "all_column", "b" * 64)
    registry, _dispatcher, bindings = _setup([old, new, full])
    by_key = {(item._model_id, item._model_version): item for item in bindings}

    registry.execute_snapshot(
        "sfc", _request(old), model_snapshot=_snapshot(old), normalized_structures=["C", "N"],
        normalized_modifier_structures={"mod": "O"},
    )
    assert by_key[("single-model", "1")].runtime.calls == 1
    assert by_key[("single-model", "2")].runtime.calls == 0
    for update in (
        {"artifact_sha256": "d" * 64},
        {"business_scope_id": "another-scope"},
        {"input_schema_version": "1.0"},
        {"version": "missing"},
    ):
        tampered = _snapshot(old).model_copy(update=update)
        with pytest.raises(ModelUnavailableError):
            registry.execute_snapshot("sfc", _request(old), model_snapshot=tampered)
    assert by_key[("single-model", "2")].runtime.calls == 0


def test_scope_catalog_lookup_uses_only_matching_exact_binding():
    single = FakeEntry("single-model", "1", "single_column", "a" * 64)
    full = FakeEntry("full-model", "1", "all_column", "b" * 64)
    _registry, dispatcher, bindings = _setup([single, full])
    single_catalog = bindings[0].candidate_catalog
    full_catalog = bindings[1].candidate_catalog
    stored_scope = {
        "scope_id": "same-scope", "project_id": "p", "model_id": "full-model", "model_version": "1"
    }
    # The production catalogs read the same SQLite scope table; both views see
    # the row, but only its exact model binding may claim it.
    single_catalog.scope_rows["same-scope"] = stored_scope
    full_catalog.scope_rows["same-scope"] = stored_scope

    binding, catalog, stored = dispatcher.catalog_for_scope("same-scope")
    assert binding._model_id == stored["model_id"] == "full-model"
    assert catalog is full_catalog
    assert catalog is not single_catalog


def test_each_exact_runtime_loads_once_under_concurrent_first_use(monkeypatch):
    single = FakeEntry("single-model", "1", "single_column", "a" * 64)
    full = FakeEntry("full-model", "1", "all_column", "b" * 64)
    fake_registry = FakeRegistry([single, full])
    single_runtime = SfcRuntime(
        registry=fake_registry,
        bound_model_id=single.model_id,
        bound_model_version=single.version,
        bound_artifact_sha256=single.artifact_sha256,
        business_scope_id="scope-single",
        structure_resolver={},
        candidate_resolver={},
    )
    full_runtime = SfcRuntime(
        registry=fake_registry,
        bound_model_id=full.model_id,
        bound_model_version=full.version,
        bound_artifact_sha256=full.artifact_sha256,
        business_scope_id="scope-full",
        structure_resolver={},
        candidate_resolver={},
    )
    counts: dict[tuple[str, str], int] = {}
    guard = Lock()

    def fake_load(registry, model_id, *, model_version):
        key = (model_id, model_version)
        with guard:
            counts[key] = counts.get(key, 0) + 1
        sleep(0.02)
        entry = registry.entry(model_id, version=model_version)
        return object(), entry

    monkeypatch.setattr(sfc_runtime_module, "load_sfc_model", fake_load)

    def concurrent_reads(runtime):
        start = Barrier(6)

        def read():
            start.wait()
            return runtime._load_bound_model()

        with ThreadPoolExecutor(max_workers=6) as pool:
            return list(pool.map(lambda _index: read(), range(6)))

    single_values = concurrent_reads(single_runtime)
    full_values = concurrent_reads(full_runtime)
    assert counts == {("single-model", "1"): 1, ("full-model", "1"): 1}
    assert len({id(model) for model, _entry in single_values}) == 1
    assert len({id(model) for model, _entry in full_values}) == 1
    assert single_values[0][0] is not full_values[0][0]


def test_failed_exact_loader_does_not_dispatch_to_sibling_or_reuse_its_cache(monkeypatch):
    single = FakeEntry("single-model", "1", "single_column", "a" * 64)
    full = FakeEntry("full-model", "1", "all_column", "b" * 64)
    entries = [single, full]
    single_leaf, full_leaf = _binding(single, entries), _binding(full, entries)
    fake_registry = FakeRegistry(entries)
    runtimes = {
        (single.model_id, single.version): SfcRuntime(
            registry=fake_registry, bound_model_id=single.model_id, bound_model_version=single.version,
            bound_artifact_sha256=single.artifact_sha256, business_scope_id="scope-single",
            structure_resolver={}, candidate_resolver={},
        ),
        (full.model_id, full.version): SfcRuntime(
            registry=fake_registry, bound_model_id=full.model_id, bound_model_version=full.version,
            bound_artifact_sha256=full.artifact_sha256, business_scope_id="scope-full",
            structure_resolver={}, candidate_resolver={},
        ),
    }
    single_leaf.runtime = runtimes[(single.model_id, single.version)]
    full_leaf.runtime = runtimes[(full.model_id, full.version)]
    dispatcher = SfcDispatchAdapter([single_leaf, full_leaf])
    calls: dict[tuple[str, str], int] = {}

    def fake_load(registry, model_id, *, model_version):
        key = (model_id, model_version)
        calls[key] = calls.get(key, 0) + 1
        if key == (single.model_id, single.version):
            raise SfcAssetUnavailable("MODEL_ARTIFACT_LOAD_FAILED")
        return object(), registry.entry(model_id, version=model_version)

    monkeypatch.setattr(sfc_runtime_module, "load_sfc_model", fake_load)

    for leaf, entry in ((single_leaf, single), (full_leaf, full)):
        runtime = leaf.runtime

        def predict(request, runtime=runtime, entry=entry, **kwargs):
            runtime._load_bound_model()
            return ([{
                "candidate_id": entry.model_id, "rank": 1, "separation": 0.5,
                "score_semantics": entry.score_semantics,
                "conditions": {
                    "column": "AD", "stationary_phase": "amylose", "mode": "coated",
                    "length_mm": 150.0, "inner_diameter_mm": 4.6, "particle_size_um": 5.0,
                    "column_temperature_c": 35.0, "flow_rate_ml_min": 2.5,
                },
            }], [], entry)

        runtime.predict = predict

    with pytest.raises(ModelUnavailableError):
        dispatcher.predict(_request(single))
    result = dispatcher.predict(_request(full))
    assert result.rows[0].candidate_id == full.model_id
    with pytest.raises(ModelUnavailableError):
        dispatcher.predict(_request(single))
    assert calls == {(single.model_id, "1"): 2, (full.model_id, "1"): 1}


def test_single_and_full_first_loads_run_concurrently_with_separate_candidate_domains(monkeypatch):
    single = FakeEntry("single-model", "1", "single_column", "a" * 64)
    full = FakeEntry("full-model", "1", "all_column", "b" * 64)
    fake_registry = FakeRegistry([single, full])
    single_candidate = SfcCandidate("single-row", "AD", "amylose", "coated", 150, 4.6, 5, 35, 1.0)
    full_candidate = SfcCandidate("full-row", "BE", "cellulose", "immobilized", 250, 3.0, 3, 40, 0.8)
    single_runtime = SfcRuntime(
        registry=fake_registry, bound_model_id=single.model_id, bound_model_version=single.version,
        bound_artifact_sha256=single.artifact_sha256, business_scope_id="scope-single",
        structure_resolver={}, candidate_resolver={"scope-single": [single_candidate]},
    )
    full_runtime = SfcRuntime(
        registry=fake_registry, bound_model_id=full.model_id, bound_model_version=full.version,
        bound_artifact_sha256=full.artifact_sha256, business_scope_id="scope-full",
        structure_resolver={}, candidate_resolver={"scope-full": [full_candidate]},
    )
    start = Barrier(2)
    counts: dict[tuple[str, str], int] = {}
    guard = Lock()

    def fake_load(registry, model_id, *, model_version):
        key = (model_id, model_version)
        with guard:
            counts[key] = counts.get(key, 0) + 1
        start.wait()
        return object(), registry.entry(model_id, version=model_version)

    monkeypatch.setattr(sfc_runtime_module, "load_sfc_model", fake_load)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(runtime._load_bound_model) for runtime in (single_runtime, full_runtime)]
        loaded = [future.result() for future in futures]

    single_candidates = single_runtime._resolve_candidates(SimpleNamespace(
        project_id="p", input=SimpleNamespace(candidate_scope_id="scope-single")
    ))
    full_candidates = full_runtime._resolve_candidates(SimpleNamespace(
        project_id="p", input=SimpleNamespace(candidate_scope_id="scope-full")
    ))
    assert counts == {(single.model_id, "1"): 1, (full.model_id, "1"): 1}
    assert loaded[0][0] is not loaded[1][0]
    assert single_runtime._loaded_entry.model_id == single.model_id
    assert full_runtime._loaded_entry.model_id == full.model_id
    assert single_candidates == [single_candidate]
    assert full_candidates == [full_candidate]


def test_trimmed_router_removes_only_sfc_routes_and_runtime_references():
    from chrom_platform.assembly.profile import _trim_prediction_router

    root = Path(__file__).resolve().parents[2]
    source = (root / "backend/src/chrom_platform/modules/prediction/router.py").read_text(encoding="utf-8")
    without_sfc = _trim_prediction_router(source, {"cc", "tlc"})
    ast.parse(without_sfc)
    for sfc_ref in (
        "SfcConditionsOptions", "SfcCandidateScopeCreate", "SfcCandidateScopeView",
        '"/sfc/conditions/options"', '"/sfc/candidate-scopes"',
    ):
        assert sfc_ref not in without_sfc
    assert '"/tlc/reverse-solvent"' in without_sfc
    assert '"/prediction/types"' in without_sfc
    _assert_response_model_names_defined(without_sfc)

    with_sfc = _trim_prediction_router(source, {"sfc", "tlc"})
    ast.parse(with_sfc)
    assert '"/sfc/conditions/options"' in with_sfc
    assert '"/sfc/candidate-scopes"' in with_sfc
    assert "SfcConditionsOptions" in with_sfc
    assert '"/tlc/reverse-solvent"' in with_sfc
    _assert_response_model_names_defined(with_sfc)

    gc_only = _trim_prediction_router(source, {"gc"})
    ast.parse(gc_only)
    for absent in ("Tlc", "Sfc"):
        assert absent not in gc_only
    assert not any(path.startswith(("/tlc/", "/sfc/")) for path in _route_paths(gc_only))
    assert '"/prediction/types"' in gc_only
    _assert_response_model_names_defined(gc_only)

    tlc_only = _trim_prediction_router(source, {"tlc"})
    ast.parse(tlc_only)
    assert '"/tlc/reverse-solvent"' in tlc_only
    assert '"/sfc/conditions/options"' not in tlc_only
    assert "SfcConditionsOptions" not in tlc_only
    _assert_response_model_names_defined(tlc_only)

    sfc_only = _trim_prediction_router(source, {"sfc"})
    ast.parse(sfc_only)
    assert '"/sfc/conditions/options"' in sfc_only
    assert '"/sfc/candidate-scopes"' in sfc_only
    assert '"/tlc/reverse-solvent"' not in sfc_only
    assert "TlcConditionOptions" not in sfc_only
    _assert_response_model_names_defined(sfc_only)


def _assert_response_model_names_defined(source: str) -> None:
    tree = ast.parse(source)
    module_names = set(dir(builtins))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            module_names.add(node.name)
        elif isinstance(node, ast.Import):
            module_names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module_names.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Assign):
            module_names.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            module_names.add(node.target.id)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            response_model = next((kw.value for kw in decorator.keywords if kw.arg == "response_model"), None)
            if response_model is not None:
                referenced = {item.id for item in ast.walk(response_model) if isinstance(item, ast.Name)}
                assert referenced <= module_names, (node.name, sorted(referenced - module_names))


def _route_paths(source: str) -> set[str]:
    tree = ast.parse(source)
    paths = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not decorator.args:
                continue
            path = decorator.args[0]
            if isinstance(path, ast.Constant) and isinstance(path.value, str):
                paths.add(path.value)
    return paths
