from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import chrom_platform.artifact_core.data_csv_window as data_csv_window
from chrom_platform.artifact_core.data_csv_window import (
    BUILD_ID,
    BUILD_MANIFEST_SHA256,
    DATASET_SCAN_PROFILE_ID,
    DOMAIN_MODULE_SHA256,
    INPUT_SHA256,
    INPUT_SIZE_BYTES,
    EXPECTED_MANIFEST_PATH,
    build_data_csv_window,
)
from chrom_platform.artifact_core.scanner_defender import ScanEvidence
from chrom_platform.artifact_core.validation_worker import (
    ArtifactValidationWorker,
    DatasetCsvScanPolicy,
    _Claim,
)


def _runtime_bindings(runtime_root: Path) -> dict[str, str]:
    expected_runtime = runtime_root / "ChromatographyPlatform" / "data-accept-20261026"
    ready_file = expected_runtime / "state" / "worker-ready.json"
    manifest_path = _manifest_path()
    values = {
        "LOCALAPPDATA": "C:/SyntheticLocalAppData",
        "ARTIFACT_ROOT": str(expected_runtime / "artifacts"),
        "CHROM_ACCEPTANCE_DATABASE_SCHEMA": "chrom_data_accept_20261026",
        "CHROM_DEFAULT_PROJECT_ID": "project_data_accept_20261026",
        "CHROM_PROJECT_IDS": "project_data_accept_20261026",
        "WORKER_QUEUE": "chrom-platform-data-accept-20261026",
        "API_PORT": "9012",
        "FRONTEND_PORT": "5174",
        "WORKER_READY_FILE": str(ready_file),
        "CHROM_BUILD_MANIFEST": str(manifest_path),
        "DATABASE_URL": "mysql+asyncmy://chrom_data_accept_runtime:fixture@127.0.0.1:3306/chrom_data_accept_20261026",
        "SYNC_DATABASE_URL": "mysql+pymysql://chrom_data_accept_runtime:fixture@127.0.0.1:3306/chrom_data_accept_20261026",
        "REDIS_URL": "redis://127.0.0.1:6379",
    }
    values["LOCALAPPDATA"] = str(runtime_root).replace("\\", "/")
    expected_runtime.mkdir(parents=True)
    (expected_runtime / "runtime.json").write_text(json.dumps(values), encoding="utf-8")
    return values


def _manifest_path() -> Path:
    return EXPECTED_MANIFEST_PATH


def test_window_is_bound_to_exact_manifest_and_tlc_source_module(monkeypatch) -> None:
    with tempfile.TemporaryDirectory(prefix="data-csv-window-", dir=Path(__file__).resolve().parent) as root:
        local_app_data = Path(root)
        expected_runtime = local_app_data / "ChromatographyPlatform" / "data-accept-20261026"
        monkeypatch.setattr(data_csv_window, "EXPECTED_LOCALAPPDATA", local_app_data)
        monkeypatch.setattr(data_csv_window, "EXPECTED_RUNTIME_ROOT", expected_runtime)
        runtime = _runtime_bindings(local_app_data)
        manifest = _manifest_path()
        window = build_data_csv_window(
            BUILD_ID, BUILD_MANIFEST_SHA256, ("datasets",),
            manifest_path=manifest, environ=runtime,
        )

        assert window is not None
        assert window.project_id == "project_data_accept_20261026"
        assert window.domain.id == "tlc_author_measurement_v1"
        assert window.domain.version == "1"
        assert window.profile.encoding == "gb18030"
        assert window.scan_policy.allowed_inputs == frozenset({(INPUT_SHA256, INPUT_SIZE_BYTES)})
        assert window.scan_policy.allowed_kind == "dataset_package"
        assert window.scan_policy.source_ref_type == "artifact"
        assert window.scanner._scanner._reviewed_profile.profile_id == DATASET_SCAN_PROFILE_ID
        assert window.scanner._scanner._allowed_inputs == frozenset({(INPUT_SHA256, INPUT_SIZE_BYTES)})
        assert window.scanner._scanner._allow_signature_updates is True

        assert build_data_csv_window(
            BUILD_ID, "0" * 64, ("datasets",), manifest_path=manifest, environ=runtime,
        ) is None
        assert build_data_csv_window(
            BUILD_ID, BUILD_MANIFEST_SHA256, ("datasets", "prediction"),
            manifest_path=manifest, environ=runtime,
        ) is None
        assert build_data_csv_window(
            BUILD_ID, BUILD_MANIFEST_SHA256, ("datasets",), manifest_path=manifest,
            environ={**runtime, "WORKER_QUEUE": "chrom-platform-formal"},
        ) is None
        assert build_data_csv_window(
            BUILD_ID, BUILD_MANIFEST_SHA256, ("datasets",), manifest_path=manifest,
            environ={**runtime, "ARTIFACT_ROOT": "C:/wrong/artifacts"},
        ) is None
        assert build_data_csv_window(
            BUILD_ID, BUILD_MANIFEST_SHA256, ("datasets",), manifest_path=manifest,
            environ={**runtime, "LOCALAPPDATA": str(local_app_data / "redirected")},
        ) is None
        assert build_data_csv_window(
            BUILD_ID, BUILD_MANIFEST_SHA256, ("datasets",), manifest_path=manifest,
            environ={**runtime, "DATABASE_URL": "mysql+asyncmy://other:fixture@127.0.0.1:3306/chrom_data_accept_20261026"},
        ) is None
        altered_runtime = {**runtime, "WORKER_QUEUE": "chrom-platform-formal"}
        expected_runtime = Path(runtime["LOCALAPPDATA"]) / "ChromatographyPlatform" / "data-accept-20261026"
        (expected_runtime / "runtime.json").write_text(json.dumps(altered_runtime), encoding="utf-8")
        assert build_data_csv_window(
            BUILD_ID, BUILD_MANIFEST_SHA256, ("datasets",), manifest_path=manifest,
            environ=runtime,
        ) is None
        assert DOMAIN_MODULE_SHA256 != "0" * 64


