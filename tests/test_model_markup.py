"""Разметка, которую пишет модель, должна доходить до Telegram.

Повод: оператор просил модель выделять заголовок жирным и ставить ссылку в
последней строке. Модель так и делала, но в канал уходили звёздки и скобки —
Telegram показывает markdown, только если его разобрать, а мы отдавали текст
как есть.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.models import GeneratedPost, RawPost, TargetChannel
from app.services import markdown_entities
from app.services.telegram_publisher import TelegramPublisherService


def _marks(text: str):
    clean, entities = markdown_entities.parse(text)
    return clean, [(e["type"], e["offset"], e["length"], e.get("url")) for e in entities]


def test_a_bold_first_line_becomes_an_entity():
    clean, marks = _marks("**Главное за день**\nДальше текст.")

    assert clean == "Главное за день\nДальше текст.", "звёздочки в текст не попадают"
    assert marks == [("bold", 0, 15, None)]


def test_a_link_in_the_last_line_keeps_its_address():
    clean, marks = _marks("Текст поста.\n\n[Подписаться](https://t.me/channel)")

    assert clean.endswith("Подписаться")
    assert marks == [("text_link", 14, 11, "https://t.me/channel")]


def test_offsets_are_counted_the_way_telegram_counts_them():
    """Эмодзи занимает две единицы UTF-16: иначе разметка уедет."""
    clean, marks = _marks("Эмодзи 🔥 перед **жирным**")

    assert marks == [("bold", 16, 6, None)]
    assert clean == "Эмодзи 🔥 перед жирным"


@pytest.mark.parametrize("source, kind", [
    ("*курсив*", "italic"),
    ("_курсив_", "italic"),
    ("__подчёркнутый__", "underline"),
    ("~~зачёркнутый~~", "strikethrough"),
    ("||скрытый||", "spoiler"),
    ("`код`", "code"),
])
def test_the_marks_models_actually_write_are_understood(source, kind):
    _, marks = _marks(source)

    assert [m[0] for m in marks] == [kind]


@pytest.mark.parametrize("text", [
    "Формула 2 * 2 даёт 4",
    "Файл file_name.txt и ещё one_two_three",
    "* пункт списка\n* второй пункт",
    "Просто текст без разметки",
])
def test_ordinary_text_is_left_alone(text):
    clean, marks = _marks(text)

    assert (clean, marks) == (text, []), "обычный текст не должен менять вид"


def test_a_bold_link_keeps_both_marks():
    clean, marks = _marks("**[Канал](https://t.me/x)** — наш")

    assert clean == "Канал — наш"
    assert ("bold", 0, 5, None) in marks
    assert ("text_link", 0, 5, "https://t.me/x") in marks


def test_an_unclosed_mark_is_not_a_mark():
    """Одинокая звёздочка — просто звёздочка, а не начало вечного жирного."""
    clean, marks = _marks("Скидка **50% на всё")

    assert clean == "Скидка **50% на всё"
    assert marks == []


def test_a_backslash_keeps_the_sign_itself():
    clean, marks = _marks(r"Звёздочка \*не разметка\*")

    assert clean == "Звёздочка *не разметка*"
    assert marks == []


def test_a_foreign_scheme_is_not_a_link():
    """Адрес в сущности Telegram принимает только http(s)."""
    clean, marks = _marks("[файл](file:///etc/passwd)")

    assert marks == []
    assert "file:///etc/passwd" in clean, "текст не теряем, просто не делаем ссылкой"


class _CapturingBot:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def send_message(self, **kwargs):
        self.sent.append(("send_message", kwargs))
        return SimpleNamespace(message_id=1)


@pytest.fixture
def target(db_session):
    channel = TargetChannel(title="Target", chat_id="@target")
    db_session.add(channel)
    db_session.commit()
    db_session.refresh(channel)
    return channel


def _draft(db_session, source, text: str, edited: str | None = None, edited_marks=None):
    raw = RawPost(source_id=source.id, telegram_message_id=1, text_hash="h", original_text="raw")
    db_session.add(raw)
    db_session.flush()
    clean, marks = markdown_entities.parse(text)
    post = GeneratedPost(
        raw_post_id=raw.id,
        generated_text=clean,
        generated_entities=json.dumps(marks, ensure_ascii=False),
        edited_text=edited,
        entities=json.dumps(edited_marks, ensure_ascii=False) if edited_marks else None,
    )
    db_session.add(post)
    db_session.commit()
    db_session.refresh(post)
    return post


def _publish(bot, db, generated_id, target_id):
    publisher = TelegramPublisherService()

    async def _get_bot(_db=None):
        return bot

    publisher._get_bot = _get_bot
    return asyncio.run(publisher.publish_generated_post(db, generated_id, target_id))


def test_the_markup_of_the_model_reaches_telegram(db_session, source, target):
    post = _draft(db_session, source, "**Главное**\n[Подписаться](https://t.me/channel)")
    bot = _CapturingBot()

    _publish(bot, db_session, post.id, target.id)

    kwargs = bot.sent[0][1]
    assert "**" not in kwargs["text"]
    kinds = [(e.type, e.offset, e.length) for e in kwargs["entities"]]
    assert ("bold", 0, 7) in kinds  # «Главное» — семь знаков
    assert any(e.type == "text_link" and e.url == "https://t.me/channel" for e in kwargs["entities"])


def test_an_edited_post_keeps_the_editors_markup(db_session, source, target):
    """Правка редактора главнее: разметка модели описывает уже не тот текст."""
    post = _draft(
        db_session, source, "**Главное**",
        edited="Своими словами",
        edited_marks=[{"type": "italic", "offset": 0, "length": 5}],
    )
    bot = _CapturingBot()

    _publish(bot, db_session, post.id, target.id)

    kwargs = bot.sent[0][1]
    assert kwargs["text"] == "Своими словами"
    assert [(e.type, e.offset, e.length) for e in kwargs["entities"]] == [("italic", 0, 5)]
