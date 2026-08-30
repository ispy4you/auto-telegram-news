"""Страница настроек: анкета, свободный текст и справка о скрытой части промпта."""

import json
import re

from app.models import AppSetting
from app.services import prompt_form, settings_registry


def _save(client, csrf, **fields):
    data = {"csrf_token": csrf(client, "/settings")}
    data.update(fields)
    return client.post("/settings", data=data)


def test_the_page_offers_both_modes(logged_in):
    page = logged_in.get("/settings").text

    assert 'id="prompt-mode"' in page
    assert 'data-prompt-mode="simple"' in page
    assert 'name="pf_tone"' in page
    assert 'name="pf_extra"' in page


def test_the_hidden_part_of_the_prompt_is_shown_read_only(logged_in):
    """Пользователь должен видеть, что уходит в модель помимо его правил."""
    page = logged_in.get("/settings").text

    assert "Что уходит в модель помимо ваших правил" in page
    assert "РИА Новости" in page, "нет примера данных новости"
    assert "suitable" in page, "нет формата ответа"
    assert 'name="auto_appendix"' not in page, "справка не должна отправляться формой"
    assert 'name="response_contract"' not in page


def test_the_questionnaire_writes_the_rules(logged_in, csrf, db_session):
    _save(logged_in, csrf,
          ai_prompt_mode="simple",
          ai_prompt="этот текст не должен сохраниться",
          pf_tone="lively",
          pf_hashtags="few",
          pf_extra="Всегда упоминай город.")

    saved = settings_registry.get("ai_prompt", db_session)
    assert "живо" in saved
    assert "хештега" in saved
    assert saved.endswith("Всегда упоминай город.")
    assert "этот текст не должен сохраниться" not in saved


def test_the_answers_are_remembered(logged_in, csrf, db_session):
    _save(logged_in, csrf, ai_prompt_mode="simple", pf_tone="expert")

    stored = json.loads(settings_registry.get("ai_prompt_form", db_session))
    assert stored["tone"] == "expert"
    assert prompt_form.loads(settings_registry.get("ai_prompt_form", db_session))["tone"] == "expert"

    page = logged_in.get("/settings").text
    chosen = re.search(r'<input[^>]*id="pf-tone-expert"[^>]*>', page, re.S)
    assert chosen and "checked" in chosen.group(0), "форма должна открыться с выбранным ответом"


def test_text_mode_saves_what_the_author_typed(logged_in, csrf, db_session):
    _save(logged_in, csrf, ai_prompt_mode="text", ai_prompt="Пиши стихами.", pf_tone="lively")

    assert settings_registry.get("ai_prompt", db_session) == "Пиши стихами."


def test_someone_who_already_edited_the_prompt_lands_in_text_mode(logged_in, db_session):
    """Иначе анкета молча затрёт чужую работу при первом же сохранении."""
    db_session.add(AppSetting(key="ai_prompt", value="Мои правила."))
    db_session.commit()

    page = logged_in.get("/settings").text

    assert 'id="prompt-mode" value="text"' in page


def test_a_fresh_install_lands_in_the_questionnaire(logged_in):
    assert 'id="prompt-mode" value="simple"' in logged_in.get("/settings").text


def test_resetting_the_prompt_drops_the_questionnaire_too(logged_in, csrf, db_session):
    _save(logged_in, csrf, ai_prompt_mode="simple", pf_tone="expert")

    logged_in.post("/settings/reset", data={
        "csrf_token": csrf(logged_in, "/settings"),
        "key": "ai_prompt",
    })

    assert db_session.get(AppSetting, "ai_prompt") is None
    assert db_session.get(AppSetting, "ai_prompt_form") is None
    assert db_session.get(AppSetting, "ai_prompt_mode") is None
