import urllib.parse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ActionLog, SourceChannel, SourceTargetRoute, TargetChannel
from app.services import manual_post
from app.web.auth import require_auth
from app.web.routes.common import current_project_id, tpl

router = APIRouter()


@router.get("/routes")
def routes_page(request: Request, db: Session = Depends(get_db), _: bool = Depends(require_auth), ok: str | None = None):
    pid = current_project_id(request, db)
    # Маршруты «источник → канал» ручному источнику не нужны: такой пост
    # публикуют руками, автоподбор целей до него не доходит.
    src_q = (select(SourceChannel)
        .where(SourceChannel.source_type != manual_post.SOURCE_TYPE)
        .order_by(SourceChannel.title))
    tgt_q = select(TargetChannel).order_by(TargetChannel.title)
    if pid is not None:
        src_q = src_q.where(SourceChannel.project_id == pid)
        tgt_q = tgt_q.where(TargetChannel.project_id == pid)
    sources = db.scalars(src_q).all()
    targets = db.scalars(tgt_q).all()
    existing_routes = db.scalars(select(SourceTargetRoute)).all()
    routed = {(r.source_id, r.target_channel_id) for r in existing_routes}
    return tpl(request, "routes.html", db, {
        "sources": sources, "targets": targets, "routed": routed, "ok": ok,
    })


@router.post("/routes/{target_id}/sync")
async def sync_routes(
    target_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    target = db.get(TargetChannel, target_id)
    if not target:
        return RedirectResponse(url="/routes", status_code=302)

    form = await request.form()
    selected_ids = {int(v) for k, v in form.multi_items() if k == "source_ids"}

    existing = {
        r.source_id: r
        for r in db.scalars(select(SourceTargetRoute).where(SourceTargetRoute.target_channel_id == target_id)).all()
    }

    added, removed = [], []
    for source_id in selected_ids:
        if source_id not in existing:
            db.add(SourceTargetRoute(source_id=source_id, target_channel_id=target_id, enabled=True))
            src = db.get(SourceChannel, source_id)
            added.append(src.title if src else str(source_id))

    for source_id, route in existing.items():
        if source_id not in selected_ids:
            db.delete(route)
            src = db.get(SourceChannel, source_id)
            removed.append(src.title if src else str(source_id))

    if added or removed:
        parts = []
        if added:
            parts.append(f"добавлено: {', '.join(added)}")
        if removed:
            parts.append(f"удалено: {', '.join(removed)}")
        db.add(ActionLog(
            action="routes_sync",
            entity_type="TargetChannel",
            entity_id=str(target_id),
            message=f"Маршруты «{target.title}»: {'; '.join(parts)}",
        ))

    db.commit()
    return RedirectResponse(url=f"/routes?ok={urllib.parse.quote(f'Маршруты канала «{target.title}» сохранены')}", status_code=302)
