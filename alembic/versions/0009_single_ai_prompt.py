"""Сводит два поля промпта в одно и убирает из него JSON-контракт.

Настройки предлагали править «системный промпт» и «шаблон пользовательского
промпта» по отдельности: правила стиля дублировались в обоих и расходились
после первой правки, а формат ответа лежал в редактируемом тексте — стёр его,
и разобрать ответ модели становилось нечем.

Склеиваем сохранённые тексты в один ключ ai_prompt и отрезаем хвост с JSON:
теперь его дописывает код. Старые ключи не трогаем — если склейка выйдет
кривой, исходники останутся на месте.

Revision ID: 0009_single_ai_prompt
Revises: 0008_generated_post_entities
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0009_single_ai_prompt"
down_revision = "0008_generated_post_entities"
branch_labels = None
depends_on = None

#: Промпты по умолчанию на момент склейки. Копия, а не импорт: миграция
#: описывает состояние базы в прошлом, а константы в коде уже изменились.
OLD_SYSTEM = """Ты профессиональный редактор новостного Telegram-канала. Твоя задача — переписать исходную новость в короткий, ясный и нейтральный Telegram-пост на русском языке.
Стиль: информационный, без воды, без эмодзи, без хештегов, без кликбейта.
Используй только факты из исходного текста. Не выдумывай факты, цифры, имена, причины и последствия.
Если в исходном тексте недостаточно информации для самостоятельного поста, верни suitable=false и краткую причину.
Не добавляй ссылки на источник в текст поста, если они не нужны по смыслу.
Не используй markdown, если это не требуется."""

OLD_TEMPLATE = """Источник: {source_title}
Дата исходной публикации: {published_at_source}
Есть медиа: {has_media}

Исходный текст:
\"\"\"
{original_text}
\"\"\"

Сформируй Telegram-пост на русском языке.
Требования:
- без эмодзи;
- без хештегов;
- без заголовка отдельной строкой, если он выглядит искусственно;
- без воды;
- нейтральный новостной стиль;
- используй столько символов, сколько нужно для полного изложения фактов из исходника;
- использовать только факты из исходника;
- если текст рекламный или мусорный, suitable=false.

Верни строго JSON:
{
  "suitable": true,
  "text": "готовый текст поста",
  "reason": ""
}

Если пост не подходит:
{
  "suitable": false,
  "text": "",
  "reason": "почему не подходит"
}"""


#: Отсюда и до конца шаблона шёл формат ответа.
_JSON_ANCHOR = "Верни строго JSON"


def _merge(system: str | None, template: str | None) -> str | None:
    """Один текст из двух. None — сохранять нечего, хватит дефолта из кода."""
    system = (system or "").strip()
    template = (template or "").strip()
    if not system and not template:
        return None
    if system == OLD_SYSTEM.strip() and template == OLD_TEMPLATE.strip():
        return None

    anchor = template.find(_JSON_ANCHOR)
    if anchor != -1:
        template = template[:anchor].rstrip()

    return "\n\n".join(part for part in (system, template) if part) or None


def _stored(bind, key: str) -> str | None:
    row = bind.execute(text("SELECT value FROM app_settings WHERE key = :key"), {"key": key}).first()
    return row[0] if row else None


def upgrade() -> None:
    bind = op.get_bind()
    if _stored(bind, "ai_prompt") is not None:
        return
    merged = _merge(_stored(bind, "ai_system_prompt"), _stored(bind, "ai_prompt_template"))
    if merged is None:
        return
    bind.execute(
        # updated_at заполняется моделью в Python, поэтому сырому INSERT его
        # нужно проставить самому: колонка NOT NULL.
        text("INSERT INTO app_settings (key, value, updated_at) VALUES (:key, :value, CURRENT_TIMESTAMP)"),
        {"key": "ai_prompt", "value": merged},
    )


def downgrade() -> None:
    op.get_bind().execute(text("DELETE FROM app_settings WHERE key = :key"), {"key": "ai_prompt"})
