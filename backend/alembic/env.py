"""Alembic environment for the MySQL-only M1 schema."""

from __future__ import annotations

import hashlib
import os
import re
from logging.config import fileConfig

from alembic import context
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import (
    Column,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    engine_from_config,
    inspect,
    pool,
    text,
)

from chrom_platform.infrastructure.metadata import Base
from chrom_platform.infrastructure.migration_compat import (
    MODEL_CENTER_0009_REVISION,
    MODEL_CENTER_0010_REVISION,
    model_center_0010_is_pending,
    prepare_model_center_0010_constraints,
    validate_existing_fencing_column,
)


config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_VERSION_TABLE = "alembic_version"
_VERSION_COLUMN = "version_num"
_VERSION_MIN_LENGTH = 128
_MYSQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _version_table_definition() -> Table:
    metadata = MetaData()
    return Table(
        _VERSION_TABLE,
        metadata,
        Column(_VERSION_COLUMN, String(_VERSION_MIN_LENGTH), nullable=False),
        PrimaryKeyConstraint(_VERSION_COLUMN, name=f"{_VERSION_TABLE}_pkc"),
    )


def _acquire_mysql_migration_lock(connection) -> str | None:
    """Acquire a per-database MySQL lock before any migration DDL."""

    if connection.dialect.name != "mysql":
        return None
    database = connection.execute(text("SELECT DATABASE()")).scalar_one_or_none()
    if not isinstance(database, str) or not database:
        raise RuntimeError("cannot acquire Alembic migration lock without a selected database")
    digest = hashlib.sha256(database.encode("utf-8")).hexdigest()[:32]
    lock_name = f"chrom_platform_alembic_{digest}"
    result = connection.execute(
        text("SELECT GET_LOCK(:lock_name, 0)"),
        {"lock_name": lock_name},
    ).scalar_one_or_none()
    if result != 1:
        raise RuntimeError("another Alembic migration is already running for this database")
    return lock_name


def _release_mysql_migration_lock(connection, lock_name: str | None) -> None:
    if lock_name is None:
        return
    result = connection.execute(
        text("SELECT RELEASE_LOCK(:lock_name)"),
        {"lock_name": lock_name},
    ).scalar_one_or_none()
    if result != 1:
        raise RuntimeError("failed to release Alembic migration lock")


def _version_revision_ids(script_directory: ScriptDirectory) -> frozenset[str]:
    revisions = frozenset(str(script.revision) for script in script_directory.walk_revisions())
    if not revisions:
        raise RuntimeError("Alembic graph has no revision IDs")
    too_long = sorted(revision for revision in revisions if len(revision) > _VERSION_MIN_LENGTH)
    if too_long:
        raise RuntimeError(
            "Alembic revision ID exceeds alembic_version.version_num capacity: "
            + ", ".join(too_long)
        )
    return revisions


def _validate_requested_revisions(script_directory: ScriptDirectory, target: str | tuple[str, ...] | None) -> None:
    if target is None:
        return
    targets = target if isinstance(target, tuple) else (target,)
    for item in targets:
        if item in {"base", "heads", "head"}:
            continue
        try:
            script_directory.get_revisions(item)
        except Exception as exc:
            raise RuntimeError(f"cannot resolve requested Alembic target {item!r}") from exc


def _version_table_shape(connection, inspector) -> tuple[dict, int]:
    columns = inspector.get_columns(_VERSION_TABLE)
    if [str(column.get("name")) for column in columns] != [_VERSION_COLUMN]:
        raise RuntimeError("alembic_version must contain exactly version_num")
    column = columns[0]
    if column.get("nullable") is not False:
        raise RuntimeError("alembic_version.version_num must be NOT NULL")
    column_type = column.get("type")
    length = getattr(column_type, "length", None)
    if not isinstance(column_type, String) or not isinstance(length, int):
        raise RuntimeError("alembic_version.version_num must be a bounded string column")
    primary_key = inspector.get_pk_constraint(_VERSION_TABLE)
    if tuple(primary_key.get("constrained_columns") or ()) != (_VERSION_COLUMN,):
        raise RuntimeError("alembic_version.version_num must be the sole primary-key column")
    return column, length


