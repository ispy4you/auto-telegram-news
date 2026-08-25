"""Семантическая дедупликация включена по умолчанию."""

import json

import pytest

from app.models import AppSetting, RawPost, RawPostStatus
from app.services.deduplication import DeduplicationService
from app.services.settings_registry import DEFAULT_SEMANTIC_THRESHOLD


@pytest.fixture
def fake_embedder(monkeypatch):
    """Вектор зависит только от первого слова — тексты с ним считаются похожими."""
    vectors = {"кошка": [1.0, 0.0], "собака": [0.0, 1.0]}

    def embed_text_json(text: str):
        first = text.split()[0] if text.split() else ""
        vec = vectors.get(first)
        return json.dumps(vec) if vec else None

    from app.services import embedder

    monkeypatch.setattr(embedder, "embed_text_json", embed_text_json)
    return embedder


def _post(db, source, message_id: int, text: str) -> RawPost:
    post = RawPost(
        source_id=source.id,
        telegram_message_id=message_id,
        text_hash=f"h{message_id}",
        original_text=text,
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


def test_default_threshold_is_enabled():
    assert DEFAULT_SEMANTIC_THRESHOLD > 0


def test_embedding_is_stored_even_when_semantics_is_off(db_session, source, fake_embedder):
    """Иначе после включения порога сравнивать не с чем — окно пустое."""
    db_session.add(AppSetting(key="semantic_threshold", value="0"))
    db_session.commit()
    post = _post(db_session, source, 1, "кошка гуляет по крыше дома")

    DeduplicationService().deduplicate_post(db_session, post)

    assert post.embedding is not None
    assert post.status == RawPostStatus.READY.value


def test_semantically_close_post_is_marked_duplicate(db_session, source, fake_embedder):
    first = _post(db_session, source, 1, "кошка гуляет по крыше старого дома")
    DeduplicationService().deduplicate_post(db_session, first)
    db_session.commit()

    second = _post(db_session, source, 2, "кошка забралась на крышу соседнего здания")
    DeduplicationService().deduplicate_post(db_session, second)

    assert second.status == RawPostStatus.DUPLICATE.value
    assert second.duplicate_of_id == first.id


def test_semantically_distant_post_stays_ready(db_session, source, fake_embedder):
    first = _post(db_session, source, 1, "кошка гуляет по крыше старого дома")
    DeduplicationService().deduplicate_post(db_session, first)
    db_session.commit()

    second = _post(db_session, source, 2, "собака охраняет двор загородного участка")
    DeduplicationService().deduplicate_post(db_session, second)

    assert second.status == RawPostStatus.READY.value
