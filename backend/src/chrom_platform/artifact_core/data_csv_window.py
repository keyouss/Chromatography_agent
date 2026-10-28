"""Exact, dataset-only admission window for the reviewed TLC CSV build."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .scanner_defender import DefenderCommandScanner, _ResultProfile
from .validation_worker import DatasetCsvScanPolicy


BUILD_ID = "data-center-invalid-window-20261026"
BUILD_MANIFEST_SHA256 = "1104f5489fbeefed95718f69d7a5038c64ba7e5655ed5ae4e182952b79641da7"
EXPECTED_MANIFEST_RELATIVE_PATH = Path("builds/candidates/data-center-invalid-window-20261026/profile.json")
EXPECTED_MANIFEST_PATH = Path(
    r"D:\Jupyter\Chromatography_agent\builds\candidates\data-center-invalid-window-20261026\profile.json"
)
EXPECTED_LOCALAPPDATA = Path(r"C:\Users\13459\AppData\Local")
EXPECTED_RUNTIME_ROOT = Path(
    r"C:\Users\13459\AppData\Local\ChromatographyPlatform\data-accept-20261026"
)
EXPECTED_DATABASE_USERNAME = "chrom_data_accept_runtime"
DOMAIN_MODULE_SHA256 = "0122468b8b38fe6f35a71d8537b6943d7683f17c0b768db1ea3309058b9f9853"
DATASET_SCAN_PROFILE_ID = "dataset-csv-tlc-header-v1-20261026"
INPUT_SHA256 = "95d934f576bd1ec2ff6c6b496b4e85543b36ccee96c9ddfebdd33561aa98154f"
INPUT_SIZE_BYTES = 247
DEFENDER_EXE_SHA256 = "a4291c5b4325895d13d32e232bf50c1b41356306803ac5dc75bba9aecb021141"
DEFENDER_PRODUCT_VERSION = "4.18.25080.5"
DEFENDER_ENGINE_VERSION = "1.1.26100.4"
DEFENDER_SIGNER_SUBJECT = "CN=Microsoft Windows, O=Microsoft Corporation, L=Redmond, S=Washington, C=US"


@dataclass(frozen=True)
class DataCsvScanWindow:
    project_id: str
    domain: Any
    profile: Any
    source_column_mapping: dict[str, str]
    scan_policy: DatasetCsvScanPolicy
    scanner: object

    def resolve_profile(self, domain_id: str, schema_version: str):
        if (domain_id, schema_version) == (self.domain.id, self.domain.version):
            return self.profile
        return None


def build_data_csv_window(
    build_id: str,
    manifest_sha256: str | None,
    modules: tuple[str, ...] | list[str] | set[str] | frozenset[str],
    *,
    manifest_path: str | Path | None = None,
    environ: Any | None = None,
) -> DataCsvScanWindow | None:
    """Return the sole approved build binding; every mismatch stays disabled."""
    if (
        build_id != BUILD_ID
        or manifest_sha256 != BUILD_MANIFEST_SHA256
        or frozenset(modules) != frozenset({"datasets"})
        or len(modules) != 1
    ):
        return None

    if not _runtime_binding_matches(manifest_path, os.environ if environ is None else environ):
        return None

    domain_module = importlib.import_module("chrom_platform.modules.data_center.tlc_author_domain")
    module_path = Path(domain_module.__file__ or "")
    try:
        module_sha256 = hashlib.sha256(module_path.read_bytes()).hexdigest()
    except (OSError, ValueError):
        return None
    if module_sha256 != DOMAIN_MODULE_SHA256:
        return None

    domain = domain_module.make_domain()
    profile = domain_module.make_profile()
    mapping = dict(domain_module.SOURCE_COLUMN_MAPPING)
    from chrom_platform.modules.data_center.tlc_author_domain import PROJECT_ID

    if (
        PROJECT_ID != "project_data_accept_20261026"
        or domain.id != "tlc_author_measurement_v1"
        or domain.version != "1"
        or domain.identity_fields != ["measurement_id"]
        or domain.deduplication_policy != "reject"
        or domain.supported_import_modes != ["append"]
        or profile.profile_id != "tlc_author_measurement_v1"
        or profile.domain_id != domain.id
        or profile.schema_version != domain.version
        or profile.encoding != "gb18030"
        or profile.field_bounds.get("rf_observed") is None
        or profile.field_bounds["rf_observed"].minimum != 0
        or profile.field_bounds["rf_observed"].maximum != 1
        or mapping.keys() != domain.record_schema.keys()
    ):
        return None

    from .defender_profile_candidate import _classify_fixed_clean

    reviewed_profile = _ResultProfile(
        profile_id=DATASET_SCAN_PROFILE_ID,
        product_version=DEFENDER_PRODUCT_VERSION,
        clean_exit_code=0,
        detection_exit_code=None,
        classify=_classify_fixed_clean,
        expected_engine_version=DEFENDER_ENGINE_VERSION,
        expected_signer_subject=DEFENDER_SIGNER_SUBJECT,
        allowed_signature_versions=None,
    )
    from .dataset_csv_elevated_runner import DatasetCsvElevatedRunner, DatasetCsvElevatedScanner

    elevated_runner = DatasetCsvElevatedRunner(
        source_manifest_sha256=os.environ.get("CHROM_BACKEND_SOURCE_MANIFEST_SHA256", "")
    )
    base_scanner = DefenderCommandScanner(
        runner=elevated_runner,
        reviewed_profile=reviewed_profile,
        allowed_inputs=frozenset({(INPUT_SHA256, INPUT_SIZE_BYTES)}),
        expected_exe_sha256=DEFENDER_EXE_SHA256,
        allow_signature_updates=True,
    )
    scanner = DatasetCsvElevatedScanner(base_scanner, elevated_runner)
    return DataCsvScanWindow(
        project_id=PROJECT_ID,
        domain=domain,
        profile=profile,
        source_column_mapping=mapping,
        scan_policy=DatasetCsvScanPolicy(
            project_id=PROJECT_ID,
            profile_id=DATASET_SCAN_PROFILE_ID,
            encoding="gb18030",
            allowed_inputs=frozenset({(INPUT_SHA256, INPUT_SIZE_BYTES)}),
            max_size_bytes=16 * 1024 * 1024,
            allowed_kind="dataset_package",
            source_ref_type="artifact",
        ),
        scanner=scanner,
    )


def _runtime_binding_matches(manifest_path: str | Path | None, environ: Any) -> bool:
    """Require the launcher-protected data acceptance runtime's fixed bindings."""
    try:
        expected_manifest = EXPECTED_MANIFEST_PATH.resolve()
        if manifest_path is None or Path(manifest_path).resolve() != expected_manifest:
            return False
        configured_manifest = environ.get("CHROM_BUILD_MANIFEST", "")
        if not isinstance(configured_manifest, str) or Path(configured_manifest).resolve() != expected_manifest:
            return False
        local_app_data = environ.get("LOCALAPPDATA", "")
        if not isinstance(local_app_data, str) or not local_app_data:
            return False
        if Path(local_app_data).resolve() != EXPECTED_LOCALAPPDATA.resolve():
            return False
        expected_runtime = EXPECTED_RUNTIME_ROOT.resolve()
        runtime_file = (expected_runtime / "runtime.json").resolve()
        if not runtime_file.is_file():
            return False
        # run-local validates the runtime's protected ACL before exporting its
        # values. Re-read the fixed file here so environment-only spoofing cannot
        # enroll a profile in an unrelated process.
        runtime = json.loads(runtime_file.read_text(encoding="utf-8-sig"))
        if not isinstance(runtime, dict):
            return False
        expected_artifact_root = (expected_runtime / "artifacts").resolve()
        expected_state_root = (expected_runtime / "state").resolve()
        ready_file = Path(environ.get("WORKER_READY_FILE", "")).resolve()
        try:
            ready_file.relative_to(expected_state_root)
        except ValueError:
            return False
        if (
            Path(environ.get("ARTIFACT_ROOT", "")).resolve() != expected_artifact_root
            or Path(runtime.get("ARTIFACT_ROOT", "")).resolve() != expected_artifact_root
            or Path(runtime.get("CHROM_BUILD_MANIFEST", "")).resolve() != expected_manifest
            or environ.get("CHROM_ACCEPTANCE_DATABASE_SCHEMA") != "chrom_data_accept_20261026"
            or runtime.get("CHROM_ACCEPTANCE_DATABASE_SCHEMA") != "chrom_data_accept_20261026"
            or environ.get("CHROM_DEFAULT_PROJECT_ID") != "project_data_accept_20261026"
            or runtime.get("CHROM_DEFAULT_PROJECT_ID") != "project_data_accept_20261026"
            or environ.get("CHROM_PROJECT_IDS") != "project_data_accept_20261026"
            or runtime.get("CHROM_PROJECT_IDS") != "project_data_accept_20261026"
            or environ.get("WORKER_QUEUE") != "chrom-platform-data-accept-20261026"
            or runtime.get("WORKER_QUEUE") != "chrom-platform-data-accept-20261026"
            or environ.get("API_PORT") != "9012"
            or str(runtime.get("API_PORT", "")) != "9012"
            or environ.get("FRONTEND_PORT") != "5174"
            or str(runtime.get("FRONTEND_PORT", "")) != "5174"
        ):
            return False
        runtime_ready_file = Path(runtime.get("WORKER_READY_FILE", "")).resolve()
        try:
            runtime_ready_file.relative_to(expected_state_root)
        except ValueError:
            return False
        if runtime_ready_file != ready_file:
            return False
        from sqlalchemy.engine import make_url

        database_url = make_url(environ.get("DATABASE_URL", ""))
        sync_database_url = make_url(environ.get("SYNC_DATABASE_URL", ""))
        runtime_database_url = make_url(runtime.get("DATABASE_URL", ""))
        runtime_sync_database_url = make_url(runtime.get("SYNC_DATABASE_URL", ""))
        redis_url = urlsplit(environ.get("REDIS_URL", ""))
        runtime_redis_url = urlsplit(runtime.get("REDIS_URL", ""))
        return bool(
            database_url.drivername == "mysql+asyncmy"
            and database_url.host == "127.0.0.1" and database_url.port == 3306
            and database_url.database == "chrom_data_accept_20261026"
            and database_url.username == EXPECTED_DATABASE_USERNAME
            and sync_database_url.drivername == "mysql+pymysql"
            and sync_database_url.host == "127.0.0.1" and sync_database_url.port == 3306
            and sync_database_url.database == "chrom_data_accept_20261026"
            and sync_database_url.username == EXPECTED_DATABASE_USERNAME
            and runtime_database_url == database_url
            and runtime_sync_database_url == sync_database_url
            and runtime_redis_url == redis_url
            and redis_url.scheme == "redis" and redis_url.hostname == "127.0.0.1"
            and redis_url.port == 6379 and not redis_url.username and not redis_url.password
            and not redis_url.query and not redis_url.fragment
        )
    except Exception:
        return False


__all__ = [
    "BUILD_ID", "BUILD_MANIFEST_SHA256", "DATASET_SCAN_PROFILE_ID", "DataCsvScanWindow",
    "EXPECTED_DATABASE_USERNAME", "EXPECTED_LOCALAPPDATA", "EXPECTED_MANIFEST_PATH",
    "EXPECTED_MANIFEST_RELATIVE_PATH", "EXPECTED_RUNTIME_ROOT", "build_data_csv_window",
]
