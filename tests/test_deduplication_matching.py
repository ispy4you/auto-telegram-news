from datetime import datetime, timedelta, timezone

from app.models import RawPost, RawPostStatus
from app.services.deduplication import DeduplicationService


def _make_post(db_session, source, telegram_message_id: int, text: str, created_at=None) -> RawPost:
    service = DeduplicationService()
    normalized = service.normalize_text(text)
    post = RawPost(
        source_id=source.id,
        telegram_message_id=telegram_message_id,
        original_text=text,
        normalized_text=normalized,
        text_hash=service.text_hash(normalized),
        status=RawPostStatus.READY.value,
        created_at=created_at or datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(post)
    db_session.commit()
    db_session.refresh(post)
    return post


def test_deduplicate_post_marks_exact_hash_match_as_duplicate(db_session, source):
    existing = _make_post(db_session, source, 1, "Обычная новость про завод")
    incoming = RawPost(
        source_id=source.id,
        telegram_message_id=2,
        original_text="Обычная новость про завод",
        normalized_text="",
        text_hash="",
        status=RawPostStatus.NEW.value,
    )
    db_session.add(incoming)
    db_session.commit()

    DeduplicationService().deduplicate_post(db_session, incoming)

    assert incoming.status == RawPostStatus.DUPLICATE.value
    assert incoming.duplicate_of_id == existing.id
    assert incoming.dedupe_score == 100.0


def test_deduplicate_post_marks_near_duplicate_via_fuzzy_match(db_session, source):
    existing = _make_post(
        db_session, source, 1,
        "Вчера в городе произошло крупное дорожно-транспортное происшествие на мосту",
    )
    incoming = RawPost(
        source_id=source.id,
        telegram_message_id=2,
        original_text="Вчера в городе произошло крупное дорожно-транспортное происшествие на мосту!!!",
        normalized_text="",
        text_hash="",
        status=RawPostStatus.NEW.value,
    )
    db_session.add(incoming)
    db_session.commit()

    DeduplicationService().deduplicate_post(db_session, incoming)

    assert incoming.status == RawPostStatus.DUPLICATE.value
    assert incoming.duplicate_of_id == existing.id
    assert incoming.dedupe_score >= 88.0


def test_deduplicate_post_keeps_distinct_texts_as_ready(db_session, source):
    _make_post(db_session, source, 1, "Открытие нового парка в центре города")
    incoming = RawPost(
        source_id=source.id,
        telegram_message_id=2,
        original_text="Цены на нефть выросли на фоне решения ОПЕК",
        normalized_text="",
        text_hash="",
        status=RawPostStatus.NEW.value,
    )
    db_session.add(incoming)
    db_session.commit()

    DeduplicationService().deduplicate_post(db_session, incoming)

    assert incoming.status == RawPostStatus.READY.value
    assert incoming.duplicate_of_id is None


def test_deduplicate_post_ignores_candidates_outside_48h_window(db_session, source):
    old_created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=72)
    _make_post(db_session, source, 1, "Обычная новость про завод", created_at=old_created_at)
    incoming = RawPost(
        source_id=source.id,
        telegram_message_id=2,
        original_text="Обычная новость про завод, но с опечаткой",
        normalized_text="",
        text_hash="",
        status=RawPostStatus.NEW.value,
    )
    db_session.add(incoming)
    db_session.commit()

    DeduplicationService().deduplicate_post(db_session, incoming)

    # Different hash (typo added) and the only candidate is outside the 48h window,
    # so it should not be matched via the fuzzy phase either.
    assert incoming.status == RawPostStatus.READY.value


def test_deduplicate_post_handles_empty_text(db_session, source):
    incoming = RawPost(
        source_id=source.id,
        telegram_message_id=1,
        original_text="",
        normalized_text="",
        text_hash="",
        status=RawPostStatus.NEW.value,
    )
    db_session.add(incoming)
    db_session.commit()

    DeduplicationService().deduplicate_post(db_session, incoming)

    assert incoming.status == RawPostStatus.READY.value
