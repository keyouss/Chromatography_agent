from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from chrom_platform.assembly import profile as profile_builder
from chrom_platform.assembly.manifest import load_manifest


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "backend" / "src"
DERIVED_HELPER = ROOT / "builds" / "model_package_derived_receipt.py"
PROFILE_SOURCE = SOURCE_ROOT / "chrom_platform/assembly/profile.py"
INVENTORY = ROOT / "builds" / "model_package_source_inventory.json"
SOURCE_INTEGRITY = ROOT / "builds" / "model_package_source_integrity.py"


def _derived():
    spec = importlib.util.spec_from_file_location("_test_model_package_derived_receipt", DERIVED_HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest(tmp_path: Path, *, model_center: bool, build_id: str):
    raw = json.loads((ROOT / "builds/universal-v1.json").read_text(encoding="utf-8"))
    raw.update({
        "build_id": build_id,
        "modules": ["prediction", *(["model_center"] if model_center else [])],
        "chromatography_types": ["gc"],
        "prediction_tools": {"gc": ["single"]},
        "utilities": [],
    })
    raw.pop("assistant_features", None)
    path = tmp_path / f"{build_id}.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path, load_manifest(path)


def _validate(
    helper,
    receipt_path: Path,
    manifest,
    package: Path,
    *,
    profile_source: Path = PROFILE_SOURCE,
    source_manifest: Path | None = None,
) -> None:
    helper.validate_candidate_receipt(
        receipt_path,
        manifest=manifest.model_dump(mode="json"),
        source_root=SOURCE_ROOT,
        package_root=package,
        source_manifest_path=source_manifest or package / "SOURCE_MANIFEST.json",
        profile_source_path=profile_source,
        helper_path=DERIVED_HELPER,
        inventory_path=INVENTORY,
    )


def test_gc_model_center_candidate_binds_trimmed_schema_and_loader_pin(tmp_path: Path) -> None:
    helper = _derived()
    manifest_path, manifest = _manifest(
        tmp_path,
        model_center=True,
        build_id="derived-gc-model-center",
    )
    source_pin = SOURCE_ROOT / "chrom_platform/adapters/gc/package_source_pins.py"
    original_source_pin = source_pin.read_bytes()
    receipt_path = tmp_path / "derived-gc-model-center.candidate.json"
    package = profile_builder.assemble_profile(
        manifest_path,
        tmp_path / "packages",
        candidate_receipt_path=receipt_path,
    )

    source_schema = SOURCE_ROOT / "chrom_platform/modules/prediction/schemas.py"
    output_schema = package / "chrom_platform/modules/prediction/schemas.py"
    output_pin = package / "chrom_platform/adapters/gc/package_source_pins.py"
    assert output_schema.read_bytes() != source_schema.read_bytes()
    assert source_pin.read_bytes() == original_source_pin
    assert "chrom_platform/modules/model_center/package_verification.py" in helper.expected_gc_closure(
        manifest.model_dump(mode="json")
    )[0]
    assert receipt_path.is_file() and package not in receipt_path.parents
    _validate(helper, receipt_path, manifest, package)

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "candidate"
    assert receipt["approval"] is None
    assert receipt["source_binding"]["schema_source_sha256"] != receipt["source_binding"]["schema_output_sha256"]
    assert receipt["required_closure_sha256"]["chrom_platform/adapters/gc/package_source_pins.py"] == (
        receipt["source_binding"]["pin_output_sha256"]
    )
    assert receipt["required_closure_sha256"]["chrom_platform/modules/prediction/schemas.py"] == (
        receipt["source_binding"]["schema_output_sha256"]
    )

    env = dict(os.environ)
    env["PYTHONPATH"] = str(package)
    check = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; "
            "import chrom_platform.adapters.gc.package_loader as m; "
            "assert Path(m.__file__).resolve().is_relative_to(Path.cwd().resolve()); "
            "m.GcSafetensorsPackageLoader.verify_integrity(); print('integrity-ok')",
        ],
        cwd=package,
        env=env,
        capture_output=True,
        text=True,
        timeout=12,
        check=False,
    )
    assert check.returncode == 0, check.stdout + check.stderr
    assert "integrity-ok" in check.stdout

    schema_relative = "chrom_platform/modules/prediction/schemas.py"
    pin_relative = "chrom_platform/adapters/gc/package_source_pins.py"
    damaged_schema = package / schema_relative
    damaged_schema.write_bytes(damaged_schema.read_bytes() + b"\n# changed after candidate receipt\n")
    pin_path = package / pin_relative
    helper.derive_output_schema_pin(
        source_pin,
        pin_path,
        damaged_schema,
    )
    schema_sha = hashlib.sha256(damaged_schema.read_bytes()).hexdigest()
    pin_sha = hashlib.sha256(pin_path.read_bytes()).hexdigest()

    source_manifest_path = package / "SOURCE_MANIFEST.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    for row in source_manifest["files"]:
        if row["path"] == schema_relative:
            row["sha256"] = schema_sha
        elif row["path"] == pin_relative:
            row["sha256"] = pin_sha
    source_manifest_path.write_text(
        json.dumps(source_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    receipt["required_closure_sha256"][schema_relative] = schema_sha
    receipt["required_closure_sha256"][pin_relative] = pin_sha
    receipt["source_binding"]["schema_output_sha256"] = schema_sha
    receipt["source_binding"]["pin_output_sha256"] = pin_sha
    receipt["source_binding"]["source_manifest_sha256"] = hashlib.sha256(
        source_manifest_path.read_bytes()
    ).hexdigest()
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError, match="MODEL_PACKAGE_RECEIPT_TRIMMED_SCHEMA_MISMATCH"):
        _validate(helper, receipt_path, manifest, package)


def test_gc_only_requires_gc_closure_and_forbids_model_center_copy(tmp_path: Path) -> None:
    helper = _derived()
    manifest_path, manifest = _manifest(
        tmp_path,
        model_center=False,
        build_id="derived-gc-only",
    )
    receipt_path = tmp_path / "derived-gc-only.candidate.json"
    package = profile_builder.assemble_profile(
        manifest_path,
        tmp_path / "packages",
        candidate_receipt_path=receipt_path,
    )
    required, forbidden, matrix = helper.expected_gc_closure(manifest.model_dump(mode="json"))
    assert matrix == "gc-only"
    assert "chrom_platform/adapters/gc/package_loader.py" in required
    assert "chrom_platform/adapters/gc/validation.py" in required
    assert "chrom_platform/core/model_load_observation.py" in required
    assert "chrom_platform/modules/model_center/package_verification.py" not in required
    assert "chrom_platform/modules/model_center" in forbidden
    assert not (package / "chrom_platform/modules/model_center").exists()
    _validate(helper, receipt_path, manifest, package)


def test_non_gc_matrix_forbids_gc_runtime_and_profile_does_not_load_gc_helpers(tmp_path: Path, monkeypatch) -> None:
    manifest = load_manifest(ROOT / "builds/sfc-v1.json")
    helper = _derived()
    required, forbidden, matrix = helper.expected_gc_closure(manifest.model_dump(mode="json"))
    assert matrix == "non-gc"
    assert required == frozenset()
    assert "chrom_platform/adapters/gc" in forbidden
    assert "chrom_platform/assembly/gc_candidate_policy.py" in forbidden

    def forbidden_load(*args, **kwargs):
        raise AssertionError("non-GC profile must not load GC-only build helpers")

    monkeypatch.setattr(profile_builder, "_load_gc_profile_source_integrity", forbidden_load)
    monkeypatch.setattr(profile_builder, "_load_gc_derived_receipt_helper", forbidden_load)
    package = tmp_path / "non-gc"
    profile_builder._copy_tree(SOURCE_ROOT, package, manifest)
    assert not (package / "chrom_platform/adapters/gc").exists()
    assert not (package / "chrom_platform/assembly/gc_candidate_policy.py").exists()


def test_candidate_receipt_destination_rejects_overlap_deliverables_and_existing_file(tmp_path: Path) -> None:
    helper = _derived()
    package = tmp_path / "pkg"
    package.mkdir()
    with pytest.raises(ValueError, match="MODEL_PACKAGE_RECEIPT_OVERLAPS_PACKAGE"):
        helper.validate_receipt_destination(package / "inside.json", package_root=package)
    with pytest.raises(ValueError, match="MODEL_PACKAGE_RECEIPT_DELIVERABLES_FORBIDDEN"):
        helper.validate_receipt_destination(
            tmp_path / "deliverables/candidate.json",
            package_root=package,
        )
    existing = tmp_path / "existing.json"
    existing.write_text("{}", encoding="utf-8")
    with pytest.raises(FileExistsError, match="MODEL_PACKAGE_RECEIPT_ALREADY_EXISTS"):
        helper.validate_receipt_destination(existing, package_root=package)
    with pytest.raises(ValueError, match="MODEL_PACKAGE_RECEIPT_PATH_INVALID"):
        helper.validate_receipt_destination(tmp_path / "../escaped.json", package_root=package)
    with pytest.raises(ValueError, match="MODEL_PACKAGE_RECEIPT_OVERLAPS_PACKAGE"):
        helper.validate_receipt_destination(
            tmp_path / "packages",
            package_root=tmp_path / "packages" / "gc-profile",
        )
    with pytest.raises(ValueError, match="MODEL_PACKAGE_RECEIPT_DELIVERABLES_FORBIDDEN"):
        helper.validate_receipt_destination(
            tmp_path / "receipt.json",
            package_root=tmp_path / "deliverables" / "gc-profile",
        )
    with pytest.raises(ValueError, match="MODEL_PACKAGE_RECEIPT_PATH_INVALID"):
        helper.validate_receipt_destination(
            tmp_path / "receipt.json",
            package_root=tmp_path / "profiles" / ".." / "gc-profile",
        )

    junction = tmp_path / "reparse-parent"
    actual_lstat = helper.os.lstat

    def fake_lstat(path):
        if Path(path) == junction:
            return SimpleNamespace(st_mode=0, st_file_attributes=helper._REPARSE_FLAG)
        return actual_lstat(path)

    helper.os.lstat = fake_lstat
    try:
        with pytest.raises(ValueError, match="MODEL_PACKAGE_RECEIPT_REPARSE_POINT"):
            helper.validate_receipt_destination(
                junction / "receipt.json",
                package_root=package,
            )
    finally:
        helper.os.lstat = actual_lstat


@pytest.mark.parametrize("relative", [
    "chrom_platform/adapters/gc/validation.py",
    "chrom_platform/core/model_load_observation.py",
])
def test_receipt_validator_rejects_missing_new_guard_closure_entry(
    tmp_path: Path, relative: str,
) -> None:
    helper = _derived()
    manifest_path, manifest = _manifest(
        tmp_path,
        model_center=True,
        build_id="derived-gc-missing-receipt-entry",
    )
    receipt_path = tmp_path / "candidate.json"
    package = profile_builder.assemble_profile(
        manifest_path,
        tmp_path / "packages",
        candidate_receipt_path=receipt_path,
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["required_closure_sha256"].pop(relative)
    damaged_receipt = tmp_path / "damaged.json"
    damaged_receipt.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError, match="MODEL_PACKAGE_RECEIPT_CLOSURE_MISMATCH"):
        _validate(helper, damaged_receipt, manifest, package)


def test_candidate_rejects_output_pin_profile_path_and_missing_actual_helper(tmp_path: Path) -> None:
    helper = _derived()
    manifest_path, manifest = _manifest(
        tmp_path,
        model_center=True,
        build_id="derived-gc-invalid-output-inputs",
    )
    receipt_path = tmp_path / "candidate.json"
    package = profile_builder.assemble_profile(
        manifest_path,
        tmp_path / "packages",
        candidate_receipt_path=receipt_path,
    )
    pin_path = package / "chrom_platform/adapters/gc/package_source_pins.py"
    pin_bytes = pin_path.read_bytes()
    pin_path.write_bytes(pin_bytes + b"\n# pin tamper\n")
    with pytest.raises(ValueError, match="MODEL_PACKAGE_RECEIPT_GENERATED_PIN_MISMATCH"):
        _validate(helper, receipt_path, manifest, package)
    pin_path.write_bytes(pin_bytes)

    helper_path = package / "chrom_platform/modules/model_center/package_verification.py"
    helper_bytes = helper_path.read_bytes()
    helper_path.unlink()
    with pytest.raises(ValueError, match="MODEL_PACKAGE_RECEIPT_FILE_MISSING"):
        _validate(helper, receipt_path, manifest, package)
    helper_path.write_bytes(helper_bytes)

    altered_profile = tmp_path / "profile-copy.py"
    altered_profile.write_bytes(PROFILE_SOURCE.read_bytes() + b"\n# altered profile source\n")
    with pytest.raises(ValueError, match="MODEL_PACKAGE_RECEIPT_PROFILE_SOURCE_PATH_MISMATCH"):
        _validate(helper, receipt_path, manifest, package, profile_source=altered_profile)

    source_manifest_path = package / "SOURCE_MANIFEST.json"
    source_manifest_bytes = source_manifest_path.read_bytes()
    receipt_bytes = receipt_path.read_bytes()
    source_manifest = json.loads(source_manifest_bytes)
    source_manifest["profile_transform"]["profile_source_sha256"] = "0" * 64
    source_manifest_path.write_text(json.dumps(source_manifest), encoding="utf-8")
    receipt = json.loads(receipt_bytes)
    receipt["source_binding"]["source_manifest_sha256"] = hashlib.sha256(
        source_manifest_path.read_bytes()
    ).hexdigest()
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError, match="MODEL_PACKAGE_RECEIPT_SOURCE_MANIFEST_TRANSFORM_MISMATCH"):
        _validate(helper, receipt_path, manifest, package)
    source_manifest_path.write_bytes(source_manifest_bytes)
    receipt_path.write_bytes(receipt_bytes)


