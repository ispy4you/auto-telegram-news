"""Снимает format()-экранирование в сохранённом шаблоне промпта.

Шаблон подставлялся через str.format(), поэтому JSON-пример в нём писался
удвоенными скобками. Подстановка больше не format(), удвоение стало лишним:
модель увидела бы `{{ "suitable": true }}` и могла повторить это в ответе.

Revision ID: 0006_unescape_prompt_template
Revises: 0005_publish_job_sent_message
"""
import re

from alembic import op
from sqlalchemy import text

revision = "0006_unescape_prompt_template"
down_revision = "0005_publish_job_sent_message"
branch_labels = None
depends_on = None

_KEY = "ai_prompt_template"
_ESCAPE = re.compile(r"\{\{|\}\}")


def _rewrite(pattern: re.Pattern, replace) -> None:
    bind = op.get_bind()
    row = bind.execute(
        text("SELECT value FROM app_settings WHERE key = :key"), {"key": _KEY}
    ).first()
    if row is None or not row[0]:
        return
    updated = pattern.sub(replace, row[0])
    if updated != row[0]:
        bind.execute(
            text("UPDATE app_settings SET value = :value WHERE key = :key"),
            {"value": updated, "key": _KEY},
        )


def upgrade() -> None:
    _rewrite(_ESCAPE, lambda m: m.group(0)[0])


def downgrade() -> None:
    # Возвращаем удвоение: со старым кодом одинарные скобки роняли генерацию.
    _rewrite(re.compile(r"[{}]"), lambda m: m.group(0) * 2)
