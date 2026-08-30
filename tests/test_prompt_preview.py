"""Проверка промпта на странице настроек.

Пользователь правит текст и хочет увидеть результат до того, как испортит им
живую генерацию. Поэтому проверка берёт текст прямо из поля, а не из базы, и
ничего после себя не оставляет.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models import ActionLog, GeneratedPost, RawPost
from app.services.ai_gateway import AiResult


@pytest.fixture
def two_posts(db_session, source):
    posts = [
        RawPost(source_id=source.id, telegram_message_id=1, text_hash="a", original_text="Старая новость"),
        RawPost(source_id=source.id, telegram_message_id=2, text_hash="b", original_text="Свежая новость"),
    ]
    db_session.add_all(posts)
    db_session.commit()
    return posts


def _check(client, csrf, prompt="Пиши коротко.", offset=0):
    return client.post("/settings/prompt-preview", data={
        "csrf_token": csrf(client, "/settings"),
        "prompt": prompt,
        "offset": str(offset),
    })


def _answer(text="Готовый пост", **kwargs):
    defaults = dict(suitable=True, text=text, reason="", model_name="test-model", finish_reason="stop")
    defaults.update(kwargs)
    return AsyncMock(return_value=AiResult(**defaults))


def test_the_button_is_offered_next_to_the_prompt(logged_in):
    page = logged_in.get("/settings").text

    assert 'name="ai_prompt"' in page
    assert "prompt-check" in page
    assert 'name="ai_prompt_template"' not in page, "второе поле промпта убрали"


def test_preview_runs_the_text_from_the_field(logged_in, csrf, two_posts):
    generate = _answer()
    with patch("app.services.ai_gateway.AiGatewayClient.generate_news_post", generate):
        body = _check(logged_in, csrf, prompt="Пиши стихами.").json()

    assert body["ok"] is True
    assert body["text"] == "Готовый пост"
    assert body["model"] == "test-model"
    assert generate.await_args.kwargs["rules"] == "Пиши стихами."


def test_preview_takes_the_freshest_post_and_can_move_to_the_next(logged_in, csrf, two_posts):
    with patch("app.services.ai_gateway.AiGatewayClient.generate_news_post", _answer()):
        first = _check(logged_in, csrf).json()
        second = _check(logged_in, csrf, offset=1).json()

    assert first["post"]["excerpt"] == "Свежая новость"
    assert second["post"]["excerpt"] == "Старая новость"


def test_preview_saves_neither_the_setting_nor_the_post(logged_in, csrf, two_posts, db_session):
    with patch("app.services.ai_gateway.AiGatewayClient.generate_news_post", _answer()):
        _check(logged_in, csrf, prompt="Пиши стихами.")

    from app.services import settings_registry
    assert db_session.scalars(select(GeneratedPost)).all() == []
    assert settings_registry.get("ai_prompt", db_session) != "Пиши стихами."
    assert db_session.scalar(select(ActionLog).where(ActionLog.action == "prompt_preview")) is not None


def test_a_failed_generation_is_reported_as_is(logged_in, csrf, two_posts):
    failure = _answer(suitable=False, text="", reason="Модель упёрлась в лимит ответа", failed=True, finish_reason="length")
    with patch("app.services.ai_gateway.AiGatewayClient.generate_news_post", failure):
        body = _check(logged_in, csrf).json()

    assert body["failed"] is True
    assert "лимит" in body["reason"]
    assert body["finish_reason"] == "length"


def test_preview_says_plainly_when_there_is_nothing_to_check(logged_in, csrf):
    body = _check(logged_in, csrf).json()

    assert body["ok"] is False
    assert "не собрано" in body["error"]
