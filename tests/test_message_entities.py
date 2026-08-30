"""Разметка приходит из браузера, поэтому проверяется целиком.

Смещения Telegram считает в единицах UTF-16: эмодзи занимает две. Ошибка
здесь не видна глазами — она проявится сдвинутым жирным шрифтом в канале.
"""

import pytest

from app.services import message_entities as me


def test_utf16_length_counts_the_way_telegram_does():
    assert me.utf16_len("привет") == 6
    assert me.utf16_len("😀") == 2, "эмодзи занимает две единицы, а не одну"
    assert me.utf16_len("😀a") == 3


def test_offsets_shift_with_the_trimmed_text():
    text, marks = me.normalize("  Привет мир  ", [{"type": "bold", "offset": 2, "length": 6}])

    assert text == "Привет мир"
    assert marks == [{"type": "bold", "offset": 0, "length": 6}]


def test_emoji_offsets_survive_untouched():
    """Смещения приходят из браузера, а он считает так же — пересчитывать нечего."""
    text, marks = me.normalize("😀 жирный", [{"type": "bold", "offset": 3, "length": 6}])

    assert text == "😀 жирный"
    assert marks[0]["offset"] == 3


def test_marks_are_clipped_to_the_text():
    _, marks = me.normalize("коротко", [{"type": "bold", "offset": 3, "length": 999}])

    assert marks == [{"type": "bold", "offset": 3, "length": 4}]


@pytest.mark.parametrize("entity", [
    {"type": "bold", "offset": 50, "length": 3},      # начинается за концом текста
    {"type": "bold", "offset": 0, "length": 0},       # пустая
    {"type": "невиданный", "offset": 0, "length": 3},  # неизвестный тип
    {"type": "bold", "offset": "ой", "length": 3},    # мусор вместо числа
    "не словарь",
])
def test_nonsense_is_dropped(entity):
    assert me.normalize("коротко", [entity])[1] == []


def test_only_http_links_pass():
    dangerous = me.normalize("тык", [{"type": "text_link", "offset": 0, "length": 3, "url": "javascript:alert(1)"}])
    assert dangerous[1] == []

    fine = me.normalize("тык", [{"type": "text_link", "offset": 0, "length": 3, "url": "https://ya.ru"}])
    assert fine[1][0]["url"] == "https://ya.ru"


def test_the_list_cannot_grow_without_bound():
    many = [{"type": "bold", "offset": 0, "length": 1} for _ in range(me.MAX_ENTITIES + 50)]
    assert len(me.normalize("текст", many)[1]) == me.MAX_ENTITIES


def test_broken_storage_reads_as_no_markup():
    assert me.loads("не json") == []
    assert me.loads(None) == []
    assert me.loads('{"type": "bold"}') == [], "ожидается список, а не объект"


def test_empty_markup_is_stored_as_nothing():
    assert me.dumps([]) is None


def test_round_trip_through_storage():
    marks = [{"type": "bold", "offset": 0, "length": 3}]
    assert me.loads(me.dumps(marks)) == marks
