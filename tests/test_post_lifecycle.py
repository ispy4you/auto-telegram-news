"""Связанные переходы двигают оба объекта, а не один."""

from app.models import (
    GeneratedPost,
    GeneratedPostStatus,
    PublishJob,
    PublishJobStatus,
    RawPost,
    RawPostStatus,
)
from app.services import post_lifecycle


def _trio(db, source):
    raw = RawPost(source_id=source.id, telegram_message_id=1, text_hash="h", original_text="t")
    db.add(raw)
    db.flush()
    generated = GeneratedPost(raw_post_id=raw.id, generated_text="текст")
    db.add(generated)
    db.flush()
    job = PublishJob(generated_post_id=generated.id, target_channel_id=1)
    db.add(job)
    db.flush()
    return raw, generated, job


def test_publish_moves_post_draft_and_job_together(db_session, source):
    raw, generated, job = _trio(db_session, source)

    post_lifecycle.mark_published(generated, raw, job, target_channel_id=5, message_id=42)

    assert generated.status == GeneratedPostStatus.PUBLISHED.value
    assert generated.telegram_message_id == 42
    assert generated.target_channel_id == 5
    assert generated.published_at is not None
    assert raw.status == RawPostStatus.PUBLISHED.value
    assert job.status == PublishJobStatus.SUCCESS.value


def test_publish_clears_the_previous_error(db_session, source):
    raw, generated, job = _trio(db_session, source)
    post_lifecycle.mark_publish_failed(generated, raw, job, "канал недоступен")

    post_lifecycle.mark_published(generated, raw, job, target_channel_id=5, message_id=42)

    assert generated.publish_error is None
    assert job.last_error is None


def test_failure_marks_both_sides(db_session, source):
    raw, generated, job = _trio(db_session, source)

    post_lifecycle.mark_publish_failed(generated, raw, job, "канал недоступен")

    assert generated.status == GeneratedPostStatus.FAILED.value
    assert generated.publish_error == "канал недоступен"
    assert raw.status == RawPostStatus.FAILED.value
    assert job.status == PublishJobStatus.FAILED.value
    assert job.last_error == "канал недоступен"


def test_retry_reset_returns_both_to_working_state(db_session, source):
    raw, generated, job = _trio(db_session, source)
    post_lifecycle.mark_publish_failed(generated, raw, job, "таймаут")

    post_lifecycle.reset_for_retry(generated, raw)

    assert generated.status == GeneratedPostStatus.APPROVED.value
    assert generated.publish_error is None
    assert raw.status == RawPostStatus.GENERATED.value


def test_rejection_is_paired(db_session, source):
    raw, generated, _job = _trio(db_session, source)

    post_lifecycle.reject(raw, reason="реклама", generated=generated)

    assert raw.status == RawPostStatus.REJECTED.value
    assert raw.ai_suitable is False
    assert raw.ai_skip_reason == "реклама"
    assert generated.status == GeneratedPostStatus.REJECTED.value


def test_duplicate_records_the_original_and_the_score(db_session, source):
    original = RawPost(source_id=source.id, telegram_message_id=1, text_hash="a", original_text="t")
    db_session.add(original)
    db_session.flush()
    copy = RawPost(source_id=source.id, telegram_message_id=2, text_hash="b", original_text="t")
    db_session.add(copy)
    db_session.flush()

    post_lifecycle.mark_duplicate(copy, original, 97.5)

    assert copy.status == RawPostStatus.DUPLICATE.value
    assert copy.duplicate_of_id == original.id
    assert copy.dedupe_score == 97.5
