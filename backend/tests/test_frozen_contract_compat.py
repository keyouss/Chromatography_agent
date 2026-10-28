"""M3/M4 candidate checks against read-only historical contract baselines."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from chrom_platform.core.dto import ActorContext
from chrom_platform.core.errors import APIError
from chrom_platform.modules.model_center.errors import ModelCenterPreconditionError
from chrom_platform.modules.model_center.router import create_router


ROOT = Path(__file__).resolve().parents[2]
M2_BASELINES = ROOT / "docs" / "contract-baselines" / "m2"
CONTRACTS = ROOT / "contracts"
SNAPSHOT = ROOT / "backend" / ".generated" / "snapshots" / "m3-m4-v1-candidate-20260921-05"
PROFILES = ("minimal-structure-v1", "sfc-v1", "cc-v1", "universal-v1")

# candidate-05 is an immutable historical snapshot.  Its universal profile
# was normalized at CB4F9B..., while the current active contract has since
# advanced.  Keep this compatibility gate tied to the matching rollback file
# instead of silently comparing an old snapshot with today's active contract.
CANDIDATE05_HISTORICAL_BASELINES = {
    "universal-v1": (
        CONTRACTS / "universal-v1" / "rollback-snapshots" /
        "revision-20261002-04-baseline" / "openapi.json",
        "CB4F9B92DDE639FC5188A4D898DE23EFB1E20B22733722BD50D7EFF580F84D1A",
    ),
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve(document: dict[str, Any], value: Any, seen: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict) and set(value) == {"$ref"}:
        name = str(value["$ref"]).rsplit("/", 1)[-1]
        if name in seen:
            return ("cycle", name)
        return _resolve(document, document["components"]["schemas"][name], seen + (name,))
    if isinstance(value, dict):
        return {key: _resolve(document, child, seen) for key, child in value.items()}
    if isinstance(value, list):
        return [_resolve(document, child, seen) for child in value]
    return value


def _candidate(profile: str) -> dict[str, Any]:
    return _load(SNAPSHOT / "openapi" / profile / "openapi.json")


def test_minimal_candidate_json_equals_frozen_contract() -> None:
    frozen = _load(M2_BASELINES / "minimal-structure-v1" / "openapi.json")
    assert _candidate("minimal-structure-v1") == frozen


@pytest.mark.parametrize("profile", ("sfc-v1", "cc-v1"))
def test_scientific_candidates_preserve_all_frozen_paths_operations_and_components(profile: str) -> None:
    frozen = _load(M2_BASELINES / profile / "openapi.json")
    candidate = _candidate(profile)
    for path, item in frozen["paths"].items():
        assert path in candidate["paths"]
        for method, operation in item.items():
            assert method in candidate["paths"][path]
            assert candidate["paths"][path][method] == operation
    for name, schema in frozen["components"]["schemas"].items():
        assert name in candidate["components"]["schemas"]
        assert candidate["components"]["schemas"][name] == schema


def test_universal_candidate_preserves_frozen_operations_after_resolving_renamed_schemas() -> None:
    frozen = _load(M2_BASELINES / "universal-v1" / "openapi.json")
    candidate = _candidate("universal-v1")
    for path, item in frozen["paths"].items():
        for method, operation in item.items():
            assert path in candidate["paths"] and method in candidate["paths"][path]
            assert _resolve(frozen, operation) == _resolve(candidate, candidate["paths"][path][method])


@pytest.mark.parametrize("profile", PROFILES)
def test_candidate05_matches_historical_contract_operations_after_resolving_schema_names(profile: str) -> None:
    # The universal candidate-05 baseline is intentionally historical.  The
    # other profiles did not change after candidate-05 and retain their
    # profile contract files as the compatibility baseline.
    baseline_path, expected_sha = CANDIDATE05_HISTORICAL_BASELINES.get(
        profile,
        (CONTRACTS / profile / "openapi.json", None),
    )
    assert baseline_path.is_file(), baseline_path
    if expected_sha is not None:
        actual_sha = hashlib.sha256(baseline_path.read_bytes()).hexdigest().upper()
        assert actual_sha == expected_sha, {
            "path": str(baseline_path),
            "expected_sha256": expected_sha,
            "actual_sha256": actual_sha,
        }
    frozen = _load(baseline_path)
    candidate = _candidate(profile)
    for path, item in frozen["paths"].items():
        for method, operation in item.items():
            assert path in candidate["paths"] and method in candidate["paths"][path]
            assert _resolve(frozen, operation) == _resolve(candidate, candidate["paths"][path][method])


def test_universal_m3_m4_write_headers_keep_runtime_bounds() -> None:
    document = _candidate("universal-v1")
    for path, item in document["paths"].items():
        if not (path.startswith("/api/v1/knowledge") or path.startswith("/api/v1/training") or path.startswith("/api/v1/models") or path.startswith("/api/v1/model-") or path.startswith("/api/v1/approval-") or path.startswith("/api/v1/conversations") or path.startswith("/api/v1/agent-runs")):
            continue
        for method, operation in item.items():
            if method not in {"post", "patch", "put", "delete"}:
                continue
            for parameter in operation.get("parameters", []):
                if parameter.get("in") != "header" or parameter.get("name") not in {"Idempotency-Key", "If-Match"}:
                    continue
                schema = parameter["schema"]
                assert parameter["required"] is True
                assert schema.get("type") == "string"
                assert schema.get("minLength") == 1
                expected_max = 128 if parameter["name"] == "Idempotency-Key" else 256
                assert schema.get("maxLength") == expected_max


class _ModelCenterService:
    def create_registration(self, *args: Any, **kwargs: Any) -> object:
        raise AssertionError("header validation should happen before service dispatch")

    def register(self, *args: Any, **kwargs: Any) -> object:
        raise ModelCenterPreconditionError("stale ETag")

    def queue_registration_validation(self, *args: Any, **kwargs: Any) -> object:
        raise ModelCenterPreconditionError("stale ETag")


def _model_center_client() -> TestClient:
    app = FastAPI()
    @app.exception_handler(APIError)
    async def _api_error(_: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.payload.model_dump(mode="json"), "request_id": "test"})

    app.state.actor_context = ActorContext(
        actor_id="actor-a", tenant_id=None, project_ids=["project-a"],
        auth_method="deployment_default", permissions=["*"], policy_version="test",
    )
    app.include_router(create_router(service=_ModelCenterService()), prefix="/api/v1")
    return TestClient(app)


def _registration_payload() -> dict[str, Any]:
    return {
        "project_id": "project-a", "source": {"kind": "external_package", "artifact_id": "artifact-a"},
        "name": "candidate", "task_type": "sfc_separation",
    }


def test_model_center_register_and_validate_header_http_constraints() -> None:
    client = _model_center_client()
    endpoint = "/api/v1/models/registrations"
    assert client.post(endpoint, json=_registration_payload()).status_code == 400
    assert client.post(endpoint, headers={"Idempotency-Key": ""}, json=_registration_payload()).status_code == 422
    assert client.post(endpoint, headers={"Idempotency-Key": "x" * 129}, json=_registration_payload()).status_code == 422

    register = "/api/v1/models/registrations/reg-a/register?project_id=project-a"
    body = {"confirmation_id": "confirmation-a"}
    # register is an M3 operation with an idempotency header only; its
    # controlled fixture proves the service is reached without an ETag gate.
    assert client.post(register, headers={"Idempotency-Key": "key-a"}, json=body).status_code == 412
    assert client.post(register, headers={"If-Match": "stale", "Idempotency-Key": "key-a"}, json=body).status_code == 412

    validate = "/api/v1/models/registrations/reg-a/validate?project_id=project-a"
    assert client.post(validate, headers={"Idempotency-Key": "key-a"}, json={}).status_code == 428
    assert client.post(validate, headers={"If-Match": "stale", "Idempotency-Key": "key-a"}, json={}).status_code == 412
    assert client.post(validate, headers={"If-Match": "", "Idempotency-Key": "key-a"}, json={}).status_code == 422
    assert client.post(validate, headers={"If-Match": "x" * 257, "Idempotency-Key": "key-a"}, json={}).status_code == 422
