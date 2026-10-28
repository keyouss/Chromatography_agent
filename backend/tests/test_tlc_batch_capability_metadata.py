from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from chrom_platform.core.dto import CapabilityHealth
from chrom_platform.core.errors import APIError
from chrom_platform.modules.prediction.adapters import (
    AdapterManifest,
    CapabilityRegistry,
    ModelVerificationEvidence,
    ModelOption,
    PredictionLimits,
    StructureCount,
    ToolCapability,
    TypeCapability,
    issue_verified_model_handle,
)
from chrom_platform.adapters.tlc.adapter import TlcPredictionAdapter
from chrom_platform.adapters.tlc.manifest import (
    TLC_ARTIFACT_ID,
    TLC_ARTIFACT_MANIFEST_SHA256,
    TLC_INPUT_SCHEMA_VERSION,
    TLC_MODEL_VERSION,
    TLC_OUTPUT_SCHEMA_VERSION,
    TLC_SCOPE_ID,
)
from chrom_platform.modules.prediction.batch import (
    BatchResourceBudget,
    BatchTemplateDefinition,
    TlcBatchCompatibleModel,
    TlcBatchModelConstraints,
    TlcBatchRow,
    TlcBatchRowV2,
)
from chrom_platform.modules.prediction.batch_health import build_tlc_batch_model_constraints
from chrom_platform.modules.prediction.router import NonBatchToolCapability, TlcBatchToolCapability, create_router
from chrom_platform.worker_cli import ProfileWorkerPlan
from chrom_platform.assembly.profile import (
    _trim_app_source,
    _trim_m2_batch_wiring,
    _trim_prediction_router,
    _trim_worker_cli,
)


_NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)
_READY = CapabilityHealth(status="READY", checked_at=_NOW)
_UNKNOWN = CapabilityHealth(status="UNKNOWN", reason="MODEL_CHECK_DEFERRED", checked_at=_NOW)


class _Registry:
    def __init__(self) -> None:
        self._tlc = TypeCapability(
            id="tlc",
            display_name="TLC",
            tools=[
                ToolCapability(
                    id="single",
                    schema_version="1.0",
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    structure_count=StructureCount(min=1, max=1, semantics="single"),
                    model_constraints={},
                    units={},
                    supported_visualizations=[],
                    limits=PredictionLimits(max_rows=1, max_candidates=1, max_evaluations=1),
                    health=_READY,
                )
            ],
            models=[],
            limits=PredictionLimits(max_rows=1, max_candidates=1, max_evaluations=1),
            health=_READY,
        )

    def capabilities(self):
        return [self._tlc]

    def capability(self, type_id: str):
        if type_id != "tlc":
            raise KeyError(type_id)
        return self._tlc

    def get_tool(self, type_id: str, tool_id: str, _schema_version: str):
        if type_id == "tlc" and tool_id == "single":
            return self._tlc.tools[0]
        raise KeyError((type_id, tool_id))


def _templates() -> list[BatchTemplateDefinition]:
    return [
        BatchTemplateDefinition(
            id="tlc-batch-basic",
            chromatography_type="tlc",
            tool="batch",
            version="tlc-batch-1",
            row_schema="tlc-batch-row-v1",
            fields=["smiles"],
            limits=BatchResourceBudget(max_runtime_seconds=3600, max_evaluations=100_000),
            health=_UNKNOWN,
        )
    ]


def _client(*, tools: dict[str, list[str]], provider=None, registry=None) -> TestClient:
    app = FastAPI()
    app.state.manifest = SimpleNamespace(prediction_tools=tools)

    @app.exception_handler(APIError)
    async def api_error(_request, exc: APIError):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.payload.model_dump(mode="json")})

    app.include_router(
        create_router(registry=registry or _Registry(), enabled_types={"tlc"}, batch_metadata_provider=provider),
        prefix="/api/v1",
    )
    return TestClient(app)


