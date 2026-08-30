"""Правила для AI пишет человек, а не программист: скобки в них — обычный текст."""

import pytest

from app.services import prompt_template
from app.services.ai_prompt import DEFAULT_AI_PROMPT

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


def test_used_lists_only_placeholders_the_author_wrote():
    assert prompt_template.used("Источник: {source_title}, {text}") == {"source_title"}
    assert prompt_template.used(DEFAULT_AI_PROMPT) == set()


def test_problems_names_the_typo_and_the_placeholders_that_exist():
    issues = " ".join(prompt_template.problems("Напиши пост про {text}"))
    assert "{text}" in issues
    assert "{original_text}" in issues, "подсказка должна перечислять доступные имена"


def test_prompt_without_placeholders_has_no_complaints():
    """Данные новости приклеиваются сами, поэтому плейсхолдеры необязательны."""
    assert prompt_template.problems(DEFAULT_AI_PROMPT) == []
    assert prompt_template.problems("Пиши коротко и по делу.") == []


def test_empty_prompt_is_reported():
    assert prompt_template.problems("   ") != []


def test_legacy_escaping_is_unwound_once():
    assert prompt_template.unescape_legacy('{{"a": 1}}') == '{"a": 1}'
    assert prompt_template.unescape_legacy("{original_text}") == "{original_text}"
