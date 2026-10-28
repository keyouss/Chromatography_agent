"""Merge the universal no-batch model-center and replay-draft branches."""

revision = "0017_merge_no_batch_replay_head"
down_revision = ("0010_model_center_job_fencing", "0015_m2_prediction_replay_draft")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
