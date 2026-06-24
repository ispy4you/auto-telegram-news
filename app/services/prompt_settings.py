from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import AppSetting
from app.services.ai_prompt import DEFAULT_AI_SYSTEM_PROMPT, DEFAULT_AI_USER_PROMPT_TEMPLATE

DEFAULT_TIMEZONE = "Europe/Moscow"


def get_ai_system_prompt(db: Session | None = None) -> str:
    return _get_setting(db, "ai_system_prompt", DEFAULT_AI_SYSTEM_PROMPT)


def get_ai_user_prompt_template(db: Session | None = None) -> str:
    return _get_setting(db, "ai_prompt_template", DEFAULT_AI_USER_PROMPT_TEMPLATE)


def get_display_timezone(db: Session | None = None) -> str:
    return _get_setting(db, "display_timezone", DEFAULT_TIMEZONE)


def _get_setting(db: Session | None, key: str, default: str) -> str:
    own_session = db is None
    session = db or SessionLocal()
    try:
        row = session.get(AppSetting, key)
        if row and row.value.strip() and row.value.strip() != "default":
            return row.value
        return default
    finally:
        if own_session:
            session.close()


def ensure_default_prompt_settings(db: Session) -> None:
    defaults = {
        "ai_system_prompt": DEFAULT_AI_SYSTEM_PROMPT,
        "ai_prompt_template": DEFAULT_AI_USER_PROMPT_TEMPLATE,
    }
    for key, value in defaults.items():
        if not db.get(AppSetting, key):
            db.add(AppSetting(key=key, value=value))
