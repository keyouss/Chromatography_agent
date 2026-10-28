from __future__ import annotations

import contextlib
import base64
import ast
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import types
from typing import Any

import pytest


SCRIPT = Path(__file__).with_name("migrate-assistant-acceptance.ps1")
PARENT = "0020_assistant_llm_configuration_lifecycle"
TARGET = "0021_assistant_framework_state"
TABLE = "assistant_framework_state"


def _embedded(name: str) -> str:
    source = SCRIPT.read_text(encoding="utf-8")
    match = re.search(rf"\${name}\s*=\s*@'\r?\n(.*?)\r?\n'@", source, re.S)
    assert match, f"missing embedded {name} Python"
    return match.group(1)


def _migration_config_policy() -> str:
    source = SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"\$migrationConfigPolicy\s*=\s*@'\r?\n(.*?)\r?\n'@", source, re.S)
    assert match, "missing migration config policy"
    return match.group(1)


def _run_powershell(script: str) -> subprocess.CompletedProcess[str]:
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, check=False,
    )


def _ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class _Result:
    def __init__(self, value: Any) -> None:
        self.value = value

    def one(self) -> Any:
        return self.value

    def all(self) -> Any:
        return self.value

    def scalars(self) -> "_Result":
        return self

    def scalar_one(self) -> Any:
        return self.value


class _Database:
    def __init__(self, *, schema: str = "chrom_assistant_accept_20261006",
                 versions: list[str] | None = None, tables: list[str] | None = None,
                 heads: list[str] | None = None, revision_parent: str = PARENT) -> None:
        self.schema = schema
        self.versions = list(versions if versions is not None else [PARENT])
        self.tables = list(tables if tables is not None else ["alembic_version", "agent_run", "project", "conversation", "message"])
        self.heads = list(heads if heads is not None else [TARGET])
        self.revision_parent = revision_parent
        self.upgrade_targets: list[str] = []
        self.engine_urls: list[str] = []
        self.upgrade_error: BaseException | None = None
        self.query_errors: dict[str, BaseException] = {}
        self.required_columns = {
            "agent_run_id", "project_id", "conversation_id", "engine", "framework_version",
            "serialization_version", "config_hash", "state", "stream_message_id", "created_at", "updated_at",
        }
        self.required_constraints = [
            ("PRIMARY", "PRIMARY KEY"),
            ("fk_assistant_framework_state_agent_run_id_agent_run", "FOREIGN KEY"),
            ("fk_assistant_framework_state_project_id_project", "FOREIGN KEY"),
            ("fk_assistant_framework_state_conversation_id_conversation", "FOREIGN KEY"),
            ("fk_assistant_framework_state_stream_message_id_message", "FOREIGN KEY"),
            ("ck_assistant_framework_state_ck_assistant_framework_state_engine", "CHECK"),
            ("ck_assistant_framework_state_ck_assistant_framework_stat_8deb", "CHECK"),
        ]

    def execute(self, statement: str, _params: Any = None) -> _Result:
        for marker, error in list(self.query_errors.items()):
            if marker in statement:
                del self.query_errors[marker]
                raise error
        if "CURRENT_USER()" in statement:
            return _Result(("acceptance@localhost", self.schema, "8.4.0"))
        if "SELECT TABLE_NAME FROM information_schema.tables" in statement:
            return _Result(sorted(self.tables))
        if "SELECT version_num FROM alembic_version" in statement:
            return _Result(sorted(self.versions))
        if "SELECT COLUMN_NAME FROM information_schema.columns" in statement and TABLE in statement:
            return _Result(sorted(self.required_columns))
        if "SELECT CONSTRAINT_NAME, CONSTRAINT_TYPE FROM information_schema.table_constraints" in statement:
            return _Result(list(self.required_constraints))
        raise AssertionError(f"Unexpected offline SQL: {statement}")


class _Connection:
    def __init__(self, database: _Database) -> None:
        self.database = database

    def __enter__(self) -> "_Connection":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, statement: str, params: Any = None) -> _Result:
        return self.database.execute(statement, params)


class _Engine:
    def __init__(self, database: _Database) -> None:
        self.database = database

    def connect(self) -> _Connection:
        return _Connection(self.database)

    def dispose(self) -> None:
        return None


