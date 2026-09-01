"""Медиа помнит, откуда оно взялось.

Файл из канала можно перекачать заново: диск — кэш, оригинал лежит в Telegram.
У файла, который загрузил редактор, источника нет. Различать их нужно, чтобы
восстановление не ходило за ним в Telegram, а панель не молчала о пропаже.

Revision ID: 0010_media_item_origin
Revises: 0009_single_ai_prompt
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_media_item_origin"
down_revision = "0009_single_ai_prompt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Всё, что уже лежит в таблице, приехало из каналов.
    op.add_column(
        "media_items",
        sa.Column("origin", sa.String(length=16), nullable=False, server_default="source"),
    )


def downgrade() -> None:
    op.drop_column("media_items", "origin")
