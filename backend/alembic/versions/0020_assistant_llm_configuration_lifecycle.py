"""Add assistant LLM test binding and an atomic active configuration pointer."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0020_assistant_llm_configuration_lifecycle"
down_revision = "0019_batch_export_status_repair"
branch_labels = None
depends_on = None

_MYSQL = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"}


def upgrade() -> None:
    bind = op.get_bind()
    duplicates = bind.execute(sa.text(
        "SELECT project_id FROM llm_configuration "
        "WHERE status='ACTIVE' GROUP BY project_id HAVING COUNT(*) > 1"
    )).all()
    if duplicates:
        projects = ", ".join(str(row[0]) for row in duplicates)
        raise RuntimeError(f"Ambiguous ACTIVE assistant LLM configurations require audited resolution: {projects}")

    with op.batch_alter_table("agent_run") as batch:
        batch.add_column(sa.Column("provider_configuration_hash", sa.String(64), nullable=True))
        batch.create_check_constraint(
            "ck_agent_run_provider_configuration_hash_length",
            "provider_configuration_hash IS NULL OR CHAR_LENGTH(provider_configuration_hash) = 64",
        )

    with op.batch_alter_table("llm_configuration") as batch:
        batch.drop_constraint("ck_llm_configuration_status", type_="check")
        batch.create_check_constraint(
            "ck_llm_configuration_status",
            "status IN ('CANDIDATE','TESTED','ACTIVE','RETIRED')",
        )
        batch.add_column(sa.Column("last_test_id", sa.String(96), nullable=True))

    with op.batch_alter_table("llm_test_result") as batch:
        batch.add_column(sa.Column("configuration_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("job_id", sa.String(96), nullable=True))
        batch.create_check_constraint(
            "ck_llm_test_result_configuration_hash_length",
            "configuration_hash IS NULL OR CHAR_LENGTH(configuration_hash) = 64",
        )
        batch.create_index("ix_llm_test_result_status_created", ["status", "created_at"])

    op.create_table(
        "llm_configuration_active",
        sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("configuration_id", sa.String(96), nullable=False),
        sa.Column("configuration_revision", sa.String(128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("etag", sa.String(128), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("project_id", name="pk_llm_configuration_active"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_llm_configuration_active_project_id_project", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["configuration_id"], ["llm_configuration.id"], name="fk_llm_configuration_active_configuration_id_llm_configuration", ondelete="RESTRICT"),
        sa.UniqueConstraint("configuration_id", name="uq_llm_configuration_active_configuration_id"),
        sa.CheckConstraint("revision >= 1", name="ck_llm_configuration_active_revision_positive"),
        **_MYSQL,
    )
    bind.execute(sa.text(
        "INSERT INTO llm_configuration_active "
        "(project_id, configuration_id, configuration_revision, revision, etag, updated_at) "
        "SELECT project_id, id, revision, 1, :etag, created_at "
        "FROM llm_configuration WHERE status='ACTIVE'"
    ), {"etag": '"active-1"'})


def downgrade() -> None:
    op.execute("UPDATE llm_configuration SET status='CANDIDATE' WHERE status='TESTED'")
    op.drop_table("llm_configuration_active")
    with op.batch_alter_table("llm_test_result") as batch:
        batch.drop_index("ix_llm_test_result_status_created")
        batch.drop_constraint("ck_llm_test_result_configuration_hash_length", type_="check")
        batch.drop_column("job_id")
        batch.drop_column("configuration_hash")
    with op.batch_alter_table("llm_configuration") as batch:
        batch.drop_column("last_test_id")
        batch.drop_constraint("ck_llm_configuration_status", type_="check")
        batch.create_check_constraint(
            "ck_llm_configuration_status",
            "status IN ('CANDIDATE','ACTIVE','RETIRED')",
        )
    with op.batch_alter_table("agent_run") as batch:
        batch.drop_constraint("ck_agent_run_provider_configuration_hash_length", type_="check")
        batch.drop_column("provider_configuration_hash")
