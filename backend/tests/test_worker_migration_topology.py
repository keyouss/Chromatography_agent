from __future__ import annotations

import json
from pathlib import Path

from alembic.script import ScriptDirectory
from alembic.config import Config

from chrom_platform.assembly.profile import assemble_profile


def _config() -> Config:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic" / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", "mysql://user:password@localhost/chrom_platform")
    return config


def test_worker_lease_branch_merges_into_universal_head() -> None:
    script = ScriptDirectory.from_config(_config())
    lease = script.get_revision("0007_worker_outbox_lease")
    merge = script.get_revision("0008_merge_worker_outbox")
    training_lease = script.get_revision("0009_training_outbox_lease")
    model_center_fencing = script.get_revision("0010_model_center_job_fencing")
    batch_prediction = script.get_revision("0011_m2_batch_prediction")
    record_export = script.get_revision("0012_m2_record_export")
    m2_merge = script.get_revision("0013_merge_m2_batch_export")
    batch_snapshot_export = script.get_revision("0014_batch_resolved_snapshot_export")
    replay_draft = script.get_revision("0015_m2_prediction_replay_draft")
    merge_replay = script.get_revision("0016_merge_batch_snapshot_replay_draft")
    no_batch_merge = script.get_revision("0017_merge_no_batch_replay_head")
    full_merge = script.get_revision("0018_merge_full_replay_heads")
    repair = script.get_revision("0019_batch_export_status_repair")
    assert lease is not None and lease.down_revision == "0002_m2_prediction_data"
    assert merge is not None and set(merge.down_revision) == {"0006_m4_assistant", "0007_worker_outbox_lease"}
    assert training_lease is not None and training_lease.down_revision == "0008_merge_worker_outbox"
    assert model_center_fencing is not None and model_center_fencing.down_revision == "0009_training_outbox_lease"
    assert batch_prediction is not None and batch_prediction.down_revision == "0010_model_center_job_fencing"
    assert record_export is not None and record_export.down_revision == "0007_worker_outbox_lease"
    assert m2_merge is not None and set(m2_merge.down_revision) == {"0011_m2_batch_prediction", "0012_m2_record_export"}
    assert batch_snapshot_export is not None and batch_snapshot_export.down_revision == "0013_merge_m2_batch_export"
    assert replay_draft is not None and replay_draft.down_revision == "0012_m2_record_export"
    assert merge_replay is not None and set(merge_replay.down_revision) == {"0014_batch_resolved_snapshot_export", "0015_m2_prediction_replay_draft"}
    assert no_batch_merge is not None and set(no_batch_merge.down_revision) == {"0010_model_center_job_fencing", "0015_m2_prediction_replay_draft"}
    assert full_merge is not None and set(full_merge.down_revision) == {"0016_merge_batch_snapshot_replay_draft", "0017_merge_no_batch_replay_head"}
    assert repair is not None and repair.down_revision == "0018_merge_full_replay_heads"
    assert script.get_heads() == ["0021_assistant_framework_state"]


def test_full_and_existing_0013_migration_paths_render_single_head_sql() -> None:
    from contextlib import redirect_stdout
    from io import StringIO
    from alembic import command

    config = _config()
    # 0018 is the last schema/topology-only head.  0019 is an online data
    # repair and must fail closed when Alembic is asked for offline SQL.
    for target in ("0018_merge_full_replay_heads", "0013_merge_m2_batch_export:0018_merge_full_replay_heads"):
        output = StringIO()
        with redirect_stdout(output):
            command.upgrade(config, target, sql=True)
        rendered = output.getvalue()
        assert "0018_merge_full_replay_heads" in rendered
        assert "0019_batch_export_status_repair" not in rendered
        assert "0016_merge_batch_snapshot_replay_draft" in rendered
        assert "0017_merge_no_batch_replay_head" in rendered


