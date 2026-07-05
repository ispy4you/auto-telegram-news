import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import GeneratedPost, GeneratedPostStatus, Project, RawPost, SourceChannel
from app.services.prompt_settings import get_display_timezone

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="app/web/templates")

POSTS_PER_PAGE = 50
GENERATED_PER_PAGE = 50
LOGS_PER_PAGE = 100


def _localdt_filter(dt, tz_name: str, fmt: str = "%d.%m.%Y %H:%M") -> str:
    if dt is None:
        return "—"
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, Exception):
        tz = ZoneInfo("Europe/Moscow")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).strftime(fmt)


templates.env.filters["localdt"] = _localdt_filter


def current_project_id(request: Request, db: Session) -> int | None:
    pid = request.session.get("current_project_id")
    if pid and db.get(Project, pid):
        return pid
    p = db.scalars(select(Project).order_by(Project.id).limit(1)).first()
    if p:
        request.session["current_project_id"] = p.id
        return p.id
    return None


def nav_counts(db: Session, project_id: int | None = None) -> dict:
    new_q = select(func.count()).select_from(RawPost).where(RawPost.status.in_(["new", "ready"]))
    draft_q = select(func.count()).select_from(GeneratedPost).where(GeneratedPost.status == GeneratedPostStatus.DRAFT.value)
    if project_id is not None:
        new_q = new_q.join(SourceChannel, RawPost.source_id == SourceChannel.id).where(SourceChannel.project_id == project_id)
        draft_q = (draft_q
            .join(RawPost, GeneratedPost.raw_post_id == RawPost.id)
            .join(SourceChannel, RawPost.source_id == SourceChannel.id)
            .where(SourceChannel.project_id == project_id))
    return {
        "nav_new": db.scalar(new_q) or 0,
        "nav_drafts": db.scalar(draft_q) or 0,
        "display_tz": get_display_timezone(db),
    }


def tpl(request: Request, name: str, db: Session, ctx: dict = None):
    project_id = current_project_id(request, db)
    current_project = db.get(Project, project_id) if project_id else None
    all_projects = db.scalars(select(Project).order_by(Project.name)).all()
    return templates.TemplateResponse(request, name, {
        **(ctx or {}),
        **nav_counts(db, project_id),
        "csrf_token": request.session.get("csrf_token", ""),
        "current_project": current_project,
        "all_projects": all_projects,
    })


def to_bool(value: str | None) -> bool:
    return value == "on"


def parse_optional_int(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
