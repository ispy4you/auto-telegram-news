"""Неверный токен бота — это сообщение оператору, а не падение.

Проверка целевого канала падала пятисоткой: токен бота не проходил проверку
формата в aiogram, а создание бота стояло до try, так что ручка не успевала
превратить ошибку в понятный ответ.
"""

import asyncio

import pytest

from app.models import ActionLog, Project, TargetChannel
from app.services import settings_registry
from app.services.telegram_publisher import BotTokenError, TelegramPublisherService


@pytest.fixture
def target(db_session):
    db_session.add(Project(id=1, name="Default", slug="default"))
    channel = TargetChannel(title="Канал", chat_id="@kanal", project_id=1)
    db_session.add(channel)
    db_session.commit()
    db_session.refresh(channel)
    return channel


def test_a_missing_token_is_explained(db_session):
    ok, message = asyncio.run(TelegramPublisherService().test_target("@kanal", db_session))

    assert ok is False
    assert "не задан" in message
    assert "Настройки" in message


def test_a_malformed_token_is_explained(db_session):
    settings_registry.store(db_session, {"telegram_bot_token": "это-не-токен"})

    ok, message = asyncio.run(TelegramPublisherService().test_target("@kanal", db_session))

    assert ok is False
    assert "не похож на настоящий" in message


def test_publishing_also_gets_a_readable_reason(db_session):
    """Тот же токен ломал и публикацию — там он улетал мимо отметки об отказе."""
    settings_registry.store(db_session, {"telegram_bot_token": "это-не-токен"})

    with pytest.raises(BotTokenError) as failure:
        asyncio.run(TelegramPublisherService()._get_bot(db_session))

    assert "не похож на настоящий" in str(failure.value)


def test_the_check_button_answers_instead_of_falling(logged_in, csrf, target, db_session):
    page_token = csrf(logged_in, "/targets")

    response = logged_in.post(f"/targets/{target.id}/test", data={"csrf_token": page_token})

    # Главное: не пятисотка, а возврат на страницу с текстом причины.
    assert response.status_code == 302
    assert "error=" in response.headers["location"]
    entry = db_session.query(ActionLog).filter(ActionLog.action == "target_test").one()
    assert "ok=False" in entry.message
