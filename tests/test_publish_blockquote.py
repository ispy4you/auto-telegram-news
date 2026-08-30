"""Оформление поста цитатой.

Цитата — единственный тег, который мы отправляем, и включает он режим HTML.
Значит, всё остальное в тексте обязано быть экранировано: один голый «<»
в новости — и Telegram отвергает сообщение целиком.
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.models import GeneratedPost, MediaItem, RawPost, TargetChannel
from app.services.telegram_publisher import TelegramPublisherService, _format_for_telegram


class _CapturingBot:
    """Запоминает, что именно ушло в Telegram."""

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
    def _make(text: str, as_blockquote: bool = False) -> GeneratedPost:
        raw = RawPost(source_id=source.id, telegram_message_id=1, text_hash="h", original_text="raw")
        db_session.add(raw)
        db_session.flush()
        post = GeneratedPost(raw_post_id=raw.id, generated_text=text, as_blockquote=as_blockquote)
        db_session.add(post)
        db_session.commit()
        db_session.refresh(post)
        return post

    return _make


def _publish(bot, db, generated_id, target_id):
    return asyncio.run(_publisher(bot).publish_generated_post(db, generated_id, target_id))


def test_plain_post_goes_out_exactly_as_before():
    assert _format_for_telegram("Просто текст", False) == ("Просто текст", None)


def test_quoted_post_is_wrapped_and_switches_to_html():
    body, parse_mode = _format_for_telegram("Просто текст", True)
    assert body == "<blockquote>Просто текст</blockquote>"
    assert parse_mode == "HTML"


def test_markup_characters_are_escaped_inside_the_quote():
    """Иначе Telegram ответит «can't parse entities» и пост не уйдёт вовсе."""
    body, _ = _format_for_telegram('Компания <АО «Ромашка»> & Co', True)
    assert "&lt;АО «Ромашка»&gt; &amp; Co" in body
    assert body.count("<") == 2, "угловые скобки остались только у самого тега"


def test_quoted_text_reaches_the_channel(db_session, make_post, target):
    post = make_post("Новость дня", as_blockquote=True)
    bot = _CapturingBot()

    _publish(bot, db_session, post.id, target.id)

    name, kwargs = bot.sent[0]
    assert name == "send_message"
    assert kwargs["text"] == "<blockquote>Новость дня</blockquote>"
    assert kwargs["parse_mode"] == "HTML"


def test_plain_text_is_sent_without_a_parse_mode(db_session, make_post, target):
    post = make_post("Новость дня")
    bot = _CapturingBot()

    _publish(bot, db_session, post.id, target.id)

    _, kwargs = bot.sent[0]
    assert kwargs["text"] == "Новость дня"
    assert kwargs["parse_mode"] is None


def test_caption_limit_counts_the_visible_text_not_the_tags(db_session, make_post, source, target, tmp_path):
    """Telegram меряет разобранное сообщение: теги цитаты в лимит не входят."""
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"jpeg")
    post = make_post("т" * 1020, as_blockquote=True)
    db_session.add(MediaItem(
        raw_post_id=post.raw_post_id,
        telegram_message_id=1,
        media_type="photo",
        file_path=str(photo),
    ))
    db_session.commit()

    bot = _CapturingBot()
    _publish(bot, db_session, post.id, target.id)

    assert [name for name, _ in bot.sent] == ["send_photo"], "текст обязан уместиться в подпись"
    _, kwargs = bot.sent[0]
    assert kwargs["caption"].startswith("<blockquote>")
    assert kwargs["parse_mode"] == "HTML"


def test_the_toggle_is_offered_on_the_post_page(logged_in, make_post):
    post = make_post("Новость дня")

    page = logged_in.get(f"/posts/{post.raw_post_id}").text

    assert 'name="as_blockquote"' in page
    assert "Оформить цитатой" in page


def test_saving_edits_keeps_the_choice(logged_in, csrf, db_session, make_post):
    post = make_post("Новость дня", as_blockquote=True)

    logged_in.post(f"/generated/{post.id}/save", data={
        "csrf_token": csrf(logged_in, f"/posts/{post.raw_post_id}"),
        "edited_text": "Правленый текст",
        "as_blockquote": "on",
    })

    db_session.refresh(post)
    assert post.edited_text == "Правленый текст"
    assert post.as_blockquote is True


def test_turning_the_toggle_off_is_saved_too(logged_in, csrf, db_session, make_post):
    post = make_post("Новость дня", as_blockquote=True)

    logged_in.post(f"/generated/{post.id}/save", data={
        "csrf_token": csrf(logged_in, f"/posts/{post.raw_post_id}"),
        "edited_text": "Новость дня",
    })

    db_session.refresh(post)
    assert post.as_blockquote is False
