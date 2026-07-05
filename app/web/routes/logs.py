import urllib.parse

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ActionLog
from app.web.auth import require_auth
from app.web.routes.common import LOGS_PER_PAGE, tpl

router = APIRouter()


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
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    page = max(1, page)
    total_pages = max(1, (total + LOGS_PER_PAGE - 1) // LOGS_PER_PAGE)
    items = db.scalars(query.offset((page - 1) * LOGS_PER_PAGE).limit(LOGS_PER_PAGE)).all()
    all_actions = [row[0] for row in db.execute(select(ActionLog.action).distinct().order_by(ActionLog.action)).all()]
    filters = {"action": action or "", "q": q or ""}
    base_qs = urllib.parse.urlencode({k: v for k, v in filters.items() if v})
    if base_qs:
        base_qs += "&"
    return tpl(request, "logs.html", db, {
        "items": items, "all_actions": all_actions, "filters": filters,
        "page": page, "total_pages": total_pages, "total": total, "base_qs": base_qs,
    })
