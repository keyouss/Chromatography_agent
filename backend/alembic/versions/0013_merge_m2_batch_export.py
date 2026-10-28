"""Merge the independently optional batch and record-export migrations."""

revision = "0013_merge_m2_batch_export"
down_revision = ("0011_m2_batch_prediction", "0012_m2_record_export")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
