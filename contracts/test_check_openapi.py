import unittest

from check_openapi import check


class ContractGateTests(unittest.TestCase):
    def spec(self):
        return {"openapi": "3.1.0", "paths": {"/api/v1/health/live": {"get": {
            "operationId": "health_live", "responses": {"200": {"content": {
                "application/json": {"schema": {"type": "object"}}}}}}}}}

    def test_valid_minimum_and_forbidden_path(self):
        spec = self.spec()
        self.assertEqual([], check(spec))
        spec["paths"]["/api/v1/coding/workspaces"] = {}
        self.assertTrue(any("forbidden" in error for error in check(spec)))

    def test_duplicate_id_and_missing_ref(self):
        spec = self.spec()
        spec["paths"]["/api/v1/system/version"] = spec["paths"]["/api/v1/health/live"]
        spec["components"] = {"schemas": {"Broken": {"$ref": "#/components/schemas/Missing"}}}
        errors = check(spec)
        self.assertTrue(any("duplicate" in error for error in errors))
        self.assertTrue(any("Unresolved" in error for error in errors))

    def test_rejects_empty_path_items_and_malformed_response_layers(self):
        spec = self.spec()
        spec["paths"]["/api/v1/empty"] = {}
        for value in (None, "responses", []):
            malformed = self.spec()
            malformed["paths"]["/api/v1/health/live"]["get"]["responses"] = value
            errors = check(malformed)
            self.assertTrue(errors)
            self.assertTrue(any("responses" in error for error in errors))
        for value in (None, "response", []):
            malformed = self.spec()
            malformed["paths"]["/api/v1/health/live"]["get"]["responses"]["200"] = value
            errors = check(malformed)
            self.assertTrue(any("invalid response" in error for error in errors))
        self.assertTrue(any("HTTP operation" in error for error in check(spec)))

    def test_rejects_prefix_boundary_routes(self):
        for route in ("/api/v10/status", "/api/v1evil/status"):
            spec = self.spec()
            spec["paths"][route] = spec["paths"].pop("/api/v1/health/live")
            self.assertTrue(any("business prefix" in error for error in check(spec)))


if __name__ == "__main__":
    unittest.main()
