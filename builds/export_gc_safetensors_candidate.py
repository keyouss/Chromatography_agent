"""Create one data-only GC package candidate from the fixed pinned checkpoint.

This is a local preparation tool, not an artifact approval, scan, or model run.
It accepts no paths or module names from the command line.
"""

from __future__ import annotations

import hashlib
import io
import importlib.metadata
import json
import os
import platform
import secrets
import stat
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "backend" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from chrom_platform.adapters.gc.manifest import (  # noqa: E402
    GC_BUSINESS_SCOPE_ID,
    GC_INPUT_SCHEMA_VERSION,
    GC_MODEL_PATH,
    GC_MODEL_SHA256,
    GC_MODEL_VERSION,
    GC_OUTPUT_SCHEMA_VERSION,
    GC_PREPROCESSING_PROTOCOL_ID,
)
from chrom_platform.adapters.gc.serving_profile import (  # noqa: E402
    GC_REFERENCE_FIXTURE_SHA256,
)
from chrom_platform.artifact_core.model_package_manifest import (  # noqa: E402
    FIXED_FORMAT,
    FIXED_LOADER_ID,
    GOLDEN_NAME,
    MANIFEST_NAME,
    MAX_CONTAINER_BYTES,
    MAX_GOLDEN_BYTES,
    MAX_MEMBER_BYTES,
    MAX_TENSOR_BYTES,
    MAX_TENSOR_ELEMENTS,
    MAX_TENSORS,
    RESOURCE_LIMITS_APPROVED,
    WEIGHTS_NAME,
    inspect_model_package,
)


CHECKPOINT = Path(GC_MODEL_PATH)
FIXTURE = ROOT / "testing" / "fixtures" / "priority-launch" / "gc-reference-preprocessing-v2.json"
CANDIDATE_ROOT = ROOT / "testing" / "runs" / "2026-10-16-model-package-chain" / "real-gc-candidates"
MAX_SOURCE_CHECKPOINT_BYTES = 8 * 1024 * 1024
_DTYPE_NAMES = {
    "torch.bool": "BOOL",
    "torch.uint8": "U8",
    "torch.int8": "I8",
    "torch.int16": "I16",
    "torch.int32": "I32",
    "torch.int64": "I64",
    "torch.float16": "F16",
    "torch.bfloat16": "BF16",
    "torch.float32": "F32",
    "torch.float64": "F64",
}


class CandidateExportError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _reject_reparse_path(path: Path) -> None:
    try:
        absolute = path.absolute()
        if path.resolve(strict=True) != absolute:
            raise CandidateExportError("FIXED_INPUT_PATH_INVALID")
        for component in (absolute, *absolute.parents):
            info = component.lstat()
            if component.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise CandidateExportError("FIXED_INPUT_REPARSE_POINT")
    except CandidateExportError:
        raise
    except OSError as exc:
        raise CandidateExportError("FIXED_INPUT_PATH_INVALID") from exc


def _checked_fixed_inputs() -> tuple[bytes, bytes]:
    _reject_reparse_path(CHECKPOINT)
    _reject_reparse_path(FIXTURE)
    try:
        checkpoint_size = CHECKPOINT.stat().st_size
        if checkpoint_size <= 0 or checkpoint_size > MAX_SOURCE_CHECKPOINT_BYTES:
            raise CandidateExportError("CHECKPOINT_SIZE_INVALID")
        with CHECKPOINT.open("rb") as stream:
            checkpoint = stream.read(MAX_SOURCE_CHECKPOINT_BYTES + 1)
        if len(checkpoint) != checkpoint_size or _sha256(checkpoint) != GC_MODEL_SHA256:
            raise CandidateExportError("CHECKPOINT_PIN_MISMATCH")
        if len(checkpoint) > MAX_SOURCE_CHECKPOINT_BYTES:
            raise CandidateExportError("CHECKPOINT_SIZE_INVALID")
        with FIXTURE.open("rb") as stream:
            fixture = stream.read(MAX_GOLDEN_BYTES + 1)
        if len(fixture) > MAX_GOLDEN_BYTES:
            raise CandidateExportError("FIXTURE_SIZE_INVALID")
        if _sha256(fixture) != GC_REFERENCE_FIXTURE_SHA256:
            raise CandidateExportError("FIXTURE_PIN_MISMATCH")
        return checkpoint, fixture
    except CandidateExportError:
        raise
    except OSError as exc:
        raise CandidateExportError("FIXED_INPUT_UNAVAILABLE") from exc


