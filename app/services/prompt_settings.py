"""Промпт и часовой пояс.

Часовой пояс — обычная настройка. Правила генерации переехали в отдельную
таблицу: их стало много, и настройкой они быть перестали. Имя функции оставлено
прежним — его знает шлюз и подменяют тесты.
"""

from sqlalchemy.orm import Session

from app.services import prompts, settings_registry

DEFAULT_TIMEZONE = settings_registry.DEFAULT_TIMEZONE


def get_ai_prompt(db: Session | None = None) -> str:
    return prompts.rules(db)


def get_display_timezone(db: Session | None = None) -> str:
    return settings_registry.get("display_timezone", db)
