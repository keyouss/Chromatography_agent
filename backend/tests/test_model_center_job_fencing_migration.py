from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import ast
import hashlib
import json
import re
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import BigInteger, Column, Integer, MetaData, PrimaryKeyConstraint, String, Table, create_engine, inspect as sqlalchemy_inspect, text

from chrom_platform.infrastructure import migration_compat
from chrom_platform.assembly.profile import _profile_migrations
from chrom_platform.modules.model_center.worker import _sync


ROOT = Path(__file__).resolve().parents[1]


def _config() -> Config:
    config = Config(str(ROOT / "alembic" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", "mysql://user:password@localhost/chrom_platform")
    return config


def test_model_center_fencing_migration_is_linear_and_mysql_ddl_safe() -> None:
    script = ScriptDirectory.from_config(_config())
    revision = script.get_revision("0010_model_center_job_fencing")
    assert revision is not None and revision.down_revision == "0009_training_outbox_lease"

    output = StringIO()
    with redirect_stdout(output):
        command.upgrade(_config(), "0010_model_center_job_fencing", sql=True)
    sql = output.getvalue()
    sql = sql[sql.rfind("-- Running upgrade 0009_training_outbox_lease"):]
    assert "ALTER TABLE registration ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1" in sql
    assert "ALTER TABLE evaluation ADD COLUMN dispatch_id VARCHAR(96)" in sql
    assert "ALTER TABLE deployment DROP CHECK ck_deployment_status" in sql
    assert "CANCELED" in sql
    assert "ck_registration_ck_registration_status" not in sql


def test_0010_preflight_uses_real_multihead_upgrade_closure() -> None:
    script = ScriptDirectory.from_config(_config())
    assert migration_compat.model_center_0010_is_pending(
        script, "0010_model_center_job_fencing", {"0009_training_outbox_lease"}
    )
    assert migration_compat.model_center_0010_is_pending(
        script,
        "0013_merge_m2_batch_export",
        {"0009_training_outbox_lease", "0012_m2_record_export"},
    )
    assert migration_compat.model_center_0010_is_pending(
        script,
        "0019_batch_export_status_repair",
        {"0009_training_outbox_lease", "0012_m2_record_export"},
    )
    assert not migration_compat.model_center_0010_is_pending(
        script, "0019_batch_export_status_repair", {"0013_merge_m2_batch_export"}
    )
    with pytest.raises(RuntimeError, match="cannot determine"):
        migration_compat.model_center_0010_is_pending(
            script, "unknown-target", {"0009_training_outbox_lease"}
        )


def test_async_business_record_without_shared_lifecycle_port_fails_closed() -> None:
    record = type("Record", (), {"job_id": "job-1"})()
    assert _sync(object(), record, "FAILED") is False


def test_deployment_compatibility_rejects_wrong_existing_column_definition() -> None:
    with pytest.raises(RuntimeError, match="deployment.attempt"):
        migration_compat.validate_existing_deployment_column(
            "attempt",
            {"type": String(96), "nullable": False, "default": None},
        )
    with pytest.raises(RuntimeError, match="deployment.dispatch_id"):
        migration_compat.validate_existing_deployment_column(
            "dispatch_id",
            {"type": String(32), "nullable": True, "default": None},
        )
    with pytest.raises(RuntimeError, match="deployment.attempt"):
        migration_compat.validate_existing_deployment_column(
            "attempt",
            {"type": Integer(), "nullable": False, "default": "2"},
        )
    with pytest.raises(RuntimeError, match="deployment.attempt"):
        migration_compat.validate_existing_deployment_column(
            "attempt",
            {"type": BigInteger(), "nullable": False, "default": None},
        )


def test_partial_0010_columns_are_accepted_only_for_known_tables_and_defaults() -> None:
    migration_compat.validate_existing_fencing_column(
        "registration",
        "attempt",
        {"type": Integer(), "nullable": False, "default": "1"},
    )
    migration_compat.validate_existing_fencing_column(
        "evaluation",
        "dispatch_id",
        {"type": String(96), "nullable": True, "default": None},
    )
    with pytest.raises(RuntimeError, match="registration.attempt"):
        migration_compat.validate_existing_fencing_column(
            "registration",
            "attempt",
            {"type": Integer(), "nullable": False, "default": "2"},
        )


def test_missing_status_check_fails_closed_before_any_ddl(monkeypatch) -> None:
    class _Dialect:
        class _Preparer:
            @staticmethod
            def quote(identifier: str) -> str:
                return f"`{identifier}`"

        identifier_preparer = _Preparer()

    class _Result:
        def mappings(self):
            return self

        def all(self):
            return []

        def scalar_one(self):
            return 0

    class _Connection:
        dialect = _Dialect()

        def execute(self, statement, _params=None):
            return _Result()

        def exec_driver_sql(self, statement):
            raise AssertionError(f"must not add a check after invalid rows: {statement}")

    class _Inspector:
        def get_table_names(self):
            return ["registration"]

        def get_check_constraints(self, table):
            return []

    monkeypatch.setattr(migration_compat, "inspect", lambda connection: _Inspector())
    with pytest.raises(RuntimeError, match="known CHECK is missing"):
        migration_compat.prepare_model_center_0010_constraints(_Connection())


class _ChecksResult:
    def __init__(self, rows: list[dict[str, str]]) -> None:
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _ChecksConnection:
    class _Dialect:
        class _Preparer:
            @staticmethod
            def quote(identifier: str) -> str:
                return f"`{identifier}`"

        identifier_preparer = _Preparer()

    dialect = _Dialect()

    def __init__(self, checks: dict[str, list[dict[str, str]]]) -> None:
        self.checks = checks
        self.statements: list[str] = []
        self.fallback_sql: str | None = None

    def execute(self, statement, params=None):
        if "information_schema.table_constraints" in str(statement):
            self.fallback_sql = str(statement)
            return _ChecksResult(self.checks[params["table"]])
        raise AssertionError(f"unexpected SQL: {statement}")

    def exec_driver_sql(self, statement: str):
        self.statements.append(statement)


class _ChecksInspector:
    def __init__(self, connection: _ChecksConnection) -> None:
        self.connection = connection

    def get_table_names(self):
        return list(self.connection.checks)

    def get_check_constraints(self, table):
        return []


def _known_old_checks() -> dict[str, list[dict[str, str]]]:
    return {
        table: [{"name": values[0], "sqltext": values[1]}]
        for table, values in migration_compat.MODEL_CENTER_0010_CHECKS.items()
    }


def test_mysql_check_constraint_fallback_joins_table_constraints(monkeypatch) -> None:
    checks = _known_old_checks()
    connection = _ChecksConnection(checks)
    inspector = _ChecksInspector(connection)
    rows = migration_compat._get_checks(connection, inspector, "registration")

    assert rows == checks["registration"]
    assert connection.fallback_sql is not None
    assert "JOIN information_schema.check_constraints" in connection.fallback_sql
    assert "tc.constraint_type = 'CHECK'" in connection.fallback_sql


def test_empty_inspector_uses_mysql_check_constraint_fallback(monkeypatch) -> None:
    checks = _known_old_checks()
    connection = _ChecksConnection(checks)
    monkeypatch.setattr(migration_compat, "inspect", lambda _connection: _ChecksInspector(connection))

    migration_compat.prepare_model_center_0010_constraints(connection)

    assert connection.statements == []


def test_known_double_prefixed_check_is_repaired_without_touching_other_tables(monkeypatch) -> None:
    checks = _known_old_checks()
    checks["registration"].append({
        "name": "ck_registration_ck_registration_status",
        "sqltext": migration_compat.MODEL_CENTER_0010_CHECKS["registration"][1],
    })
    connection = _ChecksConnection(checks)
    monkeypatch.setattr(migration_compat, "inspect", lambda _connection: _ChecksInspector(connection))

    migration_compat.prepare_model_center_0010_constraints(connection)

    assert connection.statements == [
        "ALTER TABLE `registration` DROP CHECK `ck_registration_ck_registration_status`"
    ]


@pytest.mark.parametrize("slash_count", [1, 2, 3])
def test_mysql_escaped_known_check_expression_is_accepted(monkeypatch, slash_count: int) -> None:
    checks = _known_old_checks()
    for table, values in migration_compat.MODEL_CENTER_0010_CHECKS.items():
        checks[table][0]["sqltext"] = values[1].replace("'", ("\\" * slash_count) + "'")
    connection = _ChecksConnection(checks)
    monkeypatch.setattr(migration_compat, "inspect", lambda _connection: _ChecksInspector(connection))

    migration_compat.prepare_model_center_0010_constraints(connection)

    assert connection.statements == []


def test_unknown_status_value_remains_fail_closed_with_escaped_quotes(monkeypatch) -> None:
    checks = _known_old_checks()
    checks["registration"][0]["sqltext"] = (
        "status IN (\\'DRAFT\\',\\'VALIDATING\\',\\'VALID\\',\\'INVALID\\',\\'UNREGISTERED\\')"
    )
    connection = _ChecksConnection(checks)
    monkeypatch.setattr(migration_compat, "inspect", lambda _connection: _ChecksInspector(connection))

    with pytest.raises(RuntimeError, match="unknown or conflicting CHECK"):
        migration_compat.prepare_model_center_0010_constraints(connection)
    assert connection.statements == []


def test_evaluation_pass_status_check_is_not_a_model_center_status_check(monkeypatch) -> None:
    checks = _known_old_checks()
    checks["evaluation"].append({
        "name": "ck_evaluation_ck_evaluation_pass_status",
        "sqltext": "(`pass_status` in (_utf8mb4\\'PENDING\\',_utf8mb4\\'PASSED\\',_utf8mb4\\'FAILED\\'))",
    })
    connection = _ChecksConnection(checks)
    monkeypatch.setattr(migration_compat, "inspect", lambda _connection: _ChecksInspector(connection))

    migration_compat.prepare_model_center_0010_constraints(connection)

    assert connection.statements == []


def test_unknown_status_check_fails_before_partial_ddl(monkeypatch) -> None:
    checks = _known_old_checks()
    checks["evaluation"].append({
        "name": "legacy_unknown_status_check",
        "sqltext": "status IN ('QUEUED','RUNNING')",
    })
    connection = _ChecksConnection(checks)
    monkeypatch.setattr(migration_compat, "inspect", lambda _connection: _ChecksInspector(connection))

    with pytest.raises(RuntimeError, match="unknown or conflicting CHECK"):
        migration_compat.prepare_model_center_0010_constraints(connection)
    assert connection.statements == []


def test_duplicate_known_check_name_fails_closed(monkeypatch) -> None:
    checks = _known_old_checks()
    checks["deployment"].append(dict(checks["deployment"][0]))
    connection = _ChecksConnection(checks)
    monkeypatch.setattr(migration_compat, "inspect", lambda _connection: _ChecksInspector(connection))

    with pytest.raises(RuntimeError, match="duplicate CHECK name"):
        migration_compat.prepare_model_center_0010_constraints(connection)
    assert connection.statements == []


def test_repeated_preflight_with_canonical_checks_is_noop(monkeypatch) -> None:
    connection = _ChecksConnection(_known_old_checks())
    monkeypatch.setattr(migration_compat, "inspect", lambda _connection: _ChecksInspector(connection))

    migration_compat.prepare_model_center_0010_constraints(connection)
    migration_compat.prepare_model_center_0010_constraints(connection)

    assert connection.statements == []


def _load_online_migration_runner(monkeypatch):
    env_path = ROOT / "alembic" / "env.py"
    tree = ast.parse(env_path.read_text(encoding="utf-8"), filename=str(env_path))
    node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == "_run_online_migrations")

    class _Operations:
        @staticmethod
        def add_column(_operation, table_name, column, **_kwargs):
            original_adds.append((table_name, column.name))

    class _Context:
        def __init__(self, callback):
            self.callback = callback

        def run_migrations(self):
            self.callback()

    namespace = {
        "Operations": _Operations,
        "inspect": lambda connection: connection.inspector,
        "validate_existing_fencing_column": lambda table, name, column: (
            connection_validated.append((table, name)),
            migration_compat.validate_existing_fencing_column(table, name, column),
        )[1],
        "context": None,
    }
    connection_validated: list[tuple[str, str]] = []
    original_adds: list[tuple[str, str]] = []

    module = ast.Module(body=[node], type_ignores=[])
    exec(compile(module, str(env_path), "exec"), namespace)
    return namespace["_run_online_migrations"], _Operations, _Context, connection_validated, original_adds


def test_empty_upgrade_skips_0005_deployment_fences_without_version_gate(monkeypatch) -> None:
    class _Column:
        def __init__(self, name):
            self.name = name

    class _Inspector:
        def get_table_names(self):
            return ["registration", "evaluation", "deployment"]

        def get_columns(self, table):
            if table == "deployment":
                return [
                    {"name": "attempt", "type": Integer(), "nullable": False, "default": None},
                    {"name": "dispatch_id", "type": String(96), "nullable": True, "default": None},
                ]
            return []

    class _Connection:
        inspector = _Inspector()

    connection = _Connection()
    runner, operations, context_type, validated, original_adds = _load_online_migration_runner(monkeypatch)

    def _run_steps():
        operation = SimpleNamespace(get_bind=lambda: connection)
        operations.add_column(operation, "deployment", _Column("attempt"))
        operations.add_column(operation, "deployment", _Column("dispatch_id"))
        operations.add_column(operation, "registration", _Column("attempt"))
        operations.add_column(operation, "evaluation", _Column("dispatch_id"))

    namespace = runner.__globals__
    namespace["context"] = context_type(_run_steps)
    runner(connection)

    assert original_adds == [("registration", "attempt"), ("evaluation", "dispatch_id")]
    assert validated == [("deployment", "attempt"), ("deployment", "dispatch_id")]


def test_existing_fencing_column_shape_conflict_fails_closed_before_add(monkeypatch) -> None:
    class _Column:
        name = "attempt"

    class _Inspector:
        def get_table_names(self):
            return ["deployment"]

        def get_columns(self, table):
            return [{"name": "attempt", "type": String(96), "nullable": False, "default": None}]

    class _Connection:
        inspector = _Inspector()

    connection = _Connection()
    runner, operations, context_type, _validated, original_adds = _load_online_migration_runner(monkeypatch)

    def _run_steps():
        operation = SimpleNamespace(get_bind=lambda: connection)
        operations.add_column(operation, "deployment", _Column())

    namespace = runner.__globals__
    namespace["context"] = context_type(_run_steps)
    with pytest.raises(RuntimeError, match="deployment.attempt"):
        runner(connection)
    assert original_adds == []


def _load_version_table_guard():
    env_path = ROOT / "alembic" / "env.py"
    tree = ast.parse(env_path.read_text(encoding="utf-8"), filename=str(env_path))
    wanted = {
        "_version_table_definition",
        "_version_revision_ids",
        "_validate_requested_revisions",
        "_version_table_shape",
        "_mysql_version_column_attributes",
        "_validate_version_values",
        "_ensure_version_table_capacity",
    }
    nodes = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    namespace = {
        "Column": Column,
        "MetaData": MetaData,
        "PrimaryKeyConstraint": PrimaryKeyConstraint,
        "String": String,
        "Table": Table,
        "MigrationContext": MigrationContext,
        "text": text,
        "inspect": sqlalchemy_inspect,
        "_VERSION_TABLE": "alembic_version",
        "_VERSION_COLUMN": "version_num",
        "_VERSION_MIN_LENGTH": 128,
        "_MYSQL_IDENTIFIER_RE": re.compile(r"^[A-Za-z0-9_]+$"),
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(env_path), "exec"), namespace)
    return namespace


