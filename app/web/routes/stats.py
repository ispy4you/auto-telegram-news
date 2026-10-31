from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import statistics
from app.services.prompt_settings import get_display_timezone
from app.web.auth import require_auth
from app.web.routes.common import current_project_id, tpl

router = APIRouter()


@router.get("/stats")
def stats_page(request: Request, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    pid = current_project_id(request, db)
    labels, counts = statistics.published_per_day(db, pid, get_display_timezone(db))
    return tpl(request, "stats.html", db, {
        "daily_labels": labels,
        "daily_counts": counts,
        "top_sources": statistics.top_sources(db, pid),
        "target_stats": statistics.published_per_target(db, pid),
        "funnel": statistics.funnel(db, pid),
    })
