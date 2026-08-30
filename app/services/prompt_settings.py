"""Промпт и часовой пояс.

Значения живут в реестре настроек; здесь остались удобные имена.
"""

from sqlalchemy.orm import Session

from app.services import settings_registry

DEFAULT_TIMEZONE = settings_registry.DEFAULT_TIMEZONE


def get_ai_prompt(db: Session | None = None) -> str:
    return settings_registry.get("ai_prompt", db)


def get_display_timezone(db: Session | None = None) -> str:
    return settings_registry.get("display_timezone", db)
