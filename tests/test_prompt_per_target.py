"""Промпт по умолчанию у канала назначения.

Выбирать промпт руками при каждой генерации быстро надоедает, а автопубликации
выбирать негде — она всегда брала основной. Теперь его можно закрепить за
каналом.

Название поля — «промпт по умолчанию для постов, идущих в этот канал» — не
кокетство: пост генерируется один раз на все каналы маршрута, поэтому при двух
разных промптах сработает первый по порядку. Тесты это фиксируют, чтобы
поведение не выглядело случайным.
"""

import asyncio
import re
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models import (
    GeneratedPost, Prompt, RawPost, RawPostStatus, SourceTargetRoute, TargetChannel,
)
from app.services import post_routing, prompts
from app.services.ai_gateway import AiResult
from app.services.news_pipeline import NewsPipelineService


@pytest.fixture
def prompt_pair(db_session):
    main = Prompt(name="Основной", body="Пиши сухо.", is_default=True)
    health = Prompt(name="Здоровье", body="Пиши мягко.", is_default=False)
    db_session.add_all([main, health])
    db_session.commit()
    return main, health


@pytest.fixture
def target(db_session):
    channel = TargetChannel(
        title="Канал", chat_id="-100500", enabled=True,
        auto_publish_enabled=True, default_mode="auto",
    )
    db_session.add(channel)
    db_session.commit()
    return channel


@pytest.fixture
def ready_post(db_session, source):
    post = RawPost(
        source_id=source.id,
        telegram_message_id=1,
        original_text="Исходная новость",
        normalized_text="исходная новость",
        text_hash="hash",
        status=RawPostStatus.READY.value,
    )
    db_session.add(post)
    db_session.commit()
    db_session.refresh(post)
    return post


# ---------------------------------------------------------------------------
# Какой промпт выбирается
# ---------------------------------------------------------------------------

def test_a_channel_without_a_prompt_asks_for_nothing(db_session, target, prompt_pair):
    assert prompts.for_targets(db_session, [target]) is None


def test_a_channel_with_a_prompt_names_it(db_session, target, prompt_pair):
    _, health = prompt_pair
    target.prompt_id = health.id
    db_session.commit()

    assert prompts.for_targets(db_session, [target]).name == "Здоровье"


def test_the_first_channel_with_a_prompt_wins(db_session, prompt_pair):
    """Заявленное правило, а не случайность: пост генерируется один раз."""
    main, health = prompt_pair
    first = TargetChannel(title="Первый", chat_id="-1", enabled=True)
    second = TargetChannel(title="Второй", chat_id="-2", enabled=True, prompt_id=health.id)
    db_session.add_all([first, second])
    db_session.commit()

    assert prompts.for_targets(db_session, [first, second]).name == "Здоровье"

    first.prompt_id = main.id
    db_session.commit()
    assert prompts.for_targets(db_session, [first, second]).name == "Основной"


def test_a_deleted_prompt_does_not_break_the_choice(db_session, target, prompt_pair):
    """Ссылка могла остаться от промпта, которого уже нет."""
    target.prompt_id = 9999
    db_session.commit()

    assert prompts.for_targets(db_session, [target]) is None


# ---------------------------------------------------------------------------
# Маршрут поста
# ---------------------------------------------------------------------------

def test_routed_channels_win_over_all_enabled(db_session, source, ready_post, prompt_pair):
    _, health = prompt_pair
    routed = TargetChannel(title="По маршруту", chat_id="-1", enabled=True, prompt_id=health.id)
    other = TargetChannel(title="Просто включённый", chat_id="-2", enabled=True)
    db_session.add_all([routed, other])
    db_session.flush()
    db_session.add(SourceTargetRoute(source_id=source.id, target_channel_id=routed.id, enabled=True))
    db_session.commit()

    assert [t.title for t in post_routing.targets_for(db_session, ready_post)] == ["По маршруту"]
    assert prompts.for_post(db_session, ready_post).name == "Здоровье"


def test_without_routes_every_enabled_channel_counts(db_session, ready_post, target, prompt_pair):
    _, health = prompt_pair
    target.prompt_id = health.id
    db_session.commit()

    assert prompts.for_post(db_session, ready_post).name == "Здоровье"


