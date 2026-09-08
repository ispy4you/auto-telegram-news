"""Файлы медиа на диске: куда класть и что принимать от редактора.

Диск на хостинге — кэш: после деплоя он пуст. Для медиа из каналов это
не беда, оно перекачивается из Telegram. У файла, который редактор загрузил
сам, второго экземпляра нет: он живёт до ближайшего перезапуска, и это
осознанный размен — хранилище ради одной картинки заводить дороже.
"""

import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path

from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.models import MediaType
from app.services import settings_registry

logger = logging.getLogger(__name__)

#: Что принимаем от редактора: content-type браузера → расширение и тип для
#: отправки. Список короткий намеренно — это ровно то, что Telegram кладёт
#: в альбом без сюрпризов.
UPLOAD_TYPES = {
    "image/jpeg": ("jpg", MediaType.PHOTO.value),
    "image/png": ("png", MediaType.PHOTO.value),
    "image/webp": ("webp", MediaType.PHOTO.value),
    "video/mp4": ("mp4", MediaType.VIDEO.value),
}

#: Telegram отвечает на webp «PHOTO_INVALID_DIMENSIONS»: для него это стикер,
#: а не фотография. Формат слишком ходовой, чтобы просто его не принимать —
#: браузеры сохраняют картинки из интернета именно так, — поэтому переводим
#: в JPEG на загрузке.
CONVERT_TO_JPEG = {"image/webp"}

#: Telegram принимает фотографию, только если сумма сторон не больше 10000.
#: Скриншот с современного монитора в это укладывается, а склейка, панорама или
#: длинная страница целиком — уже нет: отказ приходит на публикации, когда пост
#: уже написан и одобрен. Ужимаем на загрузке, пока это ничего не стоит.
MAX_SIDE_SUM = 10000

#: Больше десяти файлов Telegram в один альбом не соберёт.
MAX_ITEMS_PER_POST = 10

#: Читаем и пишем кусками: 50-мегабайтное видео не должно оказаться в памяти.
CHUNK = 1024 * 1024


class UploadRejected(RuntimeError):
    """Файл не приняли. Текст исключения уже человеческий — его и показываем."""


class MediaStorageService:
    def __init__(self):
        self.settings = get_settings()

    def build_dir(self, source_id: int, post_id: int) -> Path:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        target = self.settings.media_root / f"{now.year:04d}" / f"{now.month:02d}" / f"{now.day:02d}" / str(source_id) / str(post_id)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def validate_size(self, file_size: int | None) -> bool:
        if not file_size:
            return True
        max_mb = settings_registry.get("max_media_mb")
        return file_size <= max_mb * 1024 * 1024

    async def save_upload(self, upload, source_id: int, post_id: int, db=None) -> dict:
        """Кладёт загруженный файл на диск и описывает его для MediaItem.

        Имя файла придумываем сами: имя из браузера — это данные пользователя,
        и в пути ему делать нечего.
        """
        label = (upload.filename or "файл")[:80]
        content_type = (upload.content_type or "").split(";")[0].strip().lower()
        if content_type not in UPLOAD_TYPES:
            raise UploadRejected(f"«{label}»: принимаем только JPEG, PNG, WebP и MP4.")

        extension, media_type = UPLOAD_TYPES[content_type]
        max_mb = settings_registry.get("max_media_mb", db)
        target = self.build_dir(source_id, post_id) / f"manual_{secrets.token_hex(6)}.{extension}"

        size = 0
        try:
            with target.open("wb") as out:
                while True:
                    chunk = await upload.read(CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_mb * 1024 * 1024:
                        raise UploadRejected(f"«{label}»: файл больше {max_mb} МБ.")
                    out.write(chunk)
            if size == 0:
                raise UploadRejected(f"«{label}»: пустой файл.")
        except Exception:
            target.unlink(missing_ok=True)
            raise

        if content_type in CONVERT_TO_JPEG:
            # Разбор и пересборка картинки — работа процессорная и небыстрая:
            # на большом файле она заморозила бы и панель, и планировщик.
            target = await run_in_threadpool(self._to_jpeg, target, label)
            content_type, size = "image/jpeg", target.stat().st_size

        if media_type == MediaType.PHOTO.value:
            shrunk = await run_in_threadpool(self._shrink_to_limit, target, label)
            if shrunk is not None:
                size = shrunk

        return {
            "path": str(target),
            "media_type": media_type,
            "file_size": size,
            "mime_type": content_type,
        }

    @staticmethod
    def _shrink_to_limit(path: Path, label: str) -> int | None:
        """Ужимает картинку до предела Telegram. Возвращает новый размер файла.

        None — файл остался как есть: так вызывающий не переписывает размер там,
        где ничего не менялось.

        Нечитаемый файл не отвергаем, а пропускаем. Отказ был бы новой политикой
        («на загрузку принимаем только то, что разобрала Pillow»), а не частью
        уменьшения: до этой правки такой файл проходил, и ломать его загрузку
        заодно — не то, о чём просили. У webp иначе, потому что там перекодировка
        обязательна: не вышла — отправлять нечего.
        """
        from PIL import Image

        try:
            with Image.open(path) as image:
                width, height = image.size
                if width + height <= MAX_SIDE_SUM:
                    return None
                # Пропорции сохраняем: Telegram ограничивает ещё и соотношение
                # сторон, и растянутая картинка упёрлась бы уже в него.
                factor = MAX_SIDE_SUM / (width + height)
                new_size = (max(1, int(width * factor)), max(1, int(height * factor)))
                resized = image.resize(new_size, Image.LANCZOS)
                if path.suffix.lower() in (".jpg", ".jpeg"):
                    resized = resized.convert("RGB")
                    fmt, options = "JPEG", {"quality": 90}
                else:
                    fmt, options = None, {}
        except Exception:
            logger.warning("Не удалось разобрать %s — оставляем как есть", path, exc_info=True)
            return None

        try:
            # Исходник закрыт — пишем поверх него уменьшенный.
            resized.save(path, fmt, **options)
        except Exception:
            logger.warning("Не удалось сохранить уменьшенную %s", path, exc_info=True)
            raise UploadRejected(f"«{label}»: не удалось уменьшить картинку под предел Telegram.")

        logger.info("Картинка %s ужата до %s: не проходила предел Telegram", path.name, new_size)
        return path.stat().st_size

    @staticmethod
    def _to_jpeg(source: Path, label: str) -> Path:
        """Пересохраняет картинку в JPEG рядом и убирает исходник."""
        from PIL import Image

        target = source.with_suffix(".jpg")
        try:
            with Image.open(source) as image:
                # Прозрачности в JPEG нет: кладём кадр на белый лист. Через RGBA
                # к тому же приводятся и палитра, и анимация — берётся первый кадр.
                frame = image.convert("RGBA")
                canvas = Image.new("RGB", frame.size, (255, 255, 255))
                canvas.paste(frame, mask=frame.split()[-1])
                canvas.save(target, "JPEG", quality=90)
        except Exception:
            target.unlink(missing_ok=True)
            # Разбираться потом придётся по логам: пользователю в лицо ошибка
            # библиотеки не годится, а знать её причину всё равно надо.
            logger.warning("Не удалось перекодировать %s в JPEG", source, exc_info=True)
            raise UploadRejected(f"«{label}»: не удалось прочитать файл как картинку.")
        finally:
            source.unlink(missing_ok=True)
        return target
