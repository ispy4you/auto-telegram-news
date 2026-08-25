"""Пропавшие медиафайлы перекачиваются из источника, а не теряются молча."""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import ActionLog, GeneratedPost, MediaItem, RawPost, TargetChannel
from app.services.telegram_publisher import TelegramPublisherService


class _FakeBot:
    def __init__(self):
        self.calls: list[str] = []

    async def _record(self, name: str):
        self.calls.append(name)
        return SimpleNamespace(message_id=1)

    async def send_message(self, **_):
        return await self._record("send_message")

    async def send_photo(self, **_):
        return await self._record("send_photo")


def _publisher(bot: _FakeBot) -> TelegramPublisherService:
    publisher = TelegramPublisherService()

    async def _get_bot(_db=None):
        return bot

    publisher._get_bot = _get_bot
    return publisher


@pytest.fixture
def post_with_missing_media(db_session, source, tmp_path):
    raw = RawPost(source_id=source.id, telegram_message_id=7, text_hash="h", original_text="raw")
    db_session.add(raw)
    db_session.flush()
    item = MediaItem(
        raw_post_id=raw.id,
        telegram_message_id=7,
        media_type="photo",
        file_path=str(tmp_path / "gone.jpg"),  # файла нет: диск не пережил деплой
    )
    db_session.add(item)
    generated = GeneratedPost(raw_post_id=raw.id, generated_text="текст")
    db_session.add(generated)
    db_session.add(TargetChannel(title="T", chat_id="@t"))
    db_session.commit()
    db_session.refresh(generated)
    return generated, item


def _target_id(db) -> int:
    return db.scalars(select(TargetChannel)).first().id


def _actions(db) -> list[str]:
    return list(db.scalars(select(ActionLog.action)).all())


def test_missing_media_is_refetched_before_publishing(db_session, post_with_missing_media, tmp_path):
    generated, item = post_with_missing_media
    bot = _FakeBot()
    publisher = _publisher(bot)

    async def fake_restore(db, _raw_post):
        restored = tmp_path / "restored.jpg"
        restored.write_bytes(b"jpeg")
        item.file_path = str(restored)
        db.commit()
        return 1

    publisher._restore_missing_media = fake_restore

    asyncio.run(publisher.publish_generated_post(db_session, generated.id, _target_id(db_session)))

    assert bot.calls == ["send_photo"], "медиа должно уехать вместе с постом"
    assert "media_missing" not in _actions(db_session)


def test_unrecoverable_media_is_reported_not_swallowed(db_session, post_with_missing_media):
    generated, _item = post_with_missing_media
    bot = _FakeBot()
    publisher = _publisher(bot)

    async def fake_restore(_db, _raw_post):
        return 0  # сообщение в источнике удалили — восстановить нечего

    publisher._restore_missing_media = fake_restore

    asyncio.run(publisher.publish_generated_post(db_session, generated.id, _target_id(db_session)))

    assert bot.calls == ["send_message"], "без картинок пост всё равно нужен"
    assert "media_missing" in _actions(db_session), "потеря медиа не должна быть молчаливой"


def test_restore_failure_does_not_break_publishing(db_session, post_with_missing_media, monkeypatch):
    """Недоступный Telegram — не повод не опубликовать текст."""
    from app.services import telegram_reader

    async def boom(_self, _db, _raw_post):
        raise RuntimeError("Telegram недоступен")

    monkeypatch.setattr(telegram_reader.TelegramReaderService, "restore_media", boom)

    generated, _item = post_with_missing_media
    bot = _FakeBot()

    asyncio.run(_publisher(bot).publish_generated_post(db_session, generated.id, _target_id(db_session)))

    assert bot.calls == ["send_message"]
    assert "media_missing" in _actions(db_session)


# ── Какой Telethon-клиент используется для перекачки ─────────────────────────

class _FakeTelethonClient:
    def __init__(self):
        self.connected = False
        self.disconnected = False

    async def connect(self):
        self.connected = True

    async def is_user_authorized(self):
        return True

    async def get_entity(self, _username):
        return object()

    async def get_messages(self, _entity, ids=None):
        return []

    async def disconnect(self):
        self.disconnected = True


@pytest.fixture
def raw_with_missing_file(db_session, source, tmp_path):
    raw = RawPost(source_id=source.id, telegram_message_id=9, text_hash="h", original_text="t")
    db_session.add(raw)
    db_session.flush()
    db_session.add(MediaItem(
        raw_post_id=raw.id,
        telegram_message_id=9,
        media_type="photo",
        file_path=str(tmp_path / "нет-такого.jpg"),
    ))
    db_session.commit()
    db_session.refresh(raw)
    return raw


def test_restore_borrows_the_listener_client(db_session, raw_with_missing_file, monkeypatch):
    """Второй Telethon-клиент на живой сессии — то, чего проект избегает намеренно."""
    from app.services import telegram_event_listener, telegram_reader

    listener_client = _FakeTelethonClient()
    monkeypatch.setattr(telegram_event_listener, "active_client", lambda: listener_client)
    monkeypatch.setattr(
        telegram_reader.TelegramReaderService,
        "_client",
        lambda self: pytest.fail("нельзя поднимать второй клиент, пока слушатель работает"),
    )

    restored = asyncio.run(telegram_reader.TelegramReaderService().restore_media(db_session, raw_with_missing_file))

    assert restored == 0
    assert listener_client.disconnected is False, "чужой клиент отключать нельзя"


def test_restore_opens_its_own_client_when_no_listener(db_session, raw_with_missing_file, monkeypatch):
    from app.services import telegram_event_listener, telegram_reader

    own = _FakeTelethonClient()
    monkeypatch.setattr(telegram_event_listener, "active_client", lambda: None)
    monkeypatch.setattr(telegram_reader.TelegramReaderService, "_client", lambda self: own)

    asyncio.run(telegram_reader.TelegramReaderService().restore_media(db_session, raw_with_missing_file))

    assert own.connected is True
    assert own.disconnected is True, "свой клиент надо закрывать"
