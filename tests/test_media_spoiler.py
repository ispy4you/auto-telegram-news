"""Медиа уходит в канал под спойлером, если так настроен канал.

Размывает картинку сам Telegram по флагу has_spoiler: файл отправляется
нетронутым, и читатель открывает его нажатием.
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.models import GeneratedPost, MediaItem, RawPost, TargetChannel
from app.services.telegram_publisher import TelegramPublisherService


class _CapturingBot:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def _record(self, name: str, kwargs: dict):
        self.sent.append((name, kwargs))
        return SimpleNamespace(message_id=len(self.sent))

    async def send_message(self, **kwargs):
        return await self._record("send_message", kwargs)

    async def send_photo(self, **kwargs):
        return await self._record("send_photo", kwargs)

    async def send_video(self, **kwargs):
        return await self._record("send_video", kwargs)

    async def send_media_group(self, **kwargs):
        return [await self._record("send_media_group", kwargs)]


def _publish(bot, db, generated_id, target_id):
    publisher = TelegramPublisherService()

    async def _get_bot(_db=None):
        return bot

    publisher._get_bot = _get_bot
    return asyncio.run(publisher.publish_generated_post(db, generated_id, target_id))


@pytest.fixture
def make_target(db_session):
    def _make(spoiler: bool) -> TargetChannel:
        channel = TargetChannel(title="Target", chat_id="@target", spoiler_media=spoiler)
        db_session.add(channel)
        db_session.commit()
        db_session.refresh(channel)
        return channel

    return _make


@pytest.fixture
def make_post(db_session, source, tmp_path):
    def _make(media: tuple[str, ...] = ()) -> GeneratedPost:
        raw = RawPost(source_id=source.id, telegram_message_id=1, text_hash="h", original_text="raw")
        db_session.add(raw)
        db_session.flush()
        for index, kind in enumerate(media):
            path = tmp_path / f"{index}.{'mp4' if kind == 'video' else 'jpg'}"
            path.write_bytes(b"file")
            db_session.add(MediaItem(
                raw_post_id=raw.id, telegram_message_id=1, media_type=kind,
                file_path=str(path), sort_order=index,
            ))
        post = GeneratedPost(raw_post_id=raw.id, generated_text="Новость")
        db_session.add(post)
        db_session.commit()
        db_session.refresh(post)
        return post

    return _make


def test_a_photo_goes_out_hidden(db_session, make_post, make_target):
    post, target = make_post(("photo",)), make_target(True)
    bot = _CapturingBot()

    _publish(bot, db_session, post.id, target.id)

    name, kwargs = bot.sent[0]
    assert name == "send_photo"
    assert kwargs["has_spoiler"] is True


def test_a_video_goes_out_hidden_too(db_session, make_post, make_target):
    post, target = make_post(("video",)), make_target(True)
    bot = _CapturingBot()

    _publish(bot, db_session, post.id, target.id)

    name, kwargs = bot.sent[0]
    assert name == "send_video"
    assert kwargs["has_spoiler"] is True


def test_every_file_of_an_album_is_hidden(db_session, make_post, make_target):
    """Одна открытая картинка в альбоме обессмысливает спойлер на остальных."""
    post, target = make_post(("photo", "video", "photo")), make_target(True)
    bot = _CapturingBot()

    _publish(bot, db_session, post.id, target.id)

    name, kwargs = bot.sent[0]
    assert name == "send_media_group"
    assert [item.has_spoiler for item in kwargs["media"]] == [True, True, True]


def test_a_channel_without_the_flag_sends_media_open(db_session, make_post, make_target):
    post, target = make_post(("photo",)), make_target(False)
    bot = _CapturingBot()

    _publish(bot, db_session, post.id, target.id)

    assert bot.sent[0][1]["has_spoiler"] is False


def test_the_flag_reaches_the_channel_from_the_form(logged_in, db_session, csrf):
    """Галочка в карточке канала — единственный способ это включить."""
    channel = TargetChannel(title="Target", chat_id="@target")
    db_session.add(channel)
    db_session.commit()

    logged_in.post(f"/targets/{channel.id}/edit", data={
        "title": "Target", "chat_id": "@target", "username": "",
        "spoiler_media": "on", "csrf_token": csrf(logged_in, "/targets"),
    })
    db_session.refresh(channel)
    assert channel.spoiler_media is True

    # Снятая галочка в POST не приходит вовсе — это и значит «выключить».
    logged_in.post(f"/targets/{channel.id}/edit", data={
        "title": "Target", "chat_id": "@target", "username": "",
        "csrf_token": csrf(logged_in, "/targets"),
    })
    db_session.refresh(channel)
    assert channel.spoiler_media is False
