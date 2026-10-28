"""Merge no-batch and batch replay heads for the complete source graph."""

revision = "0018_merge_full_replay_heads"
down_revision = ("0016_merge_batch_snapshot_replay_draft", "0017_merge_no_batch_replay_head")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
