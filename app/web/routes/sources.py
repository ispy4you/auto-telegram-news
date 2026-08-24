import urllib.parse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ActionLog, RawPost, RawPostStatus, SourceChannel
from app.services.telegram_reader import TelegramReaderService
from app.services.telegram_reader import TelegramReaderService
from app.web.auth import require_auth
from app.web.routes.common import current_project_id, tpl

router = APIRouter()


@router.get("/sources")
def sources(request: Request, db: Session = Depends(get_db), _: bool = Depends(require_auth), error: str | None = None, ok: str | None = None):
    pid = current_project_id(request, db)
    q = select(SourceChannel).order_by(SourceChannel.created_at.desc())
    if pid is not None:
        q = q.where(SourceChannel.project_id == pid)
    items = db.scalars(q).all()
    source_ids = [s.id for s in items]
    post_totals = {row[0]: row[1] for row in db.execute(
        select(RawPost.source_id, func.count()).where(RawPost.source_id.in_(source_ids)).group_by(RawPost.source_id)
    ).all()} if source_ids else {}
    post_published = {row[0]: row[1] for row in db.execute(
        select(RawPost.source_id, func.count()).where(RawPost.source_id.in_(source_ids), RawPost.status == RawPostStatus.PUBLISHED.value).group_by(RawPost.source_id)
    ).all()} if source_ids else {}
    return tpl(request, "sources.html", db, {
        "items": items, "error": error, "ok": ok,
        "post_totals": post_totals, "post_published": post_published,
    })


@router.post("/sources")
async def create_source(
    request: Request,
    title: str = Form(...),
    username_or_url: str = Form(...),
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    pid = current_project_id(request, db)
    username = TelegramReaderService._extract_username(username_or_url)
    db.add(SourceChannel(title=title, username=username, source_type="telethon", rss_url=None, url=f"https://t.me/{username}", enabled=True, project_id=pid))
    db.commit()
    listener = getattr(request.app.state, "event_listener", None)
    if listener:
        await listener.reload_sources()
    return RedirectResponse(url="/sources", status_code=302)


@router.post("/sources/{source_id}/edit")
async def edit_source(
    source_id: int,
    request: Request,
    title: str = Form(...),
    username_or_url: str = Form(...),
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    source = db.get(SourceChannel, source_id)
    if source:
        new_username = TelegramReaderService._extract_username(username_or_url)
        if new_username != source.username:
            source.last_message_id = None  # сброс при смене канала
        source.title = title
        source.username = new_username
        source.url = f"https://t.me/{new_username}"
        db.commit()
        listener = getattr(request.app.state, "event_listener", None)
        if listener:
            await listener.reload_sources()
    return RedirectResponse(url="/sources?ok=Источник+обновлён", status_code=302)


@router.post("/sources/{source_id}/toggle")
async def toggle_source(source_id: int, request: Request, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    source = db.get(SourceChannel, source_id)
    if source:
        source.enabled = not source.enabled
        db.commit()
    listener = getattr(request.app.state, "event_listener", None)
    if listener:
        await listener.reload_sources()
    return RedirectResponse(url="/sources", status_code=302)


@router.post("/sources/{source_id}/delete")
async def delete_source(source_id: int, request: Request, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    source = db.get(SourceChannel, source_id)
    if source:
        db.delete(source)
        db.commit()
    listener = getattr(request.app.state, "event_listener", None)
    if listener:
        await listener.reload_sources()
    return RedirectResponse(url="/sources", status_code=302)


@router.post("/sources/{source_id}/fetch")
async def fetch_source(source_id: int, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    source = db.get(SourceChannel, source_id)
    if not source:
        return RedirectResponse(url="/sources?error=Source+not+found", status_code=302)
    try:
        count = await TelegramReaderService().fetch_source(db, source)
        return RedirectResponse(url=f"/sources?ok={urllib.parse.quote(f'Получено {count} новых постов из {source.title}')}", status_code=302)
    except Exception as exc:
        db.add(ActionLog(action="fetch_error", entity_type="SourceChannel", entity_id=str(source.id), message=str(exc)))
        db.commit()
        return RedirectResponse(url=f"/sources?error={urllib.parse.quote(str(exc))}", status_code=302)
