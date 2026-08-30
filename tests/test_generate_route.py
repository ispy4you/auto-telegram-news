"""Ручная генерация со страницы поста.

Раньше сломанный шаблон промпта давал 500, а недоступный шлюз навсегда
отклонял новость. И то и другое — технические сбои: пост должен остаться
нетронутым, а причина — оказаться на экране.
"""

import pytest
from sqlalchemy import select

from app.models import ActionLog, GeneratedPost, RawPost, RawPostStatus
from app.services.ai_gateway import AiResult


@pytest.fixture
def raw_post(db_session, source):
    post = RawPost(
        source_id=source.id,
        telegram_message_id=1,
        text_hash="hash",
        original_text="новость",
        status=RawPostStatus.NEW.value,
    )
    db_session.add(post)
    db_session.commit()
    db_session.refresh(post)
    return post


def _ai_returns(monkeypatch, result: AiResult) -> None:
    async def _generate(_self, _post, _db=None):
        return result

    monkeypatch.setattr("app.services.ai_gateway.AiGatewayClient.generate_news_post", _generate)


def test_gateway_failure_leaves_the_post_alone(logged_in, csrf, db_session, raw_post, monkeypatch):
    _ai_returns(monkeypatch, AiResult(False, "", "шлюз недоступен", "m", failed=True))

    response = logged_in.post(
        f"/posts/{raw_post.id}/generate",
        data={"csrf_token": csrf(logged_in, f"/posts/{raw_post.id}")},
    )

    assert response.status_code == 302
    assert "err=" in response.headers["location"]
    assert raw_post.status == RawPostStatus.NEW.value
    assert db_session.scalars(select(GeneratedPost)).all() == []
    assert "ai_error" in db_session.scalars(select(ActionLog.action)).all()


def test_the_reason_is_shown_on_the_post_page(logged_in, raw_post):
    page = logged_in.get(f"/posts/{raw_post.id}?err=шлюз+недоступен")

    assert "шлюз недоступен" in page.text


def test_editorial_refusal_still_rejects_the_post(logged_in, csrf, db_session, raw_post, monkeypatch):
    """Отказ редактора — не сбой: такую новость мы действительно отклоняем."""
    _ai_returns(monkeypatch, AiResult(False, "", "реклама", "m"))

    logged_in.post(
        f"/posts/{raw_post.id}/generate",
        data={"csrf_token": csrf(logged_in, f"/posts/{raw_post.id}")},
    )

    assert raw_post.status == RawPostStatus.REJECTED.value
    assert raw_post.ai_skip_reason == "реклама"


def test_failed_regeneration_keeps_the_existing_draft(logged_in, csrf, db_session, raw_post, monkeypatch):
    draft = GeneratedPost(raw_post_id=raw_post.id, generated_text="черновик", model_name="m")
    db_session.add(draft)
    db_session.commit()

    _ai_returns(monkeypatch, AiResult(False, "", "шлюз недоступен", "m", failed=True))
    logged_in.post(
        f"/posts/{raw_post.id}/regenerate",
        data={"csrf_token": csrf(logged_in, f"/posts/{raw_post.id}")},
    )

    db_session.refresh(draft)
    assert draft.status == "draft", "неудачная перегенерация не должна съедать черновик"
