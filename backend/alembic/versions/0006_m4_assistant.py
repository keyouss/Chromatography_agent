"""M4 Assistant conversations, runs, traces and recovery state.

Rows deliberately contain only redacted summaries and server-side references.
Provider secrets and hidden reasoning are never columns in this migration.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0006_m4_assistant"
down_revision = "0005_m3_model_center"
branch_labels = None
depends_on = None

_MYSQL = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"}


def upgrade() -> None:
    op.create_table("conversation",
        sa.Column("id", sa.String(96), nullable=False), sa.Column("project_id", sa.String(96), nullable=False), sa.Column("owner_id", sa.String(96), nullable=False), sa.Column("title", sa.String(120), nullable=False), sa.Column("mode", sa.String(16), nullable=False), sa.Column("status", sa.String(16), nullable=False), sa.Column("current_run_id", sa.String(96), nullable=True), sa.Column("last_message_at", sa.DateTime(), nullable=True), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False), sa.Column("revision", sa.Integer(), nullable=False), sa.Column("etag", sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_conversation"), sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_conversation_project_id_project", ondelete="RESTRICT"), sa.CheckConstraint("mode IN ('knowledge','business')", name="ck_conversation_mode"), sa.CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="ck_conversation_status"), sa.CheckConstraint("revision >= 1", name="ck_conversation_revision_positive"), **_MYSQL)
    op.create_index("ix_conversation_project_owner_updated", "conversation", ["project_id", "owner_id", "updated_at", "id"])

    op.create_table("agent_run",
        sa.Column("id", sa.String(96), nullable=False), sa.Column("conversation_id", sa.String(96), nullable=False), sa.Column("project_id", sa.String(96), nullable=False), sa.Column("input_message_id", sa.String(96), nullable=False), sa.Column("mode", sa.String(16), nullable=False), sa.Column("status", sa.String(32), nullable=False), sa.Column("attempt", sa.Integer(), nullable=False), sa.Column("dispatch_id", sa.String(96), nullable=True), sa.Column("provider_configuration_id", sa.String(96), nullable=True), sa.Column("provider_configuration_revision", sa.String(128), nullable=True), sa.Column("model_id", sa.String(128), nullable=False), sa.Column("prompt_version", sa.String(64), nullable=False), sa.Column("toolset_version", sa.String(64), nullable=False), sa.Column("policy_version", sa.String(64), nullable=False), sa.Column("pending_actions", sa.JSON(), nullable=False), sa.Column("related_resources", sa.JSON(), nullable=False), sa.Column("usage", sa.JSON(), nullable=False), sa.Column("last_event_seq", sa.BigInteger(), nullable=False), sa.Column("error", sa.JSON(), nullable=True), sa.Column("started_at", sa.DateTime(), nullable=True), sa.Column("finished_at", sa.DateTime(), nullable=True), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False), sa.Column("revision", sa.Integer(), nullable=False), sa.Column("etag", sa.String(128), nullable=False), sa.Column("fencing_token", sa.Integer(), nullable=False), sa.Column("lease_until", sa.DateTime(), nullable=True), sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_agent_run"), sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"], name="fk_agent_run_conversation_id_conversation", ondelete="RESTRICT"), sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_agent_run_project_id_project", ondelete="RESTRICT"), sa.CheckConstraint("mode IN ('knowledge','business')", name="ck_agent_run_mode"), sa.CheckConstraint("attempt >= 1", name="ck_agent_run_attempt_positive"), sa.CheckConstraint("last_event_seq >= 0", name="ck_agent_run_last_event_seq_nonnegative"), sa.CheckConstraint("revision >= 1", name="ck_agent_run_revision_positive"), sa.CheckConstraint("status IN ('QUEUED','RUNNING','WAITING_CONFIRMATION','CANCEL_REQUESTED','SUCCEEDED','FAILED','CANCELED','TIMED_OUT','INTERRUPTED')", name="ck_agent_run_status"), **_MYSQL)
    op.create_index("ix_agent_run_conversation_created", "agent_run", ["conversation_id", "created_at", "id"])
    op.create_index("ix_agent_run_project_status", "agent_run", ["project_id", "status", "updated_at"])

    op.create_table("message",
        sa.Column("id", sa.String(96), nullable=False), sa.Column("conversation_id", sa.String(96), nullable=False), sa.Column("seq", sa.BigInteger(), nullable=False), sa.Column("role", sa.String(16), nullable=False), sa.Column("agent_run_id", sa.String(96), nullable=True), sa.Column("status", sa.String(16), nullable=False), sa.Column("blocks", sa.JSON(), nullable=False), sa.Column("attachment_ids", sa.JSON(), nullable=False), sa.Column("client_message_id", sa.String(128), nullable=True), sa.Column("payload_hash", sa.String(64), nullable=True), sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_message"), sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"], name="fk_message_conversation_id_conversation", ondelete="RESTRICT"), sa.ForeignKeyConstraint(["agent_run_id"], ["agent_run.id"], name="fk_message_agent_run_id_agent_run", ondelete="RESTRICT"), sa.UniqueConstraint("conversation_id", "seq", name="uq_message_conversation_seq"), sa.UniqueConstraint("conversation_id", "client_message_id", name="uq_message_conversation_client_message"), sa.CheckConstraint("seq >= 1", name="ck_message_seq_positive"), sa.CheckConstraint("role IN ('user','assistant')", name="ck_message_role"), sa.CheckConstraint("status IN ('FINAL','PARTIAL','FAILED')", name="ck_message_status"), **_MYSQL)
    op.create_index("ix_message_conversation_created", "message", ["conversation_id", "created_at", "id"])
    op.create_foreign_key("fk_agent_run_input_message_id_message", "agent_run", "message", ["input_message_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_conversation_current_run_id_agent_run", "conversation", "agent_run", ["current_run_id"], ["id"], ondelete="RESTRICT")

    op.create_table("agent_attempt",
        sa.Column("id", sa.String(96), nullable=False), sa.Column("agent_run_id", sa.String(96), nullable=False), sa.Column("attempt", sa.Integer(), nullable=False), sa.Column("status", sa.String(32), nullable=False), sa.Column("dispatch_id", sa.String(96), nullable=True), sa.Column("execution_token", sa.String(128), nullable=True), sa.Column("fencing_token", sa.Integer(), nullable=True), sa.Column("lease_until", sa.DateTime(), nullable=True), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("started_at", sa.DateTime(), nullable=True), sa.Column("finished_at", sa.DateTime(), nullable=True), sa.Column("error", sa.JSON(), nullable=True), sa.Column("result_ref", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_agent_attempt"), sa.ForeignKeyConstraint(["agent_run_id"], ["agent_run.id"], name="fk_agent_attempt_agent_run_id_agent_run", ondelete="RESTRICT"), sa.UniqueConstraint("agent_run_id", "attempt", name="uq_agent_attempt_run_attempt"), sa.CheckConstraint("attempt >= 1", name="ck_agent_attempt_attempt_positive"), sa.CheckConstraint("status IN ('QUEUED','RUNNING','WAITING_CONFIRMATION','CANCEL_REQUESTED','SUCCEEDED','FAILED','CANCELED','TIMED_OUT','INTERRUPTED')", name="ck_agent_attempt_status"), **_MYSQL)
    op.create_index("ix_agent_attempt_lease", "agent_attempt", ["status", "lease_until"])

    op.create_table("agent_event",
        sa.Column("id", sa.String(96), nullable=False), sa.Column("stream_type", sa.String(32), nullable=False), sa.Column("stream_id", sa.String(96), nullable=False), sa.Column("seq", sa.BigInteger(), nullable=False), sa.Column("type", sa.String(128), nullable=False), sa.Column("schema_version", sa.String(64), nullable=False), sa.Column("data", sa.JSON(), nullable=False), sa.Column("timestamp", sa.DateTime(), nullable=False), sa.Column("agent_run_id", sa.String(96), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_agent_event"), sa.UniqueConstraint("stream_type", "stream_id", "seq", name="uq_agent_event_stream_seq"), sa.CheckConstraint("seq >= 0", name="ck_agent_event_seq_nonnegative"), **_MYSQL)
    op.create_index("ix_agent_event_stream_timestamp", "agent_event", ["stream_type", "stream_id", "timestamp"])

    op.create_table("tool_call",
        sa.Column("id", sa.String(96), nullable=False), sa.Column("agent_run_id", sa.String(96), nullable=False), sa.Column("agent_attempt_id", sa.String(96), nullable=False), sa.Column("provider_tool_call_id", sa.String(128), nullable=False), sa.Column("tool_name", sa.String(128), nullable=False), sa.Column("tool_schema_version", sa.String(64), nullable=False), sa.Column("arguments_hash", sa.String(64), nullable=True), sa.Column("binding_hash", sa.String(64), nullable=True), sa.Column("status", sa.String(32), nullable=False), sa.Column("arguments_summary", sa.JSON(), nullable=False), sa.Column("resource_ref", sa.JSON(), nullable=True), sa.Column("confirmation_id", sa.String(96), nullable=True), sa.Column("pending_confirmation", sa.JSON(), nullable=True), sa.Column("result_summary", sa.JSON(), nullable=True), sa.Column("job_id", sa.String(96), nullable=True), sa.Column("error", sa.JSON(), nullable=True), sa.Column("started_at", sa.DateTime(), nullable=True), sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_tool_call"), sa.ForeignKeyConstraint(["agent_run_id"], ["agent_run.id"], name="fk_tool_call_agent_run_id_agent_run", ondelete="RESTRICT"), sa.ForeignKeyConstraint(["agent_attempt_id"], ["agent_attempt.id"], name="fk_tool_call_agent_attempt_id_agent_attempt", ondelete="RESTRICT"), sa.CheckConstraint("status IN ('PROPOSED','VALIDATING','WAITING_CONFIRMATION','RUNNING','SUCCEEDED','FAILED','REJECTED','INTERRUPTED')", name="ck_tool_call_status"), sa.CheckConstraint("arguments_hash IS NULL OR CHAR_LENGTH(arguments_hash) = 64", name="ck_tool_call_arguments_hash_length"), sa.CheckConstraint("binding_hash IS NULL OR CHAR_LENGTH(binding_hash) = 64", name="ck_tool_call_binding_hash_length"), **_MYSQL)
    op.create_index("ix_tool_call_run_started", "tool_call", ["agent_run_id", "started_at", "id"])

    op.create_table("llm_configuration",
        sa.Column("id", sa.String(96), nullable=False), sa.Column("project_id", sa.String(96), nullable=False), sa.Column("revision", sa.String(128), nullable=False), sa.Column("url", sa.String(1000), nullable=False), sa.Column("model_id", sa.String(128), nullable=False), sa.Column("key_reference", sa.String(255), nullable=False), sa.Column("status", sa.String(16), nullable=False), sa.Column("settings", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_llm_configuration"), sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_llm_configuration_project_id_project", ondelete="RESTRICT"), sa.UniqueConstraint("project_id", "revision", name="uq_llm_configuration_project_revision"), sa.CheckConstraint("status IN ('CANDIDATE','ACTIVE','RETIRED')", name="ck_llm_configuration_status"), **_MYSQL)
    op.create_index("ix_llm_configuration_project_status", "llm_configuration", ["project_id", "status", "created_at"])

    op.create_table("llm_call",
        sa.Column("id", sa.String(96), nullable=False), sa.Column("agent_run_id", sa.String(96), nullable=False), sa.Column("agent_attempt_id", sa.String(96), nullable=False), sa.Column("request_index", sa.Integer(), nullable=False), sa.Column("transport_attempt", sa.Integer(), nullable=False), sa.Column("provider_configuration_id", sa.String(96), nullable=True), sa.Column("provider_configuration_revision", sa.String(128), nullable=True), sa.Column("model_id", sa.String(128), nullable=False), sa.Column("status", sa.String(16), nullable=False), sa.Column("model_parameters_summary", sa.JSON(), nullable=False), sa.Column("usage", sa.JSON(), nullable=False), sa.Column("estimates", sa.JSON(), nullable=False), sa.Column("latency_ms", sa.Integer(), nullable=True), sa.Column("response_summary", sa.JSON(), nullable=True), sa.Column("error", sa.JSON(), nullable=True), sa.Column("logical_context_snapshot_id", sa.String(96), nullable=True), sa.Column("provider_request_snapshot_id", sa.String(96), nullable=True), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_llm_call"), sa.ForeignKeyConstraint(["agent_run_id"], ["agent_run.id"], name="fk_llm_call_agent_run_id_agent_run", ondelete="RESTRICT"), sa.ForeignKeyConstraint(["agent_attempt_id"], ["agent_attempt.id"], name="fk_llm_call_agent_attempt_id_agent_attempt", ondelete="RESTRICT"), sa.CheckConstraint("request_index >= 1", name="ck_llm_call_request_index_positive"), sa.CheckConstraint("transport_attempt >= 1", name="ck_llm_call_transport_attempt_positive"), sa.CheckConstraint("status IN ('CREATED','RUNNING','SUCCEEDED','FAILED','INTERRUPTED')", name="ck_llm_call_status"), **_MYSQL)
    op.create_index("ix_llm_call_run_request", "llm_call", ["agent_run_id", "request_index", "transport_attempt"])

    op.create_table("context_snapshot",
        sa.Column("id", sa.String(96), nullable=False), sa.Column("llm_call_id", sa.String(96), nullable=False), sa.Column("project_id", sa.String(96), nullable=False), sa.Column("view", sa.String(32), nullable=False), sa.Column("capture_state", sa.String(16), nullable=False), sa.Column("capture_mode", sa.String(32), nullable=False), sa.Column("payload_schema_version", sa.String(128), nullable=False), sa.Column("payload", sa.JSON(), nullable=True), sa.Column("sources", sa.JSON(), nullable=False), sa.Column("redactions", sa.JSON(), nullable=False), sa.Column("truncated", sa.Boolean(), nullable=False), sa.Column("hash_scope", sa.String(64), nullable=False), sa.Column("sha256", sa.String(64), nullable=True), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_context_snapshot"), sa.ForeignKeyConstraint(["llm_call_id"], ["llm_call.id"], name="fk_context_snapshot_llm_call_id_llm_call", ondelete="RESTRICT"), sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_context_snapshot_project_id_project", ondelete="RESTRICT"), sa.CheckConstraint("capture_state IN ('AVAILABLE','NOT_CAPTURED','EXPIRED','REDACTED')", name="ck_context_snapshot_capture_state"), sa.CheckConstraint("sha256 IS NULL OR CHAR_LENGTH(sha256) = 64", name="ck_context_snapshot_sha256_length"), **_MYSQL)
    op.create_index("ix_context_snapshot_project_created", "context_snapshot", ["project_id", "created_at"])

    op.create_table("llm_test_result",
        sa.Column("id", sa.String(96), nullable=False), sa.Column("configuration_id", sa.String(96), nullable=False), sa.Column("configuration_revision", sa.String(128), nullable=False), sa.Column("status", sa.String(16), nullable=False), sa.Column("checks", sa.JSON(), nullable=False), sa.Column("error", sa.JSON(), nullable=True), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_llm_test_result"), sa.ForeignKeyConstraint(["configuration_id"], ["llm_configuration.id"], name="fk_llm_test_result_configuration_id_llm_configuration", ondelete="RESTRICT"), sa.CheckConstraint("status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')", name="ck_llm_test_result_status"), **_MYSQL)
    op.create_index("ix_llm_test_result_configuration_created", "llm_test_result", ["configuration_id", "created_at"])

    op.create_table("agent_pending_confirmation",
        sa.Column("id", sa.String(96), nullable=False), sa.Column("agent_run_id", sa.String(96), nullable=False), sa.Column("tool_call_id", sa.String(96), nullable=False), sa.Column("project_id", sa.String(96), nullable=False), sa.Column("confirmation_id", sa.String(96), nullable=True), sa.Column("resource_ref", sa.JSON(), nullable=False), sa.Column("action", sa.String(128), nullable=False), sa.Column("revision", sa.Integer(), nullable=False), sa.Column("config_hash", sa.String(64), nullable=False), sa.Column("binding_hash", sa.String(64), nullable=False), sa.Column("schema_version", sa.String(64), nullable=False), sa.Column("normalized_args", sa.JSON(), nullable=False), sa.Column("expires_at", sa.DateTime(), nullable=True), sa.Column("status", sa.String(16), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_agent_pending_confirmation"), sa.ForeignKeyConstraint(["agent_run_id"], ["agent_run.id"], name="fk_agent_pending_confirmation_agent_run_id_agent_run", ondelete="RESTRICT"), sa.ForeignKeyConstraint(["tool_call_id"], ["tool_call.id"], name="fk_agent_pending_confirmation_tool_call_id_tool_call", ondelete="RESTRICT"), sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_agent_pending_confirmation_project_id_project", ondelete="RESTRICT"), sa.CheckConstraint("revision >= 1", name="ck_agent_pending_confirmation_revision_positive"), sa.CheckConstraint("CHAR_LENGTH(config_hash) = 64", name="ck_agent_pending_confirmation_config_hash_length"), sa.CheckConstraint("CHAR_LENGTH(binding_hash) = 64", name="ck_agent_pending_confirmation_binding_hash_length"), sa.CheckConstraint("status IN ('PENDING','CONFIRMED','CONSUMED','EXPIRED','INVALIDATED')", name="ck_agent_pending_confirmation_status"), **_MYSQL)
    op.create_index("ix_agent_pending_confirmation_run_status", "agent_pending_confirmation", ["agent_run_id", "status"])


def downgrade() -> None:
    # Remove the two deferred cyclic FKs first, then drop every M4 child table
    # before its parent.  Keep this order explicit: MySQL enforces RESTRICT
    # FKs during DROP TABLE and an unordered collection can make downgrade
    # fail after a partial schema change.
    op.drop_constraint("fk_conversation_current_run_id_agent_run", "conversation", type_="foreignkey")
    op.drop_constraint("fk_agent_run_input_message_id_message", "agent_run", type_="foreignkey")
    drop_order = (
        ("agent_pending_confirmation", ["ix_agent_pending_confirmation_run_status"]),
        ("llm_test_result", ["ix_llm_test_result_configuration_created"]),
        ("context_snapshot", ["ix_context_snapshot_project_created"]),
        ("llm_call", ["ix_llm_call_run_request"]),
        ("llm_configuration", ["ix_llm_configuration_project_status"]),
        ("tool_call", ["ix_tool_call_run_started"]),
        ("agent_event", ["ix_agent_event_stream_timestamp"]),
        ("agent_attempt", ["ix_agent_attempt_lease"]),
        ("message", ["ix_message_conversation_created"]),
        ("agent_run", ["ix_agent_run_project_status", "ix_agent_run_conversation_created"]),
        ("conversation", ["ix_conversation_project_owner_updated"]),
    )
    for name, indexes in drop_order:
        for index in indexes:
            op.drop_index(index, table_name=name)
        op.drop_table(name)
