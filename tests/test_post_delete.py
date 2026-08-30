"""Удаление постов.

Дубликат ссылается на оригинал внешним ключом, о котором модели не знают.
Из-за этого «удалить выбранные» падало с 500, стоило в выборку попасть
оригиналу — а при выборе всех постов на странице это почти неизбежно.
"""

import pytest
from sqlalchemy import select

from app.models import GeneratedPost, MediaItem, PublishJob, RawPost, RawPostStatus, TargetChannel


@pytest.fixture
def original_and_duplicate(db_session, source):
    original = RawPost(source_id=source.id, telegram_message_id=1, text_hash="a", original_text="новость")
    db_session.add(original)
    db_session.flush()
    duplicate = RawPost(
        source_id=source.id,
        telegram_message_id=2,
        text_hash="b",
        original_text="та же новость",
        status=RawPostStatus.DUPLICATE.value,
        duplicate_of_id=original.id,
    )
    db_session.add(duplicate)
    db_session.commit()
    return original, duplicate


def _delete(client, csrf, ids):
    return client.post("/posts/bulk", data={
        "csrf_token": csrf(client, "/posts"),
        "bulk_action": "delete",
        "post_ids": [str(i) for i in ids],
    })


def test_deleting_an_original_no_longer_fails(logged_in, csrf, db_session, original_and_duplicate):
    original, duplicate = original_and_duplicate

    response = _delete(logged_in, csrf, [original.id])

    assert response.status_code == 302
    assert db_session.get(RawPost, original.id) is None
    survivor = db_session.get(RawPost, duplicate.id)
    assert survivor is not None, "дубликат удалять не просили"
    assert survivor.duplicate_of_id is None, "ссылка на исчезнувший оригинал должна сняться"


def test_deleting_both_at_once_works(logged_in, csrf, db_session, original_and_duplicate):
    original, duplicate = original_and_duplicate

    response = _delete(logged_in, csrf, [original.id, duplicate.id])

    assert response.status_code == 302
    assert db_session.scalars(select(RawPost)).all() == []


def test_everything_hanging_off_the_post_goes_with_it(logged_in, csrf, db_session, source):
    post = RawPost(source_id=source.id, telegram_message_id=3, text_hash="c", original_text="новость")
    db_session.add(post)
    db_session.flush()
    db_session.add(MediaItem(
        raw_post_id=post.id, telegram_message_id=3, media_type="photo", file_path="data/media/x.jpg",
    ))
    generated = GeneratedPost(raw_post_id=post.id, generated_text="текст")
    channel = TargetChannel(title="Target", chat_id="@target")
    db_session.add_all([generated, channel])
    db_session.flush()
    db_session.add(PublishJob(generated_post_id=generated.id, target_channel_id=channel.id))
    db_session.commit()

    _delete(logged_in, csrf, [post.id])

    assert db_session.scalars(select(MediaItem)).all() == []
    assert db_session.scalars(select(GeneratedPost)).all() == []
    assert db_session.scalars(select(PublishJob)).all() == []


def test_deleting_a_single_post_from_its_page(logged_in, csrf, db_session, original_and_duplicate):
    original, duplicate = original_and_duplicate

    response = logged_in.post(
        f"/posts/{original.id}/delete",
        data={"csrf_token": csrf(logged_in, "/posts")},
    )

    assert response.status_code == 302
    assert db_session.get(RawPost, original.id) is None
    assert db_session.get(RawPost, duplicate.id).duplicate_of_id is None