def _load_mysql_lock_helpers():
    env_path = ROOT / "alembic" / "env.py"
    tree = ast.parse(env_path.read_text(encoding="utf-8"), filename=str(env_path))
    wanted = {"_acquire_mysql_migration_lock", "_release_mysql_migration_lock"}
    nodes = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    namespace = {"hashlib": hashlib, "text": text}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(env_path), "exec"), namespace)
    return namespace


def test_mysql_migration_lock_is_database_scoped_and_released(monkeypatch) -> None:
    namespace = _load_mysql_lock_helpers()

    class _Preparer:
        @staticmethod
        def quote(value: str) -> str:
            return f"`{value}`"

    class _Dialect:
        name = "mysql"
        identifier_preparer = _Preparer()

    class _Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class _Connection:
        dialect = _Dialect()

        def __init__(self, database):
            self.database = database
            self.calls = []

        def execute(self, statement, params=None):
            sql = str(statement)
            self.calls.append((sql, params or {}))
            if sql == "SELECT DATABASE()":
                return _Result(self.database)
            if "GET_LOCK" in sql:
                return _Result(1)
            if "RELEASE_LOCK" in sql:
                return _Result(1)
            raise AssertionError(sql)

    first = _Connection("chrom_platform")
    second = _Connection("chrom_platform")
    other = _Connection("other_database")
    first_lock = namespace["_acquire_mysql_migration_lock"](first)
    second_lock = namespace["_acquire_mysql_migration_lock"](second)
    other_lock = namespace["_acquire_mysql_migration_lock"](other)
    assert first_lock == second_lock
    assert other_lock != first_lock
    assert len(first_lock.encode("utf-8")) <= 64
    assert "other_database" not in first_lock
    namespace["_release_mysql_migration_lock"](first, first_lock)
    assert any("RELEASE_LOCK" in sql for sql, _ in first.calls)


