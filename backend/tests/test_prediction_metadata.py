import json
import os
import sys
from pathlib import Path

import pytest

from chrom_platform.assembly import prediction_metadata as metadata


@pytest.fixture
def metadata_runtime(tmp_path, monkeypatch):
    local_app_data = tmp_path / "localappdata"
    runtime_root = local_app_data / "ChromatographyPlatform" / "runtime"
    runtime_root.mkdir(parents=True)
    ready_path = runtime_root / "state" / "worker.ready"
    ready_path.parent.mkdir()
    manifest = tmp_path / "build-manifest.json"
    manifest.write_text(json.dumps({"build_id": "universal-v1", "modules": ["prediction"]}), encoding="utf-8")
    for key in metadata._CONFIG_KEYS | metadata._ASSET_PATH_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CHROM_ASSISTANT_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setenv("WORKER_READY_FILE", str(ready_path))
    monkeypatch.setenv("CHROM_BUILD_MANIFEST", str(manifest))
    monkeypatch.setenv("BUILD_PROFILE", "universal-v1")
    monkeypatch.setattr(metadata, "_package_source_fingerprint", lambda: "isolated-fixture-source")
    if sys.platform.startswith("linux"):
        monkeypatch.setattr(metadata, "_process_cmdline", lambda _pid: "python -m chrom_platform.worker_cli")
    return runtime_root, ready_path, manifest


def _write_live_ready(path: Path, identity: dict[str, object]) -> None:
    path.write_text(json.dumps({"ready": True, **identity}), encoding="utf-8")


def test_metadata_accepts_logical_and_resolved_ready_aliases_but_not_outside(metadata_runtime, tmp_path, monkeypatch):
    logical_root, logical_ready, _ = metadata_runtime
    physical_root = tmp_path / "physical/runtime"
    physical_ready = physical_root / "state/worker.ready"
    physical_ready.parent.mkdir(parents=True)
    original_resolve = Path.resolve

    def resolve_alias(path, *args, **kwargs):
        if path == logical_root:
            return physical_root
        if path == logical_ready:
            return physical_ready
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_alias)
    expected = physical_ready.with_name(physical_ready.name + ".prediction-metadata.json")
    assert metadata.prediction_metadata_path(logical_ready) == expected
    assert metadata.prediction_metadata_path(physical_ready) == expected
    with pytest.raises(ValueError, match="OUTSIDE_RUNTIME"):
        metadata.prediction_metadata_path(tmp_path / "outside/worker.ready")


@pytest.mark.parametrize("kind", ["gc", "sfc"])
def test_fixed_startup_reference_invalidates_metadata_without_env_alias(metadata_runtime, tmp_path, monkeypatch, kind):
    _, ready_path, manifest = metadata_runtime
    source = tmp_path / "project/backend/src/chrom_platform/assembly/prediction_metadata.py"
    monkeypatch.setattr(metadata, "__file__", str(source))
    reference = metadata._startup_reference_paths()[kind]
    reference.parent.mkdir(parents=True)
    reference.write_text('{"fixture":1}', encoding="utf-8")
    manifest.write_text(json.dumps({"build_id": "universal-v1", "modules": ["prediction"], "chromatography_types": [kind]}), encoding="utf-8")
    identity = {"pid": os.getpid(), "instance_id": "worker-test-12345678", "boot_id": metadata._boot_id(), "process_starttime": metadata.current_process_starttime(os.getpid())}
    metadata.publish_prediction_metadata({"capabilities": [], "bindings": []}, worker_identity=identity)
    _write_live_ready(ready_path, identity)
    assert metadata.read_prediction_metadata() is not None
    reference.write_text('{"fixture":2}', encoding="utf-8")
    assert metadata.read_prediction_metadata() is None


def test_prediction_metadata_roundtrip_is_bound_to_ready_identity_and_config(metadata_runtime):
    _, ready_path, _ = metadata_runtime
    identity = {
        "pid": os.getpid(),
        "instance_id": "worker-test-12345678",
        "boot_id": metadata._boot_id(),
        "process_starttime": metadata.current_process_starttime(os.getpid()),
    }
    assert identity["process_starttime"]
    published = metadata.publish_prediction_metadata(
        {"capabilities": [{"id": "tlc"}], "bindings": []},
        worker_identity=identity,
    )
    _write_live_ready(ready_path, identity)

    assert metadata.read_prediction_metadata() == published
    os.environ["WORKER_QUEUE"] = "other-queue"
    try:
        assert metadata.read_prediction_metadata() is None
    finally:
        os.environ.pop("WORKER_QUEUE", None)


