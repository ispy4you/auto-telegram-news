"""Источником может быть закрытый канал — по ссылке-приглашению.

У такого канала нет имени, читать его можно только по id, а попасть в него —
только через заявку, которую одобряет администратор.

Revision ID: 0015_source_invite
Revises: 0014_target_spoiler_media
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0015_source_invite"
down_revision = "0014_target_spoiler_media"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("source_channels", sa.Column("invite_hash", sa.String(length=64), nullable=True))
    # Всё, что заведено до этой миграции, добавлено по имени — значит канал
    # публичный и вступать в него никуда не надо.
    op.add_column(
        "source_channels",
        sa.Column("join_state", sa.String(length=16), nullable=False, server_default="open"),
    )


def downgrade() -> None:
    op.drop_column("source_channels", "join_state")
    op.drop_column("source_channels", "invite_hash")
