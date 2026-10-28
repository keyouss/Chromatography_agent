"""Persist private native framework history for resumable assistant Runs."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0021_assistant_framework_state"
down_revision = "0020_assistant_llm_configuration_lifecycle"
branch_labels = None
depends_on = None

_MYSQL = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"}


def upgrade() -> None:
    op.create_table(
        "assistant_framework_state",
        sa.Column("agent_run_id", sa.String(96), nullable=False),
        sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("conversation_id", sa.String(96), nullable=False),
        sa.Column("engine", sa.String(32), server_default="pydantic-ai", nullable=False),
        sa.Column("framework_version", sa.String(128), nullable=False),
        sa.Column("serialization_version", sa.String(128), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("stream_message_id", sa.String(96), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("agent_run_id", name="pk_assistant_framework_state"),
        sa.ForeignKeyConstraint(
            ["agent_run_id"], ["agent_run.id"],
            name="fk_assistant_framework_state_agent_run_id_agent_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["project.id"],
            name="fk_assistant_framework_state_project_id_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversation.id"],
            name="fk_assistant_framework_state_conversation_id_conversation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["stream_message_id"], ["message.id"],
            name="fk_assistant_framework_state_stream_message_id_message",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("engine = 'pydantic-ai'", name="ck_assistant_framework_state_engine"),
        sa.CheckConstraint("CHAR_LENGTH(config_hash) = 64", name="ck_assistant_framework_state_config_hash_length"),
        **_MYSQL,
    )


def downgrade() -> None:
    op.drop_table("assistant_framework_state")
