import logging
import time
import urllib.parse
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import ActionLog, AppSetting, MediaItem, MediaType, RawPost
from app.services import embedder, prompt_template, settings_registry
from app.services.ai_gateway import AiGatewayClient
from app.services.prompt_settings import get_display_timezone
from app.web.auth import require_auth
from app.web.routes.common import tpl, utcnow

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/settings")
def settings_page(
    request: Request,
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
    ok: str | None = None,
    warn: str | None = None,
):
    rows = db.scalars(select(AppSetting)).all()
    cfg = {r.key: r.value for r in rows}
    env = get_settings()
    media_dir = Path("data/media")
    disk_files = [f for f in media_dir.rglob("*") if f.is_file()] if media_dir.exists() else []
    disk_bytes = sum(f.stat().st_size for f in disk_files)
    media_stats = {
        "path": str(media_dir.resolve()),
        "files": len(disk_files),
        "size_mb": round(disk_bytes / 1024 / 1024, 1),
        "size_bytes": disk_bytes,
        "db_total": db.scalar(select(func.count()).select_from(MediaItem)) or 0,
        "photos": db.scalar(select(func.count()).select_from(MediaItem).where(MediaItem.media_type == MediaType.PHOTO.value)) or 0,
        "videos": db.scalar(select(func.count()).select_from(MediaItem).where(MediaItem.media_type == MediaType.VIDEO.value)) or 0,
        "docs": db.scalar(select(func.count()).select_from(MediaItem).where(MediaItem.media_type == MediaType.DOCUMENT.value)) or 0,
    }
    return tpl(request, "settings.html", db, {
        "cfg": cfg,
        "env": env,
        "default_prompt": settings_registry.default("ai_prompt"),
        "media_size_mb": media_stats["size_mb"],
        "media_stats": media_stats,
        "ok": ok,
        "warn": warn,
        "current_tz": get_display_timezone(db),
        "embedder_model": embedder.model_name(),
        "bot_token_set": bool(cfg.get("telegram_bot_token") or env.telegram_bot_token),
        "ai_key_set": bool(cfg.get("timeweb_ai_gateway_api_key") or env.timeweb_ai_gateway_api_key),
    })


