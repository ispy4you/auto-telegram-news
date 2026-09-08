"""Промпт перестаёт быть одной настройкой и становится списком.

Правила генерации лежали в app_settings под ключом ai_prompt — один текст на
всю панель. Каналы пишут по-разному, и перед каждой генерацией «не как обычно»
приходилось править настройку, а потом возвращать обратно.

Заводим таблицу и переносим действующий текст в первую запись, помеченную как
основную: сразу после обновления ничего не меняется. Старый ключ удаляем, чтобы
не осталось второго места, где лежит то же самое, — именно так когда-то
разъехались два поля промпта.

Revision ID: 0011_prompts
Revises: 0010_media_item_origin
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "0011_prompts"
down_revision = "0010_media_item_origin"
branch_labels = None
depends_on = None

#: Правила по умолчанию на момент переноса. Копия, а не импорт: миграция
#: описывает состояние базы в прошлом, а константа в коде ещё изменится.
DEFAULT_BODY = """Ты редактор новостного Telegram-канала. Перепиши исходную новость в короткий, ясный и нейтральный пост на русском языке.

Стиль: информационный, без воды, без эмодзи, без хештегов, без кликбейта.
Отдельный заголовок первой строкой не нужен, если он выглядит искусственно.
Длина: столько, сколько нужно, чтобы изложить факты из исходника.

Используй только факты из исходного текста. Не выдумывай факты, цифры, имена, причины и последствия.
Не добавляй ссылку на источник, если она не нужна по смыслу.

Если фактов не хватает на самостоятельный пост или исходник рекламный — откажись от новости и коротко объясни почему."""

FIRST_NAME = "Основной"


def upgrade() -> None:
    op.create_table(
        "prompts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    conn = op.get_bind()
    saved = conn.execute(
        text("SELECT value FROM app_settings WHERE key = 'ai_prompt'")
    ).scalar()
    body = (saved or "").strip() or DEFAULT_BODY

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    conn.execute(
        text("INSERT INTO prompts (name, body, is_default, created_at, updated_at) "
             "VALUES (:name, :body, :is_default, :now, :now)"),
        {"name": FIRST_NAME, "body": body, "is_default": True, "now": now},
    )
    conn.execute(text("DELETE FROM app_settings WHERE key = 'ai_prompt'"))


def downgrade() -> None:
    conn = op.get_bind()
    body = conn.execute(
        text("SELECT body FROM prompts WHERE is_default = :yes ORDER BY id LIMIT 1"),
        {"yes": True},
    ).scalar()
    if body:
        conn.execute(
            text("INSERT INTO app_settings (key, value, updated_at) VALUES ('ai_prompt', :v, :now)"),
            {"v": body, "now": datetime.now(timezone.utc).replace(tzinfo=None)},
        )
    op.drop_table("prompts")
