"""Одна задача публикации на пару (пост, канал).

Раньше каждая попытка создавала новую строку, поэтому в таблице могли
накопиться дубли — их надо схлопнуть до появления ограничения.

Revision ID: 0002_publish_job_unique
Revises: 0001_baseline
"""
from alembic import op

revision = "0002_publish_job_unique"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

_CONSTRAINT = "uq_publish_job_post_target"


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM publish_jobs
        WHERE id NOT IN (
            SELECT MAX(id) FROM publish_jobs
            GROUP BY generated_post_id, target_channel_id
        )
        """
    )
    with op.batch_alter_table("publish_jobs") as batch_op:
        batch_op.create_unique_constraint(
            _CONSTRAINT, ["generated_post_id", "target_channel_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("publish_jobs") as batch_op:
        batch_op.drop_constraint(_CONSTRAINT, type_="unique")
