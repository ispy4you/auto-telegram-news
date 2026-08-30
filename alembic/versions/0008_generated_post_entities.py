"""Разметка поста хранится списком сущностей Telegram.

Признак «оформить цитатой» был частным случаем разметки: цитата на весь
текст. Переносим его в общий список и убираем колонку.

Revision ID: 0008_generated_post_entities
Revises: 0007_generated_post_blockquote
"""
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text as sql

revision = "0008_generated_post_entities"
down_revision = "0007_generated_post_blockquote"
branch_labels = None
depends_on = None


def _utf16_len(value: str) -> int:
    # Telegram считает смещения в единицах UTF-16, а не в символах.
    return len(value.encode("utf-16-le")) // 2


def upgrade() -> None:
    op.add_column("generated_posts", sa.Column("entities", sa.Text(), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(sql(
        "SELECT id, edited_text, generated_text FROM generated_posts WHERE as_blockquote"
    )).fetchall()
    for row in rows:
        body = ((row[1] if row[1] else row[2]) or "").strip()
        if not body:
            continue
        entities = [{"type": "blockquote", "offset": 0, "length": _utf16_len(body)}]
        bind.execute(
            sql("UPDATE generated_posts SET entities = :entities WHERE id = :id"),
            {"entities": json.dumps(entities, ensure_ascii=False), "id": row[0]},
        )

    with op.batch_alter_table("generated_posts") as batch:
        batch.drop_column("as_blockquote")


def downgrade() -> None:
    op.add_column(
        "generated_posts",
        sa.Column("as_blockquote", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    bind = op.get_bind()
    rows = bind.execute(sql("SELECT id, entities FROM generated_posts WHERE entities IS NOT NULL")).fetchall()
    for row in rows:
        try:
            quoted = any(item.get("type") == "blockquote" for item in json.loads(row[1]))
        except (TypeError, ValueError):
            continue
        if quoted:
            bind.execute(sql("UPDATE generated_posts SET as_blockquote = true WHERE id = :id"), {"id": row[0]})
    with op.batch_alter_table("generated_posts") as batch:
        batch.drop_column("entities")
