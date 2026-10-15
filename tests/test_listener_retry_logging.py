"""Повторяющийся обрыв не должен забивать лог.

На установке без входа в Telegram слушатель каждые 30 секунд писал одно и то
же предупреждение. В коротком логе хостинга из 200 строк 156 оказались этой
фразой: панель работала, а лог выглядел как авария.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services import telegram_event_listener as module
from app.services.telegram_event_listener import (
    _RECONNECT_DELAY,
    _RECONNECT_DELAY_MAX,
    TelegramEventListenerService,
)

NO_SESSION = "Telethon-сессия не авторизована. Войдите в Telegram в админке."


@pytest.fixture
def listener():
    """Конструктор прописывает себя в глобальный _ACTIVE_LISTENER — возвращаем его."""
    previous = module._ACTIVE_LISTENER
    yield TelegramEventListenerService()
    module._ACTIVE_LISTENER = previous


def _warnings(caplog):
    return [r for r in caplog.records if r.levelname == "WARNING"]


def test_the_same_reason_is_logged_once(listener, caplog):
    with caplog.at_level("WARNING"):
        for _ in range(10):
            listener._log_failure(NO_SESSION)

    assert len(_warnings(caplog)) == 1
    assert "не авторизована" in _warnings(caplog)[0].getMessage()


def test_pause_grows_up_to_the_ceiling(listener, caplog):
    with caplog.at_level("WARNING"):
        listener._log_failure(NO_SESSION)
        assert listener._reconnect_delay == _RECONNECT_DELAY

        listener._log_failure(NO_SESSION)
        assert listener._reconnect_delay == _RECONNECT_DELAY * 2

        for _ in range(20):
            listener._log_failure(NO_SESSION)
        assert listener._reconnect_delay == _RECONNECT_DELAY_MAX


def test_another_reason_is_logged_at_once_and_resets_the_pause(listener, caplog):
    with caplog.at_level("WARNING"):
        for _ in range(5):
            listener._log_failure(NO_SESSION)
        listener._log_failure("нет сети")

    messages = [r.getMessage() for r in _warnings(caplog)]
    assert len(messages) == 2
    assert "нет сети" in messages[1]
    # Смена причины — это новая история: ждать пять минут до первой попытки незачем.
    assert listener._reconnect_delay == _RECONNECT_DELAY


def test_after_an_hour_the_reason_repeats_with_the_number_of_attempts(listener, caplog):
    with caplog.at_level("WARNING"):
        listener._log_failure(NO_SESSION)
        listener._log_failure(NO_SESSION)
        # Отматываем отметку записи назад — так выглядит час непрерывных попыток.
        listener._failure_logged_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
        )
        listener._log_failure(NO_SESSION)

    messages = [r.getMessage() for r in _warnings(caplog)]
    assert len(messages) == 2
    assert "попыток: 3" in messages[1]


def test_a_successful_connection_forgets_the_failure(listener, caplog):
    with caplog.at_level("WARNING"):
        for _ in range(5):
            listener._log_failure(NO_SESSION)
        listener._forget_failure()
        listener._log_failure(NO_SESSION)

    # После удачного подключения тот же обрыв — это снова новость.
    assert len(_warnings(caplog)) == 2
    assert listener._reconnect_delay == _RECONNECT_DELAY
