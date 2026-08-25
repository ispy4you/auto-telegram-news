import urllib.parse

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.database import SessionLocal, get_db
from app.models import ActionLog, GeneratedPost, GeneratedPostStatus, RawPost, RawPostStatus, SourceChannel, TargetChannel
from app.services import post_lifecycle
from app.services.ai_gateway import AiGatewayClient
from app.services.news_pipeline import NewsPipelineService
from app.services.telegram_publisher import TelegramPublisherService
from app.web.auth import require_auth
from app.web.routes.common import POSTS_PER_PAGE, current_project_id, parse_optional_int, tpl

router = APIRouter()


@router.get("/posts")
def posts(
    request: Request,
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
    status: str | None = None,
    source_id: str | None = None,
    q: str | None = None,
    sort: str | None = None,
    page: int = 1,
):
    pid = current_project_id(request, db)
    sort_map = {
        "oldest": RawPost.created_at.asc(),
        "source": RawPost.source_id.asc(),
        "status": RawPost.status.asc(),
    }
    order = sort_map.get(sort, RawPost.created_at.desc())
    query = select(RawPost).options(joinedload(RawPost.source)).order_by(order)
    if pid is not None:
        query = query.join(SourceChannel, RawPost.source_id == SourceChannel.id).where(SourceChannel.project_id == pid)
    parsed_source_id = parse_optional_int(source_id)
    if status:
        query = query.where(RawPost.status == status)
    if parsed_source_id is not None:
        query = query.where(RawPost.source_id == parsed_source_id)
    search_limit = None
    if q:
        where_clause = or_(RawPost.original_text.ilike(f"%{q}%"), RawPost.normalized_text.ilike(f"%{q}%"))
        query = query.where(where_clause)
        search_limit = 2000

    total = db.scalar(select(func.count()).select_from(query.subquery()))
    page = max(1, page)
    offset = (page - 1) * POSTS_PER_PAGE
    data_query = query.limit(search_limit) if search_limit else query
    items = db.scalars(data_query.offset(offset).limit(POSTS_PER_PAGE)).all()

    total_pages = max(1, (total + POSTS_PER_PAGE - 1) // POSTS_PER_PAGE)
    src_q = select(SourceChannel).order_by(SourceChannel.title)
    tgt_q = select(TargetChannel).where(TargetChannel.enabled.is_(True))
    if pid is not None:
        src_q = src_q.where(SourceChannel.project_id == pid)
        tgt_q = tgt_q.where(TargetChannel.project_id == pid)
    sources = db.scalars(src_q).all()
    targets = db.scalars(tgt_q).all()
    filters = {"q": q or "", "source_id": source_id or "", "status": status or "", "sort": sort or ""}
    base_qs = urllib.parse.urlencode({k: v for k, v in filters.items() if v})
    return tpl(request, "posts.html", db, {
        "items": items, "sources": sources, "targets": targets, "filters": filters,
        "page": page, "total_pages": total_pages, "total": total, "base_qs": base_qs,
    })


async def _generate_single_post(post: RawPost, db: Session) -> None:
    result = await AiGatewayClient().generate_news_post(post, db)

    if result.suitable and not result.text.strip():
        result = result.__class__(
            suitable=False,
            text="",
            reason="AI вернул пустой текст — возможно, обрезан лимитом токенов или промпт не дал результата",
            model_name=result.model_name,
        )

    db.add(GeneratedPost(
        raw_post_id=post.id,
        generated_text=result.text,
        model_name=result.model_name,
        status=GeneratedPostStatus.DRAFT.value if result.suitable else GeneratedPostStatus.REJECTED.value,
        generation_error=result.reason if not result.suitable else None,
    ))
    if result.suitable:
        post_lifecycle.mark_generated(post)
    else:
        post_lifecycle.reject(post)
    post.ai_suitable = result.suitable
    post.ai_skip_reason = result.reason if not result.suitable else None
    db.commit()


async def _bulk_generate_task(ids: list[int]) -> None:
    with SessionLocal() as db:
        posts_q = db.scalars(select(RawPost).where(RawPost.id.in_(ids))).all()
        for p in posts_q:
            try:
                await _generate_single_post(p, db)
            except Exception as exc:
                db.add(ActionLog(action="bulk_generate_error", entity_type="RawPost", entity_id=str(p.id), message=str(exc)))
                db.commit()


@router.post("/posts/bulk")
async def posts_bulk(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    form = await request.form()
    action = form.get("bulk_action", "")
    ids = [int(v) for k, v in form.multi_items() if k == "post_ids"]
    if not ids:
        return RedirectResponse(url="/posts", status_code=302)

    posts_q = db.scalars(select(RawPost).where(RawPost.id.in_(ids))).all()

    if action == "reject":
        for p in posts_q:
            p.status = RawPostStatus.REJECTED.value
        db.commit()
    elif action == "delete":
        for p in posts_q:
            db.delete(p)
        db.commit()
    elif action == "generate":
        background_tasks.add_task(_bulk_generate_task, ids)
        return RedirectResponse(url="/posts", status_code=302)

    return RedirectResponse(url="/posts", status_code=302)


@router.post("/posts/fetch-now")
async def posts_fetch_now(db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    await NewsPipelineService().run_once(db)
    return RedirectResponse(url="/posts", status_code=302)


@router.get("/posts/{post_id}")
def post_detail(post_id: int, request: Request, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    post = db.scalar(
        select(RawPost)
        .options(joinedload(RawPost.source), selectinload(RawPost.media_items), selectinload(RawPost.generated_posts))
        .where(RawPost.id == post_id)
    )
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    pid = current_project_id(request, db)
    tgt_q = select(TargetChannel).where(TargetChannel.enabled.is_(True))
    if pid is not None:
        tgt_q = tgt_q.where(TargetChannel.project_id == pid)
    targets = db.scalars(tgt_q).all()
    return tpl(request, "post_detail.html", db, {"post": post, "targets": targets})


async def _do_generate(post_id: int, db: Session) -> None:
    post = db.get(RawPost, post_id)
    if not post:
        return
    await _generate_single_post(post, db)


@router.post("/posts/{post_id}/generate")
async def generate_post(post_id: int, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    await _do_generate(post_id, db)
    return RedirectResponse(url=f"/posts/{post_id}", status_code=302)


@router.post("/posts/{post_id}/edit-draft")
def edit_draft(post_id: int, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    post = db.get(RawPost, post_id)
    if not post:
        return RedirectResponse(url="/posts", status_code=302)
    gp = GeneratedPost(
        raw_post_id=post.id,
        generated_text=(post.original_text or "").strip(),
        model_name="manual",
        status=GeneratedPostStatus.DRAFT.value,
    )
    db.add(gp)
    post.status = RawPostStatus.GENERATED.value
    db.commit()
    return RedirectResponse(url=f"/posts/{post_id}", status_code=302)


@router.post("/posts/{post_id}/regenerate")
async def regenerate_post(post_id: int, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    existing_drafts = db.scalars(
        select(GeneratedPost).where(
            GeneratedPost.raw_post_id == post_id,
            GeneratedPost.status == GeneratedPostStatus.DRAFT.value,
        )
    ).all()
    for gp in existing_drafts:
        gp.status = GeneratedPostStatus.REJECTED.value
    db.flush()
    await _do_generate(post_id, db)
    return RedirectResponse(url=f"/posts/{post_id}", status_code=302)


@router.post("/posts/{post_id}/publish-raw")
async def publish_raw_post(
    post_id: int,
    target_channel_id: int = Form(...),
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    post = db.scalar(select(RawPost).options(selectinload(RawPost.media_items)).where(RawPost.id == post_id))
    if not post:
        return RedirectResponse(url="/posts", status_code=302)

    text = (post.original_text or "").strip()
    if not text and not post.has_media:
        return RedirectResponse(url=f"/posts/{post_id}", status_code=302)

    generated = GeneratedPost(raw_post_id=post.id, generated_text=text, model_name="raw", status=GeneratedPostStatus.APPROVED.value)
    db.add(generated)
    post.status = RawPostStatus.GENERATED.value
    db.flush()

    publisher = TelegramPublisherService()
    try:
        await publisher.publish_generated_post(db, generated.id, target_channel_id)
    except Exception as exc:
        db.add(ActionLog(action="publish_raw_error", entity_type="RawPost", entity_id=str(post_id), message=str(exc)))
        db.commit()
    finally:
        await publisher.close()

    return RedirectResponse(url=f"/posts/{post_id}", status_code=302)


@router.post("/posts/{post_id}/reject")
def reject_post(post_id: int, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    post = db.get(RawPost, post_id)
    if post:
        post.status = RawPostStatus.REJECTED.value
        db.commit()
    return RedirectResponse(url="/posts", status_code=302)


@router.post("/posts/{post_id}/delete")
def delete_post(post_id: int, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    post = db.get(RawPost, post_id)
    if post:
        db.delete(post)
        db.add(ActionLog(action="post_delete", entity_type="RawPost", entity_id=str(post_id), message="Post deleted"))
        db.commit()
    return RedirectResponse(url="/posts", status_code=302)
