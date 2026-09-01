"""Пост, который редактор написал сам, а не принёс робот.

Всё, что панель умеет дальше — редактор с разметкой, предпросмотр, медиа,
публикация в несколько каналов, отложка, логи — работает с `RawPost` из
канала. Чтобы вставленный руками текст получил то же самое даром, он въезжает
в те же таблицы: на проект заводится скрытый источник «Ручной ввод», и пост
ложится под него обычной строкой.

Такой пост сразу рождается в статусе GENERATED — минуя NEW и READY, на
которых работают дедупликация и автопубликация. Сравнивать вставленный текст
не с чем, а отправлять его в канал без ведома автора тем более не надо.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import GeneratedPost, GeneratedPostStatus, RawPost, RawPostStatus, SourceChannel

#: Тип скрытого источника. По нему ручной источник отличается от настоящих
#: каналов: его не читают из Telegram и не показывают в списке источников.
SOURCE_TYPE = "manual"
SOURCE_TITLE = "Ручной ввод"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def prompt_values(text: str, published_at: datetime | None = None) -> dict[str, str]:
    """Данные новости для промпта — те же поля, что у поста из канала."""
    return {
        "source_title": SOURCE_TITLE,
        "published_at_source": (published_at or _utcnow()).strftime("%Y-%m-%d %H:%M"),
        "original_text": text,
        "has_media": "нет",
    }


def get_or_create_source(db: Session, project_id: int | None) -> SourceChannel:
    """Один скрытый источник на проект. Выключен: читать из него нечего."""
    source = db.scalar(
        select(SourceChannel).where(
            SourceChannel.source_type == SOURCE_TYPE,
            SourceChannel.project_id == project_id,
        )
    )
    if source is None:
        source = SourceChannel(
            title=SOURCE_TITLE,
            username=None,
            source_type=SOURCE_TYPE,
            url="",
            enabled=False,
            project_id=project_id,
        )
        db.add(source)
        db.flush()
    return source


def create(
    db: Session,
    project_id: int | None,
    original_text: str,
    generated_text: str,
    model_name: str,
) -> RawPost:
    """Заводит ручной пост вместе с его черновиком и возвращает исходник."""
    source = get_or_create_source(db, project_id)
    published_at = _utcnow()
    post = RawPost(
        source_id=source.id,
        # У ручного поста нет сообщения в Telegram, но колонка обязательная и
        # уникальная в паре с источником. Отрицательные номера с настоящими
        # не пересекаются и сразу видно, что номер выдуман.
        telegram_message_id=_next_message_id(db, source.id),
        original_text=original_text,
        normalized_text="",
        text_hash="",
        published_at_source=published_at,
        has_media=False,
        media_count=0,
        status=RawPostStatus.GENERATED.value,
        ai_suitable=True,
    )
    db.add(post)
    db.flush()
    db.add(GeneratedPost(
        raw_post_id=post.id,
        generated_text=generated_text,
        model_name=model_name,
        status=GeneratedPostStatus.DRAFT.value,
    ))
    return post


def _next_message_id(db: Session, source_id: int) -> int:
    lowest = db.scalar(
        select(func.min(RawPost.telegram_message_id)).where(RawPost.source_id == source_id)
    )
    return min(lowest or 0, 0) - 1
