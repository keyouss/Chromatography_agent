import json
import tempfile
import unittest
from pathlib import Path

from freeze_openapi import freeze, normalize


class FreezeOpenApiTests(unittest.TestCase):
    def write(self, root: Path, paths: dict, build_id: str = "minimal-structure-v1") -> Path:
        source = root / "source.json"
        source.write_text(json.dumps({
            "openapi": "3.1.0",
            "info": {"title": "test", "version": "1", "x-build-id": build_id},
            "paths": paths,
        }), encoding="utf-8")
        return source

    def operation(self, operation_id: str) -> dict:
        return {"operationId": operation_id, "responses": {"200": {"content": {
            "application/json": {"schema": {"type": "object"}}}}}}

    def test_freezes_contract_and_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = freeze(self.write(root, {
                "/api/v1/health/live": {"get": self.operation("health_live")},
                "/api/v1/predictions": {"post": self.operation("predictions_create")},
            }), root / "contracts", "minimal-structure-v1")
            self.assertEqual(64, len(result["sha256"]))
            index = json.loads((root / "contracts" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(result["sha256"], index["contracts"]["minimal-structure-v1"]["sha256"])

    def test_rejects_future_legacy_and_unversioned_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for route in ("/api/v1/coding/workspaces", "/api/tools/run", "/predict", "/health"):
                with self.subTest(route=route), self.assertRaises(ValueError):
                    normalize(self.write(root, {route: {"get": {}}}), "minimal-structure-v1")

    def test_rejects_boundary_and_forbidden_v1_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for route in ("/api/v10/status", "/api/v1evil/status", "/api/v1/auth/session", "/api/v1/algorithm-packages"):
                with self.subTest(route=route), self.assertRaises(ValueError):
                    normalize(self.write(root, {route: {"get": self.operation("route")}}), "minimal-structure-v1")

    def test_validation_happens_before_freeze_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.write(root, {"/api/v1/health/live": {"get": {}}})
            with self.assertRaises(ValueError):
                freeze(source, root / "contracts", "minimal-structure-v1")
            self.assertFalse((root / "contracts").exists())

    def test_rejects_mismatched_build(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.write(Path(directory), {"/api/v1/health/live": {"get": {}}}, "universal-v1")
            with self.assertRaises(ValueError):
                normalize(source, "minimal-structure-v1")


if __name__ == "__main__":
    unittest.main()
