from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from chrom_platform.app import create_app
from chrom_platform.assembly.m1 import M1Assembly, assemble_m1


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "builds" / "minimal-structure-v1.json"
ERROR_CODES = ("400", "401", "403", "404", "409", "410", "412", "413", "422", "428", "429", "500", "503")


def _paths(monkeypatch, *, contract_generation: bool) -> dict:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ARTIFACT_ROOT", raising=False)
    return create_app(PROFILE, contract_generation=contract_generation).openapi()["paths"]


def test_m1_routes_are_fixed_and_unconfigured_ports_return_503(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "must-not-be-created"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ARTIFACT_ROOT", str(root))
    app = create_app(PROFILE)
    paths = app.openapi()["paths"]

    assert "/api/v1/structures/normalize" in paths
    assert "/api/v1/artifacts/{artifact_id}" in paths
    assert "/api/v1/jobs" in paths
    assert "post" not in paths["/api/v1/jobs"]
    assert not root.exists()

    client = TestClient(app)
    artifact = client.get("/api/v1/artifacts/art_missing")
    jobs = client.get("/api/v1/jobs", params={"project_id": "project-a"})
    assert artifact.status_code == 503
    assert jobs.status_code == 503
    assert artifact.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert jobs.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


def test_contract_generation_does_not_change_runtime_route_shape(monkeypatch) -> None:
    runtime = set(_paths(monkeypatch, contract_generation=False))
    contract = set(_paths(monkeypatch, contract_generation=True))
    assert runtime == contract


def test_explicit_artifact_root_is_wired_only_with_database_assembly(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    factory = lambda: None
    assembly = assemble_m1(factory, artifact_root=str(root))
    assert root.is_dir()
    assert assembly.session_factory is factory
    assert assembly.artifact_reader is not None
    assert assembly.artifact_service is not None
    assert assembly.artifact_uow_factory is not None


def test_app_wires_artifact_root_and_does_not_accept_legacy_alias(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_assemble(factory, *, artifact_root):
        captured["factory"] = factory
        captured["artifact_root"] = artifact_root
        return M1Assembly(session_factory=factory)

    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://u:p@localhost/db")
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path / "documented"))
    monkeypatch.setenv("ARTIFACT_STORAGE_ROOT", str(tmp_path / "legacy"))
    monkeypatch.setattr("chrom_platform.app.assemble_m1", fake_assemble)
    create_app(PROFILE)
    assert captured["artifact_root"] == str(tmp_path / "documented")


def test_candidate_openapi_error_refs_and_mutating_headers(monkeypatch) -> None:
    app = create_app(PROFILE, contract_generation=True)
    document = app.openapi()
    assert "/api/v1/jobs" in document["paths"]
    assert "post" not in document["paths"]["/api/v1/jobs"]

    def walk(value):
        if isinstance(value, dict):
            reference = value.get("$ref")
            if reference and reference.startswith("#/components/"):
                target = document
                for part in reference[2:].split("/"):
                    target = target[part]
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(document)
    for path_item in document["paths"].values():
        for operation in path_item.values():
            if not isinstance(operation, dict) or "responses" not in operation:
                continue
            for code in ERROR_CODES:
                assert operation["responses"][code]["content"]["application/json"]["schema"] == {
                    "$ref": "#/components/schemas/ErrorEnvelope"
                }
            parameters = operation.get("parameters", [])
            for header in ("If-Match", "Idempotency-Key"):
                matches = [item for item in parameters if item.get("in") == "header" and item.get("name") == header]
                assert len(matches) <= 1
                if matches:
                    assert matches[0]["required"] is True
                    assert "nullable" not in matches[0]["schema"]
