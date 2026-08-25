import asyncio

import pytest
from sqlalchemy import select

from app.models import ActionLog, AppSetting, GeneratedPost, RawPost, RawPostStatus, TargetChannel
from app.services.ai_gateway import AiResult
from app.services.news_pipeline import NewsPipelineService


@pytest.fixture
def ready_post(db_session, source):
    post = RawPost(
        source_id=source.id,
        telegram_message_id=1,
        text_hash="hash",
        original_text="новость",
        status=RawPostStatus.READY.value,
    )
    db_session.add(post)
    db_session.add(TargetChannel(
        title="Target", chat_id="@target", enabled=True,
        auto_publish_enabled=True, default_mode="auto",
    ))
    db_session.add(AppSetting(key="global_auto_publish_enabled", value="true"))
    db_session.commit()
    db_session.refresh(post)
    return post


def _pipeline_with_ai(result: AiResult) -> NewsPipelineService:
    pipeline = NewsPipelineService()

    async def _generate(_post, _db=None):
        return result

    pipeline.ai_client.generate_news_post = _generate
    return pipeline


def test_ai_failure_keeps_the_post_for_the_next_run(db_session, ready_post):
    pipeline = _pipeline_with_ai(AiResult(False, "", "шлюз недоступен", "m", failed=True))

    asyncio.run(pipeline.run_autopublish(db_session))

    assert ready_post.status == RawPostStatus.READY.value, "техническая ошибка не должна отклонять новость"
    actions = db_session.scalars(select(ActionLog.action)).all()
    assert "ai_error" in actions


def test_unsuitable_post_is_rejected(db_session, ready_post):
    pipeline = _pipeline_with_ai(AiResult(False, "", "реклама", "m"))

    asyncio.run(pipeline.run_autopublish(db_session))

    assert ready_post.status == RawPostStatus.REJECTED.value
    assert ready_post.ai_skip_reason == "реклама"
    assert db_session.scalars(select(GeneratedPost)).all() == []
