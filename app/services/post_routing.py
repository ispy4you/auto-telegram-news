"""Куда идёт пост: маршруты источника, иначе все включённые каналы.

Правило было внутри автопубликации, а спрашивать его стало нужно и странице
поста: промпт по умолчанию берётся у канала, в который пост поедет. Два ответа
на один вопрос разошлись бы при первой же правке.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RawPost, SourceTargetRoute, TargetChannel


def targets_for(db: Session, raw_post: RawPost) -> list[TargetChannel]:
    """Порядок по id — чтобы «первый канал» значило одно и то же всегда."""
    routed = db.scalars(
        select(TargetChannel)
        .join(SourceTargetRoute, SourceTargetRoute.target_channel_id == TargetChannel.id)
        .where(
            SourceTargetRoute.source_id == raw_post.source_id,
            SourceTargetRoute.enabled.is_(True),
            TargetChannel.enabled.is_(True),
        )
        .order_by(TargetChannel.id)
    ).all()
    if routed:
        return list(routed)
    return list(db.scalars(
        select(TargetChannel).where(TargetChannel.enabled.is_(True)).order_by(TargetChannel.id)
    ).all())
