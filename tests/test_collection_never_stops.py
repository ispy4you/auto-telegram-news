"""Сбор не должен вставать молча.

Тестировщик сообщил, что автосбор не работает вообще: кнопка «Собрать сейчас»
ничего не делает, в журнале пусто, ошибок нет. Причина была в одном флаге:
опрос по таймеру отключался в момент запуска слушателя и не включался уже
никогда — даже если слушатель не подключился ни разу.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from app.models import ActionLog
from app.services.scheduler import SchedulerService
from app.services.telegram_event_listener import TelegramEventListenerService
from app.services.telegram_reader import _TELETHON_LOCK


class _FakeListener:
    def __init__(self, *, active: bool, started: bool = True):
        self.is_active = active
        self.is_started = started


def _scheduler(listener) -> SchedulerService:
    return SchedulerService(interval_seconds=120, listener=listener)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Главная регрессия
# ---------------------------------------------------------------------------

def test_a_listener_that_never_connected_does_not_disable_polling():
    """Слушатель запущен, но не на связи — опрос обязан работать.

    Ровно этот случай и убивал сбор: флаг «когда-то запускался» оставался
    поднятым навсегда, а опрос — выключенным навсегда.
    """
    sched = _scheduler(_FakeListener(active=False, started=True))
    assert sched._should_fetch(force=False) is True


def test_without_a_listener_polling_works():
    sched = _scheduler(None)
    assert sched._should_fetch(force=False) is True


def test_a_live_listener_pauses_the_poll_but_never_cancels_it():
    """При живом слушателе опрос — страховка, а не основной режим.

    Он должен идти реже, но идти: слушатель может считать себя подключённым и
    при этом не получать обновления по каналу.
    """
    sched = _scheduler(_FakeListener(active=True))
    assert sched._should_fetch(force=False) is True  # первый прогон

    sched._last_fetch_at = _now()
    assert sched._should_fetch(force=False) is False  # только что опрашивали

    sched._last_fetch_at = _now() - timedelta(minutes=11)
    assert sched._should_fetch(force=False) is True  # страховка снова сработала


def test_manual_collection_ignores_every_pause():
    sched = _scheduler(_FakeListener(active=True))
    sched._last_fetch_at = _now()
    assert sched._should_fetch(force=True) is True


# ---------------------------------------------------------------------------
# Кнопка «Собрать сейчас» больше не врёт
# ---------------------------------------------------------------------------

class _FakeScheduler:
    def __init__(self, result, error=None):
        self._result = result
        self.last_run_error = error
        self.forced = None

    async def trigger_run(self, force_fetch: bool = False):
        self.forced = force_fetch
        return self._result


@pytest.fixture
def with_scheduler(logged_in):
    """Подсовывает приложению планировщик: lifespan в тестах не запускается."""
    import app.main as main_module

    def _install(result, error=None):
        fake = _FakeScheduler(result, error)
        main_module.app.state.scheduler = fake
        return fake

    yield _install
    main_module.app.state._state.pop("scheduler", None)


def _press_collect(logged_in, csrf):
    return logged_in.post("/fetch-now", data={"csrf_token": csrf(logged_in, "/")})


def test_the_button_actually_collects(logged_in, csrf, with_scheduler):
    """Нажатие обязано опрашивать каналы, а не проходить мимо выключателя."""
    fake = with_scheduler(0)
    _press_collect(logged_in, csrf)
    assert fake.forced is True


def test_the_button_says_how_much_it_collected(logged_in, csrf, with_scheduler):
    with_scheduler(3)
    response = _press_collect(logged_in, csrf)
    assert "Собрано новых постов: 3" in response.headers["location"]

    page = logged_in.get(response.headers["location"])
    assert "Собрано новых постов: 3" in page.text


def test_an_empty_run_is_not_reported_as_a_collection(logged_in, csrf, with_scheduler):
    with_scheduler(0)
    response = _press_collect(logged_in, csrf)
    assert "новых постов нет" in logged_in.get(response.headers["location"]).text


def test_a_failed_run_is_not_reported_as_success(logged_in, csrf, with_scheduler):
    """Прогон упал — говорим об этом, а не «новых постов нет»."""
    with_scheduler(0, error="Сессия Telethon не авторизована")
    page = logged_in.get(_press_collect(logged_in, csrf).headers["location"])
    assert "Прогон завершился ошибкой" in page.text
    assert "Сессия Telethon не авторизована" in page.text


def test_a_run_that_did_not_happen_is_not_reported_as_success(logged_in, csrf, with_scheduler):
    """Прогон уже идёт — раньше кнопка молча возвращала на дашборд."""
    with_scheduler(None)
    page = logged_in.get(_press_collect(logged_in, csrf).headers["location"])
    assert "Сбор уже идёт" in page.text


# ---------------------------------------------------------------------------
# Слушатель рассказывает о себе в журнал панели
# ---------------------------------------------------------------------------

@pytest.fixture
def listener(db_session):
    """Слушатель поверх тестовой базы.

    Конструктор прописывает себя в глобальный _ACTIVE_LISTENER — возвращаем
    прежнее значение, чтобы соседние тесты не унаследовали чужой слушатель.
    """
    from app.services import telegram_event_listener as module

    previous = module._ACTIVE_LISTENER
    service = TelegramEventListenerService()
    service._db_factory = sessionmaker(bind=db_session.get_bind())
    yield service
    module._ACTIVE_LISTENER = previous


def test_the_listener_writes_its_state_where_the_operator_looks(listener, db_session):
    listener._note("listener_error", "Слушатель Telegram отключён: сессия отозвана")
    entries = db_session.query(ActionLog).all()
    assert len(entries) == 1
    assert entries[0].action == "listener_error"
    assert "сессия отозвана" in entries[0].message


def test_the_same_state_is_not_written_twice(listener, db_session):
    """Переподключение раз в 30 секунд иначе засыпало бы журнал."""
    for _ in range(5):
        listener._note("listener_error", "Слушатель Telegram отключён: нет сети")
    assert db_session.query(ActionLog).count() == 1


def test_a_change_of_state_is_written(listener, db_session):
    listener._note("listener_error", "Слушатель Telegram отключён: нет сети")
    listener._note("listener_connected", "Слушатель Telegram подключён, каналов в мониторинге: 2.")
    assert db_session.query(ActionLog).count() == 2


def test_a_broken_journal_does_not_take_the_listener_down(listener, db_session):
    """Запись в журнал — не повод уронить сбор."""
    def _explode():
        raise RuntimeError("база недоступна")

    working = listener._db_factory
    listener._db_factory = _explode
    listener._note("listener_error", "сессия отозвана")  # не должно бросить

    # И состояние не считается записанным: когда база вернётся, строка появится.
    listener._db_factory = working
    listener._note("listener_error", "сессия отозвана")
    assert db_session.query(ActionLog).count() == 1


# ---------------------------------------------------------------------------
# Два клиента на одной сессии Telegram не встречаются
# ---------------------------------------------------------------------------

def test_the_listener_holds_the_shared_lock_while_connected(listener):
    """Опрос берёт тот же лок — так он ждёт, вместо того чтобы отключаться."""
    held = []

    async def _fake_connected():
        held.append(_TELETHON_LOCK.locked())

    listener._connect_and_listen_locked = _fake_connected
    asyncio.run(listener._connect_and_listen())

    assert held == [True]
    assert not _TELETHON_LOCK.locked()  # лок отпущен после обрыва


def test_logging_in_does_not_deadlock_against_a_connected_listener(monkeypatch):
    """Вход в Telegram сначала останавливает слушателя, потом берёт лок.

    В обратном порядке было бы намертво: лок держит подключённый слушатель, а
    отпустит он его только после stop(), до которого мы бы не дошли.
    """
    from app.services import telegram_login as login_module

    class _ConnectedListener:
        """Ведёт себя как слушатель, который держит лок всё время работы."""

        def __init__(self):
            self.is_active = True
            self.stopped = False

        async def hold(self):
            await _TELETHON_LOCK.acquire()

        async def stop(self):
            self.stopped = True
            self.is_active = False
            if _TELETHON_LOCK.locked():
                _TELETHON_LOCK.release()

    class _FakeClient:
        async def connect(self):
            return None

    class _Settings:
        telegram_api_id = 1
        telegram_api_hash = "hash"

    monkeypatch.setattr(login_module, "get_settings", lambda: _Settings())

    listener = _ConnectedListener()
    service = login_module.TelegramLoginService(listener, lambda: None)
    monkeypatch.setattr(service._reader, "_client", lambda: _FakeClient())

    async def _scenario():
        await listener.hold()
        try:
            return await asyncio.wait_for(service._ensure_client(), timeout=2)
        finally:
            await service._reset(resume_listener=False)

    client = asyncio.run(_scenario())

    assert isinstance(client, _FakeClient)
    assert listener.stopped is True
    assert not _TELETHON_LOCK.locked()


# ---------------------------------------------------------------------------
# Ошибка сбора видна, но не заваливает журнал
# ---------------------------------------------------------------------------

def _record(db_session, source, message):
    from app.services.news_pipeline import NewsPipelineService

    NewsPipelineService._record_fetch_error(db_session, source, message)


def test_the_same_failure_every_two_minutes_is_written_once(db_session, source):
    for _ in range(10):
        _record(db_session, source, "Сессия Telethon не авторизована")
    assert db_session.query(ActionLog).count() == 1


def test_a_different_failure_is_written(db_session, source):
    _record(db_session, source, "Сессия Telethon не авторизована")
    _record(db_session, source, "Timeout при подключении к Telegram")
    assert db_session.query(ActionLog).count() == 2


def test_the_same_failure_after_the_quiet_period_is_written_again(db_session, source):
    """Канал отвалился снова через полдня — это новость, а не повтор."""
    _record(db_session, source, "Сессия Telethon не авторизована")
    stale = db_session.query(ActionLog).one()
    stale.created_at = _now() - timedelta(hours=2)
    db_session.commit()

    _record(db_session, source, "Сессия Telethon не авторизована")
    assert db_session.query(ActionLog).count() == 2
