from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from builds import export_gc_safetensors_candidate as exporter


class _All:
    def __init__(self, value: bool) -> None:
        self.value = value

    def item(self) -> bool:
        return self.value

    def all(self):
        return self


class _Tensor:
    device = type("Device", (), {"type": "cpu"})()
    layout = "strided"
    dtype = "torch.float32"
    shape = (2,)

    def __init__(self, values=(1.0, 2.0), *, floating=True, size=4, layout="strided", device="cpu", dtype="torch.float32") -> None:
        self.values = values
        self._floating = floating
        self._size = size
        self.layout = layout
        self.device = type("Device", (), {"type": device})()
        self.dtype = dtype
        self.shape = (len(values),)

    def element_size(self) -> int:
        return self._size

    def is_floating_point(self) -> bool:
        return self._floating

    def detach(self):
        return self

    def contiguous(self):
        return self

    def clone(self):
        return _Tensor(self.values, floating=self._floating, size=self._size, layout=self.layout, device=self.device.type, dtype=self.dtype)

    def __eq__(self, other) -> bool:
        return isinstance(other, _Tensor) and self.values == other.values and self.dtype == other.dtype


class _Torch:
    Tensor = _Tensor
    strided = "strided"

    @staticmethod
    def isfinite(tensor: _Tensor):
        return _All(all(value == value for value in tensor.values))

    @staticmethod
    def equal(left: _Tensor, right: _Tensor) -> bool:
        return left == right


def test_tensor_validation_clones_cpu_state_and_rejects_nonfinite_or_unsupported() -> None:
    original = _Tensor()
    state = exporter._validated_state({"layer.weight": original}, _Torch)
    assert state["layer.weight"] is not original
    assert state["layer.weight"].values == (1.0, 2.0)
    with pytest.raises(exporter.CandidateExportError, match="TENSOR_NONFINITE"):
        exporter._validated_state({"bad": _Tensor((float("nan"),))}, _Torch)
    with pytest.raises(exporter.CandidateExportError, match="TENSOR_TYPE_OR_DEVICE_INVALID"):
        exporter._validated_state({"bad": _Tensor(device="cuda")}, _Torch)
    with pytest.raises(exporter.CandidateExportError, match="TENSOR_DTYPE_UNSUPPORTED"):
        exporter._validated_state({"bad": _Tensor(dtype="torch.float8_e4m3fn")}, _Torch)


def test_checkpoint_loader_uses_weights_only_cpu_and_never_falls_back() -> None:
    class Torch:
        calls = []

        @classmethod
        def load(cls, path, **kwargs):
            cls.calls.append((path, kwargs))
            assert path.read() == b"pinned bytes"
            raise TypeError("weights_only unsupported")

    with pytest.raises(exporter.CandidateExportError, match="SAFE_CHECKPOINT_LOAD_FAILED"):
        exporter._load_checkpoint(Torch, b"pinned bytes")
    assert len(Torch.calls) == 1
    assert Torch.calls[0][1] == {"weights_only": True, "map_location": "cpu"}


def test_safetensors_roundtrip_requires_exact_values() -> None:
    state = {"layer.weight": _Tensor()}
    encoded = exporter._safetensors_roundtrip(state, _Torch, lambda value: b"bytes", lambda _: state)
    assert encoded == b"bytes"
    with pytest.raises(exporter.CandidateExportError, match="SAFETENSORS_ROUNDTRIP_MISMATCH"):
        exporter._safetensors_roundtrip(
            state, _Torch, lambda value: b"bytes", lambda _: {"layer.weight": _Tensor((9.0, 9.0))},
        )
    with pytest.raises(exporter.CandidateExportError, match="SAFETENSORS_ROUNDTRIP_MISMATCH"):
        exporter._safetensors_roundtrip(
            state, _Torch, lambda value: b"bytes", lambda _: {"layer.weight": _Tensor(dtype="torch.float64")},
        )


