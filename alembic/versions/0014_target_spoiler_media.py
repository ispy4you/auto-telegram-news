"""Канал назначения умеет прятать медиа под спойлер.

Размывает картинку сам Telegram: в запрос на отправку добавляется флаг
has_spoiler, файл при этом уходит нетронутым.

Revision ID: 0014_target_spoiler_media
Revises: 0013_projects_id_sequence
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0014_target_spoiler_media"
down_revision = "0013_projects_id_sequence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default нужен существующим строкам: без него у старых каналов
    # окажется NULL, а колонка объявлена NOT NULL. Снимать его потом незачем —
    # «по умолчанию спойлера нет» остаётся верным и для новых строк.
    op.add_column(
        "target_channels",
        sa.Column("spoiler_media", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("target_channels", "spoiler_media")
