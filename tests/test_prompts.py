"""Промптов стало несколько.

Правила генерации были одной настройкой на всю панель. Каналы пишут по-разному,
и один текст на всех заставлял править настройку перед генерацией «не как
обычно», а потом возвращать обратно.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models import Prompt, RawPost
from app.services import prompts
from app.services.ai_gateway import AiResult
from app.services.ai_prompt import DEFAULT_AI_PROMPT


@pytest.fixture
def two_prompts(db_session):
    main = Prompt(name="Основной", body="Пиши сухо.", is_default=True)
    health = Prompt(name="Здоровье", body="Пиши мягко и без диагнозов.", is_default=False)
    db_session.add_all([main, health])
    db_session.commit()
    return main, health


# ---------------------------------------------------------------------------
# Какие правила уходят в модель
# ---------------------------------------------------------------------------

def test_without_a_choice_the_default_prompt_is_used(db_session, two_prompts):
    main, _ = two_prompts
    assert prompts.rules_for(db_session) == main.body


def test_a_chosen_prompt_wins(db_session, two_prompts):
    _, health = two_prompts
    assert prompts.rules_for(db_session, health.id) == health.body


def test_a_missing_prompt_falls_back_to_the_default(db_session, two_prompts):
    """Промпт могли удалить, пока страница была открыта."""
    main, _ = two_prompts
    assert prompts.rules_for(db_session, 9999) == main.body


def test_an_empty_table_still_generates(db_session):
    """Пока миграция не отработала, генерация всё равно должна работать."""
    assert prompts.rules_for(db_session) == DEFAULT_AI_PROMPT


def test_a_list_without_a_default_still_answers(db_session):
    """Пометку могли снять руками в базе — берём самый старый."""
    db_session.add_all([
        Prompt(name="Первый", body="Первый текст", is_default=False),
        Prompt(name="Второй", body="Второй текст", is_default=False),
    ])
    db_session.commit()

    assert prompts.rules_for(db_session) == "Первый текст"


def test_only_one_prompt_stays_default(db_session, two_prompts):
    main, health = two_prompts
    prompts.set_default(db_session, health)
    db_session.commit()

    assert [p.name for p in db_session.scalars(select(Prompt).where(Prompt.is_default.is_(True))).all()] == ["Здоровье"]
    assert main.is_default is False


# ---------------------------------------------------------------------------
# Страница промптов
# ---------------------------------------------------------------------------

def test_the_list_shows_which_prompt_is_the_default(logged_in, two_prompts):
    page = logged_in.get("/prompts").text

    assert "Основной" in page and "Здоровье" in page
    assert "основной" in page


def test_a_new_prompt_starts_from_the_current_rules(logged_in, csrf, db_session, two_prompts):
    """Чистый лист — плохая отправная точка: нужен тот же тон с отличиями."""
    logged_in.post("/prompts", data={"csrf_token": csrf(logged_in, "/prompts"), "name": "Спорт"})

    created = db_session.scalar(select(Prompt).where(Prompt.name == "Спорт"))
    assert created is not None
    assert created.body == "Пиши сухо."
    assert created.is_default is False


def test_saving_reports_a_typo_in_a_placeholder(logged_in, csrf, db_session, two_prompts):
    main, _ = two_prompts
    response = logged_in.post(f"/prompts/{main.id}", data={
        "csrf_token": csrf(logged_in, f"/prompts/{main.id}"),
        "name": "Основной",
        "body": "Напиши пост про {text}",
    })

    assert "warn=" in response.headers["location"]
    db_session.refresh(main)
    assert main.body == "Напиши пост про {text}", "текст всё равно сохраняется"


def test_a_json_example_in_the_prompt_is_not_a_typo(logged_in, csrf, db_session, two_prompts):
    """Фигурные скобки — обычный текст, а не повод ругаться."""
    main, _ = two_prompts
    body = 'Новость: {original_text}\nВерни JSON: {"suitable": true}'
    response = logged_in.post(f"/prompts/{main.id}", data={
        "csrf_token": csrf(logged_in, f"/prompts/{main.id}"),
        "name": "Основной",
        "body": body,
    })

    assert "warn=" not in response.headers["location"]


def test_the_last_prompt_cannot_be_deleted(logged_in, csrf, db_session):
    only = Prompt(name="Единственный", body="Текст", is_default=True)
    db_session.add(only)
    db_session.commit()

    logged_in.post(f"/prompts/{only.id}/delete", data={"csrf_token": csrf(logged_in, "/prompts")})

    assert db_session.scalars(select(Prompt)).all() == [only]


def test_deleting_the_default_promotes_another(logged_in, csrf, db_session, two_prompts):
    """Без основного генерация молча свалилась бы на константу из кода."""
    main, health = two_prompts

    logged_in.post(f"/prompts/{main.id}/delete", data={"csrf_token": csrf(logged_in, "/prompts")})

    db_session.expire_all()
    left = db_session.scalars(select(Prompt)).all()
    assert [p.name for p in left] == ["Здоровье"]
    assert left[0].is_default is True


def test_making_another_prompt_the_default(logged_in, csrf, db_session, two_prompts):
    main, health = two_prompts

    logged_in.post(f"/prompts/{health.id}/default", data={"csrf_token": csrf(logged_in, "/prompts")})

    db_session.expire_all()
    assert db_session.get(Prompt, health.id).is_default is True
    assert db_session.get(Prompt, main.id).is_default is False


# ---------------------------------------------------------------------------
# Выбор доезжает до модели
# ---------------------------------------------------------------------------

@pytest.fixture
def ready_post(db_session, source):
    post = RawPost(
        source_id=source.id,
        telegram_message_id=1,
        original_text="Исходная новость",
        normalized_text="исходная новость",
        text_hash="hash",
        status="ready",
    )
    db_session.add(post)
    db_session.commit()
    db_session.refresh(post)
    return post


def _generation(text="Готовый пост"):
    return AsyncMock(return_value=AiResult(
        suitable=True, text=text, reason="", model_name="test-model",
    ))


def test_the_chosen_prompt_reaches_the_model(logged_in, csrf, db_session, two_prompts, ready_post):
    _, health = two_prompts
    generate = _generation()

    with patch("app.services.ai_gateway.AiGatewayClient.generate_news_post", generate):
        logged_in.post(f"/posts/{ready_post.id}/generate", data={
            "csrf_token": csrf(logged_in, f"/posts/{ready_post.id}"),
            "prompt_id": str(health.id),
        })

    assert generate.await_args.kwargs["rules"] == "Пиши мягко и без диагнозов."


def test_without_a_choice_the_gateway_picks_the_default_itself(logged_in, csrf, db_session, two_prompts, ready_post):
    """Пустой выбор не подменяется текстом здесь: иначе «не выбрали» стало бы
    двумя разными состояниями, которые ведут себя одинаково только на вид."""
    generate = _generation()

    with patch("app.services.ai_gateway.AiGatewayClient.generate_news_post", generate):
        logged_in.post(f"/posts/{ready_post.id}/generate", data={
            "csrf_token": csrf(logged_in, f"/posts/{ready_post.id}"),
        })

    assert generate.await_args.kwargs["rules"] is None


def test_the_compose_tab_offers_the_same_choice(logged_in, csrf, two_prompts):
    page = logged_in.get("/compose").text

    assert 'name="prompt_id"' in page
    assert "Здоровье" in page