def test_manifest_and_archive_have_real_member_digests_and_fixed_stored_metadata() -> None:
    weights = b"safe-tensor-bytes"
    golden = b'{"fixed":true}'
    manifest_bytes = exporter._manifest(weights, golden)
    manifest = json.loads(manifest_bytes)
    assert manifest["members"]["weights.safetensors"] == {
        "sha256": hashlib.sha256(weights).hexdigest(), "size_bytes": len(weights),
    }
    package = exporter._archive({
        exporter.MANIFEST_NAME: manifest_bytes,
        exporter.WEIGHTS_NAME: weights,
        exporter.GOLDEN_NAME: golden,
    })
    import io

    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert [entry.filename for entry in archive.infolist()] == [
            "manifest.json", "weights.safetensors", "golden.json",
        ]
        assert all(entry.compress_type == zipfile.ZIP_STORED for entry in archive.infolist())
        assert all(entry.date_time == (1980, 1, 1, 0, 0, 0) for entry in archive.infolist())
        assert all(stat.S_IFMT(entry.external_attr >> 16) == stat.S_IFREG for entry in archive.infolist())


def test_exclusive_write_refuses_to_overwrite_existing_file(tmp_path) -> None:
    path = tmp_path / "candidate.zip"
    path.write_bytes(b"original")
    with pytest.raises(exporter.CandidateExportError, match="OUTPUT_ALREADY_EXISTS"):
        exporter._exclusive_write(path, b"replacement")
    assert path.read_bytes() == b"original"


def test_new_candidate_dir_rejects_outside_and_deliverables(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "model.pth"
    source.write_bytes(b"synthetic")
    deliverables = repo / "deliverables"
    deliverables.mkdir()
    with pytest.raises(exporter.CandidateExportError, match="CANDIDATE_ROOT_INVALID"):
        exporter._new_candidate_dir(tmp_path / "outside", repo_root=repo, source_path=source, deliverables_path=deliverables)
    with pytest.raises(exporter.CandidateExportError, match="CANDIDATE_ROOT_OVERLAP"):
        exporter._new_candidate_dir(deliverables / "candidate", repo_root=repo, source_path=source, deliverables_path=deliverables)


def test_new_candidate_dir_rejects_reparse_component(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "model.pth"
    source.write_bytes(b"synthetic")
    reparse = repo / "synthetic-reparse"
    reparse.mkdir()
    original_lstat = type(reparse).lstat

    def controlled_lstat(path):
        if path == reparse:
            actual = original_lstat(path)
            values = {name: getattr(actual, name) for name in dir(actual) if name.startswith("st_")}
            values["st_file_attributes"] = 0x400
            return SimpleNamespace(**values)
        return original_lstat(path)

    monkeypatch.setattr(type(reparse), "lstat", controlled_lstat)
    with pytest.raises(exporter.CandidateExportError, match="CANDIDATE_ROOT_REPARSE_POINT"):
        exporter._new_candidate_dir(reparse / "candidate", repo_root=repo, source_path=source)


def test_new_candidate_dir_rejects_repeated_existing_name(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "model.pth"
    source.write_bytes(b"synthetic")
    parent = repo / "candidate-root"
    parent.mkdir()
    stamp = "20261016T000000Z"

    class FrozenDateTime:
        @staticmethod
        def now(_timezone):
            return datetime(2026, 10, 16, tzinfo=timezone.utc)

    monkeypatch.setattr(exporter, "datetime", FrozenDateTime)
    monkeypatch.setattr(exporter.secrets, "token_hex", lambda _: "a" * 16)
    (parent / f"{stamp}-{'a' * 16}").mkdir()
    with pytest.raises(exporter.CandidateExportError, match="CANDIDATE_DIRECTORY_COLLISION"):
        exporter._new_candidate_dir(parent, repo_root=repo, source_path=source)


def test_real_cpu_safetensors_roundtrip_scalar_and_int64() -> None:
    import torch
    from safetensors.torch import load, save

    source = {
        "scalar": torch.tensor(1.25, dtype=torch.float32, device="cpu"),
        "num_batches_tracked": torch.tensor(7, dtype=torch.int64, device="cpu"),
    }
    validated = exporter._validated_state(source, torch)
    encoded = exporter._safetensors_roundtrip(validated, torch, save, load)
    restored = load(encoded)
    assert torch.equal(restored["scalar"], source["scalar"])
    assert torch.equal(restored["num_batches_tracked"], source["num_batches_tracked"])
    assert restored["scalar"].shape == source["scalar"].shape == torch.Size([])
    assert restored["num_batches_tracked"].dtype == torch.int64

    def wrong_dtype_load(data):
        result = load(data)
        result["scalar"] = result["scalar"].to(torch.float64)
        return result

    with pytest.raises(exporter.CandidateExportError, match="SAFETENSORS_ROUNDTRIP_MISMATCH"):
        exporter._safetensors_roundtrip(validated, torch, save, wrong_dtype_load)