def test_tlc_selected_batch_metadata_is_typed_and_keeps_unknown_health() -> None:
    client = _client(tools={"tlc": ["single", "batch"]}, provider=lambda kind: _templates() if kind == "tlc" else [])

    listed = client.get("/api/v1/prediction/types")
    assert listed.status_code == 200
    assert listed.json()["data"][0]["tools"] == ["single", "batch"]

    capability = client.get("/api/v1/prediction/types/tlc")
    assert capability.status_code == 200
    batch_tool = next(item for item in capability.json()["data"]["tools"] if item["id"] == "batch")
    assert batch_tool["health"]["status"] == "UNKNOWN"
    assert batch_tool["chromatography_type"] == "tlc"
    assert batch_tool["model_constraints"] == {"template_versions": ["tlc-batch-1"], "compatible_models": []}
    assert batch_tool["templates"][0]["version"] == "tlc-batch-1"
    assert batch_tool["templates"][0]["tlc_options"] is None

    schema = client.get("/api/v1/prediction/types/tlc/tools/batch/schema")
    assert schema.status_code == 200
    assert schema.json()["data"]["input_schema"]["properties"]["chromatography_type"]["const"] == "tlc"
    assert client.get("/api/v1/prediction/types/tlc/tools/batch/schema?schema_version=2.0").status_code == 422
    openapi = client.app.openapi()
    operation = openapi["paths"]["/api/v1/prediction/types/{type}"]["get"]
    response_ref = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert "BatchAwareTypeCapability" in response_ref
    batch_schema = openapi["components"]["schemas"]["TlcBatchToolCapability"]
    assert batch_schema["properties"]["id"]["const"] == "batch"
    assert batch_schema["properties"]["chromatography_type"]["const"] == "tlc"
    assert batch_schema["properties"]["templates"]["items"]["$ref"].endswith("/BatchTemplateDefinition")
    assert batch_schema["properties"]["model_constraints"]["$ref"].endswith("/TlcBatchModelConstraints")
    assert "BatchTemplateDefinition" in openapi["components"]["schemas"]
    assert "TlcBatchModelConstraints" in openapi["components"]["schemas"]
    nonbatch_schema = openapi["components"]["schemas"]["NonBatchToolCapability"]
    assert nonbatch_schema["properties"]["id"]["maxLength"] == 96
    assert nonbatch_schema["properties"]["id"]["not"] == {"const": "batch"}
    type_tools = openapi["components"]["schemas"]["BatchAwareTypeCapability"]["properties"]["tools"]["items"]
    assert [item["$ref"].rsplit("/", 1)[-1] for item in type_tools["oneOf"]] == [
        "TlcBatchToolCapability", "NonBatchToolCapability"
    ]
    schema_ref = openapi["paths"]["/api/v1/prediction/types/{type}/tools/{tool}/schema"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    schema_name = schema_ref.rsplit("/", 1)[-1]
    schema_data = openapi["components"]["schemas"][schema_name]["properties"]["data"]
    assert [item["$ref"].rsplit("/", 1)[-1] for item in schema_data["oneOf"]] == [
        "TlcBatchToolCapability", "NonBatchToolCapability"
    ]
    for invalid in (
        {**batch_tool, "id": "other"},
        {**batch_tool, "chromatography_type": "gc"},
        {key: value for key, value in batch_tool.items() if key != "chromatography_type"},
    ):
        with pytest.raises(ValidationError):
            TlcBatchToolCapability.model_validate(invalid)


def test_batch_metadata_missing_or_unselected_fails_closed_without_cross_type_exposure() -> None:
    selected_without_provider = _client(tools={"tlc": ["single", "batch"]})
    missing = selected_without_provider.get("/api/v1/prediction/types")
    assert missing.status_code == 503

    provider_calls: list[str] = []
    unselected = _client(
        tools={"tlc": ["single"], "gc": ["batch"]},
        provider=lambda kind: provider_calls.append(kind) or _templates(),
    )
    assert unselected.get("/api/v1/prediction/types/tlc/tools/batch/schema").status_code == 404
    for type_id in ("gc", "cc", "hplc", "sfc"):
        assert unselected.get(f"/api/v1/prediction/types/{type_id}/tools/batch/schema").status_code == 404
    tlc = unselected.get("/api/v1/prediction/types/tlc")
    assert tlc.status_code == 200
    assert all(item["id"] != "batch" for item in tlc.json()["data"]["tools"])
    assert provider_calls == []


def test_batch_aware_union_rejects_generic_batch_fallback_and_keeps_single_tool() -> None:
    registry = _Registry()
    generic_batch = registry._tlc.tools[0].model_copy(update={"id": "batch"})
    registry._tlc = registry._tlc.model_copy(update={"tools": [*registry._tlc.tools, generic_batch]})
    client = _client(
        tools={"tlc": ["single", "batch"]},
        provider=lambda kind: _templates() if kind == "tlc" else [],
        registry=registry,
    )

    # Generic batch metadata cannot satisfy either branch of the batch-aware union.
    response = client.get("/api/v1/prediction/types/tlc")
    assert response.status_code == 503
    assert client.get("/api/v1/prediction/types/tlc/tools/single/schema").status_code == 200
    with pytest.raises(ValidationError):
        NonBatchToolCapability.model_validate(generic_batch.model_dump(mode="python"))
    assert NonBatchToolCapability.model_validate(registry._tlc.tools[0].model_dump(mode="python")).id == "single"


def test_nonbatch_router_trim_removes_optional_dtos_routes_and_imports() -> None:
    root = Path(__file__).resolve().parents[1]
    router_source = (root / "src/chrom_platform/modules/prediction/router.py").read_text(encoding="utf-8")
    m2_source = (root / "src/chrom_platform/assembly/m2.py").read_text(encoding="utf-8")
    app_source = (root / "src/chrom_platform/app.py").read_text(encoding="utf-8")
    worker_source = (root / "src/chrom_platform/worker_cli.py").read_text(encoding="utf-8")

    trimmed_router = _trim_prediction_router(router_source, {"tlc"}, batch_enabled=False)
    compile(trimmed_router, "trimmed_prediction_router.py", "exec")
    assert "from .batch import" not in trimmed_router
    assert "BatchTemplateDefinition" not in trimmed_router
    assert "TlcBatchToolCapability" not in trimmed_router
    assert "TlcBatchCapabilityUnion" not in trimmed_router
    assert 'tool == "batch"' not in trimmed_router
    assert "/prediction/types/{type}/tools/{tool}/schema" in trimmed_router

    kept_router = _trim_prediction_router(router_source, {"tlc"}, batch_enabled=True)
    assert "from .batch import (" in kept_router
    assert "    BatchPrediction," in kept_router
    assert "BatchTemplateDefinition" in kept_router
    assert "TlcBatchToolCapability" in kept_router
    assert "TlcBatchCapabilityUnion" in kept_router

    trimmed_m2 = _trim_m2_batch_wiring(m2_source, batch_enabled=False)
    compile(trimmed_m2, "trimmed_m2.py", "exec")
    assert "batch_service" not in trimmed_m2
    assert "batch_metadata_provider" not in trimmed_m2
    assert "batch_router" not in trimmed_m2

    trimmed_app = _trim_app_source(app_source, {"tlc"}, batch_enabled=False)
    compile(trimmed_app, "trimmed_app.py", "exec")
    assert "enabled_batch_types" not in trimmed_app

    trimmed_worker = _trim_worker_cli(worker_source, {"tlc"}, batch_enabled=False)
    compile(trimmed_worker, "trimmed_worker_cli.py", "exec")
    assert "enabled_batch_types" not in trimmed_worker

    assert "enabled_batch_types" in _trim_m2_batch_wiring(m2_source, batch_enabled=True)
    assert "enabled_batch_types" in _trim_app_source(app_source, {"tlc"}, batch_enabled=True)
    assert "enabled_batch_types" in _trim_worker_cli(worker_source, {"tlc"}, batch_enabled=True)
    assert 'enabled_batch_types=frozenset(kind for kind, tools in manifest.prediction_tools.items() if "batch" in tools)' in app_source
    assert "enabled_batch_types=assembly.enabled_batch_types" in m2_source
    assert 'enabled_batch_types=getattr(plan, "enabled_batch_types", frozenset())' in worker_source


def test_worker_batch_allowlist_comes_from_exact_manifest_types(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "builds/universal-v1.json").read_text(encoding="utf-8"))
    manifest["prediction_tools"]["tlc"].append("batch")
    manifest["prediction_tools"]["gc"].append("batch")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    plan = ProfileWorkerPlan.from_manifest(manifest_path, expected_profile="universal-v1")
    assert plan.batch_enabled is True
    assert plan.enabled_batch_types == frozenset({"tlc", "gc"})


