from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent


_CHILD_GUARD = r'''
import importlib.abc
import json
import os
import sys
from pathlib import Path

root = Path(os.environ["TEST_REPOSITORY_ROOT"]).resolve()
blocked_prefixes = (
    "chrom_platform.assembly.prediction",
    "chrom_platform.adapters.sfc.adapter",
    "chrom_platform.adapters.sfc.runtime",
    "chrom_platform.adapters.gc.adapter",
    "chrom_platform.adapters.gc.runtime",
    "chrom_platform.adapters.hplc.adapter",
    "chrom_platform.adapters.cc.adapter",
    "torch",
    "torch_geometric",
    "sklearn",
)
counts = {name: 0 for name in (
    "production_registry_or_science_import",
    "database_session_factory",
    "database_health",
    "redis_client",
    "redis_health",
)}

class ImportGuard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == prefix or fullname.startswith(prefix + ".") for prefix in blocked_prefixes):
            counts["production_registry_or_science_import"] += 1
            raise AssertionError("contract generation attempted forbidden import: " + fullname)
        return None

# Exercise import-time behavior with a configured, real manifest and existing
# model/registry/catalog paths before the app module is imported.
sys.meta_path.insert(0, ImportGuard())
import chrom_platform.infrastructure.db as db_module
import chrom_platform.infrastructure.health as health_module

def fail(name):
    def called(*args, **kwargs):
        counts[name] += 1
        raise AssertionError("contract generation called forbidden dependency: " + name)
    return called

db_module.create_session_factory = fail("database_session_factory")
health_module.database_health_from_url = fail("database_health")
health_module.create_redis_ping_client = fail("redis_client")
health_module.redis_health = fail("redis_health")

import chrom_platform.app as app_module
assert not hasattr(app_module, "app"), "module import must not construct an eager app"
assert callable(app_module.create_app_from_environment), "uvicorn --factory entry disappeared"
app = app_module.create_app(contract_generation=True)
assert app.state.contract_generation is True
assert app.state.session_factory is None

openapi = app.openapi()
if os.environ["TEST_EXPECT_SFC"] == "1":
    paths = openapi["paths"]
    assert "/api/v1/predictions" in paths
    assert "/api/v1/sfc/conditions/options" in paths
    assert "/api/v1/sfc/candidate-scopes" in paths
    request_schema = paths["/api/v1/predictions"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    refs = {item["$ref"] for item in request_schema["oneOf"]}
    assert refs == {
        "#/components/schemas/SfcSingleColumnPredictionCreate",
        "#/components/schemas/SfcFullColumnPredictionCreate",
    }, refs
    components = openapi["components"]["schemas"]
    full = components["SfcFullColumnPredictionCreate"]
    assert full["properties"]["schema_version"].get("const") == "1.1"
    assert full["properties"]["structure_refs"]["minItems"] == 2
    assert full["properties"]["structure_refs"]["maxItems"] == 2
    assert full["properties"]["model_selection"]["$ref"] == "#/components/schemas/ExactModelSelection"
    assert components["ExactModelSelection"]["properties"]["mode"].get("const") == "exact"
    assert "SfcAllColumnDomain" in components
    assert "SfcSingleColumnDomain" in components
    assert "sfc_domain" in components["SfcConditionsOptions"]["properties"]
    assert "fields" in components["SfcConditionsOptions"]["properties"]
    registry = app.state.prediction_registry
    assert registry.metadata_only is True
    assert registry.registered_types() == ()
    assert registry.capabilities() == []
    assert registry.verified_handle is None
    assert getattr(app.user_middleware[0].kwargs.get("dispatch"), "__name__", "") == "metadata_generation_guard"
    for business_path in (
        "/api/v1/predictions",
        "/api/v1/sfc/conditions/options",
        "/api/v1/health/live",
        "/api/v1/system/capabilities",
    ):
        rejected = app_module._contract_generation_rejection(business_path)
        assert rejected is not None and rejected.status_code == 503
        assert rejected.body and b"CONTRACT_GENERATION_ONLY" in rejected.body
    assert app_module._contract_generation_rejection("/openapi.json") is None
else:
    assert "/api/v1/predictions" not in openapi["paths"]
    assert not any("Sfc" in name for name in openapi["components"]["schemas"])
    assert app.state.prediction_registry is None

assert all(value == 0 for value in counts.values()), counts
print("CALL_COUNTS=" + json.dumps(counts, sort_keys=True))
'''


def _run_isolated_contract_factory(*, manifest: Path, expect_sfc: bool) -> str:
    configured_assets = (
        ROOT / "Services/sfc_assets/rs15-extra-trees-s80-seed2024/metadata.json",
        ROOT / "Services/sfc_assets/rs15-extra-trees-s80-seed2024/model.joblib",
        ROOT / "Services/sfc_assets/conditions.sqlite",
    )
    assert manifest.is_file()
    assert all(path.exists() for path in configured_assets), configured_assets
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(BACKEND / "src"),
            "TEST_REPOSITORY_ROOT": str(ROOT),
            "TEST_EXPECT_SFC": "1" if expect_sfc else "0",
            "CHROM_BUILD_MANIFEST": str(manifest.resolve()),
            # Existing asset paths and a complete selection make accidental
            # production registration observable instead of hiding it.
            "CHROM_SFC_REGISTRY_PATH": str((ROOT / "Services/sfc_assets/rs15-extra-trees-s80-seed2024/metadata.json").resolve()),
            "CHROM_SFC_MODEL_ROOT": str((ROOT / "Services/sfc_assets/rs15-extra-trees-s80-seed2024").resolve()),
            "CHROM_SFC_CANDIDATE_DB_PATH": str((ROOT / "Services/sfc_assets/conditions.sqlite").resolve()),
            "CHROM_SFC_REGISTRY_SHA256": "a" * 64,
            "CHROM_SFC_MODEL_ID": "sc-rs12-amylose-dmpc-coated",
            "CHROM_SFC_MODEL_VERSION": "1",
            "CHROM_SFC_ARTIFACT_SHA256": "b" * 64,
            "CHROM_SFC_BUSINESS_SCOPE_ID": "single-column",
            "CHROM_GC_MODEL_ROOT": str((ROOT / "Services/sfc_assets/rs15-extra-trees-s80-seed2024").resolve()),
            "ARTIFACT_ROOT": str((ROOT / "testing/fixtures/priority-launch").resolve()),
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", _CHILD_GUARD],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "CALL_COUNTS={\"database_health\": 0, \"database_session_factory\": 0, \"production_registry_or_science_import\": 0, \"redis_client\": 0, \"redis_health\": 0}" in result.stdout
    print(result.stdout.strip())
    return result.stdout


def test_sfc_contract_factory_is_metadata_only_and_import_is_not_eager() -> None:
    output = _run_isolated_contract_factory(
        manifest=ROOT / "builds/sfc-v1.json",
        expect_sfc=True,
    )
    assert "CALL_COUNTS=" in output


def test_minimal_contract_factory_does_not_load_optional_adapters() -> None:
    output = _run_isolated_contract_factory(
        manifest=ROOT / "builds/minimal-structure-v1.json",
        expect_sfc=False,
    )
    assert "CALL_COUNTS=" in output
