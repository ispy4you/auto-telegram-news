"""Старые файлы из каналов уходят с диска, но пост их не теряет."""

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import (
    AppSetting,
    GeneratedPost,
    MediaItem,
    MediaOrigin,
    PublishJob,
    PublishJobStatus,
    RawPost,
    TargetChannel,
)
from app.services import media_cache

_DAY = 86400


@pytest.fixture(autouse=True)
def media_root(tmp_path, monkeypatch):
    # Настоящий media_root относительный, а менять рабочую папку нельзя:
    # относительно неё приложение ищет шаблоны.
    root = tmp_path / "data" / "media"
    root.mkdir(parents=True)
    monkeypatch.setattr(media_cache, "get_settings", lambda: SimpleNamespace(media_root=root))
    return root


def _post(db, source, message_id: int) -> RawPost:
    post = RawPost(source_id=source.id, telegram_message_id=message_id, text_hash=str(message_id))
    db.add(post)
    db.flush()
    return post


def _file(db, post, name: str, age_days: float, origin=MediaOrigin.SOURCE) -> MediaItem:
    path = media_cache.get_settings().media_root / "2026/09/01" / str(post.source_id) / str(post.id) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 1024)
    stamp = time.time() - age_days * _DAY
    os.utime(path, (stamp, stamp))
    item = MediaItem(
        raw_post_id=post.id,
        telegram_message_id=post.telegram_message_id,
        file_path=str(path),
        origin=origin.value,
    )
    db.add(item)
    db.commit()
    return item


def test_old_source_files_go_and_their_records_stay(db_session, source):
    post = _post(db_session, source, 1)
    old = _file(db_session, post, "old.mp4", age_days=11)
    fresh = _file(db_session, post, "fresh.jpg", age_days=2)

    freed = media_cache.prune(db_session)

    assert freed.files == 1 and freed.bytes == 1024
    assert not Path(old.file_path).exists()
    assert Path(fresh.file_path).exists()
    # Запись нужна, чтобы страница поста знала, что перекачать.
    assert db_session.get(MediaItem, old.id) is not None


def test_own_uploads_are_never_removed(db_session, source):
    post = _post(db_session, source, 1)
    manual = _file(db_session, post, "mine.jpg", age_days=400, origin=MediaOrigin.MANUAL)

    assert media_cache.drop_source_files(db_session, None).files == 0
    assert Path(manual.file_path).exists()


def test_file_age_is_what_counts_not_record_age(db_session, source):
    """Перекачанный при открытии поста файл свеж, хотя запись о нём старая."""
    post = _post(db_session, source, 1)
    item = _file(db_session, post, "restored.jpg", age_days=0)
    item.created_at = item.created_at.replace(year=2020)
    db_session.commit()

    media_cache.prune(db_session)

    assert Path(item.file_path).exists()


def test_posts_waiting_for_publish_keep_their_files(db_session, source):
    post = _post(db_session, source, 1)
    item = _file(db_session, post, "scheduled.jpg", age_days=30)
    channel = TargetChannel(title="Target", chat_id="@target")
    generated = GeneratedPost(raw_post_id=post.id, generated_text="текст")
    db_session.add_all([channel, generated])
    db_session.flush()
    db_session.add(PublishJob(
        generated_post_id=generated.id, target_channel_id=channel.id, status=PublishJobStatus.PENDING.value,
    ))
    db_session.commit()

    assert media_cache.prune(db_session).files == 0
    assert Path(item.file_path).exists()


def test_zero_days_disables_the_schedule(db_session, source):
    post = _post(db_session, source, 1)
    item = _file(db_session, post, "old.mp4", age_days=500)
    db_session.add(AppSetting(key="media_cache_days", value="0"))
    db_session.commit()

    assert media_cache.prune(db_session).files == 0
    assert Path(item.file_path).exists()


def test_emptied_folders_are_removed_but_the_root_stays(db_session, source, media_root):
    post = _post(db_session, source, 1)
    _file(db_session, post, "old.mp4", age_days=20)
    neighbour = _post(db_session, source, 2)
    kept = _file(db_session, neighbour, "fresh.jpg", age_days=1)

    media_cache.prune(db_session)

    assert not (media_root / "2026/09/01" / str(source.id) / str(post.id)).exists()
    assert Path(kept.file_path).exists()
    assert media_root.is_dir()


def test_already_missing_files_are_skipped(db_session, source):
    post = _post(db_session, source, 1)
    item = _file(db_session, post, "gone.jpg", age_days=20)
    Path(item.file_path).unlink()

    assert media_cache.prune(db_session).files == 0


def test_the_cleanup_is_logged(db_session, source):
    from app.models import ActionLog

    post = _post(db_session, source, 1)
    _file(db_session, post, "old.mp4", age_days=20)

    media_cache.prune(db_session)

    log = db_session.scalars(select(ActionLog).where(ActionLog.action == "media_cleanup")).one()
    assert "1 файлов" in log.message


def test_manual_cleanup_keeps_records_so_posts_can_restore(logged_in, csrf, db_session, source):
    post = _post(db_session, source, 1)
    source_item = _file(db_session, post, "old.mp4", age_days=40)
    manual_item = _file(db_session, post, "mine.jpg", age_days=40, origin=MediaOrigin.MANUAL)

    response = logged_in.post("/settings/cleanup-media", data={
        "csrf_token": csrf(logged_in, "/settings"),
        "days": "0",
    })

    assert response.status_code == 302
    assert not Path(source_item.file_path).exists()
    assert Path(manual_item.file_path).exists()
    db_session.expire_all()
    assert db_session.scalars(select(MediaItem)).all(), "записи о медиа должны остаться"