def _install_fakes(monkeypatch: pytest.MonkeyPatch, database: _Database) -> None:
    sqlalchemy = types.ModuleType("sqlalchemy")

    def create_engine(url: str, **_kwargs: Any) -> _Engine:
        database.engine_urls.append(url)
        return _Engine(database)

    sqlalchemy.create_engine = create_engine  # type: ignore[attr-defined]
    sqlalchemy.text = lambda statement: statement  # type: ignore[attr-defined]
    alembic = types.ModuleType("alembic")
    alembic_config = types.ModuleType("alembic.config")
    alembic_script = types.ModuleType("alembic.script")
    command = types.ModuleType("alembic.command")

    class Config:
        def __init__(self, path: str) -> None:
            self.path = path

    class ScriptDirectory:
        @classmethod
        def from_config(cls, _config: Config) -> "ScriptDirectory":
            return cls()

        def get_heads(self) -> list[str]:
            return list(database.heads)

        def get_revision(self, revision: str) -> Any:
            if revision != TARGET:
                return None
            return types.SimpleNamespace(down_revision=database.revision_parent)

    def upgrade(_config: Config, target: str) -> None:
        database.upgrade_targets.append(target)
        if database.upgrade_error is not None:
            raise database.upgrade_error
        database.versions = [TARGET if target == TARGET else "0099_future_head"]
        if TABLE not in database.tables:
            database.tables.append(TABLE)

    command.upgrade = upgrade  # type: ignore[attr-defined]
    alembic.command = command  # type: ignore[attr-defined]
    alembic_config.Config = Config  # type: ignore[attr-defined]
    alembic_script.ScriptDirectory = ScriptDirectory  # type: ignore[attr-defined]
    for name, module in {
        "sqlalchemy": sqlalchemy,
        "alembic": alembic,
        "alembic.command": command,
        "alembic.config": alembic_config,
        "alembic.script": alembic_script,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)


def _exec_preflight(monkeypatch: pytest.MonkeyPatch, database: _Database, *, mode: str = "1") -> str:
    _install_fakes(monkeypatch, database)
    monkeypatch.setenv("SYNC_DATABASE_URL", "mysql+pymysql://offline:test@127.0.0.1:3306/fake")
    monkeypatch.setenv("CHROM_MIGRATION_FRAMEWORK_STATE_ONLY", mode)
    monkeypatch.setenv("CHROM_MIGRATION_BACKEND_DIR", str(SCRIPT.parents[2] / "backend"))
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(compile(_embedded("preflight"), "embedded-preflight.py", "exec"), {"__name__": "__main__"})
    return output.getvalue()


def test_framework_state_preflight_accepts_only_0020_parent_and_absent_table(monkeypatch: pytest.MonkeyPatch) -> None:
    database = _Database()
    output = _exec_preflight(monkeypatch, database)
    assert "baseline=framework_state_parent" in output
    assert database.engine_urls == ["mysql+pymysql://offline:test@127.0.0.1:3306/fake"]


@pytest.mark.parametrize(
    "database",
    [
        _Database(schema="wrong_schema"),
        _Database(versions=["0019_batch_export_status_repair"]),
        _Database(versions=[PARENT, "0020_other_head"]),
        _Database(tables=["alembic_version", "assistant_framework_state"]),
        _Database(heads=[TARGET, "0020_other_head"]),
        _Database(revision_parent="0019_batch_export_status_repair"),
    ],
    ids=["schema", "wrong-from", "multiple-from", "table-exists", "multiple-heads", "wrong-parent"],
)
def test_framework_state_preflight_rejects_non_exact_baselines(
    monkeypatch: pytest.MonkeyPatch, database: _Database,
) -> None:
    with pytest.raises(SystemExit) as error:
        _exec_preflight(monkeypatch, database)
    assert error.value.code == 32
    assert database.upgrade_targets == []