def _validated_state(state: object, torch: Any) -> dict[str, Any]:
    if not isinstance(state, Mapping) or not state:
        raise CandidateExportError("CHECKPOINT_STATE_INVALID")
    if len(state) > MAX_TENSORS:
        raise CandidateExportError("TENSOR_COUNT_LIMIT")
    result: dict[str, Any] = {}
    total_elements = 0
    total_bytes = 0
    for name, tensor in state.items():
        if not isinstance(name, str) or not name or name in result:
            raise CandidateExportError("TENSOR_NAME_INVALID")
        if not isinstance(tensor, torch.Tensor) or tensor.device.type != "cpu":
            raise CandidateExportError("TENSOR_TYPE_OR_DEVICE_INVALID")
        if tensor.layout != torch.strided:
            raise CandidateExportError("TENSOR_LAYOUT_UNSUPPORTED")
        if str(tensor.dtype) not in _DTYPE_NAMES:
            raise CandidateExportError("TENSOR_DTYPE_UNSUPPORTED")
        shape = tuple(int(dim) for dim in tensor.shape)
        if len(shape) > 16 or any(dim <= 0 for dim in shape):
            raise CandidateExportError("TENSOR_SHAPE_INVALID")
        elements = 1
        for dim in shape:
            elements *= dim
            if elements > MAX_TENSOR_ELEMENTS:
                raise CandidateExportError("TENSOR_SIZE_LIMIT")
        byte_count = elements * int(tensor.element_size())
        total_elements += elements
        total_bytes += byte_count
        if total_elements > MAX_TENSOR_ELEMENTS or byte_count > MAX_TENSOR_BYTES or total_bytes > MAX_TENSOR_BYTES:
            raise CandidateExportError("TENSOR_SIZE_LIMIT")
        if tensor.is_floating_point() and not bool(torch.isfinite(tensor).all().item()):
            raise CandidateExportError("TENSOR_NONFINITE")
        # Clone makes the serialized payload independent from checkpoint storage.
        result[name] = tensor.detach().contiguous().clone()
    return result


def _manifest(weights: bytes, golden: bytes) -> bytes:
    value = {
        "schema_version": "1.0",
        "version": GC_MODEL_VERSION,
        "input_schema_version": GC_INPUT_SCHEMA_VERSION,
        "output_schema_version": GC_OUTPUT_SCHEMA_VERSION,
        "task_type": "gc",
        "scope_ids": [GC_BUSINESS_SCOPE_ID],
        "loader_id": FIXED_LOADER_ID,
        "format": FIXED_FORMAT,
        "preprocessing_protocol_id": GC_PREPROCESSING_PROTOCOL_ID,
        "members": {
            WEIGHTS_NAME: {"sha256": _sha256(weights), "size_bytes": len(weights)},
            GOLDEN_NAME: {"sha256": _sha256(golden), "size_bytes": len(golden)},
        },
    }
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _archive(members: Mapping[str, bytes]) -> bytes:
    if tuple(members) != (MANIFEST_NAME, WEIGHTS_NAME, GOLDEN_NAME):
        raise CandidateExportError("PACKAGE_MEMBER_SET_INVALID")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, strict_timestamps=True) as archive:
        for name, content in members.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(info, content)
    return output.getvalue()


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _overlaps(left: Path, right: Path) -> bool:
    return _within(left, right) or _within(right, left)


def _new_candidate_dir(
    parent: Path = CANDIDATE_ROOT,
    *,
    repo_root: Path = ROOT,
    source_path: Path = CHECKPOINT,
    deliverables_path: Path | None = None,
) -> Path:
    parent = parent.absolute()
    repo = repo_root.resolve(strict=True)
    source = source_path.resolve(strict=False)
    deliverables = (deliverables_path or (repo_root / "deliverables")).resolve(strict=False)
    resolved_parent = parent.resolve(strict=False)
    if not _within(resolved_parent, repo):
        raise CandidateExportError("CANDIDATE_ROOT_INVALID")
    if _overlaps(resolved_parent, source) or _overlaps(resolved_parent, deliverables):
        raise CandidateExportError("CANDIDATE_ROOT_OVERLAP")
    try:
        relative = parent.relative_to(repo_root)
    except ValueError as exc:
        raise CandidateExportError("CANDIDATE_ROOT_INVALID") from exc
    if not relative.parts:
        raise CandidateExportError("CANDIDATE_ROOT_INVALID")
    components = [repo_root.joinpath(*relative.parts[:index]) for index in range(1, len(relative.parts) + 1)]
    for current in components:
        if current.exists():
            try:
                info = current.lstat()
            except OSError as exc:
                raise CandidateExportError("CANDIDATE_ROOT_INVALID") from exc
            if current.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise CandidateExportError("CANDIDATE_ROOT_REPARSE_POINT")
            if current.resolve(strict=True) != current:
                raise CandidateExportError("CANDIDATE_ROOT_REPARSE_POINT")
    parent.mkdir(parents=True, exist_ok=True)
    if parent.resolve(strict=True) != parent or parent.is_symlink():
        raise CandidateExportError("CANDIDATE_ROOT_INVALID")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for _ in range(8):
        target = parent / f"{stamp}-{secrets.token_hex(8)}"
        try:
            target.mkdir(exist_ok=False)
            return target
        except FileExistsError:
            continue
    raise CandidateExportError("CANDIDATE_DIRECTORY_COLLISION")


