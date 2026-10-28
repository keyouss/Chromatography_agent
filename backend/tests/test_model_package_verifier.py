from __future__ import annotations

from dataclasses import dataclass

from chrom_platform.modules.model_center.asset_verifier import ModelPackageAssetVerifier
from chrom_platform.modules.model_center.package_verification import verify_package_request


@dataclass
class _Artifact:
    id: str = "artifact-1"
    project_id: str = "project-1"
    kind: str = "model_package"
    status: str = "AVAILABLE"
    scan_status: str = "PASSED"
    sha256: str = "a" * 64
    size_bytes: int = 100
    storage_key: str = "objects/artifact-1/content"
    metadata_json: dict[str, object] | None = None


class _Artifacts:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def get(self, project_id: str, artifact_id: str):
        self.calls += 1
        return self.value if (project_id, artifact_id) == (self.value.project_id, self.value.id) else None


class _Storage:
    def prepare_scan_target(self, *args, **kwargs):
        raise AssertionError("storage must not be reached without a real scan report")

    def assert_scan_target_unchanged(self, target):
        raise AssertionError("storage must not be reached without a real scan report")


class _NoScanReport:
    def get(self, *, project_id, report_ref):
        return None


class _Executor:
    def __init__(self):
        self.calls = 0

    def execute(self, request, *, cancelled=None, check_fence=None):
        self.calls += 1
        raise AssertionError("executor must not run without a bound scan report")


def test_asset_verifier_fails_closed_without_job_event_scan_evidence() -> None:
    artifact = _Artifact(metadata_json={"validation_report_ref": {"type": "job", "id": "job-1"}})
    artifacts = _Artifacts(artifact)
    executor = _Executor()
    verifier = ModelPackageAssetVerifier(
        artifacts=artifacts,
        storage=_Storage(),
        scan_evidence=_NoScanReport(),
        executor=executor,
    )

    result = verifier.verify(
        project_id="project-1",
        source={"kind": "external_package", "artifact_id": "artifact-1"},
        task_type="gc",
    )

    assert result["valid"] is False
    assert result["error_code"] == "MODEL_PACKAGE_SCAN_EVIDENCE_MISSING"
    assert artifacts.calls == 1
    assert executor.calls == 0


def test_asset_verifier_rejects_training_source_without_guessing_artifact() -> None:
    artifacts = _Artifacts(_Artifact())
    executor = _Executor()
    verifier = ModelPackageAssetVerifier(
        artifacts=artifacts,
        storage=_Storage(),
        scan_evidence=_NoScanReport(),
        executor=executor,
    )

    result = verifier.verify(
        project_id="project-1",
        source={"kind": "training_result", "experiment_id": "experiment-1"},
        task_type="gc",
    )

    assert result["valid"] is False
    assert result["error_code"] == "MODEL_PACKAGE_SOURCE_UNSUPPORTED"
    assert artifacts.calls == 0
    assert executor.calls == 0


def test_verifier_child_rejects_arbitrary_loader_without_touching_package() -> None:
    result = verify_package_request({
        "op": "verify_gc_safetensors",
        "project_id": "project-1",
        "artifact_id": "artifact-1",
        "task_type": "gc",
        "package_path": "C:/not-opened.zip",
        "expected_sha256": "a" * 64,
        "expected_size_bytes": 100,
        "allowed_loader_ids": ["caller-selected-import"],
    })

    assert result["valid"] is False
    assert result["error_code"] == "PACKAGE_REQUEST_INVALID"
