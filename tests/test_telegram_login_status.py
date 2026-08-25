"""Карточка входа в Telegram должна честно называть состояние.

Раньше она показывала «Переподключение…» и когда связь моргнула, и когда
сессии просто не было, — то есть молчала именно тогда, когда от пользователя
требовалось действие.
"""

import pytest

from app.services import telegram_session_store
from app.services.telegram_login import TelegramLoginService


class _FakeListener:
    def __init__(self, active=False, needs_login=False):
        self.is_active = active
        self.is_started = True
        self.needs_login = needs_login


@pytest.fixture
def service(monkeypatch):
    def _build(*, session: str | None, active=False, needs_login=False):
        monkeypatch.setattr(telegram_session_store, "load_string", lambda db=None: session)
        monkeypatch.setattr(
            telegram_session_store, "load_account",
            lambda db=None: {"first_name": "Иван", "username": "ivan", "id": 1},
        )
        return TelegramLoginService(_FakeListener(active, needs_login), lambda: None)

    return _build


def test_no_session_asks_to_log_in(service):
    assert service(session=None).status()["link"] == "none"


def test_working_session_reports_ok(service):
    assert service(session="abc", active=True).status()["link"] == "ok"


def test_rejected_session_is_not_disguised_as_reconnect(service):
    """Главный случай: сессия есть, но Telegram её не принимает."""
    assert service(session="abc", needs_login=True).status()["link"] == "revoked"


def test_temporary_disconnect_stays_connecting(service):
    assert service(session="abc").status()["link"] == "connecting"


def test_login_in_progress_wins_over_everything(service):
    svc = service(session=None)
    svc._state = "qr_pending"
    assert svc.status()["link"] == "logging_in"


def test_account_is_shown_after_restart(service):
    """Логина в этом процессе не было, но кто подключён — известно из БД."""
    status = service(session="abc", active=True).status()

    assert status["account"]["username"] == "ivan"


def test_needs_login_starts_clean():
    """Флаг живёт в памяти процесса: свежий слушатель не должен считать,
    что вход уже требуется."""
    from app.services.telegram_event_listener import TelegramEventListenerService

    assert TelegramEventListenerService().needs_login is False