def test_actual_build_manifest_is_bound_to_operator_manifest_and_source_manifest_hash(tmp_path: Path) -> None:
    helper = _derived()
    manifest_path, manifest = _manifest(
        tmp_path,
        model_center=True,
        build_id="derived-gc-build-manifest-binding",
    )
    receipt_path = tmp_path / "candidate.json"
    package = profile_builder.assemble_profile(
        manifest_path,
        tmp_path / "packages",
        candidate_receipt_path=receipt_path,
    )
    actual_manifest_path = package / "build-manifest.json"
    source_manifest_path = package / "SOURCE_MANIFEST.json"
    receipt_bytes = receipt_path.read_bytes()
    source_manifest_bytes = source_manifest_path.read_bytes()
    actual_manifest_bytes = actual_manifest_path.read_bytes()

    # A semantic change is rejected even when the candidate manifest row and
    # outer receipt hash are coherently updated to match the changed file.
    altered_actual = json.loads(actual_manifest_bytes)
    altered_actual["modules"].append("assistant")
    actual_manifest_path.write_text(json.dumps(altered_actual), encoding="utf-8")
    source_manifest = json.loads(source_manifest_bytes)
    actual_sha = hashlib.sha256(actual_manifest_path.read_bytes()).hexdigest()
    for row in source_manifest["files"]:
        if row["path"] == "build-manifest.json":
            row["sha256"] = actual_sha
            break
    source_manifest_path.write_text(json.dumps(source_manifest), encoding="utf-8")
    receipt = json.loads(receipt_bytes)
    receipt["source_binding"]["source_manifest_sha256"] = hashlib.sha256(
        source_manifest_path.read_bytes()
    ).hexdigest()
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError, match="MODEL_PACKAGE_RECEIPT_BUILD_MANIFEST_PROFILE_MISMATCH"):
        _validate(helper, receipt_path, manifest, package)

    # Even a semantically unchanged rewrite is rejected when its actual bytes
    # no longer match the build-manifest entry in SOURCE_MANIFEST.
    source_manifest_path.write_bytes(source_manifest_bytes)
    receipt_path.write_bytes(receipt_bytes)
    actual_manifest_path.write_bytes(actual_manifest_bytes + b" ")
    with pytest.raises(ValueError, match="MODEL_PACKAGE_RECEIPT_BUILD_MANIFEST_HASH_MISMATCH"):
        _validate(helper, receipt_path, manifest, package)

    actual_manifest_path.write_bytes(actual_manifest_bytes)
    with pytest.raises(ValueError, match="MODEL_PACKAGE_RECEIPT_SOURCE_MANIFEST_PATH_MISMATCH"):
        _validate(
            helper,
            receipt_path,
            manifest,
            package,
            source_manifest=package / "other-manifest.json",
        )
