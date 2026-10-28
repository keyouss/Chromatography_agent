"""Merge the M4 chain with the M2 worker-lease branch."""

from __future__ import annotations

revision = "0008_merge_worker_outbox"
down_revision = ("0006_m4_assistant", "0007_worker_outbox_lease")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
