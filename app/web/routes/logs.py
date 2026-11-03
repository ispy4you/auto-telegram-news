import urllib.parse

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ActionLog, AdminLog
from app.web import admin_journal
from app.web.auth import require_auth
from app.web.routes.common import LOGS_PER_PAGE, tpl

router = APIRouter()


def _listing(db: Session, query, page: int, filters: dict) -> dict:
    """Страница журнала: записи, счётчики и хвост ссылки с фильтрами."""
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    page = max(1, page)
    total_pages = max(1, (total + LOGS_PER_PAGE - 1) // LOGS_PER_PAGE)
    items = db.scalars(query.offset((page - 1) * LOGS_PER_PAGE).limit(LOGS_PER_PAGE)).all()
    base_qs = urllib.parse.urlencode({k: v for k, v in filters.items() if v})
    if base_qs:
        base_qs += "&"
    return {
        "items": items, "filters": filters,
        "page": page, "total_pages": total_pages, "total": total, "base_qs": base_qs,
    }


def _known_actions(db: Session, column) -> list[str]:
    return [row[0] for row in db.execute(select(column).distinct().order_by(column)).all()]


def _by_name(actions: list[str]) -> list[str]:
    """В списке фильтра стоят названия, значит и порядок — по названиям."""
    return sorted(actions, key=admin_journal.label)


@router.get("/logs")
def logs(
    request: Request,
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
    action: str | None = None,
    q: str | None = None,
    page: int = 1,
):
    query = select(ActionLog).order_by(ActionLog.created_at.desc())
    if action:
        query = query.where(ActionLog.action == action)
    if q:
        query = query.where(ActionLog.message.ilike(f"%{q}%"))
    return tpl(request, "logs.html", db, {
        "all_actions": _known_actions(db, ActionLog.action),
        **_listing(db, query, page, {"action": action or "", "q": q or ""}),
    })


@router.get("/logs/admin")
def admin_logs(
    request: Request,
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
    action: str | None = None,
    q: str | None = None,
    page: int = 1,
):
    """Действия человека в панели — отдельно от событий конвейера."""
    query = select(AdminLog).order_by(AdminLog.created_at.desc())
    if action:
        query = query.where(AdminLog.action == action)
    if q:
        # Поиск заодно по адресу: «кто это сделал» спрашивают не реже, чем «что».
        like = f"%{q}%"
        query = query.where(or_(AdminLog.message.ilike(like), AdminLog.actor.ilike(like)))
    return tpl(request, "admin_logs.html", db, {
        "all_actions": _by_name(_known_actions(db, AdminLog.action)),
        **_listing(db, query, page, {"action": action or "", "q": q or ""}),
    })
