"""Спойлер выбирается по типу медиа, а не одной галочкой.

Прятать фотографии и прятать видео — разные решения, и каналу нужно уметь
сказать «только видео».

Revision ID: 0016_spoiler_by_kind
Revises: 0015_source_invite
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0016_spoiler_by_kind"
down_revision = "0015_source_invite"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "target_channels",
        sa.Column("spoiler_media_mode", sa.String(length=8), nullable=False, server_default="off"),
    )
    # Включённая галочка значила «прятать всё» — это и переносим.
    op.execute("UPDATE target_channels SET spoiler_media_mode = 'all' WHERE spoiler_media")
    # batch_alter_table: SQLite не умеет удалять колонку ALTER-ом и
    # перестраивает таблицу целиком, PostgreSQL же выполнит обычный ALTER.
    with op.batch_alter_table("target_channels") as batch:
        batch.drop_column("spoiler_media")


def downgrade() -> None:
    op.add_column(
        "target_channels",
        sa.Column("spoiler_media", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute("UPDATE target_channels SET spoiler_media = true WHERE spoiler_media_mode <> 'off'")
    with op.batch_alter_table("target_channels") as batch:
        batch.drop_column("spoiler_media_mode")
