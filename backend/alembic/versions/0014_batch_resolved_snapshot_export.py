"""Persist immutable resolved batch inputs and export source snapshots.

Legacy rows are intentionally retained with NULL snapshot bindings. Batch
workers must reject them until a fresh validation materializes a complete
resolved snapshot; this migration does not guess or silently backfill inputs.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0014_batch_resolved_snapshot_export"
down_revision = "0013_merge_m2_batch_export"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("batch_prediction") as batch:
        batch.add_column(sa.Column("submitted_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("batch_validation_item") as batch:
        batch.add_column(sa.Column("resolved_input_snapshot", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("resolved_input_hash", sa.String(64), nullable=True))
    with op.batch_alter_table("batch_validation") as batch:
        batch.add_column(sa.Column("resolved_input_hash", sa.String(64), nullable=True))
    with op.batch_alter_table("batch_row_result") as batch:
        batch.add_column(sa.Column("validation_id", sa.String(96), nullable=True))
    with op.batch_alter_table("batch_retry_plan") as batch:
        batch.add_column(sa.Column("validation_id", sa.String(96), nullable=True))
        batch.add_column(sa.Column("model_snapshot_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("resolved_input_hash", sa.String(64), nullable=True))
    with op.batch_alter_table("batch_export") as batch:
        batch.add_column(sa.Column("source_snapshot", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("source_snapshot_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("status", sa.String(16), nullable=False, server_default="QUEUED"))
        batch.add_column(sa.Column("error", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("artifact_id", sa.String(96), nullable=True))
        batch.add_column(sa.Column("completed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("batch_export") as batch:
        batch.drop_column("completed_at")
        batch.drop_column("artifact_id")
        batch.drop_column("error")
        batch.drop_column("status")
        batch.drop_column("source_snapshot_hash")
        batch.drop_column("source_snapshot")
    with op.batch_alter_table("batch_retry_plan") as batch:
        batch.drop_column("resolved_input_hash")
        batch.drop_column("model_snapshot_hash")
        batch.drop_column("validation_id")
    with op.batch_alter_table("batch_validation_item") as batch:
        batch.drop_column("resolved_input_hash")
        batch.drop_column("resolved_input_snapshot")
    with op.batch_alter_table("batch_validation") as batch:
        batch.drop_column("resolved_input_hash")
    with op.batch_alter_table("batch_row_result") as batch:
        batch.drop_column("validation_id")
    with op.batch_alter_table("batch_prediction") as batch:
        batch.drop_column("submitted_at")
