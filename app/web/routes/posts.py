import urllib.parse

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.database import get_db
from app.models import ActionLog, GeneratedPost, GeneratedPostStatus, RawPost, RawPostStatus, SourceChannel, TargetChannel
from app.services import media_restore, media_storage, post_generation, post_lifecycle, prompts
from app.services.ai_gateway import GenerationFailed
from app.services.news_pipeline import NewsPipelineService
from app.services.telegram_publisher import TelegramPublisherService
from app.web.auth import require_auth
from app.web.routes.common import POSTS_PER_PAGE, back_to_post, current_project_id, parse_optional_int, tpl

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
        post_lifecycle.delete_posts(db, list(posts_q))
        db.commit()
    elif action == "generate":
        background_tasks.add_task(post_generation.generate_many, ids)
        return RedirectResponse(url="/posts", status_code=302)

    return RedirectResponse(url="/posts", status_code=302)


@router.post("/posts/fetch-now")
async def posts_fetch_now(db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    await NewsPipelineService().run_once(db)
    return RedirectResponse(url="/posts", status_code=302)


@router.get("/posts/{post_id}")
def post_detail(
    post_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
    err: str | None = None,
    media_err: str | None = None,
):
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
    # Файлы могли не пережить перезапуск: отмечаем пропавшие, чтобы страница
    # показала заглушку вместо битой картинки и сама попросила их перекачать.
    missing_media = {item.id for item in media_restore.missing_media(post)}
    # Своё, загруженное руками, перекачивать неоткуда: про такие файлы страница
    # говорит прямо, а не делает вид, что сейчас их вернёт.
    lost_media = {item.id for item in media_restore.lost_media(post)}
    return tpl(request, "post_detail.html", db, {
        "post": post, "targets": targets, "err": err, "media_err": media_err,
        "missing_media": missing_media, "lost_media": lost_media,
        "media_limit": media_storage.MAX_ITEMS_PER_POST,
        "prompts": prompts.all_prompts(db),
        # Предвыбор — промпт канала, в который пост поедет. Выбор остаётся:
        # привязка отвечает «чем обычно», а не «чем только и можно».
        "suggested_prompt": prompts.for_post(db, post),
    })


async def _do_generate(post_id: int, db: Session, prompt_id: int | None = None) -> None:
    post = db.get(RawPost, post_id)
    if not post:
        return
    await _generate_single_post(post, db, prompt_id)


@router.post("/posts/{post_id}/generate")
async def generate_post(
    post_id: int,
    prompt_id: int | None = Form(None),
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    try:
        await post_generation.generate_by_id(post_id, db, prompt_id)
    except GenerationFailed as exc:
        return back_to_post(post_id, str(exc))
    return back_to_post(post_id)


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
async def regenerate_post(
    post_id: int,
    prompt_id: int | None = Form(None),
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    post = db.get(RawPost, post_id)
    if not post:
        return RedirectResponse(url="/posts", status_code=302)

    # Сначала новый текст, потом отказ от старого: иначе сорвавшаяся генерация
    # оставляла пост вообще без черновика.
    try:
        result = await post_generation.ask_model(post, db, prompt_id)
    except GenerationFailed as exc:
        return back_to_post(post_id, str(exc))

    existing_drafts = db.scalars(
        select(GeneratedPost).where(
            GeneratedPost.raw_post_id == post_id,
            GeneratedPost.status == GeneratedPostStatus.DRAFT.value,
        )
    ).all()
    for gp in existing_drafts:
        gp.status = GeneratedPostStatus.REJECTED.value
    db.flush()
    post_generation.save(post, result, db)
    return back_to_post(post_id)


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
        # Причина нужна на странице, а не только в журнале: кнопка «Опубликовать
        # оригинал» иначе выглядит нажатой впустую.
        if not generated.publish_error:
            generated.publish_error = str(exc)[:500]
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
        post_lifecycle.delete_posts(db, [post])
        db.add(ActionLog(action="post_delete", entity_type="RawPost", entity_id=str(post_id), message="Post deleted"))
        db.commit()
    return RedirectResponse(url="/posts", status_code=302)
