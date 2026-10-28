"""Persist model-center attempt/dispatch fences and cancellation states."""

from __future__ import annotations

from alembic import op
from alembic import context
import sqlalchemy as sa


revision = "0010_model_center_job_fencing"
down_revision = "0009_training_outbox_lease"
branch_labels = None
depends_on = None


_REG_OLD = "status IN ('DRAFT','VALIDATING','VALID','INVALID','REGISTERED')"
_REG_NEW = "status IN ('DRAFT','VALIDATING','VALID','INVALID','REGISTERED','CANCELED')"
_EVAL_OLD = "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')"
_EVAL_NEW = "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELED')"
_DEPLOY_OLD = "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')"
_DEPLOY_NEW = "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELED')"


def _upgrade_table(table: str, old: str, new: str) -> None:
    bind = op.get_bind()
    if context.is_offline_mode() or bind.dialect.name != "sqlite":
        op.add_column(table, sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("1")))
        op.add_column(table, sa.Column("dispatch_id", sa.String(96), nullable=True))
        op.drop_constraint("status", table, type_="check")
        op.create_check_constraint("status", table, new)
        op.alter_column(table, "attempt", server_default=None)
        return
    with op.batch_alter_table(table, recreate="always") as batch:
        batch.add_column(sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("1")))
        batch.add_column(sa.Column("dispatch_id", sa.String(96), nullable=True))
        batch.drop_constraint("status", type_="check")
        batch.create_check_constraint("status", new)
    # Existing rows were backfilled by the temporary default. Keep the schema
    # honest after migration instead of leaving a hidden default for new rows.
    with op.batch_alter_table(table, recreate="always") as batch:
        batch.alter_column("attempt", server_default=None)


def upgrade() -> None:
    _upgrade_table("registration", _REG_OLD, _REG_NEW)
    _upgrade_table("evaluation", _EVAL_OLD, _EVAL_NEW)
    _upgrade_table("deployment", _DEPLOY_OLD, _DEPLOY_NEW)


def _downgrade_table(table: str, old: str, new: str) -> None:
    bind = op.get_bind()
    if context.is_offline_mode() or bind.dialect.name != "sqlite":
        op.drop_constraint("status", table, type_="check")
        op.create_check_constraint("status", table, old)
        op.drop_column(table, "dispatch_id")
        op.drop_column(table, "attempt")
        return
    with op.batch_alter_table(table, recreate="always") as batch:
        batch.drop_constraint("status", type_="check")
        batch.create_check_constraint("status", old)
        batch.drop_column("dispatch_id")
        batch.drop_column("attempt")


def downgrade() -> None:
    # CANCELED rows cannot be represented by the previous contract.
    if not context.is_offline_mode():
        bind = op.get_bind()
        for table in ("registration", "evaluation", "deployment"):
            values = bind.execute(sa.text(f"SELECT COUNT(*) FROM {table} WHERE status = 'CANCELED'")).scalar_one()
            if values:
                raise RuntimeError(f"cannot downgrade {table} while CANCELED rows exist")
    _downgrade_table("deployment", _DEPLOY_OLD, _DEPLOY_NEW)
    _downgrade_table("evaluation", _EVAL_OLD, _EVAL_NEW)
    _downgrade_table("registration", _REG_OLD, _REG_NEW)
