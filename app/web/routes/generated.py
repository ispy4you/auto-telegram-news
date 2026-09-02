import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import ActionLog, GeneratedPost, GeneratedPostStatus, RawPost, SourceChannel, TargetChannel
from app.services import message_entities, post_lifecycle
from app.services.telegram_publisher import TelegramPublisherService
from app.web.auth import require_auth
from app.web.routes.common import GENERATED_PER_PAGE, current_project_id, tpl

logger = logging.getLogger(__name__)

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
def save_generated(
    generated_id: int,
    edited_text: str = Form(""),
    entities: str = Form(""),
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    generated = db.get(GeneratedPost, generated_id)
    if generated:
        # Текст и разметка нормализуются вместе: обрезка по краям сдвигает
        # смещения, а список пришёл из браузера и доверия ему нет.
        text, marks = message_entities.normalize(edited_text, message_entities.loads(entities))
        generated.edited_text = text
        generated.entities = message_entities.dumps(marks)
        post_lifecycle.approve(generated)
        db.commit()
        return RedirectResponse(url=f"/posts/{generated.raw_post_id}", status_code=302)
    return RedirectResponse(url="/generated", status_code=302)


def _record_publish_failure(db: Session, generated_id: int, exc: Exception) -> None:
    """Причина отказа должна остаться на посте, а не только в логах.

    Обычно её пишет сам публикатор. Но часть отказов случается раньше первой
    строчки в базу — например, когда не задан токен бота, — и тогда записать
    её больше некому.
    """
    # Сорвавшаяся отправка могла оставить сессию в незакрытой транзакции —
    # тогда любой следующий запрос к ней упал бы. Публикатор всё, что успел
    # записать, уже зафиксировал, так что терять здесь нечего.
    db.rollback()
    generated = db.get(GeneratedPost, generated_id)
    if generated is None or generated.publish_error:
        return
    reason = str(exc)[:500]
    generated.publish_error = reason
    db.add(ActionLog(
        action="publish_failed",
        entity_type="GeneratedPost",
        entity_id=str(generated_id),
        message=reason,
    ))
    db.commit()


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
    except Exception as exc:
        # Отказ Telegram — обычное дело: не тот формат картинки, бот не админ
        # в канале, канал удалён. Показывать вместо панели страницу 500 незачем,
        # но и молча возвращать на пост нельзя: тогда кнопка выглядит нажатой
        # впустую.
        logger.warning("Публикация поста %s сорвалась: %s", generated_id, exc)
        _record_publish_failure(db, generated_id, exc)
    finally:
        await publisher.close()
    return RedirectResponse(url=f"/posts/{raw_post_id}", status_code=302)


@router.post("/generated/{generated_id}/reject")
def reject_generated(generated_id: int, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    generated = db.get(GeneratedPost, generated_id)
    if not generated:
        return RedirectResponse(url="/posts", status_code=302)
    raw_post_id = generated.raw_post_id
    if generated.raw_post:
        post_lifecycle.reject(generated.raw_post, generated=generated)
    else:
        generated.status = GeneratedPostStatus.REJECTED.value
    db.commit()
    return RedirectResponse(url=f"/posts/{raw_post_id}", status_code=302)
