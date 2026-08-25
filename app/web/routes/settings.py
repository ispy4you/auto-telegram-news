import logging
import urllib.parse
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import ActionLog, AppSetting, MediaItem, MediaType, RawPost
from app.services.prompt_settings import ensure_default_prompt_settings, get_ai_system_prompt, get_ai_user_prompt_template, get_display_timezone
from app.services.retention import DEFAULT_RETENTION_DAYS
from app.web.auth import require_auth
from app.web.routes.common import to_bool, tpl, utcnow

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_db), _: bool = Depends(require_auth), ok: str | None = None):
    ensure_default_prompt_settings(db)
    db.commit()
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
        "default_system_prompt": get_ai_system_prompt(db),
        "default_user_prompt": get_ai_user_prompt_template(db),
        "media_size_mb": media_stats["size_mb"],
        "media_stats": media_stats,
        "ok": ok,
        "current_tz": get_display_timezone(db),
        "bot_token_set": bool(cfg.get("telegram_bot_token") or env.telegram_bot_token),
        "ai_key_set": bool(cfg.get("timeweb_ai_gateway_api_key") or env.timeweb_ai_gateway_api_key),
    })


@router.post("/settings")
def settings_save(
    request: Request,
    # Regular fields: None means the key was missing from the POST body entirely
    # (e.g. a malformed/partial request) — skipped rather than saved as blank, so
    # a bad request can't silently wipe a setting. A real browser submission
    # always includes every field, even ones the user left empty.
    duplicate_threshold: str | None = Form(None),
    fetch_interval_seconds: str | None = Form(None),
    max_media_mb: str | None = Form(None),
    max_post_age_hours: str | None = Form(None),
    default_lookback_limit: str | None = Form(None),
    action_log_retention_days: str | None = Form(None),
    display_timezone: str | None = Form(None),
    ai_system_prompt: str | None = Form(None),
    ai_prompt_template: str | None = Form(None),
    operator_chat_id: str | None = Form(None),
    notify_draft_threshold: str | None = Form(None),
    semantic_threshold: str | None = Form(None),
    timeweb_ai_gateway_base_url: str | None = Form(None),
    timeweb_ai_gateway_model: str | None = Form(None),
    ai_temperature: str | None = Form(None),
    ai_max_tokens: str | None = Form(None),
    ai_timeout_seconds: str | None = Form(None),
    # Secrets: blank means "leave unchanged" (the form never re-renders the real
    # value), on top of the same missing-key skip as above.
    telegram_bot_token: str | None = Form(None),
    timeweb_ai_gateway_api_key: str | None = Form(None),
    # Checkboxes: browsers omit unchecked boxes entirely, so absence here means
    # "unchecked", not "not submitted" — always written, unlike the fields above.
    global_auto_publish_enabled: str | None = Form(None),
    notify_on_error: str | None = Form(None),
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    values: dict[str, str] = {
        "global_auto_publish_enabled": "true" if to_bool(global_auto_publish_enabled) else "false",
        "notify_on_error": "true" if to_bool(notify_on_error) else "false",
        "updated_at": utcnow().isoformat(),
    }

    if duplicate_threshold is not None:
        try:
            values["duplicate_threshold"] = str(max(50, min(100, int(duplicate_threshold))))
        except (ValueError, TypeError):
            values["duplicate_threshold"] = "88"

    interval = None
    if fetch_interval_seconds is not None:
        try:
            interval = max(30, min(86400, int(fetch_interval_seconds)))
        except (ValueError, TypeError):
            interval = 120
        values["fetch_interval_seconds"] = str(interval)

    if max_media_mb is not None:
        try:
            values["max_media_mb"] = str(max(1, min(500, int(max_media_mb))))
        except (ValueError, TypeError):
            values["max_media_mb"] = "50"

    if max_post_age_hours is not None:
        try:
            values["max_post_age_hours"] = "%g" % max(0, min(8760, float(max_post_age_hours)))
        except (ValueError, TypeError):
            values["max_post_age_hours"] = "24"

    if default_lookback_limit is not None:
        try:
            values["default_lookback_limit"] = str(max(1, min(500, int(default_lookback_limit))))
        except (ValueError, TypeError):
            values["default_lookback_limit"] = "50"

    if action_log_retention_days is not None:
        try:
            values["action_log_retention_days"] = str(max(0, min(365, int(action_log_retention_days))))
        except (ValueError, TypeError):
            values["action_log_retention_days"] = str(DEFAULT_RETENTION_DAYS)

    if display_timezone is not None:
        try:
            ZoneInfo(display_timezone)
            values["display_timezone"] = display_timezone
        except (ZoneInfoNotFoundError, Exception):
            values["display_timezone"] = "Europe/Moscow"

    if ai_system_prompt is not None:
        values["ai_system_prompt"] = ai_system_prompt
    if ai_prompt_template is not None:
        values["ai_prompt_template"] = ai_prompt_template

    if operator_chat_id is not None:
        values["operator_chat_id"] = operator_chat_id.strip()

    if notify_draft_threshold is not None:
        try:
            values["notify_draft_threshold"] = str(max(0, int(notify_draft_threshold)))
        except (ValueError, TypeError):
            values["notify_draft_threshold"] = "0"

    if semantic_threshold is not None:
        try:
            values["semantic_threshold"] = str(max(0.0, min(1.0, float(semantic_threshold or 0))))
        except (ValueError, TypeError):
            values["semantic_threshold"] = "0"

    if timeweb_ai_gateway_base_url is not None:
        values["timeweb_ai_gateway_base_url"] = timeweb_ai_gateway_base_url.strip()
    if timeweb_ai_gateway_model is not None:
        values["timeweb_ai_gateway_model"] = timeweb_ai_gateway_model.strip()

    if ai_temperature is not None:
        try:
            values["ai_temperature"] = str(max(0.0, min(2.0, float(ai_temperature))))
        except (ValueError, TypeError):
            values["ai_temperature"] = "0.4"

    if ai_max_tokens is not None:
        try:
            values["ai_max_tokens"] = str(max(1, min(32000, int(ai_max_tokens))))
        except (ValueError, TypeError):
            values["ai_max_tokens"] = "1600"

    if ai_timeout_seconds is not None:
        try:
            values["ai_timeout_seconds"] = str(max(5, min(300, int(ai_timeout_seconds))))
        except (ValueError, TypeError):
            values["ai_timeout_seconds"] = "60"

    # Secrets: only overwrite when the admin actually typed a new value.
    if telegram_bot_token:
        values["telegram_bot_token"] = telegram_bot_token.strip()
    if timeweb_ai_gateway_api_key:
        values["timeweb_ai_gateway_api_key"] = timeweb_ai_gateway_api_key.strip()

    for k, v in values.items():
        row = db.get(AppSetting, k)
        if row:
            row.value = v
        else:
            db.add(AppSetting(key=k, value=v))
    db.commit()

    if interval is not None:
        sched = getattr(request.app.state, "scheduler", None)
        if sched:
            try:
                sched.update_interval(interval)
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
