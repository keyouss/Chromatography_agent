"""Persist project-scoped replay drafts without scheduling predictions."""

from alembic import op
import sqlalchemy as sa


revision = "0015_m2_prediction_replay_draft"
down_revision = "0012_m2_record_export"
branch_labels = None
depends_on = None

_MYSQL = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"}


def upgrade() -> None:
    op.create_table(
        "prediction_draft",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("source_record_id", sa.String(96), nullable=False),
        sa.Column("source_prediction_id", sa.String(96), nullable=False),
        sa.Column("actor_id", sa.String(96), nullable=False),
        sa.Column("chromatography_type", sa.String(32), nullable=False),
        sa.Column("tool", sa.String(96), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("structure_refs", sa.JSON(), nullable=False),
        sa.Column("structure_snapshots", sa.JSON(), nullable=False),
        sa.Column("modifier_structure_snapshots", sa.JSON(), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("model_selection", sa.JSON(), nullable=False),
        sa.Column("source_model_snapshot", sa.JSON(), nullable=False),
        sa.Column("source_record_revision", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("etag", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_prediction_draft_revision_positive"),
        sa.CheckConstraint("source_record_revision >= 1", name="ck_prediction_draft_source_revision_positive"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_prediction_draft_project_id_project", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_record_id"], ["prediction_record.id"], name="fk_prediction_draft_source_record_id_prediction_record", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_prediction_draft"),
        **_MYSQL,
    )
    op.create_index("ix_prediction_draft_project_updated", "prediction_draft", ["project_id", "updated_at", "id"])
    op.create_index("ix_prediction_draft_project_source", "prediction_draft", ["project_id", "source_record_id"])


def downgrade() -> None:
    op.drop_index("ix_prediction_draft_project_source", table_name="prediction_draft")
    op.drop_index("ix_prediction_draft_project_updated", table_name="prediction_draft")
    op.drop_table("prediction_draft")
