"""Файлы медиа на диске: куда класть и что принимать от редактора.

Диск на хостинге — кэш: после деплоя он пуст. Для медиа из каналов это
не беда, оно перекачивается из Telegram. У файла, который редактор загрузил
сам, второго экземпляра нет: он живёт до ближайшего перезапуска, и это
осознанный размен — хранилище ради одной картинки заводить дороже.
"""

import secrets
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.models import MediaType
from app.services import settings_registry

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
            target = self._to_jpeg(target, label)
            content_type, size = "image/jpeg", target.stat().st_size

        return {
            "path": str(target),
            "media_type": media_type,
            "file_size": size,
            "mime_type": content_type,
        }

    @staticmethod
    def _to_jpeg(source: Path, label: str) -> Path:
        """Пересохраняет картинку в JPEG рядом и убирает исходник."""
        from PIL import Image

        target = source.with_suffix(".jpg")
        try:
            with Image.open(source) as image:
                # Прозрачность в JPEG не живёт: кладём кадр на белый лист.
                # Заодно это приводит к RGB палитру и анимацию (берётся первый кадр).
                frame = image.convert("RGBA")
                canvas = Image.new("RGB", frame.size, (255, 255, 255))
                canvas.paste(frame, mask=frame.split()[-1])
                canvas.save(target, "JPEG", quality=90)
        except Exception:
            target.unlink(missing_ok=True)
            raise UploadRejected(f"«{label}»: не удалось прочитать файл как картинку.")
        finally:
            source.unlink(missing_ok=True)
        return target
