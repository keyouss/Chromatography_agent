"""Validate and freeze a generated OpenAPI contract with the standard library."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from check_openapi import check

BUILD_ID = re.compile(r"[a-z][a-z0-9-]{0,63}")
LEGACY_PREFIXES = ("/api/tools",)


def normalize(source: Path, build_id: str) -> tuple[bytes, str]:
    document = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not str(document.get("openapi", "")).startswith("3.1."):
        raise ValueError("OpenAPI 3.1.x document required")
    info = document.get("info")
    if not isinstance(info, dict) or info.get("x-build-id") != build_id:
        raise ValueError("info.x-build-id does not match the selected build")
    paths = document.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise ValueError("paths must be a non-empty object")
    errors = check(document)
    if errors:
        raise ValueError("contract check failed: " + "; ".join(errors))
    for route in paths:
        if isinstance(route, str) and (route.lower() == "/predict" or route.lower().startswith(LEGACY_PREFIXES)):
            raise ValueError(f"forbidden V1 route: {route}")
    body = (json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    return body, hashlib.sha256(body).hexdigest()


def freeze(source: Path, root: Path, build_id: str) -> dict[str, str]:
    if not BUILD_ID.fullmatch(build_id):
        raise ValueError("invalid build id")
    body, digest = normalize(source, build_id)
    destination = root / build_id / "openapi.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(body)
    index_path = root / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {"schema_version": "1.0", "contracts": {}}
    index["contracts"][build_id] = {"path": f"{build_id}/openapi.json", "sha256": digest}
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"build_id": build_id, "path": str(destination), "sha256": digest}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    try:
        result = freeze(args.source.resolve(), args.root.resolve(), args.build_id)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.exit(1, f"contract freeze failed: {error}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
