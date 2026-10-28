"""Add owner-fenced leases to the platform outbox.

This migration only appends nullable lease metadata and an index.  Existing
outbox rows remain dispatchable with a zero lease token.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007_worker_outbox_lease"
down_revision = "0002_m2_prediction_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("outbox_event", sa.Column("lease_owner", sa.String(128), nullable=True))
    op.add_column(
        "outbox_event",
        sa.Column("lease_token", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("outbox_event", sa.Column("lease_expires_at", sa.DateTime(), nullable=True))
    op.create_index("ix_outbox_lease", "outbox_event", ["status", "lease_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_outbox_lease", table_name="outbox_event")
    op.drop_column("outbox_event", "lease_expires_at")
    op.drop_column("outbox_event", "lease_token")
    op.drop_column("outbox_event", "lease_owner")
