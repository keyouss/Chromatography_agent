from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from chrom_platform.core.dto import ActorContext
from chrom_platform.core.errors import APIError, InputValidationError
from chrom_platform.core.idempotency import BeginResult, IdempotencyRecord
from chrom_platform.modules.prediction.ports import PredictionCreateResult
from chrom_platform.modules.prediction.router import create_router
from chrom_platform.modules.prediction.schemas import PredictionAccepted
from chrom_platform.task_core.state import JobStatus


BODY = {
    "project_id": "project-a",
    "chromatography_type": "tlc",
    "tool": "single",
    "schema_version": "1.0",
    "model_selection": {"mode": "champion", "scope_id": "scope-a"},
    "structure_refs": ["structure-a"],
    "input": {
        "solvent_components": [
            {"solvent_id": "hexane", "fraction": 0.8},
            {"solvent_id": "ethyl_acetate", "fraction": 0.2},
        ]
    },
}


def _app(*, service=None) -> FastAPI:
    app = FastAPI()
    app.state.actor_context = ActorContext(
        actor_id="actor-a", tenant_id=None, project_ids=["project-a"],
        auth_method="deployment_default", permissions=["prediction:create"], policy_version="p1",
    )

    @app.exception_handler(APIError)
    async def api_error(_request, exc: APIError):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.payload.model_dump(mode="json")})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, _exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": {"code": "VALIDATION_FAILED"}})

    app.include_router(create_router(prediction_service=service), prefix="/api/v1")
    return app


class FakePredictionService:
    def __init__(self):
        self.calls = []

    def create(self, _ctx, payload, *, idempotency_key):
        self.calls.append((payload, idempotency_key))
        replayed = len(self.calls) > 1
        accepted = PredictionAccepted(
            id="prediction-a", prediction_record_id="record-a", job_id="job-a",
            status=JobStatus.QUEUED,
            resource_ref={"type": "prediction", "id": "prediction-a"},
        )
        return PredictionCreateResult(accepted, replayed=replayed)


def test_router_has_no_in_memory_fallback_and_requires_dependency() -> None:
    client = TestClient(_app(), raise_server_exceptions=False)
    response = client.post("/api/v1/predictions", headers={"Idempotency-Key": "k1"}, json=BODY)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "PREDICTION_DEPENDENCY_UNAVAILABLE"


def test_create_requires_idempotency_returns_location_and_replay_header() -> None:
    service = FakePredictionService()
    client = TestClient(_app(service=service))
    missing = client.post("/api/v1/predictions", json=BODY)
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"

    first = client.post("/api/v1/predictions", headers={"Idempotency-Key": "k1"}, json=BODY)
    second = client.post("/api/v1/predictions", headers={"Idempotency-Key": "k1"}, json=BODY)
    assert first.status_code == second.status_code == 202
    assert first.headers["Location"] == "/api/v1/predictions/prediction-a"
    assert second.headers["Location"] == first.headers["Location"]
    assert second.headers["Idempotency-Replayed"] == "true"
    assert client.app.openapi()["paths"]["/api/v1/predictions"]["post"]["responses"]["202"]["headers"]["Location"]["required"] is True


def test_tlc_async_operations_declare_location_header_in_openapi() -> None:
    document = _app().openapi()
    for path in ("/api/v1/tlc/condition-comparisons", "/api/v1/tlc/reverse-solvent"):
        location = document["paths"][path]["post"]["responses"]["202"]["headers"]["Location"]
        assert location["required"] is True
        assert location["schema"]["format"] == "uri-reference"