def _mysql_version_column_attributes(connection) -> tuple[str | None, str | None]:
    rows = connection.execute(
        text(
            "SELECT CHARACTER_SET_NAME, COLLATION_NAME, COLUMN_DEFAULT, EXTRA "
            "FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = :table_name AND COLUMN_NAME = :column_name"
        ),
        {"table_name": _VERSION_TABLE, "column_name": _VERSION_COLUMN},
    ).mappings().all()
    if len(rows) != 1:
        raise RuntimeError("cannot inspect alembic_version.version_num attributes")
    row = {str(key).upper(): value for key, value in rows[0].items()}
    if row.get("COLUMN_DEFAULT") is not None or row.get("EXTRA") not in (None, ""):
        raise RuntimeError("alembic_version.version_num has unsupported default or extra attributes")
    charset = row.get("CHARACTER_SET_NAME")
    collation = row.get("COLLATION_NAME")
    for value, label in ((charset, "character set"), (collation, "collation")):
        if value is not None and (not isinstance(value, str) or _MYSQL_IDENTIFIER_RE.fullmatch(value) is None):
            raise RuntimeError(f"alembic_version.version_num has invalid {label} metadata")
    return charset, collation


def _validate_version_values(connection, script_directory: ScriptDirectory, known_revisions: frozenset[str]) -> None:
    preparer = connection.dialect.identifier_preparer
    table = preparer.quote(_VERSION_TABLE)
    column = preparer.quote(_VERSION_COLUMN)
    values = [row[0] for row in connection.exec_driver_sql(f"SELECT {column} FROM {table}").fetchall()]
    unknown = sorted({value for value in values if not isinstance(value, str) or value not in known_revisions})
    if unknown:
        raise RuntimeError("alembic_version contains unknown revision value(s)")
    current_heads = set(MigrationContext.configure(connection).get_current_heads())
    if not current_heads.issubset(known_revisions):
        raise RuntimeError("alembic_version contains unknown current head(s)")


def _ensure_version_table_capacity(connection, script_directory: ScriptDirectory, target: str | tuple[str, ...] | None) -> None:
    """Ensure Alembic can persist every revision before business DDL starts."""

    known_revisions = _version_revision_ids(script_directory)
    _validate_requested_revisions(script_directory, target)
    inspector = inspect(connection)
    if not inspector.has_table(_VERSION_TABLE):
        _version_table_definition().create(connection)
        inspector = inspect(connection)
    _column, length = _version_table_shape(connection, inspector)
    # Validate persisted versions and graph state before any non-transactional
    # MySQL ALTER.  A malformed existing version table must never be widened
    # before the migration is rejected.
    _validate_version_values(connection, script_directory, known_revisions)
    if length < _VERSION_MIN_LENGTH:
        if connection.dialect.name != "mysql":
            raise RuntimeError(
                f"alembic_version.version_num is {length} characters wide on "
                f"unsupported dialect {connection.dialect.name!r}; refusing migration"
            )
        preparer = connection.dialect.identifier_preparer
        table = preparer.quote(_VERSION_TABLE)
        column = preparer.quote(_VERSION_COLUMN)
        charset, collation = _mysql_version_column_attributes(connection)
        charset_sql = f" CHARACTER SET {charset}" if charset else ""
        collation_sql = f" COLLATE {collation}" if collation else ""
        connection.exec_driver_sql(
            f"ALTER TABLE {table} MODIFY COLUMN {column} VARCHAR({_VERSION_MIN_LENGTH})"
            f"{charset_sql}{collation_sql} NOT NULL"
        )
        inspector = inspect(connection)
        _column, length = _version_table_shape(connection, inspector)
        if length < _VERSION_MIN_LENGTH:
            raise RuntimeError("failed to widen alembic_version.version_num")
        _validate_version_values(connection, script_directory, known_revisions)