def _exclusive_write(path: Path, content: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise CandidateExportError("OUTPUT_ALREADY_EXISTS") from exc


def _load_checkpoint(torch: Any, checkpoint_bytes: bytes) -> object:
    try:
        return torch.load(io.BytesIO(checkpoint_bytes), weights_only=True, map_location="cpu")
    except Exception as exc:
        raise CandidateExportError("SAFE_CHECKPOINT_LOAD_FAILED") from exc


def _safetensors_roundtrip(state: Mapping[str, Any], torch: Any, save: Any, load: Any) -> bytes:
    try:
        weights = save(state)
        restored = load(weights)
        if set(restored) != set(state) or any(
            restored[key].shape != state[key].shape
            or restored[key].dtype != state[key].dtype
            or restored[key].device.type != "cpu"
            or state[key].device.type != "cpu"
            or not torch.equal(restored[key], state[key])
            for key in state
        ):
            raise CandidateExportError("SAFETENSORS_ROUNDTRIP_MISMATCH")
        return weights
    except CandidateExportError:
        raise
    except Exception as exc:
        raise CandidateExportError("SAFETENSORS_ROUNDTRIP_FAILED") from exc


def _write_failure_receipt(path: Path, code: str) -> None:
    try:
        if path.parent == CANDIDATE_ROOT and path.is_dir() and not path.is_symlink():
            _reject_reparse_path(path)
            _exclusive_write(
                path / "export-failure.json",
                json.dumps({"status": "FAILED", "error_code": code}, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            )
    except Exception:
        pass


def export_candidate() -> Mapping[str, Any]:
    checkpoint_bytes, golden = _checked_fixed_inputs()
    try:
        import torch
        from safetensors.torch import load as load_safetensors
        from safetensors.torch import save as save_safetensors
    except ImportError as exc:
        raise CandidateExportError("EXPORT_DEPENDENCY_UNAVAILABLE") from exc
    raw_state = _load_checkpoint(torch, checkpoint_bytes)
    state = _validated_state(raw_state, torch)
    weights = _safetensors_roundtrip(state, torch, save_safetensors, load_safetensors)
    if len(weights) > MAX_MEMBER_BYTES:
        raise CandidateExportError("WEIGHTS_MEMBER_SIZE_LIMIT")
    members = {
        MANIFEST_NAME: _manifest(weights, golden),
        WEIGHTS_NAME: weights,
        GOLDEN_NAME: golden,
    }
    package = _archive(members)
    if len(package) > MAX_CONTAINER_BYTES:
        raise CandidateExportError("PACKAGE_SIZE_LIMIT")
    target_dir = _new_candidate_dir()
    package_path = target_dir / "candidate.zip"
    try:
        _exclusive_write(package_path, package)
        digest = _sha256(package)
        inspected = inspect_model_package(
            package_path, expected_sha256=digest, expected_size_bytes=len(package),
        )
        with zipfile.ZipFile(package_path, "r") as archive:
            infos = archive.infolist()
            expected_mode = (stat.S_IFREG | 0o600) << 16
            if any(
                info.compress_type != zipfile.ZIP_STORED
                or info.date_time != (1980, 1, 1, 0, 0, 0)
                or info.create_system != 3
                or info.external_attr != expected_mode
                or info.extra
                or info.comment
                for info in infos
            ):
                raise CandidateExportError("PACKAGE_METADATA_INVALID")
        receipt = {
            "status": "CANDIDATE_ONLY",
            "approval": None,
            "scanner": "NOT_RUN",
            "science": "NOT_RUN",
            "source_sha256": GC_MODEL_SHA256,
            "fixture_sha256": GC_REFERENCE_FIXTURE_SHA256,
            "package_sha256": digest,
            "package_size_bytes": len(package),
            "member_hashes": dict(inspected.member_hashes),
            "member_sizes": dict(inspected.member_sizes),
            "structure_validated": True,
            "resource_limits_approved": RESOURCE_LIMITS_APPROVED,
            "python_version": platform.python_version(),
            "torch_version": str(torch.__version__),
            "safetensors_version": importlib.metadata.version("safetensors"),
            "conversion": "DATA_ONLY_TENSOR_VALUE_PRESERVATION_ROUNDTRIP",
            "architecture": "NOT_RUN",
            "golden": "NOT_RUN",
        }
        _exclusive_write(
            target_dir / "candidate-provenance.json",
            json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"),
        )
        return {"candidate_directory": str(target_dir), **receipt}
    except CandidateExportError as exc:
        _write_failure_receipt(target_dir, exc.code)
        raise
    except Exception:
        _write_failure_receipt(target_dir, "UNEXPECTED")
        raise


def main() -> int:
    if len(sys.argv) != 1:
        print("EXPORT_FAILED:CLI_ARGUMENTS_NOT_ALLOWED")
        return 2
    try:
        result = export_candidate()
    except CandidateExportError as exc:
        print(f"EXPORT_FAILED:{exc.code}")
        return 1
    except Exception:
        print("EXPORT_FAILED:UNEXPECTED")
        return 1
    print(f"CANDIDATE_ONLY:{result['candidate_directory']}:{result['package_sha256']}:{result['package_size_bytes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