# ---------------------------------------------------------------------------
# Автопубликация
# ---------------------------------------------------------------------------

def _autopublish(db, generate):
    """Прогон автопубликации с включённым глобальным флагом.

    Подменяем только этот ключ: остальные настройки должны читаться настоящими,
    иначе тест начнёт проверять заглушку вместо пайплайна.
    """
    from app.services import settings_registry

    real_get = settings_registry.get

    def _get(key, session=None):
        return True if key == "global_auto_publish_enabled" else real_get(key, session)

    pipeline = NewsPipelineService()
    with patch("app.services.ai_gateway.AiGatewayClient.generate_news_post", generate), \
         patch("app.services.telegram_publisher.TelegramPublisherService.publish_generated_post", AsyncMock()), \
         patch("app.services.settings_registry.get", _get):
        asyncio.run(pipeline.run_autopublish(db))


def test_autopublish_uses_the_channel_prompt(db_session, ready_post, target, prompt_pair):
    """Раньше здесь всегда работал основной — выбирать было негде."""
    _, health = prompt_pair
    target.prompt_id = health.id
    db_session.commit()

    generate = AsyncMock(return_value=AiResult(True, "Готовый пост", "", "test-model"))
    _autopublish(db_session, generate)

    assert generate.await_args.kwargs["rules"] == "Пиши мягко."
    assert db_session.scalars(select(GeneratedPost)).all() != []


def test_autopublish_without_a_channel_prompt_leaves_the_choice_to_the_gateway(
    db_session, ready_post, target, prompt_pair,
):
    generate = AsyncMock(return_value=AiResult(True, "Готовый пост", "", "test-model"))
    _autopublish(db_session, generate)

    assert generate.await_args.kwargs["rules"] is None


# ---------------------------------------------------------------------------
# Страница канала и удаление промпта
# ---------------------------------------------------------------------------

def test_the_channel_page_offers_the_binding(logged_in, target, prompt_pair):
    page = logged_in.get("/targets").text

    assert 'action="/targets/{}/prompt"'.format(target.id) in page
    assert "Здоровье" in page


def test_binding_a_prompt_to_a_channel(logged_in, csrf, db_session, target, prompt_pair):
    _, health = prompt_pair

    logged_in.post(f"/targets/{target.id}/prompt", data={
        "csrf_token": csrf(logged_in, "/targets"),
        "prompt_id": str(health.id),
    })

    db_session.expire_all()
    assert db_session.get(TargetChannel, target.id).prompt_id == health.id


def test_unbinding_returns_the_channel_to_the_default(logged_in, csrf, db_session, target, prompt_pair):
    _, health = prompt_pair
    target.prompt_id = health.id
    db_session.commit()

    logged_in.post(f"/targets/{target.id}/prompt", data={
        "csrf_token": csrf(logged_in, "/targets"),
        "prompt_id": "",
    })

    db_session.expire_all()
    assert db_session.get(TargetChannel, target.id).prompt_id is None


def test_deleting_a_bound_prompt_frees_the_channel(logged_in, csrf, db_session, target, prompt_pair):
    """Иначе в интерфейсе канала осталась бы ссылка в никуда."""
    _, health = prompt_pair
    target.prompt_id = health.id
    db_session.commit()

    logged_in.post(f"/prompts/{health.id}/delete", data={"csrf_token": csrf(logged_in, "/prompts")})

    db_session.expire_all()
    assert db_session.scalars(select(Prompt)).all() != []
    assert db_session.get(TargetChannel, target.id).prompt_id is None


def test_the_post_page_preselects_the_channel_prompt(logged_in, db_session, ready_post, target, prompt_pair):
    """Привязка отвечает «чем обычно», а не «чем только и можно»."""
    _, health = prompt_pair
    target.prompt_id = health.id
    db_session.commit()

    page = logged_in.get(f"/posts/{ready_post.id}").text
    options = re.findall(r'<option value="(\d+)"\s*(selected)?[^>]*>([^<]*)', page)
    selected = [name.strip() for value, flag, name in options if flag]

    assert selected == ["Здоровье"], f"предвыбран не тот промпт: {options}"
    assert "Основной" in page, "выбор остаётся доступным"
