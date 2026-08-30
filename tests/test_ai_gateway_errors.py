"""Что панель показывает, когда шлюз ответил, но пользы в ответе нет.

Пустой ответ доходил до экрана как «AI вернул не-JSON ответ:» — и пустота
после двоеточия. Устранять было нечего, потому что причина не называлась.
"""

import asyncio

import pytest

from app.models import AppSetting, RawPost
from app.services.ai_gateway import AiGatewayClient


@pytest.fixture
def configured(db_session):
    db_session.add_all([
        AppSetting(key="timeweb_ai_gateway_base_url", value="https://gateway.test/v1"),
        AppSetting(key="timeweb_ai_gateway_api_key", value="key"),
        AppSetting(key="timeweb_ai_gateway_model", value="some/model"),
        AppSetting(key="ai_max_tokens", value="2400"),
    ])
    db_session.commit()
    return db_session


@pytest.fixture
def post(db_session, source):
    raw = RawPost(source_id=source.id, telegram_message_id=1, text_hash="h", original_text="новость")
    db_session.add(raw)
    db_session.commit()
    db_session.refresh(raw)
    return raw


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def post(self, *_args, **_kwargs):
        return _FakeResponse(self._payload)


def _gateway_answers(monkeypatch, payload):
    monkeypatch.setattr(
        "app.services.ai_gateway.httpx.AsyncClient",
        lambda **_kwargs: _FakeClient(payload),
    )


def _ask(db, post):
    return asyncio.run(AiGatewayClient().generate_news_post(post, db))


def test_truncated_answer_names_the_token_limit(monkeypatch, configured, post):
    _gateway_answers(monkeypatch, {
        "choices": [{"finish_reason": "length", "message": {"content": ""}}],
        "usage": {"completion_tokens": 2400},
    })

    result = _ask(configured, post)

    assert result.failed is True
    assert "ai_max_tokens = 2400" in result.reason


def test_a_model_that_only_thinks_is_recognised(monkeypatch, configured, post):
    """Рассуждающая модель тратит лимит на размышление и до текста не доходит."""
    _gateway_answers(monkeypatch, {
        "choices": [{"finish_reason": "stop", "message": {"content": "", "reasoning_content": "думаю…"}}],
    })

    result = _ask(configured, post)

    assert result.failed is True
    assert "рассуждение" in result.reason
    assert "ai_max_tokens" in result.reason


def test_a_plain_empty_answer_points_at_the_model(monkeypatch, configured, post):
    _gateway_answers(monkeypatch, {"choices": [{"finish_reason": "stop", "message": {"content": "   "}}]})

    result = _ask(configured, post)

    assert result.failed is True
    assert "пустой ответ" in result.reason
    assert "модель" in result.reason.lower()
    assert result.reason.strip().endswith("."), "сообщение не должно обрываться пустотой"


def test_a_filtered_answer_says_so(monkeypatch, configured, post):
    _gateway_answers(monkeypatch, {"choices": [{"finish_reason": "content_filter", "message": {"content": ""}}]})

    assert "фильтр" in _ask(configured, post).reason


def test_garbage_still_shows_what_came_back(monkeypatch, configured, post):
    _gateway_answers(monkeypatch, {
        "choices": [{"finish_reason": "stop", "message": {"content": "извините, я не могу"}}],
    })

    result = _ask(configured, post)

    assert result.failed is True
    assert "извините, я не могу" in result.reason
    assert "finish_reason=stop" in result.reason


def test_a_good_answer_still_works(monkeypatch, configured, post):
    _gateway_answers(monkeypatch, {
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": '{"suitable": true, "text": "Готовый пост", "reason": ""}'},
        }],
    })

    result = _ask(configured, post)

    assert result.failed is False
    assert result.suitable is True
    assert result.text == "Готовый пост"
