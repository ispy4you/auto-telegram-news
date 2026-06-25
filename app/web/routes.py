import logging
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.config import get_settings
from app.database import SessionLocal, get_db
from app.models import ActionLog, AppSetting, GeneratedPost, GeneratedPostStatus, MediaItem, RawPost, RawPostStatus, SourceChannel, TargetChannel
from app.services.ai_gateway import AiGatewayClient
from app.services.prompt_settings import ensure_default_prompt_settings, get_ai_system_prompt, get_ai_user_prompt_template, get_display_timezone
from app.services.news_pipeline import NewsPipelineService
from app.services.source_reader import SourceReaderService
from app.services.telegram_publisher import TelegramPublisherService
from app.services.telegram_reader import TelegramReaderService
from app.web.auth import login_action, require_auth

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")

_POSTS_PER_PAGE = 50
_GENERATED_PER_PAGE = 50
_LOGS_PER_PAGE = 100


def _localdt_filter(dt, tz_name: str, fmt: str = "%d.%m.%Y %H:%M") -> str:
    if dt is None:
        return "—"
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, Exception):
        tz = ZoneInfo("Europe/Moscow")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).strftime(fmt)


templates.env.filters["localdt"] = _localdt_filter


def _nav_counts(db: Session) -> dict:
    return {
        "nav_new": db.scalar(select(func.count()).select_from(RawPost).where(RawPost.status.in_(["new", "ready"]))) or 0,
        "nav_drafts": db.scalar(select(func.count()).select_from(GeneratedPost).where(GeneratedPost.status == GeneratedPostStatus.DRAFT.value)) or 0,
        "display_tz": get_display_timezone(db),
    }


def tpl(request: Request, name: str, db: Session, ctx: dict = None):
    return templates.TemplateResponse(request, name, {
        **(ctx or {}),
        **_nav_counts(db),
        "csrf_token": request.session.get("csrf_token", ""),
    })


def _to_bool(value: str | None) -> bool:
    return value == "on"


def _parse_optional_int(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Auth ──────────────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {
        "csrf_token": request.session.get("csrf_token", ""),
    })


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    return login_action(request, username, password)


# ── Media (authenticated) ─────────────────────────────────────────────────────

@router.get("/media/{path:path}")
def serve_media(path: str, _: bool = Depends(require_auth)):
    file = Path("data/media") / path
    if not file.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(file)


# ── Dashboard ─────────────────────────────────────────────────────────────────