def test_mysql_migration_lock_busy_and_release_failure_fail_closed() -> None:
    namespace = _load_mysql_lock_helpers()

    class _Dialect:
        name = "mysql"

    class _Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class _Connection:
        dialect = _Dialect()

        def __init__(self, values):
            self.values = iter(values)

        def execute(self, _statement, _params=None):
            return _Result(next(self.values))

    with pytest.raises(RuntimeError, match="another Alembic migration"):
        namespace["_acquire_mysql_migration_lock"](_Connection(["chrom_platform", 0]))
    with pytest.raises(RuntimeError, match="failed to release"):
        namespace["_release_mysql_migration_lock"](_Connection([0]), "chrom_platform_alembic_deadbeef")


def test_fresh_version_table_is_created_wide_before_migrations() -> None:
    namespace = _load_version_table_guard()
    script = ScriptDirectory.from_config(_config())
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        namespace["_ensure_version_table_capacity"](
            connection,
            script,
            "0019_batch_export_status_repair",
        )
        column = sqlalchemy_inspect(connection).get_columns("alembic_version")[0]
        assert column["type"].length == 128
        assert sqlalchemy_inspect(connection).get_pk_constraint("alembic_version")["constrained_columns"] == ["version_num"]


def test_version_width_covers_formal_profiles_and_batch_candidate_closures() -> None:
    builds = ROOT.parent / "builds"
    versions = ROOT / "alembic" / "versions"
    expected = {
        "universal-v1.json": (13, 31),
        "sfc-v1.json": (5, 31),
        "cc-v1.json": (5, 31),
        "minimal-structure-v1.json": (1, 19),
        "candidates/universal-v1-batch-revision-20261004-06/universal-v1.json": (19, 38),
    }
    for filename, (expected_count, expected_max_length) in expected.items():
        manifest = json.loads((builds / filename).read_text(encoding="utf-8"))
        batch_enabled = any(
            "batch" in tools
            for tools in manifest["prediction_tools"].values()
        )
        selected = _profile_migrations(
            versions,
            set(manifest["modules"]),
            batch_enabled=batch_enabled,
        )
        revision_lengths = []
        for migration in selected:
            source = (versions / migration).read_text(encoding="utf-8")
            match = re.search(r'(?m)^revision\s*=\s*["\']([^"\']+)["\']', source)
            assert match, migration
            revision_lengths.append(len(match.group(1)))
        assert len(selected) == expected_count
        assert max(revision_lengths) == expected_max_length
        assert max(revision_lengths) <= 128
        if batch_enabled:
            assert "0014_batch_resolved_snapshot_export.py" in selected
            assert "0016_merge_batch_snapshot_replay_draft.py" in selected


