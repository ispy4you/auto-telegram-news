from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import GeneratedPost, GeneratedPostStatus, RawPost, RawPostStatus, SourceChannel, TargetChannel
from app.services.telegram_publisher import TelegramPublisherService
from app.web.auth import require_auth
from app.web.routes.common import GENERATED_PER_PAGE, current_project_id, tpl

router = APIRouter()


@router.get("/generated")
def generated_posts(request: Request, db: Session = Depends(get_db), _: bool = Depends(require_auth), page: int = 1):
    pid = current_project_id(request, db)
    page = max(1, page)
    gen_q = select(GeneratedPost).options(joinedload(GeneratedPost.raw_post)).order_by(GeneratedPost.created_at.desc())
    if pid is not None:
        gen_q = (gen_q
            .join(RawPost, GeneratedPost.raw_post_id == RawPost.id)
            .join(SourceChannel, RawPost.source_id == SourceChannel.id)
            .where(SourceChannel.project_id == pid))
    total = db.scalar(select(func.count()).select_from(gen_q.subquery())) or 0
    total_pages = max(1, (total + GENERATED_PER_PAGE - 1) // GENERATED_PER_PAGE)
    items = db.scalars(gen_q.offset((page - 1) * GENERATED_PER_PAGE).limit(GENERATED_PER_PAGE)).all()
    tgt_q = select(TargetChannel).where(TargetChannel.enabled.is_(True))
    if pid is not None:
        tgt_q = tgt_q.where(TargetChannel.project_id == pid)
    targets = db.scalars(tgt_q).all()
    return tpl(request, "generated_posts.html", db, {"items": items, "targets": targets, "page": page, "total_pages": total_pages, "total": total})


@router.post("/generated/{generated_id}/save")
def save_generated(generated_id: int, edited_text: str = Form(""), db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    generated = db.get(GeneratedPost, generated_id)
    if generated:
        generated.edited_text = edited_text
        generated.status = GeneratedPostStatus.APPROVED.value
        db.commit()
        return RedirectResponse(url=f"/posts/{generated.raw_post_id}", status_code=302)
    return RedirectResponse(url="/generated", status_code=302)


@router.post("/generated/{generated_id}/publish")
async def publish_generated(
    generated_id: int,
    target_channel_id: int = Form(...),
    include_media: str | None = Form(None),
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    generated = db.get(GeneratedPost, generated_id)
    if not generated:
        return RedirectResponse(url="/generated", status_code=302)
    raw_post_id = generated.raw_post_id
    publisher = TelegramPublisherService()
    try:
        await publisher.publish_generated_post(
            db, generated_id, target_channel_id,
            include_media=include_media == "on",
        )
    finally:
        await publisher.close()
    return RedirectResponse(url=f"/posts/{raw_post_id}", status_code=302)


@router.post("/generated/{generated_id}/reject")
def reject_generated(generated_id: int, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    generated = db.get(GeneratedPost, generated_id)
    if not generated:
        return RedirectResponse(url="/posts", status_code=302)
    raw_post_id = generated.raw_post_id
    generated.status = GeneratedPostStatus.REJECTED.value
    if generated.raw_post:
        generated.raw_post.status = RawPostStatus.REJECTED.value
    db.commit()
    return RedirectResponse(url=f"/posts/{raw_post_id}", status_code=302)
