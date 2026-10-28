"""Merge the batch snapshot and records replay-draft migration branches."""

revision = "0016_merge_batch_snapshot_replay_draft"
down_revision = ("0014_batch_resolved_snapshot_export", "0015_m2_prediction_replay_draft")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
