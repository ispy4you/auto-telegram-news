"""Пропущенный пост либо приедет позже, либо будет объяснён.

Две дырки, обе молчаливые. После простоя выборка бралась «сверху»: если за это
время вышло больше limit постов, last_message_id прыгал на самый свежий, и всё,
что между, пропадало навсегда. А посты, отброшенные по возрасту, знал только
logger.debug, который на уровне INFO не печатается — в ленте просто не хватало
новости, и понять почему было негде.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models import ActionLog, RawPost
from app.services import settings_registry
from app.services.telegram_reader import TelegramReaderService


class _Client:
    """Телеграм на минималках: отдаёт заданные сообщения и помнит запрос."""

    def __init__(self, messages):
        self._messages = messages
        self.kwargs = None

    async def get_entity(self, username):
        return SimpleNamespace(username=username)

    def iter_messages(self, entity, **kwargs):
        self.kwargs = kwargs
        chosen = sorted(self._messages, key=lambda m: m.id)
        min_id = kwargs.get("min_id")
        if min_id:
            chosen = [m for m in chosen if m.id > min_id]
        limit = kwargs.get("limit")
        if kwargs.get("reverse"):
            chosen = chosen[:limit] if limit else chosen
        else:
            chosen = list(reversed(chosen))
            chosen = chosen[:limit] if limit else chosen

        async def _gen():
            for m in chosen:
                yield m

        return _gen()


def _message(msg_id: int, *, age_hours: float = 0.0):
    return SimpleNamespace(
        id=msg_id,
        grouped_id=None,
        message=f"Новость {msg_id}",
        date=datetime.now(timezone.utc) - timedelta(hours=age_hours),
        media=None,
    )


def _collect(db, source, messages, limit=3):
    reader = TelegramReaderService()
    client = _Client(messages)
    pending, last_id, skipped = asyncio.run(
        reader._collect_pending(client, db, source, limit)
    )
    return reader, client, pending, last_id, skipped


# ---------------------------------------------------------------------------
# Догон идёт с начала очереди
# ---------------------------------------------------------------------------

def test_the_catchup_starts_from_the_oldest_unread(db_session, source):
    """Главная регрессия: раньше брались самые свежие, и середина пропадала."""
    source.last_message_id = 10
    db_session.commit()

    _, client, pending, last_id, _ = _collect(
        db_session, source, [_message(i) for i in range(11, 21)], limit=3
    )

    assert client.kwargs["reverse"] is True
    assert [item["msg"].id for item in pending] == [11, 12, 13]
    assert last_id == 13, "следующий прогон продолжит с 13, а не с 20"


def test_the_queue_is_drained_over_several_runs(db_session, source):
    """Ничего не теряется: остаток разбирается следующими прогонами."""
    source.last_message_id = 10
    db_session.commit()
    messages = [_message(i) for i in range(11, 18)]

    seen = []
    for _ in range(3):
        reader, _, pending, last_id, _ = _collect(db_session, source, messages, limit=3)
        seen += [item["msg"].id for item in pending]
        reader._flush_pending(db_session, source, pending, last_id)

    assert seen == [11, 12, 13, 14, 15, 16, 17]


def test_the_very_first_fetch_takes_the_newest(db_session, source):
    """У нового источника истории нет — нужны последние, а не первые в канале."""
    _, client, pending, _, _ = _collect(
        db_session, source, [_message(i) for i in range(1, 21)], limit=3
    )

    assert "reverse" not in client.kwargs
    assert [item["msg"].id for item in pending] == [16, 17, 18, 19, 20]


# ---------------------------------------------------------------------------
# Отброшенные по возрасту объяснены
# ---------------------------------------------------------------------------

def test_posts_dropped_by_age_are_counted(db_session, source):
    settings_registry.store(db_session, {"max_post_age_hours": "24"})
    source.last_message_id = 10
    db_session.commit()

    _, _, pending, _, skipped = _collect(
        db_session,
        source,
        [_message(11, age_hours=100), _message(12, age_hours=90), _message(13)],
        limit=10,
    )

    assert skipped == 2
    assert [item["msg"].id for item in pending] == [13]


def test_the_journal_explains_the_gap(db_session, source):
    settings_registry.store(db_session, {"max_post_age_hours": "24"})
    reader = TelegramReaderService()

    reader._flush_pending(db_session, source, [], 42, skipped_old=7)

    entry = db_session.query(ActionLog).filter_by(action="fetch_skipped_old").one()
    assert "пропущено 7 постов" in entry.message
    assert "24 ч" in entry.message
    assert source.username in entry.message


def test_the_gap_is_explained_once_an_hour(db_session, source):
    """После долгого простоя очередь разбирается пачками, и каждая уходит за
    отсечку целиком. Без окна тишины это десяток одинаковых строк подряд."""
    settings_registry.store(db_session, {"max_post_age_hours": "24"})
    reader = TelegramReaderService()

    for _ in range(5):
        reader._flush_pending(db_session, source, [], 42, skipped_old=50)

    assert db_session.query(ActionLog).filter_by(action="fetch_skipped_old").count() == 1


def test_an_hour_later_the_gap_is_mentioned_again(db_session, source):
    settings_registry.store(db_session, {"max_post_age_hours": "24"})
    reader = TelegramReaderService()

    reader._flush_pending(db_session, source, [], 42, skipped_old=50)
    stale = db_session.query(ActionLog).filter_by(action="fetch_skipped_old").one()
    stale.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
    db_session.commit()
    reader._flush_pending(db_session, source, [], 43, skipped_old=50)

    assert db_session.query(ActionLog).filter_by(action="fetch_skipped_old").count() == 2


def test_another_source_is_not_silenced_by_the_first(db_session, source):
    """Окно тишины — на источник, а не на всю панель."""
    from app.models import SourceChannel

    other = SourceChannel(title="Второй", username="second", url="https://t.me/second")
    db_session.add(other)
    db_session.commit()
    reader = TelegramReaderService()

    reader._flush_pending(db_session, source, [], 42, skipped_old=10)
    reader._flush_pending(db_session, other, [], 42, skipped_old=10)

    assert db_session.query(ActionLog).filter_by(action="fetch_skipped_old").count() == 2


def test_nothing_is_written_when_nothing_was_dropped(db_session, source):
    reader = TelegramReaderService()

    reader._flush_pending(db_session, source, [], 42, skipped_old=0)

    assert db_session.query(ActionLog).filter_by(action="fetch_skipped_old").all() == []


def test_a_normal_batch_still_reports_what_it_collected(db_session, source):
    """Соседняя запись не должна потеряться из-за новой."""
    source.last_message_id = 10
    db_session.commit()
    reader, _, pending, last_id, _ = _collect(
        db_session, source, [_message(11), _message(12)], limit=10
    )

    assert reader._flush_pending(db_session, source, pending, last_id) == 2
    actions = [row.action for row in db_session.query(ActionLog).all()]
    assert actions == ["fetch_post_telethon"]
    assert db_session.query(RawPost).count() == 2
