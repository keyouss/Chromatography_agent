from __future__ import annotations

from fastapi import FastAPI

from chrom_platform.modules.knowledge.router import create_router


def test_knowledge_archive_is_idempotent_only_and_lists_use_cursor_page() -> None:
    app = FastAPI()
    app.include_router(create_router())
    spec = app.openapi()
    archive = spec["paths"]["/knowledge/documents/{document_id}/archive"]["post"]
    headers = [item for item in archive["parameters"] if item["in"] == "header"]
    assert [(item["name"], item["required"]) for item in headers] == [("Idempotency-Key", True)]
    listing = spec["paths"]["/knowledge/documents"]["get"]
    assert "cursor" in {item["name"] for item in listing["parameters"]}
    assert "after_id" not in {item["name"] for item in listing["parameters"]}
    response = spec["paths"]["/knowledge/documents"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert response["$ref"].endswith("ListEnvelope_KnowledgeDocument_")