def test_existing_non_mysql_narrow_version_table_fails_closed_without_mysql_ddl() -> None:
    namespace = _load_version_table_guard()
    script = ScriptDirectory.from_config(_config())
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    version = Table(
        "alembic_version",
        metadata,
        Column("version_num", String(32), nullable=False),
        PrimaryKeyConstraint("version_num"),
    )
    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(version.insert().values(version_num="0013_merge_m2_batch_export"))
        with pytest.raises(RuntimeError, match="unsupported dialect"):
            namespace["_ensure_version_table_capacity"](
                connection,
                script,
                "0019_batch_export_status_repair",
            )


def test_existing_mysql_narrow_version_table_is_widened_once_and_value_is_preserved(monkeypatch) -> None:
    namespace = _load_version_table_guard()
    script = ScriptDirectory.from_config(_config())

    class _Preparer:
        @staticmethod
        def quote(value: str) -> str:
            return f"`{value}`"

    class _Dialect:
        name = "mysql"
        identifier_preparer = _Preparer()

    class _Rows:
        def fetchall(self):
            return [("0013_merge_m2_batch_export",)]

        def mappings(self):
            return self

        def all(self):
            return [{
                "CHARACTER_SET_NAME": "utf8mb4",
                "COLLATION_NAME": "utf8mb4_0900_ai_ci",
                "COLUMN_DEFAULT": None,
                "EXTRA": "",
            }]

    class _Connection:
        dialect = _Dialect()

        def __init__(self):
            self.widened = False
            self.statements: list[str] = []

        def exec_driver_sql(self, statement: str):
            self.statements.append(statement)
            if statement.startswith("SELECT"):
                return _Rows()
            if statement.startswith("ALTER TABLE"):
                self.widened = True
            return _Rows()

        def execute(self, statement, _params=None):
            assert "information_schema.COLUMNS" in str(statement)
            return _Rows()

    class _Inspector:
        def __init__(self, connection):
            self.connection = connection

        def has_table(self, name):
            return True

        def get_columns(self, name):
            length = 128 if self.connection.widened else 32
            return [{"name": "version_num", "type": String(length), "nullable": False}]

        def get_pk_constraint(self, name):
            return {"constrained_columns": ["version_num"]}

    class _MigrationContext:
        @staticmethod
        def configure(connection):
            return type("Configured", (), {"get_current_heads": lambda _self: ("0013_merge_m2_batch_export",)})()

    connection = _Connection()
    monkeypatch.setitem(namespace, "inspect", lambda _connection: _Inspector(connection))
    monkeypatch.setitem(namespace, "MigrationContext", _MigrationContext)

    namespace["_ensure_version_table_capacity"](connection, script, "0019_batch_export_status_repair")
    namespace["_ensure_version_table_capacity"](connection, script, "0019_batch_export_status_repair")

    alter = [statement for statement in connection.statements if statement.startswith("ALTER TABLE")]
    assert alter == [
        "ALTER TABLE `alembic_version` MODIFY COLUMN `version_num` VARCHAR(128) "
        "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL"
    ]
    assert connection.statements.count("SELECT `version_num` FROM `alembic_version`") == 3


