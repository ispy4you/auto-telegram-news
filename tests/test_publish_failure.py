"""Отказ Telegram — это не поломка панели.

Не тот формат картинки, бот не админ в канале, канал удалён — всё это
обычные ответы Telegram. Раньше любой из них превращался в страницу 500:
причина уже была записана в базу, но админ видел её только в логах.
"""

from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.models import GeneratedPost, GeneratedPostStatus, RawPost, TargetChannel


def _ready_post(db_session, source):
    post = RawPost(source_id=source.id, telegram_message_id=1, text_hash="h", original_text="новость")
    db_session.add(post)
    db_session.flush()
    generated = GeneratedPost(raw_post_id=post.id, generated_text="Пост", model_name="m",
                              status=GeneratedPostStatus.APPROVED.value)
    target = TargetChannel(title="Канал", chat_id="@channel", enabled=True)
    db_session.add_all([generated, target])
    db_session.commit()
    return post, generated, target


def test_a_refusal_from_telegram_returns_to_the_post(logged_in, csrf, db_session, source):
    post, generated, target = _ready_post(db_session, source)
    boom = AsyncMock(side_effect=RuntimeError("Telegram server says - Bad Request: PHOTO_INVALID_DIMENSIONS"))

    with patch("app.services.telegram_publisher.TelegramPublisherService.publish_generated_post", boom):
        response = logged_in.post(f"/generated/{generated.id}/publish", data={
            "csrf_token": csrf(logged_in, f"/posts/{post.id}"),
            "target_channel_id": str(target.id),
        })

    assert response.status_code == 302, "страница 500 вместо панели — это не ответ"
    assert response.headers["location"] == f"/posts/{post.id}"


def test_the_reason_is_visible_on_the_post(logged_in, db_session, source):
    post, generated, _ = _ready_post(db_session, source)
    generated.status = GeneratedPostStatus.FAILED.value
    generated.publish_error = "Bad Request: PHOTO_INVALID_DIMENSIONS"
    db_session.commit()

    page = logged_in.get(f"/posts/{post.id}").text

    assert "Публикация не прошла" in page
    assert "PHOTO_INVALID_DIMENSIONS" in page


def test_a_successful_publish_still_redirects(logged_in, csrf, db_session, source):
    post, generated, target = _ready_post(db_session, source)

    with patch("app.services.telegram_publisher.TelegramPublisherService.publish_generated_post", AsyncMock()):
        response = logged_in.post(f"/generated/{generated.id}/publish", data={
            "csrf_token": csrf(logged_in, f"/posts/{post.id}"),
            "target_channel_id": str(target.id),
        })

    assert response.status_code == 302
    assert db_session.scalar(select(GeneratedPost)).publish_error is None
