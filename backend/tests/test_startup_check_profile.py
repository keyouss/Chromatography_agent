from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile

import pytest


CHECKER = Path(__file__).parents[2] / "deploy" / "v1" / "startup-check.py"
SPEC = importlib.util.spec_from_file_location("startup_check_profile", CHECKER)
assert SPEC is not None and SPEC.loader is not None
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class FakeRedis:
    def __init__(self):
        self.read_keys: list[str] = []
        self.dead_letter_history = {"dramatiq:chrom-platform-data-accept-20261026.XQ"}

    def type(self, key: str) -> bytes:
        self.read_keys.append(key)
        if key.endswith("data-accept-20261026"):
            return b"list"
        if key.endswith(".DQ"):
            return b"zset"
        if key in self.dead_letter_history:
            return b"zset"
        return b"none"

    def llen(self, key: str) -> int:
        return 2

    def zcard(self, key: str) -> int:
        return 3 if key.endswith(".DQ") else 4

    def scan_iter(self, *, match: str, count: int):
        del count
        return ["dramatiq:__acks__:1." + match.removeprefix("dramatiq:__acks__.*.")]

    def scard(self, key: str) -> int:
        return 1


def test_fixed_profile_bindings_never_use_caller_path() -> None:
    base = Path("C:/protected")
    assistant = CHECK.profile_binding("assistant-20261006", base)
    data = CHECK.profile_binding("data-20261026", base)
    assert assistant["runtime_file"] == str(base / "ChromatographyPlatform" / "assistant-accept-20261006" / "runtime.json")
    assert assistant["schema"] == "chrom_assistant_accept_20261006"
    assert assistant["queue"] == "chrom-platform-assistant-accept-20261006"
    assert data["runtime_file"] == str(base / "ChromatographyPlatform" / "data-accept-20261026" / "runtime.json")
    assert data["schema"] == "chrom_data_accept_20261026"
    assert data["queue"] == "chrom-platform-data-accept-20261026"
    with pytest.raises(ValueError):
        CHECK.profile_binding("C:/caller-supplied", base)


def test_data_worker_health_uses_only_the_fixed_physical_package() -> None:
    expected = Path(
        r"D:\Jupyter\Chromatography_agent\testing\runs\2026-10-27-data-import-audit"
        r"\backend-runtime-package-data-window-08\data-center-invalid-window-20261026"
    )
    assert CHECK.worker_package_path("data-20261026") == expected
    assert CHECK.worker_package_path("assistant-20261006") == Path(CHECKER).resolve().parents[2] / "backend/src"
    with pytest.raises(ValueError):
        CHECK.worker_package_path("backend/src")


def test_only_data_worker_import_disables_python_bytecode(monkeypatch) -> None:
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    CHECK.prepare_worker_package_import("assistant-20261006")
    assert sys.dont_write_bytecode is False
    CHECK.prepare_worker_package_import("data-20261026")
    assert sys.dont_write_bytecode is True
    with pytest.raises(ValueError):
        CHECK.prepare_worker_package_import("other-profile")


def test_unknown_scan_completion_blocks_idle_from_fixed_package(monkeypatch) -> None:
    from types import SimpleNamespace

    expected = CHECK.worker_package_path("data-20261026") / "chrom_platform/artifact_core/dataset_csv_elevated_runner.py"
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    module = SimpleNamespace(__file__=str(expected), ledger_processes_idle=lambda: False)
    monkeypatch.setattr(CHECK.importlib, "import_module", lambda name: module)
    assert CHECK.dataset_scan_ledger_idle() is False
    module.ledger_processes_idle = lambda: True
    assert CHECK.dataset_scan_ledger_idle() is True


def test_scan_completion_checker_cannot_fall_back_to_checkout(monkeypatch) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    module = SimpleNamespace(__file__=str(Path(CHECKER).resolve()), ledger_processes_idle=lambda: True)
    monkeypatch.setattr(CHECK.importlib, "import_module", lambda name: module)
    with pytest.raises(ValueError, match="origin mismatch"):
        CHECK.dataset_scan_ledger_idle()


