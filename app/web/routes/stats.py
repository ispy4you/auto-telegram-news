from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import GeneratedPost, GeneratedPostStatus, RawPost, RawPostStatus, SourceChannel, TargetChannel
from app.services.prompt_settings import get_display_timezone
from app.web.auth import require_auth
from app.web.routes.common import tpl

router = APIRouter()


@router.get("/stats")
def stats_page(request: Request, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    tz_name = get_display_timezone(db)
    tz = ZoneInfo(tz_name)
    now_local = datetime.now(tz)
    today_local = now_local.date()

    # Published per day (last 14 days) — list of (date_str, count)
    cutoff_14 = (today_local - timedelta(days=13))
    cutoff_14_utc = datetime.combine(cutoff_14, datetime.min.time())
    rows_daily = db.execute(
        select(
            func.date(GeneratedPost.published_at),
            func.count().label("cnt"),
        )
        .where(
            GeneratedPost.status == GeneratedPostStatus.PUBLISHED.value,
            GeneratedPost.published_at >= cutoff_14_utc,
        )
        .group_by(func.date(GeneratedPost.published_at))
        .order_by(func.date(GeneratedPost.published_at))
    ).all()
    daily_map = {r[0]: r[1] for r in rows_daily}
    daily_labels = [(cutoff_14 + timedelta(days=i)).isoformat() for i in range(14)]
    daily_counts = [daily_map.get(d, 0) for d in daily_labels]
    # Short labels: DD.MM
    daily_labels_short = [(cutoff_14 + timedelta(days=i)).strftime("%d.%m") for i in range(14)]

    # Top sources by published count
    rows_sources = db.execute(
        select(
            SourceChannel.title,
            func.count(RawPost.id).label("published"),
        )
        .join(RawPost, RawPost.source_id == SourceChannel.id)
        .where(RawPost.status == RawPostStatus.PUBLISHED.value)
        .group_by(SourceChannel.id, SourceChannel.title)
        .order_by(func.count(RawPost.id).desc())
        .limit(10)
    ).all()

    # Source accept rate: published / (published + rejected) per source
    rows_total = db.execute(
        select(
            SourceChannel.title,
            RawPost.status,
            func.count(RawPost.id).label("cnt"),
        )
        .join(RawPost, RawPost.source_id == SourceChannel.id)
        .where(RawPost.status.in_([RawPostStatus.PUBLISHED.value, RawPostStatus.REJECTED.value]))
        .group_by(SourceChannel.id, SourceChannel.title, RawPost.status)
    ).all()
    src_stats: dict[str, dict] = {}
    for title, status, cnt in rows_total:
        if title not in src_stats:
            src_stats[title] = {"published": 0, "rejected": 0}
        src_stats[title][status] = cnt

    top_sources = []
    for title, pub_count in rows_sources:
        rej = src_stats.get(title, {}).get("rejected", 0)
        total = pub_count + rej
        rate = round(pub_count / total * 100) if total else 0
        top_sources.append({"title": title, "published": pub_count, "rejected": rej, "rate": rate})

    # Published per target channel
    rows_targets = db.execute(
        select(
            TargetChannel.title,
            func.count(GeneratedPost.id).label("cnt"),
        )
        .join(GeneratedPost, GeneratedPost.target_channel_id == TargetChannel.id)
        .where(GeneratedPost.status == GeneratedPostStatus.PUBLISHED.value)
        .group_by(TargetChannel.id, TargetChannel.title)
        .order_by(func.count(GeneratedPost.id).desc())
    ).all()

    # Overall funnel
    total_fetched = db.scalar(select(func.count()).select_from(RawPost)) or 0
    total_unique = db.scalar(
        select(func.count()).select_from(RawPost)
        .where(RawPost.status != RawPostStatus.DUPLICATE.value)
    ) or 0
    total_generated = db.scalar(
        select(func.count()).select_from(GeneratedPost)
    ) or 0
    total_published = db.scalar(
        select(func.count()).select_from(GeneratedPost)
        .where(GeneratedPost.status == GeneratedPostStatus.PUBLISHED.value)
    ) or 0

    return tpl(request, "stats.html", db, {
        "daily_labels": daily_labels_short,
        "daily_counts": daily_counts,
        "top_sources": top_sources,
        "target_stats": [{"title": r[0], "count": r[1]} for r in rows_targets],
        "funnel": {
            "fetched": total_fetched,
            "unique": total_unique,
            "generated": total_generated,
            "published": total_published,
        },
    })
