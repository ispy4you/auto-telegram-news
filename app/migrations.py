"""Применение миграций Alembic.

Схемой владеет Alembic: ни create_all, ни ручных ALTER TABLE в коде больше нет.
"""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app.database import engine

logger = logging.getLogger(__name__)

_BASELINE = "0001_baseline"
_ROOT = Path(__file__).resolve().parent.parent


def _config() -> Config:
    cfg = Config(str(_ROOT / "alembic.ini"))
    # script_location в ini задан относительно рабочего каталога — приводим
    # к абсолютному, чтобы миграции применялись независимо от того, откуда запущено.
    cfg.set_main_option("script_location", str(_ROOT / "alembic"))
    return cfg


def run_migrations() -> None:
    cfg = _config()
    tables = set(inspect(engine).get_table_names())
    if tables and "alembic_version" not in tables:
        # База создана до внедрения Alembic: её схема уже соответствует базовой
        # ревизии, поэтому фиксируем точку, а не пересоздаём таблицы.
        logger.info("Схема без alembic_version — помечаем как %s", _BASELINE)
        command.stamp(cfg, _BASELINE)
    command.upgrade(cfg, "head")