@pytest.mark.parametrize(
    "selected_url",
    [
        "mysql+pymysql://offline:p%40ss%3Aword@127.0.0.1:3306/chrom_assistant_accept_20261006",
        "mysql+pymysql://offline:fake@127.0.0.1:3306/chrom_assistant_accept_20261006?charset=utf8mb4",
    ],
    ids=["encoded-password-no-query", "charset-only-query"],
)
def test_dedicated_migration_config_selects_only_sync_url_without_touching_app_files(
    tmp_path: Path, selected_url: str,
) -> None:
    source = tmp_path / "migration-only.json"
    formal = tmp_path / "priority-local" / "runtime.json"
    acceptance = tmp_path / "assistant-accept-20261006" / "runtime.json"
    source.write_text(json.dumps({"SYNC_DATABASE_URL": selected_url}), encoding="utf-8")
    formal.parent.mkdir()
    acceptance.parent.mkdir()
    formal.write_text("formal app config must not be read", encoding="utf-8")
    acceptance.write_text("acceptance app config must not be read", encoding="utf-8")
    source_before = source.read_bytes()
    policy = _migration_config_policy()
    script = f"""
$ErrorActionPreference = 'Stop'
function Assert-DpapiNoReparse([string]$Path, [string]$Name) {{ if ($Path -eq $script:RejectPath) {{ throw 'fake path rejection' }} }}
function Assert-DpapiProtectedAcl([string]$Path, [bool]$Directory, [string]$Name) {{ if ($Path -eq $script:RejectPath) {{ throw 'fake ACL rejection' }} }}
{policy}
Assert-MigrationConfigOption $true $true
$url = Resolve-MigrationConfigSource {_ps_literal(str(source))} @({_ps_literal(str(formal))}, {_ps_literal(str(acceptance))})
Set-MigrationDatabaseBindings $url
if ($url -cne {_ps_literal(selected_url)} -or [Environment]::GetEnvironmentVariable('DATABASE_URL', 'Process') -cne {_ps_literal(selected_url)} -or [Environment]::GetEnvironmentVariable('SYNC_DATABASE_URL', 'Process') -cne {_ps_literal(selected_url)}) {{ exit 9 }}
Write-Output 'SELECTED'
"""
    result = _run_powershell(script)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "SELECTED"
    assert source.read_bytes() == source_before
    assert formal.read_text(encoding="utf-8") == "formal app config must not be read"
    assert acceptance.read_text(encoding="utf-8") == "acceptance app config must not be read"


@pytest.mark.parametrize(
    ("framework_only", "path", "url", "expected"),
    [
        (False, "valid", "mysql+pymysql://u:p@127.0.0.1:3306/chrom_assistant_accept_20261006", "mode"),
        (True, "relative", "mysql+pymysql://u:p@127.0.0.1:3306/chrom_assistant_accept_20261006", "path"),
        (True, "valid", "mysql+asyncmy://u:p@127.0.0.1:3306/chrom_assistant_accept_20261006", "url"),
        (True, "valid", "mysql+pymysql://u:p@db.example:3306/chrom_assistant_accept_20261006", "url"),
        (True, "valid", "mysql+pymysql://u:p@127.0.0.1:3306/chrom_platform", "url"),
        (True, "valid", "mysql+pymysql://u:p@127.0.0.1:3306/CHROM_ASSISTANT_ACCEPT_20261006", "url"),
        (True, "valid", "mysql+pymysql://u:p@127.0.0.1:3306/chrom_assistant_accept_20261006?CHARSET=utf8mb4", "url"),
        (True, "valid", "mysql+pymysql://u:p@127.0.0.1:3306/chrom_assistant_accept_20261006\n", "url"),
        (True, "valid", "mysql+pymysql://u:p@127.0.0.1:3306/chrom_assistant_accept_20261006?charset=utf8mb4\n", "url"),
        (True, "valid", "mysql+pymysql://u:p@127.0.0.1:3306/chrom_assistant_accept_20261006?host=evil", "url"),
        (True, "valid", "mysql+pymysql://u:p@127.0.0.1:3306/chrom_assistant_accept_20261006?database=chrom_platform", "url"),
        (True, "valid", "mysql+pymysql://u:p@127.0.0.1:3306/chrom_assistant_accept_20261006?unix_socket=\\\\.\\pipe\\mysql", "url"),
        (True, "valid", "mysql+pymysql://u:p@127.0.0.1:3306/chrom_assistant_accept_20261006?read_default_file=C%3A%5Csecret.cnf", "url"),
        (True, "valid", "mysql+pymysql://u:p@127.0.0.1:3306/chrom_assistant_accept_20261006?charset=utf8mb4&host=evil", "url"),
        (True, "valid", "mysql+pymysql://u:p@127.0.0.1:3306/chrom_assistant_accept_20261006?charset=utf8mb4&charset=utf8mb4", "url"),
        (True, "valid", "mysql+pymysql://u:p@extra@127.0.0.1:3306/chrom_assistant_accept_20261006", "url"),
    ],
    ids=[
        "not-framework-state-only", "relative-path", "wrong-driver", "wrong-host", "wrong-schema",
        "uppercase-schema", "uppercase-charset",
        "trailing-lf-schema", "trailing-lf-query",
        "query-host", "query-database", "query-unix-socket", "query-read-default-file",
        "query-combination", "query-duplicate", "raw-extra-at",
    ],
)
def test_dedicated_migration_config_rejects_invalid_mode_path_and_binding(
    tmp_path: Path, framework_only: bool, path: str, url: str, expected: str,
) -> None:
    config = tmp_path / "migration-only.json"
    config.write_text(json.dumps({"SYNC_DATABASE_URL": url}), encoding="utf-8")
    supplied_path = "relative\\migration-only.json" if path == "relative" else str(config)
    policy = _migration_config_policy()
    script = f"""
$ErrorActionPreference = 'Stop'
function Assert-DpapiNoReparse([string]$Path, [string]$Name) {{ }}
function Assert-DpapiProtectedAcl([string]$Path, [bool]$Directory, [string]$Name) {{ }}
{policy}
try {{
    Assert-MigrationConfigOption ${str(framework_only).lower()} $true
    $null = Resolve-MigrationConfigSource {_ps_literal(supplied_path)} @('C:\\app\\priority-local\\runtime.json', 'C:\\app\\assistant-accept-20261006\\runtime.json')
    exit 0
}} catch {{ Write-Output 'REJECTED'; exit 5 }}
"""
    result = _run_powershell(script)
    assert result.returncode == 5, (expected, result.stdout, result.stderr)
    assert result.stdout.strip() == "REJECTED"