def test_prediction_metadata_rejects_changed_build_and_malformed_ready_marker(metadata_runtime):
    _, ready_path, manifest = metadata_runtime
    identity = {
        "pid": os.getpid(),
        "instance_id": "worker-test-12345678",
        "boot_id": metadata._boot_id(),
        "process_starttime": metadata.current_process_starttime(os.getpid()),
    }
    metadata.publish_prediction_metadata({"capabilities": [], "bindings": []}, worker_identity=identity)
    _write_live_ready(ready_path, identity)
    assert metadata.read_prediction_metadata() is not None

    manifest.write_text(json.dumps({"build_id": "cc-v1", "modules": ["prediction"]}), encoding="utf-8")
    assert metadata.read_prediction_metadata() is None
    manifest.write_text(json.dumps({"build_id": "universal-v1", "modules": ["prediction"]}), encoding="utf-8")
    ready = json.loads(ready_path.read_text(encoding="utf-8"))
    ready["unexpected"] = True
    ready_path.write_text(json.dumps(ready), encoding="utf-8")
    assert metadata.read_prediction_metadata() is None


def test_prediction_metadata_rejects_runtime_escape_and_removes_only_matching_identity(metadata_runtime, tmp_path):
    _, ready_path, _ = metadata_runtime
    with pytest.raises(ValueError, match="OUTSIDE_RUNTIME"):
        metadata.prediction_metadata_path(tmp_path / "outside.ready")

    identity = {
        "pid": os.getpid(),
        "instance_id": "worker-test-12345678",
        "boot_id": metadata._boot_id(),
        "process_starttime": metadata.current_process_starttime(os.getpid()),
    }
    target = metadata.prediction_metadata_path()
    published = metadata.publish_prediction_metadata({"capabilities": [], "bindings": []}, worker_identity=identity)
    metadata.remove_prediction_metadata(worker_identity={**identity, "instance_id": "another-worker"})
    assert target.is_file()
    metadata.remove_prediction_metadata(worker_identity=identity)
    assert not target.exists()
    assert published["registry"]["capabilities"] == []


def test_ready_file_does_not_publish_snapshot_without_lock(metadata_runtime, monkeypatch):
    from chrom_platform.worker_cli import ReadyFile

    _, ready_path, _ = metadata_runtime
    callback_calls = []
    ready = ReadyFile(ready_path, instance_id="worker-test-12345678")
    monkeypatch.setattr(ready, "_acquire_lock", lambda: (False, False))

    ready.mark_ready(before_publish=lambda _identity: callback_calls.append(True))

    assert ready.startup_conflict is True
    assert callback_calls == []
    assert not ready_path.exists()


def test_ready_file_publishes_metadata_under_lock_before_ready(metadata_runtime, monkeypatch):
    from chrom_platform.worker_cli import ReadyFile

    _, ready_path, _ = metadata_runtime
    identity_seen = []
    ready = ReadyFile(ready_path, instance_id="worker-test-12345678")

    def publish(identity):
        assert not ready_path.exists()
        metadata.publish_prediction_metadata(
            {"capabilities": [], "bindings": []},
            ready_path=ready_path,
            worker_identity=identity,
        )
        identity_seen.append(identity)

    ready.mark_ready(before_publish=publish)
    try:
        assert ready.created
        assert len(identity_seen) == 1
        assert metadata.prediction_metadata_path().is_file()
        if sys.platform.startswith("linux"):
            monkeypatch.setattr(metadata, "_process_cmdline", lambda _pid: "python -m chrom_platform.worker_cli")
        assert metadata.read_prediction_metadata() is not None
    finally:
        metadata.remove_prediction_metadata(worker_identity=identity_seen[0])
        ready.remove()


