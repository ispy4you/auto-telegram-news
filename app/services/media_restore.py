"""Восстановление медиа, не пережившего перезапуск контейнера.

Диск на хостинге — кэш, а не хранилище: после деплоя файлы пропадают, а
сообщение в исходном канале остаётся. Публикация это уже умеет и перекачивает
недостающее сама, но до неё пост в панели выглядит как набор битых картинок:
редактировать и отправлять приходится вслепую.

Поэтому страница поста просит восстановить медиа сама, при открытии. Работа
сетевая и небыстрая, так что делается она отдельным запросом, а не внутри
отрисовки страницы — иначе открытие поста ждало бы скачивания видео.
"""

import logging
import time
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import MediaOrigin, RawPost

logger = logging.getLogger(__name__)

#: Сколько не трогать пост, для которого восстановить ничего не удалось.
#: Сообщение в источнике могли удалить — тогда попытки бессмысленны, а
#: обновление страницы не должно дёргать Telegram каждый раз.
RETRY_AFTER_SECONDS = 600

_last_failure: dict[int, float] = {}
_in_flight: set[int] = set()


def reset_state() -> None:
    """Забыть накопленное — нужно тестам и ручной перепроверке."""
    _last_failure.clear()
    _in_flight.clear()


def missing_media(raw_post: RawPost) -> list:
    """Пропавшие файлы, которые есть откуда взять: только пришедшие из канала."""
    return [
        item for item in raw_post.media_items
        if item.origin != MediaOrigin.MANUAL.value and not Path(item.file_path).exists()
    ]


def lost_media(raw_post: RawPost) -> list:
    """Пропавшие файлы редактора: восстанавливать их неоткуда, только загрузить заново."""
    return [
        item for item in raw_post.media_items
        if item.origin == MediaOrigin.MANUAL.value and not Path(item.file_path).exists()
    ]


async def restore(db: Session, raw_post: RawPost) -> dict:
    """Перекачивает пропавшие файлы поста. Никогда не бросает исключение.

    Статусы: `ok` — файлы на месте, `gone` — восстановить нечего,
    `busy`/`cooldown` — сейчас не время, `error` — сорвалось.
    """
    from app.services.telegram_reader import TelegramReaderService

    missing = missing_media(raw_post)
    if not missing:
        return {"status": "ok", "restored": 0, "missing": 0}

    post_id = raw_post.id
    if post_id in _in_flight:
        return {"status": "busy", "restored": 0, "missing": len(missing)}

    failed_at = _last_failure.get(post_id)
    if failed_at is not None and time.monotonic() - failed_at < RETRY_AFTER_SECONDS:
        return {"status": "cooldown", "restored": 0, "missing": len(missing)}

    _in_flight.add(post_id)
    try:
        restored = await TelegramReaderService().restore_media(db, raw_post)
    except Exception as exc:
        _last_failure[post_id] = time.monotonic()
        logger.warning("Не удалось перекачать медиа поста %s: %s", post_id, exc)
        return {"status": "error", "restored": 0, "missing": len(missing), "reason": str(exc)[:200]}
    finally:
        _in_flight.discard(post_id)

    if not restored:
        _last_failure[post_id] = time.monotonic()
        return {"status": "gone", "restored": 0, "missing": len(missing)}

    _last_failure.pop(post_id, None)
    return {"status": "ok", "restored": restored, "missing": len(missing) - restored}