def test_unknown_mysql_head_is_rejected_before_narrow_column_alter(monkeypatch) -> None:
    namespace = _load_version_table_guard()
    script = ScriptDirectory.from_config(_config())

    class _Preparer:
        @staticmethod
        def quote(value: str) -> str:
            return f"`{value}`"

    class _Dialect:
        name = "mysql"
        identifier_preparer = _Preparer()

    class _Result:
        def fetchall(self):
            return [("0013_merge_m2_batch_export",)]

    class _Connection:
        dialect = _Dialect()

        def __init__(self):
            self.statements: list[str] = []

        def exec_driver_sql(self, statement: str):
            self.statements.append(statement)
            return _Result()

    class _Inspector:
        def has_table(self, _name):
            return True

        def get_columns(self, _name):
            return [{"name": "version_num", "type": String(32), "nullable": False}]

        def get_pk_constraint(self, _name):
            return {"constrained_columns": ["version_num"]}

    class _MigrationContext:
        @staticmethod
        def configure(_connection):
            return type("Configured", (), {"get_current_heads": lambda _self: ("unknown_head",)})()

    connection = _Connection()
    monkeypatch.setitem(namespace, "inspect", lambda _connection: _Inspector())
    monkeypatch.setitem(namespace, "MigrationContext", _MigrationContext)
    with pytest.raises(RuntimeError, match="unknown current head"):
        namespace["_ensure_version_table_capacity"](connection, script, "0019_batch_export_status_repair")
    assert not any(statement.startswith("ALTER TABLE") for statement in connection.statements)


