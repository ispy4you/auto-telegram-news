"""Схема, которую строят миграции, должна совпадать с моделями.

Без этого теста базовая ревизия может незаметно разойтись с models.py:
на новой базе появится одна схема, на существующей — другая.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

import app.database as database
from app.database import Base
import app.models  # noqa: F401  — регистрирует таблицы в Base.metadata

_ROOT = Path(__file__).resolve().parent.parent


def _alembic_config() -> Config:
    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "alembic"))
    return cfg


def _schema(engine) -> dict[str, dict[str, bool]]:
    """{таблица: {колонка: nullable}} — без типов, они различаются по диалектам."""
    inspector = inspect(engine)
    return {
        table: {col["name"]: bool(col["nullable"]) for col in inspector.get_columns(table)}
        for table in inspector.get_table_names()
        if table != "alembic_version"
    }


def test_migrations_produce_the_schema_models_describe(tmp_path, monkeypatch):
    from_models = create_engine(f"sqlite:///{tmp_path / 'models.db'}")
    Base.metadata.create_all(bind=from_models)

    from_migrations = create_engine(f"sqlite:///{tmp_path / 'migrations.db'}")
    # env.py берёт engine из app.database — подменяем на временный.
    monkeypatch.setattr(database, "engine", from_migrations)
    command.upgrade(_alembic_config(), "head")

    assert _schema(from_migrations) == _schema(from_models)


def test_publish_jobs_pair_is_unique(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'unique.db'}")
    monkeypatch.setattr(database, "engine", engine)
    command.upgrade(_alembic_config(), "head")

    constraints = inspect(engine).get_unique_constraints("publish_jobs")
    pairs = [set(c["column_names"]) for c in constraints]
    assert {"generated_post_id", "target_channel_id"} in pairs
