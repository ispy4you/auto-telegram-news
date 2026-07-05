from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import ActionLog, GeneratedPost, GeneratedPostStatus, RawPost, RawPostStatus, SourceChannel, TargetChannel
from app.services.news_pipeline import NewsPipelineService
from app.web.auth import require_auth
from app.web.routes.common import current_project_id, tpl

router = APIRouter()


@router.get("/")
async def dashboard(request: Request, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    today = date.today()
    week_ago = today - timedelta(days=7)
    pid = current_project_id(request, db)

    def _src_filter(q):
        return q.join(SourceChannel, RawPost.source_id == SourceChannel.id).where(SourceChannel.project_id == pid) if pid is not None else q

    def _gen_filter(q):
        return (q.join(RawPost, GeneratedPost.raw_post_id == RawPost.id)
                  .join(SourceChannel, RawPost.source_id == SourceChannel.id)
                  .where(SourceChannel.project_id == pid)) if pid is not None else q

    src_base = select(func.count()).select_from(SourceChannel)
    if pid is not None:
        src_base = src_base.where(SourceChannel.project_id == pid)

    tgt_base = select(TargetChannel).where(TargetChannel.enabled.is_(True))
    if pid is not None:
        tgt_base = tgt_base.where(TargetChannel.project_id == pid)

    stats = {
        "sources_active": db.scalar(src_base.where(SourceChannel.enabled.is_(True))) or 0,
        "sources_total": db.scalar(src_base) or 0,
        "new_posts": db.scalar(_src_filter(select(func.count()).select_from(RawPost).where(RawPost.status.in_(["new", "ready"])))) or 0,
        "duplicates": db.scalar(_src_filter(select(func.count()).select_from(RawPost).where(RawPost.status == RawPostStatus.DUPLICATE.value))) or 0,
        "drafts": db.scalar(_gen_filter(select(func.count()).select_from(GeneratedPost).where(GeneratedPost.status == GeneratedPostStatus.DRAFT.value))) or 0,
        "published_today": db.scalar(_gen_filter(select(func.count()).select_from(GeneratedPost).where(GeneratedPost.status == GeneratedPostStatus.PUBLISHED.value, func.date(GeneratedPost.published_at) == today))) or 0,
        "published_week": db.scalar(_gen_filter(select(func.count()).select_from(GeneratedPost).where(GeneratedPost.status == GeneratedPostStatus.PUBLISHED.value, func.date(GeneratedPost.published_at) >= week_ago))) or 0,
        "fetched_today": db.scalar(_src_filter(select(func.count()).select_from(RawPost).where(func.date(RawPost.created_at) == today))) or 0,
        "fetched_week": db.scalar(_src_filter(select(func.count()).select_from(RawPost).where(func.date(RawPost.created_at) >= week_ago))) or 0,
        "rejected_total": db.scalar(_src_filter(select(func.count()).select_from(RawPost).where(RawPost.status == RawPostStatus.REJECTED.value))) or 0,
    }
    recent_logs = db.scalars(select(ActionLog).order_by(ActionLog.created_at.desc()).limit(15)).all()
    pending_q = select(RawPost).options(joinedload(RawPost.source)).where(RawPost.status.in_(["new", "ready"])).order_by(RawPost.created_at.desc()).limit(10)
    if pid is not None:
        pending_q = pending_q.join(SourceChannel, RawPost.source_id == SourceChannel.id).where(SourceChannel.project_id == pid)
    pending_posts = db.scalars(pending_q).all()
    targets = db.scalars(tgt_base).all()
    return tpl(request, "dashboard.html", db, {
        "stats": stats,
        "recent_logs": recent_logs,
        "pending_posts": pending_posts,
        "targets": targets,
    })


@router.get("/api/scheduler-status")
async def scheduler_status(request: Request, _: bool = Depends(require_auth)):
    sched = getattr(request.app.state, "scheduler", None)
    if not sched:
        return JSONResponse({"interval_seconds": 120, "is_running": False, "last_run_at": None, "next_run_at": None})
    nxt = sched.next_run_at
    last = sched.last_run_at
    return JSONResponse({
        "interval_seconds": sched.interval_seconds,
        "is_running": sched.is_running,
        "last_run_at": last.isoformat() + "Z" if last else None,
        "next_run_at": nxt.isoformat() if nxt else None,
    })


@router.post("/fetch-now")
async def fetch_now(request: Request, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    sched = getattr(request.app.state, "scheduler", None)
    if sched:
        await sched.trigger_run()
    else:
        await NewsPipelineService().run_once(db)
    return RedirectResponse(url="/", status_code=302)
