"""Synthetic admission tests; no Defender, database, service, or uploaded files."""

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from types import SimpleNamespace

from chrom_platform.artifact_core.scanner_defender import ScanEvidence
from chrom_platform.artifact_core.validation_worker import (
    ArtifactValidationWorker, DatasetCsvScanPolicy, _Claim,
)


def _fixture(tmp_path, body=b"measurement_id,observed\nm-1,0.4\n"):
    path = tmp_path / "synthetic.csv"
    path.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()
    claim = _Claim(
        job_id="job-fixture", project_id="project-fixture", artifact_id="artifact-fixture",
        attempt=1, attempt_id="attempt-fixture", fencing_token=1, dispatch_id="dispatch-fixture",
        storage_key="fixture.csv", sha256=digest, size_bytes=len(body), media_type="text/csv",
        kind="dataset_package",
    )
    class Store:
        configured = True
        max_size_bytes = 16 * 1024 * 1024
        def prepare_scan_target(self, *args, **kwargs):
            stat = path.stat()
            return SimpleNamespace(path=path, identity=(stat.st_dev, stat.st_ino))
        def assert_scan_target_unchanged(self, target):
            assert hashlib.sha256(target.path.read_bytes()).hexdigest() == digest
    class Scanner:
        calls = 0
        profile_id = "synthetic-dataset-profile"
        def _result(self, **kwargs):
            self.calls += 1
            now = datetime.now(timezone.utc)
            evidence = ScanEvidence(
                artifact_id=kwargs["artifact_id"], scan_attempt_id=kwargs["scan_attempt_id"],
                input_sha256=kwargs["input_sha256"], input_size_bytes=kwargs["input_size_bytes"],
                provider_id="synthetic", product_version="fixture", engine_version="fixture",
                signature_version="fixture", signature_updated_at_utc=now,
                started_at_utc=now, finished_at_utc=now, profile_id=self.profile_id,
                exit_code=0, verdict="CLEAN", error_code=None, output_truncated=False,
            )
            return SimpleNamespace(status="PASSED", evidence=evidence)
        def scan_dataset(self, **kwargs):
            return self._result(**kwargs)
        def scan(self, **kwargs):
            return self._result(**kwargs)
    scanner = Scanner()
    policy = DatasetCsvScanPolicy(
        project_id=claim.project_id, profile_id=scanner.profile_id, encoding="utf-8",
        allowed_inputs=frozenset({(digest, len(body))}),
    )
    worker = ArtifactValidationWorker(session_factory=None, store=Store(), scanner=scanner, actor=None)
    return worker, scanner, claim, policy


def _enable_fixture_policy(worker, policy):
    # Deliberate trusted test injection. Production registries remain empty.
    worker.dataset_csv_policy = policy
    worker.reviewed_dataset_profile_ids = frozenset({policy.profile_id})


def test_dataset_requires_its_own_reviewed_profile_and_cannot_use_old_window(tmp_path):
    worker, scanner, claim, policy = _fixture(tmp_path)
    assert worker._validate_and_scan(claim)[2] == "DATASET_CSV_PROFILE_NOT_APPROVED"
    worker.dataset_csv_policy = policy
    assert worker._validate_and_scan(claim)[2] == "DATASET_CSV_PROFILE_NOT_APPROVED"
    _enable_fixture_policy(worker, policy)
    worker.scan_window = object()
    assert worker._validate_and_scan(claim)[2] == "DATASET_CSV_SCAN_WINDOW_NOT_ALLOWED"
    assert scanner.calls == 0


def test_dataset_admission_binds_project_hash_type_and_stricter_size(tmp_path):
    worker, scanner, claim, policy = _fixture(tmp_path)
    _enable_fixture_policy(worker, policy)
    for bad in (
        replace(claim, project_id="other-project"), replace(claim, sha256="0" * 64),
        replace(claim, media_type="application/zip"),
    ):
        assert worker._validate_and_scan(bad)[2] == "DATASET_CSV_ADMISSION_INVALID"
    worker.store.max_size_bytes = 1
    assert worker._validate_and_scan(claim)[2] == "DATASET_CSV_SIZE_LIMIT_EXCEEDED"
    assert scanner.calls == 0


def test_dataset_uses_existing_scanner_evidence_and_explicit_encoding(tmp_path):
    worker, scanner, claim, policy = _fixture(tmp_path, "id,名称\nm-1,测量\n".encode("gb18030"))
    _enable_fixture_policy(worker, replace(policy, encoding="gb18030"))
    assert worker._validate_and_scan(claim)[:3] == ("SUCCEEDED", "PASSED", None)
    scanner.profile_id = "different-profile"
    assert worker._validate_and_scan(claim)[2] == "DATASET_CSV_PROFILE_EVIDENCE_MISMATCH"
    scanner.profile_id = policy.profile_id
    worker.dataset_csv_policy = policy  # No implicit fallback from UTF-8 to GB18030.
    assert worker._validate_and_scan(claim)[:3] == ("REJECTED", "PASSED", "CSV_SYNTAX_INVALID")


def test_dataset_structure_limit_does_not_change_batch_or_model_admission(tmp_path):
    worker, scanner, claim, policy = _fixture(tmp_path, (",".join(f"c{i}" for i in range(65)) + "\n" + ",".join("x" for _ in range(65)) + "\n").encode())
    _enable_fixture_policy(worker, policy)
    assert worker._validate_and_scan(claim)[:3] == ("REJECTED", "PASSED", "CSV_HEADER_INVALID")
    assert worker._validate_and_scan(replace(claim, kind="batch_input"))[:3] == ("SUCCEEDED", "PASSED", None)
    before = scanner.calls
    assert worker._validate_and_scan(replace(claim, kind="model_package"))[2] == "ARTIFACT_FORMAT_UNSUPPORTED"
    assert scanner.calls == before
