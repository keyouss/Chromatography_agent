import json
import unittest

from preflight import configuration_checks


class PreflightTests(unittest.TestCase):
    def config(self):
        return dict(API_PORT="9012", FRONTEND_PORT="5173", WEB_PORT="8080", API_HOST="127.0.0.1",
                    DATABASE_URL="mysql+asyncmy://user:private-password@db/platform", REDIS_URL="redis://redis/0",
                    MYSQL_DATABASE="chrom_platform", MYSQL_USER="chrom_platform", MYSQL_PASSWORD="private-password", MYSQL_ROOT_PASSWORD="private-root-password",
                    ARTIFACT_ROOT="/artifacts", SECRET_KEY_FILE="/run/secrets/key", TRACE_CONTENT_MODE="off")

    def test_output_does_not_expose_secrets(self):
        checks = configuration_checks(self.config())
        self.assertNotIn("private-password", json.dumps(checks))
        self.assertFalse(any(c["status"] == "FAIL" for c in checks))

    def test_conflicting_ports_placeholder_and_raw_trace_rejected(self):
        config = self.config()
        config.update(WEB_PORT="9012", DATABASE_URL="mysql+asyncmy://APP_USER:REPLACE_ME@db/platform", TRACE_CONTENT_MODE="raw")
        failed = {c["check"] for c in configuration_checks(config) if c["status"] == "FAIL"}
        self.assertTrue({"distinct_ports", "DATABASE_URL", "trace_mode"} <= failed)


if __name__ == "__main__":
    unittest.main()
