"""Add durable owner fencing and expiry to the training transport outbox."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009_training_outbox_lease"
down_revision = "0008_merge_worker_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("training_outbox", sa.Column("lease_owner", sa.String(128), nullable=True))
    op.add_column("training_outbox", sa.Column("lease_token", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("training_outbox", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_training_outbox_lease", "training_outbox", ["status", "lease_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_training_outbox_lease", table_name="training_outbox")
    op.drop_column("training_outbox", "lease_expires_at")
    op.drop_column("training_outbox", "lease_token")
    op.drop_column("training_outbox", "lease_owner")
