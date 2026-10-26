"""Отказ шлюза должен объяснять, что чинить.

Поводом был живой случай: провайдер снял модель, шлюз ответил 410, а панель
показала «Client error '410 Gone' for url …» со ссылкой на справочник кодов —
по ней не прочитать ни про модель, ни про то, куда идти её менять.
"""

import httpx
import pytest

from app.services.ai_gateway import _why_gateway_failed


def _status(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.timeweb.ai/v1/chat/completions")
    return httpx.HTTPStatusError("", request=request, response=httpx.Response(code, request=request))


def test_a_retired_model_is_named_in_the_answer():
    text = _why_gateway_failed(_status(410), "deepseek/deepseek-v4-flash", 60)

    assert "deepseek/deepseek-v4-flash" in text, "без имени модели непонятно, что менять"
    assert "Настройк" in text
    assert "developer.mozilla.org" not in text


def test_a_rejected_key_points_at_the_key():
    assert "ключ" in _why_gateway_failed(_status(401), "любая", 60).lower()


def test_a_limit_is_told_apart_from_a_breakage():
    text = _why_gateway_failed(_status(429), "любая", 60)

    assert "лимит" in text or "токен" in text


def test_the_gateway_side_is_named_for_a_server_error():
    assert "на его стороне" in _why_gateway_failed(_status(502), "любая", 60)


def test_a_timeout_says_how_long_we_waited():
    text = _why_gateway_failed(httpx.ReadTimeout("timed out"), "любая", 45)

    assert "45" in text


def test_an_unreachable_gateway_blames_the_address():
    text = _why_gateway_failed(httpx.ConnectError("nope"), "любая", 60)

    assert "адрес" in text


def test_an_unknown_failure_keeps_its_own_text():
    """Незнакомую поломку не прячем: пусть видно будет, что именно случилось."""
    text = _why_gateway_failed(ValueError("что-то своё"), "любая", 60)

    assert "ValueError" in text and "что-то своё" in text
