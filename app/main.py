import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from sqlalchemy import select, text, update

from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.models import GeneratedPost, GeneratedPostStatus, Project, RawPost, RawPostStatus
from app.services.prompt_settings import ensure_default_prompt_settings
from app.services.scheduler import SchedulerService
from app.services.telegram_event_listener import TelegramEventListenerService
from app.web.csrf import CSRFMiddleware
from app.web.routes import router

logger = logging.getLogger(__name__)

settings = get_settings()
Path("data/media").mkdir(parents=True, exist_ok=True)
Path("data/telegram_session").mkdir(parents=True, exist_ok=True)
Base.metadata.create_all(bind=engine)

# ── Автомиграции (добавление колонок к существующим таблицам) ─────────────────
# Совместимо с SQLite и PostgreSQL: ошибка «column already exists» перехватывается.
with engine.connect() as _conn:
    # Добавление новых колонок (игнорируем если уже есть)
    for _tbl, _col, _typ in [
        ("target_channels", "publish_from", "TEXT"),
        ("target_channels", "publish_to", "TEXT"),
        ("generated_posts", "telegram_message_id", "BIGINT"),
        ("source_channels", "project_id", "INTEGER"),
        ("target_channels", "project_id", "INTEGER"),
        ("raw_posts", "embedding", "TEXT"),
    ]:
        try:
            _conn.execute(text(f"ALTER TABLE {_tbl} ADD COLUMN {_col} {_typ}"))
            _conn.commit()
        except Exception as _exc:
            _conn.rollback()
            if "already exists" not in str(_exc).lower() and "duplicate column" not in str(_exc).lower():
                logger.warning("Migration ADD COLUMN %s.%s failed: %s", _tbl, _col, _exc)
    # Расширяем Integer → BigInteger для колонок с Telegram ID (64-bit значения)
    _bigint_cols = [
        ("raw_posts", "telegram_message_id"),
        ("raw_posts", "telegram_grouped_id"),
        ("source_channels", "last_message_id"),
        ("source_channels", "telegram_channel_id"),
        ("media_items", "telegram_message_id"),
        ("generated_posts", "telegram_message_id"),
    ]
    for _tbl, _col in _bigint_cols:
        try:
            _conn.execute(text(f"ALTER TABLE {_tbl} ALTER COLUMN {_col} TYPE BIGINT"))
            _conn.commit()
        except Exception as _exc:
            _conn.rollback()
            logger.debug("Migration ALTER COLUMN %s.%s to BIGINT skipped: %s", _tbl, _col, _exc)

# ── Убедиться что дефолтный проект существует (ORM, без диалект-специфичного SQL)
with SessionLocal() as db:
    if not db.get(Project, 1):
        db.add(Project(
            id=1, name="Default", slug="default", enabled=True,
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        ))
        db.commit()
    db.execute(text("UPDATE source_channels SET project_id = 1 WHERE project_id IS NULL"))
    db.execute(text("UPDATE target_channels SET project_id = 1 WHERE project_id IS NULL"))
    db.commit()

with SessionLocal() as db:
    ensure_default_prompt_settings(db)
    db.execute(
        update(RawPost)
        .where(RawPost.status == RawPostStatus.FAILED.value)
        .values(status=RawPostStatus.GENERATED.value)
    )
    db.execute(
        update(GeneratedPost)
        .where(GeneratedPost.status == GeneratedPostStatus.FAILED.value, GeneratedPost.publish_error.is_(None))
        .values(status=GeneratedPostStatus.DRAFT.value)
    )
    db.commit()

event_listener = TelegramEventListenerService()
scheduler_service = SchedulerService(
    interval_seconds=settings.fetch_interval_seconds,
    listener=event_listener,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.scheduler = scheduler_service
    app.state.event_listener = event_listener
    scheduler_service.start()
    if settings.telegram_api_id and settings.telegram_api_hash and settings.telegram_session_path:
        await event_listener.start(SessionLocal)
    yield
    await event_listener.stop()
    scheduler_service.shutdown()


app = FastAPI(title="Telegram News Bot Admin", lifespan=lifespan)
# Middleware order: last-added runs first (outermost). Session must run before CSRF.
# So we add CSRF first (inner), then Session (outer).
app.add_middleware(CSRFMiddleware)
app.add_middleware(SessionMiddleware, secret_key=settings.app_secret_key)
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")
# /media is served via an authenticated route in routes.py — not a public StaticFiles mount.
app.include_router(router)


@app.exception_handler(HTTPException)
async def auth_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401 and request.url.path != "/login":
        return RedirectResponse(url="/login", status_code=302)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
