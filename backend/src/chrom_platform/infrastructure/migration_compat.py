"""Online compatibility checks for the frozen model-center migration."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import BigInteger, Integer, String, inspect, text
from sqlalchemy.dialects.mysql import VARCHAR as MySQLVarchar


MODEL_CENTER_0010_CHECKS = {
    "registration": (
        "ck_registration_status",
        "status IN ('DRAFT','VALIDATING','VALID','INVALID','REGISTERED')",
        "status IN ('DRAFT','VALIDATING','VALID','INVALID','REGISTERED','CANCELED')",
    ),
    "evaluation": (
        "ck_evaluation_status",
        "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')",
        "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELED')",
    ),
    "deployment": (
        "ck_deployment_status",
        "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')",
        "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELED')",
    ),
}

MODEL_CENTER_0009_REVISION = "0009_training_outbox_lease"
MODEL_CENTER_0010_REVISION = "0010_model_center_job_fencing"


def model_center_0010_is_pending(
    script_directory: Any,
    target_revision: str | tuple[str, ...] | list[str] | None,
    current_heads: set[str] | tuple[str, ...] | list[str],
) -> bool:
    """Return whether the requested upgrade closure must execute 0010.

    The revision graph, rather than revision-string ordering, is authoritative.
    A database at 0009 may be one head of a multi-head upgrade to 0013/0019;
    databases already at 0010 or later must not be preflighted again.
    """

    current = {str(value) for value in current_heads}
    if MODEL_CENTER_0009_REVISION not in current or MODEL_CENTER_0010_REVISION in current:
        return False
    if not target_revision:
        return False
    target: str | tuple[str, ...] = target_revision
    if isinstance(target_revision, list):
        target = tuple(target_revision)
    try:
        closure = script_directory.iterate_revisions(
            target,
            tuple(sorted(current)),
        )
        return any(
            getattr(revision, "revision", None) == MODEL_CENTER_0010_REVISION
            for revision in closure
        )
    except Exception as exc:
        raise RuntimeError(
            "cannot determine whether the Alembic upgrade path includes model-center 0010"
        ) from exc


def normalize_check_sql(value: str) -> str:
    """Normalize MySQL reflection output for comparison with frozen SQL."""

    normalized = value.lower().replace("`", "").replace("_utf8mb4", "")
    normalized = re.sub(r"\s+", "", normalized)
    while normalized.startswith("(") and normalized.endswith(")"):
        depth = 0
        wraps_whole_value = True
        for index, char in enumerate(normalized):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0 and index != len(normalized) - 1:
                    wraps_whole_value = False
                    break
        if not wraps_whole_value or depth != 0:
            break
        normalized = normalized[1:-1]
    return normalized


def _known_check_sql_variants(value: str) -> set[str]:
    """Return only known SQL text forms of one frozen status expression.

    MySQL 8.4 may expose CHECK_CLAUSE string quotes with one or more literal
    backslashes, while SQLAlchemy's Inspector exposes the same expression
    without them.  The accepted forms are generated from the frozen expression
    itself; this does not normalize arbitrary unknown CHECK expressions.
    """

    variants = set()
    for slash_count in range(4):
        escaped = value.replace("'", ("\\" * slash_count) + "'")
        variants.add(normalize_check_sql(escaped))
    return variants


def _contains_column_identifier(value: str, identifier: str) -> bool:
    """Return whether *value* references the standalone SQL identifier.

    MySQL's information_schema fallback can expose literal quotes with a
    small number of backslashes.  Mask quoted values before matching so a
    status value cannot be mistaken for the column.  The boundary check keeps
    ``pass_status`` out of the model-center status hook.
    """

    value = re.sub(r"\\{1,3}(['\"])", r"\1", value)
    masked: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(value):
        char = value[index]
        if quote is None:
            if char in {"'", '"'}:
                quote = char
                masked.append(" ")
            else:
                masked.append(char)
            index += 1
            continue
        if char == "\\" and index + 1 < len(value):
            index += 2
            continue
        if char == quote:
            quote = None
        index += 1

    token = re.escape(identifier)
    return re.search(rf"(?<![A-Za-z0-9_$]){token}(?![A-Za-z0-9_$])", "".join(masked), re.IGNORECASE) is not None


def quote_identifier(connection: Any, identifier: str) -> str:
    return connection.dialect.identifier_preparer.quote(identifier)


def _is_mysql_integer_32(type_: Any) -> bool:
    """Accept SQLAlchemy INTEGER/INT reflection, but never BIGINT or SMALLINT."""

    if isinstance(type_, BigInteger):
        return False
    return type(type_) is Integer or type(type_).__name__.upper() in {"INTEGER", "INT"}


def _is_varchar_96(type_: Any) -> bool:
    """Accept String(96) and MySQL VARCHAR(96), excluding CHAR/TEXT."""

    return (type(type_) is String or isinstance(type_, MySQLVarchar)) and type_.length == 96


def _get_checks(connection: Any, inspector: Any, table: str) -> list[dict[str, Any]]:
    """Use information_schema names when MySQL Inspector omits CHECK rows."""

    checks = list(inspector.get_check_constraints(table))
    try:
        rows = connection.execute(
            text(
                "SELECT cc.constraint_name AS name, cc.check_clause AS sqltext "
                "FROM information_schema.table_constraints AS tc "
                "JOIN information_schema.check_constraints AS cc "
                "ON cc.constraint_schema = tc.constraint_schema "
                "AND cc.constraint_name = tc.constraint_name "
                "WHERE tc.table_schema = DATABASE() "
                "AND tc.table_name = :table "
                "AND tc.constraint_type = 'CHECK'"
            ),
            {"table": table},
        ).mappings().all()
    except Exception:
        return checks
    return [dict(row) for row in rows]


def prepare_model_center_0010_constraints(connection: Any) -> None:
    """Normalize known 0009 status checks before 0010, failing closed.

    All three tables are inspected and validated before any DDL is issued.  A
    duplicate is repairable only when its name is the known SQLAlchemy legacy
    double-prefix spelling; an unknown or missing status check is a schema
    ambiguity and must stop the migration rather than invite a guess.
    """

    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    plans: list[str] = []
    for table, (canonical_name, old_expression, new_expression) in MODEL_CENTER_0010_CHECKS.items():
        if table not in tables:
            raise RuntimeError(f"cannot prepare 0010 {table}.status: table is missing")

        checks = _get_checks(connection, inspector, table)
        expected_sql = _known_check_sql_variants(old_expression)
        target_sql = _known_check_sql_variants(new_expression)
        # SQLAlchemy's naming convention can apply ``ck_`` twice when a
        # partially-run migration passes the already-prefixed name back through
        # the convention (for example ck_registration_ck_registration_status).
        legacy_name = f"ck_{table}_{canonical_name}"
        allowed_names = {canonical_name, legacy_name}

        def check_name(check: dict[str, Any]) -> str | None:
            value = check.get("name")
            return str(value) if value else None

        def check_sql(check: dict[str, Any]) -> str:
            return normalize_check_sql(str(check.get("sqltext", "")))

        # A status check with an unrecognized name or expression is not safe to
        # delete or preserve implicitly.  Other non-status checks on these
        # tables remain outside this compatibility hook.
        for check in checks:
            name = check_name(check)
            sql = check_sql(check)
            if _contains_column_identifier(str(check.get("sqltext", "")), "status") and (
                name not in allowed_names or sql not in expected_sql | target_sql
            ):
                raise RuntimeError(
                    f"cannot prepare 0010 {table}.status: unknown or conflicting CHECK definition"
                )

        for name in allowed_names:
            if sum(check_name(check) == name for check in checks) > 1:
                raise RuntimeError(
                    f"cannot prepare 0010 {table}.status: duplicate CHECK name is ambiguous"
                )

        named = {name: check for check in checks if (name := check_name(check)) in allowed_names}
        canonical = named.get(canonical_name)
        legacy = named.get(legacy_name)
        if canonical is not None and check_sql(canonical) not in expected_sql | target_sql:
            raise RuntimeError(f"cannot prepare 0010 {table}.status: conflicting CHECK definition")
        if legacy is not None and check_sql(legacy) not in expected_sql:
            raise RuntimeError(f"cannot prepare 0010 {table}.status: conflicting legacy CHECK definition")
        if canonical is None and legacy is None:
            raise RuntimeError(f"cannot prepare 0010 {table}.status: known CHECK is missing")

        quoted_table = quote_identifier(connection, table)
        if canonical is None and legacy is not None:
            plans.append(
                f"ALTER TABLE {quoted_table} DROP CHECK {quote_identifier(connection, legacy_name)}, "
                f"ADD CONSTRAINT {quote_identifier(connection, canonical_name)} CHECK ({old_expression})"
            )
        elif legacy is not None:
            plans.append(
                f"ALTER TABLE {quoted_table} DROP CHECK {quote_identifier(connection, legacy_name)}"
            )

    # Do not partially normalize one table and then discover an ambiguous
    # check on another table.
    for statement in plans:
        connection.exec_driver_sql(statement)


def validate_existing_fencing_column(
    table_name: str,
    column_name: str,
    column: dict[str, Any],
) -> None:
    """Fail closed unless an existing 0010 fencing column is compatible."""

    if column_name == "attempt":
        valid = _is_mysql_integer_32(column.get("type")) and column.get("nullable") is False
    elif column_name == "dispatch_id":
        column_type = column.get("type")
        valid = _is_varchar_96(column_type) and column.get("nullable") is True
    else:
        raise ValueError(f"unexpected fencing compatibility column: {column_name}")

    # 0010 removes the temporary default after backfilling.  Existing columns
    # must already have the same final shape before the add operation is
    # skipped; silently accepting a default would hide schema drift.
    # A partial 0010 run may have completed ADD COLUMN but failed before its
    # final ALTER COLUMN removed the temporary ``DEFAULT 1``.  That value is
    # the frozen migration's own backfill default and is safe to normalize on
    # retry; any other default indicates schema drift.
    default = column.get("default")
    default_text = str(default).strip("'\"") if default is not None else None
    valid = valid and (default is None or (column_name == "attempt" and default_text in {"1", "1.0"}))
    if not valid:
        raise RuntimeError(
            f"{table_name}.{column_name} exists but is incompatible with frozen 0010"
        )


def validate_existing_deployment_column(column_name: str, column: dict[str, Any]) -> None:
    """Backward-compatible wrapper for deployment-specific callers."""

    validate_existing_fencing_column("deployment", column_name, column)
