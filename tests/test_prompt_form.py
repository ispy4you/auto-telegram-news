"""Анкета собирает правила для AI из переключателей."""

import json

import pytest

from app.services import prompt_form


def test_defaults_compose_a_complete_prompt():
    text = prompt_form.compose(prompt_form.defaults())

    assert text.startswith(prompt_form.INTRO)
    assert text.endswith(prompt_form.OUTRO)
    for question in prompt_form.QUESTIONS:
        assert question.options[0].line in text


def test_every_answer_puts_its_own_line_in():
    text = prompt_form.compose({"tone": "lively", "hashtags": "few", "emoji": "some"})

    assert "живо" in text
    assert "хештега" in text
    assert "эмодзи, не больше" in text
    assert "Не добавляй хештеги." not in text, "вариант по умолчанию должен уступить место"


def test_special_wishes_go_last():
    text = prompt_form.compose({**prompt_form.defaults(), "extra": "  Всегда упоминай город.  "})

    assert text.endswith("Всегда упоминай город.")


def test_unknown_answers_fall_back_to_defaults():
    answers = prompt_form.normalize({"tone": "хулиганский", "неизвестный": "да"})

    assert answers["tone"] == prompt_form.BY_KEY["tone"].default
    assert "неизвестный" not in answers


def test_broken_json_in_the_database_does_not_break_the_page():
    assert prompt_form.loads("не json") == prompt_form.defaults()
    assert prompt_form.loads(None) == prompt_form.defaults()
    assert prompt_form.loads("[1, 2, 3]") == prompt_form.defaults()


def test_wishes_are_capped():
    answers = prompt_form.normalize({"extra": "я" * (prompt_form.MAX_EXTRA + 500)})

    assert len(answers["extra"]) == prompt_form.MAX_EXTRA


def test_stored_answers_survive_a_round_trip():
    answers = prompt_form.normalize({"tone": "expert", "extra": "Без сокращений."})

    assert prompt_form.loads(prompt_form.dumps(answers)) == answers
    assert json.loads(prompt_form.dumps(answers))["tone"] == "expert"


@pytest.mark.parametrize("question", prompt_form.QUESTIONS, ids=lambda q: q.key)
def test_the_browser_gets_the_same_lines(question):
    """Предпросмотр в форме склеивает эти же строки — расходиться им нельзя."""
    served = prompt_form.lines_for_browser()[question.key]

    assert served == {option.value: option.line for option in question.options}
