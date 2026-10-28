"""M2 type-specific batch prediction projections."""

from alembic import op
import sqlalchemy as sa


revision = "0011_m2_batch_prediction"
down_revision = "0010_model_center_job_fencing"
branch_labels = None
depends_on = None

_MYSQL = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"}


def upgrade() -> None:
    op.create_table(
        "batch_prediction",
        sa.Column("id", sa.String(96), nullable=False), sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("actor_id", sa.String(96), nullable=False), sa.Column("chromatography_type", sa.String(32), nullable=False),
        sa.Column("tool", sa.String(32), nullable=False), sa.Column("name", sa.String(200), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False), sa.Column("model_selection", sa.JSON(), nullable=False),
        sa.Column("input_artifact_id", sa.String(96), nullable=False), sa.Column("template_version", sa.String(64), nullable=False),
        sa.Column("field_mapping", sa.JSON(), nullable=False), sa.Column("common_input", sa.JSON(), nullable=False),
        sa.Column("invalid_row_policy", sa.String(24), nullable=False), sa.Column("resource_budget", sa.JSON(), nullable=False),
        sa.Column("model_snapshot", sa.JSON(), nullable=True), sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False), sa.Column("etag", sa.String(128), nullable=False),
        sa.Column("draft_status", sa.String(16), nullable=False), sa.Column("validation_id", sa.String(96), nullable=True),
        sa.Column("job_id", sa.String(96), nullable=True), sa.Column("attempt", sa.Integer(), nullable=True),
        sa.Column("execution_status", sa.String(20), nullable=True), sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False), sa.PrimaryKeyConstraint("id", name="pk_batch_prediction"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_batch_prediction_project", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["job.id"], name="fk_batch_prediction_job", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id", "input_artifact_id"], ["artifact.project_id", "artifact.id"], name="fk_batch_prediction_artifact", ondelete="RESTRICT"),
        sa.CheckConstraint("revision >= 1", name="ck_batch_prediction_revision"),
        sa.CheckConstraint("invalid_row_policy IN ('block','exclude_invalid')", name="ck_batch_prediction_invalid_policy"),
        sa.CheckConstraint("draft_status IN ('DRAFT','VALIDATING','INVALID','READY')", name="ck_batch_prediction_draft_status"),
        **_MYSQL,
    )
    op.create_index("ix_batch_prediction_project_status", "batch_prediction", ["project_id", "draft_status", "created_at"])

    op.create_table(
        "batch_validation",
        sa.Column("id", sa.String(96), nullable=False), sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("batch_id", sa.String(96), nullable=False), sa.Column("draft_revision", sa.Integer(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("template_version", sa.String(64), nullable=False), sa.Column("model_snapshot", sa.JSON(), nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False), sa.Column("valid_rows", sa.Integer(), nullable=False),
        sa.Column("invalid_rows", sa.Integer(), nullable=False), sa.Column("excluded_rows", sa.Integer(), nullable=False),
        sa.Column("total_evaluations", sa.Integer(), nullable=False), sa.Column("resource_estimate", sa.JSON(), nullable=False),
        sa.Column("errors_report_artifact_id", sa.String(96), nullable=True), sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.PrimaryKeyConstraint("id", name="pk_batch_validation"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_batch_validation_project", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["batch_id"], ["batch_prediction.id"], name="fk_batch_validation_batch", ondelete="RESTRICT"),
        **_MYSQL,
    )
    op.create_table(
        "batch_validation_item",
        sa.Column("id", sa.String(96), nullable=False), sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("validation_id", sa.String(96), nullable=False), sa.Column("row_id", sa.String(96), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=False), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("normalized_input_hash", sa.String(64), nullable=False), sa.Column("field_errors", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_batch_validation_item"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_batch_validation_item_project", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["validation_id"], ["batch_validation.id"], name="fk_batch_validation_item_validation", ondelete="RESTRICT"),
        **_MYSQL,
    )
    op.create_table(
        "batch_row_result",
        sa.Column("id", sa.String(96), nullable=False), sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("batch_id", sa.String(96), nullable=False), sa.Column("row_id", sa.String(96), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=False), sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True), sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=False), sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_batch_row_result"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_batch_row_result_project", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["batch_id"], ["batch_prediction.id"], name="fk_batch_row_result_batch", ondelete="RESTRICT"),
        **_MYSQL,
    )
    op.create_table(
        "batch_retry_plan",
        sa.Column("id", sa.String(96), nullable=False), sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("parent_batch_id", sa.String(96), nullable=False), sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False), sa.Column("etag", sa.String(128), nullable=False),
        sa.Column("row_ids", sa.JSON(), nullable=False), sa.Column("expected_attempt", sa.Integer(), nullable=False),
        sa.Column("estimated_evaluations", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_batch_retry_plan"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_batch_retry_plan_project", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["parent_batch_id"], ["batch_prediction.id"], name="fk_batch_retry_plan_batch", ondelete="RESTRICT"),
        **_MYSQL,
    )
    op.create_table(
        "batch_export",
        sa.Column("id", sa.String(96), nullable=False), sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("batch_id", sa.String(96), nullable=False), sa.Column("job_id", sa.String(96), nullable=False),
        sa.Column("format", sa.String(16), nullable=False), sa.Column("include", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.PrimaryKeyConstraint("id", name="pk_batch_export"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_batch_export_project", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["batch_id"], ["batch_prediction.id"], name="fk_batch_export_batch", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["job.id"], name="fk_batch_export_job", ondelete="RESTRICT"),
        **_MYSQL,
    )


def downgrade() -> None:
    op.drop_table("batch_export")
    op.drop_table("batch_retry_plan")
    op.drop_table("batch_row_result")
    op.drop_table("batch_validation_item")
    op.drop_table("batch_validation")
    op.drop_index("ix_batch_prediction_project_status", table_name="batch_prediction")
    op.drop_table("batch_prediction")