def test_dedicated_migration_config_rejects_app_runtime_alias_and_unprotected_parent(tmp_path: Path) -> None:
    config = tmp_path / "migration-only.json"
    config.write_text(json.dumps({"SYNC_DATABASE_URL": "mysql+pymysql://u:p@127.0.0.1:3306/chrom_assistant_accept_20261006"}), encoding="utf-8")
    policy = _migration_config_policy()
    common = f"""
$ErrorActionPreference = 'Stop'
function Assert-DpapiNoReparse([string]$Path, [string]$Name) {{ if ($Path -eq $script:RejectPath) {{ throw 'fake path rejection' }} }}
function Assert-DpapiProtectedAcl([string]$Path, [bool]$Directory, [string]$Name) {{ if ($Path -eq $script:RejectPath) {{ throw 'fake ACL rejection' }} }}
{policy}
"""
    app_alias = common + f"""
try {{ $null = Resolve-MigrationConfigSource {_ps_literal(str(config))} @({_ps_literal(str(config))}); exit 0 }}
catch {{ Write-Output 'REJECTED'; exit 5 }}
"""
    result = _run_powershell(app_alias)
    assert result.returncode == 5
    assert result.stdout.strip() == "REJECTED"

    unprotected = common + f"""
$script:RejectPath = {_ps_literal(str(tmp_path))}
try {{ $null = Resolve-MigrationConfigSource {_ps_literal(str(config))} @('C:\\app\\priority-local\\runtime.json'); exit 0 }}
catch {{ Write-Output 'REJECTED'; exit 5 }}
"""
    result = _run_powershell(unprotected)
    assert result.returncode == 5
    assert result.stdout.strip() == "REJECTED"


