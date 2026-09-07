"""Скрытая часть промпта показана в редакторе промпта и не редактируется.

Пользователь правит только правила, но в модель уходит ещё два куска: данные
новости и формат ответа. Пока их не видно, непонятно, что вообще происходит.
"""

import pytest

from app.services import ai_prompt


def test_the_example_is_exactly_what_gets_appended():
    """Справка не должна разъехаться с тем, что уходит на самом деле."""
    real = ai_prompt.build_user_message("Пиши коротко.", ai_prompt.SAMPLE_VALUES)

    assert real == "Пиши коротко.\n\n" + ai_prompt.appendix_example()


def test_the_example_shows_every_field_the_model_receives():
    example = ai_prompt.appendix_example()

    assert "Источник: РИА Новости" in example
    assert "Дата исходной публикации: 2026-08-30 09:15" in example
    assert "Есть медиа: да" in example
    assert "Исходный текст:" in example


@pytest.fixture
def editor(logged_in, db_session):
    """Страница редактора промпта: справка живёт рядом с полем правил."""
    from app.models import Prompt

    prompt = Prompt(name="Основной", body="Пиши коротко.", is_default=True)
    db_session.add(prompt)
    db_session.commit()
    return logged_in.get(f"/prompts/{prompt.id}").text


def test_the_page_shows_both_hidden_pieces(editor):
    assert "Что уходит в модель помимо ваших правил" in editor
    assert "РИА Новости" in editor, "нет примера данных новости"
    assert "Отвечай строго одним JSON-объектом" in editor, "нет формата ответа"


def test_the_reference_is_not_an_input(editor):
    """Показать — да, дать испортить — нет: формой это не отправляется."""
    assert 'name="auto_appendix"' not in editor
    assert 'name="response_contract"' not in editor
    assert "<pre" in editor