def _verified_tlc_registry() -> CapabilityRegistry:
    from chrom_platform.adapters.tlc.schema import (
        TlcConditionComparisonCreate,
        TlcConditionComparisonResult,
        TlcReverseCreate,
        TlcReverseResult,
    )
    from chrom_platform.modules.prediction.schemas import TlcPredictionCreate, TlcPredictionResult

    schema_adapter = TlcPredictionAdapter()
    schema_by_tool = {
        "single": {
            "input_schema": TlcPredictionCreate.model_json_schema(),
            "output_schema": TlcPredictionResult.model_json_schema(),
        },
        "condition_compare": {
            "input_schema": TlcConditionComparisonCreate.model_json_schema(),
            "output_schema": TlcConditionComparisonResult.model_json_schema(),
        },
        "reverse_solvent": {
            "input_schema": TlcReverseCreate.model_json_schema(),
            "output_schema": TlcReverseResult.model_json_schema(),
        },
    }
    manifest = AdapterManifest(
        adapter_id="tlc",
        model_id=TLC_ARTIFACT_ID,
        model_version=TLC_MODEL_VERSION,
        business_scope_id=TLC_SCOPE_ID,
        manifest_version="1",
        input_schema_version=TLC_INPUT_SCHEMA_VERSION,
        output_schema_version=TLC_OUTPUT_SCHEMA_VERSION,
        artifact_sha256=TLC_ARTIFACT_MANIFEST_SHA256,
        schema_available=True,
        assets_available=True,
        data_origin="production",
    )
    evidence = ModelVerificationEvidence(
        adapter_id="tlc",
        model_id=TLC_ARTIFACT_ID,
        model_version=TLC_MODEL_VERSION,
        business_scope_id=TLC_SCOPE_ID,
        input_schema_version=TLC_INPUT_SCHEMA_VERSION,
        output_schema_version=TLC_OUTPUT_SCHEMA_VERSION,
        artifact_sha256=TLC_ARTIFACT_MANIFEST_SHA256,
        manifest_verified=True,
        schema_verified=True,
        artifact_sha_verified=True,
        environment_verified=True,
        golden_sample_verified=True,
        anomaly_checks_verified=True,
        schema_by_tool=schema_by_tool,
    )
    handle = issue_verified_model_handle(manifest, evidence)
    registry = CapabilityRegistry()
    registry.register(TlcPredictionAdapter(manifest=manifest, verified_handle=handle))
    return registry