def test_entrypoint_finally_restores_all_five_process_environment_values() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    names_match = re.search(r"\$taskMigrationEnvNames\s*=\s*@\((.*?)\)", source, re.S)
    restore_match = re.search(
        r"\n\} finally \{\r?\n(    foreach \(\$taskMigrationEnvName in \$taskMigrationEnvNames\) \{.*?\n    \})\r?\n\}\s*$",
        source, re.S,
    )
    assert names_match and restore_match
    names = re.findall(r"'([^']+)'", names_match.group(1))
    assert names == [
        "DATABASE_URL", "SYNC_DATABASE_URL", "CHROM_MIGRATION_EVIDENCE_DIR",
        "CHROM_MIGRATION_FRAMEWORK_STATE_ONLY", "CHROM_MIGRATION_BACKEND_DIR",
    ]
    names_literal = ", ".join(_ps_literal(name) for name in names)
    restore = restore_match.group(1)
    script = f"""
$taskMigrationEnvNames = @({names_literal})
foreach ($taskMigrationEnvName in $taskMigrationEnvNames) {{ [Environment]::SetEnvironmentVariable($taskMigrationEnvName, 'before-' + $taskMigrationEnvName, 'Process') }}
$taskMigrationPreviousEnv = @{{}}
foreach ($taskMigrationEnvName in $taskMigrationEnvNames) {{ $taskMigrationPreviousEnv[$taskMigrationEnvName] = [Environment]::GetEnvironmentVariable($taskMigrationEnvName, 'Process') }}
foreach ($taskMigrationEnvName in $taskMigrationEnvNames) {{ [Environment]::SetEnvironmentVariable($taskMigrationEnvName, 'selected-' + $taskMigrationEnvName, 'Process') }}
{restore}
foreach ($taskMigrationEnvName in $taskMigrationEnvNames) {{ if ([Environment]::GetEnvironmentVariable($taskMigrationEnvName, 'Process') -cne ('before-' + $taskMigrationEnvName)) {{ exit 8 }} }}
Write-Output 'RESTORED'
"""
    result = _run_powershell(script)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "RESTORED"


def test_framework_state_runner_upgrades_fixed_revision_and_checks_postconditions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    database = _Database()
    _install_fakes(monkeypatch, database)
    monkeypatch.setenv("CHROM_MIGRATION_FRAMEWORK_STATE_ONLY", "1")
    monkeypatch.setenv("CHROM_MIGRATION_BACKEND_DIR", str(SCRIPT.parents[2] / "backend"))
    monkeypatch.setenv("CHROM_MIGRATION_EVIDENCE_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://offline:test@127.0.0.1:3306/fake")
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(compile(_embedded("runner"), "embedded-runner.py", "exec"), {"__name__": "__main__"})
    assert database.upgrade_targets == [TARGET]
    assert database.versions == [TARGET]
    assert "ALEMBIC_UPGRADE_OK" in output.getvalue()
    assert list(tmp_path.glob("alembic-migration-*.json"))


def test_real_alembic_env_offline_0020_to_0021_matches_runner_postcheck_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alembic import command
    from alembic.config import Config

    monkeypatch.syspath_prepend(str(SCRIPT.parents[2] / "backend" / "src"))
    config_path = SCRIPT.parents[2] / "backend" / "alembic" / "alembic.ini"
    config = Config(str(config_path))
    config.set_main_option(
        "sqlalchemy.url",
        "mysql+pymysql://offline:fake@127.0.0.1:3306/chrom_assistant_accept_20261006",
    )
    output = io.StringIO()
    config.output_buffer = output
    command.upgrade(config, f"{PARENT}:{TARGET}", sql=True)
    sql = output.getvalue()

    assert len(re.findall(r"(?im)^CREATE TABLE assistant_framework_state\s*\(", sql)) == 1
    assert len(re.findall(r"(?im)^UPDATE alembic_version\s+SET version_num='0021_assistant_framework_state'", sql)) == 1
    table_match = re.search(
        r"CREATE TABLE assistant_framework_state\s*\((.*?)\)\s*(?:ENGINE|CHARSET|COLLATE)\s*=",
        sql, re.S,
    )
    assert table_match, "real Alembic offline SQL did not contain the target table"
    table_sql = table_match.group(1)
    columns = set(re.findall(r"^\s{4}([a-z][a-z0-9_]*)\s+(?:VARCHAR|JSON|DATETIME)\b", table_sql, re.M))
    assert columns == {
        "agent_run_id", "project_id", "conversation_id", "engine", "framework_version",
        "serialization_version", "config_hash", "state", "stream_message_id", "created_at", "updated_at",
    }
    assert re.search(r"CONSTRAINT pk_assistant_framework_state PRIMARY KEY \(agent_run_id\)", table_sql)
    assert table_sql.count(" FOREIGN KEY(") == 4
    assert table_sql.count(" ON DELETE RESTRICT") == 4
    assert "CONSTRAINT ck_assistant_framework_state_ck_assistant_framework_state_engine CHECK (engine = 'pydantic-ai')" in table_sql
    assert "CONSTRAINT ck_assistant_framework_state_ck_assistant_framework_stat_8deb CHECK (CHAR_LENGTH(config_hash) = 64)" in table_sql
    emitted_checks = re.findall(r"CONSTRAINT\s+([A-Za-z0-9_]+)\s+CHECK", table_sql)
    assert len(emitted_checks) == 2

    expected_constraints = {
        "PRIMARY": "PRIMARY KEY",
        "fk_assistant_framework_state_agent_run_id_agent_run": "FOREIGN KEY",
        "fk_assistant_framework_state_project_id_project": "FOREIGN KEY",
        "fk_assistant_framework_state_conversation_id_conversation": "FOREIGN KEY",
        "fk_assistant_framework_state_stream_message_id_message": "FOREIGN KEY",
        "ck_assistant_framework_state_ck_assistant_framework_state_engine": "CHECK",
        "ck_assistant_framework_state_ck_assistant_framework_stat_8deb": "CHECK",
    }
    runner_tree = ast.parse(_embedded("runner"))
    runner_constraints = next(
        ast.literal_eval(node.value)
        for node in ast.walk(runner_tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "required_constraints" for target in node.targets)
    )
    assert runner_constraints == expected_constraints
    # MySQL information_schema reports the primary-key name as PRIMARY even though DDL retains its named symbol.
    actual_constraint_names = {
        ("PRIMARY" if name == "pk_assistant_framework_state" else name): kind
        for name, kind in re.findall(
            r"CONSTRAINT\s+([A-Za-z0-9_]+)\s+(PRIMARY KEY|FOREIGN KEY|CHECK)", table_sql,
        )
    }
    assert actual_constraint_names == expected_constraints


