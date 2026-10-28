"""Structural contract gate. Passing this is not a business implementation test."""
import argparse
import json
from pathlib import Path

METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
FORBIDDEN = {"coding", "workspaces", "sandbox-runs", "algorithm-packages", "auth"}


def check(spec: dict) -> list[str]:
    errors = []
    if not isinstance(spec, dict) or not isinstance(spec.get("paths"), dict):
        return ["OpenAPI object with paths required"]
    if not str(spec.get("openapi", "")).startswith("3."):
        errors.append("OpenAPI 3.x required")
    if not spec["paths"]:
        errors.append("No implemented API paths")
    ids = set()
    for path, entry in spec["paths"].items():
        if not isinstance(path, str):
            errors.append("path keys must be strings")
            continue
        if not path.startswith("/api/v1/"):
            errors.append(f"{path}: business prefix must be /api/v1")
        if {segment.casefold() for segment in path.strip("/").split("/")} & FORBIDDEN:
            errors.append(f"{path}: forbidden V1 route")
        if not isinstance(entry, dict):
            errors.append(f"{path}: invalid path item")
            continue
        operations = [(method, operation) for method, operation in entry.items() if method in METHODS]
        if not operations:
            errors.append(f"{path}: at least one HTTP operation required")
            continue
        for method, operation in operations:
            if not isinstance(operation, dict):
                errors.append(f"{path}: invalid operation")
                continue
            operation_id = operation.get("operationId")
            if not isinstance(operation_id, str) or not operation_id or operation_id in ids:
                errors.append(f"{method} {path}: missing or duplicate operationId")
            else:
                ids.add(operation_id)
            responses = operation.get("responses", {})
            if not isinstance(responses, dict):
                errors.append(f"{method} {path}: responses must be an object")
                continue
            successful = {str(code): response for code, response in responses.items() if str(code).startswith("2")}
            if not successful:
                errors.append(f"{method} {path}: success response required")
            for code, response in responses.items():
                code = str(code)
                if not isinstance(response, dict):
                    errors.append(f"{method} {path} {code}: invalid response")
                    continue
                if "$ref" in response:
                    if not isinstance(response["$ref"], str):
                        errors.append(f"{method} {path} {code}: invalid response reference")
                    continue
                content = response.get("content", {})
                if not isinstance(content, dict):
                    errors.append(f"{method} {path} {code}: content must be an object")
                    continue
                invalid_media = False
                for media_type, media in content.items():
                    if not isinstance(media, dict):
                        errors.append(f"{method} {path} {code} {media_type}: invalid media type")
                        invalid_media = True
                    elif "schema" in media and not media["schema"]:
                        invalid_media = True
                if code == "204":
                    continue
                if not code.startswith("2"):
                    continue
                if not content or invalid_media or any(
                    not isinstance(media, dict) or not media.get("schema") for media in content.values()
                ):
                    errors.append(f"{method} {path} {code}: typed content required")

    def walk(value):
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str):
                if not reference.startswith("#/"):
                    errors.append("External schema references are not self-contained")
                else:
                    target = spec
                    try:
                        for part in reference[2:].split("/"):
                            target = target[part.replace("~1", "/").replace("~0", "~")]
                    except (IndexError, KeyError, TypeError):
                        errors.append(f"Unresolved reference: {reference}")
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(spec)
    return errors


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    args = parser.parse_args()
    problems = check(json.loads(args.file.read_text(encoding="utf-8")))
    print(json.dumps({"status": "FAIL" if problems else "STRUCTURE_PASS", "errors": problems}, ensure_ascii=False, indent=2))
    raise SystemExit(bool(problems))
