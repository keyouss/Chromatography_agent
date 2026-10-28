from pathlib import Path

import pytest

from chrom_platform.modules.assistant import secret_store


def _layout(tmp_path):
    local_app_data = tmp_path / "LocalAppData"
    platform_root = local_app_data / "ChromatographyPlatform"
    runtime_root = platform_root / "assistant-accept-20261006"
    secret_root = runtime_root / "assistant-llm-secrets"
    secret_root.mkdir(parents=True)
    return local_app_data, platform_root, runtime_root, secret_root


def test_secret_root_accepts_only_same_approved_runtime_canonical_mapping(tmp_path, monkeypatch):
    local_app_data, _platform_root, runtime_root, secret_root = _layout(tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.delenv("CHROM_ASSISTANT_RUNTIME_ROOT", raising=False)
    assert secret_store._resolve_secret_root(secret_root) == secret_root.absolute()

    monkeypatch.setenv("CHROM_ASSISTANT_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("CHROM_ASSISTANT_SECRET_ROOT", str(secret_root))

    cached_runtime = tmp_path / "Packages" / "StoreCache" / "assistant-accept-20261006"
    cached_runtime.mkdir(parents=True)
    cached_secret_root = cached_runtime / "assistant-llm-secrets"
    cached_secret_root.mkdir()
    original_resolve = Path.resolve

    def resolve_with_runtime_alias(path, *, strict=False):
        path = Path(path)
        if path == runtime_root:
            return cached_runtime
        if path == secret_root:
            return cached_secret_root
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve_with_runtime_alias)

    assert secret_store._resolve_secret_root(secret_root) == secret_root.absolute()


def test_secret_root_rejects_unbound_or_outside_runtime(tmp_path, monkeypatch):
    local_app_data, platform_root, approved_runtime, approved_secret = _layout(tmp_path)
    other_runtime = platform_root / "priority-local"
    other_secret = other_runtime / "assistant-llm-secrets"
    other_secret.mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setenv("CHROM_ASSISTANT_RUNTIME_ROOT", str(approved_runtime))

    with pytest.raises(secret_store.SecretStoreError):
        secret_store._resolve_secret_root(other_secret)

    outside_runtime = tmp_path / "outside-runtime"
    outside_runtime.mkdir()
    monkeypatch.setenv("CHROM_ASSISTANT_RUNTIME_ROOT", str(outside_runtime))
    with pytest.raises(secret_store.SecretStoreError):
        secret_store._resolve_secret_root(approved_secret)

    monkeypatch.setenv("CHROM_ASSISTANT_RUNTIME_ROOT", str(approved_runtime))
    original_resolve = Path.resolve

    def resolve_outside_approved_runtime(path, *, strict=False):
        if Path(path) == approved_secret:
            return outside_runtime / "assistant-llm-secrets"
        return original_resolve(Path(path), strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve_outside_approved_runtime)
    with pytest.raises(secret_store.SecretStoreError):
        secret_store._resolve_secret_root(approved_secret)


def test_secret_root_rejects_reparse_runtime_component(tmp_path, monkeypatch):
    local_app_data, _platform_root, runtime_root, secret_root = _layout(tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setenv("CHROM_ASSISTANT_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setattr(secret_store, "_is_reparse", lambda path: Path(path) == runtime_root)

    with pytest.raises(secret_store.SecretStoreError, match="reparse point"):
        secret_store._resolve_secret_root(secret_root)
