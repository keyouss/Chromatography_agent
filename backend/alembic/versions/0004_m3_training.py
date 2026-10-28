"""M3 fixed-template training and candidate persistence."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_m3_training"
down_revision = "0003_m3_knowledge"
branch_labels = None
depends_on = None

_MYSQL = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"}


def upgrade() -> None:
    op.create_table(
        "training_template",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("task_type", sa.String(96), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", "version", name="pk_training_template"),
        **_MYSQL,
    )
    op.create_table(
        "training_experiment",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("data_version_id", sa.String(96), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("etag", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("candidate_status", sa.String(32), nullable=False),
        sa.Column("current_attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("validation_report", sa.JSON(), nullable=True),
        sa.Column("job_id", sa.String(96), nullable=True),
        sa.Column("candidate_model_id", sa.String(96), nullable=True),
        sa.Column("spec_locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_training_experiment"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_training_experiment_project_id_project", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["data_version_id"], ["data_version.id"], name="fk_training_experiment_data_version_id_data_version", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["job.id"], name="fk_training_experiment_job_id_job", ondelete="RESTRICT"),
        sa.CheckConstraint("revision >= 1", name="ck_training_experiment_training_experiment_revision_positive"),
        sa.CheckConstraint("CHAR_LENGTH(config_hash) = 64", name="ck_training_experiment_training_experiment_config_hash_length"),
        sa.CheckConstraint("status IN ('DRAFT','VALIDATING','INVALID','READY','QUEUED','RUNNING','CANCEL_REQUESTED','SUCCEEDED','FAILED','CANCELED','TIMED_OUT','INTERRUPTED')", name="ck_training_experiment_training_experiment_status"),
        sa.CheckConstraint("candidate_status IN ('NOT_REQUESTED','VALIDATING','CREATED','REJECTED')", name="ck_training_experiment_training_experiment_candidate_status"),
        **_MYSQL,
    )
    op.create_index("ix_training_experiment_project_status", "training_experiment", ["project_id", "status", "created_at"])

    op.create_table(
        "training_job",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("experiment_id", sa.String(96), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("dispatch_id", sa.String(96), nullable=False),
        sa.Column("result_ref", sa.JSON(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_token", sa.String(128), nullable=True),
        sa.Column("fencing_token", sa.Integer(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_training_job"),
        # The module row is a one-to-one execution extension of the public
        # M1 task.  Training and generic task IDs intentionally match.
        sa.ForeignKeyConstraint(["id"], ["job.id"], name="fk_training_job_id_job", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_training_job_project_id_project", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["experiment_id"], ["training_experiment.id"], name="fk_training_job_experiment_id_training_experiment", ondelete="RESTRICT"),
        sa.UniqueConstraint("dispatch_id", name="uq_training_job_dispatch_id"),
        sa.CheckConstraint("attempt >= 1", name="ck_training_job_training_job_attempt_positive"),
        sa.CheckConstraint("status IN ('QUEUED','RUNNING','CANCEL_REQUESTED','SUCCEEDED','FAILED','CANCELED','TIMED_OUT','INTERRUPTED')", name="ck_training_job_training_job_status"),
        **_MYSQL,
    )
    op.create_index("ix_training_job_project_status", "training_job", ["project_id", "status", "created_at"])

    op.create_table(
        "training_outbox",
        sa.Column("dispatch_id", sa.String(96), nullable=False),
        sa.Column("job_id", sa.String(96), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("dispatch_id", name="pk_training_outbox"),
        sa.ForeignKeyConstraint(["job_id"], ["training_job.id"], name="fk_training_outbox_job_id_training_job", ondelete="RESTRICT"),
        sa.CheckConstraint("attempts >= 0", name="ck_training_outbox_training_outbox_attempts_nonnegative"),
        sa.CheckConstraint("status IN ('PENDING','PROCESSING','SENT','FAILED')", name="ck_training_outbox_training_outbox_status"),
        **_MYSQL,
    )
    op.create_index("ix_training_outbox_status_created", "training_outbox", ["status", "created_at"])

    op.create_table(
        "training_audit_event",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("actor_id", sa.String(96), nullable=False),
        sa.Column("action", sa.String(160), nullable=False),
        sa.Column("resource_ref", sa.JSON(), nullable=False),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("confirmation_id", sa.String(96), nullable=True),
        sa.Column("job_id", sa.String(96), nullable=True),
        sa.Column("config_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_training_audit_event"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_training_audit_event_project_id_project", ondelete="RESTRICT"),
        sa.CheckConstraint("result IN ('SUCCEEDED','FAILED')", name="ck_training_audit_event_training_audit_event_result"),
        sa.CheckConstraint("config_hash IS NULL OR CHAR_LENGTH(config_hash) = 64", name="ck_training_audit_event_training_audit_event_config_hash_length"),
        **_MYSQL,
    )
    op.create_index("ix_training_audit_event_project_created", "training_audit_event", ["project_id", "created_at"])

    op.create_table(
        "training_candidate",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("experiment_id", sa.String(96), nullable=False),
        sa.Column("artifact_id", sa.String(96), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("published", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_training_candidate"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_training_candidate_project_id_project", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id", "artifact_id"], ["artifact.project_id", "artifact.id"], name="fk_training_candidate_project_artifact", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["experiment_id"], ["training_experiment.id"], name="fk_training_candidate_experiment_id_training_experiment", ondelete="RESTRICT"),
        sa.UniqueConstraint("experiment_id", name="uq_training_candidate_experiment_id"),
        sa.CheckConstraint("status IN ('CANDIDATE','REJECTED')", name="ck_training_candidate_training_candidate_status"),
        **_MYSQL,
    )
    op.create_index("ix_training_candidate_project_status", "training_candidate", ["project_id", "status", "created_at"])

    op.create_table(
        "experiment_metric",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("experiment_id", sa.String(96), nullable=False),
        sa.Column("metric_key", sa.String(128), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("split", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_experiment_metric"),
        sa.ForeignKeyConstraint(["experiment_id"], ["training_experiment.id"], name="fk_experiment_metric_experiment_id_training_experiment", ondelete="RESTRICT"),
        sa.UniqueConstraint("experiment_id", "metric_key", "split", name="uq_experiment_metric_key"),
        **_MYSQL,
    )
    op.create_index("ix_experiment_metric_experiment", "experiment_metric", ["experiment_id", "created_at"])

    op.create_table(
        "training_idempotency",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("actor_id", sa.String(96), nullable=False),
        sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("job_id", sa.String(96), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_training_idempotency"),
        sa.UniqueConstraint("actor_id", "project_id", "key", name="uq_training_idempotency_scope_key"),
        sa.ForeignKeyConstraint(["job_id"], ["training_job.id"], name="fk_training_idempotency_job_id_training_job", ondelete="RESTRICT"),
        sa.CheckConstraint("CHAR_LENGTH(request_hash) = 64", name="ck_training_idempotency_request_hash_length"),
        **_MYSQL,
    )
    op.create_index("ix_training_idempotency_job", "training_idempotency", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_training_idempotency_job", table_name="training_idempotency")
    op.drop_table("training_idempotency")
    op.drop_index("ix_experiment_metric_experiment", table_name="experiment_metric")
    op.drop_table("experiment_metric")
    op.drop_index("ix_training_candidate_project_status", table_name="training_candidate")
    op.drop_table("training_candidate")
    op.drop_index("ix_training_audit_event_project_created", table_name="training_audit_event")
    op.drop_table("training_audit_event")
    op.drop_index("ix_training_outbox_status_created", table_name="training_outbox")
    op.drop_table("training_outbox")
    op.drop_index("ix_training_job_project_status", table_name="training_job")
    op.drop_table("training_job")
    op.drop_index("ix_training_experiment_project_status", table_name="training_experiment")
    op.drop_table("training_experiment")
    op.drop_table("training_template")
