"""Промпт собирается из правил пользователя и данных новости."""

from unittest.mock import patch

from app.services import ai_prompt
from app.services.ai_gateway import AiGatewayClient
from app.services.ai_prompt import DEFAULT_AI_PROMPT, RESPONSE_CONTRACT

VALUES = {
    "source_title": "ТАСС",
    "published_at_source": "2026-01-01",
    "original_text": "Текст новости",
    "has_media": "да",
}


class DummySource:
    title = "Источник"


class DummyPost:
    source = DummySource()
    published_at_source = "2026-01-01"
    original_text = "Новость"
    has_media = False


def test_the_editable_prompt_says_nothing_about_json():
    """Формат ответа — договор с кодом: стереть его из настроек нельзя."""
    assert "JSON" not in DEFAULT_AI_PROMPT
    assert "suitable" not in DEFAULT_AI_PROMPT
    assert '"suitable": true' in RESPONSE_CONTRACT


def test_news_is_appended_when_the_author_wrote_no_placeholders():
    message = ai_prompt.build_user_message("Пиши коротко.", VALUES)

    assert message.startswith("Пиши коротко.")
    assert "Источник: ТАСС" in message
    assert "Дата исходной публикации: 2026-01-01" in message
    assert "Есть медиа: да" in message
    assert "Текст новости" in message


def test_a_placeholder_puts_the_value_in_place_and_not_twice():
    message = ai_prompt.build_user_message("Новость: {original_text}\nПерескажи.", VALUES)

    assert message.count("Текст новости") == 1
    assert "Исходный текст:" not in message
    # Остальные данные пользователь не расставил — они всё равно доедут.
    assert "Источник: ТАСС" in message


def test_build_messages_keeps_the_contract_in_the_system_message():
    with patch("app.services.ai_gateway.get_ai_prompt", return_value=DEFAULT_AI_PROMPT):
        messages = AiGatewayClient()._build_messages(DummyPost())

    assert messages[0]["content"] == RESPONSE_CONTRACT
    assert "Источник: Источник" in messages[1]["content"]
    assert "Новость" in messages[1]["content"]


def test_rules_can_be_overridden_for_a_preview():
    """Проверка промпта в настройках гоняет текст из поля, а не сохранённый."""
    with patch("app.services.ai_gateway.get_ai_prompt", return_value=DEFAULT_AI_PROMPT):
        messages = AiGatewayClient()._build_messages(DummyPost(), rules="Пиши стихами.")

    assert messages[1]["content"].startswith("Пиши стихами.")
