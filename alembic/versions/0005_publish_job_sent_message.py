"""Идентификатор доставленного сообщения хранится на задаче публикации.

Раньше он лежал на GeneratedPost — один на все каналы, хотя пост может
уходить в несколько. Из-за этого признак «медиа уже отправлено» протекал
между каналами.

Revision ID: 0005_publish_job_sent_message
Revises: 0004_action_log_index
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_publish_job_sent_message"
down_revision = "0004_action_log_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("publish_jobs", sa.Column("sent_message_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("publish_jobs", "sent_message_id")
