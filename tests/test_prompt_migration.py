"""Склейка двух полей промпта в одно.

Правки пользователя терять нельзя, а JSON-контракт из них надо убрать: теперь
его дописывает код, и второй экземпляр в промпте только путает модель.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

import app.database as database

_ROOT = Path(__file__).resolve().parent.parent
_BEFORE = "0008_generated_post_entities"


def _migration():
    path = _ROOT / "alembic" / "versions" / "0009_single_ai_prompt.py"
    spec = importlib.util.spec_from_file_location("migration_0009", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _upgraded(tmp_path, monkeypatch, stored: dict[str, str]):
    engine = create_engine(f"sqlite:///{tmp_path / 'prompts.db'}")
    monkeypatch.setattr(database, "engine", engine)
    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "alembic"))

    command.upgrade(cfg, _BEFORE)
    with engine.begin() as conn:
        for key, value in stored.items():
            conn.execute(
                text("INSERT INTO app_settings (key, value, updated_at) VALUES (:k, :v, CURRENT_TIMESTAMP)"),
                {"k": key, "v": value},
            )
    command.upgrade(cfg, "head")

    with engine.begin() as conn:
        return conn.execute(text("SELECT value FROM app_settings WHERE key = 'ai_prompt'")).scalar()


def test_edited_prompts_are_merged_without_the_json_contract(tmp_path, monkeypatch):
    merged = _upgraded(tmp_path, monkeypatch, {
        "ai_system_prompt": "Мои правила.",
        "ai_prompt_template": 'Пиши коротко.\n\nВерни строго JSON:\n{"suitable": true}',
    })

    assert merged == "Мои правила.\n\nПиши коротко."


def test_untouched_defaults_leave_the_new_default_in_charge(tmp_path, monkeypatch):
    """Дефолты лежали в базе у всех: приложение клало их туда при старте."""
    old = _migration()
    merged = _upgraded(tmp_path, monkeypatch, {
        "ai_system_prompt": old.OLD_SYSTEM,
        "ai_prompt_template": old.OLD_TEMPLATE,
    })

    assert merged is None


def test_a_fresh_install_gets_nothing_stored(tmp_path, monkeypatch):
    assert _upgraded(tmp_path, monkeypatch, {}) is None


@pytest.mark.parametrize("stored, expected", [
    ({"ai_system_prompt": "Только правила."}, "Только правила."),
    ({"ai_prompt_template": "Только шаблон."}, "Только шаблон."),
    ({"ai_system_prompt": "  ", "ai_prompt_template": "  "}, None),
])
def test_half_filled_settings(tmp_path, monkeypatch, stored, expected):
    assert _upgraded(tmp_path, monkeypatch, stored) == expected


def test_the_old_texts_stay_in_place(tmp_path, monkeypatch):
    """Если склейка выйдет кривой, исходники должно быть где посмотреть."""
    engine = create_engine(f"sqlite:///{tmp_path / 'kept.db'}")
    monkeypatch.setattr(database, "engine", engine)
    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "alembic"))

    command.upgrade(cfg, _BEFORE)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO app_settings (key, value, updated_at) VALUES ('ai_system_prompt', 'Мои правила.', CURRENT_TIMESTAMP)"
        ))
    command.upgrade(cfg, "head")

    with engine.begin() as conn:
        kept = conn.execute(text("SELECT value FROM app_settings WHERE key = 'ai_system_prompt'")).scalar()
    assert kept == "Мои правила."
