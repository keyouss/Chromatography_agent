"""M3 model registration, evaluation and deployment governance."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_m3_model_center"
down_revision = "0004_m3_training"
branch_labels = None
depends_on = None

_MYSQL = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"}


def upgrade() -> None:
    op.create_table(
        "registration",
        sa.Column("id", sa.String(96), nullable=False), sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False), sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("etag", sa.String(128), nullable=False), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source", sa.JSON(), nullable=False), sa.Column("name", sa.String(200), nullable=False),
        sa.Column("task_type", sa.String(96), nullable=False), sa.Column("manifest_snapshot", sa.JSON(), nullable=True),
        sa.Column("checks", sa.JSON(), nullable=False), sa.Column("job_id", sa.String(96), nullable=True),
        sa.Column("model_id", sa.String(96), nullable=True), sa.Column("error_report_ref", sa.JSON(), nullable=True),
        sa.Column("validation_report", sa.JSON(), nullable=True), sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_registration"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_registration_project_id_project", ondelete="RESTRICT"),
        sa.CheckConstraint("revision >= 1", name="ck_registration_revision_positive"),
        sa.CheckConstraint("length(config_hash) = 64", name="ck_registration_config_hash_length"),
        sa.CheckConstraint("status IN ('DRAFT','VALIDATING','VALID','INVALID','REGISTERED')", name="ck_registration_status"),
        **_MYSQL,
    )
    op.create_foreign_key("fk_registration_job_id_job", "registration", "job", ["job_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_registration_project_status", "registration", ["project_id", "status"])

    op.create_table(
        "model",
        sa.Column("id", sa.String(96), nullable=False), sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("registration_id", sa.String(96), nullable=True), sa.Column("name", sa.String(200), nullable=False),
        sa.Column("version", sa.String(96), nullable=False), sa.Column("task_type", sa.String(96), nullable=False),
        sa.Column("artifact_id", sa.String(96), nullable=False), sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("lifecycle_status", sa.String(16), nullable=False), sa.Column("deployment_status", sa.String(16), nullable=False),
        sa.Column("input_schema_version", sa.String(96), nullable=False), sa.Column("output_schema_version", sa.String(96), nullable=False),
        sa.Column("feature_version", sa.String(96), nullable=True), sa.Column("source", sa.JSON(), nullable=False),
        sa.Column("applicability", sa.JSON(), nullable=False), sa.Column("model_card", sa.JSON(), nullable=False),
        sa.Column("deployments", sa.JSON(), nullable=False), sa.Column("comparison_roles", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False), sa.Column("etag", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_model"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_model_project_id_project", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id", "artifact_id"], ["artifact.project_id", "artifact.id"], name="fk_model_project_artifact", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["registration_id"], ["registration.id"], name="fk_model_registration_id_registration", ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", "name", "version", name="uq_model_project_name_version"),
        sa.CheckConstraint("revision >= 1", name="ck_model_revision_positive"),
        sa.CheckConstraint("length(sha256) = 64", name="ck_model_sha256_length"),
        sa.CheckConstraint("lifecycle_status IN ('VALIDATING','CANDIDATE','APPROVED','REJECTED','RETIRED')", name="ck_model_lifecycle_status"),
        sa.CheckConstraint("deployment_status IN ('NOT_DEPLOYED','STAGING','PRODUCTION')", name="ck_model_deployment_status"),
        **_MYSQL,
    )
    op.create_index("ix_model_project_task", "model", ["project_id", "task_type"])

    op.create_table(
        "evaluation_protocol",
        sa.Column("id", sa.String(96), nullable=False), sa.Column("version", sa.String(64), nullable=False),
        sa.Column("task_type", sa.String(96), nullable=False), sa.Column("data_version_id", sa.String(96), nullable=False),
        sa.Column("split_manifest_sha256", sa.String(64), nullable=False), sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.PrimaryKeyConstraint("id", "version", name="pk_evaluation_protocol"),
        sa.ForeignKeyConstraint(["data_version_id"], ["data_version.id"], name="fk_evaluation_protocol_data_version_id_data_version", ondelete="RESTRICT"),
        sa.CheckConstraint("status IN ('DRAFT','APPROVED','RETIRED')", name="ck_evaluation_protocol_status"),
        sa.CheckConstraint("length(split_manifest_sha256) = 64", name="ck_evaluation_protocol_split_manifest_sha256_length"),
        **_MYSQL,
    )

    op.create_table(
        "evaluation",
        sa.Column("id", sa.String(96), nullable=False), sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("model_id", sa.String(96), nullable=False), sa.Column("protocol_id", sa.String(96), nullable=False),
        sa.Column("protocol_version", sa.String(64), nullable=False), sa.Column("job_id", sa.String(96), nullable=True),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("model_snapshot", sa.JSON(), nullable=False),
        sa.Column("protocol_snapshot", sa.JSON(), nullable=False), sa.Column("data_snapshot", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False), sa.Column("pass_status", sa.String(16), nullable=False),
        sa.Column("report_artifact_id", sa.String(96), nullable=True), sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False), sa.Column("etag", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_evaluation"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_evaluation_project_id_project", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["model_id"], ["model.id"], name="fk_evaluation_model_id_model", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["protocol_id", "protocol_version"], ["evaluation_protocol.id", "evaluation_protocol.version"], name="fk_evaluation_protocol_id_evaluation_protocol", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id", "report_artifact_id"], ["artifact.project_id", "artifact.id"], name="fk_evaluation_project_artifact", ondelete="RESTRICT"),
        sa.CheckConstraint("status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')", name="ck_evaluation_status"),
        sa.CheckConstraint("pass_status IN ('PENDING','PASSED','FAILED')", name="ck_evaluation_pass_status"),
        **_MYSQL,
    )
    op.create_index("ix_evaluation_project_model", "evaluation", ["project_id", "model_id"])
    op.create_foreign_key("fk_evaluation_job_id_job", "evaluation", "job", ["job_id"], ["id"], ondelete="RESTRICT")

    op.create_table(
        "approval_request",
        sa.Column("id", sa.String(96), nullable=False), sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("model_id", sa.String(96), nullable=False), sa.Column("evaluation_id", sa.String(96), nullable=False),
        sa.Column("evaluation_snapshot", sa.JSON(), nullable=False), sa.Column("scope_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("requested_by", sa.String(96), nullable=False),
        sa.Column("decided_by", sa.String(96), nullable=True), sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False), sa.Column("etag", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_approval_request"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_approval_request_project_id_project", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["model_id"], ["model.id"], name="fk_approval_request_model_id_model", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["evaluation_id"], ["evaluation.id"], name="fk_approval_request_evaluation_id_evaluation", ondelete="RESTRICT"),
        sa.CheckConstraint("status IN ('PENDING','APPROVED','REJECTED')", name="ck_approval_request_status"),
        sa.CheckConstraint("revision >= 1", name="ck_approval_request_revision_positive"),
        **_MYSQL,
    )
    op.create_index("ix_approval_project_status", "approval_request", ["project_id", "status"])

    op.create_table(
        "production_pointer",
        sa.Column("project_id", sa.String(96), nullable=False), sa.Column("scope_id", sa.String(96), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False), sa.Column("model_id", sa.String(96), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("project_id", "scope_id", "stage", name="pk_production_pointer"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_production_pointer_project_id_project", ondelete="RESTRICT"),
        sa.CheckConstraint("stage IN ('STAGING','PRODUCTION')", name="ck_production_pointer_stage"),
        sa.CheckConstraint("revision >= 0", name="ck_production_pointer_revision_nonnegative"),
        **_MYSQL,
    )

    op.create_table(
        "deployment_draft",
        sa.Column("id", sa.String(96), nullable=False), sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("scope_id", sa.String(96), nullable=False), sa.Column("target_model_id", sa.String(96), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False), sa.Column("expected_pointer_revision", sa.Integer(), nullable=False),
        sa.Column("rollback_model_id", sa.String(96), nullable=True), sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False), sa.Column("etag", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("validation_report", sa.JSON(), nullable=True),
        sa.Column("source_deployment_id", sa.String(96), nullable=True), sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("confirmation_id", sa.String(96), nullable=True), sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_deployment_draft"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_deployment_draft_project_id_project", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_model_id"], ["model.id"], name="fk_deployment_draft_target_model_id_model", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["rollback_model_id"], ["model.id"], name="fk_deployment_draft_rollback_model_id_model", ondelete="RESTRICT"),
        sa.CheckConstraint("stage IN ('STAGING','PRODUCTION')", name="ck_deployment_draft_stage"),
        sa.CheckConstraint("status IN ('DRAFT','READY','INVALID','SUBMITTED')", name="ck_deployment_draft_status"),
        sa.CheckConstraint("operation IN ('PUBLISH','ROLLBACK')", name="ck_deployment_draft_operation"),
        sa.CheckConstraint("expected_pointer_revision >= 0", name="ck_deployment_draft_expected_pointer_revision_nonnegative"),
        **_MYSQL,
    )
    op.create_index("ix_deployment_draft_project_scope", "deployment_draft", ["project_id", "scope_id"])

    op.create_table(
        "deployment",
        sa.Column("id", sa.String(96), nullable=False), sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("scope_id", sa.String(96), nullable=False), sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("target_model_id", sa.String(96), nullable=False), sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("expected_pointer_revision", sa.Integer(), nullable=False),
        sa.Column("pointer_revision", sa.Integer(), nullable=True), sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("draft_id", sa.String(96), nullable=False), sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("dispatch_id", sa.String(96), nullable=True), sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_deployment"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_deployment_project_id_project", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_model_id"], ["model.id"], name="fk_deployment_target_model_id_model", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["draft_id"], ["deployment_draft.id"], name="fk_deployment_draft_id_deployment_draft", ondelete="RESTRICT"),
        sa.CheckConstraint("stage IN ('STAGING','PRODUCTION')", name="ck_deployment_stage"),
        sa.CheckConstraint("operation IN ('PUBLISH','ROLLBACK')", name="ck_deployment_operation"),
        sa.CheckConstraint("status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')", name="ck_deployment_status"),
        sa.CheckConstraint("attempt >= 1", name="ck_deployment_attempt_positive"),
        **_MYSQL,
    )
    op.create_index("ix_deployment_project_scope_created", "deployment", ["project_id", "scope_id", "created_at"])
    op.create_foreign_key("fk_deployment_draft_source_deployment_id_deployment", "deployment_draft", "deployment", ["source_deployment_id"], ["id"], ondelete="RESTRICT")

    op.create_table(
        "deployment_ledger",
        sa.Column("id", sa.String(96), nullable=False), sa.Column("deployment_id", sa.String(96), nullable=False),
        sa.Column("project_id", sa.String(96), nullable=False), sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False), sa.PrimaryKeyConstraint("id", name="pk_deployment_ledger"),
        sa.ForeignKeyConstraint(["deployment_id"], ["deployment.id"], name="fk_deployment_ledger_deployment_id_deployment", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_deployment_ledger_project_id_project", ondelete="RESTRICT"),
        **_MYSQL,
    )
    op.create_index("ix_deployment_ledger_project", "deployment_ledger", ["project_id", "recorded_at"])

    op.create_table(
        "idempotency",
        sa.Column("scope", sa.String(512), nullable=False), sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("result_type", sa.String(96), nullable=True), sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.PrimaryKeyConstraint("scope", name="pk_idempotency"), **_MYSQL,
    )
    op.create_index("ix_model_center_idempotency_created", "idempotency", ["created_at"])

    op.create_table(
        "model_center_audit_event",
        sa.Column("id", sa.String(96), nullable=False), sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("actor_id", sa.String(96), nullable=False), sa.Column("interaction", sa.String(32), nullable=False),
        sa.Column("action", sa.String(160), nullable=False), sa.Column("resource_ref", sa.JSON(), nullable=False),
        sa.Column("before_summary", sa.JSON(), nullable=False), sa.Column("after_summary", sa.JSON(), nullable=False),
        sa.Column("confirmation_id", sa.String(96), nullable=True), sa.Column("config_hash", sa.String(64), nullable=True),
        sa.Column("result", sa.String(16), nullable=False), sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_model_center_audit_event"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_model_center_audit_event_project_id_project", ondelete="RESTRICT"),
        **_MYSQL,
    )
    op.create_index("ix_model_center_audit_project_timestamp", "model_center_audit_event", ["project_id", "timestamp"])

    op.create_table(
        "model_comparison",
        sa.Column("id", sa.String(96), nullable=False), sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("model_ids", sa.JSON(), nullable=False), sa.Column("protocol_snapshot", sa.JSON(), nullable=False),
        sa.Column("data_snapshot", sa.JSON(), nullable=False), sa.Column("scope_id", sa.String(96), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_model_comparison"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_model_comparison_project_id_project", ondelete="RESTRICT"),
        **_MYSQL,
    )


def downgrade() -> None:
    op.drop_table("model_comparison")
    op.drop_index("ix_model_center_audit_project_timestamp", table_name="model_center_audit_event")
    op.drop_table("model_center_audit_event")
    op.drop_index("ix_model_center_idempotency_created", table_name="idempotency")
    op.drop_table("idempotency")
    op.drop_index("ix_deployment_ledger_project", table_name="deployment_ledger")
    op.drop_table("deployment_ledger")
    op.drop_constraint("fk_deployment_draft_source_deployment_id_deployment", "deployment_draft", type_="foreignkey")
    op.drop_index("ix_deployment_project_scope_created", table_name="deployment")
    op.drop_table("deployment")
    op.drop_index("ix_deployment_draft_project_scope", table_name="deployment_draft")
    op.drop_table("deployment_draft")
    op.drop_table("production_pointer")
    op.drop_index("ix_approval_project_status", table_name="approval_request")
    op.drop_table("approval_request")
    op.drop_index("ix_evaluation_project_model", table_name="evaluation")
    op.drop_constraint("fk_evaluation_job_id_job", "evaluation", type_="foreignkey")
    op.drop_table("evaluation")
    op.drop_table("evaluation_protocol")
    op.drop_index("ix_model_project_task", table_name="model")
    op.drop_table("model")
    op.drop_index("ix_registration_project_status", table_name="registration")
    op.drop_constraint("fk_registration_job_id_job", "registration", type_="foreignkey")
    op.drop_table("registration")