def test_profile_migration_selection_keeps_m2_lease_branch_and_universal_merge(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    builds = root / "builds"
    sfc = assemble_profile(builds / "sfc-v1.json", tmp_path / "sfc")
    universal = assemble_profile(builds / "universal-v1.json", tmp_path / "universal")

    assert (sfc / "chrom_platform" / "worker_cli.py").is_file()
    for package in (sfc, universal):
        assert (package / "chrom_platform" / "artifact_core" / "validation_worker.py").is_file()
        assert (package / "chrom_platform" / "artifact_core" / "scanner_defender.py").is_file()
    minimal = assemble_profile(builds / "minimal-structure-v1.json", tmp_path / "minimal")
    assert not (minimal / "chrom_platform" / "worker_cli.py").exists()
    assert not (minimal / "chrom_platform" / "artifact_core" / "validation_worker.py").exists()
    assert not (minimal / "chrom_platform" / "artifact_core" / "scanner_defender.py").exists()
    sfc_requirements = (sfc / "requirements-profile.txt").read_text(encoding="utf-8")
    minimal_requirements = (minimal / "requirements-profile.txt").read_text(encoding="utf-8")
    universal_requirements = (universal / "requirements-profile.txt").read_text(encoding="utf-8")
    assert "asyncmy==0.2.14" in sfc_requirements
    assert "dramatiq[redis]==2.2.1" in sfc_requirements
    assert "redis==8.0.1" in sfc_requirements
    for package_pin in (
        "pydantic-ai-slim==2.56.0",
        "pydantic-graph==2.56.0",
        "openai==3.31.0",
        "httpx==0.28.1",
        "httpcore==1.0.9",
        "tiktoken==0.13.0",
    ):
        assert package_pin in universal_requirements
    assert "\npydantic-ai\n" not in f"\n{universal_requirements}"
    assert "dramatiq[redis]" not in minimal_requirements
    assert "asyncmy==" not in minimal_requirements
    sfc_names = {path.name for path in (sfc / "alembic" / "versions").glob("*.py")}
    universal_names = {path.name for path in (universal / "alembic" / "versions").glob("*.py")}
    assert "0002_m2_prediction_data.py" in sfc_names
    assert "0007_worker_outbox_lease.py" in sfc_names
    assert "0008_merge_worker_outbox.py" not in sfc_names
    assert "0015_m2_prediction_replay_draft.py" in sfc_names
    assert "0014_batch_resolved_snapshot_export.py" not in sfc_names
    assert "0015_m2_prediction_replay_draft.py" in universal_names
    assert "0017_merge_no_batch_replay_head.py" in universal_names
    assert "0018_merge_full_replay_heads.py" in universal_names
    assert "0019_batch_export_status_repair.py" in universal_names
    assert "0020_assistant_llm_configuration_lifecycle.py" in universal_names
    assert {f"000{i}_{name}.py" for i, name in ((1, "m1_persistence"), (2, "m2_prediction_data"), (3, "m3_knowledge"), (4, "m3_training"), (5, "m3_model_center"), (6, "m4_assistant"))} <= universal_names
    assert {"0007_worker_outbox_lease.py", "0008_merge_worker_outbox.py", "0009_training_outbox_lease.py", "0010_model_center_job_fencing.py"} <= universal_names
    assert "0019_batch_export_status_repair.py" not in sfc_names
    for package, expected_head in ((sfc, "0015_m2_prediction_replay_draft"), (universal, "0021_assistant_framework_state"), (minimal, "0001_m1_persistence")):
        config = Config(str(package / "alembic" / "alembic.ini"))
        config.set_main_option("script_location", str(package / "alembic"))
        assert ScriptDirectory.from_config(config).get_heads() == [expected_head]


def test_profiles_do_not_assemble_batch_capabilities_or_routes(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    builds = root / "builds"
    from chrom_platform.app import create_app

    for profile in ("universal-v1", "sfc-v1", "cc-v1", "minimal-structure-v1"):
        package = assemble_profile(builds / f"{profile}.json", tmp_path / profile)
        manifest = json.loads((package / "build-manifest.json").read_text(encoding="utf-8"))
        assert all("batch" not in tools for tools in manifest["prediction_tools"].values())
        if "prediction" in manifest["modules"]:
            assert not (package / "chrom_platform" / "modules" / "prediction" / "batch.py").exists()
            requirements = (package / "requirements-profile.txt").read_text(encoding="utf-8")
            assert "batch" not in requirements.lower()
        app = create_app(package / "build-manifest.json", contract_generation=True)
        if "prediction" in manifest["modules"]:
            registry = getattr(app.state, "prediction_registry", None)
            assert registry is not None
            assert all(tool.id != "batch" for capability in registry.capabilities() for tool in capability.tools)
        paths = app.openapi()["paths"]
        assert not any("batch" in path.lower() for path in paths)
        assert not any("batch" in method.get("operationId", "").lower() for path in paths.values() for method in path.values() if isinstance(method, dict))
        if profile == "universal-v1":
            schemas = app.openapi()["components"]["schemas"]
            for schema_name in ("DataDomainView", "PredictionDraftView", "PredictionRecord"):
                assert "chiral_lc" in schemas[schema_name]["properties"]["chromatography_type"]["enum"]
            assert "batch_input" in schemas["ArtifactView"]["properties"]["kind"]["enum"]
            assert "batch_input" in schemas["UploadCreate"]["properties"]["kind"]["enum"]
            assert "batch" in schemas["StructureCount"]["properties"]["semantics"]["enum"]
        elif "records" in manifest["modules"]:
            data_schema_source = (package / "chrom_platform" / "modules" / "data_center" / "schemas.py").read_text(encoding="utf-8")
            assert "chiral_lc" not in data_schema_source

    from chrom_platform.generate_openapi import generate_package

    universal = assemble_profile(builds / "universal-v1.json", tmp_path / "isolated-universal")
    assert (universal / "chrom_platform" / "worker_cli.py").is_file()
    assert not (universal / "chrom_platform" / "modules" / "prediction" / "batch_worker.py").exists()
    for name in ("batch_models.py", "batch_errors.py", "row_prepare.py"):
        assert not (universal / "chrom_platform" / "modules" / "prediction" / name).exists()
    generated = generate_package(universal, tmp_path / "isolated-openapi")
    document = json.loads(generated.read_text(encoding="utf-8"))
    assert not any("batch" in path.lower() for path in document["paths"])
    assert set(document["components"]["schemas"]["PredictionInputSnapshot"]["properties"]) == {
        "structure_refs", "normalized_structures", "input"
    }
    assert "PredictionExecutionInputSnapshot" not in document["components"]["schemas"]


def test_non_batch_prediction_profile_removes_batch_support_modules(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    package = assemble_profile(root / "builds" / "cc-v1.json", tmp_path / "cc-profile")
    prediction = package / "chrom_platform" / "modules" / "prediction"
    for name in ("batch_models.py", "batch_errors.py", "row_prepare.py"):
        assert not (prediction / name).exists()


def test_batch_profile_includes_resolved_snapshot_migration_and_routes(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "builds" / "universal-v1.json").read_text(encoding="utf-8"))
    manifest["prediction_tools"] = {kind: sorted(set(tools) | {"batch"}) for kind, tools in manifest["prediction_tools"].items()}
    manifest_path = tmp_path / "batch-profile.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    package = assemble_profile(manifest_path, tmp_path / "packages")
    assert (package / "chrom_platform" / "worker_cli.py").is_file()
    assert (package / "chrom_platform" / "modules" / "prediction" / "batch_worker.py").is_file()
    for name in ("batch_models.py", "batch_errors.py", "row_prepare.py"):
        assert (package / "chrom_platform" / "modules" / "prediction" / name).is_file()
    versions = package / "alembic" / "versions"
    selected = {path.name for path in versions.glob("*.py")}
    assert "0014_batch_resolved_snapshot_export.py" in selected
    assert {"0015_m2_prediction_replay_draft.py", "0016_merge_batch_snapshot_replay_draft.py", "0017_merge_no_batch_replay_head.py", "0018_merge_full_replay_heads.py", "0019_batch_export_status_repair.py", "0020_assistant_llm_configuration_lifecycle.py"} <= selected
    assert {"0011_m2_batch_prediction.py", "0012_m2_record_export.py", "0013_merge_m2_batch_export.py"} <= selected
    batch_config = Config(str(package / "alembic" / "alembic.ini"))
    batch_config.set_main_option("script_location", str(package / "alembic"))
    assert ScriptDirectory.from_config(batch_config).get_heads() == ["0021_assistant_framework_state"]
    assert (package / "chrom_platform" / "modules" / "prediction" / "batch_worker.py").is_file()

    from chrom_platform.app import create_app

    app = create_app(package / "build-manifest.json", contract_generation=True)
    operations = {
        (method.upper(), path)
        for path, item in app.openapi()["paths"].items()
        for method in item
    }
    assert ("GET", "/api/v1/batch-predictions/exports/{export_id}") in operations
    assert ("GET", "/api/v1/batch-predictions/exports/{export_id}/download") in operations

    from chrom_platform.generate_openapi import generate_package

    generated = generate_package(package, tmp_path / "batch-isolated-openapi")
    document = json.loads(generated.read_text(encoding="utf-8"))
    assert set(document["components"]["schemas"]["PredictionInputSnapshot"]["properties"]) == {
        "structure_refs", "normalized_structures", "input"
    }
    assert "PredictionExecutionInputSnapshot" not in document["components"]["schemas"]
    assert ("GET", "/api/v1/batch-predictions/exports/{export_id}/download") in {
        (method.upper(), path)
        for path, item in document["paths"].items()
        for method in item
    }
    # Batch adds routes without dropping the active universal record/data
    # contract's optional chiral_lc value from shared M2 DTOs.
    schemas = document["components"]["schemas"]
    for schema_name in ("DataDomainView", "PredictionDraftView", "PredictionRecord"):
        assert "chiral_lc" in schemas[schema_name]["properties"]["chromatography_type"]["enum"]
    assert "batch_input" in schemas["ArtifactView"]["properties"]["kind"]["enum"]
    assert "batch_input" in schemas["UploadCreate"]["properties"]["kind"]["enum"]
    assert "batch" in schemas["StructureCount"]["properties"]["semantics"]["enum"]
