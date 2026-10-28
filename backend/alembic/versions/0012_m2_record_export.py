"""Persist project-scoped prediction-record exports."""

from alembic import op
import sqlalchemy as sa


revision = "0012_m2_record_export"
down_revision = "0007_worker_outbox_lease"
branch_labels = None
depends_on = None

_MYSQL = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"}


def upgrade() -> None:
    op.create_table(
        "export",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("job_id", sa.String(96), nullable=False),
        sa.Column("format", sa.String(16), nullable=False),
        sa.Column("format_version", sa.String(32), nullable=False),
        sa.Column("source_snapshots", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("artifact_id", sa.String(96), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_export"),
        sa.UniqueConstraint("job_id", name="uq_export_job_id"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_export_project_id_project", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["job.id"], name="fk_export_job_id_job", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id", "artifact_id"], ["artifact.project_id", "artifact.id"], name="fk_export_project_artifact", ondelete="RESTRICT"),
        sa.CheckConstraint("format IN ('json','csv','xlsx')", name="ck_export_export_format"),
        sa.CheckConstraint("status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELED','TIMED_OUT','INTERRUPTED')", name="ck_export_export_status"),
        **_MYSQL,
    )
    op.create_index("ix_export_project_created", "export", ["project_id", "created_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_export_project_created", table_name="export")
    op.drop_table("export")
