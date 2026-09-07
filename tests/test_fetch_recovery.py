"""Источник, который снова отвечает, говорит об этом в журнал.

Одинаковая ошибка сбора пишется не чаще раза в час. Значит одна строка про сбой
и тишина после неё читаются двояко: то ли починилось, то ли продолжает падать
молча. Пара «упало / поднялось» снимает вопрос, не заставляя оператора идти
сверять время последнего сбора на другой странице.
"""

import asyncio

import pytest

from app.models import ActionLog
from app.services.news_pipeline import NewsPipelineService


@pytest.fixture
def pipeline():
    return NewsPipelineService()


def _run(pipeline, db_session, outcome):
    """Один прогон сбора: outcome — исключение или None для успеха."""
    async def _fetch(db, src, limit=None):
        if outcome is not None:
            raise outcome
        return 0

    pipeline.reader.fetch_source = _fetch
    asyncio.run(pipeline.fetch_new_posts(db_session))


def _actions(db_session) -> list[str]:
    return [row.action for row in db_session.query(ActionLog).order_by(ActionLog.id).all()]


def test_a_source_that_answers_again_says_so(pipeline, db_session, source):
    _run(pipeline, db_session, RuntimeError("Timeout при подключении к Telegram"))
    _run(pipeline, db_session, None)

    assert _actions(db_session) == ["fetch_error", "fetch_recovered"]
    recovered = db_session.query(ActionLog).order_by(ActionLog.id).all()[-1]
    assert source.username in recovered.message


def test_a_source_that_never_failed_stays_quiet(pipeline, db_session, source):
    """Успешный сбор — не событие: иначе журнал утонет в «всё хорошо»."""
    for _ in range(5):
        _run(pipeline, db_session, None)

    assert _actions(db_session) == []


def test_recovery_is_announced_once(pipeline, db_session, source):
    _run(pipeline, db_session, RuntimeError("Timeout при подключении к Telegram"))
    for _ in range(5):
        _run(pipeline, db_session, None)

    assert _actions(db_session) == ["fetch_error", "fetch_recovered"]


def test_a_second_outage_is_announced_again(pipeline, db_session, source):
    """Канал упал, поднялся, упал снова — оператор должен видеть обе истории."""
    _run(pipeline, db_session, RuntimeError("Timeout при подключении к Telegram"))
    _run(pipeline, db_session, None)
    _run(pipeline, db_session, RuntimeError("Сессия Telethon не авторизована"))
    _run(pipeline, db_session, None)

    assert _actions(db_session) == [
        "fetch_error", "fetch_recovered", "fetch_error", "fetch_recovered",
    ]


def test_a_broken_journal_does_not_break_the_collection(pipeline, db_session, source, monkeypatch):
    """Запись «снова отвечает» — не повод уронить сбор."""
    def _explode(db, src):
        raise RuntimeError("база недоступна")

    _run(pipeline, db_session, RuntimeError("Timeout при подключении к Telegram"))
    monkeypatch.setattr(NewsPipelineService, "_record_fetch_recovered", staticmethod(_explode))
    _run(pipeline, db_session, None)  # не должно бросить

    assert _actions(db_session) == ["fetch_error"]