@pytest.mark.parametrize("field,value", [("instance_id", None), ("instance_id", ""), ("process_starttime", None)])
def test_metadata_rejects_incomplete_worker_identity(metadata_runtime, field, value):
    _, ready_path, _ = metadata_runtime
    identity = {"pid": os.getpid(), "instance_id": "worker-test-12345678",
                "boot_id": metadata._boot_id(), "process_starttime": metadata.current_process_starttime(os.getpid())}
    metadata.publish_prediction_metadata({"capabilities": [], "bindings": []}, worker_identity=identity)
    _write_live_ready(ready_path, {**identity, field: value})
    assert metadata.read_prediction_metadata() is None


def test_metadata_rejects_asset_change_and_worker_change_during_fingerprint(metadata_runtime, tmp_path, monkeypatch):
    _, ready_path, manifest = metadata_runtime
    assets = tmp_path / "cc-assets"
    assets.mkdir()
    weight = assets / "synthetic-weight.bin"
    weight.write_bytes(b"fixture-one")
    manifest.write_text(json.dumps({"build_id": "universal-v1", "modules": ["prediction"],
                                    "chromatography_types": ["cc"]}), encoding="utf-8")
    monkeypatch.setenv("CHROM_CC_MODEL_ROOT", str(assets))
    identity = {"pid": os.getpid(), "instance_id": "worker-test-12345678",
                "boot_id": metadata._boot_id(), "process_starttime": metadata.current_process_starttime(os.getpid())}
    metadata.publish_prediction_metadata({"capabilities": [], "bindings": []}, worker_identity=identity)
    _write_live_ready(ready_path, identity)
    assert metadata.read_prediction_metadata() is not None
    weight.write_bytes(b"changed-fixture-content")
    assert metadata.read_prediction_metadata() is None
    metadata.publish_prediction_metadata({"capabilities": [], "bindings": []}, worker_identity=identity)
    original = metadata.current_prediction_metadata_expectations

    def worker_changes_during_hash():
        expected = original()
        _write_live_ready(ready_path, {**identity, "instance_id": "worker-replacement"})
        return expected

    monkeypatch.setattr(metadata, "current_prediction_metadata_expectations", worker_changes_during_hash)
    assert metadata.read_prediction_metadata() is None


def test_digest_cache_rehashes_same_size_inode_and_restored_mtime(tmp_path):
    import hashlib

    path = tmp_path / "synthetic-weight.bin"
    path.write_bytes(b"original")
    before = path.stat()
    first = metadata._sha256_file(path)
    path.write_bytes(b"modified")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = path.stat()
    assert (after.st_size, after.st_ino, after.st_mtime_ns) == (before.st_size, before.st_ino, before.st_mtime_ns)
    second = metadata._sha256_file(path)
    assert second == hashlib.sha256(b"modified").hexdigest() and second != first


def test_digest_cache_rehashes_atomic_file_replacement(tmp_path):
    import hashlib

    path = tmp_path / "asset.bin"
    path.write_bytes(b"original")
    old = path.stat()
    first = metadata._sha256_file(path)
    replacement = tmp_path / "replacement.bin"
    replacement.write_bytes(b"modified")
    os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns))
    os.replace(replacement, path)
    assert metadata._sha256_file(path) == hashlib.sha256(b"modified").hexdigest() != first


def test_metadata_rejects_unavailable_change_token(metadata_runtime, monkeypatch):
    _, ready_path, _ = metadata_runtime
    identity = {"pid": os.getpid(), "instance_id": "worker-test-12345678",
                "boot_id": metadata._boot_id(), "process_starttime": metadata.current_process_starttime(os.getpid())}
    metadata.publish_prediction_metadata({"capabilities": [], "bindings": []}, worker_identity=identity)
    _write_live_ready(ready_path, identity)

    def unavailable(_path, _info):
        raise OSError("change token unavailable")

    monkeypatch.setattr(metadata, "_file_change_time", unavailable)
    assert metadata.read_prediction_metadata() is None


def test_digest_is_not_cached_when_file_changes_during_hash(tmp_path, monkeypatch):
    path = tmp_path / "racing-asset.bin"
    path.write_bytes(b"fixture")
    calls = iter([(7, 1, 1, 1), (7, 1, 1, 2)])
    monkeypatch.setattr(metadata, "_file_signature", lambda _: next(calls))
    with pytest.raises(OSError, match="CHANGED_DURING_HASH"):
        metadata._sha256_file(path)
    assert str(path) not in metadata._HASH_CACHE