def test_dataset_scan_uses_only_the_separate_dataset_scanner() -> None:
    content = b"measurement_id,rf_observed\nm-1,0.5\n"
    digest = hashlib.sha256(content).hexdigest()
    with tempfile.TemporaryDirectory(prefix="data-scan-fixture-", dir=Path(__file__).resolve().parent) as root:
        path = Path(root) / "fixture.csv"
        path.write_bytes(content)
        now = datetime.now(timezone.utc)
        evidence = ScanEvidence(
            artifact_id="artifact-fixture", scan_attempt_id="scan-fixture",
            input_sha256=digest, input_size_bytes=len(content), provider_id="synthetic",
            product_version="fixture", engine_version="fixture", signature_version="fixture",
            signature_updated_at_utc=now, started_at_utc=now, finished_at_utc=now,
            profile_id="synthetic-window", exit_code=0, verdict="CLEAN", error_code=None,
            output_truncated=False,
        )

        class Store:
            configured = True
            max_size_bytes = 16 * 1024 * 1024

            def prepare_scan_target(self, *_args, **_kwargs):
                stat = path.stat()
                return SimpleNamespace(path=path, identity=(stat.st_dev, stat.st_ino))

            def assert_scan_target_unchanged(self, _target):
                assert hashlib.sha256(path.read_bytes()).hexdigest() == digest

        class Scanner:
            def __init__(self, outcome):
                self.calls = []
                self.outcome = outcome

            def scan_dataset(self, **kwargs):
                self.calls.append(kwargs)
                return self.outcome

        outcome = SimpleNamespace(status="PASSED", evidence=evidence)
        generic_scanner = Scanner(SimpleNamespace(status="FAILED", evidence=None))
        dataset_scanner = Scanner(outcome)
        policy = DatasetCsvScanPolicy(
            project_id="project-fixture", profile_id="synthetic-window", encoding="utf-8",
            allowed_inputs=frozenset({(digest, len(content))}),
        )
        claim = _Claim(
            job_id="job-fixture", project_id="project-fixture", artifact_id="artifact-fixture",
            attempt=1, attempt_id="attempt-fixture", fencing_token=1, dispatch_id="dispatch-fixture",
            storage_key="objects/artifact-fixture", sha256=digest, size_bytes=len(content),
            media_type="text/csv", kind="dataset_package", source_ref_type="artifact",
            scan_attempt_id="scan-fixture",
        )
        worker = ArtifactValidationWorker(
            session_factory=object(), store=Store(), scanner=generic_scanner,
            dataset_scanner=dataset_scanner, actor=None,
            dataset_csv_policy=policy, reviewed_dataset_profile_ids=frozenset({"synthetic-window"}),
        )
        unapproved_worker = ArtifactValidationWorker(
            session_factory=object(), store=Store(), scanner=generic_scanner,
            dataset_scanner=dataset_scanner, actor=None, dataset_csv_policy=policy,
        )

        result = worker._validate_and_scan(claim)
        assert result[:3] == ("SUCCEEDED", "PASSED", None), repr(result)
        assert unapproved_worker._validate_and_scan(claim)[2] == "DATASET_CSV_PROFILE_NOT_APPROVED"
        assert generic_scanner.calls == []
        assert len(dataset_scanner.calls) == 1

        wrong_source = _Claim(**{**claim.__dict__, "source_ref_type": "data_import"})
        assert worker._validate_and_scan(wrong_source)[2] == "DATASET_CSV_ADMISSION_INVALID"
        assert len(dataset_scanner.calls) == 1