def test_prediction_request_extra_fields_are_rejected() -> None:
    client = TestClient(_app(service=FakePredictionService()))
    response = client.post(
        "/api/v1/predictions", headers={"Idempotency-Key": "k1"}, json={**BODY, "tenant_id": "forged"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


def test_unsupported_prediction_schema_version_returns_typed_422() -> None:
    class SchemaRegistry:
        def get_tool(self, _type, _tool, _schema_version):
            raise InputValidationError("prediction tool or schema is not supported")

    app = FastAPI()
    @app.exception_handler(APIError)
    async def api_error(_request, exc: APIError):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.payload.model_dump(mode="json")})

    app.include_router(create_router(registry=SchemaRegistry()), prefix="/api/v1")
    response = TestClient(app).get(
        "/api/v1/prediction/types/sfc/tools/full_column/schema?schema_version=sfc-v1.1"
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"
    assert "sfc-v1.1" not in response.text


def test_http_rejects_non_unit_tlc_ratios_before_service_dispatch() -> None:
    service = FakePredictionService()
    client = TestClient(_app(service=service))
    for components in (
        [
            {"solvent_id": "hexane", "fraction": 2.0},
            {"solvent_id": "ethyl_acetate", "fraction": 0.0},
        ],
        [
            {"solvent_id": "hexane", "fraction": 0.2},
            {"solvent_id": "ethyl_acetate", "fraction": 0.2},
        ],
        [
            {"solvent_id": "petroleum_ether", "fraction": 0.8},
            {"solvent_id": "ethyl_acetate", "fraction": 0.2},
        ],
    ):
        response = client.post(
            "/api/v1/predictions",
            headers={"Idempotency-Key": "invalid-tlc"},
            json={**BODY, "input": {"solvent_components": components}},
        )
        assert response.status_code == 422
    assert service.calls == []


class _ScopeStore:
    def __init__(self):
        self.records = {}

    def get(self, scope):
        return self.records.get(scope)

    def begin(self, scope, payload_hash):
        if scope in self.records:
            return BeginResult(False, self.records[scope])
        record = IdempotencyRecord(payload_hash=payload_hash, status="IN_PROGRESS")
        self.records[scope] = record
        return BeginResult(True, record)

    def complete(self, scope, payload_hash, status_code, response):
        self.records[scope] = IdempotencyRecord(payload_hash=payload_hash, status="SUCCEEDED", status_code=status_code, response=response)

    def fail(self, scope, payload_hash, error_code):
        self.records.pop(scope, None)


class _ScopeCatalog:
    def __init__(self):
        self.created_binding = None

    @staticmethod
    def _options(scope_id):
        return {
            "scope_id": scope_id, "candidate_count": 1, "max_candidates": 1,
            "fields": [{"key": "column", "label": "Column", "unit": None, "values": ["c"]}],
        }

    def scope_binding(self, _scope_id):
        return {"project_id": "project-a", "model_id": "model-1", "model_version": "v1"}

    def options(self, scope_id, *, project_id, model_id, model_version):
        return self._options(scope_id)

    def options_for_model(self, _domain):
        return self._options(None)

    def create_scope(self, _axes, *, project_id, model_id, model_version, max_candidates, expansion_mode):
        self.created_binding = (project_id, model_id, model_version)
        return {
            "id": "scope-1", "project_id": project_id, "model_id": model_id, "model_version": model_version,
            "candidate_count": 1, "max_candidates": max_candidates,
            "expansion": "server_cartesian_or_historical_distinct_rows", "truncated": False,
            "conditions": [{"column": "c", "stationary_phase": "p", "mode": "normal", "length_mm": 1.0, "inner_diameter_mm": 1.0, "particle_size_um": 1.0, "column_temperature_c": 1.0, "flow_rate_ml_min": 1.0}],
        }


class _SfcAdapter:
    candidate_catalog = _ScopeCatalog()
    verified_handle = SimpleNamespace(
        adapter_id="sfc", model_id="model-1", model_version="v1", business_scope_id="sfc-scope",
        artifact_sha256="a" * 64, input_schema_version="1.1", output_schema_version="1.0",
        schema_by_tool={"single_column": {}},
    )
    runtime = SimpleNamespace(
        bound_model_id="model-1", bound_model_version="v1", allowed_tool="single_column",
        registry=SimpleNamespace(entry=lambda _model_id, version: SimpleNamespace(
            model_id="model-1", version=version, scope="single_column", allowed_columns=("c",),
            stationary_phase="p", mode="normal", single_column_slug="amylose", single_column_group="Amylose",
        )),
    )


class _SfcRegistry:
    def capability(self, _type):
        from chrom_platform.modules.prediction.adapters import SfcSingleColumnDomain
        health = SimpleNamespace(status="READY")
        model = SimpleNamespace(
            id="model-1", version="v1", sfc_domain=SfcSingleColumnDomain(
                model_scope="single_column", group_id="amylose", group_label="Amylose",
                stationary_phase="p", mode="normal", allowed_columns=["c"],
            ),
            health=health, artifact_sha256="a" * 64, threshold="0.5",
            business_scope_id="sfc-scope", input_schema_version="1.1", output_schema_version="1.0",
        )
        return SimpleNamespace(health=health, models=[model], tools=[SimpleNamespace(id="single_column", schema_version="1.1", health=health)])

    def adapter(self, _type):
        return _SfcAdapter()


def test_sfc_candidate_scope_requires_idempotency_and_replays_exact_payload() -> None:
    app = FastAPI()
    app.state.actor_context = ActorContext(actor_id="actor-a", project_ids=["project-a"], auth_method="service", permissions=["prediction:create"], policy_version="p1")

    @app.exception_handler(APIError)
    async def api_error(_request, exc: APIError):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.payload.model_dump(mode="json")})

    app.include_router(create_router(registry=_SfcRegistry(), idempotency_store=_ScopeStore()), prefix="/api/v1")
    client = TestClient(app)
    body = {
        "model_selection": {"mode": "exact", "model_id": "model-1", "model_version": "v1"},
        "axes": {"column": ["c"]}, "max_candidates": 1,
    }
    first = client.post("/api/v1/sfc/candidate-scopes", headers={"Idempotency-Key": "scope-key"}, json=body)
    replay = client.post("/api/v1/sfc/candidate-scopes", headers={"Idempotency-Key": "scope-key"}, json=body)
    mismatch = client.post("/api/v1/sfc/candidate-scopes", headers={"Idempotency-Key": "scope-key"}, json={"model_selection": {"mode": "exact", "model_id": "model-1", "model_version": "v1"}, "axes": {"column": ["other"]}, "max_candidates": 1})
    unknown_model = client.post("/api/v1/sfc/candidate-scopes", headers={"Idempotency-Key": "other-scope-key"}, json={"model_selection": {"mode": "exact", "model_id": "unknown-model", "model_version": "v1"}, "axes": {"column": ["c"]}, "max_candidates": 1})
    assert first.status_code == 200, first.text
    assert replay.status_code == 200 and replay.headers["Idempotency-Replayed"] == "true"
    assert mismatch.status_code == 409 and mismatch.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert unknown_model.status_code == 422
    assert _SfcAdapter.candidate_catalog.created_binding == ("project-a", "model-1", "v1")


def test_sfc_options_requires_exactly_one_selector_and_supports_model_mode() -> None:
    app = FastAPI()
    app.state.actor_context = ActorContext(actor_id="actor-a", project_ids=["project-a"], auth_method="service", permissions=["prediction:create", "prediction:read"], policy_version="p1")

    @app.exception_handler(APIError)
    async def api_error(_request, exc: APIError):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.payload.model_dump(mode="json")})

    app.include_router(create_router(registry=_SfcRegistry()), prefix="/api/v1")
    client = TestClient(app)
    assert client.get("/api/v1/sfc/conditions/options").status_code == 422
    assert client.get("/api/v1/sfc/conditions/options?model_id=model-1").status_code == 422
    response = client.get("/api/v1/sfc/conditions/options?model_id=model-1&model_version=v1")
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["scope_id"] is None
    assert payload["model_id"] == "model-1" and payload["model_version"] == "v1"
    assert payload["threshold"] == "0.5"
    assert payload["candidate_count"] == 1
    scope_response = client.get("/api/v1/sfc/conditions/options?scope_id=scope-1")
    assert scope_response.status_code == 200
    assert scope_response.json()["data"]["scope_id"] == "scope-1"