@router.post("/settings")
async def settings_save(
    request: Request,
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    """Сохраняет форму настроек.

    Границы, типы и значения по умолчанию объявлены в реестре, а не здесь:
    раньше каждое поле разбиралось вручную, и часть границ успела разойтись
    с дефолтами, которые читал остальной код.
    """
    form = await request.form()
    values: dict[str, str] = {}

    for key in settings_registry.FORM_KEYS:
        setting = settings_registry.spec(key)

        if setting.kind == settings_registry.BOOL:
            # Снятые галочки браузер не присылает вовсе, поэтому отсутствие
            # ключа здесь означает «выключено», а не «не передавали».
            values[key] = settings_registry.normalize(key, str(form.get(key) or ""))
            continue

        submitted = form.get(key)
        if submitted is None:
            # Частичная отправка не должна молча стирать настройку.
            continue
        if setting.secret and not str(submitted).strip():
            # Форма не перерисовывает секреты: пустое поле значит «оставить как есть».
            continue
        values[key] = settings_registry.normalize(key, str(submitted))

    settings_registry.store(db, values)

    # Промпт больше не может уронить генерацию, но опечатка в плейсхолдере
    # молча оставит модель без данных — поэтому говорим о ней сразу.
    warnings = prompt_template.problems(values["ai_prompt"]) if "ai_prompt" in values else []

    if "fetch_interval_seconds" in values:
        sched = getattr(request.app.state, "scheduler", None)
        if sched:
            try:
                sched.update_interval(int(values["fetch_interval_seconds"]))
            except Exception as exc:
                logger.warning("Could not update scheduler interval: %s", exc)

    if warnings:
        return RedirectResponse(
            url="/settings?warn=" + urllib.parse.quote(" ".join(warnings)[:600]),
            status_code=302,
        )
    return RedirectResponse(url="/settings", status_code=302)


def _preview_post(db: Session, offset: int) -> RawPost | None:
    """Свежая новость с текстом: offset сдвигает к следующей по свежести."""
    return db.scalars(
        select(RawPost)
        .where(RawPost.original_text != "")
        .order_by(RawPost.fetched_at.desc(), RawPost.id.desc())
        .offset(offset)
        .limit(1)
    ).first()


@router.post("/settings/prompt-preview")
async def settings_prompt_preview(
    prompt: str = Form(""),
    offset: int = Form(0),
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    """Прогоняет текст прямо из поля на свежей новости.

    Ничего не сохраняет: ни настройку, ни готовый пост. Иначе проверить правку
    можно было бы только испортив живую генерацию.
    """
    offset = max(0, min(offset, 20))
    post = _preview_post(db, offset)
    if post is None and offset:
        offset, post = 0, _preview_post(db, 0)
    if post is None:
        return JSONResponse({
            "ok": False,
            "error": "Проверить не на чем: пока не собрано ни одной новости с текстом.",
        })

    started = time.monotonic()
    result = await AiGatewayClient().generate_news_post(post, db, rules=prompt)
    elapsed = round(time.monotonic() - started, 1)

    outcome = result.reason if result.failed else ("подходит" if result.suitable else "не подходит")
    db.add(ActionLog(
        action="prompt_preview",
        entity_type="RawPost",
        entity_id=str(post.id),
        message=f"Проверка промпта на новости #{post.id}: {outcome}",
    ))
    db.commit()

    published = post.published_at_source
    return JSONResponse({
        "ok": True,
        "offset": offset,
        "post": {
            "id": post.id,
            "source": post.source.title if post.source else "источник неизвестен",
            "published_at": published.strftime("%d.%m.%Y %H:%M") if published else "",
            "excerpt": (post.original_text or "")[:400],
            "truncated": len(post.original_text or "") > 400,
        },
        "failed": result.failed,
        "suitable": result.suitable,
        "text": result.text,
        "reason": result.reason,
        "model": result.model_name or "не задана",
        "finish_reason": result.finish_reason,
        "elapsed": elapsed,
    })


@router.post("/settings/reset")
def settings_reset(
    request: Request,
    key: str = Form(...),
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    """Снимает переопределение одной настройки, возвращая её к значению по умолчанию."""
    try:
        settings_registry.reset(db, key)
    except KeyError:
        raise HTTPException(status_code=400, detail=f"Неизвестная настройка: {key}")

    if key == "fetch_interval_seconds":
        sched = getattr(request.app.state, "scheduler", None)
        if sched:
            try:
                sched.update_interval(settings_registry.get(key, db))
            except Exception as exc:
                logger.warning("Could not update scheduler interval: %s", exc)

    return RedirectResponse(url="/settings", status_code=302)


@router.post("/settings/test-notify")
async def settings_test_notify(db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    from app.services.notifier import notify_operator
    ok = await notify_operator(db, "✅ <b>Тестовое уведомление</b>\n\nУведомления настроены и работают.")
    if ok:
        return RedirectResponse(url="/settings?ok=Тестовое+уведомление+отправлено", status_code=302)
    return RedirectResponse(url="/settings?ok=Не+удалось+отправить:+проверьте+chat_id+и+bot_token", status_code=302)


@router.post("/settings/cleanup-media")
def cleanup_media(
    days: int = Form(30),
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    media_dir = Path("data/media")
    deleted_files = 0
    freed_bytes = 0
    affected_post_ids: set[int] = set()

    if days <= 0:
        # Удалить всё: сначала все файлы с диска напрямую, потом все записи из БД
        if media_dir.exists():
            for f in media_dir.rglob("*"):
                if f.is_file():
                    freed_bytes += f.stat().st_size
                    f.unlink(missing_ok=True)
                    deleted_files += 1
        # Очистить все MediaItem из БД
        all_items = db.scalars(select(MediaItem)).all()
        for item in all_items:
            affected_post_ids.add(item.raw_post_id)
            db.delete(item)
        label = "все"
    else:
        cutoff = utcnow() - timedelta(days=days)
        old_items = db.scalars(select(MediaItem).where(MediaItem.created_at < cutoff)).all()
        for item in old_items:
            p = Path(item.file_path)
            if p.exists():
                freed_bytes += p.stat().st_size
                p.unlink(missing_ok=True)
                deleted_files += 1
            affected_post_ids.add(item.raw_post_id)
            db.delete(item)
        label = f"старше {days} дн."

    db.flush()

    # Пересчитать has_media / media_count на затронутых постах
    for post_id in affected_post_ids:
        post = db.get(RawPost, post_id)
        if post:
            remaining = db.scalar(
                select(func.count()).select_from(MediaItem).where(MediaItem.raw_post_id == post_id)
            ) or 0
            post.media_count = remaining
            post.has_media = remaining > 0

    freed_mb = round(freed_bytes / 1024 / 1024, 1)
    db.add(ActionLog(
        action="media_cleanup",
        entity_type="MediaItem",
        entity_id="bulk",
        message=f"Удалено {deleted_files} файлов ({freed_mb} МБ), {label}. Обновлено постов: {len(affected_post_ids)}",
    ))
    db.commit()

    # Удалить пустые папки
    if media_dir.exists():
        for d in sorted(media_dir.rglob("*"), reverse=True):
            if d.is_dir():
                try:
                    d.rmdir()
                except OSError:
                    pass

    msg = f"Удалено {deleted_files} файлов, освобождено {freed_mb} МБ"
    return RedirectResponse(url=f"/settings?ok={urllib.parse.quote(msg)}", status_code=302)
