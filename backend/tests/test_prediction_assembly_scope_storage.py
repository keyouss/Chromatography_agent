from pathlib import Path

import pytest

from chrom_platform.assembly.prediction import _sfc_scope_storage_path


def test_sfc_scope_storage_requires_separate_absolute_writable_path(tmp_path, monkeypatch) -> None:
    candidate = tmp_path / "assets" / "candidates.sqlite3"
    model_root = tmp_path / "models"
    registry = tmp_path / "registry.json"
    run_root = tmp_path / "runtime"
    for path in (candidate.parent, model_root, run_root):
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("CHROM_SFC_SCOPE_STORAGE_PATH", str(run_root / "sfc-scopes.sqlite3"))
    assert _sfc_scope_storage_path(
        candidate_db=str(candidate), model_root=str(model_root), registry_path=str(registry)
    ) == (run_root / "sfc-scopes.sqlite3").resolve()

    monkeypatch.delenv("CHROM_SFC_SCOPE_STORAGE_PATH")
    with pytest.raises(ValueError, match="SFC_SCOPE_STORAGE_PATH_REQUIRED"):
        _sfc_scope_storage_path(candidate_db=str(candidate), model_root=str(model_root), registry_path=str(registry))

    monkeypatch.setenv("CHROM_SFC_SCOPE_STORAGE_PATH", str(candidate) + ".scopes.sqlite3")
    with pytest.raises(ValueError, match="SFC_SCOPE_STORAGE_PATH_MUST_BE_SEPARATE"):
        _sfc_scope_storage_path(candidate_db=str(candidate), model_root=str(model_root), registry_path=str(registry))


@pytest.mark.parametrize("asset_dir", ["models", "Services", "deliverables"])
def test_sfc_scope_storage_rejects_asset_trees(tmp_path, monkeypatch, asset_dir: str) -> None:
    root = tmp_path / asset_dir
    root.mkdir(parents=True)
    candidate = tmp_path / "candidate.sqlite3"
    monkeypatch.setenv("CHROM_SFC_SCOPE_STORAGE_PATH", str(root / "scopes.sqlite3"))
    with pytest.raises(ValueError):
        _sfc_scope_storage_path(candidate_db=str(candidate), model_root=str(tmp_path / "models"), registry_path=str(tmp_path / "registry.json"))
