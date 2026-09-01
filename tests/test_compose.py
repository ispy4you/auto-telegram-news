"""Вкладка «Генерация»: текст вставляют руками, дальше — обычный пост.

Проверяем три вещи: удачная генерация заводит настоящий пост с черновиком,
любая осечка возвращает набранный текст обратно в поле и ничего не создаёт,
а служебный источник не притворяется каналом.
"""

from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.models import ActionLog, GeneratedPost, GeneratedPostStatus, RawPost, RawPostStatus, SourceChannel
from app.services.deduplication import DeduplicationService
from app.services import manual_post
from app.services.ai_gateway import AiResult

TEXT = "В городе открыли новый мост через реку, движение запустили сегодня утром."


def _answer(**kwargs):
    defaults = dict(suitable=True, text="Готовый пост", reason="", model_name="test-model", finish_reason="stop")
    defaults.update(kwargs)
    return AsyncMock(return_value=AiResult(**defaults))


def _compose(client, csrf, text=TEXT):
    return client.post("/compose", data={"csrf_token": csrf(client, "/compose"), "source_text": text})


def test_the_tab_offers_a_field_and_says_where_the_rules_live(logged_in):
    page = logged_in.get("/compose").text

    assert 'name="source_text"' in page
    assert 'href="/settings"' in page


def test_generated_text_becomes_a_post_with_a_draft(logged_in, csrf, db_session):
    with patch("app.services.ai_gateway.AiGatewayClient.generate", _answer()):
        response = _compose(logged_in, csrf)

    post = db_session.scalar(select(RawPost))
    draft = db_session.scalar(select(GeneratedPost))
    assert response.status_code == 302
    assert response.headers["location"] == f"/posts/{post.id}"
    assert post.original_text == TEXT
    assert draft.generated_text == "Готовый пост"
    assert draft.model_name == "test-model"
    assert draft.status == GeneratedPostStatus.DRAFT.value


def test_a_manual_post_skips_dedup_and_autopublish(logged_in, csrf, db_session):
    """Оба этапа работают по статусам NEW и READY — ручной пост в них не попадает."""
    with patch("app.services.ai_gateway.AiGatewayClient.generate", _answer()):
        _compose(logged_in, csrf)

    post = db_session.scalar(select(RawPost))
    assert post.status == RawPostStatus.GENERATED.value


def test_the_model_gets_the_pasted_text(logged_in, csrf):
    generate = _answer()
    with patch("app.services.ai_gateway.AiGatewayClient.generate", generate):
        _compose(logged_in, csrf)

    values = generate.await_args.args[0]
    assert values["original_text"] == TEXT
    assert values["has_media"] == "нет"
    assert values["source_title"] == manual_post.SOURCE_TITLE


def test_a_refusal_keeps_the_text_and_creates_nothing(logged_in, csrf, db_session):
    refusal = _answer(suitable=False, text="", reason="Это реклама, а не новость")
    with patch("app.services.ai_gateway.AiGatewayClient.generate", refusal):
        page = _compose(logged_in, csrf).text

    assert "Это реклама, а не новость" in page
    assert TEXT in page, "набранный руками текст терять нельзя"
    assert db_session.scalars(select(RawPost)).all() == []


def test_a_broken_gateway_keeps_the_text_and_creates_nothing(logged_in, csrf, db_session):
    failure = _answer(suitable=False, text="", reason="AI gateway недоступен", failed=True)
    with patch("app.services.ai_gateway.AiGatewayClient.generate", failure):
        page = _compose(logged_in, csrf).text

    assert "AI gateway недоступен" in page
    assert TEXT in page
    assert db_session.scalars(select(RawPost)).all() == []
    assert db_session.scalar(select(ActionLog).where(ActionLog.action == "ai_error")) is not None


def test_too_short_a_text_never_reaches_the_model(logged_in, csrf, db_session):
    generate = _answer()
    with patch("app.services.ai_gateway.AiGatewayClient.generate", generate):
        page = _compose(logged_in, csrf, text="Коротко").text

    generate.assert_not_awaited()
    assert "Слишком короткий текст" in page
    assert db_session.scalars(select(RawPost)).all() == []


def test_the_second_manual_post_does_not_collide_with_the_first(logged_in, csrf, db_session):
    with patch("app.services.ai_gateway.AiGatewayClient.generate", _answer()):
        _compose(logged_in, csrf)
        _compose(logged_in, csrf, text=TEXT + " Подробности уточняются.")

    posts = db_session.scalars(select(RawPost)).all()
    sources = db_session.scalars(select(SourceChannel).where(SourceChannel.source_type == manual_post.SOURCE_TYPE)).all()
    assert len({p.telegram_message_id for p in posts}) == 2
    assert len(sources) == 1, "источник ручного ввода заводится один раз на проект"


def test_the_hidden_source_is_not_offered_as_a_channel(logged_in, csrf, db_session):
    with patch("app.services.ai_gateway.AiGatewayClient.generate", _answer()):
        _compose(logged_in, csrf)

    assert manual_post.SOURCE_TITLE not in logged_in.get("/sources").text
    assert manual_post.SOURCE_TITLE not in logged_in.get("/routes").text


def test_the_hidden_source_is_not_counted_as_a_channel(logged_in, csrf):
    """На дашборде счётчик каналов не должен расти от служебного источника."""
    with patch("app.services.ai_gateway.AiGatewayClient.generate", _answer()):
        _compose(logged_in, csrf)

    assert '>0<span class="text-muted fs-5">/0</span>' in logged_in.get("/").text


def test_a_whole_article_never_reaches_the_model(logged_in, csrf, db_session):
    """В поле мог улететь целый файл — платить за такой запрос незачем."""
    generate = _answer()
    with patch("app.services.ai_gateway.AiGatewayClient.generate", generate):
        page = _compose(logged_in, csrf, text="а" * 20001).text

    generate.assert_not_awaited()
    assert "Слишком длинный текст" in page
    assert db_session.scalars(select(RawPost)).all() == []


def test_the_same_news_from_a_channel_is_recognised_as_a_duplicate(logged_in, csrf, db_session, source):
    """Ручной пост виден дедупликации как образец, иначе новость уйдёт дважды."""
    with patch("app.services.ai_gateway.AiGatewayClient.generate", _answer()):
        _compose(logged_in, csrf)
    manual = db_session.scalar(select(RawPost))

    arrived = RawPost(source_id=source.id, telegram_message_id=777, text_hash="", original_text=TEXT)
    db_session.add(arrived)
    db_session.flush()
    DeduplicationService().deduplicate_post(db_session, arrived)

    assert arrived.status == RawPostStatus.DUPLICATE.value
    assert arrived.duplicate_of_id == manual.id
