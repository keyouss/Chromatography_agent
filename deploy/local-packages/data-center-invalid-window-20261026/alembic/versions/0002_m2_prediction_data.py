"""M2 prediction/data persistence.

This revision is explicit and independent of mutable ORM metadata.  The
prediction record belongs to data-center and is referenced by prediction.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_m2_prediction_data"
down_revision = "0001_m1_persistence"
branch_labels = None
depends_on = None

_MYSQL = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


def upgrade() -> None:
    op.create_table(
        "data_domain",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("chromatography_type", sa.String(32), nullable=False),
        sa.Column("record_schema", sa.JSON(), nullable=False),
        sa.Column("unit_definitions", sa.JSON(), nullable=False),
        sa.Column("identity_fields", sa.JSON(), nullable=False),
        sa.Column("deduplication_policy", sa.String(32), nullable=False),
        sa.Column("supported_import_modes", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id", "version", name="pk_data_domain"),
        sa.CheckConstraint("deduplication_policy IN ('reject','update','allow')", name="ck_data_domain_deduplication_policy"),
        **_MYSQL,
    )

    op.create_table(
        "data_import",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("domain_id", sa.String(96), nullable=False),
        sa.Column("artifact_id", sa.String(96), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("import_mode", sa.String(16), nullable=False),
        sa.Column("field_mapping", sa.JSON(), nullable=False),
        sa.Column("source_description", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("etag", sa.String(128), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("job_id", sa.String(96), nullable=True),
        sa.Column("validation_ref", sa.String(96), nullable=True),
        sa.Column("new_data_version_id", sa.String(96), nullable=True),
        sa.Column("request_snapshot", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_data_import"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_data_import_project_id_project", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id", "artifact_id"], ["artifact.project_id", "artifact.id"], name="fk_data_import_project_artifact", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["job.id"], name="fk_data_import_job_id_job", ondelete="RESTRICT"),
        sa.CheckConstraint("revision >= 1", name="ck_data_import_revision_positive"),
        sa.CheckConstraint("CHAR_LENGTH(config_hash) = 64", name="ck_data_import_config_hash_length"),
        sa.CheckConstraint("status IN ('DRAFT','VALIDATING','INVALID','READY','COMMITTING','COMMITTED','FAILED')", name="ck_data_import_status"),
        sa.CheckConstraint("import_mode IN ('append','replace')", name="ck_data_import_import_mode"),
        **_MYSQL,
    )
    op.create_index("ix_data_import_project_status", "data_import", ["project_id", "status", "created_at"])

    op.create_table(
        "data_import_validation",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("import_id", sa.String(96), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column("report_artifact_id", sa.String(96), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("parser_revision", sa.String(128), nullable=True),
        sa.Column("parser_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_data_import_validation"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_data_import_validation_project_id_project", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["import_id"], ["data_import.id"], name="fk_data_import_validation_import_id_data_import", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id", "report_artifact_id"], ["artifact.project_id", "artifact.id"], name="fk_data_import_validation_project_artifact", ondelete="RESTRICT"),
        sa.CheckConstraint("revision >= 1", name="ck_data_import_validation_revision_positive"),
        sa.CheckConstraint("status IN ('PENDING','VALID','STALE','INVALID')", name="ck_data_import_validation_status"),
        sa.CheckConstraint("parser_hash IS NULL OR CHAR_LENGTH(parser_hash) = 64", name="ck_data_import_validation_parser_hash_length"),
        **_MYSQL,
    )
    op.create_index("ix_data_import_validation_import", "data_import_validation", ["project_id", "import_id", "created_at"])

    op.create_table(
        "data_version",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("domain_id", sa.String(96), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("source_import_id", sa.String(96), nullable=False),
        sa.Column("snapshot_artifact_id", sa.String(96), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("quality_report_ref", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("referenced_by_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_data_version"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_data_version_project_id_project", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_import_id"], ["data_import.id"], name="fk_data_version_source_import_id_data_import", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id", "snapshot_artifact_id"], ["artifact.project_id", "artifact.id"], name="fk_data_version_project_artifact", ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", "domain_id", "version", name="uq_data_version_scope_number"),
        sa.CheckConstraint("version >= 1", name="ck_data_version_version_positive"),
        sa.CheckConstraint("record_count >= 0", name="ck_data_version_record_count_nonnegative"),
        sa.CheckConstraint("referenced_by_count >= 0", name="ck_data_version_referenced_by_nonnegative"),
        sa.CheckConstraint("CHAR_LENGTH(sha256) = 64", name="ck_data_version_sha256_length"),
        sa.CheckConstraint("status IN ('READY','ARCHIVED')", name="ck_data_version_status"),
        **_MYSQL,
    )
    op.create_index("ix_data_version_scope_status", "data_version", ["project_id", "domain_id", "status", "version"])

    op.create_table(
        "data_version_reference",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("version_id", sa.String(96), nullable=False),
        sa.Column("reference_id", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_data_version_reference"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_data_version_reference_project_id_project", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["version_id"], ["data_version.id"], name="fk_data_version_reference_version_id_data_version", ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", "version_id", "reference_id", name="uq_data_version_reference"),
        **_MYSQL,
    )
    op.create_index("ix_data_version_reference_version", "data_version_reference", ["project_id", "version_id"])

    op.create_table(
        "prediction_record",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("prediction_id", sa.String(96), nullable=True),
        sa.Column("batch_id", sa.String(96), nullable=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(96), nullable=False),
        sa.Column("chromatography_type", sa.String(32), nullable=False),
        sa.Column("model_snapshot", sa.JSON(), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("result_ref", sa.JSON(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("artifact_refs", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("etag", sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_prediction_record"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_prediction_record_project_id_project", ondelete="RESTRICT"),
        sa.CheckConstraint("revision >= 1", name="ck_prediction_record_revision_positive"),
        sa.CheckConstraint("status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELED','TIMED_OUT','INTERRUPTED','ARCHIVED')", name="ck_prediction_record_status"),
        **_MYSQL,
    )
    op.create_index("ix_prediction_record_project_created", "prediction_record", ["project_id", "created_at", "id"])

    op.create_table(
        "prediction",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("prediction_record_id", sa.String(96), nullable=False),
        sa.Column("job_id", sa.String(96), nullable=False),
        sa.Column("actor_id", sa.String(96), nullable=False),
        sa.Column("chromatography_type", sa.String(32), nullable=False),
        sa.Column("tool", sa.String(96), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("model_snapshot", sa.JSON(), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("request_snapshot", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("conversation_id", sa.String(96), nullable=True),
        sa.Column("agent_run_id", sa.String(96), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("data_origin", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("etag", sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_prediction"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_prediction_project_id_project", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["prediction_record_id"], ["prediction_record.id"], name="fk_prediction_record_id_prediction_record", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["job.id"], name="fk_prediction_job_id_job", ondelete="RESTRICT"),
        sa.UniqueConstraint("job_id", name="uq_prediction_job_id"),
        sa.CheckConstraint("revision >= 1", name="ck_prediction_revision_positive"),
        sa.CheckConstraint("status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELED','TIMED_OUT','INTERRUPTED')", name="ck_prediction_status"),
        sa.CheckConstraint("data_origin IN ('production','fixture')", name="ck_prediction_data_origin"),
        **_MYSQL,
    )
    op.create_index("ix_prediction_project_status_created", "prediction", ["project_id", "status", "created_at"])
    op.create_foreign_key(
        "fk_prediction_record_prediction_id_prediction",
        "prediction_record", "prediction", ["prediction_id"], ["id"], ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_prediction_record_prediction_id_prediction", "prediction_record", type_="foreignkey")
    op.drop_index("ix_prediction_project_status_created", table_name="prediction")
    op.drop_table("prediction")
    op.drop_index("ix_prediction_record_project_created", table_name="prediction_record")
    op.drop_table("prediction_record")
    op.drop_index("ix_data_version_reference_version", table_name="data_version_reference")
    op.drop_table("data_version_reference")
    op.drop_index("ix_data_version_scope_status", table_name="data_version")
    op.drop_table("data_version")
    op.drop_index("ix_data_import_validation_import", table_name="data_import_validation")
    op.drop_table("data_import_validation")
    op.drop_index("ix_data_import_project_status", table_name="data_import")
    op.drop_table("data_import")
    op.drop_table("data_domain")
