"""Разметка, которую пишет модель, — в сущности Telegram.

Модель отвечает обычным markdown: `**жирный**`, `[текст](ссылка)`. Telegram
же разбирает теги, только если его об этом попросить (`parse_mode`), а при
таком разборе каждый посторонний символ в тексте — повод отбить сообщение
целиком. Поэтому разметку разбираем сами и отдаём списком сущностей: в
тексте тогда нечего экранировать, а Telegram получает ровно то, что показал
редактор в панели.

Понимаем то, что модели пишут на самом деле: `**жирный**`, `__подчёркнутый__`,
`*курсив*`, `_курсив_`, `~~зачёркнутый~~`, `||скрытый||`, `` `код` ``,
```` ```блок``` ```` и `[текст](ссылка)`.
"""

import re

from app.services.message_entities import MAX_ENTITIES, MAX_URL_LENGTH, utf16_len

#: Ссылка целиком: текст в квадратных скобках, адрес в круглых.
_LINK = re.compile(r"\[([^\[\]\n]+)\]\((https?://[^\s()]+)\)")

#: Парные знаки. Порядок важен: сначала длинные, иначе `**` разберётся как `*`.
_MARKS = (
    ("```", "pre"),
    ("**", "bold"),
    ("__", "underline"),
    ("~~", "strikethrough"),
    ("||", "spoiler"),
    ("`", "code"),
    ("*", "italic"),
    ("_", "italic"),
)

#: Внутри этих сущностей разметки нет: код показывают как написано.
_VERBATIM = {"code", "pre"}

#: Экранированный знак — это сам знак, а не начало разметки.
_ESCAPABLE = set("*_~|`[]()\\")


def parse(text: str) -> tuple[str, list[dict]]:
    """Текст без знаков разметки и список сущностей к нему.

    Смещения — в единицах UTF-16, как их считает Telegram.
    """
    clean, entities = _parse(text.strip(), 0)
    entities.sort(key=lambda item: (item["offset"], -item["length"]))
    return clean, entities[:MAX_ENTITIES]


def _mark_at(src: str, i: int) -> tuple[str, str] | None:
    """Знак разметки, начинающийся в этом месте, если он тут есть."""
    for token, kind in _MARKS:
        if not src.startswith(token, i):
            continue
        # «* пункт списка» и «2 * 2» разметкой не являются: за открывающим
        # знаком должен сразу идти текст.
        after = src[i + len(token):i + len(token) + 1]
        if not after or after.isspace():
            continue
        # Подчёркивание внутри слова — часть слова (file_name, ссылка с _).
        if token in ("_", "__") and i and (src[i - 1].isalnum() or src[i - 1] == "_"):
            continue
        return token, kind
    return None


def _parse(src: str, base: int) -> tuple[str, list[dict]]:
    out: list[str] = []
    entities: list[dict] = []
    cursor = base
    i = 0

    while i < len(src):
        char = src[i]

        if char == "\\" and i + 1 < len(src) and src[i + 1] in _ESCAPABLE:
            out.append(src[i + 1])
            cursor += utf16_len(src[i + 1])
            i += 2
            continue

        link = _LINK.match(src, i)
        if link:
            inner, inner_entities = _parse(link.group(1), cursor)
            if inner:
                entities.append({
                    "type": "text_link",
                    "offset": cursor,
                    "length": utf16_len(inner),
                    "url": link.group(2)[:MAX_URL_LENGTH],
                })
                entities.extend(inner_entities)
                out.append(inner)
                cursor += utf16_len(inner)
                i = link.end()
                continue

        mark = _mark_at(src, i)
        if mark:
            token, kind = mark
            start = i + len(token)
            close = src.find(token, start)
            if close > start:
                raw = src[start:close]
                if kind in _VERBATIM:
                    inner, inner_entities = raw, []
                else:
                    inner, inner_entities = _parse(raw, cursor)
                entities.append({"type": kind, "offset": cursor, "length": utf16_len(inner)})
                entities.extend(inner_entities)
                out.append(inner)
                cursor += utf16_len(inner)
                i = close + len(token)
                continue

        out.append(char)
        cursor += utf16_len(char)
        i += 1

    return "".join(out), entities
