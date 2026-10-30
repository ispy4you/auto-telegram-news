"""Разметка текста модели хранится отдельно от правок редактора.

Модель пишет markdown, панель превращает его в сущности Telegram. Если
положить их в ту же колонку, что и разметку редактора, первая же правка
затрёт оформление, с которым пост пришёл.

Revision ID: 0017_generated_entities
Revises: 0016_spoiler_by_kind
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0017_generated_entities"
down_revision = "0016_spoiler_by_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generated_posts", sa.Column("generated_entities", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("generated_posts", "generated_entities")