def test_data_idle_queue_counts_runnable_delay_and_inflight_but_ignores_dead_letter_history() -> None:
    client = FakeRedis()
    history_before = set(client.dead_letter_history)
    pending, inflight = CHECK.queue_counts(
        client,
        "chrom-platform-data-accept-20261026",
        include_assistant=False,
    )
    assert (pending, inflight) == (5, 2)
    assert client.read_keys == [
        "dramatiq:chrom-platform-data-accept-20261026",
        "dramatiq:chrom-platform-data-accept-20261026.DQ",
    ]
    # Dramatiq's .XQ stores dead-letter history; it must not block idle or be deleted.
    assert "dramatiq:chrom-platform-data-accept-20261026.XQ" not in client.read_keys
    assert client.dead_letter_history == history_before
    assert not any("-assistant" in key for key in client.read_keys)


def test_data_database_url_is_read_directly_without_formal_runtime() -> None:
    from sqlalchemy.engine import make_url

    username = "chrom_data_accept_runtime"
    direct = f"mysql+pymysql://{username}:protected@127.0.0.1:3306/chrom_data_accept_20261026"
    worker = f"mysql+asyncmy://{username}:protected@127.0.0.1:3306/chrom_data_accept_20261026"
    url = CHECK._database_url(
        "data-20261026",
        {"DATABASE_URL": worker, "SYNC_DATABASE_URL": direct},
        Path("Z:/missing-formal-runtime-must-not-be-read"),
    )
    assert url == make_url(direct)
    assert url.username == username
    assert url.database == "chrom_data_accept_20261026"
    with pytest.raises(ValueError):
        CHECK._database_url(
            "data-20261026",
            {"DATABASE_URL": worker.replace("chrom_data_accept_20261026", "chrom_platform"), "SYNC_DATABASE_URL": direct},
            Path("Z:/missing-formal-runtime-must-not-be-read"),
        )
    with pytest.raises(ValueError):
        CHECK._database_url(
            "data-20261026",
            {
                "DATABASE_URL": worker.replace(username, "root"),
                "SYNC_DATABASE_URL": direct.replace(username, "root"),
            },
            Path("Z:/missing-formal-runtime-must-not-be-read"),
        )


def test_assistant_idle_uses_same_localappdata_base_for_both_protected_configs() -> None:
    with tempfile.TemporaryDirectory(dir=CHECKER.parent) as temporary:
        base = Path(temporary)
        product = base / "ChromatographyPlatform"
        formal = product / "priority-local"
        assistant = product / "assistant-accept-20261006"
        formal.mkdir(parents=True)
        assistant.mkdir()
        (formal / "runtime.json").write_text(json.dumps({
            "SYNC_DATABASE_URL": "mysql+pymysql://fixture:synthetic@127.0.0.1:3306/chrom_platform",
        }), encoding="utf-8")
        (assistant / "runtime.json").write_text(json.dumps({
            "CHROM_ACCEPTANCE_DATABASE_SCHEMA": "chrom_assistant_accept_20261006",
            "WORKER_QUEUE": "chrom-platform-assistant-accept-20261006",
        }), encoding="utf-8")
        runtime = CHECK._runtime_for("assistant-20261006", base)
        url = CHECK._database_url("assistant-20261006", runtime, base)
        assert url.database == "chrom_assistant_accept_20261006"
        assert url.username == "fixture"
        assert not (base / "priority-local").exists()


def test_runtime_profile_schema_and_queue_are_fail_closed() -> None:
    with tempfile.TemporaryDirectory(dir=CHECKER.parent) as temporary:
        base = Path(temporary)
        root = base / "ChromatographyPlatform" / "data-accept-20261026"
        root.mkdir(parents=True)
        runtime = {
            "CHROM_ACCEPTANCE_DATABASE_SCHEMA": "chrom_data_accept_20261026",
            "WORKER_QUEUE": "chrom-platform-data-accept-20261026",
        }
        (root / "runtime.json").write_text(json.dumps(runtime), encoding="utf-8")
        assert CHECK._runtime_for("data-20261026", base) == runtime
        runtime["WORKER_QUEUE"] = "chrom-platform-assistant-accept-20261006"
        (root / "runtime.json").write_text(json.dumps(runtime), encoding="utf-8")
        with pytest.raises(ValueError):
            CHECK._runtime_for("data-20261026", base)
