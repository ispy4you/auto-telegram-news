import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import (
    GeneratedPost,
    GeneratedPostStatus,
    MediaItem,
    PublishJob,
    PublishJobStatus,
    RawPost,
    TargetChannel,
)
from app.services.telegram_publisher import TelegramPublisherService


class _FakeBot:
    """Записывает вызовы и умеет падать на выбранных методах."""

    def __init__(self, fail_on=()):
        self.calls: list[str] = []
        self.fail_on = set(fail_on)
        self._next_id = 1000

    async def _record(self, name: str):
        self.calls.append(name)
        if name in self.fail_on:
            raise RuntimeError(f"{name} failed")
        self._next_id += 1
        return SimpleNamespace(message_id=self._next_id)

    async def send_message(self, **_):
        return await self._record("send_message")

    async def send_photo(self, **_):
        return await self._record("send_photo")

    async def send_media_group(self, **_):
        return [await self._record("send_media_group")]


def _publisher(bot: _FakeBot) -> TelegramPublisherService:
    publisher = TelegramPublisherService()

    async def _get_bot(_db=None):
        return bot

    publisher._get_bot = _get_bot
    return publisher


@pytest.fixture
def target(db_session):
    channel = TargetChannel(title="Target", chat_id="@target")
    db_session.add(channel)
    db_session.commit()
    db_session.refresh(channel)
    return channel


@pytest.fixture
def generated(db_session, source):
    raw = RawPost(source_id=source.id, telegram_message_id=1, text_hash="h", original_text="raw")
    db_session.add(raw)
    db_session.flush()
    post = GeneratedPost(raw_post_id=raw.id, generated_text="текст поста")
    db_session.add(post)
    db_session.commit()
    db_session.refresh(post)
    return post


def _publish(publisher, db, generated_id, target_id):
    return asyncio.run(publisher.publish_generated_post(db, generated_id, target_id))


def test_attempts_grow_across_retries(db_session, generated, target):
    """Счётчик попыток должен расти, иначе лимит ретраев не наступает никогда."""
    publisher = _publisher(_FakeBot(fail_on=["send_message"]))

    for expected in (1, 2, 3):
        with pytest.raises(RuntimeError):
            _publish(publisher, db_session, generated.id, target.id)
        jobs = db_session.scalars(select(PublishJob)).all()
        assert len(jobs) == 1, "на пару (пост, канал) должна быть одна задача"
        assert jobs[0].attempts == expected
        assert jobs[0].status == PublishJobStatus.FAILED.value
        generated.status = GeneratedPostStatus.APPROVED.value
        db_session.commit()


def test_published_post_is_not_sent_again(db_session, generated, target):
    generated.status = GeneratedPostStatus.PUBLISHED.value
    db_session.commit()
    bot = _FakeBot()

    _publish(_publisher(bot), db_session, generated.id, target.id)

    assert bot.calls == []


def test_media_is_not_resent_when_only_the_text_failed(db_session, generated, source, target, tmp_path):
    """Длинный текст уходит вторым сообщением — его падение не должно дублировать медиа."""
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"jpeg")
    db_session.add(MediaItem(
        raw_post_id=generated.raw_post_id,
        telegram_message_id=1,
        media_type="photo",
        file_path=str(photo),
    ))
    generated.generated_text = "т" * 1500  # длиннее лимита подписи в 1024 символа
    db_session.commit()

    failing = _FakeBot(fail_on=["send_message"])
    with pytest.raises(RuntimeError):
        _publish(_publisher(failing), db_session, generated.id, target.id)

    assert failing.calls == ["send_photo", "send_message"]
    assert generated.telegram_message_id is not None, "id доставленного медиа должен сохраниться"

    generated.status = GeneratedPostStatus.APPROVED.value
    db_session.commit()

    retry = _FakeBot()
    _publish(_publisher(retry), db_session, generated.id, target.id)

    assert retry.calls == ["send_message"], "медиа отправлять повторно нельзя"
    assert generated.status == GeneratedPostStatus.PUBLISHED.value
