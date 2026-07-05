import urllib.parse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ActionLog, TargetChannel
from app.services.telegram_publisher import TelegramPublisherService
from app.web.auth import require_auth
from app.web.routes.common import current_project_id, to_bool, tpl

router = APIRouter()


@router.get("/targets")
def targets(request: Request, db: Session = Depends(get_db), _: bool = Depends(require_auth), ok: str | None = None, error: str | None = None):
    pid = current_project_id(request, db)
    q = select(TargetChannel).order_by(TargetChannel.created_at.desc())
    if pid is not None:
        q = q.where(TargetChannel.project_id == pid)
    items = db.scalars(q).all()
    return tpl(request, "targets.html", db, {"items": items, "ok": ok, "error": error})


@router.post("/targets")
def create_target(
    request: Request,
    title: str = Form(...),
    chat_id: str = Form(...),
    username: str = Form(""),
    enabled: str | None = Form(None),
    auto_publish_enabled: str | None = Form(None),
    default_mode: str = Form("manual"),
    publish_from: str = Form(""),
    publish_to: str = Form(""),
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    pid = current_project_id(request, db)
    db.add(TargetChannel(
        title=title,
        chat_id=chat_id,
        username=username or None,
        enabled=to_bool(enabled),
        auto_publish_enabled=to_bool(auto_publish_enabled),
        default_mode=default_mode,
        publish_from=publish_from or None,
        publish_to=publish_to or None,
        project_id=pid,
    ))
    db.commit()
    return RedirectResponse(url="/targets", status_code=302)


@router.post("/targets/{target_id}/edit")
def edit_target(
    target_id: int,
    title: str = Form(...),
    chat_id: str = Form(...),
    username: str = Form(""),
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    target = db.get(TargetChannel, target_id)
    if target:
        target.title = title
        target.chat_id = chat_id.strip()
        target.username = username.strip().lstrip("@") or None
        db.commit()
    return RedirectResponse(url="/targets?ok=Канал+обновлён", status_code=302)


@router.post("/targets/{target_id}/schedule")
def update_target_schedule(
    target_id: int,
    publish_from: str = Form(""),
    publish_to: str = Form(""),
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    target = db.get(TargetChannel, target_id)
    if target:
        target.publish_from = publish_from or None
        target.publish_to = publish_to or None
        db.add(ActionLog(
            action="target_schedule_update",
            entity_type="TargetChannel",
            entity_id=str(target.id),
            message=f"Расписание «{target.title}»: {publish_from or '—'} – {publish_to or '—'}",
        ))
        db.commit()
    return RedirectResponse(url="/targets", status_code=302)


@router.post("/targets/{target_id}/toggle")
def toggle_target(target_id: int, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    target = db.get(TargetChannel, target_id)
    if target:
        target.enabled = not target.enabled
        db.commit()
    return RedirectResponse(url="/targets", status_code=302)


@router.post("/targets/{target_id}/toggle-mode")
def toggle_target_mode(target_id: int, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    target = db.get(TargetChannel, target_id)
    if target:
        if target.default_mode == "auto":
            target.default_mode = "manual"
            target.auto_publish_enabled = False
        else:
            target.default_mode = "auto"
            target.auto_publish_enabled = True
        db.add(ActionLog(
            action="target_mode_change",
            entity_type="TargetChannel",
            entity_id=str(target.id),
            message=f"Режим канала «{target.title}» изменён на «{target.default_mode}»",
        ))
        db.commit()
    return RedirectResponse(url="/targets", status_code=302)


@router.post("/targets/{target_id}/delete")
def delete_target(target_id: int, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    target = db.get(TargetChannel, target_id)
    if target:
        db.delete(target)
        db.commit()
    return RedirectResponse(url="/targets", status_code=302)


@router.post("/targets/{target_id}/test")
async def test_target(target_id: int, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    target = db.get(TargetChannel, target_id)
    if not target:
        return RedirectResponse(url="/targets?error=Target+not+found", status_code=302)
    publisher = TelegramPublisherService()
    try:
        ok, msg = await publisher.test_target(target.chat_id)
    finally:
        await publisher.close()
    db.add(ActionLog(action="target_test", entity_type="TargetChannel", entity_id=str(target.id), message=f"ok={ok}: {msg}"))
    db.commit()
    if ok:
        return RedirectResponse(url=f"/targets?ok={urllib.parse.quote(f'{target.title}: бот — {msg}')}", status_code=302)
    return RedirectResponse(url=f"/targets?error={urllib.parse.quote(f'{target.title}: {msg}')}", status_code=302)
