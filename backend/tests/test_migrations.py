from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import re

from alembic import command
from alembic.config import Config

def _config() -> Config:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic" / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", "mysql://user:password@localhost/chrom_platform")
    return config


def test_m1_migration_is_frozen_and_declares_expected_tables() -> None:
    source = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0001_m1_persistence.py"
    text = source.read_text(encoding="utf-8")
    assert "Base.metadata" not in text
    assert "infrastructure.metadata" not in text
    assert "copy(" not in text
    expected_tables = {
        "project", "artifact", "structure", "job", "job_attempt", "job_event",
        "outbox_event", "idempotency_record", "execution_ledger", "confirmation", "audit_event",
    }
    for table in expected_tables:
        assert f'"{table}"' in text
        assert f'op.create_table(' in text
    for marker in (
        '"uq_idempotency_scope_key"',
        '"fk_structure_project_artifact"',
        '"ck_confirmation_confirmation_status"',
        '"ix_job_event_job_seq"',
    ):
        assert marker in text


def test_frozen_migration_columns_and_checks_match_m1_contract() -> None:
    source = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0001_m1_persistence.py"
    text = source.read_text(encoding="utf-8")

    # This is intentionally a literal contract snapshot. It does not derive
    # expectations from current ORM metadata, so a future ORM edit cannot make
    # this test silently accept drift in the deployed baseline.
    nullable = {
        "project": {"id": False, "slug": False, "name": False, "status": False, "tenant_id": True, "revision": False, "created_at": False, "updated_at": False},
        "artifact": {"id": False, "project_id": False, "kind": False, "title": True, "media_type": False, "size_bytes": False, "sha256": True, "storage_key": False, "status": False, "scan_status": False, "metadata_json": False, "created_by": False, "retention_until": True, "created_at": False, "updated_at": False},
        "structure": {"id": False, "project_id": False, "schema_version": False, "original_input": False, "canonical_smiles": False, "is_valid": False, "has_stereochemistry": False, "stereochemistry_status": False, "stereochemistry_warnings": False, "snapshot_hash": False, "source_artifact_id": True, "created_at": False},
        "job": {"id": False, "project_id": False, "owner_id": False, "kind": False, "resource_ref": False, "status": False, "attempt": False, "progress": False, "cancelable": False, "retryable": False, "resume_mode": False, "created_at": False, "updated_at": False, "started_at": True, "finished_at": True, "deadline_at": True, "result_ref": True, "error": True, "revision": False, "etag": False},
        "job_attempt": {"id": False, "job_id": False, "attempt": False, "status": False, "resume_mode": False, "execution_token": True, "fencing_token": True, "lease_until": True, "checkpoint_ref": True, "started_at": True, "finished_at": True, "error": True, "result_ref": True},
        "job_event": {"id": False, "job_id": False, "stream_id": False, "seq": False, "type": False, "schema_version": False, "data": False, "timestamp": False},
        "outbox_event": {"dispatch_id": False, "aggregate_type": False, "aggregate_id": False, "event_type": False, "payload": False, "status": False, "attempts": False, "available_at": False, "dispatched_at": True, "last_error": True, "created_at": False},
        "idempotency_record": {"id": False, "actor_id": False, "project_id": False, "method": False, "route_identity": False, "key": False, "payload_hash": False, "status": False, "error_code": True, "result_resource_ref": True, "result_status_code": True, "expires_at": False, "created_at": False, "completed_at": True},
        "execution_ledger": {"id": False, "operation_key": False, "operation_type": False, "status": False, "job_id": True, "result_ref": True, "error": True, "started_at": False, "finished_at": True, "updated_at": False},
        "confirmation": {"id": False, "project_id": False, "actor_id": False, "resource_ref": False, "action": False, "revision": False, "config_hash": False, "binding_hash": False, "policy_version": False, "summary_snapshot": False, "status": False, "created_at": False, "expires_at": False, "consumed_at": True, "consumed_result_ref": True, "consumed_request_hash": True, "revoked_at": True},
        "audit_event": {"id": False, "project_id": True, "actor_id": False, "auth_method": False, "interaction": False, "request_id": False, "action": False, "resource_type": True, "resource_id": True, "resource_ref": False, "before_summary": False, "after_summary": False, "confirmation_id": True, "config_hash": True, "job_id": True, "agent_run_id": True, "result": False, "timestamp": False},
    }
    checks = {
        "project": ["status IN ('ACTIVE','ARCHIVED')", "revision >= 1"],
        "artifact": ["size_bytes > 0", "sha256 IS NULL OR CHAR_LENGTH(sha256) = 64", "DELETED", "REJECTED"],
        "structure": ["is_valid = 1", "stereochemistry_status IN ('specified','unspecified','not_applicable')", "CHAR_LENGTH(snapshot_hash) = 64"],
        "job": ["attempt >= 1", "revision >= 1", "CANCEL_REQUESTED", "resume_mode IN ('none','restart','checkpoint')"],
        "job_attempt": ["attempt >= 1", "CANCEL_REQUESTED", "resume_mode IN ('none','restart','checkpoint')"],
        "job_event": ["seq >= 0"],
        "outbox_event": ["attempts >= 0", "status IN ('PENDING','PROCESSING','SENT','FAILED')"],
        "idempotency_record": ["error_code IS NULL OR error_code REGEXP", "payload_hash", "status IN ('IN_PROGRESS','COMPLETED','FAILED')"],
        "execution_ledger": ["status IN ('STARTED','SUCCEEDED','FAILED','UNKNOWN')"],
        "confirmation": ["expires_at > created_at", "status IN ('CONFIRMED','CONSUMED','EXPIRED','INVALIDATED','REVOKED')"],
        "audit_event": ["interaction IN ('human','agent_tool','worker','service')", "result IN ('SUCCEEDED','FAILED')"],
    }
    for table, columns in nullable.items():
        match = re.search(rf'op\.create_table\(\s*"{table}"(?P<body>.*?)(?=\n\s*op\.create_table\(|\n\s*def downgrade)', text, re.S)
        assert match, table
        body = match.group("body")
        for column, is_nullable in columns.items():
            assert re.search(rf'sa\.Column\("{column}".*?nullable={is_nullable!s}', body, re.S), f"{table}.{column}"
        for check in checks[table]:
            assert check in body, f"{table}: {check}"


