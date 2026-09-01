"""Своё медиа в посте: загрузить, переставить, убрать.

Диск на хостинге — кэш, и для загруженного руками файла это значит «до
ближайшего перезапуска». Размен принят осознанно, но панель обязана вести себя
честно: не ходить за таким файлом в Telegram и не молчать о пропаже.
"""

from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import Settings
from app.models import MediaItem, MediaOrigin, RawPost
from app.services import media_restore, settings_registry

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 512


@pytest.fixture
def media_root(tmp_path, monkeypatch):
    """Файлы тестов не должны оседать в рабочем каталоге проекта."""
    monkeypatch.setattr(Settings, "media_root", property(lambda self: tmp_path))
    return tmp_path


@pytest.fixture
def post(db_session, source):
    item = RawPost(source_id=source.id, telegram_message_id=1, text_hash="h", original_text="новость")
    db_session.add(item)
    db_session.commit()
    return item


def _upload(client, csrf, post_id, files):
    return client.post(
        f"/posts/{post_id}/media",
        data={"csrf_token": csrf(client, f"/posts/{post_id}")},
        files=files,
    )


def _photo(name="own.jpg", content=JPEG, content_type="image/jpeg"):
    return [("files", (name, content, content_type))]


def _items(db_session):
    return db_session.scalars(select(MediaItem).order_by(MediaItem.sort_order)).all()


def test_an_uploaded_photo_becomes_part_of_the_post(logged_in, csrf, db_session, post, media_root):
    response = _upload(logged_in, csrf, post.id, _photo())

    items = _items(db_session)
    db_session.refresh(post)
    assert response.status_code == 302
    assert len(items) == 1
    assert items[0].origin == MediaOrigin.MANUAL.value
    assert items[0].media_type == "photo"
    assert Path(items[0].file_path).read_bytes() == JPEG
    assert post.has_media is True and post.media_count == 1


def test_a_multi_megabyte_file_arrives_whole(logged_in, csrf, db_session, post, media_root):
    """Файл читается кусками по мегабайту — склейка кусков должна быть точной.

    Заодно это проверка, что многомегабайтная часть multipart вообще доезжает:
    до маршрута она идёт через CSRF-middleware и разбор формы.
    """
    big = JPEG + bytes(range(256)) * 12000  # чуть больше 3 МБ

    _upload(logged_in, csrf, post.id, _photo(content=big))

    stored = Path(_items(db_session)[0].file_path)
    assert stored.stat().st_size == len(big)
    assert stored.read_bytes() == big


def test_an_empty_file_is_refused(logged_in, csrf, db_session, post, media_root):
    _upload(logged_in, csrf, post.id, _photo(content=b""))

    assert _items(db_session) == []
    assert list(media_root.rglob("*.jpg")) == []


def test_the_uploaded_file_is_not_named_by_the_browser(logged_in, csrf, db_session, post, media_root):
    """Имя из браузера — данные пользователя, в пути на диске им делать нечего."""
    _upload(logged_in, csrf, post.id, _photo(name="../../evil.jpg"))

    stored = Path(_items(db_session)[0].file_path)
    assert "evil" not in stored.name
    assert media_root in stored.parents


def test_an_empty_field_is_not_an_upload(logged_in, csrf, db_session, post, media_root):
    """Браузер с пустым полем присылает часть с filename="" — это не файл.

    Тело собрано руками: клиент тестов в таком случае отправляет обычное поле
    формы, и до маршрута дело не доходит — проверять надо ровно то, что шлёт
    браузер.
    """
    token = csrf(logged_in, f"/posts/{post.id}")
    boundary = "----boundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="csrf_token"\r\n\r\n{token}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="files"; filename=""\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n\r\n"
        f"--{boundary}--\r\n"
    ).encode()

    response = logged_in.post(
        f"/posts/{post.id}/media",
        content=body,
        headers={"content-type": f"multipart/form-data; boundary={boundary}"},
    )

    assert "media_err" in response.headers["location"]
    assert _items(db_session) == []


