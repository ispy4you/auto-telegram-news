"""Индекс по времени записи журнала.

Нужен и для чистки по возрасту, и для страницы логов, которая сортирует по дате.

Revision ID: 0004_action_log_index
Revises: 0003_default_project
"""
from alembic import op

revision = "0004_action_log_index"
down_revision = "0003_default_project"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_action_logs_created_at", "action_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_action_logs_created_at", table_name="action_logs")