def test_m1_migration_generates_mysql_offline_sql() -> None:
    output = StringIO()
    with redirect_stdout(output):
        command.upgrade(_config(), "0001_m1_persistence", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE project" in sql
    assert "CREATE TABLE execution_ledger" in sql
    assert "ENGINE=InnoDB" in sql
    assert "CHARSET=utf8mb4" in sql
    assert "CREATE TABLE sqlite" not in sql.lower()


def test_m4_downgrade_drops_children_before_parents_in_mysql_sql() -> None:
    output = StringIO()
    with redirect_stdout(output):
        command.downgrade(_config(), "0006_m4_assistant:0005_m3_model_center", sql=True)
    sql = output.getvalue().upper()
    tables = (
        "AGENT_PENDING_CONFIRMATION", "LLM_TEST_RESULT", "CONTEXT_SNAPSHOT",
        "LLM_CALL", "LLM_CONFIGURATION", "TOOL_CALL", "AGENT_EVENT",
        "AGENT_ATTEMPT", "MESSAGE", "AGENT_RUN", "CONVERSATION",
    )
    positions = [sql.index(f"DROP TABLE {table}") for table in tables]
    assert positions == sorted(positions)
    assert "DROP TABLE SQLITE" not in sql


def test_batch_resolved_snapshot_migration_generates_mysql_sql() -> None:
    output = StringIO()
    with redirect_stdout(output):
        command.upgrade(_config(), "0014_batch_resolved_snapshot_export", sql=True)
    sql = output.getvalue().lower()
    assert "alter table batch_prediction add column submitted_at datetime" in sql
    assert "alter table batch_validation_item add column resolved_input_snapshot json" in sql
    assert "alter table batch_validation_item add column resolved_input_hash varchar(64)" in sql
    assert "alter table batch_validation add column resolved_input_hash varchar(64)" in sql
    assert "alter table batch_retry_plan add column model_snapshot_hash varchar(64)" in sql
    assert "alter table batch_export add column source_snapshot json" in sql
    assert "alter table batch_export add column artifact_id varchar(96)" in sql