def _run_online_migrations(connection) -> None:
    """Run migrations while tolerating 0005's pre-existing deployment fences."""

    # 0005 already declared deployment.attempt and deployment.dispatch_id, and
    # a failed non-transactional 0010 can leave any of the three tables partly
    # altered while alembic_version remains 0009.  Skip only an existing
    # attempt/dispatch_id column whose exact reflected definition is compatible
    # with 0010.  Missing columns still use the frozen migration's ADD COLUMN;
    # conflicting definitions fail closed.
    original_add_column = Operations.add_column

    def add_column_compat(self, table_name, column, *, schema=None, if_not_exists=None, inline_references=None, inline_primary_key=None):
        existing = False
        if table_name in {"registration", "evaluation", "deployment"} and column.name in {"attempt", "dispatch_id"}:
            bind = self.get_bind()
            inspector = inspect(bind)
            if table_name in inspector.get_table_names():
                reflected = {
                    str(item["name"]): item for item in inspector.get_columns(table_name)
                }
                if column.name in reflected:
                    validate_existing_fencing_column(table_name, column.name, reflected[column.name])
                    existing = True
        if existing:
            return None
        return original_add_column(
            self,
            table_name,
            column,
            schema=schema,
            if_not_exists=if_not_exists,
            inline_references=inline_references,
            inline_primary_key=inline_primary_key,
        )

    Operations.add_column = add_column_compat
    try:
        context.run_migrations()
    finally:
        Operations.add_column = original_add_column


def _requested_target_revision() -> str | tuple[str, ...] | None:
    """Read Alembic's requested destination without inferring graph order."""

    try:
        target = context.get_revision_argument()
    except KeyError:
        # Commands such as ``current`` have no destination revision.
        target = None
    if not target:
        # ``get_revision_argument`` is populated after ``configure`` on some
        # Alembic command paths.  The command options are available earlier,
        # which is when the online preflight must run for a fresh upgrade.
        target = getattr(getattr(config, "cmd_opts", None), "revision", None)
    if not target:
        return None
    if isinstance(target, (tuple, list)):
        return tuple(str(item) for item in target)
    return str(target)


def _model_center_0010_is_target(connection) -> bool:
    """Return whether the real upgrade closure contains model-center 0010."""

    target = _requested_target_revision()
    if not target:
        return False
    current_heads = set(MigrationContext.configure(connection).get_current_heads())
    script_directory = ScriptDirectory.from_config(config)
    return model_center_0010_is_pending(script_directory, target, current_heads)


def _on_version_apply(ctx, step, heads, run_args) -> None:
    """Prepare model-center checks after 0009 in a multi-revision upgrade."""

    revision = step.get("up_revision_id") if isinstance(step, dict) else getattr(step, "up_revision_id", None)
    if (
        not context.is_offline_mode()
        and _model_center_0010_is_target(ctx.connection)
        and revision == "0009_training_outbox_lease"
    ):
        prepare_model_center_0010_constraints(ctx.connection)


def _database_url() -> str:
    """Require an explicit URL for online migration; never use a secret-like default."""

    url = os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not url or not url.strip():
        raise RuntimeError("DATABASE_URL or sqlalchemy.url is required for Alembic")
    return url


def run_migrations_offline() -> None:
    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        on_version_apply=_on_version_apply,
    )
    # Alembic 1.18.5 otherwise emits its built-in VARCHAR(32) version table.
    # Replace only the in-memory table definition; offline mode never inspects
    # or executes a database and therefore cannot run the online preflight.
    context.get_context()._version = _version_table_definition()
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    config.set_main_option("sqlalchemy.url", _database_url())
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        lock_name = _acquire_mysql_migration_lock(connection)
        try:
            script_directory = ScriptDirectory.from_config(config)
            _ensure_version_table_capacity(
                connection,
                script_directory,
                _requested_target_revision(),
            )
            model_center_0010_pending = _model_center_0010_is_target(connection)
            if model_center_0010_pending:
                prepare_model_center_0010_constraints(connection)
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                compare_server_default=True,
                on_version_apply=_on_version_apply,
            )
            with context.begin_transaction():
                _run_online_migrations(
                    connection,
                )
            # The compatibility preflight may execute MySQL DDL before 0010.
            # A final explicit commit keeps Alembic's version-table update
            # durable after MySQL's implicit DDL commits.
            connection.commit()
        finally:
            _release_mysql_migration_lock(connection, lock_name)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
