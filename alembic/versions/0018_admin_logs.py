"""Действия администратора живут в своём журнале.

Записи оператора в action_logs терялись среди записей конвейера: на одно
нажатие там приходятся десятки строк робота.

Revision ID: 0018_admin_logs
Revises: 0017_generated_entities
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0018_admin_logs"
down_revision = "0017_generated_entities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("authenticated", sa.Boolean(), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_admin_logs_action", "admin_logs", ["action"])
    op.create_index("ix_admin_logs_created_at", "admin_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_admin_logs_created_at", table_name="admin_logs")
    op.drop_index("ix_admin_logs_action", table_name="admin_logs")
    op.drop_table("admin_logs")
