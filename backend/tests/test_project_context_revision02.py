from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chrom_platform.app import create_app
from chrom_platform.core.dto import ActorContext
from chrom_platform.core.projects import ConfiguredProjectReader, ProjectSelectionError, resolve_project
from chrom_platform.modules.prediction.schemas import CcPredictionCreate


ROOT = Path(__file__).resolve().parents[2]


def _app(monkeypatch, project_ids: str, default: str | None = None):
    monkeypatch.setenv("CHROM_PROJECT_IDS", project_ids)
    if default is None:
        monkeypatch.delenv("CHROM_DEFAULT_PROJECT_ID", raising=False)
    else:
        monkeypatch.setenv("CHROM_DEFAULT_PROJECT_ID", default)
    return create_app(ROOT / "builds" / "minimal-structure-v1.json", contract_generation=True)


def test_access_context_single_project_is_server_bound(monkeypatch):
    response = TestClient(_app(monkeypatch, "project-a")).get("/api/v1/system/access-context")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["actor"]["id"] == "deployment-default"
    assert data["tenant"] is None
    assert data["permissions"] == ["*"]
    assert data["accessible_projects"] == [{"id": "project-a", "name": "project-a"}]
    assert data["default_project_id"] == "project-a"
    assert data["project_selection"] == "resolved"


def test_access_context_explicit_default_wins(monkeypatch):
    response = TestClient(_app(monkeypatch, "project-a,project-b", "project-b")).get(
        "/api/v1/system/access-context"
    )
    assert response.status_code == 200
    assert response.json()["data"]["default_project_id"] == "project-b"


def test_access_context_multiple_projects_requires_selection(monkeypatch):
    response = TestClient(_app(monkeypatch, "project-a,project-b")).get("/api/v1/system/access-context")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["default_project_id"] is None
    assert data["project_selection"] == "needs_selection"


def test_project_resolver_rejects_illegal_project_and_multi_selection():
    actor = ActorContext(
        actor_id="actor",
        project_ids=["project-a", "project-b"],
        auth_method="deployment_default",
        permissions=["*"],
        policy_version="test",
    )
    reader = ConfiguredProjectReader(actor.project_ids)
    with pytest.raises(ProjectSelectionError) as selection:
        resolve_project(actor, reader)
    assert selection.value.status_code == 422
    assert selection.value.details["selection"] == "needs_selection"
    with pytest.raises(ProjectSelectionError) as illegal:
        resolve_project(actor, reader, "project-forged")
    assert illegal.value.status_code == 404
    assert illegal.value.code == "RESOURCE_NOT_FOUND"


def test_configured_project_reader_initialization_is_idempotent():
    actor = ActorContext(
        actor_id="actor",
        project_ids=["project-a", "project-a"],
        auth_method="deployment_default",
        permissions=["*"],
        policy_version="test",
    )
    reader = ConfiguredProjectReader(actor.project_ids)
    first = list(reader.list_accessible(actor, limit=201, after_id=None))
    second = list(reader.list_accessible(actor, limit=201, after_id=None))
    assert [(item.id, item.name) for item in first] == [("project-a", "project-a")]
    assert [(item.id, item.name) for item in second] == [("project-a", "project-a")]


def test_cc_requires_two_distinct_structures():
    base = {
        "project_id": "project-a",
        "schema_version": "1.0",
        "model_selection": {"mode": "champion", "scope_id": "cc-default"},
        "chromatography_type": "cc",
        "tool": "single",
        "input": {
            "eluent_system_id": "hexane-ea",
            "loading_solvent_id": "ethyl-acetate",
            "sample_mass_mg": 1.0,
            "loading_solvent_volume_ul": 100.0,
            "flow_rate_ml_min": 1.0,
            "column_spec_id": "silica-40g",
        },
    }
    with pytest.raises(ValueError):
        CcPredictionCreate.model_validate({**base, "structure_refs": ["a"]})
    with pytest.raises(ValueError):
        CcPredictionCreate.model_validate({**base, "structure_refs": ["a", "a"]})
    value = CcPredictionCreate.model_validate({**base, "structure_refs": ["a", "b"]})
    assert value.structure_refs == ["a", "b"]
