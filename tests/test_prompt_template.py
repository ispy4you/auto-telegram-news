"""Шаблон промпта пишет человек, а не программист: скобки в нём — обычный текст."""

import pytest

from app.services import prompt_template
from app.services.ai_prompt import DEFAULT_AI_USER_PROMPT_TEMPLATE

VALUES = {
    "source_title": "ТАСС",
    "published_at_source": "2026-01-01",
    "original_text": "Текст новости",
    "has_media": "да",
}


@pytest.mark.parametrize("template", [
    'Верни строго JSON: {"suitable": true, "text": ""}',
    "Пиши в стиле {источник",
    "Пункт {0} важен",
    "Формат ответа: { suitable, text, reason }",
])
def test_braces_in_the_prompt_do_not_break_generation(template):
    """Каждая из этих строк роняла генерацию с 500 до отказа от str.format()."""
    assert prompt_template.render(template, VALUES) == template


def test_known_placeholders_are_substituted():
    rendered = prompt_template.render("Источник: {source_title}\n{original_text}", VALUES)
    assert rendered == "Источник: ТАСС\nТекст новости"


def test_unknown_placeholder_stays_as_written():
    assert prompt_template.render("Дай {text}", VALUES) == "Дай {text}"


def test_default_template_renders_with_single_braces():
    rendered = prompt_template.render(DEFAULT_AI_USER_PROMPT_TEMPLATE, VALUES)
    assert '"suitable": true' in rendered
    assert "{{" not in rendered, "удвоенные скобки не нужны и сбивают модель"
    assert "Текст новости" in rendered


def test_problems_names_the_typo_and_the_missing_text():
    issues = " ".join(prompt_template.problems("Напиши пост про {text}"))
    assert "{text}" in issues
    assert "{original_text}" in issues


def test_default_template_has_no_complaints():
    assert prompt_template.problems(DEFAULT_AI_USER_PROMPT_TEMPLATE) == []


def test_legacy_escaping_is_unwound_once():
    assert prompt_template.unescape_legacy('{{"a": 1}}') == '{"a": 1}'
    assert prompt_template.unescape_legacy("{original_text}") == "{original_text}"