def test_mysql_version_table_primary_key_error_is_rejected_before_alter(monkeypatch) -> None:
    namespace = _load_version_table_guard()
    script = ScriptDirectory.from_config(_config())

    class _Dialect:
        name = "mysql"

        class _Preparer:
            @staticmethod
            def quote(value: str) -> str:
                return f"`{value}`"

        identifier_preparer = _Preparer()

    class _Connection:
        dialect = _Dialect()
        statements: list[str] = []

        def exec_driver_sql(self, statement: str):
            self.statements.append(statement)
            return type("Rows", (), {"fetchall": lambda _self: [("0013_merge_m2_batch_export",)]})()

    class _Inspector:
        def has_table(self, _name):
            return True

        def get_columns(self, _name):
            return [{"name": "version_num", "type": String(32), "nullable": False}]

        def get_pk_constraint(self, _name):
            return {"constrained_columns": []}

    class _MigrationContext:
        @staticmethod
        def configure(_connection):
            return type("Configured", (), {"get_current_heads": lambda _self: ("0013_merge_m2_batch_export",)})()

    connection = _Connection()
    monkeypatch.setitem(namespace, "inspect", lambda _connection: _Inspector())
    monkeypatch.setitem(namespace, "MigrationContext", _MigrationContext)
    with pytest.raises(RuntimeError, match="primary-key column"):
        namespace["_ensure_version_table_capacity"](connection, script, "0019_batch_export_status_repair")
    assert not any(statement.startswith("ALTER TABLE") for statement in connection.statements)


def test_unknown_version_value_and_bad_primary_key_fail_before_migration() -> None:
    namespace = _load_version_table_guard()
    script = ScriptDirectory.from_config(_config())
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    version = Table(
        "alembic_version",
        metadata,
        Column("version_num", String(128), nullable=False),
    )
    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(version.insert().values(version_num="unknown_revision"))
        with pytest.raises(RuntimeError, match="primary-key column"):
            namespace["_ensure_version_table_capacity"](
                connection,
                script,
                "0019_batch_export_status_repair",
            )


def test_offline_version_table_uses_128_characters() -> None:
    output = StringIO()
    with redirect_stdout(output):
        command.upgrade(_config(), "0014_batch_resolved_snapshot_export", sql=True)
    sql = output.getvalue().lower()
    assert "create table alembic_version" in sql
    assert "version_num varchar(128) not null" in sql