def _run_runner(monkeypatch: pytest.MonkeyPatch, database: _Database, tmp_path: Path) -> tuple[int, str]:
    _install_fakes(monkeypatch, database)
    monkeypatch.setenv("CHROM_MIGRATION_FRAMEWORK_STATE_ONLY", "1")
    monkeypatch.setenv("CHROM_MIGRATION_BACKEND_DIR", str(SCRIPT.parents[2] / "backend"))
    monkeypatch.setenv("CHROM_MIGRATION_EVIDENCE_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://offline:test@127.0.0.1:3306/fake")
    output = io.StringIO()
    exit_code = 0
    with contextlib.redirect_stdout(output):
        try:
            exec(compile(_embedded("runner"), "embedded-runner.py", "exec"), {"__name__": "__main__"})
        except SystemExit as exc:
            exit_code = int(exc.code or 0)
    return exit_code, output.getvalue()


class OperationalError(Exception):
    sqlstate = "42000"


class _WrappedOperationalError(Exception):
    def __init__(self, message: str, orig: BaseException, statement: str | None = None) -> None:
        super().__init__(message)
        self.orig = orig
        if statement is not None:
            self.statement = statement


def _receipt(tmp_path: Path) -> dict[str, Any]:
    paths = list(tmp_path.glob("alembic-migration-*.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text(encoding="utf-8"))


def test_framework_state_runner_rejects_old_simple_constraint_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    database = _Database()
    database.required_constraints = [
        ("pk_assistant_framework_state", "PRIMARY KEY"),
        ("fk_assistant_framework_state_agent_run_id_agent_run", "FOREIGN KEY"),
        ("fk_assistant_framework_state_project_id_project", "FOREIGN KEY"),
        ("fk_assistant_framework_state_conversation_id_conversation", "FOREIGN KEY"),
        ("fk_assistant_framework_state_stream_message_id_message", "FOREIGN KEY"),
        ("ck_assistant_framework_state_engine", "CHECK"),
        ("ck_assistant_framework_state_config_hash_length", "CHECK"),
    ]
    exit_code, output = _run_runner(monkeypatch, database, tmp_path)
    receipt = _receipt(tmp_path)
    assert exit_code == 3
    assert receipt["operation_category"] == "POSTCHECK_VALIDATION"
    assert receipt["failed_statement_category"] == "UNKNOWN"
    assert "ALEMBIC_UPGRADE_FAILURE" in output


def test_upgrade_failure_receipt_extracts_dbapi_code_without_sensitive_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    database = _Database()
    database.upgrade_error = _WrappedOperationalError(
        "SQL SELECT secret_column; DSN mysql://secret-user:secret-pass@db/private",
        OperationalError(1142, "table grant denied; secret schema"),
        statement=(
            "CREATE TABLE `chrom_assistant_accept_20261006`.`assistant_framework_state` "
            "(`secret` TEXT) /* DSN=mysql://secret-user:secret-pass@db/private params=private */"
        ),
    )
    exit_code, output = _run_runner(monkeypatch, database, tmp_path)

    receipt = _receipt(tmp_path)
    assert exit_code == 3
    assert receipt["phase"] == "upgrade"
    assert receipt["failed_statement_category"] == "CREATE_TABLE"
    assert receipt["target_object"] == TABLE
    assert receipt["operation_category"] == "ALEMBIC_UPGRADE"
    assert receipt["expected_target_object"] == TABLE
    assert receipt["exception_class"] == "_WrappedOperationalError"
    assert receipt["sqlstate"] == "42000"
    assert receipt["driver_errno"] == "1142"
    assert receipt["exception_chain"][0]["category"] == "OTHER"
    assert receipt["exception_chain"][1]["category"] == "DBAPI_OPERATIONAL"
    serialized = json.dumps(receipt) + output
    for sensitive in ("secret_column", "secret-user", "secret-pass", "private", "grant denied", "secret schema"):
        assert sensitive not in serialized


def test_postcheck_and_diagnostic_failures_keep_original_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    database = _Database()
    original = _WrappedOperationalError(
        "postcheck SQL contains private data", OperationalError(1213, "private postcheck context"),
        statement=(
            "SELECT COLUMN_NAME FROM information_schema.columns "
            "WHERE table_schema=DATABASE() AND table_name='assistant_framework_state'"
        ),
    )
    diagnostic = _WrappedOperationalError(
        "diagnostic query contains private data", OperationalError(1142, "private diagnostic context"),
    )
    database.query_errors = {
        "SELECT COLUMN_NAME": original,
        "SELECT COUNT(*) FROM information_schema.tables": diagnostic,
    }
    exit_code, output = _run_runner(monkeypatch, database, tmp_path)

    receipt = _receipt(tmp_path)
    assert exit_code == 3
    assert receipt["phase"] == "postcheck"
    assert receipt["failed_statement_category"] == "SELECT"
    assert receipt["target_object"] == "unknown"
    assert receipt["operation_category"] == "POSTCHECK_COLUMNS"
    assert receipt["expected_target_object"] == TABLE
    assert receipt["driver_errno"] == "1213"
    assert receipt["diagnostic_status"] == "failed"
    assert receipt["diagnostic_operation_category"] == "DIAGNOSTIC_VERSION_TABLE"
    assert receipt["diagnostic_exception"]["driver_errno"] == "1142"
    assert receipt["exception_class"] == "_WrappedOperationalError"
    serialized = json.dumps(receipt) + output
    for sensitive in ("private data", "private postcheck context", "private diagnostic context"):
        assert sensitive not in serialized


@pytest.mark.parametrize(
    ("statement", "category", "target"),
    [
        (
            "CREATE TABLE `chrom_assistant_accept_20261006`.`assistant_framework_state` "
            "(`secret` TEXT) /* DSN=mysql://sensitive:user@host/db params=secret-value */",
            "CREATE_TABLE", TABLE,
        ),
        ("SELECT GET_LOCK('assistant_framework_state', 10)", "MYSQL_LOCK", "unknown"),
        ("SELECT DATABASE()", "SELECT", "unknown"),
        ("UPDATE alembic_version SET version_num='0021_secret'", "UPDATE", "alembic_version"),
        (None, "UNKNOWN", "unknown"),
    ],
    ids=["create-table-allowlist", "get-lock", "select-database", "update-version", "missing-statement"],
)
def test_failed_statement_metadata_uses_only_safe_sql_facts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    statement: str | None, category: str, target: str,
) -> None:
    database = _Database()
    database.upgrade_error = _WrappedOperationalError(
        "message contains sensitive DSN=mysql://user:password@host/private",
        OperationalError(1064, "driver text contains sensitive parameter"),
        statement=statement,
    )
    exit_code, output = _run_runner(monkeypatch, database, tmp_path)
    receipt = _receipt(tmp_path)
    assert exit_code == 3
    assert receipt["failed_statement_category"] == category
    assert receipt["target_object"] == target
    assert receipt["operation_category"] == "ALEMBIC_UPGRADE"
    assert receipt["expected_target_object"] == TABLE
    serialized = json.dumps(receipt) + output
    for sensitive in ("mysql://", "password", "private", "secret-value", "sensitive parameter", "0021_secret"):
        assert sensitive not in serialized


def test_constraints_query_failure_records_prequery_operation_and_actual_statement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    database = _Database()
    database.query_errors = {
        "SELECT CONSTRAINT_NAME": _WrappedOperationalError(
            "constraints query failed with secret content",
            OperationalError(1142, "diagnostic private error"),
            statement=(
                "SELECT CONSTRAINT_NAME, CONSTRAINT_TYPE FROM information_schema.table_constraints "
                "WHERE table_schema=DATABASE() AND table_name='assistant_framework_state'"
            ),
        ),
    }
    exit_code, output = _run_runner(monkeypatch, database, tmp_path)
    receipt = _receipt(tmp_path)
    assert exit_code == 3
    assert receipt["phase"] == "postcheck"
    assert receipt["operation_category"] == "POSTCHECK_CONSTRAINTS"
    assert receipt["expected_target_object"] == TABLE
    assert receipt["failed_statement_category"] == "SELECT"
    assert receipt["target_object"] == "unknown"
    assert "constraints query failed" not in json.dumps(receipt) + output


def test_failure_receipt_write_error_keeps_fixed_safe_marker_and_original_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    database = _Database()
    database.upgrade_error = _WrappedOperationalError(
        "sensitive SQL and connection details", OperationalError(1045, "secret diagnostic string"),
    )
    _install_fakes(monkeypatch, database)
    monkeypatch.setenv("CHROM_MIGRATION_FRAMEWORK_STATE_ONLY", "1")
    monkeypatch.setenv("CHROM_MIGRATION_BACKEND_DIR", str(SCRIPT.parents[2] / "backend"))
    monkeypatch.setenv("CHROM_MIGRATION_EVIDENCE_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://offline:test@127.0.0.1:3306/fake")

    def fail_replace(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("sensitive receipt path and payload")

    monkeypatch.setattr(os, "replace", fail_replace)
    output = io.StringIO()
    with contextlib.redirect_stdout(output), pytest.raises(SystemExit) as error:
        exec(compile(_embedded("runner"), "embedded-runner.py", "exec"), {"__name__": "__main__"})
    lines = output.getvalue().splitlines()
    assert error.value.code == 3
    assert lines[0] == "ALEMBIC_RECEIPT_FAILURE code=WRITE_FAILED"
    assert lines[1].startswith("ALEMBIC_UPGRADE_FAILURE stage=upgrade")
    assert "exception_class=_WrappedOperationalError" in lines[1]
    assert "receipt_status=WRITE_FAILED" in lines[1]
    assert "sensitive" not in output.getvalue()
    assert "secret diagnostic string" not in output.getvalue()
    assert list(tmp_path.glob("alembic-migration-*.json")) == []


def test_default_preflight_still_accepts_empty_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    database = _Database(schema="chrom_assistant_accept_20261006", versions=[], tables=[])
    output = _exec_preflight(monkeypatch, database, mode="0")
    assert "baseline=empty" in output


def test_default_runner_keeps_upgrade_head_behavior(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    database = _Database(schema="chrom_assistant_accept_20261006", versions=[], tables=[])
    _install_fakes(monkeypatch, database)
    monkeypatch.setenv("CHROM_MIGRATION_FRAMEWORK_STATE_ONLY", "0")
    monkeypatch.setenv("CHROM_MIGRATION_BACKEND_DIR", str(SCRIPT.parents[2] / "backend"))
    monkeypatch.setenv("CHROM_MIGRATION_EVIDENCE_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://offline:test@127.0.0.1:3306/fake")
    with contextlib.redirect_stdout(io.StringIO()):
        exec(compile(_embedded("runner"), "embedded-runner.py", "exec"), {"__name__": "__main__"})
    assert database.upgrade_targets == ["head"]