def _compatible_templates() -> list[BatchTemplateDefinition]:
    limits = BatchResourceBudget(max_runtime_seconds=3600, max_evaluations=100_000)
    return [
        BatchTemplateDefinition(
            id="tlc_batch",
            chromatography_type="tlc",
            tool="batch",
            version="tlc-batch-1",
            row_schema=TlcBatchRow.__name__,
            fields=["structure_ref", "solvent_components"],
            limits=limits,
            health=_UNKNOWN,
        ),
        BatchTemplateDefinition(
            id="tlc_batch",
            chromatography_type="tlc",
            tool="batch",
            version="tlc-batch-2",
            row_schema=TlcBatchRowV2.__name__,
            fields=["smiles", "solvent_1", "fraction_1", "solvent_2", "fraction_2"],
            limits=limits,
            health=_UNKNOWN,
        ),
    ]


def test_tlc_binding_health_requires_exact_verified_schema_and_known_row_projection() -> None:
    registry = _verified_tlc_registry()
    constraints = build_tlc_batch_model_constraints(registry, _compatible_templates())
    assert constraints.template_versions == ["tlc-batch-1", "tlc-batch-2"]
    assert len(constraints.compatible_models) == 1
    binding = constraints.compatible_models[0]
    assert (binding.model_id, binding.model_version, binding.input_schema_version) == (
        TLC_ARTIFACT_ID,
        TLC_MODEL_VERSION,
        TLC_INPUT_SCHEMA_VERSION,
    )
    assert binding.template_versions == ["tlc-batch-1", "tlc-batch-2"]
    assert binding.health.status == "READY"
    assert all(template.health.status == "UNKNOWN" for template in _compatible_templates())

    unavailable_template = _compatible_templates()[0].model_copy(
        update={"health": CapabilityHealth(status="UNAVAILABLE", reason="TEMPLATE_DISABLED", checked_at=_NOW)}
    )
    unavailable_constraints = build_tlc_batch_model_constraints(
        registry, [unavailable_template, _compatible_templates()[1]]
    )
    assert unavailable_constraints.template_versions == ["tlc-batch-1", "tlc-batch-2"]
    assert unavailable_constraints.compatible_models[0].template_versions == ["tlc-batch-2"]

    unverified = CapabilityRegistry()
    unverified.register(TlcPredictionAdapter())
    assert build_tlc_batch_model_constraints(unverified, _compatible_templates()).compatible_models == []

    unknown_template = _compatible_templates()[0].model_copy(update={"version": "tlc-batch-new", "row_schema": "UnknownRows"})
    assert build_tlc_batch_model_constraints(registry, [unknown_template]).compatible_models == []


def test_tlc_model_constraint_dto_rejects_duplicate_triplets_and_unlisted_templates() -> None:
    binding = TlcBatchCompatibleModel(
        model_id=TLC_ARTIFACT_ID,
        model_version=TLC_MODEL_VERSION,
        input_schema_version=TLC_INPUT_SCHEMA_VERSION,
        template_versions=["tlc-batch-1"],
        health=_READY,
    )
    with pytest.raises(ValidationError):
        TlcBatchModelConstraints(
            template_versions=["tlc-batch-1"],
            compatible_models=[binding, binding],
        )
    with pytest.raises(ValidationError):
        TlcBatchModelConstraints(template_versions=["tlc-batch-1"], compatible_models=[
            binding.model_copy(update={"template_versions": ["tlc-batch-unknown"]})
        ])
    with pytest.raises(ValidationError):
        TlcBatchModelConstraints.model_validate({
            "template_versions": [],
            "compatible_models": [],
            "untrusted_model": "extra",
        })
