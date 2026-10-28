from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chrom_platform.generate_openapi import generate


def _write_local_profiles(directory: Path) -> None:
    """Create contract-only manifests so backend tests need no repo-root files."""

    for build_id in ("profile-a", "profile-b", "profile-c", "profile-d"):
        (directory / f"{build_id}.json").write_text(
            json.dumps(
                {
                    "manifest_version": "1.0",
                    "build_id": build_id,
                    "modules": [],
                    "chromatography_types": [],
                    "prediction_tools": {},
                    "utilities": [],
                    "export_formats": [],
                    "auth_provider": "deployment_default",
                }
            ),
            encoding="utf-8",
        )


def _assert_local_refs_resolve(document: dict[str, Any]) -> None:
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/"):
                target: Any = document
                for part in reference[2:].split("/"):
                    part = part.replace("~1", "/").replace("~0", "~")
                    assert isinstance(target, dict) and part in target, f"unresolved local ref: {reference}"
                    target = target[part]
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(document)


def test_profile_openapi_contains_only_registered_v1_routes(tmp_path: Path) -> None:
    builds = tmp_path / "builds"
    builds.mkdir()
    _write_local_profiles(builds)
    outputs = generate(builds, tmp_path / "generated")
    assert len(outputs) == 4
    for output in outputs:
        document = json.loads(output.read_text(encoding="utf-8"))
        assert document["openapi"].startswith("3.1.")
        assert document["info"]["x-build-id"] == output.parent.name
        assert not any(
            path.startswith(prefix)
            for prefix in ("/api/v1/coding", "/api/v1/workspaces", "/api/v1/sandbox")
            for path in document["paths"]
        )
        assert "/api/v1/structures/normalize" in document["paths"]
        assert "HTTPValidationError" not in json.dumps(document)
        _assert_local_refs_resolve(document)
        for path_item in document["paths"].values():
            for operation in path_item.values():
                if not isinstance(operation, dict) or "responses" not in operation:
                    continue
                for code in ("400", "404", "409", "422", "503"):
                    assert operation["responses"][code]["content"]["application/json"]["schema"] == {
                        "$ref": "#/components/schemas/ErrorEnvelope"
                    }


def test_structure_normalize_requires_idempotency_header(tmp_path: Path) -> None:
    from chrom_platform.app import create_app

    _write_local_profiles(tmp_path)
    app = create_app(
        tmp_path / "profile-a.json",
        contract_generation=True,
    )
    operation = app.openapi()["paths"]["/api/v1/structures/normalize"]["post"]
    header = next(parameter for parameter in operation["parameters"] if parameter["name"] == "Idempotency-Key")
    assert header["required"] is True
    assert "nullable" not in header["schema"]
    assert header["schema"]["type"] == "string"


def test_prediction_request_union_is_limited_to_profile(tmp_path: Path) -> None:
    manifest = {
        "manifest_version": "1.0",
        "build_id": "sfc-profile",
        "modules": ["prediction"],
        "chromatography_types": ["sfc"],
        "prediction_tools": {"sfc": ["single_column", "full_column", "batch"]},
        "utilities": [],
        "export_formats": [],
        "auth_provider": "deployment_default",
    }
    path = tmp_path / "sfc-profile.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    from chrom_platform.app import create_app

    app = create_app(path, contract_generation=True)
    schema = app.openapi()["paths"]["/api/v1/predictions"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert schema["oneOf"] == [
        {"$ref": "#/components/schemas/SfcSingleColumnPredictionCreate"},
        {"$ref": "#/components/schemas/SfcFullColumnPredictionCreate"},
    ]


def test_profile_write_headers_are_unique_required_and_non_nullable() -> None:
    from chrom_platform.app import create_app

    root = Path(__file__).resolve().parents[2]
    schema = create_app(root / "builds" / "universal-v1.json", contract_generation=True).openapi()
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            if method.lower() not in {"post", "patch", "put", "delete"}:
                continue
            parameters = [p for p in operation.get("parameters", []) if p.get("in") == "header" and p.get("name") in {"Idempotency-Key", "If-Match"}]
            keys = [(p["in"], p["name"]) for p in parameters]
            assert len(keys) == len(set(keys)), (path, method)
            for parameter in parameters:
                assert parameter["required"] is True
                assert "nullable" not in parameter["schema"]
                assert parameter["schema"].get("type") == "string"
