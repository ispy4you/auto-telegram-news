"""Слишком большая картинка ужимается на загрузке.

Telegram принимает фотографию, только если сумма сторон не больше 10000. Отказ
приходил на публикации — когда пост уже написан, отредактирован и одобрен, а
причина выглядела как «PHOTO_INVALID_DIMENSIONS». Чинить это в тот момент
поздно и обидно; на загрузке это ничего не стоит.
"""

import asyncio
import io

import pytest
from PIL import Image

from app.services.media_storage import MAX_SIDE_SUM, MediaStorageService


class _Upload:
    """То, что отдаёт FastAPI: имя, тип и файл кусками."""

    def __init__(self, filename: str, content_type: str, payload: bytes):
        self.filename = filename
        self.content_type = content_type
        self._stream = io.BytesIO(payload)

    async def read(self, size: int) -> bytes:
        return self._stream.read(size)


def _image_bytes(width: int, height: int, fmt: str = "JPEG", mode: str = "RGB") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, (width, height), (200, 100, 50) if mode == "RGB" else (200, 100, 50, 255)).save(buffer, fmt)
    return buffer.getvalue()


@pytest.fixture
def storage(tmp_path, monkeypatch):
    service = MediaStorageService()
    monkeypatch.setattr(service.settings, "media_root", tmp_path)
    return service


def _save(storage, db, upload):
    return asyncio.run(storage.save_upload(upload, source_id=1, post_id=1, db=db))


def test_an_oversized_photo_is_shrunk_to_fit(storage, db_session):
    """8000x4000 — сумма 12000, Telegram такое не примет."""
    upload = _Upload("панорама.jpg", "image/jpeg", _image_bytes(8000, 4000))

    saved = _save(storage, db_session, upload)

    with Image.open(saved["path"]) as image:
        assert sum(image.size) <= MAX_SIDE_SUM
        # Пропорции те же: Telegram ограничивает и соотношение сторон.
        assert abs(image.size[0] / image.size[1] - 2.0) < 0.01


def test_the_reported_size_matches_the_file_on_disk(storage, db_session):
    """Иначе в базе останется размер исходника, а на диске — уменьшенный."""
    upload = _Upload("панорама.jpg", "image/jpeg", _image_bytes(8000, 4000))

    saved = _save(storage, db_session, upload)

    from pathlib import Path
    assert saved["file_size"] == Path(saved["path"]).stat().st_size


def test_a_normal_photo_is_left_alone(storage, db_session):
    """Скриншот с монитора в предел укладывается — трогать нечего."""
    original = _image_bytes(1920, 1080)
    upload = _Upload("скриншот.jpg", "image/jpeg", original)

    saved = _save(storage, db_session, upload)

    from pathlib import Path
    assert Path(saved["path"]).read_bytes() == original
    with Image.open(saved["path"]) as image:
        assert image.size == (1920, 1080)


def test_a_png_stays_a_png(storage, db_session):
    """Уменьшение — не повод молча переводить формат и терять прозрачность."""
    upload = _Upload("схема.png", "image/png", _image_bytes(7000, 6000, "PNG", "RGBA"))

    saved = _save(storage, db_session, upload)

    assert saved["mime_type"] == "image/png"
    with Image.open(saved["path"]) as image:
        assert image.format == "PNG"
        assert sum(image.size) <= MAX_SIDE_SUM
        assert image.mode == "RGBA"


def test_an_oversized_webp_is_converted_and_then_shrunk(storage, db_session):
    """Два преобразования подряд: webp Telegram считает стикером, а размер — велик."""
    upload = _Upload("длинная.webp", "image/webp", _image_bytes(9000, 5000, "WEBP"))

    saved = _save(storage, db_session, upload)

    assert saved["mime_type"] == "image/jpeg"
    with Image.open(saved["path"]) as image:
        assert image.format == "JPEG"
        assert sum(image.size) <= MAX_SIDE_SUM


def test_a_video_is_not_touched(storage, db_session):
    """Ужимать нечем и незачем: у видео свои пределы, и это не наша задача."""
    payload = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100
    upload = _Upload("ролик.mp4", "video/mp4", payload)

    saved = _save(storage, db_session, upload)

    from pathlib import Path
    assert Path(saved["path"]).read_bytes() == payload
    assert saved["mime_type"] == "video/mp4"


def test_a_file_pillow_cannot_read_passes_through_unchanged(storage, db_session):
    """Уменьшение — не повод заводить новую политику приёма.

    До этой правки такой файл загружался; отвергать его заодно значило бы
    решить за пользователя, что на загрузку принимается только разобранное
    Pillow. У webp иначе: там перекодировка обязательна.
    """
    payload = b"not an image at all" * 10
    upload = _Upload("странное.jpg", "image/jpeg", payload)

    saved = _save(storage, db_session, upload)

    from pathlib import Path
    assert Path(saved["path"]).read_bytes() == payload
    assert saved["file_size"] == len(payload)
