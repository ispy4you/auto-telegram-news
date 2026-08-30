"""Публикация отдаёт разметку списком сущностей.

Так Telegram хранит её сам. Ничего не экранируется, поэтому угловые скобки
и амперсанды в новости больше не могут отбить сообщение целиком.
"""

import asyncio
import json
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

    async def send_media_group(self, **kwargs):
        return [await self._record("send_media_group", kwargs)]


def _publisher(bot: _CapturingBot) -> TelegramPublisherService:
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
def make_post(db_session, source):
    def _make(text: str, entities=None, edited: bool = True) -> GeneratedPost:
        raw = RawPost(source_id=source.id, telegram_message_id=1, text_hash="h", original_text="raw")
        db_session.add(raw)
        db_session.flush()
        post = GeneratedPost(
            raw_post_id=raw.id,
            generated_text=text,
            edited_text=text if edited else None,
            entities=json.dumps(entities) if entities else None,
        )
        db_session.add(post)
        db_session.commit()
        db_session.refresh(post)
        return post

    return _make


def _publish(bot, db, generated_id, target_id):
    return asyncio.run(_publisher(bot).publish_generated_post(db, generated_id, target_id))


def test_markup_reaches_telegram_as_entities(db_session, make_post, target):
    post = make_post("Важная новость", [{"type": "bold", "offset": 0, "length": 7}])
    bot = _CapturingBot()

    _publish(bot, db_session, post.id, target.id)

    name, kwargs = bot.sent[0]
    assert name == "send_message"
    assert kwargs["text"] == "Важная новость", "текст уходит как есть, без тегов"
    entity = kwargs["entities"][0]
    assert (entity.type, entity.offset, entity.length) == ("bold", 0, 7)


def test_plain_post_carries_no_entities(db_session, make_post, target):
    post = make_post("Просто текст")
    bot = _CapturingBot()

    _publish(bot, db_session, post.id, target.id)

    assert bot.sent[0][1]["entities"] is None


def test_markup_characters_need_no_escaping(db_session, make_post, target):
    """Раньше это был режим HTML, и такой текст отбивался целиком."""
    post = make_post('Компания <АО «Ромашка»> & Co', [{"type": "bold", "offset": 0, "length": 8}])
    bot = _CapturingBot()

    _publish(bot, db_session, post.id, target.id)

    assert bot.sent[0][1]["text"] == 'Компания <АО «Ромашка»> & Co'


def test_markup_of_an_unedited_post_is_ignored(db_session, make_post, target):
    """Разметка описывает отредактированный текст — к исходному она неприменима."""
    post = make_post("Текст модели", [{"type": "bold", "offset": 0, "length": 5}], edited=False)
    bot = _CapturingBot()

    _publish(bot, db_session, post.id, target.id)

    assert bot.sent[0][1]["entities"] is None


def test_caption_carries_its_own_entities(db_session, make_post, target, tmp_path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"jpeg")
    post = make_post("Подпись к фото", [{"type": "italic", "offset": 0, "length": 7}])
    db_session.add(MediaItem(
        raw_post_id=post.raw_post_id, telegram_message_id=1, media_type="photo", file_path=str(photo),
    ))
    db_session.commit()

    bot = _CapturingBot()
    _publish(bot, db_session, post.id, target.id)

    name, kwargs = bot.sent[0]
    assert name == "send_photo"
    assert kwargs["caption"] == "Подпись к фото"
    assert kwargs["caption_entities"][0].type == "italic"


def test_long_text_leaves_the_caption_empty(db_session, make_post, target, tmp_path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"jpeg")
    post = make_post("т" * 1500)
    db_session.add(MediaItem(
        raw_post_id=post.raw_post_id, telegram_message_id=1, media_type="photo", file_path=str(photo),
    ))
    db_session.commit()

    bot = _CapturingBot()
    _publish(bot, db_session, post.id, target.id)

    assert [name for name, _ in bot.sent] == ["send_photo", "send_message"]
    assert bot.sent[0][1]["caption"] is None
    assert bot.sent[0][1]["caption_entities"] is None


def test_the_toolbar_is_offered_on_the_post_page(logged_in, make_post):
    post = make_post("Новость дня")

    page = logged_in.get(f"/posts/{post.raw_post_id}").text

    for mark in ("bold", "italic", "strikethrough", "code", "spoiler", "blockquote", "text_link"):
        assert f'data-mark="{mark}"' in page, f"на панели нет кнопки {mark}"
    assert "tg-editor" in page
    assert 'name="entities"' in page


def test_saving_stores_the_text_and_its_markup(logged_in, csrf, db_session, make_post):
    post = make_post("Новость дня", edited=False)

    logged_in.post(f"/generated/{post.id}/save", data={
        "csrf_token": csrf(logged_in, f"/posts/{post.raw_post_id}"),
        "edited_text": "  Важная новость  ",
        "entities": json.dumps([{"type": "bold", "offset": 2, "length": 7}]),
    })

    db_session.refresh(post)
    assert post.edited_text == "Важная новость", "края обрезаются"
    assert json.loads(post.entities) == [{"type": "bold", "offset": 0, "length": 7}], "разметка едет вместе с текстом"


def test_removing_all_markup_is_saved_too(logged_in, csrf, db_session, make_post):
    post = make_post("Новость", [{"type": "bold", "offset": 0, "length": 7}])

    logged_in.post(f"/generated/{post.id}/save", data={
        "csrf_token": csrf(logged_in, f"/posts/{post.raw_post_id}"),
        "edited_text": "Новость",
        "entities": "[]",
    })

    db_session.refresh(post)
    assert post.entities is None
