from unittest.mock import patch

from app.services.ai_gateway import AiGatewayClient
from app.services.ai_prompt import DEFAULT_AI_SYSTEM_PROMPT, DEFAULT_AI_USER_PROMPT_TEMPLATE


class DummySource:
    title = "Источник"


class DummyPost:
    source = DummySource()
    published_at_source = "2026-01-01"
    original_text = "Новость"
    has_media = False


def test_default_prompt_contains_json_instruction():
    assert "Верни строго JSON" in DEFAULT_AI_USER_PROMPT_TEMPLATE
    assert "без эмодзи" in DEFAULT_AI_SYSTEM_PROMPT


def test_build_messages_uses_default_template():
    client = AiGatewayClient()
    with (
        patch("app.services.ai_gateway.get_ai_system_prompt", return_value=DEFAULT_AI_SYSTEM_PROMPT),
        patch("app.services.ai_gateway.get_ai_user_prompt_template", return_value=DEFAULT_AI_USER_PROMPT_TEMPLATE),
    ):
        msgs = client._build_messages(DummyPost())
    assert "Верни строго JSON" in msgs[1]["content"]
    assert "Источник: Источник" in msgs[1]["content"]
