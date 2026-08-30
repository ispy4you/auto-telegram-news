"""Разметка поста — список сущностей, а не теги внутри текста.

Telegram хранит её именно так: обычный текст плюс перечень «с такого-то
места столько-то единиц — жирный». `parse_mode` лишь просит Telegram
разобрать теги самому.

Мы отдаём список напрямую. Тогда в тексте нечего экранировать и нечему
ломаться: раньше один голый «<» в новости отбивал всё сообщение целиком,
поэтому цитата и была единственным разрешённым тегом.
"""

import json

#: Что понимает Telegram и предлагает панель.
ALLOWED_TYPES = frozenset({
    "bold", "italic", "underline", "strikethrough", "spoiler",
    "code", "pre", "blockquote", "expandable_blockquote", "text_link",
})

#: Разметки на пост заведомо больше не нужно, а список приходит из браузера.
MAX_ENTITIES = 200
MAX_URL_LENGTH = 2048


def utf16_len(text: str) -> int:
    """Длина в тех единицах, в которых Telegram считает смещения.

    Для кириллицы и латиницы совпадает с числом символов, но эмодзи занимает
    две единицы. Считать питоновским len() нельзя: один смайлик в начале
    поста сдвинул бы всю разметку после него.
    """
    return len(text.encode("utf-16-le")) // 2


def loads(raw: str | None) -> list[dict]:
    """Разметка из хранилища. Испорченная запись — просто её отсутствие."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def dumps(entities: list[dict]) -> str | None:
    return json.dumps(entities, ensure_ascii=False) if entities else None


def normalize(text: str, entities: list | None) -> tuple[str, list[dict]]:
    """Приводит пару «текст + разметка» к виду, который примет Telegram.

    Список приходит из браузера, поэтому проверяется целиком: неизвестные
    типы, отрицательные длины и выход за конец текста отбрасываются. Текст
    обрезается по краям — и разметка сдвигается вместе с ним, иначе она
    указывала бы мимо.
    """
    stripped = text.strip()
    if not stripped:
        return "", []

    shift = utf16_len(text[:len(text) - len(text.lstrip())])
    limit = utf16_len(stripped)

    cleaned: list[dict] = []
    for raw in entities or []:
        if not isinstance(raw, dict) or raw.get("type") not in ALLOWED_TYPES:
            continue
        try:
            offset = int(raw["offset"]) - shift
            length = int(raw["length"])
        except (KeyError, TypeError, ValueError):
            continue

        if offset < 0:
            length += offset
            offset = 0
        length = min(length, limit - offset)
        if length <= 0 or offset >= limit:
            continue

        item = {"type": raw["type"], "offset": offset, "length": length}
        if item["type"] == "text_link":
            url = str(raw.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                continue
            item["url"] = url[:MAX_URL_LENGTH]
        cleaned.append(item)
        if len(cleaned) >= MAX_ENTITIES:
            break

    cleaned.sort(key=lambda item: (item["offset"], -item["length"]))
    return stripped, cleaned


def to_aiogram(entities: list[dict]) -> list:
    """Сущности в том виде, в каком их принимает aiogram."""
    from aiogram.types import MessageEntity

    return [
        MessageEntity(
            type=item["type"],
            offset=item["offset"],
            length=item["length"],
            url=item.get("url"),
        )
        for item in entities
    ]
