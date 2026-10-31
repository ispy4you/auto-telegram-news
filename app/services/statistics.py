"""Цифры для страницы статистики.

Считались прямо в роуте — сотня строк запросов между проверкой доступа и
рендером. Здесь на каждый вопрос страницы своя функция, и спросить её можно
из теста, не поднимая страницу целиком.

Все функции принимают project_id: в панели несколько проектов, и цифры
одного не должны складываться с цифрами другого — так считают и дашборд, и
список постов, а эта страница считала всё подряд.
"""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    GeneratedPost,
    GeneratedPostStatus,
    RawPost,
    RawPostStatus,
    SourceChannel,
    TargetChannel,
)

#: Сколько дней показывает график на странице.
DAYS = 14

#: Сколько источников попадает в таблицу.
TOP_SOURCES = 10


def _of_project(query, project_id: int | None):
    """Ограничение «посты этого проекта»: проект у поста — через его источник."""
    if project_id is None:
        return query
    return query.join(SourceChannel, RawPost.source_id == SourceChannel.id).where(
        SourceChannel.project_id == project_id
    )


def _generated_of_project(query, project_id: int | None):
    """То же для готовых постов: до источника от них два шага."""
    if project_id is None:
        return query
    return (
        query.join(RawPost, GeneratedPost.raw_post_id == RawPost.id)
        .join(SourceChannel, RawPost.source_id == SourceChannel.id)
        .where(SourceChannel.project_id == project_id)
    )


def published_per_day(
    db: Session, project_id: int | None, tz_name: str, days: int = DAYS
) -> tuple[list[str], list[int]]:
    """Подписи дней и число публикаций в каждый из них, от старого к новому.

    Сутки считаем по часовому поясу панели. В базе время хранится в UTC, и
    без пересчёта пост, вышедший в Москве в час ночи, попадал в предыдущий
    день — притом что подписи под графиком были уже местные.

    Раскладываем по дням в Python, а не в SQL: границы местных суток каждая
    СУБД считает по-своему, а публикаций за две недели столько, что считать
    их на стороне панели ничего не стоит.
    """
    tz = ZoneInfo(tz_name)
    today = datetime.now(tz).date()
    first = today - timedelta(days=days - 1)
    since = datetime.combine(first, time.min, tz).astimezone(timezone.utc).replace(tzinfo=None)

    rows = db.execute(
        _generated_of_project(
            select(GeneratedPost.published_at).where(
                GeneratedPost.status == GeneratedPostStatus.PUBLISHED.value,
                GeneratedPost.published_at >= since,
            ),
            project_id,
        )
    ).all()

    counts: dict[object, int] = {}
    for (published_at,) in rows:
        if published_at is None:
            continue
        local_day = published_at.replace(tzinfo=timezone.utc).astimezone(tz).date()
        counts[local_day] = counts.get(local_day, 0) + 1

    row = [first + timedelta(days=i) for i in range(days)]
    return [day.strftime("%d.%m") for day in row], [counts.get(day, 0) for day in row]


def top_sources(db: Session, project_id: int | None, limit: int = TOP_SOURCES) -> list[dict]:
    """Источники с наибольшим числом опубликованных постов.

    Рядом — сколько у источника отклонённых и какая доля прошла. Считаем по
    id, а не по названию: два источника могут называться одинаково, и тогда
    их отказы складывались в один.
    """
    query = (
        select(SourceChannel.id, SourceChannel.title, RawPost.status, func.count(RawPost.id))
        .join(RawPost, RawPost.source_id == SourceChannel.id)
        .where(RawPost.status.in_([RawPostStatus.PUBLISHED.value, RawPostStatus.REJECTED.value]))
        .group_by(SourceChannel.id, SourceChannel.title, RawPost.status)
    )
    if project_id is not None:
        query = query.where(SourceChannel.project_id == project_id)

    tally: dict[int, dict] = {}
    for source_id, title, status, count in db.execute(query).all():
        row = tally.setdefault(source_id, {"title": title, "published": 0, "rejected": 0})
        if status == RawPostStatus.PUBLISHED.value:
            row["published"] = count
        else:
            row["rejected"] = count

    rows = sorted(tally.values(), key=lambda row: (-row["published"], row["title"]))[:limit]
    for row in rows:
        total = row["published"] + row["rejected"]
        row["rate"] = round(row["published"] / total * 100) if total else 0
    return rows


def published_per_target(db: Session, project_id: int | None) -> list[dict]:
    """Сколько опубликовано в каждый канал публикации."""
    query = (
        select(TargetChannel.title, func.count(GeneratedPost.id))
        .join(GeneratedPost, GeneratedPost.target_channel_id == TargetChannel.id)
        .where(GeneratedPost.status == GeneratedPostStatus.PUBLISHED.value)
        .group_by(TargetChannel.id, TargetChannel.title)
        .order_by(func.count(GeneratedPost.id).desc())
    )
    if project_id is not None:
        query = query.where(TargetChannel.project_id == project_id)
    return [{"title": title, "count": count} for title, count in db.execute(query).all()]


def funnel(db: Session, project_id: int | None) -> dict:
    """Путь новости: собрали → не повтор → сгенерировали → опубликовали."""
    raw = select(func.count()).select_from(RawPost)
    generated = select(func.count()).select_from(GeneratedPost)
    return {
        "fetched": db.scalar(_of_project(raw, project_id)) or 0,
        "unique": db.scalar(
            _of_project(raw.where(RawPost.status != RawPostStatus.DUPLICATE.value), project_id)
        ) or 0,
        "generated": db.scalar(_generated_of_project(generated, project_id)) or 0,
        "published": db.scalar(
            _generated_of_project(
                generated.where(GeneratedPost.status == GeneratedPostStatus.PUBLISHED.value),
                project_id,
            )
        ) or 0,
    }
