"""Реестр настроек, которые хранятся в БД и правятся в панели.

Каждая настройка объявлена ровно один раз: имя, тип, значение по умолчанию
и границы. Раньше это знание было размазано по десяти модулям — приватная
функция импортировалась внутри тел функций, разбор значения с try/except
повторялся при каждом чтении, а границы дублировались ещё раз в обработчике
формы, местами расходясь с дефолтами.
"""

from dataclasses import dataclass
from typing import Any, Callable
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models import AppSetting
from app.services.ai_prompt import DEFAULT_AI_PROMPT

INT, FLOAT, STR, BOOL = "int", "float", "str", "bool"

DEFAULT_TIMEZONE = "Europe/Moscow"
# Косинус для paraphrase-multilingual-MiniLM: пересказ одной новости разными
# источниками обычно даёт 0.85–0.95, разные новости на одну тему — 0.75–0.85.
# 0.90 выбран консервативно: лучше пропустить повтор, чем склеить разные новости.
DEFAULT_SEMANTIC_THRESHOLD = 0.90
DEFAULT_RETENTION_DAYS = 30


@dataclass(frozen=True)
class Setting:
    key: str
    kind: str
    #: значение или функция от настроек окружения (для тех, у кого дефолт в .env)
    default: Any
    minimum: float | None = None
    maximum: float | None = None
    #: пробелы по краям бессмысленны у идентификаторов и адресов, но значимы у промптов
    strip: bool = False
    #: форма не перерисовывает секреты, поэтому пустое поле значит «оставить как есть»
    secret: bool = False
    validate: Callable[[str], bool] | None = None


def _valid_timezone(value: str) -> bool:
    try:
        ZoneInfo(value)
        return True
    except Exception:
        return False


REGISTRY: dict[str, Setting] = {s.key: s for s in [
    # ── Сбор
    Setting("fetch_interval_seconds", INT, lambda e: e.fetch_interval_seconds, minimum=30, maximum=86400),
    Setting("default_lookback_limit", INT, lambda e: e.default_lookback_limit, minimum=1, maximum=500),
    Setting("max_post_age_hours", FLOAT, 24.0, minimum=0, maximum=8760),
    Setting("max_media_mb", INT, lambda e: e.max_media_mb, minimum=1, maximum=500),
    Setting("action_log_retention_days", INT, DEFAULT_RETENTION_DAYS, minimum=0, maximum=365),

    # ── Дедупликация
    Setting("duplicate_threshold", INT, 88, minimum=50, maximum=100),
    Setting("semantic_threshold", FLOAT, DEFAULT_SEMANTIC_THRESHOLD, minimum=0, maximum=1),

    # ── Публикация и уведомления
    Setting("global_auto_publish_enabled", BOOL, False),
    Setting("notify_on_error", BOOL, False),
    Setting("notify_draft_threshold", INT, 0, minimum=0, maximum=100000),
    Setting("operator_chat_id", STR, "", strip=True),
    Setting("telegram_bot_token", STR, lambda e: e.telegram_bot_token or "", strip=True, secret=True),
    Setting("display_timezone", STR, DEFAULT_TIMEZONE, strip=True, validate=_valid_timezone),

    # ── AI
    Setting("ai_prompt", STR, DEFAULT_AI_PROMPT),
    Setting("ai_prompt_mode", STR, "simple", strip=True),
    Setting("ai_prompt_form", STR, "{}"),
    Setting("timeweb_ai_gateway_base_url", STR, lambda e: e.timeweb_ai_gateway_base_url or "", strip=True),
    Setting("timeweb_ai_gateway_api_key", STR, lambda e: e.timeweb_ai_gateway_api_key or "", strip=True, secret=True),
    Setting("timeweb_ai_gateway_model", STR, lambda e: e.timeweb_ai_gateway_model or "", strip=True),
    Setting("ai_temperature", FLOAT, lambda e: e.ai_temperature, minimum=0, maximum=2),
    Setting("ai_max_tokens", INT, lambda e: e.ai_max_tokens, minimum=1, maximum=32000),
    Setting("ai_timeout_seconds", INT, lambda e: e.ai_timeout_seconds, minimum=5, maximum=300),
]}

#: Ключи, которые редактируются формой настроек, в порядке объявления.
FORM_KEYS = tuple(REGISTRY)


def spec(key: str) -> Setting:
    try:
        return REGISTRY[key]
    except KeyError:
        raise KeyError(f"Настройка {key!r} не объявлена в реестре") from None


def default(key: str, env: Settings | None = None) -> Any:
    setting = spec(key)
    value = setting.default
    return value((env or get_settings())) if callable(value) else value


def _stored(db: Session, key: str) -> str | None:
    row = db.get(AppSetting, key)
    if row is None:
        return None
    value = row.value.strip() if row.value else ""
    # "default" исторически означало «вернуться к значению по умолчанию».
    return None if value in ("", "default") else row.value


def _coerce(setting: Setting, raw: str) -> Any:
    if setting.kind == BOOL:
        return raw.strip().lower() in ("true", "1", "yes", "on")
    if setting.kind == INT:
        return _clamp(setting, int(float(raw)))
    if setting.kind == FLOAT:
        return _clamp(setting, float(raw))
    if setting.validate and not setting.validate(raw):
        raise ValueError(f"недопустимое значение {raw!r}")
    return raw.strip() if setting.strip else raw


def _clamp(setting: Setting, value: float):
    if setting.minimum is not None:
        value = max(setting.minimum, value)
    if setting.maximum is not None:
        value = min(setting.maximum, value)
    return float(value) if setting.kind == FLOAT else int(value)


def get(key: str, db: Session | None = None) -> Any:
    """Типизированное значение настройки: из БД, иначе по умолчанию."""
    setting = spec(key)
    session = db or SessionLocal()
    try:
        raw = _stored(session, key)
    finally:
        if db is None:
            session.close()
    if raw is None:
        return default(key)
    try:
        return _coerce(setting, raw)
    except (ValueError, TypeError):
        return default(key)


def normalize(key: str, raw: str) -> str:
    """Приводит значение из формы к тому виду, в котором его можно хранить."""
    setting = spec(key)
    if setting.kind == BOOL:
        return "true" if raw.strip().lower() in ("true", "1", "yes", "on") else "false"
    try:
        value = _coerce(setting, raw)
    except (ValueError, TypeError):
        value = default(key)
    if setting.kind == FLOAT:
        return "%g" % value
    return str(value)


def reset(db: Session, key: str) -> None:
    """Убирает переопределение: дальше для настройки действует значение по умолчанию."""
    spec(key)
    row = db.get(AppSetting, key)
    if row:
        db.delete(row)
        db.commit()


def store(db: Session, values: dict[str, str]) -> None:
    """Пишет уже нормализованные значения. Ключи вне реестра отвергаются."""
    for key, value in values.items():
        spec(key)
        row = db.get(AppSetting, key)
        if row:
            row.value = value
        else:
            db.add(AppSetting(key=key, value=value))
    db.commit()