@router.get("/")
async def dashboard(request: Request, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    today = date.today()
    week_ago = today - timedelta(days=7)

    stats = {
        "sources_active": db.scalar(select(func.count()).select_from(SourceChannel).where(SourceChannel.enabled.is_(True))) or 0,
        "sources_total": db.scalar(select(func.count()).select_from(SourceChannel)) or 0,
        "new_posts": db.scalar(select(func.count()).select_from(RawPost).where(RawPost.status.in_(["new", "ready"]))) or 0,
        "duplicates": db.scalar(select(func.count()).select_from(RawPost).where(RawPost.status == RawPostStatus.DUPLICATE.value)) or 0,
        "drafts": db.scalar(select(func.count()).select_from(GeneratedPost).where(GeneratedPost.status == GeneratedPostStatus.DRAFT.value)) or 0,
        "published_today": db.scalar(select(func.count()).select_from(GeneratedPost).where(GeneratedPost.status == GeneratedPostStatus.PUBLISHED.value, func.date(GeneratedPost.published_at) == today)) or 0,
        "published_week": db.scalar(select(func.count()).select_from(GeneratedPost).where(GeneratedPost.status == GeneratedPostStatus.PUBLISHED.value, func.date(GeneratedPost.published_at) >= week_ago)) or 0,
        "fetched_today": db.scalar(select(func.count()).select_from(RawPost).where(func.date(RawPost.created_at) == today)) or 0,
        "fetched_week": db.scalar(select(func.count()).select_from(RawPost).where(func.date(RawPost.created_at) >= week_ago)) or 0,
        "rejected_total": db.scalar(select(func.count()).select_from(RawPost).where(RawPost.status == RawPostStatus.REJECTED.value)) or 0,
    }
    recent_logs = db.scalars(select(ActionLog).order_by(ActionLog.created_at.desc()).limit(15)).all()
    pending_posts = db.scalars(select(RawPost).options(joinedload(RawPost.source)).where(RawPost.status.in_(["new", "ready"])).order_by(RawPost.created_at.desc()).limit(10)).all()
    targets = db.scalars(select(TargetChannel).where(TargetChannel.enabled.is_(True))).all()
    return tpl(request, "dashboard.html", db, {
        "stats": stats,
        "recent_logs": recent_logs,
        "pending_posts": pending_posts,
        "targets": targets,
    })


@router.get("/api/scheduler-status")
async def scheduler_status(request: Request, _: bool = Depends(require_auth)):
    from fastapi.responses import JSONResponse
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


# ── Sources ───────────────────────────────────────────────────────────────────

@router.get("/sources")
def sources(request: Request, db: Session = Depends(get_db), _: bool = Depends(require_auth), error: str | None = None, ok: str | None = None):
    items = db.scalars(select(SourceChannel).order_by(SourceChannel.created_at.desc())).all()
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
def create_source(
    title: str = Form(...),
    username_or_url: str = Form(...),
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    username = TelegramReaderService._extract_username(username_or_url)
    db.add(SourceChannel(title=title, username=username, source_type="telethon", rss_url=None, url=f"https://t.me/{username}", enabled=True))
    db.commit()
    return RedirectResponse(url="/sources", status_code=302)


@router.post("/sources/{source_id}/toggle")
def toggle_source(source_id: int, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    source = db.get(SourceChannel, source_id)
    if source:
        source.enabled = not source.enabled
        db.commit()
    return RedirectResponse(url="/sources", status_code=302)


@router.post("/sources/{source_id}/delete")
def delete_source(source_id: int, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    source = db.get(SourceChannel, source_id)
    if source:
        db.delete(source)
        db.commit()
    return RedirectResponse(url="/sources", status_code=302)


@router.post("/sources/{source_id}/fetch")
async def fetch_source(source_id: int, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    source = db.get(SourceChannel, source_id)
    if not source:
        return RedirectResponse(url="/sources?error=Source+not+found", status_code=302)
    try:
        count = await SourceReaderService().fetch_source(db, source)
        return RedirectResponse(url=f"/sources?ok={urllib.parse.quote(f'Получено {count} новых постов из {source.title}')}", status_code=302)
    except Exception as exc:
        db.add(ActionLog(action="fetch_error", entity_type="SourceChannel", entity_id=str(source.id), message=str(exc)))
        db.commit()
        return RedirectResponse(url=f"/sources?error={urllib.parse.quote(str(exc))}", status_code=302)


# ── Targets ───────────────────────────────────────────────────────────────────

@router.get("/targets")
def targets(request: Request, db: Session = Depends(get_db), _: bool = Depends(require_auth), ok: str | None = None, error: str | None = None):
    items = db.scalars(select(TargetChannel).order_by(TargetChannel.created_at.desc())).all()
    return tpl(request, "targets.html", db, {"items": items, "ok": ok, "error": error})


@router.post("/targets")
def create_target(
    title: str = Form(...),
    chat_id: str = Form(...),
    username: str = Form(""),
    enabled: str | None = Form(None),
    auto_publish_enabled: str | None = Form(None),
    default_mode: str = Form("manual"),
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    db.add(TargetChannel(title=title, chat_id=chat_id, username=username or None, enabled=_to_bool(enabled), auto_publish_enabled=_to_bool(auto_publish_enabled), default_mode=default_mode))
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


# ── Posts ─────────────────────────────────────────────────────────────────────

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
    sort_map = {
        "oldest": RawPost.created_at.asc(),
        "source": RawPost.source_id.asc(),
        "status": RawPost.status.asc(),
    }
    order = sort_map.get(sort, RawPost.created_at.desc())
    query = select(RawPost).options(joinedload(RawPost.source)).order_by(order)
    parsed_source_id = _parse_optional_int(source_id)
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
    offset = (page - 1) * _POSTS_PER_PAGE
    data_query = query.limit(search_limit) if search_limit else query
    items = db.scalars(data_query.offset(offset).limit(_POSTS_PER_PAGE)).all()

    total_pages = max(1, (total + _POSTS_PER_PAGE - 1) // _POSTS_PER_PAGE)
    sources = db.scalars(select(SourceChannel).order_by(SourceChannel.title)).all()
    targets = db.scalars(select(TargetChannel).where(TargetChannel.enabled.is_(True))).all()
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
    post.status = RawPostStatus.GENERATED.value if result.suitable else RawPostStatus.REJECTED.value
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
    targets = db.scalars(select(TargetChannel).where(TargetChannel.enabled.is_(True))).all()
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


# ── Generated posts ───────────────────────────────────────────────────────────

@router.get("/generated")
def generated_posts(request: Request, db: Session = Depends(get_db), _: bool = Depends(require_auth), page: int = 1):
    page = max(1, page)
    total = db.scalar(select(func.count()).select_from(GeneratedPost)) or 0
    total_pages = max(1, (total + _GENERATED_PER_PAGE - 1) // _GENERATED_PER_PAGE)
    items = db.scalars(
        select(GeneratedPost)
        .options(joinedload(GeneratedPost.raw_post))
        .order_by(GeneratedPost.created_at.desc())
        .offset((page - 1) * _GENERATED_PER_PAGE)
        .limit(_GENERATED_PER_PAGE)
    ).all()
    targets = db.scalars(select(TargetChannel).where(TargetChannel.enabled.is_(True))).all()
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


# ── Settings ──────────────────────────────────────────────────────────────────

@router.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_db), _: bool = Depends(require_auth), ok: str | None = None):
    ensure_default_prompt_settings(db)
    db.commit()
    rows = db.scalars(select(AppSetting)).all()
    cfg = {r.key: r.value for r in rows}
    env = get_settings()
    media_dir = Path("data/media")
    media_size_mb = sum(f.stat().st_size for f in media_dir.rglob("*") if f.is_file()) // 1024 // 1024 if media_dir.exists() else 0
    return tpl(request, "settings.html", db, {
        "cfg": cfg,
        "env": env,
        "default_system_prompt": get_ai_system_prompt(db),
        "default_user_prompt": get_ai_user_prompt_template(db),
        "media_size_mb": media_size_mb,
        "ok": ok,
        "current_tz": get_display_timezone(db),
    })


@router.post("/settings")
def settings_save(
    request: Request,
    duplicate_threshold: str = Form("88"),
    fetch_interval_seconds: str = Form("120"),
    max_media_mb: str = Form("50"),
    display_timezone: str = Form("Europe/Moscow"),
    ai_system_prompt: str = Form(""),
    ai_prompt_template: str = Form(""),
    global_auto_publish_enabled: str | None = Form(None),
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    try:
        threshold = max(50, min(100, int(duplicate_threshold)))
    except (ValueError, TypeError):
        threshold = 88
    try:
        interval = max(30, min(86400, int(fetch_interval_seconds)))
    except (ValueError, TypeError):
        interval = 120
    try:
        media_mb = max(1, min(500, int(max_media_mb)))
    except (ValueError, TypeError):
        media_mb = 50

    try:
        ZoneInfo(display_timezone)
    except (ZoneInfoNotFoundError, Exception):
        display_timezone = "Europe/Moscow"

    values = {
        "duplicate_threshold": str(threshold),
        "fetch_interval_seconds": str(interval),
        "max_media_mb": str(media_mb),
        "display_timezone": display_timezone,
        "ai_system_prompt": ai_system_prompt,
        "ai_prompt_template": ai_prompt_template,
        "global_auto_publish_enabled": "true" if _to_bool(global_auto_publish_enabled) else "false",
        "updated_at": _utcnow().isoformat(),
    }
    for k, v in values.items():
        row = db.get(AppSetting, k)
        if row:
            row.value = v
        else:
            db.add(AppSetting(key=k, value=v))
    db.commit()

    sched = getattr(request.app.state, "scheduler", None)
    if sched:
        try:
            sched.update_interval(interval)
        except Exception as exc:
            logger.warning("Could not update scheduler interval: %s", exc)

    return RedirectResponse(url="/settings", status_code=302)


# ── Logs ──────────────────────────────────────────────────────────────────────

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
    total_pages = max(1, (total + _LOGS_PER_PAGE - 1) // _LOGS_PER_PAGE)
    items = db.scalars(query.offset((page - 1) * _LOGS_PER_PAGE).limit(_LOGS_PER_PAGE)).all()
    all_actions = [row[0] for row in db.execute(select(ActionLog.action).distinct().order_by(ActionLog.action)).all()]
    filters = {"action": action or "", "q": q or ""}
    base_qs = urllib.parse.urlencode({k: v for k, v in filters.items() if v})
    if base_qs:
        base_qs += "&"
    return tpl(request, "logs.html", db, {
        "items": items, "all_actions": all_actions, "filters": filters,
        "page": page, "total_pages": total_pages, "total": total, "base_qs": base_qs,
    })


# ── Media cleanup ─────────────────────────────────────────────────────────────

@router.post("/settings/cleanup-media")
def cleanup_media(
    days: int = Form(30),
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    cutoff = _utcnow() - timedelta(days=days)
    old_items = db.scalars(select(MediaItem).where(MediaItem.created_at < cutoff)).all()
    deleted_files = 0
    freed_bytes = 0
    for item in old_items:
        p = Path(item.file_path)
        if p.exists():
            freed_bytes += p.stat().st_size
            p.unlink(missing_ok=True)
            deleted_files += 1
        db.delete(item)
    db.add(ActionLog(
        action="media_cleanup",
        entity_type="MediaItem",
        entity_id="bulk",
        message=f"Deleted {deleted_files} files, freed {freed_bytes // 1024 // 1024} MB (older than {days} days)",
    ))
    db.commit()
    msg = f"Удалено {deleted_files} файлов, освобождено {freed_bytes // 1024 // 1024} МБ"
    return RedirectResponse(url=f"/settings?ok={urllib.parse.quote(msg)}", status_code=302)
