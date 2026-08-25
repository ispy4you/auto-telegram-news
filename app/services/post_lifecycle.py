"""Переходы состояний постов.

Статусы двух моделей связаны: публикация двигает и GeneratedPost, и RawPost,
и задачу публикации, а вместе со статусом надо проставить ещё три-четыре поля.
Раньше эти связки выписывались по месту в семи модулях, и любая забытая часть
давала пост, который в списке выглядит одним, а в базе является другим.

Здесь собраны только связанные переходы — те, где меняется больше одного
объекта или больше одного поля. Локальные переходы внутри одного сервиса
(например READY внутри дедупликации) остаются там, где им место.
"""

from datetime import datetime, timezone

from app.models import (
    GeneratedPost,
    GeneratedPostStatus,
    PublishJob,
    PublishJobStatus,
    RawPost,
    RawPostStatus,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def mark_duplicate(post: RawPost, original: RawPost, score: float) -> None:
    """Пост признан повтором: ссылка на оригинал и оценка идут вместе со статусом."""
    post.status = RawPostStatus.DUPLICATE.value
    post.duplicate_of_id = original.id
    post.dedupe_score = score


def mark_generated(post: RawPost) -> None:
    post.status = RawPostStatus.GENERATED.value


def reject(post: RawPost, reason: str | None = None, generated: GeneratedPost | None = None) -> None:
    """Отклонение всегда парное: черновик без своего исходника нигде не показывается."""
    post.status = RawPostStatus.REJECTED.value
    if reason is not None:
        post.ai_suitable = False
        post.ai_skip_reason = reason
    if generated is not None:
        generated.status = GeneratedPostStatus.REJECTED.value


def approve(generated: GeneratedPost) -> None:
    generated.status = GeneratedPostStatus.APPROVED.value


def schedule(generated: GeneratedPost, job: PublishJob, target_channel_id: int, when: datetime) -> None:
    """Публикация отложена до открытия окна канала."""
    generated.status = GeneratedPostStatus.SCHEDULED.value
    generated.target_channel_id = target_channel_id
    job.status = PublishJobStatus.PENDING.value
    job.scheduled_at = when


def mark_published(
    generated: GeneratedPost,
    raw_post: RawPost,
    job: PublishJob,
    target_channel_id: int,
    message_id: int | None,
) -> None:
    generated.status = GeneratedPostStatus.PUBLISHED.value
    generated.telegram_message_id = message_id
    generated.published_at = _utcnow()
    generated.target_channel_id = target_channel_id
    generated.publish_error = None
    raw_post.status = RawPostStatus.PUBLISHED.value
    job.status = PublishJobStatus.SUCCESS.value
    job.sent_message_id = message_id
    job.last_error = None


def mark_publish_failed(generated: GeneratedPost, raw_post: RawPost, job: PublishJob, error: str) -> None:
    generated.status = GeneratedPostStatus.FAILED.value
    generated.publish_error = error
    raw_post.status = RawPostStatus.FAILED.value
    job.status = PublishJobStatus.FAILED.value
    job.last_error = error


def reset_for_retry(generated: GeneratedPost, raw_post: RawPost | None) -> None:
    """Возврат упавшей публикации в рабочее состояние перед повторной попыткой."""
    generated.status = GeneratedPostStatus.APPROVED.value
    generated.publish_error = None
    if raw_post is not None:
        raw_post.status = RawPostStatus.GENERATED.value
