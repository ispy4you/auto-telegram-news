"""Промпты и часовой пояс.

Значения живут в реестре настроек; здесь остались только удобные имена
и заполнение промптов по умолчанию при первом запуске.
"""

from sqlalchemy.orm import Session

from app.models import AppSetting
from app.services import settings_registry
from app.services.ai_prompt import DEFAULT_AI_SYSTEM_PROMPT, DEFAULT_AI_USER_PROMPT_TEMPLATE

DEFAULT_TIMEZONE = settings_registry.DEFAULT_TIMEZONE


def get_ai_system_prompt(db: Session | None = None) -> str:
    return settings_registry.get("ai_system_prompt", db)


def get_ai_user_prompt_template(db: Session | None = None) -> str:
    return settings_registry.get("ai_prompt_template", db)


def get_display_timezone(db: Session | None = None) -> str:
    return settings_registry.get("display_timezone", db)


def ensure_default_prompt_settings(db: Session) -> None:
    """Кладёт промпты в БД, чтобы форма настроек показывала их текст."""
    defaults = {
        "ai_system_prompt": DEFAULT_AI_SYSTEM_PROMPT,
        "ai_prompt_template": DEFAULT_AI_USER_PROMPT_TEMPLATE,
    }
    for key, value in defaults.items():
        if not db.get(AppSetting, key):
            db.add(AppSetting(key=key, value=value))