def test_a_forbidden_type_is_refused(logged_in, csrf, db_session, post, media_root):
    response = _upload(logged_in, csrf, post.id, [("files", ("script.txt", b"hello", "text/plain"))])

    assert "media_err" in response.headers["location"]
    assert _items(db_session) == []
    assert list(media_root.rglob("*.*")) == []


def test_a_file_over_the_limit_leaves_nothing_behind(logged_in, csrf, db_session, post, media_root):
    settings_registry.store(db_session, {"max_media_mb": "1"})

    response = _upload(logged_in, csrf, post.id, _photo(content=b"0" * (1024 * 1024 + 10)))

    assert "media_err" in response.headers["location"]
    assert _items(db_session) == []
    assert list(media_root.rglob("*.jpg")) == [], "недогруженный файл должен быть убран"


def test_a_bad_second_file_cancels_the_whole_upload(logged_in, csrf, db_session, post, media_root):
    """Половина альбома хуже понятного отказа: либо всё, либо ничего."""
    response = _upload(logged_in, csrf, post.id, [
        ("files", ("good.jpg", JPEG, "image/jpeg")),
        ("files", ("bad.txt", b"hello", "text/plain")),
    ])

    assert "media_err" in response.headers["location"]
    assert _items(db_session) == []
    assert list(media_root.rglob("*.jpg")) == []


def test_an_album_does_not_grow_past_ten(logged_in, csrf, db_session, post, media_root):
    for index in range(10):
        db_session.add(MediaItem(
            raw_post_id=post.id, telegram_message_id=index, media_type="photo",
            file_path=f"нет/{index}.jpg", sort_order=index,
        ))
    db_session.commit()

    response = _upload(logged_in, csrf, post.id, _photo())

    assert "media_err" in response.headers["location"]
    assert len(_items(db_session)) == 10


def test_deleting_a_file_takes_the_row_and_the_file(logged_in, csrf, db_session, post, media_root):
    _upload(logged_in, csrf, post.id, _photo())
    item = _items(db_session)[0]
    stored = Path(item.file_path)

    logged_in.post(f"/posts/{post.id}/media/{item.id}/delete",
                   data={"csrf_token": csrf(logged_in, f"/posts/{post.id}")})

    db_session.refresh(post)
    assert _items(db_session) == []
    assert not stored.exists()
    assert post.has_media is False and post.media_count == 0


def test_moving_a_file_changes_the_album_order(logged_in, csrf, db_session, post, media_root):
    _upload(logged_in, csrf, post.id, [
        ("files", ("first.jpg", JPEG, "image/jpeg")),
        ("files", ("second.jpg", JPEG + b"x", "image/jpeg")),
    ])
    first, second = _items(db_session)

    logged_in.post(f"/posts/{post.id}/media/{second.id}/move",
                   data={"csrf_token": csrf(logged_in, f"/posts/{post.id}"), "direction": "up"})

    assert [item.id for item in _items(db_session)] == [second.id, first.id]


def test_a_lost_manual_file_is_not_chased_in_telegram(db_session, post):
    """Восстановление ходит в источник только за тем, что оттуда и пришло."""
    db_session.add_all([
        MediaItem(raw_post_id=post.id, telegram_message_id=1, media_type="photo",
                  file_path="нет/из-канала.jpg", origin=MediaOrigin.SOURCE.value),
        MediaItem(raw_post_id=post.id, telegram_message_id=0, media_type="photo",
                  file_path="нет/своё.jpg", origin=MediaOrigin.MANUAL.value),
    ])
    db_session.commit()
    db_session.refresh(post)

    assert [item.file_path for item in media_restore.missing_media(post)] == ["нет/из-канала.jpg"]
    assert [item.file_path for item in media_restore.lost_media(post)] == ["нет/своё.jpg"]


def test_an_upload_without_a_csrf_token_is_refused(logged_in, post, media_root, db_session):
    response = logged_in.post(f"/posts/{post.id}/media", files=_photo())

    assert response.status_code == 403
    assert _items(db_session) == []
