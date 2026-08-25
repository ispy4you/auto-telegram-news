"""Реестр настроек: типы, границы и значения по умолчанию объявлены один раз."""

import pytest

from app.models import AppSetting
from app.services import settings_registry as reg


def _store(db, key: str, value: str) -> None:
    db.add(AppSetting(key=key, value=value))
    db.commit()


def test_missing_value_falls_back_to_default(db_session):
    assert reg.get("duplicate_threshold", db_session) == 88


def test_stored_value_wins(db_session):
    _store(db_session, "duplicate_threshold", "95")
    assert reg.get("duplicate_threshold", db_session) == 95


def test_value_is_clamped_to_declared_bounds(db_session):
    _store(db_session, "duplicate_threshold", "1000")
    assert reg.get("duplicate_threshold", db_session) == 100


def test_garbage_falls_back_to_default(db_session):
    _store(db_session, "semantic_threshold", "не число")
    assert reg.get("semantic_threshold", db_session) == reg.DEFAULT_SEMANTIC_THRESHOLD


def test_blank_and_literal_default_mean_absent(db_session):
    _store(db_session, "notify_draft_threshold", "   ")
    assert reg.get("notify_draft_threshold", db_session) == 0
    db_session.query(AppSetting).delete()
    _store(db_session, "notify_draft_threshold", "default")
    assert reg.get("notify_draft_threshold", db_session) == 0


def test_booleans_are_typed(db_session):
    assert reg.get("global_auto_publish_enabled", db_session) is False
    _store(db_session, "global_auto_publish_enabled", "true")
    assert reg.get("global_auto_publish_enabled", db_session) is True


def test_invalid_timezone_falls_back(db_session):
    _store(db_session, "display_timezone", "Middle/Earth")
    assert reg.get("display_timezone", db_session) == reg.DEFAULT_TIMEZONE


def test_normalize_clamps_and_formats():
    assert reg.normalize("duplicate_threshold", "1000") == "100"
    assert reg.normalize("duplicate_threshold", "мусор") == "88"
    assert reg.normalize("semantic_threshold", "5") == "1"
    assert reg.normalize("global_auto_publish_enabled", "on") == "true"
    assert reg.normalize("global_auto_publish_enabled", "") == "false"
    assert reg.normalize("operator_chat_id", "  @chat  ") == "@chat"


def test_unknown_keys_are_rejected(db_session):
    with pytest.raises(KeyError):
        reg.get("не_существует", db_session)
    with pytest.raises(KeyError):
        reg.store(db_session, {"не_существует": "1"})


def test_every_form_key_round_trips(db_session):
    """Значение по умолчанию должно проходить нормализацию без потерь."""
    for key in reg.FORM_KEYS:
        raw = reg.normalize(key, str(reg.default(key)))
        reg.store(db_session, {key: raw})
        assert reg.get(key, db_session) is not None


def test_reset_removes_the_override(db_session):
    """Сохранённое значение переживает деплой — снять его можно только явно."""
    _store(db_session, "semantic_threshold", "0")
    assert reg.get("semantic_threshold", db_session) == 0

    reg.reset(db_session, "semantic_threshold")

    assert reg.get("semantic_threshold", db_session) == reg.DEFAULT_SEMANTIC_THRESHOLD


def test_reset_is_harmless_when_there_is_no_override(db_session):
    reg.reset(db_session, "semantic_threshold")

    assert reg.get("semantic_threshold", db_session) == reg.DEFAULT_SEMANTIC_THRESHOLD


def test_reset_of_unknown_key_is_rejected(db_session):
    with pytest.raises(KeyError):
        reg.reset(db_session, "не_существует")
