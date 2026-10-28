"""Frozen MySQL M1 persistence baseline.

This revision intentionally contains explicit DDL. It must not import the
current SQLAlchemy metadata: once deployed, a later model edit belongs in a
new revision and must never rewrite this schema.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_m1_persistence"
down_revision = None
branch_labels = None
depends_on = None

_MYSQL = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


def upgrade() -> None:
    op.create_table(
        "project",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("tenant_id", sa.String(96), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_project"),
        sa.UniqueConstraint("slug", name="uq_project_slug"),
        sa.CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="ck_project_project_status"),
        sa.CheckConstraint("revision >= 1", name="ck_project_project_revision_positive"),
        **_MYSQL,
    )

    op.create_table(
        "artifact",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("scan_status", sa.String(20), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(96), nullable=False),
        sa.Column("retention_until", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_artifact"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_artifact_project_id_project", ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", "id", name="uq_artifact_project_id"),
        sa.CheckConstraint("size_bytes > 0", name="ck_artifact_artifact_size_positive"),
        sa.CheckConstraint("sha256 IS NULL OR CHAR_LENGTH(sha256) = 64", name="ck_artifact_artifact_sha256_length"),
        sa.CheckConstraint("status IN ('CREATED','RECEIVED','VALIDATING','AVAILABLE','QUARANTINED','FAILED','ARCHIVED','DELETING','DELETED','EXPIRED')", name="ck_artifact_artifact_status"),
        sa.CheckConstraint("scan_status IN ('NOT_CONFIGURED','PENDING','PASSED','REJECTED','FAILED')", name="ck_artifact_artifact_scan_status"),
        **_MYSQL,
    )
    op.create_index("ix_artifact_project_status_created", "artifact", ["project_id", "status", "created_at"])
    op.create_index("ix_artifact_project_sha256", "artifact", ["project_id", "sha256"])

    op.create_table(
        "structure",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("original_input", sa.JSON(), nullable=False),
        sa.Column("canonical_smiles", sa.Text(), nullable=False),
        sa.Column("is_valid", sa.Boolean(), nullable=False),
        sa.Column("has_stereochemistry", sa.Boolean(), nullable=False),
        sa.Column("stereochemistry_status", sa.String(20), nullable=False),
        sa.Column("stereochemistry_warnings", sa.JSON(), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("source_artifact_id", sa.String(96), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_structure"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_structure_project_id_project", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id", "source_artifact_id"], ["artifact.project_id", "artifact.id"], name="fk_structure_project_artifact", ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", "snapshot_hash", name="uq_structure_project_snapshot_hash"),
        sa.CheckConstraint("is_valid = 1", name="ck_structure_structure_valid"),
        sa.CheckConstraint("CHAR_LENGTH(snapshot_hash) = 64", name="ck_structure_structure_snapshot_hash_length"),
        sa.CheckConstraint("stereochemistry_status IN ('specified','unspecified','not_applicable')", name="ck_structure_structure_stereochemistry_status"),
        **_MYSQL,
    )
    op.create_index("ix_structure_project_created", "structure", ["project_id", "created_at"])

    op.create_table(
        "job",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("owner_id", sa.String(96), nullable=False),
        sa.Column("kind", sa.String(96), nullable=False),
        sa.Column("resource_ref", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("progress", sa.JSON(), nullable=False),
        sa.Column("cancelable", sa.Boolean(), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("resume_mode", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("deadline_at", sa.DateTime(), nullable=True),
        sa.Column("result_ref", sa.JSON(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("etag", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_job"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_job_project_id_project", ondelete="RESTRICT"),
        sa.CheckConstraint("attempt >= 1", name="ck_job_job_attempt_positive"),
        sa.CheckConstraint("revision >= 1", name="ck_job_job_revision_positive"),
        sa.CheckConstraint("status IN ('QUEUED','RUNNING','CANCEL_REQUESTED','SUCCEEDED','PARTIAL','FAILED','CANCELED','TIMED_OUT','INTERRUPTED')", name="ck_job_job_status"),
        sa.CheckConstraint("resume_mode IN ('none','restart','checkpoint')", name="ck_job_job_resume_mode"),
        **_MYSQL,
    )
    op.create_index("ix_job_project_status_created", "job", ["project_id", "status", "created_at"])

    op.create_table(
        "job_attempt",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("job_id", sa.String(96), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("resume_mode", sa.String(16), nullable=False),
        sa.Column("execution_token", sa.String(128), nullable=True),
        sa.Column("fencing_token", sa.BigInteger(), nullable=True),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("checkpoint_ref", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("result_ref", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_job_attempt"),
        sa.ForeignKeyConstraint(["job_id"], ["job.id"], name="fk_job_attempt_job_id_job", ondelete="RESTRICT"),
        sa.UniqueConstraint("job_id", "attempt", name="uq_job_attempt_job_attempt"),
        sa.CheckConstraint("attempt >= 1", name="ck_job_attempt_job_attempt_number_positive"),
        sa.CheckConstraint("resume_mode IN ('none','restart','checkpoint')", name="ck_job_attempt_job_attempt_resume_mode"),
        sa.CheckConstraint("status IN ('QUEUED','RUNNING','CANCEL_REQUESTED','SUCCEEDED','PARTIAL','FAILED','CANCELED','TIMED_OUT','INTERRUPTED')", name="ck_job_attempt_job_attempt_status"),
        **_MYSQL,
    )
    op.create_index("ix_job_attempt_job_status", "job_attempt", ["job_id", "status"])

    op.create_table(
        "job_event",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("job_id", sa.String(96), nullable=False),
        sa.Column("stream_id", sa.String(96), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("type", sa.String(96), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_job_event"),
        sa.ForeignKeyConstraint(["job_id"], ["job.id"], name="fk_job_event_job_id_job", ondelete="RESTRICT"),
        sa.UniqueConstraint("stream_id", "seq", name="uq_job_event_stream_seq"),
        sa.UniqueConstraint("job_id", "seq", name="uq_job_event_job_seq"),
        sa.CheckConstraint("seq >= 0", name="ck_job_event_job_event_seq_nonnegative"),
        **_MYSQL,
    )
    op.create_index("ix_job_event_job_seq", "job_event", ["job_id", "seq"])

    op.create_table(
        "outbox_event",
        sa.Column("dispatch_id", sa.String(96), nullable=False),
        sa.Column("aggregate_type", sa.String(64), nullable=False),
        sa.Column("aggregate_id", sa.String(96), nullable=False),
        sa.Column("event_type", sa.String(96), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("dispatch_id", name="pk_outbox_event"),
        sa.CheckConstraint("status IN ('PENDING','PROCESSING','SENT','FAILED')", name="ck_outbox_event_outbox_status"),
        sa.CheckConstraint("attempts >= 0", name="ck_outbox_event_outbox_attempts_nonnegative"),
        **_MYSQL,
    )
    op.create_index("ix_outbox_status_available", "outbox_event", ["status", "available_at"])
    op.create_index("ix_outbox_aggregate", "outbox_event", ["aggregate_type", "aggregate_id"])

    op.create_table(
        "idempotency_record",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("actor_id", sa.String(96), nullable=False),
        sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("route_identity", sa.String(128), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_code", sa.String(96), nullable=True),
        sa.Column("result_resource_ref", sa.JSON(), nullable=True),
        sa.Column("result_status_code", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_idempotency_record"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_idempotency_record_project_id_project", ondelete="RESTRICT"),
        sa.UniqueConstraint("actor_id", "project_id", "method", "route_identity", "key", name="uq_idempotency_scope_key"),
        sa.CheckConstraint("status IN ('IN_PROGRESS','COMPLETED','FAILED')", name="ck_idempotency_record_idempotency_status"),
        sa.CheckConstraint("error_code IS NULL OR error_code REGEXP '^[A-Z][A-Z0-9_]*$'", name="ck_idempotency_record_idempotency_error_code_format"),
        sa.CheckConstraint("CHAR_LENGTH(payload_hash) = 64", name="ck_idempotency_record_idempotency_payload_hash_length"),
        **_MYSQL,
    )
    op.create_index("ix_idempotency_expires", "idempotency_record", ["expires_at"])

    op.create_table(
        "execution_ledger",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("operation_key", sa.String(255), nullable=False),
        sa.Column("operation_type", sa.String(96), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("job_id", sa.String(96), nullable=True),
        sa.Column("result_ref", sa.JSON(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_execution_ledger"),
        sa.ForeignKeyConstraint(["job_id"], ["job.id"], name="fk_execution_ledger_job_id_job", ondelete="RESTRICT"),
        sa.UniqueConstraint("operation_key", name="uq_execution_ledger_operation_key"),
        sa.CheckConstraint("status IN ('STARTED','SUCCEEDED','FAILED','UNKNOWN')", name="ck_execution_ledger_execution_ledger_status"),
        **_MYSQL,
    )
    op.create_index("ix_execution_ledger_job", "execution_ledger", ["job_id"])

    op.create_table(
        "confirmation",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("actor_id", sa.String(96), nullable=False),
        sa.Column("resource_ref", sa.JSON(), nullable=False),
        sa.Column("action", sa.String(160), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("binding_hash", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("summary_snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("consumed_result_ref", sa.JSON(), nullable=True),
        sa.Column("consumed_request_hash", sa.String(64), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_confirmation"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_confirmation_project_id_project", ondelete="RESTRICT"),
        sa.CheckConstraint("revision >= 1", name="ck_confirmation_confirmation_revision_positive"),
        sa.CheckConstraint("CHAR_LENGTH(config_hash) = 64", name="ck_confirmation_confirmation_config_hash_length"),
        sa.CheckConstraint("CHAR_LENGTH(binding_hash) = 64", name="ck_confirmation_confirmation_binding_hash_length"),
        sa.CheckConstraint("status IN ('CONFIRMED','CONSUMED','EXPIRED','INVALIDATED','REVOKED')", name="ck_confirmation_confirmation_status"),
        sa.CheckConstraint("expires_at > created_at", name="ck_confirmation_confirmation_expiry_after_creation"),
        **_MYSQL,
    )
    op.create_index("ix_confirmation_resource", "confirmation", ["project_id", "action", "status"])

    op.create_table(
        "audit_event",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("project_id", sa.String(96), nullable=True),
        sa.Column("actor_id", sa.String(96), nullable=False),
        sa.Column("auth_method", sa.String(32), nullable=False),
        sa.Column("interaction", sa.String(16), nullable=False),
        sa.Column("request_id", sa.String(96), nullable=False),
        sa.Column("action", sa.String(160), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=True),
        sa.Column("resource_id", sa.String(96), nullable=True),
        sa.Column("resource_ref", sa.JSON(), nullable=False),
        sa.Column("before_summary", sa.JSON(), nullable=False),
        sa.Column("after_summary", sa.JSON(), nullable=False),
        sa.Column("confirmation_id", sa.String(96), nullable=True),
        sa.Column("config_hash", sa.String(64), nullable=True),
        sa.Column("job_id", sa.String(96), nullable=True),
        sa.Column("agent_run_id", sa.String(96), nullable=True),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_event"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_audit_event_project_id_project", ondelete="RESTRICT"),
        sa.CheckConstraint("interaction IN ('human','agent_tool','worker','service')", name="ck_audit_event_audit_interaction"),
        sa.CheckConstraint("result IN ('SUCCEEDED','FAILED')", name="ck_audit_event_audit_result"),
        **_MYSQL,
    )
    op.create_index("ix_audit_event_project_timestamp", "audit_event", ["project_id", "timestamp"])
    op.create_index("ix_audit_event_request_id", "audit_event", ["request_id"])


def downgrade() -> None:
    for name in (
        "audit_event", "confirmation", "execution_ledger", "idempotency_record",
        "outbox_event", "job_event", "job_attempt", "job", "structure", "artifact", "project",
    ):
        op.drop_table(name)
