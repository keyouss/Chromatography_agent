from __future__ import annotations

from fastapi import FastAPI

from chrom_platform.modules.training.router import create_router


def test_training_m34_routes_have_strict_bodies_headers_and_location() -> None:
    app = FastAPI()
    app.include_router(create_router())
    paths = app.openapi()["paths"]
    submit = paths["/training/experiments/{experiment_id}/submit"]["post"]
    submit_headers = [item["name"] for item in submit["parameters"] if item["in"] == "header"]
    assert submit_headers == ["Idempotency-Key"]
    assert "Location" in submit["responses"]["202"]["headers"]
    validate = paths["/training/experiments/{experiment_id}/validate"]["post"]
    assert validate["requestBody"]["required"] is True
    assert validate["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("EmptyRequest")
    cancel = paths["/training/experiments/{experiment_id}/cancel"]["post"]
    assert {item["name"] for item in cancel["parameters"] if item["in"] == "header"} == {"If-Match", "Idempotency-Key"}
    for suffix in ("events", "logs", "metrics", "artifacts"):
        assert f"/training/experiments/{{experiment_id}}/{suffix}" in paths
    assert "/training/comparisons" in paths
