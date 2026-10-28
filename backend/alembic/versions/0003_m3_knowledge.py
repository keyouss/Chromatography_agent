"""M3 knowledge documents, immutable index versions and citations."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003_m3_knowledge"
down_revision = "0002_m2_prediction_data"
branch_labels = None
depends_on = None

_MYSQL = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"}


def upgrade() -> None:
    op.create_table(
        "knowledge_document",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("artifact_id", sa.String(96), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_description", sa.String(2000), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("parser_version", sa.String(128), nullable=True),
        sa.Column("index_version", sa.String(64), nullable=True),
        sa.Column("retrieval_strategy", sa.String(128), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.String(96), nullable=True),
        sa.Column("index_run_id", sa.String(96), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("etag", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_document"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_knowledge_document_project_id_project", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id", "artifact_id"], ["artifact.project_id", "artifact.id"], name="fk_knowledge_document_project_artifact", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["job.id"], name="fk_knowledge_document_job_id_job", ondelete="RESTRICT"),
        sa.CheckConstraint("version >= 1", name="ck_knowledge_document_version_positive"),
        sa.CheckConstraint("chunk_count >= 0", name="ck_knowledge_document_chunk_count_nonnegative"),
        sa.CheckConstraint("revision >= 1", name="ck_knowledge_document_revision_positive"),
        sa.CheckConstraint("status IN ('UPLOADED','INDEXING','READY','FAILED','NEEDS_OCR','ARCHIVED')", name="ck_knowledge_document_status"),
        sa.CheckConstraint("sha256 IS NULL OR CHAR_LENGTH(sha256) = 64", name="ck_knowledge_document_sha256_length"),
        **_MYSQL,
    )
    op.create_index("ix_knowledge_document_project_id", "knowledge_document", ["project_id"])
    op.create_index("ix_knowledge_document_project_status", "knowledge_document", ["project_id", "status", "id"])

    op.create_table(
        "document_version",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("document_id", sa.String(96), nullable=False),
        sa.Column("document_version", sa.String(64), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(128), nullable=False),
        sa.Column("strategy_id", sa.String(128), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("visible", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_document_version"),
        sa.ForeignKeyConstraint(["document_id"], ["knowledge_document.id"], name="fk_document_version_document_id_knowledge_document", ondelete="RESTRICT"),
        sa.CheckConstraint("chunk_count >= 0", name="ck_document_version_chunk_count_nonnegative"),
        sa.CheckConstraint("CHAR_LENGTH(sha256) = 64", name="ck_document_version_sha256_length"),
        **_MYSQL,
    )
    op.create_index("ux_document_version", "document_version", ["document_id", "document_version"], unique=True)

    op.create_table(
        "knowledge_chunk",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("document_id", sa.String(96), nullable=False),
        sa.Column("document_version", sa.String(64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("locator", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_chunk"),
        sa.ForeignKeyConstraint(["document_id"], ["knowledge_document.id"], name="fk_knowledge_chunk_document_id_knowledge_document", ondelete="RESTRICT"),
        sa.CheckConstraint("ordinal >= 0", name="ck_knowledge_chunk_ordinal_nonnegative"),
        sa.CheckConstraint("CHAR_LENGTH(content_sha256) = 64", name="ck_knowledge_chunk_content_sha256_length"),
        **_MYSQL,
    )
    op.create_index("ix_knowledge_chunk_document_version_ordinal", "knowledge_chunk", ["document_id", "document_version", "ordinal"], unique=True)
    # Keep this dialect-specific parser in the migration and ORM metadata in
    # sync; the MySQL compiler emits WITH PARSER ngram.
    op.create_index(
        "ix_knowledge_chunk_content_ft",
        "knowledge_chunk",
        ["content"],
        mysql_prefix="FULLTEXT",
        mysql_with_parser="ngram",
    )

    op.create_table(
        "index_run",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("document_id", sa.String(96), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("document_version", sa.String(64), nullable=True),
        sa.Column("parser_version", sa.String(128), nullable=True),
        sa.Column("strategy_id", sa.String(128), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_index_run"),
        sa.ForeignKeyConstraint(["document_id"], ["knowledge_document.id"], name="fk_index_run_document_id_knowledge_document", ondelete="RESTRICT"),
        sa.CheckConstraint("chunk_count >= 0", name="ck_index_run_chunk_count_nonnegative"),
        sa.CheckConstraint("status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')", name="ck_index_run_status"),
        **_MYSQL,
    )

    op.create_table(
        "citation_snapshot",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("document_id", sa.String(96), nullable=False),
        sa.Column("document_version", sa.String(64), nullable=False),
        sa.Column("chunk_id", sa.String(96), nullable=False),
        sa.Column("chunk_hash", sa.String(64), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("locator", sa.JSON(), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("document_status", sa.String(32), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("score_type", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_citation_snapshot"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_citation_snapshot_project_id_project", ondelete="RESTRICT"),
        sa.CheckConstraint("CHAR_LENGTH(chunk_hash) = 64", name="ck_citation_snapshot_chunk_hash_length"),
        sa.CheckConstraint("score >= 0", name="ck_citation_snapshot_score_nonnegative"),
        **_MYSQL,
    )
    op.create_index("ix_citation_snapshot_project_id", "citation_snapshot", ["project_id"])
    op.create_index("ix_citation_snapshot_project_document", "citation_snapshot", ["project_id", "document_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_chunk_content_ft", table_name="knowledge_chunk")
    op.drop_index("ix_citation_snapshot_project_document", table_name="citation_snapshot")
    op.drop_index("ix_citation_snapshot_project_id", table_name="citation_snapshot")
    op.drop_table("citation_snapshot")
    op.drop_table("index_run")
    op.drop_index("ix_knowledge_chunk_document_version_ordinal", table_name="knowledge_chunk")
    op.drop_table("knowledge_chunk")
    op.drop_index("ux_document_version", table_name="document_version")
    op.drop_table("document_version")
    op.drop_index("ix_knowledge_document_project_status", table_name="knowledge_document")
    op.drop_index("ix_knowledge_document_project_id", table_name="knowledge_document")
    op.drop_table("knowledge_document")
