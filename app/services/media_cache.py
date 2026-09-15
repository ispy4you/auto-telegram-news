"""Диск под медиа — кэш, и у кэша есть срок.

Видео из каналов копились на диске контейнера без ограничений: за неделю после
деплоя набежало больше 5 ГБ. При этом держать их незачем — оригинал лежит в
Telegram, страница поста перекачивает пропавшее сама при открытии, а публикация
перед отправкой.

Поэтому старые файлы из каналов удаляются, а записи о них остаются: без записи
нечего было бы перекачивать. Свои загрузки редактора не трогаем никогда — второго
экземпляра у них нет.
"""

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ActionLog, GeneratedPost, MediaItem, MediaOrigin, PublishJob, PublishJobStatus
from app.services import settings_registry

logger = logging.getLogger(__name__)

_DAY = 86400


@dataclass
class Freed:
    files: int = 0
    bytes: int = 0

    @property
    def mb(self) -> float:
        return round(self.bytes / 1024 / 1024, 1)


def _waiting_for_publish(db: Session) -> set[int]:
    """Посты, которые вот-вот уйдут в канал: их файлы понадобятся без человека.

    Публикация умеет докачать пропавшее, но это лишний поход в Telegram в самый
    неудобный момент — и если исходное сообщение успели удалить, пост уйдёт без
    картинки. Отложенную отправку лучше не ставить под этот риск.
    """
    rows = db.scalars(
        select(GeneratedPost.raw_post_id)
        .join(PublishJob, PublishJob.generated_post_id == GeneratedPost.id)
        .where(PublishJob.status.in_([PublishJobStatus.PENDING.value, PublishJobStatus.RUNNING.value]))
    )
    return set(rows)


def drop_source_files(db: Session, older_than_days: float | None) -> Freed:
    """Удаляет с диска файлы из каналов. None — все, независимо от возраста.

    Возраст считается по самому файлу, а не по записи в БД: перекачанный при
    открытии поста файл свежий, хотя запись о нём старая. Иначе он пропадал бы
    на ближайшей же чистке, пока редактор ещё работает с постом.
    """
    cutoff = None if older_than_days is None else time.time() - older_than_days * _DAY
    keep = _waiting_for_publish(db)
    root = get_settings().media_root.resolve()
    freed = Freed()

    items = db.scalars(select(MediaItem).where(MediaItem.origin == MediaOrigin.SOURCE.value))
    for item in items:
        if item.raw_post_id in keep:
            continue
        path = Path(item.file_path)
        try:
            stat = path.stat()
        except OSError:
            continue  # уже нет на диске
        if cutoff is not None and stat.st_mtime >= cutoff:
            continue
        try:
            path.unlink()
        except OSError:
            logger.warning("Не удалось удалить %s", path, exc_info=True)
            continue
        freed.files += 1
        freed.bytes += stat.st_size
        _remove_empty_parents(path, root)

    return freed


def _remove_empty_parents(path: Path, root: Path) -> None:
    """Файлы лежат по папкам дня и поста: без этого остались бы тысячи пустых."""
    directory = path.resolve().parent
    while directory != root and root in directory.parents:
        try:
            directory.rmdir()
        except OSError:
            return  # не пустая — выше тоже не пустая
        directory = directory.parent


def prune(db: Session) -> Freed:
    """Плановая чистка по настройке. 0 дней — не чистить."""
    days = settings_registry.get("media_cache_days", db)
    if days <= 0:
        return Freed()

    freed = drop_source_files(db, days)
    if freed.files:
        db.add(ActionLog(
            action="media_cleanup",
            entity_type="MediaItem",
            entity_id="auto",
            message=(
                f"Удалено {freed.files} файлов из каналов старше {days} дн. ({freed.mb} МБ). "
                "Они подкачаются из источника при открытии поста."
            ),
        ))
        db.commit()
        logger.info("Медиа: удалено %s файлов старше %s дн., %s МБ", freed.files, days, freed.mb)
    return freed
