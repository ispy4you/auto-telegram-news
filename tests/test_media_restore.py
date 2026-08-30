"""Файлы не переживают перезапуск контейнера, а сообщение в источнике — переживает.

Страница поста просит перекачать их сама. Здесь проверяется, что просьба
доходит, что безнадёжный пост не дёргают на каждое обновление страницы,
и что сорвавшееся восстановление не роняет страницу.
"""

import asyncio

import pytest

from app.models import MediaItem, RawPost, TargetChannel
from app.services import media_restore


@pytest.fixture(autouse=True)
def clean_state():
    media_restore.reset_state()
    yield
    media_restore.reset_state()


@pytest.fixture
def post_with_lost_media(db_session, source):
    post = RawPost(
        source_id=source.id,
        telegram_message_id=1,
        text_hash="h",
        original_text="новость",
        has_media=True,
        media_count=1,
    )
    db_session.add(post)
    db_session.flush()
    db_session.add(MediaItem(
        raw_post_id=post.id,
        telegram_message_id=1,
        media_type="photo",
        file_path="data/media/1/1/gone.jpg",  # файла нет: диск не пережил деплой
    ))
    db_session.commit()
    db_session.refresh(post)
    return post


def _reader_returns(monkeypatch, value):
    calls = []

    async def _restore(_self, _db, raw_post):
        calls.append(raw_post.id)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr("app.services.telegram_reader.TelegramReaderService.restore_media", _restore)
    return calls


def test_present_files_are_left_alone(db_session, post_with_lost_media, monkeypatch, tmp_path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"jpeg")
    post_with_lost_media.media_items[0].file_path = str(photo)
    db_session.commit()
    calls = _reader_returns(monkeypatch, 1)

    result = asyncio.run(media_restore.restore(db_session, post_with_lost_media))

    assert result == {"status": "ok", "restored": 0, "missing": 0}
    assert calls == [], "перекачивать нечего — в Telegram лезть незачем"


def test_lost_files_are_fetched_again(db_session, post_with_lost_media, monkeypatch):
    calls = _reader_returns(monkeypatch, 1)

    result = asyncio.run(media_restore.restore(db_session, post_with_lost_media))

    assert result["status"] == "ok"
    assert result["restored"] == 1
    assert calls == [post_with_lost_media.id]


def test_a_hopeless_post_is_not_retried_on_every_page_open(db_session, post_with_lost_media, monkeypatch):
    """Сообщение в источнике удалили — обновление страницы не должно долбить Telegram."""
    calls = _reader_returns(monkeypatch, 0)

    first = asyncio.run(media_restore.restore(db_session, post_with_lost_media))
    second = asyncio.run(media_restore.restore(db_session, post_with_lost_media))

    assert first["status"] == "gone"
    assert second["status"] == "cooldown"
    assert len(calls) == 1


def test_a_broken_session_does_not_break_the_page(db_session, post_with_lost_media, monkeypatch):
    _reader_returns(monkeypatch, RuntimeError("сессия устарела"))

    result = asyncio.run(media_restore.restore(db_session, post_with_lost_media))

    assert result["status"] == "error"
    assert "сессия устарела" in result["reason"]


def test_the_page_asks_for_the_files_itself(logged_in, db_session, post_with_lost_media):
    db_session.add(TargetChannel(title="Target", chat_id="@target", project_id=1))
    db_session.commit()

    page = logged_in.get(f"/posts/{post_with_lost_media.id}").text

    assert 'id="media-restore"' in page, "страница должна сама попросить перекачать медиа"
    assert f'/posts/{post_with_lost_media.id}/restore-media' in page
    assert "media-gone" in page, "вместо битой картинки нужна заглушка"


def test_the_route_reports_what_happened(logged_in, csrf, db_session, post_with_lost_media, monkeypatch):
    _reader_returns(monkeypatch, 1)

    response = logged_in.post(
        f"/posts/{post_with_lost_media.id}/restore-media",
        data={"csrf_token": csrf(logged_in, f"/posts/{post_with_lost_media.id}")},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "restored": 1, "missing": 0}


def test_the_banner_is_absent_when_every_file_is_in_place(logged_in, db_session, source, tmp_path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"jpeg")
    post = RawPost(source_id=source.id, telegram_message_id=2, text_hash="h2", has_media=True, media_count=1)
    db_session.add(post)
    db_session.flush()
    db_session.add(MediaItem(
        raw_post_id=post.id, telegram_message_id=2, media_type="photo", file_path=str(photo),
    ))
    db_session.commit()

    page = logged_in.get(f"/posts/{post.id}").text

    assert 'id="media-restore"' not in page
